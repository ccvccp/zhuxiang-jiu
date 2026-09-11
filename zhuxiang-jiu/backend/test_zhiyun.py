"""智运·AI智能物流大模型 专项测试(P0-P3 全量)

覆盖:
    [数据织物 zw_fabric_service]
    1.  订单聚合(总数/签收率/在途)
    2.  时效聚合(下单→签收均时长)
    3.  成本聚合(总费/均费)
    4.  物流商表现聚合(按承运商分组)
    [P0 智能路由 zw_route_service]
    5.  质量评分三因子(签收率40+时效30+费率30)
    6.  冷启动默认 70 分
    7.  多维路由决策(规则×0.6+质量×0.4)
    8.  决策留痕(候选对比)
    9.  路由边界(非法订单类型 409 口径)
    10. 健康度监测(失败率超阈值→降级建议书)
    11. 建议书口径(永不自动切换)
    [P1 轨迹智能 zw_track_service]
    12. ETA 预测(在途单外推)
    13. ETA 已签收(剩余 0)
    14. ETA 不存在运单(404 口径 KeyError)
    15. 异常四检测器(揽收超时)
    16. 异常-派送失败
    17. 异常-签收超时
    18. 延误预警总览(阈值口径)
    [P2 风控回执 zw_risk_service]
    19. 四防评分(防破损重量因子)
    20. 偏远+高货值丢失分
    21. urgent 延误分
    22. 风险四级分档
    23. 高风险缓解建议(木架/保价)
    24. 验货回执-少收(补寄建议)
    25. 验货回执-一致(通过)
    26. 理赔四类(破损/丢失/延误/污损)
    27. 理赔边界(非法类型/金额 409)
    28. 理赔建议书口径(赔付永不自动)
    [P3 分析进化 zw_analysis_service]
    29. 成本分析(物流商对比+议价建议)
    30. 运量预测(0.6+0.4 权重)
    31. 预测边界(期数越界)
    32. 反馈闭环(adopted→etaWeight+0.1)
    33. 反馈安全阀(clamp [0.4, 0.8])
    34. rejected 降权
    35. corrected 不调参
    36. 非法目标/裁决
    37. 孪生总览(四引擎+参数)
    38. 大模型总览

运行: python backend/test_zhiyun.py(自跑范式, 内存模式)
"""
import asyncio
import os
import sys
from datetime import datetime, UTC, timedelta

# 确保使用内存模式(不触碰 Redis)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repositories.store import reset_store as _reset_store
from repositories.logistics_repository import LogisticsRepository
from services.zw_fabric_service import _ZwStore, ZwFabricService
from services.zw_route_service import ZwRouteService
from services.zw_track_service import ZwTrackService
from services.zw_risk_service import ZwRiskService
from services.zw_analysis_service import ZwAnalysisService

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def _order(waybill, carrier, status, created_hours_ago,
           signed_hours_after=None, fee=28.0):
    now = datetime.now(UTC)
    created = now - timedelta(hours=created_hours_ago)
    order = {
        "waybillNo": waybill, "orderId": f"O-{waybill}",
        "orderType": "retail", "carrier": carrier,
        "status": status, "totalFee": fee,
        "createdAt": created.isoformat(),
    }
    if status == "signed" and signed_hours_after is not None:
        signed_at = created + timedelta(hours=signed_hours_after)
        order["signedAt"] = signed_at.isoformat()
    if status not in ("pending", "booked"):
        order["pickedAt"] = (created
                             + timedelta(hours=1)).isoformat()
    return order


async def _seed_orders(orders):
    repo = LogisticsRepository()
    for o in orders:
        await repo.save_order(o)


class TestFabric:

    async def run(self):
        _reset_store()
        await _seed_orders([
            _order("SF1", "SF", "signed", 100, signed_hours_after=48),
            _order("SF2", "SF", "signed", 200, signed_hours_after=72,
                   fee=38.0),
            _order("SF3", "SF", "transporting", 10, fee=28.0),
            _order("YT1", "YT", "signed", 150, signed_hours_after=120,
                   fee=12.0),
            _order("YT2", "YT", "failed", 80, fee=12.0),
        ])
        fabric = ZwFabricService()
        lake = await fabric.lake_overview()
        record("织物-订单聚合", lake["orders"]["total"] == 5
               and lake["orders"]["signed"] == 3
               and lake["orders"]["inTransit"] == 1,
               str(lake["orders"]))
        record("织物-签收率", lake["orders"]["signRate"] == 0.6)
        # 时效: 48+72+120 均值 80
        record("织物-时效聚合", lake["timeliness"]["avgSignHours"] == 80.0,
               str(lake["timeliness"]))
        record("织物-成本聚合", lake["cost"]["totalFee"] == 118.0
               and lake["cost"]["avgFee"] == 23.6,
               str(lake["cost"]))
        perf = await fabric.carrier_performance()
        record("织物-物流商聚合", perf["SF"]["total"] == 3
               and perf["SF"]["avgSignHours"] == 60.0
               and perf["YT"]["total"] == 2,
               str(perf.get("SF")))


