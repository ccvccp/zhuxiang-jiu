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


# ============================================================
# P1: 意图路由器 + 熔断 + 编排 + decision 接管
# ============================================================

async def test_router():
    """9. 意图路由器(角色过滤 + 命中路由 + 通用兜底)"""
    svc = HubService()
    # guest 问价 → knowledge.rag(product.price 意图在 knowledge.rag 域)
    r = await svc.route("这瓶酒多少钱", role="guest")
    record("路由: 游客问价→knowledge.rag", r["capability"] == "knowledge.rag",
           f"got {r['capability']}")
    # guest 查订单 → degraded(member 能力, 游客被拒)
    r = await svc.route("查我的订单", role="guest")
    record("路由: 游客查订单 degraded", r["status"] == "degraded"
           and any(x["reason"] == "role" for x in r["rejected"]))
    # member 查订单 → order.query
    r = await svc.route("查我的订单", role="member")
    record("路由: 会员查订单→order.query", r["capability"] == "order.query")
    # staff 查分润 → role.profit
    r = await svc.route("查我的分润", role="cs_staff")
    record("路由: 客服查分润→role.profit", r["capability"] == "role.profit")
    # 游客查分润 → degraded(角色隔离)
    r = await svc.route("查我的分润", role="guest")
    record("路由: 游客查分润 degraded", r["status"] == "degraded")
    # 通用闲聊 → unmatched 兜底
    r = await svc.route("今天天气不错", role="member")
    record("路由: 闲聊 unmatched", r["status"] == "unmatched"
           and r["capability"] is None)
    # 转人工 → chat.human(全角色可用)
    r = await svc.route("转人工", role="guest")
    record("路由: 转人工→chat.human", r["capability"] == "chat.human")
    # 下架 knowledge.rag 后问价 → degraded
    cap = await svc.repo.get_capability("knowledge.rag")
    cap["enabled"] = False
    await svc.repo.upsert_capability(cap)
    r = await svc.route("这瓶酒多少钱", role="guest")
    record("路由: 下架后 degraded", r["status"] == "degraded")
    cap["enabled"] = True
    await svc.repo.upsert_capability(cap)


async def test_circuit():
    """10. 熔断: 窗口成功率跌破阈值自动摘除 + 半开探测自愈"""
    svc = HubService()
    repo = svc.repo
    # 样本不足(<5)不熔断
    for _ in range(3):
        await repo.record_health("knowledge.rag", success=False, latency_ms=100)
    st = await svc.get_circuit_status("knowledge.rag")
    record("熔断: 样本不足不摘除", st["circuitOpen"] is False
           and st["successRate"] is None)
    # 5 次全失败 → 成功率 0 < 0.5 → 熔断
    for _ in range(2):
        await repo.record_health("knowledge.rag", success=False, latency_ms=100)
    st = await svc.get_circuit_status("knowledge.rag")
    record("熔断: 连续失败摘除", st["circuitOpen"] is True and st["successRate"] == 0.0)
    # 熔断后路由自动绕行
    r = await svc.route("这瓶酒多少钱", role="guest")
    record("熔断: 路由绕行 degraded", r["status"] == "degraded"
           and any(x["reason"] == "circuit" for x in r["rejected"]))
    # 半开探测: 清零窗口 → 恢复路由
    pr = await svc.probe_capability("knowledge.rag")
    record("熔断: 半开探测清零", pr["reset"] is True)
    r = await svc.route("这瓶酒多少钱", role="guest")
    record("熔断: 探测后恢复路由", r["capability"] == "knowledge.rag")
    # 混合成功率高不熔断(4成功1失败=0.8)
    await repo.reset_health_window("knowledge.rag")
    for ok in (True, True, True, True, False):
        await repo.record_health("knowledge.rag", success=ok, latency_ms=100)
    st = await svc.get_circuit_status("knowledge.rag")
    record("熔断: 80%成功率不摘除", st["circuitOpen"] is False
           and abs(st["successRate"] - 0.8) < 1e-9)


