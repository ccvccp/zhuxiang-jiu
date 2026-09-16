"""钱包盈利模块 大模型二代转段专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_wallet_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off 四层
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 三指标逐一恶化暂停 / 留痕 /
       resume / 未暂停恢复拒绝
    5. 状态序列化往返: 整档 JSON——paused 布尔 / metrics 列表
    6. 同步桥接(62号范式): legacy_current_mode 进程快照
    7. 护栏自动巡检: 默认 on / 间隔 / 冷启动 / 恶化暂停
    8. HTTP 决策面: off 下 6 端点全 409 + 鉴权优先
       (strict 401 / admin 403 before 409)
    9. HTTP 宪法豁免面: off 下资金退出权
       (withdraw/refund/settle/early-settle/claim/sign/approve/paid)
       响应不含门控特征(WALLET_MODE)——业务错误可, 门控不可
    10. HTTP 观测面: off 下 info/rules/transactions 等 200
    11. HTTP shadow/assist: 放行 + walletMode 标记
    12. 控制面: GET /mode / override / guard / resume / 未暂停 409
    13. 端点计数: 26(22+4 控制面, 下限断言防腐化)
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.wallet_mode_service import (
    WalletModeService, MODE_VALUES,
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
    """env 快照守卫(WALLET_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("WALLET_MODE")
        os.environ.pop("WALLET_MODE", None)
        if self._mode:
            os.environ["WALLET_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("WALLET_MODE", None)
        else:
            os.environ["WALLET_MODE"] = self._backup


async def run_service():
    global PASS, FAIL
    from repositories.store import reset_store

    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():
        reset_store()
        svc = WalletModeService()
        m = await svc.current_mode()
        record("三态: 默认 off(env 源)",
               m["mode"] == "off"
               and m["source"] == "env")
        try:
            await svc.require_decision_mode()
            record("三态: off 决策面拒绝", False)
        except ValueError as e:
            record("三态: off 决策面拒绝",
                   "WALLET_MODE=off" in str(e))
        os.environ["WALLET_MODE"] = "invalid"
        m = await svc.current_mode()
        record("三态: 非法 env 回落 off",
               m["mode"] == "off")
    with _EnvGuard("shadow"):
        reset_store()
        m = await WalletModeService() \
            .current_mode()
        record("三态: env shadow", m["mode"] == "shadow")
    with _EnvGuard("assist"):
        reset_store()
        m = await WalletModeService() \
            .current_mode()
        record("三态: env assist",
               m["mode"] == "assist")

    # ============================================================
    # 2. 读取链(护栏暂停 > override > env > off)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = WalletModeService()
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
        svc = WalletModeService()
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
        svc = WalletModeService()
        g = await svc.guard_check(0.10, 0.10, 0.05)
        record("护栏: 基线不暂停",
               g["breached"] is False)
        g = await svc.guard_check(0.20, 0.10, 0.05)
        record("护栏: 提现拒绝率恶化暂停",
               g["breached"] is True
               and g["breaches"][0]["metric"]
               == "withdrawRejRate")
        await svc.resume(note="恢复 1")
        g = await svc.guard_check(0.10, 0.20, 0.05)
        record("护栏: 提前取出率恶化暂停",
               g["breached"] is True
               and g["breaches"][0]["metric"]
               == "earlySettleRate")
        await svc.resume(note="恢复 2")
        g = await svc.guard_check(0.10, 0.10, 0.10)
        record("护栏: 账户冻结率恶化暂停",
               g["breached"] is True
               and g["breaches"][0]["metric"]
               == "accountFrozenRate")
        st = await svc._state()
        record("护栏: 留痕 pausedReason",
               "账户冻结率" in st.get(
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
        svc = WalletModeService()
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
        from services.wallet_scheduler import (
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
               p["metrics"]["withdrawRejRate"] == 0
               and p["metrics"]["earlySettleRate"] == 0
               and p["metrics"]["accountFrozenRate"] == 0
               and p["breached"] is False)
        # 造数: 账户 + 3 提现(rejected×2 + paid×1 → 0.667)
        from repositories.wallet_repository import (
            WalletRepository,
        )
        repo = WalletRepository()
        await repo.save_account(9001, {
            "userId": 9001, "status": "active",
            "balance": 0.0, "frozenAmount": 0.0,
            "totalDeposit": 0.0, "totalWithdraw": 0.0,
            "totalInterest": 0.0, "totalReward": 0.0,
            "totalRebate": 0.0,
            "createdAt": "2026-09-16T00:00:00"})
        for i, st_wd in enumerate(
                ("rejected", "rejected", "paid")):
            await repo.save_withdrawal({
                "withdrawNo": f"WDTEST{i}",
                "userId": 9001,
                "amount": 100.0,
                "fee": 0.0,
                "actualAmount": 100.0,
                "source": "current",
                "payChannel": "bank",
                "status": st_wd,
                "createdAt":
                    "2026-09-16T00:00:00"})
        p = await run_guard_patrol()
        record("巡检: 恶化指标触发暂停",
               p["metrics"]["withdrawRejRate"] > 0.10
               and p["breached"] is True
               and p["pausedNow"] is True,
               str(p["metrics"]))
        m = await WalletModeService() \
            .current_mode()
        record("巡检: 暂停生效(guard_pause)",
               m["mode"] == "off"
               and m["source"] == "guard_pause")
        await WalletModeService().resume(
            note="巡检测试恢复")
    _refresh_legacy({"override": "", "paused": False})


async def run_http():
    global PASS, FAIL
    import httpx
    from main import app

    admin = {"X-Role": "admin"}
    member = {"X-Member-Id": "9002"}

    # 6 决策面请求(off 下全 409; 门控在装饰器层)
    def decision_requests():
        return [
            ("POST", "/api/wallet/open",
             {}, member, "strict"),
            ("POST", "/api/wallet/deposit",
             {"amount": 1000,
              "payChannel": "alipay"}, member, "strict"),
            ("POST", "/api/wallet/pay",
             {"amount": 100}, member, "strict"),
            ("POST", "/api/wallet/transfer-regular",
             {"amount": 5000, "period": 12},
             member, "strict"),
            ("POST",
             "/api/wallet/interest/settle-monthly",
             None, member, "strict"),
            ("POST", "/api/wallet/reward/RW1/ship",
             {"waybillNo": "SF1"}, admin, "admin"),
        ]

    with _EnvGuard("off"):
        from repositories.store import reset_store
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            n_ok = 0
            for method, url, payload, hdrs, _ \
                    in decision_requests():
                kw = {"headers": hdrs}
                if payload is not None:
                    kw["json"] = payload
                r = await c.request(method, url, **kw)
                passed = r.status_code == 409
                n_ok += passed
                if not passed:
                    record(f"HTTP: off 409 {url}",
                           False,
                           f"got {r.status_code}")
            record(f"HTTP: 决策面 off 全 409"
                   f"({n_ok}/6)", n_ok == 6)

            # 鉴权优先(401/403 before 409)
            r = await c.post("/api/wallet/open", json={})
            record("HTTP: strict 面无头 401 优先",
                   r.status_code == 401,
                   f"got {r.status_code}")
            r = await c.post(
                "/api/wallet/reward/RW1/ship",
                json={"waybillNo": "SF1"})
            record("HTTP: admin 面无 Role 403 优先",
                   r.status_code == 403,
                   f"got {r.status_code}")

            # 宪法豁免面 off 不受门控
            # (响应不含门控特征串——业务错误可, 门控不可)
            exempt_requests = [
                ("POST", "/api/wallet/withdraw",
                 {"amount": 100}, member),
                ("POST", "/api/wallet/refund",
                 {"amount": 10}, member),
                ("POST",
                 "/api/wallet/deposit/DP1/settle",
                 None, member),
                ("POST",
                 "/api/wallet/deposit/DP1/early-settle",
                 None, member),
                ("POST", "/api/wallet/reward/RW1/claim",
                 {"addressId": 0}, member),
                ("POST", "/api/wallet/reward/RW1/sign",
                 None, member),
                ("POST",
                 "/api/wallet/withdrawal/WD1/approve",
                 {"decision": "approved",
                  "auditor": "admin"}, admin),
                ("POST",
                 "/api/wallet/withdrawal/WD1/paid",
                 None, admin),
            ]
            for method, url, payload, hdrs \
                    in exempt_requests:
                kw = {"headers": hdrs}
                if payload is not None:
                    kw["json"] = payload
                r = await c.request(method, url, **kw)
                gated = "WALLET_MODE" in str(
                    r.json().get("detail", ""))
                record(f"HTTP: 豁免面不受门控 "
                       f"{url.rsplit('/', 1)[-1]}",
                       not gated,
                       f"{r.status_code} {r.json().get('detail', '')[:40]}")

            # 观测面 off 200(公开 rules + member 观测)
            r = await c.get("/api/wallet/interest/rules")
            record("HTTP: 观测面 off 200 rules(公开)",
                   r.status_code == 200,
                   f"got {r.status_code}")

            # 控制面
            r = await c.get("/api/wallet/mode",
                            headers=admin)
            body = r.json()
            record("控制面: GET /mode 形状",
                   r.status_code == 200
                   and body.get("modeValues")
                   == ["off", "shadow", "assist"]
                   and "guard" in body)
            r = await c.post(
                "/api/wallet/mode/override",
                json={"mode": "shadow"},
                headers=admin)
            record("控制面: override→shadow",
                   r.status_code == 200
                   and r.json().get("mode")
                   == "shadow")
            # shadow 放行(无门控 409——业务错误可)
            r = await c.post(
                "/api/wallet/open", json={},
                headers=member)
            gated = "WALLET_MODE" in str(
                r.json().get("detail", ""))
            record("HTTP: shadow 放行(无门控特征)",
                   not gated
                   and r.status_code != 409,
                   f"got {r.status_code}")
            # assist 放行(无门控 409)
            await c.post(
                "/api/wallet/mode/override",
                json={"mode": "assist"},
                headers=admin)
            r = await c.post(
                "/api/wallet/open", json={},
                headers=member)
            gated = "WALLET_MODE" in str(
                r.json().get("detail", ""))
            record("HTTP: assist 放行(无门控特征)",
                   not gated
                   and r.status_code != 409,
                   f"got {r.status_code}")
            # 非法 override
            r = await c.post(
                "/api/wallet/mode/override",
                json={"mode": "full"},
                headers=admin)
            record("控制面: 非法 override 409",
                   r.status_code == 409)
            # 护栏恶化暂停(直接注入指标)
            r = await c.post(
                "/api/wallet/mode/guard",
                json={"withdrawRejRate": 0.9,
                      "earlySettleRate": 0.0,
                      "accountFrozenRate": 0.0},
                headers=admin)
            record("控制面: guard 三指标恶化暂停",
                   r.status_code == 200
                   and r.json().get("pausedNow")
                   is True)
            r = await c.post(
                "/api/wallet/open", json={},
                headers=member)
            record("HTTP: 暂停后决策面 409",
                   r.status_code == 409)
            r = await c.post(
                "/api/wallet/mode/resume",
                json={"note": "HTTP 测试恢复"},
                headers=admin)
            record("控制面: resume 恢复",
                   r.status_code == 200
                   and r.json().get("mode")
                   == "assist")
            r = await c.post(
                "/api/wallet/mode/resume",
                json={}, headers=admin)
            record("控制面: 未暂停 resume 409",
                   r.status_code == 409)
            # 清除 override 回 off
            await c.post(
                "/api/wallet/mode/override",
                json={"mode": ""},
                headers=admin)
            r = await c.post(
                "/api/wallet/open", json={},
                headers=member)
            record("HTTP: 清除 override 回 off 409",
                   r.status_code == 409)
        _refresh_legacy({"override": "",
                         "paused": False})


async def run_counts():
    """端点计数(下限断言防腐化)"""
    from routes.wallet_routes import router
    n = len(router.routes)
    record(f"端点: 路由 ≥26(实际 {n})",
           n >= 26)
    paths = {
        getattr(r, "path", "") for r in
        router.routes}
    record("端点: 控制面 4 端点在册",
           "/api/wallet/mode" in paths
           and "/api/wallet/mode/override" in paths
           and "/api/wallet/mode/guard" in paths
           and "/api/wallet/mode/resume" in paths)


async def main() -> bool:
    print("=" * 60)
    print("钱包盈利模块 大模型转段专项测试")
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
