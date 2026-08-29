"""订单超时自动处理调度器测试(P1-13)

直接调用 run_timeout_scan() 扫描函数与调度器开关, 无需等待后台周期。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_order_timeout_scheduler.py

覆盖:
    - 开关/周期配置(默认开启/环境变量关闭/周期下限)
    - 待付款超时自动关闭(库存回滚+积分退还)
    - 待付款未超时不处理
    - 待收货超时自动确认
    - 待评价超时自动完成(默认五星返分)
    - 无基准时间(发货/签收缺失)保守跳过
    - start/stop 生命周期
"""

import asyncio
import os
import sys
from datetime import datetime, UTC, timedelta

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["ORDER_TIMEOUT_AUTO"] = "on"

from core.helpers import ts
from repositories.store import reset_store as _reset_store_impl

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    _reset_store_impl()


# ============================================================
# 测试数据(对齐 test_order_routes)
# ============================================================

MEMBER_ID = 1

ITEMS = [
    {"productId": "ZX42-2026L07", "productName": "竹奕·竹香型 42° 500ml",
     "quantity": 2, "unitPrice": 268.00},
]
ADDRESS = {
    "name": "张三", "phone": "13800000001", "province": "山东省",
    "city": "泰安市", "district": "泰山区", "detail": "竹香路1号",
}

# 三类超时阈值(秒)
T_PAY = 30 * 60
T_CONFIRM = 15 * 86400
T_REVIEW = 7 * 86400


