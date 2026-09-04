"""50号·小竹语音信值积分引擎 T+1 结算调度器

计划(docs/50号_小竹语音信值积分引擎实施计划.md §六 P2):
    voice50_settlement 结算 job——次日凌晨统一结算
    L2/L3 pending 事件(经反作弊清洗后聚合走 45号
    deposit 验真入信值)。

范式: ai_governance_scheduler(46号 P6)平移——
    - VOICE50_SETTLE_MODE=on 开启(默认 off 零影响)
    - 幂等: 事件 pending→settled 状态迁移防重复申报
    - fail-soft: 单批异常不阻断整轮
    - 留痕: 调度统计入 voice50_settlement(operator=job)

默认零影响红线: 双开关独立(VOICE50_MODE 控计分钩子/
VOICE50_SETTLE_MODE 控结算调度——off 直通铁律)。
"""

import asyncio
import logging
import os

logger = logging.getLogger("xiaozhu_voice50_settle")

# 调度间隔(秒——默认 6 小时一轮, T+1 日切覆盖)
DEFAULT_INTERVAL = 6 * 3600


def settle_mode_enabled() -> bool:
    """结算调度开关(VOICE50_SETTLE_MODE=on 开启, 默认 off)"""
    return os.environ.get(
        "VOICE50_SETTLE_MODE", "off").strip().lower() == "on"


def _interval_seconds() -> int:
    try:
        return max(60, int(os.environ.get(
            "VOICE50_SETTLE_INTERVAL",
            str(DEFAULT_INTERVAL))))
    except ValueError:
        return DEFAULT_INTERVAL


async def run_scheduled_settlement() -> dict:
    """执行一轮 T+1 结算(可独立调用——测试/手动触发)

    结算范围: dayKey < 今日 的全部 pending L2/L3
    (次日凌晨语义——今日事件留给次日)。
    """
    from services.xiaozhu_voice50_service import (
        Voice50Service,
    )
    result = await Voice50Service().settle_day(
        operator="job")
    counts = result.get("counts") or {}
    logger.info(
        "voice50_settle_done done=%s rejected=%s "
        "skipped=%s credits=%s settledEvents=%s",
        counts.get("done"), counts.get("rejected"),
        counts.get("skipped"), counts.get("credits"),
        counts.get("settledEvents"))
    return result


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行 T+1 结算"""
    interval = _interval_seconds()
    logger.info("voice50_settle_scheduler started "
                "interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_scheduled_settlement()
        except Exception as exc:  # noqa: BLE001
            logger.warning("T+1 结算调度异常(继续运行): %s",
                           exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台结算任务(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not settle_mode_enabled():
        logger.info("voice50_settle_scheduler disabled "
                    "(VOICE50_SETTLE_MODE != on)")
        return False
    if _scheduler_task is not None \
            and not _scheduler_task.done():
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
        logger.warning("结算调度器启动失败(无事件循环): %s",
                       exc)
        return False


def stop_scheduler() -> None:
    """停止后台结算任务(测试清理用)"""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def scheduler_running() -> bool:
    """调度器是否在运行(测试/监控用)"""
    return _scheduler_task is not None \
        and not _scheduler_task.done()
