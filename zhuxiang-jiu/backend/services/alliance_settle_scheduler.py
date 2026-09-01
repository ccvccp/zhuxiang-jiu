"""37号·AI智能网站同盟模块·T+1 结算调度器

调度策略(保守设计, 对齐既有调度器模式):
    - 周期默认 1 小时(ALLIANCE_SETTLE_INTERVAL_SECONDS 可调)
    - 仅结算 paidAt 距今 ≥ SETTLE_DELAY_HOURS(默认24) 的订单
    - 单轮失败不影响下一轮(异常吞掉记日志)

环境开关:
    ALLIANCE_SETTLE_AUTO=off              关闭调度(默认关闭)
    ALLIANCE_SETTLE_INTERVAL_SECONDS=N    调度周期(默认 3600)

接入方式(main.py startup):
    from services.alliance_settle_scheduler import start_scheduler
    start_scheduler()
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_SCHEDULER_TASK: asyncio.Task | None = None


def scheduler_enabled() -> bool:
    return os.environ.get("ALLIANCE_SETTLE_AUTO", "off").strip().lower() != "off"


def scheduler_interval_seconds() -> int:
    try:
        return max(60, int(os.environ.get(
            "ALLIANCE_SETTLE_INTERVAL_SECONDS", "3600")))
    except ValueError:
        return 3600


async def _scheduler_loop() -> None:
    interval = scheduler_interval_seconds()
    logger.info("alliance_settle_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            from services.alliance_service import AllianceService
            result = await AllianceService().run_scheduled_settlement()
            if result.get("settled"):
                logger.info("alliance_settle_scheduled count=%s",
                            len(result["settled"]))
        except Exception as exc:
            logger.warning("同盟结算调度异常(继续运行): %s", exc)


def start_scheduler() -> bool:
    """启动结算调度(幂等; ALLIANCE_SETTLE_AUTO=off 返回 False)"""
    global _SCHEDULER_TASK
    if not scheduler_enabled():
        logger.info("alliance_settle_scheduler disabled "
                    "(ALLIANCE_SETTLE_AUTO=off)")
        return False
    if _SCHEDULER_TASK is not None and not _SCHEDULER_TASK.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _SCHEDULER_TASK = loop.create_task(_scheduler_loop())
        return True
    except RuntimeError as exc:
        logger.warning("调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    """停止调度任务(测试清理/应用关闭用)"""
    global _SCHEDULER_TASK
    if _SCHEDULER_TASK is not None:
        _SCHEDULER_TASK.cancel()
    _SCHEDULER_TASK = None
