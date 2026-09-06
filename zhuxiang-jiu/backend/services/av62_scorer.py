"""62号·AI智能无形资产估值 第37档案八因子评分器
(av62_scorer)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§四):
    第37档案 asset_valuation(batch21):
        | 因子 | 权重 | 口径 |
        | valuation_accuracy | 0.20 | 估值准确率(回流验证通过/总评估) |
        | attribution_grounded | 0.15 | 归因锚定率(绑定规则 ID 的解释/总量) |
        | scenario_fitness | 0.15 | 场景折算命中率 |
        | fairness_posture | 0.15 | 公平性态势(分群体差异达标率) |
        | member_trust | 0.10 | 会员信值(47号 tier 基线) |
        | appeal_overturn | 0.10 | 申诉翻转率(反向——过高=估值偏差) |
        | latency_budget | 0.05 | 评估链路 P95 达标率 |
        | coverage_breadth | 0.10 | 要素域覆盖度 |

    输出 0-100 信任分 → 三级决策:
        observe 观察(<50 估值域收窄)
        / optimize 优化执行(≥50 规则与
        权重回流) / urgent 紧急优化
        (≥80 估值复盘会)

54-61号同范式: 纯函数零落库。
"""

import logging

logger = logging.getLogger("av62_scorer")

MODEL_VERSION = "v1-av62-scorer"

SCORER_ID = "asset_valuation"

# 三级决策(DECISION_THRESHOLDS 对齐)
DECISION_OBSERVE = "observe"
DECISION_OPTIMIZE = "optimize"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_OBSERVE: "观察(估值域收窄)",
    DECISION_OPTIMIZE: "优化执行(规则与"
                       "权重回流)",
    DECISION_URGENT: "紧急优化+估值"
                     "复盘会(估值复盘会)",
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


class Av62Scorer:
    """62号八因子评分器(第37档案
    asset_valuation)"""

    WEIGHTS = {
        "valuation_accuracy": 0.20,
        "attribution_grounded": 0.15,
        "scenario_fitness": 0.15,
        "fairness_posture": 0.15,
        "member_trust": 0.10,
        "appeal_overturn": 0.10,
        "latency_budget": 0.05,
        "coverage_breadth": 0.10,
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

        # ① 估值准确率(回流验证通过/总评估)
        acc = ctx.get("valuationAccuracy")
        if acc is None:
            s1, d1 = 50.0, "估值准确率未探(中性)"
        else:
            va = _clamp(float(acc), 0, 1)
            s1 = va * 100
            d1 = f"估值准确率 {va:.0%}"
        f1 = _factor(
            "valuation_accuracy", "估值准确",
            s1, weights[
                "valuation_accuracy"], d1)

        # ② 归因锚定率(绑定规则 ID 的
        #    解释/总量——归因幻觉防线)
        grounded = ctx.get("attributionGrounded")
        if grounded is None:
            s2, d2 = 50.0, "归因锚定未探(中性)"
        else:
            ag = _clamp(float(grounded), 0, 1)
            s2 = ag * 100
            d2 = f"归因锚定率 {ag:.0%}"
        f2 = _factor(
            "attribution_grounded", "归因锚定",
            s2, weights[
                "attribution_grounded"], d2)

        # ③ 场景折算命中率
        fitness = ctx.get("scenarioFitness")
        if fitness is None:
            s3, d3 = 50.0, "场景命中未探(中性)"
        else:
            sf = _clamp(float(fitness), 0, 1)
            s3 = sf * 100
            d3 = f"场景折算命中 {sf:.0%}"
        f3 = _factor(
            "scenario_fitness", "场景命中",
            s3, weights[
                "scenario_fitness"], d3)

        # ④ 公平性态势(分群体差异达标率)
        fairness = ctx.get("fairnessPosture")
        if fairness is None:
            s4, d4 = 50.0, "公平态势未探(中性)"
        else:
            fp = _clamp(float(fairness), 0, 1)
            s4 = fp * 100
            d4 = f"公平达标率 {fp:.0%}"
        f4 = _factor(
            "fairness_posture", "公平态势",
            s4, weights[
                "fairness_posture"], d4)

        # ⑤ 会员信值(47号 tier 基线)
        tier = str(ctx.get("tier") or "")
        if tier in TIER_BASE:
            s5 = TIER_BASE[tier]
            d5 = f"47号 tier {tier} 基线 {s5}"
        else:
            s5, d5 = 70.0, \
                "tier 未探(standard 中性)"
        f5 = _factor(
            "member_trust", "会员信值",
            s5, weights["member_trust"], d5)

        # ⑥ 申诉翻转率(反向——过高=估值
        #    偏差; 5% 翻转=90 分线性映射)
        overturn = ctx.get("appealOverturnRate")
        if overturn is None:
            s6, d6 = 70.0, "申诉未探(中性)"
        else:
            ao = _clamp(float(overturn), 0, 1)
            # 反向: 0 翻转=100; 20% 翻转=0
            s6 = _clamp(
                100 - ao * 500, 0, 100)
            d6 = f"申诉翻转率 {ao:.0%}"
        f6 = _factor(
            "appeal_overturn", "申诉翻转",
            s6, weights["appeal_overturn"], d6)

        # ⑦ 评估链路 P95 达标率
        latency = ctx.get("latencyP95Ok")
        if latency is None:
            s7, d7 = 70.0, "时效未探(中性)"
        else:
            lr = _clamp(float(latency), 0, 1)
            s7 = lr * 100
            d7 = f"P95 达标率 {lr:.0%}"
        f7 = _factor(
            "latency_budget", "评估时效",
            s7, weights["latency_budget"], d7)

        # ⑧ 要素域覆盖度(九域覆盖)
        coverage = ctx.get("domainCoverage")
        if coverage is None:
            s8, d8 = 50.0, "覆盖未探(中性)"
        else:
            raw_cov = float(coverage)
            if not 0.0 <= raw_cov <= 1.0:
                raise ValueError(
                    "要素域覆盖率须在 [0,1]")
            cov = _clamp(raw_cov, 0, 1)
            s8 = cov * 100
            d8 = f"要素域覆盖 {cov:.0%}"
        f8 = _factor(
            "coverage_breadth", "域覆盖",
            s8, weights["coverage_breadth"], d8)

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
            "note": "第37档案 asset_valuation"
                    "——八因子加权(44号学习域"
                    "可演进)",
        }
