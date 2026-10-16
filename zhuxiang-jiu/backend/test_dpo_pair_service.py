# -*- coding: utf-8 -*-
"""DPO 偏好对数据面·最小闭环单测(dpo_pair_service+调度+路由冒烟)

覆盖(P4-10 第一批+第三批, 方案文档第二节+第四节):
    ① S1 配对(rag dual 源: golden×negative 同问成对)
    ② S2 配对(down 轮×同 intent up 轮 ±7 天窗口; 窗口外不成对)
    ③ S4 配对(失败轮×同 intent 高分轮; 低分轮不做 chosen)
    ④ 质量闸(空答/超长/互包含/答案相似度过高/intent 空各拒)
    ⑤ PII 复检(含手机号文本脱敏后入对——防二次泄漏)
    ⑥ 幂等(同数据跑两轮不重复产)
    ⑦ 评审环(approve/reject/flip 三操作+operator 必填+
       approved 前不在导出面)
    ⑧ stats 数字(三源占比/评审转化率)
    ⑨ 调度 off 零副作用 + run_once 产对
    ⑩ 路由 admin 鉴权冒烟(403/200/422/409/404)
    ⑪ fail-soft(S1 抛异常, S2/S4 仍产对)
    ⑫ S3 配对(第三批: 转人工留痕→人工回复 chosen / 无人工回复
       降级 knowledge 命中 / 全无放弃 / 异常 fail-soft 同源隔离)
    ⑬ S5 配对(第三批: resolved+entryId 缺口→entry.answer×
       unsolved 兜底 / 未 resolved 不配 / 同 gap 幂等)
    ⑭ export(第三批: approved 才导 / source·intent 过滤 /
       JSONL 逐行可解析+meta.datasetVersion / 双响应头)

运行:
    python -m pytest test_dpo_pair_service.py -q
"""
import asyncio
import json
import os
import re
from datetime import UTC, datetime, timedelta

# 环境自举(先于 services 导入, 对齐 test_repurchase 形态; 强制内存模式)
os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")

import pytest  # noqa: E402

from services.dpo_pair_service import (  # noqa: E402
    DpoPairService, DPO_PAIR_TABLE, SOURCE_S1, SOURCE_S2, SOURCE_S3,
    SOURCE_S4, SOURCE_S5,
    quality_gate, bigram_similarity, ANSWER_SIM_LINE, TEXT_MAX_LEN)
from services.live_qa_service import LIVE_QA_UNSOLVED_ANSWER  # noqa: E402

from repositories.store import reset_store, _mock_store  # noqa: E402


# ============================================================
# 造数工具(时间戳全 ISO UTC, 确定性)
# ============================================================

def _days_ago(n: float) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).isoformat()


def _turn(session_id: int, seq: int, raw_text: str, reply: str,
          intent: str, feedback: str = None,
          ts: str = None) -> dict:
    """轮次留痕(字段对齐 xiaozhu _save_turn 落库口径)"""
    return {
        "turnId": f"t-{session_id}-{seq}", "sessionId": session_id,
        "seq": seq, "channel": "text", "rawText": raw_text,
        "intent": intent, "reply": reply, "card": {},
        "feedback": feedback,
        "feedbackAt": (ts if feedback else ""),
        "latencyMs": 900.0, "ts": ts or _days_ago(1),
    }


def _failure(case_id: int, session_id: int, raw_text: str, kind: str,
             ts: str = None, **extra) -> dict:
    """失败留痕(字段对齐 evolution record_failure/record_score 落库口径)"""
    rec = {"caseId": case_id, "sessionId": session_id, "memberId": 1,
           "rawText": str(raw_text or "")[:200], "kind": kind,
           "ts": ts or _days_ago(1)}
    rec.update(extra)
    return rec


def _dual(sample_id: int, kind: str, question: str, answer: str,
          label: str, score: float, ts: str = None) -> dict:
    """双师样本(字段对齐 knowledge _dual_record_samples 落库口径)"""
    return {"id": sample_id, "kind": kind, "question": question,
            "answer": answer, "createdAt": ts or _days_ago(1),
            "scores": {"label": label, "totalScore": score,
                       "verdict": ("pass" if kind == "golden"
                                   else "reject"),
                       "constitutionPass": kind == "golden"}}


# ============================================================
# 打桩(store/两数据源仓储全打桩, 形态对齐 test_repurchase_predict)
# ============================================================

class _StubStore:
    """留痕打桩: next_id / save / get / list(纯 dict)"""

    def __init__(self):
        self.tables: dict = {}
        self._seq: dict = {}

    async def next_id(self, entity: str) -> int:
        self._seq[entity] = self._seq.get(entity, 0) + 1
        return self._seq[entity]

    async def save(self, table: str, record_id, record: dict) -> None:
        self.tables.setdefault(table, {})[record_id] = record

    async def get(self, table: str, record_id):
        return self.tables.get(table, {}).get(record_id)

    async def list(self, table: str) -> list:
        return list(self.tables.get(table, {}).values())


class _StubXiaozhuRepo:
    """小竹仓储打桩: scan_turns(轮次留痕)+list_records(失败留痕表)"""

    TABLE_FAILURES = "voice48_failures"

    def __init__(self, turns: list = None, failures: list = None):
        self.turns = turns or []
        self.failures = failures or []

    async def scan_turns(self, limit: int = 10000) -> list:
        return [dict(t) for t in self.turns][:limit]

    async def list_records(self, table: str, field: str = None,
                           value=None, limit: int = 100) -> list:
        return [dict(r) for r in self.failures][:limit]


