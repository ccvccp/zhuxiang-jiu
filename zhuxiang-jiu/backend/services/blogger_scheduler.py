"""40号·平台流量DV博主模块·定时调度器(雷达扫描 + 发布出队 + 学习回流
+ 雷达2.0采集评分 + 引擎学习回流)

调度策略(保守设计, 对齐 36号 promo_scheduler 模式):
    - 雷达: 周期 15 分钟(BLOGGER_RADAR_INTERVAL_SECONDS 可调),
      扫描→评分决策→(auto_follow 作品自动生成跟随)
    - 发布: 周期 5 分钟(BLOGGER_PUBLISH_INTERVAL_SECONDS 可调),
      到期出队(三限已在入队时校验)
    - 学习: 周期 60 分钟(BLOGGER_LEARNING_INTERVAL_SECONDS 可调),
      批量回流(过24h沉淀窗口) + 触发一轮 Hedge 学习(反馈不足静默跳过)
    - 雷达2.0(P7 补齐, 2026-10-03 检查升级): 周期 15 分钟
      (BLOGGER_RADAR2_INTERVAL_SECONDS), collect_events 幂等槽位
      采集 → score_events 全量三维评分——P7 设计宣称"全网实时
      价值侦测中枢", 落地为手动 API 触发(生产 36 事件后再无
      增量)属语义失真; L1 任务包仍走 46号人工确认(设计内断点
      不自动化)
    - 引擎学习(P5a/P6a 补齐, 2026-10-03): 周期 60 分钟
      (BLOGGER_ENGINE_LEARNING_INTERVAL_SECONDS), collect_signals
      + run_learning(P5a 三通道) + collect_av_signals +
      run_av_learning(P6a 完播/弹幕)——纯观测面; 创作/发布/治理
      引擎不调度(发布类动作自动化需三限/审批再评估, 列遗留)
    - 单类任务失败不影响下一轮(异常吞掉记日志)

环境开关:
    BLOGGER_RADAR_AUTO=off                关闭雷达调度(默认关闭)
    BLOGGER_RADAR_INTERVAL_SECONDS=N      雷达周期(默认 900)
    BLOGGER_PUBLISH_AUTO=off              关闭发布调度(默认关闭)
    BLOGGER_PUBLISH_INTERVAL_SECONDS=N    发布周期(默认 300)
    BLOGGER_LEARNING_AUTO=off             关闭学习调度(默认关闭)
    BLOGGER_LEARNING_INTERVAL_SECONDS=N   学习周期(默认 3600)
    BLOGGER_RADAR2_AUTO=off               雷达2.0采集评分轮(默认关闭)
    BLOGGER_RADAR2_INTERVAL_SECONDS=N     雷达2.0周期(默认 900)
    BLOGGER_ENGINE_LEARNING_AUTO=off      引擎学习回流轮(默认关闭)
    BLOGGER_ENGINE_LEARNING_INTERVAL_SECONDS=N  引擎学习周期(默认 3600)

接入方式(main.py startup):
    from services.blogger_scheduler import (
        start_radar_scheduler, start_publish_scheduler,
        start_learning_scheduler, start_radar2_scheduler,
        start_engine_learning_scheduler)
    start_radar_scheduler()
    start_publish_scheduler()
    start_learning_scheduler()
    start_radar2_scheduler()
    start_engine_learning_scheduler()
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_RADAR_TASK: asyncio.Task | None = None
_PUBLISH_TASK: asyncio.Task | None = None
_LEARNING_TASK: asyncio.Task | None = None
_RADAR2_TASK: asyncio.Task | None = None
_ENGINE_LEARNING_TASK: asyncio.Task | None = None


def radar_enabled() -> bool:
    return os.environ.get("BLOGGER_RADAR_AUTO", "off").strip().lower() != "off"


def publish_enabled() -> bool:
    return os.environ.get("BLOGGER_PUBLISH_AUTO", "off").strip().lower() != "off"


def learning_enabled() -> bool:
    return os.environ.get("BLOGGER_LEARNING_AUTO", "off").strip().lower() != "off"


def radar2_enabled() -> bool:
    return os.environ.get("BLOGGER_RADAR2_AUTO", "off").strip().lower() != "off"


def engine_learning_enabled() -> bool:
    return (os.environ.get("BLOGGER_ENGINE_LEARNING_AUTO", "off")
            .strip().lower() != "off")


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
        # 81号 HRM 批任务闸门(amber/red 暂缓下轮重试; off/shadow 恒
        # 放行——P2 Tier2 接入, 覆盖 scan+auto_follow 整轮)
        from services.hrm81_service import acquire_slot
        if not await acquire_slot("blogger_radar"):
            continue
        try:
            from services.blogger_service import BloggerService
            service = BloggerService()
            result = await service.scan()
            logger.info("blogger_radar_scheduled new=%s discarded=%s",
                        result.get("new"), result.get("discarded"))
            # auto_follow 作品自动生成跟随(P0 全自动闭环;
            # 逐作品容错——单件异常仅跳过自身, 不阻断同轮其余作品,
            # 防孤儿化: 生产实证 2026-09-14 work=55 生成异常炸掉
            # 整轮循环, 剩余 17 件 auto_follow 无 radar 决策可再触达)
            for decision in result.get("decisions", []):
                work = decision.get("work") or {}
                if work.get("status") == "auto_follow":
                    try:
                        follow = await service.generate_follow(
                            work["workId"])
                        logger.info(
                            "blogger_auto_follow work=%s follow=%s "
                            "status=%s", work["workId"],
                            follow["followId"], follow["status"])
                    except Exception as exc:
                        logger.warning(
                            "auto_follow 生成失败(跳过该作品, 同轮其余"
                            "继续): work=%s %s",
                            work.get("workId"), exc)
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
    weekly_interval = _interval(
        "BLOGGER_WEEKLY_INTERVAL_SECONDS", 7 * 86400, floor=3600)
    last_weekly = 0.0
    while True:
        await asyncio.sleep(interval)
        # 81号 HRM 批任务闸门(amber/red 暂缓下轮重试; off/shadow 恒
        # 放行——P2 Tier2 接入, 覆盖回流+评论归因+学习整轮)
        from services.hrm81_service import acquire_slot
        if not await acquire_slot("blogger_learning"):
            continue
        try:
            from services.blogger_service import BloggerService
            service = BloggerService()
            collected = await service.collect_learning_feedback()
            logger.info("blogger_learning_scheduled submitted=%s "
                        "skipped=%s", collected.get("submitted"),
                        collected.get("skipped"))
            # P4b: 评论归因回流账号层(best-effort, 单独容错)
            try:
                from services.comment_intercept_service import \
                    CommentInterceptService
                await CommentInterceptService() \
                    .collect_comment_feedback()
            except Exception as exc:
                logger.warning("评论回流调度异常(继续): %s", exc)
            # 反馈不足属常态(产出速率低), 静默跳过本轮学习;
            # 样本污染熔断(P2b)亦静默(质量门积累干净样本)
            try:
                learned = await service.run_learning()
                logger.info("blogger_learning_cycle promoted=%s",
                            learned.get("promoted"))
            except ValueError:
                pass
            # P2b 周维护(默认 7d): 时间衰减 + 平台偏置重算 + 健康巡检
            import time
            now = time.monotonic()
            if now - last_weekly >= weekly_interval:
                last_weekly = now
                decay = await service.apply_weight_decay()
                await service.recompute_platform_bias()
                health = await service.run_health_checks()
                logger.info("blogger_weekly_maintenance decayed=%s "
                            "frozen=%s rolledBack=%s",
                            decay.get("decayed"), health.get("frozen"),
                            health.get("rolledBack"))
        except Exception as exc:
            logger.warning("学习调度异常(继续运行): %s", exc)


async def _radar2_loop() -> None:
    interval = _interval("BLOGGER_RADAR2_INTERVAL_SECONDS", 900)
    logger.info("blogger_radar2_scheduler started interval=%ss", interval)
    while True:
        # 启动即首轮(对齐全站调度范式; sleep-first 在频繁容器重建下
        # 永不执行——attract72 同款坑已修, 2026-10-03)
        try:
            from services.radar_hub_service import RadarHubService
            result = await RadarHubService().collect_events()
            logger.info(
                "blogger_radar2_scheduled collected=%s duplicates=%s "
                "botFiltered=%s", result.get("collected"),
                result.get("duplicates"), result.get("botFiltered"))
            # 评分(全量事件; 安全<0.6 硬闸 L4 屏蔽在服务内)
            from services.radar_score_service import RadarScoreService
            scored = await RadarScoreService().score_events()
            logger.info("blogger_radar2_scored %s",
                        scored.get("scored")
                        or scored.get("total") or 0)
        except Exception as exc:
            logger.warning("雷达2.0调度异常(继续运行): %s", exc)
        await asyncio.sleep(interval)


async def _engine_learning_loop() -> None:
    interval = _interval("BLOGGER_ENGINE_LEARNING_INTERVAL_SECONDS", 3600)
    logger.info("blogger_engine_learning_scheduler started "
                "interval=%ss", interval)
    while True:
        # 启动即首轮(同 radar2, 对齐全站范式)
        # P5a 三通道信号采集 + 学习(各自容错, 观测面)
        try:
            from services.blogger_auto_learn_service import (
                BloggerAutoLearnService,
            )
            sig = await BloggerAutoLearnService().collect_signals()
            logger.info("blogger_p5a_signals collected=%s",
                        sig.get("collected") or sig.get("count") or 0)
            try:
                await BloggerAutoLearnService().run_learning()
            except ValueError:
                pass   # 反馈不足属常态
        except Exception as exc:
            logger.warning("P5a 引擎学习调度异常(继续): %s", exc)
        # P6a AV 信号采集 + 学习
        try:
            from services.blogger_av_learn_service import (
                BloggerAVLearnService,
            )
            av = await BloggerAVLearnService().collect_av_signals()
            logger.info("blogger_p6a_signals collected=%s",
                        av.get("collected") or av.get("count") or 0)
            try:
                await BloggerAVLearnService().run_av_learning()
            except ValueError:
                pass
        except Exception as exc:
            logger.warning("P6a 引擎学习调度异常(继续): %s", exc)
        await asyncio.sleep(interval)


def _start(task_holder: str, coro) -> bool:
    global _RADAR_TASK, _PUBLISH_TASK, _LEARNING_TASK
    global _RADAR2_TASK, _ENGINE_LEARNING_TASK
    current = {"radar": _RADAR_TASK, "publish": _PUBLISH_TASK,
               "learning": _LEARNING_TASK,
               "radar2": _RADAR2_TASK,
               "engine_learning": _ENGINE_LEARNING_TASK}[task_holder]
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
        elif task_holder == "learning":
            _LEARNING_TASK = task
        elif task_holder == "radar2":
            _RADAR2_TASK = task
        else:
            _ENGINE_LEARNING_TASK = task
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


def start_radar2_scheduler() -> bool:
    """启动雷达2.0采集评分调度(幂等; BLOGGER_RADAR2_AUTO=off 返回 False)

    2026-10-03 检查升级补齐: P7 设计"实时侦测"落地为手动触发,
    本轮补齐调度; L1 任务包仍走 46号人工确认(设计内断点)。
    """
    if not radar2_enabled():
        logger.info("blogger_radar2_scheduler disabled "
                    "(BLOGGER_RADAR2_AUTO=off)")
        return False
    return _start("radar2", _radar2_loop())


def start_engine_learning_scheduler() -> bool:
    """启动引擎学习回流调度(幂等; 默认 off 返回 False)

    P5a/P6a 观测面学习(P7 检查升级同批补齐); 创作/发布/治理
    引擎不调度(发布类自动化待三限/审批再评估, 列遗留)。
    """
    if not engine_learning_enabled():
        logger.info("blogger_engine_learning_scheduler disabled "
                    "(BLOGGER_ENGINE_LEARNING_AUTO=off)")
        return False
    return _start("engine_learning", _engine_learning_loop())


def stop_schedulers() -> None:
    """停止全部调度任务(测试清理/应用关闭用)"""
    global _RADAR_TASK, _PUBLISH_TASK, _LEARNING_TASK
    global _RADAR2_TASK, _ENGINE_LEARNING_TASK
    for task in (_RADAR_TASK, _PUBLISH_TASK, _LEARNING_TASK,
                 _RADAR2_TASK, _ENGINE_LEARNING_TASK):
        if task is not None:
            task.cancel()
    _RADAR_TASK = None
    _PUBLISH_TASK = None
    _LEARNING_TASK = None
    _RADAR2_TASK = None
    _ENGINE_LEARNING_TASK = None
