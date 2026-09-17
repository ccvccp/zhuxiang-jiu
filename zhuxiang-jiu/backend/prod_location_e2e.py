"""生产 E2E: 位置地图管理模块全链实证(13 端点)

四条链:
    A 地址管理(HTTP, X-Member-Id): add → list → update →
      set-default → delete
    B 门店/代理商/轨迹/配送(服务层造数 + HTTP 验证——四实体
      无创建路由, 造数仅供查询端点实证): add_store →
      stores/nearby(距离排序) → stores/{id} → add_agent →
      agents(按省筛) → agents/{id} → create_shipment_track →
      logistics/{shipment} → add_delivery_zone → delivery/check
      (范围内命中+AI delivery_zone门快照+即时回流 /
      范围外miss负测)
    C 区块链存证(admin): evidence(白名单6类) → verify
    负测: 无头 401 / 非法存证类型 409

清理联动面: location域差集 + ai_learning反馈条目LREM
    (delivery point {lng}:{lat}) + drift还原; seq六键留痕.
"""
import asyncio
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
U = 99006
M6 = {"X-Member-Id": str(U)}
A = {"X-Role": "admin"}
MARK = "LT位置E2E"
LNG, LAT = 117.0, 36.65  # 济南

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:loc:*"))
before_drift = r.get("zhuxiang:ai_learning:drift:delivery_zone")
fb_key = "zhuxiang:ai_learning:feedback:delivery_zone"

print(f"[inv] loc keys before: {len(before)} "
      f"(实际键前缀为 zhuxiang:loc:*, 服务层模块名缩写)")


async def seed():
    """服务层造数(门店/代理商/轨迹/配送范围)"""
    from services.location_service import LocationService
    svc = LocationService()
    store = await svc.add_store(
        store_name=f"{MARK}门店", store_type="lt_flagship",
        province="山东省", city="济南市", district="历下区",
        address="LT测试路1号", longitude=LNG, latitude=LAT,
        phone="0531-0000000", open_hours="09:00-21:00")
    agent = await svc.add_agent_location(
        agent_id=99006, agent_name=f"{MARK}代理商", agent_level="C",
        province="山东省", city="济南市", address="LT代理商路2号",
        longitude=LNG + 0.01, latitude=LAT + 0.01)
    track = await svc.create_shipment_track(
        shipment_id=f"{MARK}-SHIP-001", order_id=99001,
        carrier="LT快递", tracking_no="LT123456789",
        origin_lng=116.9, origin_lat=36.6,
        dest_lng=LNG, dest_lat=LAT,
        current_lng=116.95, current_lat=36.62,
        current_address="LT中转站", eta="2026-09-19 12:00")
    zone = await svc.add_delivery_zone(
        zone_name=f"{MARK}范围", zone_type="circle",
        center_lng=LNG, center_lat=LAT, radius=10,
        shipping_fee=8.0, free_threshold=99.0,
        delivery_time="24小时")
    return store, agent, track, zone


store, agent, track, zone = asyncio.run(seed())
print(f"[seed] store={store.get('id')} agent={agent.get('id')} "
      f"track={track.get('id')} zone={zone.get('id')}")

# ============================================================
# A 地址管理链
# ============================================================

resp = c.post("/api/location/address/add", headers=M6, json={
    "receiverName": f"{MARK}收件人", "receiverPhone": "13800000000",
    "province": "山东省", "city": "济南市", "district": "历下区",
    "detailAddress": "LT测试地址1号", "longitude": LNG,
    "latitude": LAT, "adcode": "370102", "label": "公司",
    "isDefault": False})
addr = resp.json().get("data") or {}
aid = addr.get("id")
print(f"[A1] address add: HTTP {resp.status_code} id={aid} "
      f"default={addr.get('isDefault')}")

resp = c.get("/api/location/address/list", headers=M6)
lst = resp.json().get("data") or []
print(f"[A2] address list: HTTP {resp.status_code} count={len(lst)} "
      f"hit={any(a.get('id') == aid for a in lst)}")

resp = c.put(f"/api/location/address/{aid}", headers=M6,
             json={"receiverName": f"{MARK}收件人V2"})
print(f"[A3] address update: HTTP {resp.status_code} "
      f"name={(resp.json().get('data') or {}).get('receiverName')}")

resp = c.post(f"/api/location/address/{aid}/default", headers=M6)
print(f"[A4] set-default: HTTP {resp.status_code} "
      f"default={(resp.json().get('data') or {}).get('isDefault')}")

# ============================================================
# B 门店/代理商/轨迹/配送链
# ============================================================

resp = c.get("/api/location/stores/nearby",
             params={"longitude": LNG, "latitude": LAT,
                     "radius_km": 5})
