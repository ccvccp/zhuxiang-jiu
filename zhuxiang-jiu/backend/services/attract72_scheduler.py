"""72号·AI智能自动引流大模型 健康度日度调度器(attract72_scheduler)

计划(2026-09-29 生产检查立项):
    健康度三指标检查无专属调度器——上次检查 2026-09-14,
    15 天零活动(insufficient 数据老化) → 日度调度化。

范式: kg51_scheduler(51号日度巡检)平移:
    - ATTRACT72_HEALTH_AUTO 默认 off(off=零 task 零影响)
    - ATTRACT72_HEALTH_INTERVAL 默认 86400s(下限 300s
      防忙循环)
    - 先 sleep 后执行(首轮延迟一轮)
    - fail-soft 循环异常继续运行
    - check_health 自落 meta_health 台账(checkId 累计),
      调度层仅日志留痕——健康检查属观测面动作(制动/
      模式门控跟随 service 内部惯例, 观测面不被冻结);
      红队/升档评估保持手动 SOP(真实 clickId 前提)
"""

import asyncio
import logging
import os

logger = logging.getLogger("attract72_scheduler")

DEFAULT_INTERVAL = 86400
MIN_INTERVAL = 300


def scheduler_enabled() -> bool:
    return os.environ.get(
        "ATTRACT72_HEALTH_AUTO", "off"
    ).strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    try:
        value = int(os.environ.get(
            "ATTRACT72_HEALTH_INTERVAL",
            str(DEFAULT_INTERVAL)))
        return max(MIN_INTERVAL, value)
    except ValueError:
        return DEFAULT_INTERVAL


async def run_scheduled_health() -> dict:
    """单轮健康度检查(调度/手动可共用)"""
    from services.attract72_p6_service import (
        Attract72P6Service,
    )
    health = await Attract72P6Service().check_health()
    logger.info(
        "attract72_scheduled_health checkId=%s verdict=%s",
        health.get("checkId"), health.get("verdict"))
    return health


async def _scheduler_loop() -> None:
    interval = scheduler_interval_seconds()
    while True:
        await asyncio.sleep(interval)
        try:
            await run_scheduled_health()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "引流健康度调度异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动调度器(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("attract72_scheduler disabled "
                    "(ATTRACT72_HEALTH_AUTO != on)")
        return False
    if _scheduler_task is not None \
            and not _scheduler_task.done():
        return True
    loop = asyncio.get_event_loop()
    _scheduler_task = loop.create_task(_scheduler_loop())
    logger.info("attract72_scheduler started "
                "interval=%ss",
                scheduler_interval_seconds())
    return True


def stop_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def scheduler_running() -> bool:
    return (_scheduler_task is not None
            and not _scheduler_task.done())
