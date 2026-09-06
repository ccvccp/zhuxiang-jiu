"""57号·AI智能知识库 T+1 补标调度器
(kb57_scheduler, 默认关闭)

仿 55/56号 scheduler 范式:
    周期默认 24h(KB57_LEARN_INTERVAL 可调,
    下限 5 分钟)——每轮执行决策回流补标
    (collect——幂等: pooled 终态跳过)
    + 有效期健康检查(freshness——过期种子
    自动降权触发更新)。

环境开关(双开关铁律):
    KB57_LEARN_MODE=on       开启学习调度
                              (默认 off——零影响)
    KB57_LEARN_INTERVAL=N    调度周期秒(默认 86400)

接入方式(main.py lifespan):
    from services.kb57_scheduler import (
        start_scheduler)
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger("kb57_scheduler")

MODEL_VERSION = "v1-kb57-scheduler"


def scheduler_enabled() -> bool:
    """调度总开关(KB57_LEARN_MODE=on, 默认 off)"""
    return os.environ.get(
        "KB57_LEARN_MODE", "off"
    ).strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 24h(下限 5 分钟)"""
    try:
        value = int(os.environ.get(
            "KB57_LEARN_INTERVAL", "86400"))
        return max(300, value)
    except ValueError:
        return 86400


async def run_scheduled_tasks() -> dict:
    """执行一轮 T+1 补标+有效期检查
    (可独立调用——测试与手动触发)"""
    result = {"collect": None,
              "freshness": None, "errors": []}
    try:
        from services.kb57_feedback_loop_service import (
            Kb57FeedbackLoopService,
        )
        collect = await (
            Kb57FeedbackLoopService()
            .collect_feedback())
        result["collect"] = {
            "scanned": collect.get("scanned"),
            "labeled": collect.get("labeled"),
            "poolSubmitted":
                collect.get("poolSubmitted"),
            "signals": collect.get("signals"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "kb57_sched_collect_failed: %s", exc)
        result["errors"].append(f"collect:{exc}")

    try:
        from services.kb57_seed_service import (
            Kb57SeedService,
        )
        fresh = await (
            Kb57SeedService().freshness_check())
        result["freshness"] = {
            "scanned": fresh.get("scanned"),
            "expired": fresh.get("expired"),
            "demoted": fresh.get("demoted"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "kb57_sched_fresh_failed: %s", exc)
        result["errors"].append(f"freshness:{exc}")

    # 调度层留痕
    try:
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        repo = Kb57Repository()
        event_id = await repo.next_event_id()
        await repo.add_event({
            "eventId": event_id,
            "gapId": 0,
            "eventType": "scheduler_run",
            "detail": {
                "collect": result["collect"],
                "freshness": result["freshness"],
                "errors": result["errors"][-10:],
            },
            "createdAt": ts(),
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "kb57_sched_event_failed: %s", exc)

    logger.info("kb57_scheduler_done collect=%s "
                "freshness=%s",
                result["collect"],
                result["freshness"])
    return result


async def _scheduler_loop() -> None:
    interval = scheduler_interval_seconds()
    logger.info("kb57_scheduler started "
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
        logger.info("kb57_scheduler disabled "
                    "(KB57_LEARN_MODE != on)")
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
