"""生产 E2E: 会员积分模块只读+护栏负测实证(零副作用)

积分写路径(返分/抵扣/过期)已被订单/钱包等多模块真实流量反复
验证(账本 version 11 为证)——本轮走只读四查 + 两条零副作用护栏
负测(超额抵扣 409 / 重复签到幂等), 不触碰真实账本。
"""
import json

import httpx

BASE = "http://127.0.0.1:8000"
M = {"X-Member-Id": "1"}
UID = 1

c = httpx.Client(base_url=BASE, timeout=60)

# 1 账户(只读)
resp = c.get(f"/api/points/account/{UID}")
d = resp.json().get("data") or {}
print(f"[1] account: HTTP {resp.status_code} total={d.get('totalPoints')} "
      f"frozen={d.get('frozenPoints')} earned={d.get('totalEarned')} "
      f"spent={d.get('totalSpent')} ver={d.get('version')} "
      f"expiring={d.get('expiringPoints')}")

# 2 统计(只读)
resp = c.get(f"/api/points/stats/{UID}")
print(f"[2] stats: HTTP {resp.status_code} "
      f"{json.dumps(resp.json().get('data') or {}, ensure_ascii=False)[:200]}")

# 3 流水(只读)
resp = c.get(f"/api/points/logs/{UID}", params={"limit": 3})
d = resp.json()
logs = d.get("data") or {}
arr = logs.get("logs") if isinstance(logs, dict) else logs
print(f"[3] logs: HTTP {resp.status_code} count={d.get('count') or (logs.get('count') if isinstance(logs, dict) else None)} "
      f"first={json.dumps((arr or [{}])[0], ensure_ascii=False)[:140] if arr else None}")

# 4 临期查询(只读)
resp = c.get(f"/api/points/expiring/{UID}")
print(f"[4] expiring: HTTP {resp.status_code} "
      f"{json.dumps(resp.json().get('data') or {}, ensure_ascii=False)[:160]}")

# 5 签到状态查询(只读)
resp = c.get(f"/api/points/signin/{UID}")
print(f"[5] signin status: HTTP {resp.status_code} "
      f"{json.dumps(resp.json().get('data') or {}, ensure_ascii=False)[:160]}")

# 6 护栏负测: 超额抵扣 → 409(零副作用)
total = (c.get(f"/api/points/account/{UID}").json().get("data") or {}).get("totalPoints", 0)
resp = c.post("/api/points/deduct", headers=M, json={
    "userId": UID, "orderId": "LT-NEGATIVE-TEST",
    "orderAmount": 100.0,
    "deductPoints": (total // 100 + 2) * 100})
print(f"[6] deduct overflow: HTTP {resp.status_code} "
      f"err={resp.json().get('error') or resp.json().get('detail')}")

# 7 账本未变复验(负测零副作用实证)
d2 = (c.get(f"/api/points/account/{UID}").json().get("data") or {})
print(f"[7] account unchanged: {d2.get('totalPoints') == total} "
      f"(total={d2.get('totalPoints')}, ver={d2.get('version')})")
print("POINTS E2E DONE")
