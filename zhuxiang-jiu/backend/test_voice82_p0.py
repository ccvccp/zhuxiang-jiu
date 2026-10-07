"""82号·小竹语音对话逻辑学习进化模型 P0 测试(27 断言)

覆盖: 角色推断/参数白名单(边界+收口)/画像三表构建/EMA/
      清除入口/冷启动红线/未登录边界/零侵入聚合
"""

import uuid

import pytest

from repositories.backend import get_in_memory_store
from repositories.xiaozhu_repository import Xiaozhu48Repository
from services.voice82_dialog_service import (
    COLD_START_SESSIONS, PARAM_DEFAULTS, PARAM_KEYS,
    Voice82DialogService, biz_domain_of, infer_role, params_for,
    profile_key,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_store():
    store = get_in_memory_store()
    for t in ("voice82_role", "voice82_dialog", "voice82_biz",
              Xiaozhu48Repository.TABLE_SESSIONS,
              Xiaozhu48Repository.TABLE_TURNS,
              Xiaozhu48Repository.TABLE_FAILURES):
        store.pop(t, None)
    yield


async def _seed_member(member_id: int, n_sessions: int = 3,
                       intents: list[str] | None = None,
                       tracks: list[str] | None = None):
    """造 48号源数据: sessions + turns + 评分流"""
    repo = Xiaozhu48Repository()
    intents = intents if intents is not None else ["product.new"] * 4
    tracks = tracks if tracks is not None else []
    sids = []
    for s in range(n_sessions):
        sid = f"s-{uuid.uuid4().hex[:8]}"
        store = get_in_memory_store()
        store.setdefault(repo.TABLE_SESSIONS, {})[sid] = {
            "sessionId": sid, "memberId": str(member_id),
            "ts": f"2026-10-0{s % 9 + 1}T10:00:00"}
        sids.append(sid)
    i = 0
    for sid in sids:
        for _ in range(max(1, len(intents) // n_sessions)):
            seq = await repo.next_turn_seq(sid)
            await repo.save_turn({
                "turnId": f"t-{uuid.uuid4().hex[:8]}",
                "sessionId": sid, "seq": seq, "channel": "voice",
                "audioMeta": {}, "rawText": "", "wake": True,
                "intent": intents[i % len(intents)],
                "action": intents[i % len(intents)], "reply": "ok",
                "card": {}, "jump": None, "latencyMs": 1200.0,
                "ts": "2026-10-01T10:00:00", "executed": True})
            i += 1
    n = 0
    for sid in sids:
        for tr in tracks:
            await repo.save_record(repo.TABLE_FAILURES, {
                "caseId": await repo._next_id(repo.TABLE_FAILURES),
                "sessionId": sid, "memberId": str(member_id),
                "rawText": "", "kind": "turn_score", "ts": "x",
                "reward": 0.5, "task": 1, "eff": 0.5, "smooth": 0.5,
                "intent": "chat", "track": tr, "latencyMs": 1000.0})
            n += 1
    return sids


# ============ T01-T03: 键与角色基础 ============

async def test_profile_key():
    assert profile_key(123) == "m123"            # T01
    assert profile_key(None) is None             # T02


async def test_infer_role_visitor():
    role, conf, src = infer_role(None, {}, {})
    assert role == "visitor" and conf == 1.0     # T03
    assert src == ["未登录"]


# ============ T04-T06: 角色推断规则 ============

async def test_infer_role_dealer_by_weight():
    role, conf, _ = infer_role(1, {"agent.policy": 5},
                               {"政策": 0.40})
    assert role == "dealer" and conf >= 0.6      # T04


async def test_infer_role_dealer_by_hits():
    role, _, _ = infer_role(
        1, {"agent.policy": 1, "agent.join": 1, "dealer.ask": 1},
        {})
    assert role == "dealer"                      # T05


async def test_infer_role_member_default():
    role, _, _ = infer_role(1, {"product.new": 9}, {"产品": 0.9})
    assert role == "member"                      # T06


# ============ T07-T09: 业务域映射 ============

async def test_biz_domain():
    assert biz_domain_of("agent.policy.ask") == "政策"   # T07
    assert biz_domain_of("product.new") == "产品"        # T08
    assert biz_domain_of("chat") is None                 # T09


# ============ T10-T12: 红线(off/空/冷启动恒默认) ============

async def test_params_off_mode():
    p = params_for({"role": {"role": "dealer"},
                    "dialog": {"fragmentRate": 0.9},
                    "meta": {"sessions": 99}}, mode="off")
    assert p == PARAM_DEFAULTS                    # T10


async def test_params_none_profile():
    assert params_for(None, mode="shadow") == PARAM_DEFAULTS  # T11


async def test_params_cold_start():
    p = params_for({"role": {}, "dialog": {"repeatRate": 0.9},
                    "meta": {"sessions": COLD_START_SESSIONS - 1}},
                   mode="shadow")
    assert p == PARAM_DEFAULTS                    # T12


# ============ T13-T17: 白名单映射 ============

def _pf(dlg, role="member", sessions=99):
    return params_for({"role": {"role": role},
                       "dialog": dlg,
                       "meta": {"sessions": sessions}}, mode="shadow")


async def test_params_fragment_raises_vad():
    p = _pf({"fragmentRate": 0.4})
    assert p["vad_silence_ms"] > PARAM_DEFAULTS["vad_silence_ms"]  # T13
    assert p["vad_silence_ms"] <= 2000


async def test_params_repeat_lowers_probe():
    p = _pf({"repeatRate": 0.3})
    assert p["probe_depth"] == 0                  # T14
    assert p["confirm_threshold"] == 0.5


async def test_params_interrupt_first():
    assert _pf({"interruptRate": 0.3})["interrupt_first"] == "on"  # T15


async def test_params_brisk_pace():
    assert _pf({"turnsPerMin": 8.0})["tts_pace"] == "brisk"        # T16


async def test_params_dealer_zero_probe():
    assert _pf({}, role="dealer")["probe_depth"] == 0              # T17


# ============ T18-T19: 白名单收口 ============

async def test_params_keyset_closed():
    p = _pf({"fragmentRate": 0.9, "repeatRate": 0.9,
             "interruptRate": 0.9, "turnsPerMin": 9.0})
    assert set(p.keys()) == set(PARAM_KEYS)       # T18


async def test_params_clamp():
    p = _pf({"fragmentRate": 5.0})                # 越界输入被映射封顶
    assert p["vad_silence_ms"] == 1400            # T19(公式封顶 800+600)
    assert p["vad_silence_ms"] <= 2000            # 白名单上界不破


# ============ T20-T24: 零侵入聚合构建 ============

async def test_rebuild_and_get_profile():
    await _seed_member(7, n_sessions=3,
                       intents=["agent.policy", "agent.sample"] * 4,
                       tracks=["fragment", "repeat"])
    r = await Voice82DialogService().rebuild_profile(7)
    assert r["success"] and r["meta"]["sessions"] == 3          # T20
    g = await Voice82DialogService().get_profile(7)
    assert g["success"] and g["profile"]["key"] == "m7"


async def test_rebuild_biz_top_goal():
    await _seed_member(8, intents=["agent.policy"] * 6 + ["product.new"])
    r = await Voice82DialogService().rebuild_profile(8)
    assert r["biz"].get("政策", 0) > r["biz"].get("产品", 0)     # T21
    assert r["role"]["role"] == "dealer"          # T22


async def test_rebuild_dialog_fields():
    await _seed_member(9, tracks=["fragment", "negative"])
    r = await Voice82DialogService().rebuild_profile(9)
    d = r["dialog"]
    for k in ("speechRate", "avgTurnGapSec", "turnsPerMin",
              "fragmentRate", "repeatRate", "interruptRate",
              "avgLatencyMs"):
        assert k in d                              # T23 (7 字段)


async def test_rebuild_ema_smoothing():
    await _seed_member(10, tracks=["fragment"] * 4)
    s1 = await Voice82DialogService().rebuild_profile(10)
    # 第二轮: 清评分流再造零碎片数据 → 新统计=0, EMA 混合旧值
    get_in_memory_store().pop(
        Xiaozhu48Repository.TABLE_FAILURES, None)
    await _seed_member(10, tracks=[])
    s2 = await Voice82DialogService().rebuild_profile(10)
    v1 = s1["dialog"]["fragmentRate"]
    v2 = s2["dialog"]["fragmentRate"]
    assert 0 < v2 < v1                             # T24 EMA 混合非纯新


# ============ T25-T27: 边界与清除 ============

async def test_clear_profile():
    await _seed_member(11)
    await Voice82DialogService().rebuild_profile(11)
    c = await Voice82DialogService().clear_profile(11)
    assert c["success"] and c["cleared"] == 3      # T25
    g = await Voice82DialogService().get_profile(11)
    assert not g["success"]                        # T26


async def test_rebuild_visitor_rejected():
    r = await Voice82DialogService().rebuild_profile(None)
    assert not r["success"]                        # T27


async def test_rebuild_empty_member():
    r = await Voice82DialogService().rebuild_profile(999)
    assert r["success"] and r["meta"]["sessions"] == 0
    assert r["role"]["role"] == "member"           # T28 无数据稳态
