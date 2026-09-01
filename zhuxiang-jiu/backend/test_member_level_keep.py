"""P1-4 会员保级/降级模型测试(Service 层 + HTTP 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_member_level_keep.py

覆盖(设计文档 4.4 成长值与降级规则):
    1. 升级记录周期: 消费升级 → levelUpdatedAt/periodConsume 落库; 同级消费累计
    2. 保级进度: get_level 返回 keepLevel(周期消费/要求/剩余/百分比/到期日)
    3. 到期考核: 未到期 not_expired / 达标 kept(周期重置) / 未达标 downgraded(降一级)
    4. L1 不考核 / 无周期记录跳过
    5. SVIP 续费: L5 renew 开新周期 / 非 L5 拒绝
    6. 降级缓冲恢复: 30 天内补足消费可恢复 / 未补足拒绝 / 无降级记录拒绝
    7. 全量考核: 多会员批量(kept/downgraded/skipped 统计)
    8. HTTP 层: 进度查询/续费/恢复/到期考核(401/403/200/409)
"""
import asyncio
import os
import sys
from datetime import datetime, UTC, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.member_service import MemberService, KEEP_LEVEL_CONSUME
from repositories.member_repository import MemberRepository
from repositories.store import _mock_store

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


async def _mk_member(phone: str, growth: int = 0) -> int:
    """造测试会员(直接走 repo, 绕开注册赠分逻辑)"""
    repo = MemberRepository()
    m = await repo.create({
        "phone": phone, "password": "x" * 6, "nickname": phone,
        "level": 1, "growth_value": growth, "points": 0, "status": 1,
        "created_at": datetime.now(UTC).isoformat(),
    })
    return m["id"]


