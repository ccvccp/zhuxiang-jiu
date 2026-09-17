"""生产 E2E: 信息管理模块全链实证(16 端点)

四条链:
    A 消息链(必需类): admin send → list → detail → mark-read(含409重复)
      → mark-all-read → stats → IDOR 负测(查他人 403)
    B 防骚扰门(确定性护栏): subscription GET(默认) → PUT 全天静默窗
      → 营销 send 409 → PUT 关静默 → 营销 send 200 → unsubscribe-all
      → 营销 send 409(已退订)
    C 模板链: create(draft) → get → list → update → delete
    D 群发链: batch-send(2用户) → push-logs 查询

清理联动面: ai_learning 反馈条目 LREM(note 含 msg:{testUid}) +
    drift 还原 + 快照残留防御性删除(正常已被 on_message_settled
    消费自删); seq 键留痕防 id 复用。
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
U1, U2 = 99001, 99002
M1 = {"X-Member-Id": str(U1)}
A = {"X-Role": "admin"}
MARK = "LT消息E2E"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:message*"))
before_drift = r.get("zhuxiang:ai_learning:drift:message_content")
fb_key = "zhuxiang:ai_learning:feedback:message_content"

print(f"[inv] message keys before: {len(before)}")
for k in sorted(before)[:10]:
    print(f"      {k}")

# ============================================================
# A 消息链(必需类 system, 不受防骚扰门)
# ============================================================

resp = c.post("/api/message/send", headers=A, json={
    "userId": U1, "channel": "inmail", "title": f"{MARK}站内信",
    "content": "订单已发货测试", "category": "system"})
d = resp.json().get("data") or {}
mid = d.get("id")
print(f"[A1] admin send: HTTP {resp.status_code} id={mid} "
      f"pushLogId={d.get('pushLogId')} status={d.get('status')}")

resp = c.get("/api/message/list", params={"user_id": U1}, headers=M1)
lst = resp.json().get("data") or []
print(f"[A2] list(self): HTTP {resp.status_code} count={len(lst)} "
      f"hit={any(m.get('id') == mid for m in lst)}")

resp = c.get(f"/api/message/{mid}", headers=M1)
d = resp.json().get("data") or {}
print(f"[A3] detail: HTTP {resp.status_code} title={d.get('title')} "
      f"userId={d.get('userId')}")

resp = c.post(f"/api/message/mark-read/{mid}", headers=M1)
print(f"[A4] mark-read: HTTP {resp.status_code}")
resp = c.post(f"/api/message/mark-read/{mid}", headers=M1)
print(f"[A4b] mark-read dup: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error') or resp.json().get('detail')}")

c.post("/api/message/send", headers=A, json={
    "userId": U1, "channel": "popup", "title": f"{MARK}弹窗",
    "content": "第二条未读", "category": "system"})
resp = c.post("/api/message/mark-all-read", headers=M1,
             json={"userId": U1})
print(f"[A5] mark-all-read: HTTP {resp.status_code} "
      f"{json.dumps(resp.json().get('data') or {}, ensure_ascii=False)[:100]}")

resp = c.get("/api/message/stats", headers=M1)
d = resp.json().get("data") or {}
print(f"[A6] stats(self): HTTP {resp.status_code} "
      f"total={d.get('totalMessages')} unread={d.get('unreadCount')} "
      f"read={d.get('readCount')}")

resp = c.get("/api/message/list", params={"user_id": U2}, headers=M1)
print(f"[A7] IDOR negative: HTTP {resp.status_code} (expect 403)")

# ============================================================
# B 防骚扰门(确定性护栏)
# ============================================================

resp = c.get("/api/message/subscription", headers=M1)
d = resp.json().get("data") or {}
print(f"[B1] subscription GET: HTTP {resp.status_code} "
      f"silent={d.get('silentStart')}-{d.get('silentEnd')} "
      f"enabled={d.get('silentEnabled')} daily={d.get('dailyLimit')}")

resp = c.put("/api/message/subscription", headers=M1,
             json={"silentStart": "00:00", "silentEnd": "23:59",
                   "silentEnabled": True})
print(f"[B2] PUT all-day silent: HTTP {resp.status_code}")

resp = c.post("/api/message/send", headers=A, json={
    "userId": U1, "channel": "inmail", "title": f"{MARK}营销",
    "content": "活动邀约", "category": "activity"})
print(f"[B3] marketing send in silent: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.put("/api/message/subscription", headers=M1,
             json={"silentEnabled": False})
print(f"[B4] PUT silent off: HTTP {resp.status_code}")

resp = c.post("/api/message/send", headers=A, json={
    "userId": U1, "channel": "inmail", "title": f"{MARK}营销2",
    "content": "活动邀约", "category": "activity"})
d = resp.json().get("data") or {}
print(f"[B5] marketing send silent-off: HTTP {resp.status_code} "
      f"id={d.get('id')}")

resp = c.post("/api/message/subscription/unsubscribe-all", headers=M1)
d = resp.json().get("data") or {}
print(f"[B6] unsubscribe-all: HTTP {resp.status_code} "
      f"cats={d.get('categories')}")

resp = c.post("/api/message/send", headers=A, json={
    "userId": U1, "channel": "inmail", "title": f"{MARK}营销3",
    "content": "活动邀约", "category": "activity"})
print(f"[B7] marketing send unsubscribed: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

# ============================================================
# C 模板链
# ============================================================

resp = c.post("/api/message/admin/template/create", headers=A, json={
    "name": f"{MARK}模板", "category": "system", "channel": "inmail",
    "title": "测试模板标题", "content": "测试模板内容 ${nickname}"})
d = resp.json().get("data") or {}
tid = d.get("id")
print(f"[C1] template create: HTTP {resp.status_code} id={tid} "
      f"no={d.get('templateNo')} status={d.get('status')}")

resp = c.get(f"/api/message/admin/template/{tid}", headers=A)
print(f"[C2] template get: HTTP {resp.status_code} "
      f"name={(resp.json().get('data') or {}).get('name')}")

resp = c.get("/api/message/admin/template/list", headers=A)
tl = resp.json().get("data") or []
print(f"[C3] template list: HTTP {resp.status_code} count={len(tl)} "
      f"hit={any(t.get('id') == tid for t in tl)}")

resp = c.put(f"/api/message/admin/template/{tid}", headers=A,
             json={"name": f"{MARK}模板V2"})
print(f"[C4] template update: HTTP {resp.status_code} "
      f"name={(resp.json().get('data') or {}).get('name')}")

resp = c.delete(f"/api/message/admin/template/{tid}", headers=A)
print(f"[C5] template delete: HTTP {resp.status_code}")

# ============================================================
# D 群发链
# ============================================================

resp = c.post("/api/message/admin/batch-send", headers=A, json={
    "userIds": [U1, U2], "channel": "inmail",
    "title": f"{MARK}群发", "content": "群发内容", "category": "system"})
d = resp.json().get("data") or {}
print(f"[D1] batch-send: HTTP {resp.status_code} "
      f"total={d.get('totalCount')} success={d.get('successCount')} "
      f"failed={d.get('failedCount')}")

resp = c.get("/api/message/admin/push-logs",
             params={"user_id": U1}, headers=A)
pl = resp.json().get("data") or []
print(f"[D2] push-logs: HTTP {resp.status_code} count={len(pl)} "
      f"first_task={(pl or [{}])[0].get('taskId')}")

resp = c.get("/api/message/list", params={"user_id": U2},
             headers={"X-Member-Id": str(U2)})
print(f"[D3] list(U2 self): HTTP {resp.status_code} "
      f"count={resp.json().get('count')}")

# ============================================================
# 清理(差集; seq 留痕; AI 联动面)
# ============================================================

removed = 0
for k in set(r.keys("zhuxiang:message*")) - before:
    if k.endswith(":seq"):
        continue
    r.delete(k)
    removed += 1

# ai_learning 反馈条目 LREM(on_message_settled note 格式为
# "message {userId}:{ts} sent"; 同时兼容 traffic 式 msg: 前缀)
fb_removed = 0
for raw in r.lrange(fb_key, 0, -1):
    if (f"message {U1}:" in raw or f"message {U2}:" in raw
            or f"msg:{U1}:" in raw or f"msg:{U2}:" in raw):
        r.lrem(fb_key, 1, raw)
        fb_removed += 1

# drift 还原
if before_drift is not None:
    r.set("zhuxiang:ai_learning:drift:message_content", before_drift)
else:
    r.delete("zhuxiang:ai_learning:drift:message_content")

# 快照残留防御性删除(正常已被消费)
snap_left = [k for k in r.keys(
    "zhuxiang:ai_learning:snapshot:message_content*")
    if f"msg:{U1}:" in k or f"msg:{U2}:" in k]
for k in snap_left:
    r.delete(k)
    removed += 1

residual = [k for k in set(r.keys("zhuxiang:message*")) - before
            if not k.endswith(":seq")]
fb_left = [x for x in r.lrange(fb_key, 0, -1)
           if f"msg:{U1}:" in x or f"msg:{U2}:" in x]
print(f"[clean] removed={removed} residual={residual} "
      f"fb_lrem={fb_removed} fb_left={len(fb_left)} "
      f"drift_restored={before_drift is not None}")
print("MESSAGE E2E DONE")
