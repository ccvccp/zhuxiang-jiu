"""45号·信值大模型 P6 专项——注册即开通(D1) + 信号入分/档位权益(D3/D4)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_trust45_p6.py -q

覆盖(P6 方案):
    T1 同值建档: create_role(trust_id=) 显式传入 + 冷启动首评
    T2 同值冲突: trustId 已占用 → ValueError(hook 幂等静默)
    T3 注册即开通: auth.register → trustId=memberId 档案 +
       条款同意留痕事件
    T4 fail-soft: 建档异常不阻断注册
    T5 实名升级: 占位摘要 → 真证件摘要 + 留痕; 证件冲突 → conflict
    T6 信号 shadow(默认): ingest_signal 留痕不改分
    T7 信号真入分: signalShadow=False → record_event 生效
    T8 限制矩阵: settings 关恒放行 / 开+critical 冻结兑换
    T9 修复 α 档位: settings 关 1.0 / 开+healthy 1.2
    T10 settings 白名单: 非法键拒绝
"""

import asyncio
import os

# 确保使用内存模式(45号 P0 测试同款环境)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["TRUST45_MODE"] = "assist"

import pytest

from repositories.store import reset_store as _reset_store_impl
from repositories.trust_value_repository import id_digest
from services import trust_p6_service as p6
from services.auth_service import AuthService
from services.trust_scoring_service import TrustProfileService


@pytest.fixture(autouse=True)
def _fresh_store():
    _reset_store_impl()
    yield
    _reset_store_impl()


def test_t1_same_value_role():
    """T1 同值建档: trust_id 显式传入, 冷启动基线出分"""

    async def run():
        svc = TrustProfileService()
        scored = await svc.create_role(
            "person", "同值测试", "member:901", trust_id=901)
        assert scored is not None
        rec = await svc.repo.get_profile(901)
        assert rec["trustId"] == 901 and rec["name"] == "同值测试"
        # 冷启动首评: L1=80/L2=50/L3=0 → 初始 55(watch)
        assert rec["score"] == 55.0 and rec["grade"] == "watch"

    asyncio.run(run())


def test_t2_same_value_conflict():
    """T2 同值冲突: trustId 已建档 → ValueError(hook 幂等静默依据)"""

    async def run():
        svc = TrustProfileService()
        await svc.create_role("person", "甲", "member:902",
                              trust_id=902)
        with pytest.raises(ValueError):
            await svc.create_role("person", "乙", "member:902x",
                                  trust_id=902)

    asyncio.run(run())


def test_t3_register_auto_open():
    """T3 注册即开通: trustId=memberId 同值 + 条款同意留痕"""

    async def run():
        auth = AuthService()
        reg = await auth.register("13900000911", "pass123456",
                                  age_confirmed=True)
        mid = reg["memberId"]
        svc = TrustProfileService()
        rec = await svc.repo.get_profile(mid)
        assert rec is not None, "注册未自动建档"
        assert rec["trustId"] == mid, "同值铁律未落地"
        # 条款同意留痕事件
        profile = await svc.get_profile(mid)
        events = profile.get("recentEvents") or []
        assert any(e.get("source") == "p6_terms_consent"
                   for e in events), "条款同意事件缺失"

    asyncio.run(run())


def test_t4_register_fail_soft(monkeypatch):
    """T4 fail-soft: 建档异常不阻断注册"""

    async def _boom(*a, **kw):
        raise RuntimeError("trust down")

    monkeypatch.setattr(
        TrustProfileService, "create_role", _boom)

    async def run():
        auth = AuthService()
        reg = await auth.register("13900000912", "pass123456")
        assert reg["success"] is True, "建档异常阻断了注册"

    asyncio.run(run())


def test_t5_realname_upgrade():
    """T5 实名升级: 摘要替换+留痕; 跨档案证件冲突 → conflict"""

    async def run():
        auth = AuthService()
        reg = await auth.register("13900000913", "pass123456")
        mid = reg["memberId"]
        svc = TrustProfileService()
        id_card = "110101199003078515"
        r = await svc.bind_real_digest(mid, id_card)
        assert r["status"] == "bound" and r["already"] is False
        rec = await svc.repo.get_profile(mid)
        assert rec["idDigest"] == id_digest(id_card), "摘要未升级"
        profile = await svc.get_profile(mid)
        events = profile.get("recentEvents") or []
        assert any(e.get("source") == "p6_realname_upgrade"
                   for e in events), "升级留痕缺失"
        # 幂等: 再绑同证件 → already
        r2 = await svc.bind_real_digest(mid, id_card)
        assert r2["status"] == "bound" and r2["already"] is True
        # 跨档案冲突: 另一会员绑同一证件 → conflict, 原档不变
        reg2 = await auth.register("13900000914", "pass123456")
        mid2 = reg2["memberId"]
        r3 = await svc.bind_real_digest(mid2, id_card)
        assert r3["status"] == "conflict"
        rec2 = await svc.repo.get_profile(mid2)
        assert rec2["idDigest"] == id_digest(f"member:{mid2}")

    asyncio.run(run())


