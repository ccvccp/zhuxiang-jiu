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


def test_pii_mask_and_delete_and_metrics(client):
    """P1: leads 出口脱敏 / 删除权 / 互动指标 / 对话留痕"""
    # 对话留痕
    client.post("/api/hook86/chat",
                json={"message": "推荐送客户的礼盒",
                      "fingerprint": "fp_pii_1", "channelCode": "dy_h5_d"})
    # 留资
    r = client.post("/api/hook86/lead",
                    json={"phone": "13611112222",
                          "channelCode": "dy_h5_d",
                          "intent": "select_wine",
                          "fingerprint": "fp_pii_1"})
    lead_id = r.json()["data"]["leadId"]
    # 出口脱敏: 明文不出 API
    rows = client.get("/api/hook86/leads",
                      headers={"X-Role": "admin"}).json()["data"]
    target = [x for x in rows if x["leadId"] == lead_id]
    assert target and target[0]["phone"] == "136****2222"
    # 指标
    m = client.get("/api/hook86/metrics",
                   headers={"X-Role": "admin"}).json()["data"]
    assert m["chats"] >= 1 and "select_wine" in m["intentDist"]
    assert 0 < m["chatToLeadRate"] <= 1
    # 删除权(PIPL): 删后查询 404
    assert client.delete(f"/api/hook86/lead/{lead_id}",
                         headers={"X-Role": "admin"}).status_code == 200
    assert client.delete(f"/api/hook86/lead/{lead_id}",
                         headers={"X-Role": "admin"}).status_code == 404
    # 留痕文本脱敏: 快照中不含明文手机号
    from repositories.hook86_repository import Hook86Repository
    import asyncio
    chats = asyncio.run(Hook86Repository().list_chats(limit=50))
    assert all("13611112222" not in (c.get("text") or "") for c in chats)


def test_ttl_expire_anonymize():
    """P2: 90天到期匿名化(统计保留+幂等释放+不重复处理)"""
    import asyncio
    asyncio.run(_ttl_body())


async def _ttl_body():
    from datetime import datetime, timedelta
    from repositories.hook86_repository import Hook86Repository
    repo = Hook86Repository()
    # 造一条 91 天前的旧留资 + 一条新留资
    old_id = await repo.next_lead_id()
    old = {"leadId": old_id, "phone": "13599998888",
           "channelCode": "ttl_test", "intent": "price",
           "status": "converted",
           "createdAt": (datetime.utcnow()
                         - timedelta(days=91)).isoformat(),
           "memberId": "", "source": "douyin_miniapp_h5"}
    await repo.save_lead(old)
    new_id = await repo.next_lead_id()
    new = {"leadId": new_id, "phone": "13577776666",
           "channelCode": "ttl_test", "intent": "select_wine",
           "status": "new",
           "createdAt": datetime.utcnow().isoformat(),
           "memberId": "", "source": "douyin_miniapp_h5"}
    await repo.save_lead(new)

    report = await repo.expire_leads(max_age_days=90)
    assert report["expired"] >= 1
    rows = {x["leadId"]: x for x in await repo.list_leads(limit=100)}
    # 旧→匿名化: phone 清空 + anonymizedAt 戳 + 统计字段保留
    assert rows[old_id]["phone"] == ""
    assert rows[old_id]["anonymizedAt"]
    assert rows[old_id]["status"] == "converted"
    assert rows[old_id]["channelCode"] == "ttl_test"
    # 新→不动
    assert rows[new_id]["phone"] == "13577776666"
    assert "anonymizedAt" not in rows[new_id]
    # 幂等释放: 同旧手机号可重新留资
    assert not await repo.find_dup("13599998888", "ttl_test")
    # 幂等: 再跑一轮不重复计数
    again = await repo.expire_leads(max_age_days=90)
    assert again["expired"] == 0
    # 渠道归因统计完整性(匿名化后 total 不变)
    summary = {r["channel"]: r for r in await repo.channel_summary()}
    assert summary["ttl_test"]["total"] == 2


def test_status_ttl_fields(client):
    """/status 透出 TTL 调度信息(默认 on/90天)"""
    d = client.get("/api/hook86/status").json()["data"]
    assert d["ttl"]["auto"] is True
    assert d["ttl"]["retentionDays"] == 90
    assert "lastRun" in d["ttl"]


def test_p3_purge_export_metrics(client):
    """P3: 物理压实 / 导出权+审计留痕 / metrics recent7"""
    import asyncio
    asyncio.run(_p3_body(client))


async def _p3_body(client):
    from datetime import datetime, timedelta
    from repositories.hook86_repository import Hook86Repository
    repo = Hook86Repository()
    # 1) 导出权: 造一条留资 → 导出完整明文 + 审计留痕
    r = client.post("/api/hook86/lead",
                    json={"phone": "13866667777", "channelCode": "p3",
                          "intent": "select_wine",
                          "fingerprint": "fp_p3"})
    exp = client.get("/api/hook86/lead/export",
                     params={"phone": "13866667777"},
                     headers={"X-Role": "admin"}).json()["data"]
    assert exp["count"] == 1
    assert exp["leads"][0]["phone"] == "13866667777"  # 副本含明文
    exports = repo._store()["hook86_exports"]
    assert exports and "138****7777" in exports[0]["phoneMasked"]
    # 非法手机号 422; 无闸 403
    assert client.get("/api/hook86/lead/export",
                      params={"phone": "123"},
                      headers={"X-Role": "admin"}).status_code == 422
    assert client.get("/api/hook86/lead/export",
                      params={"phone": "13866667777"}
                      ).status_code == 403
    # 2) 物理压实: 匿名化 366 天前的记录被物理删除
    lid = await repo.next_lead_id()
    await repo.save_lead({"leadId": lid, "phone": "13500000000",
                          "channelCode": "p3", "intent": "price",
                          "status": "converted",
                          "createdAt": (datetime.utcnow()
                                        - timedelta(days=400)).isoformat(),
                          "anonymizedAt": (datetime.utcnow()
                                           - timedelta(
                                               days=366)).isoformat(),
                          "memberId": "", "source": "t"})
    purged = await repo.purge_leads(max_anon_age_days=365)
    assert purged["purged"] >= 1
    ids = [x["leadId"] for x in await repo.list_leads(limit=200)]
    assert lid not in ids
    # 未匿名/未满期不受影响
    assert "13866667777" in [x["phone"] for x in
                             await repo.list_leads(limit=200)]
    # 3) metrics 日落点 + recent7
    day = datetime.utcnow().strftime("%Y-%m-%d")
    from services.hook86_service import Hook86Service
    m = await Hook86Service().metrics()
    await repo.save_metric_snapshot(day, m)
    await repo.push_metric_day(day)
    trend = client.get("/api/hook86/metrics",
                       headers={"X-Role": "admin"}).json()["data"]
    assert any(x.get("day") == day for x in trend["recent7"])
