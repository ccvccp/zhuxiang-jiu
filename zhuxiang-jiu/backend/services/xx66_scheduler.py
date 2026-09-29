"""66号·AI智能工程师大模块·定时调度器(P5a 监控维护自动化)

调度策略(对齐 promo_scheduler 既有范式):
    - 巡检: 周期 5 分钟(XX66_SCAN_INTERVAL_SECONDS 可调)——
      vitals 聚合→落快照→黄/红区起 27号链(纯记录零执行)
    - 对账: 周期 1 天(XX66_RECON_INTERVAL_SECONDS 可调)——
      四不变式全量只读校验(P3 引擎, danger 只生成建议书)
    - 单类任务失败不影响下一轮(异常吞掉记日志)

模式耦合(铁律——决策面语义由 XX66_MODE 把守):
    调度开关 XX66_SCAN_AUTO/XX66_RECON_AUTO(默认 off)只管
    "是否定时触发"; 每轮执行前置检查 XX66_MODE∈(shadow,assist):
    off 态跳过本轮(scan/reconcile 均为决策面, off=409 语义
    在调度层表现为静默跳过——与手动 HTTP 触发同口径)

环境开关:
    XX66_SCAN_AUTO=off                关闭巡检调度(默认关闭)
    XX66_SCAN_INTERVAL_SECONDS=N      巡检周期(默认 300=5min)
    XX66_RECON_AUTO=off              关闭对账调度(默认关闭)
    XX66_RECON_INTERVAL_SECONDS=N    对账周期(默认 86400=T+1,
                                      下限 3600 防误配忙循环)

接入方式(main.py startup):
    from services.xx66_scheduler import (
        start_scan_scheduler, start_recon_scheduler)
    start_scan_scheduler()
    start_recon_scheduler()
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_SCAN_TASK: asyncio.Task | None = None
_RECON_TASK: asyncio.Task | None = None

# 决策面执行态(scan/reconcile 均 409 于 off——调度层同口径跳过)
_ACTIVE_MODES = ("shadow", "assist")


def scan_enabled() -> bool:
    return os.environ.get("XX66_SCAN_AUTO", "off").strip().lower() != "off"


def recon_enabled() -> bool:
    return os.environ.get("XX66_RECON_AUTO", "off").strip().lower() != "off"


def _mode_active() -> bool:
    from services.xx66_service import current_mode
    return current_mode() in _ACTIVE_MODES


def _interval(env: str, default: int, floor: int = 60) -> int:
    try:
        return max(floor, int(os.environ.get(env, str(default))))
    except ValueError:
        return default


async def _scan_loop() -> None:
    interval = _interval("XX66_SCAN_INTERVAL_SECONDS", 300)
    logger.info("xx66_scan_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        if not _mode_active():
            logger.debug("xx66_scan_skipped (XX66_MODE=off)")
            continue
        try:
            from services.xx66_service import Xx66Service
            result = await Xx66Service().scan()
            logger.info("xx66_scan_scheduled snapshot=%s overall=%s",
                        result.get("snapshotId"),
                        result.get("vitals", {}).get("overall"))
        except Exception as exc:
            logger.warning("巡检调度异常(继续运行): %s", exc)


async def _recon_loop() -> None:
    interval = _interval("XX66_RECON_INTERVAL_SECONDS", 86400,
                          floor=3600)
    logger.info("xx66_recon_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        if not _mode_active():
            logger.debug("xx66_recon_skipped (XX66_MODE=off)")
            continue
        try:
            from services.xx66_recon_service import (
                Xx66ReconService,
            )
            result = await Xx66ReconService().run_recon()
            logger.info("xx66_recon_scheduled run=%s danger=%s",
                        result.get("runId"),
                        result.get("dangerCount"))
        except Exception as exc:
            logger.warning("对账调度异常(继续运行): %s", exc)


def _start(holder: str, coro) -> bool:
    global _SCAN_TASK, _RECON_TASK
    holders = {"scan": "_SCAN_TASK", "recon": "_RECON_TASK"}
    current = globals().get(holders[holder])
    if current is not None and not current.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        task = loop.create_task(coro)
        globals()[holders[holder]] = task
        return True
    except RuntimeError as exc:
        logger.warning("调度器启动失败(无事件循环): %s", exc)
        return False


def start_scan_scheduler() -> bool:
    """启动巡检调度(幂等; XX66_SCAN_AUTO=off 返回 False)"""
    if not scan_enabled():
        logger.info("xx66_scan_scheduler disabled (XX66_SCAN_AUTO=off)")
        return False
    return _start("scan", _scan_loop())


def start_recon_scheduler() -> bool:
    """启动对账调度(幂等; XX66_RECON_AUTO=off 返回 False)"""
    if not recon_enabled():
        logger.info("xx66_recon_scheduler disabled (XX66_RECON_AUTO=off)")
        return False
    return _start("recon", _recon_loop())


def stop_schedulers() -> None:
    """停止全部调度任务(测试清理/应用关闭用)"""
    global _SCAN_TASK, _RECON_TASK
    for task in (_SCAN_TASK, _RECON_TASK):
        if task is not None:
            task.cancel()
    _SCAN_TASK = None
    _RECON_TASK = None
