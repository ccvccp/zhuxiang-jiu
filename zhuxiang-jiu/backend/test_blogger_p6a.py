"""40号·平台流量DV博主模块·P6a 多模态自主学习引擎专项测试

覆盖(设计文档《40号 P6 升级方案》§3):
    1. 视听信号采集: 上传轨(完播/分享/收藏/弹幕情感/评论正面) /
       批量滚动轨 / 状态视图 / 404-409 语义
    2. 情感对齐奖励: 共鸣度归一 / 已知值 / γ≥α 宪法拒改(函数级+
       存储级) / β 拒改 / 合法调整 / clamp
    3. 调性迁移: 六平台参数表 / 无效平台拒绝 / 封禁元素联动
    4. 封禁元素库: 重复拒绝 / check 校验
    5. 学习联动: 冷启动回退 P5a / 情感模式回流(44号留痕) /
       consumed 幂等 / pause 拒绝

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p6a.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.blogger_service import BloggerService
from services.blogger_av_learn_service import (
    BloggerAVLearnService, AV_PLATFORMS, PLATFORM_TONE,
    compute_resonance, compute_emotion_reward,
    danmaku_negative_density, positive_density,
    AV_REWARD_ALPHA, AV_REWARD_GAMMA, AV_REWARD_DELTA,
    AV_SIGNAL_KIND_COMPLETION, AV_SIGNAL_KIND_SHARE,
    AV_SIGNAL_KIND_FAVORITE, AV_SIGNAL_KIND_COMMENT_POSITIVE,
    AV_SIGNAL_KIND_DANMAKU_NEGATIVE,
)
from services.blogger_auto_govern_service import (
    BloggerAutoGovernService,
)
from repositories.blogger_repository import (
    WORK_STATUS_AUTO_FOLLOW,
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
    global _AUTO_WORK_IDS
    _AUTO_WORK_IDS = []
    from repositories.store import reset_store as _reset
    _reset()


_AUTO_WORK_IDS: list[int] = []


async def _ensure_scanned():
    """首次扫描后缓存 auto_follow 作品 ID(二次 scan 无新作品)"""
    if _AUTO_WORK_IDS:
        return
    svc = BloggerService()
    result = await svc.scan()
    for d in result["decisions"]:
        if d["work"]["status"] == WORK_STATUS_AUTO_FOLLOW:
            _AUTO_WORK_IDS.append(d["work"]["workId"])


async def _publish_one() -> dict:
    """构造 1 条已发布跟随内容"""
    import services.blogger_service as svc_mod
    svc_mod.BLOGGER_FOLLOW_COOLDOWN_HOURS = 0
    svc_mod.FOLLOW_GAP_HOURS = 0
    await _ensure_scanned()
    if not _AUTO_WORK_IDS:
        raise RuntimeError("Mock 扫描未产出 auto_follow 作品")
    work_id = _AUTO_WORK_IDS.pop(0)
    svc = BloggerService()
    follow = await svc.generate_follow(work_id)
    await svc.publish_follow(follow["followId"], publish_at=PAST)
    await svc.process_publish_queue()
    return await svc.repo.get_follow(follow["followId"])


async def _generate_one() -> dict:
    """构造 1 条未发布跟随内容(状态校验用)"""
    await _ensure_scanned()
    if not _AUTO_WORK_IDS:
        raise RuntimeError("无剩余 auto_follow 作品")
    work_id = _AUTO_WORK_IDS.pop(0)
    return await BloggerService().generate_follow(work_id)


# ============================================================
# 1. 视听信号采集(8 断言)
# ============================================================

class TestAVSignals:
    async def run(self):
        reset_store()
        service = BloggerAVLearnService()
        follow = await _publish_one()
        fid = follow["followId"]

        # 1) 上传轨: 三率入库 + 三正向信号
        r = await service.report_av_metrics(
            fid, completion_rate=0.7, share_rate=0.06,
            favorite_rate=0.09)
        record("上报-三率入库",
               r["created"] == 3
               and r["avMetrics"]["completionRate"] == 0.7
               and r["avMetrics"]["samples"] == 1,
               f"r={r}")
        kinds = {s["kind"] for s in
                 await service.repo.list_signals(fid, limit=50)}
        record("上报-三率信号生成",
               kinds == {AV_SIGNAL_KIND_COMPLETION,
                         AV_SIGNAL_KIND_SHARE,
                         AV_SIGNAL_KIND_FAVORITE},
               f"kinds={kinds}")

        # 2) 弹幕负面密度 > 0.3 → 负向信号
        d = danmaku_negative_density(["无聊", "跳过", "好看", "推荐"])
        record("弹幕-负面密度计算", d == 0.5, f"d={d}")
        r2 = await service.report_av_metrics(
            fid, danmaku=["无聊", "跳过", "好看", "推荐"])
        neg = [s for s in await service.repo.list_signals(
            fid, channel="negative", limit=50)]
        record("弹幕-负向信号触发",
               len(neg) == 1
               and neg[0]["kind"] == AV_SIGNAL_KIND_DANMAKU_NEGATIVE,
               f"neg={len(neg)}")

        # 3) 评论正面密度 → 共鸣分量信号
        p = positive_density(["好看", "有用", "学到", "一般般"])
        record("评论-正面密度计算", p == 0.75, f"p={p}")
        r3 = await service.report_av_metrics(
            fid, comments=["好看", "有用", "学到", "一般般"])
        pos = [s for s in await service.repo.list_signals(
            fid, limit=100)
            if s["kind"] == AV_SIGNAL_KIND_COMMENT_POSITIVE]
        record("评论-正面信号生成", len(pos) == 1
               and pos[0]["value"] == 0.75,
               f"pos={len(pos)}")

        # 4) 404 语义(内容不存在)
        try:
            await service.report_av_metrics(99999,
                                            completion_rate=0.5)
            ok = False
        except KeyError:
            ok = True
        record("上报-404语义", ok)

        # 5) 状态校验(非已发布 → 409)
        pending = await _generate_one()
        try:
            await service.report_av_metrics(
                pending["followId"], completion_rate=0.5)
            ok = False
        except ValueError:
            ok = True
        record("上报-非发布409", ok)

        # 6) 指标越界(完播率 1.5 → 409)
        try:
            await service.report_av_metrics(
                fid, completion_rate=1.5)
            ok = False
        except ValueError:
            ok = True
        record("上报-指标越界409", ok)

        # 7) 批量滚动轨: avMetrics 重采样
        r4 = await service.collect_av_signals()
        record("采集-批量滚动重采样",
               r4["targets"] >= 1 and r4["collected"] >= 3,
               f"r={r4}")

        # 8) 状态视图(AV kind 计数 + 未消费)
        #    信号数 = report三率3 + 弹幕1 + 正面1 + collect重采样3 = 8
        st = await service.av_signals_status()
        record("视图-AV信号统计",
               st["byKind"][AV_SIGNAL_KIND_COMPLETION] == 2
               and st["byKind"][AV_SIGNAL_KIND_DANMAKU_NEGATIVE] == 1
               and st["unconsumed"] == 8,
               f"st={st}")


# ============================================================
# 2. 情感对齐奖励(8 断言)
# ============================================================

class TestEmotionReward:
    async def run(self):
        reset_store()
        service = BloggerAVLearnService()

        # 9) 共鸣度满分归一(完播0.6/分享0.05/收藏0.08+全正面)
        res = compute_resonance(0.6, 0.05, 0.08, 1.0)
        record("共鸣-满分归一", res == 1.0, f"res={res}")

        # 10) 共鸣度零值
        res0 = compute_resonance(0, 0, 0, 0)
        record("共鸣-零值", res0 == 0.0, f"res={res0}")

        # 11) 情感奖励已知值(conv=1, comp=100, res=1, risk=0)
        #     0.3×1 + 0.3×1 + 0.3×1 − 0.1×0 = 0.9
        rw = compute_emotion_reward(1.0, 100.0, 1.0, 0.0)
        record("奖励-已知值", rw == 0.9, f"rw={rw}")

        # 12) γ<α 拒改(函数级宪法域)
        try:
            compute_emotion_reward(1.0, 100.0, 1.0, 0.0,
                                   alpha=0.5, gamma=0.2)
            ok = False
        except ValueError as exc:
            ok = "不得低于" in str(exc)
        record("奖励-γ<α函数级拒绝", ok)

        # 13) β 拒改(服务级, 继承 P5a)
        try:
            await service.set_av_reward_params(beta=0.5)
            ok = False
        except ValueError as exc:
            ok = "宪法域" in str(exc)
        record("奖励-β拒改", ok)

        # 14) γ<α 拒改(存储合并级)
        try:
            await service.set_av_reward_params(gamma=0.1)
            ok = False
        except ValueError as exc:
            ok = "不得低于" in str(exc)
        record("奖励-γ<α存储级拒绝", ok)

        # 15) 合法调整(α 降档 → γ≥α 仍成立)
        cfg = await service.set_av_reward_params(
            alpha=0.2, delta=0.15)
        record("奖励-合法调整",
               cfg["alpha"] == 0.2 and cfg["delta"] == 0.15
               and cfg["gamma"] == AV_REWARD_GAMMA,
               f"cfg={cfg}")

        # 16) clamp 下界(conv=0, comp=0, res=0, risk=1 → −δ)
        rw2 = compute_emotion_reward(0.0, 0.0, 0.0, 1.0)
        record("奖励-clamp下界",
               rw2 == round(-AV_REWARD_DELTA, 4), f"rw2={rw2}")

        # 17) 默认参数 γ≥α(宪法域出厂态)
        record("奖励-默认γ≥α", AV_REWARD_GAMMA >= AV_REWARD_ALPHA)


# ============================================================
# 3. 调性迁移 + 封禁元素库(7 断言)
# ============================================================

class TestToneAndBanned:
    async def run(self):
        reset_store()
        service = BloggerAVLearnService()

        # 18) 抖音快节奏
        t1 = await service.get_tone("douyin")
        record("调性-抖音快节奏",
               t1["tone"]["rhythm"] == "fast"
               and t1["tone"]["density"] == "high"
               and t1["tone"]["duration"] == [15, 45],
               f"t={t1['tone']}")

        # 19) 小宇宙播客沉浸叙事
        t2 = await service.get_tone("xiaoyuzhou")
        record("调性-小宇宙播客",
               t2["tone"]["rhythm"] == "immersive"
               and t2["tone"]["form"] == "podcast"
               and t2["tone"]["duration"] == [600, 1800],
               f"t={t2['tone']}")

        # 20) B站中视频深度解说
        t3 = await service.get_tone("bilibili")
        record("调性-B站中视频",
               t3["tone"]["rhythm"] == "deep"
               and t3["tone"]["form"] == "mid_video",
               f"t={t3['tone']}")

        # 21) 无效平台拒绝
        try:
            await service.get_tone("tiktok")
            ok = False
        except ValueError:
            ok = True
        record("调性-无效平台拒绝", ok)

        # 22) 六平台全覆盖(调性表 = AV 平台集)
        record("调性-六平台全覆盖",
               set(PLATFORM_TONE.keys()) == set(AV_PLATFORMS)
               and len(AV_PLATFORMS) == 6,
               f"platforms={AV_PLATFORMS}")

        # 23) 封禁元素: 全平台 + 平台增量 → 调性视图联动
        await service.add_banned_element(
            "bgm", "危险夜曲", platform="")
        await service.add_banned_element(
            "bgm", "抖音限定BGM", platform="douyin")
        t4 = await service.get_tone("douyin")
        t5 = await service.get_tone("bilibili")
        record("封禁-调性视图联动",
               len(t4["bannedElements"]) == 2
               and len(t5["bannedElements"]) == 1,
               f"douyin={len(t4['bannedElements'])} "
               f"bili={len(t5['bannedElements'])}")

        # 24) 重复登记拒绝 + check 校验
        try:
            await service.add_banned_element(
                "bgm", "危险夜曲", platform="")
            ok = False
        except ValueError:
            ok = True
        record("封禁-重复登记拒绝", ok)
        hit = await service.check_banned("bgm", "危险夜曲",
                                         "bilibili")
        miss = await service.check_banned("bgm", "不存在曲",
                                          "douyin")
        record("封禁-check校验", hit is True and miss is False,
               f"hit={hit} miss={miss}")


# ============================================================
# 4. 学习联动(5 断言)
# ============================================================

class TestLearningLoop:
    async def run(self):
        reset_store()
        service = BloggerAVLearnService()

        # 25) 无信号 → ValueError
        try:
            await service.run_av_learning()
            ok = False
        except ValueError as exc:
            ok = "无未消费信号" in str(exc)
        record("学习-无信号拒绝", ok)

        # 26) 冷启动回退(完播样本 1 < 3 → P5a 信值对齐)
        follow = await _publish_one()
        comp = float(follow.get("complianceScore") or 0)
        await service.report_av_metrics(
            follow["followId"], completion_rate=0.7)
        r = await service.run_av_learning()
        expected = round(0.3 * comp / 100, 4)   # P5a: α0.5×0+β0.3×comp
        record("学习-冷启动回退",
               r["submitted"] == 1
               and r["results"][0]["mode"] == "cold_start"
               and r["results"][0]["reward"] == expected,
               f"r={r} expected={expected}")

        # 27) 情感模式回流(三次上报 → 样本≥3 → 情感对齐奖励)
        await service.report_av_metrics(
            follow["followId"], completion_rate=0.7,
            share_rate=0.06, favorite_rate=0.09,
            comments=["好看", "有用", "学到", "一般般"])
        await service.report_av_metrics(
            follow["followId"], completion_rate=0.72,
            share_rate=0.07, favorite_rate=0.1)
        await service.report_av_metrics(
            follow["followId"], completion_rate=0.75,
            share_rate=0.08, favorite_rate=0.11)
        r2 = await service.run_av_learning()
        # conv=0(无点击信号); res=0.4+0.3+0.2+0.1×0.75=0.975;
        # reward=0.3×0+0.3×comp/100+0.3×0.975−0.1×0
        expected2 = round(
            0.3 * comp / 100 + 0.3 * 0.975, 4)
        record("学习-情感模式回流",
               r2["results"][0]["mode"] == "emotion_aligned"
               and r2["results"][0]["reward"] == expected2,
               f"r2={r2['results']} expected={expected2}")

        # 28) 44号 feedback 留痕(source: blogger_p6a)
        from repositories.ai_learning_repository import \
            AiLearningRepository
        fbs = await AiLearningRepository().list_feedback(
            "blogger_work_gate", limit=100)
        hit = [f for f in fbs
               if f.get("source") == "blogger_p6a"
               and "av-fused" in (f.get("note") or "")]
        record("学习-44号回流留痕",
               len(hit) == 2
               and expected2 in [f.get("reward") for f in hit],
               f"hit={len(hit)}")

        # 29) consumed 幂等(重复学习 → 无信号 409)
        try:
            await service.run_av_learning()
            ok = False
        except ValueError:
            ok = True
        record("学习-consumed幂等", ok)

        # 30) pause 后学习轮拒绝(仲裁优先于调度)
        gov = BloggerAutoGovernService()
        await service.report_av_metrics(
            follow["followId"], completion_rate=0.8)
        await gov.pause_autonomy("P6a 测试暂停")
        try:
            await service.run_av_learning()
            ok = False
        except ValueError as exc:
            ok = "已暂停" in str(exc)
        record("学习-pause拒绝", ok)
        await gov.resume_autonomy()


async def main():
    tests = [TestAVSignals(), TestEmotionReward(),
             TestToneAndBanned(), TestLearningLoop()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P6a 多模态自主学习引擎专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
