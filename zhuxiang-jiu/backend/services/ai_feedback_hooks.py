"""AI 自动反馈挂钩层(v7.6: 业务事件 → 决策快照 → 自动反馈)

补齐 v7.5 自学习层的「最后一公里」: 反馈不再依赖管理员手动提交,
而是由真实业务事件自动产生:

    评分器(观察模式) ──决策快照──→ 暂存(业务键, TTL 7天)
                                        │
    业务终态事件(完成/退款/登录/支付/…) ──┘
                                        ↓
                        配对 → 自动反馈(source=auto) → 学习闭环

设计原则(延续项目「评分零侵入」哲学):
    - 观察不阻断: 挂钩只采集信号, 不改变任何业务行为(决策阻断属后续迭代)
    - 火后不管: 所有公开函数永不抛异常, 任何失败只记日志
    - 业务键去重: 快照配对即消费(consume), 同一订单/登录只产生一条反馈
    - 语义映射: 每个挂钩点维护「决策 × 终态 → correct」映射表,
      例如订单风控: pass+完成=正确, pass+退款=误放行, review+退款=预警正确

环境开关:
    AI_FEEDBACK_HOOKS=off   关闭全部挂钩(默认开启)
"""

import logging
from typing import Optional

from core.helpers import ts
from repositories.ai_learning_repository import AiLearningRepository
from services.ai_learning_service import (
    DECISION_THRESHOLDS, _action_for_score, default_weights,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-hooks"


# ============================================================
# 开关与评分器分发
# ============================================================

def hooks_enabled() -> bool:
    """挂钩总开关(AI_FEEDBACK_HOOKS=off 关闭, 默认开启)"""
    import os
    return os.environ.get("AI_FEEDBACK_HOOKS", "on").strip().lower() != "off"


async def _invoke_scorer(scorer_id: str, ctx: dict) -> Optional[dict]:
    """按注册表调用评分器(懒加载避免循环导入), 失败返回 None"""
    try:
        if scorer_id.startswith("logistics_routing:"):
            from services.ai_scoring_service import LogisticsRoutingScorer
            budget = scorer_id.split(":", 1)[1]
            return await LogisticsRoutingScorer().score({**ctx, "budget": budget})
        if scorer_id == "order_risk":
            from services.ai_scoring_service import OrderRiskScorer
            return await OrderRiskScorer().score(ctx)
        if scorer_id == "payment_routing":
            from services.ai_scoring_service import PaymentRoutingScorer
            return await PaymentRoutingScorer().score(ctx)
        if scorer_id == "traffic_antifraud":
            from services.ai_scoring_service import TrafficAntiFraudScorer
            return await TrafficAntiFraudScorer().score(ctx)
        if scorer_id == "promotion_antifraud":
            from services.ai_scoring_service import PromotionAntiFraudScorer
            return await PromotionAntiFraudScorer().score(ctx)
        if scorer_id == "auth_risk":
            from services.ai_scoring_auth_service import AuthRiskScorer
            return await AuthRiskScorer().score(ctx)
        if scorer_id in ("points_risk", "withdraw_risk"):
            from services.ai_scoring_ext_service import (
                PointsRiskScorer, WithdrawRiskScorer,
            )
            cls = {"points_risk": PointsRiskScorer,
                   "withdraw_risk": WithdrawRiskScorer}[scorer_id]
            return await cls().score(ctx)
    except Exception as exc:  # noqa: BLE001 - 挂钩失败不影响业务
        logger.warning("挂钩评分失败(scorer=%s): %s", scorer_id, exc)
    return None


def _extract_decision(scorer_id: str, score_result: dict) -> Optional[str]:
    """从评分结果提取决策动作(阈值类→动作, 路由类→推荐编码)"""
    if not isinstance(score_result, dict):
        return None
    if scorer_id in DECISION_THRESHOLDS:
        return _action_for_score(scorer_id,
                                 float(score_result.get("score") or 0))
    recommendation = score_result.get("recommendation") or {}
    return (recommendation.get("channelCode")
            or recommendation.get("carrier")
            or None)


def _to_snake(name: str) -> str:
    """camelCase → snake_case(候选项因子键对齐权重档案键名)"""
    return "".join(f"_{ch.lower()}" if ch.isupper() else ch
                   for ch in name).lstrip("_")


def _factors_from_result(scorer_id: str,
                         score_result: dict) -> Optional[list]:
    """提取因子快照

    阈值类评分器: 顶层 factors 列表直接可用。
    路由类评分器(支付/物流): 顶层无 factors, 从「最优候选项」的因子
    dict 合成快照(camelCase→snake_case 对齐可学习权重档案键名,
    档案外因子过滤丢弃)。
    """
    factors = score_result.get("factors")
    if isinstance(factors, list) and factors:
        return factors
    recommendation = score_result.get("recommendation") or {}
    code = (recommendation.get("channelCode")
            or recommendation.get("carrier") or "")
    candidates = score_result.get("candidates") or []
    best = next((c for c in candidates
                 if c.get("channelCode") == code or c.get("carrier") == code),
                None)
    if not best:
        return None
    defaults = default_weights(scorer_id)
    snapshot = []
    for key, value in (best.get("factors") or {}).items():
        name = _to_snake(str(key))
        if name in defaults:
            snapshot.append({
                "name": name,
                "score": round(float(value or 0), 1),
                "weight": 0.0,
                "contribution": round(float(value or 0), 1),
            })
    return snapshot or None


# ============================================================
# 通用配对引擎(快照存取 + 反馈生成)
# ============================================================

async def snapshot_decision(scorer_id: str, business_key: str,
                            score_result: dict) -> bool:
    """暂存一条决策快照(评分后调用), 返回是否成功"""
    if not hooks_enabled():
        return False
    decision = _extract_decision(scorer_id, score_result)
    if decision is None:
        return False
    factors = _factors_from_result(scorer_id, score_result)
    if not factors:
        return False
    score = float(score_result.get("score")
                  or (score_result.get("recommendation") or {}).get("score")
                  or 0)
    try:
        repo = AiLearningRepository()
        snapshot = {
            "decision": decision,
            "score": score,
            "factors": factors,
            "weightVersion": score_result.get("weightVersion", "v1"),
            "actualAction": None,   # 路由类: 实际使用的通道/承运商
            "meta": {},
        }
        # v7.9 知识增强: 检索 top-k 相似案例证据块(火后不管, 失败不存)
        try:
            from services.ai_knowledge_service import (
                augment_with_knowledge,
            )
            knowledge = await augment_with_knowledge(
                scorer_id, factors, score)
            if knowledge:
                snapshot["knowledge"] = knowledge
        except Exception as exc:  # noqa: BLE001
            logger.debug("快照知识增强跳过(scorer=%s): %s", scorer_id, exc)
        await repo.save_decision_snapshot(scorer_id, business_key, snapshot)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("决策快照暂存失败(scorer=%s key=%s): %s",
                       scorer_id, business_key, exc)
        return False


async def record_outcome(scorer_id: str, business_key: str,
                         outcome: str, *, correct: Optional[bool] = None,
                         note: str = "") -> Optional[dict]:
    """业务终态事件 → 消费快照 → 自动反馈

    correct 未提供时按「决策 == 期望终态」推导:
        快照 decision 即 expectedAction, outcome 即 actualAction。
    返回 submit_feedback 结果; 无快照/未启用/配对失败返回 None。
    """
    if not hooks_enabled():
        return None
    try:
        repo = AiLearningRepository()
        snapshot = await repo.consume_decision_snapshot(scorer_id, business_key)
        if snapshot is None:
            return None  # 无快照(未评分/已配对/过期) → 静默跳过
        from services.ai_learning_service import submit_feedback
        payload = {
            "scorerId": scorer_id,
            "factors": snapshot.get("factors") or [],
            "scoreAtDecision": snapshot.get("score", 0),
            "actualAction": outcome,
            "note": note or f"auto:{business_key}",
            "weightVersion": snapshot.get("weightVersion"),
            "source": "auto",
        }
        if correct is not None:
            payload["correct"] = bool(correct)
        else:
            payload["expectedAction"] = snapshot.get("decision")
        return await submit_feedback(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("自动反馈配对失败(scorer=%s key=%s): %s",
                       scorer_id, business_key, exc)
        return None


async def score_and_snapshot(scorer_id: str, business_key: str,
                             ctx: dict) -> Optional[dict]:
    """评分 + 快照一步完成(决策点调用), 返回评分结果"""
    if not hooks_enabled():
        return None
    result = await _invoke_scorer(scorer_id, ctx)
    if result is None:
        return None
    await snapshot_decision(scorer_id, business_key, result)
    return result


# ============================================================
# 领域挂钩(业务路由调用, 全部永不抛异常)
# ============================================================

# 订单风控: 决策 × 终态 → 是否正确
_ORDER_OUTCOME_CORRECT = {
    ("pass", "completed"): True,    # 放行且顺利完成 → 正确
    ("pass", "refunded"): False,    # 放行却退款 → 误放行
    ("pass", "returning"): False,   # 放行却退货 → 误放行
    ("review", "refunded"): True,   # 预警且退款 → 预警正确
    ("review", "returning"): True,
    ("review", "completed"): False, # 预警却顺利完成 → 过度预警
    ("block", "refunded"): True,
    ("block", "returning"): True,
    ("block", "completed"): False,
}


async def on_order_created(order_id: str, member_id: str,
                           items: list[dict],
                           address: Optional[dict] = None,
                           remark: str = "") -> None:
    """订单创建 → 订单风控评分(v7.8 输入富化: 真实信用/行为画像) + 快照"""
    try:
        from services.ai_context_enricher import enrich_order_risk
        ctx = await enrich_order_risk(member_id, items,
                                      address=address, remark=remark)
        await score_and_snapshot("order_risk", f"order:{order_id}", ctx)
    except Exception as exc:  # noqa: BLE001
        logger.warning("on_order_created 挂钩失败(order=%s): %s", order_id, exc)


async def on_order_outcome(order_id: str, outcome: str) -> None:
    """订单终态(completed/refunded/returning) → 自动反馈(带语义映射)"""
    try:
        repo = AiLearningRepository()
        snapshot = await repo.get_decision_snapshot("order_risk",
                                                    f"order:{order_id}")
        decision = (snapshot or {}).get("decision")
        correct = _ORDER_OUTCOME_CORRECT.get((decision, outcome))
        await record_outcome("order_risk", f"order:{order_id}", outcome,
                             correct=correct, note=f"order {outcome}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("on_order_outcome 挂钩失败(order=%s): %s", order_id, exc)


async def on_login_success(phone: str) -> None:
    """登录成功(凭证校验通过) → 认证风控评分 + 立即配对反馈

    语义: 凭证有效却仍完成登录的, 期望决策为 allow(惩罚过度拦截)。
    """
    try:
        business_key = f"login:{phone}:{ts()}"
        result = await score_and_snapshot("auth_risk", business_key, {
            "failedAttempts": 0, "newDevice": False, "ipRiskType": "clean",
        })
        if result is not None:
            decision = _extract_decision("auth_risk", result)
            await record_outcome("auth_risk", business_key, "logged_in",
                                 correct=(decision == "allow"),
                                 note=f"login {phone}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("on_login_success 挂钩失败: %s", exc)


async def on_payment(order_id: str, method: str, amount: float) -> None:
    """支付订单 → 支付路由评分 + 立即配对(推荐渠道 vs 实际渠道)"""
    try:
        business_key = f"payment:{order_id}"
        result = await score_and_snapshot("payment_routing", business_key, {
            "amount": float(amount or 0), "sceneType": "order_pay",
        })
        if result is not None:
            recommended = (result.get("recommendation") or {}).get("channelCode")
            await record_outcome("payment_routing", business_key,
                                 str(method or ""),
                                 correct=(recommended == method),
                                 note=f"pay {method} vs {recommended}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("on_payment 挂钩失败(order=%s): %s", order_id, exc)


async def on_shipped(order_id: str, carrier: str) -> None:
    """发货 → 物流路由评分(均衡策略) + 立即配对(推荐 vs 实际承运商)"""
    try:
        business_key = f"ship:{order_id}"
        # 中性默认包裹画像(后续接入订单明细富化实际重量/保价/结算方式)
        result = await score_and_snapshot(
            "logistics_routing:balanced", business_key, {"weight": 1.0})
        if result is not None:
            recommended = (result.get("recommendation") or {}).get("carrier")
            await record_outcome("logistics_routing:balanced", business_key,
                                 str(carrier or ""),
                                 correct=(recommended == carrier),
                                 note=f"ship {carrier} vs {recommended}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("on_shipped 挂钩失败(order=%s): %s", order_id, exc)


async def on_points_earned(member_id: str, points: float) -> None:
    """积分发放 → 积分防薅羊毛评分(v7.8 富化: 当日获取爆发聚合流水) + 立即配对"""
    try:
        from services.ai_context_enricher import enrich_points_risk
        ctx = await enrich_points_risk(member_id, points)
        business_key = f"points:{member_id}:{ts()}"
        result = await score_and_snapshot("points_risk", business_key, ctx)
        if result is not None:
            decision = _extract_decision("points_risk", result)
            await record_outcome("points_risk", business_key, "earned",
                                 correct=(decision == "low"),
                                 note=f"points +{points}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("on_points_earned 挂钩失败: %s", exc)


async def on_withdraw_requested(withdraw_no: str, amount: float,
                                balance: float,
                                member_id=None) -> None:
    """提现申请 → 提现风控评分(v7.8 富化: 提现频率/驳回/账户状态) + 快照

    Args:
        balance: 提现前可用余额; member_id 提供时启用画像富化(缺省中性输入)
    """
    try:
        ctx = {"amount": float(amount or 0), "balance": float(balance or 0)}
        if member_id is not None:
            from services.ai_context_enricher import enrich_withdraw_risk
            ctx = await enrich_withdraw_risk(member_id, amount, balance)
        await score_and_snapshot("withdraw_risk", f"withdraw:{withdraw_no}", ctx)
    except Exception as exc:  # noqa: BLE001
        logger.warning("on_withdraw_requested 挂钩失败(%s): %s",
                       withdraw_no, exc)


async def on_withdraw_settled(withdraw_no: str, approved: bool) -> None:
    """提现终态(approved/rejected) → 自动反馈(审批通过期望 low 风险)"""
    try:
        repo = AiLearningRepository()
        snapshot = await repo.get_decision_snapshot(
            "withdraw_risk", f"withdraw:{withdraw_no}")
        decision = (snapshot or {}).get("decision")
        correct = None
        if decision:
            correct = (decision == "low") if approved else (decision != "low")
        await record_outcome("withdraw_risk", f"withdraw:{withdraw_no}",
                             "approved" if approved else "rejected",
                             correct=correct,
                             note=f"withdraw {'approved' if approved else 'rejected'}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("on_withdraw_settled 挂钩失败(%s): %s",
                       withdraw_no, exc)


async def on_promotion_reward(biz_no: str, relation_count: int = 5) -> None:
    """推广奖励发放 → 防作弊评分 + 立即配对(正常发放期望 pay)"""
    try:
        business_key = f"promo:{biz_no}:{ts()}"
        result = await score_and_snapshot("promotion_antifraud", business_key, {
            "relationCount": relation_count,
            "avgBindToRewardHours": 48,
            "inactiveInviteeRatio": 0.2,
        })
        if result is not None:
            decision = _extract_decision("promotion_antifraud", result)
            await record_outcome("promotion_antifraud", business_key, "paid",
                                 correct=(decision == "pay"),
                                 note=f"promo reward {biz_no}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("on_promotion_reward 挂钩失败: %s", exc)


async def on_traffic_commission(biz_no: str, total_records: int = 100) -> None:
    """流量佣金计算 → 防作弊评分 + 立即配对(正常计佣期望 pass)"""
    try:
        business_key = f"traffic:{biz_no}:{ts()}"
        result = await score_and_snapshot("traffic_antifraud", business_key, {
            "recentCount": 10, "totalRecords": total_records,
            "newAccountRatio": 0.1,
        })
        if result is not None:
            decision = _extract_decision("traffic_antifraud", result)
            await record_outcome("traffic_antifraud", business_key, "settled",
                                 correct=(decision == "pass"),
                                 note=f"commission {biz_no}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("on_traffic_commission 挂钩失败: %s", exc)
