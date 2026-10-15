# -*- coding: utf-8 -*-
"""IMTM 定价因子最小闭环单测(growth80_pricing + scheduler 接入)

覆盖: 三档判定 / 与 v2-E3 口径等价 / 模式闸三态 / factors 快照 /
f(S_demand) / observe·enforce·off 三模式端到端结算
"""
import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

from services.growth80_pricing import (
    TIERS, classify_retention, compute_factors, demand_factor,
    multiplier_mode,
)
from services.growth80_escrow_scheduler import (
    _invitee_active, run_escrow_settlement,
)
from repositories.member_repository import MemberRepository
from repositories.order_repository import OrderRepository
from repositories.promotion_repository import PromotionRepository

ESCROW_CREATED = (datetime.now(UTC) - timedelta(days=9)).isoformat()
PAST_DEADLINE = (datetime.now(UTC) - timedelta(days=1)).isoformat()


def _mode(monkeypatch, value):
    monkeypatch.setenv("GROWTH_ESCROW_MULTIPLIER", value)


async def _mk_member(phone, **extra):
    data = {
        "phone": phone, "nickname": f"会员{phone[-4:]}",
        "password": "x" * 64, "status": 1, "role": "member",
        "level": 1, "growth_value": 0, "points": 0,
        "created_at": (datetime.now(UTC) - timedelta(days=10)).isoformat(),
        "last_login_at": (datetime.now(UTC) - timedelta(days=10)).isoformat(),
    }
    data.update(extra)
    return await MemberRepository().create(data)


def _escrow(mid, uid=88001, points=300):
    return {"refId": f"traffic79:{mid}", "userId": uid, "points": points,
            "createdAt": ESCROW_CREATED, "state": "pending"}


# ---------- T1 三档判定 ----------

def test_t1_tier_classification():
    """gold=首单 / base=回访或login≥3 / weak=login1-2 / none=零信号"""

    async def run():
        settings = {"escrowUnlockOrder": True}
        # gold: 有订单
        mg = await _mk_member("13900002001", login_count=0)
        await OrderRepository().create({
            "order_no": "T1G-001", "memberId": mg["id"],
            "status": "paid", "amount": 99.0})
        r = await classify_retention(_escrow(mg["id"]), settings)
        assert r["tier"] == "gold" and r["multiplier"] == 1.2
        assert r["signal"] == "首笔订单"
        # base: login≥3 无订单
        mb = await _mk_member("13900002002", login_count=3)
        r = await classify_retention(_escrow(mb["id"]), settings)
        assert r["tier"] == "base" and r["multiplier"] == 1.0
        # base: 回访(last_login 晚于 escrow createdAt) 且无订单
        mr = await _mk_member("13900002003", login_count=0,
                              last_login_at=datetime.now(UTC).isoformat())
        r = await classify_retention(_escrow(mr["id"]), settings)
        assert r["tier"] == "base"
        # weak: login 1-2, 无回访无订单
        mw = await _mk_member("13900002004", login_count=2)
        r = await classify_retention(_escrow(mw["id"]), settings)
        assert r["tier"] == "weak" and r["multiplier"] == 0.8
        assert r["eligible_now"] is False      # observe 口径仍不达标
        assert r["eligible_enforced"] is True  # enforce 口径转解冻
        # none: 零信号
        mn = await _mk_member("13900002005", login_count=0)
        r = await classify_retention(_escrow(mn["id"]), settings)
        assert r["tier"] == "none" and r["multiplier"] == 0.0
        assert r["eligible_enforced"] is False

    asyncio.run(run())


# ---------- T2 与 v2-E3 口径等价 ----------

def test_t2_eligible_now_equals_v2e3():
    """observe 达标口径(tier in gold/base)与 _invitee_active 完全等价"""

    async def run():
        settings = {"escrowUnlockOrder": True}
        cases = {"13900002101": 0, "13900002102": 3,
                 "13900002103": 2, "13900002104": 5}
        for phone, lc in cases.items():
            m = await _mk_member(phone, login_count=lc)
            esc = _escrow(m["id"])
            ok_old, _ = await _invitee_active(esc, settings)
            cls = await classify_retention(esc, settings)
            assert ok_old == cls["eligible_now"], \
                f"login={lc} 口径分歧: old={ok_old} tier={cls['tier']}"

    asyncio.run(run())


# ---------- T3 模式闸 ----------

def test_t3_mode_gate(monkeypatch):
    monkeypatch.delenv("GROWTH_ESCROW_MULTIPLIER", raising=False)
    assert multiplier_mode() == "observe"          # 默认 observe
    _mode(monkeypatch, "off")
    assert multiplier_mode() == "off"
    _mode(monkeypatch, "enforce")
    assert multiplier_mode() == "enforce"
    _mode(monkeypatch, "garbage")
    assert multiplier_mode() == "observe"          # 非法值回落


# ---------- T4 factors 快照 ----------

def test_t4_factors_snapshot(monkeypatch):
    """observe: 实发原值+would_be 假想值; enforce: unlockPoints=乘数值"""

    async def run():
        class FakeRepo:
            async def list_escrows(self, **kw):
                return []      # 小样本 → fSDemand=1.0

        settings = {"escrowUnlockOrder": True}
        mg = await _mk_member("13900002201", login_count=0)
        await OrderRepository().create({
            "order_no": "T4G-001", "memberId": mg["id"],
            "status": "paid", "amount": 50.0})
        esc = _escrow(mg["id"], points=300)
        cls = await classify_retention(esc, settings)

        _mode(monkeypatch, "observe")
        f = await compute_factors(FakeRepo(), esc, cls)
        assert f["schema"] == "imtm_pricing_v1"
        assert f["mGame"]["tier"] == "gold"
        assert f["unlockPoints"] == 300      # observe 分值不变
        assert f["wouldBePoints"] == 360     # enforce 假想值(300×1.2)
        assert f["fSDemand"] == 1.0
        assert f["fQUser"] == 1.0 and f["fPRisk"] == 1.0
        assert f["finalMultiplier"] == 1.0   # observe 不生效

        _mode(monkeypatch, "enforce")
        f2 = await compute_factors(FakeRepo(), esc, cls)
        assert f2["unlockPoints"] == 360     # enforce 生效
        assert f2["finalMultiplier"] == 1.2

    asyncio.run(run())


