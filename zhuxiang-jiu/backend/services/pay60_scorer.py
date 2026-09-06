"""60号·AI智能支付管理 第35档案八因子评分器
(pay60_scorer)

计划(docs/60号_AI智能支付管理模块实施计划.md §四):
    第35档案 payment_orchestration(batch19):
        | 因子 | 权重 | 口径 |
        | payment_success_rate | 0.20 | 支付成功率(成功/发起) |
        | verification_friction | 0.15 | 验证摩擦(直通率——高直通=体验好) |
        | recon_accuracy | 0.15 | 对账准确率(自动匹配/总量) |
        | fraud_interception | 0.15 | 欺诈拦截率(确认欺诈/拦截总数) |
        | member_trust | 0.10 | 会员信值(47号 tier 基线) |
        | dispute_rate | 0.10 | 争议率(反向因子) |
        | latency_budget | 0.05 | 支付链路 P95 达标率 |
        | coverage_breadth | 0.10 | 支付场景覆盖度(意图→支付映射) |

    输出 0-100 信任分 → 三级决策:
        observe 观察(<50) / optimize 优化执行(≥50)
        / urgent 紧急优化+支付复盘会(≥80)

54-59/63号同范式: 纯函数零落库。
"""

import logging

logger = logging.getLogger("pay60_scorer")

MODEL_VERSION = "v1-pay60-scorer"

SCORER_ID = "payment_orchestration"

# 三级决策(DECISION_THRESHOLDS 对齐)
DECISION_OBSERVE = "observe"
DECISION_OPTIMIZE = "optimize"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_OBSERVE: "观察(支付域收窄)",
    DECISION_OPTIMIZE: "优化执行(规则与"
                       "阈值回流)",
    DECISION_URGENT: "紧急优化+支付复盘会",
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


class Pay60Scorer:
    """60号八因子评分器(第35档案
    payment_orchestration)"""

    WEIGHTS = {
        "payment_success_rate": 0.20,
        "verification_friction": 0.15,
        "recon_accuracy": 0.15,
        "fraud_interception": 0.15,
        "member_trust": 0.10,
        "dispute_rate": 0.10,
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

        # ① 支付成功率(成功/发起)
        psr = ctx.get("paymentSuccessRate")
        if psr is None:
            s1, d1 = 70.0, "成功率未探(中性)"
        else:
            pr = _clamp(float(psr), 0, 1)
            s1 = pr * 100
            d1 = f"支付成功率 {pr:.0%}"
        f1 = _factor(
            "payment_success_rate",
            "支付成功率", s1,
            weights["payment_success_rate"], d1)

        # ② 验证摩擦(直通率——
        #    高直通=体验好)
        vf = ctx.get("verificationFriction")
        if vf is None:
            s2, d2 = 70.0, "直通率未探(中性)"
        else:
            vr = _clamp(float(vf), 0, 1)
            s2 = vr * 100
            d2 = f"直通率 {vr:.0%}"
        f2 = _factor(
            "verification_friction",
            "验证摩擦(直通率)", s2,
            weights["verification_friction"], d2)

        # ③ 对账准确率(自动匹配/总量)
        ra = ctx.get("reconAccuracy")
        if ra is None:
            s3, d3 = 70.0, "对账未探(中性)"
        else:
            rr = _clamp(float(ra), 0, 1)
            s3 = rr * 100
            d3 = f"对账准确率 {rr:.0%}"
        f3 = _factor(
            "recon_accuracy", "对账准确率",
            s3, weights["recon_accuracy"], d3)

        # ④ 欺诈拦截率(确认欺诈/拦截总数)
        fi = ctx.get("fraudInterception")
        if fi is None:
            s4, d4 = 70.0, "欺诈拦截未探(中性)"
        else:
            fr = _clamp(float(fi), 0, 1)
            s4 = fr * 100
            d4 = f"欺诈拦截率 {fr:.0%}"
        f4 = _factor(
            "fraud_interception",
            "欺诈拦截率", s4,
            weights["fraud_interception"], d4)

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

        # ⑥ 争议率(反向——10% 争议=0 分
        #    线性映射)
        dr = ctx.get("disputeRate")
        if dr is None:
            s6, d6 = 70.0, "争议未探(中性)"
        else:
            dv = _clamp(float(dr), 0, 1)
            # 反向: 0 争议=100; 10%=0
            s6 = _clamp(
                100 - dv * 1000, 0, 100)
            d6 = f"争议率 {dv:.0%}"
        f6 = _factor(
            "dispute_rate", "争议率(反向)",
            s6, weights["dispute_rate"], d6)

        # ⑦ 支付链路 P95 达标率
        lb = ctx.get("latencyP95Ok")
        if lb is None:
            s7, d7 = 70.0, "时效未探(中性)"
        else:
            lr = _clamp(float(lb), 0, 1)
            s7 = lr * 100
            d7 = f"P95 达标率 {lr:.0%}"
        f7 = _factor(
            "latency_budget", "支付时效",
            s7, weights["latency_budget"], d7)

        # ⑧ 支付场景覆盖度
        #    (意图→支付映射)
        cb = ctx.get("coverageBreadth")
        if cb is None:
            s8, d8 = 70.0, "覆盖未探(中性)"
        else:
            cr = _clamp(float(cb), 0, 1)
            s8 = cr * 100
            d8 = f"场景覆盖 {cr:.0%}"
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
            "factors": factors,
            "trustScore": trust_score,
            "decision": decision,
            "decisionName":
                DECISION_NAMES[decision],
            "note": "第35档案 payment_"
                    "orchestration 八因子——"
                    "信任飞轮中枢",
        }
