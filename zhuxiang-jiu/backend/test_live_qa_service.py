# -*- coding: utf-8 -*-
"""数字人直播弹幕 QA 服务单测(live_qa_service + main 挂载冒烟)

覆盖:
    T1 模式闸(off 返回 disabled 话术+mode=off / 默认 on / 非法值回落)
    T2 同问 60s 去重(归一化键——第二次 cached=True 且结果一致)
    T3 空/超长问题过滤
    T4 敏感词过滤(品牌竞品/违规词)
    T5 unsolved 低置信兜底话术 + 缺口登记 best-effort
    T6 RAG 异常 fail-soft(兜底话术, 不抛出)
    T7 stats 汇总(total/unsolved/filtered/cached/unsolvedRate/top5)
    T8 rag_answer 真实集成 smoke(asyncio 内存 store 可空构造:
       空库→unsolved 兜底; 种子发布→direct 直连命中)
    T9 main.py 挂载冒烟(两条路由注册进 FastAPI app)

打桩说明: T1-T7 以桩对象替换 KnowledgeService(计数 rag_answer /
record_gap 调用), 避免依赖真实知识库内容; T8 走真实链路。

运行: pytest test_live_qa_service.py
"""
import asyncio
import json

import pytest

import services.live_qa_service as live_qa_module
from services.live_qa_service import (
    LIVE_QA_DISABLED_ANSWER,
    LIVE_QA_FILTERED_ANSWER,
    LIVE_QA_UNSOLVED_ANSWER,
    LiveQaService,
    live_qa_mode,
)


class _StubKnowledge:
    """KnowledgeService 测试桩(计数 rag_answer/record_gap 调用)"""

    def __init__(self, result=None, error=None):
        self.result = result or {
            "answer": "竹香酒以竹笋、竹茎、竹叶配合富硒山泉水古法酿制。",
            "mode": "direct", "citations": [], "confidence": 0.9,
        }
        self.error = error
        self.rag_calls = []
        self.gap_calls = []

    async def rag_answer(self, question, provider="rule"):
        self.rag_calls.append((question, provider))
        if self.error is not None:
            raise self.error
        return dict(self.result)

    async def record_gap(self, question, session_id=""):
        self.gap_calls.append((question, session_id))
        return {"ok": True}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """每用例隔离: 留痕目录重定向 tmp + 模式闸回默认 on
    + RAG 固定 n-gram 确定性轨(不依赖外部 embedding 配置)"""
    monkeypatch.setattr(live_qa_module, "LIVE_QA_LOG_DIR", tmp_path)
    monkeypatch.delenv("LIVE_QA_MODE", raising=False)
    monkeypatch.setenv("KNOWLEDGE_EMBEDDING", "off")


# ---------- T1 模式闸 ----------

def test_t1_mode_gate(monkeypatch):
    """默认 on; off 生效; 非法值回落 on(growth80 multiplier_mode 同款口径)"""
    assert live_qa_mode() == "on"
    monkeypatch.setenv("LIVE_QA_MODE", "off")
    assert live_qa_mode() == "off"
    monkeypatch.setenv("LIVE_QA_MODE", "garbage")
    assert live_qa_mode() == "on"


def test_t1_off_mode_answer():
    """off 模式: 返回统一 disabled 话术, ragMode=off, 不触达 RAG"""

    async def run():
        stub = _StubKnowledge()
        svc = LiveQaService(knowledge=stub)
        import os
        os.environ["LIVE_QA_MODE"] = "off"
        try:
            r = await svc.answer("room-1", "竹香酒多少钱一瓶")
            assert r["answer"] == LIVE_QA_DISABLED_ANSWER
            assert r["ragMode"] == "off"
            assert r["roomId"] == "room-1"
            assert r["cached"] is False and r["filtered"] is False
            assert stub.rag_calls == []          # 模式闸先于 RAG
            assert stub.gap_calls == []
            # 留痕: 进程内记录 + JSONL 即写
            assert len(svc._records) == 1
            log_file = (live_qa_module.LIVE_QA_LOG_DIR
                        / "live_qa.jsonl")
            lines = log_file.read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 1
            assert json.loads(lines[0])["ragMode"] == "off"
        finally:
            os.environ.pop("LIVE_QA_MODE", None)

    asyncio.run(run())


# ---------- T2 同问 60s 去重 ----------

