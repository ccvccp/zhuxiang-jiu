"""57号·AI智能知识库 第32档案八因子评分器
(kb57_scorer)

计划(docs/57号_AI智能知识库模块实施计划.md §六):
    第32档案 knowledge_orchestration(batch16):
        | 因子 | 权重 | 口径 |
        | signal_quality | 0.15 | 缺口信号质量(命中×五侧覆盖度) |
        | necessity_score | 0.20 | 知识必要性(信号融合分) |
        | budget_sufficiency | 0.15 | 隐私预算余量(49号) |
        | risk_posture | 0.15 | 风险态势(46号冻结/告警密度) |
        | source_health | 0.10 | 来源健康(采集源可信度均值) |
        | history_success | 0.10 | 历史种子有效率(回流真值) |
        | compliance_posture | 0.10 | 合规态势(鉴别通过率) |
        | human_load | 0.05 | 人工负载(复审队列深度) |

    输出 0-100 信任分 → 三级决策:
        defer 观察(<50) / collect 定向采集(≥50)
        / urgent 紧急采集+人工加急复审(≥80)

54/55/56号同范式: 纯函数零落库(除44号权重
load_effective_weights——学习域层内相对权重)。
"""

import logging

logger = logging.getLogger("kb57_scorer")

MODEL_VERSION = "v1-kb57-scorer"

SCORER_ID = "knowledge_orchestration"

# 三级决策(DECISION_THRESHOLDS 对齐——44号池回放口径)
DECISION_DEFER = "defer"
DECISION_COLLECT = "collect"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_DEFER: "观察(留痕不采集)",
    DECISION_COLLECT: "定向采集(源白名单)",
    DECISION_URGENT: "紧急采集+人工加急复审",
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


class Kb57Scorer:
    """57号八因子评分器(第32档案 knowledge_orchestration)"""

    # 八因子默认权重(44号学习域可演进——护栏 [0.5,2.0] 倍)
    WEIGHTS = {
        "signal_quality": 0.15,
        "necessity_score": 0.20,
        "budget_sufficiency": 0.15,
        "risk_posture": 0.15,
        "source_health": 0.10,
        "history_success": 0.10,
        "compliance_posture": 0.10,
        "human_load": 0.05,
    }

    async def score(self, ctx: dict) -> dict:
        """评分入口: 八因子加权 → 信任分(0-100) →
        三级决策

        Raises:
            ValueError: 输入非法(必要性/覆盖率越界)
        """
        if not ctx:
            raise ValueError("评分上下文不能为空")

        from services.ai_learning_service import (
            load_effective_weights,
        )
        weights = await load_effective_weights(
            SCORER_ID, self.WEIGHTS)

        # ① 信号质量(命中数×五侧覆盖度——
        # 命中 0 视作低分 30, 单侧覆盖打折)
        hits = ctx.get("signalHits")
        coverage = ctx.get("sideCoverage")
        if coverage is not None:
            coverage = float(coverage)
            if not 0.0 <= coverage <= 1.0:
                raise ValueError("五侧覆盖度须在 [0,1]")
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

        # ② 知识必要性(信号融合分)
        necessity = ctx.get("necessityScore")
        if necessity is not None:
            necessity = float(necessity)
            if not 0.0 <= necessity <= 100.0:
                raise ValueError("必要性评分须在 [0,100]")
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
            s4, d4 = 15.0, "治理冻结/高危(采集宜缓)"
        else:
            dens = max(0.0, min(1.0, float(
                alert_density or 0)))
            s4 = _clamp(95.0 - dens * 80.0, 0, 100)
            d4 = f"告警密度 {dens:.0%}"
        f4 = _factor("risk_posture", "风险态势",
                     s4, weights["risk_posture"], d4)

        # ⑤ 来源健康(近期采集源可信度均值)
        source_health = ctx.get("sourceHealth")
        if source_health is None:
            s5, d5 = 70.0, "来源健康未探(中性)"
        else:
            sh = max(0.0, min(1.0, float(source_health)))
            s5 = sh * 100
            d5 = f"近期源可信度均值 {sh:.2f}"
        f5 = _factor("source_health", "来源健康",
                     s5, weights["source_health"], d5)

        # ⑥ 历史种子有效率(回流真值——无历史中性)
        hist = ctx.get("historySuccessRate")
        if hist is None:
            s6, d6 = 50.0, "无历史种子(中性)"
        else:
            hist = max(0.0, min(1.0, float(hist)))
            s6 = hist * 100
            d6 = f"历史种子有效率 {hist:.0%}"
        f6 = _factor("history_success", "历史有效",
                     s6, weights["history_success"], d6)

        # ⑦ 合规态势(鉴别通过率)
        pass_rate = ctx.get("compliancePassRate")
        if pass_rate is None:
            s7, d7 = 70.0, "合规态势未探(中性)"
        else:
            pr = max(0.0, min(1.0, float(pass_rate)))
            s7 = pr * 100
            d7 = f"鉴别通过率 {pr:.0%}"
        f7 = _factor("compliance_posture", "合规态势",
                     s7, weights["compliance_posture"], d7)

        # ⑧ 人工负载(复审队列深度倒置——
        # 低负载利于自主采集)
        load = ctx.get("humanReviewQueue")
        if load is None:
            s8, d8 = 50.0, "复审队列未探(中性)"
        else:
            load = _clamp(float(load), 0.0, 100.0)
            s8 = _clamp(100.0 - load, 0, 100)
            d8 = f"复审队列深度 {load:.0f}"
        f8 = _factor("human_load", "人工负载",
                     s8, weights["human_load"], d8)

        factors = [f1, f2, f3, f4, f5, f6, f7, f8]
        trust = round(sum(x["contribution"]
                          for x in factors), 1)

        # 三级决策(DECISION_THRESHOLDS 对齐)
        if trust >= 80.0:
            decision = DECISION_URGENT
        elif trust >= 50.0:
            decision = DECISION_COLLECT
        else:
            decision = DECISION_DEFER

        return {
            "success": True,
            "scorer": SCORER_ID,
            "modelVersion": MODEL_VERSION,
            "trustScore": trust,
            "decision": decision,
            "decisionName": DECISION_NAMES[decision],
            "factors": factors,
            "note": "第32档案八因子——知识缺口诊断"
                    "信任分(44号学习闭环)",
        }
