"""智单·AI智能订单大模型 专项测试(P0-P3)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_zhidan.py

覆盖:
    - 织物底座: 空数据诚实零值 / 九态分布 / GMV / 退款率 / 履约样本
    - P0 洞察: 五域问答各一断言(单量/GMV/退款/履约/异常)+未知域
      引导 / 体检四维与总分(确定性 77.5)/ 画像三维结构
    - P1 预测: ETA 公式确定性(同输入两次相等)/ whatif 三维影响
      方向 / 日单量预测确定性 / 非法参数 409
    - P2 风险: 退款评分四因子结构+分级+建议 / 不存在订单 404 /
      异常扫描三类规则全触发 / 留痕列表
    - P3 进化: 反馈闭环安全阀(多次 adopted 不超上限 0.8, 多次
      rejected 不破下限 0.4)/ 三检测器(构造 spike 序列触发
      spike+drop+surge)/ 决策备忘录假设标注
    - 宪法铁律: 管理端 403 / 既有订单模块回归(叠加零改动)
"""
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from repositories.store import _mock_store

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


ADMIN = {"X-Role": "admin"}
BASE = "/api/order-ai"

# 商品种子(P1 单价 268 / P2 单价 168)
P1 = ("ZX42-2026L07", "竹奕·竹香型 42° 500ml", 268.0)
P2 = ("ZX42-2026L05", "竹奕·竹香型 42° 500ml 礼盒", 168.0)

D1, D2, D3 = "2026-09-01", "2026-09-02", "2026-09-03"


# ============================================================
# 数据构造
# ============================================================

def reset_zd_state():
    """清智单前缀键 + 订单表(隔离织物与留痕)"""
    for k in list(_mock_store.keys()):
        if k.startswith("zd_"):
            del _mock_store[k]
    _mock_store["orders_v2"] = {}
    _mock_store["orders"] = []


def _item(product, qty):
    pid, name, price = product
    return {"productId": pid, "productName": name, "quantity": qty,
            "unitPrice": price, "subtotal": round(qty * price, 2)}


def _order(oid, member, status, items, actual, created,
           paid_at="", signed_at="", refunded_at="",
           refund_applied_at="", consumed=0):
    """构造订单(对齐 order_service 落库字段结构)"""
    total = round(sum(i["subtotal"] for i in items), 2)
    return {
        "orderId": oid, "memberId": member, "orderType": "RT",
        "status": status, "items": items,
        "priceDetail": {"goodsTotal": total, "memberDiscount": 0,
                        "couponDiscount": 0, "pointsDiscount": 0,
                        "shippingFee": 0, "actualAmount": actual,
                        "discountRate": 1.0},
        "address": {}, "remark": "",
        "logistics": {"carrier": "SF" if signed_at else "",
                      "waybillNo": "", "shippedAt": paid_at,
                      "signedAt": signed_at},
        "payment": {"method": "wechat" if paid_at else "",
                    "tradeNo": "", "paidAt": paid_at},
        "review": {"rating": 0, "content": "", "reviewedAt": ""},
        "refund": {
            "reason": "不想要了"
            if (refunded_at or refund_applied_at) else "",
            "refundedAt": refunded_at,
            "refundedAmount": actual if refunded_at else 0,
            "audit": ({"status": "pending",
                       "appliedAt": refund_applied_at}
                      if refund_applied_at else {})},
        "usedPoints": 0, "consumedPoints": consumed,
        "timeline": [], "createdAt": created, "updatedAt": created,
    }