class _StubKnowledgeRepo:
    """知识库仓储打桩: list_dual_samples(双师样本, S1)+
    list_gaps/get_entry(缺口闭环链, S5)+search_published
    (S3 降级 direct 检索)"""

    def __init__(self, samples: list = None, gaps: list = None,
                 entries: list = None, search_hits: list = None):
        self.samples = samples or []
        self.gaps = gaps or []
        self.entries = {e.get("id"): e for e in (entries or [])}
        self.search_hits = search_hits or []

    async def list_dual_samples(self, kind: str = None,
                                limit: int = 100) -> list:
        rows = [dict(s) for s in self.samples
                if kind is None or s.get("kind") == kind]
        return rows[:limit]

    async def list_gaps(self, status: str = None,
                        limit: int = 100) -> list:
        rows = [dict(g) for g in self.gaps
                if status is None or g.get("status") == status]
        return rows[:limit]

    async def get_entry(self, entry_id: int) -> dict | None:
        entry = self.entries.get(entry_id)
        return dict(entry) if entry else None

    async def search_published(self, query_vec, category: str = None,
                               top_k: int = 5,
                               query_embedding=None) -> list:
        return [tuple(h) for h in self.search_hits][:top_k]


class _StubChatRepo:
    """chat 仓储打桩: list_all_sessions+list_messages(S3 转人工链)"""

    def __init__(self, sessions: list = None, messages: list = None):
        self.sessions = sessions or []
        self.messages = messages or []

    async def list_all_sessions(self, status: str = None,
                                session_type: str = None,
                                limit: int = 100) -> list:
        rows = [dict(s) for s in self.sessions
                if status is None or s.get("status") == status]
        return rows[:limit]

    async def list_messages(self, session_id: str, limit: int = 100,
                            since_message_id: int = None) -> list:
        rows = [dict(m) for m in self.messages
                if m.get("sessionId") == session_id]
        rows.sort(key=lambda m: str(m.get("createdAt") or ""))
        return rows[:limit]


def _svc(turns=None, failures=None, dual_samples=None, gaps=None,
         entries=None, search_hits=None, chat_sessions=None,
         chat_messages=None) -> DpoPairService:
    """打桩服务(store 与数据源仓储全注入)"""
    return DpoPairService(
        store=_StubStore(),
        xiaozhu_repo=_StubXiaozhuRepo(turns, failures),
        knowledge_repo=_StubKnowledgeRepo(
            dual_samples, gaps, entries, search_hits),
        chat_repo=_StubChatRepo(chat_sessions, chat_messages))


# ============================================================
# ① S1 配对(rag dual 源)
# ============================================================

def test_s1_rag_dual_pairing():
    """同问 golden×negative 成对; 仅 golden 无 negative 不成对"""
    svc = _svc(dual_samples=[
        _dual(1, "golden", "竹奕酒多少度",
              "竹奕酒有42度和52度两款，规格均为500ml。", "稳健", 88.0),
        _dual(2, "negative", "竹奕酒多少度",
              "竹奕酒度数大概三十八度左右吧。", "探索", 35.0),
        # 仅 golden 无 negative 的问句 → 不成对
        _dual(3, "golden", "竹香酒的产地是哪里",
              "竹香酒产自山东泰安，取泰山泉水酿造。", "稳健", 90.0),
    ])
    result = asyncio.run(svc.collect_all())
    assert result["success"] is True
    assert result["produced"][SOURCE_S1] == 1
    pairs = asyncio.run(svc.list_pairs())
    assert len(pairs) == 1
    p = pairs[0]
    assert p["source"] == SOURCE_S1
    assert p["question"] == "竹奕酒多少度"
    assert p["chosen"] == "竹奕酒有42度和52度两款，规格均为500ml。"
    assert p["rejected"] == "竹奕酒度数大概三十八度左右吧。"
    assert p["status"] == "pending"          # 评审环常闭: 只产 pending
    assert p["meta"]["winnerLabel"] == "稳健"   # 判别器仅建议
    assert p["meta"]["chosenScore"] == 88.0


# ============================================================
# ② S2 配对(显式反馈 ±7 天窗口)
# ============================================================

def test_s2_feedback_pairing_in_window():
    """down 轮×同 intent 窗口内 up 轮成对(时间最近优先);
    异 intent up 轮不成对"""
    svc = _svc(turns=[
        _turn(1, 1, "给我推荐一款白酒",
              "抱歉，还在学习这款内容。", "product.new",
              feedback="down", ts=_days_ago(1)),
        # 同 intent 窗口内 up 轮(距 down 2 天, 最近)
        _turn(1, 2, "来一瓶竹奕酒",
              "好的，已为您推荐竹奕酒42度经典款。", "product.new",
              feedback="up", ts=_days_ago(3)),
        # 同 intent 窗口内更远 up 轮(距 down 5 天)
        _turn(2, 1, "换一款浓香的",
              "已为您换推荐竹香浓香款。", "product.new",
              feedback="up", ts=_days_ago(6)),
        # 异 intent up 轮(不应入选)
        _turn(3, 1, "查一下我的订单",
              "您最近有一笔订单已发货。", "order.query",
              feedback="up", ts=_days_ago(2)),
    ])
    result = asyncio.run(svc.collect_all())
    assert result["produced"][SOURCE_S2] == 1
    p = asyncio.run(svc.list_pairs())[0]
    assert p["source"] == SOURCE_S2
    assert p["question"] == "给我推荐一款白酒"
    assert p["chosen"] == "好的，已为您推荐竹奕酒42度经典款。"
    assert p["rejected"] == "抱歉，还在学习这款内容。"
    assert p["intent"] == "product.new"
    assert p["meta"]["downTurn"]["sessionId"] == 1
    assert p["meta"]["upTurn"]["seq"] == 2


