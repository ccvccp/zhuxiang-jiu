"""86号 B批·智能活动 Agent 测试(bridge 既有活动管理模块)

覆盖: 活动卡片公开安全投影 / chat activity 意图升级 /
匿名报名三闸(同意/存在性/状态) + 留资链路继承(幂等) /
老带新邀请函确定性归因码
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from main import app
    return TestClient(app)


async def _seed_activity(status: str = "registering") -> int:
    from datetime import datetime
    from repositories.activity_repository import ActivityRepository
    repo = ActivityRepository()
    aid = await repo.next_activity_id()
    now = datetime.utcnow().isoformat()
    await repo.save_activity({
        "id": aid, "activityNo": f"HD{aid}", "name": "竹香品鉴会·测试",
        "type": "interactive", "subType": "", "description": "品鉴 6 款招牌",
        "startTime": "", "endTime": "", "status": status, "budget": 0,
        "usedBudget": 0, "rules": {}, "applicableScope": {},
        "registrationCount": 0, "createdBy": 0, "approvedBy": 0,
        "createdAt": now, "updatedAt": now})
    return aid


def test_activities_public_projection(client):
    """活动卡片: 公开 200 + 安全字段投影(无 budget/rules 等内部字段)"""
    aid = asyncio.run(_seed_activity())
    r = client.get("/api/hook86/activities")
    assert r.status_code == 200
    card = next(c for c in r.json()["data"]["cards"]
                if c["activityId"] == aid)
    assert card["name"] == "竹香品鉴会·测试"
    assert card["typeName"] == "互动"
    assert card["signable"] is True
    for k in ("budget", "rules", "applicableScope", "createdBy",
              "approvedBy", "activityNo"):
        assert k not in card
    # 匿名不带 tier 块; 会员带(tier 权益联动)
    assert "tier" not in r.json()["data"]


def test_chat_activity_intent_upgraded(client):
    """chat activity 意图 → action=activities_card + 附活动卡片"""
    r = client.post("/api/hook86/chat", json={
        "message": "最近有什么活动福利", "fingerprint": "fp-act-test"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["intent"] == "activity"
    assert d["action"] == "activities_card"
    assert isinstance(d["activities"], list)
    assert "理性饮酒" in d["reply"]
    # 匿名主体不带 tier 联动块
    assert "tier" not in d


def test_chat_member_tier_linked(client):
    """登录会员 chat activity → 附 tier 权益联动块(懒评估)"""
    from repositories.member_repository import MemberRepository

    async def _seed():
        await MemberRepository().save(77001, {
            "id": 77001, "name": "联动测试", "phone": "13800009999",
            "level": 2, "growth_value": 6500, "status": 1,
            "points": 0,
            "createdAt": "2026-10-18T00:00:00"})

    asyncio.run(_seed())
    r = client.post("/api/hook86/chat", json={
        "message": "会员有什么活动福利", "memberId": "77001"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["intent"] == "activity"
    t = d["tier"]
    assert t["tier"] == "gold" and t["tierName"] == "金卡会员"
    assert "品鉴会优先报名" in t["benefits"]
    assert t["nextTierName"] == "黑金会员"


def test_signup_gates_and_inheritance(client):
    """报名三闸: 未勾同意/活动不存在/草稿不可报; 成功=留资继承幂等"""
    body = {"phone": "13800001111", "consent": False,
            "fingerprint": "fp-su1"}
    # 1) 未勾同意 → 422
    r = client.post("/api/hook86/activity/signup",
                    json={**body, "activityId": 1})
    assert r.status_code == 422
    # 2) 活动不存在 → 422
    r = client.post("/api/hook86/activity/signup", json={
        **body, "activityId": 99999999, "consent": True})
    assert r.status_code == 422
    # 3) 草稿活动不可报名 → 422
    draft_id = asyncio.run(_seed_activity(status="draft"))
    r = client.post("/api/hook86/activity/signup", json={
        **body, "activityId": draft_id, "consent": True})
    assert r.status_code == 422
    # 4) 正常报名 → ok + 幂等(同手机号同渠道不重复落库)
    aid = asyncio.run(_seed_activity())
    payload = {"activityId": aid, "phone": "13912340001",
               "consent": True, "fingerprint": "fp-su2",
               "channelCode": "invtest"}
    r = client.post("/api/hook86/activity/signup", json=payload)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["ok"] is True and d["activityId"] == aid
    assert d["activityName"] == "竹香品鉴会·测试"
    r2 = client.post("/api/hook86/activity/signup", json=payload)
    assert r2.status_code == 200
    assert r2.json()["data"]["duplicate"] is True


def test_invite_card_deterministic(client):
    """邀请函: 公开 200 + inv 前缀归因码 + 同主体同码(确定性哈希)"""
    r = client.get("/api/hook86/activity/invite?fingerprint=fp-inv-1")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["inviteCode"].startswith("inv")
    assert len(d["inviteCode"]) == 11
    assert "channel=" in d["link"] and d["inviteCode"] in d["link"]
    assert "理性饮酒" in d["shareText"]
    # 同主体同码 / 异主体异码
    r2 = client.get("/api/hook86/activity/invite?fingerprint=fp-inv-1")
    assert r2.json()["data"]["inviteCode"] == d["inviteCode"]
    r3 = client.get("/api/hook86/activity/invite?fingerprint=fp-inv-2")
    assert r3.json()["data"]["inviteCode"] != d["inviteCode"]
