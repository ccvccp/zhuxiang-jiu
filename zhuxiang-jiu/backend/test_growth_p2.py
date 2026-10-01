"""79/80号 P2 专项——LLM 动态文案 + login_count 精确计数 + EMA 基线

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_growth_p2.py -q

覆盖(v2 方案文末 P2 增量 + 79号方案 P2 文案项):
    T1 LLM 动态文案: chat mock → engine=llm, 提示词含平台/利益点
    T2 缓存命中: 同键二次调用 → cached=True 且 LLM 只调一次
    T3 LLM 失败降级: chat 异常 → engine=static(_share_tip 同文案)
    T4 login_count 计数: 注册 0 → 密码登录两次 2
    T5 E3 解冻判定: login_count≥3 → 达标(登录N次); <3 不达标
    T6 EMA 三态: 无基线首波 PASS / 基线3+突增21 WARN /
       高基线不误报 / 绝对101 FAIL
"""

import asyncio
import os

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

import pytest

from services.auth_service import AuthService
from services.growth80_escrow_scheduler import _invitee_active
from services.growth80_service import Growth80Service, _copy_cache
from services.ibms_patrol_service import check_growth_anomalies
from services.promotion_service import PromotionService
from repositories.member_repository import MemberRepository
from repositories.promotion_repository import PromotionRepository
from repositories.store import reset_store as _reset_store_impl
from datetime import UTC, datetime, timedelta


@pytest.fixture(autouse=True)
def _fresh_store():
    _reset_store_impl()
    _copy_cache.clear()
    yield
    _reset_store_impl()
    _copy_cache.clear()


LLM_COPY = "竹香酒新朋友经推广码注册即得100积分新人礼，好酒值得分享"


def _mock_llm(monkeypatch, reply=LLM_COPY, fail=False):
    """mock provider_client.chat(函数内 import 单例——实例属性遮蔽)"""
    import services.llm_client as llm_mod
    calls = {"n": 0, "user": ""}

    def fake_chat(system, user, **kw):
        calls["n"] += 1
        calls["user"] = user
        if fail:
            raise RuntimeError("llm down")
        return reply

    monkeypatch.setattr(llm_mod.provider_client, "chat", fake_chat)
    return calls


def test_t1_llm_copy_generated(monkeypatch):
    """T1 LLM 文案: engine=llm, 提示词含平台中文名与利益点"""
    calls = _mock_llm(monkeypatch)

    async def run():
        r = await Growth80Service().generate_share_copy(
            9001, item_type="product", item_id="ZXBJ-P2A",
            channel="douyin")
        assert r["engine"] == "llm" and r["cached"] is False
        assert r["copy"] == LLM_COPY
        assert calls["n"] == 1
        assert "抖音" in calls["user"]          # CHANNEL_LABEL 平台映射
        assert "新人礼" in calls["user"]        # 利益点要素

    asyncio.run(run())


def test_t2_cache_hit(monkeypatch):
    """T2 缓存: 同渠道同内容二次调用 cached=True, LLM 只调一次"""
    calls = _mock_llm(monkeypatch)

    async def run():
        svc = Growth80Service()
        r1 = await svc.generate_share_copy(
            9002, item_type="product", item_id="ZXBJ-P2B",
            channel="kuaishou")
        r2 = await svc.generate_share_copy(
            9002, item_type="product", item_id="ZXBJ-P2B",
            channel="kuaishou")
        assert r1["cached"] is False and r2["cached"] is True
        assert r2["copy"] == r1["copy"] == LLM_COPY
        assert calls["n"] == 1

    asyncio.run(run())


def test_t3_llm_fail_fallback(monkeypatch):
    """T3 降级: LLM 异常 → engine=static, 文案同 _share_tip"""
    _mock_llm(monkeypatch, fail=True)

    async def run():
        r = await Growth80Service().generate_share_copy(
            9003, item_type="promo_code", item_id="ZXBJ-P2C",
            channel="xiaohongshu")
        assert r["engine"] == "static" and r["cached"] is False
        assert r["copy"] == PromotionService._share_tip(
            "xiaohongshu", "ZXBJ-P2C")

    asyncio.run(run())


