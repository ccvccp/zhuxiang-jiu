"""41号·AI智能代驾模块·学习回流调度器

调度策略(对齐 40号 blogger_scheduler 模式):
    - 学习回流: 默认 60 分钟一轮(RIDE_LEARNING_INTERVAL_SECONDS=3600)
    - 单类任务失败不影响下一轮(异常吞掉记日志)
    - 默认整体关闭(RIDE_LEARNING_AUTO=off), 实机/生产按需开启

每轮任务:
    ① 派单决策批量回流(ride_dispatch 第23档案: settled+有评价行程)
    ② 审查决策批量回流(driver_application_gate 第22档案:
       approved 且司机有服务数据的申请)
    ③ 评价审评决策批量回流(ride_review 第24档案: 已标注评价)
    ④ 触发三档案 Hedge 学习(反馈不足 ValueError 静默跳过)
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_LEARNING_TASK: asyncio.Task | None = None


def learning_enabled() -> bool:
    return os.environ.get("RIDE_LEARNING_AUTO", "off").strip().lower() \
        != "off"


def _interval(env: str, default: int, floor: int = 60) -> int:
    try:
        return max(floor, int(os.environ.get(env, str(default))))
    except ValueError:
        return default


async def _learning_loop() -> None:
    interval = _interval("RIDE_LEARNING_INTERVAL_SECONDS", 3600)
    logger.info("ride_learning_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            from services.ride_dispatch_service import RideDispatchService
            from services.driver_gate_service import DriverGateService
            from services.ride_review_service import RideReviewService

            dispatch = RideDispatchService()
            collected = await dispatch.collect_learning_feedback()
            logger.info("ride_learning_scheduled dispatch submitted=%s "
                        "skipped=%s", collected.get("submitted"),
                        collected.get("skipped"))

            gate = DriverGateService()
            collected = await gate.collect_application_feedback()
            logger.info("ride_learning_scheduled gate submitted=%s "
                        "skipped=%s", collected.get("submitted"),
                        collected.get("skipped"))

            review = RideReviewService()
            collected = await review.collect_review_feedback()
            logger.info("ride_learning_scheduled review submitted=%s "
                        "skipped=%s", collected.get("submitted"),
                        collected.get("skipped"))

            for service, name in ((dispatch, "ride_dispatch"),
                                  (gate, "driver_application_gate"),
                                  (review, "ride_review")):
                try:
                    learned = await service.run_learning()
                    logger.info("ride_learning_cycle %s promoted=%s",
                                name, learned.get("promoted"))
                except ValueError:
                    pass    # 反馈不足, 静默跳过
        except Exception as exc:
            logger.warning("代驾学习调度异常(继续运行): %s", exc)


def _start(coro) -> None:
    global _LEARNING_TASK
    if _LEARNING_TASK is not None and not _LEARNING_TASK.done():
        return
    _LEARNING_TASK = asyncio.ensure_future(coro)


def start_learning_scheduler() -> None:
    """启动学习回流调度(幂等; RIDE_LEARNING_AUTO=off 时不启动)"""
    if not learning_enabled():
        logger.info("ride_learning_scheduler disabled "
                    "(RIDE_LEARNING_AUTO=off)")
        return
    _start(_learning_loop())


def stop_schedulers() -> None:
    """停止全部调度任务(shutdown 对称清理)"""
    global _LEARNING_TASK
    if _LEARNING_TASK is not None:
        _LEARNING_TASK.cancel()
        _LEARNING_TASK = None
