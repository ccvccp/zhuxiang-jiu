"""生产 E2E v2: 推广码矩阵(改用真实会员1/2, 会员表校验所致)

链: claim(member1 douyin渠道) → my/codes → bind(invitee=member2)
  → 自绑409 → 重复绑定409 → team/stats/rewards → settings改+恢复
  → relations → grant wallet+wine_qualify → admin rewards →
  claim_wine(酒池产品) → wine-claims → ship×2 → purchase负测
  (不存在产品) → 新建码revoke → 关系invalidate

清理: 差集(幂等已有真实码不revoke不删; owner_codes SREM仅
  移除新码成员) + AI反馈LREM + drift还原.
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
U, U2 = 1, 2  # 真实会员(claim校验会员表)
M1 = {"X-Member-Id": str(U)}
A = {"X-Role": "admin"}
MARK = "LT推广E2E"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:promotion:*"))
before_drift = r.get("zhuxiang:ai_learning:drift:promotion_antifraud")
fb_key = "zhuxiang:ai_learning:feedback:promotion_antifraud"

print(f"[inv] promotion keys before: {len(before)}")

# A 推广码链
resp = c.post("/api/promotion/code/claim", headers=M1,
              json={"channel": "douyin"})
d = resp.json()
code = d.get("code") or (d.get("codes") or [None])[0]
is_new_code = f"zhuxiang:promotion:codes:{code}" not in before
print(f"[A1] claim: HTTP {resp.status_code} code={code} "
      f"new={is_new_code} keys={sorted(d.keys())[:7]}")

resp = c.get("/api/promotion/my/codes", headers=M1)
my = resp.json().get("codes") or []
print(f"[A2] my codes: HTTP {resp.status_code} count={len(my)} "
      f"hit={any(x.get('code') == code for x in my)}")

# B 绑定链
resp = c.post("/api/promotion/bind", json={
    "code": code, "inviteeMemberId": U2})
print(f"[B1] bind m2: HTTP {resp.status_code} "
      f"{json.dumps(resp.json(), ensure_ascii=False)[:120]}")

resp = c.post("/api/promotion/bind", json={
    "code": code, "inviteeMemberId": U})
print(f"[B2] self bind: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.post("/api/promotion/bind", json={
    "code": code, "inviteeMemberId": U2})
print(f"[B3] dup bind: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.get("/api/promotion/my/team", headers=M1)
team = resp.json().get("team") or []
print(f"[B4] team: HTTP {resp.status_code} count={len(team)} "
      f"first={json.dumps((team or [{}])[0], ensure_ascii=False)[:90]}")

resp = c.get("/api/promotion/my/stats", headers=M1)
print(f"[B5] stats: HTTP {resp.status_code} "
      f"{json.dumps({k: v for k, v in resp.json().items() if k != 'success'}, ensure_ascii=False)[:130]}")

resp = c.get("/api/promotion/my/rewards", headers=M1)
print(f"[B6] my rewards: HTTP {resp.status_code} "
      f"count={len((resp.json().get('rewards') or []))}")

# C 管理链
resp = c.get("/api/promotion/admin/settings", headers=A)
orig = (resp.json().get("settings") or {}).get("level1RewardAmount")
c.put("/api/promotion/admin/settings", headers=A,
      json={"level1RewardAmount": 21.0})
resp = c.put("/api/promotion/admin/settings", headers=A,
             json={"level1RewardAmount": orig})
print(f"[C1] settings改+恢复: HTTP {resp.status_code} "
      f"L1={(resp.json().get('settings') or {}).get('level1RewardAmount')} (orig={orig})")

resp = c.get("/api/promotion/admin/relations", headers=A,
             params={"inviterMemberId": U})
rels = resp.json().get("relations") or []
print(f"[C2] relations: HTTP {resp.status_code} count={len(rels)} "
      f"invitee={(rels or [{}])[0].get('inviteeMemberId')} status={(rels or [{}])[0].get('status')}")

resp = c.post("/api/promotion/admin/rewards/grant", headers=A, json={
    "memberId": U, "rewardType": "wallet", "amount": 5.0,
    "detail": MARK})
print(f"[C3] grant wallet: HTTP {resp.status_code} "
      f"{json.dumps(resp.json(), ensure_ascii=False)[:110]}")

resp = c.post("/api/promotion/admin/rewards/grant", headers=A, json={
    "memberId": U, "rewardType": "wine_qualify", "amount": 0,
    "detail": MARK})
print(f"[C4] grant wine_qualify: HTTP {resp.status_code} "
      f"{json.dumps(resp.json(), ensure_ascii=False)[:110]}")

resp = c.get("/api/promotion/admin/rewards", headers=A,
             params={"memberId": U})
rw = resp.json().get("rewards") or []
print(f"[C5] admin rewards: HTTP {resp.status_code} count={len(rw)} "
      f"types={[x.get('rewardType') for x in rw]}")

# D 奖励链
resp = c.get("/api/promotion/products/eligible")
prods = resp.json().get("products") or []
pid = (prods or [{}])[0].get("productId")
print(f"[D1] eligible: HTTP {resp.status_code} count={len(prods)} "
      f"first={pid} {(prods or [{}])[0].get('price')}")

resp = c.post("/api/promotion/wine/claim", headers=M1, json={
    "productId": str(pid), "address": "LT市E2E号"})
d = resp.json()
cl = d.get("claim") or d
claim_id = cl.get("claimId") or cl.get("id")
print(f"[D2] claim wine: HTTP {resp.status_code} claimId={claim_id} "
      f"{json.dumps(d, ensure_ascii=False)[:110]}")

resp = c.get("/api/promotion/admin/wine-claims", headers=A,
             params={"memberId": U})
claims = resp.json().get("claims") or []
print(f"[D3] wine-claims: HTTP {resp.status_code} count={len(claims)} "
      f"status={(claims or [{}])[0].get('status')}")

resp = c.put(f"/api/promotion/admin/wine-claims/{claim_id}/ship",
             headers=A)
print(f"[D4] ship: HTTP {resp.status_code} "
      f"{json.dumps({k: v for k, v in resp.json().items() if k != 'success'}, ensure_ascii=False)[:90]}")
resp = c.put(f"/api/promotion/admin/wine-claims/{claim_id}/ship",
             headers=A)
print(f"[D5] ship again: HTTP {resp.status_code} "
      f"{json.dumps({k: v for k, v in resp.json().items() if k != 'success'}, ensure_ascii=False)[:90]}")

resp = c.post("/api/promotion/reward/purchase", headers=M1, json={
    "productId": "LT-NOT-EXIST", "quantity": 1})
print(f"[D6] purchase invalid product: HTTP {resp.status_code} "
      f"err={resp.json().get('error')}")

# 撤销(仅新建码)+作废关系
if is_new_code:
    resp = c.post(f"/api/promotion/admin/codes/{code}/revoke", headers=A)
    print(f"[C6] revoke new code: HTTP {resp.status_code} "
          f"{json.dumps(resp.json(), ensure_ascii=False)[:80]}")
else:
    print(f"[C6] skip revoke (幂等已有真实码 {code})")

resp = c.post(f"/api/promotion/admin/relations/{U2}/invalidate", headers=A)
print(f"[C7] invalidate: HTTP {resp.status_code} "
      f"{json.dumps(resp.json(), ensure_ascii=False)[:80]}")

# 清理
removed = 0
for k in set(r.keys("zhuxiang:promotion:*")) - before:
    if k.endswith(":seq"):
        continue
    r.delete(k)
    removed += 1
# owner_codes:1 SREM 仅移除新码(真实码保留)
if is_new_code:
    r.srem(f"zhuxiang:promotion:owner_codes:{U}", code)

fb_removed = 0
for raw in r.lrange(fb_key, 0, -1):
    if f"grant:{U}" in raw and MARK not in raw:
        r.lrem(fb_key, 1, raw)
        fb_removed += 1
if before_drift is not None:
    r.set("zhuxiang:ai_learning:drift:promotion_antifraud", before_drift)
else:
    r.delete("zhuxiang:ai_learning:drift:promotion_antifraud")

residual = [k for k in set(r.keys("zhuxiang:promotion:*")) - before
            if not k.endswith(":seq")]
print(f"[clean] removed={removed} residual={residual} "
      f"fb_lrem={fb_removed} "
      f"settings_L1={r.hget('zhuxiang:promotion:settings', 'level1RewardAmount')}")
print("PROMOTION E2E V2 DONE")
