"""56号·AI智能升级管理 评分器(aiup56_scorer)

计划(docs/56号_AI智能升级管理模块实施计划.md §六):
    第31档案 upgrade_orchestration 八因子信任分模型:

    signal_quality      0.15  信号质量(命中数×四侧覆盖度)
    necessity_score     0.20  升级必要性(信号融合分)
    budget_sufficiency  0.15  隐私预算余量(49号)
    risk_posture        0.15  风险态势(46号冻结/告警密度)
    model_health        0.10  模型健康(44号池对齐+46号健康分)
    history_success     0.10  历史升级成功率(回流真值)
    compliance_posture  0.10  合规态势(46号审计信号)
    human_load          0.05  人工负载(近期干预率)

    输出 0-100 信任分 → 三级决策:
        escalate(≥80) 紧急提案+双人复核
        propose(≥50)  提案
        defer(<50)    观察留痕

设计约定(54/55号同范式):
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

logger = logging.getLogger("aiup56_scorer")

MODEL_VERSION = "v1-aiup56-scorer"

# 三级决策(计划 §六)
DECISION_ESCALATE = "escalate"
DECISION_PROPOSE = "propose"
DECISION_DEFER = "defer"

DECISION_NAMES = {
    DECISION_ESCALATE: "紧急提案(双人复核)",
    DECISION_PROPOSE: "提案(常规审批)",
    DECISION_DEFER: "观察(留痕不提案)",
}


class Aiup56Scorer:
    """智能升级编排评分(56号·八因子→三级决策)

    输入 ctx 字段(全部可选——缺失走中性口径):
        signalHits: int 命中信号数
        sideCoverage: float 四侧覆盖度 [0,1]
        necessityScore: float 信号融合必要性 [0,100]
        budgetRemaining: float 隐私预算余量 [0,1]
        riskFlagged: bool 46号冻结/高危告警
        alertDensity: float 合规告警密度 [0,1]
        poolAlignment: float 44号池对齐度 [0,1]
        govHealthScore: float 46号健康分 [0,100]
        historySuccessRate: float 历史成功率 [0,1]
        humanInterventionRate: float 干预率 [0,1]
    """

    WEIGHTS: ClassVar[dict] = {
        "signal_quality": 0.15,
        "necessity_score": 0.20,
        "budget_sufficiency": 0.15,
        "risk_posture": 0.15,
        "model_health": 0.10,
        "history_success": 0.10,
        "compliance_posture": 0.10,
        "human_load": 0.05,
    }

    REQUIRED: ClassVar[list] = ["necessityScore"]

    async def score(self, ctx: dict) -> dict:
        """评分入口: 八因子加权 → 信任分(0-100) →
        三级决策

        Raises:
            ValueError: 输入非法(必要性/覆盖率越界)
        """
        if not ctx:
            raise ValueError("评分上下文不能为空")

        weights = await load_effective_weights(
            "upgrade_orchestration", self.WEIGHTS)

        # ① 信号质量(命中数×四侧覆盖度——
        # 命中 0 视作低分 30, 单侧覆盖打折)
        hits = ctx.get("signalHits")
        coverage = ctx.get("sideCoverage")
        if coverage is not None:
            coverage = float(coverage)
            if not 0.0 <= coverage <= 1.0:
                raise ValueError(
                    "四侧覆盖度须在 [0,1]")
        if hits is not None:
            hits = int(hits)
            if coverage is not None:
                s1 = _clamp(
                    30.0 + hits * 8.0
                    + coverage * 30.0, 0, 100)
                d1 = (f"命中 {hits} 信号"
                      f"×覆盖 {coverage:.0%}")
            else:
                s1 = _clamp(30.0 + hits * 8.0, 0, 100)
                d1 = f"命中 {hits} 信号(覆盖未探)"
        else:
            s1, d1 = 30.0, "无信号(低分)"
        f1 = _factor("signal_quality", "信号质量",
                     s1, weights["signal_quality"], d1)

        # ② 升级必要性(信号融合分)
        necessity = ctx.get("necessityScore")
        if necessity is not None:
            necessity = float(necessity)
            if not 0.0 <= necessity <= 100.0:
                raise ValueError(
                    "必要性评分须在 [0,100]")
            s2 = necessity
            d2 = f"融合必要性 {necessity:.1f}"
        else:
            s2, d2 = 50.0, "必要性未探(中性)"
        f2 = _factor("necessity_score", "必要性",
                     s2, weights["necessity_score"], d2)

        # ③ 预算余量(49号)
        budget = ctx.get("budgetRemaining")
        if budget is None:
            s3, d3 = 70.0, "预算未探(中性)"
        else:
            budget = max(0.0, min(1.0, float(budget)))
            s3 = 40.0 + budget * 60.0
            d3 = f"预算余量 {budget:.0%}"
        f3 = _factor("budget_sufficiency", "预算余量",
                     s3, weights["budget_sufficiency"], d3)

        # ④ 风险态势(46号冻结/告警密度——
        # 冻结/高危=压低, 平静=抬高)
        alert_density = ctx.get("alertDensity")
        if ctx.get("riskFlagged") is None \
                and alert_density is None:
            s4, d4 = 70.0, "风险未探(中性)"
        elif ctx.get("riskFlagged"):
            s4, d4 = 15.0, "治理冻结/高危(升级宜缓)"
        else:
            dens = max(0.0, min(1.0, float(
                alert_density or 0)))
            s4 = _clamp(95.0 - dens * 80.0, 0, 100)
            d4 = f"告警密度 {dens:.0%}"
        f4 = _factor("risk_posture", "风险态势",
                     s4, weights["risk_posture"], d4)

        # ⑤ 模型健康(44号池对齐+46号健康分均值)
        pool = ctx.get("poolAlignment")
        gov = ctx.get("govHealthScore")
        parts = []
        if pool is not None:
            parts.append(max(0.0, min(1.0,
                                     float(pool))) * 100)
        if gov is not None:
            parts.append(max(0.0, min(100.0,
                                      float(gov))))
        if parts:
            s5 = round(sum(parts) / len(parts), 1)
            d5 = f"池对齐/健康分均值 {s5}"
        else:
            s5, d5 = 50.0, "模型健康未探(中性)"
        f5 = _factor("model_health", "模型健康",
                     s5, weights["model_health"], d5)

        # ⑥ 历史成功率(回流真值——无历史中性)
        hist = ctx.get("historySuccessRate")
        if hist is None:
            s6, d6 = 50.0, "无历史升级(中性)"
        else:
            hist = max(0.0, min(1.0, float(hist)))
            s6 = hist * 100
            d6 = f"历史成功率 {hist:.0%}"
        f6 = _factor("history_success", "历史成功",
                     s6, weights["history_success"], d6)

        # ⑦ 合规态势(告警密度倒置)
        if alert_density is None:
            s7, d7 = 70.0, "合规未探(中性)"
        else:
            dens = max(0.0, min(1.0, float(
                alert_density)))
            s7 = _clamp(95.0 - dens * 70.0, 0, 100)
            d7 = f"合规告警密度 {dens:.0%}"
        f7 = _factor("compliance_posture", "合规态势",
                     s7, weights["compliance_posture"], d7)

        # ⑧ 人工负载(干预率倒置——低负载利于自主)
        load = ctx.get("humanInterventionRate")
        if load is None:
            s8, d8 = 50.0, "干预率未探(中性)"
        else:
            load = max(0.0, min(1.0, float(load)))
            s8 = (1.0 - load) * 100
            d8 = f"近期干预率 {load:.0%}"
        f8 = _factor("human_load", "人工负载",
                     s8, weights["human_load"], d8)

        factors = [f1, f2, f3, f4, f5, f6, f7, f8]
        trust = round(sum(x["contribution"]
                          for x in factors), 1)

        # 三级决策(DECISION_THRESHOLDS 对齐)
        if trust >= 80.0:
            decision = DECISION_ESCALATE
        elif trust >= 50.0:
            decision = DECISION_PROPOSE
        else:
            decision = DECISION_DEFER

        return {
            "success": True,
            "scorer": "upgrade_orchestration",
            "modelVersion": MODEL_VERSION,
            "weightVersion": get_active_weight_version(
                "upgrade_orchestration"),
            "trustScore": trust,
            "decision": decision,
            "decisionName": DECISION_NAMES[decision],
            "factors": factors,
            "confidence": _confidence(ctx, self.REQUIRED),
            "scoredAt": ts(),
        }
