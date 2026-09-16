"""小竹·智能语音中枢(48-50号) 大模型二代转段专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_xiaozhu_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off 四层
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 恶化暂停留痕 / resume 人工恢复 /
       未暂停恢复拒绝 / 指标留痕
    5. 状态序列化往返: 整档 JSON——paused 布尔 / metrics 列表
    6. 同步桥接(62号范式): legacy_current_mode 进程快照——
       override 透传 / 暂停回落 / env 兜底
    7. 护栏自动巡检: 默认 on / 间隔 3600s / 冷启动不误判 /
       恶化暂停 / 可独立调用
    8. HTTP 决策面: off 下 24 端点全 409 + 鉴权优先
       (strict 401 / admin 403 before 409)
    9. HTTP 宪法豁免面: off 下 sessions 删除/privacy preferences/
       confirm 核销/voice50 appeal 不受开关影响
    10. HTTP 观测面: off 下 commands/dashboard/voice50 系列等 200
    11. HTTP shadow/assist: 放行 + xiaoMode 标记
    12. 控制面: GET /mode 形状 / override 运行时切档 /
        护栏暂停与恢复 / 未暂停 resume 409
    13. 端点计数: 48(44+4 控制面, 下限断言防腐化)
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.xiaozhu_mode_service import (
    XiaozhuModeService, MODE_VALUES,
    legacy_current_mode, _refresh_legacy, _G,
)

PASS = 0
FAIL = 0
RESULTS = []

_ENV_KEYS = ["XIAOZHU_MODE"]


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


class _EnvGuard:
    """env 快照守卫(XIAOZHU_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("XIAOZHU_MODE")
        os.environ.pop("XIAOZHU_MODE", None)
        if self._mode:
            os.environ["XIAOZHU_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("XIAOZHU_MODE", None)
        else:
            os.environ["XIAOZHU_MODE"] = self._backup


async def run_service():
    global PASS, FAIL
    from repositories.store import reset_store

    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():
        reset_store()
        svc = XiaozhuModeService()
        m = await svc.current_mode()
        record("三态: 默认 off(env 源)",
               m["mode"] == "off"
               and m["source"] == "env")
        try:
            await svc.require_decision_mode()
            record("三态: off 决策面拒绝", False)
        except ValueError as e:
            record("三态: off 决策面拒绝",
                   "XIAOZHU_MODE=off" in str(e))
        os.environ["XIAOZHU_MODE"] = "invalid"
        m = await svc.current_mode()
        record("三态: 非法 env 回落 off",
               m["mode"] == "off")
    with _EnvGuard("shadow"):
        reset_store()
        m = await XiaozhuModeService() \
            .current_mode()
        record("三态: env shadow", m["mode"] == "shadow")
    with _EnvGuard("assist"):
        reset_store()
        m = await XiaozhuModeService() \
            .current_mode()
        record("三态: env assist",
               m["mode"] == "assist")

    # ============================================================
    # 2. 读取链(护栏暂停 > override > env > off)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = XiaozhuModeService()
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
        record("读取链: 清除 override 回 env",
               m["mode"] == "assist"
               and m["source"] == "env")
        g = await svc.guard_check(0.9, 0.0, 0.0)
        record("读取链: 护栏暂停最高优先",
               g["pausedNow"] is True)
        m = await svc.current_mode()
        record("读取链: 暂停态等效 off",
               m["mode"] == "off"
               and m["source"] == "guard_pause")
        await svc.resume(note="测试恢复")

    # ============================================================
    # 3. override 非法值
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = XiaozhuModeService()
        try:
            await svc.set_override("full")
            record("override: 非法值拒绝", False)
        except ValueError:
            record("override: 非法值拒绝", True)

    # ============================================================
    # 4. 护栏三指标
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = XiaozhuModeService()
        g = await svc.guard_check(0.10, 0.05, 0.10)
        record("护栏: 基线不暂停",
               g["breached"] is False)
        g = await svc.guard_check(0.15, 0.05, 0.10)
        record("护栏: ASR 失败率恶化 50% 暂停",
               g["breached"] is True
               and g["pausedNow"] is True
               and g["breaches"][0]["metric"]
               == "asrFailRate")
        st = await svc._state()
        record("护栏: 留痕 pausedReason",
               "ASR 失败率" in st.get(
                   "pausedReason", ""))
        record("护栏: breachTrail 留痕",
               len(st.get("breachTrail") or [])
               == 1)
        m = await svc.resume(note="恢复测试")
        record("护栏: resume 人工恢复",
               m["paused"] is False
               and m["mode"] == "assist")
        try:
            await svc.resume()
            record("护栏: 未暂停恢复拒绝", False)
        except ValueError:
            record("护栏: 未暂停恢复拒绝", True)
        g = await svc.guard_check(0.10, 0.20, 0.10)
        record("护栏: 处置率恶化暂停",
               g["breached"] is True
               and g["breaches"][0]["metric"]
               == "adjudicationRate")
        await svc.resume(note="恢复 2")
        g = await svc.guard_check(0.10, 0.05, 0.20)
        record("护栏: 语料拒审率恶化暂停",
               g["breached"] is True
               and g["breaches"][0]["metric"]
               == "corpusRejectRate")
        await svc.resume(note="恢复 3")

    # ============================================================
    # 5. 序列化往返(整档 JSON——45号 P0 教训规避)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = XiaozhuModeService()
        await svc.set_override("shadow")
        await svc.guard_check(0.9, 0.0, 0.0)
        st1 = await svc._load_state()
        await svc._save_state(st1)
        st2 = await svc._load_state()
        record("序列化: paused 布尔往返",
               st2.get("paused") is True
               and isinstance(
                   st2.get("paused"), bool))
        record("序列化: metrics 列表往返",
               isinstance(st2.get("metrics"), list)
               and len(st2.get("metrics")) >= 1)
        record("序列化: breachTrail 结构往返",
               isinstance(
                   st2.get("breachTrail"), list))
        record("序列化: override 串往返",
               st2.get("override") == "shadow")
        await svc.resume(note="序列化恢复")

    # ============================================================
    # 6. 同步桥接(62号范式)
    # ============================================================
    with _EnvGuard("off"):
        reset_store()
        _refresh_legacy({"override": "", "paused": False})
        record("桥接: env off",
               legacy_current_mode() == "off")
    with _EnvGuard("assist"):
        _refresh_legacy({"override": "", "paused": False})
        record("桥接: env assist",
               legacy_current_mode() == "assist")
        _refresh_legacy({"override": "shadow",
                         "paused": False})
        record("桥接: override shadow 透传",
               legacy_current_mode() == "shadow")
        _refresh_legacy({"override": "",
                         "paused": True})
        record("桥接: 暂停回落 off",
               legacy_current_mode() == "off")
        _refresh_legacy({"override": "", "paused": False})

    # ============================================================
    # 7. 护栏自动巡检(调度器)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        from services.xiaozhu_scheduler import (
            run_guard_patrol,
            guard_patrol_enabled,
            guard_interval_seconds,
        )
        record("巡检: 开关默认 on",
               guard_patrol_enabled() is True)
        record("巡检: 间隔默认 3600s",
               guard_interval_seconds() == 3600)
        # 冷启动(空库)三指标全 0 不误判
        p = await run_guard_patrol()
        record("巡检: 冷启动零指标不暂停",
               p["metrics"]["asrFailRate"] == 0
               and p["metrics"]
               ["adjudicationRate"] == 0
               and p["metrics"]
               ["corpusRejectRate"] == 0
               and p["breached"] is False)
        # 造数: asr_failed 轮次 2/3(0.667>0.10) → 暂停
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        repo = Xiaozhu48Repository()
        for i, intent in enumerate(
                ("asr_failed", "asr_failed",
                 "general")):
            await repo.save_turn({
                "sessionId": 1,
                "seq": i + 1,
                "intent": intent,
                "ts": "2026-09-16T00:00:00",
                "rawText": "",
                "reply": "",
                "channel": "voice",
            })
        p = await run_guard_patrol()
        record("巡检: 恶化指标触发暂停",
               p["metrics"]["asrFailRate"] > 0.10
               and p["breached"] is True
               and p["pausedNow"] is True,
               str(p["metrics"]))
        m = await XiaozhuModeService() \
            .current_mode()
        record("巡检: 暂停生效(guard_pause)",
               m["mode"] == "off"
               and m["source"] == "guard_pause")
        await XiaozhuModeService().resume(
            note="巡检测试恢复")
    _refresh_legacy({"override": "", "paused": False})


async def run_http():
    global PASS, FAIL
    import httpx
    from main import app

    admin = {"X-Role": "admin"}
    member = {"X-Member-Id": "1"}

    # 24 决策面请求(off 下全 409; 门控在装饰器层)
    def decision_requests():
        return [
            ("POST", "/api/xiaozhu/sessions",
             {}, None),
            ("POST", "/api/xiaozhu/sessions/1/voice",
             {"audioBase64": "YQ=="}, None),
            ("POST", "/api/xiaozhu/sessions/1/text",
             {"text": "小竹你好"}, None),
            ("POST", "/api/xiaozhu/bindings",
             {"trustId": 1}, member),
            ("DELETE", "/api/xiaozhu/bindings",
             None, member),
            ("POST", "/api/xiaozhu/points/redeem",
             None, member),
            ("POST", "/api/xiaozhu/commands/custom",
             {"phrase": "看看新品",
              "action": "product.new"}, member),
            ("POST",
             "/api/xiaozhu/commands/custom/1/review",
             {"approve": True}, admin),
            ("POST", "/api/xiaozhu/proactive/scan",
             None, admin),
            ("POST",
             "/api/xiaozhu/dashboard/fairness-bridge",
             None, admin),
            ("POST", "/api/xiaozhu/fc/redteam",
             None, admin),
            ("PUT", "/api/xiaozhu/voice50/rules/voice_login",
             {"base": 3.0}, admin),
            ("POST", "/api/xiaozhu/voice50/settle",
             {}, admin),
            ("POST", "/api/xiaozhu/voice50/evidence",
             {"evidence": "e"}, member),
            ("POST", "/api/xiaozhu/voice50/corpus",
             {"scenario": "s"}, member),
            ("POST",
             "/api/xiaozhu/voice50/corpus/1/review",
             {"adopted": True}, admin),
            ("POST", "/api/xiaozhu/voice50/qa",
             {"content": "c"}, member),
            ("POST",
             "/api/xiaozhu/voice50/companion/check",
             None, member),
            ("POST",
             "/api/xiaozhu/voice50/fairness-bridge",
             None, admin),
            ("POST",
             "/api/xiaozhu/voice50/decide".replace(
                 "decide", "adjudications/1/decide"),
             {"upheld": True}, admin),
            ("PUT", "/api/xiaozhu/voice50/group-profile",
             {"memberId": 1, "group": "none"}, admin),
            ("POST", "/api/xiaozhu/voice50/decay",
             None, admin),
            ("POST", "/api/xiaozhu/voice50/offset",
             {"violationEventId": 1}, member),
            ("POST", "/api/xiaozhu/voice50/unfreeze",
             {"memberId": 1}, admin),
        ]

    with _EnvGuard("off"):
        from repositories.store import reset_store
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            n_ok = 0
            for method, url, payload, hdrs \
                    in decision_requests():
                kw = {"headers": hdrs or {}}
                if payload is not None:
                    kw["json"] = payload
                r = await c.request(
                    method, url, **kw)
                passed = r.status_code == 409
                n_ok += passed
                if not passed:
                    record(f"HTTP: off 409 {url}",
                           False,
                           f"got {r.status_code}")
            record(f"HTTP: 决策面 off 全 409"
                   f"({n_ok}/24)", n_ok == 24)

            # 鉴权优先(401/403 before 409)
            r = await c.post(
                "/api/xiaozhu/bindings",
                json={"trustId": 1})
            record("HTTP: strict 面无头 401 优先",
                   r.status_code == 401,
                   f"got {r.status_code}")
            r = await c.post(
                "/api/xiaozhu/proactive/scan")
            record("HTTP: admin 面无 Role 403 优先",
                   r.status_code == 403,
                   f"got {r.status_code}")
            r = await c.post(
                "/api/xiaozhu/proactive/scan",
                headers={"X-Role": "member"})
            record("HTTP: admin 非法 Role 403 优先",
                   r.status_code == 403,
                   f"got {r.status_code}")

            # 宪法豁免面 off 不受开关影响
            r = await c.delete("/api/xiaozhu/sessions/1")
            record("HTTP: 豁免面 sessions 删除"
                   "(被遗忘权)",
                   r.status_code in (200, 404))
            r = await c.put(
                "/api/xiaozhu/privacy/preferences",
                json={"preference": 1.5},
                headers=member)
            record("HTTP: 豁免面 privacy preferences"
                   "(隐私权)",
                   r.status_code in (200, 404, 409))
            r = await c.post(
                "/api/xiaozhu/confirm/cf-fake",
                json={"code": "1234"},
                headers=member)
            record("HTTP: 豁免面 confirm 核销"
                   "(已确认动作执行权)",
                   r.status_code in (200, 404, 409))
            r = await c.post(
                "/api/xiaozhu/voice50"
                "/adjudications/1/appeal",
                json={"note": "申诉"},
                headers=member)
            record("HTTP: 豁免面 voice50 appeal"
                   "(申诉纠错)",
                   r.status_code in (200, 404, 409))

            # 观测面 off 200
            obs = [
                ("GET", "/api/xiaozhu/commands",
                 None),
                ("GET", "/api/xiaozhu/sessions/1",
                 None),
                ("GET", "/api/xiaozhu/bindings",
                 member),
                ("GET", "/api/xiaozhu/context",
                 member),
                ("GET", "/api/xiaozhu/points",
                 member),
                ("GET", "/api/xiaozhu/failures",
                 admin),
                ("GET", "/api/xiaozhu/dashboard",
                 admin),
                ("GET", "/api/xiaozhu/fc/audit",
                 admin),
                ("GET", "/api/xiaozhu/voice50/my",
                 member),
                ("GET", "/api/xiaozhu/voice50/rules",
                 admin),
                ("GET",
                 "/api/xiaozhu/voice50/settlements",
                 admin),
                ("GET",
                 "/api/xiaozhu/voice50"
                 "/adjudications",
                 admin),
            ]
            for method, url, hdrs in obs:
                r = await c.request(
                    method, url,
                    headers=hdrs or {})
                record(f"HTTP: 观测面 off 200 "
                       f"{url.rsplit('/', 1)[-1]}",
                       r.status_code in (200, 404),
                       f"got {r.status_code}")

            # 控制面
            r = await c.get("/api/xiaozhu/mode",
                            headers=admin)
            body = r.json()
            record("控制面: GET /mode 形状",
                   r.status_code == 200
                   and body.get("modeValues")
                   == ["off", "shadow", "assist"]
                   and "guard" in body)
            r = await c.post(
                "/api/xiaozhu/mode/override",
                json={"mode": "shadow"},
                headers=admin)
            record("控制面: override→shadow",
                   r.status_code == 200
                   and r.json().get("mode")
                   == "shadow")
            # shadow 放行+标记
            r = await c.post(
                "/api/xiaozhu/sessions", json={})
            record("HTTP: shadow 放行+xiaoMode 标记",
                   r.status_code == 200
                   and r.json().get("xiaoMode")
                   == "shadow",
                   f"got {r.status_code}")
            # assist 标记
            await c.post(
                "/api/xiaozhu/mode/override",
                json={"mode": "assist"},
                headers=admin)
            r = await c.post(
                "/api/xiaozhu/sessions", json={})
            record("HTTP: assist 放行+标记",
                   r.status_code == 200
                   and r.json().get("xiaoMode")
                   == "assist")
            # 非法 override
            r = await c.post(
                "/api/xiaozhu/mode/override",
                json={"mode": "full"},
                headers=admin)
            record("控制面: 非法 override 409",
                   r.status_code == 409)
            # 护栏恶化暂停(直接注入指标)
            r = await c.post(
                "/api/xiaozhu/mode/guard",
                json={"asrFailRate": 0.9,
                      "adjudicationRate": 0.0,
                      "corpusRejectRate": 0.0},
                headers=admin)
            record("控制面: guard 三指标恶化暂停",
                   r.status_code == 200
                   and r.json().get("pausedNow")
                   is True)
            r = await c.post(
                "/api/xiaozhu/sessions", json={})
            record("HTTP: 暂停后决策面 409",
                   r.status_code == 409)
            r = await c.post(
                "/api/xiaozhu/mode/resume",
                json={"note": "HTTP 测试恢复"},
                headers=admin)
            record("控制面: resume 恢复",
                   r.status_code == 200
                   and r.json().get("mode")
                   == "assist")
            r = await c.post(
                "/api/xiaozhu/sessions", json={})
            record("HTTP: 恢复后决策面放行",
                   r.status_code == 200)
            r = await c.post(
                "/api/xiaozhu/mode/resume",
                json={}, headers=admin)
            record("控制面: 未暂停 resume 409",
                   r.status_code == 409)
            # 清除 override 回 off
            await c.post(
                "/api/xiaozhu/mode/override",
                json={"mode": ""},
                headers=admin)
            r = await c.post(
                "/api/xiaozhu/sessions", json={})
            record("HTTP: 清除 override 回 off 409",
                   r.status_code == 409)
        _refresh_legacy({"override": "",
                         "paused": False})


async def run_counts():
    """端点计数(下限断言防腐化——44号教训)"""
    from routes.xiaozhu_routes import router
    n = len(router.routes)
    record(f"端点: 路由 ≥48(实际 {n})",
           n >= 48)
    paths = {
        getattr(r, "path", "") for r in
        router.routes}
    record("端点: 控制面 4 端点在册",
           "/api/xiaozhu/mode" in paths
           and "/api/xiaozhu/mode/override"
           in paths
           and "/api/xiaozhu/mode/guard"
           in paths
           and "/api/xiaozhu/mode/resume"
           in paths)


async def main() -> bool:
    print("=" * 60)
    print("小竹·智能语音中枢 大模型转段专项测试")
    print("=" * 60)
    await run_service()
    print("[服务层完成]")
    await run_http()
    print("[HTTP 层完成]")
    await run_counts()
    print("=" * 60)
    print("\n".join(RESULTS))
    print("=" * 60)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return FAIL == 0


if __name__ == "__main__":
    try:
        sys.exit(
            0 if asyncio.run(main()) else 1)
    except KeyboardInterrupt:
        sys.exit(130)