class TestP0Route:

    async def run(self):
        _reset_store()
        await _seed_orders([
            _order("SF1", "SF", "signed", 100, signed_hours_after=36,
                   fee=28.0),
            _order("SF2", "SF", "signed", 200, signed_hours_after=36,
                   fee=28.0),
            _order("YT1", "YT", "signed", 150, signed_hours_after=100,
                   fee=12.0),
            _order("YT2", "YT", "failed", 80, fee=12.0),
            _order("YT3", "YT", "failed", 82, fee=12.0),
            _order("YT4", "YT", "failed", 84, fee=12.0),
            _order("YT5", "YT", "failed", 86, fee=12.0),
        ])
        store = _ZwStore()
        svc = ZwRouteService(store=store)

        scores = await svc.carrier_scores()
        sf = scores["SF"]
        # 签收率1.0 时效36h<72基准→1.0 费率28<30→1.0 → 100
        record("P0-质量评分三因子", sf["score"] == 100.0
               and sf["sample"] == 2, str(sf))
        jd = scores["JD"]
        record("P0-冷启动默认70", jd["score"] == 70.0
               and jd["coldStart"] is True)

        d = await svc.route_decide(
            order_type="retail", weight=5.0, piece_count=2,
            insured_value=500.0,
            sender={"city": "济南", "address": "济南市历城区仓库"},
            receiver={"province": "山东", "city": "济南",
                      "address": "济南市历下区"})
        record("P0-多维路由决策", d["decision"]["carrier"] == "SF"
               and d["decision"]["combinedScore"] > 0)
        record("P0-决策留痕", len(d["candidates"]) >= 1
               and "formula" in d)
        try:
            await svc.route_decide(order_type="hack", weight=1,
                                   piece_count=1, insured_value=0,
                                   sender={}, receiver={})
            record("P0-路由边界", False)
        except ValueError:
            record("P0-路由边界", True)

        health = await svc.carrier_health()
        yt = next(c for c in health["carriers"]
                  if c["carrier"] == "YT")
        record("P0-健康度降级", yt["health"] == "degraded"
               and yt["failRate"] == 0.8)
        record("P0-建议书口径",
               "switchSuggestion" in yt
               and "永不自动" in yt["switchSuggestion"]["disposition"])


class TestP1Track:

    async def run(self):
        _reset_store()
        # 在途单 10h 前下单, 已揽收; 历史签收 36h
        await _seed_orders([
            _order("SF1", "SF", "signed", 100, signed_hours_after=36),
            _order("SF2", "SF", "signed", 200, signed_hours_after=36),
            _order("SF3", "SF", "transporting", 10),
            # 异常单: 20h 前下单未揽收(>4h 阈值)
            _order("SF4", "SF", "booked", 20),
            # 派送失败
            _order("SF5", "SF", "failed", 30),
            # 签收超时: 10 天前揽收未签收
            _order("SF6", "SF", "delivering", 24 * 10 + 5),
        ])
        store = _ZwStore()
        svc = ZwTrackService(store=store)

        eta = await svc.eta_predict("SF3")
        # 历史均 36h × 1.15 = 41.4 - 已耗 10 ≈ 31.4
        record("P1-ETA在途外推", eta["remainingHours"] > 25
               and eta["remainingHours"] < 35
               and eta["basis"].startswith("SF"), str(eta))

        eta2 = await svc.eta_predict("SF1")
        record("P1-ETA已签收", eta2["remainingHours"] == 0.0)

        try:
            await svc.eta_predict("NOT-EXIST")
            record("P1-ETA不存在", False)
        except KeyError:
            record("P1-ETA不存在", True)

        anomalies = await svc.detect_anomalies()
        types = {a["waybillNo"]: a["type"] for a in anomalies}
        record("P1-揽收超时", types.get("SF4") == "pickup_timeout")
        record("P1-派送失败", types.get("SF5") == "deliver_failed")
        record("P1-签收超时", types.get("SF6") == "sign_timeout")

        delays = await svc.delay_warnings()
        record("P1-延误总览", delays["total"] >= 2
               and "thresholds" in delays)


