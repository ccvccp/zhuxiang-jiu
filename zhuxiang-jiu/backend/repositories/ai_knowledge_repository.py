"""AI 案例知识库数据访问层(v7.9 阶段 1: 双模式存储 + 余弦检索)

参照成熟 AI 大模型的「长期记忆 + 检索增强(RAG) + 经验回放」机制,
为 14 个评分器建立案例知识库:

    学习周期(mark_feedback_learned) ──归档──→ 案例知识库(经验回放缓冲)
                                                │
    评分时(因子向量) ──top-k 余弦相似检索─────────┘
                                                ↓
                            邻居证据(相似案例的真实终态分布)
                                                ↓
                    v7.9 阶段 2: 检索增强评分(参数分 × 经验分校准)

案例形状(紧凑存储, 因子以 {name: score} 扁平字典表达):
    {
        caseId, scorerId, factors: {name: score},
        scoreAtDecision, action, actualAction, correct,
        source(auto/manual), businessKey, archivedAt
    }

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis(与 AiLearningRepository 一致)
    - 每评分器案例封顶 KB_MAX_CASES(2000), 归档时自动裁剪(保留最新)
    - 全 async(项目约定); Redis Key: zhuxiang:ai_knowledge:{entity}:{scorerId}
    - 环境开关 AI_KB=off 关闭归档与检索(默认开启)
"""

import json
import math
import os
from typing import Optional

from core.helpers import ts
from repositories.backend import (
    _k, get_in_memory_store, get_redis_client, is_redis_mode,
)


def kb_enabled() -> bool:
    """知识库总开关(AI_KB=off 关闭, 默认开启; 运行时动态读取)"""
    return os.environ.get("AI_KB", "on").strip().lower() != "off"


class AiKnowledgeRepository:
    """AI 案例知识库数据访问层"""

    KB_MAX_CASES = 2000          # 每评分器案例封顶(归档时裁剪, 保留最新)
    DEFAULT_TOP_K = 5            # 检索默认近邻数

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_case_id(self) -> int:
        """案例 ID 自增(反馈 ID 独立序列, 避免语义混淆)"""
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.incr(_k("ai_knowledge", "case", "seq")))
        self.store["ai_knowledge_case_seq"] = \
            self.store.get("ai_knowledge_case_seq", 0) + 1
        return self.store["ai_knowledge_case_seq"]

    # ============================================================
    # 案例写入(归档)
    # ============================================================

    @staticmethod
    def _case_from_feedback(fb: dict) -> Optional[dict]:
        """反馈记录 → 知识案例(无因子快照的反馈无法检索, 跳过)"""
        factors = {}
        for f in (fb.get("factors") or []):
            name = str(f.get("name") or "")
            try:
                score = float(f.get("score") or 0)
            except (TypeError, ValueError):
                continue
            if name:
                factors[name] = score
        if not factors:
            return None
        return {
            "factors": factors,
            "scoreAtDecision": fb.get("scoreAtDecision"),
            "action": fb.get("expectedAction") or fb.get("actualAction"),
            "actualAction": fb.get("actualAction"),
            "correct": fb.get("correct"),
            "source": fb.get("source") or "manual",
            "businessKey": fb.get("businessKey") or "",
            "note": fb.get("note") or "",
        }

    async def add_case(self, scorer_id: str, case: dict) -> int:
        """新增知识案例(返回案例 ID; 自动封顶裁剪)"""
        case = dict(case)
        case["caseId"] = await self.next_case_id()
        case["scorerId"] = scorer_id
        case["archivedAt"] = ts()
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("ai_knowledge", "case", scorer_id)
            pipe = client.pipeline()
            pipe.lpush(key, json.dumps(case, ensure_ascii=False))
            pipe.ltrim(key, 0, self.KB_MAX_CASES - 1)
            await pipe.execute()
        else:
            self.store.setdefault("ai_knowledge_cases", {}).setdefault(
                scorer_id, []).insert(0, case)
            cases = self.store["ai_knowledge_cases"][scorer_id]
            del cases[self.KB_MAX_CASES:]
        return case["caseId"]

    async def archive_feedback(self, scorer_id: str,
                               feedback_records: list[dict]) -> int:
        """批量归档反馈为案例(跳过无因子记录; 返回实际归档数)"""
        if not kb_enabled():
            return 0
        archived = 0
        for fb in feedback_records or []:
            case = self._case_from_feedback(fb)
            if case is None:
                continue
            await self.add_case(scorer_id, case)
            archived += 1
        return archived

    # ============================================================
    # 案例查询
    # ============================================================

    async def list_cases(self, scorer_id: str, limit: int = 50) -> list[dict]:
        """列出知识案例(新→旧)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.lrange(
                _k("ai_knowledge", "case", scorer_id), 0, -1)
            records = [json.loads(x) for x in raw]
        else:
            records = list(self.store.get("ai_knowledge_cases", {})
                           .get(scorer_id, []))
        return records[:limit] if limit else records

    async def count_cases(self, scorer_id: str) -> int:
        """统计案例数"""
        return len(await self.list_cases(scorer_id, limit=0))

    async def kb_stats(self, scorer_ids: list[str]) -> dict:
        """知识库统计(驾驶舱/管理端用): 各评分器案例数"""
        counts = {}
        for sid in scorer_ids:
            counts[sid] = await self.count_cases(sid)
        return {
            "success": True,
            "enabled": kb_enabled(),
            "maxCasesPerScorer": self.KB_MAX_CASES,
            "totalCases": sum(counts.values()),
            "scorerCounts": counts,
            "modelVersion": "v1-kb",
            "generatedAt": ts(),
        }

    # ============================================================
    # 余弦相似检索(RAG 核心原语)
    # ============================================================

    @staticmethod
    def _cosine(a: dict, b: dict) -> float:
        """因子分数字典的余弦相似度(缺失维度按 0; 全零向量返回 0)"""
        dims = set(a) | set(b)
        dot = sum(a.get(d, 0.0) * b.get(d, 0.0) for d in dims)
        norm_a = math.sqrt(sum(v * v for v in a.values()))
        norm_b = math.sqrt(sum(v * v for v in b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    async def search_similar(self, scorer_id: str,
                             factor_scores: dict,
                             k: int = None) -> list[dict]:
        """检索 top-k 相似案例(新→旧稳定排序, 相似度降序)

        Args:
            factor_scores: 查询因子向量 {name: score}(评分时的因子快照)
            k: 近邻数(缺省 DEFAULT_TOP_K)

        Returns:
            [{caseId, similarity, action, actualAction, correct,
              scoreAtDecision, businessKey, archivedAt}] 相似度降序
        """
        k = k or self.DEFAULT_TOP_K
        if not factor_scores:
            return []
        results = []
        for case in await self.list_cases(scorer_id, limit=0):
            sim = self._cosine(factor_scores, case.get("factors") or {})
            if sim <= 0:
                continue
            results.append({
                "caseId": case.get("caseId"),
                "similarity": round(sim, 4),
                "action": case.get("action"),
                "actualAction": case.get("actualAction"),
                "correct": case.get("correct"),
                "scoreAtDecision": case.get("scoreAtDecision"),
                "businessKey": case.get("businessKey"),
                "archivedAt": case.get("archivedAt"),
            })
        # 相似度降序, 并列时案例 ID 大(新)优先(稳定去随机)
        results.sort(key=lambda r: (r["similarity"], r["caseId"]),
                     reverse=True)
        return results[:k]
