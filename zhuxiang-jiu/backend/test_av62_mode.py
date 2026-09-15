"""62号·AI智能无形资产估值 大模型三态灰度+护栏+同步桥接 专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_av62_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 恶化暂停留痕 / 基线0 / baseline 覆盖 /
       resume / 未暂停恢复拒绝 / 指标留痕
    5. 同步桥接(本模块特有): legacy_current_mode 与异步一致 /
       override/暂停对同步读取生效 / legacy_forced 暂停期豁免
       (申诉不受开关影响铁律)
    6. 状态序列化往返: 整档 JSON——paused 布尔 / metrics 列表
    7. HTTP 决策面: off 下 7 端点全 409
    8. HTTP 观测面: off 下 registry/model-status/mode 200
    9. HTTP 豁免面: 申诉提交不受门控
    10. HTTP shadow: 放行 + av62Mode 标记
    11. 控制面: GET /mode / override 切档 / 护栏暂停恢复
"""
import asyncio
import json
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.av62_mode_service import (
    Av62ModeService, MODE_VALUES,
    legacy_current_mode, legacy_forced,
    _G,
)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


class _EnvGuard:
    """env 快照守卫(AV62_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("AV62_MODE")
        os.environ.pop("AV62_MODE", None)
        if self._mode:
            os.environ["AV62_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("AV62_MODE", None)
        else:
            os.environ["AV62_MODE"] = self._backup


def reset_mode_state():
    """清底层存储 + 进程级快照"""
    from repositories.store import reset_store
    reset_store()
    _G.update({"loaded": False, "override": "",
               "paused": False, "force": None})


async def run_service():
    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():                       # 默认 off
        reset_mode_state()
        svc = Av62ModeService()
        m = await svc.current_mode()
        record("三态: 默认 off(env 源)", m["mode"] == "off"
               and m["source"] == "env")
        try:
            await svc.require_decision_mode()
            record("三态: off 决策面拒绝", False)
        except ValueError as e:
            record("三态: off 决策面拒绝",
                   "AV62_MODE=off" in str(e))
        record("桥接: 默认同步=异步(off)",
               legacy_current_mode() == "off")
    with _EnvGuard("shadow"):
        reset_mode_state()
        svc = Av62ModeService()
        m = await svc.current_mode()
        record("三态: env shadow", m["mode"] == "shadow")
        record("桥接: shadow 同步一致",
               legacy_current_mode() == "shadow")
        m = await svc.require_decision_mode()
        record("三态: shadow 决策面放行", m["mode"] == "shadow")

    # ============================================================
    # 2. 读取链 + override + 同步桥接
    # ============================================================
    with _EnvGuard("assist"):
        reset_mode_state()
        svc = Av62ModeService()
        m = await svc.set_override("shadow", operator="tester")
        record("override: 设值优先 env", m["mode"] == "shadow"
               and m["source"] == "runtime_override")
        record("桥接: override 同步可见",
               legacy_current_mode() == "shadow")
        m = await svc.set_override("", operator="tester")
        record("override: 清除回落 env", m["mode"] == "assist"
               and m["source"] == "env")
        record("桥接: 清除后同步回落",
               legacy_current_mode() == "assist")
        try:
            await svc.set_override("bad", operator="tester")
            record("override: 非法值拒绝", False)
        except ValueError:
            record("override: 非法值拒绝", True)
        # 暂停最优先: override=assist 下触发护栏
        await svc.set_override("assist", operator="tester")
        g = await svc.guard_check(0.05, 0.10, 0.05)  # 翻案率 0.05→0.05 持平; 压测 0.10 持平
        record("护栏: 基线持平不暂停", g["breached"] is False)
        g = await svc.guard_check(0.20, 0.10, 0.05)  # 翻案率 +300%
        m = await svc.current_mode()
        record("读取链: 暂停 > override", g["breached"] is True
               and m["mode"] == "off" and m["source"] == "guard_pause"
               and m["override"] == "assist")
        record("桥接: 暂停同步可见(off)",
               legacy_current_mode() == "off")
        # 申诉强制豁免(暂停期间仍可重估——铁律)
        with legacy_forced("shadow"):
            record("桥接: 强制态暂停期豁免",
                   legacy_current_mode() == "shadow")
        record("桥接: 强制态退出还原",
               legacy_current_mode() == "off")
        m = await svc.resume(operator="tester", note="恢复验证")
        record("护栏: resume 人工恢复", m["mode"] == "assist"
               and m["source"] == "runtime_override")
        try:
            await svc.resume(operator="tester")
            record("护栏: 未暂停恢复拒绝", False)
        except ValueError:
            record("护栏: 未暂停恢复拒绝", True)

    # ============================================================
    # 3. 护栏指标语义
    # ============================================================
    with _EnvGuard("assist"):
        reset_mode_state()
        svc = Av62ModeService()
        g = await svc.guard_check(0.05, 0.10, 0.05)
        record("护栏: 基线持平不暂停", g["breached"] is False
               and g["pausedNow"] is False)
        g = await svc.guard_check(0.05, 0.30, 0.05)  # 压测 0.10→0.30
        record("护栏: 恶化>3% 自动暂停", g["breached"] is True
               and g["pausedNow"] is True)
        st = await svc._state()
        record("护栏: 暂停留痕", "护栏自动暂停"
               in st["pausedReason"]
               and len(st["breachTrail"]) == 1)
        record("护栏: 指标留痕计数", len(st["metrics"]) == 2)
        await svc.resume(operator="t")
        g = await svc.guard_check(
            0.01, 0.0, 0.0,
            baseline={"appealOverturnRate": 0.0,
                      "stressFailRate": 0.0,
                      "fairnessAnomalyRate": 0.0})
        record("护栏: 基线0绝对恶化", g["breached"] is True)
        await svc.resume(operator="t")
        g = await svc.guard_check(
            0.30, 0.10, 0.05,
            baseline={"appealOverturnRate": 0.30})
        record("护栏: baseline 覆盖生效", g["breached"] is False)
        v = await svc.status_view()
        record("观测: status_view 形状",
               v["modeValues"] == list(MODE_VALUES)
               and "guard" in v and "modelVersion" in v
               and "申诉" in v["exemptSurfaces"])

    # ============================================================
    # 4. 序列化往返(整档 JSON)
    # ============================================================
    with _EnvGuard("assist"):
        reset_mode_state()
        svc = Av62ModeService()
        st = await svc._state()
        st["metrics"].append({"checkedAt": "t", "breaches": 0})
        raw = json.dumps(st, ensure_ascii=False)
        back = json.loads(raw)
        record("序列化: paused=False 往返仍为 falsy",
               back.get("paused") is False)
        record("序列化: metrics 往返仍为 list",
               isinstance(back.get("metrics"), list)
               and len(back["metrics"]) == 1)
        back2 = json.loads(json.dumps(
            {**st, "paused": True}))
        record("序列化: paused=True 往返仍为 True",
               back2.get("paused") is True)


# ============================================================
# HTTP 层
# ============================================================
async def run_http():
    import httpx
    from main import app

    def decision_requests():
        admin = {"X-Role": "admin"}
        return [
            ("POST", "/api/av62/assets",
             {"subjectId": 1, "role": "enterprise",
              "domain": "compliance", "evidence": {}}, admin),
            ("POST", "/api/av62/assess",
             {"assetId": 1}, admin),
            ("POST", "/api/av62/scenarios/convert",
             {"subjectId": 1, "scenario": "bidding"}, admin),
            ("POST", "/api/av62/stress",
             {"assetId": 1, "removeDomains": []}, admin),
            ("POST", "/api/av62/activate",
             {"assetId": 1, "reason": "compliance_use"}, admin),
            ("POST", "/api/av62/threshold/calibrate",
             {"mode": "auto"}, admin),
            ("POST", "/api/av62/redteam", {}, admin),
        ]

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test") as client:

        # 7 决策面 off 全 409
        with _EnvGuard():
            for method, url, body, hdrs in decision_requests():
                r = await client.request(method, url, json=body,
                                         headers=hdrs)
                ok = (r.status_code == 409
                      and "决策面关闭" in r.text)
                record(f"决策面 off 409: {url}",
                       ok, f"{r.status_code} {r.text[:80]}")

            # 观测面 off 仍 200
            r = await client.get("/api/av62/registry",
                                headers={"X-Role": "admin"})
            record("观测面: registry off 仍 200",
                   r.status_code == 200)
            r = await client.get("/api/av62/model/status",
                                headers={"X-Role": "admin"})
            record("观测面: model/status off 仍 200",
                   r.status_code == 200)
            r = await client.get("/api/av62/mode")
            record("观测面: GET /mode off 仍 200",
                   r.status_code == 200
                   and r.json()["data"].get("mode") == "off")

            # 豁免面: 申诉不受门控(业务 404 而非模式 409)
            r = await client.post(
                "/api/av62/appeals",
                json={"assetId": 999999, "reason": "豁免验证"},
                headers={"X-Role": "admin"})
            record("豁免面: 申诉提交不受门控",
                   "决策面关闭" not in r.text,
                   f"{r.status_code} {r.text[:60]}")

        # shadow: 放行 + 标记
        with _EnvGuard("shadow"):
            r = await client.post(
                "/api/av62/assets",
                json={"subjectId": 901,
                      "role": "enterprise",
                      "domain": "compliance",
                      "evidence": {"licenseCount": 3,
                                   "auditResults": "通过"},
                      "label": "mode-shadow 登记"},
                headers={"X-Role": "admin"})
            record("shadow: 登记放行+标记",
                   r.status_code == 200
                   and r.json().get("av62Mode") == "shadow",
                   f"{r.status_code} {r.text[:80]}")

        # 控制面: 切档 + 护栏暂停恢复
        with _EnvGuard("off"):
            r = await client.post(
                "/api/av62/mode/override?mode=shadow")
            record("控制面: HTTP override 切 shadow",
                   r.status_code == 200
                   and r.json()["data"].get("mode") == "shadow")
            r = await client.get("/api/av62/mode")
            record("控制面: 切档后生效",
                   r.json()["data"].get("mode") == "shadow"
                   and r.json()["data"].get("source")
                   == "runtime_override")
            r = await client.post(
                "/api/av62/mode/override?mode=")
            r = await client.post(
                "/api/av62/mode/guard",
                json={"appealOverturnRate": 0.20,
                      "stressFailRate": 0.10,
                      "fairnessAnomalyRate": 0.05})
            record("控制面: HTTP guard 恶化暂停",
                   r.status_code == 200
                   and r.json()["data"].get("pausedNow") is True)
            r = await client.get("/api/av62/mode")
            record("控制面: 暂停后 off/guard_pause",
                   r.json()["data"].get("mode") == "off"
                   and r.json()["data"].get("source")
                   == "guard_pause")
            r = await client.post(
                "/api/av62/mode/resume?note=final-check")
            record("控制面: HTTP resume 恢复",
                   r.status_code == 200
                   and r.json()["data"].get("mode") == "off"
                   and r.json()["data"].get("source") == "env")
            await client.post("/api/av62/mode/override?mode=")
            r = await client.get("/api/av62/mode")
            record("控制面: 清除回落 env(off)",
                   r.json()["data"].get("mode") == "off"
                   and r.json()["data"].get("source") == "env")


def main():
    asyncio.run(run_service())
    asyncio.run(run_http())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
