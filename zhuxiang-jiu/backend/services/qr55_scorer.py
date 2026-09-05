"""55号·二维码AI智能管理 评分器(qr55_scorer)

计划(docs/55号_二维码AI智能管理模块实施计划.md §五):
    第30档案 qr_orchestration 八因子信任分模型:

    intent_confidence   0.15  意图解析置信度(规则轨)
    service_match       0.15  服务匹配度(精确/模糊)
    template_fit        0.10  模板适配度(参数完整率)
    budget_sufficiency  0.15  隐私预算余量(49号)
    member_trust        0.15  会员信值等级(45号)
    expiry_freshness    0.10  有效期新鲜度
    accessibility_need  0.10  无障碍需求命中
    risk_posture        0.10  风险态势(46号冻结/风控)

输出 0-100 信任分 → 三级生码策略:
    direct(≥70) 直接生成 / confirm(≥40) 参数确认 /
    clarify(<40) 澄清对话

设计约定(54号 Login54Scorer 同范式):
    - 纯函数式(输入 dict → 输出 dict), 零落库
    - 权重经 load_effective_weights 读取 champion
      (44号自学习闭环——异常回退默认)
    - 置信度=输入字段完整度(下限 0.3)
"""

import logging
from typing import ClassVar

from core.helpers import ts

from services.ai_learning_service import (
    get_active_weight_version, load_effective_weights,
)
from services.ai_scoring_service import (
    _clamp, _confidence, _factor,
)

logger = logging.getLogger("qr55_scorer")

MODEL_VERSION = "v1-qr55-scorer"

# 三级生码策略(计划 §五)
STRATEGY_DIRECT = "direct"
STRATEGY_CONFIRM = "confirm"
STRATEGY_CLARIFY = "clarify"

STRATEGY_NAMES = {
    STRATEGY_DIRECT: "直接生成(高信任)",
    STRATEGY_CONFIRM: "参数确认(中信任)",
    STRATEGY_CLARIFY: "澄清对话(低信任)",
}

# 会员信值等级 → 信任基线(45号口径映射)
TRUST_LEVEL_BASE = {
    "L3": 0.95, "L2": 0.80, "L1": 0.60,
    "L0": 0.40, None: 0.50,
}


