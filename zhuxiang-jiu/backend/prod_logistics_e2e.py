"""生产 E2E: 物流接口管理模块轻量实证

链路: 运费估算(只读) → 下单(AUTO智能路由) → 详情(公开) 
→ 状态流转 pending→booked→picked(admin) → 轨迹回调(公开)+轨迹查询
→ 差集清理(运单+轨迹+状态索引, 值含 LT-E2E 匹配)。
不走对账/结算(资金面)。
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
M = {"X-Member-Id": "1"}
A = {"X-Role": "admin"}
ORDER_ID = "LT-E2E-LOG-001"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

# 1 运费估算(只读)
resp = c.post("/api/logistics/estimate-fee", json={
    "carrier": "SF", "serviceType": "standard",
    "weight": 2.0, "pieceCount": 1, "insuredValue": 100.0})
d = resp.json()
fee = d.get("data") or {}
print(f"[1] estimate: HTTP {resp.status_code} "
      f"fee={fee.get('totalFee') or d.get('totalFee')} "
      f"raw={json.dumps(d, ensure_ascii=False)[:150]}")

# 2 下单(AUTO 智能路由)
resp = c.post("/api/logistics/order", headers=M, json={
    "orderId": ORDER_ID, "orderType": "retail", "carrier": "AUTO",
    "serviceType": "standard",
    "sender": {"name": "LT发货人", "phone": "19900000088",
               "address": "泰安市泰山区竹香路1号"},
    "receiver": {"name": "LT收货人", "phone": "19900000089",
                 "address": "济南市历下区LT路1号",
                 "province": "山东省", "city": "济南市"},
    "weight": 2.0, "pieceCount": 1, "volume": 0.01,
    "insuredValue": 100.0, "settleMode": "monthly"})
d = resp.json()
data = d.get("data") or d
print(f"[2] create: HTTP {resp.status_code} "
      f"waybillNo={data.get('waybillNo')} carrier={data.get('carrier')} "
      f"status={data.get('status')} fee={data.get('totalFee')}")
waybill = data["waybillNo"]

# 3 详情(公开)
resp = c.get(f"/api/logistics/order/{waybill}")
d = resp.json()
o = d.get("data") or d
print(f"[3] detail: HTTP {resp.status_code} status={o.get('status')} "
      f"carrier={o.get('carrier')} orderId={o.get('orderId')}")

# 4 状态流转 pending→booked→picked(admin)
for st, desc in (("booked", "LT已下单"), ("picked", "LT已揽收")):
    resp = c.post(f"/api/logistics/order/{waybill}/status",
                  headers=A, json={"status": st, "operator": "LT-admin",
                                   "trackDesc": desc, "trackLocation": "泰安"})
    d = resp.json()
    o = d.get("data") or d
    print(f"[4] status→{st}: HTTP {resp.status_code} "
          f"now={o.get('status')}")

# 5 轨迹回调(公开, 简化鉴权) + 轨迹查询
resp = c.post("/api/logistics/callback/track", json={
    "waybillNo": waybill, "trackStatus": "TRANSPORT",
    "unifiedStatus": "transporting", "description": "LT运输中",
    "location": "济南", "operator": "carrier"})
d = resp.json()
print(f"[5] callback: HTTP {resp.status_code} "
      f"{json.dumps(d, ensure_ascii=False)[:140]}")
resp = c.get(f"/api/logistics/order/{waybill}/tracks")
d = resp.json()
t = d.get("data") or d
tracks = t.get("tracks") if isinstance(t, dict) else t
print(f"[5] tracks: HTTP {resp.status_code} count={len(tracks or [])}")

# ---- 清理: 运单+轨迹(键或值含 LT-E2E/waybill) + 状态索引 ----
removed = 0
for k in list(r.keys("zhuxiang:logistics*")):
    if k.endswith(":seq"):
        continue
    hit = waybill in k or ORDER_ID in k
    if not hit and r.type(k) in ("string", "hash"):
        h = r.hgetall(k) if r.type(k) == "hash" else {}
        v = r.get(k) if r.type(k) == "string" else ""
        hit = ORDER_ID in json.dumps(h, ensure_ascii=False) \
            or ORDER_ID in (v or "") or waybill in (v or "")
    if hit:
        r.delete(k)
        removed += 1
for ik in ("zhuxiang:logistics:order:index:status:transporting",
           "zhuxiang:logistics:order:index:status:pending"):
    if r.type(ik) == "set" and r.srem(ik, waybill):
        removed += 1
print(f"[clean] removed {removed} keys")
print("LOGISTICS E2E DONE")