sn = resp.json().get("data") or []
print(f"[B1] stores nearby: HTTP {resp.status_code} count={len(sn)} "
      f"hit={any(s.get('id') == store.get('id') for s in sn)} "
      f"dist={(sn or [{}])[0].get('distanceKm')}")

resp = c.get(f"/api/location/stores/{store.get('id')}")
print(f"[B2] store detail: HTTP {resp.status_code} "
      f"name={(resp.json().get('data') or {}).get('storeName')}")

resp = c.get("/api/location/agents", params={"province": "山东省"})
ag = resp.json().get("data") or []
print(f"[B3] agents by province: HTTP {resp.status_code} "
      f"count={len(ag)} "
      f"hit={any(a.get('id') == agent.get('id') for a in ag)}")

resp = c.get(f"/api/location/agents/{agent.get('id')}")
print(f"[B4] agent detail: HTTP {resp.status_code} "
      f"name={(resp.json().get('data') or {}).get('agentName')}")

resp = c.get(f"/api/location/logistics/{MARK}-SHIP-001")
tk = resp.json().get("data") or {}
print(f"[B5] shipment track: HTTP {resp.status_code} "
      f"carrier={tk.get('carrier')} status={tk.get('status')} "
      f"progress={tk.get('progressPercent')}")

resp = c.post("/api/location/delivery/check", headers=M6,
              json={"longitude": LNG, "latitude": LAT})
d = resp.json().get("data") or {}
mz = d.get("matchedZones") or []
print(f"[B6] delivery check(in): HTTP {resp.status_code} "
      f"inRange={d.get('inDeliveryRange')} "
      f"zone={(mz or [{}])[0].get('zoneName')} "
      f"fee={(mz or [{}])[0].get('shippingFee')}")

resp = c.post("/api/location/delivery/check", headers=M6,
              json={"longitude": 116.0, "latitude": 36.0})
d = resp.json().get("data") or {}
print(f"[B7] delivery check(out): HTTP {resp.status_code} "
      f"inRange={d.get('inDeliveryRange')} (expect False)")

# ============================================================
# C 区块链存证链
# ============================================================

resp = c.post("/api/location/blockchain/evidence", headers=A, json={
    "evidenceType": "location", "evidenceData": f"{MARK}存证"})
d = resp.json().get("data") or {}
ehash = d.get("evidenceHash")
print(f"[C1] evidence: HTTP {resp.status_code} id={d.get('id')} "
      f"hash={ehash}")

resp = c.get("/api/location/blockchain/verify",
             params={"hash": ehash})
print(f"[C2] verify: HTTP {resp.status_code} "
      f"verified={(resp.json().get('data') or {}).get('verified')}")

# ============================================================
# 负测 + 清理
# ============================================================

resp = c.get("/api/location/address/list")
print(f"[N1] no auth: HTTP {resp.status_code} (expect 401)")

resp = c.post("/api/location/blockchain/evidence", headers=A, json={
    "evidenceType": "lt_invalid", "evidenceData": "LT"})
print(f"[N2] invalid evidence type: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.delete(f"/api/location/address/{aid}", headers=M6)
print(f"[N3] address delete: HTTP {resp.status_code} "
      f"deleted={(resp.json().get('data') or {}).get('deleted', 'ok')}")

# 清理: loc域差集(跳seq) + AI联动
removed = 0
for k in set(r.keys("zhuxiang:loc:*")) - before:
    if k.endswith(":seq"):
        continue
    r.delete(k)
    removed += 1

fb_removed = 0
for raw in r.lrange(fb_key, 0, -1):
    if (f"delivery point {LNG}:" in raw
            or "delivery point 116.0:" in raw):
        r.lrem(fb_key, 1, raw)
        fb_removed += 1

if before_drift is not None:
    r.set("zhuxiang:ai_learning:drift:delivery_zone", before_drift)
else:
    r.delete("zhuxiang:ai_learning:drift:delivery_zone")

for k in r.keys("zhuxiang:ai_learning:snapshot:delivery_zone*"):
    if f"point:{LNG}" in k or "point:116.0" in k:
        r.delete(k)
        removed += 1

residual = [k for k in set(r.keys("zhuxiang:loc:*")) - before
            if not k.endswith(":seq")]
fb_left = [x for x in r.lrange(fb_key, 0, -1)
           if f"delivery point {LNG}:" in x
           or "delivery point 116.0:" in x]
print(f"[clean] removed={removed} residual={residual} "
      f"fb_lrem={fb_removed} fb_left={len(fb_left)} "
      f"drift_restored={before_drift is not None}")
print("LOCATION E2E DONE")
