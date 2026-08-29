"""订单超时自动处理调度器(P1-13: 定时扫描, 默认开启)

设计文档 9.3 要求三类订单超时自动化:
    - 待付款(PENDING)  超时 30 分钟 → 自动关闭(释放库存+退还抵扣积分)
    - 待收货(SHIPPED)  超时 15 天   → 自动确认收货
    - 待评价(RECEIVED) 超时 7 天    → 自动完成(默认五星)

实现模式对齐 ai_learning_scheduler(v7.6):
    - 周期性后台任务(main.py lifespan 启动, 幂等)
    - 扫描逻辑独立成 run_timeout_scan(), 可单测/手动触发
    - 单笔订单处理失败不影响其他订单
    - 复用 OrderService 既有 timeout_close/confirm/complete 方法
      (锁保护/状态校验/库存回滚/积分退还全部继承)

调度策略:
    - 周期默认 60 秒(ORDER_TIMEOUT_SCAN_INTERVAL 可调), 超时粒度分钟级足够
    - 每轮扫描有全局限跑锁(跨进程 Redis 锁), 防止多实例重复处理
    - 处理结果写入调度统计(最近 N 轮, 防无限膨胀)

环境开关:
    ORDER_TIMEOUT_AUTO=off            关闭调度(默认开启)
    ORDER_TIMEOUT_SCAN_INTERVAL=N     扫描周期秒(默认 60, 下限 10)

接入方式(main.py lifespan/on_event startup):
    from services.order_timeout_scheduler import start_scheduler
    start_scheduler()   # 启动后台任务(幂等)
"""

import asyncio
import logging
import os
from datetime import datetime, UTC, timedelta

from core.helpers import ts
from core.locks import get_lock

logger = logging.getLogger(__name__)

# 每轮最多处理订单数(防单轮占用过久)
BATCH_LIMIT = 200
# 调度统计保留轮数
STATS_KEEP_ROUNDS = 50


def scheduler_enabled() -> bool:
    """调度总开关(ORDER_TIMEOUT_AUTO=off 关闭, 默认开启)"""
    return os.environ.get("ORDER_TIMEOUT_AUTO", "on").strip().lower() != "off"


def scheduler_interval_seconds() -> int:
    """扫描周期(秒), 默认 60 秒"""
    try:
        value = int(os.environ.get("ORDER_TIMEOUT_SCAN_INTERVAL", "60"))
        return max(10, value)  # 下限 10 秒, 防忙循环
    except ValueError:
        return 60