def test_t2_dedup_same_question():
    """同直播间同问(归一化后)第二次 cached=True 且结果一致, RAG 只调一次"""

    async def run():
        stub = _StubKnowledge()
        svc = LiveQaService(knowledge=stub)
        r1 = await svc.answer("room-1", "竹香酒的原料是什么")
        assert r1["cached"] is False and r1["ragMode"] == "direct"
        # 归一化口径: 多空白+大小写差异仍视为同问
        r2 = await svc.answer("room-1", "  竹香酒的原料是什么  ")
        assert r2["cached"] is True
        assert r2["answer"] == r1["answer"]
        assert r2["ragMode"] == r1["ragMode"]
        assert r2["confidence"] == r1["confidence"]
        assert r2["ts"] == r1["ts"]              # 幂等: 复用上次结果
        assert len(stub.rag_calls) == 1          # RAG 只真实调用一次
        # 第三次仍命中缓存
        r3 = await svc.answer("room-1", "竹香酒的原料是什么")
        assert r3["cached"] is True
        assert len(stub.rag_calls) == 1

    asyncio.run(run())


def test_t2_dedup_scoped_by_room_and_question():
    """不同直播间/不同问题不共享缓存(各走一次 RAG)"""

    async def run():
        stub = _StubKnowledge()
        svc = LiveQaService(knowledge=stub)
        await svc.answer("room-1", "竹香酒的原料是什么")
        r_b = await svc.answer("room-2", "竹香酒的原料是什么")
        r_q = await svc.answer("room-1", "竹香酒怎么酿造的")
        assert r_b["cached"] is False and r_q["cached"] is False
        assert len(stub.rag_calls) == 3   # 初次 + 异房间 + 异问题各一次

    asyncio.run(run())


# ---------- T3 空/超长过滤 ----------

def test_t3_length_filter():
    """strip 后空问题与超 200 字问题 → 过滤话术+filtered=True, 不触达 RAG"""

    async def run():
        stub = _StubKnowledge()
        svc = LiveQaService(knowledge=stub)
        for q in ("   ", "", "酒" * 201):
            r = await svc.answer("room-1", q)
            assert r["answer"] == LIVE_QA_FILTERED_ANSWER, q[:10]
            assert r["ragMode"] == "filtered" and r["filtered"] is True
            assert r["confidence"] == 0.0
        # 边界内(1 字 / 200 字)不过滤, 正常走 RAG
        r_ok_min = await svc.answer("room-1", "酒")
        r_ok_max = await svc.answer("room-1", "酒" * 200)
        assert r_ok_min["filtered"] is False
        assert r_ok_max["filtered"] is False
        assert len(stub.rag_calls) == 2

    asyncio.run(run())


# ---------- T4 敏感词过滤 ----------

def test_t4_sensitive_filter():
    """品牌竞品/违规词命中 → 统一话术+filtered=True, 不触达 RAG"""

    async def run():
        stub = _StubKnowledge()
        svc = LiveQaService(knowledge=stub)
        cases = ("竹香酒和茅台哪个好",      # 品牌竞品
                 "这个酒有疗效吗",          # 违规词
                 "是不是假酒啊")            # 风险词
        for q in cases:
            r = await svc.answer("room-1", q)
            assert r["answer"] == LIVE_QA_FILTERED_ANSWER, q
            assert r["ragMode"] == "filtered" and r["filtered"] is True
        assert stub.rag_calls == []
        # 正常品牌问题不误伤
        r_ok = await svc.answer("room-1", "竹香酒是怎么酿造的")
        assert r_ok["filtered"] is False and r_ok["ragMode"] == "direct"

    asyncio.run(run())


# ---------- T5 unsolved 低置信兜底 ----------

def test_t5_unsolved_fallback_and_gap():
    """unsolved → 兜底话术+confidence 0; best-effort 调 record_gap"""

    async def run():
        stub = _StubKnowledge(result={
            "answer": "", "mode": "unsolved",
            "citations": [], "confidence": 0.0,
        })
        svc = LiveQaService(knowledge=stub)
        r = await svc.answer("room-9", "这个问题知识库里没有")
        assert r["answer"] == LIVE_QA_UNSOLVED_ANSWER
        assert r["ragMode"] == "unsolved"
        assert r["confidence"] == 0.0
        assert stub.gap_calls == [("这个问题知识库里没有",
                                   "live_qa:room-9")]

    asyncio.run(run())


def test_t5_synthesized_passthrough():
    """direct/synthesized 原样返回 answer 与 confidence"""

    async def run():
        stub = _StubKnowledge(result={
            "answer": "融合答案：竹笋竹叶古法酿制。",
            "mode": "synthesized",
            "citations": [{"entryId": 1}], "confidence": 0.66,
        })
        svc = LiveQaService(knowledge=stub)
        r = await svc.answer("room-1", "竹香酒工艺")
        assert r["answer"] == "融合答案：竹笋竹叶古法酿制。"
        assert r["ragMode"] == "synthesized"
        assert r["confidence"] == 0.66
        assert stub.gap_calls == []             # 命中不登记缺口

    asyncio.run(run())


