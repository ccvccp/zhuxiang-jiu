"""生产 E2E: 酒店酒吧会所合作商模块全链实证(12 端点)

四条链:
    A 合作商链: apply(pending/D级) → 公开详情/列表 → audit
      (approve→signed, 合同期+初始等级) → transition(active
      合作中) → 非法流转负测(signed→active已过后非法)
    B 场地+铺货链: create-venue → list-venues → update-venue
      → add-stocking(active合作商, 直供模式, 差价利润=(零售-
      进货)×数量)
    C 分级+结算+统计链: grade(D→C) → settle(多级分润: 无代理
      本站80%/酒店20%, 品鉴酒1%, 铺货置offline, role总账
      VEN-×2联动记账) → stats(聚合)
    负测: 无头401 / 非法等级409 / 非active结算409

清理: venue域差集+三ids set SREM+by_partner/by_venue set
    删除 + role:ledger:VEN-{pid}-*删除; seq三键留痕.
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
U = 99007
M7 = {"X-Member-Id": str(U)}
A = {"X-Role": "admin"}
MARK = "LT酒店E2E"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:venue:*"))
print(f"[inv] venue keys before: {len(before)}")

# ============================================================
# A 合作商链
# ============================================================

resp = c.post("/api/venue/partners", headers=M7, json={
    "partnerType": "hotel", "partnerName": f"{MARK}大酒店",
    "creditCode": "LT91370100MA3LT001", "legalPerson": "LT法人",
    "contactPhone": "0531-0000000",
    "contactAddress": "LT市LT区LT路1号",
    "longitude": 117.0, "latitude": 36.65, "starLevel": 5})
pt = resp.json().get("data") or {}
pid = pt.get("id")
print(f"[A1] apply: HTTP {resp.status_code} id={pid} "
      f"status={pt.get('status')} level={pt.get('partnerLevel')} "
      f"code={pt.get('partnerCode')}")

resp = c.get(f"/api/venue/partners/{pid}")
d = resp.json().get("data") or {}
print(f"[A2] get partner: HTTP {resp.status_code} "
      f"name={d.get('partnerName')} type={d.get('partnerType')}")

resp = c.get("/api/venue/partners", params={"partner_type": "hotel"})
pl = resp.json().get("data") or []
print(f"[A3] list partners: HTTP {resp.status_code} count={len(pl)} "
      f"hit={any(p.get('id') == pid for p in pl)}")

resp = c.post(f"/api/venue/partners/{pid}/audit", headers=A, json={
    "action": "approve", "auditorId": 1,
    "contractStart": "2026-09-18", "contractEnd": "2027-09-18",
    "partnerLevel": "D"})
d = resp.json().get("data") or {}
print(f"[A4] audit approve: HTTP {resp.status_code} "
      f"status={d.get('status')} level={d.get('partnerLevel')}")

resp = c.post(f"/api/venue/partners/{pid}/transition", headers=A,
              json={"targetStatus": "active", "operatorId": 1,
                    "remark": "LT激活"})
print(f"[A5] transition active: HTTP {resp.status_code} "
      f"status={(resp.json().get('data') or {}).get('status')}")

resp = c.post(f"/api/venue/partners/{pid}/transition", headers=A,
              json={"targetStatus": "signed", "operatorId": 1})
print(f"[A6] invalid transition: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

# ============================================================
# B 场地 + 铺货链
# ============================================================

resp = c.post("/api/venue/venues", headers=M7, json={
    "partnerId": pid, "venueName": f"{MARK}宴会厅",
    "venueType": "banquet_hall", "address": "LT酒店3层",
    "capacity": 200, "managerName": "LT经理",
    "managerPhone": "13800000000", "businessHours": "09:00-22:00"})
vn = resp.json().get("data") or {}
vid = vn.get("id")
print(f"[B1] create venue: HTTP {resp.status_code} id={vid} "
      f"name={vn.get('venueName')} type={vn.get('venueType')}")

resp = c.get("/api/venue/venues", params={"partner_id": pid})
vl = resp.json().get("data") or []
print(f"[B2] list venues: HTTP {resp.status_code} count={len(vl)} "
      f"hit={any(v.get('id') == vid for v in vl)}")

resp = c.put(f"/api/venue/venues/{vid}", headers=M7,
             json={"capacity": 260})
print(f"[B3] update venue: HTTP {resp.status_code} "
      f"capacity={(resp.json().get('data') or {}).get('capacity')}")

resp = c.post("/api/venue/stockings", headers=M7, json={
    "partnerId": pid, "venueId": vid, "productId": f"{MARK}-wine1",
    "productName": "LT测试酒", "quantity": 10,
    "svipPrice": 80.0, "retailPrice": 128.0,
    "supplyMode": "direct", "stockingsDate": "2026-09-18"})
st = resp.json().get("data") or {}
sid = st.get("id")
print(f"[B4] add stocking: HTTP {resp.status_code} id={sid} "
      f"qty={st.get('quantity')} profitDiff={st.get('profitDiff')} "
      f"(=（128-80）×10=480)")

# ============================================================
# C 分级 + 结算 + 统计链
# ============================================================

resp = c.post(f"/api/venue/partners/{pid}/grade", headers=A,
              json={"newLevel": "X", "reason": "LT非法",
                    "operatorId": 1})
print(f"[C1] invalid level: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.post(f"/api/venue/partners/{pid}/grade", headers=A,
              json={"newLevel": "C", "reason": "LT升级",
                    "operatorId": 1})
d = resp.json().get("data") or {}
print(f"[C2] grade C: HTTP {resp.status_code} level={d.get('partnerLevel')} "
      f"tastingRate={d.get('tastingRate')}")

resp = c.post(f"/api/venue/partners/{pid}/settle", headers=A)
d = resp.json().get("data") or {}
print(f"[C3] settle: HTTP {resp.status_code} "
      f"stockings={d.get('stockingsCount')} "
      f"totalProfit={d.get('totalProfitDiff')} "
      f"platform={d.get('platformShare')}(80%) "
      f"partner={d.get('partnerShare')}(20%) "
      f"tastingQty={d.get('tastingQty')}(1%) "
      f"ledger={d.get('ledgerRecorded')}")

resp = c.get("/api/venue/stats", headers=A)
d = resp.json().get("data") or {}
print(f"[C4] stats: HTTP {resp.status_code} "
      f"{json.dumps({k: d[k] for k in list(d)[:6]}, ensure_ascii=False)[:170]}")

# ============================================================
# 负测
# ============================================================

resp = c.post("/api/venue/partners", json={
    "partnerType": "hotel", "partnerName": f"{MARK}无头",
    "creditCode": "LT-N"})
print(f"[N1] no auth: HTTP {resp.status_code} (expect 401)")

# 铺货结算后再 settle(无active铺货, 合作商仍active) → 结算0条
resp = c.post(f"/api/venue/partners/{pid}/settle", headers=A)
d = resp.json().get("data") or {}
print(f"[N2] settle again: HTTP {resp.status_code} "
      f"stockings={d.get('stockingsCount')} (expect 0, 已置offline)")

# 终止后结算 → 409
c.post(f"/api/venue/partners/{pid}/transition", headers=A,
       json={"targetStatus": "terminated", "operatorId": 1,
             "remark": "LT收官"})
resp = c.post(f"/api/venue/partners/{pid}/settle", headers=A)
print(f"[N3] settle terminated: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

# ============================================================
# 清理(差集 + 索引SREM + role总账)
# ============================================================

removed = 0
for k in set(r.keys("zhuxiang:venue:*")) - before:
    if k.endswith(":seq"):
        continue
    ktype = r.type(k)
    if ktype == "string":
        r.delete(k)
        removed += 1
    elif ktype == "set":
        # 我的专属索引键(新合作商的 by_partner/by_venue)
        r.delete(k)
        removed += 1

# 共享 ids 索引 SREM
r.srem("zhuxiang:venue:partner:ids", pid)
r.srem("zhuxiang:venue:venue:ids", vid)
r.srem("zhuxiang:venue:stocking:ids", sid)

# role 总账 VEN 流水
ven_keys = [k for k in r.keys("zhuxiang:role:ledger:VEN-*")
            if f"VEN-{pid}-" in k]
for k in ven_keys:
    r.delete(k)
    removed += 1

residual = [k for k in set(r.keys("zhuxiang:venue:*")) - before
            if not k.endswith(":seq")]
ids_left = (r.smembers("zhuxiang:venue:partner:ids")
            | r.smembers("zhuxiang:venue:venue:ids")
            | r.smembers("zhuxiang:venue:stocking:ids"))
ven_left = r.keys("zhuxiang:role:ledger:VEN-*")
print(f"[clean] removed={removed} residual={residual} "
      f"ids_left={ids_left} ven_ledger_left={ven_left}")
print("VENUE E2E DONE")
