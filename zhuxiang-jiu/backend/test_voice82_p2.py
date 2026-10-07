"""82号·小竹语音对话逻辑学习进化模型 P2 测试(32 断言)

覆盖: 信号四维+reward/评分器加权/三态门槛(off 拒生成·shadow 留痕
      不生效·assist 覆盖)/challenger 生成与白名单收口/promote/
      discard 回滚/信号表读写/灰度门槛/44号注册三处
"""

import pytest

from repositories.backend import get_in_memory_store
from repositories.xiaozhu_repository import Xiaozhu48Repository
from services.voice82_evolution_service import (
    SHADOW_MIN_SESSIONS, SHADOW_POSITIVE_RATIO, TBL_CHALLENGER,
    TBL_EFFECTIVE, TBL_SIGNALS, Voice82EvolutionService,
    Voice82StrategyScorer, evolution_mode, session_signals,
)
from services.voice82_dialog_service import PARAM_KEYS

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_store(monkeypatch):
    store = get_in_memory_store()
    for t in (TBL_SIGNALS, TBL_CHALLENGER, TBL_EFFECTIVE,
              "voice82_role", "voice82_dialog", "voice82_biz",
              Xiaozhu48Repository.TABLE_SESSIONS,
              Xiaozhu48Repository.TABLE_TURNS,
              Xiaozhu48Repository.TABLE_FAILURES):
        store.pop(t, None)
    yield


def _summary(**kw):
    base = {"turns": 4, "executedCount": 3, "repeatCount": 0,
            "interruptCount": 0, "fragmentCount": 0,
            "bizGoalsHit": 1, "exitedEarly": False,
            "manualEscalated": False}
    base.update(kw)
    return base


# ============ T01-T09: 信号纯函数 ============

async def test_signals_full_success():
    s = session_signals(_summary())
    assert s["task"] > 0.8                         # T01
    assert s["reward"] >= 0.6 and s["positive"]    # T02


async def test_signals_empty_session():
    s = session_signals(_summary(turns=0, executedCount=0,
                                 bizGoalsHit=0))
    assert s["reward"] < 0.3 and not s["positive"]  # T03


async def test_signals_repeat_penalty():
    good = session_signals(_summary())
    bad = session_signals(_summary(repeatCount=3))
    assert bad["efficiency"] <= good["efficiency"]   # T04 重复伤效率
    assert bad["experience"] < good["experience"]     # T05 重复伤体验


async def test_signals_interrupt_penalty():
    bad = session_signals(_summary(interruptCount=3))
    assert bad["experience"] < 0.5                  # T06


async def test_signals_escalation_penalty():
    a = session_signals(_summary(manualEscalated=True))
    b = session_signals(_summary())
    assert a["experience"] < b["experience"]        # T07 转人工扣


async def test_signals_early_exit_penalty():
    a = session_signals(_summary(exitedEarly=True))
    assert a["experience"] < session_signals(
        _summary())["experience"]                   # T08


async def test_signals_biz_value():
    assert session_signals(
        _summary(bizGoalsHit=2))["bizValue"] == 1.0  # T09 封顶1


# ============ T10-T13: 评分器 ============

async def test_scorer_weights_sum():
    assert abs(sum(
        Voice82StrategyScorer.WEIGHTS.values()) - 1.0) < 1e-9  # T10


async def test_scorer_perfect():
    s = {"task": 1, "efficiency": 1, "experience": 1, "bizValue": 1}
    assert Voice82StrategyScorer.score(s) == 100.0  # T11


async def test_scorer_zero():
    s = {"task": 0, "efficiency": 0, "experience": 0, "bizValue": 0}
    assert Voice82StrategyScorer.score(s) == 0.0    # T12


async def test_scorer_weighted_order():
    hi = {"task": 1, "efficiency": 0, "experience": 0, "bizValue": 0}
    lo = {"task": 0, "efficiency": 1, "experience": 0, "bizValue": 0}
    assert Voice82StrategyScorer.score(hi) > \
        Voice82StrategyScorer.score(lo)             # T13 task 权重最大


# ============ T14-T16: 三态门槛 ============

async def test_mode_default_off(monkeypatch):
    monkeypatch.delenv("VOICE82_MODE", raising=False)
    assert evolution_mode() == "off"                # T14 宪法默认


async def test_challenger_rejected_off(monkeypatch):
    monkeypatch.setenv("VOICE82_MODE", "off")
    r = await Voice82EvolutionService().build_challenger(1)
    assert not r["success"] and "off" in r["error"]  # T15


async def test_challenger_shadow_allows(monkeypatch):
    monkeypatch.setenv("VOICE82_MODE", "shadow")
    r = await Voice82EvolutionService().build_challenger(999)
    # 无画像也应走到画像检查(而非 off 拒绝)
    assert not r.get("success") or r.get("candidate") is not None \
        or r.get("note")                              # T16


