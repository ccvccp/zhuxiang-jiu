"""81号·硬件资源智能模型(HRM) P1 专项单测

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_hrm81_p1.py -q

覆盖(81号方案 §四六步对应 + P2 Tier1 §1):
    T1 台账: critical/batch 分层, 接入面 11 项(P1 试点 3+P2 Tier1 8),
       guard 永不让路, Tier2 未接项 gated=False
    T2 水位三档: 各指标判定 + 采集失败 unknown fail-open
    T3 acquire_slot: off/shadow 恒放行 + on 三态 + critical 豁免
    T4 LLM 节流: red 窗口内 chat 返回 None(不触网) + 窗口恢复
    T5 决策引擎: shadow 只留痕 / on+red 动作+留痕 / 熔断回 shadow
    T6 路由门槛: 决策面 off 409 / 观测面无门槛
    T7 接入面零破坏: green/unknown 水位下 11 项闸门调度器全放行
    T8 run_gated: 暂缓跳过本轮不调 fn / 放行调用 / shadow 恒放行
    T9 采样(B1): 同小时去重 / 720 封顶裁最旧
    T10 冷启动(B2): <7 不同日基线 invalid, 判定与静态全等
    T11 只紧不松(B3): 高基线收紧早警 / 低基线永不放松
    T12 kill switch(B4): HRM81_EMA off 零行为差异+emaWould 双记
    T13 磁盘外推(B5): 线性下降→剩余天数 / 不足或平坦→None
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
    # P2 接入面: 试点 3 + Tier1 学习回流 8 + Tier2 结算/雷达 9
    #            + Tier3 治理观测 5
    assert set(hrm.BATCH_GATED) == {
        "knowledge_quality", "ai_learning", "growth80_escrow",
        "ride_learning", "login54_learn", "qr55_learn",
        "aiup56_learn", "kb57_learn", "ii58_learn", "ab63_learn",
        "dm61_learn", "alliance_settle", "voice50_settle",
        "pay60_learn", "av62_learn", "citystore_assessment",
        "promo_radar", "promo_evolution", "blogger_radar",
        "blogger_learning", "security_ueba", "kg51_inspect",
        "us52_alert", "xx66_recon", "xiaozhu_weekly"}
    for m in ("trade_main", "guard_family", "order_timeout",
              "payment_expire"):
        assert hrm.MODULE_REGISTRY[m]["priority"] == "critical"
    for m in hrm.BATCH_GATED:
        assert hrm.MODULE_REGISTRY[m]["priority"] == "batch"
        assert hrm.MODULE_REGISTRY[m].get("gated") is True
    # Tier3 移出登记(动作语义项): gated=False 留 P3 复核
    assert hrm.MODULE_REGISTRY["ai_gov_health"]["gated"] is False
    assert hrm.MODULE_REGISTRY["attract72_health"]["gated"] is False


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
# T7 接入面零破坏
# ============================================================

def test_t7_gated_green_pass():
    """T7: green/unknown 水位下 11 项闸门调度器全放行(零破坏实证)"""

    async def run():
        for level in ("green", "unknown"):
            _set_water(level)
            for m in hrm.BATCH_GATED:
                # on 模式下 green/unknown 均放行
                os.environ["HRM81_MODE"] = "on"
                try:
                    assert await hrm.acquire_slot(m) is True
                finally:
                    os.environ.pop("HRM81_MODE", None)

    asyncio.run(run())


# ============================================================
# T8 run_gated 统一入口(P2 Tier1)
# ============================================================

def test_t8_run_gated(monkeypatch):
    """T8: on+amber 暂缓跳过本轮不调 fn; on+green 调用;
    shadow+red 恒放行(干跑语义)"""
    calls = []

    async def fn():
        calls.append(1)

    async def run():
        # on + green: 调用 fn
        monkeypatch.setenv("HRM81_MODE", "on")
        _set_water("green")
        await hrm.run_gated("dm61_learn", fn)
        assert calls == [1]
        # on + amber: 暂缓, 不调 fn(下轮重试)
        _set_water("amber")
        await hrm.run_gated("dm61_learn", fn)
        assert calls == [1]
        # shadow + red: 恒放行(闸门只在 on 真拦截)
        monkeypatch.setenv("HRM81_MODE", "shadow")
        _set_water("red")
        await hrm.run_gated("dm61_learn", fn)
        assert calls == [1, 1]
        # 未注册 module: 恒放行(最小惊讶)
        monkeypatch.setenv("HRM81_MODE", "on")
        _set_water("red")
        await hrm.run_gated("not_registered_x", fn)
        assert calls == [1, 1, 1]
        # Tier3 移出项(gated=False): amber 也不拦——
        # 闸门只约束显式接入者(amber 演练实证修正)
        _set_water("amber")
        assert await hrm.acquire_slot("ai_gov_health") is True

    asyncio.run(run())


# ============================================================
# T9-T13 阈值 EMA 化(P2 §二: 采样/冷启动/只紧不松/开关/磁盘外推)
# ============================================================

def test_t9_sample_dedup_and_cap():
    """T9(B1): 同小时只记一条; 720 封顶裁最旧"""

    async def run():
        m = {"memAvailableMB": 600, "diskAvailRatio": 0.4, "load1": 0.5}
        assert await hrm.record_water_sample(
            m, "2026-10-01T07:00:00+00:00") is True
        assert await hrm.record_water_sample(
            m, "2026-10-01T07:30:00+00:00") is False   # 同小时去重
        assert await hrm.record_water_sample(
            m, "2026-10-01T08:00:00+00:00") is True
        assert len(await hrm.list_water_samples()) == 2
        # 灌 768 条(32 日×24 时) → 封顶 720
        for d in range(32):
            for h in range(24):
                await hrm.record_water_sample(
                    m, f"2026-09-{d + 1:02d}T{h:02d}:00:00+00:00")
        samples = await hrm.list_water_samples()
        assert len(samples) == hrm.WATER_SAMPLES_KEEP
        # 768-720=48 条被裁 = 前两整天, 最旧剩 09-03
        assert samples[0]["ts"].startswith("2026-09-03")

    asyncio.run(run())


def test_t10_cold_start_static():
    """T10(B2): 桶内 <7 不同日 → 基线 invalid, EMA on 判定与静态全等"""
    samples = [{"ts": f"2026-09-{d:02d}T07:00:00+00:00",
                "memMB": 900, "load1": 0.5} for d in (1, 2, 3)]
    b = hrm._build_ema_baselines(samples)
    assert b[7]["days"] == 3 and b[7]["valid"] is False
    m = {"memAvailableMB": 500, "diskAvailRatio": 0.5,
         "load1": 0.5, "cpuCount": 2}
    lv_off = hrm._judge_level(m, b, False, hour=7)
    lv_on = hrm._judge_level(m, b, True, hour=7)
    assert lv_off[0] == lv_on[0] == "green"     # 500>450 静态 green
    assert lv_on[2]["mem"] == "static"          # 冷启动不参与


def test_t11_tighten_not_loosen():
    """T11(B3): 高基线收紧早警 / 低基线永不放松(自适应陷阱消解)"""
    m = {"memAvailableMB": 500, "diskAvailRatio": 0.5,
         "load1": 0.5, "cpuCount": 2}
    # 高基线 900(≥7 日, EMA 收敛 900): amber 线 max(450, 585)=585
    # → 500 amber 提前预警(静态判 green)
    hi = [{"ts": f"2026-09-{d:02d}T07:00:00+00:00", "memMB": 900}
          for d in range(1, 8)]
    b_hi = hrm._build_ema_baselines(hi)
    assert b_hi[7]["valid"] is True
    assert b_hi[7]["memEMA"] == 900.0
    lv, _, tracks = hrm._judge_level(m, b_hi, True, hour=7)
    assert lv == "amber" and tracks["mem"] == "ema"
    # 低基线 300(慢性紧张): EMA 线 195 < 450 → max 取静态 450
    # → 500 仍 green(只紧不松铁律)
    lo = [{"ts": f"2026-09-{d:02d}T07:00:00+00:00", "memMB": 300}
          for d in range(1, 8)]
    b_lo = hrm._build_ema_baselines(lo)
    lv2, _, tracks2 = hrm._judge_level(m, b_lo, True, hour=7)
    assert lv2 == "green" and tracks2["mem"] == "static"


def test_t12_ema_kill_switch(monkeypatch):
    """T12(B4): HRM81_EMA=off 行为与静态全等 + emaWould 双记;
    on 后基线参与判定"""
    monkeypatch.delenv("HRM81_EMA", raising=False)
    assert hrm.ema_enabled() is False
    from datetime import UTC, datetime
    hour_now = datetime.now(UTC).strftime("%H")
    monkeypatch.setattr(
        hrm, "_collect_host_metrics",
        lambda: {"memAvailableMB": 500, "diskAvailRatio": 0.5,
                 "load1": 0.5, "cpuCount": 2})

    async def run():
        # 灌 7 日高基线(当前小时桶)
        for d in range(1, 8):
            await hrm.record_water_sample(
                {"memAvailableMB": 900, "diskAvailRatio": 0.5,
                 "load1": 0.5},
                f"2026-09-{d:02d}T{hour_now}:00:00+00:00")
        r = await hrm.assess_water_level(refresh=True)
        assert r["level"] == "green"           # off: 静态轨零行为差异
        assert r.get("emaWould") == "amber"     # 双记观察(§2.4)
        assert r["tracks"]["mem"] == "static"
        # kill switch on: 基线参与, 500 < 585 → amber
        monkeypatch.setenv("HRM81_EMA", "on")
        hrm._level_cache.update({"at": 0, "value": None})
        r2 = await hrm.assess_water_level(refresh=True)
        assert r2["level"] == "amber"
        assert r2["tracks"]["mem"] == "ema"
        assert "emaWould" not in r2

    asyncio.run(run())


def test_t13_disk_trend():
    """T13(B5): 4 日线性下降(0.02/日) → 距 20% 线 7.0 天;
    不足 3 日 / 不降 → None"""
    samples = [{"ts": f"2026-09-{d:02d}T07:00:00+00:00",
                "diskRatio": 0.40 - 0.02 * d} for d in range(4)]
    assert hrm._disk_trend_days(samples) == 7.0
    assert hrm._disk_trend_days(samples[:2]) is None
    flat = [{"ts": s["ts"], "diskRatio": 0.40} for s in samples]
    assert hrm._disk_trend_days(flat) is None
