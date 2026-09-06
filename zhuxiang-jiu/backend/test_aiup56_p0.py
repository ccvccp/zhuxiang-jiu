"""56号·AI智能升级管理模块 P0 专项测试
(信号注册表+决策引擎+提案底座)

运行方式:
    python test_aiup56_p0.py

覆盖(56号计划 §九 P0):
    - 注册表: 10 项四侧+权重和=1.0+自检红线
    - 评分器: 八因子+三级决策切档+越界拒绝
    - 信号采集: 多源读取+阈值命中+fail-soft
    - 决策主链: defer/propose/escalate 三态+
      提案落库(信号快照+摘要+预算封顶)
    - off 铁律: 决策面 409+观测面开放
    - 第31档案入册(31 档案+batch15)
    - HTTP 层: 5 端点+鉴权
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


async def seed_gov_alerts(count: int = 1):
    """种 46号告警(合规侧信号源)"""
    from core.helpers import ts
    from repositories.ai_governance_repository \
        import AiGovernance46Repository
    repo = AiGovernance46Repository()
    for i in range(count):
        alert_id = await repo.next_alert_id()
        await repo.save_alert({
            "alertId": alert_id,
            "scorerId": "upgrade_orchestration",
            "label": "智能升级编排评分",
            "signal": "stagnation", "level": "warn",
            "message": f"p0-seed alert {i}",
            "day": ts()[:10], "occurrences": 1,
            "firstSeenAt": ts(), "lastSeenAt": ts(),
            "firstScanId": 0, "status": "open",
        })


async def seed_gov_pending(count: int = 1):
    """种 46号挂起变更(合规侧信号源)"""
    from core.helpers import ts
    from repositories.ai_governance_repository \
        import AiGovernance46Repository
    repo = AiGovernance46Repository()
    for i in range(count):
        change_id = await repo.next_change_id()
        await repo.save_change({
            "changeId": change_id,
            "scorerId": "upgrade_orchestration",
            "kind": "weight_update", "payload": {},
            "reason": f"p0-seed pending {i}",
            "status": "pending",
            "submittedBy": "p0", "reviewedBy": "",
            "reviewNote": "", "appliedAt": "",
            "createdAt": ts(),
        })


class TestRegistry:
    """01 信号注册表"""

    async def run(self):
        print("[01 注册表]")
        from services.aiup56_registry import (
            SIGNAL_REGISTRY, SIGNAL_SIDES,
            registry_view, active_signals,
            get_signal, current_mode,
        )
        record("10 项信号数量",
               len(SIGNAL_REGISTRY) == 10,
               str(len(SIGNAL_REGISTRY)))
        record("四侧覆盖",
               {v["side"] for v
                in SIGNAL_REGISTRY.values()}
               == set(SIGNAL_SIDES),
               "")
        record("active 权重和=1.0",
               abs(sum(
                   v["weight"] for v in
                   SIGNAL_REGISTRY.values()
                   if v["status"] == "active") - 1.0)
               < 1e-9, "")
        view = registry_view()
        record("注册表视图(自描述)",
               view.get("total") == 10
               and (view.get("bySide") or {}).get(
                   "model") == 4
               and view.get("weightSum") == 1.0,
               str(view.get("bySide")))
        record("active_signals 可查",
               len(active_signals()) == 10,
               str(len(active_signals())))
        record("get_signal 命中/未命中",
               get_signal("gov46_stagnation")
               is not None
               and get_signal("nope") is None,
               "")
        record("默认模式 off",
               current_mode() == "off", "")

        # 自检红线(权重越界 → RuntimeError)
        import services.aiup56_registry as reg
        origin = reg.SIGNAL_REGISTRY[
            "gov46_stagnation"]["weight"]
        try:
            reg.SIGNAL_REGISTRY[
                "gov46_stagnation"]["weight"] = 2.0
            reg._validate_registry()
            ok, err = False, "未拒绝"
        except RuntimeError as e:
            ok, err = True, str(e)[:30]
        finally:
            reg.SIGNAL_REGISTRY[
                "gov46_stagnation"]["weight"] = origin
        record("自检红线(权重越界 RuntimeError)",
               ok, err)


class TestScorer:
    """02 八因子评分器"""

    async def run(self):
        print("[02 评分器]")
        from services.aiup56_scorer import (
            Aiup56Scorer,
        )
        svc = Aiup56Scorer()

        # 空上下文拒绝
        try:
            await svc.score({})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = True, str(e)[:30]
        record("空上下文拒绝", ok, err)

        # 高必要性+优环境 → escalate
        r = await svc.score({
            "signalHits": 5,
            "sideCoverage": 1.0,
            "necessityScore": 85.0,
            "budgetRemaining": 0.9,
            "riskFlagged": False,
            "alertDensity": 0.0,
            "poolAlignment": 0.9,
            "govHealthScore": 95,
            "historySuccessRate": 0.9,
            "humanInterventionRate": 0.1,
        })
        record("escalate 切档(≥80)",
               r.get("decision") == "escalate"
               and (r.get("trustScore") or 0) >= 80,
               str((r.get("decision"),
                    r.get("trustScore"))))

        # 低必要性 → defer
        r2 = await svc.score({
            "necessityScore": 10.0,
        })
        record("defer 切档(<50)",
               r2.get("decision") == "defer",
               str((r2.get("decision"),
                    r2.get("trustScore"))))

        # 中间态 → propose
        r3 = await svc.score({
            "signalHits": 2,
            "sideCoverage": 0.5,
            "necessityScore": 55.0,
            "budgetRemaining": 0.5,
        })
        record("propose 切档(50-80)",
               r3.get("decision") == "propose",
               str((r3.get("decision"),
                    r3.get("trustScore"))))

        # 越界拒绝
        for bad in ({"necessityScore": 120.0},
                    {"sideCoverage": 1.5}):
            try:
                await svc.score(bad)
                ok, err = False, "未拒绝"
            except ValueError:
                ok, err = True, ""
            record(f"越界拒绝({list(bad)[0]})",
                   ok, err)

        # 八因子齐备+中性口径
        r4 = await svc.score({"necessityScore": 50.0})
        names = {f["name"] for f in
                 r4.get("factors") or []}
        record("八因子齐备",
               names == {
                   "signal_quality",
                   "necessity_score",
                   "budget_sufficiency",
                   "risk_posture",
                   "model_health",
                   "history_success",
                   "compliance_posture",
                   "human_load"},
               str(sorted(names)))
        record("权重和=1.0",
               abs(sum(Aiup56Scorer.WEIGHTS.values())
                   - 1.0) < 1e-9, "")


class TestScanDecide:
    """03 信号采集+决策主链"""

    async def run(self):
        print("[03 采集+决策]")
        reset_all()
        from services.aiup56_service import (
            Aiup56Service,
        )
        svc = Aiup56Service()

        # off 铁律
        os.environ["AIUP56_MODE"] = "off"
        try:
            await svc.scan_signals()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态决策面拒绝", ok, err)

        # shadow 开放
        os.environ["AIUP56_MODE"] = "shadow"

        # 空信号环境 → defer(低必要性)
        r = await svc.evaluate_and_propose()
        record("空环境 defer(留痕不建提案)",
               r.get("decision") == "defer"
               and r.get("proposalId") is None,
               str(r.get("decision")))

        # defer 留痕事件
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        events = await Aiup56Repository(
        ).list_events(limit=50)
        defer_evs = [e for e in events
                     if e.get("eventType")
                     == "signal_scan"]
        record("defer 留痕(signal_scan 事件)",
               len(defer_evs) >= 1,
               str(len(defer_evs)))

        # 合规侧 negative 信号(告警未决 → 抑制,
        # necessity 不升反稳)
        await seed_gov_alerts(1)
        await seed_gov_pending(1)
        r2 = await svc.evaluate_and_propose()
        record("合规侧信号抑制(negative 方向)",
               r2.get("decision") == "defer",
               str((r2.get("decision"),
                    r2.get("necessityScore"))))

        # 强信号环境: 55号 指标劣化快照三命中
        # (satisfaction 降 20+clarify 降 0.3+
        # waste 0.6 → necessity 0.25×100=25 > 门槛 20)
        # (清掉 negative 合规告警种子——
        #  isolated 正向场景)
        from repositories.backend import (
            get_in_memory_store,
        )
        store = get_in_memory_store()
        store["ai46_alerts"] = {}
        store["_ai46_alerts_all"] = []
        store["ai46_changes"] = {}
        store["_ai46_changes_all"] = []
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

        r3 = await svc.evaluate_and_propose()
        record("强信号提案创建(proposalId)",
               r3.get("proposalId") is not None
               and r3.get("decision") in (
                   "propose", "escalate"),
               str((r3.get("decision"),
                    r3.get("proposalId"))))

        # 提案落库校验
        if r3.get("proposalId"):
            proposal = await Aiup56Repository(
            ).get_proposal(r3["proposalId"])
            record("提案结构(draft+快照+摘要)",
                   proposal.get("status") == "draft"
                   and bool(
                       proposal.get("signalSnapshot"))
                   and bool(
                       proposal.get("summary")),
                   str(proposal.get("status")))
            record("提案预算封顶(0.1)",
                   proposal.get("budgetCap") == 0.1,
                   str(proposal.get("budgetCap")))
            fresh_events = await \
                Aiup56Repository().list_events(
                    limit=50)
            record("提案全链事件(create)",
                   any(e.get("eventType")
                       == "proposal_create"
                       for e in fresh_events),
                   "")

        # 摘要结构(人类快速审阅)
        summary = (r3.get("summary") or {})
        record("提案摘要(topSignals+风险预告)",
               bool(summary.get("topSignals"))
               and "风险预评估" in str(
                   summary.get("riskNote")),
               str(summary.get("headline"))[:40])

        os.environ["AIUP56_MODE"] = "off"


class TestHttp:
    """04 HTTP 层"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 观测面 off 可用
        resp = client.get("/api/aiup56/registry",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP registry(off 可用)",
               resp.status_code == 200
               and body.get("total") == 10,
               str(resp.status_code))

        resp = client.get(
            "/api/aiup56/model/status",
            headers=admin)
        record("HTTP model/status(off 可用)",
               resp.status_code == 200
               and (resp.json() or {}).get(
                   "success") is True,
               str(resp.status_code))

        # 决策面 off 409
        resp = client.post(
            "/api/aiup56/signals/scan",
            headers=admin)
        record("HTTP signals/scan off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 提案列表/详情(空态)
        resp = client.get("/api/aiup56/proposals",
                          headers=admin)
        record("HTTP proposals 空态",
               resp.status_code == 200
               and (resp.json() or {}).get(
                   "total") == 0,
               str(resp.status_code))
        resp = client.get(
            "/api/aiup56/proposal/99999",
            headers=admin)
        record("HTTP proposal 404",
               resp.status_code == 404,
               str(resp.status_code))

        # shadow 态全链 HTTP
        os.environ["AIUP56_MODE"] = "shadow"
        resp = client.post(
            "/api/aiup56/signals/scan",
            headers=admin)
        record("HTTP signals/scan 200(shadow)",
               resp.status_code == 200,
               str(resp.status_code))
        # 提案列表有值(defer 轮无提案——列表仍空
        # 但端点正常)
        resp = client.get("/api/aiup56/proposals",
                          headers=admin)
        record("HTTP proposals 正常",
               resp.status_code == 200,
               str(resp.status_code))

        # 鉴权
        for method, path in (
                ("GET", "/api/aiup56/registry"),
                ("POST", "/api/aiup56/signals/scan"),
                ("GET", "/api/aiup56/proposals")):
            resp = client.request(method, path)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))
        os.environ["AIUP56_MODE"] = "off"

        # 路由累计 5 端点
        from routes.aiup56_routes import (
            router as aiup_router,
        )
        count = sum(1 for r in aiup_router.routes)
        # P1 新增 4 端点(plan/tasks/code/assets)
        # → 5→9(基线语义: ≥5——P0 交付面不因
        # P1 演进破坏)
        record("56号路由累计 ≥5 端点(P1 扩至 9)",
               count >= 5, str(count))


