"""超级会员智能库 日度评估调度器(mtier_scheduler)

范式对齐 member_level_scheduler / hook86_scheduler:
    启动首轮 + 日度循环 + fail-soft; MTIER_AUTO=off 关闭(默认开)。
评估幂等(仅刷新 auto 标签与快照), 生命周期变更才写留痕。
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None
_last_run: dict = {}


def scheduler_enabled() -> bool:
    return os.environ.get("MTIER_AUTO", "on").strip().lower() not in (
        "off", "0", "false", "no")


def interval_seconds() -> int:
    try:
        return max(300, int(os.environ.get(
            "MTIER_INTERVAL", "86400")))
    except ValueError:
        return 86400


def last_run() -> dict:
    return dict(_last_run)


async def run_evaluate() -> dict:
    """单轮评估(调度/手动共用)"""
    global _last_run
    from services.mtier_service import MtierService
    report = await MtierService().evaluate_all()
    _last_run = report
    logger.info("mtier_evaluated members=%s evaluated=%s",
                report.get("members"), report.get("evaluated"))
    return report


async def _loop() -> None:
    await run_evaluate()
    while True:
        await asyncio.sleep(interval_seconds())
        try:
            await run_evaluate()
        except Exception:                 # noqa: BLE001 fail-soft
            logger.exception("mtier_evaluate_failed")


def start_scheduler() -> None:
    global _task
    if not scheduler_enabled() or _task is not None:
        return
    _task = asyncio.get_event_loop().create_task(_loop())
    logger.info("mtier_scheduler started interval=%ss",
                interval_seconds())


def stop_scheduler() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