def test_s2_feedback_pairing_out_of_window():
    """窗口外(>7 天)up 轮不成对"""
    svc = _svc(turns=[
        _turn(1, 1, "给我推荐一款白酒",
              "抱歉，还在学习这款内容。", "product.new",
              feedback="down", ts=_days_ago(1)),
        _turn(2, 1, "来一瓶竹奕酒",
              "好的，已为您推荐竹奕酒42度经典款。", "product.new",
              feedback="up", ts=_days_ago(9)),   # 距 down 8 天, 越窗
    ])
    result = asyncio.run(svc.collect_all())
    assert result["produced"][SOURCE_S2] == 0
    assert asyncio.run(svc.list_pairs()) == []


# ============================================================
# ③ S4 配对(失败挖掘)
# ============================================================

def test_s4_failure_mining_pairing():
    """negative/fallback 失败轮×同 intent 高分轮(reward≥0.6)成对;
    低分轮不做 chosen"""
    turn_r = _turn(1, 1, "有什么优惠活动吗",
                   "唔，没听懂您说的意思。", "product.new",
                   ts=_days_ago(2))
    turn_c = _turn(2, 1, "来一瓶竹奕酒",
                   "好的，已为您推荐竹奕酒42度经典款。", "product.new",
                   ts=_days_ago(2))
    svc = _svc(
        turns=[turn_r, turn_c,
               # 低分轮 intent 匹配也无高分源不成 chosen 供体
               _turn(3, 1, "随便聊聊", "好的呀，想聊点什么？",
                     "product.new", ts=_days_ago(2))],
        failures=[
            _failure(1, 1, "有什么优惠活动吗", "negative",
                     ts=_days_ago(2)),
            # 高分评分流(sessionId+intent+ts 最近定位 turn_c)
            _failure(2, 2, "", "turn_score", ts=turn_c["ts"],
                     reward=0.85, intent="product.new",
                     track="", latencyMs=800.0),
            # 低分评分流(reward 0.3 < 0.6)不做 chosen
            _failure(3, 3, "", "turn_score", ts=_days_ago(2),
                     reward=0.3, intent="product.new",
                     track="", latencyMs=900.0),
        ])
    result = asyncio.run(svc.collect_all())
    assert result["produced"][SOURCE_S4] == 1
    p = asyncio.run(svc.list_pairs())[0]
    assert p["source"] == SOURCE_S4
    assert p["question"] == "有什么优惠活动吗"
    assert p["chosen"] == "好的，已为您推荐竹奕酒42度经典款。"
    assert p["rejected"] == "唔，没听懂您说的意思。"
    assert p["intent"] == "product.new"
    assert p["meta"]["failureKind"] == "negative"
    assert p["meta"]["chosenReward"] == 0.85


# ============================================================
# ④ 质量闸(确定性纯函数直测)
# ============================================================

def test_quality_gate_rules():
    """空答/超长/互包含/答案相似度过高/intent 空各拒; 正常通过"""
    ok_q = "竹奕酒有多少度"
    ok_c = "竹奕酒有42度和52度两款，规格均为500ml。"
    ok_r = "竹奕酒度数大概三十八度左右吧。"
    # 正常通过
    passed, reason = quality_gate(ok_q, ok_c, ok_r, "knowledge_qa")
    assert passed and reason == ""
    # 空答(长度 <5 拒)
    passed, reason = quality_gate(ok_q, "", ok_r, "knowledge_qa")
    assert not passed and "长度越界" in reason
    # 超长(>2000 字拒)
    passed, reason = quality_gate("字" * (TEXT_MAX_LEN + 1), ok_c,
                                  ok_r, "knowledge_qa")
    assert not passed and "长度越界" in reason
    # 两答互包含(rejected 是 chosen 前缀子串)
    passed, reason = quality_gate(
        ok_q, "好的，竹奕酒42度和52度两款500ml装。",
        "竹奕酒42度", "knowledge_qa")
    assert not passed and "互包含" in reason
    # 答案 2-gram 相似度过高(语义同稿无偏好价值——单字差异长句)
    sim_c = ("这款竹奕酒选用优质糯高粱为原料，采用传统固态发酵工艺"
             "酿造，酒体醇厚丰满，口感绵柔甘洌，回味悠长净爽。")
    sim_r = ("这款竹奕酒选用优质粳高粱为原料，采用传统固态发酵工艺"
             "酿造，酒体醇厚丰满，口感绵柔甘洌，回味悠长净爽。")
    assert bigram_similarity(sim_c, sim_r) >= ANSWER_SIM_LINE
    passed, reason = quality_gate(ok_q, sim_c, sim_r, "knowledge_qa")
    assert not passed and "相似度" in reason
    # intent 为空(场景不可比)
    passed, reason = quality_gate(ok_q, ok_c, ok_r, "")
    assert not passed and "intent" in reason


def test_quality_gate_rejects_duplicate_question():
    """重复 question 幂等拒(同 source+question 二次不产)"""
    samples = [
        _dual(1, "golden", "竹奕酒多少度",
              "竹奕酒有42度和52度两款，规格均为500ml。", "稳健", 88.0),
        _dual(2, "negative", "竹奕酒多少度",
              "竹奕酒度数大概三十八度左右吧。", "探索", 35.0),
    ]
    svc = _svc(dual_samples=samples)
    first = asyncio.run(svc.collect_all())
    assert first["produced"][SOURCE_S1] == 1
    # 同数据第二轮: 查表去重, 不重复产
    second = asyncio.run(svc.collect_all())
    assert second["produced"][SOURCE_S1] == 0
    assert len(asyncio.run(svc.list_pairs())) == 1


# ============================================================
# ⑤ PII 复检(脱敏管线复用)
# ============================================================

def test_pii_recheck_masked_before_save():
    """含手机号文本脱敏后入对(防二次泄漏——明文不落 pair)"""
    svc = _svc(dual_samples=[
        _dual(1, "golden", "我的手机号13812345678帮我查订单",
              "已为您查询到最近一笔订单，详情已发送。", "稳健", 88.0),
        _dual(2, "negative", "我的手机号13812345678帮我查订单",
              "呃这个我也不太清楚您说的是什么。", "探索", 30.0),
    ])
    result = asyncio.run(svc.collect_all())
    assert result["produced"][SOURCE_S1] == 1
    p = asyncio.run(svc.list_pairs())[0]
    # 手机号被脱敏(不落明文), 占位符保留语义
    assert "13812345678" not in p["question"]
    assert "*手机号*" in p["question"]
    # 脱敏后仍满足长度闸(≥5 字)
    assert len(p["question"]) >= 5


# ============================================================
# ⑥ 幂等(同数据跑两轮不重复产)——跨源合并验证
# ============================================================

def test_idempotent_two_rounds_all_sources():
    """三源同数据两轮归集: 总量不翻倍"""
    svc = _svc(
        turns=[
            _turn(1, 1, "给我推荐一款白酒",
                  "抱歉，还在学习这款内容。", "product.new",
                  feedback="down", ts=_days_ago(1)),
            _turn(1, 2, "来一瓶竹奕酒",
                  "好的，已为您推荐竹奕酒42度经典款。", "product.new",
                  feedback="up", ts=_days_ago(2)),
        ],
        dual_samples=[
            _dual(1, "golden", "竹奕酒多少度",
                  "竹奕酒有42度和52度两款，规格均为500ml。",
                  "稳健", 88.0),
            _dual(2, "negative", "竹奕酒多少度",
                  "竹奕酒度数大概三十八度左右吧。", "探索", 35.0),
        ])
    r1 = asyncio.run(svc.collect_all())
    assert r1["total"] == 2
    r2 = asyncio.run(svc.collect_all())
    assert r2["total"] == 0
    assert len(asyncio.run(svc.list_pairs())) == 2


# ============================================================
# ⑦ 评审环(approve/reject/flip + operator 必填 + 导出面)
# ============================================================

def _one_pending_pair() -> tuple:
    """造一个 pending pair(打桩服务, 返回 (svc, pair_id))"""
    svc = _svc(dual_samples=[
        _dual(1, "golden", "竹奕酒多少度",
              "竹奕酒有42度和52度两款，规格均为500ml。", "稳健", 88.0),
        _dual(2, "negative", "竹奕酒多少度",
              "竹奕酒度数大概三十八度左右吧。", "探索", 35.0),
    ])
    asyncio.run(svc.collect_all())
    p = asyncio.run(svc.list_pairs())[0]
    return svc, p["pairId"]


def test_review_loop_operations():
    """approve/reject/flip 三操作语义 + operator 必填 + 评审留痕"""
    # --- approve: pending → approved, reviewerId 留痕 ---
    svc, pid = _one_pending_pair()
    assert asyncio.run(svc.list_approved()) == []   # approved 前不在导出面
    rec = asyncio.run(svc.review(pid, "approve", "管理员甲"))
    assert rec["status"] == "approved"
    assert rec["reviewerId"] == "管理员甲"
    assert rec["reviewedAt"]
    approved = asyncio.run(svc.list_approved())
    assert len(approved) == 1 and approved[0]["pairId"] == pid

    # --- reject: pending → rejected ---
    svc2, pid2 = _one_pending_pair()
    rec2 = asyncio.run(svc2.review(pid2, "reject", "管理员乙"))
    assert rec2["status"] == "rejected"
    assert rec2["reviewerId"] == "管理员乙"
    assert asyncio.run(svc2.list_approved()) == []   # rejected 不进导出面

    # --- flip: chosen/rejected 互换, status 不变, 翻转留痕 ---
    svc3, pid3 = _one_pending_pair()
    before = dict(asyncio.run(svc3.list_pairs())[0])   # 快照(防引用联动)
    rec3 = asyncio.run(svc3.review(pid3, "flip", "管理员丙"))
    assert rec3["chosen"] == before["rejected"]
    assert rec3["rejected"] == before["chosen"]
    assert rec3["status"] == "pending"      # flip 不改状态(翻转权在人)
    assert rec3["meta"]["flips"][0]["operator"] == "管理员丙"
    # flip 后再 approve → 最终仍可入导出面
    rec3b = asyncio.run(svc3.review(pid3, "approve", "管理员丙"))
    assert rec3b["status"] == "approved"
    assert len(asyncio.run(svc3.list_approved())) == 1


def test_review_loop_operator_required():
    """operator 必填(service 层 ValueError) + action 非法 + 不存在"""
    svc, pid = _one_pending_pair()
    with pytest.raises(ValueError):
        asyncio.run(svc.review(pid, "approve", "   "))   # 空操作人
    with pytest.raises(ValueError):
        asyncio.run(svc.review(pid, "bogus", "管理员"))   # 非法操作
    with pytest.raises(KeyError):
        asyncio.run(svc.review(99999, "approve", "管理员"))  # 不存在


# ============================================================
# ⑧ stats 数字(三源占比/评审转化率)
# ============================================================

