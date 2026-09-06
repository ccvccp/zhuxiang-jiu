"""63号·AI智能后台管理 T+1 决策回流调度器
(ab63_scheduler, 默认关闭)

仿 58号 ii58_scheduler 范式:
    周期默认 24h(AB63_LEARN_INTERVAL 可调,
    下限 5 分钟)——每轮执行:
    ① 决策回流补标(collect——幂等:
       pooledFeedbackId 终态跳过
       +自动过审错误率预警)
    ② 培训窗口过期检查(expire——
       7 日转化跟踪)
    ③ 高频驳回点培训推送(push——
       高频驳回触发)

环境开关(三开关铁律之一):
    AB63_LEARN_MODE=on        开启学习调度
                              (默认 off——零影响)
    AB63_LEARN_INTERVAL=N     调度周期秒
                              (默认 86400)

接入方式(main.py lifespan):
    from services.ab63_scheduler import (
        start_scheduler)
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger("ab63_scheduler")

MODEL_VERSION = "v1-ab63-scheduler"


def scheduler_enabled() -> bool:
    """调度总开关(AB63_LEARN_MODE=on, 默认 off)"""
    return os.environ.get(
        "AB63_LEARN_MODE", "off"
    ).strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 24h(下限 5 分钟)"""
    try:
        value = int(os.environ.get(
            "AB63_LEARN_INTERVAL", "86400"))
        return max(300, value)
    except ValueError:
        return 86400


async def run_scheduled_tasks() -> dict:
    """执行一轮 T+1 决策回流+培训推送
    (可独立调用——测试与手动触发)"""
    result = {"collect": None,
              "training": None, "errors": []}
    try:
        from services.ab63_learn_service import (
            Ab63LearnService,
        )
        collect = await (
            Ab63LearnService().collect_feedback())
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
            "ab63_sched_collect_failed: %s", exc)
        result["errors"].append(f"collect:{exc}")

    try:
        from services.ab63_training_service import (
            Ab63TrainingService,
        )
        training_svc = Ab63TrainingService()
        expired = await \
            training_svc.expire_overdue()
        pushed = await training_svc.push()
        result["training"] = {
            "expired": expired.get("expired"),
            "pushed": pushed.get("pushed"),
            "highFreqPoints":
                pushed.get("highFreqPoints"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ab63_sched_training_failed: %s",
            exc)
        result["errors"].append(f"training:{exc}")

    # 调度层留痕
    try:
        from repositories.ab63_repository import (
            Ab63Repository,
        )
        repo = Ab63Repository()
        event_id = await repo.next_event_id()
        await repo.add_event({
            "eventId": event_id,
            "grantId": 0,
            "eventType": "scheduler_run",
            "detail": {
                "collect": result["collect"],
                "training": result["training"],
                "errors": result["errors"][-10:],
            },
            "createdAt": ts(),
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ab63_sched_event_failed: %s", exc)

    logger.info("ab63_scheduler_done collect=%s",
                result["collect"])
    return result


async def _scheduler_loop() -> None:
    interval = scheduler_interval_seconds()
    logger.info("ab63_scheduler started "
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
        logger.info("ab63_scheduler disabled "
                    "(AB63_LEARN_MODE != on)")
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
