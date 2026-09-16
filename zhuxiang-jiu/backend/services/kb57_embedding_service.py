"""57号·语义向量检索(kb57_embedding_service, P7)

收官报告"后续方向 3"落地: embedding 向量化
+ 余弦相似度检索(暴力扫描——种子量级百级,
ANN 向量库远期再议)。

设计(对齐 knowledge_service 既有范式):
    - published/boosted 种子惰性向量化
      (title+正文, 存于种子记录 embedding
      字段——repo 五清单已同步);
    - 发布时向量化(review approved 注入)
      + 检索时批量回填(存量种子);
    - 查询向量 5 分钟缓存(问题重述
      高度重复, embed 是检索链最贵一跳);
    - fail-open: 未配置 key/请求失败/
      开关关 → 返回 None, 调用方回落
      确定性子串轨(零行为变化)。

确定性铁律: 语义轨是 LLM 增强补充,
永不替代确定性匹配——context_trigger
首命中仍由子串轨决定, 语义轨仅补位。
"""

import logging
import math
import os
import time

from repositories.kb57_repository import (
    Kb57Repository,
)

logger = logging.getLogger("kb57_embedding")

# 推荐池状态(与 feed 服务同源)
FEEDABLE_STATUSES = ("published", "boosted")

# --------------------------------------------------------
# 语义轨观测计数(P7 生产观测——命中率/回落分布)
# 双模式: Redis HINCRBY / 内存 dict(测试)
# --------------------------------------------------------
_sem_stats_mem: dict = {}


async def bump_sem_stat(field: str, n: int = 1
                        ) -> None:
    """语义轨指标打点(失败静默——观测不阻塞检索)"""
    try:
        from repositories.backend import (
            is_redis_mode, get_redis_client, _k,
        )
        if is_redis_mode():
            client = await get_redis_client()
            await client.hincrby(
                _k("kb57", "sem_stats"), field, n)
        else:
            _sem_stats_mem[field] = \
                _sem_stats_mem.get(field, 0) + n
    except Exception:
        pass


async def read_sem_stats() -> dict:
    """语义轨计数读取(观测面消费)"""
    try:
        from repositories.backend import (
            is_redis_mode, get_redis_client, _k,
        )
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("kb57", "sem_stats"))
            return {k: int(v)
                    for k, v in (data or {}).items()}
        return dict(_sem_stats_mem)
    except Exception:
        return {}

# 语义相似度阈值(低于不命中——embedding-3 中文
# 相关对实测分布 0.43-0.60, 不相关对 ≤0.44,
# 0.45 为分界; 误召回代价高于漏召回时可上调)
SIMILARITY_THRESHOLD = 0.45

# 查询向量缓存(TTL 秒/容量上限)
_QUERY_TTL = 300
_QUERY_MAX = 200
_query_cache: dict = {}

# 模型版本(观测留痕)
MODEL_VERSION = "v1-kb57-embed"


def embed_search_enabled() -> bool:
    """语义轨总开关(KB57_EMBED_SEARCH=off 关, 默认 on;
    另须 llm embedding 轨已配置——双闸)"""
    if os.environ.get(
            "KB57_EMBED_SEARCH", "on"
    ).strip().lower() == "off":
        return False
    from services.llm_client import (
        embedding_enabled,
    )
    return embedding_enabled()


def _embed_texts(
        texts: list[str]) -> list[list[float]] | None:
    """批量文本向量化(llm 轨统一接入点——
    测试 monkeypatch 锚点)"""
    from services.llm_client import (
        provider_client,
    )
    return provider_client.embed(texts)


def _seed_text(seed: dict) -> str:
    """种子向量化文本(title+正文拼接)"""
    title = str(seed.get("title") or "")
    content = str(
        (seed.get("content") or {}).get("text")
        or "")
    return f"{title}\n{content}".strip()[:4000]


def _cosine(a: list, b: list) -> float:
    """余弦相似度(纯 python——百级量级足够)"""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


