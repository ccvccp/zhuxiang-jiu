"""P1-4 会员保级/降级模型测试(Service 层 + HTTP 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_member_level_keep.py

覆盖(设计文档 4.4 成长值与降级规则):
    1. 升级记录周期: 消费升级 → levelUpdatedAt/periodConsume 落库; 同级消费累计
    2. 保级进度: get_level 返回 keepLevel(周期消费/要求/剩余/百分比/到期日)
    3. 到期考核: 未到期 not_expired / 达标 kept(周期重置) / 未达标 downgraded(降一级)
    4. L1 不考核 / 无周期记录跳过
    5. SVIP 付费: L5 续费开新周期 / L1-L4 直接购买开通(升级 L5)
    6. 降级缓冲恢复: 30 天内补足消费可恢复 / 未补足拒绝 / 无降级记录拒绝
    7. 全量考核: 多会员批量(kept/downgraded/skipped 统计)
    8. 临期预警+调度扫描: list_near_expiry(≤30天且未达标)/run_level_expiry_scan 聚合
    9. HTTP 层: 进度查询/续费/恢复/到期考核(401/403/200/409)
       +管理端 4 面(admin/list 等级筛选·admin/{id} 详情·expiry/run·expiry/preview)
    10. 订单支付消费入账: record_order_consume(升级/同级累计/L1 不累计/连续跨级)
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
    check("进度: SVIP 付费全等级可用", kl["renewable"] is True
          and kl["renewFee"] == 99.0)

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
    # 低级会员直接购买开通(L2 → L5)
    r = await svc.renew_svip(mid)      # mid 当前 L2(3b kept 后)
    check("购买: L2 直接开通 SVIP", r["success"] is True
          and r["action"] == "purchased" and r["level"] == 5
          and r["fromLevel"] == 2 and r["renewFee"] == 99.0)
    m = await repo.get_by_id(mid)
    check("购买: 等级/周期/留痕落库", m.get("level") == 5
          and m.get("periodConsume") == 0.0
          and bool(m.get("svipPurchasedAt"))
          and m.get("svipPurchasedFromLevel") == 2)
    check("购买: 成长值不虚标(仍 600)", m.get("growth_value") == 600)
    # 购买后新周期 not_expired
    r = await svc.check_level_expiry(mid)
    check("购买: 考核 not_expired", r["action"] == "not_expired")
    # 购买后(L5)再次付费 → 续费
    r = await svc.renew_svip(mid)
    check("购买后再付费为续费", r["action"] == "renewed")
    m = await repo.get_by_id(mid)
    check("续费: svipRenewedAt 留痕", bool(m.get("svipRenewedAt")))
    # L1 最低等级直接购买开通
    r = await svc.renew_svip(mid3)      # mid3 L1
    check("购买: L1 直接开通 SVIP", r["action"] == "purchased"
          and r["fromLevel"] == 1)

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

    # ============================================================
    # 8. 临期预警 + 调度扫描(list_near_expiry / run_level_expiry_scan)
    # ============================================================
    reset_store()
    near_mid = await _mk_member("13700000021")
    await repo.update_fields(near_mid, {
        "level": 3, "growth_value": 3000, "nickname": "临期未达标",
        "levelUpdatedAt": (datetime.now(UTC) - timedelta(days=335)).isoformat(),  # 剩25天
        "periodConsume": 500.0,   # L3 保级需 2000 → 25%
    })
    ok_mid = await _mk_member("13700000022")
    await repo.update_fields(ok_mid, {
        "level": 3, "growth_value": 3000, "nickname": "临期已达标",
        "levelUpdatedAt": (datetime.now(UTC) - timedelta(days=340)).isoformat(),  # 剩20天
        "periodConsume": 2500.0,  # ≥2000 → 100% 不入预警
    })
    far_mid = await _mk_member("13700000023")
    await repo.update_fields(far_mid, {
        "level": 2, "growth_value": 500, "nickname": "远期未达标",
        "levelUpdatedAt": (datetime.now(UTC) - timedelta(days=10)).isoformat(),   # 剩350天
        "periodConsume": 0.0,
    })
    l1_mid = await _mk_member("13700000024")  # L1 → 不列

    warnings = await svc.list_near_expiry(days=30)
    warn_ids = [w["memberId"] for w in warnings]
    check("预警: 临期未达标在列", near_mid in warn_ids)
    check("预警: 临期已达标不入列", ok_mid not in warn_ids)
    check("预警: 远期不入列", far_mid not in warn_ids)
    check("预警: L1 不入列", l1_mid not in warn_ids)
    near_w = next(w for w in warnings if w["memberId"] == near_mid)
    check("预警: 字段齐全(等级/进度/到期日)",
          near_w["level"] == 3 and near_w["requirement"] == 2000
          and near_w["progressPercent"] == 25.0
          and "expireAt" in near_w and "daysRemaining" in near_w)

    # 调度扫描(全量考核 + 预警快照聚合; 复用 run_level_expiry_check 锁)
    from services.member_level_scheduler import run_level_expiry_scan
    scan = await run_level_expiry_scan()
    check("扫描: 聚合含考核统计", "kept" in scan["expiry"]
          and "downgraded" in scan["expiry"])
    scan_warn_ids = [w["memberId"] for w in scan["nearExpiry"]]
    check("扫描: 预警快照含临期会员", near_mid in scan_warn_ids)
    check("扫描: 留痕字段(scannedAt/nearExpiryCount)",
          "scannedAt" in scan and scan["nearExpiryCount"] >= 1)

    # ============================================================
    # 10. 订单支付消费入账(record_order_consume——order pay 链路)
    # ============================================================
    reset_store()
    # 10a. 升级路径: L1 消费 500 → L2 + 周期落库
    oc_mid = await _mk_member("13700000025")
    r = await svc.record_order_consume(oc_mid, 500)
    check("订单入账: L1→L2 升级", r["leveledUp"] is True
          and r["toLevel"] == 2 and r["fromLevel"] == 1)
    m = await repo.get_by_id(oc_mid)
    check("订单入账: 升级周期落库", bool(m.get("levelUpdatedAt"))
          and m.get("periodConsume") == 500.0)
    check("订单入账: 成长值 500", m.get("growth_value") == 500)
    # 10b. 同级消费累计保级周期
    r = await svc.record_order_consume(oc_mid, 100)
    m = await repo.get_by_id(oc_mid)
    check("订单入账: 同级累计 600", r["leveledUp"] is False
          and m.get("periodConsume") == 600.0)
    # 10c. L1 同级不累计周期(无保级要求)
    oc_l1 = await _mk_member("13700000026")
    r = await svc.record_order_consume(oc_l1, 50)
    m = await repo.get_by_id(oc_l1)
    check("订单入账: L1 不累计周期", r["toLevel"] == 1
          and not m.get("periodConsume"))
    # 10d. 连续两笔跨级(450+60=510 → L2)
    oc_two = await _mk_member("13700000027")
    await svc.record_order_consume(oc_two, 450)
    r = await svc.record_order_consume(oc_two, 60)
    m = await repo.get_by_id(oc_two)
    check("订单入账: 第二笔跨门槛升级", r["leveledUp"] is True
          and m.get("level") == 2 and m.get("periodConsume") == 60.0)


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

    # SVIP 付费: 低级会员直接购买开通(200 purchased)
    r = client.post("/api/member/level/renew-svip", headers=M)
    body = r.json()
    check("HTTP SVIP: 低级会员购买开通 200", r.status_code == 200
          and body.get("action") == "purchased" and body.get("level") == 5,
          f"{r.status_code} {r.text[:120]}")
    # L5 后再次付费 → 续费(renewed)
    r = client.post("/api/member/level/renew-svip", headers=M)
    body = r.json()
    check("HTTP SVIP: L5 续费 renewed", r.status_code == 200
          and body.get("action") == "renewed", f"{r.status_code}")
    r = client.post("/api/member/level/renew-svip")
    check("HTTP SVIP: 无头 401", r.status_code == 401)

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

    # ============================================================
    # 9. 管理端会员面(admin/list · admin/{id} · expiry/run · expiry/preview)
    # ============================================================
    # 列表: 非管理员 403 / admin 200
    r = client.get("/api/member/admin/list")
    check("HTTP 管理列表: 无权限 403", r.status_code == 403)
    r = client.get("/api/member/admin/list", headers=ADMIN)
    body = r.json()
    check("HTTP 管理列表: admin 200", r.status_code == 200
          and body.get("success") is True and body.get("total", 0) >= 1,
          f"{r.status_code} {r.text[:120]}")
    first = (body.get("data") or [{}])[0]
    check("HTTP 管理列表: 字段齐全(脱敏手机号)",
          "memberId" in first and "level" in first
          and "****" in first.get("phone", ""), str(first)[:120])
    # 等级筛选
    r = client.get("/api/member/admin/list?level=5", headers=ADMIN)
    body = r.json()
    check("HTTP 管理列表: 等级筛选生效",
          r.status_code == 200
          and all(m.get("level") == 5 for m in body.get("data", [])),
          f"{r.text[:120]}")

    # 详情: admin 200(含保级进度) / 不存在 404
    r = client.get("/api/member/admin/1", headers=ADMIN)
    body = r.json()
    check("HTTP 管理详情: 200 含 keepLevel",
          r.status_code == 200 and "keepLevel" in body.get("data", {}),
          f"{r.status_code} {r.text[:120]}")
    r = client.get("/api/member/admin/99999", headers=ADMIN)
    check("HTTP 管理详情: 不存在 404", r.status_code == 404)

    # 全量考核(管理端): 非管理员 403 / admin 200
    r = client.post("/api/member/admin/level-expiry/run")
    check("HTTP 手动考核: 无权限 403", r.status_code == 403)
    r = client.post("/api/member/admin/level-expiry/run", headers=ADMIN)
    body = r.json()
    check("HTTP 手动考核: admin 200", r.status_code == 200
          and "kept" in body and "downgraded" in body,
          f"{r.status_code} {r.text[:120]}")

    # 临期预警: 无权限 403 / admin 200(观测面)
    r = client.get("/api/member/admin/level-expiry/preview")
    check("HTTP 临期预警: 无权限 403", r.status_code == 403)
    r = client.get("/api/member/admin/level-expiry/preview?days=30",
                    headers=ADMIN)
    body = r.json()
    check("HTTP 临期预警: admin 200(含 days/count)",
          r.status_code == 200 and body.get("days") == 30
          and "count" in body and "data" in body,
          f"{r.status_code} {r.text[:120]}")


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