class Qr55Scorer:
    """二维码生码编排评分(55号·八因子→三级策略)

    输入 ctx 字段(全部可选——缺失走中性口径):
        intentConfidence: float 意图置信度 [0,1]
        serviceMatch: str 命中态 resolved/partial/clarify
        paramComplete: float 参数完整率 [0,1]
        budgetRemaining: float 隐私预算余量 [0,1]
        memberTrustLevel: str 信值等级 L0-L3
        freshRatio: float 有效期余量比 [0,1]
        accessibility: bool 无障碍需求
        riskFlagged: bool 风控标记
    """

    WEIGHTS: ClassVar[dict] = {
        "intent_confidence": 0.15,
        "service_match": 0.15,
        "template_fit": 0.10,
        "budget_sufficiency": 0.15,
        "member_trust": 0.15,
        "expiry_freshness": 0.10,
        "accessibility_need": 0.10,
        "risk_posture": 0.10,
    }

    REQUIRED: ClassVar[list] = [
        "intentConfidence", "serviceMatch"]

    async def score(self, ctx: dict) -> dict:
        """评分入口: 八因子加权 → 信任分(0-100) →
        三级生码策略

        Raises:
            ValueError: 输入非法(置信度/完整率越界)
        """
        if not ctx:
            raise ValueError("评分上下文不能为空")

        weights = await load_effective_weights(
            "qr_orchestration", self.WEIGHTS)

        # ① 意图置信度(规则轨)
        ic = ctx.get("intentConfidence")
        if ic is not None:
            ic = float(ic)
            if not 0.0 <= ic <= 1.0:
                raise ValueError("意图置信度须在 [0,1]")
            s1 = ic * 100
            d1 = f"意图置信 {ic:.2f}"
        else:
            s1, d1 = 50.0, "无意图信号(中性)"
        f1 = _factor("intent_confidence", "意图置信",
                     s1, weights["intent_confidence"], d1)

        # ② 服务匹配度(三态)
        match = str(ctx.get("serviceMatch") or "")
        if match == "resolved":
            s2, d2 = 100.0, "精确命中"
        elif match == "partial":
            s2, d2 = 55.0, "多候选歧义"
        else:
            s2, d2 = 20.0, "需澄清(未命中)"
        f2 = _factor("service_match", "服务匹配",
                     s2, weights["service_match"], d2)

        # ③ 模板适配(参数完整率)
        pc = ctx.get("paramComplete")
        if pc is not None:
            pc = float(pc)
            if not 0.0 <= pc <= 1.0:
                raise ValueError("参数完整率须在 [0,1]")
            s3 = pc * 100
            d3 = f"参数完整率 {pc:.0%}"
        else:
            s3, d3 = 70.0, "参数未探(中性)"
        f3 = _factor("template_fit", "模板适配",
                     s3, weights["template_fit"], d3)

        # ④ 预算余量
        budget = ctx.get("budgetRemaining")
        if budget is None:
            s4, d4 = 70.0, "预算未探(中性)"
        else:
            budget = max(0.0, min(1.0, float(budget)))
            s4 = 40.0 + budget * 60.0
            d4 = f"预算余量 {budget:.0%}"
        f4 = _factor("budget_sufficiency", "预算余量",
                     s4, weights["budget_sufficiency"], d4)

        # ⑤ 会员信值等级
        level = ctx.get("memberTrustLevel")
        base = TRUST_LEVEL_BASE.get(level)
        if base is None:
            s5, d5 = 50.0, f"等级未知({level or '无'})"
        else:
            s5 = base * 100
            d5 = f"信值等级 {level}"
        f5 = _factor("member_trust", "会员信值",
                     s5, weights["member_trust"], d5)

        # ⑥ 有效期新鲜度
        fresh = ctx.get("freshRatio")
        if fresh is None:
            s6, d6 = 70.0, "新鲜度未探(中性)"
        else:
            fresh = max(0.0, min(1.0, float(fresh)))
            s6 = fresh * 100
            d6 = f"有效期余量 {fresh:.0%}"
        f6 = _factor("expiry_freshness", "新鲜度",
                     s6, weights["expiry_freshness"], d6)

        # ⑦ 无障碍需求命中(True=已适配加分)
        if ctx.get("accessibility") is None:
            s7, d7 = 50.0, "无障碍未探(中性)"
        elif ctx.get("accessibility"):
            s7, d7 = 90.0, "无障碍已适配"
        else:
            s7, d7 = 50.0, "无无障碍需求"
        f7 = _factor("accessibility_need", "无障碍",
                     s7, weights["accessibility_need"], d7)

        # ⑧ 风险态势(False=安全)
        if ctx.get("riskFlagged") is None:
            s8, d8 = 70.0, "风险未探(中性)"
        elif ctx.get("riskFlagged"):
            s8, d8 = 10.0, "风控标记(高危)"
        else:
            s8, d8 = 90.0, "无风险标记"
        f8 = _factor("risk_posture", "风险态势",
                     s8, weights["risk_posture"], d8)

        factors = [f1, f2, f3, f4, f5, f6, f7, f8]
        trust = round(sum(x["contribution"]
                          for x in factors), 1)

        # 三级生码策略(DECISION_THRESHOLDS 对齐)
        if trust >= 70.0:
            strategy = STRATEGY_DIRECT
        elif trust >= 40.0:
            strategy = STRATEGY_CONFIRM
        else:
            strategy = STRATEGY_CLARIFY

        return {
            "success": True,
            "scorer": "qr_orchestration",
            "modelVersion": MODEL_VERSION,
            "weightVersion": get_active_weight_version(
                "qr_orchestration"),
            "trustScore": trust,
            "strategy": strategy,
            "strategyName": STRATEGY_NAMES[strategy],
            "factors": factors,
            "confidence": _confidence(ctx, self.REQUIRED),
            "scoredAt": ts(),
        }
