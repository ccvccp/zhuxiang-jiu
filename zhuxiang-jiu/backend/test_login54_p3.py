"""54号·小竹AI智能登录引擎大模型 P3 专项测试
(自主升级+回滚)

运行方式:
    python test_login54_p3.py

覆盖(54号计划 §六 P3):
    - 手动晋升: promote(挑战者→冠军)+
      promoted 事件留痕; 无挑战者 409
    - 版本回滚: 指定版本/缺省最新退役——
      旧冠军入历史+rollback 事件留痕;
      无历史 409/版本不存在 409
    - 晋升-回滚闭环(QC): 学习→晋升→回滚→
      版本历史可溯(版本链/权重还原)
    - 滑动窗口回归检测: 无晋升基线 skip/
      反馈不足 skip/正常不回退/
      回退超阈值→自动回滚+冻结+告警事件
    - 调度器: 回归检测步留痕
    - HTTP 层: promote/rollback 端点+鉴权
    - 零影响: 44号/53号零改动红线
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
                             strong: bool = True):
    """直写 44号池 login_orchestration 反馈"""
    from core.helpers import ts
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    from services.login54_scorer import Login54Scorer

    ctx = dict(GOOD_CTX)
    if not strong:
        ctx.update({
            "channelSuccess": 0.1,
            "baselineMatch": 0.1,
            "channelFailCount": 4,
            "portalState": "high_risk",
        })
    result = await Login54Scorer().score(ctx)
    repo = AiLearningRepository()
    for i in range(count):
        await repo.add_feedback({
            "scorerId": "login_orchestration",
            "weightVersion": "v1",
            "scoreAtDecision":
                result.get("trustScore"),
            "actualAction":
                "silent" if strong else "enhanced",
            "expectedAction":
                "silent" if strong else "enhanced",
            "correct": True,
            "factors": result.get("factors"),
            "reward": reward,
            "note": f"p3-seed:{i}",
            "source": "login54_pipeline",
            "status": "pending",
            "createdAt": ts(),
        })


async def learn_round(svc):
    """一轮: 种子反馈+学习"""
    await seed_pool_feedback(10, 1.0)
    return await svc.run_learning()


class TestPromoteRollback:
    """01 手动晋升+版本回滚闭环"""

    async def run(self):
        print("[01 晋升-回滚闭环]")
        reset_all()
        from services.login54_learn_service import (
            Login54LearnService,
        )
        svc = Login54LearnService()

        # 无挑战者拒绝(v1 默认在役)
        try:
            await svc.promote()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "挑战者" in str(e), str(e)[:40]
        record("无挑战者晋升拒绝", ok, err)

        # 无历史回滚拒绝(v1 默认在役)
        try:
            await svc.rollback()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "无可回滚" in str(e), str(e)[:40]
        record("无历史回滚拒绝", ok, err)

        # 学习产挑战者 → 手动晋升
        r1 = await learn_round(svc)
        record("学习轮次产挑战者",
               r1.get("newStatus") == "challenger",
               str(r1.get("newStatus")))

        promote = await svc.promote()
        record("手动晋升成功",
               promote.get("success") is True
               and promote.get("promotedVersion"),
               str((promote.get("previousVersion"),
                    promote.get("promotedVersion"))))

        # promoted 事件留痕(manual 通道)
        from repositories.login54_repository import (
            Login54Repository,
        )
        events = await Login54Repository(
        ).list_model_events(limit=10)
        manual_promoted = [
            e for e in events
            if e.get("eventType") == "promoted"
            and (e.get("detail") or {})
            .get("channel") == "manual"]
        record("promoted 事件留痕(manual)",
               len(manual_promoted) >= 1,
               str(len(manual_promoted)))

        # 晋升后权重生效(champion=挑战者版本)
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(
            "login_orchestration")
        record("晋升后冠军即原挑战者",
               (view.get("champion") or {}).get("version")
               == promote.get("promotedVersion")
               and view.get("challenger") is None,
               str((view.get("champion") or {})
                   .get("version")))

        # 回滚(缺省→最新退役=v1)
        rb = await svc.rollback(reason="P3 测试回滚")
        record("回滚成功(默认最新退役)",
               rb.get("success") is True
               and rb.get("targetVersion") == "v1",
               str((rb.get("fromVersion"),
                    rb.get("targetVersion"))))

        # 权重还原断言(回滚后 champion 权重=v1 默认)
        view = await get_weights_view(
            "login_orchestration")
        champion_w = (view.get("champion") or {}) \
            .get("weights") or {}
        from services.ai_learning_service import (
            default_weights,
        )
        defaults = default_weights(
            "login_orchestration")
        same = all(
            abs(champion_w.get(k, 0) - defaults[k])
            < 1e-9 for k in defaults)
        record("回滚权重还原(v1 默认)",
               same,
               str(champion_w))

        # 版本历史可溯(rollback 链)
        from services.ai_learning_service import (
            get_history,
        )
        hist = await get_history(
            "login_orchestration", limit=50)
        versions = [h.get("version")
                    for h in hist.get("history") or []]
        record("版本历史可溯(≥2 退役版本)",
               hist.get("historyCount") >= 2
               and "v1" in versions,
               str(versions[:6]))

        # rollback 事件留痕
        events = await Login54Repository(
        ).list_model_events(limit=10)
        rollback_events = [
            e for e in events
            if e.get("eventType") == "rollback"]
        record("rollback 事件留痕",
               len(rollback_events) >= 1
               and (rollback_events[0]
                    .get("detail") or {})
               .get("targetVersion") == "v1",
               str(len(rollback_events)))

        # 指定版本回滚(回滚到已退役的挑战者版本)
        target_ver = promote.get("promotedVersion")
        rb2 = await svc.rollback(
            version_id=target_ver,
            reason="指定版本回滚测试")
        record("指定版本回滚成功",
               rb2.get("targetVersion") == target_ver,
               str(rb2.get("targetVersion")))

        # 版本不存在拒绝
        try:
            await svc.rollback(version_id="v999")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不存在" in str(e), str(e)[:40]
        record("版本不存在回滚拒绝", ok, err)


class TestRegressionDetection:
    """02 滑动窗口回归检测→自动回滚+冻结"""

    async def run(self):
        print("[02 回归检测]")
        reset_all()
        from services.login54_learn_service import (
            Login54LearnService,
        )
        svc = Login54LearnService()

        # 无晋升基线 → skip
        r = await svc.check_regression()
        record("无基线 skip",
               r.get("applicable") is False
               and "无晋升基线" in r.get("reason", ""),
               str(r.get("reason")))

        # 反馈不足 skip(手动构造晋升——池内仅 3 条)
        await seed_pool_feedback(3, 1.0)
        from repositories.ai_learning_repository \
            import AiLearningRepository
        import services.ai_learning_service as als
        repo = AiLearningRepository()
        profile = await repo.get_profile(
            "login_orchestration") or {}
        champion = als._build_version_record(
            "v1", als.default_weights(
                "login_orchestration"), "default", "-")
        challenger = als._build_version_record(
            "v2", als.default_weights(
                "login_orchestration"), "learning", "v1")
        profile.update({"champion": champion,
                        "challenger": challenger})
        await repo.save_profile(
            "login_orchestration", profile)
        await svc.promote()
        r = await svc.check_regression()
        record("反馈不足 skip",
               r.get("applicable") is False
               and "不足" in r.get("reason", ""),
               str(r.get("reason")))

        # 学习+手动晋升(建立基线)
        await learn_round(svc)
        await svc.promote()

        # 持续正反馈(高 baseline 保持)→ 不回退
        await seed_pool_feedback(10, 1.0)
        r = await svc.check_regression()
        record("正常窗口不回退",
               r.get("applicable") is True
               and r.get("regressed") is False,
               str((r.get("baseline"),
                    r.get("current"),
                    r.get("drop"))))

        # 回退场景: 换坏上下文负反馈(baseline 高,
        # current 拉低)——追加 bad 负反馈到池
        from core.helpers import ts
        from services.login54_scorer import (
            Login54Scorer,
        )
        bad_ctx = dict(GOOD_CTX)
        bad_ctx.update({
            "channelSuccess": 0.1,
            "baselineMatch": 0.1, "channelFailCount": 4,
            "portalState": "high_risk"})
        bad_result = await Login54Scorer().score(
            bad_ctx)
        repo = AiLearningRepository()
        for i in range(10):
            await repo.add_feedback({
                "scorerId": "login_orchestration",
                "weightVersion": "v1",
                "scoreAtDecision":
                    bad_result.get("trustScore"),
                "actualAction": "enhanced",
                "expectedAction": "silent",
                "correct": False,
                "factors": bad_result.get("factors"),
                "reward": -1.0,
                "note": f"p3-bad:{i}",
                "source": "login54_pipeline",
                "status": "pending",
                "createdAt": ts(),
            })

        r = await svc.check_regression()
        record("回退检测命中",
               r.get("regressed") is True
               and r.get("drop", 0)
               > r.get("threshold", 99),
               str((r.get("baseline"),
                    r.get("current"),
                    r.get("drop"),
                    r.get("threshold"))))

        # 自动回滚动作(rollback+freeze 联动)
        record("自动回滚执行",
               r.get("action") == "auto_rollback"
               and (r.get("rollback") or {})
               .get("success") is True,
               str(r.get("action")))

        # 冻结生效(46号注册中心 frozen)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        frozen = await AiGovernanceService(
        ).is_frozen("login_orchestration")
        record("回归冻结生效(46号)",
               frozen is True,
               str(frozen))

        # 冻结后学习拦截
        try:
            await svc.run_learning()
            ok, err = False, "未拦截"
        except ValueError as e:
            ok, err = "冻结" in str(e), str(e)[:40]
        record("冻结后学习拦截", ok, err)

        # regression_rollback 事件留痕(auto 通道)
        from repositories.login54_repository import (
            Login54Repository,
        )
        events = await Login54Repository(
        ).list_model_events(limit=20)
        reg_events = [
            e for e in events
            if e.get("eventType")
            == "regression_rollback"]
        record("regression_rollback 事件留痕",
               len(reg_events) >= 1
               and (reg_events[0].get("detail")
                    or {}).get("channel") == "auto"
               and (reg_events[0].get("detail")
                    or {}).get("frozen") is True,
               str(len(reg_events)))

        # 还原: 解冻(46号——先同步注册中心)
        gov = AiGovernanceService()
        await gov.sync_registry()
        change = await gov.submit_change(
            "login_orchestration", "unfreeze",
            {}, "P3 测试还原")
        await gov.review_change(
            change["changeId"], True, "admin")


class TestSchedulerAndHttp:
    """03 调度器+HTTP+零影响"""

    async def run(self):
        print("[03 调度器+HTTP]")
        reset_all()
        from services.login54_learn_service import (
            Login54LearnService,
        )
        svc = Login54LearnService()

        # HTTP 层
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # promote 无挑战者 409
        resp = client.post(
            "/api/login54/model/promote",
            headers=admin)
        record("HTTP promote 无挑战者 409",
               resp.status_code == 409,
               str(resp.status_code))

        # rollback 无历史 409
        resp = client.post(
            "/api/login54/model/rollback",
            headers=admin)
        record("HTTP rollback 无历史 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 学习→晋升→回滚全链 HTTP
        await learn_round(svc)
        resp = client.post(
            "/api/login54/model/promote",
            headers=admin)
        body = resp.json() or {}
        record("HTTP promote 200",
               resp.status_code == 200
               and body.get("promotedVersion"),
               f"{resp.status_code} "
               f"{body.get('promotedVersion')}")

        resp = client.post(
            "/api/login54/model/rollback",
            json={"reason": "HTTP 回滚测试"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP rollback 200(target=v1)",
               resp.status_code == 200
               and body.get("targetVersion") == "v1",
               str(body.get("targetVersion")))

        # 指定版本 HTTP
        resp = client.post(
            "/api/login54/model/rollback",
            json={"versionId": "v1",
                  "reason": "HTTP 指定版本"},
            headers=admin)
        record("HTTP rollback 指定版本 200",
               resp.status_code == 200,
               str(resp.status_code))

        # 鉴权
        resp = client.post(
            "/api/login54/model/promote")
        record("promote 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(
            "/api/login54/model/rollback")
        record("rollback 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 调度器: 回归检测步留痕(无基线 skip)
        from services.login54_scheduler import (
            run_scheduled_collect,
        )
        stats = await run_scheduled_collect()
        regression = stats.get("lastRegression") or {}
        record("调度器回归检测步留痕",
               regression.get("regressed") is False,
               str(regression)[:60])

        # 零影响红线
        from services.ai_learning_service import (
            run_learning_cycle,
            promote_challenger, reset_weights,
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
        from routes.login54_routes import (
            router as login54_router,
        )
        count54 = sum(
            1 for r in login54_router.routes)
        record("54号路由 ≥10 端点(P3 基线)",
               count54 >= 10, str(count54))


async def run_all():
    await TestPromoteRollback().run()
    await TestRegressionDetection().run()
    await TestSchedulerAndHttp().run()


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