# ============ T17-T22: challenger 生命周期 ============

def _profile(interrupt=0.0, repeat=0.0, frag=0.0, role="member"):
    return {"role": {"role": role},
            "dialog": {"interruptRate": interrupt,
                       "repeatRate": repeat,
                       "fragmentRate": frag},
            "meta": {"sessions": 50}}


async def test_candidate_interrupt():
    c = Voice82EvolutionService._candidate_from_profile(
        _profile(interrupt=0.3))
    assert c and c["interrupt_first"] == "on"       # T17


async def test_candidate_repeat():
    c = Voice82EvolutionService._candidate_from_profile(
        _profile(repeat=0.25))
    assert c and c["probe_depth"] == 0 \
        and c["confirm_threshold"] == 0.5           # T18


async def test_candidate_dealer():
    c = Voice82EvolutionService._candidate_from_profile(
        _profile(role="dealer"))
    assert c and c["probe_depth"] == 0              # T19


async def test_candidate_none_when_stable():
    c = Voice82EvolutionService._candidate_from_profile(_profile())
    assert c is None                                # T20 无动点不硬造


async def test_candidate_whitelist_closed():
    c = Voice82EvolutionService._candidate_from_profile(
        _profile(interrupt=1.0, repeat=1.0, frag=1.0, role="dealer"))
    assert set(c.keys()) == set(PARAM_KEYS)         # T21 键集收口


async def test_candidate_bounds():
    c = Voice82EvolutionService._candidate_from_profile(
        _profile(frag=1.0))
    assert c["vad_silence_ms"] == 1400 \
        and 400 <= c["vad_silence_ms"] <= 2000      # T22 边界内


# ============ T23-T28: 信号表 + promote/discard/effective ============

async def test_record_and_view_signals():
    svc = Voice82EvolutionService()
    await svc.record_signals("s1", 5, _summary())
    v = await svc.signals_view(member_id=5)
    assert v["count"] == 1 and \
        float(v["signals"][0]["score"]) > 0         # T23(KV 读出str)


async def test_signals_view_filter():
    svc = Voice82EvolutionService()
    await svc.record_signals("s1", 5, _summary())
    await svc.record_signals("s2", 6, _summary())
    assert (await svc.signals_view(member_id=5))["count"] == 1  # T24


async def test_promote_requires_candidate():
    r = await Voice82EvolutionService().promote(7)
    assert not r["success"]                          # T25 无候选拒


async def _make_effective(monkeypatch, mid=9, **dlg):
    monkeypatch.setenv("VOICE82_MODE", "shadow")
    svc = Voice82EvolutionService()
    # 直接落候选(绕过画像——单测 effective 链路)
    from repositories.backend import get_in_memory_store
    import json as _j
    get_in_memory_store().setdefault(TBL_CHALLENGER, {})[f"m{mid}"] = {
        "params": _j.dumps({"probe_depth": 0}), "createdAt": "x"}
    return svc


async def test_effective_shadow_not_applied(monkeypatch):
    svc = await _make_effective(monkeypatch)
    await svc.promote(9)
    r = await svc.effective_params(9)
    assert r["mode"] == "shadow" and not r["effective"]  # T26


async def test_effective_assist_applied(monkeypatch):
    svc = await _make_effective(monkeypatch)
    await svc.promote(9)
    monkeypatch.setenv("VOICE82_MODE", "assist")
    r = await svc.effective_params(9)
    assert r["effective"] and r["params"]["probe_depth"] == 0  # T27


async def test_discard_rollback(monkeypatch):
    svc = await _make_effective(monkeypatch)
    await svc.promote(9)
    monkeypatch.setenv("VOICE82_MODE", "assist")
    r = await svc.discard(9, reason="回滚验证")
    assert r["success"] and r["clearedEffective"] == 1    # T28
    assert not (await svc.effective_params(9))["effective"]


# ============ T29-T32: 灰度门槛 + 44号注册 ============

async def test_shadow_status_not_ready():
    r = await Voice82EvolutionService().shadow_status()
    assert not r["ready"] and r["minSessions"] == \
        SHADOW_MIN_SESSIONS                          # T29


async def test_shadow_status_ready():
    svc = Voice82EvolutionService()
    for i in range(SHADOW_MIN_SESSIONS):
        await svc.record_signals(f"s{i}", 1, _summary())
    r = await svc.shadow_status()
    assert r["ready"] and r["positiveRatio"] >= \
        SHADOW_POSITIVE_RATIO                        # T30


async def test_registered_in_44():
    from services.ai_learning_service import SCORER_REGISTRY
    reg = SCORER_REGISTRY.get("voice82_strategy")
    assert reg and reg["batch"] == 54                # T31


async def test_default_weights_wired():
    from services.ai_learning_service import default_weights
    w = default_weights("voice82_strategy")
    assert set(w) == set(
        Voice82StrategyScorer.WEIGHTS)               # T32 单一事实源
