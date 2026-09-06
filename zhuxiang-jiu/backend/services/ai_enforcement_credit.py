"""23号·信用管理 AI 决策门
(ai_enforcement_credit, 全站批次一)

《全站AI智能混合架构升级总计划》批次一:
    先享后付审批接入 v7.8 决策门——
    对齐 12号钱包提现五段式样板:
        ① 输入富化(enrich_credit_risk)
        ② 预生成业务键(orderNo)
        ③ enforce_decision(credit_scoring)
        ④ blocked → 拒绝(enforce 模式)
        ⑤ reviewRequired → 转人工

铁律:
    - AI_ENFORCE_MODE 默认 observe
      ——评分+快照供学习闭环, 决策
      不生效(行为 100% 兼容)
    - 决策前置到额度占用之前
      (blocked 时零额度变更)
    - enforce_decision fail-open
      兜底(引擎异常不阻塞业务)
    - 硬规则(zone_rank<3 拒绝/
      额度校验)保留为合规底线,
      AI 门为叠加层不替代
"""

import logging

from services.credit_scorer import SCORER_ID

logger = logging.getLogger(
    "ai_enforcement_credit")

MODEL_VERSION = "v1-credit-gate"


async def enrich_credit_risk(
        user_id: int, account: dict,
        amount: float, quota: float,
        used: float, month_used: float,
        monthly_limit: float,
        single_limit: float,
        paylater_orders: list = None
) -> dict:
    """决策门输入富化(23号信用域
    现成变量——确定性聚合)

    paylater_orders: 用户历史订单
    (active/review/repaid 等),
    用于逾期/履约深度统计
    """
    from core.helpers import ts as _ts
    orders = list(
        paylater_orders or [])
    overdue_n = sum(
        1 for o in orders
        if (o.get("overdueDays")
            or 0) >= 1)
    repaid_n = sum(
        1 for o in orders
        if o.get("status") == "repaid")
    # 区间持续天数
    zone_days = 0.0
    try:
        from datetime import datetime
        since = account.get(
            "scoreZoneSince")
        if since:
            created = datetime \
                .fromisoformat(
                    str(since)
                    .replace("Z", "+00:00"))
            zone_days = max(
                0.0,
                (datetime.fromisoformat(
                    _ts().replace(
                        "Z", "+00:00"))
                 - created).days)
    except (ValueError, TypeError):
        zone_days = 0.0
    return {
        "userId": int(user_id),
        "bambooScore": float(
            account.get("bambooScore")
            or 0),
        "creditLevel": account.get(
            "creditLevel"),
        "accountStatus": account.get(
            "status") or "normal",
        "quota": float(quota or 0),
        "used": float(used or 0),
        "monthUsed": float(
            month_used or 0),
        "monthlyLimit": float(
            monthly_limit or 0),
        "singleLimit": float(
            single_limit or 0),
        "amount": float(amount or 0),
        "overdueOrders": overdue_n,
        "repaidOrders": repaid_n,
        "zoneDays": zone_days,
    }


async def enforce_paylater(
        user_id: int, ctx: dict,
        order_no: str) -> dict:
    """先享后付决策门(五段式——
    对齐 enforce_withdrawal 样板)

    Returns:
        {orderNo, blocked,
         reviewRequired, decision}
        blocked=True → 调用方拒绝
        (enforce 模式生效)
    """
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        SCORER_ID,
        f"paylater:{order_no}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_paylater_blocked user=%r "
            "amount=%.2f score=%s mode=%s",
            user_id,
            float(ctx.get("amount") or 0),
            decision.get("score"),
            decision.get("mode"))
        # 用户侧不暴露内部评分细节
        raise ValueError(
            "先享后付申请被风控拦截, "
            "请稍后重试或联系客服")
    return {
        "orderNo": order_no,
        "blocked": False,
        "reviewRequired": bool(
            decision.get(
                "reviewRequired")),
        "decision": decision,
    }
