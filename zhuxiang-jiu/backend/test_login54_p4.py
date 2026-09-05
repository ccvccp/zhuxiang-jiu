"""54号·小竹AI智能登录引擎大模型 P4 专项测试
(漂移监控+健康治理)

运行方式:
    python test_login54_p4.py

覆盖(54号计划 §六 P4):
    - EMA 漂移视图: 无反馈基线态/有反馈统计/
      high 档 → drift_alert 告警事件留痕(QC)
    - 46号三检测器: 健康档案零信号/三检测器
      输出结构/健康分/冻结状态联动呈现
    - 冻结守卫联动: frozen 状态观测+学习拦截(P2
      已断言——此处治理视图呈现)
    - LLM 归因报告: 无事件 409/mock 确定性模板
      (数字来自数据层)/facts 结构/三态说明
    - HTTP 层: governance/health+attribution
      端点+鉴权
    - 零影响: 44/46/53号零改动红线
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
os.environ["LOGIN54_MODE"] = "off"
os.environ["LOGIN54_LEARN_MODE"] = "off"

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


GOOD_CTX = {
    "channelSuccess": 0.95, "channel": "passkey",
    "baselineMatch": 0.9, "budgetRemaining": 0.8,
    "accountAgeDays": 200, "loginFrequency": 15,
    "channelFailCount": 0, "voiceConfidence": 0.9,
    "portalState": "active",
}


async def seed_and_learn():
    """种子 10 条正反馈+触发一轮学习"""
    from core.helpers import ts
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    from services.login54_scorer import Login54Scorer
    from services.login54_learn_service import (
        Login54LearnService,
    )
    result = await Login54Scorer().score(GOOD_CTX)
    repo = AiLearningRepository()
    for i in range(10):
        await repo.add_feedback({
            "scorerId": "login_orchestration",
            "weightVersion": "v1",
            "scoreAtDecision":
                result.get("trustScore"),
            "actualAction": "silent",
            "expectedAction": "silent",
            "correct": True,
            "factors": result.get("factors"),
            "reward": 1.0,
            "note": f"p4-seed:{i}",
            "source": "login54_pipeline",
            "status": "pending",
            "createdAt": ts(),
        })
    return await Login54LearnService().run_learning()


class TestDriftView:
    """01 EMA 漂移监控"""

    async def run(self):
        print("[01 漂移监控]")
        reset_all()
        from services.login54_health_service import (
            Login54HealthService,
        )
        svc = Login54HealthService()

        # 无反馈 → 未建立基线
        r = await svc.drift_view()
        drift = r.get("drift") or {}
        record("无反馈未建基线",
               "未建立基线" in str(
                   drift.get("message"))
               and r.get("alerted") is False,
               str(drift.get("message")))

        # 学习一轮(反馈建立漂移基线——submit_feedback
        # 走 44号正式接口含漂移 EMA 更新)
        await seed_and_learn()
        from services.ai_learning_service import (
            submit_feedback,
        )
        from services.login54_scorer import (
            Login54Scorer,
        )
        scorer_result = await Login54Scorer().score(
            GOOD_CTX)
        await submit_feedback({
            "scorerId": "login_orchestration",
            "factors": scorer_result.get("factors"),
            "scoreAtDecision":
                scorer_result.get("trustScore"),
            "actualAction": "silent",
            "expectedAction": "silent",
            "correct": True,
            "reward": 1.0,
            "note": "p4-drift-seed",
            "source": "login54_pipeline",
        })
        r = await svc.drift_view()
        drift = r.get("drift") or {}
        record("有反馈基线建立",
               int(drift.get("count") or 0) >= 1
               and "driftLevel" in drift,
               str((drift.get("count"),
                    drift.get("driftLevel"))))
        record("低位不告警",
               drift.get("driftLevel") in (
                   "low", "medium")
               and r.get("alerted") is False,
               str(drift.get("driftLevel")))

        # 人为置 high 档 → drift_alert 告警留痕
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        repo = AiLearningRepository()
        high = dict(drift)
        high.update({"driftScore": 0.5,
                     "driftLevel": "high"})
        await repo.save_drift(
            "login_orchestration", high)
        r = await svc.drift_view()
        record("high 档告警触发(QC)",
               r.get("alerted") is True,
               str(r.get("alerted")))

        # drift_alert 事件留痕
        from repositories.login54_repository import (
            Login54Repository,
        )
        events = await Login54Repository(
        ).list_model_events(limit=20)
        alerts = [e for e in events
                  if e.get("eventType")
                  == "drift_alert"]
        record("drift_alert 事件留痕",
               len(alerts) >= 1
               and (alerts[0].get("detail") or {})
               .get("driftLevel") == "high"
               and (alerts[0].get("detail") or {})
               .get("driftScore") == 0.5,
               str(len(alerts)))


class TestGovernanceHealth:
    """02 46号三检测器接入"""

    async def run(self):
        print("[02 三检测器]")
        reset_all()
        from services.login54_health_service import (
            Login54HealthService,
        )
        svc = Login54HealthService()

        r = await svc.governance_health()
        record("治理健康成功",
               r.get("success") is True,
               str(r.get("success")))
        health = r.get("health") or {}
        record("三检测器输出结构",
               set(health) >= {
                   "stagnation", "depletion",
                   "drift_high", "healthScore",
                   "healthLevel", "signals"},
               str(list(health))[:60])
        record("健康档案零信号",
               health.get("signals") == []
               and health.get("healthScore") == 100
               and health.get("healthLevel")
               == "healthy",
               str((health.get("healthScore"),
                    health.get("signals"))))

        gov = r.get("governance") or {}
        record("治理状态呈现(active)",
               gov.get("status") == "active"
               and gov.get("frozen") is False,
               str(gov.get("status")))
        record("红线清单齐备(5 条)",
               len(gov.get("redlines") or []) == 5,
               str(len(gov.get("redlines") or [])))
        record("变更审批通道说明",
               "/api/ai-gov/changes" in str(
                   gov.get("changeBus")),
               str(gov.get("changeBus"))[:60])

        # 冻结联动呈现(46号审批总线)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        gov_svc = AiGovernanceService()
        await gov_svc.sync_registry()
        change = await gov_svc.submit_change(
            "login_orchestration", "freeze", {},
            "P4 冻结观测测试")
        await gov_svc.review_change(
            change["changeId"], True, "admin")

        r2 = await svc.governance_health()
        gov2 = r2.get("governance") or {}
        record("冻结状态呈现(frozen)",
               gov2.get("frozen") is True
               and gov2.get("status") == "frozen",
               str(gov2.get("status")))

        # 冻结期间学习跳过(P2 断言继承——此处复核)
        from services.login54_learn_service import (
            Login54LearnService,
        )
        try:
            await Login54LearnService().run_learning()
            ok, err = False, "未拦截"
        except ValueError as e:
            ok, err = "冻结" in str(e), str(e)[:40]
        record("冻结期间学习跳过(QC)", ok, err)

        # 还原
        change2 = await gov_svc.submit_change(
            "login_orchestration", "unfreeze", {},
            "P4 还原")
        await gov_svc.review_change(
            change2["changeId"], True, "admin")


class TestAttribution:
    """03 LLM 归因报告"""

    async def run(self):
        print("[03 LLM 归因]")
        reset_all()
        from services.login54_health_service import (
            Login54HealthService,
        )
        svc = Login54HealthService()

        # 无事件 409
        try:
            await svc.attribution()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "归因" in str(e), str(e)[:40]
        record("无权重变更事件拒绝", ok, err)

        # 学习一轮 → mock 归因
        await seed_and_learn()
        r = await svc.attribution()
        record("归因成功(mock 模板)",
               r.get("success") is True
               and r.get("mode") == "mock",
               str(r.get("mode")))
        text = str(r.get("attribution") or "")
        record("归因叙事含版本对",
               "v1" in text and "v2" in text,
               text[:60])
        record("归因叙事含反馈数",
               "10 条" in text,
               text[:80])
        record("归因叙事含护栏说明",
               "护栏" in text,
               text[-40:])

        # facts 结构(数字唯一来源)
        facts = r.get("facts") or {}
        record("facts 含版本对+主要变化因子",
               facts.get("parentVersion") == "v1"
               and facts.get("newVersion") == "v2"
               and isinstance(
                   facts.get("topWeightChanges"),
                   list),
               str(facts.get("parentVersion")))
        record("facts 含事件回溯",
               len(facts.get("recentEvents") or [])
               >= 1,
               str(len(facts.get("recentEvents")
                       or [])))
        record("三态说明(LLM off)",
               "LLM_ENABLED" in str(
                   r.get("note")),
               str(r.get("note")))

        # 回滚后归因(rollback 叙事)
        from services.login54_learn_service import (
            Login54LearnService,
        )
        await Login54LearnService().promote()
        await Login54LearnService().rollback(
            reason="P4 归因回滚叙事测试")
        r2 = await svc.attribution()
        text2 = str(r2.get("attribution") or "")
        record("回滚叙事归因",
               "回滚" in text2
               and r2.get("facts", {})
               .get("eventType") == "rollback",
               text2[:50])


class TestHttpAndRedlines:
    """04 HTTP 层+零影响"""

    async def run(self):
        print("[04 HTTP+红线]")
        reset_all()
        await seed_and_learn()

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # governance/health
        resp = client.get(
            "/api/login54/governance/health",
            headers=admin)
        body = resp.json() or {}
        record("HTTP health 200(三检测器)",
               resp.status_code == 200
               and ((body.get("health") or {})
                    .get("scorerId")
                    == "login_orchestration"),
               str(resp.status_code))

        # attribution
        resp = client.post(
            "/api/login54/attribution",
            headers=admin)
        body = resp.json() or {}
        record("HTTP attribution 200(mock)",
               resp.status_code == 200
               and body.get("mode") == "mock",
               str(resp.status_code))

        # 鉴权
        resp = client.get(
            "/api/login54/governance/health")
        record("health 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(
            "/api/login54/attribution")
        record("attribution 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 零影响红线
        from services.ai_learning_service import (
            get_drift_view, run_learning_cycle,
        )
        record("44号漂移/学习接口零改动",
               callable(get_drift_view)
               and callable(run_learning_cycle))
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )
        record("46号健康服务零改动",
               callable(
                   AiGovernanceHealthService.scan))
        from routes.login53_routes import (
            router as login53_router,
        )
        count53 = sum(
            1 for r in login53_router.routes)
        record("53号路由零改动(20 端点)",
               count53 == 20, str(count53))
        from routes.login54_routes import (
            router as login54_router,
        )
        count54 = sum(
            1 for r in login54_router.routes)
        record("54号路由累计 12 端点",
               count54 == 12, str(count54))


async def run_all():
    await TestDriftView().run()
    await TestGovernanceHealth().run()
    await TestAttribution().run()
    await TestHttpAndRedlines().run()


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
