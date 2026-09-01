"""36号·AI智能推广模块·定时调度器(雷达扫描 + 发布出队)

调度策略(保守设计, 对齐既有调度器模式):
    - 雷达: 周期 15 分钟(PROMO_RADAR_INTERVAL_SECONDS 可调), 扫描→自动决策
    - 发布: 周期 5 分钟(PROMO_PUBLISH_INTERVAL_SECONDS 可调), 到期出队
    - 单类任务失败不影响下一轮(异常吞掉记日志)

环境开关:
    PROMO_RADAR_AUTO=off                关闭雷达调度(默认关闭)
    PROMO_RADAR_INTERVAL_SECONDS=N      雷达周期(默认 900)
    PROMO_PUBLISH_AUTO=off              关闭发布调度(默认关闭)
    PROMO_PUBLISH_INTERVAL_SECONDS=N    发布周期(默认 300)

接入方式(main.py startup):
    from services.promo_scheduler import (
        start_radar_scheduler, start_publish_scheduler)
    start_radar_scheduler()
    start_publish_scheduler()
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_RADAR_TASK: asyncio.Task | None = None
_PUBLISH_TASK: asyncio.Task | None = None


def radar_enabled() -> bool:
    return os.environ.get("PROMO_RADAR_AUTO", "off").strip().lower() != "off"


def publish_enabled() -> bool:
    return os.environ.get("PROMO_PUBLISH_AUTO", "off").strip().lower() != "off"


def _interval(env: str, default: int, floor: int = 60) -> int:
    try:
        return max(floor, int(os.environ.get(env, str(default))))
    except ValueError:
        return default


async def _radar_loop() -> None:
    interval = _interval("PROMO_RADAR_INTERVAL_SECONDS", 900)
    logger.info("promo_radar_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            from services.promo_service import PromoService
            result = await PromoService().scan()
            logger.info("promo_radar_scheduled new=%s discarded=%s",
                        result.get("new"), result.get("discarded"))
        except Exception as exc:
            logger.warning("雷达调度异常(继续运行): %s", exc)


async def _publish_loop() -> None:
    interval = _interval("PROMO_PUBLISH_INTERVAL_SECONDS", 300)
    logger.info("promo_publish_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            from services.promo_service import PromoService
            published = await PromoService().process_publish_queue()
            if published:
                logger.info("promo_publish_scheduled count=%s", len(published))
        except Exception as exc:
            logger.warning("发布调度异常(继续运行): %s", exc)


def _start(task_holder: str, coro) -> bool:
    global _RADAR_TASK, _PUBLISH_TASK
    current = _RADAR_TASK if task_holder == "radar" else _PUBLISH_TASK
    if current is not None and not current.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        task = loop.create_task(coro)
        if task_holder == "radar":
            _RADAR_TASK = task
        else:
            _PUBLISH_TASK = task
        return True
    except RuntimeError as exc:
        logger.warning("调度器启动失败(无事件循环): %s", exc)
        return False


def start_radar_scheduler() -> bool:
    """启动雷达调度(幂等; PROMO_RADAR_AUTO=off 返回 False)"""
    if not radar_enabled():
        logger.info("promo_radar_scheduler disabled (PROMO_RADAR_AUTO=off)")
        return False
    return _start("radar", _radar_loop())


def start_publish_scheduler() -> bool:
    """启动发布调度(幂等; PROMO_PUBLISH_AUTO=off 返回 False)"""
    if not publish_enabled():
        logger.info("promo_publish_scheduler disabled (PROMO_PUBLISH_AUTO=off)")
        return False
    return _start("publish", _publish_loop())


def stop_schedulers() -> None:
    """停止全部调度任务(测试清理/应用关闭用)"""
    global _RADAR_TASK, _PUBLISH_TASK
    for task in (_RADAR_TASK, _PUBLISH_TASK):
        if task is not None:
            task.cancel()
    _RADAR_TASK = None
    _PUBLISH_TASK = None