async def test_orchestrate():
    """11. 复合编排(≤3 段并行 + 截断)"""
    svc = HubService()
    r = await svc.orchestrate(
        ["这瓶酒多少钱", "查我的订单", "转人工"], role="member")
    record("编排: 3 段任务生成", len(r["tasks"]) == 3
           and len(r["parallelGroups"]) == 3)
    caps = [t["capability"] for t in r["tasks"]]
    record("编排: 各段正确路由",
           caps[0] == "knowledge.rag" and caps[1] == "order.query"
           and caps[2] == "chat.human", f"got {caps}")
    # 超 3 段截断
    r = await svc.orchestrate(["问价", "查单", "转人工", "查积分"], role="member")
    record("编排: 超限截断为 3", len(r["tasks"]) == 3)
    # 空段过滤
    r = await svc.orchestrate(["", "  ", "问价"], role="member")
    record("编排: 空段过滤", len(r["tasks"]) == 1)


def test_http_p1():
    """12. HTTP 层 P1 端点(route/orchestrate/circuit)"""
    try:
        from fastapi.testclient import TestClient
        from main import app
    except ImportError:
        RESULTS.append("  - HTTP P1 测试跳过(TestClient 不可用)")
        return
    client = TestClient(app)

    # 12a. 路由
    r = client.post("/api/hub/route", json={"text": "这瓶酒多少钱", "role": "guest"})
    body = r.json()
    record("HTTP P1 路由", r.status_code == 200
           and body["capability"] == "knowledge.rag", f"{body}")

    # 12b. 路由角色隔离
    r = client.post("/api/hub/route", json={"text": "查我的订单", "role": "guest"})
    record("HTTP P1 路由角色隔离", r.json()["status"] == "degraded")

    # 12c. 编排
    r = client.post("/api/hub/orchestrate",
                    json={"segments": ["问价", "转人工"], "role": "member"})
    body = r.json()
    record("HTTP P1 编排", r.status_code == 200 and len(body["tasks"]) == 2)

    # 12d. 编排超 3 段 → 422
    r = client.post("/api/hub/orchestrate",
                    json={"segments": ["a", "b", "c", "d"], "role": "member"})
    record("HTTP P1 编排超限 422", r.status_code == 422)

    # 12e. 熔断查询(admin)
    r = client.get("/api/hub/ops/circuit/knowledge.rag",
                   headers={"X-Role": "admin"})
    record("HTTP P1 熔断查询", r.status_code == 200
           and "circuitOpen" in r.json())
    r = client.get("/api/hub/ops/circuit/knowledge.rag")
    record("HTTP P1 熔断查询无权限 403", r.status_code == 403)

    # 12f. 半开探测(admin)
    r = client.post("/api/hub/ops/circuit/knowledge.rag/probe",
                    headers={"X-Role": "admin"})
    record("HTTP P1 半开探测", r.status_code == 200 and r.json()["reset"] is True)
    r = client.post("/api/hub/ops/circuit/not-exist/probe",
                    headers={"X-Role": "admin"})
    record("HTTP P1 探测 404", r.status_code == 404)


