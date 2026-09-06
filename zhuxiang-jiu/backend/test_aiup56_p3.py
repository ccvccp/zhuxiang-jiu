"""56号·AI智能升级管理模块 P3 专项测试
(审计Agent+人类审批面板)

运行方式:
    python test_aiup56_p3.py

覆盖(56号计划 §九 P3):
    - 审计三重校验: 代码/逻辑/文档层
    - 一票否决: critical 违规 → rejected 回 planned
    - LLM 归因报告: mock 确定性+数字来自数据层
    - 审批面板: 强制确认清单(缺勾选拒绝)
    - escalate 双人复核: 缺第二人/同人拒绝
    - 审批流: 批准 approved/驳回回 planned
    - 状态机: tested→audited→approved 链
    - HTTP 层: 3 端点+鉴权+14 端点计数
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


async def seed_audited_proposal():
    """种一个 audited 提案(全链到 audit)"""
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
    r = await Aiup56Service().evaluate_and_propose()
    pid = r["proposalId"]
    await Aiup56PlanService().plan(pid)
    await Aiup56CodeService().code(pid)
    await Aiup56TestService().test(pid)
    await Aiup56AuditService().audit(pid)
    return pid


class TestAudit:
    """01 审计Agent"""

    async def run(self):
        print("[01 审计Agent]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"

        # 全链到 tested(未审计)
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
        r = await Aiup56Service().evaluate_and_propose()
        pid = r["proposalId"]
        await Aiup56PlanService().plan(pid)
        await Aiup56CodeService().code(pid)
        await Aiup56TestService().test(pid)

        auditor = Aiup56AuditService()

        # off 拒绝
        os.environ["AIUP56_MODE"] = "off"
        try:
            await auditor.audit(pid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态审计拒绝", ok, err)
        os.environ["AIUP56_MODE"] = "shadow"

        # 正常审计
        a = await auditor.audit(pid)
        record("审计通过(passed+audited)",
               a.get("verdict") == "passed"
               and a.get("status") == "audited",
               str((a.get("verdict"),
                    a.get("status"))))

        # 三层齐备
        layers = a.get("layers") or {}
        record("三层齐备(代码/逻辑/文档)",
               set(layers.keys()) == {
                   "code", "logic", "doc"},
               str(sorted(layers.keys())))
        record("三层全过(无 critical)",
               all((layers.get(k) or {})
                   .get("passed") for k
                   in ("code", "logic", "doc")),
               str({k: (v or {}).get("passed")
                    for k, v in layers.items()}))

        # 归因报告(mock——数字来自数据层)
        report = a.get("report") or {}
        record("归因报告(mock+事实数字)",
               a.get("reportMode") == "mock"
               and (report.get("facts") or {})
               .get("criticalTotal") == 0,
               str(a.get("reportMode")))
        record("报告结论(待人类终审)",
               "人类审批" in str(
                   report.get("headline")),
               str(report.get("headline"))[:50])

        # 高亮(无 critical → 空)
        record("高亮项(无 critical 为空)",
               a.get("highlightItems") == [],
               str(a.get("highlightItems")))

        # audit 事件留痕
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        events = await Aiup56Repository(
        ).list_events(proposal_id=pid, limit=50)
        audit_evs = [e for e in events
                     if e.get("eventType")
                     == "audit"]
        record("audit 事件留痕",
               len(audit_evs) == 1,
               str(len(audit_evs)))

        # 重复审计拒绝(状态机——audited)
        try:
            await auditor.audit(pid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "audited" in str(e), str(e)[:40]
        record("重复审计拒绝(状态机)", ok, err)


class TestVeto:
    """02 一票否决"""

    async def run(self):
        print("[02 一票否决]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        pid = await seed_audited_proposal()

        # 造 critical: 沙箱静态违规伪造
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        sandboxes = await Aiup56Repository(
        ).list_sandboxes(proposal_id=pid)
        sb = sandboxes[0]
        sb["staticGate"]["violations"] = [
            "requests.get 外部 HTTP 请求"]
        await Aiup56Repository().save_sandbox(
            sb, create=False)

        # 状态回 tested 重审计
        proposal = await Aiup56Repository(
        ).get_proposal(pid)
        proposal["status"] = "tested"
        await Aiup56Repository().save_proposal(
            proposal, create=False)

        from services.aiup56_audit_service import (
            Aiup56AuditService,
        )
        a = await Aiup56AuditService().audit(pid)
        record("一票否决(critical→rejected)",
               a.get("verdict") == "rejected"
               and a.get("status") == "planned",
               str((a.get("verdict"),
                    a.get("status"))))
        code_layer = (a.get("layers") or {}) \
            .get("code") or {}
        record("代码层检出 critical",
               code_layer.get("passed") is False
               and any(
                   f.get("severity") == "critical"
                   for f in code_layer.get(
                       "findings") or []),
               str(code_layer.get("findings")))

        # 高亮项呈现
        record("高亮项(critical 呈现)",
               len(a.get("highlightItems") or []) >= 1,
               str(a.get("highlightItems")))

        # 归因报告(否决语义)
        report = a.get("report") or {}
        record("否决报告(重规划指引)",
               "一票否决" in str(
                   report.get("headline")),
               str(report.get("headline"))[:60])


class TestReview:
    """03 人类审批面板"""

    async def run(self):
        print("[03 审批面板]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        pid = await seed_audited_proposal()

        from services.aiup56_review_service import (
            Aiup56ReviewService,
        )
        reviewer_svc = Aiup56ReviewService()

        # 面板视图
        panel = await reviewer_svc.panel(pid)
        record("面板视图(材料齐备)",
               panel.get("success") is True
               and bool(panel.get("auditReport"))
               and (panel.get("sandbox")
                    is not None),
               str(panel.get("proposalStatus")))
        record("面板确认清单(4 关键项)",
               len(panel.get(
                   "requiredConfirmations")
                   or []) == 4,
               str(len(panel.get(
                   "requiredConfirmations")
                   or [])))

        # off 态亦可审批(终审铁律不受开关影响)
        os.environ["AIUP56_MODE"] = "off"
        # 状态机: 非 audited 拒绝
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        proposal = await Aiup56Repository(
        ).get_proposal(pid)
        proposal["status"] = "tested"
        await Aiup56Repository().save_proposal(
            proposal, create=False)
        try:
            await reviewer_svc.review(
                pid, reviewer="admin",
                approved=True,
                confirmations=list(
                    ALL_CONFIRMATIONS))
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "audited" in str(e), str(e)[:40]
        record("未审计审批拒绝(状态机)", ok, err)

        # 恢复 audited
        proposal["status"] = "audited"
        await Aiup56Repository().save_proposal(
            proposal, create=False)

        # 确认清单不齐 → 拒绝(防形式化)
        try:
            await reviewer_svc.review(
                pid, reviewer="admin",
                approved=True,
                confirmations=["readAuditReport"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "确认清单不齐" in str(e), \
                str(e)[:40]
        record("确认清单不齐拒绝(防形式化)",
               ok, err)

        # 批准(全勾选)
        rv = await reviewer_svc.review(
            pid, reviewer="admin",
            approved=True,
            confirmations=list(ALL_CONFIRMATIONS),
            note="p3-test")
        record("批准(approved)",
               rv.get("verdict") == "approved"
               and rv.get("status") == "approved",
               str((rv.get("verdict"),
                    rv.get("status"))))

        # 审批留痕
        proposal = await Aiup56Repository(
        ).get_proposal(pid)
        record("审批留痕(reviewer+reviewId)",
               proposal.get("reviewedBy") == "admin"
               and (proposal.get("reviewId")
                    or 0) > 0,
               str(proposal.get("reviewedBy")))

        # approve 事件留痕
        events = await Aiup56Repository(
        ).list_events(proposal_id=pid, limit=50)
        approve_evs = [e for e in events
                       if e.get("eventType")
                       == "approve"]
        record("approve 事件留痕",
               len(approve_evs) == 1,
               str(len(approve_evs)))

        # 重复审批拒绝(approved)
        try:
            await reviewer_svc.review(
                pid, reviewer="admin",
                approved=True,
                confirmations=list(
                    ALL_CONFIRMATIONS))
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "approved" in str(e), str(e)[:40]
        record("重复审批拒绝(状态机)", ok, err)


class TestDualReview:
    """04 escalate 双人复核"""

    async def run(self):
        print("[04 双人复核]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        pid = await seed_audited_proposal()

        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        proposal = await Aiup56Repository(
        ).get_proposal(pid)
        proposal["dualReview"] = True
        proposal["escalated"] = True
        await Aiup56Repository().save_proposal(
            proposal, create=False)

        from services.aiup56_review_service import (
            Aiup56ReviewService,
        )
        reviewer_svc = Aiup56ReviewService()

        # 缺第二审批人 → 拒绝
        try:
            await reviewer_svc.review(
                pid, reviewer="admin",
                approved=True,
                confirmations=list(
                    ALL_CONFIRMATIONS))
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "双人复核" in str(e), str(e)[:40]
        record("escalate 缺第二人拒绝", ok, err)

        # 同人 → 拒绝
        try:
            await reviewer_svc.review(
                pid, reviewer="admin",
                approved=True,
                confirmations=list(
                    ALL_CONFIRMATIONS),
                second_reviewer="admin")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "同人" in str(e), str(e)[:40]
        record("双人复核同人拒绝", ok, err)

        # 双人批准
        rv = await reviewer_svc.review(
            pid, reviewer="admin",
            approved=True,
            confirmations=list(ALL_CONFIRMATIONS),
            second_reviewer="ops-lead")
        record("双人复核批准",
               rv.get("verdict") == "approved"
               and rv.get("secondReviewer")
               == "ops-lead",
               str((rv.get("verdict"),
                    rv.get("secondReviewer"))))

        # 驳回流
        reset_all()
        pid2 = await seed_audited_proposal()
        rv2 = await reviewer_svc.review(
            pid2, reviewer="admin",
            approved=False,
            confirmations=[],
            note="不通过")
        record("驳回(回 planned)",
               rv2.get("verdict") == "rejected"
               and rv2.get("status") == "planned",
               str((rv2.get("verdict"),
                    rv2.get("status"))))
        p2 = await Aiup56Repository().get_proposal(
            pid2)
        record("驳回留痕(reviewVerdict)",
               p2.get("reviewVerdict")
               == "rejected",
               str(p2.get("reviewVerdict")))
        events = await Aiup56Repository(
        ).list_events(proposal_id=pid2, limit=50)
        reject_evs = [e for e in events
                      if e.get("eventType")
                      == "reject"]
        record("reject 事件留痕",
               len(reject_evs) == 1,
               str(len(reject_evs)))


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"

        # 自建到 tested(未审计——HTTP audit 待测)
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
        r = await Aiup56Service().evaluate_and_propose()
        pid = r["proposalId"]
        await Aiup56PlanService().plan(pid)
        await Aiup56CodeService().code(pid)
        await Aiup56TestService().test(pid)

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # audit off 409
        os.environ["AIUP56_MODE"] = "off"
        resp = client.post(
            f"/api/aiup56/proposals/{pid}/audit",
            headers=admin)
        record("HTTP audit off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # review off 亦可用(终审铁律——到达状态机
        # 409[非 off 语义]即证明不受开关影响)
        resp = client.post(
            f"/api/aiup56/proposals/{pid}/review",
            json={"reviewer": "admin",
                  "approved": True,
                  "confirmations": []},
            headers=admin)
        record("HTTP review off 亦可用",
               resp.status_code == 409
               and "off" not in str(
                   (resp.json() or {})
                   .get("detail", "")),
               str((resp.status_code,
                    (resp.json() or {})
                    .get("detail"))[:50]))

        os.environ["AIUP56_MODE"] = "shadow"

        # audit 200
        resp = client.post(
            f"/api/aiup56/proposals/{pid}/audit",
            headers=admin)
        body = resp.json() or {}
        record("HTTP audit 200",
               resp.status_code == 200
               and body.get("verdict") == "passed",
               str((resp.status_code,
                    body.get("verdict"))))

        # panel 观测面
        resp = client.get(
            f"/api/aiup56/proposals/{pid}/panel",
            headers=admin)
        record("HTTP panel 观测面",
               resp.status_code == 200
               and bool((resp.json() or {})
                        .get("auditReport")),
               str(resp.status_code))

        # review 200(全链)
        resp = client.post(
            f"/api/aiup56/proposals/{pid}/review",
            json={"reviewer": "admin",
                  "approved": True,
                  "confirmations":
                      list(ALL_CONFIRMATIONS)},
            headers=admin)
        body = resp.json() or {}
        record("HTTP review 200(approved)",
               resp.status_code == 200
               and body.get("verdict") == "approved",
               str((resp.status_code,
                    body.get("verdict"))))

        # 确认不齐 409
        reset_all()
        pid2 = await seed_audited_proposal()
        resp = client.post(
            f"/api/aiup56/proposals/{pid2}/review",
            json={"reviewer": "admin",
                  "approved": True,
                  "confirmations": []},
            headers=admin)
        record("HTTP review 确认不齐 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 鉴权
        for method, path in (
                ("POST",
                 f"/api/aiup56/proposals/{pid2}"
                 f"/audit"),
                ("GET",
                 f"/api/aiup56/proposals/{pid2}"
                 f"/panel"),
                ("POST",
                 f"/api/aiup56/proposals/{pid2}"
                 f"/review")):
            resp = client.request(
                method, path,
                json={} if "review" in path else None)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 14 端点(P4 扩至 18——基线语义)
        from routes.aiup56_routes import (
            router as aiup_router,
        )
        count = sum(1 for r in aiup_router.routes)
        record("56号路由累计 ≥14 端点",
               count >= 14, str(count))
        os.environ["AIUP56_MODE"] = "off"


async def run_all():
    await TestAudit().run()
    await TestVeto().run()
    await TestReview().run()
    await TestDualReview().run()
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
