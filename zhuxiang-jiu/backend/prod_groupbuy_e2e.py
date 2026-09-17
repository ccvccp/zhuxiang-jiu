"""生产 E2E: 团购模块全链实证(member 1 = L5 SVIP)

链路: products → tiers → calculate(阶梯匹配) → apply(申请, purpose
带 LT) → detail/list → audit(approved, admin) → cancel(取消, 终态)
→ 清理(订单键值含 LT)。
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
M = {"X-Member-Id": "1"}
A = {"X-Role": "admin"}

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:groupbuy*"))

# 1 可团购产品
resp = c.get("/api/groupbuy/products", headers=M)
d = resp.json()
data = d.get("data") or {}
prods = data if isinstance(data, list) else data.get("products") or []
print(f"[1] products: HTTP {resp.status_code} count={len(prods)} "
      f"first={json.dumps(prods[0], ensure_ascii=False)[:120] if prods else None}")

# 2 阶梯折扣
resp = c.get("/api/groupbuy/tiers", headers=M)
d = resp.json()
tiers = (d.get("data") or {}).get("tiers") or d.get("tiers") or []
print(f"[2] tiers: HTTP {resp.status_code} "
      f"{json.dumps(tiers, ensure_ascii=False)[:220]}")

# 3 试算(B01 × 50)
resp = c.post("/api/groupbuy/calculate", headers=M,
              json={"items": [{"productId": "ZX42-2026B01", "quantity": 600}]})
d = resp.json()
print(f"[3] calculate: HTTP {resp.status_code} "
      f"{json.dumps(d.get('data') or d, ensure_ascii=False)[:250]}")

# 4 申请(SVIP enterprise)
resp = c.post("/api/groupbuy/apply", headers=M, json={
    "userId": 1, "userLevel": 5, "groupType": "enterprise",
    "items": [{"productId": "ZX42-2026B01", "quantity": 600}],
    "purpose": "LT全链路测试-企业采购"})
d = resp.json()
data = d.get("data") or d
print(f"[4] apply: HTTP {resp.status_code} orderNo={data.get('orderNo')} "
      f"status={data.get('status')} total={data.get('totalAmount')}")
order_no = data["orderNo"]

# 5 详情 + 列表
resp = c.get(f"/api/groupbuy/{order_no}", headers=M)
d = resp.json()
o = d.get("data") or d
print(f"[5] detail: HTTP {resp.status_code} status={o.get('status')} "
      f"tier={o.get('tier') or o.get('discountTier')}")
resp = c.get("/api/groupbuy/list", headers=M)
d = resp.json()
print(f"[5] list: count={(d.get('data') or {}).get('count') or d.get('count')}")

# 6 审核(admin approved)
resp = c.post(f"/api/groupbuy/{order_no}/audit", headers=A, json={
    "auditor": "LT-admin", "auditResult": "approved",
    "auditRemark": "LT测试通过"})
d = resp.json()
o = d.get("data") or d
print(f"[6] audit: HTTP {resp.status_code} status={o.get('status')}")

# 7 取消(终态)
resp = c.put(f"/api/groupbuy/{order_no}/cancel", headers=M,
             json={"userId": 1, "reason": "LT测试取消"})
d = resp.json()
o = d.get("data") or d
print(f"[7] cancel: HTTP {resp.status_code} status={o.get('status')}")

# ---- 清理: 键或值含 LT / order_no ----
removed = 0
for k in set(r.keys("zhuxiang:groupbuy*")):
    if k in before:
        continue
    hit = order_no in k
    if not hit and r.type(k) in ("string", "hash"):
        v = r.get(k) if r.type(k) == "string" else ""
        h = r.hgetall(k) if r.type(k) == "hash" else {}
        hit = ("LT" in json.dumps(h, ensure_ascii=False)
               or "LT" in (v or "")) or order_no in (v or "")
    if hit:
        r.delete(k)
        removed += 1
residual = [k for k in set(r.keys("zhuxiang:groupbuy*")) - before]
print(f"[clean] removed {removed} keys, residual={residual}")
print("GROUPBUY E2E DONE")