def seed_small():
    """Phase B 种子: 10 单(可控精确数字)

    履约样本 3 个(48/72/96h → 均值 72h); 会员 1 高频 3 单+1 囤货
    12 件; 秒退款 1 单(支付后 12h 申请); 总量 10 / GMV 5696。
    """
    orders = [
        _order("RT-B-01", 1, "COMPLETED", [_item(P1, 2)], 536,
               f"{D1}T08:00:00+00:00", paid_at=f"{D1}T08:05:00+00:00",
               signed_at="2026-09-03T08:05:00+00:00"),
        _order("RT-B-02", 1, "COMPLETED", [_item(P1, 1)], 268,
               f"{D1}T10:00:00+00:00", paid_at=f"{D1}T10:02:00+00:00",
               signed_at="2026-09-04T10:02:00+00:00"),
        _order("RT-B-03", 1, "COMPLETED", [_item(P2, 1)], 168,
               f"{D1}T12:00:00+00:00", paid_at=f"{D1}T12:03:00+00:00",
               signed_at="2026-09-05T12:03:00+00:00"),
        _order("RT-B-04", 2, "REFUNDED", [_item(P1, 2)], 536,
               f"{D2}T09:00:00+00:00", paid_at=f"{D2}T09:05:00+00:00",
               refunded_at="2026-09-06T09:00:00+00:00"),
        _order("RT-B-05", 3, "PENDING", [_item(P1, 2)], 536,
               f"{D2}T15:00:00+00:00"),
        _order("RT-B-06", 2, "PAID", [_item(P2, 1)], 168,
               f"{D2}T18:00:00+00:00", paid_at=f"{D2}T18:01:00+00:00"),
        _order("RT-B-07", 3, "RETURNING", [_item(P1, 2)], 536,
               f"{D3}T20:00:00+00:00", paid_at=f"{D3}T20:01:00+00:00",
               refund_applied_at="2026-09-04T08:01:00+00:00"),
        _order("RT-B-08", 1, "PAID", [_item(P1, 12)], 3216,
               f"{D2}T11:00:00+00:00", paid_at=f"{D2}T11:01:00+00:00",
               consumed=200),
        _order("RT-B-09", 4, "CANCELLED", [_item(P1, 1)], 268,
               f"{D1}T16:00:00+00:00"),
        _order("RT-B-10", 4, "PAID", [_item(P1, 1)], 268,
               f"{D3}T09:00:00+00:00", paid_at=f"{D3}T09:02:00+00:00"),
    ]
    _mock_store["orders_v2"] = {o["orderId"]: o for o in orders}
    _mock_store["orders"] = list(orders)


def seed_detectors():
    """Phase C 种子: 150 单(构造三检测器触发序列)

    历史三日(09-06~08)每日 30 单(25 PAID + 5 CANCELLED);
    当期日(09-09)60 单(35 COMPLETED + 21 CANCELLED + 4 PAID)
    → 单量 spike(60≥2×30) / PAID drop(4≤25/2 且降幅 21≥20)
    / 取消 surge(21≥3×5 且 ≥20)。
    """
    orders = []
    for di, day in enumerate(("2026-09-06", "2026-09-07",
                              "2026-09-08")):
        for i in range(25):
            hour = 8 + (i % 12)
            orders.append(_order(
                f"RT-D-H{di}-{i:02d}", 100 + (i % 5), "PAID",
                [_item(P1, 1)], 268,
                f"{day}T{hour:02d}:00:00+00:00",
                paid_at=f"{day}T{(hour + 1) % 24:02d}:00:00+00:00"))
        for i in range(5):
            orders.append(_order(
                f"RT-D-C{di}-{i:02d}", 200 + i, "CANCELLED",
                [_item(P1, 1)], 268, f"{day}T20:00:00+00:00"))
    day = "2026-09-09"
    for i in range(35):
        hour = 8 + (i % 12)
        orders.append(_order(
            f"RT-D-F{i:02d}", 300 + (i % 5), "COMPLETED",
            [_item(P1, 1)], 268, f"{day}T{hour:02d}:00:00+00:00",
            paid_at=f"{day}T{hour:02d}:00:00+00:00",
            signed_at=f"{day}T{(hour + 2) % 24:02d}:00:00+00:00"))
    for i in range(21):
        orders.append(_order(
            f"RT-D-X{i:02d}", 400 + (i % 5), "CANCELLED",
            [_item(P1, 1)], 268, f"{day}T19:00:00+00:00"))
    for i in range(4):
        orders.append(_order(
            f"RT-D-P{i:02d}", 500 + i, "PAID",
            [_item(P1, 1)], 268, f"{day}T10:00:00+00:00",
            paid_at=f"{day}T11:00:00+00:00"))
    _mock_store["orders_v2"] = {o["orderId"]: o for o in orders}
    _mock_store["orders"] = list(orders)


# ============================================================
# Phase A: 空数据诚实零值
# ============================================================

