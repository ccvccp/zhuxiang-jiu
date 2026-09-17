"""生产 E2E: 老酒兑换回收模块全链实证(21 端点)

四条链:
    A 老酒兑换链: valuation/submit(2015年A档)→valuation详情→
      my-valuations→application/submit(exchange)→applications列表
      →review通过→exchange(差额转积分)→exchange/complete
      (老酒入库)→exchanges列表→inventory(库存+1)
    B 折现回收链: valuation(forExchange=false)→application
      (recycle+payout)→review通过→recycle-for-cash(80%折现+
      个税)→complete(入库)
    C 新酒议价链: new-wine/valuation(2025年1岁酒→AI基准价)
      →negotiation详情→negotiations列表→propose(出价系数
      超范围409负测→合法出价, AI recycle_valuation门observe
      快照)→counter(AI反价)→accept(终态回流)→new-wine/
      recycle(完成回收)
    D 统计+负测: stats(管理员)→老酒估价酒龄不足409

清理联动面: recycle域差集 + inventory hash字段HDEL +
    ai_learning反馈条目LREM(negotiation:{negId}) + drift还原;
    seq四键(application/valuation/exchange/negotiation)留痕.
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
U = 99005
M5 = {"X-Member-Id": str(U)}
A = {"X-Role": "admin"}
MARK = "LT老酒E2E"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:recycle:*"))
before_drift = r.get("zhuxiang:ai_learning:drift:recycle_valuation")
fb_key = "zhuxiang:ai_learning:feedback:recycle_valuation"

print(f"[inv] recycle keys before: {len(before)}")

# ============================================================
# A 老酒兑换链
# ============================================================

resp = c.post("/api/recycle/valuation/submit", headers=M5, json={
    "userId": U, "productId": f"{MARK}-OLD-001", "purchasePrice": 500,
    "purchaseDate": "2015-06-01", "conditionGrade": "A",
    "memberLevel": 3, "forExchange": True})
val = resp.json().get("data") or {}
vid = val.get("id")
print(f"[A1] valuation: HTTP {resp.status_code} id={vid} "
      f"age={val.get('wineAge')}y oldValue={val.get('oldValue')} "
      f"cash={val.get('cashValue')} hash={str(val.get('blockHash'))[:12]}")

resp = c.get(f"/api/recycle/valuation/{vid}")
print(f"[A2] valuation detail: HTTP {resp.status_code} "
      f"productId={(resp.json().get('data') or {}).get('productId')}")

resp = c.get("/api/recycle/my-valuations", headers=M5)
mine = resp.json().get("data") or []
print(f"[A3] my-valuations: HTTP {resp.status_code} count={len(mine)} "
      f"hit={any(v.get('id') == vid for v in mine)}")

resp = c.post("/api/recycle/application/submit", headers=M5, json={
    "userId": U, "type": "exchange", "valuationIds": [vid],
    "newProductId": f"{MARK}-NEW-001", "newProductPrice": 300.0})
app = resp.json().get("data") or {}
aid = app.get("id")
print(f"[A4] application(exchange): HTTP {resp.status_code} id={aid} "
      f"no={app.get('applicationNo')} oldValue={app.get('oldWineTotalValue')}")

resp = c.get("/api/recycle/applications", params={"user_id": U})
lst = resp.json().get("data") or []
print(f"[A5] applications: HTTP {resp.status_code} count={len(lst)} "
      f"hit={any(a.get('id') == aid for a in lst)}")

resp = c.post(f"/api/recycle/application/{aid}/review", headers=A,
              json={"approved": True, "reviewer": "LT-admin"})
print(f"[A6] review: HTTP {resp.status_code} "
      f"status={(resp.json().get('data') or {}).get('status')}")

resp = c.post(f"/api/recycle/application/{aid}/exchange", headers=M5,
              json={"newProductId": f"{MARK}-NEW-001",
                    "newProductPrice": 300.0,
                    "diffPaymentMethod": "wechat"})
ex = resp.json().get("data") or {}
exid = ex.get("id")
print(f"[B7] exchange: HTTP {resp.status_code} exId={exid} "
      f"priceDiff={ex.get('priceDiff')} points={ex.get('pointsConverted')}")

resp = c.post(f"/api/recycle/exchange/{exid}/complete", headers=A)
print(f"[A8] complete: HTTP {resp.status_code} "
      f"{json.dumps(resp.json().get('data') or {}, ensure_ascii=False)[:90]}")

resp = c.get("/api/recycle/exchanges", params={"user_id": U})
exs = resp.json().get("data") or []
print(f"[A9] exchanges: HTTP {resp.status_code} count={len(exs)} "
      f"hit={any(e.get('id') == exid for e in exs)}")

resp = c.get("/api/recycle/inventory", headers=A)
inv = resp.json().get("data") or {}
print(f"[A10] inventory: HTTP {resp.status_code} "
      f"LT_OLD_001={inv.get(f'{MARK}-OLD-001')}")

# ============================================================
# B 折现回收链
# ============================================================

resp = c.post("/api/recycle/valuation/submit", headers=M5, json={
    "userId": U, "productId": f"{MARK}-OLD-002", "purchasePrice": 800,
    "purchaseDate": "2014-03-01", "conditionGrade": "B",
    "memberLevel": 1, "forExchange": False})
val2 = resp.json().get("data") or {}
vid2 = val2.get("id")
print(f"[B1] valuation2(cash): HTTP {resp.status_code} id={vid2} "
      f"cash={val2.get('cashValue')}")

resp = c.post("/api/recycle/application/submit", headers=M5, json={
    "userId": U, "type": "recycle", "valuationIds": [vid2],
    "payoutMethod": "bank", "payoutAccount": "LT-ACCOUNT"})
app2 = resp.json().get("data") or {}
aid2 = app2.get("id")
print(f"[B2] application(recycle): HTTP {resp.status_code} id={aid2}")

c.post(f"/api/recycle/application/{aid2}/review", headers=A,
       json={"approved": True, "reviewer": "LT-admin"})

resp = c.post(f"/api/recycle/application/{aid2}/recycle", headers=M5,
              json={"payoutMethod": "bank",
                    "payoutAccount": "LT-ACCOUNT"})
ex2 = resp.json().get("data") or {}
exid2 = ex2.get("id")
print(f"[B3] recycle-for-cash: HTTP {resp.status_code} exId={exid2} "
      f"cash={ex2.get('cashAmount')} tax={ex2.get('taxAmount')} "
      f"payout={ex2.get('actualPayout')}")

resp = c.post(f"/api/recycle/exchange/{exid2}/complete", headers=A)
print(f"[B4] complete2: HTTP {resp.status_code}")

# ============================================================
# C 新酒议价链
# ============================================================

resp = c.post("/api/recycle/new-wine/valuation", headers=M5, json={
    "userId": U, "productId": f"{MARK}-NEW-002", "purchasePrice": 400,
    "purchaseDate": "2025-06-01", "conditionGrade": "A",
    "bottleCount": 1})
neg = resp.json().get("data") or {}
nid = neg.get("id")
base_price = neg.get("aiBasePrice")
print(f"[C1] new-wine valuation: HTTP {resp.status_code} negId={nid} "
      f"category={neg.get('wineAgeCategoryName')} "
      f"aiBase={base_price} status={neg.get('status')}")

resp = c.get(f"/api/recycle/negotiation/{nid}")
print(f"[C2] negotiation detail: HTTP {resp.status_code} "
      f"round={neg.get('negotiationRound')} max={neg.get('maxRounds')}")

resp = c.get("/api/recycle/negotiations", params={"user_id": U})
negs = resp.json().get("data") or []
print(f"[C3] negotiations: HTTP {resp.status_code} count={len(negs)} "
      f"hit={any(n.get('id') == nid for n in negs)}")

resp = c.post(f"/api/recycle/negotiation/{nid}/propose", headers=M5,
              json={"proposedPrice": base_price * 1.5, "reason": "LT高价"})
print(f"[C4] propose out-of-range: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.post(f"/api/recycle/negotiation/{nid}/propose", headers=M5,
              json={"proposedPrice": round(base_price * 1.05, 2),
                    "reason": "LT合理出价"})
print(f"[C5] propose x1.05: HTTP {resp.status_code} "
      f"round={(resp.json().get('data') or {}).get('negotiationRound')} "
      f"status={(resp.json().get('data') or {}).get('status')}")

resp = c.post(f"/api/recycle/negotiation/{nid}/counter", headers=A,
              json={"counterPrice": round(base_price * 0.95, 2),
                    "aiReason": "LT反价"})
print(f"[C6] ai counter x0.95: HTTP {resp.status_code} "
      f"current={(resp.json().get('data') or {}).get('currentPrice')}")

resp = c.post(f"/api/recycle/negotiation/{nid}/accept", headers=M5,
              json={"acceptedBy": "user"})
acc = resp.json().get("data") or {}
print(f"[C7] accept: HTTP {resp.status_code} final={acc.get('finalPrice')} "
      f"status={acc.get('status')}")

resp = c.post(f"/api/recycle/new-wine/{nid}/recycle", headers=M5,
              json={"payoutMethod": "bank", "payoutAccount": "LT-ACC"})
done = resp.json().get("data") or {}
print(f"[C8] new-wine recycle: HTTP {resp.status_code} "
      f"{json.dumps(done, ensure_ascii=False)[:120]}")

# ============================================================
# D 统计 + 负测
# ============================================================

resp = c.get("/api/recycle/stats", headers=A)
d = resp.json().get("data") or {}
print(f"[D1] stats: HTTP {resp.status_code} "
      f"apps={d.get('totalApplications')} "
      f"exchanges={d.get('totalExchanges')} "
      f"valucs={d.get('totalValuations')} "
      f"keys={sorted(d.keys())[:8]}")

resp = c.post("/api/recycle/valuation/submit", headers=M5, json={
    "userId": U, "productId": f"{MARK}-YOUNG", "purchasePrice": 100,
    "purchaseDate": "2025-12-01", "conditionGrade": "A"})
print(f"[D2] young wine: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

# ============================================================
# 清理(差集 + inventory字段 + AI联动)
# ============================================================

removed = 0
for k in set(r.keys("zhuxiang:recycle:*")) - before:
    if k.endswith(":seq"):
        continue
    r.delete(k)
    removed += 1

# inventory hash 字段回退(若键为本轮新建已被差集删除)
for pid in (f"{MARK}-OLD-001", f"{MARK}-OLD-002", f"{MARK}-NEW-002"):
    r.hdel("zhuxiang:recycle:inventory", pid)

fb_removed = 0
for raw in r.lrange(fb_key, 0, -1):
    # on_negotiation_settled note 为 "negotiation {id} accepted @¥{p}"
    # (空格式), 决策键为 negotiation:{id}(冒号)——双格式匹配
    if (f"negotiation {nid} " in raw
            or f"negotiation:{nid}" in raw):
        r.lrem(fb_key, 1, raw)
        fb_removed += 1

if before_drift is not None:
    r.set("zhuxiang:ai_learning:drift:recycle_valuation", before_drift)
else:
    r.delete("zhuxiang:ai_learning:drift:recycle_valuation")

for k in r.keys("zhuxiang:ai_learning:snapshot:recycle_valuation*"):
    if f"negotiation:{nid}" in k:
        r.delete(k)
        removed += 1

residual = [k for k in set(r.keys("zhuxiang:recycle:*")) - before
            if not k.endswith(":seq")]
fb_left = [x for x in r.lrange(fb_key, 0, -1)
           if f"negotiation:{nid}" in x]
print(f"[clean] removed={removed} residual={residual} "
      f"fb_lrem={fb_removed} fb_left={len(fb_left)} "
      f"drift_restored={before_drift is not None}")
print("RECYCLE E2E DONE")
