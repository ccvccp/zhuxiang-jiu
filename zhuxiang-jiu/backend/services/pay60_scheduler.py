"""60号·AI智能支付管理 T+1 决策回流调度器
(pay60_scheduler, 默认关闭)

仿 58号 ii58_scheduler/63号 ab63_scheduler 范式:
    周期默认 24h(PAY60_LEARN_INTERVAL 可调,
    下限 5 分钟)——每轮执行:
    ① 支付事件回流(collect——幂等:
       payId 1:1 pooledFeedbackId
       终态跳过)
    ② 对账批次(recon——差异检测
       +冲正建议留痕)
    ③ 现金流预测缺口预警
       (forecast——T+1 报告留痕)

环境开关(四开关铁律之一):
    PAY60_LEARN_MODE=on        开启学习调度
                              (默认 off——零影响)
    PAY60_LEARN_INTERVAL=N     调度周期秒
                              (默认 86400)

接入方式(main.py lifespan):
    from services.pay60_scheduler import (
        start_scheduler)
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger("pay60_scheduler")

MODEL_VERSION = "v1-pay60-scheduler"


def scheduler_enabled() -> bool:
    """调度总开关(PAY60_LEARN_MODE=on, 默认 off)"""
    return os.environ.get(
        "PAY60_LEARN_MODE", "off"
    ).strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 24h(下限 5 分钟)"""
    try:
        value = int(os.environ.get(
            "PAY60_LEARN_INTERVAL", "86400"))
        return max(300, value)
    except ValueError:
        return 86400


async def run_scheduled_tasks() -> dict:
    """执行一轮 T+1 支付回流+对账+预测
    (可独立调用——测试与手动触发)"""
    result = {"collect": None,
              "recon": None,
              "forecast": None,
              "errors": []}

    # ① 支付事件回流
    try:
        from services.pay60_learn_service import (
            Pay60LearnService,
        )
        collect = await (
            Pay60LearnService()
            .collect_feedback())
        result["collect"] = {
            "scanned": collect.get("scanned"),
            "labeled": collect.get("labeled"),
            "skipped": collect.get("skipped"),
            "poolSubmitted":
                collect.get("poolSubmitted"),
            "signals": collect.get("signals"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pay60_sched_collect_failed: %s",
            exc)
        result["errors"].append(
            f"collect:{exc}")

    # ② 对账批次(差异自动检测)
    try:
        from services.pay60_recon_service import (
            Pay60ReconService,
        )
        recon = await (
            Pay60ReconService().run_recon())
        result["recon"] = {
            "scanned": recon.get("scanned"),
            "matched": recon.get("matched"),
            "differences":
                recon.get("differences"),
            "autoPending":
                recon.get("autoPending"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pay60_sched_recon_failed: %s",
            exc)
        result["errors"].append(
            f"recon:{exc}")

    # ③ 现金流预测(缺口预警)
    try:
        from services.pay60_learn_service import (
            Pay60LearnService,
        )
        forecast = await (
            Pay60LearnService().forecast())
        result["forecast"] = {
            "net": forecast.get(
                "forecast", {}).get(
                    "net"),
            "gapAlert":
                forecast.get("gapAlert"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pay60_sched_forecast_failed: %s",
            exc)
        result["errors"].append(
            f"forecast:{exc}")

    # 调度层留痕
    try:
        from repositories.pay60_repository import (
            Pay60Repository,
        )
        repo = Pay60Repository()
        event_id = await repo.next_event_id()
        await repo.add_event({
            "eventId": event_id,
            "payId": 0,
            "eventType": "scheduler_run",
            "detail": {
                "collect":
                    result["collect"],
                "recon":
                    result["recon"],
                "forecast":
                    result["forecast"],
                "errors":
                    result["errors"][-10:],
            },
            "createdAt": ts(),
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pay60_sched_event_failed: %s",
            exc)

    logger.info(
        "pay60_scheduler_done collect=%s "
        "recon=%s",
        result["collect"], result["recon"])
    return result


async def _scheduler_loop() -> None:
    interval = scheduler_interval_seconds()
    logger.info("pay60_scheduler started "
                "interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_scheduled_tasks()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "学习调度异常(继续运行): %s",
                exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("pay60_scheduler disabled "
                    "(PAY60_LEARN_MODE != on)")
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
            "调度器启动失败(无事件循环): %s",
            exc)
        return False


def stop_scheduler() -> None:
    """停止后台调度任务(测试清理用)"""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None