class TestP2Risk:

    async def run(self):
        _reset_store()
        store = _ZwStore()
        svc = ZwRiskService(store=store)

        r = await svc.risk_assess(order_type="retail", weight=10.0,
                                  piece_count=2, insured_value=300.0)
        # 防破损 40+20=60 / 防丢失 20 / 防延误 25
        # 0.35×60+0.25×20+0.4×25 = 36
        record("P2-四防评分", r["damageScore"] == 60.0
               and r["lossScore"] == 20.0
               and r["delayScore"] == 25.0
               and r["riskScore"] == 36.0, str(r))

        r2 = await svc.risk_assess(order_type="retail", weight=10.0,
                                   piece_count=2, insured_value=8000.0,
                                   receiver_province="新疆", urgent=True)
        # 防丢失 20+30+20=70 / 防延误 25+30=55
        # 0.35×60+0.25×70+0.4×55 = 21+17.5+22 = 60.5
        record("P2-偏远高货值", r2["lossScore"] == 70.0
               and r2["delayScore"] == 55.0
               and r2["riskLevel"] in ("high", "extreme"), str(r2))

        r3 = await svc.risk_assess(order_type="retail", weight=50.0,
                                   piece_count=10, insured_value=20000.0)
        # 防破损 40+100=140→100; 0.35×100+0.25×40+0.4×25=55
        record("P2-风险分档", r3["riskLevel"] in
               ("low", "medium", "high", "extreme"))
        record("P2-高风险缓解", any("保价" in s or "木架" in s
                                    for s in r2["suggestions"]))

        ir = await svc.inspect_receipt(order_id="TG-001",
                                       expected_count=200,
                                       actual_count=198,
                                       inspector="王经理")
        record("P2-验货少收", ir["result"] == "shortage"
               and ir["diff"] == -2
               and "人工" in ir["disposition"])
        ir2 = await svc.inspect_receipt(order_id="TG-002",
                                        expected_count=100,
                                        actual_count=100,
                                        inspector="李经理")
        record("P2-验货一致", ir2["result"] == "pass")

        c = await svc.create_claim(waybill_no="SF1", order_id="O-1",
                                   carrier="SF", claim_type="damage",
                                   claim_amount=268.0,
                                   description="瓶身破损")
        record("P2-理赔破损", c["claimTypeName"] == "破损理赔"
               and c["standard"] == "保价金额全额赔付"
               and c["slaDays"] == 7)
        for bad_args in (
                dict(waybill_no="SF1", order_id="O", carrier="SF",
                     claim_type="hack", claim_amount=100),
                dict(waybill_no="SF1", order_id="O", carrier="SF",
                     claim_type="damage", claim_amount=-5)):
            try:
                await svc.create_claim(**bad_args)
                record("P2-理赔边界", False)
            except ValueError:
                record("P2-理赔边界", True)
                break
        c2 = await svc.create_claim(waybill_no="SF2", order_id="O-2",
                                    carrier="SF", claim_type="delay",
                                    claim_amount=28.0)
        record("P2-理赔建议书",
               "永不自动" in c2["disposition"]
               and c2["status"] == "pending_review")


class TestP3Analysis:

    async def run(self):
        _reset_store()
        await _seed_orders([
            _order("SF1", "SF", "signed", 100, signed_hours_after=36,
                   fee=28.0),
            _order("SF2", "SF", "signed", 200, signed_hours_after=36,
                   fee=28.0),
            _order("YT1", "YT", "signed", 150, signed_hours_after=100,
                   fee=12.0),
            _order("DB1", "DB", "signed", 120, signed_hours_after=60,
                   fee=55.0),
            _order("DB2", "DB", "signed", 130, signed_hours_after=60,
                   fee=55.0),
            _order("DB3", "DB", "signed", 140, signed_hours_after=60,
                   fee=55.0),
        ])
        store = _ZwStore()
        svc = ZwAnalysisService(store=store)

        cost = await svc.cost_analysis()
        record("P3-成本对比", len(cost["byCarrier"]) == 3
               and cost["byCarrier"][0]["carrier"] == "YT")
        record("P3-议价建议", any("议价" in s or "再平衡" in s
                                  for s in cost["suggestions"]),
               str(cost["suggestions"]))

        vf = await svc.volume_forecast(horizon=2)
        record("P3-运量预测", vf["historyMonths"] >= 1
               and len(vf["rows"]) == 2
               and vf["rows"][0]["predictedOrders"] > 0)
        try:
            await svc.volume_forecast(horizon=10)
            record("P3-预测边界", False)
        except ValueError:
            record("P3-预测边界", True)

        f = await svc.feedback("route_decision", "adopted", "准")
        record("P3-反馈升权", f["etaWeightAfter"] == 0.7)
        for _ in range(10):
            await svc.feedback("eta", "adopted")
        params = await svc.get_params()
        record("P3-安全阀clamp", params["etaWeight"] == 0.8)
        await svc.feedback("eta", "rejected", "误报")
        params = await svc.get_params()
        record("P3-拒绝降权", params["etaWeight"] == 0.7)
        await svc.feedback("risk_assess", "corrected", "部分")
        params2 = await svc.get_params()
        record("P3-corrected不调参",
               params2["etaWeight"] == params["etaWeight"])
        for args in (("hacking", "adopted"), ("eta", "later")):
            try:
                await svc.feedback(*args)
                record("P3-非法反馈", False, str(args))
                break
            except ValueError:
                record("P3-非法反馈", True)

        tw = await svc.twin()
        record("P3-孪生总览", "engines" in tw
               and tw["evolution"]["etaWeight"] == 0.7
               and "lake" in tw)
        st = await svc.status()
        record("P3-大模型总览", st["module"] == "智运·AI智能物流大模型"
               and st["orders"] >= 6)


async def main():
    await TestFabric().run()
    await TestP0Route().run()
    await TestP1Track().run()
    await TestP2Risk().run()
    await TestP3Analysis().run()

    print("\n".join(RESULTS))
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
