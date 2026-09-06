"""64号·信值兑换管理 第38档案八因子评分器
(xx64_scorer)

计划(docs/64号_信值兑换商品服务AI智能管理模块
实施计划.md §五):
    第38档案 value_exchange(batch22):
        | 因子 | 权重 | 口径 |
        | exchange_health | 0.20 |
            兑换健康度(正常完成/总订单) |
        | rule_compliance | 0.15 |
            刚性规则拦截准确率 |
        | arbitrage_blocked | 0.15 |
            套利拦截率 |
        | anchor_stability | 0.15 |
            锚定稳定性(购买力指数
            波动率反向) |
        | liquidity_posture | 0.10 |
            流动性态势(消耗/发行
            速率比) |
        | member_trust | 0.10 |
            会员信值(47号 tier 基线) |
        | appeal_overturn | 0.10 |
            申诉翻转率(反向) |
        | latency_budget | 0.05 |
            结算链路 P95 达标率 |

    输出 0-100 信任分 → 三级决策:
        observe 观察(<50 结算域收窄)
        / optimize 优化执行(≥50 规则
        与权重回流) / urgent 紧急优化
        (≥80 兑换经济复盘会)

54-62号同范式: 纯函数零落库。
"""

import logging

logger = logging.getLogger("xx64_scorer")

MODEL_VERSION = "v1-xx64-scorer"

SCORER_ID = "value_exchange"

# 三级决策(DECISION_THRESHOLDS 对齐)
DECISION_OBSERVE = "observe"
DECISION_OPTIMIZE = "optimize"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_OBSERVE:
        "观察(结算域收窄)",
    DECISION_OPTIMIZE:
        "优化执行(规则与权重回流)",
    DECISION_URGENT:
        "紧急优化(兑换经济复盘会)",
}

# 47号 tier 基线分
TIER_BASE = {
    "trusted": 90.0,
    "standard": 70.0,
    "watched": 50.0,
    "restricted": 30.0,
}


def _clamp(value: float, low: float,
           high: float) -> float:
    return max(low, min(high, value))


def _factor(name: str, label: str,
            score: float, weight: float,
            detail: str) -> dict:
    return {
        "name": name, "label": label,
        "score": round(float(score), 1),
        "weight": round(float(weight), 4),
        "contribution": round(
            float(score) * float(weight), 2),
        "detail": detail,
    }


