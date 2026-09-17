"""生产 E2E: 订单评价同步商品评价链路验证(缺陷1修复)

流程: 记录基线 → 下单→支付→发货→签收→评价 → 验证商品评价列表可见
输出 ORDER_ID 供清理脚本使用。
"""
import json

import httpx

BASE = "http://127.0.0.1:8000"
H = {"X-Member-Id": "1"}
A = {"X-Role": "admin"}
PID = "ZX42-2026L07"

c = httpx.Client(base_url=BASE, timeout=60)

# 0 记录评价前基线
r = c.get(f"/api/product/{PID}")
p0 = r.json()["product"]
avg0, cnt0 = p0.get("rating_avg", 0), p0.get("rating_count", 0)
print(f"[0] before: rating_avg={avg0} rating_count={cnt0}")

r = c.get(f"/api/product/{PID}/reviews", params={"page": 1, "pageSize": 100})
total0 = r.json()["total"]
print(f"[0] reviews total before: {total0}")

items = [{
    "productId": PID,
    "productName": "竹奕·竹香经典 42° 500ml",
    "quantity": 1,
    "unitPrice": 268.00,
}]
addr = {"name": "张三", "phone": "13800000001", "province": "山东省",
        "city": "泰安市", "district": "泰山区", "detail": "竹香路1号"}

# 1 创建订单
r = c.post("/api/order/create", json={
    "items": items, "address": addr, "usePoints": 0,
    "remark": "E2E同步验证", "ageConfirmed": True}, headers=H)
print("[1] create:", r.status_code)
d = r.json()
oid = d["orderId"]
print("    orderId:", oid, "actual:", d["priceDetail"]["actualAmount"])

# 2 支付
r = c.post(f"/api/order/{oid}/pay", json={}, headers=H)
print("[2] pay:", r.status_code, r.json().get("status"))

# 3 发货
r = c.post(f"/api/order/{oid}/ship",
           json={"carrier": "顺丰", "waybillNo": "SF-E2E-SYNC1"}, headers=A)
print("[3] ship:", r.status_code, r.json().get("status"))

# 4 签收
r = c.post(f"/api/order/{oid}/confirm", headers=H)
print("[4] confirm:", r.status_code, r.json().get("status"))

# 5 评价
r = c.post(f"/api/order/{oid}/review", json={
    "rating": 5, "content": "E2E同步验证: 香气清雅, 绵柔顺喉, 回甘明显"},
    headers=H)
print("[5] review:", r.status_code)
d = r.json()
print("    status:", d.get("status"), "rewardPoints:", d.get("rewardPoints"))
print("    reviewSynced:", d.get("reviewSynced"))
assert d.get("reviewSynced") == 1, "SYNC FAILED: reviewSynced != 1"

# 6 商品评价列表可见(修复目标)
r = c.get(f"/api/product/{PID}/reviews", params={"page": 1, "pageSize": 100})
d = r.json()
print(f"[6] reviews: {r.status_code} total: {d['total']} (expect {total0 + 1})")
found = [x for x in d["reviews"] if x.get("order_id") == oid]
if found:
    x = found[0]
    print("    FOUND review_id:", x.get("review_id"))
    print("    member_id:", x.get("member_id"),
          "nickname:", x.get("member_nickname"))
    print("    rating:", x.get("rating"), "content:", x.get("content"))
    assert x.get("rating") == 5
    assert "E2E同步验证" in x.get("content", "")
    print("[6] PASS: 商品页评价区可见订单评价")
else:
    print("[6] FAIL: 商品评价列表未见该评价")
    raise SystemExit(1)

# 7 评分聚合变化
r = c.get(f"/api/product/{PID}")
p1 = r.json()["product"]
print("[7] after: rating_avg=", p1.get("rating_avg"),
      "rating_count=", p1.get("rating_count"))

print("ORDER_ID_FOR_CLEANUP:", oid)
print("RATING_RESTORE:", json.dumps({"avg": avg0, "cnt": cnt0}))
print("E2E ALL PASS")
