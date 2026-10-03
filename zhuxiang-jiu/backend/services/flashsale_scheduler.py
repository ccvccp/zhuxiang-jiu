"""限时秒杀模块·超时回补调度器(flashsale_scheduler)

背景(2026-10-04 检查升级立项):
    expire-cancel 批量取消+回补端点"供定时任务触发"(注释明示),
    但 main.py 无任何秒杀定时任务——支付超时(默认 15 分钟)自动
    取消在生产依赖人工调用, 属"有手脚没心跳"断链。

范式: 全站调度器平移(blogger/attract72 等):
    - FLASHSALE_EXPIRE_AUTO 默认 off(off=零 task 零影响)
    - FLASHSALE_EXPIRE_INTERVAL_SECONDS 默认 60s(下限 30s)
    - 启动即首轮(对齐全站范式; sleep-first 在频繁容器重建下
      永不执行——attract72 坑已修, 不复踩)
    - fail-soft 单轮异常继续运行; 取消 0 单属常态(静默)
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 60
MIN_INTERVAL = 30

_task: asyncio.Task | None = None


def scheduler_enabled() -> bool:
    return os.environ.get(
        "FLASHSALE_EXPIRE_AUTO", "off").strip().lower() == "on"


def interval_seconds() -> int:
    try:
        value = int(os.environ.get(
            "FLASHSALE_EXPIRE_INTERVAL_SECONDS",
            str(DEFAULT_INTERVAL)))
        return max(MIN_INTERVAL, value)
    except ValueError:
        return DEFAULT_INTERVAL


async def run_expire_round() -> dict:
    """单轮超时批量取消+回补(调度/手动共用)"""
    from services.flashsale_service import FlashSaleService
    return await FlashSaleService().cancel_expired_orders()


async def _loop() -> None:
    interval = interval_seconds()
    logger.info("flashsale_expire_scheduler started interval=%ss",
                interval)
    while True:
        try:
            result = await run_expire_round()
            cancelled = result.get("cancelledCount") \
                if isinstance(result, dict) else None
            if cancelled:
                logger.info(
                    "flashsale_expire_scheduled cancelled=%s",
                    cancelled)
        except Exception as exc:  # noqa: BLE001
            logger.warning("秒杀超时回补调度异常(继续运行): %s", exc)
        await asyncio.sleep(interval)


def start_scheduler() -> bool:
    """启动调度器(幂等; FLASHSALE_EXPIRE_AUTO=off 返回 False)"""
    global _task
    if not scheduler_enabled():
        logger.info("flashsale_expire_scheduler disabled "
                    "(FLASHSALE_EXPIRE_AUTO=off)")
        return False
    if _task is not None and not _task.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _task = loop.create_task(_loop())
        return True
    except RuntimeError as exc:
        logger.warning("调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    global _task
    if _task is not None:
        _task.cancel()
    _task = None


def scheduler_running() -> bool:
    return _task is not None and not _task.done()
