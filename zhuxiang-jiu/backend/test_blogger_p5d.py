"""40号·平台流量DV博主模块·P5d 自治理与进化层专项测试

覆盖(设计文档《40号 P5 升级方案》§6):
    1. 异常自愈: 失败词表归因(限流/下架/受限/瞬时/未知)/
       重试档位/耗尽转人工队列/自愈审计留痕
    2. 透明度看板: 自治开关/漏斗归因/策略排行/干预史/自愈流水
    3. 决策可解释: 规则链路回放(评分快照/合规闸门/审计流水)
    4. 人类干预: pause 最高优先级(学习/固化/低预算推广全拒)/
       理由必填/幂等叠加/resume 显式/快照回滚(缺失拒绝)/
       规则注入留痕

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p5d.py
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

from services.blogger_service import BloggerService
from services.blogger_auto_govern_service import (
    BloggerAutoGovernService, HEAL_RETRY_MAX, SNAPSHOT_KEEP,
    INTERVENTION_PAUSE, INTERVENTION_RESUME,
    INTERVENTION_ROLLBACK, INTERVENTION_INJECT,
)
from services.blogger_auto_learn_service import (
    BloggerAutoLearnService,
)
from services.blogger_auto_create_service import (
    BloggerAutoCreateService,
)
from services.blogger_auto_publish_service import (
    BloggerAutoPublishService, BOOST_EXECUTED,
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
    """构造 1 条已发布跟随内容"""
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
    return await svc.repo.get_follow(follow["followId"])


# ============================================================
# 1. 异常自愈(7 断言)
# ============================================================

class TestAutoHeal:
    async def run(self):
        reset_store()
        svc = BloggerAutoGovernService()
        follow = await _publish_one()

        # 1) 限流归因 → 换号
        r = await svc.heal_failure(
            follow["followId"], "rate limit exceeded")
        record("自愈-限流换号",
               r["category"] == "rate_limit"
               and r["action"]["kind"] == "switch_account",
               f"c={r['category']}")

        # 2) 内容下架归因 → 人工队列
        r2 = await svc.heal_failure(
            follow["followId"], "content removed by platform")
        record("自愈-下架人工队列",
               r2["category"] == "content_takedown"
               and r2["action"]["kind"] == "manual_queue")

        # 3) 账号受限归因 → cooling
        r3 = await svc.heal_failure(
            follow["followId"], "account banned for violation")
        record("自愈-受限cooling",
               r3["category"] == "account_restricted"
               and r3["action"]["kind"] == "account_cooling")

        # 4) 瞬时失败 → 重试档位(1/3)
        r4 = await svc.heal_failure(
            follow["followId"], "network timeout", attempt=1)
        record("自愈-瞬时重试档位",
               r4["category"] == "transient"
               and r4["action"]["kind"] == "retry"
               and r4["attempt"] == 1)

        # 5) 重试耗尽 → 人工队列(永不静默丢弃)
        r5 = await svc.heal_failure(
            follow["followId"], "network timeout",
            attempt=HEAL_RETRY_MAX + 1)
        record("自愈-耗尽转人工",
               r5["action"]["kind"] == "manual_queue")

        # 6) 未知错误 → 重试轨(兜底)
        r6 = await svc.heal_failure(
            follow["followId"], "some weird error", attempt=1)
        record("自愈-未知重试兜底",
               r6["category"] == "unknown"
               and r6["action"]["kind"] == "retry")

        # 7) 自愈审计留痕(blogger_audits auto_heal 动作)
        heals = [a for a in await svc.repo.list_audits(limit=100)
                 if a.get("action") == "auto_heal"]
        record("自愈-审计留痕", len(heals) >= 6,
               f"n={len(heals)}")


# ============================================================
# 2. 透明度看板 + 决策可解释(4 断言)
# ============================================================

class TestDashboard:
    async def run(self):
        # 8) 看板四区聚合
        reset_store()
        svc = BloggerAutoGovernService()
        follow = await _publish_one()
        d = await svc.evolution_dashboard()
        record("看板-四区聚合",
               all(k in d for k in
                   ("autonomy", "funnel", "strategies",
                    "interventions", "heals"))
               and d["funnel"]["published"] == 1
               and d["autonomy"]["paused"] is False,
               f"keys={list(d.keys())}")

        # 9) 决策可解释: 链路含评分快照 + 合规闸门
        ex = await svc.decision_explain(follow["followId"])
        steps = [c["step"] for c in ex["chain"]]
        record("可解释-规则链路回放",
               "work_decision" in steps
               and "compliance_gate" in steps
               and any("评分" in c["explain"]
                       or "主导因子" in c["explain"]
                       for c in ex["chain"]),
               f"steps={steps}")

        # 10) 可解释 404 语义
        try:
            await svc.decision_explain(99999)
            ok = False
        except KeyError:
            ok = True
        record("可解释-404语义", ok)

        # 11) 看板自愈流水区(auto_heal 审计聚合)
        await svc.heal_failure(follow["followId"], "rate limit")
        d2 = await svc.evolution_dashboard()
        record("看板-自愈流水聚合", len(d2["heals"]) >= 1)


# ============================================================
# 3. 人类干预通道(11 断言)
# ============================================================

class TestIntervention:
    async def run(self):
        reset_store()
        gov = BloggerAutoGovernService()
        learn = BloggerAutoLearnService()
        create = BloggerAutoCreateService()
        pub = BloggerAutoPublishService()
        follow = await _publish_one()

        # 12) pause 理由必填
        try:
            await gov.pause_autonomy("  ")
            ok = False
        except ValueError:
            ok = True
        record("干预-pause理由必填", ok)

        # 13) pause 前学习轮可跑(无信号 ValueError ≠ pause 拒绝)
        try:
            await learn.run_learning()
            msg = ""
        except ValueError as exc:
            msg = str(exc)
        record("干预-暂停前学习可跑",
               "无未消费信号" in msg, f"msg={msg}")

        # 14) pause 即时生效(状态 + 快照抓取)
        pr = await gov.pause_autonomy("红队演练冻结")
        state = await gov.repo.get_autonomy_state()
        snapshots = state.get("strategySnapshots") or {}
        record("干预-pause即时生效",
               pr["state"]["paused"] is True
               and state["paused"] is True
               and "红队演练冻结" in state["reason"]
               and len(snapshots) == 1,
               f"snaps={len(snapshots)}")

        # 15) pause 后学习轮被拒(最高优先级)
        try:
            await learn.run_learning()
            ok = False
        except ValueError as exc:
            ok = "暂停" in str(exc) and "resume" in str(exc)
        record("干预-暂停拒学习轮", ok)

        # 16) pause 后实验固化被拒
        r = await create.generate_versions("测试选题", "student")
        for v in r["versions"]:
            await create.record_version_metrics(
                v["versionId"], clicks=100, registered=5)
        try:
            await create.promote_experiment(
                r["experiment"]["experimentId"])
            ok = False
        except ValueError as exc:
            ok = "暂停" in str(exc)
        record("干预-暂停拒实验固化", ok)

        # 17) pause 后低预算自动推广被拒
        try:
            await pub.execute_boost(follow["followId"], 50.0)
            ok = False
        except ValueError as exc:
            ok = "暂停" in str(exc)
        record("干预-暂停拒低预算推广", ok)

        # 18) pause 幂等(理由叠加) + 干预留痕(evidenceHash)
        pr2 = await gov.pause_autonomy("追加理由")
        rec = pr2["intervention"]
        record("干预-pause幂等叠加",
               pr2["state"]["paused"] is True
               and "追加理由" in pr2["state"]["reason"]
               and bool(rec.get("evidenceHash")),
               f"hash={rec.get('evidenceHash', '')[:8]}")

        # 19) resume 显式(当前暂停态 → 成功; 再 resume → 拒绝)
        rr = await gov.resume_autonomy()
        try:
            await gov.resume_autonomy()
            re_reject = False
        except ValueError:
            re_reject = True
        record("干预-resume显式",
               rr["state"]["paused"] is False and re_reject)

        # 20) rollback 快照缺失拒绝(清空快照态)
        state = await gov.repo.get_autonomy_state()
        state["strategySnapshots"] = {}
        await gov.repo.save_autonomy_state(state)
        try:
            await gov.rollback_strategies()
            ok = False
        except ValueError as exc:
            ok = "无策略库快照" in str(exc) or "编造" in str(exc)
        record("干预-回滚无快照拒绝", ok)

        # 21) rollback 指定快照不存在拒绝 → pause 抓快照 → 回滚成功
        try:
            await gov.rollback_strategies(snapshot_id=99)
            ok = False
        except ValueError:
            ok = True
        record("干预-回滚快照不存在拒绝", ok)
        # pause 抓快照后回滚(恢复策略计数)
        await gov.pause_autonomy("回滚前快照")
        s2 = await gov.repo.get_autonomy_state()
        snap_ids = list((s2.get("strategySnapshots") or {}).keys())
        rb = await gov.rollback_strategies(
            snapshot_id=int(snap_ids[0]))
        record("干预-回滚应用快照",
               rb["restored"] >= 0
               and rb["snapshot"]["takenAt"] != "",
               f"restored={rb['restored']}")
        await gov.resume_autonomy()

        # 22) 规则注入留痕 + 非法结构拒绝
        inj = await gov.inject_rule(
            {"type": "hook_blacklist",
             "value": ["hook_price_anchor"]})
        try:
            await gov.inject_rule({"value": [1]})
            ok = False
        except ValueError:
            ok = True
        record("干预-规则注入留痕",
               inj["intervention"]["kind"] == INTERVENTION_INJECT
               and inj["intervention"]["payload"]["rule"]["type"]
               == "hook_blacklist" and ok,
               f"inj={inj.get('intervention', {}).get('kind')}")


async def main():
    tests = [TestAutoHeal(), TestDashboard(), TestIntervention()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P5d 自治理与进化层专项测试")
    print("=" * 60)
    print("\n".join(RESULTS))
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
