"""生产 E2E: 收款管理模块轻量实证(零资金副作用路径)

create(建单 pending) → detail(查询) → list(我的支付单)
→ close(关闭未支付单) → 差集清理。
不触发 start/callback/退款(资金敏感面, 已由 pay69/pay71 各期专项覆盖)。
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
M = {"X-Member-Id": "1"}
ORDER_ID = "LT-E2E-PAY-001"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:payment*"))

# 1 创建支付单
resp = c.post("/api/payment/pay", headers=M, json={
    "orderId": ORDER_ID, "orderType": "retail",
    "totalAmount": 9.9, "payChannel": "alipay",
    "payMethod": "jsapi", "sceneType": "order_pay"})
d = resp.json()
print(f"[1] create: HTTP {resp.status_code} "
      f"payNo={d.get('payNo')} status={d.get('status')}")
pay_no = d["payNo"]

# 2 详情
resp = c.get(f"/api/payment/{pay_no}", headers=M)
d = resp.json()
print(f"[2] detail: HTTP {resp.status_code} "
      f"raw={json.dumps(d, ensure_ascii=False)[:280]}")

# 3 我的支付单列表
resp = c.get("/api/payment/pays", headers=M)
d = resp.json()
print(f"[3] list: HTTP {resp.status_code} "
      f"count={d.get('count')} total={d.get('total')}")

# 4 关闭未支付单
resp = c.post(f"/api/payment/{pay_no}/close", headers=M,
              json={"reason": "LT全链路测试关闭"})
d = resp.json()
print(f"[4] close: HTTP {resp.status_code} "
      f"status={d.get('status')} {json.dumps(d, ensure_ascii=False)[:160]}")

# ---- 清理: 本轮新增键(orderId 含 LT-E2E; 索引 user/order 均为既有键改值) ----
removed = 0
for k in set(r.keys("zhuxiang:payment*")):
    if k.endswith(":seq"):
        continue
    h = r.hgetall(k) if r.type(k) == "hash" else {}
    if any("LT-E2E-PAY-001" in str(v) for v in h.values()) or pay_no in k:
        r.delete(k)
        removed += 1
# 索引修正: order 索引 SREM pay_no / user 索引 SREM pay_no(若为 set)
for ik in (f"zhuxiang:payment:order:index:order:{ORDER_ID}",
           "zhuxiang:payment:order:index:user:1"):
    t = r.type(ik)
    if t == "set":
        r.srem(ik, pay_no)
        if not r.smembers(ik):
            r.delete(ik)
    elif t == "list":
        r.lrem(ik, 1, pay_no)
        if not r.lrange(ik, 0, -1):
            r.delete(ik)
residual = [k for k in (set(r.keys("zhuxiang:payment*")) - before)
            if not k.endswith(":seq")]
print(f"[clean] removed {removed} keys, residual={residual}")
print("PAYMENT E2E DONE")
