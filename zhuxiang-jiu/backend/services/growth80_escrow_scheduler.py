"""80号 v2-E3·Escrow 延迟结算调度器(traffic79 引进积分观察期)

惯例对齐 ibms_patrol_scheduler / order_timeout_scheduler:
    - main.py startup 启动, 幂等; 结算逻辑独立 run_escrow_settlement
      可单测
    - 双闸: GROWTH_ESCROW_AUTO(环境, 默认 off) + settings.
      referralEscrowEnabled(业务, 默认 False)——环境闸只停扫描,
      存量 pending 照常被手动结算端点处理

环境开关:
    GROWTH_ESCROW_AUTO=off|on     调度总开关(默认 off)
    GROWTH_ESCROW_INTERVAL=N      扫描周期秒(默认 3600, 下限 300)

结算规则(实施方案 §3.3):
    到期 pending escrow → 达标判定(被引进人注册后回访登录 /
    首笔订单) → unlocked(earn_points+站内信) / forfeited(作废不发)
    熔断: 近 7 天解冻率 < 90% 连续 3 轮 → referralEscrowEnabled
    自动置 False + 管理员告警(回落即时发放)
"""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

UNLOCK_RATE_THRESHOLD = 0.90   # 熔断线: 7 天解冻率
FUSE_ROUNDS = 3               # 连续触发轮数(小时级轮, 防小样本抖动)


def scheduler_enabled() -> bool:
    """调度总开关(GROWTH_ESCROW_AUTO 默认 off——74号惯例)"""
    return os.environ.get("GROWTH_ESCROW_AUTO", "off").strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    try:
        value = int(os.environ.get("GROWTH_ESCROW_INTERVAL", "3600"))
        return max(300, value)
    except ValueError:
        return 3600


async def run_escrow_settlement(force: bool = False) -> dict:
    """到期 escrow 结算(独立可单测; 手动结算端点复用)

    force=True 时忽略 referralEscrowEnabled(存量结算不受开关影响)。
    Returns:
        {scanned, unlocked, forfeited, skipped, fuseTriggered}
    """
    from repositories.promotion_repository import PromotionRepository
    repo = PromotionRepository()
    settings = await repo.get_settings()
    if not settings.get("referralEscrowEnabled") and not force:
        return {"scanned": 0, "unlocked": 0, "forfeited": 0,
                "skipped": 0, "fuseTriggered": False}

    from core.locks import get_lock
    pending = await repo.list_escrows(state="pending", limit=10000)
    now = datetime.now(UTC)
    scanned = unlocked = forfeited = 0

    for escrow in pending:
        try:
            deadline = _parse_dt(escrow.get("deadline"))
            if deadline and deadline > now:
                continue   # 未到期
            scanned += 1
            async with get_lock(
                    f"promotion:escrow:{escrow['escrowId']}"):
                fresh = await repo.get_escrow(escrow["escrowId"])
                if not fresh or fresh.get("state") != "pending":
                    continue   # 并发已结算(幂等)
                eligible, why = await _invitee_active(
                    fresh, settings)
                if eligible:
                    await _unlock(repo, fresh, why)
                    unlocked += 1
                else:
                    await repo.update_escrow(fresh["escrowId"], {
                        "state": "forfeited",
                        "settledAt": _now_iso(),
                        "reason": "观察期满未活跃",
                    })
                    forfeited += 1
        except Exception as exc:  # noqa: BLE001
            scanned -= 1
            logger.warning("escrow_settle_skip id=%s: %s",
                           escrow.get("escrowId"), exc)

    fuse = await _fuse_check(repo, unlocked, forfeited)
    if unlocked or forfeited:
        logger.info("escrow_settled scanned=%s unlocked=%s forfeited=%s "
                    "fuse=%s", scanned, unlocked, forfeited, fuse)
    return {"scanned": scanned, "unlocked": unlocked,
            "forfeited": forfeited, "skipped": len(pending) - scanned,
            "fuseTriggered": fuse}


