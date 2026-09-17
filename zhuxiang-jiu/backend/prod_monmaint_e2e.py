"""生产 E2E: AI智能监控+维护模块全链实证(24 端点)

Monitor 链(12): health → metrics-collect → metrics-list →
  alert-create → alert-list → alert-acknowledge →
  incident-create → incident-list → incident-investigate →
  dashboard-create → dashboard-list → stats
Maintenance 链(12): tasks-create → tasks-list → tasks-execute
  → health-create+执行 → health-list → health-detail →
  recovery-create → recovery-list → optimizations-create →
  optimizations-list → inspect-all(一键巡检) → stats
负测: 无头 403

清理: monitor/maintenance双域差集(跳各seq键)——inspect巡检
自动创建的健康检查记录一并清理; 生产两域此前零数据.
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
A = {"X-Role": "admin"}
MARK = "LT监控E2E"

c = httpx.Client(base_url=BASE, timeout=120)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before_m = set(r.keys("zhuxiang:monitor:*"))
before_t = set(r.keys("zhuxiang:maintenance:*"))
print(f"[inv] monitor={len(before_m)} maintenance={len(before_t)}")

# ============================================================
# Monitor 链
# ============================================================

resp = c.get("/api/monitor/health", headers=A)
print(f"[M1] health: HTTP {resp.status_code} "
      f"{json.dumps(resp.json().get('data') or {}, ensure_ascii=False)[:110]}")

resp = c.post("/api/monitor/metrics", headers=A, json={
    "metricName": f"{MARK}_cpu", "metricType": "system",
    "metricValue": 42.5, "source": "LT-node",
    "metricUnit": "%", "tags": {"env": "lt"},
    "threshold": {"max": 90.0}})
mt = resp.json().get("data") or {}
mid = mt.get("id")
print(f"[M2] metric collect: HTTP {resp.status_code} id={mid} "
      f"name={mt.get('metricName')} value={mt.get('metricValue')}")

resp = c.get("/api/monitor/metrics", headers=A,
             params={"metric_name": f"{MARK}_cpu"})
ml = resp.json().get("data") or []
print(f"[M3] metrics list: HTTP {resp.status_code} count={len(ml)} "
      f"hit={any(m.get('id') == mid for m in ml)}")

resp = c.post("/api/monitor/alerts", headers=A, json={
    "alertName": f"{MARK}CPU告警", "alertType": "resource",
    "alertLevel": "warning", "source": "LT-node",
    "metricId": mid, "threshold": {"max": 90.0},
    "currentValue": 42.5, "description": "LT测试告警"})
al = resp.json().get("data") or {}
aid = al.get("id")
print(f"[M4] alert create: HTTP {resp.status_code} id={aid} "
      f"level={al.get('alertLevel')} status={al.get('alertStatus')}")

resp = c.get("/api/monitor/alerts", headers=A,
             params={"alert_type": "resource"})
alst = resp.json().get("data") or []
print(f"[M5] alerts list: HTTP {resp.status_code} count={len(alst)} "
      f"hit={any(a.get('id') == aid for a in alst)}")

resp = c.put(f"/api/monitor/alerts/{aid}", headers=A,
             json={"action": "acknowledge", "operator": "LT-admin"})
print(f"[M6] alert acknowledge: HTTP {resp.status_code} "
      f"status={(resp.json().get('data') or {}).get('alertStatus')}")

resp = c.post("/api/monitor/incidents", headers=A, json={
    "incidentName": f"{MARK}测试故障", "incidentType": "service",
    "incidentLevel": "P2", "source": "LT-node",
    "impact": {"service": "lt-svc"}, "alertIds": [aid],
    "assignee": "LT-admin"})
inc = resp.json().get("data") or {}
iid = inc.get("id")
print(f"[M7] incident create: HTTP {resp.status_code} id={iid} "
      f"level={inc.get('incidentLevel')} status={inc.get('incidentStatus')}")

resp = c.get("/api/monitor/incidents", headers=A,
             params={"incident_level": "P2"})
il = resp.json().get("data") or []
print(f"[M8] incidents list: HTTP {resp.status_code} count={len(il)} "
      f"hit={any(x.get('id') == iid for x in il)}")

resp = c.put(f"/api/monitor/incidents/{iid}", headers=A,
             json={"action": "investigate", "operator": "LT-admin",
                   "rootCause": "LT根因"})
print(f"[M9] incident investigate: HTTP {resp.status_code} "
      f"status={(resp.json().get('data') or {}).get('incidentStatus')}")

resp = c.post("/api/monitor/dashboards", headers=A, json={
    "dashboardName": f"{MARK}仪表盘", "dashboardType": "custom",
    "owner": "LT-admin", "widgets": [{"type": "chart"}],
    "refreshInterval": 60, "isShared": True})
db = resp.json().get("data") or {}
did = db.get("id")
print(f"[M10] dashboard create: HTTP {resp.status_code} id={did} "
      f"name={db.get('dashboardName')}")

resp = c.get("/api/monitor/dashboards", headers=A,
             params={"owner": "LT-admin"})
dl = resp.json().get("data") or []
print(f"[M11] dashboards list: HTTP {resp.status_code} count={len(dl)} "
      f"hit={any(x.get('id') == did for x in dl)}")

resp = c.get("/api/monitor/stats", headers=A)
d = resp.json().get("data") or {}
print(f"[M12] monitor stats: HTTP {resp.status_code} "
      f"{json.dumps({k: d[k] for k in list(d)[:5]}, ensure_ascii=False)[:140]}")

# ============================================================
# Maintenance 链
# ============================================================

resp = c.post("/api/maintenance/tasks", headers=A, json={
    "taskName": f"{MARK}备份任务", "taskType": "backup",
    "target": "lt-redis", "triggerType": "manual",
    "params": {"scope": "all"}})
tk = resp.json().get("data") or {}
tid = tk.get("id")
print(f"[T1] task create: HTTP {resp.status_code} id={tid} "
      f"type={tk.get('taskType')} status={tk.get('taskStatus')}")

resp = c.get("/api/maintenance/tasks", headers=A,
             params={"task_type": "backup"})
tl = resp.json().get("data") or []
print(f"[T2] tasks list: HTTP {resp.status_code} count={len(tl)} "
      f"hit={any(x.get('id') == tid for x in tl)}")

resp = c.put(f"/api/maintenance/tasks/{tid}", headers=A,
             json={"action": "execute",
                   "result": {"backedUp": True, "size": 1024}})
print(f"[T3] task execute: HTTP {resp.status_code} "
      f"status={(resp.json().get('data') or {}).get('taskStatus')}")

resp = c.post("/api/maintenance/health", headers=A, json={
    "checkName": f"{MARK}检查项", "serviceName": "lt-backend",
    "checkType": "http", "checkConfig": {"url": "/health"},
    "threshold": {"maxResponseMs": 500},
    "healthStatus": "healthy", "responseTime": 42})
hc = resp.json().get("data") or {}
hid = hc.get("id")
print(f"[T4] health create+run: HTTP {resp.status_code} id={hid} "
      f"status={hc.get('healthStatus')} rt={hc.get('responseTime')}ms")

resp = c.get("/api/maintenance/health", headers=A,
             params={"service_name": "lt-backend"})
hl = resp.json().get("data") or []
print(f"[T5] health list: HTTP {resp.status_code} count={len(hl)} "
      f"hit={any(x.get('id') == hid for x in hl)}")

resp = c.get(f"/api/maintenance/health/{hid}", headers=A)
print(f"[T6] health detail: HTTP {resp.status_code} "
      f"name={(resp.json().get('data') or {}).get('checkName')}")

resp = c.post("/api/maintenance/recovery", headers=A, json={
    "faultType": "service_down", "faultSource": "LT-node",
    "faultDescription": "LT测试故障",
    "recoveryLevel": "auto"})
rc = resp.json().get("data") or {}
rid = rc.get("id")
print(f"[T7] recovery create: HTTP {resp.status_code} id={rid} "
      f"level={rc.get('recoveryLevel')} status={rc.get('recoveryStatus')}")

resp = c.get("/api/maintenance/recovery", headers=A,
             params={"fault_type": "service_down"})
rl = resp.json().get("data") or []
print(f"[T8] recovery list: HTTP {resp.status_code} count={len(rl)} "
      f"hit={any(x.get('id') == rid for x in rl)}")

resp = c.post("/api/maintenance/optimizations", headers=A, json={
    "optimizationType": "cache", "target": "lt-redis",
    "proposal": "LT增加缓存", "expectedBenefit": {"latency": "-30%"}})
op = resp.json().get("data") or {}
oid = op.get("id")
print(f"[T9] optimization create: HTTP {resp.status_code} id={oid} "
      f"type={op.get('optimizationType')} status={op.get('optimizationStatus')}")

resp = c.get("/api/maintenance/optimizations", headers=A,
             params={"optimization_type": "cache"})
ol = resp.json().get("data") or []
print(f"[T10] optimizations list: HTTP {resp.status_code} count={len(ol)} "
      f"hit={any(x.get('id') == oid for x in ol)}")

resp = c.post("/api/maintenance/inspect", headers=A)
d = resp.json().get("data") or {}
checks = d.get("checks") or d.get("healthChecks") or []
print(f"[T11] inspect-all: HTTP {resp.status_code} "
      f"total={d.get('totalServices', d.get('total', len(checks)))} "
      f"healthy={d.get('healthyCount', d.get('healthy'))} "
      f"keys={sorted(d.keys())[:8]}")

resp = c.get("/api/maintenance/stats", headers=A)
d = resp.json().get("data") or {}
print(f"[T12] maintenance stats: HTTP {resp.status_code} "
      f"{json.dumps({k: d[k] for k in list(d)[:5]}, ensure_ascii=False)[:140]}")

resp = c.get("/api/monitor/stats")
print(f"[N1] no auth: HTTP {resp.status_code} (expect 403)")

# ============================================================
# 清理(双域差集; seq留痕)
# ============================================================

removed = 0
for k in set(r.keys("zhuxiang:monitor:*")) - before_m:
    if k.endswith(":seq"):
        continue
    r.delete(k)
    removed += 1
for k in set(r.keys("zhuxiang:maintenance:*")) - before_t:
    if k.endswith(":seq"):
        continue
    r.delete(k)
    removed += 1

res_m = [k for k in set(r.keys("zhuxiang:monitor:*")) - before_m
         if not k.endswith(":seq")]
res_t = [k for k in set(r.keys("zhuxiang:maintenance:*")) - before_t
         if not k.endswith(":seq")]
print(f"[clean] removed={removed} residual_monitor={res_m} "
      f"residual_maintenance={res_t}")
print("MONITOR+MAINTENANCE E2E DONE")
