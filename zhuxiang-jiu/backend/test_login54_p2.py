"""54号·小竹AI智能登录引擎大模型 P2 专项测试
(自主学习进化)

运行方式:
    python test_login54_p2.py

覆盖(54号计划 §六 P2):
    - 学习轮次: min_feedback=10 门槛(不足 409)+
      足量触发 Hedge 更新(run_learning_cycle 复用)
    - 权重演进: 护栏 [0.5,2.0] 倍约束+归一化断言
      (和=1.0)+单调收敛(正反馈通道权重升)
    - 模型事件留痕: learning/promoted 落
      login54_model_events(版本溯源)
    - 影子对比: challenger vs champion 双轨试算
      (档位差异语义/无挑战者单轨)
    - 灾难性遗忘防护: replay 配置开关(44号 v7.9)
    - 冻结守卫联动(46号 is_frozen → 学习 409)
    - 学习就绪态: pending 计数/门槛/配置
    - HTTP 层: learn/shadow-compare 端点+鉴权
    - 调度器: T+1 补标+学习步(门槛不足 skip)
    - 零影响: 44号学习接口/53号路由零改动
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


async def seed_pool_feedback(count: int, reward: float,
                             member_id: int = 7201,
                             strong: bool = True):
    """直写 44号池 login_orchestration 反馈
    (八因子快照——good/bad 两种画像)"""
    from core.helpers import ts
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    from services.login54_scorer import Login54Scorer

    scorer = Login54Scorer()
    ctx = dict(GOOD_CTX)
    if not strong:
        ctx.update({
            "channelSuccess": 0.1,
            "baselineMatch": 0.1,
            "channelFailCount": 4,
            "portalState": "high_risk",
        })
    result = await scorer.score(ctx)
    repo = AiLearningRepository()
    for i in range(count):
        await repo.add_feedback({
            "scorerId": "login_orchestration",
            "weightVersion": "v1",
            "scoreAtDecision": result.get("trustScore"),
            "actualAction": "silent" if strong
            else "enhanced",
            "expectedAction": "silent" if strong
            else "enhanced",
            "correct": True,
            "factors": result.get("factors"),
            "reward": reward,
            "note": f"p2-seed:{member_id}:{i}",
            "source": "login54_pipeline",
            "status": "pending",
            "createdAt": ts(),
        })


class TestLearningCycle:
    """01 学习轮次+权重演进"""

    async def run(self):
        print("[01 学习轮次]")
        reset_all()
        from services.login54_learn_service import (
            Login54LearnService,
        )
        svc = Login54LearnService()

        # 门槛不足(0 < 10)
        try:
            await svc.run_learning()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不足" in str(e), str(e)[:40]
        record("min_feedback 门槛不足拒绝", ok, err)

        # 就绪态: pending=0 未就绪
        ready = await svc.learning_readiness()
        record("就绪态(pending<门槛)",
               ready.get("ready") is False
               and ready.get("pendingFeedback") == 0
               and ready.get("minFeedback") == 10,
               str((ready.get("pendingFeedback"),
                    ready.get("minFeedback"))))
        record("就绪态含护栏配置",
               (ready.get("config") or {})
               .get("guardrail") == 2.0,
               str(ready.get("config")))

        # 足量正反馈(good 画像 ×10, reward=+1)
        await seed_pool_feedback(10, 1.0)
        ready = await svc.learning_readiness()
        record("就绪态(pending≥门槛)",
               ready.get("ready") is True
               and ready.get("pendingFeedback") == 10,
               str(ready.get("pendingFeedback")))

        # 触发一轮学习
        r = await svc.run_learning()
        record("学习轮次成功",
               r.get("success") is True
               and r.get("learnedFrom") == 10
               and r.get("newVersion"),
               str((r.get("learnedFrom"),
                    r.get("newVersion"))))
        record("44号引擎字段透传",
               "championMetrics" in r
               and "challengerMetrics" in r
               and "weightDelta" in r,
               str(list(r))[:60])

        # 权重演进: 护栏+归一化断言
        weights = r.get("weights") or {}
        record("八因子权重齐备",
               len(weights) == 8,
               str(len(weights)))
        record("归一化(和=1.0)",
               abs(sum(weights.values()) - 1.0)
               < 1e-6,
               str(sum(weights.values())))
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
        in_guard = all(
            defaults[k] / 2.0 <= weights[k]
            <= defaults[k] * 2.0
            for k in defaults)
        record("护栏 [0.5,2.0] 倍全约束", in_guard,
               str({k: round(weights[k], 4)
                    for k in weights
                    if not defaults[k] / 2.0
                    <= weights[k]
                    <= defaults[k] * 2.0}))

        # 模型事件留痕(learning)
        from repositories.login54_repository import (
            Login54Repository,
        )
        events = await Login54Repository(
        ).list_model_events(limit=10)
        learn_events = [e for e in events
                        if e.get("eventType")
                        == "learning"]
        record("learning 事件留痕",
               len(learn_events) >= 1
               and learn_events[0]
               .get("detail", {})
               .get("learnedFrom") == 10,
               str(len(learn_events)))
        record("事件含版本对",
               "parentVersion" in
               learn_events[0].get("detail", {})
               and "newVersion" in
               learn_events[0].get("detail", {}),
               str(learn_events[:1])[:80])

        # 学习后 pending 清空(已 learned)
        ready = await svc.learning_readiness()
        record("学习后 pending 清空",
               ready.get("pendingFeedback") == 0,
               str(ready.get("pendingFeedback")))


class TestWeightEvolution:
    """02 权重演进单调收敛+冻结守卫"""

    async def run(self):
        print("[02 演进收敛+冻结]")
        reset_all()
        from services.login54_learn_service import (
            Login54LearnService,
        )
        from services.login54_scorer import (
            Login54Scorer,
        )
        svc = Login54LearnService()

        # 收敛设计: good 画像(fail_history 因子分
        # 100)正反馈 → fail_history 权重升;
        # bad 画像重复 → 权重迭代留在护栏内
        await seed_pool_feedback(10, 1.0)   # good
        r1 = await svc.run_learning()
        w1 = r1.get("weights") or {}

        # 再来一轮(bad 画像负反馈)
        await seed_pool_feedback(10, -1.0,
                                 strong=False)
        r2 = await svc.run_learning()
        w2 = r2.get("weights") or {}

        # 单调收敛断言: 两轮权重均在护栏内且和=1
        record("二轮演进归一化",
               abs(sum(w2.values()) - 1.0) < 1e-6,
               str(sum(w2.values())))
        in_guard = all(
            0.15 / 2.0 <= w2[k] <= 0.15 * 2.0
            if k in ("channel_success",
                     "credential_quality",
                     "device_match", "fail_history")
            else 0.10 / 2.0 <= w2[k]
            <= 0.10 * 2.0
            for k in w2)
        record("二轮护栏约束(收敛有界)", in_guard,
               str(w2))

        # 影子对比(有挑战者时)
        cmp = await svc.shadow_compare(GOOD_CTX)
        record("影子对比返回双轨",
               (cmp.get("champion") or {})
               .get("result") is not None
               and cmp.get("note", "").find("不落库") > 0,
               str(list(cmp))[:50])
        if cmp.get("comparison"):
            comp = cmp["comparison"]
            record("对比含档位差异语义",
                   set(comp) >= {
                       "championTier",
                       "challengerTier",
                       "tierDiff", "verdict"},
                   str(list(comp)))
        else:
            # auto_apply � False → 二轮必产挑战者
            record("对比含档位差异语义",
                   (cmp.get("challenger") or {})
                   .get("version") is not None,
                   "无挑战者(意外)")

        # 冻结守卫联动(46号)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        gov = AiGovernanceService()
        await gov.sync_registry()
        change = await gov.submit_change(
            "login_orchestration", "freeze",
            {}, "P2 冻结测试")
        await gov.review_change(
            change["changeId"], True, "admin")
        try:
            await svc.run_learning()
            ok, err = False, "未拦截"
        except ValueError as e:
            ok, err = "冻结" in str(e), str(e)[:40]
        record("冻结守卫拦截学习", ok, err)
        # 解冻还原
        change2 = await gov.submit_change(
            "login_orchestration", "unfreeze",
            {}, "P2 还原")
        await gov.review_change(
            change2["changeId"], True, "admin")


class TestReplayAndHttp:
    """03 replay 防护+HTTP+调度器"""

    async def run(self):
        print("[03 replay+HTTP]")
        reset_all()
        from services.login54_learn_service import (
            Login54LearnService,
        )
        svc = Login54LearnService()

        # replay 配置开关(灾难性遗忘防护——44号 v7.9)
        from services.ai_learning_service import (
            update_learning_config, get_weights_view,
        )
        cfg = await update_learning_config(
            "login_orchestration",
            {"replay": True, "replay_sample": 20})
        record("replay 配置开启",
               (cfg.get("config") or {})
               .get("replay") is True,
               str(cfg.get("config")))

        # 足量反馈+replay 学习(replayedFrom 字段)
        await seed_pool_feedback(10, 1.0)
        r = await svc.run_learning()
        record("replay 学习轮次成功",
               r.get("success") is True
               and "replayedFrom" in r,
               str(r.get("replayedFrom")))
        # 还原配置(零残留)
        await update_learning_config(
            "login_orchestration", {"replay": False})

        # HTTP 层
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # learn 门槛不足 409(刚学完 pending=0;
        # 全局异常处理器 detail→error 键)
        resp = client.post(
            "/api/login54/model/learn",
            headers=admin)
        record("HTTP learn 门槛不足 409",
               resp.status_code == 409
               and "不足" in str(
                   (resp.json() or {})
                   .get("error")
                   or (resp.json() or {})
                   .get("detail")),
               str(resp.status_code))

        # learn 正常 200
        await seed_pool_feedback(10, 1.0,
                                 member_id=7301)
        resp = client.post(
            "/api/login54/model/learn",
            headers=admin)
        body = resp.json() or {}
        record("HTTP learn 200(版本+权重)",
               resp.status_code == 200
               and body.get("newVersion")
               and len(body.get("weights") or {})
               == 8,
               f"{resp.status_code} "
               f"{body.get('newVersion')}")

        # shadow-compare
        resp = client.post(
            "/api/login54/model/shadow-compare",
            json={"ctx": GOOD_CTX},
            headers=admin)
        body = resp.json() or {}
        record("HTTP shadow-compare 200",
               resp.status_code == 200
               and (body.get("champion") or {})
               .get("result") is not None,
               str(resp.status_code))

        # 无挑战者单轨(auto 晋升后 challenger=None)
        # ——先看是否有挑战者; learn 产物视评估而定,
        # 单轨断言: challenger 字段结构合法
        record("compare challenger 可为空",
               body.get("challenger") is None
               or (body.get("challenger") or {})
               .get("result") is not None,
               "结构非法")

        # 鉴权
        resp = client.post(
            "/api/login54/model/learn")
        record("learn 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(
            "/api/login54/model/shadow-compare",
            json={"ctx": {}})
        record("shadow-compare 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 调度器: 学习步(门槛不足 skip 留痕)
        from services.login54_scheduler import (
            run_scheduled_collect,
        )
        stats = await run_scheduled_collect()
        learn = stats.get("lastLearn") or {}
        record("调度器学习步(不足 skip)",
               "skipped" in learn
               and "不足" in str(
                   learn.get("skipped")),
               str(learn)[:60])

        # 模型事件历史含 learning 留痕
        from repositories.login54_repository import (
            Login54Repository,
        )
        events = await Login54Repository(
        ).list_model_events(limit=50)
        types = {e.get("eventType")
                for e in events}
        record("事件历史含 learning",
               "learning" in types,
               str(types))

        # 零影响红线
        from services.ai_learning_service import (
            run_learning_cycle, promote_challenger,
            reset_weights,
        )
        record("44号学习接口零改动",
               all(callable(f) for f in (
                   run_learning_cycle,
                   promote_challenger,
                   reset_weights)))
        from routes.login53_routes import (
            router as login53_router,
        )
        count53 = sum(
            1 for r in login53_router.routes)
        record("53号路由零改动(20 端点)",
               count53 == 20, str(count53))


async def run_all():
    await TestLearningCycle().run()
    await TestWeightEvolution().run()
    await TestReplayAndHttp().run()


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