def run_empty(client):
    r = client.get(f"{BASE}/overview", headers=ADMIN)
    b = r.json()["data"]
    check("织物-空数据诚实零值(hasData/总量)", r.status_code == 200
          and b["hasData"] is False and b["totalOrders"] == 0)
    check("织物-空数据零值(GMV/退款率/客单价)",
          b["gmv"] == 0 and b["refundRate"] == 0
          and b["avgOrderValue"] == 0 and b["avgFulfillmentHours"] == 0)
    check("织物-九态分布骨架+空日时序",
          len(b["statusDistribution"]) == 9 and b["dailySeries"] == [])
    r = client.get(f"{BASE}/orders", headers=ADMIN)
    check("织物-订单列表空诚实 []", r.json()["count"] == 0)

    r = client.post(f"{BASE}/qa", headers=ADMIN,
                    json={"text": "现在有多少订单"})
    check("问答-空数据诚实(暂无)", "暂无" in r.json()["data"]["answer"])

    r = client.get(f"{BASE}/eta", headers=ADMIN)
    b = r.json()["data"]
    check("ETA-样本0诚实 None+提示", b["etaHours"] is None
          and b["sampleSize"] == 0 and "暂无" in b["note"])

    r = client.get(f"{BASE}/checkup", headers=ADMIN)
    b = r.json()["data"]
    check("体检-空数据四维零分", b["hasData"] is False
          and b["totalScore"] == 0 and len(b["dimensions"]) == 4)

    r = client.get(f"{BASE}/forecast", headers=ADMIN)
    check("预测-空时序 409(诚实不编造)", r.status_code == 409)


# ============================================================
# Phase B: P0 洞察中枢
# ============================================================

def run_p0(client):
    # 1. 五域问答(数字 100% 插值自查询层)
    r = client.post(f"{BASE}/qa", headers=ADMIN,
                    json={"text": "现在有多少订单"})
    b = r.json()["data"]
    check("问答-单量域(总量插值)", b["domain"] == "volume"
          and "10" in b["answer"] and "8" in b["answer"])
    r = client.post(f"{BASE}/qa", headers=ADMIN,
                    json={"text": "GMV 是多少"})
    b = r.json()["data"]
    check("问答-GMV域(实付合计插值)", b["domain"] == "gmv"
          and "5696" in b["answer"] and "712" in b["answer"])
    r = client.post(f"{BASE}/qa", headers=ADMIN,
                    json={"text": "退款率是多少"})
    b = r.json()["data"]
    check("问答-退款域(退款率插值)", b["domain"] == "refund"
          and "10" in b["answer"] and "1" in b["answer"])
    r = client.post(f"{BASE}/qa", headers=ADMIN,
                    json={"text": "平均履约时长多久"})
    b = r.json()["data"]
    check("问答-履约域(72h 插值)", b["domain"] == "fulfillment"
          and "72" in b["answer"] and "72" in str(b["dataSnapshot"]))
    r = client.post(f"{BASE}/qa", headers=ADMIN,
                    json={"text": "最近有什么异常"})
    b = r.json()["data"]
    check("问答-异常域(风险观测插值)", b["domain"] == "anomaly"
          and "anomaly-scan" in b["answer"])
    r = client.post(f"{BASE}/qa", headers=ADMIN,
                    json={"text": "你是谁"})
    b = r.json()["data"]
    check("问答-未知域引导语", b["domain"] == "unknown"
          and "可试试" in b["answer"])

    # 2. 体检四维(确定性: 80/50/100/80 → 77.5)
    r = client.get(f"{BASE}/checkup", headers=ADMIN)
    b = r.json()["data"]
    dims = b["dimensions"]
    check("体检-四维各 0-100", r.status_code == 200
          and len(dims) == 4 and all(0 <= d["score"] <= 100
                                     for d in dims))
    check("体检-总分四维等权(77.5)", b["totalScore"] == 77.5)
    check("体检-履约维度满百(72h=基线)",
          dims[2]["dim"] == "履约时效" and dims[2]["score"] == 100.0)
    check("体检-formula+建议书 disposition",
          "0.25" in b["formula"] and "建议书" in b["disposition"]
          and "公式" not in b["disposition"])
    check("体检-等级与建议", b["grade"] in ("A 优秀", "B 良好",
          "C 关注", "D 预警") and len(b["suggestions"]) >= 1)
    r = client.get(f"{BASE}/checkups", headers=ADMIN)
    check("体检-历史留痕", r.json()["count"] >= 1)

    # 3. 三维画像
    r = client.get(f"{BASE}/portrait", headers=ADMIN)
    b = r.json()["data"]
    check("画像-top 会员(会员1 ¥4188)",
          b["topMembers"][0]["memberId"] == 1
          and b["topMembers"][0]["amount"] == 4188.0)
    check("画像-top 商品(P1 20 件)",
          b["topProducts"][0]["productId"] == P1[0]
          and b["topProducts"][0]["quantity"] == 20)
    buckets = b["timeBuckets"]
    check("画像-时段分布(求和=总量, 峰值上午)",
          sum(x["count"] for x in buckets) == 10
          and b["peakBucket"] == "上午(6-12时)")
    check("画像-建议书 disposition", "建议" in b["disposition"])
    r = client.get(f"{BASE}/portraits", headers=ADMIN)
    check("画像-历史留痕", r.json()["count"] >= 1)


