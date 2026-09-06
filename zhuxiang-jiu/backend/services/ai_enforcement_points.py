"""03号·会员积分 AI 决策门
(ai_enforcement_points, 全站批次二)

《全站AI智能混合架构升级总计划》批次二:
    积分主通道(返分/抵现/退款)接入
    v7.8 决策门——已注册 points_risk
    评分器(batch2)此前仅签到观察,
    批次二补全三条主通道:

        返分 earn_order_points
            → 键 points:earn:{order_id}
        抵现 deduct_points
            → 键 points:deduct:{order_id}
        退款 refund_points
            → 键 points:refund:{order_id}

    服务层挂门(一处接线覆盖全部调用方:
    points_routes 直连 + order_service
    create/pay/refund + member_service
    consume/deduct)。

铁律:
    - AI_ENFORCE_MODE 默认 observe
      ——评分+快照供学习闭环, 决策
      不生效(行为 100% 兼容)
    - 积分硬规则(每日/每月上限/30%
      抵现上限/FIFO)保留为合规底线,
      AI 门为叠加层不替代
    - enforce_decision fail-open 兜底
    - enrich 确定性聚合(当日正流水/
      当日抵现次数/夜间时段), LLM
      不进判定链
"""

import logging
from datetime import datetime

logger = logging.getLogger(
    "ai_enforcement_points")

MODEL_VERSION = "v1-points-gate"

SCORER_ID = "points_risk"

# 通道 → 决策键前缀
CHANNEL_EARN = "earn"
CHANNEL_DEDUCT = "deduct"
CHANNEL_REFUND = "refund"

# 夜间时段(0-5 点——薅羊毛高发窗口)
NIGHT_HOURS = frozenset(range(0, 6))


async def enrich_points_channel(
        user_id: int, action: str,
        points: float) -> dict:
    """决策门输入富化(03号积分域
    当日流水——确定性聚合)

    action: earn/deduct/refund
    points: 本次积分变动绝对值
    """
    from repositories.points_repository import (
        PointsRepository,
    )

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    today_earned = 0.0
    today_redeem_n = 0
    try:
        logs = await PointsRepository() \
            .list_logs(int(user_id), limit=10000) \
            or []
        for log in logs:
            if not str(
                    log.get("createdAt") or ""
            ).startswith(today_str):
                continue
            pts = float(
                log.get("points") or 0)
            if pts > 0:
                today_earned += pts
            elif pts < 0:
                today_redeem_n += 1
    except Exception:  # pragma: no cover
        pass

    # 本次动作计入(挂钩在发放前触发,
    # 流水尚未落笔——预聚合口径)
    if action == CHANNEL_EARN:
        today_earned += float(points or 0)
    elif action in (CHANNEL_DEDUCT,
                    CHANNEL_REFUND):
        today_redeem_n += 1

    return {
        "todayEarned": round(
            today_earned, 1),
        "dailyEarnCap": 200,
        "dailyRedeemCount": today_redeem_n,
        "singleChannelRatio": 0.3,
        "sameDeviceAccounts": 1,
        "violationCount": 0,
        "nightActionRatio": 1.0
        if now.hour in NIGHT_HOURS else 0.0,
    }


async def enforce_points_action(
        user_id: int, action: str,
        ctx: dict, order_id: str) -> dict:
    """积分主通道决策门(五段式)

    Returns:
        {orderId, channel, blocked,
         reviewRequired, decision}
        blocked=True → 调用方拒绝
        (enforce 模式生效)
    """
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        SCORER_ID,
        f"points:{action}:{order_id}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_points_blocked user=%r "
            "action=%s order=%s score=%s",
            user_id, action, order_id,
            decision.get("score"))
        raise ValueError(
            "积分操作被风控拦截, 请稍后重试"
            "或联系客服")
    return {
        "orderId": order_id,
        "channel": action,
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }
