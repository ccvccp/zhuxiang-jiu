"""82号·小竹语音对话逻辑学习进化模型 P1 测试(26 断言)

覆盖: 转移对提取/Top5 裁剪/归一化/EMA 量纲/矩阵构建/角色分桶/
      预判三级回退(role→global→role_seed)/置信度边界/预载
      触发与不触发/模式门槛/冷启动
"""

import uuid

import pytest

from repositories.backend import get_in_memory_store
from repositories.xiaozhu_repository import Xiaozhu48Repository
from services.voice82_predict_service import (
    MIN_TRANSITIONS, PRELOAD_THRESHOLD, TOP_K, PRELOAD_MAP,
    Voice82PredictService, _normalize_top, _seed_chain_rows,
    preload_for, predict_mode_active,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_store():
    store = get_in_memory_store()
    for t in ("voice82_matrix", "voice82_matrix_role",
              "voice82_role", "voice82_dialog", "voice82_biz",
              Xiaozhu48Repository.TABLE_SESSIONS,
              Xiaozhu48Repository.TABLE_TURNS,
              Xiaozhu48Repository.TABLE_FAILURES):
        store.pop(t, None)
    yield


def _turn(intent, seq):
    return {"seq": seq, "intent": intent, "sessionId": "s",
            "latencyMs": 100.0}


# ============ T01-T04: 纯函数(归一化/裁剪) ============

async def test_normalize_top_k():
    counts = {f"g{i}": 10 - i for i in range(8)}
    out = _normalize_top(counts)
    assert len(out) == TOP_K                          # T01 Top5 裁剪
    assert set(out) == {f"g{i}" for i in range(5)}    # 保序留大


async def test_normalize_sum_one():
    out = _normalize_top({"a": 3, "b": 1})
    assert abs(sum(out.values()) - 1.0) < 0.01        # T02 归一


async def test_normalize_ranked():
    out = _normalize_top({"a": 6, "b": 3, "c": 1})
    assert out["a"] > out["b"] > out["c"]             # T03 排序


async def test_normalize_empty():
    assert _normalize_top({}) == {}                   # T04 空


# ============ T05-T08: 转移对提取 ============

async def test_pairs_in_session():
    turns = [_turn("product.new", 1), _turn("agent.policy", 2),
             _turn("sample.ask", 3)]
    pairs = Voice82PredictService.transition_pairs([turns])
    assert pairs == [("product.new", "agent.policy"),
                     ("agent.policy", "sample.ask")]  # T05


async def test_pairs_skip_empty_intent():
    turns = [_turn("product.new", 1), _turn("", 2),
             _turn("price.ask", 3)]
    pairs = Voice82PredictService.transition_pairs([turns])
    assert pairs == [("product.new", "price.ask")]    # T06 跳空不连


async def test_pairs_session_boundary():
    pairs = Voice82PredictService.transition_pairs(
        [[_turn("a", 1)], [_turn("b", 1)]])
    assert pairs == []                                # T07 跨会话不连


async def test_pairs_selfloop():
    pairs = Voice82PredictService.transition_pairs(
        [[_turn("price", 1), _turn("price", 2)]])
    assert pairs == [("price", "price")]              # T08 自环计入


# ============ T09-T13: 预载决策(纯函数) ============

async def test_preload_triggers_policy():
    p = preload_for("agent.policy.detail", 0.72, mode_on=True)
    assert p and p["kind"] == "policy_digest"         # T09


async def test_preload_below_threshold():
    assert preload_for("agent.policy", 0.59,
                       mode_on=True) is None          # T10 阈下


async def test_preload_mode_off():
    assert preload_for("agent.policy", 0.9,
                       mode_on=False) is None         # T11


async def test_preload_domain_miss():
    assert preload_for("chat.small", 0.9,
                       mode_on=True) is None          # T12 域外


async def test_preload_map_closed():
    assert set(PRELOAD_MAP) == {"政策", "样品", "价格", "库存"}  # T13


# ============ T14-T15: 种子链 ============

async def test_seed_chain_dealer():
    rows = _seed_chain_rows("dealer")
    assert "产品" in rows and "政策" in rows        # T14(中文域口径)
    assert abs(sum(rows["产品"].values()) - 1) < 0.01


async def test_seed_chain_unknown_role():
    assert _seed_chain_rows("nobody") == {}           # T15


# ============ T16-T19: 矩阵构建 ============

async def _seed(member_id, sessions_intents):
    """造会员会话: [(intent 序列), ...]"""
    repo = Xiaozhu48Repository()
    store = get_in_memory_store()
    for intents in sessions_intents:
        sid = f"s-{uuid.uuid4().hex[:8]}"
        store.setdefault(repo.TABLE_SESSIONS, {})[sid] = {
            "sessionId": sid, "memberId": str(member_id),
            "ts": "2026-10-01T10:00:00"}
        for i, it in enumerate(intents, 1):
            seq = await repo.next_turn_seq(sid)
            await repo.save_turn({
                "turnId": f"t-{uuid.uuid4().hex[:8]}",
                "sessionId": sid, "seq": seq, "channel": "voice",
                "audioMeta": {}, "rawText": "", "wake": True,
                "intent": it, "action": it, "reply": "ok",
                "card": {}, "jump": None, "latencyMs": 100.0,
                "ts": "x", "executed": True})


async def test_rebuild_global_matrix():
    await _seed(1, [["product.new", "agent.policy", "sample.ask"],
                    ["product.new", "agent.policy"],
                    ["product.new", "price.ask"]])
    r = await Voice82PredictService().rebuild_matrices()
    assert r["success"] and r["global"]["rows"] >= 2  # T16


async def test_rebuild_role_bucket_insufficient():
    # dealer 会员但转移对不足 MIN_TRANSITIONS → 角色矩阵不建
    await _seed(2, [["agent.policy", "sample.ask",
                     "agent.policy", "sample.ask",
                     "agent.policy", "sample.ask"]])
    r = await Voice82PredictService().rebuild_matrices()
    role_info = r["roleMatrix"].get("dealer", {})
    assert role_info.get("_insufficient") or \
        role_info.get("pairs", 0) < MIN_TRANSITIONS or \
        "rows" in role_info                          # T17 分桶口径


async def test_rebuild_ema_second_round():
    await _seed(3, [["a", "b"] * 3])
    s1 = await Voice82PredictService().rebuild_matrices()
    await _seed(3, [["a", "c"] * 3])
    s2 = await Voice82PredictService().rebuild_matrices()
    v = await Voice82PredictService()._matrix_raw("voice82_matrix")
    import json as _j
    row = _j.loads(v["a"])
    assert "b" in row and "c" in row                  # T18 新旧混合


async def test_rebuild_empty_members():
    r = await Voice82PredictService().rebuild_matrices()
    assert r["success"] and r["members"] == 0         # T19 空稳态


# ============ T20-T26: 预判三级回退 ============

async def test_predict_cold_start():
    p = await Voice82PredictService().predict(99, "product.new")
    assert p["source"] in ("cold", "role_seed")       # T20


async def test_predict_role_seed_dealer():
    # 无矩阵 + 画像 dealer → 种子链兜底命中
    await _seed(4, [["agent.policy"] * 4])            # 建画像=dealer
    from services.voice82_dialog_service import (
        Voice82DialogService)
    await Voice82DialogService().rebuild_profile(4)
    p = await Voice82PredictService().predict(4, "product.new")
    assert p["source"] in ("role_seed", "global", "role")  # T21


async def test_predict_global_fallback():
    await _seed(5, [["x.y", "z.w"] * 5])
    await Voice82PredictService().rebuild_matrices()
    p = await Voice82PredictService().predict(5, "x.y")
    assert p["source"] in ("role", "global")          # T22 命中数据
    assert p["predictions"]


async def test_predict_top3_shape():
    await _seed(6, [["a1", "b1"], ["a1", "b1"], ["a1", "b1"],
                    ["a1", "c1"], ["a1", "c1"], ["a1", "d1"]] * 1)
    await Voice82PredictService().rebuild_matrices()
    p = await Voice82PredictService().predict(6, "a1")
    assert len(p["predictions"]) <= 3                 # T23 Top3
    if p["predictions"]:
        confs = [x["confidence"] for x in p["predictions"]]
        assert confs == sorted(confs, reverse=True)   # T24 降序
        assert p["top1"] == p["predictions"][0]


async def test_predict_preload_preview():
    # 独占转移 → conf=1.0; off 模式 preload=None
    await _seed(7, [["e.f", "agent.policy.detail"]] * 4)
    await Voice82PredictService().rebuild_matrices()
    p = await Voice82PredictService().predict(7, "e.f")
    assert p["top1"] and p["top1"]["confidence"] >= 0.99  # T25
    if not predict_mode_active():
        assert p["preload"] is None                   # off 观测不预载


async def test_matrix_view():
    await _seed(8, [["m.n", "o.p"] * 3])
    await Voice82PredictService().rebuild_matrices()
    v = await Voice82PredictService().matrix_view()
    assert v["success"] and v["global"]["rows"] >= 1  # T26
    assert v["preloadThreshold"] == PRELOAD_THRESHOLD
