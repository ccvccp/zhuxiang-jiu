"""40号·平台流量DV博主模块·P6b 音视频自主创作工坊专项测试

覆盖(设计文档《40号 P6 升级方案》§4):
    1. 脚本生成: 形式决策(平台规则树)/分镜结构(3 场景+情绪+
       时长标记)/违禁词零出现/AI 水印+哈希/合规分落库/参数 409
    2. 授权校验: 登记哈希存证/BGM 授权通过/不存在 404/
       撤回拒绝/过期拒绝/封禁拒绝(P6a 联动)/声纹人设/
       声纹撤回联动拒绝
    3. 合规内生: licensed 缺授权拒绝/声纹过期拒绝/人设停用拒绝/
       素材槽未授权 404/素材槽授权通过/pause 拒绝
    4. 渲染三态: mock meta 产物/播客 mp3+音频参数/
       real fail-hard + mock_fallback 降级留痕
    5. A/B 联动: 实验创建(版本引用 avWorkId)/
       P5b 指标注入+promote 兼容/胜出固化策略库

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p6b.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
# mock 槽位/日期固定(测试确定性: 跨槽评分分布无三档保证)
os.environ["BLOGGER_MOCK_SLOT"] = "1"
os.environ["BLOGGER_MOCK_DATE"] = "20260910"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.blogger_av_create_service import (
    BloggerAVCreateService, decide_form, map_style,
    FORM_SHORT_VIDEO, FORM_MID_VIDEO, FORM_PODCAST,
    EMOTION_WARM, EMOTION_PROFESSIONAL, EMOTION_PLAYFUL,
)
from services.blogger_av_learn_service import BloggerAVLearnService
from services.blogger_auto_create_service import (
    BloggerAutoCreateService, AI_WATERMARK, FORBIDDEN_WORDS,
    HOOK_PRICE_ANCHOR, HOOK_GIFT_FACE,
)
from services.blogger_auto_govern_service import (
    BloggerAutoGovernService,
)

PASS = 0
FAIL = 0
RESULTS = []

PAST = "2000-01-01T00:00:00+00:00"


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


async def _persona(svc: BloggerAVCreateService,
                   name: str = "竹小香") -> dict:
    """构造 1 个原创 IP 人设"""
    return await svc.register_persona(
        name, "original_ip", voice_style="medium",
        tone_style="warm")


# ============================================================
# 1. 脚本生成(6 断言)
# ============================================================

class TestScriptGen:
    async def run(self):
        reset_store()
        svc = BloggerAVCreateService()
        persona = await _persona(svc)
        pid = persona["personaId"]

        # 1) 形式决策(平台规则树: 抖音短视频/B站中视频/小宇宙播客)
        s1 = await svc.generate_script(
            "选酒避坑", "douyin", pid, HOOK_PRICE_ANCHOR)
        s2 = await svc.generate_script(
            "选酒避坑", "bilibili", pid, HOOK_PRICE_ANCHOR)
        s3 = await svc.generate_script(
            "选酒避坑", "xiaoyuzhou", pid, HOOK_PRICE_ANCHOR)
        record("脚本-形式决策",
               s1["form"] == FORM_SHORT_VIDEO
               and s2["form"] == FORM_MID_VIDEO
               and s3["form"] == FORM_PODCAST
               and decide_form("douyin") == FORM_SHORT_VIDEO,
               f"{s1['form']}/{s2['form']}/{s3['form']}")

        # 2) 分镜结构: 3 场景(钩子/主体/尾)+情绪标签+时长标记
        sb = s1["storyboards"]
        record("脚本-分镜结构",
               len(sb) == 3
               and [s["durationMark"] for s in sb]
               == ["hook", "body", "tail"]
               and all(s.get("emotion") for s in sb)
               and sb[0]["durationSec"] >= 1,
               f"n={len(sb)}")

        # 3) 违禁词零出现(生成时置零——sanitize 复用)
        joined = "\n".join(s["text"] for s in sb)
        record("脚本-违禁词零出现",
               not any(w in joined for w in FORBIDDEN_WORDS),
               "命中禁用词")

        # 4) AI 水印(尾分镜)+ 不可见水印哈希(40 位)
        record("脚本-AI水印+哈希",
               AI_WATERMARK in sb[-1]["text"]
               and s1["aiWatermark"] == AI_WATERMARK
               and len(s1["watermarkHash"]) == 40,
               f"hash={s1['watermarkHash']}")

        # 5) 合规分落库(三审闸门复用, ≥60)
        record("脚本-合规分落库",
               float(s1["complianceScore"]) >= 60
               and float(s3["complianceScore"]) >= 60,
               f"s1={s1['complianceScore']}")

        # 6) 参数 409(无效平台/钩子/风格指令)
        errs = 0
        for kwargs in (
                dict(topic="t", platform="tiktok", persona_id=pid,
                     hook_type=HOOK_PRICE_ANCHOR),
                dict(topic="t", platform="douyin", persona_id=pid,
                     hook_type="hook_nonexist"),
                dict(topic="t", platform="douyin", persona_id=pid,
                     hook_type=HOOK_PRICE_ANCHOR,
                     style="更冷酷")):
            try:
                await svc.generate_script(**kwargs)
            except ValueError:
                errs += 1
        record("脚本-参数409", errs == 3, f"errs={errs}")


# ============================================================
# 2. 授权校验(8 断言)
# ============================================================

class TestLicenses:
    async def run(self):
        reset_store()
        svc = BloggerAVCreateService()
        persona = await _persona(svc)
        pid = persona["personaId"]

        # 7) 授权登记(哈希存证 40 位)
        lic = await svc.register_license(
            "bgm", "竹香小调", "官方内容库", scope="全平台非商用")
        record("授权-登记哈希存证",
               len(lic["evidenceHash"]) == 40
               and lic["status"] == "active",
               f"lic={lic.get('evidenceHash')}")

        # 8) BGM 授权通过(脚本带 bgmLicenseId 生成成功)
        s = await svc.generate_script(
            "选酒避坑", "douyin", pid, HOOK_PRICE_ANCHOR,
            bgm_license_id=lic["licenseId"])
        record("授权-BGM通过",
               s["bgmLicenseId"] == lic["licenseId"],
               f"bgm={s['bgmLicenseId']}")

        # 9) BGM 不存在 → KeyError(404)
        try:
            await svc.generate_script(
                "t", "douyin", pid, HOOK_PRICE_ANCHOR,
                bgm_license_id=999)
            ok = False
        except KeyError:
            ok = True
        record("授权-不存在404", ok)

        # 10) BGM 撤回 → 生成拒绝(秒级联动)
        await svc.revoke_license(lic["licenseId"])
        try:
            await svc.generate_script(
                "t", "douyin", pid, HOOK_PRICE_ANCHOR,
                bgm_license_id=lic["licenseId"])
            ok = False
        except ValueError as exc:
            ok = "撤回" in str(exc) or "失效" in str(exc)
        record("授权-BGM撤回拒绝", ok)

        # 11) BGM 过期 → 生成拒绝
        lic2 = await svc.register_license(
            "bgm", "过期曲", "某厂", expires_at=PAST)
        try:
            await svc.generate_script(
                "t", "douyin", pid, HOOK_PRICE_ANCHOR,
                bgm_license_id=lic2["licenseId"])
            ok = False
        except ValueError as exc:
            ok = "过期" in str(exc)
        record("授权-BGM过期拒绝", ok)

        # 12) BGM 封禁元素 → 拒绝(P6a 封禁库实时拦截)
        av_learn = BloggerAVLearnService()
        await av_learn.add_banned_element(
            "bgm", "危险夜曲", platform="douyin")
        lic3 = await svc.register_license("bgm", "危险夜曲", "某厂")
        try:
            await svc.generate_script(
                "t", "douyin", pid, HOOK_PRICE_ANCHOR,
                bgm_license_id=lic3["licenseId"])
            ok = False
        except ValueError as exc:
            ok = "封禁" in str(exc)
        record("授权-BGM封禁拒绝", ok)

        # 13) 声纹授权人设(licensed persona 绑定 voice 授权)
        voice = await svc.register_license(
            "voice", "温暖女声", "声音厂牌")
        vp = await svc.register_persona(
            "小竹姐姐", "licensed", voice_style="slow",
            tone_style="warm", license_id=voice["licenseId"])
        sv = await svc.generate_script(
            "品鉴入门", "xiaoyuzhou", vp["personaId"],
            HOOK_GIFT_FACE, style="更专业")
        record("授权-声纹人设通过",
               sv["personaId"] == vp["personaId"]
               and sv["emotion"] == EMOTION_PROFESSIONAL,
               f"emo={sv['emotion']}")

        # 14) 声纹撤回 → 人设生成拒绝(授权硬门联动)
        await svc.revoke_license(voice["licenseId"])
        try:
            await svc.generate_script(
                "t", "douyin", vp["personaId"], HOOK_PRICE_ANCHOR)
            ok = False
        except ValueError as exc:
            ok = "撤回" in str(exc) or "失效" in str(exc)
        record("授权-声纹撤回联动拒绝", ok)


# ============================================================
# 3. 合规内生(6 断言)
# ============================================================

class TestCompliance:
    async def run(self):
        reset_store()
        svc = BloggerAVCreateService()
        persona = await _persona(svc)
        pid = persona["personaId"]

        # 15) licensed 人设缺授权 → 拒绝(红线: 禁止未授权声纹)
        try:
            await svc.register_persona(
                "盗声者", "licensed")
            ok = False
        except ValueError:
            ok = True
        record("合规-licensed缺授权拒绝", ok)

        # 16) 声纹授权过期 → 人设校验拒绝
        #     (先在役登记人设, 再将授权置为过期——模拟到期)
        voice = await svc.register_license("voice", "到期声", "某厂")
        vp = await svc.register_persona(
            "到期人设", "licensed",
            license_id=voice["licenseId"])
        await svc.repo.update_license(
            voice["licenseId"], {"expiresAt": PAST})
        try:
            await svc.generate_script(
                "t", "douyin", vp["personaId"], HOOK_PRICE_ANCHOR)
            ok = False
        except ValueError as exc:
            ok = "过期" in str(exc)
        record("合规-声纹过期拒绝", ok)

        # 17) 人设停用 → 拒绝
        persona2 = await _persona(svc, "退役人设")
        await svc.repo.update_persona(
            persona2["personaId"], {"status": "retired"})
        try:
            await svc.generate_script(
                "t", "douyin", persona2["personaId"],
                HOOK_PRICE_ANCHOR)
            ok = False
        except ValueError as exc:
            ok = "停用" in str(exc)
        record("合规-人设停用拒绝", ok)

        # 18) 素材槽未授权(不存在)→ KeyError
        try:
            await svc.generate_script(
                "t", "douyin", pid, HOOK_PRICE_ANCHOR,
                material_slots=[999])
            ok = False
        except KeyError:
            ok = True
        record("合规-素材槽404", ok)

        # 19) 素材槽授权通过(kind=material 在役)
        mat = await svc.register_license(
            "material", "竹林实拍素材", "UGC创作者")
        s = await svc.generate_script(
            "选酒避坑", "douyin", pid, HOOK_PRICE_ANCHOR,
            material_slots=[mat["licenseId"]])
        record("合规-素材槽通过",
               s["materialSlots"] == [mat["licenseId"]],
               f"slots={s['materialSlots']}")

        # 20) pause 后脚本生成拒绝(P5d 仲裁优先于调度)
        gov = BloggerAutoGovernService()
        await gov.pause_autonomy("P6b 测试暂停")
        try:
            await svc.generate_script(
                "t", "douyin", pid, HOOK_PRICE_ANCHOR)
            ok = False
        except ValueError as exc:
            ok = "已暂停" in str(exc)
        record("合规-pause拒绝", ok)
        await gov.resume_autonomy()


# ============================================================
# 4. 渲染三态(3 断言)
# ============================================================

class TestRender:
    async def run(self):
        reset_store()
        import services.blogger_av_create_service as av_mod
        svc = BloggerAVCreateService()
        persona = await _persona(svc)
        s1 = await svc.generate_script(
            "选酒避坑", "douyin", persona["personaId"],
            HOOK_PRICE_ANCHOR)
        s2 = await svc.generate_script(
            "品鉴入门", "xiaoyuzhou", persona["personaId"],
            HOOK_GIFT_FACE)

        # 21) mock 渲染: meta + 占位路径 + rendered 状态
        w1 = await svc.render_work(s1["scriptId"])
        record("渲染-mock产物",
               w1["renderStatus"] == "rendered"
               and w1["meta"]["filePath"].startswith("/mock/av/")
               and w1["meta"]["filePath"].endswith(".mp4")
               and w1["meta"]["renderParams"]["video"] is True,
               f"w={w1['meta']['filePath']}")

        # 22) 播客渲染: .mp3 + 音频参数 + 分镜携带
        w2 = await svc.render_work(s2["scriptId"])
        record("渲染-播客mp3",
               w2["meta"]["filePath"].endswith(".mp3")
               and w2["meta"]["renderParams"]["video"] is False
               and w2["meta"]["renderParams"]["audioKbps"] == 128
               and len(w2["meta"]["storyboards"]) == 3,
               f"w={w2['meta']['filePath']}")

        # 23) real fail-hard + mock_fallback 降级留痕
        av_mod.AV_CHANNEL_MODE = "real"
        real_fail = False
        try:
            await svc.render_work(s1["scriptId"])
        except RuntimeError:
            real_fail = True
        av_mod.AV_CHANNEL_MODE = "mock_fallback"
        w3 = await svc.render_work(s1["scriptId"])
        av_mod.AV_CHANNEL_MODE = "mock"
        record("渲染-real硬失败+降级留痕",
               real_fail
               and w3["renderMode"] == "mock_fallback"
               and w3["receipt"]["fallback"] is True,
               f"real={real_fail} mode={w3['renderMode']}")


# ============================================================
# 5. A/B 联动(3 断言)
# ============================================================

class TestAB:
    async def run(self):
        reset_store()
        svc = BloggerAVCreateService()
        persona = await _persona(svc)
        pid = persona["personaId"]

        # 24) 实验创建: ≥2 已渲染作品, 版本引用 avWorkId
        sa = await svc.generate_script(
            "选酒避坑", "douyin", pid, HOOK_PRICE_ANCHOR)
        sb_ = await svc.generate_script(
            "选酒避坑", "douyin", pid, HOOK_GIFT_FACE)
        wa = await svc.render_work(sa["scriptId"])
        wb = await svc.render_work(sb_["scriptId"])
        try:
            await svc.create_av_experiment(
                "t", "douyin", [wa["avWorkId"]])
            lone = False
        except ValueError:
            lone = True
        exp = await svc.create_av_experiment(
            "选酒避坑AB", "douyin",
            [wa["avWorkId"], wb["avWorkId"]])
        record("AB-实验创建",
               lone
               and len(exp["versions"]) == 2
               and {v["avWorkId"] for v in exp["versions"]}
               == {wa["avWorkId"], wb["avWorkId"]}
               and exp["experiment"]["status"] == "running",
               f"exp={exp['experiment'].get('versionIds')}")

        # 25) P5b 指标注入 + promote 兼容(样本≥50, 胜出评估)
        p5b = BloggerAutoCreateService()
        va, vb = exp["versions"]
        await p5b.record_version_metrics(
            va["versionId"], clicks=60, registered=3, ordered=1)
        await p5b.record_version_metrics(
            vb["versionId"], clicks=60)
        pro = await p5b.promote_experiment(
            exp["experiment"]["experimentId"])
        record("AB-promote兼容",
               pro["experiment"]["status"] == "promoted"
               and pro["winner"]["versionId"] == va["versionId"]
               and pro["winner"]["status"] == "winner",
               f"winner={pro['winner'].get('versionId')}")

        # 26) 胜出固化策略库(winCount+1, 钩子+结构双固化)
        hook_s = await svc.repo.find_strategy(
            "hook", HOOK_PRICE_ANCHOR)
        struct_s = await svc.repo.find_strategy(
            "structure", sa["structureType"])
        record("AB-固化策略库",
               hook_s is not None
               and int(hook_s["winCount"]) == 1
               and struct_s is not None
               and int(struct_s["winCount"]) == 1,
               f"hook={hook_s and hook_s.get('winCount')}")


async def main():
    tests = [TestScriptGen(), TestLicenses(), TestCompliance(),
             TestRender(), TestAB()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P6b 音视频自主创作工坊专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
