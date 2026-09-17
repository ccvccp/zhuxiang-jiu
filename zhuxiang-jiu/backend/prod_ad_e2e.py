"""生产 E2E: 广告管理模块全链实证(17 端点)

四条链:
    A 广告位链: slots create → list → update(后置 delete)
    B 广告主链: create(draft) → list(公开, 修复后含seq不崩) →
      detail → update → review(合规通过 score100) → online
      (建投放+AI投放门快照) → impression/click/conversion
      (效果三录) → stats(CVR/ROI) → placements/list(全局路径,
      修复后含seq不崩) → offline(消费快照+反馈回流) →
      stats/overview
    C 负测: 未审核直接上线 409(状态机) + 违规广告 review 驳回
    D 广告位删除收尾

清理联动面: ad域差集(含hash统计/placement列表/review列表) +
    slots hash字段HDEL + ai_learning反馈条目LREM(adonline:{id}
    匹配) + drift还原; 各seq键留痕防id复用.
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
M1 = {"X-Member-Id": "1"}
A = {"X-Role": "admin"}
MARK = "LT广告E2E"
SLOT = "LT_SLOT_E2E"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:ad:*"))
before_drift = r.get("zhuxiang:ai_learning:drift:ad_placement")
fb_key = "zhuxiang:ai_learning:feedback:ad_placement"

print(f"[inv] ad keys before: {len(before)}")

# ============================================================
# A 广告位链
# ============================================================

resp = c.post("/api/ads/slots", headers=A, json={
    "slotCode": SLOT, "name": f"{MARK}广告位", "position": "首页顶部",
    "size": "1920x600", "supportedTypes": ["IMAGE", "BANNER"],
    "dailyEstimateImpressions": 10000})
d = resp.json().get("data") or {}
print(f"[A1] slot create: HTTP {resp.status_code} code={d.get('slotCode')} "
      f"status={d.get('status')}")

resp = c.get("/api/ads/slots")
slots = resp.json().get("data") or []
print(f"[A2] slot list: HTTP {resp.status_code} count={len(slots)} "
      f"hit={any(s.get('slotCode') == SLOT for s in slots)}")

resp = c.put(f"/api/ads/slots/{SLOT}", headers=A,
             json={"name": f"{MARK}广告位V2"})
print(f"[A3] slot update: HTTP {resp.status_code} "
      f"name={(resp.json().get('data') or {}).get('name')}")

# ============================================================
# B 广告主链
# ============================================================

resp = c.post("/api/ads", headers=A, json={
    "advertiserName": f"{MARK}广告主", "name": f"{MARK}banner",
    "type": "BANNER", "position": SLOT,
    "title": f"{MARK}竹香佳酿 广告 过量饮酒有害健康",
    "description": "LT测试广告描述 过量饮酒有害健康",
    "targetUrl": "https://zxjiu.com/lt", "budget": 1000,
    "dailyBudget": 100})
ad = resp.json().get("data") or {}
aid = ad.get("id")
print(f"[B1] ad create: HTTP {resp.status_code} id={aid} "
      f"no={ad.get('adNo')} status={ad.get('status')}")

resp = c.get("/api/ads")
lst = resp.json().get("data") or []
print(f"[B2] ad list(public): HTTP {resp.status_code} count={len(lst)} "
      f"hit={any(a.get('id') == aid for a in lst)}")

resp = c.get(f"/api/ads/{aid}")
print(f"[B3] ad detail: HTTP {resp.status_code} "
      f"title={(resp.json().get('data') or {}).get('title')[:30]}")

resp = c.put(f"/api/ads/{aid}", headers=A,
             json={"description": f"{MARK}更新描述 过量饮酒有害健康"})
print(f"[B4] ad update: HTTP {resp.status_code} "
      f"status={(resp.json().get('data') or {}).get('status')}")

resp = c.post(f"/api/ads/{aid}/online", headers=A)
print(f"[B5] online before approved: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.post(f"/api/ads/{aid}/review", headers=A)
d = resp.json().get("data") or {}
print(f"[B6] review: HTTP {resp.status_code} result={d.get('result')} "
      f"score={d.get('score')} issues={d.get('issues')} "
      f"status={d.get('status')}")

resp = c.post(f"/api/ads/{aid}/online", headers=A)
d = resp.json().get("data") or {}
pid = d.get("placementId")
print(f"[B7] online: HTTP {resp.status_code} placementId={pid} "
      f"placementNo={d.get('placementNo')} status={d.get('status')}")

resp = c.post(f"/api/ads/{aid}/impression", headers=M1,
              json={"count": 100})
print(f"[B8] impression x100: HTTP {resp.status_code} "
      f"{json.dumps(resp.json().get('data') or {}, ensure_ascii=False)[:80]}")

resp = c.post(f"/api/ads/{aid}/click", headers=M1, json={"count": 5})
print(f"[B9] click x5: HTTP {resp.status_code}")

resp = c.post(f"/api/ads/{aid}/conversion", headers=M1,
              json={"count": 2, "revenue": 999.0})
print(f"[B10] conversion x2: HTTP {resp.status_code}")

resp = c.get(f"/api/ads/{aid}/stats", headers=A)
d = resp.json().get("data") or {}
print(f"[B11] ad stats: HTTP {resp.status_code} "
      f"impressions={d.get('impressions')} clicks={d.get('clicks')} "
      f"conversions={d.get('conversions')} revenue={d.get('revenue')} "
      f"ctr={d.get('ctr')} cvr={d.get('cvr')}")

resp = c.get("/api/ads/placements/list", headers=A)
pl = resp.json().get("data") or []
print(f"[B12] placements list: HTTP {resp.status_code} count={len(pl)} "
      f"hit={any(p.get('id') == pid for p in pl)}")

resp = c.post(f"/api/ads/{aid}/offline", headers=A,
              json={"reason": f"{MARK}下线"})
print(f"[B13] offline: HTTP {resp.status_code} "
      f"status={(resp.json().get('data') or {}).get('status')}")

resp = c.get("/api/ads/stats/overview", headers=A)
d = resp.json().get("data") or {}
print(f"[B14] overview: HTTP {resp.status_code} "
      f"total={d.get('totalAds')} online={d.get('onlineAds')} "
      f"slots={d.get('totalSlots')} "
      f"impressions={d.get('totalImpressions')}")

# ============================================================
# C 负测: 违规广告 review 驳回
# ============================================================

resp = c.post("/api/ads", headers=A, json={
    "advertiserName": f"{MARK}广告主", "name": f"{MARK}违规",
    "type": "IMAGE", "position": SLOT,
    "title": f"{MARK}千杯不醉神酒", "description": "LT违规描述"})
bad_id = (resp.json().get("data") or {}).get("id")
resp = c.post(f"/api/ads/{bad_id}/review", headers=A)
d = resp.json().get("data") or {}
print(f"[C1] bad ad review: HTTP {resp.status_code} "
      f"result={d.get('result')} score={d.get('score')} "
      f"issues={[i.get('type') for i in d.get('issues') or []]}")

resp = c.post(f"/api/ads/{bad_id}/online", headers=A)
print(f"[C2] online rejected ad: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

# ============================================================
# D 广告位删除
# ============================================================

resp = c.delete(f"/api/ads/slots/{SLOT}", headers=A)
print(f"[D1] slot delete: HTTP {resp.status_code}")

# ============================================================
# 清理(差集 + hash字段 + AI联动)
# ============================================================

removed = 0
for k in set(r.keys("zhuxiang:ad:*")) - before:
    if k.endswith(":seq"):
        continue
    if r.type(k) == "hash" and k.endswith(f"stats:{aid}"):
        r.delete(k)
        removed += 1
        continue
    if r.type(k) == "string":
        r.delete(k)
        removed += 1
    elif r.type(k) == "list":
        r.delete(k)
        removed += 1

# slots hash 字段(已 DELETE 端点清理, 防御性复查)
if r.hexists("zhuxiang:ad:slots", SLOT):
    r.hdel("zhuxiang:ad:slots", SLOT)
    removed += 1

# ai_learning 反馈条目 LREM(on_ad_offline note 格式为
# "ad {id} offline({reason})"; 兼容 adonline:{id} 决策键格式)
fb_removed = 0
for raw in r.lrange(fb_key, 0, -1):
    if (f"ad {aid} offline" in raw or f"adonline:{aid}" in raw):
        r.lrem(fb_key, 1, raw)
        fb_removed += 1

# drift 还原
if before_drift is not None:
    r.set("zhuxiang:ai_learning:drift:ad_placement", before_drift)
else:
    r.delete("zhuxiang:ai_learning:drift:ad_placement")

# 快照残留防御删除(正常已被 offline 消费)
for k in r.keys("zhuxiang:ai_learning:snapshot:ad_placement*"):
    if f"adonline:{aid}" in k:
        r.delete(k)
        removed += 1

residual = [k for k in set(r.keys("zhuxiang:ad:*")) - before
            if not k.endswith(":seq")]
slot_left = r.hexists("zhuxiang:ad:slots", SLOT)
fb_left = [x for x in r.lrange(fb_key, 0, -1) if f"adonline:{aid}" in x]
print(f"[clean] removed={removed} residual={residual} "
      f"slot_left={slot_left} fb_lrem={fb_removed} "
      f"fb_left={len(fb_left)} drift_restored={before_drift is not None}")
print("AD E2E DONE")
