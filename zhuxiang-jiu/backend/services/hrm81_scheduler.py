"""81号·HRM 资源统筹调度器(水位周期评估+决策)

惯例对齐 ibms_patrol_scheduler / growth80_escrow_scheduler:
    - main.py startup 启动, 幂等; 决策逻辑独立 run_hrm_decision
      可单测
    - 双闸: HRM81_AUTO(环境, 默认 off) + HRM81_MODE(业务分段,
      默认 off)——AUTO 只停扫描, 水位缓存与 acquire_slot 闸门
      语义仍随 MODE 生效(手动 run 端点可触发)

环境开关:
    HRM81_AUTO=off|on        调度总开关(默认 off)
    HRM81_INTERVAL=N         评估周期秒(默认 300, 下限 60)
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def scheduler_enabled() -> bool:
    """调度总开关(HRM81_AUTO 默认 off——74号惯例)"""
    return os.environ.get("HRM81_AUTO", "off").strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    try:
        value = int(os.environ.get("HRM81_INTERVAL", "300"))
        return max(60, value)
    except ValueError:
        return 300


def scheduler_running() -> bool:
    return _scheduler_task is not None and not _scheduler_task.done()


async def _scheduler_loop() -> None:
    interval = scheduler_interval_seconds()
    logger.info("hrm81_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            from services.hrm81_service import run_hrm_decision
            await run_hrm_decision()
        except ValueError:
            pass   # MODE=off: 调度挂载但未放行(双闸语义)
        except Exception as exc:  # noqa: BLE001
            logger.warning("HRM 决策异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("hrm81_scheduler disabled (HRM81_AUTO=off)")
        return False
    if _scheduler_task is not None and not _scheduler_task.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _scheduler_task = loop.create_task(_scheduler_loop())
        return True
    except RuntimeError:
        logger.warning("hrm81_scheduler no event loop")
        return False