def test_stats_numbers():
    """stats 口径: 总量/三源占比/状态分桶/评审转化率/周增"""
    svc = _svc()
    seen = set()
    # 造 4 对: S1×2, S2×1, S4×1(直入 _save_pair, 绕过数据源)
    rows = [
        (SOURCE_S1, "竹奕酒多少度",
         "竹奕酒有42度和52度两款，规格均为500ml。",
         "竹奕酒度数大概三十八度左右吧。", "knowledge_qa"),
        (SOURCE_S1, "竹香酒的产地是哪里",
         "竹香酒产自山东泰安，取泰山泉水酿造。",
         "这个产地信息我也不确定呢。", "knowledge_qa"),
        (SOURCE_S2, "给我推荐一款白酒",
         "好的，已为您推荐竹奕酒42度经典款。",
         "抱歉，还在学习这款内容。", "product.new"),
        (SOURCE_S4, "有什么优惠活动吗",
         "当前会员日竹奕酒有专属优惠活动。",
         "唔，没听懂您说的意思。", "product.new"),
    ]
    for source, q, c, r, intent in rows:
        assert asyncio.run(svc._save_pair(
            source, q, c, r, intent, track="test",
            meta={"kind": "test"}, seen_keys=seen))
    # 评审 2 对: 1 approve + 1 reject → 转化率 0.5
    pairs = asyncio.run(svc.list_pairs())   # pairId 倒序: 4,3,2,1
    asyncio.run(svc.review(pairs[0]["pairId"], "approve", "管理员"))
    asyncio.run(svc.review(pairs[1]["pairId"], "reject", "管理员"))
    stats = asyncio.run(svc.stats())
    assert stats["total"] == 4
    assert stats["bySource"] == {SOURCE_S1: 2, SOURCE_S2: 1,
                                 SOURCE_S4: 1}
    assert stats["byStatus"] == {"pending": 2, "approved": 1,
                                 "rejected": 1}
    assert stats["reviewRate"] == 0.5     # 1/(1+1)
    # 周增趋势: 近 7 天含今日, 今日 4 条
    today = datetime.now(UTC).date().isoformat()
    today_row = [d for d in stats["last7dDaily"]
                 if d["date"] == today][0]
    assert today_row["count"] == 4
    assert len(stats["last7dDaily"]) == 7   # 无留痕日补零, 恒 7 天
    # intent 分布(knowledge_qa 与 product.new 各 2, 并列第一按
    # intent 名字典序——确定性排序)
    top2 = {row["intent"]: row["count"]
            for row in stats["byIntent"][:2]}
    assert top2 == {"knowledge_qa": 2, "product.new": 2}


# ============================================================
# ⑨ 调度: off 零副作用 + run_once 产对
# ============================================================

def test_scheduler_off_zero_side_effect(monkeypatch):
    """DPO_COLLECT_AUTO 默认 off: start_collect_loop 零行为"""
    reset_store()
    monkeypatch.delenv("DPO_COLLECT_AUTO", raising=False)
    from services import dpo_collect_scheduler as sched
    sched.start_collect_loop()          # off → 直接 return, 零任务
    assert not _mock_store.get("zk_dpo_pairs")   # 无任何留痕


def test_scheduler_run_once_produces_pairs(monkeypatch):
    """run_once 真实链路(内存 store + 造数)产对"""
    reset_store()
    # 现场留痕表造数(S1 rag dual + S2 显式反馈)
    _mock_store["knowledge_dual_samples"] = {
        1: _dual(1, "golden", "竹奕酒多少度",
                 "竹奕酒有42度和52度两款，规格均为500ml。",
                 "稳健", 88.0),
        2: _dual(2, "negative", "竹奕酒多少度",
                 "竹奕酒度数大概三十八度左右吧。", "探索", 35.0),
    }
    _mock_store["voice48_turns"] = {
        (1, 1): _turn(1, 1, "给我推荐一款白酒",
                      "抱歉，还在学习这款内容。", "product.new",
                      feedback="down", ts=_days_ago(1)),
        (1, 2): _turn(1, 2, "来一瓶竹奕酒",
                      "好的，已为您推荐竹奕酒42度经典款。",
                      "product.new", feedback="up", ts=_days_ago(2)),
    }
    from services import dpo_collect_scheduler as sched
    result = asyncio.run(sched.run_once())
    assert result["success"] is True
    assert result["produced"][SOURCE_S1] == 1
    assert result["produced"][SOURCE_S2] == 1
    assert result["total"] == 2
    # 落表核对(zk_dpo_pairs, _ZkStore 惯例键)
    pairs = _mock_store.get("zk_dpo_pairs") or {}
    assert len(pairs) == 2
    assert {p["source"] for p in pairs.values()} == {SOURCE_S1,
                                                     SOURCE_S2}
    reset_store()


# ============================================================
# ⑩ 路由 admin 鉴权冒烟
# ============================================================

