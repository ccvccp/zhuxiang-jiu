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
    T10 L1 小时分桶累计(3 direct+1 unsolved → 桶值/趋势正确)
    T11 L1 连续 3 桶超阈值 → 管理员告警恰好一次(含 topQuestion)
    T12 L1 armed 后仍超阈值不重复告警
    T13 L1 跌回阈值解除 armed → 再连续 3 桶可再告警
    T14 L2 gaps 按 live_qa 来源过滤(频次倒序/含最近时间)
    T15 L2 to-draft 落 knowledge 待审 status=pending 且不 publish
    T16 L3 answer 传参验证(打桩断言 rag_answer 收到 category=live,
       env LIVE_QA_KB_CATEGORY 可配; categoryFallback 透传)
    T17 L3 真实链路(种 live 条目→直播问法优先命中 live 答案;
       live 语料缺失→回退全库泛条目+categoryFallback=True)

打桩说明: T1-T7 以桩对象替换 KnowledgeService(计数 rag_answer /
record_gap 调用), 避免依赖真实知识库内容; T8/T14/T15 走真实链路;
T10-T13 注入假时钟(monkeypatch live_qa_module._now, 与留痕目录
patch 同款方式)并打桩告警发送(_notify_admins_unsolved 计数)。

运行: pytest test_live_qa_service.py
"""
import asyncio
import json
from datetime import datetime

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

    async def rag_answer(self, question, provider="rule", category=None):
        # L3: 签名同步加 category 参数(透传记录三元组供传参断言)
        self.rag_calls.append((question, provider, category))
        if self.error is not None:
            raise self.error
        return dict(self.result)

    async def record_gap(self, question, session_id=""):
        self.gap_calls.append((question, session_id))
        return {"ok": True}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """每用例隔离: 留痕目录重定向 tmp + 模式闸回默认 on
    + 直播知识类名回默认 live + RAG 固定 n-gram 确定性轨
    (不依赖外部 embedding 配置)"""
    monkeypatch.setattr(live_qa_module, "LIVE_QA_LOG_DIR", tmp_path)
    monkeypatch.delenv("LIVE_QA_MODE", raising=False)
    monkeypatch.delenv("LIVE_QA_KB_CATEGORY", raising=False)
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


# ---------- L1/L2 新增辅助(假时钟 + 告警发送桩) ----------

_UNSOLVED_RESULT = {"answer": "", "mode": "unsolved",
                    "citations": [], "confidence": 0.0}


def _set_hour(monkeypatch, dt):
    """注入假时钟(分桶/告警判定走 live_qa_module._now)"""
    monkeypatch.setattr(live_qa_module, "_now", lambda: dt)


class _AlertRecorder:
    """管理员告警发送桩(计数调用并快照告警载荷)"""

    def __init__(self):
        self.calls = []

    async def __call__(self, alert_buckets, top_question):
        self.calls.append({
            "buckets": [b["hour"] for b in alert_buckets],
            "rates": [b["unsolvedRate"] for b in alert_buckets],
            "top": dict(top_question),
        })
        return True


def _unresolved_stub():
    return _StubKnowledge(result=dict(_UNSOLVED_RESULT))


# ---------- T10 L1 小时分桶累计 ----------

def test_t10_hourly_bucket_accumulation(monkeypatch):
    """同小时 3 direct + 1 unsolved → 桶计数/unsolvedRate/趋势正确"""

    async def run():
        _set_hour(monkeypatch, datetime(2026, 10, 16, 9, 30))
        stub = _StubKnowledge()
        svc = LiveQaService(knowledge=stub)
        # 3 次 direct(不同问题避免去重缓存干扰)
        for q in ("竹香酒多少钱", "竹香酒的度数", "竹香酒的产地"):
            await svc.answer("room-1", q)
        # 1 次 unsolved
        stub.result = dict(_UNSOLVED_RESULT)
        await svc.answer("room-1", "完全没见过的问题")
        s = svc.stats()
        assert len(s["hourlyBuckets"]) == 1
        b = s["hourlyBuckets"][0]
        assert b["hour"] == "2026-10-16 09"
        assert b["total"] == 4 and b["unsolved"] == 1
        assert b["filtered"] == 0 and b["cached"] == 0
        assert b["unsolvedRate"] == 0.25
        assert s["unsolvedRateTrend"] == [0.25]

    asyncio.run(run())


# ---------- T11 L1 连续 3 桶超阈值 → 告警恰好一次 ----------

def test_t11_alert_once_after_three_high_buckets(monkeypatch):
    """桶 08/09/10 率均 100% → 翻页后首条记录触发告警一次, 含 topQuestion"""

    async def run():
        recorder = _AlertRecorder()
        monkeypatch.setattr(live_qa_module, "_notify_admins_unsolved",
                            recorder)
        stub = _unresolved_stub()
        svc = LiveQaService(knowledge=stub)
        base = datetime(2026, 10, 16, 8, 5)
        # 桶 08: 同问×2(1 真实+1 缓存)+1 异问 → unsolved 3/3
        _set_hour(monkeypatch, base)
        await svc.answer("room-1", "多少钱一瓶")
        await svc.answer("room-1", "多少钱一瓶")
        await svc.answer("room-1", "配送时效怎么样")
        # 桶 09/10: 各 1 条 unsolved
        _set_hour(monkeypatch, base.replace(hour=9))
        await svc.answer("room-1", "能开发票吗")
        _set_hour(monkeypatch, base.replace(hour=10))
        await svc.answer("room-1", "有礼品装吗")
        assert recorder.calls == []      # 仅 2 个已结束桶, 未达 3 不告警
        # 翻页到 11 点: 08/09/10 成为已结束桶 → 首条记录触发告警
        _set_hour(monkeypatch, base.replace(hour=11))
        await svc.answer("room-1", "度数是多少啊")
        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["buckets"] == ["2026-10-16 08", "2026-10-16 09",
                                   "2026-10-16 10"]
        assert call["rates"] == [1.0, 1.0, 1.0]
        # topQuestion: 「多少钱一瓶」跨桶计数 2 次居首
        assert call["top"]["question"] == "多少钱一瓶"
        assert call["top"]["count"] == 2

    asyncio.run(run())


# ---------- T12 L1 armed 后仍超阈值不重复 ----------

def test_t12_no_repeat_alert_while_armed(monkeypatch):
    """告警后 armed: 后续桶持续超阈值不再重复发送"""

    async def run():
        recorder = _AlertRecorder()
        monkeypatch.setattr(live_qa_module, "_notify_admins_unsolved",
                            recorder)
        stub = _unresolved_stub()
        svc = LiveQaService(knowledge=stub)
        base = datetime(2026, 10, 16, 8, 5)
        for hour in (8, 9, 10):           # 3 个高桶
            _set_hour(monkeypatch, base.replace(hour=hour))
            await svc.answer("room-1", f"未解问题零{hour}")
        _set_hour(monkeypatch, base.replace(hour=11))
        await svc.answer("room-1", "未解问题十一")   # 触发告警
        assert len(recorder.calls) == 1
        # 桶 11/12/13 继续全 unsolved → 仍 armed 不重复
        for hour in (12, 13):
            _set_hour(monkeypatch, base.replace(hour=hour))
            await svc.answer("room-1", f"未解问题{hour}号")
        _set_hour(monkeypatch, base.replace(hour=14))
        await svc.answer("room-1", "未解问题十四号")  # last3=11/12/13
        assert len(recorder.calls) == 1

    asyncio.run(run())


# ---------- T13 L1 跌回解除 armed → 可再告警 ----------

def test_t13_rearm_after_rate_recovers(monkeypatch):
    """低率桶解除 armed(重置连续)→ 再连续 3 高桶可再次告警"""

    async def run():
        recorder = _AlertRecorder()
        monkeypatch.setattr(live_qa_module, "_notify_admins_unsolved",
                            recorder)
        stub = _unresolved_stub()
        svc = LiveQaService(knowledge=stub)
        base = datetime(2026, 10, 16, 8, 5)
        for hour in (8, 9, 10):           # 3 个高桶
            _set_hour(monkeypatch, base.replace(hour=hour))
            await svc.answer("room-1", f"首轮问题{hour}")
        _set_hour(monkeypatch, base.replace(hour=11))
        await svc.answer("room-1", "首轮问题十一")     # 告警#1
        assert len(recorder.calls) == 1
        # 桶 11 补 2 条 direct → 率 1/3 ≈ 0.333 ≤ 0.40(跌回)
        stub.result = _StubKnowledge().result
        _set_hour(monkeypatch, base.replace(hour=11))
        await svc.answer("room-1", "常规问题甲")
        await svc.answer("room-1", "常规问题乙")
        # 桶 12 low(1 direct) → 最近 3 桶含低桶 → 解除 armed
        _set_hour(monkeypatch, base.replace(hour=12))
        await svc.answer("room-1", "常规问题丙")
        assert len(recorder.calls) == 1
        # 桶 13/14/15 重新连续 3 个高桶 → 桶 16 首条记录再告警
        stub.result = dict(_UNSOLVED_RESULT)
        for hour in (13, 14, 15):
            _set_hour(monkeypatch, base.replace(hour=hour))
            await svc.answer("room-1", f"二轮问题{hour}")
        _set_hour(monkeypatch, base.replace(hour=16))
        await svc.answer("room-1", "二轮问题十六")     # 告警#2
        assert len(recorder.calls) == 2
        assert recorder.calls[1]["buckets"] == [
            "2026-10-16 13", "2026-10-16 14", "2026-10-16 15"]

    asyncio.run(run())


# ---------- T14 L2 gaps 按 live_qa 来源过滤 ----------

def test_t14_gaps_filter_live_qa_source():
    """直播缺口列表只含 sessionId 前缀 live_qa: 的缺口, 频次倒序"""

    async def run():
        from repositories.store import reset_store
        reset_store()
        svc = LiveQaService()      # 真实 KnowledgeService
        ks = svc._knowledge
        await ks.record_gap("直播高频问题A", session_id="live_qa:room-1")
        await ks.record_gap("直播高频问题A", session_id="live_qa:room-1")
        await ks.record_gap("直播问题B", session_id="live_qa:room-2")
        await ks.record_gap("客服来源问题C", session_id="chat:abc")
        await ks.record_gap("教学来源问题D", session_id="chat_teach:1")
        gaps = await svc.list_gaps()
        assert len(gaps) == 2                    # 只显 live_qa 来源(A/B)
        assert all(g["sessionId"].startswith("live_qa:") for g in gaps)
        # 频次倒序: 同问累计 2 次居首, 且带最近提问时间
        assert gaps[0]["question"] == "直播高频问题A"
        assert gaps[0]["askCount"] == 2
        assert gaps[0]["lastAskedAt"]
        assert [g["askCount"] for g in gaps] == sorted(
            [g["askCount"] for g in gaps], reverse=True)

    asyncio.run(run())


# ---------- T15 L2 to-draft 落 pending 草稿且不 publish ----------

def test_t15_gap_to_draft_pending_not_published():
    """转草稿: knowledge 待审 status=pending/未 publish; 缺口 resolve 闭环"""

    async def run():
        from repositories.store import reset_store
        from services.knowledge_service import _reset_knowledge_caches
        reset_store()
        _reset_knowledge_caches()
        svc = LiveQaService()
        ks = svc._knowledge
        gap = await ks.record_gap("直播转草稿问题A",
                                  session_id="live_qa:room-9")
        result = await svc.gap_to_draft(gap_id=gap["id"])
        assert result["entryStatus"] == "pending"
        assert result["gapResolved"] is True
        # 草稿在 knowledge 候选池: pending 且从未发布
        entry = await ks.get_entry(result["entryId"])
        assert entry["status"] == "pending"
        assert entry["publishedAt"] == ""
        assert entry["question"] == "直播转草稿问题A"
        assert entry["answer"] == live_qa_module.LIVE_QA_GAP_DRAFT_ANSWER
        # 缺口已 resolve 并关联草稿条目(对齐 teach_submit 闭环范式)
        resolved = [g for g in await ks.list_gaps(status="resolved")
                    if g["id"] == gap["id"]]
        assert resolved and resolved[0]["entryId"] == result["entryId"]
        # 转草稿后不再出现在直播缺口列表(闭环)
        assert await svc.list_gaps() == []
        # 非直播来源缺口拒绝转草稿; question 文本定位亦可
        gap2 = await ks.record_gap("客服来源问题B", session_id="chat:abc")
        with pytest.raises(KeyError):
            await svc.gap_to_draft(gap_id=gap2["id"])
        await ks.record_gap("直播物流几天能到货", session_id="live_qa:room-2")
        r3 = await svc.gap_to_draft(question="直播物流几天能到货")
        assert r3["entryStatus"] == "pending"

    asyncio.run(run())


# ---------- T16 L3 category 传参验证(打桩) ----------

def test_t16_answer_passes_live_category(monkeypatch):
    """answer 调 rag_answer 收到 category=live(env 可配)
    + 打桩结果的 categoryFallback 透传"""

    async def run():
        stub = _StubKnowledge()
        svc = LiveQaService(knowledge=stub)
        r = await svc.answer("room-1", "竹香酒直播间今晚多少钱")
        assert r["ragMode"] == "direct"
        # 默认直播知识类名 live, 位置参数 provider="rule" 照旧
        assert stub.rag_calls == [("竹香酒直播间今晚多少钱",
                                   "rule", "live")]
        # env 重定向直播知识类名
        monkeypatch.setenv("LIVE_QA_KB_CATEGORY", "live_vip")
        stub2 = _StubKnowledge()
        svc2 = LiveQaService(knowledge=stub2)
        await svc2.answer("room-1", "竹香酒直播间今晚多少钱")
        assert stub2.rag_calls == [("竹香酒直播间今晚多少钱",
                                    "rule", "live_vip")]
        # 打桩结果带 categoryFallback → answer 返回体透传
        stub3 = _StubKnowledge(result={
            "answer": "全库回退答案。", "mode": "direct",
            "citations": [], "confidence": 0.8,
            "categoryFallback": True,
        })
        svc3 = LiveQaService(knowledge=stub3)
        r3 = await svc3.answer("room-x", "回退标记透传验证问题")
        assert r3["ragMode"] == "direct"
        assert r3["categoryFallback"] is True

    asyncio.run(run())


# ---------- T17 L3 真实链路: live 语料优先命中 ----------

def test_t17_live_corpus_priority_real_chain():
    """真实检索链路: live 语料缺失→回退全库泛条目+标记 True;
    运营录入 live 条目后→直播问法优先命中 live 答案+标记 False"""

    async def run():
        from repositories.store import reset_store
        from services.knowledge_service import _reset_knowledge_caches
        reset_store()
        _reset_knowledge_caches()   # 清模块级 RAG 缓存(跨用例隔离)
        svc = LiveQaService()      # 真实 KnowledgeService(内存 store)
        ks = svc._knowledge
        # ① live 语料缺失: 全库只有 faq 泛条目 → 回退全库命中
        faq = await ks.create_entry(
            question="竹香酒下单后几天能到货",
            answer="全库口径: 默认快递三天内送达。",
            keywords="到货 物流", category="faq", source="manual")
        await ks.review_entry(faq["id"], approve=True, reviewer_id=1)
        await ks.publish_entry(faq["id"], publisher_id=1)
        r_fallback = await svc.answer(
            "room-live", "竹香酒下单后几天能到货")
        assert r_fallback["ragMode"] == "direct"
        assert r_fallback["answer"] == "全库口径: 默认快递三天内送达。"
        assert r_fallback["categoryFallback"] is True
        # ② 运营按 category="live" 录入直播语料(既有 create_entry,
        #    先审后显链路照旧) → 直播问法优先命中 live 答案
        live = await ks.create_entry(
            question="直播间今晚下单什么时候能送到",
            answer="直播口径: 今晚下单次日内送达。",
            keywords="直播 下单 送达", category="live", source="manual")
        await ks.review_entry(live["id"], approve=True, reviewer_id=1)
        await ks.publish_entry(live["id"], publisher_id=1)
        r_live = await svc.answer(
            "room-live", "直播间今晚下单什么时候能送到")
        assert r_live["ragMode"] == "direct"
        assert r_live["answer"] == "直播口径: 今晚下单次日内送达。"
        assert r_live["categoryFallback"] is False

    asyncio.run(run())
