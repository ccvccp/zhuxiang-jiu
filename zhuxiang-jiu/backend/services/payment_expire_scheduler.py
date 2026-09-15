"""支付单超时自动关闭调度器(05号收款 P2-4: 定时扫描, 默认开启)

职责:
    扫描进行中支付单(pending/paying/failed), 超过 expireTime 的
    自动关闭(reason=TIMEOUT), 防待支付单堆积:
    - pending 超时 → 用户创建未付(充值/SVIP/订单)放弃;
    - paying 超时 → real 渠道发起后用户未完成支付(渠道侧同步过期);
    - failed 超时 → 失败单超 expireTime 后不再允许重试。

不做超时关闭(资金在途/终态):
    paid / refunding / refunded / closed —— 状态机本身已终态保护。

实现模式对齐 order_timeout_scheduler(P1-13):
    - 周期性后台任务(main.py lifespan 启动, 幂等)
    - 扫描逻辑独立成 run_expire_scan(), 可单测/手动触发
    - 单笔处理失败不影响其他订单(并发竞态: 恰好回调支付 → 状态
      校验失败, 本轮跳过)
    - 复用 PaymentService.close_pay(锁保护/状态校验/幂等全部继承)

调度策略:
    - 周期默认 900 秒(15 分钟, PAY_EXPIRE_SCAN_INTERVAL 可调,
      下限 60), 支付单超时粒度 30 分钟, 分钟级扫描足够
    - 每轮扫描有全局限跑锁(跨进程 Redis 锁), 防多实例重复处理
    - 处理结果写入调度统计(最近 20 条明细, 防膨胀)

环境开关:
    PAY_EXPIRE_AUTO=off            关闭调度(默认开启)
    PAY_EXPIRE_SCAN_INTERVAL=N     扫描周期秒(默认 900, 下限 60)

接入方式(main.py lifespan/on_event startup):
    from services.payment_expire_scheduler import start_scheduler
    start_scheduler()   # 启动后台任务(幂等)
"""

import asyncio
import logging
import os
from datetime import datetime, UTC

from core.helpers import ts
from core.locks import get_lock

logger = logging.getLogger(__name__)

# 每轮最多处理支付单数(防单轮占用过久)
BATCH_LIMIT = 200
# 调度统计明细保留条数
STATS_KEEP = 20


def scheduler_enabled() -> bool:
    """调度总开关(PAY_EXPIRE_AUTO=off 关闭, 默认开启)"""
    return os.environ.get("PAY_EXPIRE_AUTO", "on").strip().lower() != "off"


def scheduler_interval_seconds() -> int:
    """扫描周期(秒), 默认 900(15 分钟)"""
    try:
        value = int(os.environ.get("PAY_EXPIRE_SCAN_INTERVAL", "900"))
        return max(60, value)  # 下限 60 秒, 防忙循环
    except ValueError:
        return 900


def _parse_ts(value: str) -> datetime | None:
    """解析 ISO8601 时间戳(含时区与否均可), 失败返回 None"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_expired(order: dict, now: datetime) -> bool:
    """判定支付单是否超过 expireTime(取不到视为未超时, 保守处理)"""
    expire = _parse_ts(order.get("expireTime"))
    if expire is None:
        return False
    return now >= expire


async def run_expire_scan() -> dict:
    """执行一轮超时扫描(可独立调用, 便于测试/手动运维触发)

    全局限跑锁保护: 多实例部署时同一轮扫描只有一个实例执行,
    单实例内幂等(锁 + close_pay 状态校验双保险)。

    Returns:
        本轮扫描统计
    """
    from services.payment_service import PaymentService
    from repositories.payment_repository import PaymentRepository

    now = datetime.now(UTC)
    repo = PaymentRepository()
    svc = PaymentService()
    closed, skipped, failed = [], 0, []

    async with get_lock("payment:expire:scan"):
        try:
            candidates = await repo.list_active_orders(BATCH_LIMIT)
        except Exception as exc:
            logger.warning("payment_expire_scan_list_failed err=%s", exc)
            candidates = []

        for order in candidates:
            pay_no = order.get("payNo", "")
            if not pay_no or not _is_expired(order, now):
                continue
            try:
                r = await svc.close_pay(pay_no, "TIMEOUT")
                # 幂等返回(已关闭)计入 skipped 不重复计数
                if r.get("idempotent"):
                    skipped += 1
                else:
                    closed.append(pay_no)
            except Exception as exc:
                # 并发竞态: 恰好回调支付(paid) → 状态校验失败, 本轮跳过
                failed.append({"payNo": pay_no, "error": str(exc)})

    result = {
        "scannedAt": ts(),
        "scannedCount": len(candidates),
        "closedCount": len(closed),
        "skippedCount": skipped,
        "failedCount": len(failed),
        "closed": closed[-STATS_KEEP:],
        "failed": failed[-STATS_KEEP:],
    }
    if closed or failed:
        logger.info(
            "payment_expire_scan scanned=%d closed=%d failed=%d",
            len(candidates), len(closed), len(failed))
    return result


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行超时扫描"""
    interval = scheduler_interval_seconds()
    logger.info("payment_expire_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_expire_scan()
        except Exception as exc:
            logger.warning("支付单超时扫描异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)

    在 main.py 的 lifespan/startup 中调用一次即可。
    """
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("payment_expire_scheduler disabled (PAY_EXPIRE_AUTO=off)")
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
        logger.warning("支付单超时调度器启动失败(无事件循环): %s", exc)
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