# ============================================================
# Phase B: P1 预测沙盘
# ============================================================

def run_p1(client):
    # 1. ETA 加权预测(0.6×72 + 0.4×72 = 72)
    r = client.get(f"{BASE}/eta", headers=ADMIN)
    b = r.json()["data"]
    check("ETA-加权公式(72h)", b["etaHours"] == 72.0
          and b["sampleSize"] == 3 and b["recentAvg"] == 72.0)
    check("ETA-formula 推理链留痕", "0.6" in b["formula"]
          and "近3单" in b["formula"])
    r2 = client.get(f"{BASE}/eta", headers=ADMIN)
    check("ETA-确定性(同输入两次相等)",
          r2.json()["data"] == r.json()["data"])

    # 2. What-if 三维影响方向
    r = client.post(f"{BASE}/whatif", headers=ADMIN,
                    json={"aovDelta": 0.2})
    b = r.json()["data"]
    check("WhatIf-客单价+20%→GMV 上行",
          b["scenario"]["gmv"] == 6835.2
          and b["impacts"]["gmvDelta"] == 1139.2)
    r = client.post(f"{BASE}/whatif", headers=ADMIN,
                    json={"cancelRateDelta": 0.2})
    b = r.json()["data"]
    check("WhatIf-取消率+20%→GMV 下行",
          b["scenario"]["gmv"] == 4556.8
          and b["impacts"]["gmvDelta"] == -1139.2)
    r = client.post(f"{BASE}/whatif", headers=ADMIN,
                    json={"shipDelayDays": 3})
    b = r.json()["data"]
    check("WhatIf-发货延迟3天→履约 144h+超时风险",
          b["scenario"]["projectedFulfillmentHours"] == 144.0
          and b["scenario"]["overtimeRiskRatio"] == 1.0
          and b["scenario"]["estOvertimeOrders"] == 8.0)
    body = {"shipDelayDays": 2, "cancelRateDelta": 0.1,
            "aovDelta": 0.05}
    r = client.post(f"{BASE}/whatif", headers=ADMIN, json=body)
    b = r.json()["data"]
    check("WhatIf-三维组合(有效单量+建议书)",
          b["scenario"]["effectiveOrders"] == 7.2
          and "建议书" in b["disposition"]
          and len(b["mitigations"]) >= 1)
    r2 = client.post(f"{BASE}/whatif", headers=ADMIN, json=body)
    check("WhatIf-确定性(同输入两次相等)", r2.json()["data"] == b)
    check("WhatIf-非法延迟 409", client.post(
        f"{BASE}/whatif", headers=ADMIN,
        json={"shipDelayDays": 99}).status_code == 409)
    check("WhatIf-非法取消率 409", client.post(
        f"{BASE}/whatif", headers=ADMIN,
        json={"cancelRateDelta": 0.9}).status_code == 409)

    # 3. 日单量滚动预测([4,4,2] → 基线 3.33, 斜率 -1)
    r = client.get(f"{BASE}/forecast", headers=ADMIN,
                   params={"periods": 5})
    b = r.json()["data"]
    rows = b["rows"]
    check("预测-行数与首行(2.83@次日)", len(rows) == 5
          and rows[0]["forecastVolume"] == 2.83
          and rows[0]["date"] == "2026-09-04")
    check("预测-非负 clamp+formula",
          all(x["forecastVolume"] >= 0 for x in rows)
          and "0.6" in b["formula"] and "趋势" in b["formula"])
    check("预测-基线口径(近3日+全期)",
          b["basis"]["recentAvg"] == 3.33
          and b["basis"]["fullAvg"] == 3.33
          and b["basis"]["historyDays"] == 3)
    r2 = client.get(f"{BASE}/forecast", headers=ADMIN,
                    params={"periods": 5})
    check("预测-确定性(同输入两次相等)", r2.json()["data"]["rows"]
          == rows)


