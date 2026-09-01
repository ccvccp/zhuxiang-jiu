"""P1-17 智能选物流商测试(Service 层 + HTTP 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_logistics_smart_route.py

覆盖(设计文档 2.3 智能选型路由 / 2.4 路由规则详表):
    1. 团购路由: ≥100瓶同城→货拉拉整车 / 跨省→德邦零担; 50-100瓶同城→货拉拉小货车/跨省→德邦大件
    2. 高货值: ≥¥10,000 → 顺丰保价运输(优先)
    3. 偏远地区: 新疆/西藏等 → 圆通经济
    4. 零售默认: 顺丰(≤5件 express / >5件 standard)
    5. 下单接入: carrier 缺省/AUTO → 智能选择落库; 显式 carrier 不受影响
    6. HTTP 层: route-carrier 推荐端点 + order AUTO 下单
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.logistics_service import smart_route_carrier
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


SENDER = {"name": "竹香酒仓", "phone": "02812345678",
          "address": "四川省成都市锦江区酒厂路 1 号"}
RECEIVER_NORMAL = {"name": "张三", "phone": "13800001111", "address": "XX 市",
                   "province": "广东", "city": "深圳"}
RECEIVER_REMOTE = {"name": "李四", "phone": "13800002222", "address": "XX 市",
                   "province": "新疆", "city": "乌鲁木齐"}
RECEIVER_SAME_CITY = {"name": "王五", "phone": "13800003333", "address": "XX 区",
                      "province": "四川", "city": "成都"}


def run_routing():
    global PASS, FAIL
    for k in list(_mock_store.keys()):
        if "logistics" in k:
            del _mock_store[k]

    # ============================================================
    # 1. 团购路由(瓶数 = 件数 × 6)
    # ============================================================
    r = smart_route_carrier("groupbuy", 100, 20, 0, SENDER, RECEIVER_SAME_CITY)
    check("团购: 120瓶同城→货拉拉整车", r["carrier"] == "LLL"
          and r["serviceType"] == "整车配送", f"got {r['carrier']}/{r['serviceType']}")
    r = smart_route_carrier("groupbuy", 100, 20, 0, SENDER, RECEIVER_NORMAL)
    check("团购: 120瓶跨省→德邦零担", r["carrier"] == "DB"
          and r["serviceType"] == "零担物流", f"got {r['carrier']}")
    r = smart_route_carrier("groupbuy", 50, 10, 0, SENDER, RECEIVER_SAME_CITY)
    check("团购: 60瓶同城→货拉拉小货车", r["carrier"] == "LLL"
          and r["serviceType"] == "小货车")
    r = smart_route_carrier("groupbuy", 50, 10, 0, SENDER, RECEIVER_NORMAL)
    check("团购: 60瓶跨省→德邦大件", r["carrier"] == "DB"
          and r["serviceType"] == "大件快递")
    r = smart_route_carrier("groupbuy", 20, 4, 0, SENDER, RECEIVER_NORMAL)
    check("团购: 24瓶小批量→顺丰标快", r["carrier"] == "SF")

    # ============================================================
    # 2. 高货值保价(优先级最高)
    # ============================================================
    r = smart_route_carrier("retail", 5, 1, 15000, SENDER, RECEIVER_NORMAL)
    check("高货值: ≥1万→顺丰保价运输", r["carrier"] == "SF"
          and r["serviceType"] == "保价运输", f"got {r['serviceType']}")
    r = smart_route_carrier("groupbuy", 100, 20, 15000, SENDER, RECEIVER_SAME_CITY)
    check("高货值: 团购整车场景优先(保价列为候选)",
          r["carrier"] == "LLL"
          and any(c["serviceType"] == "保价运输" for c in r["candidates"]))

    # ============================================================
    # 3. 偏远地区
    # ============================================================
    r = smart_route_carrier("retail", 5, 1, 0, SENDER, RECEIVER_REMOTE)
    check("偏远: 新疆→圆通经济", r["carrier"] == "YT"
          and r["serviceType"] == "经济快递", f"got {r['carrier']}")
    check("偏远: remote 标识", r["remote"] is True)

    # ============================================================
    # 4. 零售默认
    # ============================================================
    r = smart_route_carrier("retail", 5, 1, 0, SENDER, RECEIVER_NORMAL)
    check("零售: 1件→顺丰 express", r["carrier"] == "SF"
          and r["serviceType"] == "express")
    r = smart_route_carrier("retail", 30, 8, 0, SENDER, RECEIVER_NORMAL)
    check("零售: 8件→顺丰 standard(卡班)", r["carrier"] == "SF"
          and r["serviceType"] == "standard")
    check("零售: sameCity False", r["sameCity"] is False)
    # 候选列表结构
    check("结构: candidates 含评分与理由",
          len(r["candidates"]) >= 1 and r["candidates"][0].get("score", 0) > 0
          and bool(r["reason"]))


async def run_order_integration():
    global PASS, FAIL
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    M = {"X-Member-Id": "1"}

    base_receiver = {"name": "赵六", "phone": "13800004444",
                     "address": "南山区科技园 1 号",
                     "province": "广东", "city": "深圳"}
    sender = {"name": "竹香酒仓", "phone": "02812345678",
              "address": "四川省成都市锦江区酒厂路 1 号"}

    # ============================================================
    # 5. 下单接入: carrier=AUTO → 智能选择
    # ============================================================
    r = client.post("/api/logistics/order", headers=M, json={
        "orderId": "SR-ORD-1", "orderType": "retail", "carrier": "AUTO",
        "sender": sender, "receiver": base_receiver,
        "weight": 5, "pieceCount": 1, "insuredValue": 0})
    body = r.json()
    check("下单: AUTO 零售→顺丰", r.status_code == 200
          and body["data"]["carrier"] == "SF", f"{r.status_code} {r.text[:150]}")
    check("下单: smartRouting 附带回执", body["data"].get("smartRouting", {})
          .get("carrier") == "SF")

    # carrier 缺省(不发字段) → 智能选择(偏远→圆通)
    remote_receiver = dict(base_receiver, province="西藏", city="拉萨",
                           address="城关区 1 号")
    r = client.post("/api/logistics/order", headers=M, json={
        "orderId": "SR-ORD-2", "orderType": "retail",
        "sender": sender, "receiver": remote_receiver,
        "weight": 5, "pieceCount": 1})
    body = r.json()
    check("下单: 缺省 carrier 偏远→圆通", r.status_code == 200
          and body["data"]["carrier"] == "YT", f"{r.status_code} {r.text[:150]}")

    # 团购大批量同城→货拉拉
    same_city_receiver = dict(base_receiver, province="四川", city="成都",
                              address="锦江区 2 号")
    r = client.post("/api/logistics/order", headers=M, json={
        "orderId": "SR-ORD-3", "orderType": "groupbuy",
        "sender": sender, "receiver": same_city_receiver,
        "weight": 120, "pieceCount": 20})
    body = r.json()
    check("下单: 团购 120 瓶同城→货拉拉", r.status_code == 200
          and body["data"]["carrier"] == "LLL")

    # 显式 carrier 仍可指定(不触发智能路由)
    r = client.post("/api/logistics/order", headers=M, json={
        "orderId": "SR-ORD-4", "orderType": "retail", "carrier": "JD",
        "sender": sender, "receiver": base_receiver,
        "weight": 5, "pieceCount": 1})
    body = r.json()
    check("下单: 显式 JD 不受影响", r.status_code == 200
          and body["data"]["carrier"] == "JD"
          and "smartRouting" not in body["data"])

    # ============================================================
    # 6. 推荐端点
    # ============================================================
    r = client.post("/api/logistics/route-carrier", json={
        "orderType": "groupbuy", "weight": 100, "pieceCount": 20,
        "insuredValue": 0, "sender": sender, "receiver": same_city_receiver})
    body = r.json()
    check("推荐: 团购同城→LLL", r.status_code == 200
          and body["data"]["carrier"] == "LLL", f"{r.status_code} {r.text[:150]}")
    check("推荐: 候选列表非空", len(body["data"]["candidates"]) >= 1)
    r = client.post("/api/logistics/route-carrier", json={
        "orderType": "retail", "weight": 5, "pieceCount": 1,
        "insuredValue": 0, "sender": sender, "receiver": base_receiver})
    check("推荐: 零售→SF", r.json()["data"]["carrier"] == "SF")
    # 参数校验
    r = client.post("/api/logistics/route-carrier", json={
        "orderType": "retail", "weight": -1, "pieceCount": 1,
        "insuredValue": 0, "sender": sender, "receiver": base_receiver})
    check("推荐: 非法重量 422", r.status_code == 422)


def main():
    run_routing()
    asyncio.run(run_order_integration())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
