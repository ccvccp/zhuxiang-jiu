"""59号·AI智能服务编排 第34档案八因子评分器
(ii59_scorer)

计划(docs/59号_AI智能服务编排模块实施计划.md §六):
    第34档案 service_orchestration(batch18):
        | 因子 | 权重 | 口径 |
        | session_resolution | 0.20 | 会话解决率 |
        | search_adoption | 0.15 | 搜索采纳率 |
        | recommend_diversity | 0.15 | 推荐多样性 |
        | risk_accuracy | 0.15 | 风控准确率 |
        | member_trust | 0.10 | 会员信值 |
        | escalation_rate | 0.10 | 人工接管率 |
        | latency_budget | 0.05 | 延迟预算 |
        | coverage_breadth | 0.10 | 服务覆盖度 |

    输出 0-100 信任分 → 三级决策:
        observe 观察(<50) / optimize 优化执行(≥50)
        / urgent 紧急优化+人工加急(≥80)

54-58号同范式: 纯函数零落库。
"""

import logging

logger = logging.getLogger("ii59_scorer")

MODEL_VERSION = "v1-ii59-scorer"

SCORER_ID = "service_orchestration"

# 三级决策(DECISION_THRESHOLDS 对齐)
DECISION_OBSERVE = "observe"
DECISION_OPTIMIZE = "optimize"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_OBSERVE: "观察(不优化)",
    DECISION_OPTIMIZE: "优化执行(编排模板+"
                       "语料回流)",
    DECISION_URGENT: "紧急优化+人工加急"
                     "(服务复盘会)",
}

# 47号 tier 基线分(58号 TIER_BASE 对齐)
TIER_BASE = {
    "trusted": 90.0,
    "standard": 70.0,
    "watched": 50.0,
    "restricted": 30.0,
}


def _clamp(value: float, low: float,
           high: float) -> float:
    return max(low, min(high, value))


def _factor(name: str, label: str, score: float,
            weight: float, detail: str) -> dict:
    return {
        "name": name, "label": label,
        "score": round(float(score), 1),
        "weight": round(float(weight), 4),
        "contribution": round(
            float(score) * float(weight), 2),
        "detail": detail,
    }


class Ii59Scorer:
    """59号八因子评分器(第34档案
    service_orchestration)"""

    # 八因子默认权重(44号学习域可演进——
    # 护栏 [0.5,2.0] 倍)
    WEIGHTS = {
        "session_resolution": 0.20,
        "search_adoption": 0.15,
        "recommend_diversity": 0.15,
        "risk_accuracy": 0.15,
        "member_trust": 0.10,
        "escalation_rate": 0.10,
        "latency_budget": 0.05,
        "coverage_breadth": 0.10,
    }

    async def score(self, ctx: dict) -> dict:
        """评分入口: 八因子加权 → 信任分(0-100) →
        三级决策

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

        # ① 会话解决率(resolved/总闭话)
        resolution = ctx.get("sessionResolution")
        if resolution is None:
            s1, d1 = 50.0, "会话未建(中性)"
        else:
            rr = _clamp(float(resolution), 0, 1)
            s1 = rr * 100
            d1 = f"会话解决率 {rr:.0%}"
        f1 = _factor(
            "session_resolution", "会话解决",
            s1, weights["session_resolution"], d1)

        # ② 搜索采纳率(点击/曝光)
        adoption = ctx.get("searchAdoption")
        if adoption is None:
            s2, d2 = 50.0, "采纳未探(中性)"
        else:
            ar = _clamp(float(adoption), 0, 1)
            s2 = ar * 100
            d2 = f"搜索采纳率 {ar:.0%}"
        f2 = _factor(
            "search_adoption", "搜索采纳",
            s2, weights["search_adoption"], d2)

        # ③ 推荐多样性(类目熵归一)
        diversity = ctx.get("recommendDiversity")
        if diversity is None:
            s3, d3 = 50.0, "多样性未探(中性)"
        else:
            dv = _clamp(float(diversity), 0, 1)
            s3 = dv * 100
            d3 = f"类目熵 {dv:.2f}"
        f3 = _factor(
            "recommend_diversity", "推荐多样性",
            s3, weights[
                "recommend_diversity"], d3)

        # ④ 风控准确率(处置正确率回流)
        risk_acc = ctx.get("riskAccuracy")
        if risk_acc is None:
            s4, d4 = 50.0, "风控未探(中性)"
        else:
            ra = _clamp(float(risk_acc), 0, 1)
            s4 = ra * 100
            d4 = f"处置正确率 {ra:.0%}"
        f4 = _factor(
            "risk_accuracy", "风控准确",
            s4, weights["risk_accuracy"], d4)

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

        # ⑥ 人工接管率(过高=编排能力弱——
        #    反向因子: 接管率低分高)
        escalation = ctx.get("escalationRate")
        if escalation is None:
            s6, d6 = 70.0, "接管率未探(中性)"
        else:
            er = _clamp(float(escalation), 0, 1)
            # 反向: 10% 接管=90 分(满分参照),
            # 50% 接管=50 分, 线性映射
            s6 = _clamp(
                100 - er * 100 * 1.25, 0, 100)
            d6 = f"人工接管率 {er:.0%}"
        f6 = _factor(
            "escalation_rate", "接管率",
            s6, weights["escalation_rate"], d6)

        # ⑦ 延迟预算(P95 达标率)
        latency = ctx.get("latencyP95Ok")
        if latency is None:
            s7, d7 = 70.0, "延迟未探(中性)"
        else:
            lr = _clamp(float(latency), 0, 1)
            s7 = lr * 100
            d7 = f"P95 达标率 {lr:.0%}"
        f7 = _factor(
            "latency_budget", "延迟预算",
            s7, weights["latency_budget"], d7)

        # ⑧ 服务覆盖度(意图→服务映射覆盖)
        coverage = ctx.get("serviceCoverage")
        if coverage is None:
            s8, d8 = 50.0, "覆盖未探(中性)"
        else:
            raw_cov = float(coverage)
            if not 0.0 <= raw_cov <= 1.0:
                raise ValueError(
                    "服务覆盖率须在 [0,1]")
            cov = _clamp(raw_cov, 0, 1)
            s8 = cov * 100
            d8 = f"意图服务映射覆盖 {cov:.0%}"
        f8 = _factor(
            "coverage_breadth", "服务覆盖",
            s8, weights["coverage_breadth"], d8)

        factors = [f1, f2, f3, f4, f5, f6, f7, f8]
        trust_score = round(sum(
            f["contribution"]
            for f in factors), 1)
        trust_score = _clamp(trust_score, 0, 100)

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
            "note": "第34档案 service_orchestration"
                    "——八因子加权(44号学习域"
                    "可演进)",
        }
