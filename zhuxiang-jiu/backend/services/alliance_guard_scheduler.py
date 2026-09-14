"""37号·AI智能网站同盟大模型——护栏实测自动巡检调度器
(alliance_guard_scheduler)

功能:
    - 周期触发 auto_guard_check(实测三指标聚合→
      guard_check 链路, 恶化>3% 自动暂停)
    - 默认周期 24 小时(ALLIANCE37_GUARD_INTERVAL_SECONDS 可调)
    - 单轮失败不影响下一轮(异常吞掉记日志)

指标口径(确定性——LLM 禁入):
    - refundRate = 窗口内冲正结算 / 窗口内总结算
    - complaintRate = 窗口内差评(score≤2) / 窗口内总评价
    - terminationRate = 窗口内终止商户 / 全量商户
    - 样本门: 结算<5 / 评价<5 / 商户<3 → 指标记 0

环境开关:
    ALLIANCE37_GUARD_AUTO=off                  关闭调度(默认关闭)
    ALLIANCE37_GUARD_INTERVAL_SECONDS=N        调度周期(默认 86400)

接入方式(main.py startup):
    from services.alliance_guard_scheduler import start_scheduler
    start_scheduler()
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_SCHEDULER_TASK: asyncio.Task | None = None


def scheduler_enabled() -> bool:
    return os.environ.get(
        "ALLIANCE37_GUARD_AUTO", "off"
    ).strip().lower() != "off"


def scheduler_interval_seconds() -> int:
    try:
        return max(60, int(os.environ.get(
            "ALLIANCE37_GUARD_INTERVAL_SECONDS", "86400")))
    except ValueError:
        return 86400


async def _scheduler_loop() -> None:
    interval = scheduler_interval_seconds()
    logger.info("alliance_guard_scheduler started "
                "interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            from services.alliance_mode_service import (
                AllianceModeService,
            )
            result = await AllianceModeService() \
                .auto_guard_check()
            guard = result.get("guard") or {}
            if guard.get("breached"):
                logger.warning(
                    "alliance_guard_auto_breached: %s",
                    guard.get("breaches"))
            else:
                metrics = (result.get("computed")
                           or {}).get("metrics") or {}
                logger.info(
                    "alliance_guard_auto_ok metrics=%s",
                    metrics)
        except Exception as exc:
            logger.warning(
                "护栏自动巡检异常(继续运行): %s", exc)


def start_scheduler() -> bool:
    """启动护栏巡检调度(幂等; ALLIANCE37_GUARD_AUTO=off
    返回 False)"""
    global _SCHEDULER_TASK
    if not scheduler_enabled():
        logger.info("alliance_guard_scheduler disabled "
                    "(ALLIANCE37_GUARD_AUTO=off)")
        return False
    if _SCHEDULER_TASK is not None \
            and not _SCHEDULER_TASK.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _SCHEDULER_TASK = loop.create_task(_scheduler_loop())
        return True
    except RuntimeError as exc:
        logger.warning("调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    """停止调度任务(测试清理/应用关闭用)"""
    global _SCHEDULER_TASK
    if _SCHEDULER_TASK is not None:
        _SCHEDULER_TASK.cancel()
    _SCHEDULER_TASK = None
