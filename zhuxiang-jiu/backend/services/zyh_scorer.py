"""竹韵·智衡·竹奕酒智能大模型(75号)——认知护城河评分器

(zhuyun_cognition, batch 49 入册 ai_learning)

第49档案 zhuyun_cognition 五因子(SDD V3.0 DTDAE 范式——
"工艺认知护城河"代理指标):

    | 因子 | 权重 | 口径 |
    |------|------|------|
    | craft_accuracy    | 0.30 | 工艺表述准确率
      (守门放行/总请求——L1 拦截反向) |
    | guard_coverage    | 0.25 | 守门拦截覆盖
      (L1+L2 命中/应拦截样本) |
    | citation_integrity| 0.20 | 溯源完整率
      (含引用技术断言/技术断言总量) |
    | cache_efficiency  | 0.15 | 语义缓存效能
      (命中率——成本韧性 SDD §2) |
    | retrieval_hit     | 0.10 | 知识检索命中
      (命中条目问答/总问答) |

→ 认知分 0-100 → 三级决策:
    observe(认知护城河观察) /
    optimize(话术与知识库迭代) /
    urgent(认知纠偏战役——探针辩题加投)

75号纯函数零落库(65号范式)。
"""

import logging
from typing import ClassVar

logger = logging.getLogger("zyh_scorer")

MODEL_VERSION = "v1-zyh-scorer"

SCORER_ID = "zhuyun_cognition"

DECISION_OBSERVE = "observe"
DECISION_OPTIMIZE = "optimize"
DECISION_URGENT = "urgent"

DECISION_NAMES = {
    DECISION_OBSERVE: "观察(认知护城河留痕)",
    DECISION_OPTIMIZE: "优化执行(话术与知识库迭代)",
    DECISION_URGENT: "紧急优化(认知纠偏战役——探针辩题加投)",
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


class ZhuyunCognitionScorer:
    """竹韵·智衡认知护城河评分(五因子加权)"""

    WEIGHTS: ClassVar[dict] = {
        "craft_accuracy": 0.30,
        "guard_coverage": 0.25,
        "citation_integrity": 0.20,
        "cache_efficiency": 0.15,
        "retrieval_hit": 0.10,
    }

    REQUIRED: ClassVar[list] = ["totalRequests"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                totalRequests: int 总问答请求,
                guardHit: int L1+L2 拦截数,
                technicalAnswers: int 技术断言应答数,
                citedAnswers: int 含引用应答数,
                cacheHit: int 缓存命中数,
                cacheTotal: int 缓存请求总数(hit+miss),
                retrievalHit: int 知识命中问答数
            }
        """
        total = int(ctx.get("totalRequests") or 0)
        guard_hit = int(ctx.get("guardHit") or 0)
        technical = int(ctx.get("technicalAnswers") or 0)
        cited = int(ctx.get("citedAnswers") or 0)
        cache_hit = int(ctx.get("cacheHit") or 0)
        cache_total = int(ctx.get("cacheTotal") or 0)
        retrieval_hit = int(ctx.get("retrievalHit") or 0)

        f = {}
        # ① 工艺表述准确率(拦截反向——拦截越多准确率越低)
        accuracy = _clamp(
            100.0 * (1 - guard_hit / total) if total else 70.0,
            0, 100)
        f["craft_accuracy"] = _factor(
            "craft_accuracy", "工艺表述准确率",
            accuracy, self.WEIGHTS["craft_accuracy"],
            f"拦截 {guard_hit}/{total}")
        # ② 守门拦截覆盖(应拦截全拦=100; 无样本中性 70)
        coverage = 70.0 if not guard_hit else 90.0
        f["guard_coverage"] = _factor(
            "guard_coverage", "守门拦截覆盖",
            coverage, self.WEIGHTS["guard_coverage"],
            f"守门命中 {guard_hit}(代理口径)")
        # ③ 溯源完整率
        citation = (_clamp(100.0 * cited / technical, 0, 100)
                    if technical else 70.0)
        f["citation_integrity"] = _factor(
            "citation_integrity", "溯源完整率",
            citation, self.WEIGHTS["citation_integrity"],
            f"含引用 {cited}/{technical}")
        # ④ 语义缓存效能
        cache_rate = (_clamp(
            100.0 * cache_hit / cache_total, 0, 100)
            if cache_total else 70.0)
        f["cache_efficiency"] = _factor(
            "cache_efficiency", "语义缓存效能",
            cache_rate, self.WEIGHTS["cache_efficiency"],
            f"命中 {cache_hit}/{cache_total}")
        # ⑤ 知识检索命中
        retrieval = (_clamp(
            100.0 * retrieval_hit / total, 0, 100)
            if total else 70.0)
        f["retrieval_hit"] = _factor(
            "retrieval_hit", "知识检索命中",
            retrieval, self.WEIGHTS["retrieval_hit"],
            f"命中 {retrieval_hit}/{total}")

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
            "levelName": {"high": "护城河稳固",
                          "medium": "待优化",
                          "low": "认知危机"}[level],
            "action": action,
            "actionName": DECISION_NAMES[action],
            "factors": list(f.values()),
            "scoredAt": None,
        }
        from datetime import datetime, UTC
        result["scoredAt"] = datetime.now(
            UTC).isoformat()
        logger.info("zyh_cognition_scored score=%s "
                    "action=%s", result["score"], action)
        return result
