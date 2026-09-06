"""63号·AI智能后台管理模块 P0 专项测试
(后台注册表+角色感知底座+第38档案)

运行方式:
    python test_ab63_p0.py

覆盖(63号计划 §九 P0):
    - 后台注册表: 四轴规则 20 条+四角色
      模板+启动自检断言域
    - 权限裁决骨架: 四轴计算(tier/
      合规/场景)+reason 可解释链
      +门槛(高危 70/常规 60)
    - 工作台渲染: 角色模板+novice/
      mature 视图+无障碍
    - 第38档案八因子+三级决策
    - HTTP 层: 5 端点+鉴权
    - 宪法: 44号 35 档案+auth/58号
      零改动
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


class TestRegistry:
    """01 后台注册表"""

    async def run(self):
        print("[01 后台注册表]")
        reset_all()
        from services.ab63_registry import (
            ACTION_DOMAINS,
            HIGH_RISK_ACTIONS,
            ROLE_ACTION_BASE,
            ROLE_DOMAINS,
            TIER_BONUS,
            WORKBENCH_TEMPLATES,
            evaluate_permission,
            get_template,
            registry_view,
        )

        # 数量+结构
        record("四轴规则 20 条(4×5)",
               len(ROLE_ACTION_BASE) == 20,
               str(len(ROLE_ACTION_BASE)))
        record("角色域四类",
               len(ROLE_DOMAINS) == 4,
               str(len(ROLE_DOMAINS)))
        record("动作域五类",
               len(ACTION_DOMAINS) == 5,
               str(len(ACTION_DOMAINS)))
        record("工作台模板四角色",
               len(WORKBENCH_TEMPLATES) == 4,
               str(len(
                   WORKBENCH_TEMPLATES)))

        # tier 修正表
        record("tier 修正四档(trusted+20)",
               TIER_BONUS.get("trusted") == 20
               and TIER_BONUS.get(
                   "restricted") == -30,
               str(TIER_BONUS))

        # 高危动作域
        record("高危动作域(batch/免审/下发)",
               set(HIGH_RISK_ACTIONS)
               == {"batch_ops",
                   "whitelist_quota",
                   "rule_broadcast"},
               str(HIGH_RISK_ACTIONS))

        # 确定性裁决抽查
        v1 = evaluate_permission(
            "ally_merchant", "basic_crud",
            tier="trusted",
            compliance_rate=0.9,
            period="normal",
            sensitivity="low")
        record("同盟商基础 CRUD(trusted 达标)",
               v1.get("granted") is True
               and v1.get("score") > 60,
               str((v1.get("granted"),
                    v1.get("score"))))
        record("reason 可解释链",
               "基线" in str(
                   (v1.get("reason")
                    or {}).get("text"))
               and "门槛" in str(
                   (v1.get("reason")
                    or {}).get("text")),
               str(v1.get("reason"))[:70])

        v2 = evaluate_permission(
            "ally_merchant", "batch_ops",
            tier="restricted",
            compliance_rate=0.3,
            period="peak",
            sensitivity="high")
        record("restricted+高危场景拒绝",
               v2.get("granted") is False,
               str(v2.get("score")))

        v3 = evaluate_permission(
            "compliance_auditor",
            "review_decide",
            tier="standard")
        record("审核员裁决域(标准即可)",
               v3.get("granted") is True,
               str(v3.get("score")))

        # 角色域外/动作域外
        v4 = evaluate_permission(
            "hacker_role", "basic_crud")
        record("角色域外拒绝",
               v4.get("granted") is False,
               str(v4.get("score")))

        # registry 视图
        view = registry_view()
        record("registry 视图(观测面)",
               view.get("ruleEntries") == 20
               and view.get("templates") == 4
               and view.get("mode") == "off",
               str((view.get("ruleEntries"),
                    view.get("templates"))))

        # 模板结构(同盟商 novice)
        tpl = get_template("ally_merchant")
        record("同盟商 novice 视图"
               "(隐藏高级+合规向导)",
               (tpl.get("noviceView")
                or {}).get("hideAdvanced")
               is True
               and (tpl.get("noviceView")
                    or {}).get(
                   "highlightGuide")
               == "合规向导",
               str(tpl.get("noviceView")))
        record("同盟商无障碍(auto)",
               (tpl.get("accessibility")
                or {}).get("largeFont")
               == "auto",
               str(tpl.get(
                   "accessibility")))


class TestGrant:
    """02 权限裁决骨架"""

    async def run(self):
        print("[02 权限裁决]")
        reset_all()
        from services.ab63_service import (
            Ab63Service,
        )
        svc = Ab63Service()

        # off 拒绝
        try:
            await svc.evaluate_grant(
                1, "ally_merchant",
                "basic_crud")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态裁决拒绝", ok, err)

        os.environ["AB63_MODE"] = "shadow"

        # 角色域外拒绝
        try:
            await svc.evaluate_grant(
                1, "hacker", "basic_crud")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "域外" in str(e), \
                str(e)[:30]
        record("角色域外拒绝(服务层)", ok, err)

        # 动作域外拒绝
        try:
            await svc.evaluate_grant(
                1, "ally_merchant",
                "steal_data")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "域外" in str(e), \
                str(e)[:30]
        record("动作域外拒绝(服务层)", ok, err)

        # 合法裁决(trusted+高合规)
        r1 = await svc.evaluate_grant(
            1, "ally_merchant", "basic_crud",
            tier="trusted",
            compliance_rate=0.95)
        record("裁决受理(grantId+granted)",
               int(r1.get("grantId") or 0) > 0
               and r1.get("granted") is True,
               str((r1.get("grantId"),
                    r1.get("granted"))))
        record("裁决 reason(服务层可解释)",
               "基线" in str(
                   (r1.get("reason")
                    or {}).get("text")),
               str(r1.get("reason"))[:60])

        # 落库归因(上下文快照)
        from repositories.ab63_repository \
            import Ab63Repository
        repo = Ab63Repository()
        grant = await repo.get_grant(
            r1.get("grantId"))
        record("裁决落库(上下文快照)",
               grant is not None
               and (grant.get("context")
                    or {}).get("tier")
               == "trusted"
               and (grant.get("context")
                    or {}).get(
                   "complianceRate") == 0.95,
               str(grant.get("context")))

        # tier 联动差异(trusted vs restricted)
        r2 = await svc.evaluate_grant(
            2, "ally_merchant", "batch_ops",
            tier="trusted",
            compliance_rate=0.9)
        r3 = await svc.evaluate_grant(
            3, "ally_merchant", "batch_ops",
            tier="restricted",
            compliance_rate=0.3)
        record("tier 联动(trusted>restricted)",
               r2.get("score")
               > r3.get("score"),
               str((r2.get("score"),
                    r3.get("score"))))

        # 高危门槛(70)
        record("高危门槛(batch 70)",
               r2.get("threshold") == 70,
               str(r2.get("threshold")))

        # 场景惩罚(peak+high)
        r4 = await svc.evaluate_grant(
            4, "ops_operator", "batch_ops",
            tier="standard",
            compliance_rate=0.8,
            period="peak",
            sensitivity="high")
        r5 = await svc.evaluate_grant(
            5, "ops_operator", "batch_ops",
            tier="standard",
            compliance_rate=0.8,
            period="normal",
            sensitivity="low")
        record("场景惩罚(peak+high 降分)",
               r4.get("score")
               < r5.get("score"),
               str((r4.get("score"),
                    r5.get("score"))))

        # 裁决列表
        lst = await svc.list_grants()
        record("裁决列表(byRole 统计)",
               lst.get("total") == 5
               and (lst.get("byRole")
                    or {}).get(
                   "ally_merchant") == 3,
               str((lst.get("total"),
                    lst.get("byRole"))))

        # grant 事件留痕
        events = await repo.list_events(
            event_type="grant", limit=20)
        record("grant 事件留痕(5 条)",
               len(events) == 5,
               str(len(events)))
        os.environ["AB63_MODE"] = "off"


class TestWorkbench:
    """03 工作台渲染骨架"""

    async def run(self):
        print("[03 工作台]")
        reset_all()
        from services.ab63_service import (
            Ab63Service,
        )
        svc = Ab63Service()

        # off 拒绝
        try:
            await svc.render_workbench(
                1, "ally_merchant")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态渲染拒绝", ok, err)

        os.environ["AB63_MODE"] = "shadow"

        # 角色域外拒绝
        try:
            await svc.render_workbench(
                1, "hacker")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "域外" in str(e), \
                str(e)[:30]
        record("渲染角色域外拒绝", ok, err)

        # novice 视图
        r1 = await svc.render_workbench(
            1, "ally_merchant", novice=True)
        record("novice 渲染(wbId+视图)",
               int(r1.get("wbId") or 0) > 0
               and r1.get("view")
               == "noviceView",
               str((r1.get("wbId"),
                    r1.get("view"))))
        view1 = (r1.get("renderOptions")
                 or {}).get("view") or {}
        record("novice 呈现(隐藏高级+向导)",
               view1.get("hideAdvanced")
               is True
               and view1.get(
                   "highlightGuide")
               == "合规向导",
               str(view1)[:60])

        # mature 视图
        r2 = await svc.render_workbench(
            2, "ally_merchant")
        view2 = (r2.get("renderOptions")
                 or {}).get("view") or {}
        record("mature 呈现(批量工具栏)",
               r2.get("view") == "matureView"
               and view2.get("batchToolbar")
               is True,
               str(view2)[:60])

        # 审核员模板(专长队列+AI 预标注)
        r3 = await svc.render_workbench(
            3, "compliance_auditor")
        view3 = (r3.get("renderOptions")
                 or {}).get("view") or {}
        record("审核员模板(预标注+判例)",
               view3.get("aiPrelabels")
               is True
               and view3.get("similarCases")
               is True,
               str(view3)[:60])

        # 管理员模板(风险热点)
        r4 = await svc.render_workbench(
            4, "platform_admin")
        view4 = (r4.get("renderOptions")
                 or {}).get("view") or {}
        record("管理员模板(风险热点+下发)",
               "风险热点" in str(
                   view4.get("dashboard")),
               str(view4)[:60])

        # 渲染落库
        from repositories.ab63_repository \
            import Ab63Repository
        repo = Ab63Repository()
        wb = await repo.get_workbench(
            r1.get("wbId"))
        record("渲染落库(renderOptions)",
               wb is not None
               and "view" in (
                   wb.get("renderOptions")
                   or {}),
               str(wb.get("viewKey")))

        # render 事件留痕
        events = await repo.list_events(
            event_type="render", limit=20)
        record("render 事件留痕(4 条)",
               len(events) == 4,
               str(len(events)))
        os.environ["AB63_MODE"] = "off"


class TestScorer:
    """04 第38档案八因子"""

    async def run(self):
        print("[04 第38档案]")
        reset_all()
        from services.ab63_scorer import (
            Ab63Scorer,
        )
        scorer = Ab63Scorer()

        # 空上下文拒绝
        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空上下文拒绝", ok, err)

        # 全因子高分
        r = await scorer.score({
            "guardEffectiveness": 0.9,
            "autoReviewAccuracy": 0.95,
            "permissionFitness": 0.9,
            "reviewConsistency": 0.85,
            "tier": "trusted",
            "appealOverturnRate": 0.03,
            "latencyP95Ok": 0.95,
            "roleCoverage": 1.0,
        })
        record("八因子齐备",
               len(r.get("factors") or []) == 8,
               str(len(r.get("factors"))))
        record("权重和=1.0",
               abs(sum((r.get(
                   "weightsUsed") or {})
                   .values()) - 1.0) < 0.01,
               str(sum((r.get(
                   "weightsUsed") or {})
                   .values())))
        record("高分→optimize/urgent",
               r.get("decision") in (
                   "optimize", "urgent"),
               str((r.get("trustScore"),
                    r.get("decision"))))

        # 低分→observe
        r2 = await scorer.score({
            "guardEffectiveness": 0.2,
            "autoReviewAccuracy": 0.3,
            "permissionFitness": 0.2,
            "reviewConsistency": 0.2,
            "tier": "restricted",
            "appealOverturnRate": 0.3,
            "latencyP95Ok": 0.3,
            "roleCoverage": 0.2,
        })
        record("低分→observe",
               r2.get("decision") == "observe"
               and (r2.get("trustScore")
                    or 0) < 50,
               str((r2.get("trustScore"),
                    r2.get("decision"))))

        # 申诉翻转反向因子
        r3 = await scorer.score({
            "appealOverturnRate": 0.02})
        r4 = await scorer.score({
            "appealOverturnRate": 0.2})
        f3 = [f for f in
              r3.get("factors")
              if f["name"]
              == "appeal_overturn"]
        f4 = [f for f in
              r4.get("factors")
              if f["name"]
              == "appeal_overturn"]
        record("申诉翻转反向(低翻转高分)",
               (f3[0]["score"] if f3 else 0)
               > (f4[0]["score"] if f4
                  else 100),
               str((f3[0]["score"] if f3
                    else None,
                    f4[0]["score"] if f4
                    else None)))

        # tier 基线
        r5 = await scorer.score({
            "tier": "trusted"})
        f5 = [f for f in
              r5.get("factors")
              if f["name"] == "member_trust"]
        record("tier 基线(trusted=90)",
               f5 and f5[0]["score"] == 90.0,
               str(f5[0]["score"] if f5
                   else None))

        # 覆盖率越界拒绝
        try:
            await scorer.score({
                "roleCoverage": 1.5})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "覆盖率" in str(e), \
                str(e)[:30]
        record("覆盖率越界拒绝", ok, err)

        # 因子明细八条
        names = {f["name"] for f in
                 r.get("factors")}
        record("因子明细八条",
               names == {
                   "guard_effectiveness",
                   "auto_review_accuracy",
                   "permission_fitness",
                   "review_consistency",
                   "member_trust",
                   "appeal_overturn",
                   "latency_budget",
                   "coverage_breadth"},
               str(sorted(names)))


class TestConstitution:
    """05 宪法断言"""

    async def run(self):
        print("[05 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 35 档案在册",
               len(SCORER_REGISTRY) == 35,
               str(len(SCORER_REGISTRY)))
        record("第38档案 admin_orchestration",
               "admin_orchestration"
               in SCORER_REGISTRY,
               "")

        from services.ii58_registry import (
            INTENT_REGISTRY,
        )
        record("58号 INTENT_REGISTRY 零改动",
               len(INTENT_REGISTRY) == 12,
               str(len(INTENT_REGISTRY)))

        # auth 中间件零改动(模块可导入)
        try:
            from core import auth_middleware
            record("auth_middleware 零改动"
                   "(模块在册)",
                   auth_middleware is not None,
                   "")
        except ImportError:
            record("auth_middleware 零改动"
                   "(模块在册)",
                   False, "导入失败")


class TestHttp:
    """06 HTTP 层"""

    async def run(self):
        print("[06 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 观测面 off 可用
        resp = client.get("/api/ab63/registry",
                          headers=admin)
        body = resp.json() or {}
        record("HTTP registry 观测面 200",
               resp.status_code == 200
               and body.get("ruleEntries")
               == 20
               and body.get("mode") == "off",
               str((resp.status_code,
                    body.get("ruleEntries"))))

        resp = client.get(
            "/api/ab63/model/status",
            headers=admin)
        record("HTTP model/status 200",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("status")
                    or {}).get("scorerId")
               == "admin_orchestration",
               str(resp.status_code))

        # 决策面 off 409
        resp = client.post(
            "/api/ab63/grants",
            json={"memberId": 1,
                  "role": "ally_merchant",
                  "action": "basic_crud"},
            headers=admin)
        record("HTTP grants off 409",
               resp.status_code == 409,
               str(resp.status_code))

        resp = client.post(
            "/api/ab63/workbench/render",
            json={"memberId": 1,
                  "role": "ally_merchant"},
            headers=admin)
        record("HTTP workbench off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 裁决列表观测面
        resp = client.get(
            "/api/ab63/grants",
            headers=admin)
        body = resp.json() or {}
        record("HTTP grants 列表观测面 200",
               resp.status_code == 200
               and body.get("total") == 0,
               str((resp.status_code,
                    body.get("total"))))

        # shadow 全链
        os.environ["AB63_MODE"] = "shadow"
        resp = client.post(
            "/api/ab63/grants",
            json={"memberId": 1,
                  "role": "ally_merchant",
                  "action": "basic_crud",
                  "tier": "trusted",
                  "complianceRate": 0.95},
            headers=admin)
        body = resp.json() or {}
        record("HTTP grants 200(granted)",
               resp.status_code == 200
               and body.get("granted") is True,
               str((resp.status_code,
                    body.get("granted"))))

        # 域外 409
        resp = client.post(
            "/api/ab63/grants",
            json={"memberId": 1,
                  "role": "hacker",
                  "action": "basic_crud"},
            headers=admin)
        record("HTTP 角色域外 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 工作台渲染
        resp = client.post(
            "/api/ab63/workbench/render",
            json={"memberId": 1,
                  "role": "ally_merchant",
                  "novice": True},
            headers=admin)
        body = resp.json() or {}
        record("HTTP workbench 200(novice)",
               resp.status_code == 200
               and body.get("view")
               == "noviceView",
               str((resp.status_code,
                    body.get("view"))))

        # 列表过滤
        resp = client.get(
            "/api/ab63/grants?role="
            "ally_merchant",
            headers=admin)
        body = resp.json() or {}
        record("HTTP grants 角色过滤",
               body.get("total") == 1,
               str(body.get("total")))

        # 鉴权 403
        for method, path in (
                ("GET", "/api/ab63/registry"),
                ("POST", "/api/ab63/grants"),
                ("GET", "/api/ab63/grants"),
                ("POST",
                 "/api/ab63/workbench/render"),
                ("GET",
                 "/api/ab63/model/status")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 21 端点(P5 +dashboard/redteam)
        from routes.ab63_routes import (
            router as ab_router,
        )
        count = sum(
            1 for r in ab_router.routes)
        record("63号路由累计 21 端点",
               count == 21, str(count))
        os.environ["AB63_MODE"] = "off"


async def run_all():
    await TestRegistry().run()
    await TestGrant().run()
    await TestWorkbench().run()
    await TestScorer().run()
    await TestConstitution().run()
    await TestHttp().run()


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
