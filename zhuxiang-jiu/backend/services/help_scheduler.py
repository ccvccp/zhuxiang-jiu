"""AI智能叫帮大模型(67号) 护栏自动巡检调度器

大模型二代(全站范式——图标主题/条款协议/PDM 同款):
    HELP_GUARD_AUTO 默认 on(护栏是保护机制)
    HELP_GUARD_INTERVAL 默认 3600s(最小 60s)

巡检三指标(确定性聚合——分母 0 取 0, 冷启动不误判;
LLM 禁入判定链; 口径详见 help_mode_service):
    爽约率 cancelRate = cancelled / (completed + cancelled)
    差评率 lowReviewRate = score<=2 评价 / 总评价
    在途滞留率 stuckRate = (matched + in_progress) /
        (matched + in_progress + completed)

聚合口径: help_repository list_orders/list_reviews
(上限 10000)确定性聚合——patrol() 委托 mode service。

run_guard_patrol 可独立调用(控制面
POST /api/help/mode/guard 缺省巡检)。
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_GUARD_TASK = None


def guard_patrol_enabled() -> bool:
    """护栏巡检开关(HELP_GUARD_AUTO,
    默认 on——护栏自动暂停是保护机制)"""
    return os.environ.get(
        "HELP_GUARD_AUTO", "on").lower() == "on"


def guard_interval_seconds() -> int:
    """护栏巡检间隔(默认 3600s)"""
    try:
        return max(60, int(os.environ.get(
            "HELP_GUARD_INTERVAL", "3600")))
    except ValueError:
        return 3600


async def run_guard_patrol() -> dict:
    """护栏巡检一轮(确定性聚合 + guard_check——可独立调用)"""
    from services.help_mode_service import (
        HelpModeService,
    )
    return await HelpModeService().patrol()


async def _guard_loop() -> None:
    """护栏巡检循环(整轮异常不退出)"""
    while True:
        try:
            r = await run_guard_patrol()
            if r.get("pausedNow"):
                logger.warning(
                    "help_guard_patrol_paused: %s",
                    r.get("breaches"))
            else:
                logger.info(
                    "help_guard_patrol_ok metrics=%s",
                    r.get("metrics"))
        except Exception as exc:
            logger.error(
                "help_guard_patrol_fail: %s", exc)
        await asyncio.sleep(
            guard_interval_seconds())


def start_guard_loop() -> bool:
    """启动护栏巡检循环(幂等; 未启用返回 False)"""
    if not guard_patrol_enabled():
        return False
    global _GUARD_TASK
    if _GUARD_TASK and not _GUARD_TASK.done():
        return True
    _GUARD_TASK = asyncio.get_event_loop() \
        .create_task(_guard_loop())
    logger.info("help_guard_loop_started interval=%ss",
                guard_interval_seconds())
    return True
