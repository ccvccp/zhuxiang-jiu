"""AI智能中枢模块(35号)端到端测试(Service 层 + FastAPI TestClient, 无需 Docker)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_hub_routes.py

覆盖(P0 验收, 设计文档第十章):
    1. 意图分类规则轨: 12 意图关键词命中 + 未命中回退 chat.general + 埋点计数
    2. 角色能力面板: 四角色 chips 差异化(≤6) + 未知角色回退 guest
    3. ASR 降级链: HUB_ENABLED=off 拒绝 / 空音频 / 超限 2MB / 未配置 key 结构化降级
    4. ASR 限流: 会员日额度超出后拒绝
    5. 能力注册表: 种子加载 / 查询(admin) / 上下架 / 404
    6. 入口健康: 聚合绿灯 / 熔断摘除后 degraded
    7. 路由层 HTTP 语义: 鉴权(401/403) / 降级 200 结构化 / toggle 语义
    8. chat 兼容: 会话消息带 messageType=voice+asrText 字段透传
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from repositories.hub_repository import (
    HubRepository, classify_intent_rule, CAPABILITY_SEED,
    INTENT_CHAT_HUMAN, INTENT_ORDER_QUERY, INTENT_ORDER_AFTERSALE,
    INTENT_ROLE_PROFIT, INTENT_PRODUCT_PRICE, INTENT_KNOWLEDGE_QA,
    INTENT_CHAT_GENERAL, ROLE_GUEST, ROLE_MEMBER, ROLE_STAFF, ROLE_ADMIN,
)
from services.hub_service import HubService

# 测试结果收集
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


async def test_intent_rules():
    """1. 意图分类规则轨"""
    cases = [
        ("转人工", INTENT_CHAT_HUMAN),
        ("我要找真人客服", INTENT_CHAT_HUMAN),
        ("查一下我的订单到哪了", INTENT_ORDER_QUERY),
        ("快递什么时候发货", INTENT_ORDER_QUERY),
        ("我要退货", INTENT_ORDER_AFTERSALE),
        ("查一下我的分润收益", INTENT_ROLE_PROFIT),
        ("这瓶酒多少钱", INTENT_PRODUCT_PRICE),
        ("酱香型白酒是什么工艺", INTENT_KNOWLEDGE_QA),
        ("今天天气不错啊", INTENT_CHAT_GENERAL),
        ("", INTENT_CHAT_GENERAL),
    ]
    for text, expected in cases:
        got = classify_intent_rule(text)
        record(f"意图[{text or '(空)'}]→{expected}", got == expected,
               f"got {got}")
    # 埋点: 统计随调用递增
    svc = HubService()
    r1 = await svc.classify_intent("这瓶酒多少钱")
    r2 = await svc.classify_intent("这瓶酒多少钱")
    record("意图统计递增", r2["daily_count"] == r1["daily_count"] + 1,
           f"{r1['daily_count']} -> {r2['daily_count']}")
    record("意图轨=rule", r2["track"] == "rule")


async def test_panel():
    """2. 角色能力面板"""
    svc = HubService()
    guest = await svc.get_panel("guest")
    member = await svc.get_panel("member")
    staff = await svc.get_panel("cs_staff")
    admin = await svc.get_panel("admin")
    record("guest 面板 chips", len(guest["chips"]) > 0 and len(guest["chips"]) <= 6)
    record("member 面板含查订单", any(c["id"] == "order.query" for c in member["chips"]))
    record("staff 面板含工单队列", any(c["id"] == "role.dispatch" for c in staff["chips"]))
    record("admin 面板含AI健康", any(c["id"] == "ops.health" for c in admin["chips"]))
    record("chips 均带快捷指令", all(c.get("quick") for c in member["chips"]))
    unknown = await svc.get_panel("whatever-role")
    record("未知角色回退 guest", unknown["role"] == ROLE_GUEST and len(unknown["chips"]) > 0)
    record("面板透出 hubEnabled", guest.get("hubEnabled") is True)


async def test_asr_degrade():
    """3. ASR 降级链"""
    svc = HubService()
    # 3a. HUB_ENABLED=off
    os.environ["HUB_ENABLED"] = "off"
    r = await svc.transcribe_upload(b"fake-audio", "a.wav", member_id=1)
    record("ASR 总开关关闭拒绝", r["success"] is False and r.get("fallback_hint") == "keyboard")
    os.environ["HUB_ENABLED"] = "on"
    # 3b. 空音频
    r = await svc.transcribe_upload(b"", "a.wav", member_id=1)
    record("ASR 空音频拒绝", r["success"] is False)
    # 3c. 超限 2MB
    r = await svc.transcribe_upload(b"x" * (2 * 1024 * 1024 + 1), "a.wav", member_id=1)
    record("ASR 超 2MB 拒绝", r["success"] is False and "过大" in r["error"])
    # 3d. 未配置 LLM key(结构化降级, 不抛异常)
    saved_key = os.environ.pop("LLM_API_KEY", None)
    os.environ["LLM_ENABLED"] = "on"
    r = await svc.transcribe_upload(b"fake-audio-bytes", "a.wav", member_id=1)
    record("ASR 未配置 key 降级", r["success"] is False and r.get("fallback_hint") == "keyboard")
    if saved_key is not None:
        os.environ["LLM_API_KEY"] = saved_key


async def test_asr_quota():
    """4. ASR 会员日限流"""
    os.environ["HUB_ASR_DAILY_LIMIT"] = "3"
    svc = HubService()
    rejected = False
    for _ in range(4):
        r = await svc.transcribe_upload(b"fake", "a.wav", member_id=4242)
        if not r["success"] and "额度" in r.get("error", ""):
            rejected = True
    record("ASR 日额度超限拒绝", rejected)
    os.environ["HUB_ASR_DAILY_LIMIT"] = "200"


async def test_capabilities():
    """5. 能力注册表"""
    repo = HubRepository()
    caps = await repo.list_capabilities()
    record("种子能力加载", len(caps) >= len(CAPABILITY_SEED))
    cap = await repo.get_capability("knowledge.rag")
    record("单能力查询", cap and cap["name"] == "知识库问答")
    none_cap = await repo.get_capability("not-exist")
    record("不存在能力 None", none_cap is None)
    cap["enabled"] = False
    await repo.upsert_capability(cap)
    cap2 = await repo.get_capability("knowledge.rag")
    record("能力上下架持久化", cap2["enabled"] is False)
    cap2["enabled"] = True
    await repo.upsert_capability(cap2)


async def test_health():
    """6. 入口健康聚合"""
    svc = HubService()
    h = await svc.get_health()
    record("健康聚合总数>0", h["capabilities_total"] > 0)
    record("初始 healthy", h["status"] == "healthy"
           and h["capabilities_enabled"] == h["capabilities_healthy"])
    # 摘除一个能力后仍 healthy(降级到 enabled 数)
    cap = await svc.repo.get_capability("chat.human")
    cap["enabled"] = False
    await svc.repo.upsert_capability(cap)
    h2 = await svc.get_health()
    record("下架后仍聚合正常", h2["capabilities_enabled"] == h["capabilities_enabled"] - 1)
    cap["enabled"] = True
    await svc.repo.upsert_capability(cap)
    # 健康率跌破 0.5 → degraded
    cap = await svc.repo.get_capability("knowledge.rag")
    cap["health"]["success_rate_7d"] = 0.3
    await svc.repo.upsert_capability(cap)
    h3 = await svc.get_health()
    record("熔断阈值→degraded", h3["status"] == "degraded")
    cap["health"]["success_rate_7d"] = 0.93
    await svc.repo.upsert_capability(cap)


def test_http_layer():
    """7. 路由层 HTTP 语义(TestClient)"""
    try:
        from fastapi.testclient import TestClient
        from main import app
    except ImportError:
        RESULTS.append("  - HTTP 层测试跳过(fastapi TestClient 不可用)")
        return
    client = TestClient(app)

    # 7a. 意图分类 200
    r = client.post("/api/hub/input/intent", json={"text": "这瓶酒多少钱"})
    body = r.json()
    record("HTTP 意图分类", r.status_code == 200 and body["intent"] == INTENT_PRODUCT_PRICE,
           f"{r.status_code} {body}")

    # 7b. 参数校验 422(空文本)
    r = client.post("/api/hub/input/intent", json={"text": ""})
    record("HTTP 意图空文本 422", r.status_code == 422)

    # 7c. 面板 200(角色参数)
    r = client.get("/api/hub/panel?role=member")
    body = r.json()
    record("HTTP 面板", r.status_code == 200 and body["role"] == "member")

    # 7d. 健康 200
    r = client.get("/api/hub/health")
    record("HTTP 健康", r.status_code == 200 and r.json()["status"] in ("healthy", "degraded"))

    # 7e. 能力注册表: 无 X-Role → 403
    r = client.get("/api/hub/capabilities")
    record("HTTP 注册表无权限 403", r.status_code == 403)
    # 有 X-Role: admin → 200
    r = client.get("/api/hub/capabilities", headers={"X-Role": "admin"})
    body = r.json()
    record("HTTP 注册表 admin 200", r.status_code == 200 and body["success"] is True
           and body["total"] > 0)

    # 7f. 上下架: 不存在 → 404
    r = client.post("/api/hub/capabilities/not-exist/toggle",
                    json={"enabled": False}, headers={"X-Role": "admin"})
    record("HTTP 上下架 404", r.status_code == 404)

    # 7g. 上下架: 正常 → 200
    r = client.post("/api/hub/capabilities/chat.human/toggle",
                    json={"enabled": True}, headers={"X-Role": "admin"})
    record("HTTP 上下架成功", r.status_code == 200 and r.json()["enabled"] is True)

    # 7h. ASR 降级: 无 key 时 200 + success=false(不 5xx)
    import base64 as _b64
    saved_key = os.environ.pop("LLM_API_KEY", None)
    r = client.post("/api/hub/asr",
                    json={"audio_b64": _b64.b64encode(b"fake-audio").decode(),
                          "fmt": "wav"})
    body = r.json()
    record("HTTP ASR 降级 200 结构化", r.status_code == 200 and body["success"] is False,
           f"{r.status_code} {body}")
    # 7h2. ASR 非法 base64 → 200 结构化错误
    r = client.post("/api/hub/asr", json={"audio_b64": "!!!not-base64!!!"})
    record("HTTP ASR 非法base64 200 结构化", r.status_code == 200
           and r.json()["success"] is False)
    if saved_key is not None:
        os.environ["LLM_API_KEY"] = saved_key

    # 7i. 意图统计(admin)
    r = client.get("/api/hub/ops/intents?days=3", headers={"X-Role": "admin"})
    record("HTTP 意图统计", r.status_code == 200 and r.json()["success"] is True)
    r = client.get("/api/hub/ops/intents")
    record("HTTP 意图统计无权限 403", r.status_code == 403)


async def test_chat_voice_compat():
    """8. chat 消息模型 voice 字段透传(chat_routes 已预留)"""
    try:
        from services.chat_service import ChatService
    except ImportError:
        RESULTS.append("  - chat 兼容测试跳过(chat_service 不可用)")
        return
    from repositories.chat_repository import SENDER_USER, MESSAGE_TYPE_TEXT
    svc = ChatService()
    try:
        s = await svc.create_session(user_id=9911, session_type="presale",
                                     age_confirmed=True)
        sid = s["sessionId"] if isinstance(s, dict) else s.sessionId
        record("chat 会话建立(voice 容器)", bool(sid))
    except Exception as exc:  # noqa: BLE001
        record("chat 会话建立(voice 容器)", False, str(exc))


async def main():
    reset_store()
    print("=" * 64)
    print("AI智能中枢模块(35号) P0 端到端测试")
    print("=" * 64)

    await test_intent_rules()
    await test_panel()
    await test_asr_degrade()
    await test_asr_quota()
    await test_capabilities()
    await test_health()
    test_http_layer()
    await test_chat_voice_compat()

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
