"""63号·AI智能后台管理 第38档案八因子评分器
(ab63_scorer)

计划(docs/63号_AI智能后台管理模块实施计划.md §四):
    第38档案 admin_orchestration(batch22):
        | 因子 | 权重 | 口径 |
        | guard_effectiveness | 0.20 | 护航有效性(合规前置率) |
        | auto_review_accuracy | 0.15 | 自动过审准确率 |
        | permission_fitness | 0.15 | 权限适配度 |
        | review_consistency | 0.15 | 审核一致性 |
        | member_trust | 0.10 | 会员信值(47号 tier) |
        | appeal_overturn | 0.10 | 申诉翻转率(反向) |
        | latency_budget | 0.05 | 审核时效 P95 |
        | coverage_breadth | 0.10 | 角色域覆盖度 |

    输出 0-100 信任分 → 三级决策:
        observe 观察(<50) / optimize 优化执行(≥50)
        / urgent 紧急优化+人工加急(≥80)

54-59号同范式: 纯函数零落库。
"""

import logging

logger = logging.getLogger("ab63_scorer")

MODEL_VERSION = "v1-ab63-scorer"

SCORER_ID = "admin_orchestration"

# 三级决策(DECISION_THRESHOLDS 对齐)
DECISION_OBSERVE = "observe"
DECISION_OPTIMIZE = "optimize"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_OBSERVE: "观察(护航域收窄)",
    DECISION_OPTIMIZE: "优化执行(规则与"
                       "阈值回流)",
    DECISION_URGENT: "紧急优化+人工加急"
                     "(后台复盘会)",
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


class Ab63Scorer:
    """63号八因子评分器(第38档案
    admin_orchestration)"""

    WEIGHTS = {
        "guard_effectiveness": 0.20,
        "auto_review_accuracy": 0.15,
        "permission_fitness": 0.15,
        "review_consistency": 0.15,
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

        # ① 护航有效性(合规前置率——
        #    编辑态拦截/总问题)
        guard = ctx.get("guardEffectiveness")
        if guard is None:
            s1, d1 = 50.0, "护航未探(中性)"
        else:
            gr = _clamp(float(guard), 0, 1)
            s1 = gr * 100
            d1 = f"合规前置率 {gr:.0%}"
        f1 = _factor(
            "guard_effectiveness", "护航有效性",
            s1, weights["guard_effectiveness"],
            d1)

        # ② 自动过审准确率(L1 无投诉下架)
        auto_acc = ctx.get("autoReviewAccuracy")
        if auto_acc is None:
            s2, d2 = 50.0, "自动过审未探(中性)"
        else:
            ar = _clamp(float(auto_acc), 0, 1)
            s2 = ar * 100
            d2 = f"L1 准确率 {ar:.0%}"
        f2 = _factor(
            "auto_review_accuracy",
            "自动过审准确",
            s2, weights[
                "auto_review_accuracy"], d2)

        # ③ 权限适配度(动态授权命中率)
        perm = ctx.get("permissionFitness")
        if perm is None:
            s3, d3 = 50.0, "权限适配未探(中性)"
        else:
            pr = _clamp(float(perm), 0, 1)
            s3 = pr * 100
            d3 = f"动态授权命中率 {pr:.0%}"
        f3 = _factor(
            "permission_fitness", "权限适配",
            s3, weights["permission_fitness"],
            d3)

        # ④ 审核一致性(AI 预审与人工
        #    偏差率——反向)
        consist = ctx.get("reviewConsistency")
        if consist is None:
            s4, d4 = 50.0, "一致性未探(中性)"
        else:
            rc = _clamp(float(consist), 0, 1)
            s4 = rc * 100
            d4 = f"AI 预审采纳一致率 {rc:.0%}"
        f4 = _factor(
            "review_consistency", "审核一致性",
            s4, weights[
                "review_consistency"], d4)

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

        # ⑦ 审核时效 P95 达标率
        latency = ctx.get("latencyP95Ok")
        if latency is None:
            s7, d7 = 70.0, "时效未探(中性)"
        else:
            lr = _clamp(float(latency), 0, 1)
            s7 = lr * 100
            d7 = f"P95 达标率 {lr:.0%}"
        f7 = _factor(
            "latency_budget", "审核时效",
            s7, weights["latency_budget"], d7)

        # ⑧ 角色域覆盖度(模板覆盖)
        coverage = ctx.get("roleCoverage")
        if coverage is None:
            s8, d8 = 50.0, "覆盖未探(中性)"
        else:
            raw_cov = float(coverage)
            if not 0.0 <= raw_cov <= 1.0:
                raise ValueError(
                    "角色覆盖率须在 [0,1]")
            cov = _clamp(raw_cov, 0, 1)
            s8 = cov * 100
            d8 = f"角色域覆盖 {cov:.0%}"
        f8 = _factor(
            "coverage_breadth", "角色覆盖",
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
            "note": "第38档案 admin_orchestration"
                    "——八因子加权(44号学习域"
                    "可演进)",
        }
