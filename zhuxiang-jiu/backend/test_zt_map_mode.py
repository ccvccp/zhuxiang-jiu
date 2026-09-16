"""智图·AI智能地图大模型 大模型二代转段专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_zt_map_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off 四层
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 三指标逐一恶化暂停 / 留痕 /
       resume / 未暂停恢复拒绝
       (三指标: 建议驳回率/调度误报率/月台饱和率
       ——工单为处置预案草稿, pending 不进误报分母)
    5. 状态序列化往返: 整档 JSON——paused 布尔 / metrics 列表
    6. 同步桥接(62号范式): legacy_current_mode 进程快照
    7. 护栏自动巡检: 默认 on / 间隔 / 冷启动 / 恶化暂停
    8. HTTP 决策面: off 下 11 端点全 409 + 鉴权优先
       (admin 403 before 409)
    9. HTTP 观测面: off 下 roles/situation/radar 不受影响
    10. HTTP shadow/assist: 放行 + ztMode 标记
    11. 控制面: GET /mode / override / guard / resume / 未暂停 409
    12. 端点计数: 28(24+4 控制面, 下限断言防腐化)
       + 既有 location 13 端点零破坏
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.zt_map_mode_service import (
    ZtMapModeService, MODE_VALUES,
    legacy_current_mode, _refresh_legacy,
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
    """env 快照守卫(ZT_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("ZT_MODE")
        os.environ.pop("ZT_MODE", None)
        if self._mode:
            os.environ["ZT_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("ZT_MODE", None)
        else:
            os.environ["ZT_MODE"] = self._backup


async def run_service():
    global PASS, FAIL
    from repositories.store import reset_store

    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():
        reset_store()
        svc = ZtMapModeService()
        m = await svc.current_mode()
        record("三态: 默认 off(env 源)",
               m["mode"] == "off"
               and m["source"] == "env")
        try:
            await svc.require_decision_mode()
            record("三态: off 决策面拒绝", False)
        except ValueError as e:
            record("三态: off 决策面拒绝",
                   "ZT_MODE=off" in str(e))
        os.environ["ZT_MODE"] = "invalid"
        m = await svc.current_mode()
        record("三态: 非法 env 回落 off",
               m["mode"] == "off")
    with _EnvGuard("shadow"):
        reset_store()
        m = await ZtMapModeService() \
            .current_mode()
        record("三态: env shadow", m["mode"] == "shadow")
    with _EnvGuard("assist"):
        reset_store()
        m = await ZtMapModeService() \
            .current_mode()
        record("三态: env assist",
               m["mode"] == "assist")

    # ============================================================
    # 2. 读取链(护栏暂停 > override > env > off)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = ZtMapModeService()
        m = await svc.current_mode()
        record("读取链: env assist",
               m["mode"] == "assist"
               and m["source"] == "env")
        await svc.set_override("shadow")
        m = await svc.current_mode()
        record("读取链: override 优先",
               m["mode"] == "shadow"
               and m["source"] == "runtime_override")
        await svc.set_override("")
        m = await svc.current_mode()
        record("读取链: 清除 override 回落 env",
               m["mode"] == "assist"
               and m["source"] == "env")
        # 恶化暂停(直接调 guard_check——驳回率爆表)
        await svc.guard_check(0.5, 0.0, 0.0)
        m = await svc.current_mode()
        record("读取链: 护栏暂停最优先",
               m["mode"] == "off"
               and m["source"] == "guard_pause")
        await svc.resume(note="读取链测试恢复")

    # ============================================================
    # 3. override
    # ============================================================
    with _EnvGuard("off"):
        reset_store()
        svc = ZtMapModeService()
        try:
            await svc.set_override("bogus")
            record("override: 非法值拒绝", False)
        except ValueError:
            record("override: 非法值拒绝", True)
        m = await svc.set_override("assist")
        record("override: 设值 assist",
               m["mode"] == "assist"
               and m["source"] == "runtime_override")

    # ============================================================
    # 4. 护栏(三指标恶化 >3% 自动暂停)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = ZtMapModeService()
        r = await svc.guard_check(0.10, 0.10, 0.10)
        record("护栏: 基线持平不暂停",
               r["breached"] is False
               and r["pausedNow"] is False)
        # 建议驳回率恶化
        r = await svc.guard_check(0.20, 0.10, 0.10)
        record("护栏: 建议驳回率恶化暂停",
               r["breached"] is True
               and r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "rejectRate")
        st_trail = await svc._state()
        record("护栏: 暂停留痕",
               bool(st_trail["paused"])
               and "建议驳回率" in st_trail["pausedReason"]
               and len(st_trail["breachTrail"]) == 1)
        m = await svc.resume(note="护栏测试恢复")
        record("护栏: resume 恢复",
               m["paused"] is False
               and m["mode"] == "assist")
        try:
            await svc.resume()
            record("护栏: 未暂停恢复拒绝", False)
        except ValueError:
            record("护栏: 未暂停恢复拒绝", True)
        # 调度误报率恶化
        r = await svc.guard_check(0.10, 0.30, 0.10)
        record("护栏: 调度误报率恶化暂停",
               r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "falseAlarmRate")
        await svc.resume(note="恢复")
        # 月台饱和率恶化(基线 0.60)
        r = await svc.guard_check(0.10, 0.10, 0.80)
        record("护栏: 月台饱和率恶化暂停",
               r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "dockSaturationRate")
        await svc.resume(note="恢复")
        # 基线为 0 时绝对值恶化(防除零)
        r = await svc.guard_check(
            0.0, 0.0, 0.0,
            baseline={"rejectRate": 0.0,
                      "falseAlarmRate": 0.10,
                      "dockSaturationRate": 0.60})
        record("护栏: 零基线防除零不误判",
               r["breached"] is False)
        r = await svc.guard_check(
            0.01, 0.0, 0.0,
            baseline={"rejectRate": 0.0,
                      "falseAlarmRate": 0.10,
                      "dockSaturationRate": 0.60})
        record("护栏: 零基线绝对恶化暂停",
               r["pausedNow"] is True)
        await svc.resume(note="恢复")

    # ============================================================
    # 5. 状态序列化往返(整档 JSON)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = ZtMapModeService()
        await svc.guard_check(0.20, 0.10, 0.10)
        st2 = await svc._load_state()
        record("序列化: paused 布尔往返",
               st2["paused"] is True)
        record("序列化: metrics 列表往返",
               isinstance(st2["metrics"], list)
               and len(st2["metrics"]) >= 1)
        record("序列化: breachTrail 往返",
               isinstance(st2["breachTrail"], list)
               and len(st2["breachTrail"]) == 1
               and "建议驳回率" in st2["breachTrail"][0]["reason"])
        record("序列化: 指标留痕滚动截断",
               st2["metrics"][0]["metrics"]["rejectRate"]
               == 0.2)
        await svc.resume(note="序列化测试恢复")

    # ============================================================
    # 6. 同步桥接(62号范式)
    # ============================================================
    with _EnvGuard():
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        record("桥接: 快照干净回落 off",
               legacy_current_mode() == "off")
    with _EnvGuard("assist"):
        reset_store()
        svc = ZtMapModeService()
        await svc.set_override("shadow")
        record("桥接: override 后同步 shadow",
               legacy_current_mode() == "shadow")
        await svc.guard_check(0.5, 0.0, 0.0)
        record("桥接: 暂停后同步 off",
               legacy_current_mode() == "off")
        await svc.resume(note="桥接测试恢复")
        await svc.set_override("")
        record("桥接: 清除后回落 env assist",
               legacy_current_mode() == "assist")
    _refresh_legacy({"override": "", "paused": False})

    # ============================================================
    # 7. 护栏自动巡检(zt_tickets/zt_feedbacks 聚合)
    # ============================================================
    with _EnvGuard("assist"):
        from services.zt_map_scheduler import (
            guard_patrol_enabled,
            guard_interval_seconds,
            run_guard_patrol,
        )
        from services.zt_fabric_service import _ZtStore
        record("巡检: 默认 on",
               guard_patrol_enabled() is True)
        os.environ["ZT_GUARD_AUTO"] = "off"
        record("巡检: 开关可关",
               guard_patrol_enabled() is False)
        os.environ["ZT_GUARD_AUTO"] = "on"
        os.environ["ZT_GUARD_INTERVAL"] = "120"
        record("巡检: 间隔可调(120s)",
               guard_interval_seconds() == 120)
        os.environ["ZT_GUARD_INTERVAL"] = "bad"
        record("巡检: 非法间隔回落 3600",
               guard_interval_seconds() == 3600)
        os.environ.pop("ZT_GUARD_INTERVAL", None)
        os.environ.pop("ZT_GUARD_AUTO", None)

        reset_store()
        store = _ZtStore()
        # 冷启动(无工单/无反馈/无预约)——不误暂停
        r = await run_guard_patrol()
        record("巡检: 冷启动不误判",
               r["metrics"]["rejectRate"] == 0.0
               and r["metrics"]["falseAlarmRate"] == 0.0
               and r["metrics"]["dockSaturationRate"]
               == 0.0
               and r["pausedNow"] is False)
        # 口径铁律: 仅 pending 草稿工单(无人处置)
        # 不进误报分母——不误暂停
        await store.save("zt_tickets", 97001,
                         {"ticketId": 97001,
                          "status": "pending"})
        await store.save("zt_tickets", 97002,
                         {"ticketId": 97002,
                          "status": "pending"})
        r = await run_guard_patrol()
        record("巡检: pending 草稿不误判(人工确认制)",
               r["metrics"]["falseAlarmRate"] == 0.0
               and r["pausedNow"] is False,
               str(r["metrics"]))
        # 造数: 2 dismissed + 1 acked 工单
        # (误报率 2/3=0.667 恶化)
        await store.save("zt_tickets", 97003,
                         {"ticketId": 97003,
                          "status": "dismissed"})
        await store.save("zt_tickets", 97004,
                         {"ticketId": 97004,
                          "status": "dismissed"})
        await store.save("zt_tickets", 97005,
                         {"ticketId": 97005,
                          "status": "acked"})
        r = await run_guard_patrol()
        record("巡检: 误报率聚合恶化暂停",
               abs(r["metrics"]["falseAlarmRate"]
                   - 2 / 3) < 1e-3
               and r["breached"] is True
               and r["pausedNow"] is True,
               str(r["metrics"]))
        m = await ZtMapModeService().current_mode()
        record("巡检: 暂停生效(guard_pause)",
               m["mode"] == "off"
               and m["source"] == "guard_pause")
        await ZtMapModeService().resume(
            note="巡检测试恢复")
    _refresh_legacy({"override": "", "paused": False})


async def run_http():
    global PASS, FAIL
    import httpx
    from main import app

    admin = {"X-Role": "admin"}

    # 11 决策面请求(off 下全 409; 门控在装饰器层)
    def decision_requests():
        return [
            ("POST", "/api/map-ai/intent/parse",
             {"text": "吃饭买酒"}),
            ("POST", "/api/map-ai/intent/search",
             {"text": "买酒", "longitude": 104.081,
              "latitude": 30.660}),
            ("POST", "/api/map-ai/intent/behavior-hints",
             {"role": "consumer"}),
            ("POST", "/api/map-ai/poi/register",
             {"poiCode": "T-990", "name": "门控测试点",
              "poiType": "flagship", "longitude": 104.0,
              "latitude": 30.6}),
            ("POST", "/api/map-ai/supplier/dock-booking",
             {"supplierName": "门控测试", "slot": "08:00"}),
            ("POST", "/api/map-ai/b2b/warehouse-match",
             {"longitude": 104.08, "latitude": 30.66,
              "quantity": 100}),
            ("POST", "/api/map-ai/command/scan", None),
            ("POST", "/api/map-ai/command/tickets/1/ack",
             {"disposition": "acked"}),
            ("POST", "/api/map-ai/evolution/behavior",
             {"memberId": 1, "stage": "search"}),
            ("POST", "/api/map-ai/evolution/sandbox",
             {"name": "门控沙盘", "longitude": 104.0,
              "latitude": 30.6}),
            ("POST", "/api/map-ai/evolution/feedback",
             {"targetType": "poi", "verdict": "adopted"}),
        ]

    with _EnvGuard("off"):
        from repositories.store import reset_store
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            n_ok = 0
            for method, url, payload in decision_requests():
                r = await c.request(
                    method, url, json=payload,
                    headers=admin)
                body = r.json()
                gated = "ZT_MODE" in str(
                    body.get("detail")
                    or body.get("error", ""))
                passed = r.status_code == 409 and gated
                n_ok += passed
                if not passed:
                    record(f"HTTP: off 409 {url}",
                           False,
                           f"got {r.status_code}")
            record(f"HTTP: 决策面 off 全 409"
                   f"({n_ok}/11)", n_ok == 11)

            # 鉴权优先(403 before 409)
            r = await c.post(
                "/api/map-ai/intent/parse",
                json={"text": "吃饭买酒"})
            record("HTTP: admin 面无 Role 403 优先",
                   r.status_code == 403,
                   f"got {r.status_code}")

            # 观测面 off 不受影响
            for url in ("/api/map-ai/intent/roles",
                        "/api/map-ai/command/situation",
                        "/api/map-ai/management/radar"):
                r = await c.get(url, headers=admin)
                record(f"HTTP: 观测面 off 不受限 "
                       f"{url.rsplit('/', 1)[-1]}",
                       r.status_code == 200,
                       f"got {r.status_code}")

            # 控制面
            r = await c.get("/api/map-ai/mode",
                            headers=admin)
            body = r.json()
            record("控制面: GET /mode 形状",
                   r.status_code == 200
                   and body.get("modeValues")
                   == ["off", "shadow", "assist"]
                   and "guard" in body)
            r = await c.get("/api/map-ai/mode")
            record("控制面: GET /mode admin 门禁",
                   r.status_code == 403)
            r = await c.post(
                "/api/map-ai/mode/override",
                json={"mode": "shadow"},
                headers=admin)
            record("控制面: override→shadow",
                   r.status_code == 200
                   and r.json()["data"]["mode"]
                   == "shadow")
            r = await c.post(
                "/api/map-ai/mode/override",
                json={"mode": "bogus"},
                headers=admin)
            record("控制面: 非法 override 409",
                   r.status_code == 409)
            r = await c.post(
                "/api/map-ai/mode/override",
                json={"mode": ""},
                headers=admin)
            record("控制面: 清除 override 回落 env",
                   r.status_code == 200
                   and r.json()["data"]["mode"] == "off")
            r = await c.post(
                "/api/map-ai/mode/resume",
                headers=admin)
            record("控制面: 未暂停 resume 409",
                   r.status_code == 409)

            # 显式 guard 指标(恶化→暂停→resume)
            r = await c.post(
                "/api/map-ai/mode/guard",
                json={"rejectRate": 0.5,
                      "falseAlarmRate": 0.1,
                      "dockSaturationRate": 0.6},
                headers=admin)
            record("控制面: guard 显式恶化暂停",
                   r.status_code == 200
                   and r.json()["data"]["pausedNow"]
                   is True)
            r = await c.post(
                "/api/map-ai/mode/resume",
                headers=admin)
            record("控制面: resume 恢复",
                   r.status_code == 200
                   and r.json()["data"]["paused"]
                   is False)

    # shadow/assist 放行 + ztMode 标记
    with _EnvGuard("assist"):
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            r = await c.post(
                "/api/map-ai/intent/parse",
                json={"text": "吃饭买酒"},
                headers=admin)
            record("HTTP: assist 放行+ztMode 标记",
                   r.status_code == 200
                   and r.json().get("ztMode")
                   == "assist",
                   f"{r.status_code} {str(r.json())[:60]}")
    with _EnvGuard("shadow"):
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            r = await c.post(
                "/api/map-ai/intent/parse",
                json={"text": "吃饭买酒"},
                headers=admin)
            record("HTTP: shadow 放行+ztMode 标记",
                   r.status_code == 200
                   and r.json().get("ztMode")
                   == "shadow",
                   f"{r.status_code} {str(r.json())[:60]}")

    # 端点计数(下限断言防腐化)
    with _EnvGuard("off"):
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            spec = (await c.get(
                "/openapi.json")).json()
            paths = spec.get("paths", {})
            zt_n = sum(
                1 for p in paths
                if p.startswith("/api/map-ai/"))
            loc_n = sum(
                1 for p in paths
                if p.startswith("/api/location/"))
            record(f"端点计数: /api/map-ai/ ≥28"
                   f"(实际 {zt_n})",
                   zt_n >= 28)
            record(f"端点计数: /api/location/ ≥12"
                   f"(实际 {loc_n}——PUT/DELETE 共享"
                   f" address/{{id}} 路径)",
                   loc_n >= 12)


async def main():
    await run_service()
    await run_http()
    print("\n".join(RESULTS))
    print("-" * 56)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