# ---------- T6 RAG 异常 fail-soft ----------

def test_t6_rag_exception_failsoft():
    """rag_answer 抛异常 → 兜底话术不外抛, 归一 ragMode=unsolved"""

    async def run():
        stub = _StubKnowledge(error=RuntimeError("redis down"))
        svc = LiveQaService(knowledge=stub)
        r = await svc.answer("room-1", "竹香酒的原料是什么")
        assert r["answer"] == LIVE_QA_UNSOLVED_ANSWER
        assert r["ragMode"] == "unsolved"
        assert r["confidence"] == 0.0

    asyncio.run(run())


# ---------- T7 stats 汇总 ----------

def test_t7_stats_summary():
    """total/unsolved/filtered/cached/unsolvedRate/topQuestions 口径"""

    async def run():
        stub = _StubKnowledge()
        svc = LiveQaService(knowledge=stub)
        # Q1 ×3 → 1 次真实 + 2 次缓存(direct)
        for _ in range(3):
            await svc.answer("room-1", "竹香酒的原料是什么")
        # Q2 敏感词 ×1(filtered)
        await svc.answer("room-1", "和茅台比怎么样")
        # Q3 ×2 → 1 次真实 + 1 次缓存(unsolved)
        stub.result = {"answer": "", "mode": "unsolved",
                       "citations": [], "confidence": 0.0}
        for _ in range(2):
            await svc.answer("room-1", "知识库没有的问题")
        # 其他直播间隔离统计(恢复 direct 桩结果, 再真实调用一次)
        stub.result = {
            "answer": "竹香酒以竹笋、竹茎、竹叶配合富硒山泉水古法酿制。",
            "mode": "direct", "citations": [], "confidence": 0.9,
        }
        await svc.answer("room-2", "竹香酒的原料是什么")

        s = svc.stats()
        assert s["total"] == 7
        assert s["unsolved"] == 2
        assert s["filtered"] == 1
        assert s["cached"] == 3
        assert s["unsolvedRate"] == round(2 / 7, 4)
        # top1 = 高频问(room-1 三次 + room-2 一次 = 4 条记录)
        assert s["topQuestions"][0]["question"] == "竹香酒的原料是什么"
        assert s["topQuestions"][0]["count"] == 4
        assert len(s["topQuestions"]) == 3
        # 按直播间过滤
        s2 = svc.stats(room_id="room-2")
        assert s2["total"] == 1 and s2["cached"] == 0
        assert s2["unsolved"] == 0 and s2["filtered"] == 0

    asyncio.run(run())


# ---------- T8 真实 rag_answer 集成 smoke ----------

def test_t8_real_rag_smoke():
    """真实 KnowledgeService 链路: 空库→unsolved 兜底; 种子发布→direct"""

    async def run():
        from repositories.store import reset_store
        from services.knowledge_service import _reset_knowledge_caches
        reset_store()
        _reset_knowledge_caches()   # 清模块级 RAG 缓存(跨用例隔离)
        svc = LiveQaService()      # 真实 KnowledgeService(内存 store 空构造)
        # 空库: 无候选 → unsolved → 兜底话术 + 真实缺口登记
        r = await svc.answer("room-real", "徂徕山泉水酿造的工艺参数细节")
        assert r["ragMode"] == "unsolved"
        assert r["answer"] == LIVE_QA_UNSOLVED_ANSWER
        # 种子一条 published 知识 → 同问 direct 命中(真实检索链路)
        ks = svc._knowledge
        entry = await ks.create_entry(
            question="竹香酒直播间专属测试问题是什么",
            answer="这是直播弹幕 QA 真实链路冒烟答案。",
            keywords="直播 冒烟", source="manual")
        await ks.review_entry(entry["id"], approve=True, reviewer_id=1)
        await ks.publish_entry(entry["id"], publisher_id=1)
        r2 = await svc.answer("room-real",
                              "竹香酒直播间专属测试问题是什么")
        assert r2["ragMode"] == "direct"
        assert r2["answer"] == "这是直播弹幕 QA 真实链路冒烟答案。"
        assert r2["confidence"] > 0

    asyncio.run(run())


# ---------- T9 main 挂载冒烟 ----------

def test_t9_main_mounted():
    """main.py 挂载后两条路由注册进 FastAPI app(对齐既有 import main 冒烟)"""
    import main
    paths = {getattr(r, "path", None) for r in main.app.routes}
    assert "/api/live-qa/answer" in paths
    assert "/api/live-qa/stats" in paths
