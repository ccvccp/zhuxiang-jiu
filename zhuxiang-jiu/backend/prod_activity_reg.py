"""补测: 活动报名链(带 X-Member-Id 头)"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
M = {"X-Member-Id": "1"}
A = {"X-Role": "admin"}

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:activity*"))

resp = c.post("/api/activity/admin/create", headers=A, json={
    "name": "LT报名链补测品鉴会", "type": "interactive",
    "description": "LT", "budget": 500.0, "createdBy": 3})
data = (resp.json().get("data") or resp.json())
aid = data.get("activityId") or data.get("id")
print(f"[1] create: HTTP {resp.status_code} id={aid}")

c.post(f"/api/activity/admin/transition/{aid}", headers=A,
       json={"targetStatus": "registering", "operator": 3})
print("[2] transition→registering done")

resp = c.post("/api/activity/register", headers=M, json={
    "activityId": aid, "userId": 1, "participateData": {"ch": "LT"}})
d = resp.json()
print(f"[3] register: HTTP {resp.status_code} "
      f"{json.dumps(d.get('data') or d, ensure_ascii=False)[:150]}")

resp = c.get("/api/activity/my-registrations", headers=M)
d = resp.json()
regs = d.get("data") or []
print(f"[4] my-registrations: HTTP {resp.status_code} "
      f"count={len(regs) if isinstance(regs, list) else d.get('count')}")

resp = c.get(f"/api/activity/stats/{aid}")
d = resp.json()
print(f"[5] stats: regCount={(d.get('data') or {}).get('registrationCount')} "
      f"status={(d.get('data') or {}).get('status')}")

resp = c.post("/api/activity/cancel", headers=M, json={
    "activityId": aid, "userId": 1})
d = resp.json()
print(f"[6] cancel reg: HTTP {resp.status_code} "
      f"{json.dumps(d.get('data') or d, ensure_ascii=False)[:120]}")

c.post(f"/api/activity/admin/transition/{aid}", headers=A,
       json={"targetStatus": "cancelled", "operator": 3})
print("[7] transition→cancelled done")

removed = 0
for k in set(r.keys("zhuxiang:activity*")):
    if k in before:
        continue
    hit = str(aid) in k
    if not hit and r.type(k) in ("string", "hash"):
        h = r.hgetall(k) if r.type(k) == "hash" else {}
        v = r.get(k) if r.type(k) == "string" else ""
        hit = "LT" in json.dumps(h, ensure_ascii=False) or "LT" in (v or "")
    if hit:
        r.delete(k)
        removed += 1
residual = [k for k in set(r.keys("zhuxiang:activity*")) - before]
print(f"[clean] removed {removed} keys, residual={residual}")
print("ACTIVITY REG E2E DONE")
