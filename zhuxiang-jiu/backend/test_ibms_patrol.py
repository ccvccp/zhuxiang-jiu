"""74号·智能后台管理模型(IBMS)——巡检总线+LLM 诊断专项

覆盖(P1 方案 §三 六步路径之单测环):
    T1 分段铁律(IBMS_PATROL_MODE 默认 off → 巡检/诊断 409;
       shadow/on 放行)
    T2 巡检三态判定(mock 六项: PASS/WARN/FAIL 全谱+summary 计数)
    T3 巡检 fail-soft(单项抛异常不阻断其余, 自身记 FAIL)
    T4 shadow 干跑留痕不触达 / on 有非 PASS 才告警触达
    T5 告警闭环格式(notify_ibms_alerts: PASS 过滤/FAIL→critical/
       WARN→warn/signal=ibms)
    T6 diagnose LLM 降级(未配 LLM → fallback 摘要+readOnly 铁律)
    T7 diagnose LLM 结构化(mock chat 返回 JSON → 四键结构)
    T8 路由守卫(观测面无门槛 / 决策面 off 409 / 非 admin 403)

运行: python -m pytest test_ibms_patrol.py -q
(全程不依赖真实 Redis/LLM/26号/66号——mock 确定性)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import services.ibms_patrol_service as ibms
from services.ibms_patrol_service import (
    PATROL_ITEMS, diagnose, patrol_mode, run_patrol,
)


def _fake_items(monkeypatch, states):
    """monkeypatch 巡检清单为确定性假项(states: dict 名→state)"""
    def _factory(state):
        async def fn():
            return {"state": state, "count": 1,
                    "message": f"mock {state}"}
        return fn

    items = [(name, name, _factory(state))
             for name, state in states.items()]
    monkeypatch.setattr(ibms, "PATROL_ITEMS", items)


@pytest.fixture(autouse=True)
def _mode_off(monkeypatch):
    """每用例恢复默认 off 铁律(防 env 串染)"""
    monkeypatch.delenv("IBMS_PATROL_MODE", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    yield


# ---------- T1 分段铁律 ----------

def test_t1_mode_default_off():
    assert patrol_mode() == "off"
    assert patrol_mode("shadow") == "shadow"


@pytest.mark.asyncio
async def test_t1_run_patrol_off_blocked(monkeypatch):
    monkeypatch.setenv("IBMS_PATROL_MODE", "off")
    with pytest.raises(ValueError):
        await run_patrol()
    with pytest.raises(ValueError):
        await diagnose()


# ---------- T2 三态判定 ----------

@pytest.mark.asyncio
async def test_t2_states(monkeypatch):
    _fake_items(monkeypatch, {"a": "PASS", "b": "WARN", "c": "FAIL"})
    report = await run_patrol(mode="shadow", dispatch=False)
    assert report["summary"] == {"total": 3, "fail": 1,
                                 "warn": 1, "pass": 1}
    assert report["mode"] == "shadow"
    states = {r["rule"]: r["state"] for r in report["results"]}
    assert states == {"a": "PASS", "b": "WARN", "c": "FAIL"}


# ---------- T3 fail-soft ----------

@pytest.mark.asyncio
async def test_t3_failsoft(monkeypatch):
    async def boom():
        raise RuntimeError("检查项自身炸了")

    async def ok():
        return {"state": "PASS", "count": 0, "message": "正常"}

    monkeypatch.setattr(ibms, "PATROL_ITEMS", [
        ("boom", "炸的", boom), ("ok", "好的", ok)])
    report = await run_patrol(mode="shadow", dispatch=False)
    states = {r["rule"]: r["state"] for r in report["results"]}
    assert states["boom"] == "FAIL"
    assert states["ok"] == "PASS"
    assert "自身异常" in report["results"][0]["message"]


# ---------- T4 shadow/on 告警语义 ----------

@pytest.mark.asyncio
async def test_t4_shadow_no_dispatch(monkeypatch):
    called = []
    from services.security_alert_service import SecurityAlertService

    async def fake_notify(self, results, force=False):
        called.append(results)
        return {"sent": 0, "failed": 0, "deduped": 0}

    monkeypatch.setattr(SecurityAlertService, "notify_ibms_alerts",
                        fake_notify)
    _fake_items(monkeypatch, {"x": "FAIL"})
    report = await run_patrol(mode="shadow")
    assert called == []            # shadow: 干跑留痕零触达
    assert report["summary"]["fail"] == 1


@pytest.mark.asyncio
async def test_t4_on_dispatch_and_all_pass_silent(monkeypatch):
    called = []
    from services.security_alert_service import SecurityAlertService

    async def fake_notify(self, results, force=False):
        called.append(results)
        return {"sent": 1, "failed": 0, "deduped": 0}

    monkeypatch.setattr(SecurityAlertService, "notify_ibms_alerts",
                        fake_notify)
    # 全 PASS: 不值得发(on 也不触达——防骚扰)
    _fake_items(monkeypatch, {"good": "PASS"})
    report = await run_patrol(mode="on")
    assert called == []
    assert "notified" not in report
    # 有 FAIL: 触达
    _fake_items(monkeypatch, {"bad": "FAIL"})
    report = await run_patrol(mode="on")
    assert len(called) == 1
    assert report["notified"]["sent"] == 1


# ---------- T5 告警格式 ----------

@pytest.mark.asyncio
async def test_t5_alert_format(monkeypatch):
    from services.security_alert_service import SecurityAlertService
    svc = SecurityAlertService()
    captured = {}

    async def fake_dispatch(self, alerts, force, collected_at=None):
        captured["alerts"] = alerts
        return {"sent": 1, "failed": 0, "deduped": 0}

    monkeypatch.setattr(SecurityAlertService, "_dispatch",
                        fake_dispatch)
    await svc.notify_ibms_alerts([
        {"name": "p", "state": "PASS", "message": "过"},
        {"name": "w", "state": "WARN", "rule": "w_rule",
         "message": "警"},
        {"name": "f", "state": "FAIL", "rule": "f_rule",
         "message": "炸"},
    ])
    alerts = captured["alerts"]
    assert len(alerts) == 2           # PASS 被过滤
    assert {a["signal"] for a in alerts} == {"ibms"}
    assert alerts[0]["level"] == "warn"
    assert alerts[1]["level"] == "critical"
    assert alerts[1]["rule"] == "f_rule"


# ---------- T6/T7 diagnose ----------

@pytest.mark.asyncio
async def test_t6_diagnose_fallback(monkeypatch):
    _fake_items(monkeypatch, {"z": "PASS"})
    await run_patrol(mode="shadow", dispatch=False)
    result = await diagnose(mode="shadow")
    assert result["fallback"] is not None
    assert "LLM 不可用" in result["fallback"]["summary"]
    assert result["llm"] is None
    assert result["readOnly"] is True      # 只读铁律显式标注


@pytest.mark.asyncio
async def test_t7_diagnose_llm_structured(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key-llm-on")
    fake_json = ('{"summary": "巡检健康", "rootCauseCandidates": ["无"],'
                 ' "suggestedActions": [{"action": "无需动作",'
                 ' "risk": "low", "via": "无"}], "riskLevel": "low"}')
    import services.llm_client as llmmod
    monkeypatch.setattr(
        llmmod.provider_client, "chat",
        lambda *a, **kw: fake_json)
    _fake_items(monkeypatch, {"z": "WARN"})
    await run_patrol(mode="shadow", dispatch=False)
    result = await diagnose(mode="shadow")
    assert result["llm"] is not None
    assert result["llm"]["riskLevel"] == "low"
    assert result["llm"]["rootCauseCandidates"] == ["无"]
    assert result["fallback"] is None


# ---------- T9 host_health 主机级(74号缺口 1 巡检直采形态) ----------

NODE_FAKE = """
node_filesystem_avail_bytes{mountpoint="/",device="/dev/vda3"} 1.7e10
node_filesystem_size_bytes{mountpoint="/",device="/dev/vda3"} 4.0e10
node_memory_MemAvailable_bytes 5.8e8
node_load1 0.35
node_cpu_seconds_total{cpu="0",mode="idle"} 100
node_cpu_seconds_total{cpu="1",mode="idle"} 100
""".strip()


@pytest.mark.asyncio
async def test_t9_host_health_pass(monkeypatch):
    def fake_open(url, timeout=0):
        class R:
            def read(self):
                return NODE_FAKE.encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    r = await ibms.check_host_health()
    assert r["state"] == "PASS"
    assert "磁盘" in r["message"] and "内存" in r["message"]


@pytest.mark.asyncio
async def test_t9_host_health_disk_warn_and_exporter_down(monkeypatch):
    def boom(url, timeout=0):
        raise OSError("connection refused")

    # 采集器不可达 → FAIL(ibms-monitoring.yml 掉了的自证)
    monkeypatch.setattr("urllib.request.urlopen", boom)
    r = await ibms.check_host_health()
    assert r["state"] == "FAIL"
    assert "不可达" in r["message"]


def test_t9_metric_parser():
    assert ibms._parse_metric(NODE_FAKE, "node_load1") == 0.35
    assert ibms._parse_metric(
        NODE_FAKE, "node_filesystem_avail_bytes", "/") == 1.7e10
    assert ibms._parse_metric(NODE_FAKE, "node_filesystem_avail_bytes",
                              "/other") is None


# ---------- T8 路由守卫 ----------

def test_t8_routes():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.ibms_routes import register_ibms_routes

    app = FastAPI()
    register_ibms_routes(app)
    client = TestClient(app)
    admin = {"X-Role": "admin"}

    # 观测面: off 也可查(看得见永远先于管得着)
    r = client.get("/api/ibms/status", headers=admin)
    assert r.status_code == 200
    assert r.json()["data"]["mode"] == "off"
    assert len(r.json()["data"]["items"]) == len(PATROL_ITEMS)
    r = client.get("/api/ibms/patrol/history", headers=admin)
    assert r.status_code == 200
    # 守卫: 非 admin 403
    assert client.get("/api/ibms/status").status_code == 403
    # 决策面: off 409 铁律
    assert client.post("/api/ibms/patrol/run",
                       headers=admin).status_code == 409
    assert client.post("/api/ibms/diagnose", headers=admin,
                       json={"target": "all"}).status_code == 409
