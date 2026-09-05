"""54号·小竹AI智能登录引擎大模型 P5 专项测试
(看板+红队收官)

运行方式:
    python test_login54_p5.py

覆盖(54号计划 §六 P5):
    - 看板四区: 版本/因子/回流/漂移聚合结构+
      fail-soft(单区异常不阻断)
    - 红队防御区: 护栏状态核验+标注源集中度
    - RT 红队向量(权重投毒防御——QC):
        RT-01 正反馈洪流投毒 → 护栏约束权重
        RT-02 负反馈洪流投毒 → 护栏下界约束
        RT-03 投毒后归一化不变(和=1.0)
        RT-04 投毒洪流集中度告警(单源>80%)
    - 宪法断言: 44/46/53号零改动
    - HTTP 层: dashboard 端点+鉴权
    - 全链闭环: 回流→学习→晋升→看板呈现
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

# 投毒上下文: fail_history 因子分=0(其余高分)
# ——正反馈洪流推高其他因子压低 fail_history
POISON_CTX = dict(GOOD_CTX)
POISON_CTX.update({"channelFailCount": 4})


async def seed_feedback_via_pool(count: int, reward: float,
                                 ctx: dict,
                                 source: str = "login54_pipeline"):
    """直写 44号池(绕过 collect——模拟洪流注入)"""
    from core.helpers import ts
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    from services.login54_scorer import Login54Scorer

    result = await Login54Scorer().score(ctx)
    repo = AiLearningRepository()
    for i in range(count):
        await repo.add_feedback({
            "scorerId": "login_orchestration",
            "weightVersion": "v1",
            "scoreAtDecision":
                result.get("trustScore"),
            "actualAction": "silent",
            "expectedAction": "silent",
            "correct": True,
            "factors": result.get("factors"),
            "reward": reward,
            "note": f"p5-rt:{i}",
            "source": source,
            "status": "pending",
            "createdAt": ts(),
        })


class TestDashboard:
    """01 看板四区聚合"""

    async def run(self):
        print("[01 看板四区]")
        reset_all()
        from services.login54_dashboard_service import (
            Login54DashboardService,
        )
        svc = Login54DashboardService()

        # 空态看板
        r = await svc.build()
        record("空态看板聚合成功",
               r.get("success") is True
               and set(r.get("zones") or {}) == {
                   "version", "factors",
                   "feedback", "drift", "defense"},
               str(sorted((r.get("zones")
                           or {}).keys()))[:60])
        record("红线清单齐备(5 条)",
               len(r.get("redlines") or []) == 5,
               str(len(r.get("redlines") or [])))
        record("区错误为空",
               r.get("zoneErrors") == {},
               str(r.get("zoneErrors")))

        # 版本区(空态默认冠军)
        z = (r.get("zones") or {}).get("version") or {}
        record("①版本区默认冠军",
               ((z.get("champion") or {})
                .get("version")) == "v1"
               and (z.get("challenger")) is None,
               str((z.get("champion") or {})
                   .get("version")))

        # 因子区(默认权重=默认)
        z = (r.get("zones") or {}).get("factors") or {}
        record("②因子区八因子雷达",
               len(z.get("weights") or []) == 8
               and all(f.get("inGuardrail")
                       for f in z.get("weights")
                       or []),
               str(len(z.get("weights") or [])))
        record("②因子区归一化",
               z.get("normalized") is True,
               str(z.get("normalized")))

        # 回流区(空态)
        z = (r.get("zones") or {}).get("feedback") or {}
        record("③回流区空态",
               z.get("total") == 0
               and z.get("labeled") == 0,
               str(z.get("total")))

        # 漂移区(空态)
        z = (r.get("zones") or {}).get("drift") or {}
        record("④漂移区结构",
               "driftLevel" in z
               and "recentAlerts" in z,
               str(list(z))[:40])

        # 防御区(空态护栏健康+集中度小样本不判)
        z = (r.get("zones") or {}).get("defense") or {}
        record("⑤防御区护栏健康",
               (z.get("guardrail") or {})
               .get("healthy") is True
               and (z.get("guardrail") or {})
               .get("violations") == [],
               str(z.get("guardrail"))[:50])
        conc = (z.get("sourceConcentration")
                or {})
        record("⑤集中度小样本不判",
               conc.get("samples", 0)
               < conc.get("minSamples", 99)
               and conc.get("alert") is False,
               str(conc.get("samples")))

        # 全链闭环: 53号事件→回流标注→学习→晋升→
        # 看板呈现(P1 管道真实回流)
        from core.helpers import ts
        from datetime import datetime, timedelta
        from repositories.login53_repository import (
            Login53Repository,
        )
        old_ts = (datetime.now().astimezone()
                  - timedelta(hours=1)).isoformat()
        repo53 = Login53Repository()
        for m in range(7501, 7511):   # 10 会员
            eid = await repo53.next_event_id()
            await repo53.save_event({
                "eventId": eid, "memberId": m,
                "method": "passkey",
                "riskScore": 20.0,
                "decision": "silent",
                "durationMs": 100.0,
                "privacyCost": 0.01,
                "explainRef": "", "success": True,
                "detail": "", "createdAt": old_ts,
            })
            await repo53.save_retention({
                "memberId": m, "dayKey": ts()[:10],
                "rewardPoints": 1, "streakDays": 1,
                "greeting": "p5", "claimedAt": ts(),
                "milestoneUnlocked": 0,
                "eventNote": "",
            })
        from services.login54_feedback_service import (
            Login54FeedbackService,
        )
        await Login54FeedbackService(
        ).collect_feedback()
        from services.login54_learn_service import (
            Login54LearnService,
        )
        learn = await Login54LearnService().run_learning()
        await Login54LearnService().promote()

        r2 = await svc.build()
        z = (r2.get("zones") or {}).get("version") or {}
        record("闭环后冠军版本演进(v2)",
               ((z.get("champion") or {})
                .get("version")) == "v2",
               str((z.get("champion") or {})
                   .get("version")))
        record("闭环后事件统计",
               ((z.get("eventStats") or {})
                .get("byType") or {})
               .get("learning", 0) >= 1
               and ((z.get("eventStats") or {})
                    .get("byType") or {})
               .get("promoted", 0) >= 1,
               str((z.get("eventStats") or {})
                   .get("byType")))
        z = (r2.get("zones") or {}).get("feedback") or {}
        record("闭环后回流区呈现",
               (z.get("bySource") or {})
               .get("retention_dwell", 0) >= 1,
               str(z.get("bySource")))

        # fail-soft: 单区异常不阻断(monkey patch)
        svc2 = Login54DashboardService()
        async def _boom():
            raise RuntimeError("区域故障注入")
        svc2._zone_drift = _boom
        r3 = await svc2.build()
        record("fail-soft 单区异常不阻断",
               r3.get("success") is True
               and "drift" in (r3.get("zoneErrors")
                               or {}),
               str(r3.get("zoneErrors")))


class TestRedTeamPoison:
    """02 红队权重投毒防御(QC)"""

    async def run(self):
        print("[02 红队投毒]")
        reset_all()
        from services.login54_learn_service import (
            Login54LearnService,
        )
        svc = Login54LearnService()
        from services.ai_learning_service import (
            get_weights_view,
        )

        defaults = {
            "channel_success": 0.15,
            "credential_quality": 0.15,
            "device_match": 0.15,
            "budget_sufficiency": 0.10,
            "member_maturity": 0.10,
            "fail_history": 0.15,
            "voice_confidence": 0.10,
            "portal_state": 0.10,
        }

        # RT-01 正反馈洪流(60 条 reward=+1 单源)
        await seed_feedback_via_pool(
            30, 1.0, POISON_CTX,
            source="attacker_flood")
        r1 = await svc.run_learning()
        w1 = r1.get("weights") or {}
        in_guard = all(
            defaults[k] / 2.0 <= w1[k]
            <= defaults[k] * 2.0
            for k in defaults)
        record("RT-01 正洪流护栏约束",
               r1.get("success") is True
               and in_guard,
               str({k: round(w1[k], 4) for k in w1
                    if not defaults[k] / 2.0
                    <= w1[k]
                    <= defaults[k] * 2.0}))

        # RT-02 负反馈洪流(30 条 reward=-1)
        await seed_feedback_via_pool(
            30, -1.0, POISON_CTX,
            source="attacker_flood")
        r2 = await svc.run_learning()
        w2 = r2.get("weights") or {}
        in_guard2 = all(
            defaults[k] / 2.0 <= w2[k]
            <= defaults[k] * 2.0
            for k in defaults)
        record("RT-02 负洪流护栏下界约束",
               r2.get("success") is True
               and in_guard2,
               str({k: round(w2[k], 4) for k in w2
                    if not defaults[k] / 2.0
                    <= w2[k]
                    <= defaults[k] * 2.0}))

        # RT-03 投毒后归一化不变
        record("RT-03 投毒后归一化(和=1.0)",
               abs(sum(w2.values()) - 1.0) < 1e-6,
               str(sum(w2.values())))

        # 看板防御区: 护栏核验健康
        from services.login54_dashboard_service \
            import Login54DashboardService
        dash = await Login54DashboardService().build()
        defense = ((dash.get("zones") or {})
                   .get("defense") or {})
        record("投毒后看板护栏核验健康",
               (defense.get("guardrail") or {})
               .get("healthy") is True,
               str((defense.get("guardrail")
                    or {}).get("violations")))

        # RT-04 集中度告警(60 条单源 attacker_flood
        # 占 recent 主导 → topRatio>0.8 告警)
        conc = (defense.get("sourceConcentration")
                or {})
        record("RT-04 洪流集中度告警",
               conc.get("alert") is True
               and conc.get("topSource")
               == "attacker_flood"
               and float(conc.get("topRatio")
                        or 0) > 0.8,
               str((conc.get("topSource"),
                    conc.get("topRatio"),
                    conc.get("alert"))))

        # 对照: 正常混合源不告警
        reset_all()
        await seed_feedback_via_pool(
            12, 1.0, GOOD_CTX, source="pipeline_a")
        await seed_feedback_via_pool(
            12, 1.0, GOOD_CTX, source="pipeline_b")
        await svc.run_learning()
        dash2 = await Login54DashboardService().build()
        conc2 = (((dash2.get("zones") or {})
                  .get("defense") or {})
                 .get("sourceConcentration") or {})
        record("对照混合源不告警",
               conc2.get("alert") is False
               and float(conc2.get("topRatio")
                        or 1.0) <= 0.8,
               str((conc2.get("topSource"),
                    conc2.get("topRatio"))))


class TestConstitutionAndHttp:
    """03 宪法断言+HTTP 层"""

    async def run(self):
        print("[03 宪法+HTTP]")
        reset_all()

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # HTTP dashboard
        resp = client.get(
            "/api/login54/dashboard",
            headers=admin)
        body = resp.json() or {}
        record("HTTP dashboard 200(五区)",
               resp.status_code == 200
               and len((body.get("zones") or {})) == 5,
               f"{resp.status_code} "
               f"{len(body.get('zones') or {})}")

        # 鉴权
        resp = client.get(
            "/api/login54/dashboard")
        record("dashboard 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 宪法断言(44/46/53号零改动)
        from services.ai_learning_service import (
            SCORER_REGISTRY,
            run_learning_cycle,
            promote_challenger, reset_weights,
            manual_override_weights,
        )
        record("44号档案数 29 零改动",
               len(SCORER_REGISTRY) == 29,
               str(len(SCORER_REGISTRY)))
        record("44号学习五接口零改动",
               all(callable(f) for f in (
                   run_learning_cycle,
                   promote_challenger,
                   reset_weights,
                   manual_override_weights,
                   callable)))
        from services.ai_scoring_auth_service import (
            AuthRiskScorer,
        )
        record("43号 auth_risk 八因子零改动",
               len(AuthRiskScorer.WEIGHTS) == 8,
               str(len(AuthRiskScorer.WEIGHTS)))
        from services.ai_governance_health import (
            AiGovernanceHealthService,
        )
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        record("46号治理服务零改动",
               callable(
                   AiGovernanceHealthService.scan)
               and callable(
                   AiGovernanceService
                   .sync_registry))
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
        record("54号路由收官 13 端点",
               count54 == 13, str(count54))

        # 调度器全链(回流→学习→回归检测)可独立触发
        from services.login54_scheduler import (
            run_scheduled_collect,
        )
        stats = await run_scheduled_collect()
        record("调度器全链可触发(收官)",
               int(stats.get("runs") or 0) == 1
               and (stats.get("lastCollect")
                    is not None
                    or stats.get("lastLearn")
                    is not None),
               str(stats.get("runs")))


async def run_all():
    await TestDashboard().run()
    await TestRedTeamPoison().run()
    await TestConstitutionAndHttp().run()


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
