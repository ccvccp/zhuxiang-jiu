"""智图大模型 护栏自动巡检调度器

大模型二代(全站范式——小竹/钱包/信用同款):
    ZT_GUARD_AUTO 默认 on(护栏是保护机制)
    ZT_GUARD_INTERVAL 默认 3600s(最小 60s)

巡检三指标(确定性计算——分母 0 取 0, 冷启动
不误判; LLM 禁入判定链):
    建议驳回率 rejectRate
        = rejected 反馈 / 总反馈
          (AI 建议质量恶化代理)
    调度误报率 falseAlarmRate
        = dismissed 工单 / 已处置工单(acked+dismissed)
          (异常扫描误报恶化代理——工单为《处置预案
          草稿》人工确认制, pending 为业务常态
          不进分母, 冷启动/未使用不误判)
    月台饱和率 dockSaturationRate
        = 峰值时段预约车数 / (月台数 × 每时段上限)
          (月台运力饱和恶化代理)

聚合口径: _ZtStore 整表 list(zt_tickets/
zt_feedbacks/zt_dock_bookings/zt_pois,
上限保护 500 条——智图共享表存储)。

run_guard_patrol 可独立调用(控制面
POST /api/map-ai/mode/guard 缺省聚合)。
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_GUARD_TASK = None

# 巡检聚合上限(防无限膨胀——智图共享表)
_PATROL_LIST_CAP = 500


def guard_patrol_enabled() -> bool:
    """护栏巡检开关(ZT_GUARD_AUTO,
    默认 on——护栏自动暂停是保护机制)"""
    return os.environ.get(
        "ZT_GUARD_AUTO",
        "on").lower() == "on"


def guard_interval_seconds() -> int:
    """护栏巡检间隔(默认 3600s)"""
    try:
        return max(60, int(os.environ.get(
            "ZT_GUARD_INTERVAL",
            "3600")))
    except ValueError:
        return 3600


async def run_guard_patrol() -> dict:
    """护栏巡检一轮(确定性指标计算
    + guard_check——可独立调用)

    三指标口径:
        建议驳回率 = rejected / 总反馈
        调度误报率 = dismissed / 已处置(acked+dismissed)
        月台饱和率 = 峰值时段预约车数 / 月台总容量
    分母为 0 时指标取 0(冷启动/未使用不误判)。
    """
    from services.zt_fabric_service import (
        _ZtStore, ZtFabricService,
    )
    from services.zt_resource_service import (
        DOCK_SLOTS, DOCK_MAX_PER_SLOT,
    )
    from services.zt_map_mode_service import (
        ZtMapModeService,
    )
    store = _ZtStore()

    tickets = (await store.list("zt_tickets"))[:_PATROL_LIST_CAP]
    feedbacks = (await store.list(
        "zt_feedbacks"))[:_PATROL_LIST_CAP]
    bookings = (await store.list(
        "zt_dock_bookings"))[:_PATROL_LIST_CAP]
    # 月台底座: fabric 合并视图(代码种子 + 注册表)——
    # 种子不落库, 直读 zt_pois 表会漏月台
    dock_pois = await ZtFabricService(store) \
        .list_pois(poi_type="dock")

    # 工单: 已处置(acked+dismissed)为误报率分母
    # (pending 为处置预案草稿常态, 不进分母)
    ticket_total = len(tickets)
    ticket_pending = sum(
        1 for t in tickets
        if (t.get("status") or "") == "pending")
    ticket_acked = sum(
        1 for t in tickets
        if (t.get("status") or "") == "acked")
    ticket_dismissed = sum(
        1 for t in tickets
        if (t.get("status") or "") == "dismissed")
    settled_n = ticket_acked + ticket_dismissed

    feedback_total = len(feedbacks)
    feedback_rejected = sum(
        1 for f in feedbacks
        if (f.get("verdict") or "") == "rejected")

    # 月台饱和率: 峰值时段预约车数 / 月台总容量
    dock_n = len(dock_pois)
    slot_trucks = {s: 0 for s in DOCK_SLOTS}
    for b in bookings:
        slot = b.get("slot") or ""
        if slot in slot_trucks:
            slot_trucks[slot] += int(
                b.get("truckCount") or 1)
    dock_capacity = dock_n * DOCK_MAX_PER_SLOT
    peak_trucks = max(slot_trucks.values()) \
        if slot_trucks else 0

    reject_rate = (
        feedback_rejected / feedback_total
        if feedback_total > 0 else 0.0)
    false_alarm_rate = (
        ticket_dismissed / settled_n
        if settled_n > 0 else 0.0)
    dock_saturation_rate = (
        peak_trucks / dock_capacity
        if dock_capacity > 0 else 0.0)

    guard = await ZtMapModeService() \
        .guard_check(
            reject_rate,
            false_alarm_rate,
            dock_saturation_rate)
    return {
        "metrics": {
            "rejectRate": round(reject_rate, 4),
            "falseAlarmRate": round(false_alarm_rate, 4),
            "dockSaturationRate": round(
                dock_saturation_rate, 4),
        },
        "samples": {
            "tickets": {
                "total": ticket_total,
                "pending": ticket_pending,
                "acked": ticket_acked,
                "dismissed": ticket_dismissed},
            "feedbacks": {
                "total": feedback_total,
                "rejected": feedback_rejected},
            "docks": {
                "docks": dock_n,
                "capacity": dock_capacity,
                "peakTrucks": peak_trucks},
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
                    "zt_guard_patrol_paused: "
                    "%s", r.get("breaches"))
            else:
                logger.info(
                    "zt_guard_patrol_ok "
                    "metrics=%s",
                    r.get("metrics"))
        except Exception as exc:
            logger.error(
                "zt_guard_patrol_fail: %s",
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
    logger.info("zt_guard_loop_started "
                "interval=%ss",
                guard_interval_seconds())
    return True
