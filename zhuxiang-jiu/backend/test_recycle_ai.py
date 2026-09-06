"""全站批次二·13号老酒兑换 AI 升级 专项测试

运行方式:
    python test_recycle_ai.py

覆盖(《全站AI智能混合架构升级总计划》批次二):
    - recycle_valuation 评分器(第42档案
      八因子/三级决策/置信度)
    - 议价决策门(observe 模式行为兼容
      /enforce 模式拦截/medium 转人工标记)
    - 议价回流闭环(accept/reject 终态
      →语义映射配对)
    - 44号注册同步(42 档案 batch26)
"""

import asyncio
import os
import sys
from datetime import date, timedelta

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


def _new_purchase_date(years_ago: int) -> str:
    d = date.today() - timedelta(days=365 * years_ago + 10)
    return d.isoformat()


async def seed_credit_account(user_id, score=800):
    from repositories.store import _mock_store
    from repositories.credit_repository import (
        CreditRepository,
    )
    repo = CreditRepository()
    await repo.get_or_create_score(user_id)
    acct = _mock_store["credit_scores"][user_id]
    acct["bambooScore"] = score
    acct["creditLevel"] = "L4"
    return acct


class TestScorer:
    """01 recycle_valuation 评分器"""

    async def run(self):
        print("[01 评分器]")
        reset_all()
        from services.recycle_scorer import (
            RecycleValuationScorer,
        )
        scorer = RecycleValuationScorer()

        # 空上下文拒绝
        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空上下文拒绝", ok, err)

        # 低风险(优质议价)
        r = await scorer.score({
            "proposedPrice": 980, "aiBasePrice": 1000,
            "negotiationRound": 1, "maxRounds": 3,
            "conditionGrade": "A", "wineAge": 0.5,
            "bottleCount": 1, "bambooScore": 800,
            "historyPrices": [1000],
            "userNegotiationCount": 0,
        })
        record("优质议价低风险",
               r.get("level") == "low"
               and (r.get("score") or 0) < 30,
               str((r.get("score"), r.get("level"))))

        # 高风险(D级+满偏离+低信任+满轮次+高频议价)
        r2 = await scorer.score({
            "proposedPrice": 1100, "aiBasePrice": 1000,
            "negotiationRound": 3, "maxRounds": 3,
            "conditionGrade": "D", "wineAge": 2.9,
            "bottleCount": 3, "bambooScore": 300,
            "historyPrices": [1000, 900, 1000, 900],
            "userNegotiationCount": 6,
        })
        record("高风险议价 high",
               r2.get("level") == "high"
               and (r2.get("score") or 0) >= 60,
               str((r2.get("score"), r2.get("level"))))

        # 八因子齐备
        record("八因子齐备",
               len(r.get("factors") or []) == 8,
               str(len(r.get("factors") or [])))

        # 权重和=1.0
        record("权重和=1.0",
               abs(sum((r.get("weightsUsed")
                        or {}).values())
                   if r.get("weightsUsed")
                   else abs(sum(
                       f["weight"] for f in
                       r.get("factors") or [])) - 1.0)
               < 0.001,
               "")

        # 品质等级映射
        r_a = await scorer.score({
            "proposedPrice": 1000, "aiBasePrice": 1000,
            "conditionGrade": "A"})
        r_d = await scorer.score({
            "proposedPrice": 1000, "aiBasePrice": 1000,
            "conditionGrade": "D"})
        fa = {f["name"]: f["score"]
              for f in r_a["factors"]}
        fd = {f["name"]: f["score"]
              for f in r_d["factors"]}
        record("品质A=0/D=90",
               fa.get("grade_factor") == 0
               and fd.get("grade_factor") == 90,
               str((fa.get("grade_factor"),
                    fd.get("grade_factor"))))

        # 偏离度边界(±10% 折算)
        record("偏离10%→因子100",
               {f["name"]: f["score"]
                for f in r2["factors"]}
               .get("deviation_ratio") == 100.0,
               "")

        # 置信度(缺必填降级——两必填全缺 0.6)
        r_c = await scorer.score({"conditionGrade": "A"})
        record("置信度降级0.6",
               r_c.get("confidence") == 0.6,
               str(r_c.get("confidence")))

        # 输出结构
        record("输出结构齐备",
               r.get("scorer") == "recycle_valuation"
               and r.get("module") == "13老酒兑换"
               and "modelVersion" in r
               and "action" in r, "")


