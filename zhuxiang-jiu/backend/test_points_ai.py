"""全站批次二·03号积分主通道 AI 接线 专项测试

运行方式:
    python test_points_ai.py

覆盖(《全站AI智能混合架构升级总计划》批次二):
    - points_risk 决策门接线三条主通道
      (返分 earn/抵现 deduct/退款 refund)
      ——服务层一处接线覆盖
      routes+order_service+member_service
    - 三通道决策快照(observe 模式行为兼容)
    - 三通道终态回流(发放/抵扣/扣回
      →自动反馈语义配对)
    - enforce 模式(中风险 reviewRequired
      标记+高风险单元拦截)
    - 夜间时段富化+当日聚合口径
"""

import asyncio
import os
import sys
from datetime import datetime

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["AI_ENFORCE_MODE"] = "observe"

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


async def seed_points(user_id, total=10000):
    from repositories.points_repository import (
        PointsRepository,
    )
    repo = PointsRepository()
    await repo.get_or_create_account(user_id)
    from repositories.store import _mock_store
    acct = _mock_store["points_accounts"][user_id]
    acct["totalPoints"] = total
    acct["totalEarned"] = total
    return acct


class TestEnrich:
    """01 输入富化"""

    async def run(self):
        print("[01 输入富化]")
        reset_all()
        from services.ai_enforcement_points import (
            enrich_points_channel,
        )

        # 空流水: earn 通道计入本次
        ctx = await enrich_points_channel(
            4001, "earn", 150)
        record("earn 本次计入当日",
               ctx.get("todayEarned") == 150.0,
               str(ctx.get("todayEarned")))

        # deduct 通道计入当日兑换次数
        ctx2 = await enrich_points_channel(
            4001, "deduct", 500)
        record("deduct 计入兑换次数",
               ctx2.get("dailyRedeemCount") == 1,
               str(ctx2.get("dailyRedeemCount")))

        # 夜间时段富化(确定性: 与当前小时一致)
        hour = datetime.now().hour
        expect_night = 1.0 if hour in range(0, 6) \
            else 0.0
        record("夜间时段口径一致",
               ctx.get("nightActionRatio")
               == expect_night,
               str((ctx.get("nightActionRatio"),
                    expect_night)))

        # 流水聚合: earn 后再查当日累计
        from repositories.points_repository import (
            PointsRepository,
        )
        repo = PointsRepository()
        await repo.add_log({
            "userId": 4002, "type": "earn",
            "source": "order", "points": 300,
            "balance": 300,
            "refId": "O-ENRICH",
            "refDesc": "debug",
            "createdAt":
                datetime.now().isoformat(),
            "expireAt": None, "status": "ok"})
        ctx3 = await enrich_points_channel(
            4002, "earn", 50)
        record("当日正流水聚合",
               ctx3.get("todayEarned") == 350.0,
               str(ctx3.get("todayEarned")))


class TestChannels:
    """02 三主通道接线(observe 行为兼容)"""

    async def run(self):
        print("[02 三主通道]")
        reset_all()
        from services.points_service import (
            PointsService,
        )
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        svc = PointsService()
        repo = AiLearningRepository()
        await seed_points(4101)

        # ① 返分通道
        r = await svc.earn_order_points(
            4101, "O-AI-E1", 100.0, 1)
        record("返分通道兼容",
               r.get("earnedPoints") == 150,
               str(r.get("earnedPoints")))
        # 返分即回流(快照配对即消费——键空)
        snap = await repo.get_decision_snapshot(
            "points_risk",
            "points:earn:O-AI-E1")
        record("返分快照闭环",
               snap is None,
               "快照未被消费")

        # ② 抵现通道
        r = await svc.deduct_points(
            4101, "O-AI-D1", 1000.0, 100)
        record("抵现通道兼容",
               r.get("deductPoints") == 100,
               str(r.get("deductPoints")))
        snap = await repo.get_decision_snapshot(
            "points_risk",
            "points:deduct:O-AI-D1")
        record("抵现快照闭环",
               snap is None,
               "快照未被消费")

        # ③ 退款通道
        r = await svc.refund_points(
            4101, "O-AI-E1", 100)
        record("退款通道兼容",
               r.get("actualRefund") == 100,
               str(r.get("actualRefund")))
        snap = await repo.get_decision_snapshot(
            "points_risk",
            "points:refund:O-AI-E1")
        record("退款快照闭环",
               snap is None,
               "快照未被消费")

        # 单元级: 决策门快照留痕(无终态
        # 消费路径——直接调门验证评分入池;
        # 用净账户用户保证确定性低风险)
        from services.ai_enforcement_points import (
            enrich_points_channel,
            enforce_points_action,
        )
        await seed_points(4105)
        ctx = await enrich_points_channel(
            4105, "earn", 50)
        gate = await enforce_points_action(
            4105, "earn", ctx, "O-GATE-U1")
        snap = await repo.get_decision_snapshot(
            "points_risk",
            "points:earn:O-GATE-U1")
        record("决策门快照留痕(单元)",
               snap is not None
               and snap.get("decision") == "low",
               str(snap)[:50])

        # 硬规则保留: 抵现超 30% 上限拒绝
        try:
            await svc.deduct_points(
                4101, "O-AI-D2", 100.0, 5000)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("硬规则30%上限保留", ok, err)


