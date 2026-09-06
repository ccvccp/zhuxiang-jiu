"""全站批次一·23号信用管理 AI 升级 专项测试

运行方式:
    python test_credit_ai.py

覆盖(《全站AI智能混合架构升级总计划》批次一):
    - credit_scoring 评分器(第41档案
      八因子/三级决策/置信度)
    - 先享后付决策门(observe 模式
      行为兼容/enforce 模式拦截/
      reviewRequired 转人工)
    - 回流闭环(还款/审批终态→
      语义映射配对)
    - 旁路修复(45号转换轨不再
      即时改写 creditLevel)
    - 44号注册同步(41 档案)
"""

import asyncio
import os
import sys

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


async def seed_credit_account(user_id, score=700,
                              level="L4",
                              quota=None):
    """信用账户种子(直写内存态)"""
    from repositories.store import _mock_store
    from repositories.credit_repository import (
        CreditRepository,
        LEVEL_PAYLATER_QUOTA,
    )
    repo = CreditRepository()
    await repo.get_or_create_score(user_id)
    acct = _mock_store["credit_scores"][user_id]
    acct["bambooScore"] = score
    acct["creditLevel"] = level
    acct["paylaterQuota"] = quota if quota \
        is not None else \
        LEVEL_PAYLATER_QUOTA.get(level, 0)
    return acct


class TestScorer:
    """01 credit_scoring 评分器"""

    async def run(self):
        print("[01 评分器]")
        reset_all()
        from services.credit_scorer import (
            CreditScoringScorer,
        )
        scorer = CreditScoringScorer()

        # 空上下文拒绝
        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空上下文拒绝", ok, err)

        # 低风险(优质客户)
        r = await scorer.score({
            "bambooScore": 750,
            "quota": 5000, "used": 300,
            "monthlyLimit": 20000,
            "monthUsed": 1500,
            "singleLimit": 3000,
            "amount": 500,
            "overdueOrders": 0,
            "zoneDays": 120,
            "accountStatus": "normal",
            "repaidOrders": 5,
        })
        record("优质客户低风险",
               r.get("level") == "low"
               and (r.get("score") or 0)
               < 30,
               str((r.get("score"),
                    r.get("level"))))

        # 高风险(近门槛+逾期+满额)
        r2 = await scorer.score({
            "bambooScore": 560,
            "quota": 2000, "used": 1900,
            "monthlyLimit": 6000,
            "monthUsed": 5500,
            "singleLimit": 1000,
            "amount": 950,
            "overdueOrders": 2,
            "zoneDays": 3,
            "accountStatus": "normal",
            "repaidOrders": 0,
        })
        record("高风险客户 high",
               r2.get("level") == "high"
               and (r2.get("score")
                    or 0) >= 60,
               str((r2.get("score"),
                    r2.get("level"))))

        # 八因子齐备
        record("八因子齐备",
               len(r.get("factors")
                   or []) == 8,
               str(len(r.get("factors")
                          or [])))

        # 权重和=1.0
        record("权重和=1.0",
               abs(sum((r.get(
                   "weightsUsed")
                   or {}).values())
                   if r.get("weightsUsed")
                   else sum(
                   CreditScoringScorer
                   .WEIGHTS.values())
                   - 1.0) < 0.01,
               "")

        # 置信度(缺必填降级)
        r3 = await scorer.score({
            "bambooScore": 700})
        record("缺字段降置信度",
               (r3.get("confidence")
                or 1.0) < 1.0,
               str(r3.get("confidence")))


class TestRegistry:
    """02 44号注册同步"""

    async def run(self):
        print("[02 注册同步]")
        from services.ai_learning_service import (
            DECISION_THRESHOLDS,
            SCORER_REGISTRY,
            default_weights,
        )
        record("44号 45 档案",
               len(SCORER_REGISTRY) == 45,
               str(len(SCORER_REGISTRY)))
        record("credit_scoring 在册"
               "(batch25)",
               SCORER_REGISTRY.get(
                   "credit_scoring",
               {}).get("batch") == 25,
               str(SCORER_REGISTRY.get(
                   "credit_scoring")))
        record("决策阈值注册",
               DECISION_THRESHOLDS.get(
                   "credit_scoring")
               == [(60.0, "high"),
                   (30.0, "medium"),
                   (0.0, "low")],
               str(DECISION_THRESHOLDS
                   .get("credit_scoring")))
        weights = default_weights(
            "credit_scoring")
        record("默认权重可取",
               len(weights) == 8
               and weights.get(
                   "zone_margin") == 0.20,
               str(weights))