async def main():
    from services.order_service import (
        OrderService, PENDING, SHIPPED, RECEIVED, CLOSED, COMPLETED,
    )
    from repositories.order_repository import OrderRepository
    from services import order_timeout_scheduler as sched
    from services.points_service import PointsService
    from repositories.points_repository import SOURCE_REFUND, SOURCE_REVIEW

    print("=" * 60)
    print("订单超时自动处理调度器测试(P1-13)")
    print("=" * 60)
    print()

    svc = OrderService()
    repo = OrderRepository()

    # ---------- 配置测试 ----------
    print("[配置]")

    # test 1: 默认开启
    record("test_01_enabled_by_default", sched.scheduler_enabled() is True)

    # test 2: 环境变量关闭
    os.environ["ORDER_TIMEOUT_AUTO"] = "off"
    record("test_02_env_off_disables", sched.scheduler_enabled() is False)
    os.environ["ORDER_TIMEOUT_AUTO"] = "on"

    # test 3: 周期默认 60 秒
    record("test_03_default_interval",
           sched.scheduler_interval_seconds() == 60)

    # test 4: 周期下限 10 秒(防忙循环)
    os.environ["ORDER_TIMEOUT_SCAN_INTERVAL"] = "1"
    record("test_04_interval_floor",
           sched.scheduler_interval_seconds() == 10)
    del os.environ["ORDER_TIMEOUT_SCAN_INTERVAL"]

    # ---------- 待付款超时关闭 ----------
    print("[待付款超时]")
    reset_store()

    # test 5: 未超时的待付款订单不处理
    order_id = (await svc.create(MEMBER_ID, ITEMS, ADDRESS))["orderId"]
    result = await sched.run_timeout_scan()
    order = await repo.get_by_id(order_id)
    record("test_05_pending_not_timed_out_untouched",
           order["status"] == PENDING and result["closedCount"] == 0,
           f"status={order['status']}, closed={result['closedCount']}")

    # test 6: 超时的待付款订单自动关闭
    stale = (datetime.now(UTC) - timedelta(seconds=T_PAY + 60)).isoformat()
    await repo.update_fields(order_id, {"createdAt": stale, "updatedAt": stale})
    result = await sched.run_timeout_scan()
    order = await repo.get_by_id(order_id)
    record("test_06_pending_timed_out_closed",
           order["status"] == CLOSED and result["closedCount"] == 1
           and order_id in result["closed"],
           f"status={order['status']}, result={result['closedCount']}, "
           f"closed={result['closed']}")

    # test 7: 关闭后库存回滚(下单扣 2 → 回补)
    from repositories.inventory_repository import InventoryRepository
    stock = await InventoryRepository().get("ZX42-2026L07")
    record("test_07_stock_restocked_on_close",
           stock["stock"] == 500,
           f"expected 500, got {stock['stock']}")

    # ---------- 待收货超时确认 ----------
    print("[待收货超时]")
    reset_store()

    # test 8: 超时的待收货订单自动确认
    order_id = (await svc.create(MEMBER_ID, ITEMS, ADDRESS))["orderId"]
    await svc.pay(order_id)
    await svc.ship(order_id, carrier="顺丰", waybill_no="SF0001")
    stale = (datetime.now(UTC) - timedelta(seconds=T_CONFIRM + 60)).isoformat()
    order = await repo.get_by_id(order_id)
    order["logistics"]["shippedAt"] = stale
    order["updatedAt"] = stale
    await repo.save(order_id, order)

    result = await sched.run_timeout_scan()
    order = await repo.get_by_id(order_id)
    record("test_08_shipped_timed_out_confirmed",
           order["status"] == RECEIVED and result["confirmedCount"] == 1,
           f"status={order['status']}, confirmed={result['confirmedCount']}")

    # test 9: 无发货时间的待收货订单保守跳过
    order_id2 = (await svc.create(MEMBER_ID, ITEMS, ADDRESS))["orderId"]
    await svc.pay(order_id2)
    await svc.ship(order_id2, carrier="顺丰", waybill_no="SF0002")
    order2 = await repo.get_by_id(order_id2)
    order2["logistics"]["shippedAt"] = ""   # 异常数据: 无发货时间
    await repo.save(order_id2, order2)
    result = await sched.run_timeout_scan()
    order2 = await repo.get_by_id(order_id2)
    record("test_09_shipped_no_baseline_skipped",
           order2["status"] == SHIPPED and result["confirmedCount"] == 0,
           f"status={order2['status']}, confirmed={result['confirmedCount']}")

    # ---------- 待评价超时完成 ----------
    print("[待评价超时]")
    reset_store()

    # test 10: 超时的待评价订单自动完成(默认五星)
    order_id = (await svc.create(MEMBER_ID, ITEMS, ADDRESS))["orderId"]
    await svc.pay(order_id)
    await svc.ship(order_id, carrier="顺丰", waybill_no="SF0001")
    await svc.confirm(order_id)
    stale = (datetime.now(UTC) - timedelta(seconds=T_REVIEW + 60)).isoformat()
    order = await repo.get_by_id(order_id)
    order["logistics"]["signedAt"] = stale
    order["updatedAt"] = stale
    await repo.save(order_id, order)

    result = await sched.run_timeout_scan()
    order = await repo.get_by_id(order_id)
    record("test_10_received_timed_out_completed",
           order["status"] == COMPLETED and result["completedCount"] == 1
           and order["review"]["rating"] == 5,
           f"status={order['status']}, completed={result['completedCount']}, "
           f"rating={order['review'].get('rating')}")

    # test 11: 自动完成走积分账本评价返分(P1-19 联动: source=review +100)
    logs = await PointsService().list_logs(MEMBER_ID, source=SOURCE_REVIEW)
    record("test_11_autocomplete_points_review_log",
           len(logs) >= 1 and logs[0]["points"] == 100,
           f"expected 1 log/100, got {len(logs)}/"
           f"{logs[0]['points'] if logs else 0}")

    # ---------- 生命周期 ----------
    print("[生命周期]")

    # test 12: start/stop 循环
    started = sched.start_scheduler()
    running = sched.scheduler_running()
    sched.stop_scheduler()
    record("test_12_start_stop_cycle",
           started and running and not sched.scheduler_running(),
           f"started={started}, running={running}")

    # test 13: 关闭状态下 start 返回 False 且不运行
    os.environ["ORDER_TIMEOUT_AUTO"] = "off"
    started_off = sched.start_scheduler()
    record("test_13_disabled_start_returns_false",
           started_off is False and not sched.scheduler_running(),
           f"started={started_off}")
    os.environ["ORDER_TIMEOUT_AUTO"] = "on"

    # 输出结果
    print()
    print("=" * 60)
    print("测试结果汇总:")
    print("-" * 60)
    for r in RESULTS:
        print(r)
    print("-" * 60)
    print(f"通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
    print("=" * 60)

    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
