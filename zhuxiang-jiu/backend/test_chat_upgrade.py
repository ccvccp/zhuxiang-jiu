"""AI智能聊天及人工聊天模块 · 全域升级专项测试(P0-P4)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_chat_upgrade.py

覆盖(约 34 断言):
    - P0 越权加固: 无头 401 / 他人会话 403 / 自己会话 200 /
      admin 任意 200 / 不存在 404 / 发消息与已读端点同口径
    - P1 实时: since_message_id 增量(仅新消息) / 全量兼容 /
      read 端点标记 AI 消息已读
    - P2 客服工作台: 非 admin 403 / 排队列表 / 接入(分配+系统消息) /
      重复接入幂等 / 回复落库 / 未接入回复 409 / 敏感词 409
    - P3 三态灰度: 默认 off / override 切档 / shadow 留痕(rule 答案
      呈现 + LLM 答案 shadowLlm 字段) / assist LLM 轨 / 护栏恶化
      自动降档 / resume 人工恢复 / 旧开关兼容映射
    - P4: 转人工调度异常留痕(dispatchError)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from fastapi.testclient import TestClient

from main import app
from repositories.store import reset_store
from services.chat_service import ChatService
from services.chat_mode_service import ChatModeService
from repositories.chat_repository import (
    SESSION_STATUS_HUMAN, SESSION_STATUS_AI, SESSION_STATUS_ENDED,
)

PASS = 0
FAIL = 0
RESULTS = []

USER_A = 1001
USER_B = 1002


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} — {detail}")


def hdr(member=None, admin=False):
    h = {}
    if member is not None:
        h["X-Member-Id"] = str(member)
    if admin:
        h["X-Role"] = "admin"
    return h


async def seed_session(client, user_id=USER_A, stype="presale"):
    """建会话(AI 接待态)"""
    resp = client.post("/api/chat/sessions",
                       json={"userId": user_id, "sessionType": stype,
                             "ageConfirmed": True},
                       headers=hdr(member=user_id))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def seed_human_session(client, user_id=USER_A):
    """建会话并转人工(human_chatting 未分配)"""
    session = await seed_session(client, user_id)
    resp = client.post(f"/api/chat/sessions/{session['sessionId']}/transfer",
                       json={"reason": "测试转人工"},
                       headers=hdr(member=user_id))
    assert resp.status_code == 200, resp.text
    return session["sessionId"]


# ============================================================
# P0 越权加固
# ============================================================

async def test_p0_authorization(client):
    print("[P0 越权加固]")
    session = await seed_session(client, USER_A)
    sid = session["sessionId"]

    # 1. 无头 401
    r = client.get(f"/api/chat/sessions/{sid}")
    check("P0-无头401", r.status_code == 401, f"got {r.status_code}")

    # 2. 他人会话 403
    r = client.get(f"/api/chat/sessions/{sid}", headers=hdr(member=USER_B))
    check("P0-他人会话403", r.status_code == 403, f"got {r.status_code}")

    # 3. 自己会话 200
    r = client.get(f"/api/chat/sessions/{sid}", headers=hdr(member=USER_A))
    check("P0-自己会话200", r.status_code == 200, f"got {r.status_code}")

    # 4. admin 任意会话 200
    r = client.get(f"/api/chat/sessions/{sid}", headers=hdr(admin=True))
    check("P0-admin任意200", r.status_code == 200, f"got {r.status_code}")

    # 5. 不存在 404
    r = client.get("/api/chat/sessions/CS_NOT_EXIST", headers=hdr(member=USER_A))
    check("P0-不存在404", r.status_code == 404, f"got {r.status_code}")

    # 6. 他人发消息 403
    r = client.post(f"/api/chat/sessions/{sid}/messages",
                    json={"senderType": "user", "senderId": USER_B,
                          "messageType": "text", "content": "hi"},
                    headers=hdr(member=USER_B))
    check("P0-他人发消息403", r.status_code == 403, f"got {r.status_code}")

    # 7. read 端点他人 403
    r = client.post(f"/api/chat/sessions/{sid}/read",
                    headers=hdr(member=USER_B))
    check("P0-read他人403", r.status_code == 403, f"got {r.status_code}")


# ============================================================
# P1 实时: since 增量 + read
# ============================================================

async def test_p1_incremental(client):
    print("[P1 实时增量轮询]")
    session = await seed_session(client, USER_A)
    sid = session["sessionId"]

    # 发 3 条用户消息(AI 态同步回复)
    for content in ("竹香酒多少钱", "竹香酒多少钱2", "竹香酒多少钱3"):
        r = client.post(f"/api/chat/sessions/{sid}/messages",
                        json={"senderType": "user", "senderId": USER_A,
                              "messageType": "text", "content": content},
                        headers=hdr(member=USER_A))
        assert r.status_code == 200, r.text

    # 1. 全量
    r = client.get(f"/api/chat/sessions/{sid}/messages",
                   headers=hdr(member=USER_A))
    msgs = r.json()["data"]
    check("P1-全量查询", len(msgs) >= 6, f"got {len(msgs)}")

    # 2. since 增量: 取中位消息 id 为游标, 仅返回其后的消息
    cursor = msgs[len(msgs) // 2]["id"]
    r = client.get(f"/api/chat/sessions/{sid}/messages?since_message_id={cursor}",
                   headers=hdr(member=USER_A))
    fresh = r.json()["data"]
    check("P1-增量仅新消息", len(fresh) > 0
          and all(m["id"] > cursor for m in fresh),
          f"cursor={cursor} got {len(fresh)}")

    # 3. since=最大id → 空
    max_id = max(m["id"] for m in msgs)
    r = client.get(f"/api/chat/sessions/{sid}/messages?since_message_id={max_id}",
                   headers=hdr(member=USER_A))
    check("P1-增量空结果", len(r.json()["data"]) == 0,
          f"got {len(r.json()['data'])}")

    # 4. read 标记 AI 消息已读
    r = client.post(f"/api/chat/sessions/{sid}/read", headers=hdr(member=USER_A))
    marked = r.json()["data"]["marked"]
    check("P1-read标记AI消息", marked > 0, f"marked={marked}")

    # 5. 二次 read → 0(幂等)
    r = client.post(f"/api/chat/sessions/{sid}/read", headers=hdr(member=USER_A))
    check("P1-read幂等", r.json()["data"]["marked"] == 0,
          f"marked={r.json()['data']['marked']}")


# ============================================================
# P2 客服工作台
# ============================================================

async def test_p2_cs_workbench(client):
    print("[P2 客服工作台]")
    sid = await seed_human_session(client, USER_A)

    # 1. 非 admin 403
    r = client.get("/api/chat/cs/queue", headers=hdr(member=USER_B))
    check("P2-非admin403", r.status_code == 403, f"got {r.status_code}")

    # 2. 排队列表(admin, human_chatting)
    r = client.get("/api/chat/cs/queue", headers=hdr(admin=True))
    rows = r.json()["data"]
    check("P2-排队列表", any(s["sessionId"] == sid for s in rows),
          f"count={len(rows)}")

    # 3. 接入会话(分配客服+系统消息)
    r = client.post(f"/api/chat/cs/sessions/{sid}/accept",
                    headers=hdr(admin=True))
    d = r.json()["data"]
    check("P2-接入分配", r.status_code == 200
          and d["customerServiceId"] and d.get("alreadyAssigned") is False,
          f"{r.status_code} {d}")

    msgs = client.get(f"/api/chat/sessions/{sid}/messages",
                      headers=hdr(admin=True)).json()["data"]
    check("P2-接入系统消息",
          any(m["senderType"] == "system" and "已接入" in m["content"]
              for m in msgs), "未找到接入系统消息")

    # 4. 重复接入幂等
    r = client.post(f"/api/chat/cs/sessions/{sid}/accept",
                    headers=hdr(admin=True))
    check("P2-重复接入幂等", r.status_code == 200
          and r.json()["data"].get("alreadyAssigned") is True,
          f"{r.status_code}")

    # 5. 客服回复落库
    r = client.post(f"/api/chat/cs/sessions/{sid}/reply",
                    json={"customerServiceId": 1, "content": "您好, 请问有什么可以帮您?"},
                    headers=hdr(admin=True))
    check("P2-客服回复", r.status_code == 200
          and r.json()["data"]["messageId"] > 0, f"{r.status_code}")

    msgs = client.get(f"/api/chat/sessions/{sid}/messages",
                      headers=hdr(admin=True)).json()["data"]
    cs_msgs = [m for m in msgs if m["senderType"] == "customer_service"]
    check("P2-回复不触发AI", len(cs_msgs) == 1, f"cs_msgs={len(cs_msgs)}")

    # 6. 未接入会话回复 409
    sid2 = await seed_human_session(client, USER_B)
    r = client.post(f"/api/chat/cs/sessions/{sid2}/reply",
                    json={"customerServiceId": 1, "content": "hi"},
                    headers=hdr(admin=True))
    check("P2-未接入回复409", r.status_code == 409, f"got {r.status_code}")

    # 7. 敏感词回复 409
    r = client.post(f"/api/chat/cs/sessions/{sid}/reply",
                    json={"customerServiceId": 1, "content": "刷单"},
                    headers=hdr(admin=True))
    check("P2-敏感词409", r.status_code == 409, f"got {r.status_code}")

    # 8. AI 态会话接入 409(仅 human_chatting 可接入)
    session_ai = await seed_session(client, USER_A)
    r = client.post(f"/api/chat/cs/sessions/{session_ai['sessionId']}/accept",
                    headers=hdr(admin=True))
    check("P2-AI态接入409", r.status_code == 409, f"got {r.status_code}")


# ============================================================
# P3 三态灰度 + 护栏
# ============================================================

async def make_fake_rag():
    """伪造 RAG(rule/llm 双轨答案)"""
    async def fake_rag(query, provider="rule"):
        if provider == "llm":
            return {"mode": "synthesized", "answer": "LLM答案",
                    "confidence": 0.95, "citations": [{"entryId": 9}]}
        return {"mode": "direct", "answer": "规则答案",
                "confidence": 0.85, "citations": [{"entryId": 1}]}
    return fake_rag


async def test_p3_mode(client):
    print("[P3 三态灰度+护栏]")
    svc = ChatService()
    mode_svc = ChatModeService()
    fake_rag = await make_fake_rag()

    # 1. 默认 off
    r = client.get("/api/chat/mode", headers=hdr(admin=True))
    m = r.json()["data"]
    check("P3-默认off", m["mode"] == "off" and m["source"] == "env",
          f"{m}")

    # 2. override 切 shadow
    r = client.post("/api/chat/mode/override", json={"mode": "shadow"},
                    headers=hdr(admin=True))
    m = r.json()["data"]
    check("P3-override切shadow", m["mode"] == "shadow"
          and m["source"] == "runtime_override", f"{m}")

    # 3. shadow: rule 答案呈现 + LLM 答案留痕
    svc.knowledge_svc.rag_answer = fake_rag
    session = await svc.create_session(USER_A)
    result = await svc.send_message(
        session["sessionId"], "user", USER_A, "text", "竹香酒多少钱")
    reply = result["aiReply"]
    check("P3-shadow规则呈现", reply["content"] == "规则答案",
          f"got {reply['content']}")
    check("P3-shadowLLM留痕", reply.get("shadowLlm")
          and reply["shadowLlm"]["answer"] == "LLM答案",
          f"got {reply.get('shadowLlm')}")

    # 4. 落库消息含 shadowLlm(admin 可审计)
    msgs = await svc.list_messages(session["sessionId"])
    stored = [m for m in msgs if m.get("senderType") == "ai"
              and m.get("shadowLlm")]
    check("P3-shadow落库留痕", len(stored) == 1
          and stored[0]["shadowLlm"]["answer"] == "LLM答案",
          f"stored={len(stored)}")

    # 5. override 切 assist: LLM 轨呈现
    client.post("/api/chat/mode/override", json={"mode": "assist"},
                headers=hdr(admin=True))
    session2 = await svc.create_session(USER_A)
    result = await svc.send_message(
        session2["sessionId"], "user", USER_A, "text", "竹香酒多少钱")
    reply = result["aiReply"]
    check("P3-assistLLM呈现", reply["content"] == "LLM答案",
          f"got {reply['content']}")
    check("P3-assist无留痕字段", not reply.get("shadowLlm"),
          f"got {reply.get('shadowLlm')}")

    # 6. override 清除回 off
    client.post("/api/chat/mode/override", json={"mode": ""},
                headers=hdr(admin=True))
    session3 = await svc.create_session(USER_A)
    result = await svc.send_message(
        session3["sessionId"], "user", USER_A, "text", "竹香酒多少钱")
    check("P3-override清除回off", result["aiReply"]["content"] == "规则答案",
          f"got {result['aiReply']['content']}")

    # 7. 旧开关兼容: KNOWLEDGE_CHAT_LLM=on → assist
    os.environ["KNOWLEDGE_CHAT_LLM"] = "on"
    m = await mode_svc.current_mode()
    check("P3-旧开关映射assist", m["mode"] == "assist",
          f"mode={m['mode']}")
    del os.environ["KNOWLEDGE_CHAT_LLM"]

    # 8. 护栏恶化自动降档
    r = client.post("/api/chat/mode/guard",
                    json={"complaintRate": 0.10, "unresolvedRate": 0.05,
                          "baselineComplaintRate": 0.02,
                          "baselineUnresolvedRate": 0.04},
                    headers=hdr(admin=True))
    d = r.json()["data"]
    check("P3-护栏恶化检出", d["breached"] is True
          and d["pausedNow"] is True, f"{d}")

    m = await mode_svc.current_mode()
    check("P3-护栏降档off", m["mode"] == "off"
          and m["source"] == "guard_pause", f"{m}")

    # 9. resume 人工恢复
    r = client.post("/api/chat/mode/resume", headers=hdr(admin=True))
    m = r.json()["data"]
    check("P3-resume恢复", r.status_code == 200
          and m["paused"] is False, f"{r.status_code} {m}")

    # 10. 护栏未恶化不降档
    r = client.post("/api/chat/mode/guard",
                    json={"complaintRate": 0.02, "unresolvedRate": 0.04,
                          "baselineComplaintRate": 0.02,
                          "baselineUnresolvedRate": 0.04},
                    headers=hdr(admin=True))
    check("P3-护栏正常不降档", r.json()["data"]["breached"] is False,
          f"{r.json()['data']}")

    # 11. mode 端点非 admin 403
    r = client.get("/api/chat/mode")
    check("P3-mode端点403", r.status_code == 403, f"got {r.status_code}")


# ============================================================
# P4 调度异常留痕
# ============================================================

async def test_p4_dispatch_error():
    print("[P4 调度异常留痕]")
    svc = ChatService()

    async def broken_dispatch(*args, **kwargs):
        raise RuntimeError("调度中枢不可用")

    import services.role_service as role_module
    origin = role_module.RoleService.dispatch_customer_service
    role_module.RoleService.dispatch_customer_service = broken_dispatch
    try:
        session = await svc.create_session(USER_A)
        await svc._do_transfer(session, reason="测试")
        check("P4-异常留痕dispatchError",
              session.get("dispatchError") == "调度中枢不可用",
              f"got {session.get('dispatchError')}")
        check("P4-异常回退默认客服",
              session["customerServiceId"] == 1
              and session["status"] == SESSION_STATUS_HUMAN,
              f"cs={session.get('customerServiceId')}")
    finally:
        role_module.RoleService.dispatch_customer_service = origin


# ============================================================
# 主流程
# ============================================================

async def main():
    print("=" * 64)
    print("AI智能聊天及人工聊天模块 · 全域升级专项测试(P0-P4)")
    print("=" * 64)

    client = TestClient(app)

    reset_store()
    await test_p0_authorization(client)
    print()

    reset_store()
    await test_p1_incremental(client)
    print()

    reset_store()
    await test_p2_cs_workbench(client)
    print()

    reset_store()
    await test_p3_mode(client)
    print()

    reset_store()
    await test_p4_dispatch_error()

    print()
    print("-" * 64)
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