class TestGate:
    """03 先享后付决策门"""

    async def run(self):
        print("[03 决策门]")
        reset_all()
        from services.credit_service import (
            CreditService,
        )
        await seed_credit_account(101)
        svc = CreditService()

        # observe 模式行为兼容(低风险
        # 自动通过)
        order = await svc \
            .create_paylater_order(
                101, 500)
        record("observe 模式自动通过"
               "(行为兼容)",
               order.get("status")
               == "active"
               and order.get(
                   "riskLevel")
               == "low",
               str((order.get("status"),
                    order.get(
                        "riskLevel"))))

        # 决策快照已存(学习闭环
        # 即使 observe 也启动)
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        snap = await \
            AiLearningRepository() \
            .get_decision_snapshot(
                "credit_scoring",
                f"paylater:"
                f"{order.get('orderNo')}")
        record("决策快照已存"
               "(observe 学习)",
               snap is not None
               and snap.get(
                   "decision")
               is not None,
               str(bool(snap)))

        # 人工审批回流(review 终态)
        await seed_credit_account(
            102, score=560)
        order2 = await svc \
            .create_paylater_order(
                102, 900)
        # 560 分 → zone_rank<3? 不,
        # 560>=550 是 L3——但 560
        # 距门槛近+大额会转 review
        if order2.get("status") \
                == "review":
            rv = await svc \
                .review_paylater_order(
                    order2["orderId"],
                    approved=True)
            record("审批回流触发",
                   rv.get("status")
                   == "active",
                   str(rv.get("status")))
        else:
            record("审批回流触发",
                   False,
                   f"未进 review: "
                   f"{order2.get('status')}")

        # 还款回流(repaid 终态)
        order3 = await svc \
            .create_paylater_order(
                101, 300)
        rp = await svc \
            .repay_paylater_order(
                order3["orderId"])
        record("还款回流触发",
               rp.get("status")
               == "repaid",
               str(rp.get("status")))

        # 反馈记录落库(44号池)
        feedbacks = await \
            AiLearningRepository() \
            .list_feedback(
                "credit_scoring",
                limit=20)
        record("44号池反馈留痕"
               "(≥2 条)",
               len(feedbacks) >= 2,
               str(len(feedbacks)))


class TestEnforceMode:
    """04 enforce 模式拦截"""

    async def run(self):
        print("[04 enforce 模式]")
        reset_all()
        from services.credit_service import (
            CreditService,
        )
        # 高风险客户(近门槛+逾期
        # 历史+满额)
        await seed_credit_account(
            201, score=560, level="L3")
        svc = CreditService()

        # observe(默认)——不拦截
        os.environ[
            "AI_ENFORCE_MODE"] = "observe"
        o1 = await svc \
            .create_paylater_order(
                201, 500)
        record("observe 不拦截",
               o1.get("status")
               in ("active",
                   "review"),
               str(o1.get("status")))

        # enforce 冷启动保护——须先
        # 灌 ≥50 条正确反馈样本
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        repo = AiLearningRepository()
        for _ in range(55):
            await repo.add_feedback({
                "scorerId":
                    "credit_scoring",
                "factors": [{
                    "name":
                        "zone_margin",
                    "score": 10.0,
                    "weight": 0.20}],
                "scoreAtDecision":
                    15.0,
                "actualAction":
                    "low",
                "correct": True,
                "reward": 1.0,
                "source": "auto",
            })

        # enforce + scopes 圈定
        # credit_scoring → 拦截
        os.environ[
            "AI_ENFORCE_MODE"] = "enforce"
        os.environ[
            "AI_ENFORCE_SCOPES"] = \
            "credit_scoring"
        await seed_credit_account(
            202, score=555, level="L3")
        # 直写历史逾期单(3 张——
        # 风险分确过 60 拦截线;
        # 双索引: orderId 主表
        # + by_user 用户索引)
        from repositories.store import _mock_store
        _mock_store.setdefault(
            "credit_paylater_orders",
            {})
        _mock_store.setdefault(
            "credit_paylater_orders"
            "_by_user",
            {})
        _mock_store[
            "credit_paylater_orders"
            "_by_user"].setdefault(
            202, [])
        for oid in (997, 998, 999):
            _mock_store[
                "credit_paylater_orders"][
                oid] = {
                "orderId": oid,
                "userId": 202,
                "orderNo":
                    f"PLTEST{oid}",
                "amount": 500,
                "status": "repaid",
                "overdueDays": 10,
                "createdAt":
                    "2026-01-01"
                    "T00:00:00",
            }
            _mock_store[
                "credit_paylater"
                "_orders_by_user"][
                202].append(oid)
        # 满额度使用
        acct = _mock_store[
            "credit_scores"][202]
        acct["paylaterUsed"] = 1900
        blocked = False
        try:
            await svc \
                .create_paylater_order(
                    202, 50)
        except ValueError as exc:
            blocked = "风控拦截" in str(exc)
        record("enforce 高风险拦截",
               blocked,
               "未拦截" if not blocked
               else "拦截 OK")

        # 还原环境
        os.environ[
            "AI_ENFORCE_MODE"] = "observe"
        os.environ.pop(
            "AI_ENFORCE_SCOPES", None)