def _parse_ts(value: str) -> datetime | None:
    """解析 ISO8601 时间戳(含时区与否均可), 失败返回 None"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    # 统一转 UTC(去时区者视为 UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_timed_out(order: dict, now: datetime, timeout_seconds: int,
                  baseline_key: str) -> bool:
    """判断订单是否超时(基准时间取不到则视为未超时, 保守处理)

    baseline_key 优先级: 指定字段 → updatedAt(兜底)
    """
    baseline = (_parse_ts(order.get(baseline_key))
                or _parse_ts(order.get("updatedAt")))
    if baseline is None:
        return False
    return now - baseline >= timedelta(seconds=timeout_seconds)


async def run_timeout_scan() -> dict:
    """执行一轮超时扫描(可独立调用, 便于测试/手动运维触发)

    全局限跑锁保护: 多实例部署时同一轮扫描只有一个实例执行,
    单实例内幂等(锁 + timeout 方法的状态校验双保险)。

    Returns:
        本轮扫描统计
    """
    from services.order_service import (
        OrderService, PENDING, SHIPPED, RECEIVED,
        TIMEOUT_PAY, TIMEOUT_CONFIRM, TIMEOUT_REVIEW,
    )
    from repositories.order_repository import OrderRepository

    now = datetime.now(UTC)
    repo = OrderRepository()
    svc = OrderService()
    closed, confirmed, completed, failed = [], [], [], []

    async with get_lock("order:timeout:scan"):
        # 1. 待付款超时 → 自动关闭
        try:
            pending_orders = await repo.list_by_status(PENDING)
            for order in pending_orders[:BATCH_LIMIT]:
                if not _is_timed_out(order, now, TIMEOUT_PAY, "createdAt"):
                    continue
                try:
                    await svc.timeout_close(order["orderId"])
                    closed.append(order["orderId"])
                except Exception as exc:
                    # 并发状态下单者恰好支付/取消 → 状态校验失败, 本轮跳过
                    failed.append({"orderId": order["orderId"], "step": "close",
                                   "error": str(exc)})
        except Exception as exc:
            logger.warning("timeout_scan_pending_failed err=%s", exc)

        # 2. 待收货超时 → 自动确认收货(基准=发货时间)
        try:
            shipped_orders = await repo.list_by_status(SHIPPED)
            for order in shipped_orders[:BATCH_LIMIT]:
                logistics = order.get("logistics") or {}
                shipped_at = _parse_ts(logistics.get("shippedAt"))
                if shipped_at is None:
                    continue  # 无发货时间(异常数据)跳过, 防误判
                if now - shipped_at < timedelta(seconds=TIMEOUT_CONFIRM):
                    continue
                try:
                    await svc.timeout_confirm(order["orderId"])
                    confirmed.append(order["orderId"])
                except Exception as exc:
                    failed.append({"orderId": order["orderId"], "step": "confirm",
                                   "error": str(exc)})
        except Exception as exc:
            logger.warning("timeout_scan_shipped_failed err=%s", exc)

        # 3. 待评价超时 → 自动完成(基准=签收时间)
        try:
            received_orders = await repo.list_by_status(RECEIVED)
            for order in received_orders[:BATCH_LIMIT]:
                logistics = order.get("logistics") or {}
                signed_at = _parse_ts(logistics.get("signedAt"))
                if signed_at is None:
                    continue  # 无签收时间跳过, 防误判
                if now - signed_at < timedelta(seconds=TIMEOUT_REVIEW):
                    continue
                try:
                    await svc.timeout_complete(order["orderId"])
                    completed.append(order["orderId"])
                except Exception as exc:
                    failed.append({"orderId": order["orderId"], "step": "complete",
                                   "error": str(exc)})
        except Exception as exc:
            logger.warning("timeout_scan_received_failed err=%s", exc)

    result = {
        "scannedAt": ts(),
        "closedCount": len(closed),
        "confirmedCount": len(confirmed),
        "completedCount": len(completed),
        "failedCount": len(failed),
        "closed": closed[-20:],          # 最近 20 条明细, 防膨胀
        "confirmed": confirmed[-20:],
        "completed": completed[-20:],
        "failed": failed[-20:],
    }
    if closed or confirmed or completed or failed:
        logger.info(
            "order_timeout_scan closed=%d confirmed=%d completed=%d failed=%d",
            len(closed), len(confirmed), len(completed), len(failed))
    return result


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行超时扫描"""
    interval = scheduler_interval_seconds()
    logger.info("order_timeout_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_timeout_scan()
        except Exception as exc:
            logger.warning("订单超时扫描异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)

    在 main.py 的 lifespan/startup 中调用一次即可。
    """
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("order_timeout_scheduler disabled (ORDER_TIMEOUT_AUTO=off)")
        return False
    if _scheduler_task is not None and not _scheduler_task.done():
        return True  # 已在运行
    try:
        try:
            loop = asyncio.get_running_loop()   # 协程上下文(正常路径)
        except RuntimeError:
            loop = asyncio.get_event_loop()     # 兼容旧式调用
        _scheduler_task = loop.create_task(_scheduler_loop())
        return True
    except RuntimeError as exc:  # 无事件循环(如测试环境直接调用)
        logger.warning("订单超时调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    """停止后台调度任务(测试清理用)"""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def scheduler_running() -> bool:
    """调度器是否在运行(测试/监控用)"""
    return _scheduler_task is not None and not _scheduler_task.done()
