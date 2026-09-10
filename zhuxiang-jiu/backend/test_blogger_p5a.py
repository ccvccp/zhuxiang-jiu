"""40号·平台流量DV博主模块·P5a 自主学习引擎专项测试

覆盖(设计文档《40号 P5 升级方案》§3):
    1. 三通道信号采集: 正向(attract 归因)/合规(三审分)/
       负向上报(举报/投诉/评论负面词密度/限流)/滚动采样不去重
    2. 信值对齐复合奖励: 公式分量/conversion 与 risk clamp/
       β 正贡献/冷启动回退/β 宪法域拒改/参数只读
    3. 微调研: 高置信拒绝/创建/单票即定/human_feedback 留痕/
       重复投票拒绝/pending 过滤
    4. 学习联动: 无信号拒绝/复合奖励回流(source: blogger_p5a)/
       consumed 幂等/学习轮触发语义

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p5a.py
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
from services.blogger_auto_learn_service import (
    BloggerAutoLearnService, negative_density,
    compute_conversion_efficiency, compute_risk_loss,
    compute_value_aligned_reward, REWARD_BETA,
    SIGNAL_CHANNEL_POSITIVE, SIGNAL_CHANNEL_NEGATIVE,
    SIGNAL_CHANNEL_COMPLIANCE,
    SIGNAL_KIND_CLICKS, SIGNAL_KIND_REGISTERED, SIGNAL_KIND_ORDERED,
    SIGNAL_KIND_COMPLIANCE_SCORE, SIGNAL_KIND_REPORTS,
    SIGNAL_KIND_COMPLAINTS, SIGNAL_KIND_NEGATIVE_SENTIMENT,
    SIGNAL_KIND_HUMAN_FEEDBACK,
)
from repositories.blogger_repository import (
    WORK_STATUS_AUTO_FOLLOW, FOLLOW_STATUS_PUBLISHED,
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


async def _publish_one() -> dict:
    """构造 1 条已发布跟随内容(scan→跟随→发布)"""
    import services.blogger_service as svc_mod
    svc_mod.BLOGGER_FOLLOW_COOLDOWN_HOURS = 0
    svc_mod.FOLLOW_GAP_HOURS = 0
    svc = BloggerService()
    result = await svc.scan()
    works = [d["work"] for d in result["decisions"]
             if d["work"]["status"] == WORK_STATUS_AUTO_FOLLOW]
    if not works:
        raise RuntimeError("Mock 扫描未产出 auto_follow 作品")
    follow = await svc.generate_follow(works[0]["workId"])
    await svc.publish_follow(follow["followId"], publish_at=PAST)
    await svc.process_publish_queue()
    published = await svc.repo.get_follow(follow["followId"])
    if published.get("status") != FOLLOW_STATUS_PUBLISHED:
        raise RuntimeError("跟随内容未进入 published 状态")
    return published


async def _mk_signals(service: BloggerAutoLearnService,
                      follow: dict) -> None:
    """手动构造一组正向+合规信号(可控复合奖励断言)"""
    for ch, kind, val in (
        (SIGNAL_CHANNEL_POSITIVE, SIGNAL_KIND_CLICKS, 10.0),
        (SIGNAL_CHANNEL_POSITIVE, SIGNAL_KIND_REGISTERED, 1.0),
        (SIGNAL_CHANNEL_POSITIVE, SIGNAL_KIND_ORDERED, 1.0),
        (SIGNAL_CHANNEL_COMPLIANCE,
         SIGNAL_KIND_COMPLIANCE_SCORE, 100.0),
    ):
        await service._put_signal(follow, ch, kind, val)


# ============================================================
# 1. 三通道信号采集(8 断言)
# ============================================================

class TestSignalCollect:
    async def run(self):
        reset_store()
        service = BloggerAutoLearnService()
        follow = await _publish_one()

        # 1) 单条采集: 正向3种 + 合规1种 = 4 条
        r = await service.collect_signals(follow_id=follow["followId"])
        record("采集-单条四信号(正3+合规1)",
               r["collected"] == 4 and r["targets"] == 1,
               f"collected={r['collected']}")

        # 2) 合规信号值 = 跟随内容三审分
        sigs = await service.repo.list_signals(
            follow_id=follow["followId"],
            channel=SIGNAL_CHANNEL_COMPLIANCE)
        record("采集-合规分直取三审",
               sigs and sigs[0]["value"]
               == float(follow.get("complianceScore") or 0),
               f"signals={len(sigs)}")

        # 3) 重复采集 = 多采样点(原始流不去重)
        await service.collect_signals(follow_id=follow["followId"])
        count = len(await service.repo.list_signals(
            follow_id=follow["followId"], limit=100))
        record("采集-滚动采样不去重",
               count == 8, f"count={count}")

        # 4) 非 published 内容采集 → ValueError
        follow["status"] = "approved"
        await service.repo.save_follow(follow)
        try:
            await service.collect_signals(
                follow_id=follow["followId"])
            ok = False
        except ValueError:
            ok = True
        record("采集-非已发布拒绝", ok)
        follow["status"] = FOLLOW_STATUS_PUBLISHED
        await service.repo.save_follow(follow)

        # 5) 不存在的内容 → KeyError
        try:
            await service.collect_signals(follow_id=99999)
            ok = False
        except KeyError:
            ok = True
        record("采集-不存在404语义", ok)

        # 6) 负向上报: 举报+投诉
        rn = await service.report_negative(
            follow["followId"], reports=5, complaints=2)
        neg = await service.repo.list_signals(
            follow_id=follow["followId"],
            channel=SIGNAL_CHANNEL_NEGATIVE)
        kinds = {s["kind"] for s in neg}
        record("负向-举报投诉信号",
               rn["created"] == 2
               and kinds == {SIGNAL_KIND_REPORTS,
                             SIGNAL_KIND_COMPLAINTS},
               f"kinds={kinds}")

        # 7) 负面评论密度 > 阈值 → sentiment 信号
        rn2 = await service.report_negative(
            follow["followId"],
            comments=["这个骗子平台", "垃圾广告别信",
                      "还行吧一般般", "挺有意思的"])
        sent = [s for s in await service.repo.list_signals(
            follow_id=follow["followId"],
            channel=SIGNAL_CHANNEL_NEGATIVE)
            if s["kind"] == SIGNAL_KIND_NEGATIVE_SENTIMENT]
        record("负向-评论负面词密度信号",
               rn2["sentimentDensity"] == 0.5
               and len(sent) == 1
               and sent[0]["value"] == 0.5,
               f"density={rn2['sentimentDensity']}")

        # 8) 干净评论 → 无 sentiment 信号
        rn3 = await service.report_negative(
            follow["followId"], comments=["很好喝", "已下单",
                                           "包装不错", "推荐"])
        record("负向-干净评论无信号",
               rn3["created"] == 0
               and rn3["sentimentDensity"] == 0.0,
               f"created={rn3['created']}")


# ============================================================
# 2. 信值对齐复合奖励(8 断言)
# ============================================================

class TestValueAlignedReward:
    async def run(self):
        # 9) 公式分量: α0.5×conv + β0.3×comp − γ0.2×risk
        r = compute_value_aligned_reward(0.8, 100.0, 0.0)
        record("奖励-公式数值",
               abs(r - (0.5 * 0.8 + 0.3 * 1.0)) < 1e-9,
               f"r={r}")

        # 10) conversion clamp 上限(注册转化远超基准 → 1.0)
        c = compute_conversion_efficiency(20, 10, 100)
        record("奖励-转化效率clamp",
               c == 1.0, f"c={c}")

        # 11) risk 举报率分量(举报率 2% 基准 → 满分)
        rk = compute_risk_loss(reports=2, clicks=100,
                               rate_limits=0, complaints=0)
        record("奖励-风险举报分量",
               abs(rk - 0.5) < 1e-9, f"rk={rk}")

        # 12) risk clamp(全部风险源满载 → 1.0)
        rk2 = compute_risk_loss(reports=100, clicks=100,
                                rate_limits=10, complaints=100)
        record("奖励-风险clamp",
               rk2 == 1.0, f"rk2={rk2}")

        # 13) β 正贡献: 高合规 vs 零合规差 ≈ 0.3
        r_hi = compute_value_aligned_reward(0.5, 100.0, 0.0)
        r_lo = compute_value_aligned_reward(0.5, 0.0, 0.0)
        record("奖励-合规正贡献(β=0.3)",
               abs((r_hi - r_lo) - 0.3) < 1e-9,
               f"hi={r_hi} lo={r_lo}")

        # 14) 冷启动回退: 零点击 → compute_reward 轨(-0.1)
        from services.blogger_service import compute_reward
        from repositories.blogger_repository import CLICK_P90_REF
        cold = compute_reward(0, 0.0, 1.0, CLICK_P90_REF)
        record("奖励-冷启动回退轨",
               cold == -0.1, f"cold={cold}")

        # 15) β 宪法域拒改
        service = BloggerAutoLearnService()
        try:
            await service.set_reward_params(beta=0.5)
            ok = False
        except ValueError as exc:
            ok = "宪法域" in str(exc)
        record("奖励-β宪法域拒改", ok)

        # 16) 参数只读视图(β 常量 + 域标注)
        cfg = await service.get_reward_config()
        record("奖励-config只读β",
               cfg["beta"] == REWARD_BETA == 0.3
               and cfg["betaDomain"] == [0.1, 1.0]
               and "宪法域" in cfg["betaNote"],
               f"cfg={cfg.get('beta')}")


# ============================================================
# 3. 微调研(6 断言)
# ============================================================

class TestPolls:
    async def run(self):
        reset_store()
        service = BloggerAutoLearnService()

        # 17) 高置信拒绝(≥0.8)
        try:
            await service.create_poll(
                "钩子风格", "选哪个?", ["A", "B", "C"], 0.85)
            ok = False
        except ValueError as exc:
            ok = "无需微调研" in str(exc)
        record("调研-高置信拒绝", ok)

        # 18) 低置信创建成功(open + votes 全 0)
        poll = await service.create_poll(
            "钩子风格", "学生党内容开头选哪个?",
            ["价格锚点", "场景种草", "情感陪伴"], 0.65)
        record("调研-低置信创建",
               poll["status"] == "open" and poll["answer"] == -1
               and poll["votes"] == {0: 0, 1: 0, 2: 0},
               f"poll={poll.get('status')}")

        # 19) 投票 → 单票即定(closed + answer + 计数)
        voted = await service.vote_poll(poll["pollId"], 1)
        record("调研-单票即定",
               voted["status"] == "closed"
               and voted["answer"] == 1
               and voted["votes"][1] == 1,
               f"votes={voted.get('votes')}")

        # 20) 投票生成 human_feedback 信号留痕
        hf = [s for s in await service.repo.list_signals(
            limit=100) if s["kind"] == SIGNAL_KIND_HUMAN_FEEDBACK]
        record("调研-human_feedback留痕",
               len(hf) == 1 and hf[0]["consumed"] is True
               and hf[0]["raw"].get("pollId") == poll["pollId"],
               f"len={len(hf)}")

        # 21) 已关闭重复投票 → ValueError
        try:
            await service.vote_poll(poll["pollId"], 0)
            ok = False
        except ValueError:
            ok = True
        record("调研-重复投票拒绝", ok)

        # 22) pending 只含 open
        p2 = await service.create_poll(
            "发布时段", "错峰选哪档?", ["早间", "晚间"], 0.7)
        pending = await service.list_pending_polls()
        record("调研-pending过滤",
               [p["pollId"] for p in pending] == [p2["pollId"]],
               f"pending={[p['pollId'] for p in pending]}")


# ============================================================
# 4. 学习联动(4 断言)
# ============================================================

class TestLearningLoop:
    async def run(self):
        reset_store()
        service = BloggerAutoLearnService()

        # 23) 无信号 → ValueError
        try:
            await service.run_learning()
            ok = False
        except ValueError as exc:
            ok = "无未消费信号" in str(exc)
        record("学习-无信号拒绝", ok)

        # 24) 信号消费 → 复合奖励回流(value_aligned 轨)
        follow = await _publish_one()
        await _mk_signals(service, follow)
        r = await service.run_learning()
        # conv=(1×0.4+1×0.6)/10÷0.05=2.0→clamp 1.0;
        # reward=0.5×1.0+0.3×1.0−0.2×0=0.8
        record("学习-复合奖励回流",
               r["submitted"] == 1
               and r["results"][0]["mode"] == "value_aligned"
               and r["results"][0]["reward"] == 0.8,
               f"r={r}")

        # 25) consumed 幂等(重复消费 → 无信号 ValueError)
        try:
            await service.run_learning()
            ok = False
        except ValueError:
            ok = True
        record("学习-consumed幂等", ok)

        # 26) 44号 feedback 入库(source: blogger_p5a)
        from repositories.ai_learning_repository import \
            AiLearningRepository
        fbs = await AiLearningRepository().list_feedback(
            "blogger_work_gate", limit=50)
        hit = [f for f in fbs
               if f.get("source") == "blogger_p5a"
               and "signal-fused" in (f.get("note") or "")]
        record("学习-44号回流留痕",
               len(hit) >= 1
               and hit[0].get("reward") == 0.8,
               f"hit={len(hit)}")


async def main():
    tests = [TestSignalCollect(), TestValueAlignedReward(),
             TestPolls(), TestLearningLoop()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P5a 自主学习引擎专项测试")
    print("=" * 60)
    print("\n".join(RESULTS))
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
