"""55号·二维码AI智能管理模块 P3 专项测试
(自主学习进化+升级回滚)

运行方式:
    python test_qr55_p3.py

覆盖(55号计划 §六 P3):
    - 学习门槛: 反馈不足 ValueError/就绪态翻转
    - 学习轮次: Hedge→challenger+护栏 [0.5,2.0] 倍
      +权重归一化(和=1.0)+单调收敛
    - 影子对比: challenger 双轨试算+档位差异
    - 手动晋升: promotedVersion+晋升基线留痕
    - 版本回滚: 权重还原+历史入册+事件留痕
    - 晋升-回滚闭环(QC-2)
    - 滑动窗口回归检测: 不适用/正常/回退自动回滚
      +46号冻结+regression_rollback 留痕
    - 冻结守卫: 冻结中 learn ValueError
    - HTTP 层: learn/promote/rollback+鉴权+14 端点
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

PASS = 0
FAIL = 0
RESULTS = []

# 八因子上下文(高信任——direct 策略轨)
GOOD_CTX = {
    "intentConfidence": 0.9,
    "serviceMatch": "resolved",
    "paramComplete": 1.0,
    "budgetRemaining": 0.9,
    "memberTrustLevel": "L3",
    "freshRatio": 1.0,
    "accessibility": False,
    "riskFlagged": False,
}

# 弱上下文(低信任——confirm/clarify 策略轨)
WEAK_CTX = {
    "intentConfidence": 0.1,
    "serviceMatch": "clarify",
    "paramComplete": 0.2,
    "budgetRemaining": 0.05,
    "memberTrustLevel": "L0",
    "freshRatio": 0.1,
    "accessibility": False,
    "riskFlagged": True,
}

BASE_WEIGHTS = {
    "intent_confidence": 0.15,
    "service_match": 0.15,
    "template_fit": 0.10,
    "budget_sufficiency": 0.15,
    "member_trust": 0.15,
    "expiry_freshness": 0.10,
    "accessibility_need": 0.10,
    "risk_posture": 0.10,
}


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


async def seed_pool_feedback(count: int, reward: float,
                             strong: bool = True):
    """直写 44号池 qr_orchestration 反馈"""
    from core.helpers import ts
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    from services.qr55_scorer import Qr55Scorer

    ctx = dict(GOOD_CTX if strong else WEAK_CTX)
    result = await Qr55Scorer().score(ctx)
    repo = AiLearningRepository()
    for i in range(count):
        await repo.add_feedback({
            "scorerId": "qr_orchestration",
            "weightVersion": "v1",
            "scoreAtDecision":
                result.get("trustScore"),
            "actualAction":
                "direct" if strong else "clarify",
            "expectedAction":
                "direct" if strong else "clarify",
            "correct": True,
            "factors": result.get("factors"),
            "reward": reward,
            "note": f"p3-seed:{i}",
            "source": "qr55_pipeline",
            "status": "pending",
            "createdAt": ts(),
        })


class TestLearning:
    """01 学习门槛+轮次+护栏"""

    async def run(self):
        print("[01 学习轮次]")
        reset_all()
        from services.qr55_learn_service import (
            Qr55LearnService,
        )
        svc = Qr55LearnService()

        # 学习门槛: 无反馈 → ValueError
        try:
            await svc.run_learning()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = True, str(e)[:40]
        record("反馈不足 ValueError(门槛)", ok, err)

        # 就绪态: pending=0
        r = await svc.learning_readiness()
        record("就绪态(pending=0 不就绪)",
               r.get("pendingFeedback") == 0
               and r.get("ready") is False,
               str(r))

        # 种 12 条强反馈 → 就绪
        await seed_pool_feedback(12, 1.0, strong=True)
        r2 = await svc.learning_readiness()
        record("就绪态(≥10 就绪翻转)",
               r2.get("pendingFeedback") == 12
               and r2.get("ready") is True,
               str(r2.get("ready")))

        # 学习轮次: Hedge → challenger
        learn = await svc.run_learning()
        record("Hedge 学习(learnedFrom≥10)",
               (learn.get("learnedFrom") or 0) >= 10
               and bool(learn.get("newVersion")),
               str((learn.get("learnedFrom"),
                    learn.get("newVersion"))))

        # 权重归一化+护栏
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(
            "qr_orchestration")
        challenger = ((view.get("challenger")
                       or {}).get("weights")) or {}
        if challenger:
            total = round(sum(challenger.values()), 6)
            record("权重归一化(和=1.0)",
                   abs(total - 1.0) < 1e-6, str(total))
            guard_ok = all(
                BASE_WEIGHTS[k] / 2.0
                <= challenger.get(k, 0)
                <= BASE_WEIGHTS[k] * 2.0
                for k in BASE_WEIGHTS)
            record("护栏 [0.5,2.0] 倍(QC-1)",
                   guard_ok, str(challenger))
        else:
            record("权重归一化(和=1.0)", False,
                   "无 challenger(学习未产出)")
            record("护栏 [0.5,2.0] 倍(QC-1)", False,
                   "无 challenger")

        # 单调收敛(QC-1): 多轮学习 delta 有界
        await seed_pool_feedback(12, 1.0, strong=True)
        learn2 = await svc.run_learning()
        delta = learn2.get("weightDelta") or {}
        bounded = all(
            abs(float(v)) <= 0.1
            for v in delta.values()) if delta else True
        record("权重演进单调收敛(delta 有界)",
               bounded, str(delta))

        # 模型事件留痕(learning)
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        events = await Qr55Repository(
        ).list_model_events(limit=100)
        types = {e.get("eventType") for e in events}
        record("learning 事件留痕(版本可溯)",
               "learning" in types, str(types))


class TestShadowCompare:
    """02 影子对比"""

    async def run(self):
        print("[02 影子对比]")
        reset_all()
        from services.qr55_learn_service import (
            Qr55LearnService,
        )
        svc = Qr55LearnService()

        # 无 challenger: 仅 champion 轨
        r = await svc.shadow_compare(dict(GOOD_CTX))
        record("无挑战者(仅冠军轨)",
               r.get("challenger") is None
               and r.get("comparison") is None
               and bool(r.get("champion")),
               str(r.get("challenger")))

        # 种反馈学习 → challenger 生成
        await seed_pool_feedback(12, 1.0, strong=True)
        await svc.run_learning()
        r2 = await svc.shadow_compare(dict(GOOD_CTX))
        comparison = r2.get("comparison") or {}
        record("双轨试算(champion+challenger)",
               bool(r2.get("challenger"))
               and bool(comparison.get(
                   "challengerStrategy")),
               str(comparison))
        record("档位差异语义(三态合法)",
               comparison.get("verdict") in (
                   "challenger更宽(信任倾向)",
                   "challenger更严(保守倾向)",
                   "档位一致"),
               str(comparison.get("verdict")))


class TestPromoteRollback:
    """03 手动晋升+版本回滚(闭环 QC-2)"""

    async def run(self):
        print("[03 晋升+回滚]")
        reset_all()
        from services.qr55_learn_service import (
            Qr55LearnService,
        )
        svc = Qr55LearnService()

        # 无挑战者晋升 → ValueError
        try:
            await svc.promote()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = True, str(e)[:40]
        record("无挑战者晋升 ValueError", ok, err)

        # 无历史回滚 → ValueError
        try:
            await svc.rollback()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = True, str(e)[:40]
        record("无历史回滚 ValueError", ok, err)

        # 学习 → 晋升
        await seed_pool_feedback(12, 1.0, strong=True)
        await svc.run_learning()
        promo = await svc.promote()
        record("手动晋升(promotedVersion)",
               bool(promo.get("promotedVersion")),
               str(promo.get("promotedVersion")))

        # 晋升事件留痕+基线
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        events = await Qr55Repository(
        ).list_model_events(limit=100)
        promoted = [e for e in events
                    if e.get("eventType")
                    == "promoted"]
        record("promoted 事件留痕(基线参照)",
               len(promoted) == 1
               and ((promoted[0].get("detail")
                     or {}).get("challengerMetrics")
                    is not None),
               str(len(promoted)))

        # 版本回滚
        rb = await svc.rollback(reason="p3-test")
        weights = rb.get("weights") or {}
        total = round(sum(weights.values()), 6)
        record("回滚(权重还原+和=1.0)",
               abs(total - 1.0) < 1e-6
               and bool(rb.get("targetVersion")),
               str((rb.get("targetVersion"), total)))

        # rollback 事件留痕
        events = await Qr55Repository(
        ).list_model_events(limit=100)
        rolled = [e for e in events
                  if e.get("eventType")
                  == "rollback"]
        record("rollback 事件留痕(版本可溯)",
               len(rolled) == 1
               and (rolled[0].get("detail")
                    or {}).get("channel")
               == "manual",
               str(len(rolled)))

        # 晋升-回滚闭环(QC-2): 历史可溯
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(
            "qr_orchestration")
        champion_version = (view.get("champion")
                            or {}).get("version")
        record("晋升-回滚闭环(冠军版本可溯)",
               bool(champion_version)
               and bool(rb.get("newVersion"))
               and rb.get("newVersion")
               != promo.get("promotedVersion"),
               str((champion_version,
                    rb.get("newVersion"))))


class TestRegression:
    """04 滑动窗口回归检测"""

    async def run(self):
        print("[04 回归检测]")
        reset_all()
        from services.qr55_learn_service import (
            Qr55LearnService,
        )
        svc = Qr55LearnService()

        # 不适用: 无晋升基线
        r = await svc.check_regression()
        record("不适用(无晋升基线)",
               r.get("applicable") is False,
               str(r.get("reason")))

        # 学习 → 晋升(强反馈基线)
        await seed_pool_feedback(12, 1.0, strong=True)
        await svc.run_learning()
        await svc.promote()

        # 反馈不足 → 不适用(清池后仅种 2 条——
        # 44号 list_feedback 不分状态, 需清旧反馈)
        from repositories.backend import (
            get_in_memory_store,
        )
        get_in_memory_store().setdefault(
            "ai_learning_feedback", {})[
            "qr_orchestration"] = []
        await seed_pool_feedback(2, 1.0, strong=True)
        r2 = await svc.check_regression()
        record("不适用(晋升后反馈不足)",
               r2.get("applicable") is False
               and "反馈不足" in str(
                   r2.get("reason")),
               str(r2.get("reason")))

        # 正常: 高质量反馈 → 不回退
        await seed_pool_feedback(8, 1.0, strong=True)
        r3 = await svc.check_regression()
        record("正常(指标未回退)",
               r3.get("applicable") is True
               and r3.get("regressed") is False,
               str((r3.get("baseline"),
                    r3.get("current"))))

        # 回退: 负 reward 洪流 → 自动回滚+冻结
        await seed_pool_feedback(10, -1.0,
                                 strong=False)
        r4 = await svc.check_regression()
        record("回退命中(regressed)",
               r4.get("regressed") is True
               and (r4.get("drop") or 0)
               > (r4.get("threshold") or 1),
               str((r4.get("drop"),
                    r4.get("threshold"))))
        record("自动回滚(action=auto_rollback)",
               r4.get("action") == "auto_rollback"
               and bool((r4.get("rollback") or {})
                        .get("newVersion")),
               str(r4.get("action")))
        record("46号冻结联动",
               (r4.get("freeze") or {})
               .get("frozen") is True,
               str(r4.get("freeze")))

        # regression_rollback 事件留痕
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        events = await Qr55Repository(
        ).list_model_events(limit=100)
        rr = [e for e in events
              if e.get("eventType")
              == "regression_rollback"]
        record("regression_rollback 事件留痕",
               len(rr) == 1
               and (rr[0].get("detail")
                    or {}).get("channel") == "auto",
               str(len(rr)))

        # 冻结守卫: 冻结中 learn → ValueError
        try:
            await svc.run_learning()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "冻结" in str(e), str(e)[:40]
        record("冻结中学习 ValueError(守卫)",
               ok, err)


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # learn: 反馈不足 409
        resp = client.post("/api/qr55/model/learn",
                           headers=admin)
        record("HTTP learn 门槛不足 409",
               resp.status_code == 409,
               str(resp.status_code))

        # promote: 无挑战者 409
        resp = client.post("/api/qr55/model/promote",
                           headers=admin)
        record("HTTP promote 无挑战者 409",
               resp.status_code == 409,
               str(resp.status_code))

        # rollback: 无历史 409
        resp = client.post(
            "/api/qr55/model/rollback",
            headers=admin)
        record("HTTP rollback 无历史 409",
               resp.status_code == 409,
               str(resp.status_code))

        # model/status: 就绪态并入
        resp = client.get("/api/qr55/model/status",
                          headers=admin)
        body = resp.json() or {}
        readiness = ((body.get("status") or {})
                     .get("readiness")) or {}
        record("HTTP model/status 就绪态并入",
               resp.status_code == 200
               and "ready" in readiness
               and "pendingFeedback" in readiness,
               str(readiness))

        # 学习全链 HTTP: 种反馈 → learn 200 →
        # promote 200 → rollback 200
        await seed_pool_feedback(12, 1.0,
                                 strong=True)
        resp = client.post("/api/qr55/model/learn",
                           headers=admin)
        record("HTTP learn 200",
               resp.status_code == 200,
               str(resp.status_code))
        resp = client.post(
            "/api/qr55/model/promote",
            headers=admin)
        record("HTTP promote 200",
               resp.status_code == 200,
               str(resp.status_code))
        resp = client.post(
            "/api/qr55/model/rollback",
            headers=admin)
        record("HTTP rollback 200",
               resp.status_code == 200,
               str(resp.status_code))

        # 鉴权
        for path in ("/api/qr55/model/learn",
                     "/api/qr55/model/promote",
                     "/api/qr55/model/rollback"):
            resp = client.post(path)
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 14 端点
        from routes.qr55_routes import (
            router as qr_router,
        )
        count = sum(1 for r in qr_router.routes)
        # P4 新增 4 端点(governance/probe/compensate/
        # attribution)→ 14→18(基线语义: ≥14——P3
        # 交付面不因 P4 演进破坏)
        record("55号路由累计 ≥14 端点(P4 扩至 18)",
               count >= 14, str(count))


async def run_all():
    await TestLearning().run()
    await TestShadowCompare().run()
    await TestPromoteRollback().run()
    await TestRegression().run()
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