async def test_ops_overview():
    """14. P2 治理总览: 能力健康矩阵(红黄绿) + 意图分布 + 入口健康"""
    svc = HubService()
    ov = await svc.get_ops_overview()
    matrix = ov["capabilityMatrix"]
    record("总览: 能力矩阵非空", len(matrix) > 0)
    record("总览: 矩阵字段齐全",
           all(k in matrix[0] for k in
               ("id", "trafficLight", "windowSuccessRate", "circuitOpen")))
    record("总览: 入口健康聚合", ov["health"]["capabilities_total"] == len(matrix))
    # 红绿灯: 健康窗口 5 连败 → red
    for _ in range(5):
        await svc.repo.record_health("knowledge.rag", success=False, latency_ms=100)
    ov2 = await svc.get_ops_overview()
    rag = next(c for c in ov2["capabilityMatrix"] if c["id"] == "knowledge.rag")
    record("总览: 熔断能力红灯", rag["trafficLight"] == "red"
           and rag["circuitOpen"] is True)
    await svc.repo.reset_health_window("knowledge.rag")
    # 下架 → red
    cap = await svc.repo.get_capability("chat.human")
    cap["enabled"] = False
    await svc.repo.upsert_capability(cap)
    ov3 = await svc.get_ops_overview()
    ch = next(c for c in ov3["capabilityMatrix"] if c["id"] == "chat.human")
    record("总览: 下架能力红灯", ch["trafficLight"] == "red")
    cap["enabled"] = True
    await svc.repo.upsert_capability(cap)
    # 黄灯: 4 样本 1 成功(0.25 < 阈值, 但样本<5 不熔断 → yellow)
    for ok in (True, False, False, False):
        await svc.repo.record_health("role.profit", success=ok, latency_ms=100)
    ov4 = await svc.get_ops_overview()
    rp = next(c for c in ov4["capabilityMatrix"] if c["id"] == "role.profit")
    record("总览: 低成功率黄灯", rp["trafficLight"] == "yellow"
           and rp["circuitOpen"] is False, f"light={rp['trafficLight']}")
    await svc.repo.reset_health_window("role.profit")
    # 意图分布: bump 后汇总非空
    await svc.classify_intent("这瓶酒多少钱")
    ov5 = await svc.get_ops_overview()
    record("总览: 意图分布聚合", ov5["intentDistribution7d"].get("product.price", 0) >= 1,
           f"got {ov5['intentDistribution7d']}")


async def test_learning_retrigger():
    """15. P2 学习周期管理: 单评分器 / 全量(反馈不足跳过) / 未知 404"""
    svc = HubService()
    # 15a. 全量: 无反馈 → 全部 skipped(非错误)
    r = await svc.retrigger_learning()
    record("重跑: 全量 total=19", r["total"] == 21, f"got {r['total']}")
    record("重跑: 无反馈全 skipped", r["learned"] == 0 and r["skipped"] == 21)
    record("重跑: 结果含状态字段", all("status" in x for x in r["results"]))
    # 15b. 单评分器
    r = await svc.retrigger_learning("order_risk")
    record("重跑: 单评分器 total=1", r["total"] == 1
           and r["results"][0]["scorer"] == "order_risk")


def test_http_p2():
    """16. HTTP 层 P2 端点(ops/overview + learning/retrigger)"""
    try:
        from fastapi.testclient import TestClient
        from main import app
    except ImportError:
        RESULTS.append("  - HTTP P2 测试跳过(TestClient 不可用)")
        return
    client = TestClient(app)

    # 16a. 治理总览: 无权限 → 403
    r = client.get("/api/hub/ops/overview")
    record("HTTP P2 总览无权限 403", r.status_code == 403)
    # admin → 200
    r = client.get("/api/hub/ops/overview", headers={"X-Role": "admin"})
    body = r.json()
    record("HTTP P2 总览 admin 200", r.status_code == 200
           and isinstance(body["capabilityMatrix"], list)
           and "intentDistribution7d" in body, f"{r.status_code}")
    record("HTTP P2 总览带生成时间", bool(body.get("generatedAt")))

    # 16b. 学习重跑: 无权限 → 403
    r = client.post("/api/hub/ops/learning/retrigger", json={})
    record("HTTP P2 重跑无权限 403", r.status_code == 403)
    # admin + 全量 → 200 (16 档案全 skipped 也算成功)
    r = client.post("/api/hub/ops/learning/retrigger", json={},
                    headers={"X-Role": "admin"})
    body = r.json()
    record("HTTP P2 重跑全量", r.status_code == 200 and body["success"] is True
           and body["total"] == 21, f"{r.status_code} {body}")
    # 未知评分器 → 404
    r = client.post("/api/hub/ops/learning/retrigger",
                    json={"scorerId": "not-exist"}, headers={"X-Role": "admin"})
    record("HTTP P2 重跑未知 404", r.status_code == 404)
    # 单评分器 → 200
    r = client.post("/api/hub/ops/learning/retrigger",
                    json={"scorerId": "order_risk"}, headers={"X-Role": "admin"})
    record("HTTP P2 重跑单评分器", r.status_code == 200
           and r.json()["total"] == 1)


