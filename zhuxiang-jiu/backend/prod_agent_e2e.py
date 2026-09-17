"""生产 E2E: 代理商管理模块全链路实证

链路: 申请入驻→admin审核(建档案)→钱包充值(Redis,测试操作)→档案
更新→进货(等级折扣+扣库存+扣钱包)→返利计算(档位)→返利提现→风控
报告+评估→差集清理(agent域全键+库存回补, seq保留)。

隔离: 公司名/联系人带 LT 标记; 新代理商档案整体删除即净。
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
A = {"X-Role": "admin"}
PID = "ZX42-2026B01"  # ¥88
QTY = 20

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

# 预清理: 上轮中断残留(hash 值含 LT 标记; 索引/申请单同)
pre = 0
for k in r.keys("zhuxiang:agent*"):
    if k.endswith(":seq"):
        continue
    h = r.hgetall(k) if r.type(k) == "hash" else {}
    if any("LT" in str(v) for v in h.values()):
        r.delete(k)
        pre += 1
if pre:
    print(f"[pre-clean] removed {pre} residual keys")

# 返利档位(信息)
tiers = c.get("/api/agent/rebate/tiers").json()
print(f"[0] rebate tiers: {json.dumps(tiers.get('tiers', tiers), ensure_ascii=False)[:200]}")

before = set(r.keys("zhuxiang:agent*"))

# 1 申请入驻(公开)
resp = c.post("/api/agent/apply", json={
    "companyName": "LT测试代理商有限公司", "contactName": "LT联系人",
    "contactPhone": "19900000099", "region": "山东省泰安市LT区",
    "applyLevel": "D"})
d = resp.json()
print(f"[1] apply: HTTP {resp.status_code} applyId={d.get('applyId')} "
      f"status={d.get('status')}")
apply_id = d["applyId"]

# 2 admin 审核(建档案)
resp = c.post(f"/api/agent/audit/{apply_id}", json={
    "decision": "approved", "auditRemark": "LT全链路测试"},
    headers=A)
d = resp.json()
print(f"[2] audit: HTTP {resp.status_code} agentId={d.get('agentId')} "
      f"level={d.get('level')}")
agent_id = d["agentId"]
AG = {"X-Agent-Id": str(agent_id)}

# 3 钱包充值(新档案 wallet=0, 测试操作; 浮点安全 HSET; 档案整体删除即净)
_key = f"zhuxiang:agent:{agent_id}"
_base = float(r.hget(_key, "wallet") or 0)
r.hset(_key, "wallet", _base + 2000)
print(f"[3] wallet top-up 2000 -> {r.hget(_key, 'wallet')}")

# 4 档案更新
resp = c.put(f"/api/agent/{agent_id}", json={
    "address": "LT市LT区竹香路1号"}, headers=AG)
print(f"[4] profile update: HTTP {resp.status_code}")

# 5 进货(20 瓶 ×88, D 级 0.8 折)
resp = c.post(f"/api/agent/{agent_id}/purchase", json={
    "items": [{"productId": PID, "quantity": QTY}]}, headers=AG)
d = resp.json()
print(f"[5] purchase: HTTP {resp.status_code} purchaseId={d.get('purchaseId')} "
      f"goodsTotal={d.get('goodsTotal')} discount={d.get('discountRate')} "
      f"paid={d.get('totalAmount')} wallet={d.get('wallet')}")
purchase_amount = d["totalAmount"]
purchase_id = d["purchaseId"]

# 6 进货记录查询
resp = c.get(f"/api/agent/{agent_id}/purchases", headers=AG).json()
print(f"[6] purchases list: count={resp.get('count')}")

# 7 返利计算(档位 20 万起: 以 T1 档月度进货额 25 万试算产生返利记录;
#    calc 端点为手动传额试算+落记录设计)
resp = c.post(f"/api/agent/{agent_id}/rebate/calc", json={
    "purchaseAmount": 250000}, headers=AG)
d = resp.json()
print(f"[7] rebate calc: HTTP {resp.status_code} "
      f"{json.dumps(d, ensure_ascii=False)[:250]}")
rebate_id = (d.get("data") or d).get("rebateId")

# 8 返利列表 + 汇总
resp = c.get(f"/api/agent/{agent_id}/rebates", headers=AG).json()
print(f"[8] rebates: count={resp.get('count')}")
resp = c.get(f"/api/agent/{agent_id}/rebate/summary", headers=AG).json()
print(f"[8] summary: {json.dumps(resp, ensure_ascii=False)[:180]}")

# 9 返利提现
if rebate_id:
    resp = c.post(f"/api/agent/{agent_id}/rebate/withdraw", json={
        "rebateId": rebate_id}, headers=AG)
    d = resp.json()
    print(f"[9] withdraw: HTTP {resp.status_code} "
          f"amount={d.get('amount')} wallet={d.get('wallet')} "
          f"status={d.get('status')}")
else:
    print("[9] withdraw skipped(无返利记录)")

# 10 风控报告 + 评估
resp = c.get(f"/api/agent/{agent_id}/risk/report", headers=AG).json()
print(f"[10] risk report: HTTP "
      f"{json.dumps(resp, ensure_ascii=False)[:180]}")
resp = c.post(f"/api/agent/{agent_id}/risk/assess", headers=AG)
d = resp.json()
print(f"[10] risk assess: HTTP {resp.status_code} "
      f"{json.dumps(d, ensure_ascii=False)[:180]}")

# ---- 差集清理(agent 域; seq 保留) + 库存回补 ----
after = set(r.keys("zhuxiang:agent*"))
new_keys = [k for k in (after - before) if not k.endswith(":seq")]
for k in new_keys:
    r.delete(k)
r.hincrby(f"zhuxiang:inventory:{PID}", "stock", QTY)
residual = set(r.keys("zhuxiang:agent*")) - before
print(f"[clean] removed {len(new_keys)} keys, residual={sorted(residual)}, "
      f"stock+{QTY} -> {r.hget(f'zhuxiang:inventory:{PID}', 'stock')}")
print("AGENT E2E DONE")
