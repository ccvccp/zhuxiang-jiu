"""61号·AI智能系统升级决策 T+1 反馈回流调度器
(dm61_scheduler, 默认关闭)

仿 60/63号调度器范式:
    周期默认 24h(DM61_LEARN_INTERVAL 可调,
    下限 5 分钟)——每轮执行:
    ① RLHF 反馈回流补标(collect——幂等:
       decisionId 1:1 终态跳过
       +决策置信度校准预警)

环境开关(三开关铁律之一):
    DM61_LEARN_MODE=on        开启学习调度
                              (默认 off——零影响)
    DM61_LEARN_INTERVAL=N     调度周期秒
                              (默认 86400)

接入方式(main.py lifespan):
    from services.dm61_scheduler import (
        start_scheduler)
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger("dm61_scheduler")

MODEL_VERSION = "v1-dm61-scheduler"


def scheduler_enabled() -> bool:
    """调度总开关(DM61_LEARN_MODE=on, 默认 off)"""
    return os.environ.get(
        "DM61_LEARN_MODE", "off"
    ).strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 24h(下限 5 分钟)"""
    try:
        value = int(os.environ.get(
            "DM61_LEARN_INTERVAL", "86400"))
        return max(300, value)
    except ValueError:
        return 86400


async def run_scheduled_tasks() -> dict:
    """执行一轮 T+1 反馈回流
    (可独立调用——测试与手动触发)"""
    result = {"collect": None, "errors": []}
    try:
        from services.dm61_learn_service import (
            Dm61LearnService,
        )
        collect = await (
            Dm61LearnService().collect_feedback())
        result["collect"] = {
            "scanned": collect.get("scanned"),
            "labeled": collect.get("labeled"),
            "skipped": collect.get("skipped"),
            "poolSubmitted":
                collect.get("poolSubmitted"),
            "signals": collect.get("signals"),
            "calibrationAlert":
                collect.get("calibrationAlert"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "dm61_sched_collect_failed: %s",
            exc)
        result["errors"].append(f"collect:{exc}")

    # 调度层留痕
    try:
        from repositories.dm61_repository import (
            Dm61Repository,
        )
        repo = Dm61Repository()
        event_id = await repo.next_event_id()
        await repo.add_event({
            "eventId": event_id,
            "requestId": 0,
            "eventType": "scheduler_run",
            "detail": {
                "collect": result["collect"],
                "errors": result["errors"][-10:],
            },
            "createdAt": ts(),
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "dm61_sched_event_failed: %s", exc)

    logger.info("dm61_scheduler_done collect=%s",
                result["collect"])
    return result


async def _scheduler_loop() -> None:
    interval = scheduler_interval_seconds()
    logger.info("dm61_scheduler started "
                "interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_scheduled_tasks()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "学习调度异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("dm61_scheduler disabled "
                    "(DM61_LEARN_MODE != on)")
        return False
    if _scheduler_task is not None and \
            not _scheduler_task.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _scheduler_task = loop.create_task(
            _scheduler_loop())
        return True
    except RuntimeError as exc:
        logger.warning(
            "调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    """停止后台调度任务(测试清理用)"""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None
