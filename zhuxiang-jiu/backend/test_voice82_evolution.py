"""82号·P4 开闸测试(assist 按比例放量+开闸决策数据面)

覆盖: 三源优先级(Redis 键>env>默认 0)/set-get 往返+clamp/
      分桶确定性(Knuth 乘法散列可复现)/放量外恒基线+专用留痕
      voice82_rollout_hold/放量内 effective merge/off·shadow
      零变化/冷启动次序/dashboard 数字+advice 四档/两路由
      admin 鉴权+越界 422
"""

import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app
from repositories.backend import get_in_memory_store
from repositories.xiaozhu_repository import Xiaozhu48Repository
from services.voice82_dialog_service import Voice82DialogService
from services.voice82_evolution_service import (
    PANEL_BASELINE, ROLLOUT_REDIS_KEY, TBL_CHALLENGER, TBL_EFFECTIVE,
    TBL_SIGNALS, Voice82EvolutionService, _in_rollout,
    assist_rollout_pct, set_assist_rollout, wire_params,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """env 隔离(放量/模式键)+voice82 表清空"""
    monkeypatch.delenv("VOICE82_MODE", raising=False)
    monkeypatch.delenv("VOICE82_ASSIST_ROLLOUT", raising=False)
    store = get_in_memory_store()
    for t in (TBL_SIGNALS, TBL_CHALLENGER, TBL_EFFECTIVE,
              "voice82_role", "voice82_dialog", "voice82_biz",
              Xiaozhu48Repository.TABLE_SESSIONS,
              Xiaozhu48Repository.TABLE_TURNS,
              Xiaozhu48Repository.TABLE_FAILURES):
        store.pop(t, None)
    yield


class _FakeRC:
    """内存 Redis fake(get/set 运行时键 + hgetall/hset/keys 服务 KV 原语)"""

    def __init__(self):
        self.d = {}
        self.h = {}

    async def get(self, k):
        return self.d.get(k)

    async def set(self, k, v, ex=None):
        self.d[k] = v
        return True

    async def hgetall(self, k):
        return dict(self.h.get(k) or {})

    async def hset(self, k, mapping=None):
        self.h.setdefault(k, {}).update(mapping or {})

    async def delete(self, *keys):
        return sum(1 for k in keys if self.h.pop(k, None) is not None)

    async def keys(self, pattern):
        import fnmatch
        return [k for k in list(self.h) + list(self.d)
                if fnmatch.fnmatch(k, pattern)]


def _stub_redis(monkeypatch) -> _FakeRC:
    """打桩服务模块级 Redis 名(项目内存 fake 形态)"""
    rc = _FakeRC()
    monkeypatch.setattr(
        "services.voice82_evolution_service.is_redis_mode",
        lambda: True)

    async def _fake_client():
        return rc

    monkeypatch.setattr(
        "services.voice82_evolution_service.get_redis_client",
        _fake_client)
    return rc


def _mk_effective(mid, params):
    """直接落 effective(绕过画像——单测覆盖链路)"""
    get_in_memory_store().setdefault(TBL_EFFECTIVE, {})[f"m{mid}"] = {
        "params": json.dumps(params), "promotedAt": "x"}


def _summary(**kw):
    base = {"turns": 4, "executedCount": 3, "repeatCount": 0,
            "interruptCount": 0, "fragmentCount": 0,
            "bizGoalsHit": 1, "exitedEarly": False,
            "manualEscalated": False}
    base.update(kw)
    return base


# ============ ① rollout_pct 三源优先级 ============

async def test_rollout_pct_source_priority(monkeypatch):
    assert await assist_rollout_pct() == 0          # 默认 0(env 未设)
    monkeypatch.setenv("VOICE82_ASSIST_ROLLOUT", "30")
    assert await assist_rollout_pct() == 30         # env 生效
    rc = _stub_redis(monkeypatch)
    rc.d[ROLLOUT_REDIS_KEY] = "55"
    assert await assist_rollout_pct() == 55         # Redis 键 > env
    rc.d[ROLLOUT_REDIS_KEY] = "150"
    assert await assist_rollout_pct() == 100        # 越界 clamp 上界
    rc.d[ROLLOUT_REDIS_KEY] = "abc"
    assert await assist_rollout_pct() == 30         # 键非法回落 env

    async def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        "services.voice82_evolution_service.get_redis_client", boom)
    assert await assist_rollout_pct() == 30         # fail-soft 回落 env


# ============ ② set/get 往返 + clamp ============

async def test_set_rollout_roundtrip_clamp(monkeypatch):
    rc = _stub_redis(monkeypatch)
    p = await set_assist_rollout(42)
    assert p == 42 and rc.d[ROLLOUT_REDIS_KEY] == "42"
    assert await assist_rollout_pct() == 42         # 写读往返
    await set_assist_rollout(-5)
    assert rc.d[ROLLOUT_REDIS_KEY] == "0"           # clamp 下界
    await set_assist_rollout(150)
    assert rc.d[ROLLOUT_REDIS_KEY] == "100"         # clamp 上界
    monkeypatch.setattr(
        "services.voice82_evolution_service.is_redis_mode",
        lambda: False)
    with pytest.raises(ValueError, match="env"):
        await set_assist_rollout(10)                # Redis 不可用→提示 env


