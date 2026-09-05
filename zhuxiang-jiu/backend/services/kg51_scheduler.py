"""51号·小竹可信知识图谱 巡检调度器(kg51_scheduler)

计划(docs/51号_小竹可信知识图谱实施计划.md §八 P4):
    日度巡检调度轨——SOP 阶段6"每日巡检"的调度化。

范式: ai_governance_scheduler(46号 P6)平移:
    - KG_INSPECT_MODE 默认 off(off=零 task 零影响)
    - KG_INSPECT_INTERVAL 默认 86400s(下限 300s
      防忙循环)
    - 先 sleep 后执行(首轮延迟一轮)
    - fail-soft 循环异常继续运行
    - stats 留痕(runs/lastRunAt/lastInspection)

巡检内容(每轮):
    run_inspection(三指标快照)——治理端点
    POST /inspect/run 的调度化版。
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger("kg51_scheduler")

DEFAULT_INTERVAL = 86400
MIN_INTERVAL = 300


def scheduler_enabled() -> bool:
    return os.environ.get(
        "KG_INSPECT_MODE", "off").strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    try:
        value = int(os.environ.get(
            "KG_INSPECT_INTERVAL",
            str(DEFAULT_INTERVAL)))
        return max(MIN_INTERVAL, value)
    except ValueError:
        return DEFAULT_INTERVAL


async def run_scheduled_inspection() -> dict:
    """单轮巡检(调度/手动可共用)"""
    from repositories.kg51_repository import (
        Kg51Repository,
    )
    from services.kg51_governance_service import (
        Kg51GovernanceService,
    )
    inspection = await Kg51GovernanceService(
    ).run_inspection()
    stats = (await Kg51Repository(
    ).get_scheduler_stats()) or {}
    stats = {
        "runs": int(stats.get("runs", 0)) + 1,
        "lastRunAt": ts(),
        "lastIntervalSeconds":
            scheduler_interval_seconds(),
        "lastInspection": {
            "inspectionId": inspection.get(
                "inspectionId"),
            "completeness": inspection.get(
                "completeness"),
            "consistency": inspection.get(
                "consistency"),
            "freshness": inspection.get("freshness"),
            "issues": len(inspection.get("issues")
                          or []),
        },
    }
    await Kg51Repository().save_scheduler_stats(stats)
    logger.info("kg51_scheduled_inspection runs=%s",
                stats["runs"])
    return stats


async def _scheduler_loop() -> None:
    interval = scheduler_interval_seconds()
    while True:
        await asyncio.sleep(interval)
        try:
            await run_scheduled_inspection()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "图谱巡检调度异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动调度器(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("kg51_scheduler disabled "
                    "(KG_INSPECT_MODE != on)")
        return False
    if _scheduler_task is not None \
            and not _scheduler_task.done():
        return True
    loop = asyncio.get_event_loop()
    _scheduler_task = loop.create_task(_scheduler_loop())
    logger.info("kg51_scheduler started "
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
