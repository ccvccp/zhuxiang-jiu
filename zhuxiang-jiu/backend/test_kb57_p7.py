"""57号·AI智能知识库模块 P7 专项测试
(语义向量检索——embedding 检索/惰性回填/
context_trigger 语义补位/fail-open 回落)

运行方式:
    python test_kb57_p7.py

覆盖:
    - 语义检索: 余弦相似度 top-K+阈值过滤
    - 惰性回填: 无向量种子检索时批量补
    - 发布向量化: review 注入(review 单元
      以 ensure_seed_vector 直测)
    - 语义补位: 确定性不足 3 时补足+去重
    - fail-open: 未配置 key/开关关 → 确定性轨独存
    - 查询向量缓存: 重述复用
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ.pop("KB57_EMBED_SEARCH", None)
os.environ["KNOWLEDGE_EMBEDDING"] = "on"

PASS = 0
FAIL = 0
RESULTS = []

MEMBER = 8001


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def make_seed(title, text,
                    value_tags=None):
    """直建 published 种子(向量测试池)"""
    from core.helpers import ts
    from repositories.kb57_repository import (
        Kb57Repository,
    )
    repo = Kb57Repository()
    seed_id = await repo.next_seed_id()
    await repo.save_seed({
        "seedId": seed_id,
        "seedVersion": 1,
        "type": "text",
        "title": title,
        "content": {"text": text,
                    "mediaRef": None,
                    "transcript": None,
                    "keyframes": None,
                    "alt": None},
        "contentHash": "sha256:t",
        "complianceFingerprint":
            "sha256:t",
        "valueTags": value_tags or [],
        "sourceId": "ops_manual",
        "sourceCredibility": 0.9,
        "privacyCost": 0.002,
        "knowledgeReason": "p7",
        "humanVerified": True,
        "validUntil": "2099-01-01",
        "abTest": {"active": False,
                   "variantOf": None},
        "status": "published",
        "gapId": 0,
        "resourceId": 0,
        "viewCount": 0,
        "positiveCount": 0,
        "negativeCount": 0,
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    return seed_id


class TestEmbedSearch:
    """01 语义检索核心"""

    async def run(self):
        print("[01 语义检索]")
        reset_all()
        import services.kb57_embedding_service \
            as emb_mod
        from services.kb57_embedding_service import (
            Kb57EmbeddingService,
        )
        svc = Kb57EmbeddingService()

        # 未配置 key → None(fail-open)
        r = await svc.semantic_search("清甜口感")
        record("未配置 key 返回 None",
               r is None, str(r))

        # 开关显式 off(即使配置 key) → None
        saved = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "test-key"
        os.environ["LLM_ENABLED"] = "on"
        os.environ["KB57_EMBED_SEARCH"] = "off"
        r = await svc.semantic_search("清甜口感")
        record("开关 off 返回 None",
               r is None, str(r))
        os.environ["KB57_EMBED_SEARCH"] = "on"

        # 种子池: 甘冽(味觉向) vs 闻香(嗅觉向)
        sid_taste = await make_seed(
            "味觉·甘冽",
            "舌尖清甜如山泉回甘")
        # 嗅觉种子(对照)
        await make_seed(
            "嗅觉·闻香",
            "雨后竹林青竹表皮香气")

        # 伪向量: 味觉轴 [1,0] 嗅觉轴 [0,1]
        def fake_embed(texts):
            out = []
            for t in texts:
                if "甘" in t or "甜" in t \
                        or "清甜" in t:
                    out.append([1.0, 0.0])
                else:
                    out.append([0.0, 1.0])
            return out

        emb_mod._embed_texts = fake_embed
        emb_mod._query_cache.clear()

        # 惰性回填+语义命中
        r = await svc.semantic_search(
            "清甜的口感", limit=2)
        record("语义检索返回结果",
               isinstance(r, list) and len(r) >= 1,
               str(r)[:80])
        if r:
            top = r[0]
            record("语义命中味觉种子(排序)",
                   (top.get("seed") or {})
                   .get("seedId") == sid_taste,
                   str((top.get("seed") or {})
                       .get("seedId")))
            record("相似度 ≥ 阈值 0.5",
                   (top.get("similarity") or 0)
                   >= 0.5,
                   str(top.get("similarity")))

        # 惰性回填后向量已存(回读种子)
        from repositories.kb57_repository import (
            Kb57Repository,
        )
        seed_back = await (
            Kb57Repository().get_seed(
                sid_taste))
        record("惰性回填向量入库",
               bool(seed_back.get("embedding")),
               str(seed_back.get("embedding"))[:40])

        # 缓存命中(第二次同 query 不再调
        # embed——计数器验证)
        calls = {"n": 0}
        real_fake = emb_mod._embed_texts

        def counting_embed(texts):
            calls["n"] += 1
            return real_fake(texts)

        emb_mod._embed_texts = counting_embed
        emb_mod._query_cache.clear()
        await svc.semantic_search("清甜的口感")
        await svc.semantic_search("清甜的口感")
        record("查询向量缓存复用(2 次 1 embed)",
               calls["n"] == 1,
               str(calls["n"]))
        emb_mod._embed_texts = real_fake

        # 还原环境
        emb_mod._query_cache.clear()
        os.environ["KNOWLEDGE_EMBEDDING"] = "off"
        if saved is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = saved
        os.environ["LLM_ENABLED"] = "off"


class TestFallback:
    """02 fail-open 回落(真实无 key 环境)"""

    async def run(self):
        print("[02 回落]")
        reset_all()
        from services.kb57_feed_service import (
            Kb57FeedService,
        )
        os.environ["KB57_MODE"] = "assist"

        sid = await make_seed(
            "政策指南", "补贴办理流程",
            value_tags=["policy"])
        feed_svc = Kb57FeedService()

        # 确定性轨独存(语义轨 None 不影响)
        r = await feed_svc.context_trigger(
            MEMBER, trigger_type="search_miss",
            query="policy subsidy")
        record("无 key 确定性轨独存",
               sid in (r.get("matchedSeeds")
                       or []),
               str(r.get("matchedSeeds")))
        record("语义轨空标注",
               (r.get("semanticMatched") or [])
               == [],
               str(r.get("semanticMatched")))
        os.environ["KB57_MODE"] = "off"


class TestSemanticFill:
    """03 语义补位融合(context_trigger)"""

    async def run(self):
        print("[03 语义补位]")
        reset_all()
        import services.kb57_embedding_service \
            as emb_mod
        from services.kb57_feed_service import (
            Kb57FeedService,
        )
        os.environ["KB57_MODE"] = "assist"
        os.environ["LLM_API_KEY"] = "test-key"
        os.environ["LLM_ENABLED"] = "on"
        os.environ["KNOWLEDGE_EMBEDDING"] = "on"
        os.environ["KB57_EMBED_SEARCH"] = "on"

        # 味觉种子(子串不命中的 query 用)
        sid_taste = await make_seed(
            "味觉·甘冽",
            "舌尖清甜如山泉回甘",
            value_tags=["味觉"])

        def fake_embed(texts):
            return [[1.0, 0.0]
                    if ("甘" in t or "清甜" in t
                        or "口感" in t)
                    else [0.0, 1.0]
                    for t in texts]

        emb_mod._embed_texts = fake_embed
        emb_mod._query_cache.clear()

        feed_svc = Kb57FeedService()

        # query "口感层次"——子串不命中,
        # 语义轨补位
        r = await feed_svc.context_trigger(
            MEMBER, trigger_type="search_miss",
            query="口感层次怎么样")
        record("语义补位命中(子串未中)",
               sid_taste in (
                   r.get("matchedSeeds")
                   or []),
               str(r.get("matchedSeeds")))
        record("语义命中有标注",
               sid_taste in (
                   r.get("semanticMatched")
                   or []),
               str(r.get("semanticMatched")))

        # 确定性命中时不重复(去重)
        r = await feed_svc.context_trigger(
            MEMBER, trigger_type="search_miss",
            query="甘冽")   # 标题子串命中
        rec_ids = r.get("matchedSeeds") or []
        record("确定性+语义去重(无重复)",
               len(rec_ids)
               == len(set(rec_ids)),
               str(rec_ids))

        # 还原
        emb_mod._query_cache.clear()
        os.environ.pop("LLM_API_KEY", None)
        os.environ["LLM_ENABLED"] = "off"
        os.environ["KB57_MODE"] = "off"


class TestObservability:
    """04 语义轨观测打点"""

    async def run(self):
        print("[04 观测打点]")
        reset_all()
        import services.kb57_embedding_service \
            as emb_mod
        from services.kb57_embedding_service import (
            Kb57EmbeddingService,
            read_sem_stats,
        )
        os.environ["LLM_API_KEY"] = "test-key"
        os.environ["LLM_ENABLED"] = "on"
        os.environ["KNOWLEDGE_EMBEDDING"] = "on"
        os.environ["KB57_EMBED_SEARCH"] = "on"

        await make_seed(
            "味觉·甘冽", "舌尖清甜如山泉回甘")
        emb_mod._sem_stats_mem.clear()
        emb_mod._query_cache.clear()

        # 种子向量 [1,0]; query 命中轴 [1,0],
        # 无关轴 [0,1](正交 → 空)
        def fake_embed(texts):
            return [[1.0, 0.0]
                    if "甘" in t or "清甜" in t
                    else [0.0, 1.0]
                    for t in texts]

        emb_mod._embed_texts = fake_embed
        svc = Kb57EmbeddingService()

        # 命中轨
        await svc.semantic_search(
            "清甜的口感", limit=2)
        # 空轨(正交)
        await svc.semantic_search(
            "zz无关词", limit=2)
        stats = await read_sem_stats()
        record("打点: searches=2",
               stats.get("searches") == 2,
               str(stats))
        record("打点: hits 计数",
               int(stats.get("hits") or 0) >= 1,
               str(stats.get("hits")))
        record("打点: empty 计数",
               int(stats.get("empty") or 0) == 1,
               str(stats.get("empty")))

        # 观测面 HTTP(内存态计数读取)
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get(
            "/api/kb57/semantics/stats",
            headers={"X-Role": "admin"})
        body = resp.json() or {}
        record("HTTP 观测面 200+结构",
               resp.status_code == 200
               and "counts" in body
               and "threshold" in body
               and body.get("threshold") == 0.45,
               str(resp.status_code))

        # 还原
        emb_mod._query_cache.clear()
        os.environ.pop("LLM_API_KEY", None)
        os.environ["LLM_ENABLED"] = "off"
        os.environ["KB57_MODE"] = "off"


async def run_all():
    await TestEmbedSearch().run()
    await TestFallback().run()
    await TestSemanticFill().run()
    await TestObservability().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
