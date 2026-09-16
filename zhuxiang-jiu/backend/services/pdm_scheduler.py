"""智能产品管理大模型 护栏自动巡检调度器

大模型二代(全站范式——智图/信用/钱包/小竹同款):
    PDM_GUARD_AUTO 默认 on(护栏是保护机制)
    PDM_GUARD_INTERVAL 默认 3600s(最小 60s)

巡检三指标(确定性计算——分母 0 取 0, 冷启动
不误判; LLM 禁入判定链):
    终审驳回率 manualRejectRate
        = rejected / 终审已决(rejected+on_sale)
          (人工终审质量恶化代理——流程态
          draft/ai_reviewing/manual_reviewing
          不进分母, 建议书制口径不误判)
    图片违规标记率 imageFlagRate
        = flagged / 总图片
          (审图恶化代理)
    AI 预检拦截率 aiRejectRate
        = aiReview.action==reject
          / 全部已预审(fast_track+manual_review+reject)
          (AI 预检恶化代理)

聚合口径: pdm_repository list_pdm_products(≤1000)
+ list_images(≤1000) 确定性聚合。

run_guard_patrol 可独立调用(控制面
POST /api/pdm/mode/guard 缺省聚合)。
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_GUARD_TASK = None

# 巡检聚合上限(防无限膨胀)
_PATROL_LIST_CAP = 1000


def guard_patrol_enabled() -> bool:
    """护栏巡检开关(PDM_GUARD_AUTO,
    默认 on——护栏自动暂停是保护机制)"""
    return os.environ.get(
        "PDM_GUARD_AUTO",
        "on").lower() == "on"


def guard_interval_seconds() -> int:
    """护栏巡检间隔(默认 3600s)"""
    try:
        return max(60, int(os.environ.get(
            "PDM_GUARD_INTERVAL",
            "3600")))
    except ValueError:
        return 3600


async def run_guard_patrol() -> dict:
    """护栏巡检一轮(确定性指标计算
    + guard_check——可独立调用)

    三指标口径:
        终审驳回率 = rejected / (rejected+on_sale)
        图片违规标记率 = flagged / 总图片
        AI 预检拦截率 = reject / 全部已预审
    分母为 0 时指标取 0(冷启动不误判)。
    """
    from repositories.pdm_repository import (
        PdmRepository,
        STATUS_REJECTED, STATUS_ON_SALE,
    )
    from services.pdm_mode_service import (
        PdmModeService,
    )
    repo = PdmRepository()

    overlays = (await repo.list_pdm_products(
        limit=_PATROL_LIST_CAP))
    images = (await repo.list_images(
        limit=_PATROL_LIST_CAP))

    # 终审驳回率: 终审已决 = rejected + on_sale
    # (流程态 draft/ai_reviewing/manual_reviewing
    #  不进分母——建议书制口径)
    rejected_n = sum(
        1 for o in overlays
        if (o.get("status") or "") == STATUS_REJECTED)
    on_sale_n = sum(
        1 for o in overlays
        if (o.get("status") or "") == STATUS_ON_SALE)
    settled_n = rejected_n + on_sale_n

    # 图片违规标记率
    image_total = len(images)
    flagged_n = sum(
        1 for i in images
        if (i.get("status") or "") == "flagged")

    # AI 预检拦截率(全部已有 aiReview 结果的商品)
    ai_pass = ai_manual = ai_reject = 0
    for o in overlays:
        action = (o.get("aiReview") or {}).get("action")
        if action == "fast_track":
            ai_pass += 1
        elif action == "manual_review":
            ai_manual += 1
        elif action == "reject":
            ai_reject += 1
    ai_total = ai_pass + ai_manual + ai_reject

    manual_reject_rate = (
        rejected_n / settled_n
        if settled_n > 0 else 0.0)
    image_flag_rate = (
        flagged_n / image_total
        if image_total > 0 else 0.0)
    ai_reject_rate = (
        ai_reject / ai_total
        if ai_total > 0 else 0.0)

    guard = await PdmModeService() \
        .guard_check(
            manual_reject_rate,
            image_flag_rate,
            ai_reject_rate)
    return {
        "metrics": {
            "manualRejectRate": round(
                manual_reject_rate, 4),
            "imageFlagRate": round(image_flag_rate, 4),
            "aiRejectRate": round(ai_reject_rate, 4),
        },
        "samples": {
            "products": {
                "total": len(overlays),
                "rejected": rejected_n,
                "onSale": on_sale_n},
            "images": {
                "total": image_total,
                "flagged": flagged_n},
            "aiPrecheck": {
                "total": ai_total,
                "fastTrack": ai_pass,
                "manualReview": ai_manual,
                "reject": ai_reject},
        },
        "breached": guard.get("breached"),
        "pausedNow": guard.get("pausedNow"),
        "breaches": guard.get("breaches")
        or [],
    }


async def _guard_loop() -> None:
    """护栏巡检循环(整轮异常不退出)"""
    while True:
        try:
            r = await run_guard_patrol()
            if r.get("pausedNow"):
                logger.warning(
                    "pdm_guard_patrol_paused: "
                    "%s", r.get("breaches"))
            else:
                logger.info(
                    "pdm_guard_patrol_ok "
                    "metrics=%s",
                    r.get("metrics"))
        except Exception as exc:
            logger.error(
                "pdm_guard_patrol_fail: %s",
                exc)
        await asyncio.sleep(
            guard_interval_seconds())


def start_guard_loop() -> bool:
    """启动护栏巡检循环(幂等;
    未启用返回 False)"""
    if not guard_patrol_enabled():
        return False
    global _GUARD_TASK
    if _GUARD_TASK and not _GUARD_TASK.done():
        return True
    _GUARD_TASK = asyncio.get_event_loop() \
        .create_task(_guard_loop())
    logger.info("pdm_guard_loop_started "
                "interval=%ss",
                guard_interval_seconds())
    return True