# ============================================================
# Phase B: P2 退款裁决与风险
# ============================================================

def run_p2(client):
    # 1. 退款评分(囤货单 RT-B-08: 四因子 → 52 分中风险)
    r = client.get(f"{BASE}/refund-score/RT-B-08", headers=ADMIN)
    b = r.json()["data"]
    check("退款评分-四因子结构", r.status_code == 200
          and len(b["factors"]) == 4
          and sum(b["weights"].values()) == 1.0)
    check("退款评分-分级与建议(中风险→人工)",
          b["score"] == 52.0 and b["level"] == "mid"
          and b["suggestion"] == "manual")
    check("退款评分-推理链+建议书",
          len(b["reasoning"]) >= 2 and "0.30" in b["formula"]
          and "建议书" in b["disposition"])
    r2 = client.get(f"{BASE}/refund-score/RT-B-08", headers=ADMIN)
    check("退款评分-确定性(同输入两次相等)",
          r2.json()["data"] == b)
    # 正常单 RT-B-01 → 0 分低风险 → 建议同意
    r = client.get(f"{BASE}/refund-score/RT-B-01", headers=ADMIN)
    b = r.json()["data"]
    check("退款评分-正常单(低风险→同意)",
          b["score"] == 0.0 and b["level"] == "low"
          and b["suggestion"] == "approve")
    check("退款评分-不存在订单 404", client.get(
        f"{BASE}/refund-score/RT-NOPE", headers=ADMIN).status_code
        == 404)

    # 2. 异常扫描(三类规则全触发: 高频/囤货/秒退款)
    r = client.post(f"{BASE}/anomaly-scan", headers=ADMIN)
    b = r.json()["data"]
    types = {a["type"] for a in b["anomalies"]}
    check("扫描-三类异常全覆盖", r.status_code == 200
          and b["anomalyCount"] == 3
          and types == {"high_frequency", "bulk_stockpile",
                        "instant_refund"})
    check("扫描-建议书永不拦截", all(
        a["level"] in ("low", "mid", "high")
        and "建议" in a["suggestion"] for a in b["anomalies"])
        and "不自动拦截" in b["disposition"])
    r = client.get(f"{BASE}/anomalies", headers=ADMIN)
    check("扫描-历史留痕列表", r.json()["count"] >= 3)


# ============================================================
# Phase B→C: P3 进化闭环(反馈学习在换种子前进行)
# ============================================================

