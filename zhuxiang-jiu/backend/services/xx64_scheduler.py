"""64号·信值兑换管理 T+1 调度
(xx64_scheduler, P4)

计划(docs/64号_P4_价值锚定与治理层
详细设计.md §七):
    run_scheduled_tasks 一轮 =
        ① 指数快照(snapshot)
        + ② 回流(collect_feedback)
        + ③ 供需扫描(supply_demand)
        + ④ 校准检查(rate_check
          →46号)
        + 调度留痕(scheduler_run)

开关(三开关铁律):
    XX64_LEARN_MODE=on  开启调度
    (默认 off——观测面与手动
     触发不受影响)

范式: 对齐 PAY60 调度
(pay60_scheduler.py)——
run_scheduled_tasks 可独立
调用(测试/手动触发); 三任务
fail-soft 互不阻塞。
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger("xx64_sched")

_TASK = None  # 后台循环句柄


def scheduler_enabled() -> bool:
    """调度总开关(XX64_LEARN_MODE
    =on, 默认 off)"""
    return os.environ.get(
        "XX64_LEARN_MODE", "off"
    ).lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度间隔(XX64_SCHED_INTERVAL
    秒, 默认 86400=T+1)"""
    try:
        return max(60, int(
            os.environ.get(
                "XX64_SCHED_INTERVAL",
                "86400")))
    except ValueError:
        return 86400


async def run_scheduled_tasks() -> dict:
    """执行一轮 T+1 四任务
    (可独立调用——测试与手动触发)"""
    result = {"snapshot": None,
              "collect": None,
              "supplyDemand": None,
              "rateCheck": None,
              "errors": []}

    # ① 指数快照
    try:
        from services.xx64_anchor_service import (
            Xx64AnchorService,
        )
        snap = await (
            Xx64AnchorService()
            .snapshot())
        result["snapshot"] = {
            "skipped": snap.get(
                "skipped"),
            "avgPrice": snap.get(
                "avgPrice"),
            "purchasingPower":
                snap.get(
                    "purchasingPower"),
            "samples": snap.get(
                "samples"),
        }
    except Exception as exc:
        logger.warning(
            "xx64_sched_snap_failed: %s",
            exc)
        result["errors"].append(
            f"snapshot:{exc}")

    # ② 回流
    try:
        from services.xx64_learn_service import (
            Xx64LearnService,
        )
        collect = await (
            Xx64LearnService()
            .collect_feedback())
        result["collect"] = {
            "scanned": collect.get(
                "scanned"),
            "labeled": collect.get(
                "labeled"),
            "skipped": collect.get(
                "skipped"),
            "poolSubmitted":
                collect.get(
                    "poolSubmitted"),
        }
    except Exception as exc:
        logger.warning(
            "xx64_sched_collect_failed:"
            " %s", exc)
        result["errors"].append(
            f"collect:{exc}")

    # ③ 供需扫描
    try:
        from services.xx64_anchor_service import (
            Xx64AnchorService,
        )
        sd = await (
            Xx64AnchorService()
            .supply_demand_scan())
        result["supplyDemand"] = {
            "scannedProducts":
                sd.get(
                    "scannedProducts"),
            "alerts": len(
                sd.get("alerts") or []),
        }
    except Exception as exc:
        logger.warning(
            "xx64_sched_sd_failed: %s",
            exc)
        result["errors"].append(
            f"supplyDemand:{exc}")

    # ④ 校准检查(46号)
    try:
        from services.xx64_anchor_service import (
            Xx64AnchorService,
        )
        rate = await (
            Xx64AnchorService()
            .rate_check())
        result["rateCheck"] = {
            "status": rate.get(
                "status"),
            "submitted": rate.get(
                "submitted"),
        }
    except Exception as exc:
        logger.warning(
            "xx64_sched_rate_failed: %s",
            exc)
        result["errors"].append(
            f"rateCheck:{exc}")

    # 调度留痕
    try:
        from repositories.xx64_repository import (
            Xx64Repository,
        )
        repo = Xx64Repository()
        await repo.add_event({
            "eventId": await
            repo.next_event_id(),
            "orderId": 0,
            "eventType":
                "scheduler_run",
            "detail": result,
            "createdAt": ts(),
        })
    except Exception as exc:
        logger.warning(
            "xx64_sched_event_failed: %s",
            exc)

    logger.info(
        "xx64_scheduler_done snap=%s "
        "collect=%s",
        result["snapshot"],
        result["collect"])
    return result


async def _scheduler_loop() -> None:
    """后台循环(间隔可配)"""
    while True:
        try:
            await run_scheduled_tasks()
        except Exception as exc:
            logger.warning(
                "xx64_sched_round_failed: %s",
                exc)
        await asyncio.sleep(
            scheduler_interval_seconds())


def start_scheduler() -> bool:
    """启动后台调度(幂等——
    已运行跳过)"""
    global _TASK
    if not scheduler_enabled():
        logger.info(
            "xx64_scheduler off "
            "(XX64_LEARN_MODE != on)")
        return False
    if _TASK and not _TASK.done():
        return True
    _TASK = asyncio.get_event_loop() \
        .create_task(_scheduler_loop())
    logger.info(
        "xx64_scheduler started "
        "(interval=%ss)",
        scheduler_interval_seconds())
    return True


def stop_scheduler() -> None:
    """停止后台调度"""
    global _TASK
    if _TASK and not _TASK.done():
        _TASK.cancel()
    _TASK = None
    logger.info("xx64_scheduler stopped")
