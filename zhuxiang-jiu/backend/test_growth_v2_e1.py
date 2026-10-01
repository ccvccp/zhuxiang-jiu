"""79/80号 v2-E1 专项——管理端积分追回端点

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_growth_v2_e1.py -q

覆盖(v2 方案 §二.3):
    T1 追回成功: 负流水(type=spend/source=admin_revoke)+余额扣减
    T2 同源幂等: 同 refId 二次追回 → ValueError(409 口径)
    T3 欠口不透支: 余额不足扣至 0, shortfall 记录且流水说明含欠口
    T4 路由守卫: confirm=false 409 / 无 X-Role 403 / 正常 200
    T5 参数校验: points<=0 → ValueError
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

import pytest

from services.points_service import PointsService
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


def test_t1_revoke_success():
    """T1 追回成功: 发 300 → 追回 200 → 余额 100 + 负流水"""

    async def run():
        a = await _mk_member("13900000001")
        await PointsService().earn_points(
            a["id"], 300, source="traffic79", ref_id="traffic79:999")
        r = await PointsService().revoke_points(
            a["id"], 200, ref_id="traffic79:999",
            ref_desc="刷单追回(工单#T1)")
        assert r["revoked"] == 200
        assert r["shortfall"] == 0
        assert r["balance"] == 100

        logs = await PointsService().repo.list_logs(
            a["id"], source="admin_revoke", limit=10)
        assert len(logs) == 1
        assert logs[0]["type"] == "spend"
        assert logs[0]["points"] == -200
        assert logs[0]["refId"] == "traffic79:999"
        assert logs[0]["balance"] == 100

    asyncio.run(run())


def test_t2_idempotent_same_refid():
    """T2 同源幂等: 同 refId 二次追回被拒; 不同 refId 正常"""

    async def run():
        a = await _mk_member("13900000011")
        await PointsService().earn_points(
            a["id"], 300, source="traffic79", ref_id="traffic79:888")
        first = await PointsService().revoke_points(
            a["id"], 200, ref_id="traffic79:888")
        assert first["revoked"] == 200
        with pytest.raises(ValueError, match="已追回过"):
            await PointsService().revoke_points(
                a["id"], 100, ref_id="traffic79:888")
        # 不同 refId 不受影响(余额 100 够扣)
        other = await PointsService().revoke_points(
            a["id"], 10, ref_id="share80:2026-10-01:product:PX")
        assert other["revoked"] == 10

    asyncio.run(run())


def test_t3_shortfall_no_overdraft():
    """T3 欠口: 余额 100 追回 400 → 实扣 100 欠 300, 余额 0 非负"""

    async def run():
        a = await _mk_member("13900000021")
        await PointsService().earn_points(
            a["id"], 100, source="welcome80", ref_id="welcome:1")
        r = await PointsService().revoke_points(
            a["id"], 400, ref_id="welcome:1", ref_desc="团伙追回")
        assert r["revoked"] == 100
        assert r["shortfall"] == 300
        assert r["balance"] == 0

        logs = await PointsService().repo.list_logs(
            a["id"], source="admin_revoke", limit=10)
        assert "欠口300" in logs[0]["refDesc"]

        # 追回后再发分→新余额从 0 起(欠口不自动扣)
        await PointsService().earn_points(
            a["id"], 50, source="share80", ref_id="d:1")
        acct = await PointsService().repo.get_account(a["id"])
        assert acct["totalPoints"] == 50

    asyncio.run(run())


def test_t4_route_guards():
    """T4 路由守卫: confirm 409 / 无 admin 403 / 正常 200"""

    import asyncio as _a
    member_id = _a.run(_mk_member("13900000031"))["id"]
    _a.run(PointsService().earn_points(
        member_id, 300, source="traffic79", ref_id="traffic79:777"))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.promotion_routes import register_promotion_routes
    app = FastAPI()
    register_promotion_routes(app)
    client = TestClient(app)

    body_ok = {"memberId": member_id, "points": 100,
               "refId": "traffic79:777", "reason": "T4", "confirm": True}
    body_no_confirm = dict(body_ok, confirm=False)

    r1 = client.post("/api/promotion/admin/points/revoke",
                     json=body_no_confirm, headers={"X-Role": "admin"})
    assert r1.status_code == 409 and "confirm" in r1.json()["detail"]

    r2 = client.post("/api/promotion/admin/points/revoke",
                     json=body_ok)
    assert r2.status_code == 403

    r3 = client.post("/api/promotion/admin/points/revoke",
                     json=body_ok, headers={"X-Role": "admin"})
    assert r3.status_code == 200
    assert r3.json()["revoked"] == 100

    # 重复追回 → 409(幂等)
    r4 = client.post("/api/promotion/admin/points/revoke",
                     json=body_ok, headers={"X-Role": "admin"})
    assert r4.status_code == 409


def test_t5_invalid_points():
    """T5 参数: points<=0 → ValueError"""

    async def run():
        a = await _mk_member("13900000041")
        with pytest.raises(ValueError, match="正数"):
            await PointsService().revoke_points(a["id"], 0, ref_id="x:1")

    asyncio.run(run())
