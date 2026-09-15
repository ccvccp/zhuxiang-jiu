"""智运·AI智能物流大模型 三态灰度+护栏+控制面 专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_zw_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off 四层
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 恶化暂停留痕 / 基线0规则 / baseline 覆盖 /
       resume 人工恢复 / 未暂停恢复拒绝 / 指标留痕
    5. 状态序列化往返: 整档 JSON——paused 布尔 / metrics 列表
       (45号 P0 教训: 字段级清单漏注册致 Redis 真值误判——本模块
        整档 JSON 存储, 类型天然保留)
    6. HTTP 决策面: off 下 17 端点全 409(决策面关闭)
    7. HTTP 观测面: off 下 status/route/health/mode 200
    8. HTTP 公开观测面: binding/verify 非模式 409(公开+脱敏)
    9. HTTP shadow/assist: 放行 + zwMode 标记
    10. 控制面: GET /mode 形状 / override 运行时切档 / 护栏暂停与恢复
"""
import asyncio
import json
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.zw_mode_service import (
    ZwModeService, MODE_VALUES,
)

PASS = 0
FAIL = 0
RESULTS = []

_ENV_KEYS = ["ZW_MODE"]


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


class _EnvGuard:
    """env 快照守卫(ZW_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("ZW_MODE")
        os.environ.pop("ZW_MODE", None)
        if self._mode:
            os.environ["ZW_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("ZW_MODE", None)
        else:
            os.environ["ZW_MODE"] = self._backup


async def run_service():
    global PASS, FAIL
    from repositories.store import reset_store

    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():                       # 默认 off
        reset_store()
        svc = ZwModeService()
        m = await svc.current_mode()
        record("三态: 默认 off(env 源)", m["mode"] == "off"
               and m["source"] == "env")
        try:
            await svc.require_decision_mode()
            record("三态: off 决策面拒绝", False)
        except ValueError as e:
            record("三态: off 决策面拒绝",
                   "ZW_MODE=off" in str(e))
        os.environ["ZW_MODE"] = "invalid"
        m = await svc.current_mode()
        record("三态: 非法 env 回落 off", m["mode"] == "off")
    with _EnvGuard("shadow"):
        reset_store()
        svc = ZwModeService()
        m = await svc.current_mode()
        record("三态: env shadow", m["mode"] == "shadow")
        m = await svc.require_decision_mode()
        record("三态: shadow 决策面放行", m["mode"] == "shadow")
    with _EnvGuard("assist"):
        reset_store()
        svc = ZwModeService()
        m = await svc.current_mode()
        record("三态: env assist", m["mode"] == "assist")

    # ============================================================
    # 2. 读取链 + override
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = ZwModeService()
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
        g = await svc.guard_check(0.30, 0.01, 0.05)   # 延迟率 0.10→0.30 恶化 200%
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
        svc = ZwModeService()
        # 未恶化: 三指标等于基线
        g = await svc.guard_check(0.10, 0.05, 0.10)
        record("护栏: 基线持平不暂停", g["breached"] is False
               and g["pausedNow"] is False)
        # 恶化: 轨迹异常率 0.05→0.10(+100%)
        g = await svc.guard_check(0.10, 0.10, 0.10)
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
            0.01, 0.0, 0.0,
            baseline={"delayRate": 0.0, "anomalyRate": 0.0,
                      "circuitBreakRate": 0.0})
        record("护栏: 基线0绝对恶化", g["breached"] is True)
        await svc.resume(operator="t")
        # baseline 覆盖: 抬高基线 → 原值不再恶化
        g = await svc.guard_check(
            0.30, 0.01, 0.05,
            baseline={"delayRate": 0.30})
        record("护栏: baseline 覆盖生效", g["breached"] is False)
        # 观测面 status_view 形状
        v = await svc.status_view()
        record("观测: status_view 形状",
               v["modeValues"] == list(MODE_VALUES)
               and "guard" in v and "modelVersion" in v
               and "基础物流" in v["decisionSurfaces"])

    # ============================================================
    # 4. 状态序列化往返(整档 JSON——45号 P0 教训回归)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = ZwModeService()
        st = await svc._state()
        st["metrics"].append({"checkedAt": "t", "breaches": 0})
        st["paused"] = False
        # Redis 模式即 json.dumps→SET→GET→json.loads;
        # 内存模式直接模拟同一往返
        raw = json.dumps(st, ensure_ascii=False)
        back = json.loads(raw)
        record("序列化: paused=False 往返仍为 falsy",
               back.get("paused") is False,
               f"paused={back.get('paused')!r}")
        record("序列化: metrics 往返仍为 list",
               isinstance(back.get("metrics"), list)
               and len(back["metrics"]) == 1)
        record("序列化: breachTrail 往返仍为 list",
               isinstance(back.get("breachTrail"), list)
               and back["breachTrail"] == [])
        st2 = dict(st)
        st2["paused"] = True
        st2["pausedReason"] = "r"
        back2 = json.loads(json.dumps(st2))
        record("序列化: paused=True 往返仍为 True",
               back2.get("paused") is True)


# ============================================================
# HTTP 层(ASGITransport 单循环)
# ============================================================
async def run_http():
    global PASS, FAIL
    import httpx
    from main import app

    # 17 决策面请求(off 下全 409; 载荷须过 pydantic——门控在装饰器层)
    def decision_requests():
        admin = {"X-Role": "admin"}
        return [
            ("POST", "/api/logistics-ai/route/decide",
             {"orderType": "retail", "weight": 1,
              "pieceCount": 1}, admin),
            ("POST", "/api/logistics-ai/risk/assess",
             {"weight": 1, "pieceCount": 1}, admin),
            ("POST", "/api/logistics-ai/risk/inspect-receipt",
             {"orderId": "X1", "expectedCount": 1,
              "actualCount": 1, "inspector": "i"}, admin),
            ("POST", "/api/logistics-ai/risk/claims",
             {"waybillNo": "W1", "orderId": "X1", "carrier": "SF",
              "claimType": "damage", "claimAmount": 1}, admin),
            ("POST", "/api/logistics-ai/evolution/feedback",
             {"targetType": "route_decision",
              "verdict": "adopted"}, admin),
            ("POST", "/api/logistics-ai/semantic/normalize",
             {"carrier": "SF", "payload": {}}, admin),
            ("POST", "/api/logistics-ai/semantic/register-carrier",
             {"carrier": "YT", "carrierName": "圆通",
              "fieldMap": {}}, admin),
            ("POST", "/api/logistics-ai/semantic/order-profile",
             {"weight": 1, "pieceCount": 1}, admin),
            ("POST", "/api/logistics-ai/semantic/feature-route",
             {"profile": {}}, admin),
            ("POST", "/api/logistics-ai/circuit/report",
             {}, admin),
            ("POST", "/api/logistics-ai/binding/tri-code",
             {"waybillNo": "W1", "orderId": "X1",
              "batchCode": "B1", "antiFakeCode": "A1"}, admin),
            ("POST", "/api/logistics-ai/binding/reverse",
             {"orderId": "X1", "condition": "unopened"}, admin),
            ("POST", "/api/logistics-ai/alert/scan",
             {}, admin),
            ("POST", "/api/logistics-ai/alerts/1/ack",
             {"disposition": "acked"}, admin),
            ("POST", "/api/logistics-ai/evolution2/preference",
             {"memberId": 1, "scope": "b2c", "prefs": {}}, admin),
            ("POST", "/api/logistics-ai/evolution2/suggest",
             {}, admin),
            ("POST", "/api/logistics-ai/evolution2/suggestions/1/decide",
             {"verdict": "adopted"}, admin),
        ]

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test") as client:

        # --------------------------------------------------------
        # 6. off: 17 决策面全 409
        # --------------------------------------------------------
        with _EnvGuard():     # off
            for method, url, body, hdrs in decision_requests():
                r = await client.request(method, url, json=body,
                                         headers=hdrs)
                ok = (r.status_code == 409
                      and "决策面关闭" in r.text)
                record(f"决策面 off 409: {url}",
                       ok, f"{r.status_code} {r.text[:80]}")

            # 7. off: 观测面仍 200
            r = await client.get("/api/logistics-ai/status",
                                headers={"X-Role": "admin"})
            record("观测面: status off 仍 200",
                   r.status_code == 200
                   and r.json().get("success") is True)
            r = await client.get("/api/logistics-ai/route/health",
                                headers={"X-Role": "admin"})
            record("观测面: route/health off 仍 200",
                   r.status_code == 200)
            r = await client.get("/api/logistics-ai/mode")
            record("观测面: GET /mode off 仍 200",
                   r.status_code == 200
                   and r.json()["data"].get("mode") == "off")

            # 8. off: 公开观测面(三码验真)非模式 409
            r = await client.get(
                "/api/logistics-ai/binding/verify/FAKE123")
            record("公开面: binding/verify 不受门控(非模式 409)",
                   "决策面关闭" not in r.text,
                   f"{r.status_code} {r.text[:60]}")

        # --------------------------------------------------------
        # 9. shadow: 放行 + 标记 / assist 标记
        # --------------------------------------------------------
        with _EnvGuard("shadow"):
            r = await client.post(
                "/api/logistics-ai/route/decide",
                headers={"X-Role": "admin"},
                json={"orderType": "retail", "weight": 1,
                      "pieceCount": 1,
                      "sender": {"name": "s", "phone": "1",
                                 "address": "a"},
                      "receiver": {"name": "r", "phone": "2",
                                   "address": "b"}})
            record("shadow: 路由决策放行+标记",
                   r.status_code == 200
                   and r.json().get("zwMode") == "shadow",
                   f"{r.status_code} {r.text[:80]}")

        with _EnvGuard("assist"):
            r = await client.post(
                "/api/logistics-ai/route/decide",
                headers={"X-Role": "admin"},
                json={"orderType": "retail", "weight": 1,
                      "pieceCount": 1})
            record("assist: 路由决策放行+标记",
                   r.status_code == 200
                   and r.json().get("zwMode") == "assist",
                   f"{r.status_code} {r.text[:80]}")

        # --------------------------------------------------------
        # 10. 控制面: 运行时切档 + 护栏暂停/恢复(HTTP)
        # --------------------------------------------------------
        with _EnvGuard("off"):
            r = await client.post(
                "/api/logistics-ai/mode/override?mode=shadow")
            record("控制面: HTTP override 运行时切 shadow",
                   r.status_code == 200
                   and r.json()["data"].get("mode") == "shadow")
            r = await client.get("/api/logistics-ai/mode")
            record("控制面: 切档后生效",
                   r.json()["data"].get("mode") == "shadow"
                   and r.json()["data"].get("source")
                   == "runtime_override")
            await client.post(
                "/api/logistics-ai/mode/override?mode=")
            # 护栏暂停(延迟率 0.10→0.30)
            r = await client.post(
                "/api/logistics-ai/mode/guard",
                json={"delayRate": 0.30, "anomalyRate": 0.05,
                      "circuitBreakRate": 0.10})
            record("控制面: HTTP guard 恶化暂停",
                   r.status_code == 200
                   and r.json()["data"].get("pausedNow") is True)
            r = await client.get("/api/logistics-ai/mode")
            record("控制面: 暂停后 mode=off/guard_pause",
                   r.json()["data"].get("mode") == "off"
                   and r.json()["data"].get("source")
                   == "guard_pause")
            r = await client.post(
                "/api/logistics-ai/mode/resume"
                "?note=%E6%81%A2%E5%A4%8D%E9%AA%8C%E8%AF%81")
            record("控制面: HTTP resume 人工恢复",
                   r.status_code == 200
                   and r.json()["data"].get("mode") == "off"
                   and r.json()["data"].get("source") == "env")
            await client.post(
                "/api/logistics-ai/mode/override?mode=")
            r = await client.get("/api/logistics-ai/mode")
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
