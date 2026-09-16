"""信用管理模块 护栏自动巡检调度器

大模型二代(全站范式——65号/小竹/钱包同款):
    CREDIT_GUARD_AUTO 默认 on(护栏是保护机制)
    CREDIT_GUARD_INTERVAL 默认 3600s(最小 60s)

巡检三指标(确定性计算——分母 0 取 0, 冷启动
不误判; LLM 禁入判定链):
    逾期还款率 overdueRepayRate
        = overdue 订单 / (repaid+overdue) 终态订单
          (信用履约恶化代理)
    paylater 拒单率 paylaterRejectRate
        = rejected 订单 / 总订单
          (先用后付风控压力代理)
    黑名单占比 blacklistRate
        = blacklist 账户 / 总账户
          (信用恶化面代理)

聚合口径: list_scores 全量(≤500 户, blacklist 计数)
+ 逐户 list_paylater_orders(≤100 条/户, 状态聚合)。

run_guard_patrol 可独立调用(控制面
POST /api/credit/mode/guard 缺省聚合)。
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_GUARD_TASK = None


def guard_patrol_enabled() -> bool:
    """护栏巡检开关(CREDIT_GUARD_AUTO,
    默认 on——护栏自动暂停是保护机制)"""
    return os.environ.get(
        "CREDIT_GUARD_AUTO",
        "on").lower() == "on"


def guard_interval_seconds() -> int:
    """护栏巡检间隔(默认 3600s)"""
    try:
        return max(60, int(os.environ.get(
            "CREDIT_GUARD_INTERVAL",
            "3600")))
    except ValueError:
        return 3600


async def run_guard_patrol() -> dict:
    """护栏巡检一轮(确定性指标计算
    + guard_check——可独立调用)

    三指标口径:
        逾期还款率 = overdue / (repaid+overdue) 终态
        paylater 拒单率 = rejected / 总订单
        黑名单占比 = blacklist 账户 / 总账户
    分母为 0 时指标取 0(冷启动不误判)。
    """
    from repositories.credit_repository import (
        CreditRepository,
    )
    from services.credit_mode_service import (
        CreditModeService,
    )
    repo = CreditRepository()

    # 全量账户(上限保护)——黑名单占比 + 聚合用户清单
    accounts = await repo.list_scores()
    accounts = accounts[:500]
    account_total = len(accounts)
    blacklist_n = sum(
        1 for a in accounts
        if (a.get("status") or "")
        == "blacklist")

    # 逐户聚合 paylater 订单状态(上限保护)
    order_total = 0
    order_rejected = 0
    repaid_n = 0
    overdue_n = 0
    for acc in accounts:
        user_id = acc.get("userId") \
            or acc.get("user_id")
        if user_id is None:
            continue
        for od in await repo.list_paylater_orders(
                user_id, limit=100):
            status = od.get("status") or ""
            order_total += 1
            if status == "rejected":
                order_rejected += 1
            elif status == "repaid":
                repaid_n += 1
            elif status == "overdue":
                overdue_n += 1

    final_n = repaid_n + overdue_n
    overdue_repay_rate = (
        overdue_n / final_n
        if final_n > 0 else 0.0)
    paylater_reject_rate = (
        order_rejected / order_total
        if order_total > 0 else 0.0)
    blacklist_rate = (
        blacklist_n / account_total
        if account_total > 0 else 0.0)

    guard = await CreditModeService() \
        .guard_check(
            overdue_repay_rate,
            paylater_reject_rate,
            blacklist_rate)
    return {
        "metrics": {
            "overdueRepayRate": round(
                overdue_repay_rate, 4),
            "paylaterRejectRate": round(
                paylater_reject_rate, 4),
            "blacklistRate": round(
                blacklist_rate, 4),
        },
        "samples": {
            "paylater": {
                "total": order_total,
                "rejected": order_rejected,
                "repaid": repaid_n,
                "overdue": overdue_n},
            "accounts": {
                "total": account_total,
                "blacklist": blacklist_n},
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
                    "credit_guard_patrol_paused: "
                    "%s", r.get("breaches"))
            else:
                logger.info(
                    "credit_guard_patrol_ok "
                    "metrics=%s",
                    r.get("metrics"))
        except Exception as exc:
            logger.error(
                "credit_guard_patrol_fail: %s",
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
    logger.info("credit_guard_loop_started "
                "interval=%ss",
                guard_interval_seconds())
    return True
