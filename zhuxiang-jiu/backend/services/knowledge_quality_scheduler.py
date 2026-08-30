"""知识质量进化调度器(P2: 定时扫描, 默认开启)

P2 智能进化三类后台任务:
    - 质量淘汰: 重算 published 条目质量分, 低分+陈旧 → 降级退役
    - 渐进信任: 高可信来源的 pending 条目自动审核通过(D-16)
    - (缺口摘要为读侧报表, 无需调度)

实现模式对齐 order_timeout_scheduler / ai_learning_scheduler:
    - 周期性后台任务(main.py lifespan 启动, 幂等)
    - 扫描逻辑独立成 run_quality_scan(), 可单测/手动触发
    - 全局限跑锁(跨进程 Redis 锁)防多实例重复

调度策略:
    - 周期默认 6 小时(知识治理粒度, 无需分钟级)
    - 单轮失败不影响下一轮

环境开关:
    KNOWLEDGE_QUALITY_AUTO=off            关闭调度(默认开启)
    KNOWLEDGE_QUALITY_SCAN_INTERVAL=N     扫描周期秒(默认 21600, 下限 300)

接入方式(main.py lifespan/on_event startup):
    from services.knowledge_quality_scheduler import start_scheduler
    start_scheduler()   # 启动后台任务(幂等)
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger(__name__)


def scheduler_enabled() -> bool:
    """调度总开关(KNOWLEDGE_QUALITY_AUTO=off 关闭, 默认开启)"""
    return os.environ.get(
        "KNOWLEDGE_QUALITY_AUTO", "on").strip().lower() != "off"


def scheduler_interval_seconds() -> int:
    """扫描周期(秒), 默认 6 小时"""
    try:
        value = int(os.environ.get(
            "KNOWLEDGE_QUALITY_SCAN_INTERVAL", "21600"))
        return max(300, value)  # 下限 5 分钟
    except ValueError:
        return 21600


async def run_quality_scan() -> dict:
    """执行一轮知识质量扫描(可独立调用, 便于测试/手动运维触发)

    Returns:
        本轮扫描统计
    """
    from services.knowledge_service import KnowledgeService

    svc = KnowledgeService()
    sweep = await svc.quality_sweep()
    auto_approve = await svc.auto_approve_run()
    # 紧急缺口提醒管理员(缺口→通知→教学 飞轮, best-effort 不阻断)
    notify = await svc.notify_urgent_gaps()
    result = {
        "scannedAt": ts(),
        "sweep": {"refreshed": sweep["refreshed"],
                  "skipped": sweep.get("skipped", 0),
                  "retiredCount": sweep["retiredCount"]},
        "autoApprove": {"count": auto_approve["autoApprovedCount"]},
        "urgentGapNotify": {"notified": notify.get("notified", 0)},
    }
    if (sweep["retiredCount"] or auto_approve["autoApprovedCount"]
            or notify.get("notified")):
        logger.info("knowledge_quality_scan retired=%d autoApproved=%d "
                    "urgentNotified=%d",
                    sweep["retiredCount"],
                    auto_approve["autoApprovedCount"],
                    notify.get("notified", 0))
    return result


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行质量扫描"""
    interval = scheduler_interval_seconds()
    logger.info("knowledge_quality_scheduler started interval=%ss",
                interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_quality_scan()
        except Exception as exc:
            logger.warning("知识质量扫描异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("knowledge_quality_scheduler disabled "
                    "(KNOWLEDGE_QUALITY_AUTO=off)")
        return False
    if _scheduler_task is not None and not _scheduler_task.done():
        return True  # 已在运行
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _scheduler_task = loop.create_task(_scheduler_loop())
        return True
    except RuntimeError as exc:
        logger.warning("知识质量调度器启动失败(无事件循环): %s", exc)
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
