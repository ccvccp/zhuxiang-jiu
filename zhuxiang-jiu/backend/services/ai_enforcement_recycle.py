"""13号·老酒兑换 AI 决策门
(ai_enforcement_recycle, 全站批次二)

《全站AI智能混合架构升级总计划》批次二:
    议价轨接入 v7.8 决策门——对齐
    批次一 ai_enforcement_credit 五段式:
        ① 输入富化(enrich_negotiation_risk
           ——议价记录+用户信用域现成变量
           确定性聚合)
        ② 预生成业务键(negotiation:{neg_id})
        ③ enforce_decision(recycle_valuation)
        ④ blocked → 拒绝(enforce 模式)
        ⑤ reviewRequired → 转人工复核

铁律:
    - AI_ENFORCE_MODE 默认 observe
      ——评分+快照供学习闭环, 决策
      不生效(行为 100% 兼容)
    - 议价硬规则(±10%系数/轮次上限/
      状态机)保留为合规底线, AI 门为
      叠加层不替代
    - enforce_decision fail-open 兜底
      (引擎异常不阻塞业务)
"""

import logging

from services.recycle_scorer import SCORER_ID

logger = logging.getLogger(
    "ai_enforcement_recycle")

MODEL_VERSION = "v1-recycle-gate"


async def enrich_negotiation_risk(
        user_id: int, negotiation: dict,
        proposed_price: float) -> dict:
    """决策门输入富化(13号议价域
    现成变量——确定性聚合)

    negotiation: 议价记录(repo 原始 dict),
    含 aiBasePrice/history/negotiationRound/
    conditionGrade/wineAge/bottleCount
    """
    from repositories.credit_repository import (
        CreditRepository,
    )
    from repositories.recycle_repository import (
        RecycleRepository,
    )

    # 用户竹信分(无账户 → 中性 700)
    bamboo = 700.0
    try:
        account = await CreditRepository() \
            .get_score(int(user_id))
        if account:
            bamboo = float(
                account.get("bambooScore")
                or 700.0)
    except Exception:  # pragma: no cover
        pass

    # 历史议价单数(压价操纵频次)
    neg_count = 0
    try:
        negs = await RecycleRepository() \
            .list_negotiations(
                user_id=int(user_id), limit=500)
        neg_count = len(negs or [])
    except Exception:  # pragma: no cover
        pass

    history = negotiation.get("history") or []
    history_prices = [
        float(h.get("price") or 0)
        for h in history
        if h.get("price")]
    return {
        "userId": int(user_id),
        "proposedPrice": float(
            proposed_price or 0),
        "aiBasePrice": float(
            negotiation.get("aiBasePrice")
            or 0),
        "negotiationRound": int(
            negotiation.get(
                "negotiationRound") or 0) + 1,
        "maxRounds": int(
            negotiation.get("maxRounds") or 3),
        "conditionGrade": negotiation.get(
            "conditionGrade") or "A",
        "wineAge": float(
            negotiation.get("wineAge") or 0),
        "bottleCount": int(
            negotiation.get("bottleCount") or 1),
        "bambooScore": bamboo,
        "historyPrices": history_prices,
        "userNegotiationCount": neg_count,
    }


async def enforce_proposal(
        user_id: int, ctx: dict,
        neg_id: int) -> dict:
    """用户出价决策门(五段式——
    对齐 enforce_paylater 样板)

    Returns:
        {negId, blocked, reviewRequired,
         decision}
        blocked=True → 调用方拒绝
        (enforce 模式生效)
    """
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        SCORER_ID,
        f"negotiation:{neg_id}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_proposal_blocked user=%r "
            "neg=%r score=%s mode=%s",
            user_id, neg_id,
            decision.get("score"),
            decision.get("mode"))
        # 用户侧不暴露内部评分细节
        raise ValueError(
            "出价被风控拦截, 请稍后重试"
            "或联系客服")
    return {
        "negId": neg_id,
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }
