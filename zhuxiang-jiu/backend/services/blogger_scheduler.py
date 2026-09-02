"""40号·平台流量DV博主模块·定时调度器(雷达扫描 + 发布出队 + 学习回流)

调度策略(保守设计, 对齐 36号 promo_scheduler 模式):
    - 雷达: 周期 15 分钟(BLOGGER_RADAR_INTERVAL_SECONDS 可调),
      扫描→评分决策→(auto_follow 作品自动生成跟随)
    - 发布: 周期 5 分钟(BLOGGER_PUBLISH_INTERVAL_SECONDS 可调),
      到期出队(三限已在入队时校验)
    - 学习: 周期 60 分钟(BLOGGER_LEARNING_INTERVAL_SECONDS 可调),
      批量回流(过24h沉淀窗口) + 触发一轮 Hedge 学习(反馈不足静默跳过)
    - 单类任务失败不影响下一轮(异常吞掉记日志)

环境开关:
    BLOGGER_RADAR_AUTO=off                关闭雷达调度(默认关闭)
    BLOGGER_RADAR_INTERVAL_SECONDS=N      雷达周期(默认 900)
    BLOGGER_PUBLISH_AUTO=off              关闭发布调度(默认关闭)
    BLOGGER_PUBLISH_INTERVAL_SECONDS=N    发布周期(默认 300)
    BLOGGER_LEARNING_AUTO=off             关闭学习调度(默认关闭)
    BLOGGER_LEARNING_INTERVAL_SECONDS=N   学习周期(默认 3600)

接入方式(main.py startup):
    from services.blogger_scheduler import (
        start_radar_scheduler, start_publish_scheduler,
        start_learning_scheduler)
    start_radar_scheduler()
    start_publish_scheduler()
    start_learning_scheduler()
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_RADAR_TASK: asyncio.Task | None = None
_PUBLISH_TASK: asyncio.Task | None = None
_LEARNING_TASK: asyncio.Task | None = None


def radar_enabled() -> bool:
    return os.environ.get("BLOGGER_RADAR_AUTO", "off").strip().lower() != "off"


def publish_enabled() -> bool:
    return os.environ.get("BLOGGER_PUBLISH_AUTO", "off").strip().lower() != "off"


def learning_enabled() -> bool:
    return os.environ.get("BLOGGER_LEARNING_AUTO", "off").strip().lower() != "off"


def _interval(env: str, default: int, floor: int = 60) -> int:
    try:
        return max(floor, int(os.environ.get(env, str(default))))
    except ValueError:
        return default


async def _radar_loop() -> None:
    interval = _interval("BLOGGER_RADAR_INTERVAL_SECONDS", 900)
    logger.info("blogger_radar_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            from services.blogger_service import BloggerService
            service = BloggerService()
            result = await service.scan()
            logger.info("blogger_radar_scheduled new=%s discarded=%s",
                        result.get("new"), result.get("discarded"))
            # auto_follow 作品自动生成跟随(P0 全自动闭环)
            for decision in result.get("decisions", []):
                work = decision.get("work") or {}
                if work.get("status") == "auto_follow":
                    follow = await service.generate_follow(
                        work["workId"])
                    logger.info(
                        "blogger_auto_follow work=%s follow=%s "
                        "status=%s", work["workId"],
                        follow["followId"], follow["status"])
        except Exception as exc:
            logger.warning("雷达调度异常(继续运行): %s", exc)


async def _publish_loop() -> None:
    interval = _interval("BLOGGER_PUBLISH_INTERVAL_SECONDS", 300)
    logger.info("blogger_publish_scheduler started interval=%ss",
                interval)
    while True:
        await asyncio.sleep(interval)
        try:
            from services.blogger_service import BloggerService
            published = await BloggerService().process_publish_queue()
            if published:
                logger.info("blogger_publish_scheduled count=%s",
                            len(published))
        except Exception as exc:
            logger.warning("发布调度异常(继续运行): %s", exc)


async def _learning_loop() -> None:
    interval = _interval("BLOGGER_LEARNING_INTERVAL_SECONDS", 3600)
    logger.info("blogger_learning_scheduler started interval=%ss",
                interval)
    while True:
        await asyncio.sleep(interval)
        try:
            from services.blogger_service import BloggerService
            service = BloggerService()
            collected = await service.collect_learning_feedback()
            logger.info("blogger_learning_scheduled submitted=%s "
                        "skipped=%s", collected.get("submitted"),
                        collected.get("skipped"))
            # 反馈不足属常态(产出速率低), 静默跳过本轮学习
            try:
                learned = await service.run_learning()
                logger.info("blogger_learning_cycle promoted=%s",
                            learned.get("promoted"))
            except ValueError:
                pass
        except Exception as exc:
            logger.warning("学习调度异常(继续运行): %s", exc)


def _start(task_holder: str, coro) -> bool:
    global _RADAR_TASK, _PUBLISH_TASK, _LEARNING_TASK
    current = {"radar": _RADAR_TASK, "publish": _PUBLISH_TASK,
               "learning": _LEARNING_TASK}[task_holder]
    if current is not None and not current.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        task = loop.create_task(coro)
        if task_holder == "radar":
            _RADAR_TASK = task
        elif task_holder == "publish":
            _PUBLISH_TASK = task
        else:
            _LEARNING_TASK = task
        return True
    except RuntimeError as exc:
        logger.warning("调度器启动失败(无事件循环): %s", exc)
        return False


def start_radar_scheduler() -> bool:
    """启动雷达调度(幂等; BLOGGER_RADAR_AUTO=off 返回 False)"""
    if not radar_enabled():
        logger.info("blogger_radar_scheduler disabled "
                    "(BLOGGER_RADAR_AUTO=off)")
        return False
    return _start("radar", _radar_loop())


def start_publish_scheduler() -> bool:
    """启动发布调度(幂等; BLOGGER_PUBLISH_AUTO=off 返回 False)"""
    if not publish_enabled():
        logger.info("blogger_publish_scheduler disabled "
                    "(BLOGGER_PUBLISH_AUTO=off)")
        return False
    return _start("publish", _publish_loop())


def start_learning_scheduler() -> bool:
    """启动学习调度(幂等; BLOGGER_LEARNING_AUTO=off 返回 False)"""
    if not learning_enabled():
        logger.info("blogger_learning_scheduler disabled "
                    "(BLOGGER_LEARNING_AUTO=off)")
        return False
    return _start("learning", _learning_loop())


def stop_schedulers() -> None:
    """停止全部调度任务(测试清理/应用关闭用)"""
    global _RADAR_TASK, _PUBLISH_TASK, _LEARNING_TASK
    for task in (_RADAR_TASK, _PUBLISH_TASK, _LEARNING_TASK):
        if task is not None:
            task.cancel()
    _RADAR_TASK = None
    _PUBLISH_TASK = None
    _LEARNING_TASK = None
