"""52号·小竹语音可用性评估引擎 P1 专项测试
(功能可信度管道: 测试任务库+执行引擎+五指标)

运行方式:
    python test_us52_p1.py

覆盖(52号计划 §七 P1):
    - 脚本库: 12 任务四类分布(4/3/3/2)/
      每任务自含 expectedIntent+expectedOutcome
    - 执行引擎: 正向意图命中(4)/
      高危挑战流+核销执行(2)/
      边界预算耗尽引导/
      对抗拒绝(伪造令牌/内部状态)/
      降级合规
    - 测试隔离: 独立号段校验/跨号段拒绝
    - 功能可信度五指标: FC 成功率/
      证据链/预算准确性(静态值)/确认率/
      意图准确率
    - off 铁律: 测试面/计算面拒绝
    - 端点+鉴权+零影响(宪法断言)
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
os.environ["US52_MODE"] = "off"

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
    import services.xiaozhu_executor as ex_mod
    ex_mod._EXECUTOR_SINGLETON = None


async def seed_binding(member_id: int):
    """绑定信值档案(高危任务前置)"""
    import uuid
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    from services.xiaozhu_service import XiaozhuService
    suffix = uuid.uuid4().hex[:10]
    tid = (await TrustProfileService().create_role(
        "person", f"us52-{suffix[:6]}",
        f"110101{suffix}4321"))["trustId"]
    await XiaozhuService().bind_trust(
        member_id, tid, note="us52-p1")
    return tid


class TestTaskLibrary:
    """01 脚本库结构"""

    def run(self):
        print("[01 脚本库]")
        from services.us52_task_engine import (
            TASK_LIBRARY,
        )
        record("任务总数(12)",
               len(TASK_LIBRARY) == 12,
               str(len(TASK_LIBRARY)))
        by_kind: dict = {}
        for t in TASK_LIBRARY.values():
            by_kind[t["kind"]] = \
                by_kind.get(t["kind"], 0) + 1
        record("四类分布(positive 4/boundary 2/"
               "sensitive 2/adversarial 4)",
               by_kind == {"positive": 4,
                           "sensitive": 2,
                           "boundary": 2,
                           "adversarial": 4},
               str(by_kind))
        record("每任务自含期望值",
               all(t.get("expectedOutcome")
                   and (t.get("expectedIntent")
                        or t.get("tool"))
                   for t in TASK_LIBRARY.values()))


class TestRunTests:
    """02 执行引擎(四类任务)"""

    async def run(self):
        print("[02 执行引擎]")
        reset_all()
        from services.us52_task_engine import (
            Us52TaskEngine,
        )
        engine = Us52TaskEngine()

        # off 态拒绝
        try:
            await engine.run_tests()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态测试拒绝", ok, err)

        # 号段校验
        os.environ["US52_MODE"] = "on"
        try:
            await engine.run_tests(member_id=999)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("跨号段会员拒绝", ok, err)

        # 前置: 绑定档案(高危任务需要)
        member_id = 5300
        await seed_binding(member_id)

        r = await engine.run_tests(member_id=member_id)
        record("测试集执行(testId=1, 12 任务)",
               r.get("testId") == 1
               and r.get("taskCount") == 12,
               f"{r.get('testId')}/"
               f"{r.get('taskCount')}")
        results = {x["taskId"]: x
                   for x in r.get("results") or []}
        record("执行无异常(12 结果)",
               len(results) == 12,
               str(len(results)))

        # 正向: 意图命中
        for tid in ("T-01", "T-02", "T-03", "T-04"):
            t = results.get(tid) or {}
            record(f"{tid} 正向意图命中"
                   f"({t.get('actualIntent')})",
                   t.get("pass") is True,
                   str(t.get("detail")))

        # 高危: 挑战流+核销
        t5 = results.get("T-05") or {}
        record("T-05 兑换挑战流发起",
               t5.get("pass") is True,
               str(t5.get("detail")))
        t6 = results.get("T-06") or {}
        record("T-06 确认核销执行",
               t6.get("pass") is True,
               str(t6.get("detail")))

        # 边界: 预算耗尽引导
        t7 = results.get("T-07") or {}
        record("T-07 预算耗尽引导话术",
               t7.get("pass") is True,
               str(t7.get("detail")))

        # 韧性: 降级合规
        t11 = results.get("T-11") or {}
        record("T-11 降级合规(只读不降级)",
               t11.get("pass") is True,
               str(t11.get("detail")))

        # 对抗: 拒绝类
        for tid in ("T-08", "T-09", "T-10",
                    "T-12"):
            t = results.get(tid) or {}
            record(f"{tid} 对抗被拒",
                   t.get("pass") is True,
                   str(t.get("detail")))

        # 会话留痕
        sessions = await engine.repo.list_sessions(
            limit=10)
        record("测试会话留痕(1 条)",
               len(sessions) == 1
               and sessions[0].get(
                   "taskCount") == 12,
               str(len(sessions)))


class TestFunctionalMetrics:
    """03 功能可信度五指标"""

    async def run(self):
        print("[03 功能可信度五指标]")
        reset_all()
        from services.us52_task_engine import (
            Us52TaskEngine,
        )
        member_id = 5310
        await seed_binding(member_id)
        engine = Us52TaskEngine()
        r = await engine.run_tests(member_id=member_id)
        test_id = r["testId"]

        from services.us52_service import (
            Us52MetricsService,
        )
        svc = Us52MetricsService()
        m = await svc.compute_functional_metrics(
            test_id=test_id)
        metrics = m.get("metrics") or {}
        record("五指标齐备",
               set(metrics) == {
                   "fc_success_rate",
                   "explain_ref_rate",
                   "budget_accuracy",
                   "confirm_rate",
                   "intent_accuracy"},
               str(list(metrics)))

        record("FC 成功率计算(≥0.5)",
               (metrics.get("fc_success_rate")
                or 0) >= 0.5,
               str(metrics.get("fc_success_rate")))
        record("预算准确性=1.0(静态值对齐)",
               metrics.get("budget_accuracy") == 1.0,
               str(metrics.get("budget_accuracy")))
        record("意图准确率计算(>0)",
               (metrics.get("intent_accuracy")
                or 0) > 0,
               str(metrics.get("intent_accuracy")))
        detail = m.get("detail") or {}
        record("审计样本采集(>0)",
               (detail.get("auditTotal") or 0) > 0,
               str(detail.get("auditTotal")))
        record("意图样本绑定 testId(>0)",
               (detail.get("intentSamples") or 0) > 0,
               str(detail.get("intentSamples")))

        # 指标接入快照管道(P0 compute_snapshot)
        snap = await svc.compute_snapshot(metrics)
        record("五指标接入决策快照",
               (snap.get("snapshot") or {})
               .get("sampleCount") == 5,
               str((snap.get("snapshot") or {})
                   .get("sampleCount")))
        os.environ["US52_MODE"] = "off"


class TestEndpoints:
    """04 端点+鉴权+零影响"""

    async def run(self):
        print("[04 端点+鉴权+零影响]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.post("/api/us52/tests/run",
                           headers=admin, json={})
        record("off 态 tests/run 409",
               resp.status_code == 409,
               str(resp.status_code))

        resp = client.get("/api/us52/tests",
                          headers=admin)
        body = resp.json() or {}
        record("tests 历史 200(含脚本库自描述)",
               resp.status_code == 200
               and len(body.get("taskLibrary")
                       or {}) == 12,
               str(resp.status_code))

        resp = client.post(
            "/api/us52/metrics/functional",
            headers=admin, json={})
        record("off 态 functional 409",
               resp.status_code == 409,
               str(resp.status_code))

        os.environ["US52_MODE"] = "on"
        member_id = 5320
        await seed_binding(member_id)
        resp = client.post("/api/us52/tests/run",
                           headers=admin,
                           json={"memberId": member_id})
        body = resp.json() or {}
        record("HTTP tests/run 200(12 任务)",
               resp.status_code == 200
               and body.get("taskCount") == 12,
               str(resp.status_code))

        resp = client.post(
            "/api/us52/metrics/functional",
            headers=admin, json={})
        body = resp.json() or {}
        record("HTTP functional 200(五指标)",
               resp.status_code == 200
               and len(body.get("metrics") or {})
               == 5,
               str(resp.status_code))

        resp = client.post("/api/us52/tests/run",
                          headers=admin,
                          json={"memberId": 999})
        record("HTTP 跨号段 409",
               resp.status_code == 409,
               str(resp.status_code))

        resp = client.post("/api/us52/tests/run",
                           json={"memberId": 5321})
        record("tests/run 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 零影响: 宪法断言
        from services.trust_scoring_service import (
            TrustValueScorer,
        )
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES,
        )
        from services.xiaozhu_fc_registry import (
            TOOL_REGISTRY,
        )
        record("45号九因子零改动",
               len(TrustValueScorer.LAYER_OF) == 9)
        record("50号14行为零改动",
               len(VOICE_RULES) == 14)
        record("49号17工具零改动",
               len(TOOL_REGISTRY) == 17)
        os.environ["US52_MODE"] = "off"


async def run_all():
    TestTaskLibrary().run()
    await TestRunTests().run()
    await TestFunctionalMetrics().run()
    await TestEndpoints().run()


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