class TestRegistry:
    """02 44号注册同步"""

    async def run(self):
        print("[02 注册表]")
        reset_all()
        from services.ai_learning_service import (
            SCORER_REGISTRY, DECISION_THRESHOLDS,
            default_weights,
        )
        record("42 档案",
               len(SCORER_REGISTRY) == 45,
               str(len(SCORER_REGISTRY)))
        entry = SCORER_REGISTRY.get("recycle_valuation")
        record("batch26 入册",
               (entry or {}).get("batch") == 26
               and (entry or {}).get("module")
               == "13老酒兑换",
               str(entry))
        thresholds = DECISION_THRESHOLDS.get(
            "recycle_valuation")
        record("决策阈值三级",
               thresholds == [(60.0, "high"),
                               (30.0, "medium"),
                               (0.0, "low")],
               str(thresholds))
        weights = default_weights("recycle_valuation")
        record("默认权重八因子",
               len(weights) == 8
               and abs(sum(weights.values())
                       - 1.0) < 0.001,
               str(len(weights)))

        # _invoke_scorer 分支
        from services.ai_feedback_hooks import (
            _invoke_scorer,
        )
        result = await _invoke_scorer(
            "recycle_valuation",
            {"proposedPrice": 1000,
             "aiBasePrice": 1000})
        record("挂钩分支可调",
               (result or {}).get("scorer")
               == "recycle_valuation",
               str(result)[:60])


class TestGate:
    """03 议价决策门(observe 行为兼容)"""

    async def run(self):
        print("[03 决策门 observe]")
        reset_all()
        from services.recycle_service import (
            RecycleService,
        )
        from repositories.recycle_repository import (
            NEG_STATUS_USER_PROPOSED,
        )
        await seed_credit_account(3001, 800)
        svc = RecycleService()

        # 新酒估价(当年 A 级 1 瓶, 基准 900)
        neg = await svc.submit_new_wine_valuation(
            3001, "ZX42-AITEST", 1000.0,
            _new_purchase_date(0), "A", 1)
        neg_id = neg["id"]
        ai_base = neg["aiBasePrice"]

        # observe 模式: 出价行为 100% 兼容
        proposed = round(ai_base * 1.02, 2)
        result = await svc.user_propose_price(
            neg_id, proposed, "品质良好")
        record("observe 出价兼容",
               result["status"]
               == NEG_STATUS_USER_PROPOSED
               and result["currentPrice"]
               == proposed,
               str(result.get("status")))

        # 决策快照留痕
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        repo = AiLearningRepository()
        snap = await repo.get_decision_snapshot(
            "recycle_valuation",
            f"negotiation:{neg_id}")
        record("决策快照已存",
               snap is not None
               and snap.get("decision") == "low",
               str(snap)[:60])

        # aiReviewRequired 标记落记录(低风险 False)
        neg_data = await svc.get_negotiation(neg_id)
        record("低风险无转人工标记",
               neg_data.get("aiReviewRequired")
               is False,
               str(neg_data.get("aiReviewRequired")))

        # 硬规则保留: 系数超范围拒绝
        try:
            await svc.user_propose_price(
                neg_id, round(ai_base * 1.2, 2))
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("硬规则系数±10%保留", ok, err)


class TestFeedback:
    """04 议价回流闭环"""

    async def run(self):
        print("[04 议价回流]")
        reset_all()
        from services.recycle_service import (
            RecycleService,
        )
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        await seed_credit_account(3002, 800)
        svc = RecycleService()

        # 议价: 出价 → AI 反价 → 接受 → 回流
        neg = await svc.submit_new_wine_valuation(
            3002, "ZX42-AITEST", 1000.0,
            _new_purchase_date(0), "A", 1)
        neg_id = neg["id"]
        await svc.user_propose_price(
            neg_id, round(neg["aiBasePrice"] * 1.02, 2))
        await svc.ai_counter_price(
            neg_id, round(neg["aiBasePrice"] * 1.01, 2))
        await svc.accept_negotiation(neg_id, "user")

        repo = AiLearningRepository()
        fbs = await repo.list_feedback(
            "recycle_valuation")
        accepted_fb = [f for f in fbs
                       if f.get("actualAction")
                       == "accepted"]
        record("接受→自动反馈",
               len(accepted_fb) >= 1
               and accepted_fb[-1].get("source")
               == "auto",
               str(len(accepted_fb)))

        # 拒绝议价 → 回流
        neg2 = await svc.submit_new_wine_valuation(
            3002, "ZX42-AITEST", 1000.0,
            _new_purchase_date(0), "A", 1)
        await svc.user_propose_price(
            neg2["id"],
            round(neg2["aiBasePrice"] * 1.05, 2))
        await svc.reject_negotiation(
            neg2["id"], "user", "价格不合适")
        fbs = await repo.list_feedback(
            "recycle_valuation")
        rejected_fb = [f for f in fbs
                       if f.get("actualAction")
                       == "rejected"]
        record("拒绝→自动反馈",
               len(rejected_fb) >= 1,
               str(len(rejected_fb)))

        # 快照消费后不再重复配对
        snap = await repo.get_decision_snapshot(
            "recycle_valuation",
            f"negotiation:{neg_id}")
        record("快照配对即消费",
               snap is None, str(snap)[:40])


