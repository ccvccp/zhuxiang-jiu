"""超级会员智能库测试(mtier)

覆盖: 确定性规则(tier 阈值/lifecycle 状态机含挽回) /
评估画像落库+脱敏+幂等+manual 标签保留 / 端点 admin 闸 /
出口零明文 PII / 生命周期迁移留痕
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from services.mtier_service import (
    LC_ACTIVE, LC_CHURN_RISK, LC_DORMANT, LC_NEW, LC_NONE,
    LC_REPEAT, LC_WON_BACK, TIER_BLACK, TIER_GOLD, TIER_NONE,
    TIER_SILVER, MtierService)


def test_rules():
    """tier 阈值 + lifecycle 状态机(确定性, 零存储依赖)"""
    svc = MtierService()
    calc_t = svc._calc_tier
    assert calc_t(0) == TIER_NONE
    assert calc_t(1999) == TIER_NONE
    assert calc_t(2000) == TIER_SILVER
    assert calc_t(5999) == TIER_SILVER
    assert calc_t(6000) == TIER_GOLD
    assert calc_t(19999) == TIER_GOLD
    assert calc_t(20000) == TIER_BLACK
    calc_l = svc._calc_lifecycle
    assert calc_l(LC_NONE, []) == LC_NONE
    assert calc_l(LC_NONE, [5]) == LC_NEW
    assert calc_l(LC_NONE, [10, 40]) == LC_REPEAT
    assert calc_l(LC_NONE, [40]) == LC_ACTIVE
    assert calc_l(LC_NONE, [80]) == LC_DORMANT
    assert calc_l(LC_NONE, [150]) == LC_CHURN_RISK
    assert calc_l(LC_DORMANT, [5]) == LC_WON_BACK     # 沉睡后回流
    assert calc_l(LC_CHURN_RISK, [20]) == LC_WON_BACK


async def _seed_member(mid: int, *, growth: float = 7500) -> None:
    from repositories.member_repository import MemberRepository
    await MemberRepository().save(mid, {
        "id": mid, "name": f"会员{mid}", "phone": "13877776666",
        "level": 3, "growth_value": growth, "status": 1,
        "points": 0, "createdAt": datetime.utcnow().isoformat()})


async def _seed_order(member_id: int, *, days_ago: int,
                      amount: float = 350, status: str = "PAID") -> str:
    from repositories.order_repository import OrderRepository
    paid = (datetime.utcnow() - timedelta(
        days=days_ago)).isoformat()
    oid = f"MT-TEST-{member_id}-{days_ago}-{int(amount)}"
    await OrderRepository().save(oid, {
        "orderId": oid, "memberId": member_id, "status": status,
        "priceDetail": {"actualAmount": amount},
        "payment": {"paidAt": paid}, "createdAt": paid})
    return oid


def test_evaluate_profile_and_tags():
    """评估: tier/标签/脱敏落库 + manual 标签抗刷新"""
    asyncio.run(_body())


async def _body():
    await _seed_member(88001, growth=7500)
    await _seed_order(88001, days_ago=10, amount=350)
    svc = MtierService()
    p = await svc.evaluate_member(88001)
    assert p["tier"] == TIER_GOLD and p["tierName"] == "金卡会员"
    assert p["lifecycle"] == LC_NEW
    assert p["totalConsume"] == 7500
    assert p["tags"]["price_band"]["value"] == "high"
    assert p["phoneMasked"] == "138****6666"       # 出口脱敏
    assert "13877776666" not in str(p)             # 零明文
    # manual 打标 → 再评估不被 auto 覆盖
    await svc.set_tag(88001, "vip_note", "高净值待维护")
    p2 = await svc.evaluate_member(88001)
    assert p2["tags"]["vip_note"]["value"] == "高净值待维护"
    assert p2["tags"]["vip_note"]["source"] == "manual"
    assert p2["tags"]["price_band"]["source"] == "auto"
    # 幂等: lifecycle 未变不写留痕
    assert p2["prevLifecycle"] == ""


def test_lifecycle_transition_log():
    """沉睡 → 回流: won_back 迁移留痕"""
    asyncio.run(_trans_body())


async def _trans_body():
    await _seed_member(88002, growth=2500)
    await _seed_order(88002, days_ago=80, amount=120)   # 沉睡
    svc = MtierService()
    p1 = await svc.evaluate_member(88002)
    assert p1["lifecycle"] == LC_DORMANT
    await _seed_order(88002, days_ago=5, amount=200)    # 回流
    p2 = await svc.evaluate_member(88002)
    assert p2["lifecycle"] == LC_WON_BACK
    logs = await svc.logs()
    row = next(l for l in logs if l["memberId"] == 88002)
    assert row["from"] == LC_DORMANT and row["to"] == LC_WON_BACK


@pytest.fixture()
def client():
    from main import app
    return TestClient(app)


def test_endpoints_admin_gates(client):
    """端点: admin 闸 + summary/members 正常 + 出口零明文"""
    asyncio.run(_seed_member(88003, growth=22000))
    asyncio.run(_seed_order(88003, days_ago=8, amount=400))
    asyncio.run(MtierService().evaluate_member(88003))

    # 无 admin → 403
    assert client.get("/api/mtier/summary").status_code == 403
    admin = {"X-Role": "admin"}
    s = client.get("/api/mtier/summary", headers=admin).json()["data"]
    assert s["summary"]["tierDist"]["black"] >= 1
    rows = client.get("/api/mtier/members?tier=black",
                      headers=admin).json()["data"]
    assert rows and rows[0]["tier"] == "black"
    assert "13877776666" not in str(rows)          # 名单零明文
    # 手动打标: 空值 422 / 无画像 404
    assert client.post("/api/mtier/members/88003/tags",
                       headers=admin,
                       json={"key": "k", "value": ""}).status_code == 422
    assert client.post("/api/mtier/members/999999/tags",
                       headers=admin,
                       json={"key": "k", "value": "v"}).status_code == 404
    # 迁移留痕端点
    assert client.get("/api/mtier/logs",
                      headers=admin).status_code == 200


def test_me_view(client):
    """C 端本人视图: 进度/仅 auto 标签(manual 不外露)/鉴权闸"""
    asyncio.run(_seed_member(88004, growth=3500))
    asyncio.run(_seed_order(88004, days_ago=12, amount=280))
    svc = MtierService()
    asyncio.run(svc.evaluate_member(88004))
    asyncio.run(svc.set_tag(88004, "internal_note", "运营内部标记"))

    # 无身份头 → 401
    assert client.get("/api/mtier/me").status_code == 401
    assert client.get("/api/mtier/me",
                      headers={"X-Member-Id": "abc"}).status_code == 401
    # 本人视图
    d = client.get("/api/mtier/me",
                   headers={"X-Member-Id": "88004"}).json()["data"]
    assert d["tier"] == TIER_SILVER
    assert d["nextTier"] == "gold" and d["nextThreshold"] == 6000
    # 3500/2000-6000 区间 → (3500-2000)/4000 = 0.375
    assert abs(d["progress"] - 0.375) < 1e-6
    assert "internal_note" not in d["tags"]        # manual 不外露
    assert d["tags"]["consume_band"]["source"] == "auto"
    assert d["phoneMasked"] == "138****6666"
    assert "会员首单券" in d["tierBenefits"]        # 银卡权益清单
    # 无画像会员即时评估(懒评估)
    asyncio.run(_seed_member(88005, growth=800))
    d2 = client.get("/api/mtier/me",
                    headers={"X-Member-Id": "88005"}).json()["data"]
    assert d2["tier"] == TIER_NONE and d2["nextTier"] == "silver"
    # 不存在会员 → 404
    assert client.get("/api/mtier/me",
                      headers={"X-Member-Id": "999999"}
                      ).status_code == 404
