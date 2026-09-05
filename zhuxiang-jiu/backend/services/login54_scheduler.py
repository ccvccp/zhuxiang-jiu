"""54号·小竹AI智能登录引擎大模型 学习调度器
(login54_scheduler, 默认关闭)

仿 46号 ai_governance_scheduler 范式(独立模块 +
start/stop 幂等):
    - 周期默认 24h(LOGIN54_LEARN_INTERVAL 可调,
      下限 5 分钟)
    - 每轮执行: 决策回流 T+1 批次补标(collect——
      幂等扫描: 驻留/申诉延时信号转正, 已标注跳过)
      → 统计留痕(login54_model_events, 版本溯源)

环境开关(计划 §六开关矩阵——双开关铁律):
    LOGIN54_LEARN_MODE=on        开启学习调度
                                (默认 off——默认零影响)
    LOGIN54_LEARN_INTERVAL=N     调度周期秒(默认 86400)

注: LOGIN54_MODE(模型面)与本开关(学习调度面)独立——
    回流采集不依赖模型面 on(collect 手动触发亦可用)。

接入方式(main.py lifespan):
    from services.login54_scheduler import (
        start_scheduler)
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger("login54_scheduler")

MODEL_VERSION = "v1-login54-scheduler"


def scheduler_enabled() -> bool:
    """调度总开关(LOGIN54_LEARN_MODE=on 开启, 默认 off)"""
    return os.environ.get(
        "LOGIN54_LEARN_MODE", "off").strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 24 小时"""
    try:
        value = int(os.environ.get(
            "LOGIN54_LEARN_INTERVAL", "86400"))
        return max(300, value)   # 下限 5 分钟防忙循环
    except ValueError:
        return 86400


async def run_scheduled_collect() -> dict:
    """执行一轮 T+1 批次补标(可独立调用, 便于测试与
    手动触发——collect 内部: 53号 events 幂等扫描→
    pending 转正→44号池双写)"""
    from services.login54_feedback_service import (
        Login54FeedbackService,
    )
    result = {"collect": None, "errors": []}
    try:
        collect = await Login54FeedbackService(
        ).collect_feedback()
        result["collect"] = {
            "scanned": collect.get("scanned"),
            "labeled": collect.get("labeled"),
            "deferred": collect.get("deferred"),
            "poolSubmitted":
                collect.get("poolSubmitted"),
            "signals": collect.get("signals"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("login54_sched_collect_failed: %s",
                       exc)
        result["errors"].append(f"collect:{exc}")

    # 统计留痕(供运维观察——collect 侧已落
    # model_events, 此处仅调度层状态)
    stats = {
        "runs": 0, "lastRunAt": ts(),
        "lastIntervalSeconds":
            scheduler_interval_seconds(),
        "lastCollect": result["collect"],
        "lastErrors": result["errors"][-10:],
    }
    try:
        from repositories.login54_repository import (
            Login54Repository,
        )
        repo = Login54Repository()
        repo._ensure_store()
        prev = repo.store.get(
            "_login54_learn_scheduler_stats") or {}
        stats["runs"] = int(prev.get("runs") or 0) + 1
        repo.store[
            "_login54_learn_scheduler_stats"] = stats
    except Exception as exc:  # noqa: BLE001
        logger.warning("login54_sched_stats_failed: %s",
                       exc)
    logger.info("login54_scheduler_done collect=%s",
                result["collect"])
    return stats


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行 T+1 批次补标"""
    interval = scheduler_interval_seconds()
    logger.info("login54_scheduler started "
                "interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_scheduled_collect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("学习调度异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("login54_scheduler disabled "
                    "(LOGIN54_LEARN_MODE != on)")
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
        logger.warning("学习调度器启动失败"
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
