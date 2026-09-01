"""P1-10 城市门店月度考核调度器测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_citystore_assessment_scheduler.py

覆盖:
    1. 日期门卫: 1 日触发(目标=上一自然月) / 跨年(1 月→上年 12 月) /
       3 日内补跑窗口 / 4 日后跳过
    2. 自动考核: 活跃网店全部考核(待审核/已取消排除)
    3. 幂等重入: 同月重复执行已考核店全部跳过
    4. 开关: CITYSTORE_ASSESSMENT_AUTO=off 关闭
    5. 周期下限: SCAN_INTERVAL 下限 60
    6. start/stop 幂等
"""
import asyncio
import os
import sys
from datetime import datetime, UTC

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
os.environ["CITYSTORE_ASSESSMENT_AUTO"] = "on"

from services.citystore_assessment_scheduler import (
    run_monthly_assessment, target_month,
    scheduler_enabled, scheduler_interval_seconds,
    start_scheduler, stop_scheduler, scheduler_running,
    scheduler_stats,
)
from services.citystore_service import CityStoreService
from repositories.citystore_repository import CityStoreRepository
from repositories.store import reset_store

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


MEMBER = 6001
MONTH = "2026-08"


async def _seed_stores(svc, n=3):
    """创建 n 家运营中网店(不同城市)+1 待审核+1 已取消"""
    cities = [("370100", "济南市"), ("370200", "青岛市"), ("370300", "淄博市")]
    codes = []
    for i in range(n):
        store = await svc.apply(
            member_id=MEMBER + i, member_level=5,
            store_name=f"调度考核店{i}", city_code=cities[i][0],
            city_name=cities[i][1], province_code="370000",
            province_name="山东省",
            business_license=f"91370100MA0000{i}XX",
            food_license=f"JY1370100123456{i}")
        await svc.audit_store(store_code=store["storeCode"],
                              auditor="admin01", approved=True)
        codes.append(store["storeCode"])
    # 待审核(不参与考核)
    pending = await svc.apply(
        member_id=MEMBER + 90, member_level=5, store_name="待审核店",
        city_code="370400", city_name="枣庄市", province_code="370000",
        province_name="山东省", business_license="91370100MA00009X1",
        food_license="JY13701001234569")
    # 已取消(不参与考核)
    cancelled = await svc.apply(
        member_id=MEMBER + 91, member_level=5, store_name="已取消店",
        city_code="370500", city_name="东营市", province_code="370000",
        province_name="山东省", business_license="91370100MA00009X2",
        food_license="JY13701001234570")
    await svc.audit_store(store_code=cancelled["storeCode"],
                          auditor="admin01", approved=True)
    await svc.update_status(cancelled["storeCode"], 4, operator="admin01")
    return codes, pending["storeCode"], cancelled["storeCode"]


def run_gate_tests():
    # ============================================================
    # 1. 日期门卫
    # ============================================================
    m = target_month(datetime(2026, 9, 1, tzinfo=UTC))
    check("门卫: 9 月 1 日考核 8 月", m == "2026-08", f"got={m}")
    m = target_month(datetime(2026, 1, 1, tzinfo=UTC))
    check("门卫: 跨年 1 月→上年 12 月", m == "2025-12", f"got={m}")
    m = target_month(datetime(2026, 9, 3, tzinfo=UTC))
    check("门卫: 3 日内补跑窗口", m == "2026-08", f"got={m}")
    m = target_month(datetime(2026, 9, 4, tzinfo=UTC))
    check("门卫: 4 日后跳过", m is None, f"got={m}")
    m = target_month(datetime(2026, 9, 15, tzinfo=UTC))
    check("门卫: 月中跳过", m is None, f"got={m}")


async def run_scheduler_tests():
    svc = CityStoreService()
    repo = CityStoreRepository()

    # ============================================================
    # 2. 自动考核(活跃店全部考核, 待审核/已取消排除)
    # ============================================================
    reset_store()
    codes, pending_code, cancelled_code = await _seed_stores(svc)
    r = await run_monthly_assessment(month=MONTH)
    check("考核: 3 家活跃店全部考核", r["assessedCount"] == 3
          and r["totalStores"] == 3, f"r={r}")
    check("考核: 考核记录落库",
          all([await repo.get_assessment(c, MONTH) for c in codes]))
    check("考核: 明细含门店码", set(r["assessed"]) == set(codes))

    # ============================================================
    # 3. 幂等重入(同月重复执行)
    # ============================================================
    r2 = await run_monthly_assessment(month=MONTH)
    check("幂等: 重复执行全部跳过", r2["assessedCount"] == 0
          and r2["skippedCount"] == 3, f"r={r2}")

    # 调度统计留存
    stats = scheduler_stats()
    check("统计: 调度统计留存", len(stats) >= 2
          and stats[-1]["month"] == MONTH)

    # ============================================================
    # 4. 窗口外跳过
    # ============================================================
    from unittest.mock import patch
    with patch("services.citystore_assessment_scheduler.target_month",
               return_value=None):
        r3 = await run_monthly_assessment()
    check("窗口: 窗口外跳过", r3.get("skipped") is True
          and "考核窗口" in r3.get("reason", ""), f"r={r3}")


def run_switch_tests():
    # ============================================================
    # 5. 开关与周期
    # ============================================================
    os.environ["CITYSTORE_ASSESSMENT_AUTO"] = "off"
    check("开关: off 关闭", scheduler_enabled() is False)
    os.environ["CITYSTORE_ASSESSMENT_AUTO"] = "on"
    check("开关: 默认开启", scheduler_enabled() is True)

    os.environ["CITYSTORE_ASSESSMENT_SCAN_INTERVAL"] = "5"
    check("周期: 下限 60 秒", scheduler_interval_seconds() == 60)
    os.environ["CITYSTORE_ASSESSMENT_SCAN_INTERVAL"] = "120"
    check("周期: 自定义 120 秒", scheduler_interval_seconds() == 120)
    del os.environ["CITYSTORE_ASSESSMENT_SCAN_INTERVAL"]

    # ============================================================
    # 6. start/stop 幂等
    # ============================================================
    async def _lifecycle():
        ok1 = start_scheduler()
        ok2 = start_scheduler()  # 幂等
        running = scheduler_running()
        stop_scheduler()
        stopped = not scheduler_running()
        return ok1 and ok2 and running and stopped

    check("生命周期: start 幂等/stop 生效", asyncio.run(_lifecycle()))


def main():
    run_gate_tests()
    asyncio.run(run_scheduler_tests())
    run_switch_tests()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