# ---------- T5 f(S_demand) ----------

def test_t5_demand_factor():
    """作废率线性惩罚 + 地板 0.5 + 小样本跳过"""

    class FakeRepo:
        def __init__(self, rows):
            self.rows = rows

        async def list_escrows(self, **kw):
            return self.rows

    def _row(state, days_ago=1):
        return {"state": state, "settledAt": (
            datetime.now(UTC) - timedelta(days=days_ago)).isoformat()}

    async def run():
        # 5 条全作废 → 地板 0.5
        assert await demand_factor(
            FakeRepo([_row("forfeited")] * 5), {"userId": 1}) == 0.5
        # 10 条 3 作废 → 0.7
        rows = [_row("forfeited")] * 3 + [_row("unlocked")] * 7
        assert await demand_factor(FakeRepo(rows), {"userId": 1}) == 0.7
        # 4 条(样本<5) → 跳过 1.0
        assert await demand_factor(
            FakeRepo([_row("forfeited")] * 4), {"userId": 1}) == 1.0
        # 7 天外的作废不计入
        rows = [_row("forfeited", days_ago=9)] * 5 + [_row("unlocked")] * 5
        assert await demand_factor(FakeRepo(rows), {"userId": 1}) == 1.0

    asyncio.run(run())


# ---------- T6/T7/T8 三模式端到端 ----------

async def _mk_pending_escrow(repo, phone, points=300, **member_extra):
    m = await _mk_member(phone, **member_extra)
    esc = await repo.save_escrow({
        "escrowId": await repo.next_escrow_id(),
        "userId": 88001, "inviteeId": m["id"], "points": points,
        "source": "traffic79", "refId": f"traffic79:{m['id']}",
        "state": "pending", "deadline": PAST_DEADLINE,
        "createdAt": ESCROW_CREATED,
    })
    return esc, m


def test_t6_observe_e2e(monkeypatch):
    """observe: gold 解冻原值+factors 快照; weak 仍 forfeited 但留痕"""

    async def run():
        _mode(monkeypatch, "observe")
        repo = PromotionRepository()
        e_gold, _ = await _mk_pending_escrow(repo, "13900002301")
        await OrderRepository().create({
            "order_no": "T6G-001", "memberId": e_gold["inviteeId"],
            "status": "paid", "amount": 10.0})
        e_weak, _ = await _mk_pending_escrow(
            repo, "13900002302", login_count=2)

        r = await run_escrow_settlement(force=True)
        assert r["unlocked"] >= 1 and r["forfeited"] >= 1

        g = await repo.get_escrow(e_gold["escrowId"])
        assert g["state"] == "unlocked"
        assert g["settledPoints"] == 300                 # 分值不变
        assert g["factors"]["mGame"]["tier"] == "gold"   # 快照在
        assert g["factors"]["mode"] == "observe"
        assert g["factors"]["wouldBePoints"] == 360

        w = await repo.get_escrow(e_weak["escrowId"])
        assert w["state"] == "forfeited"                 # 行为不变
        assert w["factors"]["mGame"]["tier"] == "weak"   # 档位留痕
        assert w["factors"]["wouldBePoints"] == 240      # 假想值留痕

    asyncio.run(run())


def test_t7_enforce_e2e(monkeypatch):
    """enforce: gold 解冻 360 / weak 转 80% 解冻 240 / none 维持没收"""

    async def run():
        _mode(monkeypatch, "enforce")
        repo = PromotionRepository()
        e_gold, _ = await _mk_pending_escrow(repo, "13900002401")
        await OrderRepository().create({
            "order_no": "T7G-001", "memberId": e_gold["inviteeId"],
            "status": "paid", "amount": 10.0})
        e_weak, _ = await _mk_pending_escrow(
            repo, "13900002402", login_count=1)
        e_none, _ = await _mk_pending_escrow(repo, "13900002403")

        r = await run_escrow_settlement(force=True)
        assert r["unlocked"] >= 2 and r["forfeited"] >= 1

        g = await repo.get_escrow(e_gold["escrowId"])
        assert g["state"] == "unlocked" and g["settledPoints"] == 360
        w = await repo.get_escrow(e_weak["escrowId"])
        assert w["state"] == "unlocked" and w["settledPoints"] == 240
        n = await repo.get_escrow(e_none["escrowId"])
        assert n["state"] == "forfeited"

    asyncio.run(run())


def test_t8_off_bit_identical(monkeypatch):
    """off: 无 factors 字段、无 settledPoints、行为与 v2-E3 一致"""

    async def run():
        _mode(monkeypatch, "off")
        repo = PromotionRepository()
        e_base, _ = await _mk_pending_escrow(
            repo, "13900002501", login_count=4)

        await run_escrow_settlement(force=True)
        b = await repo.get_escrow(e_base["escrowId"])
        assert b["state"] == "unlocked"
        assert "factors" not in b            # 零留痕
        assert "settledPoints" not in b      # 零新字段

    asyncio.run(run())
