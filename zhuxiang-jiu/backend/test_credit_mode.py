"""信用管理模块 大模型二代转段专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_credit_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off 四层
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 三指标逐一恶化暂停 / 留痕 /
       resume / 未暂停恢复拒绝
    5. 状态序列化往返: 整档 JSON——paused 布尔 / metrics 列表
    6. 同步桥接(62号范式): legacy_current_mode 进程快照
    7. 护栏自动巡检: 默认 on / 间隔 / 冷启动 / 恶化暂停
    8. HTTP 决策面: off 下 8 端点全 409 + 鉴权优先
       (strict 401 / admin 403 before 409)
    9. HTTP 宪法豁免面: off 下履约与兑换权
       (paylater repay/exchange)响应不含门控特征
    10. HTTP 观测面: off 下 catalog 等不受影响
    11. HTTP shadow/assist: 放行(无门控特征)
    12. 控制面: GET /mode / override / guard / resume / 未暂停 409
    13. 端点计数: 25(21+4 控制面, 下限断言防腐化)
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.credit_mode_service import (
    CreditModeService, MODE_VALUES,
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
    """env 快照守卫(CREDIT_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("CREDIT_MODE")
        os.environ.pop("CREDIT_MODE", None)
        if self._mode:
            os.environ["CREDIT_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("CREDIT_MODE", None)
        else:
            os.environ["CREDIT_MODE"] = self._backup


async def run_service():
    global PASS, FAIL
    from repositories.store import reset_store

    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():
        reset_store()
        svc = CreditModeService()
        m = await svc.current_mode()
        record("三态: 默认 off(env 源)",
               m["mode"] == "off"
               and m["source"] == "env")
        try:
            await svc.require_decision_mode()
            record("三态: off 决策面拒绝", False)
        except ValueError as e:
            record("三态: off 决策面拒绝",
                   "CREDIT_MODE=off" in str(e))
        os.environ["CREDIT_MODE"] = "invalid"
        m = await svc.current_mode()
        record("三态: 非法 env 回落 off",
               m["mode"] == "off")
    with _EnvGuard("shadow"):
        reset_store()
        m = await CreditModeService() \
            .current_mode()
        record("三态: env shadow", m["mode"] == "shadow")
    with _EnvGuard("assist"):
        reset_store()
        m = await CreditModeService() \
            .current_mode()
        record("三态: env assist",
               m["mode"] == "assist")

    # ============================================================
    # 2. 读取链(护栏暂停 > override > env > off)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = CreditModeService()
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
        svc = CreditModeService()
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
        svc = CreditModeService()
        g = await svc.guard_check(0.10, 0.10, 0.05)
        record("护栏: 基线不暂停",
               g["breached"] is False)
        g = await svc.guard_check(0.20, 0.10, 0.05)
        record("护栏: 逾期还款率恶化暂停",
               g["breached"] is True
               and g["breaches"][0]["metric"]
               == "overdueRepayRate")
        await svc.resume(note="恢复 1")
        g = await svc.guard_check(0.10, 0.20, 0.05)
        record("护栏: paylater 拒单率恶化暂停",
               g["breached"] is True
               and g["breaches"][0]["metric"]
               == "paylaterRejectRate")
        await svc.resume(note="恢复 2")
        g = await svc.guard_check(0.10, 0.10, 0.10)
        record("护栏: 黑名单占比恶化暂停",
               g["breached"] is True
               and g["breaches"][0]["metric"]
               == "blacklistRate")
        st = await svc._state()
        record("护栏: 留痕 pausedReason",
               "黑名单占比" in st.get(
                   "pausedReason", ""))
        m = await svc.resume(note="恢复 3")
        record("护栏: resume 人工恢复",
               m["paused"] is False
               and m["mode"] == "assist")
        try:
            await svc.resume()
            record("护栏: 未暂停恢复拒绝", False)
        except ValueError:
            record("护栏: 未暂停恢复拒绝", True)

    # ============================================================
    # 5. 序列化往返(整档 JSON)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = CreditModeService()
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
        from services.credit_scheduler import (
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
               p["metrics"]["overdueRepayRate"] == 0
               and p["metrics"]
               ["paylaterRejectRate"] == 0
               and p["metrics"]["blacklistRate"] == 0
               and p["breached"] is False)
        # 造数: 账户 + 3 订单(rejected×2 + approved×1 → 拒单 0.667)
        from repositories.credit_repository import (
            CreditRepository,
        )
        repo = CreditRepository()
        await repo.save_score({
            "userId": 9001, "roleType": "member",
            "bambooScore": 500, "creditLevel": "L1",
            "creditPoints": 0, "status": "normal",
            "createdAt": "2026-09-16T00:00:00"})
        for i, st_od in enumerate(
                ("rejected", "rejected",
                 "approved")):
            await repo.add_paylater_order({
                "orderId": i + 1,
                "userId": 9001,
                "orderNo": f"PLTEST{i}",
                "amount": 100.0, "type": "goods",
                "riskLevel": "low",
                "status": st_od,
                "createdAt":
                    "2026-09-16T00:00:00"})
        p = await run_guard_patrol()
        record("巡检: 恶化指标触发暂停",
               p["metrics"]["paylaterRejectRate"]
               > 0.10
               and p["breached"] is True
               and p["pausedNow"] is True,
               str(p["metrics"]))
        m = await CreditModeService() \
            .current_mode()
        record("巡检: 暂停生效(guard_pause)",
               m["mode"] == "off"
               and m["source"] == "guard_pause")
        await CreditModeService().resume(
            note="巡检测试恢复")
    _refresh_legacy({"override": "", "paused": False})


async def run_http():
    global PASS, FAIL
    import httpx
    from main import app

    admin = {"X-Role": "admin"}
    member = {"X-Member-Id": "9002"}

    # 8 决策面请求(off 下全 409; 门控在装饰器层)
    def decision_requests():
        return [
            ("POST", "/api/credit/adjust",
             {"userId": 9002, "delta": 10,
              "reason": "t"}, admin),
            ("POST", "/api/credit/upgrade",
             {"userId": 9002,
              "targetLevel": "L2"}, admin),
            ("POST", "/api/credit/downgrade",
             {"userId": 9002,
              "targetLevel": "L1"}, admin),
            ("POST", "/api/credit/blacklist",
             {"userId": 9002}, admin),
            ("POST", "/api/credit/restore",
             {"userId": 9002}, admin),
            ("POST", "/api/credit/paylater/order",
             {"userId": 9002, "amount": 100}, member),
            ("POST", "/api/credit/paylater/review",
             {"orderId": 1,
              "approved": True}, admin),
            ("POST", "/api/credit/quarterly/settle",
             {"userId": 9002, "year": 2026,
              "quarter": 3}, admin),
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
                r = await c.request(
                    method, url, json=payload,
                    headers=hdrs)
                passed = r.status_code == 409
                n_ok += passed
                if not passed:
                    record(f"HTTP: off 409 {url}",
                           False,
                           f"got {r.status_code}")
            record(f"HTTP: 决策面 off 全 409"
                   f"({n_ok}/8)", n_ok == 8)

            # 鉴权优先(401/403 before 409)
            r = await c.post(
                "/api/credit/paylater/order",
                json={"userId": 9002, "amount": 100})
            record("HTTP: strict 面无头 401 优先",
                   r.status_code == 401,
                   f"got {r.status_code}")
            r = await c.post(
                "/api/credit/adjust",
                json={"userId": 9002, "delta": 10})
            record("HTTP: admin 面无 Role 403 优先",
                   r.status_code == 403,
                   f"got {r.status_code}")

            # 宪法豁免面 off 不受门控
            # (响应不含门控特征串——业务错误可, 门控不可)
            exempt_requests = [
                ("POST", "/api/credit/paylater/repay",
                 {"orderId": 1,
                  "repayAmount": 50}, member),
                ("POST", "/api/credit/exchange",
                 {"type": "cash", "amount": 10,
                  "item": "T"}, member),
            ]
            for method, url, payload, hdrs \
                    in exempt_requests:
                r = await c.request(
                    method, url, json=payload,
                    headers=hdrs)
                gated = "CREDIT_MODE" in str(
                    r.json().get("detail")
                    or r.json().get("error", ""))
                record(f"HTTP: 豁免面不受门控 "
                       f"{url.rsplit('/', 1)[-1]}",
                       not gated,
                       f"{r.status_code} {str(r.json())[:40]}")

            # 观测面 off 不受影响
            r = await c.get(
                "/api/credit/exchange/catalog")
            record("HTTP: 观测面 off catalog 不受限",
                   r.status_code == 200,
                   f"got {r.status_code}")

            # 控制面
            r = await c.get("/api/credit/mode",
                            headers=admin)
            body = r.json()
            record("控制面: GET /mode 形状",
                   r.status_code == 200
                   and body.get("modeValues")
                   == ["off", "shadow", "assist"]
                   and "guard" in body)
            r = await c.post(
                "/api/credit/mode/override",
                json={"mode": "shadow"},
                headers=admin)
            record("控制面: override→shadow",
                   r.status_code == 200
                   and r.json().get("mode")
                   == "shadow")
            # shadow 放行(无门控 409——业务错误可)
            r = await c.post(
                "/api/credit/paylater/order",
                json={"userId": 9002, "amount": 100},
                headers=member)
            gated = "CREDIT_MODE" in str(
                r.json().get("detail")
                or r.json().get("error", ""))
            record("HTTP: shadow 放行(无门控特征)",
                   not gated
                   and r.status_code != 409,
                   f"got {r.status_code}")
            # assist 放行
            await c.post(
                "/api/credit/mode/override",
                json={"mode": "assist"},
                headers=admin)
            r = await c.post(
                "/api/credit/paylater/order",
                json={"userId": 9002, "amount": 100},
                headers=member)
            gated = "CREDIT_MODE" in str(
                r.json().get("detail")
                or r.json().get("error", ""))
            record("HTTP: assist 放行(无门控特征)",
                   not gated
                   and r.status_code != 409,
                   f"got {r.status_code}")
            # 非法 override
            r = await c.post(
                "/api/credit/mode/override",
                json={"mode": "full"},
                headers=admin)
            record("控制面: 非法 override 409",
                   r.status_code == 409)
            # 护栏恶化暂停(直接注入指标)
            r = await c.post(
                "/api/credit/mode/guard",
                json={"overdueRepayRate": 0.9,
                      "paylaterRejectRate": 0.0,
                      "blacklistRate": 0.0},
                headers=admin)
            record("控制面: guard 三指标恶化暂停",
                   r.status_code == 200
                   and r.json().get("pausedNow")
                   is True)
            r = await c.post(
                "/api/credit/paylater/order",
                json={"userId": 9002, "amount": 100},
                headers=member)
            record("HTTP: 暂停后决策面 409",
                   r.status_code == 409)
            r = await c.post(
                "/api/credit/mode/resume",
                json={"note": "HTTP 测试恢复"},
                headers=admin)
            record("控制面: resume 恢复",
                   r.status_code == 200
                   and r.json().get("mode")
                   == "assist")
            r = await c.post(
                "/api/credit/mode/resume",
                json={}, headers=admin)
            record("控制面: 未暂停 resume 409",
                   r.status_code == 409)
            # 清除 override 回 off
            await c.post(
                "/api/credit/mode/override",
                json={"mode": ""},
                headers=admin)
            r = await c.post(
                "/api/credit/paylater/order",
                json={"userId": 9002, "amount": 100},
                headers=member)
            record("HTTP: 清除 override 回 off 409",
                   r.status_code == 409)
        _refresh_legacy({"override": "",
                         "paused": False})


async def run_counts():
    """端点计数(下限断言防腐化)"""
    from routes.credit_routes import router
    n = len(router.routes)
    record(f"端点: 路由 ≥25(实际 {n})",
           n >= 25)
    paths = {
        getattr(r, "path", "") for r in
        router.routes}
    record("端点: 控制面 4 端点在册",
           "/api/credit/mode" in paths
           and "/api/credit/mode/override" in paths
           and "/api/credit/mode/guard" in paths
           and "/api/credit/mode/resume" in paths)


async def main() -> bool:
    print("=" * 60)
    print("信用管理模块 大模型转段专项测试")
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
