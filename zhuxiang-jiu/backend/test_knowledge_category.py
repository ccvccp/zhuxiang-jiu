# -*- coding: utf-8 -*-
"""知识检索 category 维度单测(L3: rag_answer 扩 category 参数)

依据 docs/复购预测与直播QA模块优化建议.md §三 P1 L3——
直播高频问题与通用 FAQ 混在全库检索召回噪声大, rag_answer 增
可选 category(默认 None 全库, 既有调用方零变化):
    ① category 过滤命中(category="live" 命中 live 条目而非全库
       泛条目; 该类有候选但低置信不回退)
    ② 该类零命中 → 自动回退全库重试一次(fail-soft,
       categoryFallback=True)
    ③ 语义缓存 key 加 category 维度(同问题不同类不串;
       None 维持旧 key 形态)
    ④ category=None 行为与旧完全一致(旧 key/返回无新键)

运行: pytest test_knowledge_category.py
"""
import asyncio

import pytest

from repositories.store import reset_store
from services.knowledge_service import (
    KnowledgeService, _CACHES, _reset_knowledge_caches,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """每用例隔离: 内存 store 重置 + RAG 缓存清空(test_live_qa 同款
    先例) + 固定 n-gram 确定性轨(不依赖外部 embedding 配置)"""
    reset_store()
    _reset_knowledge_caches()
    monkeypatch.setenv("KNOWLEDGE_EMBEDDING", "off")


async def _publish(svc: KnowledgeService, question: str, answer: str,
                   category: str, keywords: str = "") -> dict:
    """辅助: 创建→审核通过→发布(先审后显链路照旧)"""
    entry = await svc.create_entry(question=question, answer=answer,
                                    category=category, keywords=keywords)
    await svc.review_entry(entry["id"], approve=True, reviewer_id=1)
    return await svc.publish_entry(entry["id"], publisher_id=1)


# ---------- ① category 过滤命中 ----------

def test_category_filter_hits_live_entry():
    """category="live": 类内同问 direct 命中 live 条目(泛条目被
    category 过滤隔离在外); 泛问法在 live 类内仅有低置信候选时
    不回退(unsolved 且 categoryFallback=False)"""

    async def run():
        svc = KnowledgeService()
        live = await _publish(svc, "直播间竹香酒今晚专属价是多少",
                              "直播间专属价 99 元每瓶, 今晚八点前有效。",
                              category="live", keywords="直播 专属 价格")
        await _publish(svc, "竹香酒多少钱一瓶",
                       "竹香酒常规零售价 128 元每瓶。",
                       category="faq", keywords="价格 多少钱")
        # live 类内同问 → direct 命中 live 条目
        r = await svc.rag_answer("直播间竹香酒今晚专属价是多少",
                                 category="live")
        assert r["mode"] == "direct"
        assert r["answer"] == "直播间专属价 99 元每瓶, 今晚八点前有效。"
        assert r["citations"][0]["entryId"] == live["id"]
        assert r["categoryFallback"] is False    # 该类有命中不回退
        # 同问全库(None)同样命中 live 条目, 且返回不带新键
        r_all = await svc.rag_answer("直播间竹香酒今晚专属价是多少")
        assert r_all["citations"][0]["entryId"] == live["id"]
        assert "categoryFallback" not in r_all
        # 泛问法在 live 类内仅有低置信候选 → 不回退全库(unsolved)
        r_low = await svc.rag_answer("竹香酒多少钱一瓶", category="live")
        assert r_low["mode"] == "unsolved"
        assert r_low["categoryFallback"] is False

    asyncio.run(run())


# ---------- ② 该类零命中 → 回退全库 ----------

def test_category_empty_falls_back_to_full_library():
    """category="live" 类零条目 → 回退全库重试一次, 命中 faq 泛条目
    并带 categoryFallback=True 标记(fail-soft 不崩)"""

    async def run():
        svc = KnowledgeService()
        faq = await _publish(svc, "竹香酒多少钱一瓶",
                             "竹香酒常规零售价 128 元每瓶。",
                             category="faq", keywords="价格 多少钱")
        # live 类零条目 → 检索空 → 回退全库命中 faq 泛条目
        r = await svc.rag_answer("竹香酒多少钱一瓶", category="live")
        assert r["mode"] == "direct"
        assert r["answer"] == "竹香酒常规零售价 128 元每瓶。"
        assert r["citations"][0]["entryId"] == faq["id"]
        assert r["categoryFallback"] is True
        # 空库 + category + 无泛条目问题 → 回退后仍空 → unsolved 不崩
        r_empty = await svc.rag_answer("完全无关的问题xyzq",
                                       category="live")
        assert r_empty["mode"] == "unsolved"
        assert r_empty["categoryFallback"] is True

    asyncio.run(run())


# ---------- ③ 缓存 key 加 category 维度 ----------

def test_result_cache_key_isolated_by_category():
    """同问题不同 category 各自命中本类条目(缓存不串类);
    key 形态: 带类 "{q}|rule|{cat}" / 不带类 "{q}|rule"(旧形态)"""

    async def run():
        svc = KnowledgeService()
        await _publish(svc, "缓存隔离问法直播口径版",
                       "直播口径: 今晚直播间下单立减。",
                       category="live", keywords="直播 口径")
        await _publish(svc, "缓存隔离问法常规口径版",
                       "常规口径: 门店零售价不变。",
                       category="faq", keywords="常规 口径")
        q = "缓存隔离问法"
        r_live = await svc.rag_answer(q, category="live")
        r_faq = await svc.rag_answer(q, category="faq")
        # 两类各自命中本类条目(若缓存串类, 第二次复用第一次结果)
        assert "直播口径" in r_live["answer"]
        assert "常规口径" in r_faq["answer"]
        assert (r_live["citations"][0]["entryId"]
                != r_faq["citations"][0]["entryId"])
        # 缓存 key 形态: 带类三段式写入, 互不覆盖
        keys = set(_CACHES["result"].keys())
        assert f"{q}|rule|live" in keys
        assert f"{q}|rule|faq" in keys
        assert f"{q}|rule" not in keys    # 尚未以 None 调用过该问法
        # None 调用: 旧 key 形态两段式, 不与带类缓存冲突
        r_none = await svc.rag_answer(q)
        assert r_none["mode"] in ("direct", "synthesized")
        assert "categoryFallback" not in r_none
        assert f"{q}|rule" in _CACHES["result"]

    asyncio.run(run())


# ---------- ④ category=None 与旧行为完全一致 ----------

def test_category_none_keeps_legacy_behavior():
    """category=None: 旧缓存 key 形态 "{q}|rule" + 返回结构无新键
    + 位置参数既有签名 rag_answer(q, "rule") 兼容"""

    async def run():
        svc = KnowledgeService()
        await _publish(svc, "兼容验证竹香酒产地在哪里",
                       "产地为山东泰安徂徕山水源保护区。",
                       category="faq", keywords="产地 徂徕山")
        q = "兼容验证竹香酒产地在哪里"
        r1 = await svc.rag_answer(q)               # 不传 category
        assert r1["mode"] == "direct"
        assert "categoryFallback" not in r1
        assert f"{q}|rule" in _CACHES["result"]    # 旧 key 形态
        # 既有位置参数调用形态兼容, 结果一致(缓存清空后重算)
        _reset_knowledge_caches()
        r2 = await svc.rag_answer(q, "rule")
        assert r2 == r1

    asyncio.run(run())
