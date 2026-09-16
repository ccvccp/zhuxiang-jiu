"""AI智能叫帮大模型(67号) 大模型二代转段专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    $env:AUTH_MODE="compat"; python test_help_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off 四层
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 三指标逐一恶化暂停 / 留痕 /
       resume / 未暂停恢复拒绝
       (三指标: 爽约率/差评率/在途滞留率)
    5. 状态序列化往返: 整档 JSON
    6. 同步桥接(62号范式): legacy_current_mode 进程快照
    7. 护栏巡检聚合: 冷启动零样本 / 造数聚合正确性
    8. HTTP 决策面: off 下 7 端点全 409(特征串法
       detail/error 双键)
    9. HTTP 宪法豁免面: off 下 accept 等 6 端点无门控
       (无 HELP_MODE 特征串, 进业务 404/409)
    10. HTTP 鉴权优先: 无 X-Member-Id 401 先于门控 409
    11. HTTP 观测面: categories/parse off 下公开 200
    12. HTTP assist: 放行 + helpMode 标记
    13. 控制面: GET /mode / override / guard / resume
    14. 端点计数: 33(29+4 控制面, 下限断言防腐化)
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")

from services.help_mode_service import (
    HelpModeService, MODE_VALUES,
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
    """env 快照守卫(HELP_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("HELP_MODE")
        os.environ.pop("HELP_MODE", None)
        if self._mode:
            os.environ["HELP_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("HELP_MODE", None)
        else:
            os.environ["HELP_MODE"] = self._backup


async def run_service():
    from repositories.store import reset_store

    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():
        reset_store()
        _refresh_legacy({"override": "", "paused": False})
        svc = HelpModeService()
        st = await svc.current_mode()
        record("三态-默认off",
               st["mode"] == "off" and st["source"] == "env")
    for m in MODE_VALUES:
        with _EnvGuard(m):
            reset_store()
            _refresh_legacy({"override": "", "paused": False})
            st = await HelpModeService().current_mode()
            record(f"三态-env {m}", st["mode"] == m)
    with _EnvGuard("bogus"):
        reset_store()
        _refresh_legacy({"override": "", "paused": False})
        st = await HelpModeService().current_mode()
        record("三态-非法回落off", st["mode"] == "off")
    with _EnvGuard("off"):
        reset_store()
        _refresh_legacy({"override": "", "paused": False})
        try:
            await HelpModeService().require_decision_mode()
            record("三态-require拒绝", False, "未抛")
        except ValueError as e:
            record("三态-require拒绝", "HELP_MODE" in str(e))

    # ============================================================
    # 2. 读取链(暂停 > override > env)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = HelpModeService()
        st = await svc.current_mode()
        record("读取链-env assist", st["mode"] == "assist")
        await svc.set_override("shadow")
        st = await svc.current_mode()
        record("读取链-override优先",
               st["mode"] == "shadow"
               and st["source"] == "runtime_override")
        await svc.guard_check(1.0, 0.0, 0.0)  # 爽约率恶化
        st = await svc.current_mode()
        record("读取链-护栏暂停最优先",
               st["mode"] == "off" and st["source"] == "guard_pause"
               and st["paused"] is True)
        await svc.resume(note="测试恢复")
        st = await svc.current_mode()
        record("读取链-恢复后override仍生效",
               st["mode"] == "shadow" and not st["paused"])

    # ============================================================
    # 3. override
    # ============================================================
    with _EnvGuard("off"):
        reset_store()
        svc = HelpModeService()
        await svc.set_override("assist")
        st = await svc.current_mode()
        record("override-设值", st["mode"] == "assist")
        await svc.set_override("")
        st = await svc.current_mode()
        record("override-清除回落env", st["mode"] == "off")
        try:
            await svc.set_override("wrong")
            record("override-非法拒绝", False, "未抛")
        except ValueError:
            record("override-非法拒绝", True)

    # ============================================================
    # 4. 护栏(三指标逐一恶化)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = HelpModeService()
        r = await svc.guard_check(0.10, 0.05, 0.60)
        record("护栏-基线不暂停",
               not r["breached"] and not r["pausedNow"])
        for name, args in (
                ("爽约率", (0.15, 0.05, 0.60)),
                ("差评率", (0.10, 0.08, 0.60)),
                ("滞留率", (0.10, 0.05, 0.70))):
            reset_store()
            r = await HelpModeService().guard_check(*args)
            record(f"护栏-{name}恶化暂停",
                   r["breached"] and r["pausedNow"])
        reset_store()
        svc = HelpModeService()
        await svc.guard_check(0.15, 0.05, 0.60)
        st = await svc._state()
        record("护栏-留痕",
               bool(st.get("pausedReason"))
               and bool(st.get("breachTrail")))
        await svc.resume(note="恢复测试")
        st = await svc.current_mode()
        record("护栏-resume", st["mode"] == "assist")
        try:
            await svc.resume()
            record("护栏-未暂停恢复拒绝", False, "未抛")
        except ValueError:
            record("护栏-未暂停恢复拒绝", True)

    # ============================================================
    # 5. 序列化往返
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = HelpModeService()
        await svc.set_override("shadow")
        await svc.guard_check(0.10, 0.05, 0.60)
        st1 = await svc._state()
        st2 = await HelpModeService()._state()
        record("序列化-整档往返",
               st2["override"] == "shadow"
               and len(st2.get("metrics") or []) == 1)

    # ============================================================
    # 6. 同步桥接
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        _refresh_legacy({"override": "", "paused": False})
        record("桥接-env直读",
               legacy_current_mode() == "assist")
        await HelpModeService().set_override("shadow")
        record("桥接-override快照",
               legacy_current_mode() == "shadow")
        await HelpModeService().guard_check(0.15, 0.0, 0.0)
        record("桥接-暂停快照", legacy_current_mode() == "off")
        _refresh_legacy({"override": "", "paused": False})
        with _EnvGuard(None):
            record("桥接-env缺省off",
                   legacy_current_mode() == "off")


async def run_patrol():
    """巡检聚合(造数 → 确定性聚合正确性)"""
    from repositories.store import reset_store
    from repositories.help_repository import (
        HelpRepository,
    )

    with _EnvGuard("assist"):
        reset_store()
        svc = HelpModeService()
        r = await svc.patrol()
        record("巡检-冷启动零样本不误判",
               r["metrics"]["cancelRate"] == 0
               and r["metrics"]["lowReviewRate"] == 0
               and not r["breached"])
        # 造数: 2 completed + 1 cancelled + 2 matched + 1 差评
        repo = HelpRepository()
        for i, status in enumerate(
                ("completed", "completed", "cancelled",
                 "matched", "matched"), start=1):
            await repo.save_order({
                "orderId": i, "status": status,
                "publisherId": 900 + i, "mode": "public",
                "createdAt": f"2026-09-16T00:0{i}:00"})
        await repo.save_review({
            "reviewId": 1, "orderId": 1, "reviewerId": 901,
            "revieweeId": 902, "score": 2})
        await repo.save_review({
            "reviewId": 2, "orderId": 2, "reviewerId": 902,
            "revieweeId": 901, "score": 5})
        r = await svc.patrol()
        record("巡检-聚合正确性",
               r["metrics"]["cancelRate"] == round(1 / 3, 4)
               and r["metrics"]["lowReviewRate"] == 0.5
               and r["metrics"]["stuckRate"] == 0.5
               and r["sample"]["decided"] == 3)
        # 小样本保护: decided=3/reviews=2/flow=4 全部 <10 →
        # 全部跳过恶化判定(指标照常留痕, 不自动暂停)
        st = await svc.current_mode()
        record("巡检-小样本保护不暂停",
               sorted(r.get("skippedSmallSample") or [])
               == ["cancelRate", "lowReviewRate",
                   "stuckRate"]
               and not r["breached"]
               and st["mode"] == "assist")


async def run_http():
    import httpx
    from main import app
    from repositories.store import reset_store

    hdrs = {"X-Member-Id": "1"}

    # 7 决策面请求(off 下全 409; PublishRequest 经纬度必填)
    def decision_requests():
        return [
            ("POST", "/api/help/orders",
             {"mode": "public", "category": "carry",
              "title": "帮忙搬东西", "description": "x",
              "longitude": 117.0, "latitude": 36.6}),
            ("POST", "/api/help/orders/1/donate",
             {"amount": 3}),
            ("POST", "/api/help/heritage/apply",
             {"heirMemberId": 2}),
            ("POST", "/api/help/heritage/1/accept", None),
            ("POST", "/api/help/heritage/1/cancel", None),
            ("POST", "/api/help/csr/packages",
             {"name": "测试包", "amount": 100}),
            ("POST", "/api/help/csr/packages/1/donate",
             {"orderId": 1, "amount": 10}),
        ]

    # 6 豁免面请求(off 下无门控, 进业务)
    def exempt_requests():
        return [
            ("POST", "/api/help/orders/999/accept", None),
            ("POST", "/api/help/orders/999/start", None),
            ("POST", "/api/help/orders/999/complete", None),
            ("POST", "/api/help/orders/999/cancel", None),
            ("POST", "/api/help/orders/999/review",
             {"score": 5, "content": "好"}),
        ]

    with _EnvGuard("off"):
        reset_store()
        _refresh_legacy({"override": "", "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            # 决策面 off 409
            n_ok = 0
            for method, url, payload in decision_requests():
                r = await c.request(method, url, json=payload,
                                    headers=hdrs)
                body = r.json()
                gated = "HELP_MODE" in str(
                    body.get("detail")
                    or body.get("error", ""))
                n_ok += (r.status_code == 409 and gated)
            record("HTTP: 决策面 off 全 409(7/7)", n_ok == 7)

            # 鉴权 401 优先(无 X-Member-Id)
            n_401 = 0
            for method, url, payload in decision_requests():
                r = await c.request(method, url, json=payload)
                n_401 += (r.status_code == 401)
            record("HTTP: 鉴权401优先于门控409", n_401 == 7)

            # 豁免面: off 下无门控(订单不存在 → 404 业务)
            n_exempt = 0
            for method, url, payload in exempt_requests():
                r = await c.request(method, url, json=payload,
                                    headers=hdrs)
                body = r.json()
                gated = "HELP_MODE" in str(
                    body.get("detail")
                    or body.get("error", ""))
                n_exempt += (not gated and r.status_code != 409)
            record("HTTP: 豁免面 off 无门控(5/5)", n_exempt == 5)

            # 观测面 off 公开 200
            r1 = await c.get("/api/help/categories")
            r2 = await c.post("/api/help/parse", json={
                "title": "帮忙搬东西", "description": "x"})
            record("HTTP: 观测面 off 公开200",
                   r1.status_code == 200
                   and r2.status_code == 200)

    with _EnvGuard("assist"):
        reset_store()
        _refresh_legacy({"override": "", "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            # assist 放行 + helpMode 标记
            r = await c.post("/api/help/orders", json={
                "mode": "public", "category": "carry",
                "title": "帮忙搬东西",
                "description": "正常描述",
                "longitude": 117.0, "latitude": 36.6},
                headers=hdrs)
            body = r.json()
            record("HTTP: assist 放行+标记",
                   r.status_code == 200
                   and body.get("helpMode") == "assist")

            # 控制面: mode 总览
            r = await c.get("/api/help/mode", headers={
                "X-Member-Id": "1", "X-Role": "admin"})
            body = r.json().get("data") or {}
            record("HTTP: 控制面 mode 总览",
                   r.status_code == 200
                   and body.get("mode") == "assist"
                   and body.get("exemptSurfaces"))

            # 控制面: 非法 override
            r = await c.post("/api/help/mode/override",
                             json={"mode": "wrong"}, headers={
                                 "X-Member-Id": "1",
                                 "X-Role": "admin"})
            record("HTTP: 控制面非法切档409",
                   r.status_code == 409)

            # 控制面: guard 巡检
            r = await c.post("/api/help/mode/guard", headers={
                "X-Member-Id": "1", "X-Role": "admin"})
            record("HTTP: 控制面 guard 巡检",
                   r.status_code == 200)

            # 控制面: 无 X-Role 403
            r = await c.get("/api/help/mode",
                            headers={"X-Member-Id": "1"})
            record("HTTP: 控制面403", r.status_code == 403)

    # 端点计数(方法级, 下限断言防腐化)
    paths = [(route.path, m)
             for route in app.routes
             for m in getattr(route, "methods", [])
             if route.path.startswith("/api/help/")]
    record("HTTP: 端点计数>=33方法级",
           len(paths) >= 33, f"got {len(paths)}")


async def main():
    print("=" * 60)
    print("AI智能叫帮大模型(67号) 二代转段专项测试")
    print("=" * 60)
    await run_service()
    await run_patrol()
    await run_http()
    print("-" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
