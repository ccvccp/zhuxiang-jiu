"""全站批次四·治理类三模块 AI 升级 专项测试

运行方式:
    python test_batch4_ai.py

覆盖(《全站AI智能混合架构升级总计划》批次四):
    - trace_integrity 评分器(第46档案
      batch30——activate_life_code 首扫激活门)
    - compliance_inspection 评分器(第47
      档案 batch31——monitor_behavior 巡检门)
    - citystore_health 评分器(第48档案
      batch32——run_assessment 月度考核门)
    - 三门 observe 行为兼容+快照留痕
    - 三回流(处罚/巡检/考核终态→配对)
    - enforce 模式高风险拦截
    - 44号注册同步(48 档案)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["AI_ENFORCE_MODE"] = "observe"

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


class TestScorers:
    """01 三评分器单元"""

    async def run(self):
        print("[01 三评分器]")
        reset_all()
        from services.trace_integrity_scorer import (
            TraceIntegrityScorer,
        )
        from services.compliance_inspection_scorer import (
            ComplianceInspectionScorer,
        )
        from services.citystore_health_scorer import (
            CitystoreHealthScorer,
        )

        for cls in (TraceIntegrityScorer,
                    ComplianceInspectionScorer,
                    CitystoreHealthScorer):
            try:
                await cls().score({})
                ok = False
            except ValueError:
                ok = True
            record(f"空上下文拒绝({cls.__name__})",
                   ok)

        # trace 低/高
        r = await TraceIntegrityScorer().score({
            "lifeCode": "BLC-TEST-1", "orderId": "O1",
            "crossRegion": False,
            "purchaseChannel": "online",
            "purchasePrice": 288,
            "expectedPrice": 288,
            "boxCode": "TBC1",
            "boxOpened": False,
            "userActivationCount": 1})
        record("激活低风险",
               r.get("level") == "low",
               str((r.get("score"), r.get("level"))))
        r2 = await TraceIntegrityScorer().score({
            "lifeCode": "BLC-TEST-2", "orderId": "",
            "crossRegion": True,
            "purchaseChannel": "",
            "purchasePrice": 0,
            "boxCode": "", "boxOpened": True,
            "userActivationCount": 5})
        record("激活高风险 high",
               r2.get("level") == "high"
               and (r2.get("score") or 0) >= 60,
               str((r2.get("score"), r2.get("level"))))
        record("激活八因子",
               len(r.get("factors") or []) == 8, "")

        # compliance 低/高
        r3 = await ComplianceInspectionScorer().score({
            "amount": 100, "dailyTotal": 500,
            "entityViolations": 0,
            "action": "browse", "frequency": 1,
            "hasEvidence": True,
            "manualFlag": False})
        record("巡检低风险",
               r3.get("level") == "low",
               str((r3.get("score"), r3.get("level"))))
        r4 = await ComplianceInspectionScorer().score({
            "amount": 60000, "dailyTotal": 250000,
            "entityViolations": 3,
            "action": "withdraw", "frequency": 5,
            "hasEvidence": False,
            "manualFlag": True})
        record("巡检高风险 high",
               r4.get("level") == "high"
               and (r4.get("score") or 0) >= 60,
               str((r4.get("score"), r4.get("level"))))
        record("巡检八因子",
               len(r3.get("factors") or []) == 8, "")

        # citystore 低/高
        r5 = await CitystoreHealthScorer().score({
            "monthlyPurchase": 10000,
            "monthlySales": 6000,
            "purchaseTarget": 9000,
            "salesTarget": 5000,
            "consecutiveBelow": 0,
            "currentDiscount": 90,
            "status": 1, "channelCount": 3,
            "orderCount": 8})
        record("考核低风险",
               r5.get("level") == "low",
               str((r5.get("score"), r5.get("level"))))
        r6 = await CitystoreHealthScorer().score({
            "monthlyPurchase": 1000,
            "monthlySales": 200,
            "consecutiveBelow": 3,
            "currentDiscount": 70,
            "status": 3, "channelCount": 1,
            "orderCount": 0})
        record("考核高风险 high",
               r6.get("level") == "high"
               and (r6.get("score") or 0) >= 60,
               str((r6.get("score"), r6.get("level"))))
        record("考核八因子",
               len(r5.get("factors") or []) == 8, "")


class TestRegistry:
    """02 44号注册同步"""

    async def run(self):
        print("[02 注册表]")
        reset_all()
        from services.ai_learning_service import (
            SCORER_REGISTRY, DECISION_THRESHOLDS,
            default_weights,
        )
        record("48 档案",
               len(SCORER_REGISTRY) == 55,
               str(len(SCORER_REGISTRY)))
        for sid, batch in (
                ("trace_integrity", 30),
                ("compliance_inspection", 31),
                ("citystore_health", 32)):
            entry = SCORER_REGISTRY.get(sid)
            record(f"{sid} batch{batch} 入册",
                   (entry or {}).get("batch") == batch,
                   str(entry))
            record(f"{sid} 阈值三级",
                   DECISION_THRESHOLDS.get(sid)
                   == [(60.0, "high"),
                       (30.0, "medium"),
                       (0.0, "low")], "")
            record(f"{sid} 默认权重八因子",
                   len(default_weights(sid)) == 8, "")
        from services.ai_feedback_hooks import (
            _invoke_scorer,
        )
        for sid in ("trace_integrity",
                    "compliance_inspection",
                    "citystore_health"):
            result = await _invoke_scorer(
                sid, {"lifeCode": "L", "amount": 1,
                      "monthlyPurchase": 1,
                      "monthlySales": 1})
            record(f"挂钩分支可调({sid})",
                   (result or {}).get("scorer")
                   == sid, "")


class TestGates:
    """03 三门接线 observe 兼容+快照+回流"""

    async def run(self):
        print("[03 三门接线]")
        reset_all()
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        repo = AiLearningRepository()

        # ---- ① trace 激活门 ----
        from services.trace_service import (
            TraceService,
        )
        tsvc = TraceService()
        codes = await tsvc.generate_life_codes(
            "ZX42-B4TEST", "B4批次", 5)
        life_code = codes["lifeCodes"][0][
            "lifeCode"]
        r = await tsvc.activate_life_code(
            life_code, user_id=7701,
            user_name="B4用户",
            purchase_channel="online",
            purchase_price=288.0,
            order_id="O-B4-1")
        record("激活 observe 兼容",
               (r or {}).get("status") == "active",
               str((r or {}).get("status")))
        snap = await repo.get_decision_snapshot(
            "trace_integrity",
            f"activate:{life_code}")
        record("激活快照已存",
               snap is not None
               and snap.get("decision") == "low",
               str(snap)[:50])
        # 硬规则: 重复激活拒绝
        try:
            await tsvc.activate_life_code(
                life_code, user_id=7701)
            ok = False
        except ValueError:
            ok = True
        record("一瓶一激活硬规则保留", ok)

        # ---- ② compliance 巡检门 ----
        from services.compliance_service import (
            ComplianceService,
        )
        csvc = ComplianceService()
        r = await csvc.monitor_behavior(
            "B4模块", "browse",
            behavior_data={"amount": 100},
            risk_level="low")
        record("巡检 observe 兼容",
               r.get("riskLevel") == "low",
               str(r.get("riskLevel")))
        fbs = await repo.list_feedback(
            "compliance_inspection")
        record("巡检→自动反馈",
               len([f for f in fbs
                    if f.get("source") == "auto"
                    and f.get("actualAction")
                    == "monitored_low"]) >= 1,
               str(len(fbs)))
        # 硬规则: 非法等级拒绝
        try:
            await csvc.monitor_behavior(
                "B4模块", "browse",
                risk_level="invalid")
            ok = False
        except ValueError:
            ok = True
        record("风险等级硬规则保留", ok)

        # ---- ③ citystore 考核门 ----
        from services.citystore_service import (
            CityStoreService,
        )
        ssvc = CityStoreService()
        store = await ssvc.apply(
            member_id=1001, member_level=5,
            store_name="B4测试网店",
            city_code="370100",
            city_name="济南市",
            province_code="370000",
            province_name="山东省",
            business_license="91370100MA00B4XX",
            food_license="JY1370100B4",
            tax_reg_no="91370100MA00B4XX")
        sc = store["storeCode"]
        await ssvc.audit_store(
            sc, "admin", True)
        # 达标订单(采购口径由 orders 统计)
        await ssvc.add_order(
            sc, "O-B4-S1", "P1", "B4酒",
            50, 288.0, 14400.0,
            sales_channel=1)
        month = "2026-08"
        r = await ssvc.run_assessment(sc, month)
        record("考核 observe 兼容",
               r.get("qualificationStatus") in
               (1, 2, 3),
               str(r.get("qualificationStatus")))
        fbs = await repo.list_feedback(
            "citystore_health")
        record("考核→自动反馈",
               len([f for f in fbs
                    if f.get("source") == "auto"
                    and f.get("actualAction")
                    .startswith("qual_")]) >= 1,
               str(len(fbs)))
        # 硬规则: 重复考核拒绝
        try:
            await ssvc.run_assessment(sc, month)
            ok = False
        except ValueError:
            ok = True
        record("重复考核硬规则保留", ok)


class TestEnforceMode:
    """04 enforce 模式拦截"""

    async def run(self):
        print("[04 enforce 拦截]")
        reset_all()
        os.environ["AI_ENFORCE_MODE"] = "enforce"
        os.environ["AI_ENFORCE_SCOPES"] = \
            "trace_integrity,compliance" \
            "_inspection,citystore_health"
        try:
            from repositories.ai_learning_repository import (
                AiLearningRepository,
            )
            repo = AiLearningRepository()
            for sid in ("trace_integrity",
                        "compliance_inspection",
                        "citystore_health"):
                for _ in range(55):
                    await repo.add_feedback({
                        "scorerId": sid,
                        "factors": [{
                            "name": "x",
                            "score": 10.0,
                            "weight": 0.5}],
                        "correct": True,
                        "createdAt":
                            "2026-01-01T00:00:00",
                    })

            # ① 激活高风险拦截(直接构造
            # 高风险 ctx——单元级验门)
            from services.ai_enforcement_governance import (
                enrich_trace_activation,
                enforce_trace_activation,
            )
            ctx = await enrich_trace_activation({
                "lifeCode": "BLC-B4-HIGH",
                "orderId": "", "boxCodeId": None,
                "crossRegion": True}, 7702)
            # 叠加极端画像(评分器口径)
            ctx.update(crossRegion=True,
                       purchaseChannel="",
                       purchasePrice=0,
                       boxCode="", boxOpened=True,
                       userActivationCount=5)
            blocked = False
            try:
                await enforce_trace_activation(
                    "BLC-B4-HIGH", ctx)
            except ValueError as exc:
                blocked = "风控拦截" in str(exc)
            record("enforce 激活拦截(单元)",
                   blocked, "未拦截")

            # ② 巡检高风险拦截(直接构造
            # 高风险 ctx——单元级验门)
            from services.ai_enforcement_governance import (
                enrich_behavior_monitor,
                enforce_behavior_monitor,
            )
            ctx2 = await enrich_behavior_monitor(
                "B4模块", "withdraw",
                {"amount": 60000})
            ctx2.update(
                dailyTotal=250000.0,
                entityViolations=3,
                frequency=5,
                hasEvidence=False,
                manualFlag=True)
            blocked = False
            try:
                await enforce_behavior_monitor(
                    "B4-key-1", ctx2)
            except ValueError as exc:
                blocked = "风控拦截" in str(exc)
            record("enforce 巡检拦截(单元)",
                   blocked, "未拦截")

            # ③ 考核高风险拦截(单元)
            from services.ai_enforcement_governance import (
                enrich_citystore_health,
                enforce_citystore_assessment,
            )
            ctx3 = await enrich_citystore_health({
                "storeCode": "CS-B4",
                "consecutiveBelowPurchase": 3,
                "consecutiveBelowSales": 3,
                "currentDiscount": 70,
                "status": 3}, 100, 200)
            blocked = False
            try:
                await enforce_citystore_assessment(
                    "CS-B4", "2026-08", ctx3)
            except ValueError as exc:
                blocked = "风控拦截" in str(exc)
            record("enforce 考核拦截(单元)",
                   blocked, "未拦截")

            # ④ 低风险放行(三门)
            ctx_ok = await enrich_trace_activation({
                "lifeCode": "BLC-B4-LOW",
                "orderId": "O1",
                "boxCodeId": None}, 7703)
            ok1 = False
            try:
                r = await enforce_trace_activation(
                    "BLC-B4-LOW", ctx_ok)
                ok1 = r.get("blocked") is False
            except ValueError:
                ok1 = False
            record("enforce 激活低风险放行",
                   ok1, "被误拦")
        finally:
            os.environ["AI_ENFORCE_MODE"] = \
                "observe"
            os.environ.pop(
                "AI_ENFORCE_SCOPES", None)


class TestConstitution:
    """05 宪法铁律"""

    async def run(self):
        print("[05 宪法铁律]")
        reset_all()
        os.environ.pop("AI_ENFORCE_MODE", None)
        from services.ai_enforcement import (
            enforcement_mode,
        )
        record("决策门默认 observe",
               all(enforcement_mode(s)
                   == "observe"
                   for s in ("trace_integrity",
                             "compliance_inspection",
                             "citystore_health")),
               "")
        from services.citystore_health_scorer import (
            CitystoreHealthScorer,
        )
        ctx = {"monthlyPurchase": 10000,
               "monthlySales": 6000}
        r1 = await CitystoreHealthScorer() \
            .score(dict(ctx))
        r2 = await CitystoreHealthScorer() \
            .score(dict(ctx))
        record("确定性评分(同入同出)",
               r1["score"] == r2["score"], "")
        # orderId 契约保留(激活门不吞订单号)
        from services.trace_service import (
            TraceService,
        )
        tsvc = TraceService()
        codes = await tsvc.generate_life_codes(
            "ZX42-B4CON", "B4C批次", 2)
        life_code = codes["lifeCodes"][0][
            "lifeCode"]
        r = await tsvc.activate_life_code(
            life_code, user_id=7704,
            order_id="O-CONTRACT-1")
        life = await tsvc.repo.get_life_by_code(
            life_code)
        record("orderId 分润契约保留",
               (life or {}).get("orderId")
               == "O-CONTRACT-1",
               str((life or {}).get("orderId")))


async def main():
    print("=" * 62)
    print("全站批次四·治理类三模块 AI 升级"
          " 专项测试")
    print("=" * 62)
    for cls in (TestScorers, TestRegistry,
                TestGates, TestEnforceMode,
                TestConstitution):
        await cls().run()
    print(f"\n{'=' * 62}")
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 62)
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
