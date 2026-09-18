"""织智·Synapse-Weave(76号)——织造质量评分器

(synapse_weave, batch 50 入册 ai_learning)

第50档案 synapse_weave 五因子(《织智创新方案》
SW-Eval-Benchmark——"织造质量"代理指标):

    | 因子 | 权重 | 口径 |
    |------|------|------|
    | validation_pass    | 0.30 | 交叉验证通过率
      (passed 织造/总织造) |
    | persona_tension    | 0.25 | 人格张力
      (PDS 均值——文档人格经线锚定) |
    | router_alignment   | 0.20 | 经纬路由贴合
      (routerAlignment 均值) |
    | fact_fidelity      | 0.15 | 事实保真
      (factFidelity 通过占比) |
    | hotspot_symbiosis  | 0.10 | 热点共生
      (热点重写占比×共生分均值)

→ 织造分 0-100 → 三级决策:
    observe(织机运转良好) /
    optimize(织补式进化触发) /
    urgent(人格校准战役——人格经线审视)

76号纯函数零落库(75号范式)。
"""

import logging
from typing import ClassVar

logger = logging.getLogger("synapse_scorer")

MODEL_VERSION = "v1-synapse-scorer"

SCORER_ID = "synapse_weave"

DECISION_OBSERVE = "observe"
DECISION_OPTIMIZE = "optimize"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_OBSERVE: "观察(织机运转留痕)",
    DECISION_OPTIMIZE: "优化执行(织补式进化触发)",
    DECISION_URGENT: ("紧急优化(人格校准战役——"
                      "人格经线审视)"),
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


class SynapseWeaveScorer:
    """织智织造质量评分(五因子加权)"""

    WEIGHTS: ClassVar[dict] = {
        "validation_pass": 0.30,
        "persona_tension": 0.25,
        "router_alignment": 0.20,
        "fact_fidelity": 0.15,
        "hotspot_symbiosis": 0.10,
    }

    REQUIRED: ClassVar[list] = ["totalWeaves"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                totalWeaves: int 总织造数,
                passedWeaves: int 交叉验证通过数,
                pdsAvg: float PDS 均值(0-1),
                alignmentAvg: float 路由贴合均值(0-1),
                factPassWeaves: int 事实保真通过数,
                hotspotRewrites: int 热点重写数,
                symbiosisAvg: float 热点共生均值(0-1)
            }
        """
        total = int(ctx.get("totalWeaves") or 0)
        passed = int(ctx.get("passedWeaves") or 0)
        pds_avg = float(ctx.get("pdsAvg") or 0)
        alignment_avg = float(
            ctx.get("alignmentAvg") or 0)
        fact_pass = int(ctx.get("factPassWeaves") or 0)
        hotspot_n = int(ctx.get("hotspotRewrites") or 0)
        symbiosis_avg = float(
            ctx.get("symbiosisAvg") or 0)

        f = {}
        # ① 交叉验证通过率
        validation = (_clamp(
            100.0 * passed / total, 0, 100)
            if total else 70.0)
        f["validation_pass"] = _factor(
            "validation_pass", "交叉验证通过率",
            validation,
            self.WEIGHTS["validation_pass"],
            f"通过 {passed}/{total}")
        # ② 人格张力(PDS 均值)
        persona = _clamp(pds_avg * 100, 0, 100)
        f["persona_tension"] = _factor(
            "persona_tension", "人格张力(PDS)",
            persona, self.WEIGHTS["persona_tension"],
            f"PDS 均值 {pds_avg}")
        # ③ 经纬路由贴合
        alignment = _clamp(alignment_avg * 100, 0, 100)
        f["router_alignment"] = _factor(
            "router_alignment", "经纬路由贴合",
            alignment,
            self.WEIGHTS["router_alignment"],
            f"贴合均值 {alignment_avg}")
        # ④ 事实保真
        fidelity = (_clamp(
            100.0 * fact_pass / total, 0, 100)
            if total else 70.0)
        f["fact_fidelity"] = _factor(
            "fact_fidelity", "事实保真",
            fidelity, self.WEIGHTS["fact_fidelity"],
            f"保真 {fact_pass}/{total}")
        # ⑤ 热点共生(重写占比×共生均值)
        symbiosis = (_clamp(
            100.0 * (hotspot_n / total if total else 0)
            * symbiosis_avg, 0, 100)
            if total and hotspot_n else 0.0)
        f["hotspot_symbiosis"] = _factor(
            "hotspot_symbiosis", "热点共生",
            symbiosis,
            self.WEIGHTS["hotspot_symbiosis"],
            f"重写 {hotspot_n}共生 {symbiosis_avg}")

        total_score = sum(
            x["contribution"] for x in f.values())
        if total_score >= 80:
            level, action = "high", DECISION_OBSERVE
        elif total_score >= 60:
            level, action = "medium", DECISION_OPTIMIZE
        else:
            level, action = "low", DECISION_URGENT

        result = {
            "success": True, "scorer": SCORER_ID,
            "modelVersion": MODEL_VERSION,
            "score": round(total_score, 1),
            "level": level,
            "levelName": {"high": "织机稳固",
                          "medium": "待织补",
                          "low": "织造危机"}[level],
            "action": action,
            "actionName": DECISION_NAMES[action],
            "factors": list(f.values()),
            "scoredAt": None,
        }
        from datetime import datetime, UTC
        result["scoredAt"] = datetime.now(
            UTC).isoformat()
        logger.info("synapse_weave_scored score=%s "
                    "action=%s", result["score"], action)
        return result
