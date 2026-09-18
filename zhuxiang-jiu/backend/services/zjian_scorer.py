"""竹鉴·BambooVerify(77号)——质检引证评分器

(bamboo_verify, batch 51 入册 ai_learning)

第51档案 bamboo_verify 五因子(质检引证域代理指标):

    | 因子 | 权重 | 口径 |
    |------|------|------|
    | citation_coverage | 0.30 | 引证覆盖
      (含报告引证技术应答/技术应答) |
    | metric_accuracy   | 0.30 | 指标准确
      (引证应答指标值与典藏一致占比) |
    | block_effective   | 0.20 | 拦截有效
      (医疗/夸大断言全拦) |
    | retrieval_hit     | 0.20 | 检索命中
      (命中指标问答/有效问答) |

→ 引证分 0-100 → 三级决策:
    observe(典藏运转良好) /
    optimize(指标域词表迭代) /
    urgent(引证纠偏——典藏核对)

77号纯函数零落库(75/76号范式)。
"""

import logging
from typing import ClassVar

logger = logging.getLogger("zjian_scorer")

MODEL_VERSION = "v1-zjian-scorer"

SCORER_ID = "bamboo_verify"

DECISION_OBSERVE = "observe"
DECISION_OPTIMIZE = "optimize"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_OBSERVE: "观察(典藏运转留痕)",
    DECISION_OPTIMIZE: "优化执行(指标域词表迭代)",
    DECISION_URGENT: "紧急优化(引证纠偏——典藏核对)",
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


class BambooVerifyScorer:
    """竹鉴质检引证评分(五因子加权)"""

    WEIGHTS: ClassVar[dict] = {
        "citation_coverage": 0.30,
        "metric_accuracy": 0.30,
        "block_effective": 0.20,
        "retrieval_hit": 0.20,
    }

    REQUIRED: ClassVar[list] = ["totalAsks"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                totalAsks: int 总问答数,
                technicalAsks: int 技术应答数(命中指标),
                citedAsks: int 含报告引证应答数,
                accurateAsks: int 指标值与典藏一致数,
                blockedAsks: int 拦截数(医疗/夸大),
                noHitAsks: int 未命中指标数
            }
        """
        total = int(ctx.get("totalAsks") or 0)
        technical = int(ctx.get("technicalAsks") or 0)
        cited = int(ctx.get("citedAsks") or 0)
        accurate = int(ctx.get("accurateAsks") or 0)
        blocked = int(ctx.get("blockedAsks") or 0)
        no_hit = int(ctx.get("noHitAsks") or 0)

        f = {}
        # ① 引证覆盖
        coverage = (_clamp(
            100.0 * cited / technical, 0, 100)
            if technical else 70.0)
        f["citation_coverage"] = _factor(
            "citation_coverage", "引证覆盖",
            coverage, self.WEIGHTS["citation_coverage"],
            f"引证 {cited}/{technical}")
        # ② 指标准确
        accuracy = (_clamp(
            100.0 * accurate / technical, 0, 100)
            if technical else 70.0)
        f["metric_accuracy"] = _factor(
            "metric_accuracy", "指标准确",
            accuracy, self.WEIGHTS["metric_accuracy"],
            f"准确 {accurate}/{technical}")
        # ③ 拦截有效(应拦截全拦=100; 无样本中性 70)
        block_eff = 70.0 if not blocked else 90.0
        f["block_effective"] = _factor(
            "block_effective", "拦截有效",
            block_eff, self.WEIGHTS["block_effective"],
            f"拦截 {blocked}(医疗/夸大)")
        # ④ 检索命中
        retrieval = (_clamp(
            100.0 * (total - no_hit) / total, 0, 100)
            if total else 70.0)
        f["retrieval_hit"] = _factor(
            "retrieval_hit", "检索命中",
            retrieval, self.WEIGHTS["retrieval_hit"],
            f"命中 {total - no_hit}/{total}")

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
            "levelName": {"high": "典藏稳固",
                          "medium": "待优化",
                          "low": "引证危机"}[level],
            "action": action,
            "actionName": DECISION_NAMES[action],
            "factors": list(f.values()),
            "scoredAt": None,
        }
        from datetime import datetime, UTC
        result["scoredAt"] = datetime.now(
            UTC).isoformat()
        logger.info("bamboo_verify_scored score=%s "
                    "action=%s", result["score"], action)
        return result
