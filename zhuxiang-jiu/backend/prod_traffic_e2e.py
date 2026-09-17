"""生产 E2E: 流量管理模块全链实证(21 端点)

三条链:
    A 推广员链(用户端+管理端): create → list → detail → level
      → lead/record → leads → stats → commission/calculate
      → fission → source/create → source/list → distribute → admin/stats
    B 达人链(8端点): create → list → platform → sync → promo-code
      → attribute → attribution → detail
    C 联动清理: role 总账 TRF-* + ai_learning 反馈条目(LREM)与
      drift 聚合还原(快照已被 record_outcome 消费自删)

清理: 差集法 + LT 标记值匹配; seq 键留痕防 id 复用。
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
M = {"X-Member-Id": "1"}
A = {"X-Role": "admin"}
MARK = "LT流量E2E"
ORDER_ID = "LT-TRF-E2E-001"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before_traffic = set(r.keys("zhuxiang:traffic*"))
before_drift = r.get("zhuxiang:ai_learning:drift:traffic_antifraud")
fb_key = "zhuxiang:ai_learning:feedback:traffic_antifraud"
before_fb_raw = r.lrange(fb_key, 0, -1)

print(f"[inv] traffic keys before: {len(before_traffic)}")
for k in sorted(before_traffic)[:12]:
    print(f"      {k}")

# ============================================================
# A 推广员链
# ============================================================

resp = c.post("/api/traffic/promoter/create", headers=M, json={
    "userId": 91001, "name": f"{MARK}推广员", "level": "trainee"})
d = resp.json().get("data") or {}
pid = d.get("id")
pcode = d.get("promoterCode")
print(f"[A1] promoter create: HTTP {resp.status_code} id={pid} "
      f"code={pcode} level={d.get('level')} rate={d.get('commissionRate')}")

resp = c.get("/api/traffic/promoter/list")
lst = (resp.json().get("data") or [])
hit = any(p.get("id") == pid for p in lst)
print(f"[A2] promoter list: HTTP {resp.status_code} count={len(lst)} hit={hit}")

resp = c.get(f"/api/traffic/promoter/{pid}", headers=M)
d = resp.json().get("data") or {}
print(f"[A3] promoter detail: HTTP {resp.status_code} "
      f"name={d.get('name')} invited={d.get('totalInvited')}")

resp = c.get(f"/api/traffic/promoter/level/{pid}", headers=M)
d = resp.json().get("data") or {}
print(f"[A4] promoter level: HTTP {resp.status_code} level={d.get('currentLevel')} "
      f"next={d.get('nextLevel')} condition={d.get('nextCondition')}")

resp = c.post("/api/traffic/lead/record", headers=M, json={
    "promoterId": pid, "userId": 91002, "source": "douyin",
    "medium": "video", "utmParams": MARK, "status": "registered"})
d = resp.json().get("data") or {}
print(f"[A5] lead record: HTTP {resp.status_code} leadId={d.get('id')} "
      f"status={d.get('status')}")

resp = c.get("/api/traffic/leads", params={"promoter_id": pid})
leads = (resp.json().get("data") or [])
print(f"[A6] leads list: HTTP {resp.status_code} count={len(leads)} "
      f"first_source={(leads or [{}])[0].get('source')}")

resp = c.get("/api/traffic/stats", params={"promoter_id": pid})
d = resp.json().get("data") or {}
print(f"[A7] promoter stats: HTTP {resp.status_code} "
      f"invited={d.get('totalInvited')} registered={d.get('totalRegistered')} "
      f"effective={d.get('effectiveLeads')}")

resp = c.post("/api/traffic/commission/calculate", headers=A, json={
    "promoterId": pid, "orderId": ORDER_ID, "orderAmount": 1000.0,
    "userId": 91002})
d = resp.json().get("data") or {}
cid = d.get("commissionId")
print(f"[A8] commission: HTTP {resp.status_code} id={cid} "
      f"commission={d.get('commission')} rate={d.get('commissionRate')} "
      f"ledger={d.get('ledgerRecorded')}")

resp = c.get(f"/api/traffic/fission/{pid}", headers=A)
d = resp.json().get("data") or {}
print(f"[A9] fission tree: HTTP {resp.status_code} "
      f"direct={d.get('directSubordinates')} total={d.get('totalSubordinates')}")

resp = c.post("/api/traffic/source/create", headers=A, json={
    "code": "lt-e2e-src", "name": f"{MARK}来源", "description": "LT"})
d = resp.json().get("data") or {}
sid = d.get("id")
print(f"[B1] source create: HTTP {resp.status_code} id={sid} code={d.get('code')}")

resp = c.get("/api/traffic/source/list")
srcs = (resp.json().get("data") or [])
print(f"[B2] source list: HTTP {resp.status_code} count={len(srcs)} "
      f"hit={any(s.get('id') == sid for s in srcs)}")

resp = c.post("/api/traffic/distribute", headers=A, json={
    "totalTraffic": 100, "strategy": "average"})
d = resp.json().get("data") or {}
dists = d.get("distributions") or []
me = [x for x in dists if x.get("promoterId") == pid]
print(f"[B3] distribute: HTTP {resp.status_code} distributors={d.get('distributorCount')} "
      f"me={me[0].get('traffic') if me else None}")

resp = c.get("/api/traffic/admin/stats", headers=A)
d = resp.json().get("data") or {}
print(f"[B4] admin stats: HTTP {resp.status_code} promoters={d.get('totalPromoters')} "
      f"leads={d.get('totalLeads')} sources={d.get('totalSources')} "
      f"commission={d.get('totalCommission')}")

# ============================================================
# C 达人链(8 端点)
# ============================================================

resp = c.post("/api/traffic/influencer/create", headers=A, json={
    "userId": 91003, "name": f"{MARK}达人", "level": "C",
    "commissionRate": 0.10})
d = resp.json().get("data") or {}
iid = d.get("id")
print(f"[C1] influencer create: HTTP {resp.status_code} id={iid} "
      f"level={d.get('level')} rate={d.get('commissionRate')}")

resp = c.get("/api/traffic/influencer/list", headers=M)
infs = (resp.json().get("data") or [])
print(f"[C2] influencer list: HTTP {resp.status_code} count={len(infs)} "
      f"hit={any(i.get('id') == iid for i in infs)}")

resp = c.post(f"/api/traffic/influencer/{iid}/platform", headers=A, json={
    "platform": "douyin", "platformUid": "LT-DY-001",
    "platformName": f"{MARK}抖音号", "followerCount": 10000})
d = resp.json().get("data") or {}
plat_id = d.get("id")
print(f"[C3] platform add: HTTP {resp.status_code} id={plat_id} "
      f"followers={d.get('followerCount')}")

resp = c.post(f"/api/traffic/influencer/platform/{plat_id}/sync", headers=A,
              json={"followerCount": 20000, "verified": True})
d = resp.json().get("data") or {}
print(f"[C4] platform sync: HTTP {resp.status_code} "
      f"followers={d.get('followerCount')} verified={d.get('verified')}")

resp = c.post(f"/api/traffic/influencer/{iid}/promo-code", headers=A,
              json={"platform": "douyin"})
d = resp.json().get("data") or {}
promo = d.get("promoCode")
code_id = d.get("id")
print(f"[C5] promo code: HTTP {resp.status_code} code={promo} id={code_id}")

resp = c.post("/api/traffic/influencer/attribute", headers=A, json={
    "promoCode": promo, "isClick": True, "isLead": True,
    "orderAmount": 1000.0})
d = resp.json().get("data") or {}
print(f"[C6] attribute: HTTP {resp.status_code} attributed={d.get('attributed')} "
      f"inf={d.get('influencerId')} amount={d.get('orderAmount')}")

resp = c.get(f"/api/traffic/influencer/{iid}/attribution", headers=M)
d = resp.json().get("data") or {}
print(f"[C7] attribution: HTTP {resp.status_code} gmv={d.get('totalGmv')} "
      f"orders={d.get('totalOrders')} traffic={d.get('totalTraffic')} "
      f"platforms={d.get('platformCount')}")

resp = c.get(f"/api/traffic/influencer/{iid}", headers=M)
d = resp.json().get("data") or {}
print(f"[C8] influencer detail: HTTP {resp.status_code} "
      f"platforms={len(d.get('platforms') or [])} "
      f"codes={len(d.get('promoCodes') or [])} "
      f"followers={(d.get('platforms') or [{}])[0].get('followerCount')}")

# ============================================================
# 清理(差集 + LT 值匹配; seq 留痕)
# ============================================================

removed = 0
for k in set(r.keys("zhuxiang:traffic*")) - before_traffic:
    if k.endswith(":seq"):
        continue  # seq 计数器留痕防 id 复用
    if r.type(k) == "string":
        v = r.get(k) or ""
        if any(t in k for t in (str(pid), str(sid), str(iid), str(plat_id),
                                 str(code_id), pcode or "∅", promo or "∅",
                                 "lt-e2e-src")) or MARK in v or ORDER_ID in v:
            r.delete(k)
            removed += 1
    elif r.type(k) == "list":
        # 索引链表(lead/commission/platform/code by parent)
        if any(str(t) in k for t in (pid, iid)):
            r.delete(k)
            removed += 1

# lead 明细键(值含 MARK/ORDER_ID 但键名只有 leadId)
for k in set(r.keys("zhuxiang:traffic:lead:*")) - before_traffic:
    v = r.get(k) or ""
    if MARK in v:
        r.delete(k)
        removed += 1

# role 总账 TRF 流水
ledger_key = f"zhuxiang:role:ledger:TRF-{cid}"
if r.get(ledger_key) is not None:
    r.delete(ledger_key)
    removed += 1

# ai_learning 反馈条目 LREM(按 note 匹配 biz_no)
biz_no = f"{pid}:{ORDER_ID}"
fb_removed = 0
for raw in r.lrange(fb_key, 0, -1):
    if f"commission {biz_no}" in raw:
        r.lrem(fb_key, 1, raw)
        fb_removed += 1

# drift 聚合还原
if before_drift is not None:
    r.set("zhuxiang:ai_learning:drift:traffic_antifraud", before_drift)
else:
    r.delete("zhuxiang:ai_learning:drift:traffic_antifraud")

# 快照残留检查(正常已被消费)
snap_left = [k for k in r.keys("zhuxiang:ai_learning:snapshot:traffic_antifraud*")
             if biz_no in k]
for k in snap_left:
    r.delete(k)
    removed += 1

residual = [k for k in set(r.keys("zhuxiang:traffic*")) - before_traffic
            if not k.endswith(":seq")]
fb_after = [x for x in r.lrange(fb_key, 0, -1) if biz_no in x]
ledger_left = r.get(ledger_key) is not None
print(f"[clean] traffic removed={removed} residual={residual} "
      f"fb_lrem={fb_removed} fb_left={len(fb_after)} "
      f"ledger_left={ledger_left} drift_restored={before_drift is not None}")
print("TRAFFIC E2E DONE")
