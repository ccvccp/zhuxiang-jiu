"""生产造数: 双码追溯(生命码管理)全链路测试

链路: 箱码生成→生命码生成→箱码绑定→激活(积分奖励)→扫码溯源→
链路查询→防窜检测(本区)→转让→统计汇总; 差集清理(排除seq防id复用)。
测试批次带 -LT 后缀与生产批次隔离; 激活奖励50积分与订单E2E同口径
接受漂移(报告说明)。
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
A = {"X-Role": "admin"}
BATCH = "ZX52-2026X02-B01-LT"
PID = "ZX52-2026X02"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)
M = {"X-Member-Id": "1"}

# 预清理: 键名或值含 -LT 的残留(实体键 box:{id}/life:{id}/scan:{id} 的批次在值里,
# 索引键在键名里; "zhuxiang:trace:*" 带冒号不误伤 traceprod; seq 保留防 id 复用)
def clean_lt():
    n = 0
    for k in r.keys("zhuxiang:trace:*"):
        if k.endswith(":seq"):
            continue
        v = r.get(k) if r.type(k) == "string" else None
        if "-LT" in k or (v and "-LT" in v):
            r.delete(k)
            n += 1
    return n


pre_cleaned = clean_lt()
if pre_cleaned:
    print(f"[pre-clean] removed {pre_cleaned} residual keys")

before = set(r.keys("zhuxiang:trace*"))

# 1 生成箱码(1 箱)
d = c.post("/api/trace/box/generate", json={
    "productId": PID, "batchNo": BATCH, "count": 1},
    headers=A).json()["data"]
box = d["boxes"][0]
print(f"[1] box generate: id={box['id']} code={box['boxCode']}")

# 2 生成生命码(2 枚)
d = c.post("/api/trace/life/generate", json={
    "productId": PID, "batchNo": BATCH, "count": 2,
    "productName": "竹奕·竹香小坛 52°", "productAbv": 52,
    "productVolume": "500ml"}, headers=A).json()["data"]
lifes = d["lifeCodes"]
print(f"[2] life generate: {[(l['id'], l['lifeCode']) for l in lifes]}")

# 3 箱码绑定生命码
d = c.post("/api/trace/box/bind", json={
    "boxId": box["id"], "lifeCodeIds": [l["id"] for l in lifes]},
    headers=A).json()
print(f"[3] box bind: success={d.get('success')}")

life_code = lifes[0]["lifeCode"]

# 4 激活(member 1, 泰安本区)
resp = c.post("/api/trace/activate", headers=M, json={
    "lifeCode": life_code, "userId": 1, "userName": "测试会员",
    "province": "山东省", "city": "泰安市", "district": "泰山区",
    "purchaseChannel": "offline", "purchasePrice": 168.0})
d = resp.json()
data = d.get("data") or {}
print(f"[4] activate: HTTP {resp.status_code} success={d.get('success')} "
      f"reward={data.get('rewardPoints')} status={data.get('status')} "
      f"err={d.get('error') or d.get('detail')}")

# 5 扫码溯源
resp = c.post("/api/trace/scan", headers=M, json={
    "code": life_code, "userId": 1,
    "province": "山东省", "city": "泰安市"})
d = resp.json()
print(f"[5] scan: HTTP {resp.status_code} success={d.get('success')} "
      f"err={d.get('error') or d.get('detail')}")

# 6 溯源链路
d = c.get(f"/api/trace/chain/{life_code}").json()
chain = (d.get("data") or {}).get("chain") or []
print(f"[6] chain: success={d.get('success')} nodes={len(chain)}")

# 7 防窜检测(本区泰安, 应无风险)
d = c.post("/api/trace/anti-channel", json={
    "lifeCode": life_code, "longitude": 117.13, "latitude": 36.19,
    "province": "山东省", "city": "泰安市"}).json()
ac = d.get("data") or {}
print(f"[7] anti-channel: risk={ac.get('riskLevel', ac)} "
      f"raw={json.dumps(ac, ensure_ascii=False)[:160]}")

# 8 转让 1→2
resp = c.post("/api/trace/transfer", headers=M, json={
    "lifeCode": life_code, "fromUserId": 1, "toUserId": 2,
    "toName": "竹香爱好者", "transferType": "gift",
    "province": "山东省", "city": "泰安市"})
d = resp.json()
print(f"[8] transfer: HTTP {resp.status_code} success={d.get('success')} "
      f"err={d.get('error') or d.get('detail')}")

# 9 统计汇总
d = c.get("/api/trace/stats", headers=A).json()["data"]
print(f"[9] stats: boxes={d['totalBoxes']} lifes={d['totalLifeCodes']} "
      f"active={d['activeCount']} rate={d['activationRate']}")

# ---- 清理: 实体键(值含-LT) + 索引键(键名含-LT), seq 保留 ----
cleaned_n = clean_lt()
residual = [k for k in r.keys("zhuxiang:trace:*")
            if not k.endswith(":seq")
            and ("-LT" in k
                 or ((r.type(k) == "string"
                      and "-LT" in (r.get(k) or ""))))]
print(f"[clean] removed {cleaned_n} keys, residual: {residual}")
print("TRACE E2E DONE")
