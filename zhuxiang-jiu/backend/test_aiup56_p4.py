"""56号·AI智能升级管理模块 P4 专项测试
(交付+语义回滚+决策回流)

运行方式:
    python test_aiup56_p4.py

覆盖(56号计划 §九 P4):
    - 资产包交付: approved→delivered(versioned 出口
      +灰度跟踪窗口+无审批不可交付铁律)
    - 语义回滚: 预案分步执行留痕+45号 L2 受影响
      用户信值补偿
    - 决策回流: 七类真值信号+44号池双写+幂等
    - T+1 补标调度器(默认 off)
    - HTTP 层: 4 端点+鉴权+18 端点计数
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
os.environ.pop("AIUP56_LEARN_MODE", None)
os.environ.pop("AIUP56_LEARN_INTERVAL", None)

PASS = 0
FAIL = 0
RESULTS = []

ALL_CONFIRMATIONS = (
    "readAuditReport", "reviewedSandbox",
    "acknowledgedRollback", "acknowledgedBudget")


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


async def seed_approved_proposal():
    """种一个 approved 提案(全链到人工审批)"""
    from core.helpers import ts
    from repositories.qr55_repository import (
        Qr55Repository,
    )
    repo55 = Qr55Repository()
    for snap in (
            {"satisfactionScore": 80.0,
             "clarifyEfficiency": 0.8,
             "penetrationRate": 0.7},
            {"satisfactionScore": 60.0,
             "clarifyEfficiency": 0.5,
             "penetrationRate": 0.4}):
        meid = await repo55.next_model_event_id()
        await repo55.save_model_event({
            "modelEventId": meid,
            "eventType": "metrics_snapshot",
            "detail": {"metrics": snap},
            "createdAt": ts(),
        })
    from services.aiup56_service import Aiup56Service
    from services.aiup56_plan_service import (
        Aiup56PlanService,
    )
    from services.aiup56_code_service import (
        Aiup56CodeService,
    )
    from services.aiup56_test_service import (
        Aiup56TestService,
    )
    from services.aiup56_audit_service import (
        Aiup56AuditService,
    )
    from services.aiup56_review_service import (
        Aiup56ReviewService,
    )
    r = await Aiup56Service().evaluate_and_propose()
    pid = r["proposalId"]
    await Aiup56PlanService().plan(pid)
    await Aiup56CodeService().code(pid)
    await Aiup56TestService().test(pid)
    await Aiup56AuditService().audit(pid)
    await Aiup56ReviewService().review(
        pid, reviewer="admin", approved=True,
        confirmations=list(ALL_CONFIRMATIONS))
    return pid


async def make_proposal(**overrides):
    """直建最小提案(信号判定测试用——精确控制
    状态/字段)"""
    from repositories.aiup56_repository import (
        Aiup56Repository,
    )
    repo = Aiup56Repository()
    pid = await repo.next_proposal_id()
    record_ = {
        "proposalId": pid,
        "status": "draft",
        "signalSnapshot": {
            "hits": [
                {"source": "us52_usability_drop"}],
            "sideCoverage": 0.5,
        },
        "necessityScore": 55.0,
        "estimatedGain": 10.0,
        "budgetCap": 0.1,
        "budgetSpent": 0.0,
        "tasks": [],
        "headline": "p4-test",
    }
    record_.update(overrides)
    await repo.save_proposal(record_)
    return pid


class TestDeliver:
    """01 资产包交付"""

    async def run(self):
        print("[01 资产包交付]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        pid = await seed_approved_proposal()

        from services.aiup56_deliver_service import (
            Aiup56DeliverService,
        )
        deliverer = Aiup56DeliverService()

        # off 亦可用(交付链人工动作不受开关影响——
        # 终审人工铁律)
        os.environ["AIUP56_MODE"] = "off"
        try:
            await deliverer.deliver(pid)
            ok, err = True, ""
        except ValueError as e:
            ok, err = "approved" in str(e), str(e)[:40]
        except Exception as e:  # noqa: BLE001
            ok, err = False, f"off 态异常: {e}"
        record("交付 off 亦可用(终审铁律)", ok, err)

        # 非 approved 拒绝(无审批不可交付铁律)
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        pid2 = await seed_approved_proposal()
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        proposal = await Aiup56Repository().get_proposal(
            pid2)
        proposal["status"] = "audited"
        await Aiup56Repository().save_proposal(
            proposal, create=False)
        try:
            await deliverer.deliver(pid2)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "approved" in str(e), str(e)[:40]
        record("无审批交付拒绝(铁律)", ok, err)

        # 404
        try:
            await deliverer.deliver(99999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("交付 404(不存在)", ok, err)

        # 正常交付(恢复 approved)
        proposal["status"] = "approved"
        await Aiup56Repository().save_proposal(
            proposal, create=False)
        d = await deliverer.deliver(pid2)

        pkg = d.get("package") or {}
        record("交付成功(delivered)",
               d.get("status") == "delivered"
               and d.get("success") is True,
               str(d.get("status")))
        record("资产包(草稿+测试计划+VALUE_REASON)",
               len(pkg.get("drafts") or []) >= 1
               and len(pkg.get("testPlans") or []) >= 1
               and len(pkg.get("VALUE_REASONs") or [])
               >= 1,
               str((len(pkg.get("drafts") or []),
                    len(pkg.get("VALUE_REASONs")
                        or []))))
        record("回滚预案随包(tasks 对齐)",
               len(pkg.get("rollbackPlans") or []) >= 1,
               str(len(pkg.get("rollbackPlans")
                        or [])))
        record("审计报告随包", bool(
            pkg.get("auditReport")),
               str(pkg.get("auditReport"))[:40])
        record("沙箱结论随包(passed)",
               pkg.get("sandboxVerdict") == "passed",
               str(pkg.get("sandboxVerdict")))
        record("审批信息随包(reviewer)",
               (pkg.get("approval") or {})
               .get("reviewedBy") == "admin",
               str(pkg.get("approval")))
        record("预算随包(cap=0.1)",
               (pkg.get("budget") or {}).get("cap")
               == 0.1,
               str(pkg.get("budget")))
        record("部署说明(AI 永不落盘铁律)",
               "CI" in str(pkg.get("deploymentNote")),
               str(pkg.get("deploymentNote"))[:40])

        # 状态翻转+留痕(仓储读回——序列化验证)
        stored = await Aiup56Repository().get_proposal(
            pid2)
        record("状态翻转(deliveredAt)",
               stored.get("status") == "delivered"
               and bool(stored.get("deliveredAt")),
               str(stored.get("status")))
        record("资产包仓储留痕(结构完整)",
               isinstance(
                   stored.get("deliveryPackage"), dict)
               and (stored.get("deliveryPackage")
                    or {}).get("assetId"),
               str(type(
                   stored.get("deliveryPackage"))))

        # deliver 事件留痕
        events = await Aiup56Repository().list_events(
            proposal_id=pid2, limit=50)
        deliver_evs = [e for e in events
                       if e.get("eventType")
                       == "deliver"]
        record("deliver 事件留痕",
               len(deliver_evs) == 1,
               str(len(deliver_evs)))

        # 重复交付拒绝(状态机)
        try:
            await deliverer.deliver(pid2)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "delivered" in str(e), str(e)[:40]
        record("重复交付拒绝(状态机)", ok, err)


class TestRollback:
    """02 语义回滚+信值补偿"""

    async def run(self):
        print("[02 语义回滚]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        pid = await seed_approved_proposal()

        from services.aiup56_deliver_service import (
            Aiup56DeliverService,
        )
        deliverer = Aiup56DeliverService()

        # 非 delivered 拒绝
        try:
            await deliverer.rollback(pid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "delivered" in str(e), str(e)[:40]
        record("未交付回滚拒绝(状态机)", ok, err)

        # 404
        try:
            await deliverer.rollback(99999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("回滚 404(不存在)", ok, err)

        # 45号档案(补偿对象)
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        member = await TrustProfileService().create_role(
            "person", "灰度受影响用户", "ID-AIUP56-P4")
        member_id = member["trustId"]

        # 交付后回滚(带补偿)
        await deliverer.deliver(pid)
        rb = await deliverer.rollback(
            pid, reason="灰度指标异常",
            affected_members=[member_id, 99999])

        record("回滚成功(rolled_back)",
               rb.get("status") == "rolled_back",
               str(rb.get("status")))
        steps = rb.get("steps") or []
        record("预案分步执行留痕(≥1 步)",
               len(steps) >= 1
               and all(s.get("executed") is True
                      for s in steps),
               str(len(steps)))
        record("分步含策略+步骤+数据清理",
               all(("strategy" in s
                    and "steps" in s
                    and "dataCleanup" in s)
                   for s in steps),
               str(steps[:1]))

        comp = rb.get("compensation") or {}
        record("补偿(成功 1+跳过 1)",
               comp.get("attempted") == 2
               and comp.get("compensated") == 1
               and comp.get("skipped") == 1,
               str({k: comp.get(k) for k in
                    ("attempted", "compensated",
                     "skipped")}))

        # 45号 L2 存证留痕(platform_conduct)
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
        record("45号 L2 存证留痕(platform_conduct)",
               len(dep) >= 1,
               str(len(dep)))

        # 状态翻转+留痕(仓储读回)
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        stored = await Aiup56Repository().get_proposal(
            pid)
        record("回滚留痕(原因+分步+补偿)",
               stored.get("status") == "rolled_back"
               and stored.get("rollbackReason")
               == "灰度指标异常"
               and isinstance(
                   stored.get("rollbackSteps"), list)
               and isinstance(
                   stored.get("compensation"), dict),
               str((stored.get("status"),
                    type(stored.get(
                        "rollbackSteps")))))

        # rollback 事件留痕
        events = await Aiup56Repository().list_events(
            proposal_id=pid, limit=50)
        rb_evs = [e for e in events
                  if e.get("eventType")
                  == "rollback"]
        record("rollback 事件留痕",
               len(rb_evs) == 1
               and (rb_evs[0].get("detail")
                    or {}).get("compensated") == 1,
               str(len(rb_evs)))

        # 重复回滚拒绝(状态机)
        try:
            await deliverer.rollback(pid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "delivered" in str(e), str(e)[:40]
        record("重复回滚拒绝(状态机)", ok, err)


def SIGNAL_REWARDS_OK(rewards: dict) -> bool:
    return rewards == {
        "deliver_success": 1.0,
        "value_achieved": 0.8,
        "value_missed": 0.3,
        "reject_recurrence": -0.6,
        "rollback_after_deliver": -0.8,
        "veto_overturned": -0.5,
        "budget_halt_frequent": -0.4,
    }


class TestFeedback:
    """03 决策回流(七类信号+44号池双写)"""

    async def run(self):
        print("[03 决策回流]")
        reset_all()

        from services.aiup56_feedback_service import (
            Aiup56FeedbackService,
            SIGNAL_REWARDS,
        )
        fb = Aiup56FeedbackService()

        # 七类信号场景种数(直建精确控制)
        p1 = await make_proposal(
            status="delivered")  # 无增益数据
        p2 = await make_proposal(
            status="delivered", actualGain=9.5)  # ≥90%
        p3 = await make_proposal(
            status="delivered", actualGain=5.0)  # 弱满足
        p4 = await make_proposal(
            status="rolled_back")
        p5 = await make_proposal(
            status="approved",
            auditVerdict="rejected")  # 否决被推翻
        p6 = await make_proposal(
            status="planned",
            reviewVerdict="rejected")  # 驳回复发
        p7 = await make_proposal(
            status="blocked",
            testVerdict="budget_halted")  # 预算熔断
        p8 = await make_proposal(
            status="draft")  # 无信号→跳过

        r = await fb.collect_feedback()
        record("回流扫描(8 提案)",
               r.get("scanned") == 8
               and r.get("labeled") == 7
               and r.get("skipped") == 1,
               str((r.get("scanned"),
                    r.get("labeled"),
                    r.get("skipped"))))

        signals = r.get("signals") or {}
        expect = {
            "deliver_success": 1,
            "value_achieved": 1,
            "value_missed": 1,
            "rollback_after_deliver": 1,
            "veto_overturned": 1,
            "reject_recurrence": 1,
            "budget_halt_frequent": 1,
        }
        record("七类信号全命中(各 1)",
               signals == expect,
               str(signals))

        # reward 数值口径
        record("reward 口径(±七值)",
               SIGNAL_REWARDS_OK(SIGNAL_REWARDS),
               str(SIGNAL_REWARDS))

        # 44号池双写(池记录核对)
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        pool = await AiLearningRepository(
        ).list_feedback("upgrade_orchestration")
        record("44号池双写(7 条)",
               len(pool) == 7,
               str(len(pool)))
        rewards_in_pool = sorted(
            float(x.get("reward") or 0) for x in pool)
        record("池内 reward 谱系(七值)",
               rewards_in_pool == sorted(
                   SIGNAL_REWARDS.values()),
               str(rewards_in_pool))
        record("池记录来源(aiup56_pipeline)",
               all(x.get("source") == "aiup56_pipeline"
                   for x in pool),
               str({x.get("source")
                    for x in pool}))

        # 提案回写(pooledFeedbackId+信号+奖励)
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        stored = await Aiup56Repository().get_proposal(
            p4)
        record("提案回写(pooled+信号+奖励)",
               int(stored.get("pooledFeedbackId")
                   or 0) > 0
               and stored.get("poolSignal")
               == "rollback_after_deliver"
               and stored.get("poolReward") == -0.8,
               str((stored.get("pooledFeedbackId"),
                    stored.get("poolSignal"),
                    stored.get("poolReward"))))

        # learn_signal 事件留痕
        events = await Aiup56Repository().list_events(
            proposal_id=p4, limit=50)
        learn_evs = [e for e in events
                     if e.get("eventType")
                     == "learn_signal"]
        record("learn_signal 事件留痕",
               len(learn_evs) == 1
               and (learn_evs[0].get("detail")
                    or {}).get("reward") == -0.8,
               str(len(learn_evs)))

        # 幂等(重复 collect 跳过)
        r2 = await fb.collect_feedback()
        record("幂等(重复补标跳过)",
               r2.get("labeled") == 0
               and r2.get("skipped") == 8,
               str((r2.get("labeled"),
                    r2.get("skipped"))))
        pool2 = await AiLearningRepository(
        ).list_feedback("upgrade_orchestration")
        record("幂等(池不重复)",
               len(pool2) == 7,
               str(len(pool2)))

        # 统计观测面
        stats = await fb.feedback_stats()
        record("回流统计(分布+池提交)",
               (stats.get("bySignal") or {})
               .get("rollback_after_deliver") == 1
               and stats.get("poolSubmitted") == 7,
               str(stats.get("bySignal")))


def SIGNAL_REWORDS_OK(rewards: dict) -> bool:
    return rewards == {
        "deliver_success": 1.0,
        "value_achieved": 0.8,
        "value_missed": 0.3,
        "reject_recurrence": -0.6,
        "rollback_after_deliver": -0.8,
        "veto_overturned": -0.5,
        "budget_halt_frequent": -0.4,
    }


class TestScheduler:
    """04 T+1 补标调度器"""

    async def run(self):
        print("[04 调度器]")
        reset_all()
        from services.aiup56_scheduler import (
            scheduler_enabled,
            scheduler_interval_seconds,
            run_scheduled_collect,
        )

        # 默认 off(零影响铁律)
        os.environ.pop("AIUP56_LEARN_MODE", None)
        record("默认关闭(零影响)",
               scheduler_enabled() is False,
               str(scheduler_enabled()))

        # 开关显式开启
        os.environ["AIUP56_LEARN_MODE"] = "on"
        record("显式开启(on)",
               scheduler_enabled() is True,
               str(scheduler_enabled()))
        os.environ.pop("AIUP56_LEARN_MODE", None)

        # 周期(默认 24h+下限 5 分钟)
        record("周期默认 24h",
               scheduler_interval_seconds() == 86400,
               str(scheduler_interval_seconds()))
        os.environ["AIUP56_LEARN_INTERVAL"] = "10"
        record("周期下限 5 分钟",
               scheduler_interval_seconds() == 300,
               str(scheduler_interval_seconds()))
        os.environ["AIUP56_LEARN_INTERVAL"] = "3600"
        record("周期可调(1h)",
               scheduler_interval_seconds() == 3600,
               str(scheduler_interval_seconds()))
        os.environ.pop("AIUP56_LEARN_INTERVAL", None)

        # 独立执行一轮(含留痕)
        await make_proposal(status="rolled_back")
        result = await run_scheduled_collect()
        collect = result.get("collect") or {}
        record("独立执行(补标 1 条)",
               collect.get("labeled") == 1,
               str(collect))

        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        events = await Aiup56Repository().list_events(
            proposal_id=0, limit=10)
        sched_evs = [e for e in events
                     if e.get("eventType")
                     == "scheduler_run"]
        record("scheduler_run 事件留痕",
               len(sched_evs) == 1
               and (sched_evs[0].get("detail")
                    or {}).get("collect")
               is not None,
               str(len(sched_evs)))


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        pid = await seed_approved_proposal()

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 交付链 off 亦可用(终审人工铁律——
        # 404/409 非 off 语义即证明不受开关影响)
        os.environ["AIUP56_MODE"] = "off"
        resp = client.post(
            f"/api/aiup56/proposals/99999/deliver",
            headers=admin)
        record("HTTP deliver off 亦可用(404 非 off)",
               resp.status_code == 404,
               str(resp.status_code))

        # deliver 200(off 态人工链路)
        resp = client.post(
            f"/api/aiup56/proposals/{pid}/deliver",
            headers=admin)
        body = resp.json() or {}
        record("HTTP deliver 200(delivered)",
               resp.status_code == 200
               and body.get("status") == "delivered",
               str((resp.status_code,
                    body.get("status"))))
        record("HTTP 资产包(versioned)",
               bool((body.get("package") or {})
                    .get("assetId")),
               str((body.get("package") or {})
                   .get("assetId")))

        # rollback 200(带受影响用户)
        resp = client.post(
            f"/api/aiup56/proposals/{pid}/rollback",
            json={"reason": "灰度异常",
                  "affectedMembers": []},
            headers=admin)
        body = resp.json() or {}
        record("HTTP rollback 200(rolled_back)",
               resp.status_code == 200
               and body.get("status") == "rolled_back",
               str((resp.status_code,
                    body.get("status"))))
        record("HTTP 回滚分步留痕",
               len(body.get("steps") or []) >= 1,
               str(len(body.get("steps") or [])))

        # 重复回滚 409(状态机)
        resp = client.post(
            f"/api/aiup56/proposals/{pid}/rollback",
            json={"reason": "again"},
            headers=admin)
        record("HTTP 重复回滚 409",
               resp.status_code == 409,
               str(resp.status_code))

        # feedback/collect 200
        resp = client.post(
            "/api/aiup56/feedback/collect",
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect 200(标注 1)",
               resp.status_code == 200
               and body.get("labeled") == 1,
               str((resp.status_code,
                    body.get("labeled"))))

        # feedback/stats 200(观测面)
        resp = client.get(
            "/api/aiup56/feedback/stats",
            headers=admin)
        body = resp.json() or {}
        record("HTTP stats 观测面",
               resp.status_code == 200
               and (body.get("bySignal") or {})
               .get("rollback_after_deliver") == 1
               and body.get("poolSubmitted") == 1,
               str((resp.status_code,
                    body.get("bySignal"))))

        # 幂等(HTTP 层重复 collect)
        resp = client.post(
            "/api/aiup56/feedback/collect",
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect 幂等(0 标注)",
               resp.status_code == 200
               and body.get("labeled") == 0,
               str(body.get("labeled")))

        # 鉴权 403
        for method, path, payload in (
                ("POST",
                 f"/api/aiup56/proposals/{pid}"
                 f"/deliver", None),
                ("POST",
                 f"/api/aiup56/proposals/{pid}"
                 f"/rollback", {}),
                ("POST",
                 "/api/aiup56/feedback/collect", None),
                ("GET",
                 "/api/aiup56/feedback/stats", None)):
            resp = client.request(
                method, path, json=payload)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 18 端点(P5 扩至 20——基线语义)
        from routes.aiup56_routes import (
            router as aiup_router,
        )
        count = sum(1 for r in aiup_router.routes)
        record("56号路由累计 ≥18 端点",
               count >= 18, str(count))
        os.environ["AIUP56_MODE"] = "off"


async def run_all():
    await TestDeliver().run()
    await TestRollback().run()
    await TestFeedback().run()
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
