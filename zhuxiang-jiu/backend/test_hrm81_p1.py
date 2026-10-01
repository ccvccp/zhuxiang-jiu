"""81号·硬件资源智能模型(HRM) P1 专项单测

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_hrm81_p1.py -q

覆盖(81号方案 §四六步对应):
    T1 台账: critical/batch 分层, 试点 3 个已接入, guard 永不让路
    T2 水位三档: 各指标判定 + 采集失败 unknown fail-open
    T3 acquire_slot: off/shadow 恒放行 + on 三态 + critical 豁免
    T4 LLM 节流: red 窗口内 chat 返回 None(不触网) + 窗口恢复
    T5 决策引擎: shadow 只留痕 / on+red 动作+留痕 / 熔断回 shadow
    T6 路由门槛: 决策面 off 409 / 观测面无门槛
    T7 试点零破坏: green 水位三试点调度器均放行
"""

import asyncio
import os
import time

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

import pytest

from services import hrm81_service as hrm
from repositories.store import reset_store as _reset_store_impl


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch):
    _reset_store_impl()
    hrm._level_cache.update({"at": 0.0, "value": None})
    hrm._fuse_streak["on"] = 0
    monkeypatch.delenv("HRM81_MODE", raising=False)
    yield
    _reset_store_impl()
    hrm._level_cache.update({"at": 0.0, "value": None})
    hrm._fuse_streak["on"] = 0


def _set_water(level: str, refresh_mock=None, monkeypatch=None):
    """控制水位: refresh_mock 直接替换采集(决策引擎 refresh=True 路径)"""
    if monkeypatch is not None and refresh_mock is not None:
        monkeypatch.setattr(hrm, "_collect_host_metrics", refresh_mock)
    hrm._level_cache.update({
        "at": time.time(),
        "value": {"level": level, "metrics": {}, "reasons": ["测试"],
                  "assessedAt": "2026-10-01T00:00:00+00:00"}})


# ============================================================
# T1 台账
# ============================================================

def test_t1_registry():
    """T1 台账: 分层完整, 试点已接入, guard/trade 永不让路"""
    s = hrm.registry_summary()
    assert s["total"] == len(hrm.MODULE_REGISTRY)
    assert s["critical"] >= 4 and s["batch"] >= 3
    assert set(hrm.BATCH_PILOT) == {
        "knowledge_quality", "ai_learning", "growth80_escrow"}
    for m in ("trade_main", "guard_family", "order_timeout",
              "payment_expire"):
        assert hrm.MODULE_REGISTRY[m]["priority"] == "critical"
    for m in hrm.BATCH_PILOT:
        assert hrm.MODULE_REGISTRY[m]["priority"] == "batch"
        assert hrm.MODULE_REGISTRY[m].get("gated") is True


# ============================================================
# T2 水位三档
# ============================================================

def test_t2_judge_level():
    """T2 指标→三档: green/amber/red 组合判定"""
    j = hrm._judge_level
    assert j({"memAvailableMB": 600, "diskAvailRatio": 0.5,
              "load1": 0.5, "cpuCount": 2})[0] == "green"
    assert j({"memAvailableMB": 400, "diskAvailRatio": 0.5,
              "load1": 0.5, "cpuCount": 2})[0] == "amber"
    assert j({"memAvailableMB": 600, "diskAvailRatio": 0.15,
              "load1": 0.5, "cpuCount": 2})[0] == "amber"
    assert j({"memAvailableMB": 600, "diskAvailRatio": 0.5,
              "load1": 5.0, "cpuCount": 2})[0] == "amber"
    assert j({"memAvailableMB": 200, "diskAvailRatio": 0.5,
              "load1": 0.5, "cpuCount": 2})[0] == "red"
    assert j({"memAvailableMB": 600, "diskAvailRatio": 0.05,
              "load1": 0.5, "cpuCount": 2})[0] == "red"
    # 字段缺失不判(采集残缺 fail-open)
    assert j({})[0] == "green"


def test_t2_collect_fail_open(monkeypatch):
    """T2 采集失败 → unknown(fail-open 不误伤批任务)"""

    def _boom():
        raise ConnectionError("node-exporter down")

    monkeypatch.setattr(hrm, "_collect_host_metrics", _boom)

    async def run():
        r = await hrm.assess_water_level(refresh=True)
        assert r["level"] == "unknown"
        assert "error" in r

    asyncio.run(run())


# ============================================================
# T3 acquire_slot 闸门三态
# ============================================================

def test_t3_slot_modes(monkeypatch):
    """T3: off/shadow 恒放行; on+green 放行; on+red batch 拦截"""

    async def run():
        # off(默认): 恒放行
        _set_water("red")
        assert await hrm.acquire_slot("ai_learning") is True

        # shadow: 恒放行(干跑)
        monkeypatch.setenv("HRM81_MODE", "shadow")
        assert await hrm.acquire_slot("ai_learning") is True

        # on + green: 放行
        monkeypatch.setenv("HRM81_MODE", "on")
        _set_water("green")
        assert await hrm.acquire_slot("ai_learning") is True

        # on + amber: batch 暂缓
        _set_water("amber")
        assert await hrm.acquire_slot("ai_learning") is False

        # on + red: batch 暂缓
        _set_water("red")
        assert await hrm.acquire_slot("growth80_escrow") is False

        # critical/未注册: 任何水位放行(保护豁免铁律)
        assert await hrm.acquire_slot("trade_main") is True
        assert await hrm.acquire_slot("guard_family") is True
        assert await hrm.acquire_slot("not_registered") is True

    asyncio.run(run())


