"""79/80号 v2-E3 专项——Escrow 延迟结算(延迟发放轨)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_growth_v2_e3.py -q

覆盖(实施方案 §五):
    T1 开关关(默认): 绑定→即时发 300(现行零破坏)
    T2 开关开: 绑定→不即时发/escrow pending/新人礼仍即时
    T3 双域幂等: 即时轨已发后开 escrow 不再建(切换防双发)
    T4 结算达标(回访): last_login_at 晚于 createdAt → unlocked+300
    T5 结算达标(首单): get_by_member 非空 → unlocked
    T6 结算未达标: 无回访无单且到期 → forfeited 不发分
    T7 结算幂等: 重跑空扫, unlocked/forfeited 不再变
    T8 熔断: 连续 3 轮解冻率 <90% → referralEscrowEnabled 自动 False
    T9 调度器惯例: GROWTH_ESCROW_AUTO 默认 off
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

import pytest

from services.promotion_service import PromotionService
from services.points_service import PointsService
from services.growth80_escrow_scheduler import (
    run_escrow_settlement, scheduler_enabled,
)
from repositories.promotion_repository import PromotionRepository
from repositories.member_repository import MemberRepository
from repositories.store import reset_store as _reset_store_impl


@pytest.fixture(autouse=True)
def _fresh_store():
    _reset_store_impl()
    yield
    _reset_store_impl()


async def _mk_member(phone, **extra):
    data = {
        "phone": phone, "nickname": f"会员{phone[-4:]}",
        "password": "x" * 64, "status": 1, "role": "member",
        "level": 3, "growth_value": 600, "points": 0,
        "created_at": datetime.now(UTC).isoformat(),
    }
    data.update(extra)
    return await MemberRepository().create(data)


async def _bind_new(inviter_id, phone):
    m = await _mk_member(phone)
    promo = PromotionService()
    code = (await promo.list_my_codes(inviter_id))[0]["code"]
    return await promo.bind_relation(code, m["id"]), m["id"]


async def _logs(member_id, source="traffic79"):
    return await PointsService().repo.list_logs(
        member_id, source=source, limit=200)


def test_t1_disabled_default_instant():
    """T1 默认关: 绑定即时发 300(现行零破坏)"""

    async def run():
        promo = PromotionService()
        inv = await _mk_member("13900000001")
        await promo.claim_promo_code(inv["id"], "douyin")
        r, m = await _bind_new(inv["id"], "13900000002")
        assert r["counted"] is True
        logs = await _logs(inv["id"])
        assert len(logs) == 1 and logs[0]["points"] == 300
        assert (await PromotionRepository().escrow_stats())["pending"] == 0

    asyncio.run(run())


def test_t2_enabled_escrow_pending():
    """T2 开关开: 不即时发/escrow pending/新人礼仍即时"""

    async def run():
        promo = PromotionService()
        await promo.update_settings({"referralEscrowEnabled": True},
                                    admin="t3")
        inv = await _mk_member("13900000011")
        await promo.claim_promo_code(inv["id"], "douyin")
        r, m = await _bind_new(inv["id"], "13900000012")
        assert r["counted"] is True
        assert await _logs(inv["id"]) == []          # 引进分未发
        stats = await PromotionRepository().escrow_stats()
        assert stats["pending"] == 1                 # escrow 建立
        # 新人礼仍即时(设计: 小额拉新体验优先)
        new_logs = await _logs(m, source="welcome80")
        assert len(new_logs) == 1 and new_logs[0]["points"] == 100

    asyncio.run(run())


def test_t3_dual_domain_idempotent():
    """T3 双域幂等: 即时轨已发 → 开 escrow 后同对不重复处理"""

    async def run():
        promo = PromotionService()
        inv = await _mk_member("13900000021")
        await promo.claim_promo_code(inv["id"], "douyin")
        r, m = await _bind_new(inv["id"], "13900000022")
        assert len(await _logs(inv["id"])) == 1
        # 切换 escrow 后再调发放(模拟开关切换场景)
        await promo.update_settings({"referralEscrowEnabled": True},
                                    admin="t3")
        again = await promo._award_referral_points(inv["id"], m)
        assert again is None
        assert (await PromotionRepository()
                .escrow_stats())["pending"] == 0

    asyncio.run(run())


async def _mk_due_escrow(inviter, invitee, revisit=None, order=None):
    """造一条已到期的 pending escrow(revisit/order 控制达标)"""
    promo = PromotionService()
    await promo.update_settings({"referralEscrowEnabled": True}, admin="t")
    r = await promo._award_referral_points(inviter, invitee)
    repo = PromotionRepository()
    escrow_id = r["escrowId"]
    await repo.update_escrow(escrow_id, {
        "deadline": (datetime.now(UTC) - timedelta(days=1)).isoformat()})
    if revisit is not None:
        await MemberRepository().update_fields(
            invitee, {"last_login_at": revisit})
    return escrow_id


def test_t4_unlock_by_revisit():
    """T4 回访解冻: last_login_at > createdAt → unlocked + 300 到账"""

    async def run():
        inv = await _mk_member("13900000031")
        m = await _mk_member("13900000032")
        escrow_id = await _mk_due_escrow(
            inv["id"], m["id"],
            revisit=(datetime.now(UTC) + timedelta(minutes=5)).isoformat())
        result = await run_escrow_settlement(force=True)
        assert result["unlocked"] == 1
        logs = await _logs(inv["id"])
        assert len(logs) == 1 and logs[0]["points"] == 300
        escrow = await PromotionRepository().get_escrow(escrow_id)
        assert escrow["state"] == "unlocked"

    asyncio.run(run())


def test_t5_unlock_by_order():
    """T5 首单解冻: get_by_member 非空 → unlocked"""

    async def run():
        inv = await _mk_member("13900000041")
        m = await _mk_member("13900000042")
        escrow_id = await _mk_due_escrow(inv["id"], m["id"])
        from repositories import order_repository as ormod
        orig = ormod.OrderRepository.get_by_member

        async def fake_get_by_member(self, member_id, status=None):
            return [{"orderId": "O1"}]
        ormod.OrderRepository.get_by_member = fake_get_by_member
        try:
            result = await run_escrow_settlement(force=True)
        finally:
            ormod.OrderRepository.get_by_member = orig
        assert result["unlocked"] == 1
        escrow = await PromotionRepository().get_escrow(escrow_id)
        assert escrow["state"] == "unlocked"
        assert escrow["reason"] == "首笔订单"

    asyncio.run(run())


def test_t6_forfeit_inactive():
    """T6 未活跃作废: 无回访无单到期 → forfeited 不发分不通知"""

    async def run():
        inv = await _mk_member("13900000051")
        m = await _mk_member("13900000052")
        escrow_id = await _mk_due_escrow(inv["id"], m["id"])
        result = await run_escrow_settlement(force=True)
        assert result["forfeited"] == 1
        assert await _logs(inv["id"]) == []
        escrow = await PromotionRepository().get_escrow(escrow_id)
        assert escrow["state"] == "forfeited"
        assert escrow["reason"] == "观察期满未活跃"

    asyncio.run(run())


def test_t7_settlement_idempotent():
    """T7 结算幂等: 重跑空扫, 状态不再变"""

    async def run():
        inv = await _mk_member("13900000061")
        m = await _mk_member("13900000062")
        await _mk_due_escrow(
            inv["id"], m["id"],
            revisit=(datetime.now(UTC) + timedelta(minutes=5)).isoformat())
        await run_escrow_settlement(force=True)
        r2 = await run_escrow_settlement(force=True)
        assert r2["unlocked"] == 0 and r2["forfeited"] == 0
        assert len(await _logs(inv["id"])) == 1   # 不重复发

    asyncio.run(run())


def test_t8_fuse_auto_disable():
    """T8 熔断: 连续 3 轮解冻率 <90% → 自动关 escrow"""

    async def run():
        from services import growth80_escrow_scheduler as sched
        promo = PromotionService()
        await promo.update_settings({"referralEscrowEnabled": True},
                                    admin="t8")
        # 造 5 条 settled(1 unlocked 4 forfeited → 20% 解冻率)
        repo = PromotionRepository()
        for i in range(5):
            await repo.save_escrow({
                "escrowId": await repo.next_escrow_id(),
                "userId": 900 + i, "source": "traffic79",
                "refId": f"traffic79:9{i}", "points": 300,
                "state": "unlocked" if i == 0 else "forfeited",
                "deadline": "", "createdAt": "2026-09-01T00:00:00+00:00",
                "settledAt": datetime.now(UTC).isoformat(), "reason": "t"})
        sched._fuse_streak = 0
        triggered = False
        for _ in range(3):
            triggered = await sched._fuse_check(repo, 0, 1)
        assert triggered is True
        s = await promo.get_settings()
        assert s["referralEscrowEnabled"] is False   # 自动回落

    asyncio.run(run())


def test_t9_scheduler_default_off():
    """T9 调度器惯例: GROWTH_ESCROW_AUTO 默认 off"""
    os.environ.pop("GROWTH_ESCROW_AUTO", None)
    assert scheduler_enabled() is False
    from services.growth80_escrow_scheduler import start_scheduler
    assert start_scheduler() is False
