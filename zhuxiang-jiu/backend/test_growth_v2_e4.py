"""79/80号 v2-E4 专项——阶梯奖励 + IBMS 绑定速率检查项

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_growth_v2_e4.py -q

覆盖(v2 方案 §1.2 + §2.2):
    T1 阶梯基础: 默认阈值 50 未达 → 每人 300
    T2 精英档: 阈值调低后当月达档 → 400
    T3 王者档: 达王者阈值 → 500
    T4 关阶梯: referralTierElite=0 → 恒基础分
    T5 check_growth_anomalies 三态: PASS / WARN(>50) / FAIL(>100)
    T6 注册表: PATROL_ITEMS 8 项含 growth_anomalies
"""

import asyncio
import os

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

import pytest

from services.promotion_service import PromotionService
from services.points_service import PointsService
from services.ibms_patrol_service import PATROL_ITEMS, check_growth_anomalies
from repositories.promotion_repository import PromotionRepository
from repositories.member_repository import MemberRepository
from repositories.store import reset_store as _reset_store_impl
from datetime import UTC, datetime


@pytest.fixture(autouse=True)
def _fresh_store():
    _reset_store_impl()
    yield
    _reset_store_impl()


async def _mk_member(phone):
    return await MemberRepository().create({
        "phone": phone, "nickname": f"会员{phone[-4:]}",
        "password": "x" * 64, "status": 1, "role": "member",
        "level": 3, "growth_value": 600, "points": 0,
        "created_at": datetime.now(UTC).isoformat(),
    })


async def _bind_seq(inviter_id, phones):
    """依次绑定一组新会员, 返回每次发放的积分流水点数列表
    (list_logs 头插——最新在前, 取 logs[0])"""
    promo = PromotionService()
    code = (await promo.list_my_codes(inviter_id))[0]["code"]
    amounts = []
    for phone in phones:
        m = await _mk_member(phone)
        await promo.bind_relation(code, m["id"])
        logs = await PointsService().repo.list_logs(
            inviter_id, source="traffic79", limit=200)
        amounts.append(logs[0]["points"] if logs else None)
    return amounts


def test_t1_tier_default_base():
    """T1 默认阈值 50: 少量绑定每人 300"""

    async def run():
        promo = PromotionService()
        inv = await _mk_member("13900000001")
        await promo.claim_promo_code(inv["id"], "douyin")
        amounts = await _bind_seq(inv["id"],
                                   ["13900000002", "13900000003"])
        assert amounts == [300, 300]

    asyncio.run(run())


def test_t2_tier_elite():
    """T2 精英档: 阈值 2 → 前两笔 300, 第三笔起 400"""

    async def run():
        promo = PromotionService()
        await promo.update_settings({"referralTierElite": 2,
                                     "referralTierKing": 0},
                                    admin="t4")
        inv = await _mk_member("13900000011")
        await promo.claim_promo_code(inv["id"], "douyin")
        amounts = await _bind_seq(
            inv["id"], ["13900000012", "13900000013", "13900000014"])
        # 档位=含当笔的月累计(save 先于 award): 第1笔计1<2→300,
        # 第2笔计2≥2→400, 第3笔计3→400
        assert amounts == [300, 400, 400]

    asyncio.run(run())


def test_t3_tier_king():
    """T3 王者档: 精英2/王者4 → 第5笔起 500"""

    async def run():
        promo = PromotionService()
        await promo.update_settings({"referralTierElite": 2,
                                     "referralTierKing": 4},
                                    admin="t4")
        inv = await _mk_member("13900000021")
        await promo.claim_promo_code(inv["id"], "douyin")
        phones = [f"1390000002{i}" for i in range(2, 8)]
        amounts = await _bind_seq(inv["id"], phones)
        # 含当笔月累计: 1→300, 2,3→400(精英), 4+→500(王者)
        assert amounts == [300, 400, 400, 500, 500, 500]

    asyncio.run(run())


def test_t4_tier_disabled():
    """T4 关阶梯: referralTierElite=0 → 恒基础分"""

    async def run():
        promo = PromotionService()
        await promo.update_settings({"referralTierElite": 0},
                                    admin="t4")
        inv = await _mk_member("13900000031")
        await promo.claim_promo_code(inv["id"], "douyin")
        amounts = await _bind_seq(
            inv["id"], ["13900000032", "13900000033", "13900000034"])
        assert amounts == [300, 300, 300]

    asyncio.run(run())


def test_t5_growth_anomalies_states():
    """T5 检查项三态: 无/少量 PASS → 51 条 WARN → 101 条 FAIL"""

    async def run():
        repo = PromotionRepository()
        # PASS: 无 24h 内关系
        r = await check_growth_anomalies()
        assert r["state"] == "PASS"

        # 造 51 条(同 inviter 24h 内)
        for i in range(51):
            await repo.save_relation({
                "inviteeMemberId": 5000 + i, "inviterMemberId": 4242,
                "code": "ZXBJ-T5", "channel": "direct",
                "status": "valid",
                "createdAt": datetime.now(UTC).isoformat()})
        r2 = await check_growth_anomalies()
        assert r2["state"] == "WARN"
        assert "4242" in r2["message"] and "51" in r2["message"]

        # 补到 101
        for i in range(50):
            await repo.save_relation({
                "inviteeMemberId": 5100 + i, "inviterMemberId": 4242,
                "code": "ZXBJ-T5", "channel": "direct",
                "status": "valid",
                "createdAt": datetime.now(UTC).isoformat()})
        r3 = await check_growth_anomalies()
        assert r3["state"] == "FAIL"

    asyncio.run(run())


def test_t6_patrol_registry():
    """T6 注册表: 8 项, growth_anomalies 在册"""
    rules = [name for name, _, _ in PATROL_ITEMS]
    assert len(rules) == 8
    assert "growth_anomalies" in rules
    assert "host_health" in rules   # 既有项零破坏
