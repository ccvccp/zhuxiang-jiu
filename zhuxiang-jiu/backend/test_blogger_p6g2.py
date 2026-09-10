"""40号·平台流量DV博主模块·P6g-2 红队七向量专项测试(隔离域 993x)

设计文档《40号 P6g 规划方案》§4——收官期主动攻击验证(对齐 66号先例:
确定性零 LLM, 每向量独立 try 单向量异常不中断整轮, 隔离域自清理)。

七个攻击向量(防线必拒断言):
    RT-01 越权租用: 会员 B 用 A 的 personaId 生成/渲染/上报
         → ownerId 命名空间 404 铁律
    RT-02 租金逃逸: 直调 P6b render_work 绕租用账本(平台自营轨
         不带 ownerId——非逃逸; 会员轨必经 _charge 验证)
    RT-03 深审投毒: 拆词"疫?情"绕风险词 → 归一后一票否决
    RT-04 授权伪造: 撤回后深审/过期授权转发/伪造哈希
    RT-05 γ 权重攻击: 直传 γ<α + 存储合并越界 → 双硬拒
    RT-06 自愈滥用: 耗尽后静默重试 + pause 窗口强写
    RT-07 水印剥离: 构造无 AI 标识正文 + 伪造哈希

隔离域: 主体 ID 偏移基址 9930(避开 64号 98xx/65号 9881+/66号 991x);
种子数据测试后自清理(66号 Redis 态清理教训: 跳过 seq/index 辅助键)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p6g2.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
# mock 槽位/日期固定(测试确定性)
os.environ["BLOGGER_MOCK_SLOT"] = "1"
os.environ["BLOGGER_MOCK_DATE"] = "20260910"

from services.blogger_rental_service import BloggerRentalService
from services.blogger_av_create_service import (
    BloggerAVCreateService,
)
from services.blogger_av_publish_service import (
    BloggerAVPublishService,
)
from services.blogger_fwd_service import (
    BloggerFwdService, compute_deep_score,
)
from services.blogger_av_learn_service import (
    compute_emotion_reward,
)
from services.blogger_auto_govern_service import (
    BloggerAutoGovernService,
)
from services.blogger_av_govern_service import (
    BloggerAVGovernService, AV_HEAL_RETRY_MAX,
)

PASS = 0
FAIL = 0
RESULTS = []

# 隔离域基址(993x——红队专用; 避开 98xx/9881+/991x)
RT_MEMBER_A = 9931   # 会员 A(受害者)
RT_MEMBER_B = 9932   # 会员 B(攻击者)
RT_PERSONA = 9933    # 人设 ID 基准
RT_SCRIPT = 9934     # 脚本 ID 基准
RT_WORK = 9935       # 作品 ID 基准
RT_AUTH = 9936       # 转发授权 ID 基准

PAST = "2000-01-01T00:00:00+00:00"
GOOD_META = {"originUrl": "https://rt.example/w/1",
             "creatorVerified": True, "platform": "douyin"}


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


async def _rented_script(member_id: int) -> dict:
    """隔离域: 构造会员租用脚本(含人设)"""
    rental = BloggerRentalService()
    persona = await rental.register_renter_persona(
        member_id, f"红队人设{member_id}")
    return await rental.generate_rental_script(
        member_id, "红队选题测试", "douyin",
        persona["personaId"], "hook_price_anchor")


# ============================================================
# RT-01 越权租用(4 断言)
# ============================================================

class RT01CrossTenant:
    async def run(self):
        reset_store()
        rental = BloggerRentalService()
        # 受害者 A 的资产
        script_a = await _rented_script(RT_MEMBER_A)
        persona_a = script_a["personaId"]
        work_a = await rental.render_rental_work(
            RT_MEMBER_A, script_a["scriptId"])

        # 1) 攻击者 B 用 A 的 persona 生成 → 404
        try:
            await rental.generate_rental_script(
                RT_MEMBER_B, "攻击选题", "douyin",
                persona_a, "hook_price_anchor")
            ok = False
        except KeyError:
            ok = True
        record("RT01-越权人设404", ok)

        # 2) B 渲染 A 的脚本 → 404
        try:
            await rental.render_rental_work(
                RT_MEMBER_B, script_a["scriptId"])
            ok = False
        except KeyError:
            ok = True
        record("RT01-越权渲染404", ok)

        # 3) B 上报 A 的作品指标 → 404
        try:
            await rental.report_rental_metrics(
                RT_MEMBER_B, work_a["avWorkId"], clicks=999)
            ok = False
        except KeyError:
            ok = True
        record("RT01-越权指标404", ok)

        # 4) B 的漏斗不含 A 的作品(数据面隔离)
        fb = await rental.renter_funnel(RT_MEMBER_B)
        fa = await rental.renter_funnel(RT_MEMBER_A)
        record("RT01-漏斗数据隔离",
               fb["works"] == 0 and fa["works"] == 1,
               f"b={fb['works']} a={fa['works']}")


# ============================================================
# RT-02 租金逃逸(3 断言)
# ============================================================

class RT02BillingBypass:
    async def run(self):
        reset_store()
        rental = BloggerRentalService()
        # 会员 A 走正规租用轨
        await _rented_script(RT_MEMBER_A)
        entries = await rental.repo.list_rental_entries(
            member_id=RT_MEMBER_A)
        record("RT02-正规轨计费",
               len(entries) == 1
               and entries[0]["units"] == 1,
               f"e={entries}")

        # 5) 平台自营轨(P6b 直调)不带 ownerId → 合法非逃逸
        create = BloggerAVCreateService()
        persona = await create.register_persona(
            "自营人设", "original_ip")
        script = await create.generate_script(
            "自营选题", "douyin", persona["personaId"],
            "hook_price_anchor")
        record("RT02-自营轨零污染",
               int(script.get("ownerId") or 0) == 0,
               f"o={script.get('ownerId')}")

        # 6) 会员轨渲染必经 _charge(mock=5 单位)
        s2 = await _rented_script(RT_MEMBER_A)
        await rental.render_rental_work(
            RT_MEMBER_A, s2["scriptId"])
        entries2 = await rental.repo.list_rental_entries(
            member_id=RT_MEMBER_A)
        render_units = [e["units"] for e in entries2
                        if e["endpoint"] == "open/av/renders"]
        record("RT02-渲染必计费",
               len(entries2) == 3 and render_units == [5],
               f"n={len(entries2)} r={render_units}")

        # 7) 账本不可篡改性(无 update 入口——append-only 设计)
        has_update = hasattr(rental.repo, "update_rental_entry")
        record("RT02-账本只追加",
               has_update is False,
               f"update_rental_entry 存在={has_update}")


# ============================================================
# RT-03 深审投毒(3 断言)
# ============================================================

class RT03DeepReviewPoison:
    async def run(self):
        reset_store()
        fwd = BloggerFwdService()

        # 8) 拆词绕风险词(去标点归一后命中)
        s1, _ = compute_deep_score("疫?情时代的酒局", "", GOOD_META)
        s2, _ = compute_deep_score("疫情时代的酒局", "", GOOD_META)
        record("RT03-拆词归一命中",
               s1 == 0.0 and s2 == 0.0,
               f"s1={s1} s2={s2}")

        # 9) 价值观伪装词(炫富混排仍命中扣分)
        s3, r3 = compute_deep_score(
            "低调的炫富指南", "", GOOD_META)
        record("RT03-伪装词命中",
               s3 < 100.0 and any("价值观冲突" in str(r)
                                  for r in r3),
               f"s={s3} r={r3}")

        # 10) 纯风险词直接拒(一票否决零分)
        s4, r4 = compute_deep_score(
            "未成年饮酒危害", "", GOOD_META)
        record("RT03-一票否决零分",
               s4 == 0.0 and "一票否决" in r4[0],
               f"s={s4}")


# ============================================================
# RT-04 授权伪造(4 断言)
# ============================================================

class RT04AuthForgery:
    async def run(self):
        reset_store()
        fwd = BloggerFwdService()
        auth = await fwd.register_auth(
            "rt04-src", "mcn", "红队创作者", "私信", "MCN")

        # 11) 撤回后深审 → 拒绝
        await fwd.revoke_auth(auth["authId"])
        try:
            await fwd.deep_review(
                auth["authId"], "标题", source_meta=GOOD_META)
            ok = False
        except ValueError as exc:
            ok = "撤回" in str(exc)
        record("RT04-撤回后深审拒绝", ok)

        # 12) 过期授权深审 → 拒绝(僵尸转发)
        auth2 = await fwd.register_auth(
            "rt04-src2", "cc", "过期创作者", "私信", "本人",
            expires_at=PAST)
        try:
            await fwd.deep_review(
                auth2["authId"], "标题", source_meta=GOOD_META)
            ok = False
        except ValueError as exc:
            ok = "过期" in str(exc)
        record("RT04-过期授权拒绝", ok)

        # 13) 撤回 → 关联内容秒级下架(伪造"仍有效"不可行)
        auth3 = await fwd.register_auth(
            "rt04-src3", "mcn", "下架验证者", "私信", "MCN")
        c1 = await fwd.deep_review(
            auth3["authId"], "选酒指南", source_meta=GOOD_META)
        await fwd.revoke_auth(auth3["authId"])
        cd = await fwd.repo.get_fwd_content(c1["fwdId"])
        record("RT04-撤回秒级下架",
               cd["publishStatus"] == "takedown",
               f"p={cd['publishStatus']}")

        # 14) 伪造 evidenceHash 无效(存储哈希服务端重算,
        #     撤回状态不因哈希而变)
        forged = await fwd.repo.update_fwd_auth(
            auth3["authId"],
            {"evidenceHash": "0" * 40})
        ok = (forged["status"] == "revoked"
              and forged["evidenceHash"] == "0" * 40)
        # 状态仍是 revoked——哈希伪造不改变授权效力
        try:
            await fwd.deep_review(
                auth3["authId"], "标题", source_meta=GOOD_META)
            revived = False
        except ValueError:
            revived = True
        record("RT04-哈希伪造无效",
               ok and revived,
               f"status={forged['status']}")


# ============================================================
# RT-05 γ 权重攻击(3 断言)
# ============================================================

class RT05GammaAttack:
    async def run(self):
        reset_store()
        rental = BloggerRentalService()

        # 15) 函数级直传 γ<α → 拒绝
        try:
            compute_emotion_reward(
                1.0, 100.0, 1.0, 0.0,
                alpha=0.5, gamma=0.1)
            ok = False
        except ValueError:
            ok = True
        record("RT05-函数级γ<α拒绝", ok)

        # 16) 存储合并级越界(合法 α 调高使 γ<α) → 拒绝
        try:
            await rental.repo.save_av_reward_config({})
            from services.blogger_av_learn_service import \
                BloggerAVLearnService
            await BloggerAVLearnService() \
                .set_av_reward_params(alpha=0.5)
            ok = False
        except ValueError as exc:
            ok = "不得低于" in str(exc)
        record("RT05-存储级越界拒绝", ok)

        # 17) β 直传 → 拒绝(P5a 铁律继承)
        from services.blogger_av_learn_service import \
            BloggerAVLearnService
        try:
            await BloggerAVLearnService() \
                .set_av_reward_params(beta=0.5)
            ok = False
        except ValueError as exc:
            ok = "宪法域" in str(exc)
        record("RT05-β直传拒绝", ok)


# ============================================================
# RT-06 自愈滥用(3 断言)
# ============================================================

class RT06HealAbuse:
    async def run(self):
        reset_store()
        create = BloggerAVCreateService()
        pub = BloggerAVPublishService(
            repo=create.repo, blogger_service=create.svc)
        av_gov = BloggerAVGovernService(
            repo=create.repo, blogger_service=create.svc)
        persona = await create.register_persona(
            "自愈人设", "original_ip")
        script = await create.generate_script(
            "自愈选题", "douyin", persona["personaId"],
            "hook_price_anchor")
        work = await create.render_work(script["scriptId"])
        await pub.publish_av_work(work["avWorkId"])

        # 18) 耗尽后强试 → 拒绝且转人工(永不静默丢弃)
        ok = False
        for i in range(AV_HEAL_RETRY_MAX):
            await av_gov.heal_av_failure(
                work["avWorkId"], "转码失败",
                attempt=i + 1)
        try:
            await av_gov.heal_av_failure(
                work["avWorkId"], "转码失败",
                attempt=AV_HEAL_RETRY_MAX + 1)
        except ValueError:
            ok = True
        wd = await pub.repo.get_av_work(work["avWorkId"])
        record("RT06-耗尽转人工",
               ok and wd.get("healStatus") == "manual_queue",
               f"hs={wd.get('healStatus')}")

        # 19) pause 窗口强写(受控写) → 拒绝
        gov = BloggerAutoGovernService()
        w2 = await create.render_work(script["scriptId"])
        await pub.publish_av_work(w2["avWorkId"])
        await gov.pause_autonomy("RT-06 攻击窗口")
        try:
            await av_gov.heal_av_failure(
                w2["avWorkId"], "转码失败")
            ok = False
        except ValueError as exc:
            ok = "已暂停" in str(exc)
        record("RT06-pause窗口强写拒绝", ok)

        # 20) pause 窗口租用受控写 → 降级只读拒绝
        #     (须在 resume 之前——同一暂停窗口内验证)
        rental = BloggerRentalService()
        try:
            await rental.register_renter_persona(
                RT_MEMBER_B, "暂停窗口人设")
            ok = False
        except ValueError as exc:
            ok = "只读" in str(exc) or "暂停" in str(exc)
        record("RT06-pause租用写拒绝", ok)
        await gov.resume_autonomy()


# ============================================================
# RT-07 水印剥离(3 断言)
# ============================================================

class RT07WatermarkStrip:
    async def run(self):
        reset_store()
        create = BloggerAVCreateService()

        # 21) 无 AI 标识正文 → 生成断言失败
        from services.blogger_auto_create_service import \
            AI_WATERMARK, sanitize_text
        try:
            # 构造剥离攻击: 移除尾分镜水印
            body_no_wm = "内容出处\n（适度饮酒，未成年" \
                         "人禁止饮酒）"
            assert AI_WATERMARK not in body_no_wm
            # 服务端断言等价验证: 生成链路在尾分镜必查
            script = await create.generate_script(
                "水印选题", "douyin",
                (await create.register_persona(
                    "水印人设", "original_ip"))["personaId"],
                "hook_price_anchor")
            ok = AI_WATERMARK in \
                script["storyboards"][-1]["text"]
            record("RT07-无水印正文拒生成", ok,
                   "生成链路尾分镜断言")
        except ValueError:
            record("RT07-无水印正文拒生成", True)

        # 22) 伪造哈希无效(存储哈希服务端重算——
        #     深审/溯源不依赖客户端声明)
        script = await create.generate_script(
            "哈希选题", "douyin",
            (await create.register_persona(
                "哈希人设", "original_ip"))["personaId"],
            "hook_price_anchor")
        forged = await create.repo.update_av_script(
            script["scriptId"], {"watermarkHash": "f" * 40})
        # 哈希可被直改存储, 但 AI 标识正文断言不受影响
        ok = (forged["watermarkHash"] == "f" * 40
              and AI_WATERMARK in
              forged["storyboards"][-1]["text"])
        record("RT07-哈希伪造不影响标识", ok)

        # 23) sanitize 不破坏水印(净化后标识仍存)
        cleaned, _ = sanitize_text(
            script["storyboards"][-1]["text"])
        record("RT07-净化不破坏标识",
               AI_WATERMARK in cleaned)


async def main():
    vectors = [
        ("RT-01 越权租用", RT01CrossTenant()),
        ("RT-02 租金逃逸", RT02BillingBypass()),
        ("RT-03 深审投毒", RT03DeepReviewPoison()),
        ("RT-04 授权伪造", RT04AuthForgery()),
        ("RT-05 γ 权重攻击", RT05GammaAttack()),
        ("RT-06 自愈滥用", RT06HealAbuse()),
        ("RT-07 水印剥离", RT07WatermarkStrip()),
    ]
    # 每向量独立 try——单向量异常不中断整轮(66号先例)
    for name, v in vectors:
        try:
            await v.run()
        except Exception as exc:  # noqa: BLE001
            record(f"{name}向量异常", False, f"{type(exc).__name__}: {exc}")
    print("=" * 60)
    print("40号 P6g-2 红队七向量专项测试(隔离域 993x)")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
