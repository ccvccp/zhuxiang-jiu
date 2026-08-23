"""AI 检索增强评分引擎(v7.9 阶段 2: RAG 式知识增强)

参照大模型 RAG(检索增强生成)机制, 在评分器输出之上叠加知识库证据:

    评分器(参数分) ──因子向量──→ 案例知识库 top-k 余弦检索
                                     ↓
                          邻居证据(相似案例的真实终态分布)
                                     ↓
              ┌──────────────────────┴──────────────────────┐
              ↓                                             ↓
    校准融合(经验分)                              区域可靠性判定
    calibratedScore =                           wrongRate ≥ 阈值且证据充分
      score + clamp(α·(经验分-参数分))           → regionUnreliable=True
    (护栏: |调整| ≤ max_adjust)                  → enforce 模式升级人工复核

安全设计(对齐 v7.8 决策阻断哲学):
    - 证据只「加严」不「放宽」: regionUnreliable 最多把 pass 升级为
      reviewRequired(人工复核), 永不触发 blocked, 永不降级已有风险动作
    - 证据不足不生效: evidenceCount < min_evidence 时仅记录不判定
    - 全链路火后不管: 检索/计算失败返回 None, 评分行为完全不变

环境开关与参数(运行时动态读取):
    AI_KB=off              关闭知识库(默认开启, 与存储层共用)
    AI_KB_ALPHA            校准融合系数(默认 0.3)
    AI_KB_MAX_ADJUST       校准幅度护栏(默认 15 分)
    AI_KB_MIN_EVIDENCE     区域判定最小证据数(默认 3)
    AI_KB_TOP_K            检索近邻数(默认 5)
"""

import logging
import os
from typing import Optional

from repositories.ai_knowledge_repository import (
    AiKnowledgeRepository, kb_enabled,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-kb-augment"


def _cfg_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "") or default)
    except ValueError:
        return default


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "") or default)
    except ValueError:
        return default


async def augment_with_knowledge(scorer_id: str, factors: list,
                                 score: float) -> Optional[dict]:
    """检索增强: 因子向量 → top-k 邻居证据 + 校准分 + 区域可靠性

    Args:
        factors: 评分器输出的因子快照([{name, score}, ...])
        score: 参数分(评分器原始评分)

    Returns:
        知识证据块; 知识库关闭/无证据/检索失败返回 None:
        {
            evidenceCount, correctCount, wrongRate,
            avgSimilarity, topSimilarity,
            empiricalScore, calibratedScore,
            regionUnreliable, neighbors: [...top-k 摘要],
            modelVersion
        }
    """
    if not kb_enabled() or not factors:
        return None
    try:
        query = {}
        for f in factors:
            name = str(f.get("name") or "")
            try:
                val = float(f.get("score") or 0)
            except (TypeError, ValueError):
                continue
            if name:
                query[name] = val
        if not query:
            return None

        repo = AiKnowledgeRepository()
        k = _cfg_int("AI_KB_TOP_K", 5)
        neighbors = await repo.search_similar(scorer_id, query, k=k)
        if not neighbors:
            return None

        evidence = [n for n in neighbors
                    if n.get("correct") is not None]
        correct_count = sum(1 for n in evidence if n["correct"] is True)
        sims = [n["similarity"] for n in neighbors]

        # 经验分: 相似度加权平均(仅有决策分的邻居参与)
        scored = [(n["similarity"], n["scoreAtDecision"])
                  for n in neighbors
                  if isinstance(n.get("scoreAtDecision"), (int, float))]
        weight_sum = sum(w for w, _ in scored)
        empirical = (sum(w * s for w, s in scored) / weight_sum
                     if weight_sum > 0 else None)

        # 校准融合(护栏约束)
        alpha = _cfg_float("AI_KB_ALPHA", 0.3)
        max_adjust = _cfg_float("AI_KB_MAX_ADJUST", 15.0)
        calibrated = None
        if empirical is not None:
            delta = alpha * (float(empirical) - float(score))
            calibrated = round(float(score) + max(-max_adjust,
                                                  min(max_adjust, delta)), 1)

        # 区域可靠性: 相似案例历史决策错误率过高 → 该区域模型不可靠
        min_evidence = _cfg_int("AI_KB_MIN_EVIDENCE", 3)
        wrong_rate = (round((len(evidence) - correct_count)
                            / len(evidence), 4) if evidence else None)
        unreliable = bool(
            evidence and len(evidence) >= min_evidence
            and wrong_rate is not None and wrong_rate >= 0.5)

        return {
            "evidenceCount": len(evidence),
            "correctCount": correct_count,
            "wrongRate": wrong_rate,
            "avgSimilarity": round(sum(sims) / len(sims), 4),
            "topSimilarity": max(sims),
            "empiricalScore": (round(float(empirical), 1)
                               if empirical is not None else None),
            "calibratedScore": calibrated,
            "regionUnreliable": unreliable,
            "neighbors": [{
                "caseId": n["caseId"], "similarity": n["similarity"],
                "action": n["action"], "actualAction": n["actualAction"],
                "correct": n["correct"],
            } for n in neighbors],
            "modelVersion": MODEL_VERSION,
        }
    except Exception as exc:  # noqa: BLE001 - 增强失败不影响评分
        logger.warning("知识增强失败(scorer=%s): %s", scorer_id, exc)
        return None


def should_escalate_review(knowledge: Optional[dict]) -> bool:
    """证据驱动的复核升级判定(只加严不放宽)

    规则: 区域不可靠(相似案例错误率 ≥ 50% 且证据数达标) → True。
    enforce 决策门用它把 pass 动作升级为 reviewRequired(人工复核),
    永不升级为 blocked。
    """
    return bool(knowledge and knowledge.get("regionUnreliable"))