def test_t4_login_count():
    """T4 计数: 注册 0 → 密码登录两次 2"""

    async def run():
        auth = AuthService()
        reg = await auth.register("13900000901", "pass123456")
        mid = reg["memberId"]
        member = await MemberRepository().get_by_id(mid)
        assert member.get("login_count") == 0

        await auth.login("13900000901", "pass123456")
        await auth.login("13900000901", "pass123456")
        member = await MemberRepository().get_by_id(mid)
        assert member.get("login_count") == 2

    asyncio.run(run())


async def _mk_member(phone, **extra):
    data = {
        "phone": phone, "nickname": f"会员{phone[-4:]}",
        "password": "x" * 64, "status": 1, "role": "member",
        "level": 1, "growth_value": 0, "points": 0,
        "created_at": (datetime.now(UTC)
                       - timedelta(days=10)).isoformat(),
        "last_login_at": (datetime.now(UTC)
                          - timedelta(days=10)).isoformat(),
    }
    data.update(extra)
    return await MemberRepository().create(data)


def test_t5_escrow_login_count_gate():
    """T5 E3 判定: login_count≥3 达标; <3 不达标(回访口不触发)"""

    async def run():
        old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        settings = {"escrowUnlockOrder": True}

        m3 = await _mk_member("13900000902", login_count=3)
        ok, why = await _invitee_active(
            {"refId": f"traffic79:{m3['id']}", "userId": 88001,
             "createdAt": old}, settings)
        assert ok is True and "登录3次" in why

        m2 = await _mk_member("13900000903", login_count=2)
        ok2, why2 = await _invitee_active(
            {"refId": f"traffic79:{m2['id']}", "userId": 88001,
             "createdAt": old}, settings)
        assert ok2 is False

    asyncio.run(run())


def test_t6_ema_states():
    """T6 EMA 三态: 无基线 PASS / 基线3+突增21 WARN /
    高基线不误报 / 绝对101 FAIL"""

    async def run():
        repo = PromotionRepository()
        now = datetime.now(UTC)
        base_when = (now - timedelta(days=3)).isoformat()

        async def _rel(inviter, invitee, when):
            await repo.save_relation({
                "inviteeMemberId": invitee, "inviterMemberId": inviter,
                "code": "ZXBJ-P2E", "channel": "direct",
                "status": "valid", "createdAt": when})

        # a) 无基线新推荐人首波 25 条(<50) → PASS(EMA 无基线不判)
        for i in range(25):
            await _rel(70001, 60100 + i, now.isoformat())
        ra = await check_growth_anomalies()
        assert ra["state"] == "PASS"

        # b) 基线 3 条 + 24h 突增 21 条(>日均0.4×10 且 >20) → WARN
        for i in range(3):
            await _rel(70002, 60200 + i, base_when)
        for i in range(21):
            await _rel(70002, 60300 + i, now.isoformat())
        rb = await check_growth_anomalies()
        assert rb["state"] == "WARN"
        assert "70002" in rb["message"] and "EMA" in rb["message"]

        # c) 高基线不误报: 7 天 70 条(日均10) + 今日 30(<100) → 非嫌疑
        for i in range(70):
            await _rel(70003, 60400 + i, base_when)
        for i in range(30):
            await _rel(70003, 60500 + i, now.isoformat())
        rc = await check_growth_anomalies()
        assert "70003" not in rc["message"]

        # d) 绝对阈值: 101 条 → FAIL
        for i in range(101):
            await _rel(70004, 60600 + i, now.isoformat())
        rd = await check_growth_anomalies()
        assert rd["state"] == "FAIL"

    asyncio.run(run())
