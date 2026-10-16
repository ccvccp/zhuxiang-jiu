"""82号·小竹语音对话逻辑学习进化模型 P3 接入测试(15 断言)

覆盖: wire_params shadow 基线(零行为变化)/assist 覆盖/白名单
      clamp/fail-soft/非 dict 响应边界/采集器画像同轮刷新/
      保护引擎注册(voice82 模块入册+静态断言+指纹探针)
"""

import uuid

import pytest

from repositories.backend import get_in_memory_store
from repositories.xiaozhu_repository import Xiaozhu48Repository
from services.voice82_evolution_service import (
    PANEL_BASELINE, TBL_CHALLENGER, TBL_EFFECTIVE, TBL_SIGNALS,
    Voice82EvolutionService, wire_params,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_store():
    store = get_in_memory_store()
    for t in (TBL_SIGNALS, TBL_CHALLENGER, TBL_EFFECTIVE,
              "voice82_role", "voice82_dialog", "voice82_biz",
              Xiaozhu48Repository.TABLE_SESSIONS,
              Xiaozhu48Repository.TABLE_TURNS,
              Xiaozhu48Repository.TABLE_FAILURES):
        store.pop(t, None)
    yield


def _mk_effective(mid, params):
    """直接落 effective(绕过画像——单测 wire 覆盖链路)"""
    import json as _j
    get_in_memory_store().setdefault(TBL_EFFECTIVE, {})[f"m{mid}"] = {
        "params": _j.dumps(params), "promotedAt": "x"}


# ============ T01-T03: shadow 基线(零行为变化) ============

async def test_wire_shadow_baseline(monkeypatch):
    monkeypatch.setenv("VOICE82_MODE", "shadow")
    result = {"reply": "ok"}
    p = await wire_params(result, 1)
    assert p["vadSilenceMs"] == PANEL_BASELINE["vad_silence_ms"]  # T01
    assert p["effective"] is False and p["mode"] == "shadow"
    assert result["voice82"] == p                  # T02 注入同一份


async def test_wire_shadow_keeps_candidate(monkeypatch):
    """shadow 有候选: 不生效但候选保留(留痕观测)"""
    import json as _j
    monkeypatch.setenv("VOICE82_MODE", "shadow")
    get_in_memory_store().setdefault(TBL_CHALLENGER, {})["m2"] = {
        "params": _j.dumps({"probe_depth": 0}), "createdAt": "x"}
    p = await wire_params({}, 2)
    assert p["effective"] is False                  # T03 候选不生效
    assert "m2" in get_in_memory_store()[TBL_CHALLENGER]


# ============ T04-T06: assist 覆盖 + clamp ============

async def test_wire_assist_applies(monkeypatch):
    monkeypatch.setenv("VOICE82_MODE", "assist")
    # P4 开闸: assist 注入还需放量内——100=全量
    monkeypatch.setenv("VOICE82_ASSIST_ROLLOUT", "100")
    _mk_effective(3, {"vad_silence_ms": 1400, "probe_depth": 0,
                      "interrupt_first": "on"})
    p = await wire_params({}, 3)
    assert p["effective"] is True                   # T04
    assert p["vadSilenceMs"] == 1400 and \
        p["probeDepth"] == 0 and p["interruptFirst"] == "on"


async def test_wire_clamp_bounds(monkeypatch):
    monkeypatch.setenv("VOICE82_MODE", "assist")
    monkeypatch.setenv("VOICE82_ASSIST_ROLLOUT", "100")
    _mk_effective(4, {"vad_silence_ms": 99999,
                      "probe_depth": 9,
                      "confirm_threshold": 0.05,
                      "tts_pace": "turbo",
                      "interrupt_first": "maybe"})
    p = await wire_params({}, 4)
    assert p["vadSilenceMs"] == 2000 and \
        p["probeDepth"] == 2                       # T05 白名单上界
    assert p["confirmThreshold"] == 0.5 and \
        p["ttsPace"] == "normal" and \
        p["interruptFirst"] == "off"               # T06 非法值回基线


# ============ T07-T09: fail-soft 与边界 ============

async def test_wire_failsoft_none_result(monkeypatch):
    """result 非 dict: 不炸不注入, payload 仍返回"""
    monkeypatch.setenv("VOICE82_MODE", "shadow")
    p = await wire_params(None, 5)
    assert p and p["vadSilenceMs"] == 1200         # T07


async def test_wire_exception_returns_none(monkeypatch):
    async def boom(mid):
        raise RuntimeError("82号 故障模拟")
    monkeypatch.setattr(
        Voice82EvolutionService, "effective_params", boom)
    result = {"reply": "ok"}
    p = await wire_params(result, 6)
    assert p is None and "voice82" not in result   # T08 fail-soft


async def test_wire_visitor_member_none(monkeypatch):
    monkeypatch.setenv("VOICE82_MODE", "shadow")
    p = await wire_params({}, None)
    assert p and p["effective"] is False            # T09 游客不炸


# ============ T10-T11: 采集器画像同轮刷新 ============

async def _seed_old_session(mid):
    repo = Xiaozhu48Repository()
    store = get_in_memory_store()
    sid = f"s-{uuid.uuid4().hex[:8]}"
    store.setdefault(repo.TABLE_SESSIONS, {})[sid] = {
        "sessionId": sid, "memberId": str(mid),
        "status": "active", "ts": "2026-10-01T09:00:00+00:00"}
    seq = await repo.next_turn_seq(sid)
    await repo.save_turn({
        "turnId": f"t-{uuid.uuid4().hex[:8]}", "sessionId": sid,
        "seq": seq, "channel": "voice", "audioMeta": {},
        "rawText": "", "wake": True, "intent": "product.new",
        "action": "product.new", "reply": "ok", "card": {},
        "jump": None, "latencyMs": 100.0,
        "ts": "2026-10-01T10:00:00+00:00", "executed": True})
    return sid


async def test_collect_refreshes_profile():
    await _seed_old_session(31)
    r = await Voice82EvolutionService().collect_ended_sessions()
    assert r["recorded"] == 1 and \
        r["profilesRefreshed"] == 1                 # T10
    prof = get_in_memory_store().get("voice82_role") or {}
    assert "m31" in prof                            # T11 画像落库


# ============ T12-T15: 保护引擎注册 ============

def test_guard_registered():
    from core.module_guard import PROTECTED_MODULES
    cfg = PROTECTED_MODULES.get("voice82")
    assert cfg and "xiaozhu-voice.html" in \
        " ".join(cfg["guard_files"])                # T12
    assert any("test_voice82_p3" in c
               for c in cfg["suite_cmd"])           # T13 套件含 p3


def test_guard_static_asserts_pass():
    from core.module_guard import (
        check_static_asserts, default_repo_root,
    )
    flaws = check_static_asserts("voice82", default_repo_root())
    assert flaws == []                              # T14 静态断言全绿


def test_guard_probe_stable():
    from core.module_guard import (
        PROTECTED_MODULES, _probe,
    )
    a = _probe(PROTECTED_MODULES["voice82"]["param_probe"])
    b = _probe(PROTECTED_MODULES["voice82"]["param_probe"])
    assert a == b and len(a) == 4                    # T15 指纹稳定