class TestBypassFix:
    """05 旁路修复(45号转换轨)"""

    async def run(self):
        print("[05 旁路修复]")
        reset_all()
        # 45号转换轨调分后
        # creditLevel 不再即时改写
        from services.trust_asset_service import (
            TrustAssetService,
        )
        from repositories.store import _mock_store
        svc = TrustAssetService()
        # L4 账户(700 分)转换 200
        # → 500 分(L2 区间)——
        # 等级应保持 L4(由评估器
        # 管理), 仅区间跟踪重置
        await seed_credit_account(
            301, score=700, level="L4")
        # 45号信值档案种子
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        from core.helpers import ts as _ts
        await TrustValue45Repository() \
            .save_profile({
                "trustId": 301,
                "role": "person",
                "name": "旁路测试",
                "idDigest":
                    "digest-301",
                "factors": {},
                "score": 100.0,
                "rawScore": 100.0,
                "grade": "A",
                "fused": False,
                "frozen": False,
                "createdAt": _ts(),
                "updatedAt": _ts(),
            })
        await svc.convert(
            301, 301, 200)
        acct = _mock_store[
            "credit_scores"][301]
        record("转换后等级不变"
               "(评估器管理)",
               acct.get("creditLevel")
               == "L4"
               and acct.get(
                   "bambooScore")
               == 500,
               str((acct.get(
                    "creditLevel"),
                   acct.get(
                       "bambooScore"))))
        record("区间跟踪重置",
               acct.get(
                   "scoreZoneSince")
               is not None,
               str(acct.get(
                   "scoreZoneSince")))


class TestConstitution:
    """06 宪法断言"""

    async def run(self):
        print("[06 宪法断言]")
        from services.ai_enforcement import (
            enforcement_mode,
        )
        os.environ.pop(
            "AI_ENFORCE_MODE", None)
        record("决策门默认 observe"
               "(行为兼容)",
               enforcement_mode(
                   "credit_scoring")
               == "observe",
               str(enforcement_mode(
                   "credit_scoring")))

        # 硬规则保留(低于 L3 门槛
        # 仍拒绝——AI 门不替代;
        # L2 额度 0 → "无先享后付额度")
        reset_all()
        from services.credit_service import (
            CreditService,
        )
        await seed_credit_account(
            401, score=400, level="L2")
        rejected = False
        try:
            await CreditService() \
                .create_paylater_order(
                    401, 100)
        except ValueError as exc:
            rejected = "无先享后付额度" in str(exc)
        record("L2 硬规则拒绝保留",
               rejected,
               "硬规则被绕过" if not
               rejected else "OK")


async def run_all():
    await TestScorer().run()
    await TestRegistry().run()
    await TestGate().run()
    await TestEnforceMode().run()
    await TestBypassFix().run()
    await TestConstitution().run()


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
