"""86号·合规引流钩子 Agent 测试

覆盖: 意图分类 4 类+兜底 / 合规闸双向(输入拦截+输出含理性饮酒声明)
/ 留资幂等+24h 频控硬闸(第3次拒绝) / 手机号校验 / 路由 admin 闸
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.hook86_service import Hook86Service


@pytest.fixture(scope="module")
def svc():
    return Hook86Service()


def test_intent_classify(svc):
    assert svc.classify("想送人礼盒, 推荐一款入门的")["intent"] == "select_wine"
    assert svc.classify("这酒什么工艺酿造的, 度数多少")["intent"] == "taste"
    assert svc.classify("现在多少钱, 有优惠吗")["intent"] == "price"
    assert svc.classify("本月有什么活动福利")["intent"] == "activity"
    assert svc.classify("你好呀")["intent"] == "other"


def test_chat_disclaimer_and_action(svc):
    import asyncio
    asyncio.run(_chat_body(svc))


async def _chat_body(svc):
    out = await svc.chat("帮我选酒送长辈", subject="m:1")
    assert out["action"] == "lead_form"
    assert "理性饮酒" in out["reply"]
    assert "未成年人禁止饮酒" in out["reply"]
    out2 = await svc.chat("随便聊聊", subject="m:1")
    assert out2["action"] == "none"


def test_chat_guardrail_input(monkeypatch):
    """输入命中违禁词 → blocked, 回复走分类话术+声明"""
    from services import hook86_service as m

    class FakeG:
        def check_input(self, text, context=None):
            return {"blocked": True, "category": "excessive_drinking",
                    "word": "x", "response": "请注意理性饮酒",
                    "sanitized": ""}
        def filter_output(self, text, context=None):
            return text

    monkeypatch.setattr(m, "get_guardrail", lambda: FakeG())
    out = m.Hook86Service().chat("多喝点没事", subject="m:2")
    import asyncio
    out = asyncio.run(out)
    assert out["blocked"] is True
    assert "理性饮酒" in out["reply"]


def test_lead_flow_and_freq(svc, monkeypatch):
    """幂等 + 24h≤2 频控: 同主体第3次拒绝"""
    import asyncio
    asyncio.run(_lead_body(svc))


async def _lead_body(svc):
    from repositories.hook86_repository import Hook86Repository
    # 内存模式隔离: 重建仓储 store
    r1 = await svc.submit_lead("13800001111", channel_code="dy_h5_a",
                               intent="select_wine", subject="fp:t1")
    assert r1["ok"] and r1["leadId"] > 0
    # 幂等: 同手机号+渠道 24h 重复
    r2 = await svc.submit_lead("13800001111", channel_code="dy_h5_a",
                               subject="fp:t1")
    assert r2["ok"] and r2["duplicate"] is True
    # 频控: 换手机号但同主体, 第2次过, 第3次拒
    r3 = await svc.submit_lead("13800002222", channel_code="dy_h5_a",
                               subject="fp:t1")
    assert r3["ok"] is True
    r4 = await svc.submit_lead("13800003333", channel_code="dy_h5_a",
                               subject="fp:t1")
    assert r4["ok"] is False and r4["reason"] == "freq_limited"
    # 不同主体不受影响
    r5 = await svc.submit_lead("13800004444", channel_code="dy_h5_b",
                               subject="fp:t2")
    assert r5["ok"] is True


def test_lead_phone_invalid(svc):
    import asyncio
    with pytest.raises(Exception):
        asyncio.run(svc.submit_lead("12345", subject="fp:t3"))


# ---------- 路由层 ----------

@pytest.fixture()
def client():
    from main import app
    return TestClient(app)


def test_route_status(client):
    r = client.get("/api/hook86/status")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["leadFreqLimit24h"] == 2 and d["module"] == "hook86"


def test_route_chat(client):
    r = client.post("/api/hook86/chat",
                    json={"message": "推荐一款送客户的礼盒",
                          "fingerprint": "fp_route_1",
                          "channelCode": "dy_h5_a"})
    assert r.status_code == 200
    assert r.json()["data"]["intent"] == "select_wine"
    # 空消息 422
    assert client.post("/api/hook86/chat",
                       json={"message": " "}).status_code == 422


def test_route_admin_gate(client):
    assert client.get("/api/hook86/leads").status_code == 403
    r = client.get("/api/hook86/leads", headers={"X-Role": "admin"})
    assert r.status_code == 200
    assert client.get("/api/hook86/channels").status_code == 403


def test_route_lead_flow(client):
    r1 = client.post("/api/hook86/lead",
                     json={"phone": "13900005555", "channelCode": "dy_h5_c",
                           "intent": "price", "fingerprint": "fp_route_2"})
    assert r1.status_code == 200 and r1.json()["data"]["ok"]
    assert client.post("/api/hook86/lead",
                       json={"phone": "123",
                             "fingerprint": "fp_route_2"}).status_code == 422
