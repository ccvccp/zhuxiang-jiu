"""限时秒杀·超时回补调度器专项测试(2026-10-04 检查升级)

运行: python test_flashsale_sched.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("FLASHSALE_EXPIRE_AUTO", None)
os.environ.pop("FLASHSALE_EXPIRE_INTERVAL_SECONDS", None)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


async def main():
    from repositories.store import reset_store
    from services import flashsale_scheduler as sched

    print("=" * 60)
    print("限时秒杀·超时回补调度器测试")
    print("=" * 60)
    print()

    reset_store()

    # ---------- 开关与周期 ----------
    record("test_01_default_off", sched.scheduler_enabled() is False)
    os.environ["FLASHSALE_EXPIRE_AUTO"] = "on"
    record("test_02_env_on", sched.scheduler_enabled() is True)
    record("test_03_default_interval_60",
           sched.interval_seconds() == 60)
    os.environ["FLASHSALE_EXPIRE_INTERVAL_SECONDS"] = "1"
    record("test_04_interval_floor_30",
           sched.interval_seconds() == 30)
    os.environ["FLASHSALE_EXPIRE_INTERVAL_SECONDS"] = "abc"
    record("test_05_invalid_fallback",
           sched.interval_seconds() == 60)
    os.environ.pop("FLASHSALE_EXPIRE_INTERVAL_SECONDS", None)

    # ---------- 生命周期 ----------
    os.environ.pop("FLASHSALE_EXPIRE_AUTO", None)
    record("test_06_off_start_false",
           sched.start_scheduler() is False
           and sched.scheduler_running() is False)
    os.environ["FLASHSALE_EXPIRE_AUTO"] = "on"
    ok = sched.start_scheduler() is True and sched.scheduler_running()
    record("test_07_start_runs", ok)
    first = sched._task
    sched.start_scheduler()
    record("test_08_idempotent", sched._task is first)
    sched.stop_scheduler()
    record("test_09_stop", sched.scheduler_running() is False)
    os.environ.pop("FLASHSALE_EXPIRE_AUTO", None)

    # ---------- 单轮语义 ----------
    # 空库一轮: 取消 0 单属常态, 不炸
    result = await sched.run_expire_round()
    record("test_10_round_empty_ok",
           isinstance(result, dict),
           f"result={result}")

    # 真实链路: 建场+加品+发布+下单(不支付)+回拨时间+超时取消
    from services.flashsale_service import FlashSaleService
    svc = FlashSaleService()
    from repositories.store import _mock_store
    # 构造产品(products 表为 dict 键值结构)
    _mock_store["products"] = _mock_store.get("products", {})
    _mock_store["products"]["ZX99-SCHED-01"] = {
        "id": "ZX99-SCHED-01", "name": "调度测试酒", "price": 299.0,
        "original_price": 359.0, "member_price": 269.1,
        "status": "active", "stock": 100}
    session = await svc.create_session(
        name="调度测试场", start_time="2026-01-01T00:00:00",
        end_time="2099-01-01T00:00:00")
    sid = session["sessionId"]
    item = await svc.add_item(sid, "ZX99-SCHED-01", 199.0, 5, 2)
    await svc.publish_session(sid)
    # 构造会员
    _mock_store["members"] = _mock_store.get("members", {})
    _mock_store["members"][99001] = {
        "memberId": 99001, "phone": "13900099001", "status": "active",
        "level": 1, "created_at": "2020-01-01T00:00:00"}
    order = await svc.purchase(99001, sid, item["itemId"], 1)
    record("test_11_order_created",
           order.get("status") == "pending_payment", f"order={order}")
    # 回拨 createdAt 20 分钟前(超默认 15 分钟)
    _fo = _mock_store.get("flash_orders", {})
    _orders = (_fo.values() if isinstance(_fo, dict)
               else _fo)
    for o in _orders:
        if isinstance(o, dict) \
                and o.get("orderNo") == order.get("orderNo"):
            from datetime import datetime, timedelta, UTC
            o["createdAt"] = (datetime.now(UTC)
                              - timedelta(minutes=20)).isoformat()
    result = await sched.run_expire_round()
    record("test_12_round_cancels_expired",
           (result.get("cancelledCount") or 0) >= 1,
           f"result={result}")
    # 库存回补验证
    _fi = _mock_store.get("flash_items", {})
    _items = (_fi.values() if isinstance(_fi, dict) else _fi)
    it = next((i for i in _items
               if isinstance(i, dict)
               and i.get("sessionId") == sid), None)
    record("test_13_stock_restored",
           it is not None and it.get("soldCount") == 0,
           f"item={it}")

    # 汇总
    print()
    print("-" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过 {PASS} 项, 失败 {FAIL} 项")
    if FAIL:
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
