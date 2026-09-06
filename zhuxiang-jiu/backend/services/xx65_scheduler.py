"""65号·网店及商品AI智能管理 T+1
调度器(xx65_scheduler, P4)

计划(§八 P4):
    XX65_LEARN_MODE=on → 四任务
    (巡检标记→商品回流→教练分发
    →调度留痕), 每任务独立 try
    fail-soft 互不阻塞; 默认 off
    (函数可独立调用——测试与
    手动触发, 对齐 64号范式)。

铁律:
    - 回流端点 /feedback/collect
      不受 XX65_MODE/LEARN_MODE
      影响(通道永不关停)
    - 调度留痕 scheduler_run 落
      xx65_events(shopId=0 系统
      事件)
    - 全确定性, LLM 不进调度链
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger(
    "xx65_scheduler")

_TASK = None


def scheduler_enabled() -> bool:
    """调度开关(XX65_LEARN_MODE,
    默认 off)"""
    return os.environ.get(
        "XX65_LEARN_MODE",
        "off").lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度间隔(默认 86400s=T+1)"""
    try:
        return max(60, int(
            os.environ.get(
                "XX65_SCHED_INTERVAL",
                "86400")))
    except ValueError:
        return 86400


async def run_scheduled_tasks() -> dict:
    """执行一轮 T+1 四任务
    (可独立调用)"""
    result = {
        "inspect": None,
        "collect": None,
        "coach": None,
        "errors": [],
    }

    # ① 防御③巡检(published 商品
    #    全量重扫——产生 flagged
    #    信号供回流消费)
    try:
        from services.xx65_service import (
            Xx65Service,
        )
        insp = await \
            Xx65Service() \
            .inspect_products()
        result["inspect"] = {
            "scanned":
                insp.get("scanned"),
            "flagged":
                insp.get("flagged"),
        }
    except Exception as exc:
        logger.warning(
            "xx65_sched_inspect"
            "_failed: %s", exc)
        result["errors"].append(
            f"inspect:{exc}")

    # ② 商品回流(productId 1:1
    #    幂等——通道不受开关影响)
    try:
        from services.xx65_learn_service import (
            Xx65LearnService,
        )
        collect = await \
            Xx65LearnService() \
            .collect_feedback()
        result["collect"] = {
            "scanned":
                collect.get("scanned"),
            "labeled":
                collect.get("labeled"),
            "skipped":
                collect.get("skipped"),
            "poolSubmitted":
                collect.get(
                    "poolSubmitted"),
            "poolFailed":
                collect.get(
                    "poolFailed"),
        }
    except Exception as exc:
        logger.warning(
            "xx65_sched_collect"
            "_failed: %s", exc)
        result["errors"].append(
            f"collect:{exc}")

    # ③ 教练分发留痕(active 店铺
    #    按档分发——每日贴士触达)
    try:
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        from services.xx65_service import (
            Xx65Service,
        )
        repo = Xx65Repository()
        shops = await \
            repo.list_shops(
                status="active",
                limit=100)
        delivered = 0
        svc = Xx65Service()
        for shop in shops:
            tips = await \
                svc.coach_tips(
                    shop["shopId"])
            delivered += int(
                tips.get("total")
                or 0)
        result["coach"] = {
            "shops": len(shops),
            "tipsDelivered":
                delivered,
        }
    except Exception as exc:
        logger.warning(
            "xx65_sched_coach"
            "_failed: %s", exc)
        result["errors"].append(
            f"coach:{exc}")

    # ④ 调度留痕(scheduler_run
    #    全量结果——shopId=0
    #    系统事件)
    try:
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        repo = Xx65Repository()
        await repo.add_event({
            "eventId":
                await repo
                .next_event_id(),
            "shopId": 0,
            "eventType":
                "scheduler_run",
            "detail": result,
            "createdAt": ts(),
        })
    except Exception as exc:
        logger.warning(
            "xx65_sched_event"
            "_failed: %s", exc)

    return result


async def _scheduler_loop() -> None:
    """后台调度循环(整轮异常
    不退出)"""
    interval = \
        scheduler_interval_seconds()
    while True:
        try:
            await run_scheduled_tasks()
        except Exception as exc:
            logger.warning(
                "xx65_sched_loop"
                "_failed: %s", exc)
        await asyncio.sleep(interval)


def start_scheduler() -> bool:
    """启动后台调度(幂等——
    LEARN_MODE=on 才启动)"""
    global _TASK
    if not scheduler_enabled():
        return False
    if _TASK and not _TASK.done():
        return True
    _TASK = asyncio.get_event_loop() \
        .create_task(
            _scheduler_loop())
    return True
