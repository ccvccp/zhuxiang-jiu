"""网站图标智能管理大模型 大模型二代转段专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    $env:AUTH_MODE="compat"; python test_site_theme_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off 四层
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 三指标逐一恶化暂停 / 留痕 /
       resume / 未暂停恢复拒绝
       (三指标: AI低分率/回滚率/图标失效引用率)
    5. 状态序列化往返: 整档 JSON
    6. 同步桥接(62号范式): legacy_current_mode 进程快照
    7. 护栏自动巡检: 默认 on / 间隔 / 冷启动 /
       AI低分率聚合恶化暂停
    8. HTTP 决策面: off 下 7 端点全 409(特征串法
       detail/error 双键, JWT Depends 鉴权天然优先)
    9. HTTP C 端运行时换肤: off 下 active/icons 公开 200
    10. HTTP 鉴权优先: 无 Token 401 先于门控 409
    11. HTTP shadow/assist: 放行 + themeMode 标记
    12. 控制面: GET /mode / override / guard / resume
    13. 端点计数: 16(12+4 控制面, 下限断言防腐化)
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")

from services.site_theme_mode_service import (
    SiteThemeModeService, MODE_VALUES,
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
    """env 快照守卫(SITE_THEME_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("SITE_THEME_MODE")
        os.environ.pop("SITE_THEME_MODE", None)
        if self._mode:
            os.environ["SITE_THEME_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("SITE_THEME_MODE", None)
        else:
            os.environ["SITE_THEME_MODE"] = self._backup


async def run_service():
    global PASS, FAIL
    from repositories.store import reset_store

    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():
        reset_store()
        svc = SiteThemeModeService()
        m = await svc.current_mode()
        record("三态: 默认 off(env 源)",
               m["mode"] == "off"
               and m["source"] == "env")
        try:
            await svc.require_decision_mode()
            record("三态: off 决策面拒绝", False)
        except ValueError as e:
            record("三态: off 决策面拒绝",
                   "SITE_THEME_MODE=off" in str(e))
        os.environ["SITE_THEME_MODE"] = "invalid"
        m = await svc.current_mode()
        record("三态: 非法 env 回落 off",
               m["mode"] == "off")
    with _EnvGuard("shadow"):
        reset_store()
        m = await SiteThemeModeService() \
            .current_mode()
        record("三态: env shadow", m["mode"] == "shadow")
    with _EnvGuard("assist"):
        reset_store()
        m = await SiteThemeModeService() \
            .current_mode()
        record("三态: env assist",
               m["mode"] == "assist")

    # ============================================================
    # 2. 读取链(护栏暂停 > override > env > off)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = SiteThemeModeService()
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
        # 恶化暂停(直接调 guard_check——低分率爆表)
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
        svc = SiteThemeModeService()
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
        svc = SiteThemeModeService()
        r = await svc.guard_check(0.10, 0.10, 0.10)
        record("护栏: 基线持平不暂停",
               r["breached"] is False
               and r["pausedNow"] is False)
        # AI 低分率恶化
        r = await svc.guard_check(0.20, 0.10, 0.10)
        record("护栏: AI低分率恶化暂停",
               r["breached"] is True
               and r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "aiLowScoreRate")
        st_trail = await svc._state()
        record("护栏: 暂停留痕",
               bool(st_trail["paused"])
               and "AI 低分率" in st_trail["pausedReason"]
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
        # 回滚率恶化
        r = await svc.guard_check(0.10, 0.30, 0.10)
        record("护栏: 回滚率恶化暂停",
               r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "rollbackRate")
        await svc.resume(note="恢复")
        # 图标失效引用率恶化
        r = await svc.guard_check(0.10, 0.10, 0.40)
        record("护栏: 图标失效引用率恶化暂停",
               r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "brokenIconRefRate")
        await svc.resume(note="恢复")
        # 基线为 0 时绝对值恶化(防除零)
        r = await svc.guard_check(
            0.0, 0.0, 0.0,
            baseline={"aiLowScoreRate": 0.0,
                      "rollbackRate": 0.10,
                      "brokenIconRefRate": 0.10})
        record("护栏: 零基线防除零不误判",
               r["breached"] is False)
        r = await svc.guard_check(
            0.01, 0.0, 0.0,
            baseline={"aiLowScoreRate": 0.0,
                      "rollbackRate": 0.10,
                      "brokenIconRefRate": 0.10})
        record("护栏: 零基线绝对恶化暂停",
               r["pausedNow"] is True)
        await svc.resume(note="恢复")

    # ============================================================
    # 5. 状态序列化往返(整档 JSON)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = SiteThemeModeService()
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
               and "AI 低分率" in st2["breachTrail"][0]["reason"])
        record("序列化: 指标留痕滚动截断",
               st2["metrics"][0]["metrics"]["aiLowScoreRate"]
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
        svc = SiteThemeModeService()
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
    # 7. 护栏自动巡检(图标主题仓储聚合)
    # ============================================================
    with _EnvGuard("assist"):
        from services.site_theme_scheduler import (
            guard_patrol_enabled,
            guard_interval_seconds,
            run_guard_patrol,
        )
        from repositories.site_theme_repository \
            import SiteThemeRepository
        record("巡检: 默认 on",
               guard_patrol_enabled() is True)
        os.environ["SITE_THEME_GUARD_AUTO"] = "off"
        record("巡检: 开关可关",
               guard_patrol_enabled() is False)
        os.environ["SITE_THEME_GUARD_AUTO"] = "on"
        os.environ["SITE_THEME_GUARD_INTERVAL"] = "120"
        record("巡检: 间隔可调(120s)",
               guard_interval_seconds() == 120)
        os.environ["SITE_THEME_GUARD_INTERVAL"] = "bad"
        record("巡检: 非法间隔回落 3600",
               guard_interval_seconds() == 3600)
        os.environ.pop("SITE_THEME_GUARD_INTERVAL", None)
        os.environ.pop("SITE_THEME_GUARD_AUTO", None)

        reset_store()
        repo = SiteThemeRepository()

        async def seed_theme(name: str, score) -> None:
            tid = await repo.next_theme_id()
            await repo.save_theme({
                "themeId": tid, "name": name,
                "status": "draft", "colors": {},
                "icons": {}, "description": "",
                "aiCheck": (
                    {"score": score} if score is not None
                    else None)})

        # 冷启动(无主题/无日志/无图标)——不误暂停
        r = await run_guard_patrol()
        record("巡检: 冷启动不误判",
               r["metrics"]["aiLowScoreRate"] == 0.0
               and r["metrics"]["rollbackRate"] == 0.0
               and r["metrics"]["brokenIconRefRate"] == 0.0
               and r["pausedNow"] is False)
        # 造数: 3 已评估(1 低分 40 + 2 高分 80/90)
        # (AI 低分率 1/3=0.333 未恶化? 基线 0.10 →
        #  0.333 恶化 → 暂停)
        await seed_theme("低分主题", 40.0)
        await seed_theme("高分一", 80.0)
        await seed_theme("高分二", 90.0)
        r = await run_guard_patrol()
        record("巡检: AI低分率聚合恶化暂停",
               abs(r["metrics"]["aiLowScoreRate"]
                   - 1 / 3) < 1e-3
               and r["breached"] is True
               and r["pausedNow"] is True,
               str(r["metrics"]))
        m = await SiteThemeModeService().current_mode()
        record("巡检: 暂停生效(guard_pause)",
               m["mode"] == "off"
               and m["source"] == "guard_pause")
        await SiteThemeModeService().resume(
            note="巡检测试恢复")
    _refresh_legacy({"override": "", "paused": False})


async def run_http():
    global PASS, FAIL
    import httpx
    from main import app
    from core.auth import hash_password
    from repositories.member_repository import (
        MemberRepository,
    )
    from services.auth_service import AuthService

    # 构造 admin 会员 + JWT
    async def _mk_token() -> str:
        repo = MemberRepository()
        await repo.create({
            "phone": "13900000077", "nickname": "模式测试",
            "password": hash_password("x" * 64),
            "status": 1, "role": "admin", "level": 3,
            "growth_value": 600, "points": 0})
        result = await AuthService().login(
            phone="13900000077", password="x" * 64)
        return result["accessToken"]

    # 合法配色(通过 AI 健康度)
    good_colors = {
        "primary": "#355c44", "primaryLight": "#4a7c59",
        "navBar": "#355c44", "tabSelected": "#355c44",
        "tabColor": "#999999", "tabBg": "#ffffff",
        "textOnPrimary": "#ffffff"}

    # 7 决策面请求(off 下全 409)
    def decision_requests(token: str):
        h = {"Authorization": f"Bearer {token}"}
        return [
            ("POST", "/api/site-theme/themes",
             {"name": "门控测试", "colors": good_colors}, h),
            ("PUT", "/api/site-theme/themes/1",
             {"description": "门控编辑"}, h),
            ("POST", "/api/site-theme/themes/1/ai-check",
             None, h),
            ("POST", "/api/site-theme/themes/1/activate",
             None, h),
            ("POST", "/api/site-theme/themes/1/archive",
             None, h),
            ("POST", "/api/site-theme/admin/logs/1/rollback",
             None, h),
            ("POST", "/api/site-theme/admin/icons",
             {"emoji": "\U0001F389"}, h),
        ]

    with _EnvGuard("off"):
        from repositories.store import reset_store
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        token = await _mk_token()
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            n_ok = 0
            for method, url, payload, hdrs \
                    in decision_requests(token):
                r = await c.request(
                    method, url, json=payload,
                    headers=hdrs)
                body = r.json()
                gated = "SITE_THEME_MODE" in str(
                    body.get("detail")
                    or body.get("error", ""))
                passed = r.status_code == 409 and gated
                n_ok += passed
                if not passed:
                    record(f"HTTP: off 409 {url}",
                           False,
                           f"got {r.status_code}")
            record(f"HTTP: 决策面 off 全 409"
                   f"({n_ok}/7)", n_ok == 7)

            # 鉴权优先(无 Token 401 before 409)
            r = await c.post(
                "/api/site-theme/themes",
                json={"name": "无鉴权",
                      "colors": good_colors})
            record("HTTP: 无 Token 401 优先",
                   r.status_code == 401,
                   f"got {r.status_code}")

            # C 端运行时换肤 off 不受影响(公开)
            for url in ("/api/site-theme/active",
                        "/api/site-theme/icons"):
                r = await c.get(url)
                record(f"HTTP: C端换肤 off 不受限 "
                       f"{url.rsplit('/', 1)[-1]}",
                       r.status_code == 200,
                       f"got {r.status_code}")

            # 控制面
            auth = {"Authorization": f"Bearer {token}"}
            r = await c.get("/api/site-theme/mode",
                            headers=auth)
            body = r.json()
            record("控制面: GET /mode 形状",
                   r.status_code == 200
                   and body.get("modeValues")
                   == ["off", "shadow", "assist"]
                   and "guard" in body)
            r = await c.get("/api/site-theme/mode")
            record("控制面: GET /mode admin 门禁",
                   r.status_code == 401)
            r = await c.post(
                "/api/site-theme/mode/override",
                json={"mode": "shadow"}, headers=auth)
            record("控制面: override→shadow",
                   r.status_code == 200
                   and r.json()["data"]["mode"]
                   == "shadow")
            r = await c.post(
                "/api/site-theme/mode/override",
                json={"mode": "bogus"}, headers=auth)
            record("控制面: 非法 override 409",
                   r.status_code == 409)
            r = await c.post(
                "/api/site-theme/mode/override",
                json={"mode": ""}, headers=auth)
            record("控制面: 清除 override 回落 env",
                   r.status_code == 200
                   and r.json()["data"]["mode"] == "off")
            r = await c.post(
                "/api/site-theme/mode/resume",
                headers=auth)
            record("控制面: 未暂停 resume 409",
                   r.status_code == 409)

            # 显式 guard 指标(恶化→暂停→resume)
            r = await c.post(
                "/api/site-theme/mode/guard",
                json={"aiLowScoreRate": 0.5,
                      "rollbackRate": 0.1,
                      "brokenIconRefRate": 0.1},
                headers=auth)
            record("控制面: guard 显式恶化暂停",
                   r.status_code == 200
                   and r.json()["data"]["pausedNow"]
                   is True)
            r = await c.post(
                "/api/site-theme/mode/resume",
                headers=auth)
            record("控制面: resume 恢复",
                   r.status_code == 200
                   and r.json()["data"]["paused"]
                   is False)

    # assist 放行 + themeMode 标记
    with _EnvGuard("assist"):
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        token = await _mk_token()
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            r = await c.post(
                "/api/site-theme/themes",
                json={"name": "标记测试",
                      "colors": good_colors},
                headers={"Authorization":
                         f"Bearer {token}"})
            record("HTTP: assist 放行+themeMode 标记",
                   r.status_code == 200
                   and r.json().get("themeMode")
                   == "assist",
                   f"{r.status_code} "
                   f"{str(r.json())[:60]}")

    # 端点计数(下限断言防腐化, 方法级)
    with _EnvGuard("off"):
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            spec = (await c.get(
                "/openapi.json")).json()
            paths = spec.get("paths", {})
            st_methods = sum(
                len(paths[p])
                for p in paths
                if p.startswith("/api/site-theme"))
            record(f"端点计数: /api/site-theme ≥16 方法"
                   f"(实际 {st_methods})",
                   st_methods >= 16)


async def main():
    await run_service()
    await run_http()
    print("\n".join(RESULTS))
    print("-" * 56)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
