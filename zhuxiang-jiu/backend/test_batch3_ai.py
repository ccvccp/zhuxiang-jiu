"""全站批次三·内容类三模块 AI 升级 专项测试

运行方式:
    python test_batch3_ai.py

覆盖(《全站AI智能混合架构升级总计划》批次三):
    - product_launch 评分器(第43档案
      batch27——pdm put_on_sale 上架终审门)
    - activity_risk 评分器(第44档案
      batch28——audit_activity 审核门)
    - ad_placement 评分器(第45档案
      batch29——online_ad 投放门)
    - 三门 observe 行为兼容+快照留痕
    - 三回流(下架/审核/下线终态→
      语义映射配对)
    - enforce 模式高风险拦截
    - 44号注册同步(45 档案)
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

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


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestScorers:
    """01 三评分器单元"""

    async def run(self):
        print("[01 三评分器]")
        reset_all()
        from services.product_launch_scorer import (
            ProductLaunchScorer,
        )
        from services.activity_risk_scorer import (
            ActivityRiskScorer,
        )
        from services.ad_placement_scorer import (
            AdPlacementScorer,
        )

        # 空上下文拒绝×3
        for cls in (ProductLaunchScorer,
                    ActivityRiskScorer,
                    AdPlacementScorer):
            try:
                await cls().score({})
                ok = False
            except ValueError:
                ok = True
            record(f"空上下文拒绝({cls.__name__})",
                   ok)

        # product_launch 低/高风险
        r = await ProductLaunchScorer().score({
            "price": 900, "originalPrice": 1000,
            "stock": 200, "ratingAvg": 4.8,
            "ratingCount": 60, "salesMonthly": 30,
            "ageDays": 10, "infoMissing": 0,
            "riskFlags": 0})
        record("上架低风险",
               r.get("level") == "low"
               and (r.get("score") or 0) < 30,
               str((r.get("score"), r.get("level"))))
        r2 = await ProductLaunchScorer().score({
            "price": 100, "originalPrice": 1000,
            "stock": 0, "ratingAvg": 3.0,
            "ratingCount": 0, "salesMonthly": 0,
            "ageDays": 400, "infoMissing": 4,
            "riskFlags": 3})
        record("上架高风险 high",
               r2.get("level") == "high"
               and (r2.get("score") or 0) >= 60,
               str((r2.get("score"), r2.get("level"))))
        record("上架八因子",
               len(r.get("factors") or []) == 8, "")

        # activity_risk 低/高风险
        r3 = await ActivityRiskScorer().score({
            "budget": 500, "prizeTotalValue": 200,
            "prizeProbability": 20,
            "durationDays": 7, "type": "promotion",
            "concurrentSameType": 0,
            "nightWindow": False,
            "applicableScope": "vip"})
        record("活动低风险",
               r3.get("level") == "low",
               str((r3.get("score"), r3.get("level"))))
        r4 = await ActivityRiskScorer().score({
            "budget": 20000, "prizeTotalValue": 18000,
            "prizeProbability": 100,
            "durationDays": 60, "type": "lottery",
            "concurrentSameType": 4,
            "nightWindow": True,
            "applicableScope": "all"})
        record("活动高风险 high",
               r4.get("level") == "high"
               and (r4.get("score") or 0) >= 60,
               str((r4.get("score"), r4.get("level"))))
        record("活动八因子",
               len(r3.get("factors") or []) == 8, "")

        # ad_placement 低/高风险
        r5 = await AdPlacementScorer().score({
            "reviewScore": 100, "dailyBudget": 500,
            "slotConcurrency": 1, "slotCapacity": 5,
            "adType": "BANNER", "durationDays": 7,
            "targetRules": "vip", "slotCtr": 0.06,
            "scheduleMissing": False})
        record("投放低风险",
               r5.get("level") == "low",
               str((r5.get("score"), r5.get("level"))))
        r6 = await AdPlacementScorer().score({
            "reviewScore": 60, "dailyBudget": 20000,
            "slotConcurrency": 5, "slotCapacity": 1,
            "adType": "POPUP", "durationDays": 60,
            "targetRules": "all", "slotCtr": 0.01,
            "scheduleMissing": True})
        record("投放高风险 high",
               r6.get("level") == "high"
               and (r6.get("score") or 0) >= 60,
               str((r6.get("score"), r6.get("level"))))
        record("投放八因子",
               len(r5.get("factors") or []) == 8, "")
        # CTR 无数据中性 30
        r7 = await AdPlacementScorer().score({
            "reviewScore": 100, "dailyBudget": 100,
            "slotCtr": None})
        ctr_f = {f["name"]: f["score"]
                 for f in r7["factors"]}
        record("位CTR无数据中性",
               ctr_f.get("ctr_baseline") == 30.0,
               str(ctr_f.get("ctr_baseline")))


class TestRegistry:
    """02 44号注册同步"""

    async def run(self):
        print("[02 注册表]")
        reset_all()
        from services.ai_learning_service import (
            SCORER_REGISTRY, DECISION_THRESHOLDS,
            default_weights,
        )
        record("45 档案",
               len(SCORER_REGISTRY) == 56,
               str(len(SCORER_REGISTRY)))
        for sid, batch, module in (
                ("product_launch", 27, "01产品展示"),
                ("activity_risk", 28, "09活动管理"),
                ("ad_placement", 29, "10广告投放")):
            entry = SCORER_REGISTRY.get(sid)
            record(f"{sid} batch{batch} 入册",
                   (entry or {}).get("batch") == batch
                   and (entry or {}).get("module")
                   == module,
                   str(entry))
            record(f"{sid} 阈值三级",
                   DECISION_THRESHOLDS.get(sid)
                   == [(60.0, "high"),
                       (30.0, "medium"),
                       (0.0, "low")],
                   "")
            record(f"{sid} 默认权重八因子",
                   len(default_weights(sid)) == 8,
                   "")
        # 挂钩分支
        from services.ai_feedback_hooks import (
            _invoke_scorer,
        )
        for sid in ("product_launch",
                    "activity_risk", "ad_placement"):
            result = await _invoke_scorer(
                sid, {"price": 100, "budget": 100,
                      "reviewScore": 100})
            record(f"挂钩分支可调({sid})",
                   (result or {}).get("scorer")
                   == sid, "")


class TestGates:
    """03 三门 observe 行为兼容+快照+回流"""

    async def run(self):
        print("[03 三门接线]")
        reset_all()

        # ---- ① pdm 上架门 ----
        from repositories.member_repository import (
            MemberRepository,
        )
        from services.perm_service import PermService
        member_repo = MemberRepository()
        perm_svc = PermService()
        SUPER = 2
        seq = getattr(TestGates, "_seq", 6001)
        TestGates._seq = seq + 1
        operator = await member_repo.create({
            "phone": f"13977{seq:06d}",
            "password": "x", "nickname": "B3运营",
            "avatar": "", "gender": 1, "level": 1,
            "growth_value": 0, "points": 0,
            "status": 1, "reg_source": "phone",
            "role": "member"})
        operator = operator["id"]
        await perm_svc.assign_grant(
            SUPER, operator, "product.operate")
        from services.pdm_service import PdmService
        pdm = PdmService()
        p = await pdm.create_product(
            operator, "member",
            {"name": "B3测试酒", "price": 288,
             "stock": 100, "subtitle": "清香",
             "description": "测试描述",
             "tags": ["测试"], "scenes": ["自饮"]})
        pid = p["product_id"]
        result = await pdm.put_on_sale(
            SUPER, "admin", pid)
        record("上架 observe 兼容",
               (result or {}).get("status")
               == "on_sale",
               str((result or {}).get("status")))
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        repo = AiLearningRepository()
        snap = await repo.get_decision_snapshot(
            "product_launch", f"onsale:{pid}")
        record("上架快照已存",
               snap is not None
               and snap.get("decision") == "low",
               str(snap)[:50])
        # 下架 → 回流
        await pdm.take_off_sale(
            SUPER, "admin", pid, "测试下架")
        fbs = await repo.list_feedback(
            "product_launch")
        record("下架→自动反馈",
               len([f for f in fbs
                    if f.get("actualAction")
                    == "delisted"]) >= 1,
               str(len(fbs)))

        # ---- ② 活动审核门 ----
        from services.activity_service import (
            ActivityService,
        )
        asvc = ActivityService()
        now = datetime.utcnow()
        act = await asvc.create_activity(
            "B3活动", "promotion",
            start_time=_iso(now + timedelta(days=1)),
            end_time=_iso(now + timedelta(days=8)),
            budget=500.0,
            applicable_scope={"vip": True},
            created_by=1)
        aid = act["id"]
        r = await asvc.audit_activity(
            aid, approve=True, auditor=1)
        record("审核 observe 兼容",
               r.get("status") == "registering",
               str(r.get("status")))
        snap = await repo.get_decision_snapshot(
            "activity_risk", f"activity:{aid}")
        record("审核快照闭环(即消费)",
               snap is None,
               "快照未被消费")
        fbs = await repo.list_feedback(
            "activity_risk")
        record("审核通过→自动反馈",
               len([f for f in fbs
                    if f.get("actualAction")
                    == "approved"]) >= 1,
               str(len(fbs)))
        # 拒绝路径
        act2 = await asvc.create_activity(
            "B3活动2", "promotion",
            start_time=_iso(now + timedelta(days=1)),
            end_time=_iso(now + timedelta(days=8)),
            budget=500.0, created_by=1)
        await asvc.audit_activity(
            act2["id"], approve=False, auditor=1,
            reason="不合适")
        fbs = await repo.list_feedback(
            "activity_risk")
        record("审核拒绝→自动反馈",
               len([f for f in fbs
                    if f.get("actualAction")
                    == "rejected"]) >= 1,
               str(len(fbs)))

        # ---- ③ 广告投放门 ----
        from services.ad_service import AdService
        adsvc = AdService()
        ad = await adsvc.create_ad(
            "B3广告主", "B3广告", "BANNER",
            "home_banner_1",
            "[广告]B3竹叶青酒 过量饮酒有害健康",
            description="测试广告",
            start_time=_iso(now),
            end_time=_iso(now + timedelta(days=7)),
            budget=3000, daily_budget=300,
            target_rules={"vip": True})
        ad_id = ad["id"]
        await adsvc.review_ad(ad_id)
        r = await adsvc.online_ad(ad_id)
        record("投放 observe 兼容",
               r.get("status") == "online",
               str(r.get("status")))
        snap = await repo.get_decision_snapshot(
            "ad_placement", f"adonline:{ad_id}")
        record("投放快照已存",
               snap is not None,
               str(snap)[:40])
        await adsvc.offline_ad(ad_id, "调整排期")
        fbs = await repo.list_feedback(
            "ad_placement")
        record("下线→自动反馈",
               len([f for f in fbs
                    if f.get("actualAction")
                    == "offline"]) >= 1,
               str(len(fbs)))


class TestEnforceMode:
    """04 enforce 模式拦截"""

    async def run(self):
        print("[04 enforce 拦截]")
        reset_all()
        os.environ["AI_ENFORCE_MODE"] = "enforce"
        os.environ["AI_ENFORCE_SCOPES"] = \
            "product_launch,activity_risk," \
            "ad_placement"
        try:
            from repositories.ai_learning_repository import (
                AiLearningRepository,
            )
            repo = AiLearningRepository()
            for sid in ("product_launch",
                        "activity_risk",
                        "ad_placement"):
                for _ in range(55):
                    await repo.add_feedback({
                        "scorerId": sid,
                        "factors": [{
                            "name": "x",
                            "score": 10.0,
                            "weight": 0.5}],
                        "correct": True,
                        "createdAt":
                            "2026-01-01T00:00:00",
                    })

            # ① 上架高风险拦截(0库存+深折+
            # 信息缺口)
            from repositories.member_repository import (
                MemberRepository,
            )
            from services.perm_service import PermService
            member_repo = MemberRepository()
            perm_svc = PermService()
            SUPER = 2
            seq = getattr(TestEnforceMode,
                          "_seq", 6101)
            TestEnforceMode._seq = seq + 1
            op = await member_repo.create({
                "phone": f"13978{seq:06d}",
                "password": "x", "nickname": "B3运营",
                "avatar": "", "gender": 1, "level": 1,
                "growth_value": 0, "points": 0,
                "status": 1, "reg_source": "phone",
                "role": "member"})
            op = op["id"]
            await perm_svc.assign_grant(
                SUPER, op, "product.operate")
            from services.pdm_service import PdmService
            pdm = PdmService()
            bad = await pdm.create_product(
                op, "member",
                {"name": "B3劣质品", "price": 50,
                 "originalPrice": 2000,
                 "stock": 0})
            blocked = False
            try:
                await pdm.put_on_sale(
                    SUPER, "admin", bad["product_id"])
            except ValueError as exc:
                blocked = "风控拦截" in str(exc)
            record("enforce 上架拦截",
                   blocked, "未拦截")
            good = await pdm.create_product(
                op, "member",
                {"name": "B3优质品", "price": 288,
                 "stock": 200, "subtitle": "s",
                 "description": "d",
                 "tags": ["t"], "scenes": ["s"]})
            passed = False
            try:
                r = await pdm.put_on_sale(
                    SUPER, "admin",
                    good["product_id"])
                passed = (r or {}).get("status") \
                    == "on_sale"
            except ValueError:
                passed = False
            record("enforce 上架低风险放行",
                   passed, "被误拦")

            # ② 活动高风险拦截
            from services.activity_service import (
                ActivityService,
            )
            now = datetime.utcnow()
            asvc = ActivityService()
            bad_act = await asvc.create_activity(
                "B3高风险活动", "lottery",
                start_time=_iso(
                    now + timedelta(hours=2)),
                end_time=_iso(
                    now + timedelta(days=60)),
                budget=50000.0, created_by=1)
            # 高风险奖池: 概率总和 100%+奖池占预算 90%
            # (单奖 ≤¥5000 合规红线内堆满奖池)
            await asvc.configure_prizes(
                bad_act["id"],
                [{"prizeName": f"高档奖品{i}",
                  "prizeType": "physical",
                  "prizeValue": 4900.0,
                  "probability": 5,
                  "dailyLimit": 1, "totalLimit": 1}
                 for i in range(9)]
                + [{"prizeName": "参与奖",
                    "prizeType": "points",
                    "prizeValue": 10.0,
                    "probability": 55,
                    "dailyLimit": 100,
                    "totalLimit": 1000}])
            blocked = False
            try:
                await asvc.audit_activity(
                    bad_act["id"], approve=True,
                    auditor=1)
            except ValueError as exc:
                blocked = "风控拦截" in str(exc)
            record("enforce 活动拦截",
                   blocked, "未拦截")
            good_act = await asvc.create_activity(
                "B3低风险活动", "promotion",
                start_time=_iso(
                    now + timedelta(days=1)),
                end_time=_iso(
                    now + timedelta(days=8)),
                budget=500.0, created_by=1)
            passed = False
            try:
                r = await asvc.audit_activity(
                    good_act["id"], approve=True,
                    auditor=1)
                passed = r.get("status") \
                    == "registering"
            except ValueError:
                passed = False
            record("enforce 活动低风险放行",
                   passed, "被误拦")

            # ③ 广告高风险拦截(POPUP+拥塞)
            from services.ad_service import AdService
            adsvc = AdService()
            bad_ad = await adsvc.create_ad(
                "B3广告主", "B3弹窗广告", "POPUP",
                "popup_1",
                "[广告]B3 过量饮酒有害健康",
                start_time=_iso(now),
                end_time=_iso(
                    now + timedelta(days=60)),
                budget=50000,
                daily_budget=20000)
            await adsvc.review_ad(bad_ad["id"])
            blocked = False
            try:
                await adsvc.online_ad(bad_ad["id"])
            except ValueError as exc:
                blocked = "风控拦截" in str(exc)
            record("enforce 投放拦截",
                   blocked, "未拦截")
            good_ad = await adsvc.create_ad(
                "B3广告主", "B3横幅广告", "BANNER",
                "home_banner_2",
                "[广告]B3竹叶青 过量饮酒有害健康",
                start_time=_iso(now),
                end_time=_iso(
                    now + timedelta(days=7)),
                budget=3000, daily_budget=300,
                target_rules={"vip": True})
            await adsvc.review_ad(good_ad["id"])
            passed = False
            try:
                r = await adsvc.online_ad(
                    good_ad["id"])
                passed = r.get("status") == "online"
            except ValueError:
                passed = False
            record("enforce 投放低风险放行",
                   passed, "被误拦")
        finally:
            os.environ["AI_ENFORCE_MODE"] = "observe"
            os.environ.pop(
                "AI_ENFORCE_SCOPES", None)


class TestConstitution:
    """05 宪法铁律"""

    async def run(self):
        print("[05 宪法铁律]")
        reset_all()
        os.environ.pop("AI_ENFORCE_MODE", None)
        from services.ai_enforcement import (
            enforcement_mode,
        )
        record("决策门默认 observe",
               all(enforcement_mode(s)
                   == "observe"
                   for s in ("product_launch",
                             "activity_risk",
                             "ad_placement")), "")
        # 确定性
        from services.activity_risk_scorer import (
            ActivityRiskScorer,
        )
        ctx = {"budget": 1000,
               "durationDays": 7}
        r1 = await ActivityRiskScorer().score(
            dict(ctx))
        r2 = await ActivityRiskScorer().score(
            dict(ctx))
        record("确定性评分(同入同出)",
               r1["score"] == r2["score"], "")
        # 硬规则保留: 活动状态机
        from services.activity_service import (
            ActivityService,
        )
        asvc = ActivityService()
        now = datetime.utcnow()
        act = await asvc.create_activity(
            "B3硬规则", "promotion",
            start_time=_iso(now),
            end_time=_iso(now + timedelta(days=3)),
            budget=100.0, created_by=1)
        await asvc.audit_activity(
            act["id"], approve=True, auditor=1)
        try:
            await asvc.audit_activity(
                act["id"], approve=True, auditor=1)
            ok = False
        except ValueError:
            ok = True
        record("审核状态机硬规则保留",
               ok, "重复审核未拒绝")


async def main():
    print("=" * 62)
    print("全站批次三·内容类三模块 AI 升级"
          " 专项测试")
    print("=" * 62)
    for cls in (TestScorers, TestRegistry,
                TestGates, TestEnforceMode,
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
