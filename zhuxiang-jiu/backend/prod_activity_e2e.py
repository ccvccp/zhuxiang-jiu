"""生产 E2E: 活动管理模块全链实证

链路: admin/create(创建) → transition(发布 registering) → list(公开
可见) → register(member1 报名) → my-registrations → stats 
→ cancel(取消报名) → transition(活动 cancelled 终态) → 清理。
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
M = {"X-Member-Id": "1"}
A = {"X-Role": "admin"}

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:activity*"))

# 1 创建(interactive, LT 标记)
resp = c.post("/api/activity/admin/create", headers=A, json={
    "name": "LT全链路测试品鉴会", "type": "interactive",
    "description": "LT测试活动", "budget": 1000.0,
    "startTime": "2026-09-18T10:00:00", "endTime": "2026-09-20T22:00:00",
    "createdBy": 3})
d = resp.json()
data = d.get("data") or d
print(f"[1] create: HTTP {resp.status_code} id={data.get('activityId') or data.get('id')} "
      f"status={data.get('status')}")
aid = data.get("activityId") or data.get("id")

# 2 发布(draft→registering)
resp = c.post(f"/api/activity/admin/transition/{aid}", headers=A,
              json={"targetStatus": "registering", "operator": 3})
d = resp.json()
data = d.get("data") or d
print(f"[2] transition→registering: HTTP {resp.status_code} "
      f"status={data.get('status')}")

# 3 公开列表可见
resp = c.get("/api/activity/list")
d = resp.json()
print(f"[3] list: HTTP {resp.status_code} count={d.get('count')} "
      f"first={json.dumps((d.get('data') or [{}])[0], ensure_ascii=False)[:100]}")

# 4 报名(member 1)
resp = c.post("/api/activity/register", json={
    "activityId": aid, "userId": 1,
    "participateData": {"channel": "LT"}})
d = resp.json()
print(f"[4] register: HTTP {resp.status_code} "
      f"{json.dumps(d.get('data') or d, ensure_ascii=False)[:150]}")

# 5 我的报名
resp = c.get("/api/activity/my-registrations", headers=M)
d = resp.json()
regs = d.get("data") or []
print(f"[5] my-registrations: HTTP {resp.status_code} count={len(regs) if isinstance(regs, list) else d.get('count')}")

# 6 统计
resp = c.get(f"/api/activity/stats/{aid}")
d = resp.json()
print(f"[6] stats: HTTP {resp.status_code} "
      f"{json.dumps(d.get('data') or {}, ensure_ascii=False)[:180]}")

# 7 取消报名
resp = c.post("/api/activity/cancel", json={
    "activityId": aid, "userId": 1})
d = resp.json()
print(f"[7] cancel reg: HTTP {resp.status_code} "
      f"{json.dumps(d.get('data') or d, ensure_ascii=False)[:120]}")

# 8 活动终态(cancelled)
resp = c.post(f"/api/activity/admin/transition/{aid}", headers=A,
              json={"targetStatus": "cancelled", "operator": 3})
d = resp.json()
data = d.get("data") or d
print(f"[8] transition→cancelled: HTTP {resp.status_code} "
      f"status={data.get('status')}")

# ---- 清理: 键或值含 LT ----
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
print("ACTIVITY E2E DONE")
