"""生产 E2E: AI智能客服模块全链实证(22 端点)

六条链:
    A 会话+AI消息: create → my-sessions → 知识命中消息(RAG/legacy双轨)
      → 未命中消息(fallback+缺口记录) → 消息全量/增量查询 → read → detail
    B 转人工+客服链: transfer(建 source=ai 工单+调度派单) → cs/queue
      → accept(幂等) → cs/reply
    C 终态链: satisfaction(5分) → close
    D 知识库CRUD: create → list → update → delete
    E 管理面: admin/sessions(索引修复后全量可见) → stats
    F 灰度链: mode GET → guard 健康(不降档) → guard 恶化(>3% 自动
      guard_pause) → mode 复核(off/guard_pause) → resume(回 env)

清理联动面: chat 域差集 + session_index SREM + knowledge_list LREM
    + knowledge 缺口记录(值匹配) + role:dispatch 差集 + ticket 差集
    + mode:state 快照还原; 各 seq 键留痕防 id 复用。
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
U = 99003
M3 = {"X-Member-Id": str(U)}
A = {"X-Role": "admin"}
MARK = "ltmq"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before_chat = set(r.keys("zhuxiang:chat:*"))
before_ticket = set(r.keys("zhuxiang:ticket:*"))
before_dispatch = set(r.keys("zhuxiang:role:dispatch:*"))
before_gap = set(r.keys("zhuxiang:knowledge:gap*"))
before_state = r.get("zhuxiang:chat:mode:state")

print(f"[inv] chat={len(before_chat)} ticket={len(before_ticket)} "
      f"dispatch={len(before_dispatch)} gap={len(before_gap)}")

# ============================================================
# A 会话 + AI 消息链
# ============================================================

resp = c.post("/api/chat/sessions", headers=M3, json={
    "userId": U, "sessionType": "presale"})
d = resp.json().get("data") or {}
sid = d.get("sessionId")
print(f"[A1] create session: HTTP {resp.status_code} sid={sid} "
      f"status={d.get('status')}")

resp = c.get("/api/chat/my-sessions", headers=M3)
lst = resp.json().get("data") or []
print(f"[A2] my-sessions: HTTP {resp.status_code} count={len(lst)} "
      f"hit={any(s.get('sessionId') == sid for s in lst)}")

resp = c.post(f"/api/chat/sessions/{sid}/messages", headers=M3, json={
    "senderType": "user", "senderId": U, "messageType": "text",
    "content": f"{MARK}茅台酒价格多少钱一瓶"})
d = resp.json().get("data") or {}
ai = d.get("aiReply") or {}
print(f"[A3] send msg(hit): HTTP {resp.status_code} "
      f"aiConfidence={ai.get('aiConfidence')} "
      f"ragMode={ai.get('ragMode')} knowledgeId={ai.get('knowledgeId')} "
      f"len={len(ai.get('content') or '')}")

resp = c.post(f"/api/chat/sessions/{sid}/messages", headers=M3, json={
    "senderType": "user", "senderId": U, "messageType": "text",
    "content": f"{MARK}完全无关的奇怪问题zzz"})
d = resp.json().get("data") or {}
ai2 = d.get("aiReply") or {}
print(f"[A4] send msg(miss): HTTP {resp.status_code} "
      f"fallback={ai2.get('fallback')} transferred={d.get('transferred')}")

resp = c.get(f"/api/chat/sessions/{sid}/messages", headers=M3)
msgs = resp.json().get("data") or []
first_mid = (msgs[0] or {}).get("id") if msgs else 0
print(f"[A5] messages full: HTTP {resp.status_code} count={len(msgs)} "
      f"senders={[m.get('senderType') for m in msgs]}")

resp = c.get(f"/api/chat/sessions/{sid}/messages",
             params={"since_message_id": first_mid}, headers=M3)
inc = resp.json().get("data") or []
print(f"[A6] messages incremental: HTTP {resp.status_code} "
      f"since={first_mid} count={len(inc)}")

resp = c.post(f"/api/chat/sessions/{sid}/read", headers=M3)
print(f"[A7] mark read: HTTP {resp.status_code} "
      f"{json.dumps(resp.json().get('data') or {}, ensure_ascii=False)[:80]}")

resp = c.get(f"/api/chat/sessions/{sid}", headers=M3)
d = resp.json().get("data") or {}
print(f"[A8] session detail: HTTP {resp.status_code} "
      f"status={d.get('status')} unresolved={d.get('unresolvedCount')}")

# ============================================================
# B 转人工 + 客服链
# ============================================================

resp = c.post(f"/api/chat/sessions/{sid}/transfer", headers=M3,
              json={"reason": f"{MARK}E2E转人工"})
d = resp.json().get("data") or {}
print(f"[B1] transfer: HTTP {resp.status_code} status={d.get('status')} "
      f"ticketNo={d.get('ticketNo')} cs={d.get('customerServiceId')}")

resp = c.get("/api/chat/cs/queue", headers=A,
             params={"status": "human_chatting"})
q = resp.json().get("data") or []
print(f"[B2] cs/queue: HTTP {resp.status_code} count={len(q)} "
      f"hit={any(s.get('sessionId') == sid for s in q)}")

resp = c.post(f"/api/chat/cs/sessions/{sid}/accept", headers=A)
d = resp.json().get("data") or {}
print(f"[B3] cs accept: HTTP {resp.status_code} "
      f"cs={d.get('customerServiceId')} already={d.get('alreadyAssigned')}")

resp = c.post(f"/api/chat/cs/sessions/{sid}/reply", headers=A, json={
    "customerServiceId": 1, "content": f"{MARK}客服回复测试"})
d = resp.json().get("data") or {}
print(f"[B4] cs reply: HTTP {resp.status_code} "
      f"msgId={d.get('messageId')}")

# ============================================================
# C 终态链(关闭 → 评价; 评价须会话已关闭)
# ============================================================

resp = c.post(f"/api/chat/sessions/{sid}/close", headers=M3)
print(f"[C1] close: HTTP {resp.status_code} "
      f"status={(resp.json().get('data') or {}).get('status')}")

resp = c.post(f"/api/chat/sessions/{sid}/satisfaction", headers=M3,
              json={"satisfaction": 5})
d = resp.json().get("data") or {}
print(f"[C2] satisfaction: HTTP {resp.status_code} score={d.get('satisfaction')}")

# ============================================================
# D 知识库 CRUD 链
# ============================================================

resp = c.post("/api/chat/knowledge", headers=A, json={
    "category": "faq", "question": f"{MARK}茅台酒价格多少钱一瓶",
    "answer": f"{MARK}答: 建议零售价请以商品页为准",
    "keywords": f"{MARK} 茅台 价格"})
kid = (resp.json().get("data") or {}).get("id")
print(f"[D1] knowledge create: HTTP {resp.status_code} id={kid}")

resp = c.get("/api/chat/knowledge", params={"category": "faq"})
kl = resp.json().get("data") or []
print(f"[D2] knowledge list: HTTP {resp.status_code} count={len(kl)} "
      f"hit={any(k.get('id') == kid for k in kl)}")

resp = c.put(f"/api/chat/knowledge/{kid}", headers=A,
             json={"answer": f"{MARK}答: 已更新"})
print(f"[D3] knowledge update: HTTP {resp.status_code} "
      f"answer={(resp.json().get('data') or {}).get('answer')}")

resp = c.delete(f"/api/chat/knowledge/{kid}", headers=A)
print(f"[D4] knowledge delete: HTTP {resp.status_code}")

# ============================================================
# E 管理面
# ============================================================

resp = c.get("/api/chat/admin/sessions", headers=A)
print(f"[E1] admin/sessions: HTTP {resp.status_code} "
      f"count={resp.json().get('count')}")

resp = c.get("/api/chat/stats", headers=A)
d = resp.json().get("data") or {}
print(f"[E2] stats: HTTP {resp.status_code} total={d.get('totalSessions')} "
      f"keys={sorted(d.keys())[:8]}")

# ============================================================
# F 灰度链(护栏 pause/resume 全周期)
# ============================================================

resp = c.get("/api/chat/mode", headers=A)
d = resp.json().get("data") or {}
mode_before = d.get("mode")
print(f"[F1] mode: mode={d.get('mode')} source={d.get('source')} "
      f"paused={d.get('paused')}")

resp = c.post("/api/chat/mode/guard", headers=A, json={
    "complaintRate": 0.01, "unresolvedRate": 0.02,
    "baselineComplaintRate": 0.01, "baselineUnresolvedRate": 0.02})
d = resp.json().get("data") or {}
print(f"[F2] guard healthy: HTTP {resp.status_code} "
      f"breached={d.get('breached')} pausedNow={d.get('pausedNow')}")

resp = c.post("/api/chat/mode/guard", headers=A, json={
    "complaintRate": 0.10, "unresolvedRate": 0.02,
    "baselineComplaintRate": 0.05, "baselineUnresolvedRate": 0.02})
d = resp.json().get("data") or {}
print(f"[F3] guard breach: HTTP {resp.status_code} "
      f"breached={d.get('breached')} pausedNow={d.get('pausedNow')} "
      f"breaches={[(b.get('metric'), b.get('deterioration')) for b in d.get('breaches') or []]}")

resp = c.get("/api/chat/mode", headers=A)
d = resp.json().get("data") or {}
print(f"[F4] mode after breach: mode={d.get('mode')} "
      f"source={d.get('source')} reason={d.get('pausedReason', '')[:60]}")

resp = c.post("/api/chat/mode/resume", headers=A)
d = resp.json().get("data") or {}
print(f"[F5] resume: HTTP {resp.status_code} mode={d.get('mode')} "
      f"source={d.get('source')}")

# ============================================================
# 清理(四域差集 + 索引回补 + 状态还原)
# ============================================================

removed = 0
for k in set(r.keys("zhuxiang:chat:*")) - before_chat:
    if k.endswith(":seq"):
        continue
    if k == "zhuxiang:chat:mode:state":
        continue  # 状态单独还原
    if k.endswith(f"sessions_by_user:{U}"):
        r.delete(k)
        removed += 1
        continue
    if "messages_by_session" in k and sid in k:
        r.delete(k)
        removed += 1
        continue
    if r.type(k) == "string":
        v = r.get(k) or ""
        # 消息 JSON 均含 sessionId; 用户消息另含 MARK/senderId
        if sid in k or sid in v or MARK in v or str(U) in v:
            r.delete(k)
            removed += 1

# session_index SREM 我的会话
if sid:
    r.srem("zhuxiang:chat:session_index", sid)

# knowledge_list LREM(已 DELETE 端点清理, 防御性复查)
for kid_raw in r.lrange("zhuxiang:chat:knowledge_list", 0, -1):
    v = r.get(f"zhuxiang:chat:knowledge:{kid_raw}") or ""
    if MARK in v:
        r.lrem("zhuxiang:chat:knowledge_list", 0, int(kid_raw))
        r.delete(f"zhuxiang:chat:knowledge:{kid_raw}")
        removed += 1

# ticket 差集(我的 GD 工单)
for k in set(r.keys("zhuxiang:ticket:*")) - before_ticket:
    if k.endswith(":seq"):
        continue
    r.delete(k)
    removed += 1

# role:dispatch 差集(我的派单)
for k in set(r.keys("zhuxiang:role:dispatch:*")) - before_dispatch:
    if k.endswith(":seq"):
        continue
    r.delete(k)
    removed += 1

# knowledge 缺口记录(值匹配 + 索引 SREM)
for k in set(r.keys("zhuxiang:knowledge:gap*")) - before_gap:
    if k.endswith(":seq"):
        continue
    if r.type(k) == "string":
        v = r.get(k) or ""
        if MARK in v:
            gid = json.loads(v).get("id")
            r.delete(k)
            r.srem("zhuxiang:knowledge:gap:index", gid)
            r.srem("zhuxiang:knowledge:gap:index:open", gid)
            removed += 1

# mode 状态快照还原
if before_state is not None:
    r.set("zhuxiang:chat:mode:state", before_state)
else:
    r.delete("zhuxiang:chat:mode:state")

res_chat = [k for k in set(r.keys("zhuxiang:chat:*")) - before_chat
            if not k.endswith(":seq")]
res_ticket = [k for k in set(r.keys("zhuxiang:ticket:*")) - before_ticket
              if not k.endswith(":seq")]
res_dispatch = [k for k in set(r.keys("zhuxiang:role:dispatch:*")) - before_dispatch
                if not k.endswith(":seq")]
res_gap = [k for k in set(r.keys("zhuxiang:knowledge:gap*")) - before_gap
           if not k.endswith(":seq")]
print(f"[clean] removed={removed} residual chat={res_chat} "
      f"ticket={res_ticket} dispatch={res_dispatch} gap={res_gap} "
      f"state_restored={before_state is not None}")
print("CHAT E2E DONE")