class TestFeedback:
    """03 三通道终态回流"""

    async def run(self):
        print("[03 终态回流]")
        reset_all()
        from services.points_service import (
            PointsService,
        )
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        svc = PointsService()
        repo = AiLearningRepository()
        await seed_points(4201)

        await svc.earn_order_points(
            4201, "O-FB-E1", 100.0, 1)
        await svc.deduct_points(
            4201, "O-FB-D1", 1000.0, 100)
        await svc.refund_points(
            4201, "O-FB-E1", 100)

        fbs = await repo.list_feedback(
            "points_risk")
        actions = {f.get("actualAction")
                   for f in fbs}
        record("三通道各回流一条",
               {"earn_settled",
                "deduct_settled",
                "refund_settled"} <= actions,
               str(actions))
        auto = [f for f in fbs
                if f.get("source") == "auto"]
        record("回流来源 auto",
               len(auto) >= 3,
               str(len(auto)))

        # 快照配对即消费(三键全空)
        for key in ("points:earn:O-FB-E1",
                    "points:deduct:O-FB-D1",
                    "points:refund:O-FB-E1"):
            snap = await repo \
                .get_decision_snapshot(
                    "points_risk", key)
            if snap is not None:
                record("快照配对即消费",
                       False, key)
                return
        record("快照配对即消费", True, "")


class TestEnforceMode:
    """04 enforce 模式"""

    async def run(self):
        print("[04 enforce 模式]")
        reset_all()
        os.environ["AI_ENFORCE_MODE"] = "enforce"
        os.environ["AI_ENFORCE_SCOPES"] = \
            "points_risk"
        try:
            # 冷启动喂料
            from repositories.ai_learning_repository import (
                AiLearningRepository,
            )
            repo = AiLearningRepository()
            for _ in range(55):
                await repo.add_feedback({
                    "scorerId": "points_risk",
                    "factors": [{
                        "name": "earn_burst",
                        "score": 10.0,
                        "weight": 0.25}],
                    "correct": True,
                    "createdAt":
                        "2026-01-01T00:00:00",
                })

            # 单元级: 高风险 ctx 拦截
            from services.ai_enforcement_points import (
                enrich_points_channel,
                enforce_points_action,
            )
            ctx = dict(
                await enrich_points_channel(
                    4301, "earn", 100))
            ctx["todayEarned"] = 500.0
            ctx["dailyRedeemCount"] = 8
            ctx["singleChannelRatio"] = 0.9
            ctx["sameDeviceAccounts"] = 3
            ctx["violationCount"] = 4
            ctx["nightActionRatio"] = 0.5
            blocked = False
            try:
                await enforce_points_action(
                    4301, "earn", ctx, "O-EF1")
            except ValueError as exc:
                blocked = "风控拦截" in str(exc)
            record("enforce 高风险拦截(单元)",
                   blocked, "未拦截")

            # 服务级: 正常画像不受影响
            from services.points_service import (
                PointsService,
            )
            await seed_points(4302)
            passed = False
            try:
                r = await PointsService() \
                    .earn_order_points(
                        4302, "O-EF2", 100.0, 1)
                passed = (r.get("earnedPoints")
                          == 150)
            except ValueError:
                passed = False
            record("enforce 低风险放行(服务)",
                   passed, "被误拦")

            # 中风险画像: reviewRequired
            # 标记(不阻断业务)
            await seed_points(4303)
            from repositories.points_repository import (
                PointsRepository,
            )
            prepo = PointsRepository()
            for i in range(6):
                await prepo.add_log({
                    "userId": 4303,
                    "type": "spend",
                    "source": "deduct",
                    "points": -100,
                    "balance": 0,
                    "refId": f"O-SEED-{i}",
                    "refDesc": "debug",
                    "createdAt":
                        datetime.now().isoformat(),
                    "expireAt": None,
                    "status": "consumed"})
            ctx_m = dict(
                await enrich_points_channel(
                    4303, "earn", 500))
            gate = await enforce_points_action(
                4303, "earn", ctx_m, "O-MID1")
            record("中风险 reviewRequired",
                   gate.get("reviewRequired") is True,
                   str(gate.get("reviewRequired")))
        finally:
            os.environ["AI_ENFORCE_MODE"] = \
                "observe"
            os.environ.pop(
                "AI_ENFORCE_SCOPES", None)


class TestConstitution:
    """05 宪法铁律"""

    async def run(self):
        print("[05 宪法铁律]")
        reset_all()

        # 决策门默认 observe
        os.environ.pop("AI_ENFORCE_MODE", None)
        from services.ai_enforcement import (
            enforcement_mode,
        )
        record("决策门默认 observe",
               enforcement_mode("points_risk")
               == "observe",
               enforcement_mode("points_risk"))

        # 评分器确定性(无 LLM)
        import inspect
        from services.ai_scoring_ext_service import (
            PointsRiskScorer,
        )
        src = inspect.getsource(
            PointsRiskScorer.score)
        record("判定链无 LLM",
               "llm" not in src.lower(), "")

        # 服务层单点接线(三通道均经
        # PointsService——一处接线覆盖
        # routes/order/member 全调用方)
        import services.points_service as psm
        src2 = inspect.getsource(psm)
        record("三通道门挂载齐备",
               src2.count("enforce_points_action")
               == 6
               and src2.count("on_points_settled")
               == 6,
               str((src2.count(
                    "enforce_points_action"),
                   src2.count(
                       "on_points_settled"))))


async def main():
    print("=" * 62)
    print("全站批次二·03号积分主通道 AI 接线"
          " 专项测试")
    print("=" * 62)
    for cls in (TestEnrich, TestChannels,
                TestFeedback, TestEnforceMode,
                TestConstitution):
        await cls().run()
    print(f"\n{'=' * 62}")
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 62)
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
