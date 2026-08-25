"""AI 语义评分层测试(Service 层 + HTTP 层, 39 项)

覆盖 5 个评分器:
    OrderRiskScorer(6) / PaymentRoutingScorer(6) / LogisticsRoutingScorer(7)
    TrafficAntiFraudScorer(4) / PromotionAntiFraudScorer(4)
    + HTTP 层(12): 全部 5 端点的 403 鉴权 / 200 正常调用 / 422 参数校验 / 409 业务冲突

在宿主机运行(需已安装 fastapi + httpx):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_ai_scoring_routes.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")

from services.ai_scoring_service import (
    OrderRiskScorer, PaymentRoutingScorer, LogisticsRoutingScorer,
    TrafficAntiFraudScorer, PromotionAntiFraudScorer,
)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


async def _expect_value_error(name, coro):
    try:
        await coro
        record(name, False, "未抛出 ValueError")
    except ValueError:
        record(name, True)
    except Exception as exc:
        record(name, False, f"异常类型错误: {type(exc).__name__}: {exc}")


async def main():
    order = OrderRiskScorer()
    payment = PaymentRoutingScorer()
    logistics = LogisticsRoutingScorer()
    traffic = TrafficAntiFraudScorer()
    promotion = PromotionAntiFraudScorer()

    print("=" * 64)
    print("AI 语义评分层测试(5 评分器: 订单风控/支付路由/物流路由/流量防作弊/推广码防作弊)")
    print("=" * 64)

    # ========================================================
    # 1. 订单风控评分
    # ========================================================
    r = await order.score({
        "bambooScore": 850, "registerHours": 8760, "orderAmount": 299.0,
        "totalQuantity": 2, "historyOrders": 30, "historyCancels": 1,
        "addressComplete": True, "remark": "老客复购", "orderHour": 15,
    })
    record("01_order_low_risk_pass",
           r["level"] == "low" and r["action"] == "pass" and r["score"] < 35,
           f"score={r['score']}, level={r['level']}, action={r['action']}")

    r = await order.score({
        "bambooScore": 100, "registerHours": 2, "orderAmount": 15800.0,
        "totalQuantity": 25, "historyOrders": 8, "historyCancels": 6,
        "addressComplete": False, "remark": "刷单返现", "orderHour": 3,
    })
    record("02_order_high_risk_block",
           r["level"] == "high" and r["action"] == "block" and r["score"] >= 65,
           f"score={r['score']}, level={r['level']}, action={r['action']}")

    r = await order.score({
        "bambooScore": 450, "registerHours": 48, "orderAmount": 3000.0,
        "totalQuantity": 3, "historyOrders": 5, "historyCancels": 2,
        "addressComplete": True, "remark": "", "orderHour": 10,
    })
    record("03_order_medium_risk_review",
           r["level"] == "medium" and r["action"] == "review",
           f"score={r['score']}, level={r['level']}")

    r = await order.score({"orderAmount": 500.0, "bambooScore": 700,
                           "registerHours": 1000, "historyOrders": 10})
    total_contrib = round(sum(f["contribution"] for f in r["factors"]), 1)
    record("04_order_factor_sum_consistency",
           abs(r["score"] - total_contrib) < 0.5 and len(r["factors"]) == 8,
           f"score={r['score']}, sum={total_contrib}, factors={len(r['factors'])}")

    r = await order.score({"orderAmount": 100.0})
    full = await order.score({"orderAmount": 100.0, "bambooScore": 600,
                              "registerHours": 100, "historyOrders": 5})
    record("05_order_confidence_drops_with_missing_fields",
           r["confidence"] < full["confidence"] and r["confidence"] >= 0.3,
           f"partial={r['confidence']}, full={full['confidence']}")

    await _expect_value_error(
        "06_order_negative_amount_rejected",
        order.score({"orderAmount": -1}))

    # ========================================================
    # 2. 支付路由评分
    # ========================================================
    r = await payment.score({"amount": 299.0, "sceneType": "order_pay"})
    rec = r["recommendation"]["channelCode"]
    record("07_payment_retail_prefers_third_party",
           rec in ("wechat", "alipay") and r["candidates"][0]["rank"] == 1,
           f"recommendation={rec}")

    r = await payment.score({"amount": 80000.0, "sceneType": "agent_purchase"})
    rec = r["recommendation"]["channelCode"]
    record("08_payment_agent_purchase_prefers_bank",
           rec in ("bank", "unionpay"),
           f"recommendation={rec}(大额代理进货应路由银行渠道)")

    r = await payment.score({
        "amount": 500.0, "sceneType": "order_pay",
        "channels": [
            {"channelCode": "wechat", "channelType": "third_party",
             "feeRate": 0.006, "fixedFee": 0, "minAmount": 0.01,
             "maxAmount": 50000, "dailyLimit": 100000, "dailyAmount": 99900,
             "status": "active"},
            {"channelCode": "alipay", "channelType": "third_party",
             "feeRate": 0.0055, "fixedFee": 0, "minAmount": 0.01,
             "maxAmount": 50000, "dailyLimit": 100000, "dailyAmount": 0,
             "status": "active"},
        ]})
    rec = r["recommendation"]["channelCode"]
    wechat = next(c for c in r["candidates"] if c["channelCode"] == "wechat")
    record("09_payment_daily_limit_exhausted_falls_back",
           rec == "alipay" and wechat["eligible"] is False,
           f"recommendation={rec}, wechat eligible={wechat['eligible']}")

    r = await payment.score({
        "amount": 100.0, "sceneType": "order_pay",
        "channels": [{"channelCode": "wechat", "channelType": "third_party",
                      "feeRate": 0.006, "fixedFee": 0, "minAmount": 0.01,
                      "maxAmount": 50000, "dailyLimit": 100000,
                      "status": "maintenance"}]})
    record("10_payment_maintenance_channel_not_recommended",
           r["recommendation"]["channelCode"] == ""
           and r["candidates"][0]["eligible"] is False,
           f"recommendation={r['recommendation']}")

    await _expect_value_error(
        "11_payment_zero_amount_rejected",
        payment.score({"amount": 0, "sceneType": "order_pay"}))

    await _expect_value_error(
        "12_payment_invalid_scene_rejected",
        payment.score({"amount": 100.0, "sceneType": "vip_pay"}))

    # ========================================================
    # 3. 物流路由评分
    # ========================================================
    r = await logistics.score({"weight": 2.0, "insuredValue": 500.0,
                               "settleMode": "monthly", "sameCity": True,
                               "budget": "balanced"})
    rec = r["recommendation"]["carrier"]
    record("13_logistics_same_city_balanced_prefers_sf_jd",
           rec in ("SF", "JD"),
           f"recommendation={rec}")

    r = await logistics.score({"weight": 2.0, "budget": "cost"})
    rec = r["recommendation"]["carrier"]
    record("14_logistics_cost_budget_prefers_yt",
           rec == "YT",
           f"recommendation={rec}(成本优先应选圆通)")

    r = await logistics.score({"weight": 2.0, "budget": "speed",
                               "serviceType": "express"})
    rec = r["recommendation"]["carrier"]
    record("15_logistics_speed_budget_prefers_sf",
           rec == "SF",
           f"recommendation={rec}(时效优先应选顺丰)")

    r = await logistics.score({"weight": 3000.0})
    record("16_logistics_overweight_no_recommendation",
           r["recommendation"]["carrier"] == ""
           and all(c["eligible"] is False for c in r["candidates"]),
           f"recommendation={r['recommendation']}")

    r = await logistics.score({"weight": 5.0, "insuredValue": 5000.0})
    lll = next(c for c in r["candidates"] if c["carrier"] == "LLL")
    record("17_logistics_high_insured_excludes_lll",
           lll["eligible"] is False,
           f"LLL eligible={lll['eligible']}(高保价不支持保价应淘汰)")

    r = await logistics.score({"weight": 2.0, "settleMode": "monthly"})
    yt = next(c for c in r["candidates"] if c["carrier"] == "YT")
    sf = next(c for c in r["candidates"] if c["carrier"] == "SF")
    record("18_logistics_monthly_settle_supported",
           yt["factors"]["settleFit"] == 100 and sf["factors"]["settleFit"] == 100,
           "圆通/顺丰均支持月结")

    r = await logistics.score({"weight": 30.0, "sameCity": True})
    lll = next(c for c in r["candidates"] if c["carrier"] == "LLL")
    sf = next(c for c in r["candidates"] if c["carrier"] == "SF")
    record("19_logistics_same_city_carriers_get_region_bonus",
           lll["factors"]["regionFit"] == 100 and sf["factors"]["regionFit"] == 100,
           "同城承运商区域适配满分")

    await _expect_value_error(
        "20_logistics_zero_weight_rejected",
        logistics.score({"weight": 0}))

    # ========================================================
    # 4. 流量防作弊评分
    # ========================================================
    r = await traffic.score({
        "promoterId": 1, "recentCount": 3, "avgIntervalSeconds": 3600,
        "newAccountRatio": 0.1, "nightRatio": 0.05, "conversionRate": 0.3,
        "uniqueSources": 6, "totalRecords": 100, "effectiveRecords": 70,
        "fraudCount": 0,
    })
    record("21_traffic_normal_pass",
           r["level"] == "low" and r["action"] == "pass",
           f"score={r['score']}, level={r['level']}")

    r = await traffic.score({
        "promoterId": 2, "recentCount": 30, "avgIntervalSeconds": 3,
        "newAccountRatio": 0.95, "nightRatio": 0.6, "conversionRate": 0.97,
        "uniqueSources": 1, "totalRecords": 200, "effectiveRecords": 20,
        "fraudCount": 2,
    })
    record("22_traffic_script_fraud_block",
           r["level"] == "high" and r["action"] == "block" and r["score"] >= 60,
           f"score={r['score']}, level={r['level']}, action={r['action']}")

    r = await traffic.score({"promoterId": 3, "recentCount": 5,
                             "totalRecords": 50, "newAccountRatio": 0.5,
                             "fraudCount": 3})
    record("23_traffic_history_fraud_escalates",
           r["score"] >= 30,
           f"score={r['score']}(历史作弊3次应至少中风险)")

    r = await traffic.score({"promoterId": 4, "conversionRate": 0.99,
                             "totalRecords": 10, "recentCount": 1,
                             "newAccountRatio": 0})
    conv = next(f for f in r["factors"] if f["name"] == "conversion")
    record("24_traffic_conversion_anomaly_scored",
           conv["score"] > 0,
           f"conversion factor score={conv['score']}")

    # ========================================================
    # 5. 推广码防作弊评分
    # ========================================================
    r = await promotion.score({
        "promoterId": 11, "relationCount": 120, "avgBindToRewardHours": 120,
        "inactiveInviteeRatio": 0.1, "nightBindRatio": 0.05,
        "fastestHundredDays": 30, "selfLoopSuspect": False,
        "revokedCount": 0, "appealCount": 0,
    })
    record("25_promotion_normal_pay",
           r["level"] == "low" and r["action"] == "pay",
           f"score={r['score']}, level={r['level']}")

    r = await promotion.score({
        "promoterId": 12, "relationCount": 100, "avgBindToRewardHours": 0.5,
        "inactiveInviteeRatio": 0.9, "nightBindRatio": 0.7,
        "fastestHundredDays": 1, "selfLoopSuspect": True,
        "revokedCount": 2, "appealCount": 1,
    })
    record("26_promotion_hard_fraud_review",
           r["level"] == "high" and r["action"] == "review" and r["score"] >= 60,
           f"score={r['score']}, level={r['level']}, action={r['action']}")

    r = await promotion.score({"promoterId": 13, "relationCount": 60,
                               "avgBindToRewardHours": 5,
                               "inactiveInviteeRatio": 0.6})
    record("27_promotion_medium_risk_hold",
           r["level"] == "medium" and r["action"] == "hold",
           f"score={r['score']}, level={r['level']}")

    r = await promotion.score({"promoterId": 14, "relationCount": 10,
                               "avgBindToRewardHours": 100,
                               "inactiveInviteeRatio": 0.2,
                               "selfLoopSuspect": True})
    loop = next(f for f in r["factors"] if f["name"] == "loop_suspect")
    record("28_promotion_loop_suspect_full_score",
           loop["score"] == 100 and loop["contribution"] == 20.0,
           f"loop factor={loop}")

    # ========================================================
    # 6. HTTP 层(TestClient 全栈)
    # ========================================================
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    resp = client.post("/api/ai-scoring/order-risk",
                       json={"orderAmount": 500.0})
    record("29_http_requires_admin_role",
           resp.status_code == 403, f"status={resp.status_code}")

    resp = client.post("/api/ai-scoring/order-risk", headers={"X-Role": "admin"},
                       json={"orderAmount": 500.0, "bambooScore": 800,
                             "registerHours": 5000, "historyOrders": 20})
    body = resp.json()
    record("30_http_order_risk_success",
           resp.status_code == 200 and body.get("success") is True
           and body.get("scorer") == "order_risk" and "factors" in body,
           f"status={resp.status_code}, body={body}")

    resp = client.post("/api/ai-scoring/payment-routing",
                       headers={"X-Role": "admin"},
                       json={"amount": -5, "sceneType": "order_pay"})
    record("31_http_payment_negative_amount_422",
           resp.status_code == 422, f"status={resp.status_code}(Pydantic gt=0 拦截)")

    resp = client.post("/api/ai-scoring/logistics-routing",
                       headers={"X-Role": "admin"},
                       json={"weight": 2.0, "budget": "cost"})
    body = resp.json()
    record("32_http_logistics_routing_success",
           resp.status_code == 200 and body.get("recommendation", {}).get("carrier") == "YT",
           f"status={resp.status_code}, recommendation={body.get('recommendation')}")

    # ========================================================
    # 7. HTTP 层补充: traffic / promotion 端点 + payment 409
    # ========================================================
    resp = client.post("/api/ai-scoring/traffic-antifraud",
                       json={"promoterId": 1, "recentCount": 1})
    record("33_http_traffic_requires_admin_role",
           resp.status_code == 403, f"status={resp.status_code}")

    resp = client.post("/api/ai-scoring/traffic-antifraud",
                       headers={"X-Role": "admin"},
                       json={"promoterId": 1, "recentCount": 3,
                             "avgIntervalSeconds": 3600, "newAccountRatio": 0.1,
                             "nightRatio": 0.05, "conversionRate": 0.3,
                             "uniqueSources": 6, "totalRecords": 100,
                             "effectiveRecords": 70, "fraudCount": 0})
    body = resp.json()
    record("34_http_traffic_antifraud_success",
           resp.status_code == 200 and body.get("success") is True
           and body.get("scorer") == "traffic_antifraud"
           and body.get("action") == "pass" and len(body.get("factors", [])) == 7,
           f"status={resp.status_code}, action={body.get('action')}")

    resp = client.post("/api/ai-scoring/traffic-antifraud",
                       headers={"X-Role": "admin"},
                       json={"promoterId": 2, "newAccountRatio": 1.5})
    record("35_http_traffic_ratio_over_1_422",
           resp.status_code == 422, f"status={resp.status_code}(Pydantic le=1 拦截)")

    resp = client.post("/api/ai-scoring/promotion-antifraud",
                       json={"promoterId": 11, "relationCount": 1})
    record("36_http_promotion_requires_admin_role",
           resp.status_code == 403, f"status={resp.status_code}")

    resp = client.post("/api/ai-scoring/promotion-antifraud",
                       headers={"X-Role": "admin"},
                       json={"promoterId": 11, "relationCount": 120,
                             "avgBindToRewardHours": 120,
                             "inactiveInviteeRatio": 0.1, "nightBindRatio": 0.05,
                             "fastestHundredDays": 30, "selfLoopSuspect": False,
                             "revokedCount": 0, "appealCount": 0})
    body = resp.json()
    record("37_http_promotion_antifraud_success",
           resp.status_code == 200 and body.get("success") is True
           and body.get("scorer") == "promotion_antifraud"
           and body.get("action") == "pay" and len(body.get("factors", [])) == 6,
           f"status={resp.status_code}, action={body.get('action')}")

    resp = client.post("/api/ai-scoring/promotion-antifraud",
                       headers={"X-Role": "admin"},
                       json={"promoterId": 12, "selfLoopSuspect": True,
                             "avgBindToRewardHours": -3})
    record("38_http_promotion_negative_hours_422",
           resp.status_code == 422, f"status={resp.status_code}(Pydantic ge=0 拦截)")

    resp = client.post("/api/ai-scoring/payment-routing",
                       headers={"X-Role": "admin"},
                       json={"amount": 100.0, "sceneType": "vip_pay"})
    record("39_http_payment_invalid_scene_409",
           resp.status_code == 409, f"status={resp.status_code}(ValueError→409)")

    # ========================================================
    # 汇总
    # ========================================================
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS + FAIL}  通过: {PASS}  失败: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
