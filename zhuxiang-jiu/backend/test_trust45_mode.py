"""45号·信值大模型升级专项测试(三态灰度 + 护栏 + 控制面)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_trust45_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off 四层
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 恶化暂停留痕 / 基线0规则 / baseline 覆盖 /
       resume 人工恢复 / 未暂停恢复拒绝 / 指标留痕
    5. HTTP 决策面: off 下 17 端点全 409(决策面关闭)
    6. HTTP 观测面: off 下 mode/dashboard/learning-status 200
    7. HTTP 豁免面: off 下 appeals/collect/attribution 不受门控
    8. HTTP shadow/assist: 放行 + trust45Mode 标记
    9. 控制面: GET /mode 形状 / override 运行时切档
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.trust45_mode_service import (
    Trust45ModeService, MODE_VALUES,
)

PASS = 0
FAIL = 0
RESULTS = []

_ENV_KEYS = ["TRUST45_MODE"]


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


class _EnvGuard:
    """env 快照守卫(TRUST45_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("TRUST45_MODE")
        os.environ.pop("TRUST45_MODE", None)
        if self._mode:
            os.environ["TRUST45_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("TRUST45_MODE", None)
        else:
            os.environ["TRUST45_MODE"] = self._backup


async def run_service():
    global PASS, FAIL
    from repositories.store import reset_store

    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():                       # 默认 off
        reset_store()
        svc = Trust45ModeService()
        m = await svc.current_mode()
        record("三态: 默认 off(env 源)", m["mode"] == "off"
               and m["source"] == "env")
        try:
            await svc.require_decision_mode()
            record("三态: off 决策面拒绝", False)
        except ValueError as e:
            record("三态: off 决策面拒绝",
                   "TRUST45_MODE=off" in str(e))
        os.environ["TRUST45_MODE"] = "invalid"
        m = await svc.current_mode()
        record("三态: 非法 env 回落 off", m["mode"] == "off")
    with _EnvGuard("shadow"):
        reset_store()
        svc = Trust45ModeService()
        m = await svc.current_mode()
        record("三态: env shadow", m["mode"] == "shadow")
        m = await svc.require_decision_mode()
        record("三态: shadow 决策面放行", m["mode"] == "shadow")
    with _EnvGuard("assist"):
        reset_store()
        svc = Trust45ModeService()
        m = await svc.current_mode()
        record("三态: env assist", m["mode"] == "assist")

    # ============================================================
    # 2. 读取链 + override
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = Trust45ModeService()
        m = await svc.set_override("shadow", operator="tester")
        record("override: 设值优先 env", m["mode"] == "shadow"
               and m["source"] == "runtime_override")
        m = await svc.set_override("", operator="tester")
        record("override: 清除回落 env", m["mode"] == "assist"
               and m["source"] == "env")
        try:
            await svc.set_override("bad", operator="tester")
            record("override: 非法值拒绝", False)
        except ValueError:
            record("override: 非法值拒绝", True)
        # 暂停最优先: 设 override=assist 后触发护栏
        await svc.set_override("assist", operator="tester")
        g = await svc.guard_check(0.10, 0.01, 0.03)   # 翻转率 0.05→0.10 恶化 100%
        m = await svc.current_mode()
        record("读取链: 暂停 > override", g["breached"] is True
               and m["mode"] == "off" and m["source"] == "guard_pause"
               and m["override"] == "assist")
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
        reset_store()
        svc = Trust45ModeService()
        # 未恶化: 三指标等于基线
        g = await svc.guard_check(0.05, 0.02, 0.05)
        record("护栏: 基线持平不暂停", g["breached"] is False
               and g["pausedNow"] is False)
        # 恶化: 兑换拒绝率 0.05→0.10(+100%)
        g = await svc.guard_check(0.05, 0.02, 0.10)
        record("护栏: 恶化>3% 自动暂停", g["breached"] is True
               and g["pausedNow"] is True)
        st = await svc._state()
        record("护栏: 暂停留痕(pausedReason/breachTrail)",
               "护栏自动暂停" in st["pausedReason"]
               and len(st["breachTrail"]) == 1)
        record("护栏: 指标留痕计数", len(st["metrics"]) == 2)
        await svc.resume(operator="t")
        # 基线 0 规则: baseline 置 0 + cur>0 → 绝对恶化
        g = await svc.guard_check(
            0.01, 0.0, 0.0, baseline={"redeemRejectRate": 0.0,
                                      "circuitRate": 0.0,
                                      "appealOverturnRate": 0.0})
        record("护栏: 基线0绝对恶化", g["breached"] is True)
        await svc.resume(operator="t")
        # baseline 覆盖: 抬高基线 → 原值不再恶化
        g = await svc.guard_check(
            0.10, 0.01, 0.03,
            baseline={"appealOverturnRate": 0.10})
        record("护栏: baseline 覆盖生效", g["breached"] is False)
        # 观测面 status_view 形状
        v = await svc.status_view()
        record("观测: status_view 形状",
               v["modeValues"] == list(MODE_VALUES)
               and "guard" in v and "modelVersion" in v)


# ============================================================
# HTTP 层(ASGITransport 单循环)
# ============================================================
async def run_http():
    global PASS, FAIL
    import httpx
    from main import app

    # 17 决策面请求(off 下全 409)
    def decision_requests():
        admin = {"X-Role": "admin"}
        return [
            ("POST", "/api/trust/roles",
             {"role": "person", "name": "甲", "idNumber": "X1"}, {}),
            ("POST", "/api/trust/roles/1/score", {}, {}),
            ("POST", "/api/trust/roles/1/events",
             {"layer": "L3", "factor": "community", "delta": 5}, admin),
            ("POST", "/api/trust/radar/scan/1", {}, {}),
            ("POST", "/api/trust/probes",
             {"trustId": 1, "provider": "zhima"}, {}),
            ("POST", "/api/trust/deposits",
             {"trustId": 1, "layer": "L3", "factor": "community",
              "observed": 10, "peerBaseline": 10, "evidence": "e"}, {}),
            ("POST", "/api/trust/repairs",
             {"trustId": 1, "violationEventId": 1, "repairs": []}, {}),
            ("POST", "/api/trust/repairs/1/verify", {}, {}),
            ("POST", "/api/trust/redeem",
             {"trustId": 1, "amount": 1, "merchant": "m"}, {}),
            ("POST", "/api/trust/redeem/1/confirm",
             {"merchant": "m"}, {}),
            ("POST", "/api/trust/convert",
             {"trustId": 1, "userId": 1, "creditPoints": 1}, {}),
            ("POST", "/api/trust/merchant/deposit",
             {"merchant": "m", "amount": 1}, admin),
            ("POST", "/api/trust/patches",
             {"kind": "beta_update", "payload": {}, "note": "x"}, admin),
            ("POST", "/api/trust/open/redeem/confirm",
             {"redeemId": 1, "merchant": "m",
              "idempotencyKey": "12345678",
              "nonce": "0123456789abcdef",
              "timestamp": 2000000000}, {}),
            ("POST", "/api/trust/open/deposits",
             {"trustId": 1, "layer": "L3", "factor": "community",
              "observed": 10, "peerBaseline": 10, "evidence": "e"}, {}),
            ("POST", "/api/trust/open/convert",
             {"trustId": 1, "userId": 1, "creditPoints": 1,
              "nonce": "0123456789abcdef",
              "timestamp": 2000000000}, {}),
            ("POST", "/api/trust/learning/run", {}, admin),
        ]

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test") as client:

        # --------------------------------------------------------
        # 5. off: 17 决策面全 409
        # --------------------------------------------------------
        with _EnvGuard():     # off
            for method, url, body, hdrs in decision_requests():
                r = await client.request(method, url, json=body,
                                         headers=hdrs)
                ok = (r.status_code == 409
                      and "决策面关闭" in r.text)
                record(f"决策面 off 409: {url}",
                       ok, f"{r.status_code} {r.text[:80]}")

            # 6. off: 观测面仍 200
            r = await client.get("/api/trust/mode")
            record("观测面: GET /mode off 仍 200",
                   r.status_code == 200
                   and r.json().get("mode") == "off")
            r = await client.get("/api/trust/open/dashboard")
            record("观测面: dashboard off 仍 200",
                   r.status_code == 200
                   and r.json().get("success") is True)
            r = await client.get("/api/trust/learning/status",
                                headers={"X-Role": "admin"})
            record("观测面: learning/status off 仍 200",
                   r.status_code == 200)

            # 7. off: 豁免面(纠错/回流)不受门控
            r = await client.post("/api/trust/appeals", json={
                "trustId": 999999, "eventId": 1,
                "reason": "off 下提交(豁免验证)"})
            record("豁免面: off 下申诉可提交(非模式 409)",
                   "决策面关闭" not in r.text,
                   f"{r.status_code} {r.text[:80]}")
            r = await client.post(
                "/api/trust/learning/collect",
                headers={"X-Role": "admin"})
            record("豁免面: off 下回流可执行",
                   r.status_code == 200
                   and "决策面关闭" not in r.text,
                   f"{r.status_code} {r.text[:80]}")
            r = await client.get(
                "/api/trust/attribution/999999/1")
            record("豁免面: off 下归因可查询(非模式 409)",
                   "决策面关闭" not in r.text)

        # --------------------------------------------------------
        # 8. shadow: 放行 + 标记 / assist 标记
        # --------------------------------------------------------
        with _EnvGuard("shadow"):
            r = await client.post("/api/trust/roles", json={
                "role": "person", "name": "影子甲",
                "idNumber": "TRUST45-SHADOW-1"})
            record("shadow: 建档放行+标记",
                   r.status_code == 200
                   and r.json().get("trust45Mode") == "shadow",
                   f"{r.status_code} {r.text[:80]}")
            trust_id = r.json().get("trustId")
            r = await client.post(
                f"/api/trust/roles/{trust_id}/score")
            record("shadow: 重算放行+标记",
                   r.status_code == 200
                   and r.json().get("trust45Mode") == "shadow")

        with _EnvGuard("assist"):
            r = await client.post("/api/trust/roles", json={
                "role": "person", "name": "辅助甲",
                "idNumber": "TRUST45-ASSIST-1"})
            record("assist: 建档放行+标记",
                   r.status_code == 200
                   and r.json().get("trust45Mode") == "assist")

        # --------------------------------------------------------
        # 9. 控制面: 运行时切档(HTTP)
        # --------------------------------------------------------
        with _EnvGuard("off"):
            r = await client.post(
                "/api/trust/mode/override?mode=shadow")
            record("控制面: HTTP override 运行时切 shadow",
                   r.status_code == 200
                   and r.json().get("mode") == "shadow")
            r = await client.get("/api/trust/mode")
            record("控制面: 切档后生效",
                   r.json().get("mode") == "shadow"
                   and r.json().get("source") == "runtime_override")
            await client.post("/api/trust/mode/override?mode=")
            r = await client.get("/api/trust/mode")
            record("控制面: 清除回落 env(off)",
                   r.json().get("mode") == "off")


def main():
    asyncio.run(run_service())
    asyncio.run(run_http())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