class TestConstitution:
    """05 第31档案入册+宪法"""

    async def run(self):
        print("[05 宪法]")
        from services.ai_learning_service import (
            SCORER_REGISTRY, DECISION_THRESHOLDS,
        )
        record("44号档案数 31(upgrade 在册)",
               len(SCORER_REGISTRY) == 31
               and "upgrade_orchestration"
               in SCORER_REGISTRY,
               str(len(SCORER_REGISTRY)))
        entry = SCORER_REGISTRY.get(
            "upgrade_orchestration") or {}
        record("batch15 入册",
               entry.get("batch") == 15,
               str(entry.get("batch")))
        record("决策阈值表注册",
               "upgrade_orchestration"
               in DECISION_THRESHOLDS,
               "")

        # 55号零改动(qr_orchestration 仍在册)
        record("55号零改动(qr 档案保持)",
               "qr_orchestration" in SCORER_REGISTRY,
               "")

        # 46号零改动(治理台账 30 档案)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService().sync_registry()
        reg = await AiGovernanceService(
        ).list_registry()
        record("46号台账 ≥30(零改动)",
               (reg.get("total") or 0) >= 30,
               str(reg.get("total")))


async def run_all():
    await TestRegistry().run()
    await TestScorer().run()
    await TestScanDecide().run()
    await TestHttp().run()
    await TestConstitution().run()


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
