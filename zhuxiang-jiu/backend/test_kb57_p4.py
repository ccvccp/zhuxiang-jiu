"""57号·AI智能知识库模块 P4 专项测试
(决策回流+信值联动+T+1 调度)

运行方式:
    python test_kb57_p4.py

覆盖(57号计划 §十一 P4):
    - 决策回流: 六类信号+44号池双写+幂等
    - 误导召回补偿: 45号 L2 deposit
    - T+1 调度器(默认 off)
    - HTTP 层: 2 端点+鉴权+23 端点计数
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ.pop("KB57_LEARN_MODE", None)
os.environ.pop("KB57_LEARN_INTERVAL", None)

PASS = 0
FAIL = 0
RESULTS = []

MEMBER = 7001


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def make_seed(status: str = "published",
                    views: int = 0,
                    positive: int = 0,
                    negative: int = 0,
                    **overrides) -> int:
    """直建最小种子(信号判定测试用)"""
    import hashlib
    from core.helpers import ts
    from repositories.kb57_repository import (
        Kb57Repository,
    )
    repo = Kb57Repository()
    gap_id = await repo.next_gap_id()
    await repo.save_gap({
        "gapId": gap_id, "status": "resolved",
        "priority": "medium", "topic": "p4-helper",
        "signalSnapshot": {"hits": []},
        "suggestedSources": [],
        "budgetCap": 0.1, "budgetSpent": 0.0,
        "createdAt": ts(), "updatedAt": ts(),
    })
    seed_id = await repo.next_seed_id()
    record_ = {
        "seedId": seed_id, "seedVersion": 1,
        "type": "text", "title": "p4-seed",
        "content": {"text": "c", "mediaRef": None,
                    "transcript": None,
                    "keyframes": None, "alt": None},
        "contentHash": "sha256:x",
        "complianceFingerprint":
            "sha256:" + hashlib.sha256(
                f"p4-{seed_id}".encode(
                    "utf-8")).hexdigest()[:32],
        "valueTags": ["policy"],
        "sourceId": "gov_policy_official",
        "sourceCredibility": 0.95,
        "privacyCost": 0.002,
        "knowledgeReason": "p4",
        "humanVerified": True,
        "validUntil": "2099-01-01",
        "abTest": {"active": False,
                   "variantOf": None},
        "status": status,
        "gapId": gap_id, "resourceId": 0,
        "viewCount": views,
        "positiveCount": positive,
        "negativeCount": negative,
        "pooledFeedbackId": 0,
        "llmCalls": 0,
        "createdAt": ts(), "updatedAt": ts(),
    }
    record_.update(overrides)
    await repo.save_seed(record_)
    return seed_id


class TestFeedbackLoop:
    """01 决策回流(六类信号+44号池双写)"""

    async def run(self):
        print("[01 决策回流]")
        reset_all()
        from services.kb57_feedback_loop_service import (
            Kb57FeedbackLoopService,
            SIGNAL_REWARDS,
        )
        loop = Kb57FeedbackLoopService()

        # 六类信号场景种数
        s1 = await make_seed(     # 有效使用
            views=5, positive=1, negative=0)
        s2 = await make_seed(     # 高价值
            views=5, positive=4, negative=1)
        s3 = await make_seed(     # 弱满足
            views=5, positive=1, negative=3)
        s4 = await make_seed(     # 召回
            status="recalled", views=3)
        s5 = await make_seed(     # 无信号(少浏览)
            views=1)
        s6 = await make_seed(     # 无信号(rejected)
            status="rejected")

        # 缺口复发域
        from core.helpers import ts as _ts
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        repo = Kb57Repository()
        gap_id = await repo.next_gap_id()
        await repo.save_gap({
            "gapId": gap_id, "status": "resolved",
            "priority": "high", "topic": "p4-rec",
            "signalSnapshot": {
                "hits": [{"signalId":
                          "kb_gap_open"}]},
            "suggestedSources": [],
            "budgetCap": 0.1, "budgetSpent": 0.0,
            "createdAt": _ts(),
            "updatedAt": _ts(),
        })

        r = await loop.collect_feedback()
        record("回流扫描(6 种子)",
               r.get("scanned") == 6,
               str(r.get("scanned")))
        signals = r.get("signals") or {}
        expect = {
            "seed_effective": 1,
            "seed_high_value": 1,
            "seed_weak": 1,
            "seed_recalled": 1,
            "gap_reject_recurrence": 1,
        }
        record("五类信号命中(缺口复发含)",
               signals == expect,
               str(signals))
        record("标注 5+跳过 2(无信号+种子5)",
               r.get("labeled") == 5
               and r.get("skipped") == 2,
               str((r.get("labeled"),
                    r.get("skipped"))))

        # reward 口径
        record("reward 口径(±六值)",
               SIGNAL_REWARDS == {
                   "seed_effective": 1.0,
                   "seed_high_value": 0.8,
                   "seed_weak": 0.3,
                   "gap_reject_recurrence": -0.6,
                   "seed_recalled": -0.8,
                   "compliance_overturned": -0.5,
                   "budget_halt_frequent": -0.4},
               str(SIGNAL_REWARDS))

        # 44号池双写核对
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        pool = await AiLearningRepository(
        ).list_feedback("knowledge_orchestration")
        record("44号池双写(5 条)",
               len(pool) == 5,
               str(len(pool)))
        rewards_in_pool = sorted(
            float(x.get("reward") or 0)
            for x in pool)
        record("池内 reward 谱系(五值)",
               rewards_in_pool == sorted(
                   [1.0, 0.8, 0.3, -0.6, -0.8]),
               str(rewards_in_pool))
        record("池记录来源(kb57_pipeline)",
               all(x.get("source") == "kb57_pipeline"
                    for x in pool),
               str({x.get("source")
                    for x in pool}))

        # 种子回写(pooledFeedbackId+信号+奖励)
        stored = await repo.get_seed(s4)
        record("种子回写(pooled+信号+奖励)",
               int(stored.get("pooledFeedbackId")
                   or 0) > 0
               and stored.get("poolSignal")
               == "seed_recalled"
               and stored.get("poolReward") == -0.8,
               str((stored.get("pooledFeedbackId"),
                    stored.get("poolSignal"),
                    stored.get("poolReward"))))

        # learn_signal 事件留痕
        events = await repo.list_events(limit=100)
        learn_evs = [e for e in events
                     if e.get("eventType")
                     == "learn_signal"]
        record("learn_signal 事件留痕(5 次)",
               len(learn_evs) == 5,
               str(len(learn_evs)))

        # 幂等(重复 collect 跳过)
        r2 = await loop.collect_feedback()
        record("幂等(重复补标跳过)",
               r2.get("labeled") == 0
               and r2.get("skipped") == 6,
               str((r2.get("labeled"),
                    r2.get("skipped"))))
        pool2 = await AiLearningRepository(
        ).list_feedback("knowledge_orchestration")
        record("幂等(池不重复)",
               len(pool2) == 5,
               str(len(pool2)))

        # 缺口复发幂等(gapRecurrence 标记)
        gap_stored = await repo.get_gap(gap_id)
        record("缺口复发标记(gapRecurrence)",
               gap_stored.get("gapRecurrence")
               is True,
               str(gap_stored.get(
                   "gapRecurrence")))

        # 统计观测面
        stats = await loop.feedback_stats()
        record("回流统计(分布+池提交)",
               (stats.get("bySignal") or {})
               .get("seed_recalled") == 1
               and stats.get("poolSubmitted") == 5,
               str((stats.get("bySignal"),
                    stats.get(
                        "poolSubmitted"))))


class TestCompensate:
    """02 误导召回补偿(45号 L2)"""

    async def run(self):
        print("[02 召回补偿]")
        reset_all()

        from services.kb57_feedback_loop_service import (
            Kb57FeedbackLoopService,
        )
        loop = Kb57FeedbackLoopService()

        # 45号档案(补偿对象)
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        member = await TrustProfileService().create_role(
            "person", "KB57-P4-MEMBER",
            "ID-KB57-P4-COMP")
        member_id = member["trustId"]

        # 补偿执行器(成功 1+跳过 1)
        r = await loop.compensate_recall(
            [member_id, 99999], seed_id=1,
            reason="内容误导风险")
        record("补偿(成功 1+失败 1)",
               r.get("attempted") == 2
               and r.get("compensated") == 1,
               str((r.get("attempted"),
                    r.get("compensated"))))

        # 45号 L2 存证留痕
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        events45 = await \
            TrustValue45Repository(
            ).list_events_by_trust(member_id)
        dep = [e for e in events45
               if e.get("layer") == "L2"
               and str(e.get("factor") or "")
               == "platform_conduct"]
        record("45号 L2 存证(platform_conduct)",
               len(dep) >= 1,
               str(len(dep)))

        # recall_compensate 事件留痕
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        events = await Kb57Repository().list_events(
            limit=50)
        comp_evs = [e for e in events
                    if e.get("eventType")
                    == "recall_compensate"]
        record("recall_compensate 事件留痕",
               len(comp_evs) == 1
               and (comp_evs[0].get("detail")
                    or {}).get("compensated") == 1,
               str(len(comp_evs)))


class TestRecallCompensationLink:
    """03 recall 联动补偿(P2 预留接口激活)"""

    async def run(self):
        print("[03 recall 补偿联动]")
        reset_all()
        os.environ["KB57_MODE"] = "assist"

        # 45号档案
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        member = await TrustProfileService().create_role(
            "person", "KB57-P4-MEMBER-2",
            "ID-KB57-P4-LINK")
        member_id = member["trustId"]

        # 发布种子→召回(带受影响用户)
        sid = await make_seed(views=2)
        from services.kb57_review_service import (
            Kb57ReviewService,
        )
        r = await Kb57ReviewService().recall(
            sid, reason="误导风险",
            affected_members=[member_id, 88888])
        comp = r.get("compensation") or {}
        record("recall 联动补偿(1 成功)",
               r.get("status") == "recalled"
               and comp.get("attempted") == 2
               and comp.get("compensated") == 1,
               str((comp.get("attempted"),
                    comp.get("compensated"))))

        # 45号 L2 存证
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        events45 = await \
            TrustValue45Repository(
            ).list_events_by_trust(member_id)
        dep = [e for e in events45
               if e.get("layer") == "L2"
               and str(e.get("factor") or "")
               == "platform_conduct"]
        record("联动 45号 L2 存证",
               len(dep) >= 1,
               str(len(dep)))
        os.environ["KB57_MODE"] = "off"


class TestScheduler:
    """04 T+1 调度器"""

    async def run(self):
        print("[04 调度器]")
        reset_all()
        from services.kb57_scheduler import (
            scheduler_enabled,
            scheduler_interval_seconds,
            run_scheduled_tasks,
        )

        # 默认 off(零影响铁律)
        os.environ.pop("KB57_LEARN_MODE", None)
        record("默认关闭(零影响)",
               scheduler_enabled() is False,
               str(scheduler_enabled()))

        os.environ["KB57_LEARN_MODE"] = "on"
        record("显式开启(on)",
               scheduler_enabled() is True,
               str(scheduler_enabled()))
        os.environ.pop("KB57_LEARN_MODE", None)

        record("周期默认 24h",
               scheduler_interval_seconds() == 86400,
               str(scheduler_interval_seconds()))
        os.environ["KB57_LEARN_INTERVAL"] = "10"
        record("周期下限 5 分钟",
               scheduler_interval_seconds() == 300,
               str(scheduler_interval_seconds()))
        os.environ.pop("KB57_LEARN_INTERVAL", None)

        # 独立执行一轮(含 fresh 种子→无过期)
        await make_seed(views=3, positive=2)
        result = await run_scheduled_tasks()
        collect = result.get("collect") or {}
        record("独立执行(补标 1 条)",
               collect.get("labeled") == 1,
               str(collect))
        fresh = result.get("freshness") or {}
        record("独立执行(有效期检查)",
               fresh.get("scanned") == 1
               and fresh.get("demoted") == 0,
               str(fresh))

        # scheduler_run 事件留痕
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        events = await Kb57Repository().list_events(
            limit=50)
        sched_evs = [e for e in events
                     if e.get("eventType")
                     == "scheduler_run"]
        record("scheduler_run 事件留痕",
               len(sched_evs) == 1,
               str(len(sched_evs)))


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 种 recalled 种子
        sid = await make_seed(status="recalled",
                              views=3)

        # feedback/collect 200(不受开关影响)
        os.environ["KB57_MODE"] = "off"
        resp = client.post(
            "/api/kb57/feedback/collect",
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect 200(off 亦可用)",
               resp.status_code == 200
               and body.get("labeled") == 1,
               str((resp.status_code,
                    body.get("labeled"))))

        # feedback/stats 200(观测面)
        resp = client.get(
            "/api/kb57/feedback/stats",
            headers=admin)
        body = resp.json() or {}
        record("HTTP stats 观测面",
               resp.status_code == 200
               and (body.get("bySignal") or {})
               .get("seed_recalled") == 1
               and body.get("poolSubmitted") == 1,
               str((resp.status_code,
                    body.get("bySignal"))))

        # 幂等(HTTP 重复 collect)
        resp = client.post(
            "/api/kb57/feedback/collect",
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect 幂等(0 标注)",
               resp.status_code == 200
               and body.get("labeled") == 0,
               str(body.get("labeled")))

        # 鉴权 403
        for method, path in (
                ("POST",
                 "/api/kb57/feedback/collect"),
                ("GET",
                 "/api/kb57/feedback/stats")):
            resp = client.request(method, path)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 23 端点(P5 扩至 25——基线语义)
        from routes.kb57_routes import (
            router as kb_router,
        )
        count = sum(1 for r in kb_router.routes)
        record("57号路由累计 ≥23 端点",
               count >= 23, str(count))


async def run_all():
    await TestFeedbackLoop().run()
    await TestCompensate().run()
    await TestRecallCompensationLink().run()
    await TestScheduler().run()
    await TestHttp().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
