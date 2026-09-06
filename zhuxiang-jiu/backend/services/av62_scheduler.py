"""62号·AI智能无形资产估值 T+1 验证回流调度器
(av62_scheduler, 默认关闭)

仿 60/61/63号调度器范式:
    周期默认 24h(AV62_LEARN_INTERVAL
    可调, 下限 5 分钟)——每轮执行:
    ① 验证回流补标(collect——幂等:
       assessId 1:1 已池化跳过
       +偏差预警建议经 46号)
    ② 衰减批量结算(decay——流动性
       档案全量刷新+decaying 标记)

环境开关(三开关铁律之一):
    AV62_LEARN_MODE=on        开启学习调度
                              (默认 off——零影响)
    AV62_LEARN_INTERVAL=N     调度周期秒
                              (默认 86400)

接入方式(main.py lifespan):
    from services.av62_scheduler import (
        start_scheduler)
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger("av62_scheduler")

MODEL_VERSION = "v1-av62-scheduler"


def scheduler_enabled() -> bool:
    """调度总开关(AV62_LEARN_MODE=on, 默认 off)"""
    return os.environ.get(
        "AV62_LEARN_MODE", "off"
    ).strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 24h(下限 5 分钟)"""
    try:
        value = int(os.environ.get(
            "AV62_LEARN_INTERVAL", "86400"))
        return max(300, value)
    except ValueError:
        return 86400


async def run_scheduled_tasks() -> dict:
    """执行一轮 T+1 回流+衰减结算
    (可独立调用——测试与手动触发)"""
    result = {"collect": None,
              "decaySettle": None,
              "errors": []}
    try:
        from services.av62_learn_service import (
            Av62LearnService,
        )
        collect = await (
            Av62LearnService()
            .collect_verification())
        result["collect"] = {
            "scanned": collect.get("scanned"),
            "labeled": collect.get("labeled"),
            "skipped": collect.get("skipped"),
            "poolSubmitted":
                collect.get("poolSubmitted"),
            "signals": collect.get("signals"),
            "deviationAlert":
                collect.get("deviationAlert"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "av62_sched_collect_failed: %s",
            exc)
        result["errors"].append(f"collect:{exc}")

    try:
        from services.av62_learn_service import (
            Av62LearnService,
        )
        decay = await (
            Av62LearnService().settle_decay())
        result["decaySettle"] = {
            "refreshed": decay.get("refreshed"),
            "decaying": decay.get("decaying"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "av62_sched_decay_failed: %s", exc)
        result["errors"].append(f"decay:{exc}")

    # 调度层留痕
    try:
        from repositories.av62_repository import (
            Av62Repository,
        )
        repo = Av62Repository()
        event_id = await repo.next_event_id()
        await repo.add_event({
            "eventId": event_id,
            "assetId": 0,
            "eventType": "scheduler_run",
            "detail": {
                "collect": result["collect"],
                "decaySettle":
                    result["decaySettle"],
                "errors":
                    result["errors"][-10:],
            },
            "createdAt": ts(),
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "av62_sched_event_failed: %s", exc)

    logger.info("av62_scheduler_done collect=%s "
                "decay=%s", result["collect"],
                result["decaySettle"])
    return result


async def _scheduler_loop() -> None:
    interval = scheduler_interval_seconds()
    logger.info("av62_scheduler started "
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
        logger.info("av62_scheduler disabled "
                    "(AV62_LEARN_MODE != on)")
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