# ============ ③ _in_rollout 确定性分桶 ============

def test_in_rollout_deterministic():
    assert _in_rollout(104, 30) == _in_rollout(104, 30)   # 同 id 稳定
    assert all(not _in_rollout(i, 0) for i in range(101, 111))  # 0 全外
    assert all(_in_rollout(i, 100) for i in range(101, 111))    # 100 全内
    assert _in_rollout(None, 50) is False                 # 游客恒外
    # 分桶稳定性: 固定 10 个 id 在 pct=30 的命中集合(硬编码复现)
    hits30 = {i for i in range(101, 111) if _in_rollout(i, 30)}
    assert hits30 == {104, 108}
    hits50 = {i for i in range(101, 111) if _in_rollout(i, 50)}
    assert hits30 < hits50                        # 放量扩大命中只增
    # 单会员桶边界: 3 号桶 28.3 → pct=25 外 / pct=30 内
    assert not _in_rollout(3, 25) and _in_rollout(3, 30)


# ============ ④ assist+放量外: wire 恒基线 + 专用留痕 ============

async def test_wire_rollout_hold_baseline_and_log(monkeypatch, caplog):
    monkeypatch.setenv("VOICE82_MODE", "assist")
    _mk_effective(3, {"probe_depth": 0, "vad_silence_ms": 1400})
    with caplog.at_level(logging.INFO, logger="voice82_evolution"):
        p = await wire_params({}, 3)              # 默认 pct=0 全员放量外
    assert p["effective"] is False
    assert p["vadSilenceMs"] == PANEL_BASELINE["vad_silence_ms"]
    assert p["probeDepth"] == PANEL_BASELINE["probe_depth"]
    holds = [r for r in caplog.records
             if "voice82_rollout_hold" in r.getMessage()]
    assert holds, "放量外应留 voice82_rollout_hold 日志"
    msg = holds[0].getMessage()
    assert "member=3" in msg and "pct=0" in msg \
        and "bucket=28.3" in msg                  # 分桶值+pct 入 key 可复盘


# ============ ⑤ assist+放量内: 走 effective merge ============

async def test_rollout_in_merges_effective(monkeypatch):
    monkeypatch.setenv("VOICE82_MODE", "assist")
    monkeypatch.setenv("VOICE82_ASSIST_ROLLOUT", "30")   # 3 号桶 28.3 命中
    _mk_effective(3, {"vad_silence_ms": 1400, "probe_depth": 0})
    r = await Voice82EvolutionService().effective_params(3)
    assert r["effective"] and r["params"]["vad_silence_ms"] == 1400
    p = await wire_params({}, 3)
    assert p["effective"] is True and p["vadSilenceMs"] == 1400
    # Redis 运行时键放量(env 未设; effective 种子走服务 _hset
    # ——redis 打桩模式下 KV 原语也切换, 直写内存表不可见)
    monkeypatch.delenv("VOICE82_ASSIST_ROLLOUT")
    rc = _stub_redis(monkeypatch)
    rc.d[ROLLOUT_REDIS_KEY] = "30"
    svc2 = Voice82EvolutionService()
    await svc2._hset(TBL_EFFECTIVE, "m3", {
        "params": json.dumps({"vad_silence_ms": 1400}),
        "promotedAt": "x"})
    p2 = await wire_params({}, 3)
    assert p2["effective"] is True


# ============ ⑥ off/shadow 零变化(放量全开也不生效) ============

async def test_off_shadow_unchanged_by_rollout(monkeypatch):
    monkeypatch.setenv("VOICE82_ASSIST_ROLLOUT", "100")
    _mk_effective(3, {"probe_depth": 0})
    monkeypatch.setenv("VOICE82_MODE", "off")
    r = await Voice82EvolutionService().effective_params(3)
    assert not r["effective"] and r["mode"] == "off" \
        and r.get("hold") is None                 # off 路径零变化
    monkeypatch.setenv("VOICE82_MODE", "shadow")
    p = await wire_params({}, 3)
    assert p["effective"] is False and \
        p["vadSilenceMs"] == PANEL_BASELINE["vad_silence_ms"]


# ============ ⑦ 冷启动次序: 冷启动 hold 优先于放量 ============

async def _seed_one_session(mid):
    repo = Xiaozhu48Repository()
    store = get_in_memory_store()
    sid = f"s-{uuid.uuid4().hex[:8]}"
    store.setdefault(repo.TABLE_SESSIONS, {})[sid] = {
        "sessionId": sid, "memberId": str(mid), "status": "closed",
        "ts": "2026-10-01T09:00:00+00:00"}
    seq = await repo.next_turn_seq(sid)
    await repo.save_turn({
        "turnId": f"t-{uuid.uuid4().hex[:8]}", "sessionId": sid,
        "seq": seq, "channel": "voice", "audioMeta": {},
        "rawText": "", "wake": True, "intent": "product.new",
        "action": "product.new", "reply": "ok", "card": {},
        "jump": None, "latencyMs": 100.0,
        "ts": "2026-10-01T10:00:00+00:00", "executed": True})


