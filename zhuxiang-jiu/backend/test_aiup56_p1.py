"""56号·AI智能升级管理模块 P1 专项测试
(规划+编码 Agent)

运行方式:
    python test_aiup56_p1.py

覆盖(56号计划 §九 P1):
    - 规划Agent: 任务拆解(信号→类别模板)/依赖
      分析/信值影响预估/回滚预案框架
    - 状态机: draft→planned→coded 流转+非法
      状态拒绝
    - 编码Agent: 代码草稿+测试计划+VALUE_REASON
      注释+资产版本化
    - LLM 回退: mock 确定性(shadow 态不走 real)
    - 预算铁律: LLM 成本计量(封顶内)
    - HTTP 层: 4 端点+鉴权+9 端点计数
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


async def seed_proposal():
    """种一个 draft 提案(强信号: 55号 指标劣化)"""
    from core.helpers import ts
    from repositories.qr55_repository import (
        Qr55Repository,
    )
    repo55 = Qr55Repository()
    for snap in (
            {"satisfactionScore": 80.0,
             "clarifyEfficiency": 0.8,
             "penetrationRate": 0.7},
            {"satisfactionScore": 60.0,
             "clarifyEfficiency": 0.5,
             "penetrationRate": 0.4}):
        meid = await repo55.next_model_event_id()
        await repo55.save_model_event({
            "modelEventId": meid,
            "eventType": "metrics_snapshot",
            "detail": {"metrics": snap},
            "createdAt": ts(),
        })
    from services.aiup56_service import Aiup56Service
    r = await Aiup56Service().evaluate_and_propose()
    return r


class TestPlan:
    """01 规划Agent"""

    async def run(self):
        print("[01 规划Agent]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        r = await seed_proposal()
        proposal_id = r.get("proposalId")

        from services.aiup56_plan_service import (
            Aiup56PlanService,
        )
        plan = Aiup56PlanService()

        # off 态拒绝
        os.environ["AIUP56_MODE"] = "off"
        try:
            await plan.plan(proposal_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态规划拒绝", ok, err)
        os.environ["AIUP56_MODE"] = "shadow"

        # 不存在提案
        try:
            await plan.plan(99999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("不存在提案 404 语义", ok, err)

        # 规划执行(shadow → mock 轨)
        p = await plan.plan(proposal_id)
        record("规划执行(mock 轨)",
               p.get("success") is True
               and p.get("status") == "planned"
               and p.get("mode") == "mock",
               str((p.get("status"),
                    p.get("mode"))))

        # 任务产出(usability 类——qr55 信号命中)
        tasks = p.get("tasks") or []
        record("任务拆解(usability 模板)",
               len(tasks) == 2
               and all("[usability]" in t["title"]
                      for t in tasks),
               str([t["title"] for t in tasks]))

        # 任务结构(依赖+信值预估+回滚预案)
        t0 = tasks[0]
        record("任务结构(依赖+预算+增益)",
               bool(t0.get("dependencies"))
               and t0.get("privacyCost") is not None
               and t0.get("estimatedGain")
               is not None,
               str(t0.get("dependencies")))
        rollback = t0.get("rollbackPlan") or {}
        record("回滚预案框架(strategy+steps)",
               bool(rollback.get("strategy"))
               and "steps" in rollback
               and "dataCleanup" in rollback,
               str(rollback.get("strategy")))

        # 信值影响预估汇总
        record("信值预估汇总(estimatedGain)",
               (p.get("estimatedGain") or 0) > 0,
               str(p.get("estimatedGain")))

        # 状态机: 重复规划拒绝
        try:
            await plan.plan(proposal_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "planned" in str(e), str(e)[:40]
        record("重复规划拒绝(状态机)", ok, err)

        # 任务列表(观测面)
        tl = await plan.list_tasks(proposal_id)
        record("任务列表(观测面)",
               tl.get("total") == 2
               and tl.get("planMode") == "mock",
               str(tl.get("total")))

        # 规划事件留痕
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        events = await Aiup56Repository().list_events(
            proposal_id=proposal_id, limit=50)
        plan_evs = [e for e in events
                   if e.get("eventType") == "plan"]
        record("plan 事件留痕",
               len(plan_evs) == 1
               and (plan_evs[0].get("detail")
                    or {}).get("tasks") == 2,
               str(len(plan_evs)))

        # 状态翻转落库
        proposal = await Aiup56Repository(
        ).get_proposal(proposal_id)
        record("提案状态 planned",
               proposal.get("status") == "planned"
               and proposal.get("plannedTasks") == 2,
               str(proposal.get("status")))


class TestCode:
    """02 编码Agent"""

    async def run(self):
        print("[02 编码Agent]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        r = await seed_proposal()
        proposal_id = r.get("proposalId")

        from services.aiup56_plan_service import (
            Aiup56PlanService,
        )
        from services.aiup56_code_service import (
            Aiup56CodeService,
        )
        await Aiup56PlanService().plan(proposal_id)
        coder = Aiup56CodeService()

        # 状态机: 未规划不可编码
        reset_all()
        r2 = await seed_proposal()
        try:
            await coder.code(r2.get("proposalId"))
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "draft" in str(e), str(e)[:40]
        record("未规划编码拒绝(状态机)", ok, err)
        await Aiup56PlanService().plan(
            r2.get("proposalId"))

        # off 态拒绝
        os.environ["AIUP56_MODE"] = "off"
        try:
            await coder.code(r2.get("proposalId"))
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态编码拒绝", ok, err)
        os.environ["AIUP56_MODE"] = "shadow"

        # 编码执行(shadow → mock 轨)
        c = await coder.code(r2.get("proposalId"))
        record("编码执行(mock 轨+状态 coded)",
               c.get("success") is True
               and c.get("status") == "coded"
               and c.get("mode") == "mock",
               str((c.get("status"),
                    c.get("mode"))))

        # 资产版本 v1
        record("资产版本化(v1)",
               c.get("assetVersion") == 1
               and (c.get("assetId") or 0) > 0,
               str((c.get("assetId"),
                    c.get("assetVersion"))))

        # VALUE_REASON 注释即证据
        record("VALUE_REASON 注释产出",
               (c.get("valueReasonCount") or 0) >= 2,
               str(c.get("valueReasonCount")))

        # 资产结构(草稿+测试计划)
        assets = await coder.list_assets(
            r2["proposalId"])
        asset = (assets.get("assets") or [{}])[0]
        record("资产结构(草稿+测试计划)",
               len(asset.get("drafts") or []) == 2
               and len(asset.get("testPlans") or [])
               == 2,
               str(len(asset.get("drafts") or [])))
        draft = (asset.get("drafts")
                 or [{}])[0]
        record("草稿内容(类型注解+VALUE_REASON)",
               "VALUE_REASON" in str(
                   draft.get("code"))
               and draft.get("language") == "python",
               str(draft.get("language")))
        test_plan = (asset.get("testPlans")
                      or [{}])[0]
        record("测试计划(用例矩阵 normal/"
               "boundary/exception)",
               len(test_plan.get("cases") or [])
               == 3
               and {c["type"] for c
                    in test_plan["cases"]}
               == {"normal", "boundary",
                   "exception"},
               str(len(test_plan.get("cases")
                        or [])))

        # 重复编码拒绝(状态机——coded 后)
        try:
            await coder.code(r2["proposalId"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "coded" in str(e), str(e)[:40]
        record("重复编码拒绝(状态机)", ok, err)

        # code 事件留痕
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        events = await Aiup56Repository().list_events(
            proposal_id=r2["proposalId"],
            limit=50)
        code_evs = [e for e in events
                    if e.get("eventType") == "code"]
        record("code 事件留痕",
               len(code_evs) == 1,
               str(len(code_evs)))

        # 预算(mock 轨零成本)
        proposal = await Aiup56Repository(
        ).get_proposal(r2["proposalId"])
        record("mock 轨零预算成本",
               proposal.get("budgetSpent") in
               (0, 0.0, None),
               str(proposal.get("budgetSpent")))


class TestHttp:
    """03 HTTP 层"""

    async def run(self):
        print("[03 HTTP]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        r = await seed_proposal()
        proposal_id = r.get("proposalId")

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 409
        os.environ["AIUP56_MODE"] = "off"
        resp = client.post(
            f"/api/aiup56/proposals/{proposal_id}"
            f"/plan", headers=admin)
        record("HTTP plan off 409",
               resp.status_code == 409,
               str(resp.status_code))
        os.environ["AIUP56_MODE"] = "shadow"

        # plan 200
        resp = client.post(
            f"/api/aiup56/proposals/{proposal_id}"
            f"/plan", headers=admin)
        body = resp.json() or {}
        record("HTTP plan 200(mock)",
               resp.status_code == 200
               and body.get("mode") == "mock"
               and body.get("status") == "planned",
               str(resp.status_code))

        # tasks 观测面
        resp = client.get(
            f"/api/aiup56/proposals/{proposal_id}"
            f"/tasks", headers=admin)
        record("HTTP tasks 观测面",
               resp.status_code == 200
               and (resp.json() or {}).get(
                   "total") == 2,
               str(resp.status_code))

        # code 200
        resp = client.post(
            f"/api/aiup56/proposals/{proposal_id}"
            f"/code", headers=admin)
        body = resp.json() or {}
        record("HTTP code 200(资产 v1)",
               resp.status_code == 200
               and body.get("assetVersion") == 1,
               str((resp.status_code,
                    body.get("assetVersion"))))

        # assets 观测面
        resp = client.get(
            f"/api/aiup56/proposals/{proposal_id}"
            f"/assets", headers=admin)
        record("HTTP assets 观测面",
               resp.status_code == 200
               and (resp.json() or {}).get(
                   "total") == 1,
               str(resp.status_code))

        # 404
        resp = client.get(
            "/api/aiup56/proposals/99999/tasks",
            headers=admin)
        record("HTTP tasks 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权
        for method, path in (
                ("POST",
                 f"/api/aiup56/proposals/"
                 f"{proposal_id}/plan"),
                ("GET",
                 f"/api/aiup56/proposals/"
                 f"{proposal_id}/tasks"),
                ("POST",
                 f"/api/aiup56/proposals/"
                 f"{proposal_id}/code"),
                ("GET",
                 f"/api/aiup56/proposals/"
                 f"{proposal_id}/assets")):
            resp = client.request(method, path)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 9 端点
        from routes.aiup56_routes import (
            router as aiup_router,
        )
        count = sum(1 for r in aiup_router.routes)
        # P2 新增 2 端点(test/sandboxes)
        # → 9→11(基线语义: ≥9——P1 交付面不因
        # P2 演进破坏)
        record("56号路由累计 ≥9 端点(P2 扩至 11)",
               count >= 9, str(count))
        os.environ["AIUP56_MODE"] = "off"


async def run_all():
    await TestPlan().run()
    await TestCode().run()
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
