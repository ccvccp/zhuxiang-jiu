"""56号·AI智能升级管理模块 P2 专项测试
(测试Agent+信值沙箱)

运行方式:
    python test_aiup56_p2.py

覆盖(56号计划 §九 P2):
    - 用例矩阵: normal/boundary/exception 三型
    - 静态关: 敏感 API 黑名单+PII 字面量
    - 预算关: 封顶内通过/超支熔断 budget_halted
    - 价值关: 增益达标/不足 blocked
    - 三关通过: verdict passed + 状态 tested
    - 状态机: coded→tested/blocked 流转
    - HTTP 层: 2 端点+鉴权+11 端点计数
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


async def seed_full_proposal():
    """种一个 coded 提案(强信号→决策→规划→编码)"""
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
    from services.aiup56_plan_service import (
        Aiup56PlanService,
    )
    from services.aiup56_code_service import (
        Aiup56CodeService,
    )
    r = await Aiup56Service().evaluate_and_propose()
    pid = r["proposalId"]
    await Aiup56PlanService().plan(pid)
    await Aiup56CodeService().code(pid)
    return pid


class TestSandbox:
    """01 用例矩阵+三关评估"""

    async def run(self):
        print("[01 三关评估]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        pid = await seed_full_proposal()

        from services.aiup56_test_service import (
            Aiup56TestService,
        )
        tester = Aiup56TestService()

        # off 拒绝
        os.environ["AIUP56_MODE"] = "off"
        try:
            await tester.test(pid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态测试拒绝", ok, err)
        os.environ["AIUP56_MODE"] = "shadow"

        # 不存在提案
        try:
            await tester.test(99999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("不存在提案 404 语义", ok, err)

        # 正常执行(三关全过)
        t = await tester.test(pid)
        record("三关通过(passed+tested)",
               t.get("verdict") == "passed"
               and t.get("status") == "tested",
               str((t.get("verdict"),
                    t.get("status"))))

        # 用例矩阵
        cases = t.get("caseMatrix") or []
        record("用例矩阵(6 用例三型)",
               len(cases) == 6
               and all(c.get("passed")
                       for c in cases),
               str(len(cases)))
        case_types = {c.get("type") for c in cases}
        record("用例三型覆盖",
               case_types == {"normal",
                              "boundary",
                              "exception"},
               str(case_types))

        # 三关结构
        gates = t.get("gates") or {}
        record("静态关通过(无违规)",
               (gates.get("static") or {})
               .get("passed") is True
               and (gates.get("static") or {})
               .get("violations") == [],
               str(gates.get("static")))
        record("预算关通过(封顶内)",
               (gates.get("budget") or {})
               .get("passed") is True
               and (gates.get("budget") or {})
               .get("mode") == "within_cap",
               str(gates.get("budget")))
        record("价值关通过(增益达标)",
               (gates.get("value") or {})
               .get("passed") is True
               and (gates.get("value") or {})
               .get("estimatedGain", 0) > 0,
               str(gates.get("value")))

        # 沙箱留痕
        sandboxes = await tester.list_sandboxes(pid)
        record("沙箱留痕(观测面)",
               (sandboxes.get("total") or 0) == 1
               and (sandboxes.get("sandboxes")
                    or [{}])[0].get("verdict")
               == "passed",
               str(sandboxes.get("total")))

        # test 事件留痕
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        events = await Aiup56Repository().list_events(
            proposal_id=pid, limit=50)
        test_evs = [e for e in events
                    if e.get("eventType")
                    == "test"]
        record("test 事件留痕",
               len(test_evs) == 1
               and (test_evs[0].get("detail")
                    or {}).get("verdict")
               == "passed",
               str(len(test_evs)))

        # 重复测试拒绝(状态机——tested)
        try:
            await tester.test(pid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "tested" in str(e), str(e)[:40]
        record("重复测试拒绝(状态机)", ok, err)

        # 未编码拒绝(状态机)
        reset_all()
        pid2 = await seed_full_proposal()
        from repositories.aiup56_repository import (
            Aiup56Repository as Repo,
        )
        p2 = await Repo().get_proposal(pid2)
        p2["status"] = "planned"
        await Repo().save_proposal(p2, create=False)
        try:
            await tester.test(pid2)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "coded" in str(e), str(e)[:40]
        record("未编码测试拒绝(状态机)", ok, err)


class TestGates:
    """02 三关各门禁"""

    async def run(self):
        print("[02 门禁单测]")
        from services.aiup56_test_service import (
            Aiup56TestService,
        )
        tester = Aiup56TestService()

        # 静态关: 敏感 API 检出
        gate = tester._static_gate([{
            "title": "恶意草稿",
            "code": "import requests\n"
                    "requests.get('http://evil')\n"
                    "result = eval(user_input)\n",
        }])
        record("静态关敏感 API 检出",
               gate.get("passed") is False
               and len(gate.get("violations")
                      or []) == 2,
               str(gate.get("violations")))

        # 静态关: PII 检出
        gate2 = tester._static_gate([{
            "title": "PII 草稿",
            "code": "phone = 13800138000\n",
        }])
        record("静态关 PII 字面量检出",
               gate2.get("passed") is False
               and any("手机号" in v for v
                       in gate2.get("violations")
                       or []),
               str(gate2.get("violations")))

        # 静态关: 干净草稿通过
        gate3 = tester._static_gate([{
            "title": "干净草稿",
            "code": "def add(a: int, b: int)"
                    " -> int:\n    return a + b\n",
        }])
        record("静态关干净草稿通过",
               gate3.get("passed") is True
               and gate3.get("violations") == [],
               str(gate3.get("violations")))

        # 静态关: 自造加密告警(不阻断)
        gate4 = tester._static_gate([{
            "title": "弱加密",
            "code": "import hashlib\n"
                    "h = hashlib.md5(b'x')\n",
        }])
        record("静态关弱加密告警不阻断",
               gate4.get("passed") is True
               and len(gate4.get("warnings")
                      or []) >= 1,
               str(gate4.get("warnings")))

        # 预算关: 封顶内
        bg, _ = await tester._budget_gate({
            "budgetSpent": 0.01,
            "budgetCap": 0.1,
        })
        record("预算关封顶内通过",
               bg.get("passed") is True,
               str(bg.get("projected")))

        # 预算关: 超支熔断
        bg2, note = await tester._budget_gate({
            "budgetSpent": 0.15,
            "budgetCap": 0.1,
        })
        record("预算关超支熔断(halted)",
               bg2.get("passed") is False
               and bg2.get("verdict") == "halted"
               and "熔断" in note,
               str((bg2.get("verdict"), note)))

        # 价值关: 增益不足
        vg = tester._value_gate({
            "estimatedGain": 0.0})
        record("价值关增益不足 blocked",
               vg.get("passed") is False,
               str(vg.get("estimatedGain")))

        # 价值关: 达标
        vg2 = tester._value_gate({
            "estimatedGain": 1.2})
        record("价值关增益达标",
               vg2.get("passed") is True,
               str(vg2.get("estimatedGain")))


class TestBlockedFlow:
    """03 blocked 流(预算熔断全链)"""

    async def run(self):
        print("[03 blocked 流]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        pid = await seed_full_proposal()

        # 耗尽预算(budgetSpent > cap)
        from repositories.aiup56_repository import (
            Aiup56Repository,
        )
        proposal = await Aiup56Repository(
        ).get_proposal(pid)
        proposal["budgetSpent"] = 0.5
        proposal["budgetCap"] = 0.1
        await Aiup56Repository().save_proposal(
            proposal, create=False)

        from services.aiup56_test_service import (
            Aiup56TestService,
        )
        t = await Aiup56TestService().test(pid)
        record("预算熔断(budget_halted)",
               t.get("verdict") == "budget_halted"
               and t.get("status") == "blocked",
               str((t.get("verdict"),
                    t.get("status"))))
        record("熔断语义(告警留痕)",
               "熔断" in str(
                   t.get("budgetNote")),
               str(t.get("budgetNote")))

        # 沙箱留痕 verdict
        sandboxes = await Aiup56Repository(
        ).list_sandboxes(proposal_id=pid)
        record("沙箱留痕(budget_halted)",
               (sandboxes or [{}])[0].get(
                   "verdict") == "budget_halted",
               str(len(sandboxes)))


class TestHttp:
    """04 HTTP 层"""

    async def run(self):
        print("[04 HTTP]")
        reset_all()
        os.environ["AIUP56_MODE"] = "shadow"
        pid = await seed_full_proposal()

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 409
        os.environ["AIUP56_MODE"] = "off"
        resp = client.post(
            f"/api/aiup56/proposals/{pid}/test",
            headers=admin)
        record("HTTP test off 409",
               resp.status_code == 409,
               str(resp.status_code))
        os.environ["AIUP56_MODE"] = "shadow"

        # test 200
        resp = client.post(
            f"/api/aiup56/proposals/{pid}/test",
            headers=admin)
        body = resp.json() or {}
        record("HTTP test 200(passed)",
               resp.status_code == 200
               and body.get("verdict") == "passed",
               str((resp.status_code,
                    body.get("verdict"))))

        # sandboxes 观测面
        resp = client.get(
            f"/api/aiup56/proposals/{pid}"
            f"/sandboxes", headers=admin)
        record("HTTP sandboxes 观测面",
               resp.status_code == 200
               and (resp.json() or {}).get(
                   "total") == 1,
               str(resp.status_code))

        # 鉴权
        for method, path in (
                ("POST",
                 f"/api/aiup56/proposals/{pid}"
                 f"/test"),
                ("GET",
                 f"/api/aiup56/proposals/{pid}"
                 f"/sandboxes")):
            resp = client.request(method, path)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 11 端点
        from routes.aiup56_routes import (
            router as aiup_router,
        )
        count = sum(1 for r in aiup_router.routes)
        # P3 新增 3 端点(audit/panel/review)
        # → 11→14(基线语义: ≥11——P2 交付面不因
        # P3 演进破坏)
        record("56号路由累计 ≥11 端点(P3 扩至 14)",
               count >= 11, str(count))
        os.environ["AIUP56_MODE"] = "off"


async def run_all():
    await TestSandbox().run()
    await TestGates().run()
    await TestBlockedFlow().run()
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
