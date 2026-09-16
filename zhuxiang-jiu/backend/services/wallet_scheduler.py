"""钱包盈利模块 护栏自动巡检调度器

大模型二代(全站范式——65号/小竹同款):
    WALLET_GUARD_AUTO 默认 on(护栏是保护机制)
    WALLET_GUARD_INTERVAL 默认 3600s(最小 60s)

巡检三指标(确定性计算——分母 0 取 0, 冷启动
不误判; LLM 禁入判定链):
    提现拒绝率 withdrawRejRate
        = rejected 提现单 / 总提现单
          (list_withdrawals 全量——审批健康度代理)
    提前取出率 earlySettleRate
        = early_settled 定期 / 总结清定期
          (盈利模式健康度代理——提前取出=收益+奖品流失)
    账户冻结率 accountFrozenRate
        = frozen 账户 / 总账户
          (list_accounts 全量——风控压力代理)

聚合口径: list_accounts 全量(≤500 户) + 逐户
list_withdrawals/list_deposits(≤100 条/户)。

run_guard_patrol 可独立调用(控制面
POST /api/wallet/mode/guard 缺省聚合)。
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_GUARD_TASK = None


def guard_patrol_enabled() -> bool:
    """护栏巡检开关(WALLET_GUARD_AUTO,
    默认 on——护栏自动暂停是保护机制)"""
    return os.environ.get(
        "WALLET_GUARD_AUTO",
        "on").lower() == "on"


def guard_interval_seconds() -> int:
    """护栏巡检间隔(默认 3600s)"""
    try:
        return max(60, int(os.environ.get(
            "WALLET_GUARD_INTERVAL",
            "3600")))
    except ValueError:
        return 3600


async def run_guard_patrol() -> dict:
    """护栏巡检一轮(确定性指标计算
    + guard_check——可独立调用)

    三指标口径:
        提现拒绝率 = rejected 提现 / 总提现
        提前取出率 = early_settled 定期 / 总结清定期
        账户冻结率 = frozen 账户 / 总账户
    分母为 0 时指标取 0(冷启动不误判)。
    """
    from repositories.wallet_repository import (
        WalletRepository,
    )
    from services.wallet_mode_service import (
        WalletModeService,
    )
    repo = WalletRepository()

    # 全量账户(上限保护)——冻结率 + 聚合用户清单
    accounts = await repo.list_accounts()
    accounts = accounts[:500]
    account_total = len(accounts)
    account_frozen = sum(
        1 for a in accounts
        if (a.get("status") or "")
        == "frozen")

    # 逐户聚合提现单与定期(上限保护)
    wd_total = 0
    wd_rejected = 0
    dep_settled = 0
    dep_early = 0
    for acc in accounts:
        user_id = acc.get("userId") \
            or acc.get("user_id")
        if user_id is None:
            continue
        for wd in await repo.list_withdrawals(
                user_id, limit=100):
            wd_total += 1
            if (wd.get("status") or "") \
                    == "rejected":
                wd_rejected += 1
        for dp in await repo.list_deposits(
                user_id, limit=100):
            status = dp.get("status") or ""
            if status in ("settled",
                          "early_settled"):
                dep_settled += 1
                if status == "early_settled":
                    dep_early += 1

    withdraw_rej_rate = (
        wd_rejected / wd_total
        if wd_total > 0 else 0.0)
    early_settle_rate = (
        dep_early / dep_settled
        if dep_settled > 0 else 0.0)
    account_frozen_rate = (
        account_frozen / account_total
        if account_total > 0 else 0.0)

    guard = await WalletModeService() \
        .guard_check(
            withdraw_rej_rate,
            early_settle_rate,
            account_frozen_rate)
    return {
        "metrics": {
            "withdrawRejRate": round(
                withdraw_rej_rate, 4),
            "earlySettleRate": round(
                early_settle_rate, 4),
            "accountFrozenRate": round(
                account_frozen_rate, 4),
        },
        "samples": {
            "withdrawals": {
                "total": wd_total,
                "rejected": wd_rejected},
            "deposits": {
                "settled": dep_settled,
                "earlySettled": dep_early},
            "accounts": {
                "total": account_total,
                "frozen": account_frozen},
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
                    "wallet_guard_patrol_paused: "
                    "%s", r.get("breaches"))
            else:
                logger.info(
                    "wallet_guard_patrol_ok "
                    "metrics=%s",
                    r.get("metrics"))
        except Exception as exc:
            logger.error(
                "wallet_guard_patrol_fail: %s",
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
    logger.info("wallet_guard_loop_started "
                "interval=%ss",
                guard_interval_seconds())
    return True
