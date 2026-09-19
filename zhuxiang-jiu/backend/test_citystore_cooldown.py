"""P1-11 90 天冷静期测试(Service 层——县区网店口径)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_citystore_cooldown.py

覆盖(设计文档 8.3 资格取消与重新申请):
    1. 运营后被取消: 90 天内重申被拒(提示剩余天数)
    2. 冷静期满(91 天): 重申成功
    3. 驳回(从未运营): 不触发冷静期, 立即重申成功
    4. 取消后区县释放: 其他会员可申请同区县
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
from repositories.wallet_repository import WalletRepository

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


async def _ensure_wallet(member_id: int):
    """预置钱包账户(active + 余额 5000)"""
    wallet = WalletRepository()
    acc = await wallet.get_account(member_id)
    if acc is None:
        await wallet.open_account(member_id, {
            "userId": member_id, "status": "active", "balance": 0.0})
    await wallet.add_balance(member_id, 5000.0)


async def _apply_store(svc, member_id, district_code, name):
    await _ensure_wallet(member_id)
    return await svc.apply(
        member_id=member_id, member_level=5, store_name=name,
        district_code=district_code,
        id_name="测试店主", id_number="11010519491231002X",
        signature_confirm=True)


async def run_tests():
    repo = CityStoreRepository()
    svc = CityStoreService()

    # ============================================================
    # 1. 运营后被取消 → 90 天内重申被拒
    # ============================================================
    reset_store()
    store = await _apply_store(svc, MEMBER, "370102", "冷静期店")
    await svc.audit_store(store_code=store["storeCode"],
                          auditor="admin01", approved=True)
    await svc.update_status(store["storeCode"], STORE_STATUS_CANCELLED,
                            operator="admin01")
    # 取消当天重申(换区县避开独占校验)
    try:
        await _apply_store(svc, MEMBER, "370202", "重申店")
        check("冷静期: 90 天内重申被拒", False)
    except ValueError as e:
        # 日期口径: 服务端 UTC 与本地可能相差一天,
        # 剩余天数 89/90 均为正确边界
        check("冷静期: 90 天内重申被拒", "冷静期" in str(e)
              and ("剩余 89 天" in str(e)
                   or "剩余 90 天" in str(e)), f"e={e}")

    # ============================================================
    # 2. 冷静期满(91 天前取消) → 重申成功
    # ============================================================
    old_date = (date.today() - timedelta(days=91)).isoformat()
    _mock_store["city_stores"][store["storeCode"]]["closeDate"] = old_date
    r = await _apply_store(svc, MEMBER, "370202", "重申店")
    check("冷静期: 满 91 天重申成功", r["storeCode"].startswith("CS-370202"))

    # ============================================================
    # 3. 驳回(从未运营) → 不触发冷静期
    # ============================================================
    reset_store()
    # 先开一家被驳回的店(openDate 为 None)
    store2 = await _apply_store(svc, OTHER_MEMBER, "370102", "驳回店")
    await svc.audit_store(store_code=store2["storeCode"],
                          auditor="admin01", approved=False)
    # 驳回当天立即重申(换区县)
    r = await _apply_store(svc, OTHER_MEMBER, "370202", "驳回后重申")
    check("驳回: 不触发冷静期立即重申", r["storeCode"].startswith("CS-370202"))

    # ============================================================
    # 4. 取消后区县释放(其他会员可申请同区县)
    # ============================================================
    reset_store()
    store3 = await _apply_store(svc, MEMBER, "370102", "释放区县店")
    await svc.audit_store(store_code=store3["storeCode"],
                          auditor="admin01", approved=True)
    await svc.update_status(store3["storeCode"], STORE_STATUS_CANCELLED,
                            operator="admin01")
    # 原会员仍被冷静期拦(跨区县), 其他会员可申请原区县
    r = await _apply_store(svc, OTHER_MEMBER, "370102", "接手店")
    check("释放: 取消后其他会员可申请同区县",
          r["storeCode"].startswith("CS-370102"))

    # ============================================================
    # 5. 历史店查询(含已取消)
    # ============================================================
    history = await repo.list_history_stores_by_member(MEMBER)
    check("历史: 查询含已取消店",
          any(s["storeCode"] == store3["storeCode"] for s in history)
          and any(s.get("status") == STORE_STATUS_CANCELLED for s in history))


def main():
    asyncio.run(run_tests())
    print("=" * 60)
    print("90 天冷静期测试(县区网店口径)".center(50))
    print("=" * 60)
    for r in RESULTS:
        print(r)
    print("-" * 60)
    print(f"TOTAL: {PASS} pass, {FAIL} fail / {PASS + FAIL}")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