async def _invitee_active(escrow: dict, settings: dict) -> tuple[bool, str]:
    """达标判定: 被引进人首笔订单 / 注册后回访登录(实施方案 §3.3)

    (摸底实证: member 无 login_count 计数——以 last_login_at 晚于
    escrow 创建时间为回访口径, 僵尸号不会回来; 精确计数列 P2)
    """
    ref_id = str(escrow.get("refId") or "")
    invitee = ref_id.rsplit(":", 1)[-1]
    try:
        invitee_id = int(invitee)
    except ValueError:
        return False, "refId 解析失败"

    # 首笔订单(有订单即强活跃信号——订单创建本身即行为)
    if settings.get("escrowUnlockOrder", True):
        try:
            from repositories.order_repository import OrderRepository
            orders = await OrderRepository().get_by_member(invitee_id)
            if orders:
                return True, "首笔订单"
        except Exception as exc:  # noqa: BLE001
            logger.warning("escrow_order_check_skip: %s", exc)

    # 注册后回访(last_login_at > escrow.createdAt) / 登录精确计数
    try:
        from repositories.member_repository import MemberRepository
        member = await MemberRepository().get_by_id(invitee_id)
        last_login = str((member or {}).get("last_login_at") or "")
        created = str(escrow.get("createdAt") or "")
        if last_login and created and last_login > created:
            return True, "注册后回访"
        # P2-b: 登录精确计数 ≥3 次(旧会员无字段则本条不适用)
        login_count = int((member or {}).get("login_count", 0) or 0)
        if login_count >= 3:
            return True, f"登录{login_count}次"
    except Exception as exc:  # noqa: BLE001
        logger.warning("escrow_revisit_check_skip: %s", exc)
    return False, ""


async def _unlock(repo, escrow: dict, why: str) -> None:
    """达标发放: earn_points(幂等 refId 同源) + 状态 + 站内信"""
    from services.points_service import PointsService
    user_id = int(escrow["userId"])
    await PointsService().earn_points(
        user_id, int(escrow.get("points", 0)),
        source=str(escrow.get("source") or "traffic79"),
        ref_id=str(escrow.get("refId") or ""),
        ref_desc=f"引进积分观察期达标解冻({why}, v2-E3)")
    await repo.update_escrow(escrow["escrowId"], {
        "state": "unlocked",
        "settledAt": _now_iso(),
        "reason": why,
    })
    try:
        from repositories.message_repository import (
            CHANNEL_INMAIL, CATEGORY_MEMBER,
        )
        from services.message_service import MessageService
        await MessageService().send_message(
            user_id=user_id, channel=CHANNEL_INMAIL,
            title="推荐奖励已到账",
            content=(f"您推荐的会员已活跃，+{escrow.get('points', 0)} "
                     f"积分已到账（观察期达标）。"),
            category=CATEGORY_MEMBER,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("escrow_unlock_notify_skip: %s", exc)


_fuse_streak = 0   # 模块级连续触发计数(熔断防御性, 重启清零可接受)


async def _fuse_check(repo, unlocked: int, forfeited: int) -> bool:
    """熔断自检: 近 7 天 settled 解冻率 <90% 连续 FUSE_ROUNDS 轮 →
    自动关 escrow + 管理员告警(只降不升, 恢复需人工)"""
    global _fuse_streak
    if unlocked + forfeited == 0:
        return False
    week_ago = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    settled = [e for e in await repo.list_escrows(limit=10000)
               if e.get("state") in ("unlocked", "forfeited")
               and str(e.get("settledAt") or "") >= week_ago]
    n_unlocked = len([e for e in settled if e.get("state") == "unlocked"])
    n_total = len(settled)
    if n_total == 0:
        return False
    rate = n_unlocked / n_total
    if rate >= UNLOCK_RATE_THRESHOLD:
        _fuse_streak = 0
        return False
    _fuse_streak += 1
    logger.warning("escrow_fuse_check rate=%.2f streak=%s/%s",
                   rate, _fuse_streak, FUSE_ROUNDS)
    if _fuse_streak < FUSE_ROUNDS:
        return False
    # 熔断: 自动回落即时发放 + 告警
    _fuse_streak = 0
    try:
        from services.promotion_service import PromotionService
        await PromotionService().update_settings(
            {"referralEscrowEnabled": False}, admin="escrow-fuse")
        from services.security_alert_service import SecurityAlertService
        await SecurityAlertService().notify_growth_alerts(
            0, "escrow-fuse", 0,
            extra=(f"Escrow 熔断: 近7天解冻率 {rate:.0%} < 90% 连续 "
                   f"{FUSE_ROUNDS} 轮, 已自动回落即时发放(referralEscrow"
                   f"Enabled=False), 请人工核查引进质量。"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("escrow_fuse_action_skip: %s", exc)
    return True


def _parse_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _scheduler_loop() -> None:
    interval = scheduler_interval_seconds()
    logger.info("growth80_escrow_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_escrow_settlement()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Escrow 结算异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("growth80_escrow_scheduler disabled "
                    "(GROWTH_ESCROW_AUTO=off)")
        return False
    if _scheduler_task is not None and not _scheduler_task.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _scheduler_task = loop.create_task(_scheduler_loop())
        return True
    except RuntimeError as exc:
        logger.warning("Escrow 调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def scheduler_running() -> bool:
    return _scheduler_task is not None and not _scheduler_task.done()