# ============================================================
# T6-T10 D3 信号总线 / D4 档位权益(保守灰度口径)
# ============================================================

def test_t6_signal_shadow_default():
    """T6: 信号默认 shadow——留痕可回看, 分数不变"""

    async def run():
        svc = TrustProfileService()
        await svc.create_role("person", "信号测试", "member:905",
                              trust_id=905)
        before = (await svc.repo.get_profile(905))["score"]
        r = await p6.ingest_signal(905, "escrow_forfeit",
                                   summary="测试作废信号")
        assert r["shadow"] is True and r["applied"] is False
        after = (await svc.repo.get_profile(905))["score"]
        assert after == before, "shadow 不应改分"
        rows = await p6.shadow_signals(905)
        assert len(rows) == 1
        assert rows[0]["source"] == "escrow_forfeit"
        assert rows[0]["delta"] == p6.SIGNAL_SOURCES[
            "escrow_forfeit"]["delta"]

    asyncio.run(run())


def test_t7_signal_apply_when_unset_shadow():
    """T7: signalShadow=False → 真入分(record_event 链生效)"""

    async def run():
        svc = TrustProfileService()
        await svc.create_role("person", "入分测试", "member:906",
                              trust_id=906)
        await p6.update_p6_settings({"signalShadow": False},
                                    admin="test")
        r = await p6.ingest_signal(906, "escrow_forfeit")
        assert r["applied"] is True
        rec = await svc.repo.get_profile(906)
        assert rec["score"] < 55.0, "负向信号应扣分"
        profile = await svc.get_profile(906)
        events = profile.get("recentEvents") or []
        assert any(e.get("source") == "p6_escrow_forfeit"
                   for e in events)

    asyncio.run(run())


def test_t8_restrict_matrix():
    """T8: settings 关恒放行; 开+critical 冻结兑换"""

    async def run():
        svc = TrustProfileService()
        await svc.create_role("person", "限制测试", "member:907",
                              trust_id=907)
        # settings 默认关 → 恒放行
        v = await p6.restrict_check(907, "redeem")
        assert v["restricted"] is False
        # 开限制 + 压档位到 critical
        await p6.update_p6_settings({"restrictRedeem": True},
                                    admin="test")
        rec = await svc.repo.get_profile(907)
        rec["grade"] = "critical"
        await svc.repo.save_profile(rec)
        v2 = await p6.restrict_check(907, "redeem")
        assert v2["restricted"] is True
        assert "修复通道" in v2["reason"]
        # strained → 不冻结但上限系数 0.5
        rec["grade"] = "strained"
        await svc.repo.save_profile(rec)
        v3 = await p6.restrict_check(907, "redeem")
        assert v3["restricted"] is False
        assert v3.get("capFactor") == 0.5

    asyncio.run(run())


def test_t9_repair_alpha_benefit():
    """T9: 修复费率——settings 关 1.0; 开+healthy 1.2"""

    async def run():
        svc = TrustProfileService()
        await svc.create_role("person", "权益测试", "member:908",
                              trust_id=908)
        assert await p6.repair_alpha_factor(908) == 1.0
        await p6.update_p6_settings({"benefitRepairAlpha": True},
                                    admin="test")
        # watch 档 → 1.0(权益仅 healthy)
        assert await p6.repair_alpha_factor(908) == 1.0
        rec = await svc.repo.get_profile(908)
        rec["grade"] = "healthy"
        await svc.repo.save_profile(rec)
        assert await p6.repair_alpha_factor(908) == 1.2
        # 未建档 → fail-soft 1.0
        assert await p6.repair_alpha_factor(999999) == 1.0

    asyncio.run(run())


def test_t10_settings_whitelist():
    """T10: settings 白名单——非法键拒绝"""

    async def run():
        with pytest.raises(ValueError):
            await p6.update_p6_settings({"hackKey": True})
        # 合法键全量回读
        s = await p6.update_p6_settings({"restrictHelp": True},
                                        admin="test")
        assert s["restrictHelp"] is True
        assert s["signalShadow"] is True   # 未动键保持默认

    asyncio.run(run())


def test_t11_state_json_roundtrip():
    """T11: p6 字段 Redis 序列化往返(生产 str-append 崩 bug 回归锁)"""
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    repo = TrustValue45Repository()
    state = {
        "p6": {"signalShadow": True, "restrictRedeem": False},
        "p6ShadowSignals": [
            {"trustId": 1, "source": "escrow_forfeit"},
            {"trustId": 2, "source": "growth_anomaly"},
        ],
    }
    # Redis hash 字段口径: serialize(list/dict→json str) →
    # deserialize(json str→list/dict)
    restored = repo._deserialize(repo._serialize(state))
    assert restored["p6"] == state["p6"]
    assert restored["p6ShadowSignals"] == state["p6ShadowSignals"]
    # 空值边界
    empty = repo._deserialize(repo._serialize(
        {"p6": {}, "p6ShadowSignals": []}))
    assert empty["p6"] == {} and empty["p6ShadowSignals"] == []
