"""P1-11 90 天冷静期测试(Service 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_citystore_cooldown.py

覆盖(设计文档 8.3 资格取消与重新申请):
    1. 运营后被取消: 90 天内重申被拒(提示剩余天数)
    2. 冷静期满(91 天): 重申成功
    3. 审核驳回(从未运营): 不触发冷静期, 立即重申成功
    4. 取消后城市释放: 其他会员可申请同城市
    5. 历史店查询: list_history_stores_by_member 含已取消店
"""
import asyncio
import os
import sys
from datetime import date, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.citystore_service import CityStoreService
from repositories.citystore_repository import (
    CityStoreRepository, STORE_STATUS_CANCELLED, COOLDOWN_DAYS,
)
from repositories.store import _mock_store, reset_store

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


MEMBER = 8001
OTHER_MEMBER = 8002


async def _apply_store(svc, member_id, city_code, city_name, name):
    return await svc.apply(
        member_id=member_id, member_level=5, store_name=name,
        city_code=city_code, city_name=city_name,
        province_code="370000", province_name="山东省",
        business_license="91370100MA000CDX1",
        food_license="JY13701007654321")


async def run_tests():
    repo = CityStoreRepository()
    svc = CityStoreService()

    # ============================================================
    # 1. 运营后被取消 → 90 天内重申被拒
    # ============================================================
    reset_store()
    store = await _apply_store(svc, MEMBER, "370100", "济南市", "冷静期店")
    await svc.audit_store(store_code=store["storeCode"],
                          auditor="admin01", approved=True)
    await svc.update_status(store["storeCode"], STORE_STATUS_CANCELLED,
                            operator="admin01")
    # 取消当天重申(换城市避开独占校验)
    try:
        await _apply_store(svc, MEMBER, "370200", "青岛市", "重申店")
        check("冷静期: 90 天内重申被拒", False)
    except ValueError as e:
        check("冷静期: 90 天内重申被拒", "冷静期" in str(e)
              and "剩余 90 天" in str(e), f"e={e}")

    # ============================================================
    # 2. 冷静期满(91 天前取消) → 重申成功
    # ============================================================
    old_date = (date.today() - timedelta(days=91)).isoformat()
    _mock_store["city_stores"][store["storeCode"]]["closeDate"] = old_date
    r = await _apply_store(svc, MEMBER, "370200", "青岛市", "重申店")
    check("冷静期: 满 91 天重申成功", r["storeCode"].startswith("CS-370200"))

    # ============================================================
    # 3. 审核驳回(从未运营) → 不触发冷静期
    # ============================================================
    reset_store()
    # 先开一家被驳回的店(openDate 为 None)
    store2 = await _apply_store(svc, OTHER_MEMBER, "370100", "济南市", "驳回店")
    await svc.audit_store(store_code=store2["storeCode"],
                          auditor="admin01", approved=False)
    # 驳回当天立即重申(换城市)
    r = await _apply_store(svc, OTHER_MEMBER, "370200", "青岛市", "驳回后重申")
    check("驳回: 不触发冷静期立即重申", r["storeCode"].startswith("CS-370200"))

    # ============================================================
    # 4. 取消后城市释放(其他会员可申请同城市)
    # ============================================================
    reset_store()
    store3 = await _apply_store(svc, MEMBER, "370100", "济南市", "释放城市店")
    await svc.audit_store(store_code=store3["storeCode"],
                          auditor="admin01", approved=True)
    await svc.update_status(store3["storeCode"], STORE_STATUS_CANCELLED,
                            operator="admin01")
    # 原会员仍被冷静期拦(跨城市), 其他会员可申请原城市
    r = await _apply_store(svc, OTHER_MEMBER, "370100", "济南市", "接手店")
    check("释放: 取消后其他会员可申请同城市",
          r["storeCode"].startswith("CS-370100"))

    # ============================================================
    # 5. 历史店查询(含已取消)
    # ============================================================
    history = await repo.list_history_stores_by_member(MEMBER)
    check("历史: 查询含已取消店",
          any(s["storeCode"] == store3["storeCode"] for s in history)
          and any(s.get("status") == STORE_STATUS_CANCELLED for s in history))

    # 常量口径
    check("常量: COOLDOWN_DAYS=90", COOLDOWN_DAYS == 90)


def main():
    asyncio.run(run_tests())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