async def run_service():
    global PASS, FAIL
    from repositories.store import reset_store
    reset_store()

    svc = MemberService()
    repo = MemberRepository()

    # ============================================================
    # 1. 升级记录周期 + 同级消费累计
    # ============================================================
    mid = await _mk_member("13700000001")
    # 消费 500 → 升 L2
    r = await svc.consume(mid, 500)
    check("升级: 消费 500 升 L2", r["toLevel"] == 2 and r["leveledUp"] is True)
    m = await repo.get_by_id(mid)
    check("周期: levelUpdatedAt 落库", bool(m.get("levelUpdatedAt")))
    check("周期: periodConsume=500(升级单笔计入)", m.get("periodConsume") == 500.0)
    # 同级消费累计(未再升级)
    await svc.consume(mid, 100)
    m = await repo.get_by_id(mid)
    check("周期: 同级消费累计 600", m.get("periodConsume") == 600.0)

    # ============================================================
    # 2. 保级进度(get_level.keepLevel)
    # ============================================================
    r = await svc.get_level(mid)
    kl = r["keepLevel"]
    check("进度: 周期消费 600", kl["periodConsume"] == 600.0)
    check("进度: L2 要求 300", kl["requirement"] == 300)
    check("进度: 已达标 100%", kl["progressPercent"] == 100.0
          and kl["remainingAmount"] == 0)
    check("进度: 剩余天数≈360", kl["daysRemaining"] is not None
          and 350 <= kl["daysRemaining"] <= 360)
    check("进度: L2 不可续费", kl["renewable"] is False)

    # ============================================================
    # 3. 到期考核三分支
    # ============================================================
    # 3a. 未到期
    r = await svc.check_level_expiry(mid)
    check("考核: 未到期 not_expired", r["action"] == "not_expired")

    # 3b. 到期达标 → kept(重置周期)
    await repo.update_fields(mid, {
        "levelUpdatedAt": (datetime.now(UTC) - timedelta(days=370)).isoformat(),
        "periodConsume": 350.0,   # ≥ L2 要求 300
    })
    r = await svc.check_level_expiry(mid)
    check("考核: 到期达标 kept", r["action"] == "kept")
    m = await repo.get_by_id(mid)
    check("考核: kept 周期重置", m.get("periodConsume") == 0.0
          and bool(m.get("levelUpdatedAt")))

    # 3c. 到期未达标 → downgraded(降一级)
    mid2 = await _mk_member("13700000002")
    await svc.consume(mid2, 500)          # 升 L2
    await repo.update_fields(mid2, {
        "levelUpdatedAt": (datetime.now(UTC) - timedelta(days=400)).isoformat(),
        "periodConsume": 99.0,            # < 300
    })
    r = await svc.check_level_expiry(mid2)
    check("考核: 到期未达标 downgraded", r["action"] == "downgraded"
          and r["fromLevel"] == 2 and r["toLevel"] == 1)
    m = await repo.get_by_id(mid2)
    check("考核: 降级落库", m.get("level") == 1
          and bool(m.get("levelDowngradedAt"))
          and m.get("levelDowngradedFrom") == 2)

    # ============================================================
    # 4. L1 不考核 / 无周期记录跳过
    # ============================================================
    mid3 = await _mk_member("13700000003")   # L1
    r = await svc.check_level_expiry(mid3)
    check("考核: L1 跳过", r["action"] == "skip")
    mid4 = await _mk_member("13700000004")
    await repo.update_fields(mid4, {"level": 3, "levelUpdatedAt": ""})
    r = await svc.check_level_expiry(mid4)
    check("考核: 无周期记录跳过", r["action"] == "skip")

    # ============================================================
    # 5. SVIP 续费
    # ============================================================
    mid5 = await _mk_member("13700000005")
    await repo.update_fields(mid5, {
        "level": 5, "growth_value": 9999,
        "levelUpdatedAt": (datetime.now(UTC) - timedelta(days=370)).isoformat(),
        "periodConsume": 10.0,
    })
    r = await svc.renew_svip(mid5)
    check("续费: L5 开新周期", r["success"] is True and r["renewFee"] == 99.0
          and r["validMonths"] == 12)
    m = await repo.get_by_id(mid5)
    check("续费: 周期重置", m.get("periodConsume") == 0.0
          and bool(m.get("svipRenewedAt")))
    # 续费后考核不再到期
    r = await svc.check_level_expiry(mid5)
    check("续费: 考核 not_expired", r["action"] == "not_expired")
    # 非 L5 拒绝
    try:
        await svc.renew_svip(mid)
        check("续费: 非 L5 拒绝", False)
    except ValueError as e:
        check("续费: 非 L5 拒绝", "L5" in str(e))

    # ============================================================
    # 6. 降级缓冲恢复
    # ============================================================
    # mid2 已降级(缓冲期内): periodConsume=0, 未补足 → 拒绝
    try:
        await svc.recover_level(mid2)
        check("恢复: 未补足拒绝", False)
    except ValueError as e:
        check("恢复: 未补足拒绝", "未达标" in str(e))
    # 补足消费(直接 update_fields 模拟补单, 不触发升级判定)
    await repo.update_fields(mid2, {"periodConsume": 300.0})
    r = await svc.recover_level(mid2)
    check("恢复: 补足后恢复 L2", r["success"] is True
          and r["recoveredLevel"] == 2)
    m = await repo.get_by_id(mid2)
    check("恢复: 降级记录清除", m.get("level") == 2
          and not m.get("levelDowngradedAt"))
    # 无降级记录拒绝
    try:
        await svc.recover_level(mid)
        check("恢复: 无降级记录拒绝", False)
    except ValueError as e:
        check("恢复: 无降级记录拒绝", "无需恢复" in str(e))
    # 超 30 天缓冲期拒绝
    mid6 = await _mk_member("13700000006")
    await repo.update_fields(mid6, {
        "level": 1, "periodConsume": 300.0,
        "levelDowngradedAt": (datetime.now(UTC) - timedelta(days=31)).isoformat(),
        "levelDowngradedFrom": 2,
    })
    try:
        await svc.recover_level(mid6)
        check("恢复: 超 30 天缓冲拒绝", False)
    except ValueError as e:
        check("恢复: 超 30 天缓冲拒绝", "缓冲期" in str(e))

    # ============================================================
    # 7. 全量考核(多会员批量)
    # ============================================================
    reset_store()
    mids = []
    for i, (growth, days_ago, consume) in enumerate([
            (500, 370, 350.0),    # L2 达标 → kept
            (500, 370, 50.0),     # L2 未达标 → downgraded
            (500, 10, 0.0),       # L2 未到期 → skipped(not_expired)
    ]):
        mid_x = await _mk_member(f"1370000001{i}")
        await repo.update_fields(mid_x, {
            "level": 2, "growth_value": growth,
            "levelUpdatedAt": (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),
            "periodConsume": consume,
        })
        mids.append(mid_x)
    await _mk_member("13700000019")   # L1 → 不入批次
    r = await svc.run_level_expiry_check()
    # 注: reset_store 会重建 seed 会员(含 level≥2 但无周期记录者 → skip)
    check("批量: kept=1", r["kept"] == 1)
    check("批量: downgraded=1", r["downgraded"] == 1)
    check("批量: skipped 含 not_expired 与 seed 无记录", r["skipped"] >= 1)
    check("批量: L1 不入批次(total=3+seed)", r["total"] == 4)
    check("批量: failed=0", r["failed"] == 0)


def run_http():
    global PASS, FAIL
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    M = {"X-Member-Id": "1"}          # seed 会员 1
    ADMIN = {"X-Role": "admin"}

    # 进度查询(401 + 200)
    r = client.get("/api/member/level")
    check("HTTP 进度: 无头 401", r.status_code == 401)
    r = client.get("/api/member/level", headers=M)
    check("HTTP 进度: 200 含 keepLevel", r.status_code == 200
          and "keepLevel" in r.json(), f"{r.status_code} {r.text[:120]}")

    # 续费: 会员 1 非 L5 → 409
    r = client.post("/api/member/level/renew-svip", headers=M)
    check("HTTP 续费: 非 L5 409", r.status_code == 409, f"got {r.status_code}")
    r = client.post("/api/member/level/renew-svip")
    check("HTTP 续费: 无头 401", r.status_code == 401)

    # 恢复: 无降级记录 → 409
    r = client.post("/api/member/level/recover", headers=M)
    check("HTTP 恢复: 无记录 409", r.status_code == 409, f"got {r.status_code}")

    # 全量考核: 无权限 403 / admin 200
    r = client.post("/api/member/level/expiry-check")
    check("HTTP 考核: 无权限 403", r.status_code == 403)
    r = client.post("/api/member/level/expiry-check", headers=ADMIN)
    body = r.json()
    check("HTTP 考核: admin 200", r.status_code == 200
          and body.get("success") is True, f"{r.status_code} {r.text[:150]}")


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
