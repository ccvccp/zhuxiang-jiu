"""58号·AI智能优化意图识别 第33档案八因子评分器
(ii58_scorer)

计划(docs/58号_AI智能优化意图识别算法模块实施计划.md §五):
    第33档案 intent_orchestration(batch17):
        | 因子 | 权重 | 口径 |
        | corpus_quality | 0.15 | 语料质量(正样本占比+
        |                  | humanVerified 率) |
        | intent_confidence | 0.20 | 意图置信度(近期平均) |
        | member_trust | 0.15 | 会员信值(47号 tier 基线) |
        | boundary_clarity | 0.15 | 边界清晰度(对抗区分率) |
        | history_success | 0.10 | 历史识别成功率(回流真值) |
        | compliance_posture | 0.10 | 合规态势(越界拦截率) |
        | latency_budget | 0.05 | 延迟预算(规则轨达标率) |
        | coverage_breadth | 0.10 | 覆盖广度(意图域覆盖度) |

    输出 0-100 信任分 → 三级决策:
        observe 观察(<50) / optimize 优化执行(≥50)
        / urgent 紧急优化+人工加急(≥80)

54/55/56/57号同范式: 纯函数零落库。
"""

import logging

logger = logging.getLogger("ii58_scorer")

MODEL_VERSION = "v1-ii58-scorer"

SCORER_ID = "intent_orchestration"

# 三级决策(DECISION_THRESHOLDS 对齐——44号池回放口径)
DECISION_OBSERVE = "observe"
DECISION_OPTIMIZE = "optimize"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_OBSERVE: "观察(不优化)",
    DECISION_OPTIMIZE: "优化执行(语料+标注回流)",
    DECISION_URGENT: "紧急优化+人工加急"
                      "(Bad Case 复盘会)",
}

# 47号 tier 基线分(计划 §五)
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


class Ii58Scorer:
    """58号八因子评分器(第33档案 intent_orchestration)"""

    # 八因子默认权重(44号学习域可演进——
    # 护栏 [0.5,2.0] 倍)
    WEIGHTS = {
        "corpus_quality": 0.15,
        "intent_confidence": 0.20,
        "member_trust": 0.15,
        "boundary_clarity": 0.15,
        "history_success": 0.10,
        "compliance_posture": 0.10,
        "latency_budget": 0.05,
        "coverage_breadth": 0.10,
    }

    async def score(self, ctx: dict) -> dict:
        """评分入口: 八因子加权 → 信任分(0-100) →
        三级决策

        Raises:
            ValueError: 输入非法(置信度/覆盖率越界)
        """
        if not ctx:
            raise ValueError("评分上下文不能为空")

        from services.ai_learning_service import (
            load_effective_weights,
        )
        weights = await load_effective_weights(
            SCORER_ID, self.WEIGHTS)

        # ① 语料质量(正样本占比+humanVerified 率)
        positive_ratio = ctx.get("positiveRatio")
        verified_ratio = ctx.get("humanVerifiedRatio")
        if positive_ratio is None \
                and verified_ratio is None:
            s1, d1 = 50.0, "语料未建(中性)"
        else:
            pr = _clamp(float(
                positive_ratio or 0), 0, 1)
            vr = _clamp(float(
                verified_ratio or 0), 0, 1)
            s1 = (pr + vr) / 2 * 100
            d1 = (f"正样本 {pr:.0%}+人工审核 "
                  f"{vr:.0%}")
        f1 = _factor("corpus_quality", "语料质量",
                     s1, weights["corpus_quality"], d1)

        # ② 意图置信度(近期识别平均)
        avg_conf = ctx.get("avgConfidence")
        if avg_conf is None:
            s2, d2 = 50.0, "置信度未探(中性)"
        else:
            avg_conf = _clamp(float(avg_conf),
                             0.0, 1.0)
            s2 = avg_conf * 100
            d2 = f"近期平均置信度 {avg_conf:.2f}"
        f2 = _factor("intent_confidence",
                     "意图置信度",
                     s2, weights["intent_confidence"], d2)

        # ③ 会员信值(47号 tier 基线)
        tier = str(ctx.get("tier") or "")
        if tier in TIER_BASE:
            s3 = TIER_BASE[tier]
            d3 = f"47号 tier {tier} 基线 {s3}"
        else:
            s3, d3 = 70.0, "tier 未探(standard 中性)"
        f3 = _factor("member_trust", "会员信值",
                     s3, weights["member_trust"], d3)

        # ④ 边界清晰度(对抗区分率)
        boundary = ctx.get("boundaryAccuracy")
        if boundary is None:
            s4, d4 = 50.0, "边界未探(中性)"
        else:
            ba = _clamp(float(boundary), 0, 1)
            s4 = ba * 100
            d4 = f"对抗区分正确率 {ba:.0%}"
        f4 = _factor("boundary_clarity", "边界清晰度",
                     s4, weights["boundary_clarity"], d4)

        # ⑤ 历史识别成功率(回流真值)
        hist = ctx.get("historySuccessRate")
        if hist is None:
            s5, d5 = 50.0, "无历史(中性)"
        else:
            hr = _clamp(float(hist), 0, 1)
            s5 = hr * 100
            d5 = f"历史成功率 {hr:.0%}"
        f5 = _factor("history_success", "历史成功",
                     s5, weights["history_success"], d5)

        # ⑥ 合规态势(越界拦截率)
        intercept = ctx.get("boundaryInterceptRate")
        if intercept is None:
            s6, d6 = 70.0, "合规态势未探(中性)"
        else:
            ir = _clamp(float(intercept), 0, 1)
            s6 = ir * 100
            d6 = f"越界拦截率 {ir:.0%}"
        f6 = _factor("compliance_posture", "合规态势",
                     s6, weights["compliance_posture"], d6)

        # ⑦ 延迟预算(规则轨 P95 达标率)
        latency = ctx.get("latencyP95Ok")
        if latency is None:
            s7, d7 = 70.0, "延迟未探(中性)"
        else:
            lr = _clamp(float(latency), 0, 1)
            s7 = lr * 100
            d7 = f"P95 达标率 {lr:.0%}"
        f7 = _factor("latency_budget", "延迟预算",
                     s7, weights["latency_budget"], d7)

        # ⑧ 覆盖广度(意图域语料覆盖度)
        coverage = ctx.get("intentCoverage")
        if coverage is None:
            s8, d8 = 50.0, "覆盖未探(中性)"
        else:
            raw_cov = float(coverage)
            if not 0.0 <= raw_cov <= 1.0:
                raise ValueError(
                    "意图覆盖率须在 [0,1]")
            cov = _clamp(raw_cov, 0, 1)
            s8 = cov * 100
            d8 = f"意图域覆盖度 {cov:.0%}"
        f8 = _factor("coverage_breadth", "覆盖广度",
                     s8, weights["coverage_breadth"], d8)

        factors = [f1, f2, f3, f4, f5, f6, f7, f8]
        trust = round(sum(x["contribution"]
                          for x in factors), 1)

        # 三级决策(DECISION_THRESHOLDS 对齐)
        if trust >= 80.0:
            decision = DECISION_URGENT
        elif trust >= 50.0:
            decision = DECISION_OPTIMIZE
        else:
            decision = DECISION_OBSERVE

        return {
            "success": True,
            "scorer": SCORER_ID,
            "modelVersion": MODEL_VERSION,
            "trustScore": trust,
            "decision": decision,
            "decisionName": DECISION_NAMES[decision],
            "factors": factors,
            "note": "第33档案八因子——意图识别质量"
                    "信任分(44号学习闭环)",
        }