class TestEnforceMode:
    """05 enforce 模式拦截"""

    async def run(self):
        print("[05 enforce 拦截]")
        reset_all()
        os.environ["AI_ENFORCE_MODE"] = "enforce"
        os.environ["AI_ENFORCE_SCOPES"] = \
            "recycle_valuation"
        try:
            # 冷启动喂料(55 条正确反馈)
            from repositories.ai_learning_repository import (
                AiLearningRepository,
            )
            repo = AiLearningRepository()
            for _ in range(55):
                await repo.add_feedback({
                    "scorerId": "recycle_valuation",
                    "factors": [{
                        "name": "deviation_ratio",
                        "score": 10.0,
                        "weight": 0.22}],
                    "correct": True,
                    "createdAt":
                        "2026-01-01T00:00:00",
                })

            from services.recycle_service import (
                RecycleService,
            )
            svc = RecycleService()
            await seed_credit_account(3003, 300)

            # 高风险画像: D 级+满瓶+低信任
            # +高频议价(5 单历史)
            for _ in range(5):
                await svc.submit_new_wine_valuation(
                    3003, "ZX42-AITEST", 1000.0,
                    _new_purchase_date(0), "A", 1)
            neg = await svc.submit_new_wine_valuation(
                3003, "ZX42-AITEST", 1000.0,
                _new_purchase_date(2), "D", 3)
            blocked = False
            try:
                await svc.user_propose_price(
                    neg["id"],
                    round(neg["aiBasePrice"] * 1.10, 2))
            except ValueError as exc:
                blocked = "风控拦截" in str(exc)
            record("enforce 高风险拦截",
                   blocked, "未被拦截")

            # 低风险画像放行
            await seed_credit_account(3004, 800)
            neg2 = await svc \
                .submit_new_wine_valuation(
                    3004, "ZX42-AITEST", 1000.0,
                    _new_purchase_date(0), "A", 1)
            passed = False
            try:
                r = await svc.user_propose_price(
                    neg2["id"],
                    round(neg2["aiBasePrice"]
                          * 1.02, 2))
                passed = (r.get("status")
                          == "user_proposed")
            except ValueError:
                passed = False
            record("enforce 低风险放行",
                   passed, "被误拦")
        finally:
            os.environ["AI_ENFORCE_MODE"] = "observe"
            os.environ.pop(
                "AI_ENFORCE_SCOPES", None)


class TestConstitution:
    """06 宪法铁律"""

    async def run(self):
        print("[06 宪法铁律]")
        reset_all()
        from services.recycle_scorer import (
            RecycleValuationScorer,
        )
        scorer = RecycleValuationScorer()

        # 确定性: 同输入同输出
        ctx = {"proposedPrice": 1050,
               "aiBasePrice": 1000,
               "conditionGrade": "B",
               "negotiationRound": 2}
        r1 = await scorer.score(dict(ctx))
        r2 = await scorer.score(dict(ctx))
        record("确定性评分(同入同出)",
               r1["score"] == r2["score"],
               f"{r1['score']} vs {r2['score']}")

        # LLM 不进判定链(评分器无网络调用)
        import inspect
        src = inspect.getsource(
            type(scorer).score)
        record("判定链无 LLM",
               "llm" not in src.lower()
               and "LLM" not in src, "")

        # 决策门默认 observe
        os.environ.pop("AI_ENFORCE_MODE", None)
        from services.ai_enforcement import (
            enforcement_mode,
        )
        record("决策门默认 observe",
               enforcement_mode(
                   "recycle_valuation")
               == "observe",
               enforcement_mode(
                   "recycle_valuation"))

        # 议价硬规则优先于 AI 门
        # (系数超范围先拒绝——见 TestGate)


async def main():
    print("=" * 62)
    print("全站批次二·13号老酒兑换 AI 升级"
          " 专项测试")
    print("=" * 62)
    for cls in (TestScorer, TestRegistry,
                TestGate, TestFeedback,
                TestEnforceMode,
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
