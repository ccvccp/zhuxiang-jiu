"""条款协议大模型 大模型二代转段专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_agreement_mode.py

覆盖:
    1. 三态语义: 默认 off / env 三态 / 非法回落 / require_decision off 拒绝
    2. 读取链: 护栏暂停 > override > env > off 四层
    3. override: 设值优先 / 清除回落 / 非法值拒绝
    4. 护栏: 未恶化不暂停 / 三指标逐一恶化暂停 / 留痕 /
       resume / 未暂停恢复拒绝
       (三指标: 条款停用率/协议停用率/版本失配率
       ——流程态 draft/reviewing 不进条款停用分母)
    5. 状态序列化往返: 整档 JSON
    6. 同步桥接(62号范式): legacy_current_mode 进程快照
    7. 护栏自动巡检: 默认 on / 间隔 / 冷启动 /
       流程态不进分母 / 条款停用率恶化暂停
    8. HTTP 决策面: off 下 4 端点全 409(特征串法
       detail/error 双键) + 鉴权优先(admin 403)
    9. HTTP 用户同意权豁免: off 下 consent
       响应不含门控特征(签署条款是用户法律行为)
    10. HTTP 观测面: off 下不受影响
    11. HTTP shadow/assist: 放行 + agreementMode 标记
    12. 控制面: GET /mode / override / guard / resume
    13. 端点计数: 15(11+4 控制面, 下限断言防腐化)
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.agreement_mode_service import (
    AgreementModeService, MODE_VALUES,
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
    """env 快照守卫(AGREEMENT_MODE 每段独立)"""

    def __init__(self, mode=None):
        self._mode = mode

    def __enter__(self):
        self._backup = os.environ.get("AGREEMENT_MODE")
        os.environ.pop("AGREEMENT_MODE", None)
        if self._mode:
            os.environ["AGREEMENT_MODE"] = self._mode
        return self

    def __exit__(self, *a):
        if self._backup is None:
            os.environ.pop("AGREEMENT_MODE", None)
        else:
            os.environ["AGREEMENT_MODE"] = self._backup


async def run_service():
    global PASS, FAIL
    from repositories.store import reset_store

    # ============================================================
    # 1. 三态语义
    # ============================================================
    with _EnvGuard():
        reset_store()
        svc = AgreementModeService()
        m = await svc.current_mode()
        record("三态: 默认 off(env 源)",
               m["mode"] == "off"
               and m["source"] == "env")
        try:
            await svc.require_decision_mode()
            record("三态: off 决策面拒绝", False)
        except ValueError as e:
            record("三态: off 决策面拒绝",
                   "AGREEMENT_MODE=off" in str(e))
        os.environ["AGREEMENT_MODE"] = "invalid"
        m = await svc.current_mode()
        record("三态: 非法 env 回落 off",
               m["mode"] == "off")
    with _EnvGuard("shadow"):
        reset_store()
        m = await AgreementModeService() \
            .current_mode()
        record("三态: env shadow", m["mode"] == "shadow")
    with _EnvGuard("assist"):
        reset_store()
        m = await AgreementModeService() \
            .current_mode()
        record("三态: env assist",
               m["mode"] == "assist")

    # ============================================================
    # 2. 读取链(护栏暂停 > override > env > off)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = AgreementModeService()
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
        # 恶化暂停(直接调 guard_check——停用率爆表)
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
        svc = AgreementModeService()
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
        svc = AgreementModeService()
        r = await svc.guard_check(0.10, 0.10, 0.10)
        record("护栏: 基线持平不暂停",
               r["breached"] is False
               and r["pausedNow"] is False)
        # 条款停用率恶化
        r = await svc.guard_check(0.20, 0.10, 0.10)
        record("护栏: 条款停用率恶化暂停",
               r["breached"] is True
               and r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "agreementInactiveRate")
        st_trail = await svc._state()
        record("护栏: 暂停留痕",
               bool(st_trail["paused"])
               and "条款停用率" in st_trail["pausedReason"]
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
        # 协议停用率恶化
        r = await svc.guard_check(0.10, 0.30, 0.10)
        record("护栏: 协议停用率恶化暂停",
               r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "protocolInactiveRate")
        await svc.resume(note="恢复")
        # 版本失配率恶化
        r = await svc.guard_check(0.10, 0.10, 0.40)
        record("护栏: 版本失配率恶化暂停",
               r["pausedNow"] is True
               and r["breaches"][0]["metric"]
               == "versionMismatchRate")
        await svc.resume(note="恢复")
        # 基线为 0 时绝对值恶化(防除零)
        r = await svc.guard_check(
            0.0, 0.0, 0.0,
            baseline={"agreementInactiveRate": 0.0,
                      "protocolInactiveRate": 0.10,
                      "versionMismatchRate": 0.10})
        record("护栏: 零基线防除零不误判",
               r["breached"] is False)
        r = await svc.guard_check(
            0.01, 0.0, 0.0,
            baseline={"agreementInactiveRate": 0.0,
                      "protocolInactiveRate": 0.10,
                      "versionMismatchRate": 0.10})
        record("护栏: 零基线绝对恶化暂停",
               r["pausedNow"] is True)
        await svc.resume(note="恢复")

    # ============================================================
    # 5. 状态序列化往返(整档 JSON)
    # ============================================================
    with _EnvGuard("assist"):
        reset_store()
        svc = AgreementModeService()
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
               and "条款停用率" in st2["breachTrail"][0]["reason"])
        record("序列化: 指标留痕滚动截断",
               st2["metrics"][0]["metrics"]
               ["agreementInactiveRate"] == 0.2)
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
        svc = AgreementModeService()
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
    # 7. 护栏自动巡检(条款协议仓储聚合)
    # ============================================================
    with _EnvGuard("assist"):
        from services.agreement_scheduler import (
            guard_patrol_enabled,
            guard_interval_seconds,
            run_guard_patrol,
        )
        from repositories.agreement_repository import (
            AgreementRepository,
        )
        record("巡检: 默认 on",
               guard_patrol_enabled() is True)
        os.environ["AGREEMENT_GUARD_AUTO"] = "off"
        record("巡检: 开关可关",
               guard_patrol_enabled() is False)
        os.environ["AGREEMENT_GUARD_AUTO"] = "on"
        os.environ["AGREEMENT_GUARD_INTERVAL"] = "120"
        record("巡检: 间隔可调(120s)",
               guard_interval_seconds() == 120)
        os.environ["AGREEMENT_GUARD_INTERVAL"] = "bad"
        record("巡检: 非法间隔回落 3600",
               guard_interval_seconds() == 3600)
        os.environ.pop("AGREEMENT_GUARD_INTERVAL", None)
        os.environ.pop("AGREEMENT_GUARD_AUTO", None)

        reset_store()
        repo = AgreementRepository()

        async def seed(status: str, version: str,
                       no: str) -> None:
            """造一条条款(save_agreement 全字段)"""
            aid = await repo.next_agreement_id()
            await repo.save_agreement({
                "id": aid, "agreementNo": no,
                "name": f"条款{no}", "type": "term",
                "applicableRole": "user",
                "currentVersion": version,
                "status": status, "effectiveDate": None,
                "versionHistory": []})

        # 冷启动(无条款/无协议/无同意)——不误暂停
        r = await run_guard_patrol()
        record("巡检: 冷启动不误判",
               r["metrics"]["agreementInactiveRate"] == 0.0
               and r["metrics"]["protocolInactiveRate"] == 0.0
               and r["metrics"]["versionMismatchRate"] == 0.0
               and r["pausedNow"] is False)
        # 造数: 2 draft + 1 reviewing 流程态条款
        # (不进条款停用分母——不误暂停)
        for no in ("T-D-01", "T-D-02"):
            await seed("draft", "v1.0", no)
        await seed("reviewing", "v1.0", "T-R-01")
        r = await run_guard_patrol()
        record("巡检: 流程态不进条款停用分母(建议书制)",
               r["metrics"]["agreementInactiveRate"] == 0.0
               and r["pausedNow"] is False,
               str(r["metrics"]))
        # 造数: 2 published + 3 inactive
        # (条款停用率 3/5=0.6 恶化)
        for no in ("T-P-01", "T-P-02"):
            await seed("published", "v2.0", no)
        for no in ("T-I-01", "T-I-02", "T-I-03"):
            await seed("inactive", "v1.0", no)
        r = await run_guard_patrol()
        record("巡检: 条款停用率聚合恶化暂停",
               abs(r["metrics"]["agreementInactiveRate"]
                   - 0.6) < 1e-3
               and r["breached"] is True
               and r["pausedNow"] is True,
               str(r["metrics"]))
        m = await AgreementModeService().current_mode()
        record("巡检: 暂停生效(guard_pause)",
               m["mode"] == "off"
               and m["source"] == "guard_pause")
        await AgreementModeService().resume(
            note="巡检测试恢复")
    _refresh_legacy({"override": "", "paused": False})


async def run_http():
    global PASS, FAIL
    import httpx
    from main import app

    admin = {"X-Role": "admin"}
    member = {"X-Member-Id": "9002"}

    # 4 决策面请求(off 下全 409; 门控在装饰器层)
    def decision_requests():
        return [
            ("POST", "/api/agreements",
             {"agreementNo": "T-MODE-01",
              "name": "门控测试条款"}),
            ("POST", "/api/agreements/1/publish", None),
            ("POST", "/api/agreements/1/versions",
             {"content": "新版内容"}),
            ("POST", "/api/agreements/role-protocols",
             {"role": "user", "agreementId": 1}),
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
                gated = "AGREEMENT_MODE" in str(
                    body.get("detail")
                    or body.get("error", ""))
                passed = r.status_code == 409 and gated
                n_ok += passed
                if not passed:
                    record(f"HTTP: off 409 {url}",
                           False,
                           f"got {r.status_code}")
            record(f"HTTP: 决策面 off 全 409"
                   f"({n_ok}/4)", n_ok == 4)

            # 鉴权优先(403 before 409)
            r = await c.post(
                "/api/agreements",
                json={"agreementNo": "T-X",
                      "name": "无鉴权"})
            record("HTTP: admin 面无 Role 403 优先",
                   r.status_code == 403,
                   f"got {r.status_code}")

            # 用户同意权豁免: consent off 不受门控
            # (响应不含门控特征串——业务错误可)
            r = await c.post(
                "/api/agreements/1/consent",
                json={"signMethod": "checkbox"},
                headers=member)
            body = r.json()
            gated = "AGREEMENT_MODE" in str(
                body.get("detail")
                or body.get("error", ""))
            record("HTTP: 用户同意权 consent 不受门控",
                   not gated and r.status_code != 409,
                   f"{r.status_code} {str(body)[:60]}")

            # 观测面 off 不受影响
            for url in ("/api/agreements",
                        "/api/agreements/stats/overview"):
                r = await c.get(url, headers=admin)
                record(f"HTTP: 观测面 off 不受限 "
                       f"{url.rsplit('/', 1)[-1]}",
                       r.status_code == 200,
                       f"got {r.status_code}")

            # 控制面
            r = await c.get("/api/agreements/mode",
                            headers=admin)
            body = r.json()
            record("控制面: GET /mode 形状",
                   r.status_code == 200
                   and body.get("modeValues")
                   == ["off", "shadow", "assist"]
                   and "guard" in body)
            r = await c.get("/api/agreements/mode")
            record("控制面: GET /mode admin 门禁",
                   r.status_code == 403)
            r = await c.post(
                "/api/agreements/mode/override",
                json={"mode": "shadow"},
                headers=admin)
            record("控制面: override→shadow",
                   r.status_code == 200
                   and r.json()["data"]["mode"]
                   == "shadow")
            r = await c.post(
                "/api/agreements/mode/override",
                json={"mode": "bogus"},
                headers=admin)
            record("控制面: 非法 override 409",
                   r.status_code == 409)
            r = await c.post(
                "/api/agreements/mode/override",
                json={"mode": ""},
                headers=admin)
            record("控制面: 清除 override 回落 env",
                   r.status_code == 200
                   and r.json()["data"]["mode"] == "off")
            r = await c.post(
                "/api/agreements/mode/resume",
                headers=admin)
            record("控制面: 未暂停 resume 409",
                   r.status_code == 409)

            # 显式 guard 指标(恶化→暂停→resume)
            r = await c.post(
                "/api/agreements/mode/guard",
                json={"agreementInactiveRate": 0.5,
                      "protocolInactiveRate": 0.1,
                      "versionMismatchRate": 0.1},
                headers=admin)
            record("控制面: guard 显式恶化暂停",
                   r.status_code == 200
                   and r.json()["data"]["pausedNow"]
                   is True)
            r = await c.post(
                "/api/agreements/mode/resume",
                headers=admin)
            record("控制面: resume 恢复",
                   r.status_code == 200
                   and r.json()["data"]["paused"]
                   is False)

    # shadow/assist 放行 + agreementMode 标记
    with _EnvGuard("assist"):
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            r = await c.post(
                "/api/agreements",
                json={"agreementNo": "T-MARK",
                      "name": "标记测试条款"},
                headers=admin)
            record("HTTP: assist 放行+agreementMode 标记",
                   r.status_code == 200
                   and r.json().get("agreementMode")
                   == "assist",
                   f"{r.status_code} {str(r.json())[:60]}")
    with _EnvGuard("shadow"):
        reset_store()
        _refresh_legacy({"override": "",
                         "paused": False})
        async with httpx.AsyncClient(
                app=app, base_url="http://t") as c:
            r = await c.post(
                "/api/agreements",
                json={"agreementNo": "T-MARK",
                      "name": "标记测试条款"},
                headers=admin)
            record("HTTP: shadow 放行+agreementMode 标记",
                   r.status_code == 200
                   and r.json().get("agreementMode")
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
            # 方法级计数(POST/GET 共享路径)
            agr_methods = sum(
                len(paths[p])
                for p in paths
                if p.startswith("/api/agreements"))
            record(f"端点计数: /api/agreements ≥15 方法"
                   f"(实际 {agr_methods})",
                   agr_methods >= 15)


async def main():
    await run_service()
    await run_http()
    print("\n".join(RESULTS))
    print("-" * 56)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
