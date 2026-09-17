"""生产 E2E: 合作接口管理模块全链实证(10 端点)

四条链:
    A 申请链(用户端+管理端): POST applications(X-Member-Id,
      金额2万/资质1个/含电话) → my-applications → 管理端列表
      → 公开详情 → review(确定性规则+AI partner_review门
      observe快照, score100过审)
    B 签约链: sign(approved→signed, 保证金阶梯2万×25%=5000,
      建CT协议+激活PT合作方+消费快照反馈回流) → contracts列表
      → 独立建协议(阶梯校验) → terminate终止
    C 合作方链: partners公开列表(含新激活方) → PUT分级
      (bronze→silver)
    D 统计: stats/overview
    负测: 金额<1000→409 / pending直接签约→409 / 保证金低于
      阶梯→409

清理联动面: cooperation域差集 + ai_learning反馈条目LREM
    (application {no} signed / app:{no} 双格式) + drift还原;
    seq三键(partner/application/contract)留痕防id复用.
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
U = 99004
M4 = {"X-Member-Id": str(U)}
A = {"X-Role": "admin"}
MARK = "LT合作E2E"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:cooperation:*"))
before_drift = r.get("zhuxiang:ai_learning:drift:partner_review")
fb_key = "zhuxiang:ai_learning:feedback:partner_review"

print(f"[inv] cooperation keys before: {len(before)}")

# ============================================================
# A 申请链
# ============================================================

resp = c.post("/api/cooperation/applications", headers=M4, json={
    "partnerName": f"{MARK}合作方", "partnerType": "enterprise",
    "type": "new", "businessScope": "LT测试业务范围",
    "estimatedAmount": 20000, "contactName": "LT联系人",
    "contactPhone": "13800000000", "contactEmail": "lt@zxjiu.com",
    "qualificationFiles": ["https://zxjiu.com/lt/qual1.pdf"]})
app = resp.json().get("data") or {}
aid = app.get("id")
ano = app.get("applicationNo")
pid = app.get("partnerId")
print(f"[A1] application create: HTTP {resp.status_code} id={aid} "
      f"no={ano} partnerId={pid} status={app.get('status')}")

resp = c.get("/api/cooperation/my-applications", headers=M4)
mine = resp.json().get("data") or []
print(f"[A2] my-applications: HTTP {resp.status_code} count={len(mine)} "
      f"hit={any(a.get('id') == aid for a in mine)}")

resp = c.get("/api/cooperation/applications", headers=A)
lst = resp.json().get("data") or []
print(f"[A3] admin list: HTTP {resp.status_code} count={len(lst)} "
      f"hit={any(a.get('id') == aid for a in lst)}")

resp = c.get(f"/api/cooperation/applications/{aid}")
d = resp.json().get("data") or {}
print(f"[A4] application detail: HTTP {resp.status_code} "
      f"partnerName={d.get('partnerName')} amount={d.get('estimatedAmount')}")

resp = c.post(f"/api/cooperation/applications/{aid}/sign", headers=A,
              json={"contractTitle": f"{MARK}协议", "depositAmount": 0})
print(f"[A5] sign before review: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.post(f"/api/cooperation/applications/{aid}/review", headers=A)
d = resp.json().get("data") or {}
print(f"[A6] review: HTTP {resp.status_code} result={d.get('result')} "
      f"score={d.get('score')} issues={d.get('issues')} "
      f"status={d.get('status')}")

# ============================================================
# B 签约链
# ============================================================

resp = c.post(f"/api/cooperation/applications/{aid}/sign", headers=A,
              json={"contractTitle": f"{MARK}协议",
                    "startDate": "2026-09-18", "endDate": "2027-09-18",
                    "depositAmount": 0})
d = resp.json().get("data") or {}
cid = d.get("contractId")
print(f"[B1] sign: HTTP {resp.status_code} contractId={cid} "
      f"contractNo={d.get('contractNo')} deposit={d.get('depositAmount')} "
      f"rate={d.get('depositRate')} partnerStatus={d.get('partnerStatus')}")

resp = c.post(f"/api/cooperation/applications/{aid}/sign", headers=A,
              json={"depositAmount": 0})
print(f"[B2] sign again: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.get("/api/cooperation/contracts", headers=A)
cls = resp.json().get("data") or []
print(f"[B3] contracts list: HTTP {resp.status_code} count={len(cls)} "
      f"hit={any(x.get('id') == cid for x in cls)}")

resp = c.post("/api/cooperation/contracts", headers=A, json={
    "partnerId": pid, "title": f"{MARK}独立协议", "amount": 10000,
    "depositAmount": 0})
d2 = resp.json().get("data") or {}
cid2 = d2.get("id")
print(f"[B4] contract create: HTTP {resp.status_code} id={cid2} "
      f"deposit={d2.get('depositAmount')} (1万×30%=3000)")

resp = c.post("/api/cooperation/contracts", headers=A, json={
    "partnerId": pid, "title": f"{MARK}低保证金", "amount": 10000,
    "depositAmount": 100})
print(f"[B5] low deposit: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.post(f"/api/cooperation/contracts/{cid}/terminate", headers=A,
              json={"reason": f"{MARK}终止"})
print(f"[B6] terminate: HTTP {resp.status_code} "
      f"status={(resp.json().get('data') or {}).get('status')}")

# ============================================================
# C 合作方链
# ============================================================

resp = c.get("/api/cooperation/partners")
pts = resp.json().get("data") or []
me = [p for p in pts if p.get("id") == pid]
print(f"[C1] partners list(public): HTTP {resp.status_code} "
      f"count={len(pts)} hit={bool(me)} "
      f"level={me[0].get('level') if me else None}")

resp = c.put(f"/api/cooperation/partners/{pid}", headers=A,
             json={"level": "silver"})
print(f"[C2] partner update: HTTP {resp.status_code} "
      f"level={(resp.json().get('data') or {}).get('level')}")

# ============================================================
# D 统计 + 负测
# ============================================================

resp = c.get("/api/cooperation/stats/overview", headers=A)
d = resp.json().get("data") or {}
print(f"[D1] overview: HTTP {resp.status_code} "
      f"partners={d.get('totalPartners')} "
      f"apps={d.get('totalApplications')} "
      f"contracts={d.get('totalContracts')} "
      f"keys={sorted(d.keys())[:10]}")

resp = c.post("/api/cooperation/applications", headers=M4, json={
    "partnerName": f"{MARK}小额", "businessScope": "LT",
    "estimatedAmount": 100, "contactPhone": "13800000000"})
print(f"[D2] amount below min: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

# ============================================================
# 清理(差集 + AI联动)
# ============================================================

removed = 0
for k in set(r.keys("zhuxiang:cooperation:*")) - before:
    if k.endswith(":seq"):
        continue
    r.delete(k)
    removed += 1

fb_removed = 0
for raw in r.lrange(fb_key, 0, -1):
    if (f"application {ano} " in raw or f"app:{ano}" in raw):
        r.lrem(fb_key, 1, raw)
        fb_removed += 1

if before_drift is not None:
    r.set("zhuxiang:ai_learning:drift:partner_review", before_drift)
else:
    r.delete("zhuxiang:ai_learning:drift:partner_review")

for k in r.keys("zhuxiang:ai_learning:snapshot:partner_review*"):
    if f"app:{ano}" in k:
        r.delete(k)
        removed += 1

residual = [k for k in set(r.keys("zhuxiang:cooperation:*")) - before
            if not k.endswith(":seq")]
fb_left = [x for x in r.lrange(fb_key, 0, -1) if ano in x]
print(f"[clean] removed={removed} residual={residual} "
      f"fb_lrem={fb_removed} fb_left={len(fb_left)} "
      f"drift_restored={before_drift is not None}")
print("COOPERATION E2E DONE")