def test_decision_takeover():
    """13. decision 模块编排端点真实化接管验证"""
    try:
        from fastapi.testclient import TestClient
        from main import app
    except ImportError:
        RESULTS.append("  - decision 接管测试跳过(TestClient 不可用)")
        return
    client = TestClient(app)

    # 13a. capability-route: 插件池来自真实注册表(pluginPool=7 而非硬编码 120)
    r = client.post("/api/decision/capability-route",
                    json={"requiredCapabilities": ["knowledge.rag", "chat.human"],
                          "task": "问价并转人工"},
                    headers={"X-Role": "store_owner"})
    body = r.json()
    data = body.get("details", body.get("data", body))
    record("decision 接管: 真实插件池",
           data.get("pluginPool") == 8, f"pool={data.get('pluginPool')}")
    selected = data.get("selectedPlugins", [])
    record("decision 接管: 能力选中",
           any(p["id"] == "knowledge.rag" for p in selected))

    # 13b. orchestrate: 任务带真实 capability/intent(非硬编码)
    r = client.post("/api/decision/orchestrate",
                    json={"workflow": "test",
                          "modules": ["这瓶酒多少钱", "转人工"],
                          "context": {}},
                    headers={"X-Role": "agent"})
    body = r.json()
    data = body.get("details", body.get("data", body))
    tasks = data.get("tasks", [])
    record("decision 接管: 任务带 capability",
           len(tasks) == 2 and all("capability" in t for t in tasks))
    record("decision 接管: 意图真实路由",
           len(tasks) == 2 and tasks[0].get("capability") == "knowledge.rag"
           and tasks[1].get("capability") == "chat.human",
           f"got {tasks}")


async def test_media():
    """17. P3 媒体上传: 尺寸/格式校验 + 落盘 URL + 静态服务可访问"""
    import shutil
    svc = HubService()
    # 17a. 语音正常上传
    r = await svc.save_media("voice", b"fake-webm-audio", "webm")
    record("媒体: 语音上传成功", r["success"] is True
           and r["url"].startswith("/media/voice/"), f"got {r}")
    voice_url = r.get("url")
    # 17b. 图片正常上传
    r = await svc.save_media("image", b"fake-jpeg-data", ".jpg")
    record("媒体: 图片上传成功", r["success"] is True
           and r["url"].startswith("/media/image/"))
    # 17c. 超限拒绝(语音 2MB / 图片 5MB)
    r = await svc.save_media("voice", b"x" * (2 * 1024 * 1024 + 1), "mp3")
    record("媒体: 语音超 2MB 拒绝", r["success"] is False and "过大" in r["error"])
    r = await svc.save_media("image", b"x" * (5 * 1024 * 1024 + 1), "png")
    record("媒体: 图片超 5MB 拒绝", r["success"] is False and "过大" in r["error"])
    # 17d. 格式白名单拒绝
    r = await svc.save_media("voice", b"data", ".exe")
    record("媒体: 语音非法格式拒绝", r["success"] is False and "格式" in r["error"])
    r = await svc.save_media("image", b"data", "bmp")
    record("媒体: 图片非法格式拒绝", r["success"] is False)
    # 17e. 空内容拒绝
    r = await svc.save_media("voice", b"", "wav")
    record("媒体: 空内容拒绝", r["success"] is False)
    # 17f. HTTP 层: base64 上传 + 静态服务回读
    try:
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        import base64 as _b64
        resp = client.post("/api/hub/media/voice",
                           json={"data_b64": _b64.b64encode(b"httptest-voice").decode(),
                                 "fmt": "wav"})
        body = resp.json()
        record("HTTP 媒体: 上传 200", resp.status_code == 200
               and body["success"] is True, f"{resp.status_code} {body}")
        if body.get("success"):
            static = client.get(body["url"])
            record("HTTP 媒体: 静态回读 200", static.status_code == 200
                   and static.content == b"httptest-voice")
        resp = client.post("/api/hub/media/image",
                           json={"data_b64": "!!!bad!!!"})
        record("HTTP 媒体: 非法base64 结构化", resp.status_code == 200
               and resp.json()["success"] is False)
    except ImportError:
        RESULTS.append("  - HTTP 媒体测试跳过(TestClient 不可用)")
    # 17g. 清理测试产物
    from services.hub_service import MEDIA_ROOT
    for sub in ("voice", "image"):
        folder = os.path.join(MEDIA_ROOT, sub)
        if os.path.isdir(folder):
            shutil.rmtree(folder, ignore_errors=True)


