"""79号·网站会员流量智能模块 P1 专项——引进注册积分轨+站内信+会员漏斗

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_traffic79_p1.py -q

覆盖(P1 启动评估方案 §三 实施路径):
    T1 全链: 领码→resolve_click→attach_registration→绑定 counted
       +300 积分(refId=traffic79:{invitee})+站内信+归因记录
       (生产真实链路: auth 注册归并→bind_relation→_award_referral_points)
    T2 幂等: _award_referral_points 重复调用不发二次(流水查重)
    T3 老会员: 注册>24h 绑定 counted=False 不发积分
    T4 开关: pointsPerReferral=0 关停 → 不发; 恢复后新绑定发
    T5 参数面: update_settings 修改生效 / 负值拒绝(409 口径)
    T6 漏斗: get_my_funnel 码点击/注册/积分/转化率汇总
    T7 钱包轨并行: 积分轨与 level1 钱包奖励同触发互不影响
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

import pytest

from services.promotion_service import PromotionService
from services.points_service import PointsService
from services.message_service import MessageService
from services.attract_service import AttractService
from repositories.member_repository import MemberRepository
from repositories.attract_repository import AttractRepository
from repositories.store import _mock_store, reset_store as _reset_store_impl
from datetime import UTC, datetime, timedelta


@pytest.fixture(autouse=True)
def _fresh_store():
    _reset_store_impl()
    yield
    _reset_store_impl()


async def _mk_member(phone, created_at=None):
    """创建测试会员(默认注册时间=现在即'新人')"""
    return await MemberRepository().create({
        "phone": phone, "nickname": f"会员{phone[-4:]}",
        "password": "x" * 64, "status": 1, "role": "member",
        "level": 3, "growth_value": 600, "points": 0,
        "created_at": created_at or datetime.now(UTC).isoformat(),
    })


async def _logs(member_id):
    return await PointsService().repo.list_logs(
        member_id, source="traffic79", limit=200)


async def _inmails(member_id):
    return await MessageService().list_messages(
        member_id, "inmail", None, None, 20)


def test_t1_full_chain_referral_points():
    """T1 全链: 点击→注册归并→绑定→300 积分+站内信+归因"""

    async def run():
        promo = PromotionService()
        a = await _mk_member("13900000001")
        b = await _mk_member("13900000002")
        r = await promo.claim_promo_code(a["id"], "douyin")
        code = r["code"]

        # 短码点击(attract 链)
        click = await AttractService().resolve_click(
            code, utm_source="douyin", ip="1.2.3.4",
            user_agent="test-ua")
        assert click["clickId"] > 0
        assert click["codeType"] == "promotion"

        # 注册归并(生产链路: auth 注册后自动调)
        attr = await AttractService().attach_registration(
            click["clickId"], b["id"])
        assert attr["memberId"] == b["id"]

        # 300 积分到账(refId 幂等标记)
        logs = await _logs(a["id"])
        assert len(logs) == 1, f"应发 1 条流水, 实际 {len(logs)}"
        assert logs[0]["points"] == 300
        assert logs[0]["refId"] == f"traffic79:{b['id']}"

        # 站内信 1 条(标题含推荐奖励)
        mails = await _inmails(a["id"])
        assert len(mails) == 1, f"应发 1 条站内信, 实际 {len(mails)}"
        assert "300" in mails[0]["title"] or "积分" in mails[0]["title"] \
            or "积分" in mails[0]["content"]

        # 引荐人积分账户余额
        account = await PointsService().repo.get_account(a["id"])
        assert account["totalPoints"] == 300

    asyncio.run(run())


def test_t2_idempotent_no_double_award():
    """T2 幂等: 重复调 _award_referral_points 不发二次"""

    async def run():
        promo = PromotionService()
        a = await _mk_member("13900000011")
        b = await _mk_member("13900000012")
        await promo.claim_promo_code(a["id"], "direct")

        first = await promo._award_referral_points(a["id"], b["id"])
        assert first is not None and first["points"] == 300

        # 重复调用(模拟异常重入): 流水查重拦截
        second = await promo._award_referral_points(a["id"], b["id"])
        assert second is None
        logs = await _logs(a["id"])
        assert len(logs) == 1

    asyncio.run(run())


def test_t3_old_member_not_counted():
    """T3 老会员(注册>24h)绑定不计业绩不发积分"""

    async def run():
        promo = PromotionService()
        a = await _mk_member("13900000021")
        old = await _mk_member(
            "13900000022",
            created_at=(datetime.now(UTC) - timedelta(hours=25)).isoformat())
        r = await promo.claim_promo_code(a["id"], "direct")

        result = await promo.bind_relation(r["code"], old["id"])
        assert result["counted"] is False

        logs = await _logs(a["id"])
        assert logs == [], "老会员绑定不应发积分"

    asyncio.run(run())


def test_t4_points_switch_off_on():
    """T4 开关: pointsPerReferral=0 关停; 恢复后新绑定发"""

    async def run():
        promo = PromotionService()
        a = await _mk_member("13900000031")
        await promo.claim_promo_code(a["id"], "direct")
        await promo.update_settings({"pointsPerReferral": 0},
                                    admin="t79")

        b = await _mk_member("13900000032")
        br = await _mk_member("13900000033")
        code_b = (await promo.list_my_codes(a["id"]))[0]["code"]
        await promo.bind_relation(code_b, b["id"])
        assert await _logs(a["id"]) == [], "关停期绑定不应发积分"

        await promo.update_settings({"pointsPerReferral": 300},
                                    admin="t79")
        await promo.bind_relation(code_b, br["id"])   # br 也绑同码
        logs = await _logs(a["id"])
        assert len(logs) == 1 and logs[0]["points"] == 300

    asyncio.run(run())


def test_t5_settings_validation():
    """T5 参数面: 修改生效/负值拒绝"""

    async def run():
        promo = PromotionService()
        s = await promo.get_settings()
        assert s["pointsPerReferral"] == 300   # DEFAULT 默认

        r = await promo.update_settings({"pointsPerReferral": 500},
                                        admin="t79")
        assert r["pointsPerReferral"] == 500

        with pytest.raises(ValueError, match="引进积分"):
            await promo.update_settings({"pointsPerReferral": -1},
                                        admin="t79")

    asyncio.run(run())


def test_t6_my_funnel():
    """T6 漏斗: 码点击/注册/积分/转化率汇总"""

    async def run():
        promo = PromotionService()
        a = await _mk_member("13900000041")
        b = await _mk_member("13900000042")
        r = await promo.claim_promo_code(a["id"], "douyin")
        code = r["code"]

        # 造点击(2 次)+注册归并(1 次)
        c1 = await AttractService().resolve_click(code, utm_source="douyin")
        c2 = await AttractService().resolve_click(code, utm_source="douyin")
        await AttractService().attach_registration(c1["clickId"], b["id"])

        funnel = await promo.get_my_funnel(a["id"])
        assert funnel["totals"]["codes"] == 1
        assert funnel["totals"]["clicks"] == 2
        assert funnel["totals"]["registered"] == 1
        assert funnel["totals"]["pointsEarned"] == 300
        assert funnel["totals"]["conversionRate"] == 0.5
        row = funnel["codes"][0]
        assert row["code"] == code
        assert row["clicks"] == 2 and row["registered"] == 1
        assert "分享" in row["shareTip"] or row["shareTip"]

    asyncio.run(run())


def test_t7_wallet_track_parallel():
    """T7 钱包轨并行: 积分轨与 level1 钱包奖励同触发互不影响"""

    async def run():
        promo = PromotionService()
        a = await _mk_member("13900000051")
        b = await _mk_member("13900000052")
        # 钱包轨前提: 引荐人开通钱包(deposit_reward 要求)
        from services.wallet_service import WalletService
        await WalletService().open(a["id"])
        # 阈值调 1: 单次绑定即同时触发钱包轮
        await promo.update_settings({"level1Threshold": 1,
                                     "level1RewardAmount": 20},
                                    admin="t79")
        r = await promo.claim_promo_code(a["id"], "direct")

        result = await promo.bind_relation(r["code"], b["id"])
        assert result["counted"] is True

        # 积分轨: 300
        logs = await _logs(a["id"])
        assert len(logs) == 1 and logs[0]["points"] == 300

        # 钱包轨: 20 元奖励余额(并行不冲突)
        rewards = await promo.list_my_rewards(a["id"])
        wallet_rewards = [x for x in rewards
                          if x.get("rewardType") == "wallet"]
        assert len(wallet_rewards) == 1
        assert wallet_rewards[0]["amount"] == 20.0

    asyncio.run(run())