class Kb57EmbeddingService:
    """57号语义向量检索(P7)"""

    def __init__(self):
        self.repo = Kb57Repository()

    # --------------------------------------------------------
    # 向量化(发布时单种子 + 检索时批量回填)
    # --------------------------------------------------------

    async def ensure_seed_vector(
            self, seed: dict) -> dict:
        """单种子向量注入(review 发布时调用;
        已有向量/非发布态跳过, 失败不阻断)"""
        if seed.get("status") \
                not in FEEDABLE_STATUSES:
            return seed
        if seed.get("embedding"):
            return seed
        try:
            vecs = _embed_texts(
                [_seed_text(seed)])
            if vecs:
                seed["embedding"] = [
                    round(v, 6) for v in vecs[0]]
                await self.repo.save_seed(
                    seed, create=False)
                logger.info(
                    "kb57_seed_embedded "
                    "seed=%s dim=%s",
                    seed.get("seedId"),
                    len(seed["embedding"]))
        except Exception as exc:
            logger.warning(
                "kb57_embed_seed_failed: %s", exc)
        return seed

    async def _backfill_vectors(
            self, pool: list[dict]) -> int:
        """批量回填(检索时对无向量种子惰性补)

        Returns: 回填种子数(0=无需/失败)
        """
        pending = [
            s for s in pool
            if s.get("status")
            in FEEDABLE_STATUSES
            and not s.get("embedding")]
        if not pending:
            return 0
        try:
            vecs = _embed_texts(
                [_seed_text(s) for s in pending])
            if not vecs \
                    or len(vecs) != len(pending):
                return 0
            for s, v in zip(pending, vecs,
                            strict=False):
                s["embedding"] = [
                    round(x, 6) for x in v]
                await self.repo.save_seed(
                    s, create=False)
            logger.info(
                "kb57_seed_backfilled count=%s",
                len(pending))
            return len(pending)
        except Exception as exc:
            logger.warning(
                "kb57_backfill_failed: %s", exc)
            return 0

    # --------------------------------------------------------
    # 语义检索
    # --------------------------------------------------------

    async def semantic_search(
            self, query: str,
            limit: int = 3
    ) -> list[dict] | None:
        """语义检索(查询向量×池内种子余弦 top-K)

        Returns:
            [{"seed": {...}, "similarity": 0.87},
             ...] 按 similarity 降序;
            语义轨关闭/未配置/失败 → None
            (调用方回落确定性轨)。
        """
        if not embed_search_enabled():
            await bump_sem_stat("disabled")
            return None
        q = str(query or "").strip()
        if len(q) < 2:
            return None

        # 池收集(published/boosted)
        pool = []
        for status in FEEDABLE_STATUSES:
            pool.extend(
                await self.repo.list_seeds(
                    status=status, limit=200))
        if not pool:
            return None

        # 存量回填(无向量种子惰性补)
        backfilled = await self._backfill_vectors(
            pool)
        if backfilled:
            await bump_sem_stat(
                "backfilled", backfilled)
        embedded = [
            s for s in pool if s.get("embedding")]
        if not embedded:
            return None

        # 查询向量(5 分钟缓存)
        qvec = self._query_vector(q)
        if qvec is None:
            await bump_sem_stat("failed")
            return None

        # 余弦排序 top-K
        scored = []
        for s in embedded:
            sim = _cosine(
                qvec, s["embedding"])
            if sim >= SIMILARITY_THRESHOLD:
                scored.append((sim, s))
        scored.sort(key=lambda kv: -kv[0])

        await bump_sem_stat("searches")
        if not scored:
            await bump_sem_stat("empty")
        else:
            await bump_sem_stat(
                "hits", len(scored[:limit]))

        return [
            {"seed": s,
             "similarity": round(sim, 4)}
            for sim, s in scored[:limit]]

    def _query_vector(
            self, query: str) -> list | None:
        """查询向量(TTL 缓存——重述复用)"""
        cached = _query_cache.get(query)
        if cached is not None \
                and cached[0] > time.time():
            return cached[1]
        try:
            vecs = _embed_texts([query])
        except Exception as exc:
            logger.warning(
                "kb57_query_embed_failed: %s", exc)
            return None
        if not vecs:
            return None
        _query_cache[query] = (
            time.time() + _QUERY_TTL, vecs[0])
        if len(_query_cache) > _QUERY_MAX:
            for k in list(
                    _query_cache)[
                    :len(_query_cache) // 2]:
                _query_cache.pop(k, None)
        return vecs[0]
