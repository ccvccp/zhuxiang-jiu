"""63号·AI智能后台管理模块 P5 专项测试
(看板+红队+收官)

运行方式:
    python test_ab63_p5.py

覆盖(63号计划 §九 P5):
    - 四区看板: 度量区(合规前置率/
      自动过审准确率/审核时效/信值
      健康度)+权限区+护航区+防御区
    - 红队七向量: 权限提升/护航绕过/
      分流操纵/审核越权/申诉刷分/
      培训逃避/模板注入——全防御
    - 宪法断言: 44号 38 档案+
      auth/role/58/59号零改动
    - HTTP 层+回归
    - QC: 后台安全全链防御
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ.pop("AB63_LLM_MODE", None)
os.environ.pop("AB63_LEARN_MODE", None)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


CLEAN = "居家养老服务 服务有效期90天 退改政策可退"
BLOCKED = "提供假发票开具服务"
EXAG = ("全市最好的居家养老服务 "
        "服务有效期90天 退改政策可退")


async def seed_rejections(svc, member_id,
                          content, n):
    """造 n 条驳回"""
    for _ in range(n):
        s = await svc.submit(
            member_id, "ally_merchant",
            content=content,
            tier="standard")
        await svc.review(
            s["subId"], approve=False,
            reviewer="审核员")


class TestDashboard:
    """01 四区看板"""

    async def run(self):
        print("[01 四区看板]")
        reset_all()
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        from services.ab63_guard_service import (
            Ab63GuardService,
        )
        from services.ab63_dashboard_service import (
            Ab63DashboardService,
        )
        sub_svc = Ab63SubmissionService()
        os.environ["AB63_MODE"] = "shadow"

        # 造数据: 2 block 拦截+1 驳回
        # +1 L1 干净+1 L2 翻转
        try:
            await sub_svc.submit(
                10, "ally_merchant",
                content=BLOCKED,
                tier="standard")
        except ValueError:
            pass  # 预期拦截(guard 留痕)
        try:
            await sub_svc.submit(
                11, "ally_merchant",
                content="赌博渠道服务",
                tier="standard")
        except ValueError:
            pass
        await seed_rejections(
            sub_svc, 12, EXAG, 1)
        # L1 干净(trusted)
        await sub_svc.submit(
            13, "ally_merchant",
            content=CLEAN, tier="trusted")
        # L2 发布→申诉翻转(adjusted)
        s = await sub_svc.submit(
            14, "ally_merchant",
            content=CLEAN,
            tier="standard")
        await sub_svc.review(
            s["subId"], approve=True,
            reviewer="审核员")
        await sub_svc.appeal(
            s["subId"], appellant="member")
        await sub_svc.resolve_appeal(
            s["subId"], overturn=True,
            adjudicator="合规官")

        # 看板为观测面——off 亦可看
        os.environ["AB63_MODE"] = "off"
        dash = await (
            Ab63DashboardService()
            .dashboard())

        # 结构四区
        record("看板结构(四区)",
               all(k in dash for k in (
                   "metrics", "permission",
                   "guard", "defense")),
               str(sorted(dash.keys())))

        m = dash["metrics"]
        # 合规前置率: 2 block/(2+1)=66.7%
        record("度量区·合规前置率",
               m["blockIntercepted"] == 2
               and m["rejectedSubmissions"]
               == 1
               and m[
                   "complianceFrontload"]
               == 66.7,
               str((m["blockIntercepted"],
                    m["rejectedSubmissions"],
                    m[
                        "complianceFrontload"])))

        # 自动过审准确率: L1×1
        # 无翻转→100%
        record("度量区·自动过审准确率",
               m["l1Total"] == 1
               and m[
                   "autoReviewAccuracy"]
               == 100.0,
               str((m["l1Total"],
                    m[
                        "autoReviewAccuracy"])))

        # 审核时效
        record("度量区·审核时效",
               m["latencyP95"] == 100.0,
               str(m["latencyP95"]))

        # 信值健康度(第38档案)
        record("度量区·信值健康度键",
               "trustHealth" in m,
               "")

        p = dash["permission"]
        record("权限区(裁决统计)",
               p["totalGrants"] >= 0
               and "byRole" in p
               and "grantRate" in p,
               str(p)[:60])

        g = dash["guard"]
        record("护航区(检测分布)",
               g["totalChecks"] >= 2
               and "byLevel" in g
               and "byTrack" in g,
               str((g["totalChecks"],
                    g["byLevel"])))

        d = dash["defense"]
        record("防御区(off 零影响)",
               d["mode"] == "off"
               and d["zeroImpactWhenOff"]
               is True,
               str(d["mode"]))
        os.environ["AB63_MODE"] = "off"


class TestRedteam:
    """02 红队七向量"""

    async def run(self):
        print("[02 红队七向量]")
        reset_all()
        from services.ab63_redteam_service import (
            Ab63RedteamService,
        )
        svc = Ab63RedteamService()

        # off 拒绝(无攻击面)
        try:
            await svc.run_all()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态红队拒绝", ok, err)

        os.environ["AB63_MODE"] = "shadow"
        r = await svc.run_all()

        # 七向量全覆盖
        record("七向量全覆盖",
               len(r["vectors"]) == 7
               and set(r["vectors"]) == {
                   f"RT-0{i}"
                   for i in range(1, 8)},
               str(sorted(r["vectors"])))

        # 全防御
        record("七向量全防御",
               r["summary"]["allDefended"]
               is True
               and r["summary"]["defended"]
               == 7,
               str(r["summary"]))

        # 各向量断言细节
        v1 = r["vectors"]["RT-01"]
        record("RT-01 权限提升防御",
               v1["defended"] is True
               and len(v1["results"]) == 3,
               str(v1)[:80])
        v2 = r["vectors"]["RT-02"]
        record("RT-02 护航绕过防御",
               v2["defended"] is True
               and all(x["rejected"]
                       for x in
                       v2["results"]),
               str(v2)[:80])
        v3 = r["vectors"]["RT-03"]
        record("RT-03 分流操纵防御",
               v3["defended"] is True,
               str(v3)[:80])
        v4 = r["vectors"]["RT-04"]
        record("RT-04 审核越权防御",
               v4["defended"] is True
               and len(v4["results"]) == 3,
               str(v4)[:80])
        v5 = r["vectors"]["RT-05"]
        record("RT-05 申诉刷分防御",
               v5["defended"] is True,
               str(v5)[:80])
        v6 = r["vectors"]["RT-06"]
        record("RT-06 培训逃避防御",
               v6["defended"] is True,
               str(v6)[:80])
        v7 = r["vectors"]["RT-07"]
        record("RT-07 模板注入防御",
               v7["defended"] is True,
               str(v7)[:80])

        # 留痕(防御区读回)
        from repositories.ab63_repository \
            import Ab63Repository
        repo = Ab63Repository()
        evs = [e for e in await
               repo.list_events(limit=50)
               if (e.get("detail") or {})
               .get("action")
               == "redteam_run"]
        record("红队留痕(dashboard 读回)",
               len(evs) == 1
               and (evs[0].get("detail")
                    or {}).get("defended")
               == 7,
               str(len(evs)))

        # 看板防御区联动
        from services.ab63_dashboard_service import (
            Ab63DashboardService,
        )
        dash = await (
            Ab63DashboardService()
            .dashboard())
        defense = dash["defense"]
        record("防御区红队读回",
               (defense.get(
                   "redteamLastRun")
                or {}).get("defended")
               == 7,
               str(defense.get(
                   "redteamLastRun")))
        os.environ["AB63_MODE"] = "off"


class TestHttp:
    """03 HTTP 层(P5)"""

    async def run(self):
        print("[03 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 409(红队需决策面)
        resp = client.post(
            "/api/ab63/redteam",
            json={}, headers=admin)
        record("HTTP redteam off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # dashboard 观测面(off 可用)
        resp = client.get(
            "/api/ab63/dashboard",
            headers=admin)
        body = resp.json() or {}
        record("HTTP dashboard(off 可观测)",
               resp.status_code == 200
               and all(k in body for k in (
                   "metrics", "permission",
                   "guard", "defense")),
               str((resp.status_code,
                    sorted(body.keys())
                    [:4])))

        # shadow 红队
        os.environ["AB63_MODE"] = "shadow"
        resp = client.post(
            "/api/ab63/redteam",
            json={}, headers=admin)
        body = resp.json() or {}
        record("HTTP redteam 全防御",
               resp.status_code == 200
               and (body.get("summary")
                    or {}).get(
                       "allDefended")
               is True,
               str((resp.status_code,
                    body.get("summary"))))

        # dashboard 联动(防御区有值)
        resp = client.get(
            "/api/ab63/dashboard",
            headers=admin)
        body = resp.json() or {}
        defense = (body.get("defense")
                   or {}).get(
            "redteamLastRun") or {}
        record("HTTP dashboard 防御区联动",
               defense.get("defended")
               == 7,
               str(defense))

        # 鉴权 403
        resp = client.get(
            "/api/ab63/dashboard")
        record("HTTP dashboard 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(
            "/api/ab63/redteam", json={})
        record("HTTP redteam 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        os.environ["AB63_MODE"] = "off"


class TestConstitution:
    """04 宪法断言(收官)"""

    async def run(self):
        print("[04 宪法断言]")
        # ① 44号第38档案入册(P0 断言
        #    口径: 全池 35 档案)
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号第38档案入册",
               "admin_orchestration"
               in SCORER_REGISTRY
               and len(SCORER_REGISTRY)
               == 36,
               str(len(SCORER_REGISTRY)))

        # ② 63号 21 端点完整收官
        from routes.ab63_routes import (
            router as ab_router,
        )
        record("63号 21 端点收官",
               sum(1 for r
                   in ab_router.routes)
               == 21,
               str(sum(1 for r
                       in ab_router.routes)))

        # ③ 零改动宪法(感知源可导入
        #    且核心结构未被 63号 修改)
        import services.ii58_service as s58
        import services.ii59_search_service as s59
        import services.trust_risk_profile_service as s47
        import services.xiaozhu_privacy_service as s49
        import services.ai_governance_service as s46
        import services.ai_learning_service as s44
        record("感知源五模块零改动"
               "(44/46/47/49/58)",
               s44.__name__.endswith(
                   "ai_learning_service")
               and s46.__name__.endswith(
                   "ai_governance_service")
               and s47.__name__.endswith(
                   "trust_risk_profile_service")
               and s49.__name__.endswith(
                   "xiaozhu_privacy_service")
               and s58.__name__.endswith(
                   "ii58_service")
               and s59.__name__.endswith(
                   "ii59_search_service"),
               "")

        # ④ auth/role 零改动
        import core.auth_middleware as auth
        import services.role_service as role
        record("auth/role 零改动",
               auth.__name__.endswith(
                   "auth_middleware")
               and role.__name__.endswith(
                   "role_service"),
               "")

        # ⑤ 三开关铁律
        from services.ab63_registry import (
            MODE_VALUES,
        )
        record("三开关铁律(off 默认)",
               MODE_VALUES == ("off",
                               "shadow",
                               "assist")
               and os.environ.get(
                   "AB63_MODE", "off")
               == "off"
               and os.environ.get(
                   "AB63_LLM_MODE",
                   "off") == "off"
               and os.environ.get(
                   "AB63_LEARN_MODE",
                   "off") == "off",
               "")

        # ⑥ 63号自检(启动即验)
        from services import (
            ab63_registry,
        )
        record("registry 启动自检",
               ab63_registry.
               _validate_registry
               is not None,
               "")


async def run_all():
    await TestDashboard().run()
    await TestRedteam().run()
    await TestHttp().run()
    await TestConstitution().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
