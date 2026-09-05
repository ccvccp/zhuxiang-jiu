"""52号·小竹语音可用性评估引擎 阈值告警日度调度器
(us52_alert_scheduler, 默认关闭)

仿 46号 ai_governance_scheduler 范式(独立模块 +
start/stop 幂等):
    - 周期默认 24h(US52_ALERT_SCAN_INTERVAL 可调,
      下限 5 分钟)
    - 每轮执行: 五维指标计算 → 静态基线+动态漂移
      告警(当日同键去重) → 统计留痕(供运维观察)

环境开关:
    US52_ALERT_MODE=on            开启告警调度
                                  (默认 off——默认零影响铁律;
                                  双开关: US52_MODE 计算面
                                  亦须 on 才真正执行)
    US52_ALERT_SCAN_INTERVAL=N    调度周期秒(默认 86400)

接入方式(main.py lifespan):
    from services.us52_alert_scheduler import (
        start_scheduler)
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, _k,
)

logger = logging.getLogger("us52_alert_scheduler")

MODEL_VERSION = "v1-us52-alert-scheduler"


def scheduler_enabled() -> bool:
    """调度总开关(US52_ALERT_MODE=on 开启, 默认 off)"""
    return os.environ.get(
        "US52_ALERT_MODE", "off").strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 24 小时"""
    try:
        value = int(os.environ.get(
            "US52_ALERT_SCAN_INTERVAL", "86400"))
        return max(300, value)   # 下限 5 分钟防忙循环
    except ValueError:
        return 86400


async def run_scheduled_alert_scan() -> dict:
    """执行一轮告警扫描(可独立调用, 便于测试与
    手动触发——scan 内部: 五维计算 → 快照留痕 →
    基线+漂移告警当日同键去重)"""
    from services.us52_service import (
        Us52MetricsService,
    )
    result = {"scan": None, "errors": []}
    try:
        scan = await Us52MetricsService().scan_alerts()
        result["scan"] = {
            "scanId": scan.get("scanId"),
            "metricCount": scan.get("metricCount"),
            "alertsNew": scan.get("alertsNew"),
            "alertsDeduped":
                scan.get("alertsDeduped"),
        }
    except ValueError as exc:
        # off 态语义: 计算面关(留痕 skip 不报错)
        result["errors"].append(f"off:{str(exc)[:60]}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("us52_sched_scan_failed: %s", exc)
        result["errors"].append(f"scan:{exc}")

    # 统计留痕(runs/lastRunAt/摘要——供运维观察)
    stats = {
        "runs": 0, "lastRunAt": ts(),
        "lastIntervalSeconds":
            scheduler_interval_seconds(),
        "lastScan": result["scan"],
        "lastErrors": result["errors"][-10:],
    }
    try:
        if is_redis_mode():
            client = await get_redis_client()
            prev = await client.hgetall(
                _k("us52", "alerts", "scheduler_stats"))
            if prev:
                stats["runs"] = int(
                    prev.get("runs") or 0) + 1
            await client.hset(
                _k("us52", "alerts", "scheduler_stats"),
                mapping={
                    k: (v if isinstance(v, str)
                        else str(v))
                    for k, v in stats.items()})
        else:
            from repositories.us52_repository import (
                Us52Repository,
            )
            repo = Us52Repository()
            repo._ensure_store()
            prev = repo.store.get(
                "_us52_alert_scheduler_stats") or {}
            stats["runs"] = int(
                prev.get("runs") or 0) + 1
            repo.store[
                "_us52_alert_scheduler_stats"] = stats
    except Exception as exc:  # noqa: BLE001
        logger.warning("us52_sched_stats_failed: %s", exc)
    logger.info("us52_alert_scheduler_done scan=%s",
                result["scan"])
    return stats


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行告警扫描"""
    interval = scheduler_interval_seconds()
    logger.info("us52_alert_scheduler started "
                "interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_scheduled_alert_scan()
        except Exception as exc:  # noqa: BLE001
            logger.warning("告警调度异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("us52_alert_scheduler disabled "
                    "(US52_ALERT_MODE != on)")
        return False
    if _scheduler_task is not None and \
            not _scheduler_task.done():
        return True   # 已在运行
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _scheduler_task = loop.create_task(
            _scheduler_loop())
        return True
    except RuntimeError as exc:
        logger.warning("告警调度器启动失败"
                       "(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    """停止后台调度任务(测试清理用)"""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def scheduler_running() -> bool:
    """调度器是否在运行(测试/监控用)"""
    return _scheduler_task is not None and \
        not _scheduler_task.done()