async def test_cold_start_holds_before_rollout(monkeypatch):
    monkeypatch.setenv("VOICE82_MODE", "assist")
    monkeypatch.setenv("VOICE82_ASSIST_ROLLOUT", "100")  # 即使全量
    await _seed_one_session(77)
    await Voice82DialogService().rebuild_profile(77)  # sessions=1<10
    _mk_effective(77, {"probe_depth": 0})
    r = await Voice82EvolutionService().effective_params(77)
    assert not r["effective"] and r["hold"] == "cold_start"


# ============ ⑧ dashboard 数字 + advice 四档 ============

async def test_dashboard_numbers_and_advice(monkeypatch):
    svc = Voice82EvolutionService()
    await svc.record_signals("s1", 1, _summary())   # ts=现在 → 近7日
    store = get_in_memory_store()
    old = dict(store[TBL_SIGNALS]["s1"])
    old["ts"] = "2026-01-01T00:00:00+00:00"         # >7 日不计 last7d
    store[TBL_SIGNALS]["s-old"] = old
    _mk_effective(9, {"probe_depth": 0})
    store.setdefault(TBL_CHALLENGER, {})["m8"] = {"params": "{}"}

    async def _ready(self):
        return {"success": True, "sessions": 250, "positive": 175,
                "positiveRatio": 0.7, "minSessions": 200,
                "minPositiveRatio": 0.55, "ready": True}

    async def _not_ready(self):
        return {"success": True, "sessions": 42, "positive": 20,
                "positiveRatio": 0.476, "minSessions": 200,
                "minPositiveRatio": 0.55, "ready": False}

    monkeypatch.setattr(Voice82EvolutionService, "shadow_status", _ready)
    monkeypatch.setenv("VOICE82_ASSIST_ROLLOUT", "0")
    d = await svc.rollout_dashboard()
    assert d["success"] and d["rolloutPct"] == 0 and d["mode"] == "off"
    assert d["signals"]["total"] == 2 and d["signals"]["last7d"] == 1
    assert d["signals"]["avg"]["task"] > 0          # 四维均值聚合
    assert d["challenger"] == 1 and d["effectiveCount"] == 1
    assert d["advice"] == "门槛达标, 可从 5% 起步"    # 档②
    monkeypatch.setenv("VOICE82_ASSIST_ROLLOUT", "30")
    assert (await svc.rollout_dashboard())["advice"] == \
        "灰度中 30%, 观察 7 日再放量"                # 档③
    monkeypatch.setenv("VOICE82_ASSIST_ROLLOUT", "100")
    assert (await svc.rollout_dashboard())["advice"] == \
        "全量, 关注 needs_evolution 率"              # 档④
    monkeypatch.setattr(Voice82EvolutionService,
                        "shadow_status", _not_ready)
    monkeypatch.setenv("VOICE82_ASSIST_ROLLOUT", "0")
    assert (await svc.rollout_dashboard())["advice"] == \
        ("影子数据未达标(sessions=42/200, "
         "正反馈=47.6%/55%), 维持 shadow")           # 档①


# ============ ⑨ 路由: admin 鉴权 + 越界 422 + 写键返回刷新值 ============

def test_rollout_routes_admin_guard():
    assert client.get("/api/voice82/rollout").status_code == 403
    assert client.post("/api/voice82/rollout",
                       json={"pct": 5}).status_code == 403


def test_rollout_get_returns_dashboard():
    r = client.get("/api/voice82/rollout",
                   headers={"X-Role": "admin"})
    assert r.status_code == 200
    d = r.json()
    assert d["success"] and "gate" in d and "advice" in d \
        and d["rolloutPct"] == 0                    # 默认未开闸


def test_rollout_post_pct_validation():
    admin = {"X-Role": "admin"}
    for bad in ({"pct": 101}, {"pct": -1}, {"pct": "abc"}, {}):
        r = client.post("/api/voice82/rollout", json=bad,
                        headers=admin)
        assert r.status_code == 422, bad            # 越界/非整数/缺失


def test_rollout_post_sets_and_refreshes(monkeypatch):
    rc = _stub_redis(monkeypatch)
    r = client.post("/api/voice82/rollout", json={"pct": 5},
                    headers={"X-Role": "admin"})
    assert r.status_code == 200
    assert r.json()["rolloutPct"] == 5             # 返回刷新值
    assert rc.d[ROLLOUT_REDIS_KEY] == "5"
    monkeypatch.setattr(
        "services.voice82_evolution_service.is_redis_mode",
        lambda: False)
    r2 = client.post("/api/voice82/rollout", json={"pct": 5},
                     headers={"X-Role": "admin"})
    assert r2.status_code == 409 and \
        "env" in r2.json()["error"]                  # Redis 不可用提示
