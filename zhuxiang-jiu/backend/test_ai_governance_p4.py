"""46号·AI 治理与合规中枢 P4 专项测试(合规材料自动化)

运行方式:
    python test_ai_governance_p4.py

覆盖(计划 §七):
    - 备案材料: 六节结构完整性/数字来自数据层断言
      (权重表/因子数/变更数引用真实数据)/责任主体占位/
      模板版本留痕/全档案汇总版
    - 审计报告: 时间窗聚合数学断言(变更数/审批通过率/
      告警统计/冻结事件)/窗口过滤(旧数据排除)/
      days 参数校验/中文结论
    - LLM 三态: mock 确定性(off 时 llmMode=mock)
    - 数据链验证: 灌完整链数据(审批+告警+公平性报告)后
      材料数字与数据源一致
    - HTTP 层: 两端点结构与鉴权
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

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


async def seed_full_chain():
    """灌完整治理链数据: 冻结审批+告警+公平性报告"""
    from services.ai_governance_service import (
        AiGovernanceService,
    )
    from services.ai_governance_health import (
        AiGovernanceHealthService,
    )
    from services.ai_governance_fairness import (
        AiGovernanceFairnessService,
    )
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    gov_svc = AiGovernanceService()
    await gov_svc.sync_registry()

    # 冻结审批(freeze + unfreeze 留痕)
    r = await gov_svc.submit_change(
        "trust_value", "freeze", {}, "P4 验收冻结")
    await gov_svc.review_change(r["changeId"], True)
    r = await gov_svc.submit_change(
        "trust_value", "unfreeze", {}, "P4 验收解冻")
    await gov_svc.review_change(r["changeId"], True)
    # 一条驳回留痕
    r = await gov_svc.submit_change(
        "trust_value", "config", {}, "测试驳回")
    await gov_svc.review_change(r["changeId"], False)

    # 健康告警(灌停滞数据)
    from datetime import timedelta as td
    repo = AiLearningRepository()
    old = (datetime.now(UTC)
           - td(days=40)).isoformat()
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
            "expectedAction": "pass", "correct": True,
            "factors": [], "note": "", "source": "manual",
            "status": "pending",
            "createdAt": datetime.now(UTC).isoformat()})
    await AiGovernanceHealthService().scan()

    # 公平性报告
    fair = AiGovernanceFairnessService()
    await fair.submit_samples("trust_value", [
        {"group": "A", "score": 80, "passed": True}] * 10 +
        [{"group": "B", "score": 50, "passed": False}] * 10)
    await fair.run_audit("trust_value")
    return gov_svc


class TestFiling:
    async def run(self):
        print("[01 备案材料六节]")
        reset_all()
        await seed_full_chain()
        from services.ai_governance_compliance import (
            AiGovernanceComplianceService,
        )
        svc = AiGovernanceComplianceService()

        r = await svc.build_filing("trust_value")
        record("单档案材料", r["success"] is True
               and r["count"] == 1, str(r)[:60])
        f = r["filings"][0]
        sections = f["sections"]
        record("六节结构完整",
               list(sections) == [
                   "section1_basic", "section2_data",
                   "section3_logic", "section4_fairness",
                   "section5_risk", "section6_changes"],
               str(list(sections)))
        record("①算法基本信息",
               sections["section1_basic"]
               ["algorithmName"] == "信值三层评分"
               and "占位" in sections["section1_basic"]
               ["responsibility"],
               str(sections["section1_basic"])[:60])
        record("②数据来源红线声明",
               "最小采集" in sections["section2_data"]
               ["redline"],
               sections["section2_data"]["redline"][:40])
        # ③ 数字来自数据层: 权重表 = 真实冠军权重
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        record("③权重表与真实数据一致",
               sections["section3_logic"]["factorCount"]
               == len(TrustValueScorer.WEIGHTS)
               and sections["section3_logic"]["weights"]
               == dict(TrustValueScorer.WEIGHTS),
               str(sections["section3_logic"]
                   ["factorCount"]))
        record("③公式声明",
               "Σ" in sections["section3_logic"]
               ["formula"],
               sections["section3_logic"]["formula"])
        # ④ 公平性引用
        record("④公平性结论引用",
               sections["section4_fairness"]["fairness"]
               ["flagged"] is True
               and sections["section4_fairness"]
               ["fairness"]["sampleCount"] == 20,
               str(sections["section4_fairness"]
                   ["fairness"])[:70])
        record("④健康引用",
               sections["section4_fairness"]["health"]
               ["healthScore"] is not None,
               str(sections["section4_fairness"]
                   ["health"])[:60])
        # ⑤ 风险防控
        record("⑤风险防控三通道",
               "冻结" in sections["section5_risk"]
               ["freeze"]
               and "申诉" in sections["section5_risk"]
               ["appeal"]
               and "fail-soft" in sections["section5_risk"]
               ["failSoft"],
               str(sections["section5_risk"])[:60])
        # ⑥ 变更清单(数字来自审批总线)
        record("⑥变更数与审批总线一致",
               sections["section6_changes"]
               ["totalChanges"] == 3
               and sections["section6_changes"]
               ["approved"] == 2
               and sections["section6_changes"]
               ["rejected"] == 1,
               str(sections["section6_changes"]
                   ["totalChanges"]))
        record("模板版本留痕",
               f["templateVersion"] == "v1-filing",
               str(f["templateVersion"]))
        record("mock模式确定性",
               r["llmMode"] == "mock", str(r["llmMode"]))

        # 全档案汇总版
        r = await svc.build_filing()
        record("全档案汇总29份", r["count"] == 30,
               str(r["count"]))
        record("每份六节齐备",
               all(len(f["sections"]) == 6
                   for f in r["filings"]),
               "缺节")

        # 未入册
        try:
            await svc.build_filing("unknown_scorer")
            record("未入册404", False, "未抛")
        except KeyError:
            record("未入册404", True)

        # 空台账
        reset_all()
        r = await svc.build_filing()
        record("空台账安全", r["success"] is True
               and r["count"] == 0, str(r)[:60])


class TestAuditReport:
    async def run(self):
        print("[02 审计报告时间窗]")
        reset_all()
        await seed_full_chain()
        from services.ai_governance_compliance import (
            AiGovernanceComplianceService,
        )
        svc = AiGovernanceComplianceService()

        r = await svc.build_report(days=30)
        record("报告结构", r["success"] is True
               and r["windowDays"] == 30,
               str(r["windowDays"]))
        record("变更聚合3次", r["changes"]["total"] == 3,
               str(r["changes"]))
        record("通过率2/3",
               r["changes"]["approved"] == 2
               and r["changes"]["rejected"] == 1
               and r["changes"]["approvalRate"]
               == round(2 / 3, 4),
               str(r["changes"]["approvalRate"]))
        record("告警聚合",
               r["alerts"]["total"] >= 1
               and (r["alerts"]["bySignal"] or {})
               .get("stagnation") >= 1,
               str(r["alerts"]))
        record("公平性聚合",
               r["fairness"]["reportsGenerated"] == 1
               and r["fairness"]["flaggedCount"] == 1
               and r["fairness"]["flaggedScorers"]
               == ["trust_value"],
               str(r["fairness"]))
        record("冻结事件聚合",
               r["freezeEvents"]["total"] == 2,
               str(r["freezeEvents"]["total"]))
        record("台账分布",
               r["registry"]["total"] == 30
               and r["registry"]["active"] == 30
               and r["registry"]["frozen"] == 0,
               str(r["registry"]))
        record("健康快照引用",
               (r["health"] or {}).get("lastScanId")
               is not None,
               str(r["health"]))
        record("中文结论",
               "变更 3 次" in r["conclusion"]
               and "建议优先复核" in r["conclusion"],
               r["conclusion"][:60])

        # 窗口过滤: 灌一条 40 天前的变更 → 30 天窗排除
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        repo = AiGovernance46Repository()
        old_ts = (datetime.now(UTC)
                  - timedelta(days=40)).isoformat()
        cid = await repo.next_change_id()
        await repo.save_change({
            "changeId": cid, "govId": 1,
            "scorerId": "order_risk", "kind": "freeze",
            "payload": {}, "reason": "旧变更",
            "requestedBy": "admin", "status": "approved",
            "reviewedBy": "admin", "reviewNote": "",
            "error": "", "requestedAt": old_ts,
            "reviewedAt": old_ts})
        r30 = await svc.build_report(days=30)
        r90 = await svc.build_report(days=90)
        record("窗口过滤(30天排除旧)",
               r30["changes"]["total"] == 3
               and r90["changes"]["total"] == 4,
               f"30d={r30['changes']['total']} "
               f"90d={r90['changes']['total']}")

        # days 校验
        for bad in (0, -1, 366):
            try:
                await svc.build_report(days=bad)
                record(f"days非法拒绝({bad})",
                       False, "未抛")
            except ValueError:
                record(f"days非法拒绝({bad})", True)


class TestHttp:
    async def run(self):
        print("[03 HTTP 层]")
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
        client.post("/api/ai-gov/registry/sync",
                    headers=admin)

        # 鉴权
        resp = client.get("/api/ai-gov/compliance/filing")
        record("备案缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.get("/api/ai-gov/compliance/report")
        record("报告缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 备案 200
        resp = client.get(
            "/api/ai-gov/compliance/filing"
            "?scorerId=trust_value", headers=admin)
        body = resp.json()
        record("备案200单档案", resp.status_code == 200
               and body.get("count") == 1
               and len((body.get("filings")
                        or [{}])[0].get("sections")
                       or {}) == 6,
               str(body)[:60])
        # 全量
        resp = client.get("/api/ai-gov/compliance/filing",
                          headers=admin)
        body = resp.json()
        record("备案200全量29", resp.status_code == 200
               and body.get("count") == 30,
               str(body.get("count")))

        # 未入册 404
        resp = client.get(
            "/api/ai-gov/compliance/filing"
            "?scorerId=no_such", headers=admin)
        record("备案未入册404", resp.status_code == 404,
               str(resp.status_code))

        # 报告 200
        resp = client.get(
            "/api/ai-gov/compliance/report?days=30",
            headers=admin)
        body = resp.json()
        record("报告200", resp.status_code == 200
               and body.get("windowDays") == 30
               and "conclusion" in body,
               str(body)[:60])

        # days 边界
        resp = client.get(
            "/api/ai-gov/compliance/report?days=0",
            headers=admin)
        record("days0拒绝", resp.status_code == 422,
               str(resp.status_code))
        resp = client.get(
            "/api/ai-gov/compliance/report?days=400",
            headers=admin)
        record("days400拒绝", resp.status_code == 422,
               str(resp.status_code))

        # P0-P3 路由回归
        for name, path in (
                ("P0台账回归", "/api/ai-gov/registry"),
                ("P1健康回归", "/api/ai-gov/health"),
                ("P2报告回归",
                 "/api/ai-gov/fairness/report"),
                ("P3日志回归", "/api/ai-gov/replay"),
        ):
            resp = client.get(path, headers=admin)
            record(name, resp.status_code == 200,
                   str(resp.status_code))


async def run_all():
    await TestFiling().run()
    await TestAuditReport().run()
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