def run_p3_calm(client):
    # 1. 阈值未触发(防冷启动误报: 日单量 [4,4,2] μ<5)
    r = client.get(f"{BASE}/detect", headers=ADMIN)
    b = r.json()["data"]
    check("检测-平缓时序零告警(防误报)",
          b["alertCount"] == 0 and b["days"] == 3)

    # 2. 备忘录(数据插值+假设标注)
    r = client.post(f"{BASE}/memo", headers=ADMIN,
                    json={"topic": "promotion_prep",
                          "notes": "中秋档备货"})
    b = r.json()["data"]
    check("备忘录-大促备货(假设标注)",
          r.status_code == 200 and b["topicName"] == "大促备货"
          and all("假设" in x for x in b["assumptions"])
          and "备货" in b["recommendation"])
    r = client.post(f"{BASE}/memo", headers=ADMIN,
                    json={"topic": "timeout_policy"})
    b = r.json()["data"]
    check("备忘录-超时策略(预警线插值)",
          b["topicName"] == "超时策略"
          and "预警线" in b["recommendation"]
          and "建议书" in b["disposition"])
    check("备忘录-非法主题 409", client.post(
        f"{BASE}/memo", headers=ADMIN,
        json={"topic": "ghost"}).status_code == 409)
    r = client.get(f"{BASE}/memos", headers=ADMIN)
    check("备忘录-留痕列表", r.json()["count"] >= 2)

    # 3. 反馈闭环安全阀(0.6 起, adopted×1.05, clamp [0.4,0.8])
    r = client.get(f"{BASE}/params", headers=ADMIN)
    b = r.json()["data"]
    check("进化-参数默认 0.6+安全阀", b["etaRecentWeight"] == 0.6
          and b["clamp"] == [0.4, 0.8])
    for _ in range(10):
        rr = client.post(f"{BASE}/feedback", headers=ADMIN,
                         json={"targetType": "eta_forecast",
                               "verdict": "adopted"})
        assert rr.status_code == 200, rr.text[:200]
    r = client.get(f"{BASE}/params", headers=ADMIN)
    check("进化-多次 adopted 不超上限(0.8)",
          r.json()["data"]["etaRecentWeight"] == 0.8)
    for _ in range(20):
        rr = client.post(f"{BASE}/feedback", headers=ADMIN,
                         json={"targetType": "eta_forecast",
                               "verdict": "rejected"})
        assert rr.status_code == 200, rr.text[:200]
    r = client.get(f"{BASE}/params", headers=ADMIN)
    check("进化-多次 rejected 不破下限(0.4)",
          r.json()["data"]["etaRecentWeight"] == 0.4)
    check("进化-非法裁决 409", client.post(
        f"{BASE}/feedback", headers=ADMIN,
        json={"targetType": "eta_forecast",
              "verdict": "maybe"}).status_code == 409)
    check("进化-非法目标 409", client.post(
        f"{BASE}/feedback", headers=ADMIN,
        json={"targetType": "ghost",
              "verdict": "adopted"}).status_code == 409)
    r = client.get(f"{BASE}/feedbacks", headers=ADMIN)
    check("进化-反馈留痕列表", r.json()["count"] >= 30)


# ============================================================
# Phase C: 三检测器触发
# ============================================================

def run_p3_detect(client):
    r = client.get(f"{BASE}/detect", headers=ADMIN)
    b = r.json()["data"]
    types = {a["type"] for a in b["alerts"]}
    check("检测-spike/drop/surge 三告警全触发",
          b["alertCount"] == 3 and b["days"] == 4
          and types == {"spike", "drop", "surge"})
    check("检测-告警含指标/基线/细节", all(
        a["metric"] and "baseline" in a and "detail" in a
        for a in b["alerts"]))
    r2 = client.get(f"{BASE}/detect", headers=ADMIN)
    check("检测-确定性(同输入两次相等)",
          r2.json()["data"] == b)


# ============================================================
# 宪法铁律回归
# ============================================================

def run_guards(client):
    r = client.get(f"{BASE}/status")
    check("铁律-管理端无鉴权 403", r.status_code == 403)
    r = client.get(f"{BASE}/status", headers=ADMIN)
    b = r.json()["data"]
    check("铁律-状态视图(模块名+铁律声明)",
          r.status_code == 200
          and b["module"] == "智单·AI智能订单大模型"
          and len(b["constitution"]) >= 4)
    # 既有订单模块回归(叠加式升级零改动)
    r = client.get("/api/order/admin/list", headers=ADMIN)
    check("铁律-既有订单模块回归 200", r.status_code == 200)


def main():
    from fastapi.testclient import TestClient
    from main import app
    from routes.zd_routes import register_zd_routes
    register_zd_routes(app)

    reset_zd_state()
    client = TestClient(app)

    run_empty(client)      # Phase A: 空数据诚实零值
    seed_small()           # Phase B: 10 单精确种子
    run_p0(client)
    run_p1(client)
    run_p2(client)
    run_p3_calm(client)    # 反馈学习/备忘录(换种子前)
    seed_detectors()       # Phase C: 150 单检测序列
    run_p3_detect(client)
    run_guards(client)

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