def _route_client(monkeypatch, svc) -> "TestClient":
    """裸 FastAPI 挂载 dpo 路由(不经 main 中间件, X-Role 直通)"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import dpo_routes as dr
    monkeypatch.setattr(dr, "_service", svc)
    app = FastAPI()
    dr.register_dpo_routes(app)
    return TestClient(app)


def test_routes_admin_smoke(monkeypatch):
    """三端点鉴权冒烟: 无 X-Role 403 / admin 200 /
    review operator 必填 422 / action 非法 409 / 不存在 404"""
    svc, pid = _one_pending_pair()
    client = _route_client(monkeypatch, svc)

    # 无角色 → 403
    assert client.get("/api/dpo/pairs").status_code == 403
    assert client.get("/api/dpo/stats").status_code == 403

    headers = {"X-Role": "admin"}
    # pairs 列表(status 过滤 pending)
    r = client.get("/api/dpo/pairs", headers=headers,
                   params={"status": "pending"})
    assert r.status_code == 200
    assert r.json()["pagination"]["count"] == 1
    assert r.json()["data"][0]["status"] == "pending"

    # stats 面板
    r = client.get("/api/dpo/stats", headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 1
    assert r.json()["data"]["bySource"][SOURCE_S1] == 1

    # review: operator 必填 → Pydantic 422
    r = client.post(f"/api/dpo/pairs/{pid}/review", headers=headers,
                    json={"action": "approve", "operator": ""})
    assert r.status_code == 422
    r = client.post(f"/api/dpo/pairs/{pid}/review", headers=headers,
                    json={"action": "approve"})
    assert r.status_code == 422

    # review: action 非法 → 409(ValueError 映射)
    r = client.post(f"/api/dpo/pairs/{pid}/review", headers=headers,
                    json={"action": "bogus", "operator": "管理员"})
    assert r.status_code == 409

    # review: 不存在 → 404(KeyError 映射)
    r = client.post("/api/dpo/pairs/99999/review", headers=headers,
                    json={"action": "approve", "operator": "管理员"})
    assert r.status_code == 404

    # review: flip 正常
    r = client.post(f"/api/dpo/pairs/{pid}/review", headers=headers,
                    json={"action": "flip", "operator": "管理员"})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "pending"


# ============================================================
# ⑪ fail-soft(单源异常跳过该源不断整轮)
# ============================================================

def test_fail_soft_single_source_error(monkeypatch):
    """S1 数据源抛异常: 该源记 errors 跳过, S2/S4 仍产对"""
    svc = _svc(
        turns=[
            _turn(1, 1, "给我推荐一款白酒",
                  "抱歉，还在学习这款内容。", "product.new",
                  feedback="down", ts=_days_ago(1)),
            _turn(1, 2, "来一瓶竹奕酒",
                  "好的，已为您推荐竹奕酒42度经典款。", "product.new",
                  feedback="up", ts=_days_ago(2)),
        ],
        dual_samples=[
            _dual(1, "golden", "竹奕酒多少度",
                  "竹奕酒有42度和52度两款，规格均为500ml。",
                  "稳健", 88.0),
        ])
    # 打桩 S1 抛异常(模拟双师样本读取故障)
    async def _boom(seen):
        raise RuntimeError("双师样本读取异常")
    svc.collect_s1 = _boom
    result = asyncio.run(svc.collect_all())
    assert result["success"] is True       # 整轮不失败
    assert result["produced"][SOURCE_S1] == 0
    assert result["produced"][SOURCE_S2] == 1   # S2 正常产对
    assert result["total"] == 1
    assert any("S1" in e for e in result["errors"])
    assert len(asyncio.run(svc.list_pairs())) == 1


# ============================================================
# ⑫ S3 转人工隐式配对(第三批: chat 留痕→隐式负偏好对)
# ============================================================

def _chat_msg(session_id: str, sender: str, content: str,
              created_at: str) -> dict:
    """chat 消息留痕(字段对齐 chat_repository.add_message 落库口径)"""
    return {"sessionId": session_id, "senderType": sender,
            "senderId": 0, "messageType": "text", "content": content,
            "createdAt": created_at}


# 转人工锚点前的典型链: 用户问 → AI 兜底答(3 次未解决触发)
_S3_USER_Q = "竹香酒开瓶之后到底能放几天啊"
_S3_AI_FALLBACK = ("抱歉, 未找到与您问题匹配的答案。如需进一步帮助, "
                   "可回复\"转人工\"联系人工客服。")
_S3_CS_REPLY = ("您好，竹香酒开瓶后建议7天内饮用完毕，"
                "密封冷藏可存放约15天。")


def test_s3_transfer_pairing_with_cs_reply():
    """S3: 转人工留痕(锚点前 user 问+ai 兜底答, 锚点后人工回复)
    → chosen=人工坐席回复 / rejected=触发转人工前的最后自动答"""
    svc = _svc(
        chat_sessions=[
            {"sessionId": "CS1", "status": "human_chatting",
             "customerServiceId": 7},
            # 未转人工会话(customerServiceId 空)不入 S3 面
            {"sessionId": "CS2", "status": "ai_chatting",
             "customerServiceId": None},
        ],
        chat_messages=[
            _chat_msg("CS1", "user", _S3_USER_Q,
                      "2026-10-10T10:00:00"),
            _chat_msg("CS1", "ai", _S3_AI_FALLBACK,
                      "2026-10-10T10:00:01"),
            _chat_msg("CS1", "system", "已为您转接人工客服(工号7), 请稍候。",
                      "2026-10-10T10:00:02"),
            _chat_msg("CS1", "customer_service", _S3_CS_REPLY,
                      "2026-10-10T10:05:00"),
        ])
    result = asyncio.run(svc.collect_all())
    assert result["produced"][SOURCE_S3] == 1
    p = asyncio.run(svc.list_pairs())[0]
    assert p["source"] == SOURCE_S3
    assert p["question"] == _S3_USER_Q
    assert p["chosen"] == _S3_CS_REPLY          # 人工回复为 chosen
    assert p["rejected"] == _S3_AI_FALLBACK     # 最后自动答为 rejected
    assert p["intent"] == "chat_transfer"
    assert p["status"] == "pending"             # 评审环常闭: 只产 pending
    assert p["meta"]["chosenFrom"] == "cs_reply"
    assert p["meta"]["sessionId"] == "CS1"


def test_s3_degrades_to_knowledge_hit():
    """S3 降级链: 无人工回复时 chosen=knowledge direct 命中答案
    (相似度≥噪声线); 检索无命中则放弃不产对"""
    msgs = [
        _chat_msg("CS1", "user", _S3_USER_Q, "2026-10-10T10:00:00"),
        _chat_msg("CS1", "ai", _S3_AI_FALLBACK, "2026-10-10T10:00:01"),
        _chat_msg("CS1", "system", "已为您转接人工客服(工号7), 工单号T1。",
                  "2026-10-10T10:00:02"),
        # 无 customer_service 回复 → 触发降级链
    ]
    sessions = [{"sessionId": "CS1", "status": "ended",
                 "customerServiceId": 7}]
    entry = {"id": 88, "question": "竹香酒开瓶后能放几天",
             "answer": "竹香酒开瓶后建议7天内饮用完毕，风味最佳。",
             "status": "published"}
    # ① knowledge 命中(相似度 0.55 ≥ 噪声线 0.10)→ 降级 chosen
    svc = _svc(chat_sessions=sessions, chat_messages=msgs,
               entries=[entry], search_hits=[(entry, 0.55)])
    result = asyncio.run(svc.collect_all())
    assert result["produced"][SOURCE_S3] == 1
    p = asyncio.run(svc.list_pairs())[0]
    assert p["chosen"] == entry["answer"]
    assert p["meta"]["chosenFrom"] == "knowledge_fallback"
    assert p["meta"]["knowledgeEntryId"] == 88
    assert p["meta"]["knowledgeSimilarity"] == 0.55
    # ② 检索空(无命中)→ 放弃不产对(宁缺毋滥)
    svc2 = _svc(chat_sessions=sessions, chat_messages=msgs,
                search_hits=[])
    r2 = asyncio.run(svc2.collect_all())
    assert r2["produced"][SOURCE_S3] == 0
    assert asyncio.run(svc2.list_pairs()) == []


def test_s3_fail_soft_isolated():
    """S3 数据源抛异常: 该源记 errors 跳过, S1/S2 仍产对(同源隔离)"""
    svc = _svc(
        turns=[
            _turn(1, 1, "给我推荐一款白酒",
                  "抱歉，还在学习这款内容。", "product.new",
                  feedback="down", ts=_days_ago(1)),
            _turn(1, 2, "来一瓶竹奕酒",
                  "好的，已为您推荐竹奕酒42度经典款。", "product.new",
                  feedback="up", ts=_days_ago(2)),
        ],
        dual_samples=[
            _dual(1, "golden", "竹奕酒多少度",
                  "竹奕酒有42度和52度两款，规格均为500ml。",
                  "稳健", 88.0),
            _dual(2, "negative", "竹奕酒多少度",
                  "竹奕酒度数大概三十八度左右吧。", "探索", 35.0),
        ])
    async def _boom(seen):
        raise RuntimeError("chat 仓储读取故障")
    svc.collect_s3 = _boom
    result = asyncio.run(svc.collect_all())
    assert result["success"] is True
    assert result["produced"][SOURCE_S1] == 1
    assert result["produced"][SOURCE_S2] == 1
    assert result["produced"][SOURCE_S3] == 0
    assert result["total"] == 2
    assert any("S3" in e for e in result["errors"])


# ============================================================
# ⑬ S5 对抗评审对(第三批: selfplay A6 缺口 × L2 发布闭环桥)
# ============================================================

def _s5_fixture() -> tuple:
    """S5 造数: resolved+entryId 缺口 × published 知识条目"""
    gap = {"id": 11, "question": "竹香酒开瓶后能放几天",
           "normQuestion": "竹香酒开瓶后能放几天", "sessionId": "",
           "askCount": 3, "status": "resolved", "entryId": 88,
           "createdAt": _days_ago(5), "lastAskedAt": _days_ago(4),
           "resolvedAt": _days_ago(3)}
    entry = {"id": 88, "question": "竹香酒开瓶后能放几天",
             "answer": "竹香酒开瓶后建议7天内饮用完毕，风味最佳。",
             "status": "published", "category": "product"}
    return gap, entry


def test_s5_resolved_gap_pairs():
    """S5: resolved+entryId 缺口 → chosen=entry.answer(published 态)
    × rejected=LIVE_QA_UNSOLVED_ANSWER 兜底话术"""
    gap, entry = _s5_fixture()
    svc = _svc(gaps=[gap], entries=[entry])
    result = asyncio.run(svc.collect_all())
    assert result["produced"][SOURCE_S5] == 1
    p = asyncio.run(svc.list_pairs())[0]
    assert p["source"] == SOURCE_S5
    assert p["question"] == gap["question"]
    assert p["chosen"] == entry["answer"]
    assert p["rejected"] == LIVE_QA_UNSOLVED_ANSWER
    assert p["status"] == "pending"          # 评审环常闭: 只产 pending
    assert p["meta"]["gapId"] == 11
    assert p["meta"]["entryId"] == 88


def test_s5_unresolved_or_unpublished_skip():
    """S5: 未 resolved 缺口不配 / entryId 无值不配 / entry 缺失
    不配 / entry 非 published 态(草稿)不配"""
    open_gap = {"id": 12, "question": "竹香酒的执行标准号是什么",
                "status": "open", "entryId": 0}
    dangling_gap = {"id": 13, "question": "竹香酒出口过哪些国家",
                    "status": "resolved", "entryId": 99}   # entry 缺失
    draft_gap = {"id": 14, "question": "基酒陈酿了几年",
                 "status": "resolved", "entryId": 77}
    draft_entry = {"id": 77, "question": "基酒陈酿了几年",
                   "answer": "基酒陈酿三年。", "status": "pending"}
    svc = _svc(gaps=[open_gap, dangling_gap, draft_gap],
               entries=[draft_entry])
    result = asyncio.run(svc.collect_all())
    assert result["produced"][SOURCE_S5] == 0
    assert asyncio.run(svc.list_pairs()) == []


def test_s5_idempotent_same_gap():
    """S5 幂等: 同 gap 两轮归集只产一次"""
    gap, entry = _s5_fixture()
    svc = _svc(gaps=[gap], entries=[entry])
    r1 = asyncio.run(svc.collect_all())
    assert r1["produced"][SOURCE_S5] == 1
    r2 = asyncio.run(svc.collect_all())
    assert r2["produced"][SOURCE_S5] == 0
    assert len(asyncio.run(svc.list_pairs())) == 1


# ============================================================
# ⑭ export 数据集导出(第三批: 快照面+过滤+JSONL 路由)
# ============================================================

def _export_fixture() -> DpoPairService:
    """导出造数: S1 approved + S2 approved(异 intent) + S4 pending
    + S3 rejected 四态 pair 各一"""
    svc = _svc()
    seen = set()
    rows = [
        (SOURCE_S1, "竹奕酒多少度",
         "竹奕酒有42度和52度两款，规格均为500ml。",
         "竹奕酒度数大概三十八度左右吧。", "knowledge_qa"),
        (SOURCE_S2, "给我推荐一款白酒",
         "好的，已为您推荐竹奕酒42度经典款。",
         "抱歉，还在学习这款内容。", "product.new"),
        (SOURCE_S4, "有什么优惠活动吗",
         "当前会员日竹奕酒有专属优惠活动。",
         "唔，没听懂您说的意思。", "product.new"),
        (SOURCE_S3, _S3_USER_Q, _S3_CS_REPLY, _S3_AI_FALLBACK,
         "chat_transfer"),
    ]
    for source, q, c, r, intent in rows:
        assert asyncio.run(svc._save_pair(
            source, q, c, r, intent, track="test",
            meta={"kind": "test"}, seen_keys=seen))
    pairs = asyncio.run(svc.list_pairs())    # pairId 倒序
    by_source = {p["source"]: p for p in pairs}
    asyncio.run(svc.review(
        by_source[SOURCE_S1]["pairId"], "approve", "管理员甲"))
    asyncio.run(svc.review(
        by_source[SOURCE_S2]["pairId"], "approve", "管理员乙"))
    asyncio.run(svc.review(
        by_source[SOURCE_S3]["pairId"], "reject", "管理员丙"))
    # S4 保持 pending
    return svc


def test_export_approved_only_by_default():
    """export 默认 min_status=approved: pending/rejected 不出现;
    min_status 放宽到 pending 导全量三态快照"""
    svc = _export_fixture()
    rows, version = asyncio.run(svc.export_pairs())
    assert version.startswith("dpo-")
    assert re.match(r"^dpo-\d{8}-\d{6}$", version)
    # 默认 approved 才导: 只 S1/S2 两对 approved(pending S4 与
    # rejected S3 不出现)
    assert [r["source"] for r in rows] == [SOURCE_S1, SOURCE_S2]
    # 导出即快照不打标记: 可重复导出(同数据二次导出条数一致)
    rows_again, _ = asyncio.run(svc.export_pairs())
    assert len(rows_again) == len(rows)
    # min_status=pending → 推进度下限全放: 四对全量
    rows_all, _ = asyncio.run(svc.export_pairs(min_status="pending"))
    assert len(rows_all) == 4
    # min_status=rejected → rejected+approved(已评审面)
    rows_reviewed, _ = asyncio.run(
        svc.export_pairs(min_status="rejected"))
    assert {r["source"] for r in rows_reviewed} == {
        SOURCE_S1, SOURCE_S2, SOURCE_S3}
    # 非法 min_status → ValueError
    with pytest.raises(ValueError):
        asyncio.run(svc.export_pairs(min_status="bogus"))


def test_export_source_intent_filter():
    """export source/intent 过滤: 精确匹配(空=不过滤)"""
    svc = _export_fixture()
    rows, _ = asyncio.run(svc.export_pairs(source=SOURCE_S2))
    assert [r["source"] for r in rows] == [SOURCE_S2]
    rows, _ = asyncio.run(svc.export_pairs(intent="knowledge_qa"))
    assert [r["source"] for r in rows] == [SOURCE_S1]
    # 组合过滤: source=S2 + intent=knowledge_qa → 空集
    rows, _ = asyncio.run(svc.export_pairs(
        source=SOURCE_S2, intent="knowledge_qa"))
    assert rows == []


def test_export_route_jsonl_and_headers(monkeypatch):
    """export 路由: JSONL 逐行可解析 + meta 含 datasetVersion 六
    字段 + 双响应头(X-Dataset-Version/X-Dataset-Count) + 鉴权"""
    svc = _export_fixture()
    client = _route_client(monkeypatch, svc)
    # 非 admin → 403
    assert client.get("/api/dpo/export").status_code == 403
    # 非法 min_status → 409(ValueError 映射)
    r409 = client.get("/api/dpo/export", headers={"X-Role": "admin"},
                      params={"min_status": "bogus"})
    assert r409.status_code == 409
    # 默认导出(approved 面)
    r = client.get("/api/dpo/export", headers={"X-Role": "admin"})
    assert r.status_code == 200
    version = r.headers["X-Dataset-Version"]
    assert version.startswith("dpo-")
    assert re.match(r"^dpo-\d{8}-\d{6}$", version)
    assert r.headers["X-Dataset-Count"] == "2"    # 两对 approved
    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    assert len(lines) == 2
    parsed = [json.loads(ln) for ln in lines]     # 逐行独立可解析
    assert all(set(row) == {"prompt", "chosen", "rejected", "meta"}
               for row in parsed)
    first = parsed[0]
    assert first["prompt"] == "竹奕酒多少度"
    assert first["chosen"] == "竹奕酒有42度和52度两款，规格均为500ml。"
    assert first["rejected"] == "竹奕酒度数大概三十八度左右吧。"
    meta = first["meta"]
    assert set(meta) == {"source", "intent", "track", "pairId",
                         "reviewedAt", "datasetVersion"}
    assert meta["source"] == SOURCE_S1
    assert meta["intent"] == "knowledge_qa"
    assert meta["datasetVersion"] == version      # 版本同步写进每行
    assert meta["reviewedAt"]                     # approved 留评审时间
    # source 过滤经路由 query 生效
    r2 = client.get("/api/dpo/export", headers={"X-Role": "admin"},
                    params={"source": SOURCE_S2})
    assert r2.headers["X-Dataset-Count"] == "1"
    assert json.loads(r2.text.splitlines()[0])["meta"]["source"] \
        == SOURCE_S2
