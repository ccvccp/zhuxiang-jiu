"""80号·全域会员智能增长 P1 专项——分享积分轨+被邀新人礼

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_growth80_p1.py -q

覆盖(P1 启动评估方案 §三 实施路径):
    T1 分享计分: 上报→20 分+流水(refId={dateKey}:product:{id})
    T2 日限: 第 6 次(默认 5/日)不计分
    T3 同内容幂等: 同 item 当日第二次 counted=False
    T4 开关: sharePointsPerAction=0 → ValueError(409 口径)
    T5 新人礼全链: 绑定计业绩→引荐人 300(79)+被邀人 100(80)双轨
    T6 新人礼幂等: award_newcomer 重复调用不发二次
    T7 观测面: today_share_count(次数/上限/剩余)
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

import pytest

from services.growth80_service import (
    Growth80Service, SHARE_SOURCE, WELCOME_SOURCE,
)
from services.promotion_service import PromotionService
from services.points_service import PointsService
from services.message_service import MessageService
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


async def _logs(member_id, source):
    return await PointsService().repo.list_logs(
        member_id, source=source, limit=200)


def test_t1_share_points_award():
    """T1 分享计分: 首次上报 20 分 + 流水格式正确"""

    async def run():
        a = await _mk_member("13900000001")
        r = await Growth80Service().report_share(
            a["id"], item_type="product", item_id="P100")
        assert r["counted"] is True and r["points"] == 20
        assert r["todayCount"] == 1

        logs = await _logs(a["id"], SHARE_SOURCE)
        assert len(logs) == 1 and logs[0]["points"] == 20
        # refId 格式: {dateKey}:{itemType}:{itemId}
        from services.growth80_service import _date_key
        assert logs[0]["refId"] == f"{_date_key()}:product:P100"

    asyncio.run(run())


def test_t2_daily_limit():
    """T2 日限: 默认 5 次/日, 第 6 次不计"""

    async def run():
        a = await _mk_member("13900000011")
        svc = Growth80Service()
        for i in range(5):
            r = await svc.report_share(a["id"], "product", f"P{i}")
            assert r["counted"] is True, f"第{i+1}次应计分"
        r6 = await svc.report_share(a["id"], "product", "P5")
        assert r6["counted"] is False
        assert "上限" in r6["reason"]

    asyncio.run(run())


def test_t3_same_item_idempotent():
    """T3 同内容当日幂等: 同 item 二次 counted=False, 其他 item 正常"""

    async def run():
        a = await _mk_member("13900000021")
        svc = Growth80Service()
        r1 = await svc.report_share(a["id"], "product", "PX")
        r2 = await svc.report_share(a["id"], "product", "PX")
        assert r1["counted"] is True
        assert r2["counted"] is False and "已计分" in r2["reason"]
        # 其他 item 不受影响
        r3 = await svc.report_share(a["id"], "promo_code", "ZXBJ-AAA")
        assert r3["counted"] is True

    asyncio.run(run())


def test_t4_share_disabled():
    """T4 开关: sharePointsPerAction=0 → ValueError"""

    async def run():
        a = await _mk_member("13900000031")
        await PromotionService().update_settings(
            {"sharePointsPerAction": 0}, admin="t80")
        with pytest.raises(ValueError, match="未开启"):
            await Growth80Service().report_share(a["id"], "product", "P1")

    asyncio.run(run())


def test_t5_newcomer_full_chain():
    """T5 新人礼全链: 绑定计业绩 → 引荐人 300(79) + 被邀人 100(80)"""

    async def run():
        promo = PromotionService()
        a = await _mk_member("13900000041")
        b = await _mk_member("13900000042")
        r = await promo.claim_promo_code(a["id"], "douyin")

        await promo.bind_relation(r["code"], b["id"])

        inviter_logs = await _logs(a["id"], "traffic79")
        invitee_logs = await _logs(b["id"], WELCOME_SOURCE)
        assert len(inviter_logs) == 1 \
            and inviter_logs[0]["points"] == 300
        assert len(invitee_logs) == 1 \
            and invitee_logs[0]["points"] == 100
        assert invitee_logs[0]["refId"] == f"welcome:{b['id']}"

    asyncio.run(run())


def test_t6_newcomer_idempotent():
    """T6 新人礼幂等: 重复调用不发二次"""

    async def run():
        b = await _mk_member("13900000051")
        first = await Growth80Service().award_newcomer(b["id"])
        assert first is not None and first["points"] == 100
        second = await Growth80Service().award_newcomer(b["id"])
        assert second is None
        logs = await _logs(b["id"], WELCOME_SOURCE)
        assert len(logs) == 1

    asyncio.run(run())


def test_t7_today_share_count():
    """T7 观测面: today_share_count 汇总正确"""

    async def run():
        a = await _mk_member("13900000061")
        svc = Growth80Service()
        await svc.report_share(a["id"], "product", "P1")
        await svc.report_share(a["id"], "product", "P2")
        t = await svc.today_share_count(a["id"])
        assert t["todayCount"] == 2
        assert t["dailyLimit"] == 5
        assert t["remaining"] == 3
        assert t["pointsPerAction"] == 20

    asyncio.run(run())
