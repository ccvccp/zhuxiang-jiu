"""46号·AI 治理与合规中枢 P5 专项测试(治理看板与干预通道)

运行方式:
    python test_ai_governance_p5.py

覆盖(计划 §八):
    - 看板聚合结构: 六区块齐备/红线常驻/干预入口说明
    - ① 档案总览: 状态分布/batch 分组/冻结档案列表
    - ② 审批队列: pending 列表/统计/一键审批字段
    - ③ 健康排行: Top/Bottom 排序/无快照降级提示/
      检测器命中统计
    - ④ 公平性视图: flagged 列表/无报告降级提示
    - ⑤ 回放轨迹: 最近日志/漂移标记
    - ⑥ 合规入口: 端点链接/最近审计引用
    - fail-soft 分区: 单区块数据源异常不阻断看板
    - 干预闭环 E2E: 看板提交冻结申请→审批→注册中心
      生效→run_learning 守卫拦截→解冻恢复
    - HTTP 层: 端点结构与鉴权
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

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


async def seed_registry():
    from services.ai_governance_service import (
        AiGovernanceService,
    )
    await AiGovernanceService().sync_registry()


class TestDashboardStructure:
    async def run(self):
        print("[01 看板聚合结构]")
        reset_all()
        await seed_registry()
        from services.ai_governance_dashboard import (
            AiGovernanceDashboardService,
        )
        r = await AiGovernanceDashboardService().build()
        record("聚合成功", r["success"] is True, str(r)[:60])
        zones = r.get("zones") or {}
        record("六区块齐备",
               set(zones) == {"registry", "approvals",
                              "health", "fairness",
                              "replay", "compliance"},
               str(sorted(zones)))
        record("红线常驻(§九)",
               len(r.get("redlines") or []) == 5
               and "fail-soft" in r["redlines"][0],
               str((r.get("redlines") or [""])[:1]))
        record("干预入口说明",
               "POST /api/ai-gov/changes"
               in r["intervention"]["submitEndpoint"],
               str(r["intervention"])[:70])
        record("区块错误为空",
               r.get("zoneErrors") == [],
               str(r.get("zoneErrors")))
        record("generatedAt时间戳",
               bool(r.get("generatedAt")),
               str(r.get("generatedAt")))


class TestZones:
    async def run(self):
        print("[02 六区块内容]")
        reset_all()
        await seed_registry()
        from services.ai_governance_dashboard import (
            AiGovernanceDashboardService,
        )
        svc = AiGovernanceDashboardService()

        # ① 档案总览
        z = await svc._zone_registry()
        record("①总数29", z["total"] == 30, str(z["total"]))
        record("①active分布",
               z["byStatus"]["active"] == 30
               and z["byStatus"]["frozen"] == 0,
               str(z["byStatus"]))
        record("①batch覆盖14",
               len(z["byBatch"]) == 14,
               str(len(z["byBatch"])))
        record("①无冻结档案",
               z["frozenScorers"] == [],
               str(z["frozenScorers"]))

        # ② 审批队列(空)
        z = await svc._zone_approvals()
        record("②空队列", z["pendingCount"] == 0
               and z["pendingChanges"] == [],
               str(z["pendingCount"]))

        # ③ 健康排行(无快照降级)
        z = await svc._zone_health()
        record("③无快照降级提示",
               "暂无巡检快照" in z["note"]
               and z["avgScore"] is None,
               str(z["note"])[:40])

        # ④ 公平性(无报告降级)
        z = await svc._zone_fairness()
        record("④无报告降级提示",
               "暂无审计报告" in z["note"]
               and z["flaggedCount"] == 0,
               str(z["note"])[:40])

        # ⑤ 回放轨迹(空)
        z = await svc._zone_replay()
        record("⑤空日志", z["logsTotal"] == 0
               and z["driftedCount"] == 0,
               str(z["logsTotal"]))

        # ⑥ 合规入口
        z = await svc._zone_compliance()
        record("⑥端点链接齐备",
               "compliance/filing" in z["endpoints"]["filing"]
               and "compliance/report" in
               z["endpoints"]["report"],
               str(z["endpoints"]))
        record("⑥最近审计引用",
               (z.get("lastAudit") or {})
               .get("windowDays") == 30,
               str(z.get("lastAudit"))[:60])


class TestZonesWithData:
    async def run(self):
        print("[03 六区块数据链]")
        reset_all()
        await seed_registry()
        from services.ai_governance_dashboard import (
            AiGovernanceDashboardService,
        )
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        from services.ai_governance_replay import (
            AiGovernanceReplayService,
        )
        from services.ai_learning_service import (
            update_learning_config,
        )
        from services.trust_scoring_service import (
            TrustValueScorer,
        )

        # 灌全链数据: 冻结审批+健康告警+公平性flag+回放漂移
        gov = AiGovernanceService()
        r = await gov.submit_change(
            "trust_value", "freeze", {},
            "P5看板验收冻结")
        await gov.review_change(r["changeId"], True)

        from datetime import UTC, datetime, timedelta
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        repo = AiLearningRepository()
        old = (datetime.now(UTC)
               - timedelta(days=40)).isoformat()
        await repo.save_profile("trust_value", {
            "champion": {"version": "v1", "weights": {},
                         "source": "default",
                         "parentVersion": "-", "stats": {},
                         "note": "", "createdAt": old}})
        for _ in range(2):
            await repo.add_feedback({
                "scorerId": "trust_value",
                "weightVersion": "v1",
                "scoreAtDecision": 50.0,
                "actualAction": "pass",
                "expectedAction": "pass",
                "correct": True, "factors": [], "note": "",
                "source": "manual", "status": "pending",
                "createdAt": datetime.now(UTC).isoformat()})
        await AiGovernanceHealthService().scan()

        fair = AiGovernanceFairnessService()
        await fair.submit_samples("trust_value", [
            {"group": "A", "score": 80,
             "passed": True}] * 10 +
            [{"group": "B", "score": 50,
             "passed": False}] * 10)
        await fair.run_audit("trust_value")

        replay = AiGovernanceReplayService()
        factors = [{"name": n, "value": 60.0}
                   for n in TrustValueScorer.WEIGHTS]
        await replay.submit_log("trust_value",
                               "dash:drift:001",
                               factors, 95.0)
        await replay.submit_log("trust_value",
                                "dash:ok:002",
                                factors, 60.0)

        # 验证看板反映全链
        svc = AiGovernanceDashboardService()
        b = await svc.build()
        z = b["zones"]

        record("①frozen分布反映",
               z["registry"]["byStatus"]["frozen"] == 1
               and z["registry"]["frozenScorers"]
               == ["trust_value"],
               str(z["registry"]["byStatus"]))
        record("③健康快照引用",
               (z["health"]["lastScan"] or {})
               .get("scanId") is not None
               and z["health"]["hits"]["stagnation"] >= 1,
               str(z["health"]["hits"]))
        record("③Bottom含命中档案",
               any(e["scorerId"] == "trust_value"
                   for e in z["health"]["bottom"]),
               str(z["health"]["bottom"])[:80])
        record("③Top5结构",
               len(z["health"]["top"]) == 5
               and all(e.get("healthScore") is not None
                       for e in z["health"]["top"]),
               str(len(z["health"]["top"])))
        record("④flagged列表",
               z["fairness"]["flaggedCount"] == 1
               and (z["fairness"]["flagged"]
                    or [{}])[0]["scorerId"]
               == "trust_value",
               str(z["fairness"]["flagged"])[:70])
        record("⑤漂移计数",
               z["replay"]["driftedCount"] == 1
               and z["replay"]["logsTotal"] == 2,
               f"total={z['replay']['logsTotal']} "
               f"drifted={z['replay']['driftedCount']}")
        record("⑥审计引用变更",
               (z["compliance"]["lastAudit"] or {})
               .get("changes") == 1,
               str(z["compliance"].get("lastAudit"))[:60])

        # 解冻收尾(还原 active)
        r = await gov.submit_change(
            "trust_value", "unfreeze", {}, "验收完成")
        await gov.review_change(r["changeId"], True)


class TestFailSoft:
    async def run(self):
        print("[04 fail-soft 分区]")
        reset_all()
        await seed_registry()
        from services.ai_governance_dashboard import (
            AiGovernanceDashboardService,
        )
        svc = AiGovernanceDashboardService()

        # 单区块数据源异常 → 看板仍返回(区块 error 留痕)
        async def _boom():
            raise RuntimeError("公平性区块瞬断")
        orig = svc._zone_fairness
        svc._zone_fairness = _boom
        try:
            r = await svc.build()
            record("单区块异常不阻断看板",
                   r["success"] is True
                   and r["zoneErrors"] == ["fairness"],
                   str(r["zoneErrors"]))
            record("异常区块error留痕",
                   "瞬断" in str(
                       (r["zones"]["fairness"] or {})
                       .get("error", "")),
                   str(r["zones"]["fairness"])[:60])
            record("其余区块照常",
                   (r["zones"]["registry"] or {})
                   .get("total") == 30,
                   str(r["zones"].get("registry"))[:40])
        finally:
            svc._zone_fairness = orig


class TestInterventionLoop:
    async def run(self):
        print("[05 干预闭环 E2E]")
        reset_all()
        await seed_registry()
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        from services.ai_learning_service import (
            update_learning_config,
        )
        await update_learning_config("trust_value",
                                     {"min_feedback": 1})
        gov = AiGovernanceService()

        # 学习可运行(未冻结)
        from services.ai_learning_service import (
            run_learning_cycle,
        )
        try:
            await run_learning_cycle("trust_value")
            record("冻结前可学习", True)
        except ValueError as e:
            record("冻结前可学习",
                   "冻结" not in str(e), str(e))

        # 看板语义的闭环: 提交→审批→生效→守卫拦截
        r = await gov.submit_change(
            "trust_value", "freeze", {},
            "看板干预闭环测试")
        await gov.review_change(r["changeId"], True)
        try:
            await run_learning_cycle("trust_value")
            record("冻结守卫拦截", False, "未抛")
        except ValueError as e:
            record("冻结守卫拦截",
                   "冻结" in str(e), str(e))

        # 看板档案总览反映 frozen
        from services.ai_governance_dashboard import (
            AiGovernanceDashboardService,
        )
        b = await AiGovernanceDashboardService().build()
        record("看板反映干预生效",
               (b["zones"]["registry"]["byStatus"]
                .get("frozen")) == 1,
               str(b["zones"]["registry"]["byStatus"]))

        # 解冻 → 学习恢复
        r = await gov.submit_change(
            "trust_value", "unfreeze", {}, "闭环完成")
        await gov.review_change(r["changeId"], True)
        try:
            await run_learning_cycle("trust_value")
            record("解冻学习恢复", True)
        except ValueError as e:
            record("解冻学习恢复",
                   "冻结" not in str(e), str(e))


class TestHttp:
    async def run(self):
        print("[06 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.ai_governance_routes import (
            register_ai_governance_routes,
        )
        app = FastAPI()
        register_ai_governance_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 鉴权
        resp = client.get("/api/ai-gov/dashboard")
        record("看板缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 同步台账后看板 200
        client.post("/api/ai-gov/registry/sync",
                    headers=admin)
        resp = client.get("/api/ai-gov/dashboard",
                          headers=admin)
        body = resp.json()
        record("看板200六区块", resp.status_code == 200
               and set(body.get("zones") or {})
               == {"registry", "approvals", "health",
                   "fairness", "replay", "compliance"},
               str(sorted((body.get("zones")
                           or {}).keys())))
        record("看板红线常驻",
               len(body.get("redlines") or []) == 5,
               str(len(body.get("redlines") or [])))
        record("看板干预入口",
               "submitEndpoint"
               in body.get("intervention", {}),
               str(body.get("intervention"))[:60])

        # 看板数据链: 灌 pending 后看板反映
        client.post("/api/ai-gov/changes", json={
            "scorerId": "trust_value", "kind": "freeze",
            "reason": "HTTP 看板测试"}, headers=admin)
        resp = client.get("/api/ai-gov/dashboard",
                          headers=admin)
        approvals = ((resp.json().get("zones") or {})
                    .get("approvals") or {})
        record("看板审批队列反映",
               approvals.get("pendingCount") == 1
               and len(approvals.get(
                   "pendingChanges") or []) == 1,
               str(approvals.get("pendingCount")))

        # P0-P4 路由回归
        for name, path in (
                ("P0台账回归", "/api/ai-gov/registry"),
                ("P1健康回归", "/api/ai-gov/health"),
                ("P1告警回归", "/api/ai-gov/alerts"),
                ("P2报告回归",
                 "/api/ai-gov/fairness/report"),
                ("P3日志回归", "/api/ai-gov/replay"),
                ("P4备案回归",
                 "/api/ai-gov/compliance/filing"),
                ("P4审计回归",
                 "/api/ai-gov/compliance/report"),
        ):
            resp = client.get(path, headers=admin)
            record(name, resp.status_code == 200,
                   str(resp.status_code))


async def run_all():
    await TestDashboardStructure().run()
    await TestZones().run()
    await TestZonesWithData().run()
    await TestFailSoft().run()
    await TestInterventionLoop().run()
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
