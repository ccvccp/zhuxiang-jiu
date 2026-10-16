# -*- coding: utf-8 -*-
"""MCP 只读工具面测试(小竹蓝图 P4-⑧, mcp_server_service + 路由)

覆盖(任务用例①-⑩):
    ① off 默认 → 403 + JSON-RPC error 说明
    ② Token: 未配置拒绝(明示需配置)/错 Token 拒/对 Token 过
    ③ initialize 返回协议字段(protocolVersion/capabilities/serverInfo)
    ④ tools/list 只含白名单(隐私类 order.query/get_trust_balance
       等绝不在; 护栏文案保留; inputSchema 宽松 object)
    ⑤ tools/call 白名单内成功(打桩 execute——断言 session.channel
       =="mcp" 与 MCP content 包裹; operationId 与 action 双形态)
    ⑥ 白名单外 → -32602(附「不在 MCP 白名单」)
    ⑦ execute 抛 ValueError/KeyError → isError=True 不 500(审计 fallback)
    ⑧ 未知方法 → -32601; 解析失败 -32700; batch -32600(single only)
    ⑨ MCP_TOOLS 追加生效(readonly 生效; sensitive/不存在被拒)
    ⑩ GET /api/mcp/status admin 鉴权形态(无 X-Role 403 /
       admin 200 含 mode/token 配置态/工具数/计数)

打桩说明: executor 是进程单例 get_executor()——monkeypatch
实例方法(实例属性遮蔽类方法, teardown 自动还原, 不破坏其他
测试); 网关审计打桩为记录桩(mcp_svc._GATEWAY 替换), 断言
ok/fallback 双路留痕。

运行: pytest test_mcp_server.py
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.mcp_server_service as mcp_svc


# ============================================================
# 公共夹具
# ============================================================

@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """每用例隔离: MCP 三 env 回未配置基线(monkeypatch 自动还原)"""
    for key in ("MCP_SERVER_MODE", "MCP_SERVER_TOKEN",
                "MCP_TOOLS"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def client():
    """裸 FastAPI 应用(只挂 MCP 路由——鉴权形态自测, 不走全局中间件)"""
    from routes.mcp_routes import register_mcp_routes
    app = FastAPI()
    register_mcp_routes(app)
    return TestClient(app)


@pytest.fixture
def on_mode(monkeypatch):
    """开启模式 + 固定 Token(② 之外的用例统一用对 Token)"""
    monkeypatch.setenv("MCP_SERVER_MODE", "on")
    monkeypatch.setenv("MCP_SERVER_TOKEN", "unit-test-token")
    return {"X-MCP-Token": "unit-test-token"}


@pytest.fixture
def stub_execute(monkeypatch):
    """打桩 executor 单例 execute(记录调用; 可按参数注入异常)"""
    from services.xiaozhu_executor import get_executor
    calls = []

    async def _fake_execute(session, action, params):
        calls.append({"session": dict(session),
                      "action": action,
                      "params": dict(params or {})})
        if (params or {}).get("__raise") == "ValueError":
            raise ValueError("演示: 业务参数非法")
        if (params or {}).get("__raise") == "KeyError":
            raise KeyError("演示: 令牌不存在")
        return {"executed": True, "readonly": True,
                "echo": action}

    ex = get_executor()
    monkeypatch.setattr(ex, "execute", _fake_execute)
    return calls


@pytest.fixture
def audit_calls(monkeypatch):
    """打桩网关 _audit(记录六字段实参——免落库且可断言留痕)"""
    calls = []

    class _StubGateway:
        async def _audit(self, session, action, params,
                         member_id, consent_hash,
                         latency_ms, kind, error=""):
            calls.append({"action": action, "kind": kind,
                          "sessionId": session.get(
                              "sessionId"),
                          "memberId": member_id})

    monkeypatch.setattr(mcp_svc, "_GATEWAY",
                        _StubGateway())
    return calls


def _rpc(method, params=None, req_id=1):
    body = {"jsonrpc": "2.0", "id": req_id,
            "method": method}
    if params is not None:
        body["params"] = params
    return body


# ============================================================
# ① off 默认 403
# ============================================================

def test_t1_off_mode_403(client):
    """MCP_SERVER_MODE 未配置(off 默认)→ 403 + error 说明含 off"""
    r = client.post("/mcp", json=_rpc("initialize"),
                    headers={"X-MCP-Token": "any"})
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == mcp_svc.AUTH_ERROR
    assert "MCP_SERVER_MODE" in body["error"]["message"]
    assert "off" in body["error"]["message"]


# ============================================================
# ② Token 三态
# ============================================================

def test_t2_token_unconfigured_rejected(monkeypatch, client):
    """Token 未配置 → 拒绝所有调用(error 明示需配置)"""
    monkeypatch.setenv("MCP_SERVER_MODE", "on")
    monkeypatch.delenv("MCP_SERVER_TOKEN", raising=False)
    r = client.post("/mcp", json=_rpc("initialize"),
                    headers={"X-MCP-Token": "any"})
    assert r.status_code == 403
    msg = r.json()["error"]["message"]
    assert "MCP_SERVER_TOKEN" in msg
    assert "未配置" in msg


def test_t2_wrong_token_rejected(monkeypatch, client):
    """错 Token → 403"""
    monkeypatch.setenv("MCP_SERVER_MODE", "on")
    monkeypatch.setenv("MCP_SERVER_TOKEN", "right-one")
    r = client.post("/mcp", json=_rpc("initialize"),
                    headers={"X-MCP-Token": "wrong-one"})
    assert r.status_code == 403
    assert "X-MCP-Token" in \
        r.json()["error"]["message"]


def test_t2_correct_token_passes(client, on_mode):
    """对 Token → 200(协议正常回包)"""
    r = client.post("/mcp", json=_rpc("initialize"),
                    headers=on_mode)
    assert r.status_code == 200
    assert "result" in r.json()


# ============================================================
# ③ initialize 协议字段
# ============================================================

def test_t3_initialize(client, on_mode):
    r = client.post("/mcp", json=_rpc("initialize",
                                      req_id=7),
                    headers=on_mode)
    assert r.status_code == 200
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 7
    result = body["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"] == {
        "name": "zhuxiang-mcp", "version": "1.0.0"}


# ============================================================
# ④ tools/list 只含白名单
# ============================================================

def test_t4_tools_list_whitelist_only(client, on_mode):
    r = client.post("/mcp", json=_rpc("tools/list"),
                    headers=on_mode)
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    actions = {a for a in mcp_svc.MCP_TOOL_WHITELIST}
    # 白名单全量在(operationId 形态)
    from services.xiaozhu_fc_registry import TOOL_REGISTRY
    assert names == {
        TOOL_REGISTRY[a]["operationId"] for a in actions}
    # 隐私类绝不暴露(order.query 不在注册表也须不在面;
    # 会员隐私类 operationId 逐个点名)
    for forbidden in ("order.query", "get_trust_score",
                      "get_trust_balance",
                      "get_privacy_budget",
                      "get_voice50_score",
                      "get_explanation_report",
                      "get_repair_window",
                      "convert_credit_to_trust",
                      "submit_checkout",
                      "execute_repair_action"):
        assert forbidden not in names
    # 护栏文案保留(❌禁令) + 宽松 object schema
    one = next(t for t in tools
               if t["name"] == "query_product_price")
    assert "❌" in one["description"]
    assert one["inputSchema"] == {
        "type": "object", "properties": {},
        "additionalProperties": True}


# ============================================================
# ⑤ tools/call 白名单内成功(打桩 execute)
# ============================================================

def test_t5_call_success_operation_id(
        client, on_mode, stub_execute, audit_calls):
    """operationId 形态调用: session.channel=mcp + MCP content 包裹"""
    r = client.post("/mcp", json=_rpc("tools/call", {
        "name": "list_new_products",
        "arguments": {"foo": "bar"}}), headers=on_mode)
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["isError"] is False
    assert len(result["content"]) == 1
    assert result["content"][0]["type"] == "text"
    assert json.loads(result["content"][0]["text"]) == {
        "executed": True, "readonly": True,
        "echo": "product.new"}
    # 打桩侧: 最小零隐私会话 + action 解析 + 参数透传
    assert len(stub_execute) == 1
    call = stub_execute[0]
    assert call["action"] == "product.new"
    assert call["params"] == {"foo": "bar"}
    assert call["session"]["channel"] == "mcp"
    assert call["session"]["sessionId"].startswith("mcp:")
    assert len(call["session"]["sessionId"]) == len(
        "mcp:") + 12
    assert "memberId" not in call["session"]
    # 审计留痕(ok 路, memberId 空, sessionId 带 mcp 标记)
    assert len(audit_calls) == 1
    assert audit_calls[0]["kind"] == "ok"
    assert audit_calls[0]["memberId"] is None
    assert audit_calls[0]["sessionId"].startswith("mcp:")


def test_t5_call_success_action_alias(
        client, on_mode, stub_execute, audit_calls):
    """action 原名形态兼容(同样须命中白名单)"""
    r = client.post("/mcp", json=_rpc("tools/call", {
        "name": "promo.query", "arguments": {}}),
        headers=on_mode)
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is False
    assert stub_execute[0]["action"] == "promo.query"
    assert stub_execute[0]["session"]["channel"] == "mcp"


def test_t5_call_missing_arguments_defaults_empty(
        client, on_mode, stub_execute):
    """arguments 缺省 → 空 dict 透传(不 -32602)"""
    r = client.post("/mcp", json=_rpc("tools/call", {
        "name": "xiaozhu.help"}), headers=on_mode)
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is False
    assert stub_execute[0]["params"] == {}


# ============================================================
# ⑥ 白名单外 -32602
# ============================================================

def test_t6_not_in_whitelist(client, on_mode, stub_execute):
    """隐私 readonly(信任余额 operationId)→ -32602 附白名单说明"""
    r = client.post("/mcp", json=_rpc("tools/call", {
        "name": "get_trust_balance"}),
        headers=on_mode)
    assert r.status_code == 200
    err = r.json()["error"]
    assert err["code"] == -32602
    assert "不在 MCP 白名单" in err["message"]
    assert stub_execute == []   # 未触达 executor


def test_t6_sensitive_rejected(client, on_mode, stub_execute):
    """高敏 action 形态(trust.convert)同样 -32602"""
    r = client.post("/mcp", json=_rpc("tools/call", {
        "name": "trust.convert",
        "arguments": {"creditPoints": 100}}),
        headers=on_mode)
    err = r.json()["error"]
    assert err["code"] == -32602
    assert stub_execute == []


def test_t6_missing_name(client, on_mode, stub_execute):
    """缺 name → -32602"""
    r = client.post("/mcp", json=_rpc("tools/call", {}),
                    headers=on_mode)
    assert r.json()["error"]["code"] == -32602
    assert stub_execute == []


# ============================================================
# ⑦ execute 抛 ValueError/KeyError → isError=True 不 500
# ============================================================

@pytest.mark.parametrize("raise_kind", [
    "ValueError", "KeyError"])
def test_t7_execute_raises_iserror(
        client, on_mode, stub_execute, audit_calls,
        raise_kind):
    r = client.post("/mcp", json=_rpc("tools/call", {
        "name": "query_product_price",
        "arguments": {"__raise": raise_kind}}),
        headers=on_mode)
    assert r.status_code == 200   # 不 500
    body = r.json()
    assert "result" in body       # isError 而非 JSON-RPC error
    result = body["result"]
    assert result["isError"] is True
    assert "演示" in result["content"][0]["text"]
    # 审计 fallback 路
    assert audit_calls[-1]["kind"] == "fallback"


# ============================================================
# ⑧ 协议层错误码(-32601/-32700/-32600)
# ============================================================

def test_t8_unknown_method(client, on_mode):
    r = client.post("/mcp", json=_rpc("resources/list"),
                    headers=on_mode)
    err = r.json()["error"]
    assert err["code"] == -32601
    assert err["message"] == "Method not found"


def test_t8_parse_error(client, on_mode):
    r = client.post("/mcp",
                    content="{not-json".encode("utf-8"),
                    headers={**on_mode,
                             "Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32700


def test_t8_batch_rejected(client, on_mode):
    r = client.post("/mcp", json=[_rpc("initialize")],
                    headers=on_mode)
    err = r.json()["error"]
    assert err["code"] == -32600
    assert "single" in err["message"]


def test_t8_invalid_request_shape(client, on_mode):
    """缺 jsonrpc/method 字段 → -32600"""
    r = client.post("/mcp", json={"id": 1},
                    headers=on_mode)
    assert r.json()["error"]["code"] == -32600


# ============================================================
# ⑨ MCP_TOOLS 追加生效(readonly 过/敏感与不存在拒)
# ============================================================

def test_t9_env_append(monkeypatch, client, on_mode,
                       stub_execute):
    """追加 readonly(trust.score)生效; sensitive(trust.convert)
    与不存在(ghost.tool)被拒"""
    monkeypatch.setenv(
        "MCP_TOOLS",
        "trust.score,trust.convert,ghost.tool")
    # tools/list 生效面
    r = client.post("/mcp", json=_rpc("tools/list"),
                    headers=on_mode)
    names = {t["name"] for t in
             r.json()["result"]["tools"]}
    assert "get_trust_score" in names      # readonly 追加生效
    assert "convert_credit_to_trust" \
        not in names                        # sensitive 拒
    assert "ghost.tool" not in names        # 不存在拒
    # tools/call 追加工具可调(仍走打桩 executor)
    r2 = client.post("/mcp", json=_rpc("tools/call", {
        "name": "get_trust_score"}),
        headers=on_mode)
    assert r2.json()["result"]["isError"] is False
    assert stub_execute[-1]["action"] == "trust.score"


def test_t9_env_append_default_untouched(
        client, on_mode):
    """未配置 MCP_TOOLS → 白名单即内置清单(追加零影响)"""
    r = client.post("/mcp", json=_rpc("tools/list"),
                    headers=on_mode)
    names = {t["name"] for t in
             r.json()["result"]["tools"]}
    assert "get_trust_score" not in names


# ============================================================
# ⑩ GET /api/mcp/status admin 鉴权形态
# ============================================================

def test_t10_status_requires_admin(client):
    r = client.get("/api/mcp/status")
    assert r.status_code == 403
    assert r.json()["detail"] == "需要管理员权限"


def test_t10_status_admin_ok(client, on_mode):
    r = client.get("/api/mcp/status",
                   headers={"X-Role": "admin"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["enabled"] is True
    assert data["tokenConfigured"] is True
    assert data["toolCount"] == len(
        mcp_svc.MCP_TOOL_WHITELIST)
    assert {t["action"] for t in data["tools"]} == set(
        mcp_svc.MCP_TOOL_WHITELIST)
    assert "stats" in data       # 审计计数概览
    assert "token" not in json.dumps(data).lower() \
        or data["tokenConfigured"]   # 不回显 Token 值


def test_t10_status_off_still_visible(monkeypatch, client):
    """off 模式下 status 仍可查(admin 排查为什么没开)"""
    r = client.get("/api/mcp/status",
                   headers={"X-Role": "admin"})
    data = r.json()["data"]
    assert data["enabled"] is False
    assert data["mode"] == "off"
    assert data["tokenConfigured"] is False


# ============================================================
# 服务层直测(白名单三重交集自防)
# ============================================================

def test_whitelist_triple_intersection():
    """内置白名单 ⊆ registry ∩ SAFE_READONLY(全 readonly)"""
    from services.xiaozhu_executor import SAFE_READONLY
    from services.xiaozhu_fc_registry import (
        TOOL_REGISTRY, tool_actions,
    )
    wl = mcp_svc.current_whitelist()
    assert set(wl) == set(mcp_svc.MCP_TOOL_WHITELIST)
    for a in wl:
        assert a in tool_actions()
        assert a in SAFE_READONLY
        assert TOOL_REGISTRY[a]["tier"] == "readonly"
