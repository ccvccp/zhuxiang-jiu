"""61号·AI智能系统升级决策 第36档案八因子评分器
(dm61_scorer)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§四):
    第36档案 decision_orchestration(batch20):
        | 因子 | 权重 | 口径 |
        | decision_accuracy | 0.20 | 决策准确率(采纳后结果良好/总采纳) |
        | autonomous_ratio | 0.15 | 自治域占比(L1 直通/总量——过高需复核) |
        | simulation_hit_rate | 0.15 | 影响预测命中率(预估与实际偏差<阈值) |
        | dissent_effectiveness | 0.15 | 反对意见有效性(预警被证实/总预警) |
        | member_trust | 0.10 | 会员信值(47号 tier 基线) |
        | rollback_success | 0.10 | 回滚预案可靠性 |
        | latency_budget | 0.05 | 决策链路 P95 达标率 |
        | coverage_breadth | 0.10 | 决策场景覆盖度(标签域覆盖) |

    输出 0-100 信任分 → 三级决策:
        observe 观察(<50 建议域收窄)
        / optimize 优化执行(≥50 阈值与图谱回流)
        / urgent 紧急优化(≥80 决策复盘会)

54-63号同范式: 纯函数零落库。
"""

import logging

logger = logging.getLogger("dm61_scorer")

MODEL_VERSION = "v1-dm61-scorer"

SCORER_ID = "decision_orchestration"

# 三级决策(DECISION_THRESHOLDS 对齐)
DECISION_OBSERVE = "observe"
DECISION_OPTIMIZE = "optimize"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_OBSERVE: "观察(建议域收窄)",
    DECISION_OPTIMIZE: "优化执行(阈值与"
                       "图谱回流)",
    DECISION_URGENT: "紧急优化+决策复盘会"
                     "(决策复盘会)",
}

# 47号 tier 基线分
TIER_BASE = {
    "trusted": 90.0,
    "standard": 70.0,
    "watched": 50.0,
    "restricted": 30.0,
}

# 自治域占比健康区间(过高=决策权篡夺
# 风险——高于 0.3 线性降分; 0.3=100)
AUTONOMOUS_HEALTHY_MAX = 0.30


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


class Dm61Scorer:
    """61号八因子评分器(第36档案
    decision_orchestration)"""

    WEIGHTS = {
        "decision_accuracy": 0.20,
        "autonomous_ratio": 0.15,
        "simulation_hit_rate": 0.15,
        "dissent_effectiveness": 0.15,
        "member_trust": 0.10,
        "rollback_success": 0.10,
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

        # ① 决策准确率(采纳后结果良好/总采纳)
        acc = ctx.get("decisionAccuracy")
        if acc is None:
            s1, d1 = 50.0, "决策准确率未探(中性)"
        else:
            ar = _clamp(float(acc), 0, 1)
            s1 = ar * 100
            d1 = f"决策准确率 {ar:.0%}"
        f1 = _factor(
            "decision_accuracy", "决策准确率",
            s1, weights["decision_accuracy"], d1)

        # ② 自治域占比(L1 直通/总量——过高
        #    需复核: 决策权篡夺防线)
        auto_ratio = ctx.get("autonomousRatio")
        if auto_ratio is None:
            s2, d2 = 50.0, "自治占比未探(中性)"
        else:
            ao = _clamp(float(auto_ratio), 0, 1)
            if ao <= AUTONOMOUS_HEALTHY_MAX:
                # 健康区间内: 线性给分
                # (占比适度自治=能力体现)
                s2 = 60 + ao / AUTONOMOUS_HEALTHY_MAX \
                    * 40
            else:
                # 超健康线: 线性降分
                # (自治过高=人类兜底弱化)
                s2 = _clamp(
                    100 - (ao
                           - AUTONOMOUS_HEALTHY_MAX)
                    / (1 - AUTONOMOUS_HEALTHY_MAX)
                    * 60, 0, 100)
            d2 = f"自治域占比 {ao:.0%}" \
                 f"(健康线 {AUTONOMOUS_HEALTHY_MAX:.0%})"
        f2 = _factor(
            "autonomous_ratio", "自治占比",
            s2, weights["autonomous_ratio"], d2)

        # ③ 影响预测命中率(预估与实际偏差
        #    <阈值——影子沙箱有效性)
        hit = ctx.get("simulationHitRate")
        if hit is None:
            s3, d3 = 50.0, "预测命中未探(中性)"
        else:
            hr = _clamp(float(hit), 0, 1)
            s3 = hr * 100
            d3 = f"影响预测命中率 {hr:.0%}"
        f3 = _factor(
            "simulation_hit_rate", "预测命中",
            s3, weights[
                "simulation_hit_rate"], d3)

        # ④ 反对意见有效性(预警被证实/总预警
        #    ——AI 说"不"的质量)
        dissent = ctx.get("dissentEffectiveness")
        if dissent is None:
            s4, d4 = 50.0, "预警有效未探(中性)"
        else:
            de = _clamp(float(dissent), 0, 1)
            s4 = de * 100
            d4 = f"反对意见有效率 {de:.0%}"
        f4 = _factor(
            "dissent_effectiveness", "预警有效",
            s4, weights[
                "dissent_effectiveness"], d4)

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

        # ⑥ 回滚预案可靠性(可恢复性——
        #    5 分钟可恢复断言达标率)
        rollback = ctx.get("rollbackSuccessRate")
        if rollback is None:
            s6, d6 = 50.0, "回滚可靠未探(中性)"
        else:
            rs = _clamp(float(rollback), 0, 1)
            s6 = rs * 100
            d6 = f"回滚预案达标率 {rs:.0%}"
        f6 = _factor(
            "rollback_success", "回滚可靠",
            s6, weights["rollback_success"], d6)

        # ⑦ 决策链路 P95 达标率
        latency = ctx.get("latencyP95Ok")
        if latency is None:
            s7, d7 = 70.0, "时效未探(中性)"
        else:
            lr = _clamp(float(latency), 0, 1)
            s7 = lr * 100
            d7 = f"P95 达标率 {lr:.0%}"
        f7 = _factor(
            "latency_budget", "决策时效",
            s7, weights["latency_budget"], d7)

        # ⑧ 决策场景覆盖度(标签域覆盖)
        coverage = ctx.get("scenarioCoverage")
        if coverage is None:
            s8, d8 = 50.0, "覆盖未探(中性)"
        else:
            raw_cov = float(coverage)
            if not 0.0 <= raw_cov <= 1.0:
                raise ValueError(
                    "场景覆盖率须在 [0,1]")
            cov = _clamp(raw_cov, 0, 1)
            s8 = cov * 100
            d8 = f"标签域覆盖 {cov:.0%}"
        f8 = _factor(
            "coverage_breadth", "场景覆盖",
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
            "note": "第36档案 decision_"
                    "orchestration——八因子加权"
                    "(44号学习域可演进)",
        }
