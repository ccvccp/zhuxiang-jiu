"""智能产品管理大模型 大模型二代转段专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_pdm_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off 四层
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 三指标逐一恶化暂停 / 留痕 /
       resume / 未暂停恢复拒绝
       (三指标: 终审驳回率/图片违规标记率/AI预检拦截率
       ——流程态不进终审分母, 建议书制口径)
    5. 状态序列化往返: 整档 JSON
    6. 同步桥接(62号范式): legacy_current_mode 进程快照
    7. 护栏自动巡检: 默认 on / 间隔 / 冷启动 /
       流程态不进分母 / 终审驳回率恶化暂停
    8. HTTP 决策面: off 下 16 端点全 409(特征串法
       detail/error 双键) + 鉴权优先(strict 401)
    9. HTTP 安全阀豁免: off 下 force-delist
       响应不含门控特征(安全处置权永不关停)
    10. HTTP 观测面: off 下不受影响
    11. HTTP shadow/assist: 放行 + pdmMode 标记
    12. 控制面: GET /mode / override / guard / resume
    13. 端点计数: 30(26+4, 下限断言防腐化)
       + 产品展示基础 7 端点零破坏
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.pdm_mode_service import (
    PdmModeService, MODE_VALUES,
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
    """env 快照守卫(PDM_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("PDM_MODE")
        os.environ.pop("PDM_MODE", None)
        if self._mode:
            os.environ["PDM_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("PDM_MODE", None)
        else:
            os.environ["PDM_MODE"] = self._backup


async def run_service():
    global PASS, FAIL
    from repositories.store import reset_store

    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():
        reset_store()
        svc = PdmModeService()
        m = await svc.current_mode()
        record("三态: 默认 off(env 源)",
               m["mode"] == "off"
               and m["source"] == "env")
        try:
            await svc.require_decision_mode()
            record("三态: off 决策面拒绝", False)
        except ValueError as e:
            record("三态: off 决策面拒绝",
                   "PDM_MODE=off" in str(e))
        os.environ["PDM_MODE"] = "invalid"
        m = await svc.current_mode()
        record("三态: 非法 env 回落 off",
               m["mode"] == "off")
    with _EnvGuard("shadow"):
        reset_store()
        m = await PdmModeService() \
            .current_mode()
        record("三态: env shadow", m["mode"] == "shadow")
    with _EnvGuard("assist"):
        reset_store()
        m = await PdmModeService() \
            .current_mode()
        record("三态: env assist",
               m["mode"] == "assist")

    # ============================================================
    # 2. 读取链(护栏暂停 > override > env > off)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = PdmModeService()
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
        svc = PdmModeService()
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
        svc = PdmModeService()
        r = await svc.guard_check(0.10, 0.10, 0.10)
        record("护栏: 基线持平不暂停",
               r["breached"] is False
               and r["pausedNow"] is False)
        # 终审驳回率恶化
        r = await svc.guard_check(0.20, 0.10, 0.10)
        record("护栏: 终审驳回率恶化暂停",
               r["breached"] is True
               and r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "manualRejectRate")
        st_trail = await svc._state()
        record("护栏: 暂停留痕",
               bool(st_trail["paused"])
               and "终审驳回率" in st_trail["pausedReason"]
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
        # 图片违规标记率恶化
        r = await svc.guard_check(0.10, 0.30, 0.10)
        record("护栏: 图片违规标记率恶化暂停",
               r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "imageFlagRate")
        await svc.resume(note="恢复")
        # AI 预检拦截率恶化
        r = await svc.guard_check(0.10, 0.10, 0.40)
        record("护栏: AI预检拦截率恶化暂停",
               r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "aiRejectRate")
        await svc.resume(note="恢复")
        # 基线为 0 时绝对值恶化(防除零)
        r = await svc.guard_check(
            0.0, 0.0, 0.0,
            baseline={"manualRejectRate": 0.0,
                      "imageFlagRate": 0.10,
                      "aiRejectRate": 0.10})
        record("护栏: 零基线防除零不误判",
               r["breached"] is False)
        r = await svc.guard_check(
            0.01, 0.0, 0.0,
            baseline={"manualRejectRate": 0.0,
                      "imageFlagRate": 0.10,
                      "aiRejectRate": 0.10})
        record("护栏: 零基线绝对恶化暂停",
               r["pausedNow"] is True)
        await svc.resume(note="恢复")

    # ============================================================
    # 5. 状态序列化往返(整档 JSON)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = PdmModeService()
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
               and "终审驳回率" in st2["breachTrail"][0]["reason"])
        record("序列化: 指标留痕滚动截断",
               st2["metrics"][0]["metrics"]["manualRejectRate"]
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
        svc = PdmModeService()
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
    # 7. 护栏自动巡检(PDM 仓储聚合)
    # ============================================================
    with _EnvGuard("assist"):
        from services.pdm_scheduler import (
            guard_patrol_enabled,
            guard_interval_seconds,
            run_guard_patrol,
        )
        from repositories.pdm_repository import (
            PdmRepository,
        )
        record("巡检: 默认 on",
               guard_patrol_enabled() is True)
        os.environ["PDM_GUARD_AUTO"] = "off"
        record("巡检: 开关可关",
               guard_patrol_enabled() is False)
        os.environ["PDM_GUARD_AUTO"] = "on"
        os.environ["PDM_GUARD_INTERVAL"] = "120"
        record("巡检: 间隔可调(120s)",
               guard_interval_seconds() == 120)
        os.environ["PDM_GUARD_INTERVAL"] = "bad"
        record("巡检: 非法间隔回落 3600",
               guard_interval_seconds() == 3600)
        os.environ.pop("PDM_GUARD_INTERVAL", None)
        os.environ.pop("PDM_GUARD_AUTO", None)

        reset_store()
        repo = PdmRepository()
        # 冷启动(无商品/无图片)——不误暂停
        r = await run_guard_patrol()
        record("巡检: 冷启动不误判",
               r["metrics"]["manualRejectRate"] == 0.0
               and r["metrics"]["imageFlagRate"] == 0.0
               and r["metrics"]["aiRejectRate"] == 0.0
               and r["pausedNow"] is False)
        # 口径铁律: 流程态(draft/ai_reviewing/
        # manual_reviewing)不进终审分母——不误暂停
        for pid in ("T-MODE-01", "T-MODE-02",
                    "T-MODE-03"):
            await repo.save_pdm_product({
                "productId": pid,
                "status": "manual_reviewing",
                "name": "流程态口径测试"})
        r = await run_guard_patrol()
        record("巡检: 流程态不进终审分母(建议书制)",
               r["metrics"]["manualRejectRate"] == 0.0
               and r["pausedNow"] is False,
               str(r["metrics"]))
        # 造数: 2 rejected + 1 on_sale
        # (终审驳回率 2/3=0.667 恶化)
        await repo.save_pdm_product({
            "productId": "T-MODE-04",
            "status": "rejected", "name": "驳回1"})
        await repo.save_pdm_product({
            "productId": "T-MODE-05",
            "status": "rejected", "name": "驳回2"})
        await repo.save_pdm_product({
            "productId": "T-MODE-06",
            "status": "on_sale", "name": "在售1"})
        r = await run_guard_patrol()
        record("巡检: 终审驳回率聚合恶化暂停",
               abs(r["metrics"]["manualRejectRate"]
                   - 2 / 3) < 1e-3
               and r["breached"] is True
               and r["pausedNow"] is True,
               str(r["metrics"]))
        m = await PdmModeService().current_mode()
        record("巡检: 暂停生效(guard_pause)",
               m["mode"] == "off"
               and m["source"] == "guard_pause")
        await PdmModeService().resume(
            note="巡检测试恢复")
    _refresh_legacy({"override": "", "paused": False})


async def run_http():
    global PASS, FAIL
    import httpx
    from main import app

    admin = {"X-Member-Id": "1", "X-Role": "admin"}

    # 16 决策面请求(off 下全 409; 门控在装饰器层)
    def decision_requests():
        return [
            ("POST", "/api/pdm/products",
             {"name": "门控测试", "price": 10}),
            ("PUT", "/api/pdm/products/T-900",
             {"name": "门控编辑"}),
            ("POST", "/api/pdm/products/T-900/submit", None),
            ("POST", "/api/pdm/products/T-900/ai-precheck",
             None),
            ("POST", "/api/pdm/products/T-900/review",
             {"approved": True}),
            ("POST", "/api/pdm/products/T-900/list", None),
            ("POST", "/api/pdm/products/T-900/delist",
             {"reason": "门控测试"}),
            ("POST", "/api/pdm/products/T-900/versions/rollback",
             {"version": 1}),
            ("POST", "/api/pdm/images",
             {"dataBase64": "aGVsbG8="}),
            ("POST", "/api/pdm/images/1/reupload",
             {"dataBase64": "aGVsbG8="}),
            ("POST", "/api/pdm/images/1/destroy", None),
            ("POST", "/api/pdm/products/T-900/learning-feedback",
             {"decision": "approve"}),
            ("POST", "/api/pdm/products/T-900/design/"
             "generate-main-image", None),
            ("POST", "/api/pdm/products/T-900/design/"
             "copy-optimize", None),
            ("PUT", "/api/pdm/products/T-900/images",
             {"main": "http://t/x.png"}),
            ("POST", "/api/pdm/products/T-900/images/rollback",
             {"version": 1}),
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
                gated = "PDM_MODE" in str(
                    body.get("detail")
                    or body.get("error", ""))
                passed = r.status_code == 409 and gated
                n_ok += passed
                if not passed:
                    record(f"HTTP: off 409 {url}",
                           False,
                           f"got {r.status_code}")
            record(f"HTTP: 决策面 off 全 409"
                   f"({n_ok}/16)", n_ok == 16)

            # 鉴权优先(401 before 409)
            r = await c.post(
                "/api/pdm/products",
                json={"name": "t", "price": 10})
            record("HTTP: strict 面无头 401 优先",
                   r.status_code == 401,
                   f"got {r.status_code}")

            # 安全阀豁免: force-delist off 不受门控
            # (响应不含门控特征串——业务错误可)
            r = await c.post(
                "/api/pdm/products/T-900/force-delist",
                json={"reason": "安全阀测试"},
                headers=admin)
            body = r.json()
            gated = "PDM_MODE" in str(
                body.get("detail")
                or body.get("error", ""))
            record("HTTP: 安全阀 force-delist 不受门控",
                   not gated and r.status_code != 409,
                   f"{r.status_code} {str(body)[:60]}")

            # 观测面 off 不受影响
            for url in ("/api/pdm/products",
                        "/api/pdm/report/overview"):
                r = await c.get(url, headers=admin)
                record(f"HTTP: 观测面 off 不受限 "
                       f"{url.rsplit('/', 1)[-1]}",
                       r.status_code == 200,
                       f"got {r.status_code}")

            # 控制面
            r = await c.get("/api/pdm/mode",
                            headers={"X-Role": "admin"})
            body = r.json()
            record("控制面: GET /mode 形状",
                   r.status_code == 200
                   and body.get("modeValues")
                   == ["off", "shadow", "assist"]
                   and "guard" in body)
            r = await c.get("/api/pdm/mode")
            record("控制面: GET /mode admin 门禁",
                   r.status_code == 403)
            r = await c.post(
                "/api/pdm/mode/override",
                json={"mode": "shadow"},
                headers={"X-Role": "admin"})
            record("控制面: override→shadow",
                   r.status_code == 200
                   and r.json()["data"]["mode"]
                   == "shadow")
            r = await c.post(
                "/api/pdm/mode/override",
                json={"mode": "bogus"},
                headers={"X-Role": "admin"})
            record("控制面: 非法 override 409",
                   r.status_code == 409)
            r = await c.post(
                "/api/pdm/mode/override",
                json={"mode": ""},
                headers={"X-Role": "admin"})
            record("控制面: 清除 override 回落 env",
                   r.status_code == 200
                   and r.json()["data"]["mode"] == "off")
            r = await c.post(
                "/api/pdm/mode/resume",
                headers={"X-Role": "admin"})
            record("控制面: 未暂停 resume 409",
                   r.status_code == 409)

            # 显式 guard 指标(恶化→暂停→resume)
            r = await c.post(
                "/api/pdm/mode/guard",
                json={"manualRejectRate": 0.5,
                      "imageFlagRate": 0.1,
                      "aiRejectRate": 0.1},
                headers={"X-Role": "admin"})
            record("控制面: guard 显式恶化暂停",
                   r.status_code == 200
                   and r.json()["data"]["pausedNow"]
                   is True)
            r = await c.post(
                "/api/pdm/mode/resume",
                headers={"X-Role": "admin"})
            record("控制面: resume 恢复",
                   r.status_code == 200
                   and r.json()["data"]["paused"]
                   is False)

    # shadow/assist 放行 + pdmMode 标记
    with _EnvGuard("assist"):
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            r = await c.post(
                "/api/pdm/products",
                json={"name": "标记测试", "price": 10},
                headers=admin)
            record("HTTP: assist 放行+pdmMode 标记",
                   r.status_code == 200
                   and r.json().get("pdmMode")
                   == "assist",
                   f"{r.status_code} {str(r.json())[:60]}")
    with _EnvGuard("shadow"):
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            r = await c.post(
                "/api/pdm/products",
                json={"name": "标记测试", "price": 10},
                headers=admin)
            record("HTTP: shadow 放行+pdmMode 标记",
                   r.status_code == 200
                   and r.json().get("pdmMode")
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
            # 方法级计数(3 处 POST/GET、GET/PUT 共享路径)
            pdm_methods = sum(
                len(paths[p])
                for p in paths
                if p.startswith("/api/pdm/"))
            pdm_paths = sum(
                1 for p in paths
                if p.startswith("/api/pdm/"))
            prod_n = sum(
                1 for p in paths
                if p.startswith("/api/product"))
            record(f"端点计数: /api/pdm/ ≥30 方法"
                   f"(实际 {pdm_methods} 方法/"
                   f"{pdm_paths} 路径)",
                   pdm_methods >= 30)
            record(f"端点计数: /api/product ≥7"
                   f"(实际 {prod_n})",
                   prod_n >= 7)


async def main():
    await run_service()
    await run_http()
    print("\n".join(RESULTS))
    print("-" * 56)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
