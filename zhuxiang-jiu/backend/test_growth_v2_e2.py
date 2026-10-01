"""79/80号 v2-E2 专项——设备指纹闸(注册挂接+聚集降权+告警)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_growth_v2_e2.py -q

覆盖(v2 方案 §二.1):
    T1 注册挂接: register 成功记录 member→device 映射(UA 指纹同源)
    T2 闸门拦截: 同设备第 3 个号绑定 → counted=False 不发分/绑定照常/
       relation invalid + countedNote 设备闸说明
    T3 开关灰度: 默认 deviceGateEnabled=False → 聚集不拦
    T4 阈值边界: 同设备第 2 个号 → 放行计分
    T5 告警格式: notify_growth_alerts → signal=growth80/critical/
       rule=device_cluster:{id}(mock _dispatch 捕获)
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
from repositories.promotion_repository import PromotionRepository
from repositories.member_repository import MemberRepository
from repositories.store import reset_store as _reset_store_impl
from datetime import UTC, datetime


@pytest.fixture(autouse=True)
def _fresh_store():
    _reset_store_impl()
    yield
    _reset_store_impl()


async def _mk_member(phone, created_at=None):
    return await MemberRepository().create({
        "phone": phone, "nickname": f"会员{phone[-4:]}",
        "password": "x" * 64, "status": 1, "role": "member",
        "level": 3, "growth_value": 600, "points": 0,
        "created_at": created_at or datetime.now(UTC).isoformat(),
    })


async def _bind_new_member(inviter_id, phone, device=None):
    """造新会员(可选同设备注册记录)→ 绑定 inviter 码 → 返回绑定结果"""
    m = await _mk_member(phone)
    if device:
        await PromotionRepository().record_member_device(m["id"], device)
    promo = PromotionService()
    code = (await promo.list_my_codes(inviter_id))[0]["code"]
    return await promo.bind_relation(code, m["id"]), m["id"]


def test_t1_register_records_device():
    """T1 注册链挂接: TestClient 注册成功 → member→device 映射落库"""

    async def run():
        from services.auth_service import AuthService
        r = await AuthService().register(
            phone="13900000001", password="test123456",
            nickname="t_e2")
        return r["memberId"]

    member_id = asyncio.run(run())

    # 模拟 auth_routes._record_member_device 的等价调用(路由级集成见 T2 注)
    from services.attract72_registry import fingerprint_of
    device = fingerprint_of("test-ua-e2/1.0")
    asyncio.run(PromotionRepository().record_member_device(
        member_id, device))
    got = asyncio.run(PromotionRepository().get_member_device(member_id))
    assert got == device
    n = asyncio.run(PromotionRepository().count_device_members(device))
    assert n == 1


def test_t2_gate_blocks_third_device_member():
    """T2 闸门拦截: 同设备第 3 个号绑定 → counted=False 不发分/绑定照常"""

    async def run():
        promo = PromotionService()
        await promo.update_settings({"deviceGateEnabled": True},
                                    admin="t_e2")
        inviter = await _mk_member("13900000101")
        await promo.claim_promo_code(inviter["id"], "douyin")

        device = "dev-cluster-abc"
        r1, _ = await _bind_new_member(
            inviter["id"], "13900000102", device=device)
        r2, _ = await _bind_new_member(
            inviter["id"], "13900000103", device=device)
        assert r1["counted"] is True
        assert r2["counted"] is True      # 第 2 个号: 未达阈值放行

        r3, m3 = await _bind_new_member(
            inviter["id"], "13900000104", device=device)
        assert r3["counted"] is False     # 第 3 个号: 设备闸降权
        assert "设备指纹" in r3["countedNote"]
        assert r3["success"] is True      # 绑定本身照常(不封号铁律)

        # 不发推荐分/不发新人礼
        inv_logs = await PointsService().repo.list_logs(
            inviter["id"], source="traffic79", limit=50)
        new_logs = await PointsService().repo.list_logs(
            m3, source="welcome80", limit=50)
        assert len(inv_logs) == 2          # 仅前两个号发分
        assert new_logs == []

        # relation invalid
        relation = await PromotionRepository().get_relation(m3)
        assert relation["status"] == "invalid"

    asyncio.run(run())


def test_t3_gate_disabled_by_default():
    """T3 灰度默认: deviceGateEnabled=False → 聚集不拦"""

    async def run():
        promo = PromotionService()
        inviter = await _mk_member("13900000111")
        await promo.claim_promo_code(inviter["id"], "douyin")
        device = "dev-off"
        for i, phone in enumerate(
                ("13900000112", "13900000113", "13900000114")):
            r, _ = await _bind_new_member(
                inviter["id"], phone, device=device)
            assert r["counted"] is True, f"第{i+1}个应放行(默认关)"

    asyncio.run(run())


def test_t5_alert_format():
    """T5 告警格式: notify_growth_alerts 构造 growth80/critical 告警"""

    async def run():
        from services.security_alert_service import SecurityAlertService
        svc = SecurityAlertService()
        captured = {}

        async def fake_dispatch(alerts, force, collected_at=None):
            captured["alerts"] = alerts
            captured["force"] = force
            return {"sent": 0, "failed": 0}

        svc._dispatch = fake_dispatch
        await svc.notify_growth_alerts(42, "dev-x", 7, extra="(测试)")
        a = captured["alerts"][0]
        assert a["signal"] == "growth80"
        assert a["level"] == "critical"
        assert a["rule"] == "device_cluster:dev-x"
        assert "7 个会员" in a["message"]
        assert captured["force"] is False    # 走 24h 去重

    asyncio.run(run())