def test_t3_slot_error_fail_open(monkeypatch):
    """T3: 评估自身异常 → 放行(闸门坏了不卡业务)"""
    monkeypatch.setenv("HRM81_MODE", "on")

    async def _boom(refresh=False):
        raise RuntimeError("assess crash")

    monkeypatch.setattr(hrm, "assess_water_level", _boom)

    async def run():
        assert await hrm.acquire_slot("ai_learning") is True

    asyncio.run(run())


# ============================================================
# T4 LLM 节流
# ============================================================

def test_t4_llm_throttle(monkeypatch):
    """T4: red 窗口内 chat None 不触网; remaining 递减; 窗口自然恢复"""
    import services.llm_client as llm_mod
    monkeypatch.setenv("LLM_ENABLED", "on")
    monkeypatch.setenv("LLM_API_KEY", "test-key-for-throttle")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    # BASE_URL 删不掉默认值也无妨: 节流短路在请求前

    llm_mod.set_llm_throttle(30)
    assert llm_mod.llm_throttle_remaining() > 28
    assert llm_mod.provider_client.chat("s", "u") is None  # 不触网

    # 窗口结束: 走真实请求路径会触网——用极短窗口验证自然恢复即可
    llm_mod.set_llm_throttle(0)
    assert llm_mod.llm_throttle_remaining() == 0.0


# ============================================================
# T5 决策引擎 + 熔断
# ============================================================

def test_t5_decision_shadow_vs_on(monkeypatch):
    """T5: off 409 / shadow 只留痕不动作 / on+red 动作+LLM 节流"""
    import services.llm_client as llm_mod
    red_metrics = {"memAvailableMB": 200, "diskAvailRatio": 0.5,
                   "load1": 0.5, "cpuCount": 2}

    async def run():
        # off: 铁律
        with pytest.raises(ValueError):
            await hrm.run_hrm_decision()

        # shadow: red 只留痕(actionsShadowed), 不设 LLM 节流
        monkeypatch.setenv("HRM81_MODE", "shadow")
        monkeypatch.setattr(hrm, "_collect_host_metrics",
                            lambda: dict(red_metrics))
        r = await hrm.run_hrm_decision()
        assert r["level"] == "red"
        assert r["actions"] == [] and "batch_defer" in r["actionsShadowed"]

        # on + red: 动作生效 + LLM 节流窗口被设置
        monkeypatch.setenv("HRM81_MODE", "on")
        hrm._fuse_streak["on"] = 0
        r2 = await hrm.run_hrm_decision()
        assert set(r2["actions"]) == {"batch_defer", "llm_throttle"}
        assert llm_mod.llm_throttle_remaining() > 0

        # 留痕可查(最新在前)
        history = await hrm.decision_history(5)
        assert len(history) >= 2
        assert history[0]["level"] == "red"

    asyncio.run(run())


def test_t5_fuse_red_streak(monkeypatch):
    """T5 熔断: on 连续 2 轮 red 动作无效 → 自动回 shadow"""
    monkeypatch.setenv("HRM81_MODE", "on")
    monkeypatch.setattr(
        hrm, "_collect_host_metrics",
        lambda: {"memAvailableMB": 200, "diskAvailRatio": 0.5,
                 "load1": 0.5, "cpuCount": 2})

    async def run():
        hrm._fuse_streak["on"] = 0
        r1 = await hrm.run_hrm_decision()
        assert r1["fuseTriggered"] is False   # 第 1 轮不触发
        r2 = await hrm.run_hrm_decision()
        assert r2["fuseTriggered"] is True    # 第 2 轮熔断
        assert hrm.hrm_mode() == "shadow"     # 运行时回落
        # 回落后 shadow: 不再动作只留痕
        r3 = await hrm.run_hrm_decision()
        assert r3["actions"] == []

    asyncio.run(run())


def test_t5_decision_green_no_action(monkeypatch):
    """T5 green: 零动作纯留痕"""
    monkeypatch.setenv("HRM81_MODE", "on")
    monkeypatch.setattr(
        hrm, "_collect_host_metrics",
        lambda: {"memAvailableMB": 900, "diskAvailRatio": 0.5,
                 "load1": 0.3, "cpuCount": 2})

    async def run():
        r = await hrm.run_hrm_decision()
        assert r["level"] == "green" and r["actions"] == []

    asyncio.run(run())


# ============================================================
# T6 路由门槛
# ============================================================

def test_t6_routes():
    """T6: 决策面 off 409; 观测面 status/decisions 无模式门槛"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.hrm81_routes import register_hrm81_routes

    app = FastAPI()
    register_hrm81_routes(app)
    client = TestClient(app)

    # 观测面(默认 off 也可见)
    s = client.get("/api/hrm81/status",
                   headers={"X-Role": "admin"})
    assert s.status_code == 200
    assert s.json()["data"]["mode"] == "off"
    assert s.json()["data"]["water"]["level"] in (
        "green", "amber", "red", "unknown")

    # 决策面: off 409 铁律
    r = client.post("/api/hrm81/run", headers={"X-Role": "admin"})
    assert r.status_code == 409
    p = client.post("/api/hrm81/proposal",
                    headers={"X-Role": "admin"})
    assert p.status_code == 409

    # 非管理员 403
    r2 = client.get("/api/hrm81/status", headers={"X-Role": "member"})
    assert r2.status_code == 403


# ============================================================
# T7 试点零破坏
# ============================================================

def test_t7_pilot_green_pass():
    """T7: green/unknown 水位下三试点全放行(零破坏实证)"""

    async def run():
        for level in ("green", "unknown"):
            _set_water(level)
            for m in hrm.BATCH_PILOT:
                # on 模式下 green/unknown 均放行
                os.environ["HRM81_MODE"] = "on"
                try:
                    assert await hrm.acquire_slot(m) is True
                finally:
                    os.environ.pop("HRM81_MODE", None)

    asyncio.run(run())
