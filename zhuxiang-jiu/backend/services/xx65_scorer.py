"""65号·网店及商品AI智能管理 评分器
(xx65_scorer, P0)

第39档案 shop_operation 八因子
(计划 §五——44号 ai_learning 入册
batch24, 计数更新 39→40):

    | 因子 | 权重 | 口径 |
    |------|------|------|
    | shop_health        | 0.20 | 店铺健康度
      (active 时长/违规次数反向) |
    | content_compliance | 0.20 | 内容合规率
      (一次过审/总生成) |
    | ai_adoption        | 0.15 | AI 采用率
      (AI 生成内容发布占比) |
    | campaign_roi      | 0.15 | 活动投产
      (GMV 增幅/信值消耗) |
    | member_trust       | 0.10 | 会员信值
      (47号 tier 基线) |
    | dispute_rate       | 0.10 | 争议率
      (投诉/订单——反向) |
    | growth_velocity    | 0.05 | 成长速率
      (等级跃迁周期) |
    | latency_budget     | 0.05 | 生成链路
      P95 达标率 |

→ 信任分 0-100 → 三级决策:
    observe(内容域收窄) /
    optimize(推荐策略回流) /
    urgent(店主经营复盘会)

54-64号同范式: 纯函数零落库。
"""

import logging
from typing import ClassVar

logger = logging.getLogger("xx65_scorer")

MODEL_VERSION = "v1-xx65-scorer"

SCORER_ID = "shop_operation"

# 三级决策(DECISION_THRESHOLDS 对齐)
DECISION_OBSERVE = "observe"
DECISION_OPTIMIZE = "optimize"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_OBSERVE:
        "观察(内容域收窄)",
    DECISION_OPTIMIZE:
        "优化执行(推荐策略回流)",
    DECISION_URGENT:
        "紧急优化(店主经营复盘会)",
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


class Xx65Scorer:
    """65号八因子评分器(第39档案
    shop_operation)"""

    WEIGHTS: ClassVar[dict] = {
        "shop_health": 0.20,
        "content_compliance": 0.20,
        "ai_adoption": 0.15,
        "campaign_roi": 0.15,
        "member_trust": 0.10,
        "dispute_rate": 0.10,
        "growth_velocity": 0.05,
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

        # ① 店铺健康度(active 时长
        #    /违规次数反向: 0 次=100;
        #    3 次=0)
        health = ctx.get("shopHealth")
        if health is None:
            s1, d1 = 50.0, "健康度未探(中性)"
        else:
            sh = _clamp(float(health),
                        0, 1)
            s1 = sh * 100
            d1 = f"店铺健康度 {sh:.0%}"
        f1 = _factor(
            "shop_health", "店铺健康",
            s1, weights["shop_health"], d1)

        # ② 内容合规率(一次过审
        #    /总生成)
        compliance = ctx.get(
            "contentCompliance")
        if compliance is None:
            s2, d2 = 50.0, "合规未探(中性)"
        else:
            cc = _clamp(float(compliance),
                        0, 1)
            s2 = cc * 100
            d2 = f"内容合规率 {cc:.0%}"
        f2 = _factor(
            "content_compliance", "内容合规",
            s2, weights[
                "content_compliance"], d2)

        # ③ AI 采用率(AI 生成内容
        #    发布占比)
        adoption = ctx.get("aiAdoption")
        if adoption is None:
            s3, d3 = 50.0, "采用未探(中性)"
        else:
            aa = _clamp(float(adoption),
                        0, 1)
            s3 = aa * 100
            d3 = f"AI 采用率 {aa:.0%}"
        f3 = _factor(
            "ai_adoption", "AI采用",
            s3, weights["ai_adoption"], d3)

        # ④ 活动投产(GMV 增幅/
        #    信值消耗: 1.0=80;
        #    偏离扣减)
        roi = ctx.get("campaignRoi")
        if roi is None:
            s4, d4 = 60.0, "投产未探(中性)"
        else:
            cr = _clamp(float(roi), 0, 5)
            deviation = abs(cr - 1.0)
            s4 = _clamp(
                80 - deviation * 30,
                0, 100)
            d4 = f"活动投产比 {cr:.2f}"
        f4 = _factor(
            "campaign_roi", "活动投产",
            s4, weights["campaign_roi"], d4)

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

        # ⑥ 争议率(投诉/订单
        #    ——反向: 0%=100;
        #    10%=0)
        dispute = ctx.get("disputeRate")
        if dispute is None:
            s6, d6 = 70.0, "争议未探(中性)"
        else:
            dr = _clamp(float(dispute),
                        0, 1)
            s6 = _clamp(
                100 - dr * 1000, 0, 100)
            d6 = f"争议率 {dr:.1%}"
        f6 = _factor(
            "dispute_rate", "争议率",
            s6, weights["dispute_rate"], d6)

        # ⑦ 成长速率(等级跃迁周期:
        #    快=加分; 未跃迁中性)
        growth = ctx.get("growthVelocity")
        if growth is None:
            s7, d7 = 60.0, "成长未探(中性)"
        else:
            gv = _clamp(float(growth),
                        0, 1)
            s7 = gv * 100
            d7 = f"成长速率 {gv:.0%}"
        f7 = _factor(
            "growth_velocity", "成长速率",
            s7, weights[
                "growth_velocity"], d7)

        # ⑧ 生成链路 P95 达标率
        latency = ctx.get("latencyP95Ok")
        if latency is None:
            s8, d8 = 70.0, "时效未探(中性)"
        else:
            lp = _clamp(float(latency),
                        0, 1)
            s8 = lp * 100
            d8 = f"P95 达标率 {lp:.0%}"
        f8 = _factor(
            "latency_budget", "生成时效",
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
            "note": "第39档案 shop_operation"
                    "——八因子加权(44号学习域"
                    "可演进)",
        }
