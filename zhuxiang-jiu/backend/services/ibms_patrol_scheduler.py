"""74号·IBMS 巡检定时调度器(无人值守轨, 默认 off——分段铁律)

惯例对齐 order_timeout_scheduler(P1-13):
    - main.py startup 启动, 幂等; 扫描逻辑独立 run_patrol 可单测
    - IBMS_PATROL_MODE=off 时 run_patrol 本身 409(双保险), 调度器
      亦不挂载(IBMS_PATROL_AUTO 默认 off)

环境开关:
    IBMS_PATROL_AUTO=off                关闭调度(默认)
    IBMS_PATROL_MODE=off|shadow|on      巡检分段(off 默认, 409)
    IBMS_PATROL_INTERVAL=N              巡检周期秒(默认 3600, 下限 300)

接入(main.py startup):
    from services.ibms_patrol_scheduler import start_scheduler
    start_scheduler()
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def scheduler_enabled() -> bool:
    """调度总开关(IBMS_PATROL_AUTO=off 关闭, 默认 off——与巡检模式
    双闸: 调度挂了但 MODE=off 时 run_patrol 仍 409, 双保险)"""
    return os.environ.get("IBMS_PATROL_AUTO", "off").strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    """巡检周期(秒), 默认 3600(小时级巡检——38号 P5 语义: 高频无意义,
    慢信号域), 下限 300 防忙循环"""
    try:
        value = int(os.environ.get("IBMS_PATROL_INTERVAL", "3600"))
        return max(300, value)
    except ValueError:
        return 3600


async def _scheduler_loop() -> None:
    """后台循环: 周期性巡检(默认 shadow 语义由 MODE 决定,
    调度只负责定时——mode 语义全在 run_patrol)"""
    from services.ibms_patrol_service import run_patrol
    interval = scheduler_interval_seconds()
    logger.info("ibms_patrol_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_patrol()
        except ValueError:
            pass   # MODE=off: 静默跳过(双保险, 不刷日志)
        except Exception as exc:  # noqa: BLE001
            logger.warning("IBMS 巡检异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("ibms_patrol_scheduler disabled (IBMS_PATROL_AUTO=off)")
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
    except RuntimeError as exc:
        logger.warning("IBMS 调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    """停止后台调度(测试清理用)"""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def scheduler_running() -> bool:
    """调度器是否在运行(测试/监控用)"""
    return _scheduler_task is not None and not _scheduler_task.done()