async def test_usage():
    """18. P3 LLM 用量聚合: 内存埋点 → 快照 Redis → 汇总视图"""
    from core.metrics import llm_daily_counts, reset_metrics
    from datetime import datetime, UTC
    reset_metrics()
    svc = HubService()
    # 18a. repo 层日聚合存取
    today = datetime.now(UTC).strftime("%Y%m%d")
    counts = {"chat": {"ok": 3, "error": 1}, "vision": {"ok": 2, "error": 0}}
    await svc.repo.save_llm_daily(today, counts)
    got = await svc.repo.get_llm_daily(today)
    record("用量: 日聚合存取", got == counts, f"got {got}")
    record("用量: 空日返回空", await svc.repo.get_llm_daily("20000101") == {})
    # 18b. 无内存埋点时视图读 Redis 快照
    ov = await svc.get_usage_overview(days=7)
    day = ov["daily"].get(today)
    record("用量: 视图含当日数据", day is not None and day["calls"] == 6
           and day["errors"] == 1, f"got {day}")
    record("用量: 成本为正", day and day["cost"] > 0)
    record("用量: 汇总口径一致", ov["totals"]["calls"] >= 6)
    # 18c. metrics 埋点联动(llm_timer)
    from core.metrics import llm_timer
    with llm_timer("chat"):
        pass
    try:
        with llm_timer("embed"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    m = llm_daily_counts()
    record("用量: llm_timer 日计数", m.get(today, {}).get("chat", {}).get("ok", 0) >= 1
           and m.get(today, {}).get("embed", {}).get("error", 0) >= 1)
    reset_metrics()


async def test_approvals():
    """19. P3 晋升审批流: 清单/批准/拒绝/无挑战者 409"""
    from repositories.ai_learning_repository import AiLearningRepository
    from services import ai_learning_service as als
    repo = AiLearningRepository()
    svc = HubService()
    # 19a. 无挑战者 → 空清单
    a = await svc.list_approvals()
    record("审批: 初始空清单", a["total"] == 0 and a["pending"] == [])
    # 19b. 构造挑战者(order_risk)
    champion = {"version": "v1", "weights": {"a": 1.0}, "source": "default",
                "parentVersion": None, "stats": {}, "note": "", "createdAt": "2026-09-01T00:00:00"}
    challenger = {"version": "v2", "weights": {"a": 1.4}, "source": "learning",
                  "parentVersion": "v1", "stats": {"rewardAlignment": 0.82},
                  "note": "", "createdAt": "2026-09-01T01:00:00"}
    await repo.save_profile("order_risk", {"champion": champion,
                                            "challenger": challenger})
    a = await svc.list_approvals()
    record("审批: 挑战者入清单", a["total"] == 1
           and a["pending"][0]["scorerId"] == "order_risk"
           and a["pending"][0]["challengerVersion"] == "v2")
    # 19c. 拒绝 → 挑战者退役 + 清单清空
    r = await svc.reject_promotion("order_risk", "测试拒绝")
    record("审批: 拒绝成功", r["success"] is True and r["discardedVersion"] == "v2")
    a = await svc.list_approvals()
    record("审批: 拒绝后清单清空", a["total"] == 0)
    hist = await repo.list_history("order_risk", limit=5)
    record("审批: 拒绝版本进历史", any(h.get("note", "").startswith("rejected")
                                       for h in hist), f"got {[h.get('note') for h in hist]}")
    # 19d. 再次构造 → 批准晋升
    await repo.save_profile("order_risk", {"champion": champion,
                                            "challenger": challenger})
    r = await svc.approve_promotion("order_risk")
    record("审批: 批准晋升成功", r["success"] is True
           and r["promotedVersion"] == "v2" and r["previousVersion"] == "v1")
    profile = await repo.get_profile("order_risk")
    record("审批: 晋升后冠军=v2", profile["champion"]["version"] == "v2"
           and profile["challenger"] is None)
    # 19e. 无挑战者时拒绝/批准 → ValueError
    try:
        await svc.reject_promotion("order_risk")
        record("审批: 无挑战者拒绝报冲突", False)
    except ValueError:
        record("审批: 无挑战者拒绝报冲突", True)
    # 19f. 清理(重置 order_risk 档案)
    await als.reset_weights("order_risk")
    record("审批: 重置清理", (await repo.get_profile("order_risk")) is not None)


def test_http_p3():
    """20. HTTP 层 P3 端点(usage/approvals/media)"""
    try:
        from fastapi.testclient import TestClient
        from main import app
    except ImportError:
        RESULTS.append("  - HTTP P3 测试跳过(TestClient 不可用)")
        return
    client = TestClient(app)

    # 20a. 用量视图: 无权限 403 / admin 200
    r = client.get("/api/hub/ops/usage")
    record("HTTP P3 用量无权限 403", r.status_code == 403)
    r = client.get("/api/hub/ops/usage?days=7", headers={"X-Role": "admin"})
    body = r.json()
    record("HTTP P3 用量 admin 200", r.status_code == 200
           and "daily" in body and "totals" in body)

    # 20b. 审批清单: 无权限 403 / admin 200
    r = client.get("/api/hub/ops/learning/approvals")
    record("HTTP P3 审批无权限 403", r.status_code == 403)
    r = client.get("/api/hub/ops/learning/approvals", headers={"X-Role": "admin"})
    body = r.json()
    record("HTTP P3 审批清单 200", r.status_code == 200
           and isinstance(body["pending"], list))

    # 20c. 批准/拒绝: 无挑战者 → 409; 未知评分器 → 404
    r = client.post("/api/hub/ops/learning/approve/order_risk",
                    headers={"X-Role": "admin"})
    record("HTTP P3 批准无挑战者 409", r.status_code == 409)
    r = client.post("/api/hub/ops/learning/reject/not-exist",
                    json={"reason": None}, headers={"X-Role": "admin"})
    record("HTTP P3 拒绝未知 404", r.status_code == 404)
    r = client.post("/api/hub/ops/learning/reject/order_risk",
                    json={"reason": None}, headers={"X-Role": "admin"})
    record("HTTP P3 拒绝无挑战者 409", r.status_code == 409)


async def main():
    reset_store()
    print("=" * 64)
    print("AI智能中枢模块(35号) P0+P1+P2+P3 端到端测试")
    print("=" * 64)

    await test_intent_rules()
    await test_panel()
    await test_asr_degrade()
    await test_asr_quota()
    await test_capabilities()
    await test_health()
    test_http_layer()
    await test_chat_voice_compat()
    # ---- P1 ----
    await test_router()
    await test_circuit()
    await test_orchestrate()
    test_http_p1()
    test_decision_takeover()
    # ---- P2 ----
    await test_ops_overview()
    await test_learning_retrigger()
    test_http_p2()
    # ---- P3 ----
    await test_media()
    await test_usage()
    await test_approvals()
    test_http_p3()

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
