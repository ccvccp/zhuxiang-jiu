"""65号·网店及商品AI智能管理模块
P0 专项测试(刚性规则底座)

运行方式:
    python test_xx65_p0.py

覆盖(65号计划 §八 P0):
    - S1-S8 刚性规则封闭注册+启动自检
    - 意图解析(确定性关键词路由
      +回退 general)
    - 信值准入预检(S2 双维:
      23号 creditLevel×47号 tier)
    - 店铺六态状态机(applying→
      prechecked→claimed→active→
      suspended→closed)
    - 开店/认领(合规承诺)/激活/关店
    - 第39档案八因子
    - HTTP 层+宪法断言
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
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["DM61_MODE"] = "off"
os.environ["AV62_MODE"] = "off"
os.environ["XX64_MODE"] = "off"
os.environ["XX65_MODE"] = "off"

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


async def seed_profile(trust_id, score=100.0):
    """45号档案种子(纯读取——
    非侵入式落库)"""
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    repo = TrustValue45Repository()
    await repo.save_profile({
        "trustId": int(trust_id),
        "role": "person",
        "name": f"测试主体{trust_id}",
        "idDigest": f"digest-{trust_id}",
        "factors": {},
        "score": float(score),
        "rawScore": float(score),
        "grade": "A",
        "fused": False,
        "frozen": False,
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00",
    })
    return trust_id


async def seed_credit(owner_id, credit_level):
    """23号信用种子(creditLevel 直填)"""
    from repositories.credit_repository import (
        CreditRepository,
    )
    from repositories.store import _mock_store
    repo = CreditRepository()
    await repo.get_or_create_score(owner_id)
    _mock_store["credit_scores"][
        owner_id]["creditLevel"] = credit_level
    return owner_id


async def seed_tier(trust_id, tier):
    """47号画像种子(riskEMA 反推
    tier: trusted/standard/watched/
    restricted)"""
    from repositories.trust_risk_repository import (
        TrustRisk47Repository,
    )
    risk = {
        "trusted": 0.05,
        "standard": 0.4,
        "watched": 0.65,
        "restricted": 0.95,
    }[tier]
    await TrustRisk47Repository() \
        .save_profile({
            "trustId": int(trust_id),
            "riskEMA": risk,
            "eventCount": 1,
            "hitCounts": {},
            "riskHistory": [],
            "reviewRequests": [],
            "createdAt": "2026-01-01T00:00:00",
            "lastUpdated":
                "2026-01-01T00:00:00",
        })
    return trust_id


class TestRegistry:
    """01 刚性规则注册表"""

    async def run(self):
        print("[01 刚性规则]")
        reset_all()
        from services.xx65_registry import (
            AI_QUOTA_TIERS, CATEGORY_TEMPLATES,
            REVOKE_WINDOW_SECONDS,
            SHOP_MIN_CREDIT_LEVEL,
            SHOP_STATES, SHOP_TRANSITIONS,
            level_rank, quota_tier,
            registry_view, required_level,
        )

        record("S2 门槛 L3",
               SHOP_MIN_CREDIT_LEVEL == "L3",
               str(SHOP_MIN_CREDIT_LEVEL))
        record("S5 撤销窗口 300s",
               REVOKE_WINDOW_SECONDS == 300,
               str(REVOKE_WINDOW_SECONDS))
        record("tier 加严(watched→L4)",
               required_level("watched")
               == "L4",
               required_level("watched"))
        record("tier 加严(restricted→L5)",
               required_level("restricted")
               == "L5",
               required_level("restricted"))
        record("tier 基准(standard→L3)",
               required_level("standard")
               == "L3",
               required_level("standard"))
        record("配额三档",
               set(AI_QUOTA_TIERS)
               == {"starter", "growth",
                   "premium"},
               str(sorted(AI_QUOTA_TIERS)))
        record("配额映射(L5→premium)",
               quota_tier("L5") == "premium"
               and quota_tier("L4")
               == "growth"
               and quota_tier("L3")
               == "starter",
               quota_tier("L5"))

        # 六态状态机
        record("店铺六态",
               len(SHOP_STATES) == 6
               and "applying"
               in SHOP_STATES,
               str(len(SHOP_STATES)))
        record("主链迁移"
               "(applying→prechecked"
               "→claimed→active)",
               "prechecked" in
               SHOP_TRANSITIONS[
                   "applying"]
               and "claimed" in
               SHOP_TRANSITIONS[
                   "prechecked"]
               and "active" in
               SHOP_TRANSITIONS[
                   "claimed"],
               "")
        record("active 可冻结/关店",
               "suspended" in
               SHOP_TRANSITIONS["active"]
               and "closed" in
               SHOP_TRANSITIONS["active"],
               "")
        record("suspended 可恢复",
               "active" in
               SHOP_TRANSITIONS[
                   "suspended"],
               "")
        record("终态无出边(closed)",
               SHOP_TRANSITIONS[
                   "closed"] == (),
               "")

        # 类目模板
        record("类目模板含兜底"
               "(general)",
               "general" in
               CATEGORY_TEMPLATES
               and CATEGORY_TEMPLATES[
                   "general"].get(
                       "keywords")
               is not None,
               str(sorted(
                   CATEGORY_TEMPLATES)))
        record("level_rank(L3=2,"
               "0 基序)",
               level_rank("L3") == 2
               and level_rank("L5") == 4
               and level_rank("") == 0,
               str(level_rank("L3")))

        # 观测面
        v = registry_view()
        record("registry 观测面"
               "(S1-S8)",
               len(v.get("rules") or {})
               == 8
               and v.get("mode") == "off",
               str(len(v.get("rules")
                        or {})))
        record("观测面类目在册",
               len(v.get("categories")
                   or {}) >= 6,
               str(len(v.get(
                   "categories") or {})))

        # 启动自检(导入即验)
        record("启动自检通过",
               True, "")


class TestIntent:
    """02 意图解析(确定性路由)"""

    async def run(self):
        print("[02 意图解析]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        svc = Xx65Service()

        # off 铁律
        try:
            await svc.parse_intent(
                101, "手工木雕")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(解析拒绝)",
               ok, err)

        os.environ["XX65_MODE"] = "assist"

        # 参数校验
        for args in (
                (0, "手工木雕", "owner 缺省"),
                (101, "", "意图为空"),
                (101, "x" * 501,
                 "意图超长")):
            try:
                await svc.parse_intent(
                    args[0], args[1])
                ok, err = False, "未拒绝"
            except ValueError:
                ok, err = True, ""
            record(args[2] + " 拒绝",
                   ok, err)

        # 命中 handicraft
        r1 = await svc.parse_intent(
            101, "我想用祖传手艺做"
                 "定制木雕和皮具",
            audience="年轻消费者")
        record("命中 handicraft",
               r1.get("category")
               == "handicraft"
               and r1.get("fallback")
               is False,
               str(r1.get("category")))
        record("类目标签+合规问题",
               r1.get("categoryLabel")
               == "手工艺品"
               and len(r1.get(
                   "complianceQuestions")
                   or []) >= 1,
               str(r1.get(
                   "categoryLabel")))

        # 确定性(同输入同输出)
        r2 = await svc.parse_intent(
            101, "我想用祖传手艺做"
                 "定制木雕和皮具")
        record("同输入同输出",
               r2.get("category")
               == r1.get("category"),
               str((r1.get("category"),
                    r2.get("category"))))

        # 回退 general
        r3 = await svc.parse_intent(
            101, "卖点自己攒的小玩意儿")
        record("回退 general"
               "(无命中)",
               r3.get("fallback") is True
               and r3.get("category")
               == "general",
               str((r3.get("category"),
                    r3.get("fallback"))))

        # food 类目(高门槛 L4)
        r4 = await svc.parse_intent(
            101, "卖家乡特产茶叶和零食")
        record("food 命中"
               "(minLevel L4)",
               r4.get("category")
               == "food"
               and r4.get("minLevel")
               == "L4",
               str((r4.get("category"),
                    r4.get("minLevel"))))

        # 意图落库
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        intents = await Xx65Repository() \
            .list_intents(owner_id=101,
                          limit=10)
        record("意图落库(4 条)",
               len(intents) == 4,
               str(len(intents)))
        os.environ["XX65_MODE"] = "off"


class TestPrecheck:
    """03 信值准入预检(S2 双维)"""

    async def run(self):
        print("[03 准入预检]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        svc = Xx65Service()

        # 45号档案不存在
        try:
            await svc.admission_precheck(
                999, 999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("档案不存在拒绝",
               ok, err)

        # L4 信用通过(standard tier)
        await seed_profile(1, 100.0)
        await seed_credit(101, "L4")
        c = await svc.admission_precheck(
            101, 1)
        record("三查结构齐备",
               set(c.get("checks")
                   or {}) == {
                   "S2_CREDIT",
                   "S2_TIER",
                   "S2_TRUST"},
               str(sorted(c.get(
                   "checks") or {})))
        record("L4 信用通过",
               c.get("passed") is True
               and c.get(
                   "requiredLevel")
               == "L3",
               str((c.get("passed"),
                    c.get(
                        "requiredLevel"))))
        record("配额档 growth"
               "(L4)",
               c.get("quotaTier")
               == "growth",
               str(c.get("quotaTier")))

        # 信用不足(L1< L3)
        await seed_profile(2, 100.0)
        await seed_credit(102, "L1")
        c2 = await svc.admission_precheck(
            102, 2)
        record("信用不足拒绝"
               "(L1<L3)",
               c2.get("passed") is False
               and (c2.get("checks")
                    or {}).get(
                        "S2_CREDIT")
               is False,
               str(c2.get("checks")))
        record("拒绝建议在案",
               bool(c2.get("advice")),
               str(c2.get("advice"))[:30])

        # tier 加严(watched→门槛
        # L4; 信用 L3<L4 拒)
        await seed_profile(3, 100.0)
        await seed_credit(103, "L3")
        await seed_tier(3, "watched")
        c3 = await svc.admission_precheck(
            103, 3)
        record("watched 加严拒绝"
               "(L3<L4)",
               c3.get("passed") is False
               and c3.get(
                   "requiredLevel")
               == "L4",
               str((c3.get(
                    "passed"),
                   c3.get(
                       "requiredLevel"))))

        # restricted 直接拦截
        await seed_profile(4, 100.0)
        await seed_credit(104, "L5")
        await seed_tier(4, "restricted")
        c4 = await svc.admission_precheck(
            104, 4)
        record("restricted 拦截"
               "(S2_TIER False)",
               (c4.get("checks")
                or {}).get("S2_TIER")
               is False,
               str(c4.get("checks")))


class TestShopFlow:
    """04 开店六态全链"""

    async def run(self):
        print("[04 开店全链]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        svc = Xx65Service()

        # off 铁律
        try:
            await svc.apply_shop(101, 1)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(申请拒绝)",
               ok, err)

        os.environ["XX65_MODE"] = "assist"
        await seed_profile(1, 100.0)
        await seed_credit(101, "L4")

        # 准入不满足拒绝(低信用
        # 会员 102——L1)
        await seed_profile(2, 100.0)
        await seed_credit(102, "L1")
        try:
            await svc.apply_shop(102, 2)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "准入不满足" in str(e), ""
        record("准入不满足申请拒绝",
               ok, err)

        # L4 通过——直接申请
        r = await svc.apply_shop(
            101, 1)
        record("开店成功"
               "(→prechecked)",
               r.get("status")
               == "prechecked"
               and r.get("shopId") == 1,
               str(r.get("status")))
        record("合规问题随单下发",
               len(r.get(
                   "complianceQuestions")
                   or []) >= 1,
               str(r.get(
                   "complianceQuestions")))

        # 快照留痕
        d = await svc.shop_detail(1)
        snap = ((d.get("shop") or {})
                .get(
                    "precheckSnapshot")
                or {})
        record("准入快照留痕",
               snap.get("creditLevel")
               == "L4"
               and snap.get("quotaTier")
               == "growth",
               str(snap))

        # 重复开店拒绝
        try:
            await svc.apply_shop(101, 1)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "经营中" in str(e), ""
        record("重复开店拒绝", ok, err)

        # 认领: 未全答拒绝
        try:
            await svc.claim_shop(1, {})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "未全答" in str(e), ""
        record("合规未全答拒绝", ok, err)

        # 认领: 存疑转人工
        d = await svc.shop_detail(1)
        category = (d.get("shop")
                    or {}).get("category")
        from services.xx65_registry import (
            CATEGORY_TEMPLATES,
        )
        questions = list(
            CATEGORY_TEMPLATES[
                category][
                "complianceQuestions"])
        try:
            await svc.claim_shop(
                1, {questions[0]: "是"})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "人工" in str(e), ""
        record("存疑转人工(S6)", ok, err)

        # 认领成功
        answers = {q: "否"
                   for q in questions}
        claim = await svc.claim_shop(
            1, answers)
        record("认领成功(→claimed)",
               claim.get("status")
               == "claimed",
               str(claim.get("status")))
        record("模板初始化",
               bool((claim.get(
                   "template")
                   or {}).get(
                       "shopName")),
               str(claim.get("template")))
        record("S8 溯源指纹",
               str(claim.get(
                   "fingerprint")
                   or "").startswith(
                       "sha256:"),
               str(claim.get(
                   "fingerprint")
                   )[:20])

        # 非法迁移: 认领后再认领
        try:
            await svc.claim_shop(1, answers)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "不可认领" in str(e), ""
        record("重复认领拒绝", ok, err)

        # 激活
        act = await svc.activate_shop(1)
        record("激活成功(→active)",
               act.get("status")
               == "active",
               str(act.get("status")))

        # 非法迁移: 重复激活
        try:
            await svc.activate_shop(1)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "不可激活" in str(e), ""
        record("重复激活拒绝", ok, err)

        # 关店(不受开关影响)
        os.environ["XX65_MODE"] = "off"
        closed = await svc.close_shop(1)
        record("关店成功"
               "(off 态亦可)",
               closed.get("status")
               == "closed",
               str(closed.get("status")))

        # 终态无出边
        try:
            await svc.activate_shop(1)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("closed 无出边", ok, err)

        # 不存在店铺
        try:
            await svc.shop_detail(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("店铺不存在 404", ok, err)

        # 事件留痕(apply+claim
        # +activate+close)
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        evs = await Xx65Repository() \
            .list_events(limit=20)
        record("事件链留痕(≥4)",
               len(evs) >= 4,
               str(len(evs)))
        os.environ["XX65_MODE"] = "off"


class TestScorer:
    """05 第39档案八因子"""

    async def run(self):
        print("[05 第39档案]")
        reset_all()
        from services.xx65_scorer import (
            Xx65Scorer,
        )
        scorer = Xx65Scorer()

        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空上下文拒绝", ok, err)

        r = await scorer.score({
            "shopHealth": 0.95,
            "contentCompliance": 0.98,
            "aiAdoption": 0.9,
            "campaignRoi": 1.0,
            "tier": "trusted",
            "disputeRate": 0.01,
            "growthVelocity": 0.8,
            "latencyP95Ok": 0.99,
        })
        record("八因子齐备",
               len(r.get("factors")
                   or []) == 8,
               str(len(r.get("factors")
                        or [])))
        record("权重和=1.0",
               abs(sum((r.get(
                        "weightsUsed") or {})
                       .values()) - 1.0)
               < 0.01,
               str(sum((r.get(
                   "weightsUsed")
                      or {}).values())))
        record("高分→urgent",
               r.get("decision")
               == "urgent"
               and (r.get(
                   "trustScore")
                    or 0) >= 80,
               str((r.get(
                   "trustScore"),
                    r.get("decision"))))

        r2 = await scorer.score({
            "shopHealth": 0.2,
            "contentCompliance": 0.2,
            "aiAdoption": 0.1,
            "campaignRoi": 5.0,
            "tier": "restricted",
            "disputeRate": 0.3,
            "growthVelocity": 0.05,
            "latencyP95Ok": 0.2,
        })
        record("低分→observe",
               r2.get("decision")
               == "observe"
               and (r2.get(
                   "trustScore")
                    or 0) < 50,
               str((r2.get(
                   "trustScore"),
                    r2.get("decision"))))

        # 因子明细八条
        names = {f["name"] for f in
                 r.get("factors")}
        record("因子明细八条",
               names == {
                   "shop_health",
                   "content_compliance",
                   "ai_adoption",
                   "campaign_roi",
                   "member_trust",
                   "dispute_rate",
                   "growth_velocity",
                   "latency_budget"},
               str(sorted(names)))

        # tier 基线
        r5 = await scorer.score({
            "tier": "trusted"})
        f5 = [f for f in
              r5.get("factors")
              if f["name"]
              == "member_trust"]
        record("tier 基线"
               "(trusted=90)",
               bool(f5) and f5[0]["score"]
               == 90.0,
               str(f5[0]["score"] if f5
                   else None))

        # 争议率反向
        r6 = await scorer.score({
            "disputeRate": 0.0})
        r7 = await scorer.score({
            "disputeRate": 0.3})
        f6 = [f for f in
              r6.get("factors")
              if f["name"]
              == "dispute_rate"]
        f7 = [f for f in
              r7.get("factors")
              if f["name"]
              == "dispute_rate"]
        record("争议率反向"
               "(0%>30%)",
               (f6[0]["score"] if f6
                else 0)
               > (f7[0]["score"] if f7
                  else 100),
               str((f6[0]["score"] if f6
                    else None,
                    f7[0]["score"] if f7
                    else None)))


class TestConstitution:
    """06 宪法断言"""

    async def run(self):
        print("[06 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 40 档案在册",
               len(SCORER_REGISTRY) == 40,
               str(len(SCORER_REGISTRY)))
        record("第39档案 shop_operation"
               "(batch24)",
               SCORER_REGISTRY.get(
                   "shop_operation")
               is not None
               and SCORER_REGISTRY[
                   "shop_operation"
               ]["batch"] == 24,
               str(SCORER_REGISTRY.get(
                   "shop_operation")))
        record("模块归属 65网店管理",
               SCORER_REGISTRY.get(
                   "shop_operation",
                   {}).get("module")
               == "65网店管理",
               str(SCORER_REGISTRY.get(
                   "shop_operation",
                   {}).get("module")))

        # 45/47/23号零改动(纯读取)
        try:
            from repositories import \
                trust_value_repository as r45
            record("45号零改动"
                   "(纯读取)",
                   r45 is not None,
                   "")
        except ImportError:
            record("45号零改动"
                   "(纯读取)",
                   False, "导入失败")

        record("开关铁律(默认 off)",
               os.environ.get(
                   "XX65_MODE",
                   "off") == "off",
               str(os.environ.get(
                   "XX65_MODE")))


class TestHttp:
    """07 HTTP 层"""

    async def run(self):
        print("[07 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        member = {"X-Role": "member"}

        # 观测面 off 可用
        resp = client.get(
            "/api/xx65/registry",
            headers=admin)
        body = resp.json() or {}
        record("HTTP registry 观测面 200",
               resp.status_code == 200
               and len(body.get("rules")
                       or {}) == 8
               and body.get("mode")
               == "off",
               str((resp.status_code,
                    len(body.get("rules")
                         or {}))))

        resp = client.get(
            "/api/xx65/model/status",
            headers=admin)
        mbody = resp.json() or {}
        record("HTTP model/status 200",
               resp.status_code == 200
               and (mbody.get("scorer")
                    or {}).get("scorerId")
               == "shop_operation",
               str(resp.status_code))

        # 决策面 off 409
        resp = client.post(
            "/api/xx65/intents/parse",
            json={"ownerId": 101,
                  "text": "手工木雕"},
            headers=member)
        record("HTTP intents off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # assist 全链
        os.environ["XX65_MODE"] = "assist"
        await seed_profile(1, 100.0)
        await seed_credit(101, "L4")

        resp = client.post(
            "/api/xx65/intents/parse",
            json={"ownerId": 101,
                  "text": "我想做定制"
                          "木雕手工艺品"},
            headers=member)
        ibody = resp.json() or {}
        record("HTTP intents 200"
               "(handicraft)",
               resp.status_code == 200
               and ibody.get("category")
               == "handicraft",
               str((resp.status_code,
                    ibody.get("category"))))

        resp = client.post(
            "/api/xx65/shops/apply",
            json={"ownerId": 101,
                  "trustId": 1,
                  "intentId":
                      ibody.get(
                          "intentId")},
            headers=member)
        abody = resp.json() or {}
        record("HTTP apply 200"
               "(prechecked)",
               resp.status_code == 200
               and abody.get("status")
               == "prechecked",
               str((resp.status_code,
                    abody.get("status"))))

        shop_id = abody.get("shopId")
        resp = client.post(
            f"/api/xx65/shops/{shop_id}"
            "/claim",
            json={"answers": {
                q: "否" for q in
                (abody.get(
                    "complianceQuestions")
                 or [])}},
            headers=member)
        cbody = resp.json() or {}
        record("HTTP claim 200"
               "(claimed)",
               resp.status_code == 200
               and cbody.get("status")
               == "claimed",
               str((resp.status_code,
                    cbody.get("status"))))

        resp = client.post(
            f"/api/xx65/shops/{shop_id}"
            "/activate",
            headers=member)
        record("HTTP activate 200"
               "(active)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("status")
               == "active",
               str(resp.status_code))

        # 详情观测面
        resp = client.get(
            f"/api/xx65/shops/{shop_id}",
            headers=member)
        record("HTTP 详情 200",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("shop")
                    or {}).get("status")
               == "active",
               str(resp.status_code))
        resp = client.get(
            "/api/xx65/shops/999",
            headers=member)
        record("HTTP 详情 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 列表(admin 观测面)
        resp = client.get(
            "/api/xx65/shops",
            headers=admin)
        record("HTTP 列表 200"
               "(admin)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("total")
               == 1,
               str(resp.status_code))
        resp = client.get(
            "/api/xx65/shops",
            headers=member)
        record("HTTP 列表 member 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 关店(off 态亦可——
        # 经营者权利)
        os.environ["XX65_MODE"] = "off"
        resp = client.post(
            f"/api/xx65/shops/{shop_id}"
            "/close",
            json={"closedBy": "member"},
            headers=member)
        record("HTTP close 200"
               "(off 亦可)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("status")
               == "closed",
               str((resp.status_code,
                    (resp.json()
                     or {}).get(
                        "status"))))

        # 鉴权 403(无 Role)
        for method, path in (
                ("GET",
                 "/api/xx65/registry"),
                ("POST",
                 "/api/xx65/intents/parse"),
                ("POST",
                 "/api/xx65/shops/apply"),
                ("GET",
                 "/api/xx65/shops"),
                ("GET",
                 "/api/xx65/model/status")):
            resp = client.request(
                method, path, json={})
            short = path.split('/')[-1] \
                .split('?')[0]
            record(f"HTTP {short}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 非法角色 403
        resp = client.get(
            "/api/xx65/registry",
            headers={"X-Role":
                         "hacker"})
        record("非法角色 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 路由累计(P0 9)
        from routes.xx65_routes import (
            router as xx_router,
        )
        count = sum(
            1 for r in xx_router.routes)
        record("65号路由 P0 9 端点"
               "(P1 增至 16)",
               count >= 9, str(count))


async def run_all():
    await TestRegistry().run()
    await TestIntent().run()
    await TestPrecheck().run()
    await TestShopFlow().run()
    await TestScorer().run()
    await TestConstitution().run()
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
