"""AI 自学习定时调度器(v7.6: 定时学习, 默认开启)

成熟 MLOps 惯例: 在线学习不应依赖人工触发。本调度器周期性扫描
全部评分器, 待学习反馈攒够 min_feedback 时自动执行学习周期
(Hedge 更新 → 挑战者/自动晋升), 让「越用越准」无需人工介入。

调度策略(保守设计):
    - 周期默认 6 小时(AI_LEARNING_INTERVAL_SECONDS 可调), 避开高频扰动
    - 反馈不足 min_feedback 的评分器本轮跳过(不产生垃圾学习)
    - 单个评分器学习失败不影响其他评分器
    - 学习结果写入调度统计(overview 展示 autoLearnRuns/lastRunAt)

环境开关:
    AI_LEARNING_AUTO=off              关闭调度(默认开启)
    AI_LEARNING_INTERVAL_SECONDS=N    调度周期(默认 21600 = 6h)

接入方式(main.py lifespan):
    from services.ai_learning_scheduler import start_scheduler
    start_scheduler()   # 启动后台任务(幂等)
"""

import asyncio
import logging
import os

from core.helpers import ts
from repositories.ai_learning_repository import AiLearningRepository

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-scheduler"


def scheduler_enabled() -> bool:
    """调度总开关(AI_LEARNING_AUTO=off 关闭, 默认开启)"""
    return os.environ.get("AI_LEARNING_AUTO", "on").strip().lower() != "off"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 6 小时"""
    try:
        value = int(os.environ.get("AI_LEARNING_INTERVAL_SECONDS", "21600"))
        return max(60, value)  # 下限 1 分钟, 防误配为 0 导致忙循环
    except ValueError:
        return 21600


async def run_scheduled_learning() -> dict:
    """执行一轮调度扫描(可独立调用, 便于测试)

    对每个评分器: pending ≥ min_feedback → 自动学习; 否则跳过。
    """
    from services.ai_learning_service import (
        DEFAULT_LEARNING_CONFIG, SCORER_REGISTRY, run_learning_cycle,
    )
    repo = AiLearningRepository()
    results = []
    for scorer_id in SCORER_REGISTRY:
        try:
            config = {**DEFAULT_LEARNING_CONFIG,
                      **(await repo.get_config(scorer_id) or {})}
            pending = await repo.count_feedback(scorer_id, status="pending")
            if pending < config["min_feedback"]:
                continue
            learned = await run_learning_cycle(scorer_id)
            results.append({
                "scorerId": scorer_id,
                "learnedFrom": learned["learnedFrom"],
                "newVersion": learned["newVersion"],
                "promoted": learned["promoted"],
            })
            logger.info("ai_learning_scheduled scorer=%s %s promoted=%s",
                        scorer_id, learned["newVersion"], learned["promoted"])
        except ValueError:
            # 反馈数在读取与学习之间被并发消费 → 本轮跳过, 下轮再学
            continue
        except Exception as exc:
            logger.warning("调度学习失败(scorer=%s): %s", scorer_id, exc)

    stats = await repo.get_scheduler_stats() or {"runs": 0}
    stats = {
        "runs": int(stats.get("runs", 0)) + 1,
        "lastRunAt": ts(),
        "lastIntervalSeconds": scheduler_interval_seconds(),
        "lastLearnedScorers": len(results),
        "lastResults": results[-20:],   # 最近 20 条, 防无限膨胀
    }
    await repo.save_scheduler_stats(stats)
    return stats


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行调度扫描"""
    interval = scheduler_interval_seconds()
    logger.info("ai_learning_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_scheduled_learning()
        except Exception as exc:
            logger.warning("调度扫描异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)

    在 main.py 的 lifespan/startup 中调用一次即可。
    """
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("ai_learning_scheduler disabled (AI_LEARNING_AUTO=off)")
        return False
    if _scheduler_task is not None and not _scheduler_task.done():
        return True  # 已在运行
    try:
        try:
            loop = asyncio.get_running_loop()   # 协程上下文(正常路径)
        except RuntimeError:
            loop = asyncio.get_event_loop()     # 兼容旧式调用
        _scheduler_task = loop.create_task(_scheduler_loop())
        return True
    except RuntimeError as exc:  # 无事件循环(如测试环境直接调用)
        logger.warning("调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    """停止后台调度任务(测试清理用)"""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def scheduler_running() -> bool:
    """调度器是否在运行(测试/监控用)"""
    return _scheduler_task is not None and not _scheduler_task.done()