class Xx64Scorer:
    """64号八因子评分器(第38档案
    value_exchange)"""

    WEIGHTS = {
        "exchange_health": 0.20,
        "rule_compliance": 0.15,
        "arbitrage_blocked": 0.15,
        "anchor_stability": 0.15,
        "liquidity_posture": 0.10,
        "member_trust": 0.10,
        "appeal_overturn": 0.10,
        "latency_budget": 0.05,
    }

    async def score(self, ctx: dict) -> dict:
        """评分入口: 八因子加权 → 信任分
        (0-100) → 三级决策

        Raises:
            ValueError: 输入非法(比率越界)
        """
        if not ctx:
            raise ValueError("评分上下文不能为空")

        from services.ai_learning_service import (
            load_effective_weights,
        )
        weights = await load_effective_weights(
            SCORER_ID, self.WEIGHTS)

        # ① 兑换健康度(正常完成/总订单)
        health = ctx.get("exchangeHealth")
        if health is None:
            s1, d1 = 50.0, "健康度未探(中性)"
        else:
            h = _clamp(float(health), 0, 1)
            s1 = h * 100
            d1 = f"兑换健康度 {h:.0%}"
        f1 = _factor(
            "exchange_health", "兑换健康",
            s1, weights[
                "exchange_health"], d1)

        # ② 刚性规则拦截准确率
        compliance = ctx.get(
            "ruleCompliance")
        if compliance is None:
            s2, d2 = 50.0, "合规未探(中性)"
        else:
            rc = _clamp(float(compliance),
                        0, 1)
            s2 = rc * 100
            d2 = f"规则拦截准确率 {rc:.0%}"
        f2 = _factor(
            "rule_compliance", "规则合规",
            s2, weights[
                "rule_compliance"], d2)

        # ③ 套利拦截率
        arbitrage = ctx.get(
            "arbitrageBlocked")
        if arbitrage is None:
            s3, d3 = 50.0, "套利未探(中性)"
        else:
            ab = _clamp(float(arbitrage),
                        0, 1)
            s3 = ab * 100
            d3 = f"套利拦截率 {ab:.0%}"
        f3 = _factor(
            "arbitrage_blocked", "套利拦截",
            s3, weights[
                "arbitrage_blocked"], d3)

        # ④ 锚定稳定性(购买力指数
        #    波动率反向: 0% 波动=100;
        #    30% 波动=0)
        anchor = ctx.get("anchorVolatility")
        if anchor is None:
            s4, d4 = 70.0, "锚定未探(中性)"
        else:
            av = _clamp(float(anchor), 0, 1)
            s4 = _clamp(
                100 - av * 333.3, 0, 100)
            d4 = f"指数波动率 {av:.0%}"
        f4 = _factor(
            "anchor_stability", "锚定稳定",
            s4, weights[
                "anchor_stability"], d4)

        # ⑤ 流动性态势(消耗/发行
        #    速率比: 1.0 均衡=90;
        #    过热>2 或枯竭<0.3 扣减)
        liquidity = ctx.get(
            "liquidityRatio")
        if liquidity is None:
            s5, d5 = 70.0, "流动性未探(中性)"
        else:
            lr = _clamp(float(liquidity),
                        0, 5)
            deviation = abs(lr - 1.0)
            s5 = _clamp(
                90 - deviation * 40,
                0, 100)
            d5 = f"消耗/发行比 {lr:.2f}"
        f5 = _factor(
            "liquidity_posture", "流动性",
            s5, weights[
                "liquidity_posture"], d5)

        # ⑥ 会员信值(47号 tier 基线)
        tier = str(ctx.get("tier") or "")
        if tier in TIER_BASE:
            s6 = TIER_BASE[tier]
            d6 = f"47号 tier {tier} 基线 {s6}"
        else:
            s6, d6 = 70.0, \
                "tier 未探(standard 中性)"
        f6 = _factor(
            "member_trust", "会员信值",
            s6, weights["member_trust"], d6)

        # ⑦ 申诉翻转率(反向——过高=
        #    结算偏差; 0 翻转=100;
        #    20% 翻转=0)
        overturn = ctx.get(
            "appealOverturnRate")
        if overturn is None:
            s7, d7 = 70.0, "申诉未探(中性)"
        else:
            ao = _clamp(float(overturn),
                        0, 1)
            s7 = _clamp(
                100 - ao * 500, 0, 100)
            d7 = f"申诉翻转率 {ao:.0%}"
        f7 = _factor(
            "appeal_overturn", "申诉翻转",
            s7, weights[
                "appeal_overturn"], d7)

        # ⑧ 结算链路 P95 达标率
        latency = ctx.get("latencyP95Ok")
        if latency is None:
            s8, d8 = 70.0, "时效未探(中性)"
        else:
            lp = _clamp(float(latency),
                        0, 1)
            s8 = lp * 100
            d8 = f"P95 达标率 {lp:.0%}"
        f8 = _factor(
            "latency_budget", "结算时效",
            s8, weights["latency_budget"], d8)

        factors = [
            f1, f2, f3, f4, f5, f6, f7, f8]
        trust_score = round(sum(
            f["contribution"]
            for f in factors), 1)
        trust_score = _clamp(
            trust_score, 0, 100)

        if trust_score >= 80.0:
            decision = DECISION_URGENT
        elif trust_score >= 50.0:
            decision = DECISION_OPTIMIZE
        else:
            decision = DECISION_OBSERVE

        return {
            "success": True,
            "modelVersion": MODEL_VERSION,
            "scorerId": SCORER_ID,
            "trustScore": trust_score,
            "decision": decision,
            "decisionName":
                DECISION_NAMES[decision],
            "factors": factors,
            "weightsUsed": weights,
            "note": "第38档案 value_exchange"
                    "——八因子加权(44号学习域"
                    "可演进)",
        }
