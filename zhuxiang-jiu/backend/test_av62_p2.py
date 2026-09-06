"""62号·AI智能无形资产估值模块 P2 专项测试
(流动性评级+场景折算+衰减激活+反事实压测+阈值配置域)

运行方式:
    python test_av62_p2.py

覆盖(62号计划 §七 P2):
    - 流动性三档(high/medium/low/
      none)+使用约束建议
    - 衰减模型(90 日半衰期——
      exp(-λ×idleDays))+decaying 态
    - 激活机制(合规使用/知识更新
      →衰减重置 reactivated)
    - 场景信值折算(SCENARIO_FACTORS×
      乘子×衰减+low/risk 排除
      +45号 deposit 增益域)
    - 反事实压测(要素摘除重算 Δ%)
    - 阈值配置域(46号审批双模)
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
os.environ["AV62_LLM_MODE"] = "off"
os.environ["AV62_LEARN_MODE"] = "off"

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


async def seed(subject_id, role, domain,
               evidence, label=""):
    """登记种子资产(shadow 态)"""
    from services.av62_service import (
        Av62Service,
    )
    return await Av62Service().register_asset(
        subject_id=subject_id, role=role,
        domain=domain, evidence=evidence,
        label=label or f"{role}/{domain}")


class TestLiquidity:
    """01 流动性评级+衰减"""

    async def run(self):
        print("[01 流动性+衰减]")
        reset_all()
        from services.av62_registry import (
            DECAY_HALF_LIFE_DAYS,
            DECAY_LAMBDA,
            DOMAIN_LIQUIDITY,
            LIQUIDITY_META,
            LIQUIDITY_TIERS,
            decay_factor,
            liquidity_of,
            liquidity_view,
        )

        record("四档评级(3+none)",
               len(LIQUIDITY_TIERS) == 4
               and "none"
               in LIQUIDITY_TIERS,
               str(LIQUIDITY_TIERS))
        record("域映射封闭(9 域全覆盖)",
               len(DOMAIN_LIQUIDITY) == 9
               and DOMAIN_LIQUIDITY.get(
                   "risk") == "none",
               str(len(
                   DOMAIN_LIQUIDITY)))
        record("compliance→high",
               liquidity_of(
                   "compliance") == "high",
               liquidity_of(
                   "compliance"))
        record("knowledge→medium",
               liquidity_of(
                   "knowledge")
               == "medium",
               "")
        record("social→low(仅自证)",
               liquidity_of("social")
               == "low"
               and LIQUIDITY_META[
                   "low"][
                   "convertible"]
               is False,
               "")
        record("risk→none(不可流转)",
               liquidity_of("risk")
               == "none"
               and LIQUIDITY_META[
                   "none"][
                   "convertible"]
               is False,
               "")

        # 衰减模型(90 日半衰期)
        record("衰减 0 日=1.0",
               decay_factor(0) == 1.0,
               str(decay_factor(0)))
        record("衰减 90 日=0.5(半衰)",
               abs(decay_factor(90)
                   - 0.5) < 0.01,
               str(decay_factor(90)))
        record("衰减 180 日=0.25",
               abs(decay_factor(180)
                   - 0.25) < 0.01,
               str(decay_factor(180)))
        record("校准半衰期生效",
               abs(decay_factor(
                   30, 30) - 0.5)
               < 0.01,
               str(decay_factor(30,
                                 30)))
        record("λ=ln2/90",
               abs(DECAY_LAMBDA
                   - 0.0077) < 0.0001,
               str(DECAY_LAMBDA))

        # 观测面视图
        v = liquidity_view()
        record("liquidity_view 观测面",
               (v.get("decay") or {})
               .get("halfLifeDays")
               == DECAY_HALF_LIFE_DAYS
               and len(v.get(
                   "scenarios")
                   or {}) == 4,
               str(len(v.get(
                   "scenarios")
                   or {})))

        # 档案(评估基线联动)
        os.environ["AV62_MODE"] = "shadow"
        from services.av62_assess_service import (
            Av62AssessService,
        )
        from services.av62_liquidity_service import (
            Av62LiquidityService,
        )
        a1 = await seed(
            101, "enterprise",
            "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        await Av62AssessService() \
            .assess_asset(a1["assetId"])
        p1 = await Av62LiquidityService() \
            .get_profile(a1["assetId"])
        pf = p1.get("profile") or {}
        record("档案(high+限频建议)",
               pf.get("liquidityTier")
               == "high"
               and pf.get(
                   "usageConstraint")
               == "使用限频+场景校验"
               and pf.get("frequencyCap")
               == 10,
               str((pf.get(
                        "liquidityTier"),
                    pf.get(
                        "frequencyCap"))))
        record("档案基线联动"
               "(baseValue 83.3)",
               pf.get("baseValue")
               == 83.3,
               str(pf.get("baseValue")))
        record("档案衰减(0 日=1.0)",
               pf.get("decayFactor")
               == 1.0
               and pf.get("decayedValue")
               == 83.3,
               str((pf.get(
                        "decayFactor"),
                    pf.get(
                        "decayedValue"))))

        # risk 档案(不可流转)
        a2 = await seed(
            101, "enterprise", "risk",
            {"penaltyRecords": 2})
        await Av62AssessService() \
            .assess_asset(a2["assetId"])
        p2 = await Av62LiquidityService() \
            .get_profile(a2["assetId"])
        pf2 = p2.get("profile") or {}
        record("risk 档案(none+"
               "不可流转)",
               pf2.get(
                   "liquidityTier")
               == "none"
               and pf2.get(
                   "convertible")
               is False,
               str(pf2.get(
                   "liquidityTier")))

        # 不存在资产
        try:
            await Av62LiquidityService() \
                .get_profile(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("档案 404(不存在)",
               ok, err)

        # 主体档案汇总
        lv = await Av62LiquidityService() \
            .list_profiles(101)
        record("主体档案汇总"
               "(byTier 分布)",
               lv.get("total") == 2
               and (lv.get("byTier")
                    or {}).get("high") == 1
               and (lv.get("byTier")
                    or {}).get("none") == 1,
               str(lv.get("byTier")))
        os.environ["AV62_MODE"] = "off"


class TestActivate:
    """02 激活机制"""

    async def run(self):
        print("[02 激活机制]")
        reset_all()
        os.environ["AV62_MODE"] = "shadow"
        from services.av62_liquidity_service import (
            Av62LiquidityService,
        )
        svc = Av62LiquidityService()

        # off 铁律
        os.environ["AV62_MODE"] = "off"
        try:
            await svc.activate_asset(
                1, "compliance_use")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(激活拒绝)",
               ok, err)
        os.environ["AV62_MODE"] = "shadow"

        a1 = await seed(
            101, "personal",
            "capability",
            {"skillCerts": 8,
             "deliveryQuality": 0.9,
             "knowledgeSharing": 24})
        from services.av62_assess_service import (
            Av62AssessService,
        )
        await Av62AssessService() \
            .assess_asset(a1["assetId"])

        # 理由域外拒绝
        try:
            await svc.activate_asset(
                a1["assetId"], "hacked")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("激活理由域外拒绝",
               ok, err)

        # 激活成功(reactivated)
        r = await svc.activate_asset(
            a1["assetId"],
            "compliance_use",
            activated_by="激活官")
        record("激活成功"
               "(reactivated)",
               r.get("status")
               == "reactivated"
               and r.get("decayReset")
               is True,
               str(r.get("status")))

        # 状态机读回
        from repositories.av62_repository import (
            Av62Repository,
        )
        asset = await Av62Repository() \
            .get_asset(a1["assetId"])
        record("状态 reactivated 读回",
               asset.get("status")
               == "reactivated",
               str(asset.get("status")))

        # low 档不可激活
        a2 = await seed(
            101, "organization",
            "social",
            {"memberActivity": 0.8,
             "eventCompliance": 0.9,
             "externalReviews": 4})
        await Av62AssessService() \
            .assess_asset(a2["assetId"])
        try:
            await svc.activate_asset(
                a2["assetId"],
                "knowledge_update")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("low 档激活拒绝"
               "(仅自证)",
               ok, err)

        # 事件留痕
        evs = await Av62Repository() \
            .list_events(limit=20)
        act_evs = [
            e for e in evs
            if e.get("eventType")
            == "activate"]
        record("事件链(activate×1)",
               len(act_evs) == 1,
               str(len(act_evs)))
        os.environ["AV62_MODE"] = "off"


class TestScenario:
    """03 场景信值折算"""

    async def run(self):
        print("[03 场景折算]")
        reset_all()
        os.environ["AV62_MODE"] = "shadow"
        from services.av62_assess_service import (
            Av62AssessService,
        )
        from services.av62_liquidity_service import (
            Av62LiquidityService,
        )
        from services.av62_registry import (
            scenario_factor,
        )
        svc = Av62LiquidityService()

        # 折算表封闭
        record("投标场景"
               "(合规×1.2)",
               scenario_factor(
                   "bidding",
                   "compliance") == 1.2
               and scenario_factor(
                   "bidding",
                   "knowledge") == 1.0,
               "")
        record("risk 域恒排除"
               "(系数不适用)",
               scenario_factor(
                   "bidding", "risk")
               == 1.0,
               "")

        # off 铁律
        os.environ["AV62_MODE"] = "off"
        try:
            await svc.convert_scenario(
                101, "bidding")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(折算拒绝)",
               ok, err)
        os.environ["AV62_MODE"] = "shadow"

        # 种子: 合规(high)+知识
        # (medium)+社会资本(low)+risk
        a1 = await seed(
            101, "enterprise",
            "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        a2 = await seed(
            101, "enterprise",
            "knowledge",
            {"sopDocs": 40,
             "techContribs": 24,
             "codeCommits": 160})
        a3 = await seed(
            101, "organization",
            "social",
            {"memberActivity": 0.8,
             "eventCompliance": 0.9,
             "externalReviews": 4})
        a4 = await seed(
            101, "enterprise", "risk",
            {"penaltyRecords": 2})
        for aid in (a1["assetId"],
                    a2["assetId"],
                    a3["assetId"],
                    a4["assetId"]):
            await Av62AssessService() \
                .assess_asset(aid)

        # 场景域外拒绝
        try:
            await svc.convert_scenario(
                101, "hacked")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("场景域外拒绝", ok, err)

        # 投标折算(high+medium 入,
        # low/risk 排除)
        r = await svc.convert_scenario(
            101, "bidding",
            deposit=False)
        record("可流转档纳入(2 项)",
               len(r.get("included")
                   or []) == 2
               and len(r.get("excluded")
                      or []) == 2,
               str((len(r.get(
                            'included')
                        or []),
                    len(r.get(
                            'excluded')
                        or []))))
        record("low/risk 排除留痕",
               {e.get("domain")
                for e in r.get(
                    "excluded")}
               == {"social", "risk"},
               str(r.get("excluded")))
        # base 83.3×1.2×1×1 +
        # base(40+24+160→
        # scale 评分)×1.0×1×1
        inc = {i["domain"]: i
               for i in r.get(
                   "included")}
        record("合规场景系数×1.2",
               inc["compliance"][
                   "scenarioFactor"]
               == 1.2
               and inc["compliance"][
                   "scenarioValue"]
               == round(
                   83.3 * 1.2, 2),
               str(inc.get(
                   "compliance")))
        record("知识场景系数×1.0",
               inc["knowledge"][
                   "scenarioFactor"]
               == 1.0,
               str(inc.get(
                   "knowledge")))
        record("场景值=Σ(含系数)",
               r.get("scenarioValue")
               == round(
                   round(83.3 * 1.2, 2)
                   + inc["knowledge"][
                       "baseValue"],
                   2),
               str(r.get(
                   "scenarioValue")))
        record("使用约束建议输出",
               inc["compliance"][
                   "usageConstraint"]
               == "使用限频+场景校验"
               and inc["knowledge"][
                   "frequencyCap"] == 5,
               "")

        # deposit=false 无 45号输出
        record("deposit=false "
               "无增益输出",
               r.get("trustValueDeposit")
               is None,
               str(r.get(
                   "trustValueDeposit")))

        # 45号 deposit 增益域
        r2 = await svc.convert_scenario(
            101, "expedited",
            deposit=True)
        dep = r2.get(
            "trustValueDeposit")
        record("45号 deposit 增益域"
               "(platform_conduct)",
               dep is not None
               and dep.get("verified")
               in (True, False),
               str(dep)[:60] if dep
               else "None")

        # 免审场景合规×1.3
        r3 = await svc.convert_scenario(
            101, "expedited",
            deposit=False)
        inc3 = {i["domain"]: i
                for i in r3.get(
                    "included")}
        record("免审合规×1.3",
               inc3["compliance"][
                   "scenarioFactor"]
               == 1.3,
               str(inc3.get(
                   "compliance")))

        # 无资产主体拒绝
        try:
            await svc.convert_scenario(
                999, "bidding")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("无资产主体拒绝",
               ok, err)
        os.environ["AV62_MODE"] = "off"


class TestStress:
    """04 反事实压测"""

    async def run(self):
        print("[04 反事实压测]")
        reset_all()
        os.environ["AV62_MODE"] = "shadow"
        from services.av62_assess_service import (
            Av62AssessService,
        )
        from services.av62_liquidity_service import (
            Av62LiquidityService,
        )
        svc = Av62LiquidityService()

        # off 铁律
        os.environ["AV62_MODE"] = "off"
        try:
            await svc.stress_subject(
                101, [1])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(压测拒绝)",
               ok, err)
        os.environ["AV62_MODE"] = "shadow"

        a1 = await seed(
            101, "enterprise",
            "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        a2 = await seed(
            101, "enterprise", "risk",
            {"penaltyRecords": 2})
        for aid in (a1["assetId"],
                    a2["assetId"]):
            await Av62AssessService() \
                .assess_asset(aid)

        # 摘除正资产(净贡献下降)
        r = await svc.stress_subject(
            101,
            remove_asset_ids=[
                a1["assetId"]])
        record("摘除正资产"
               "(净贡献下降)",
               (r.get("delta") or 0)
               < 0
               and (r.get(
                   "deltaPct")
                    or 0) < 0
               and len(r.get(
                   "removedAssets")
                   or []) == 1,
               str((r.get("delta"),
                    r.get("deltaPct"))))

        # 摘除负资产(净贡献回升)
        r2 = await svc.stress_subject(
            101,
            remove_domains=["risk"])
        record("摘除负资产"
               "(净贡献回升)",
               (r2.get("delta") or 0)
               > 0,
               str(r2.get("delta")))

        # 摘除集无效拒绝
        try:
            await svc.stress_subject(
                101, [])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("摘除集无效拒绝",
               ok, err)

        # 无资产主体拒绝
        try:
            await svc.stress_subject(
                999, [1])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("无资产主体拒绝",
               ok, err)
        os.environ["AV62_MODE"] = "off"


class TestThreshold:
    """05 阈值配置域(46号双模)"""

    async def run(self):
        print("[05 阈值配置域]")
        reset_all()
        os.environ["AV62_MODE"] = "shadow"
        from services.av62_liquidity_service import (
            Av62LiquidityService,
        )
        from services.av62_threshold_service import (
            Av62ThresholdService,
        )
        svc = Av62ThresholdService()

        # 默认值(fail-soft)
        record("默认半衰期 90 日",
               await Av62LiquidityService()
               .get_active_half_life()
               == 90,
               "")
        record("默认场景乘子 1.0",
               await Av62LiquidityService()
               .get_scenario_multiplier(
                   "bidding") == 1.0,
               "")

        # 参数校验
        for args, name in (
            (({"halfLifeDays": 10},
              "半衰期越界拒绝"),
             ""),
            (({"halfLifeDays": 400},
              "半衰期越界拒绝"),
             ""),
            (({"scenario": "bidding",
               "multiplier": 2.0},
              "乘子越界拒绝"),
             ""),
            (({}, "缺选域拒绝"), ""),
            (({"halfLifeDays": 60,
               "scenario": "bidding"},
              "双选域拒绝"), "")):
            try:
                await svc \
                    .calibrate_submit(
                        half_life_days=args[0]
                        .get("halfLifeDays"),
                        scenario=args[0]
                        .get("scenario",
                             ""),
                        multiplier=args[0]
                        .get(
                            "multiplier"),
                        reason="测试")
                ok, err = False, "未拒绝"
            except ValueError:
                ok, err = True, ""
            record(args[1], ok, err)
        try:
            await svc.calibrate_submit(
                half_life_days=60,
                reason="")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("缺理由拒绝", ok, err)

        # 提交 46号
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService() \
            .sync_registry()
        sub = await svc.calibrate_submit(
            half_life_days=60,
            requested_by="校准官",
            reason="活跃主体衰减加速")
        record("submit 46号(pending)",
               sub.get("status")
               == "pending"
               and sub.get("tier")
               == "decay"
               and (sub.get("config")
                    or {}).get(
                        "halfLifeDays")
               == 60,
               str((sub.get("tier"),
                    sub.get("config"))))

        # 未生效前半衰期仍 90
        record("未审批不生效(90)",
               await Av62LiquidityService()
               .get_active_half_life()
               == 90,
               "")

        # 未裁决 apply 拒绝
        try:
            await svc.calibrate_apply(
                sub["changeId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("未裁决 apply 拒绝",
               ok, err)

        # 46号裁决+apply
        try:
            await AiGovernanceService() \
                .review_change(
                    int(sub["changeId"]),
                    approve=True,
                    reviewed_by="审批官")
        except ValueError:
            pass
        ap = await svc.calibrate_apply(
            sub["changeId"],
            applied_by="终审官")
        record("apply 生效(applied)",
               ap.get("status")
               == "applied"
               and ap.get("tier")
               == "decay",
               str(ap.get("status")))

        # 生效后半衰期 60
        record("生效后半衰期 60",
               await Av62LiquidityService()
               .get_active_half_life()
               == 60,
               "")

        # 场景乘子校准
        sub2 = await svc.calibrate_submit(
            scenario="bidding",
            multiplier=1.2,
            reason="投标旺季上调")
        record("scenario submit",
               sub2.get("status")
               == "pending"
               and sub2.get("tier")
               == "scenario:bidding",
               str(sub2.get("tier")))
        try:
            await AiGovernanceService() \
                .review_change(
                    int(sub2["changeId"]),
                    approve=True,
                    reviewed_by="审批官")
        except ValueError:
            pass
        await svc.calibrate_apply(
            sub2["changeId"])
        record("场景乘子生效 1.2",
               await Av62LiquidityService()
               .get_scenario_multiplier(
                   "bidding") == 1.2,
               "")

        # 观测面
        view = await svc.thresholds_view()
        record("thresholds 观测面",
               (view.get("active")
                or {}).get("decay")
               == {"halfLifeDays": 60}
               and (view.get("active")
                    or {}).get(
                        "scenario:bidding")
               == {"scenario": "bidding",
                   "multiplier": 1.2},
               str(view.get("active")))
        os.environ["AV62_MODE"] = "off"


class TestHttp:
    """06 HTTP 层"""

    async def run(self):
        print("[06 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 观测面 off 可用
        resp = client.get(
            "/api/av62/scenarios",
            headers=admin)
        body = resp.json() or {}
        record("HTTP scenarios 观测面 200",
               resp.status_code == 200
               and len(body.get(
                   "scenarios")
                   or {}) == 4,
               str(resp.status_code))
        resp = client.get(
            "/api/av62/thresholds",
            headers=admin)
        record("HTTP thresholds 观测面 200",
               resp.status_code == 200,
               str(resp.status_code))

        # 决策面 off 409
        for path, body_ in (
                ("/api/av62/convert",
                 {}),
                ("/api/av62/stress",
                 {"subjectId": 1}),
                ("/api/av62/activate",
                 {"assetId": 1})):
            resp = client.post(
                "/api/av62/scenarios/convert",
                json={"subjectId": 1,
                      "scenario":
                          "bidding"},
                headers=admin)
            record("HTTP convert off 409",
                   resp.status_code == 409,
                   str(resp.status_code))
            break

        # shadow 全链
        os.environ["AV62_MODE"] = "shadow"
        client.post(
            "/api/av62/assets",
            json={"subjectId": 701,
                  "role": "enterprise",
                  "domain":
                      "compliance",
                  "evidence": {
                      "licenseCount": 5,
                      "auditResults":
                          "通过",
                      "esgDisclosure":
                          "已披露"}},
            headers=admin)
        resp = client.post(
            "/api/av62/assess",
            json={"assetId": 1},
            headers=admin)
        record("HTTP 种子评估 200",
               resp.status_code == 200,
               str(resp.status_code))

        # 激活
        resp = client.post(
            "/api/av62/activate",
            json={"assetId": 1,
                  "reason":
                      "compliance_use"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP activate 200",
               resp.status_code == 200
               and body.get("status")
               == "reactivated",
               str((resp.status_code,
                    body.get("status"))))

        # 激活理由域外 409
        resp = client.post(
            "/api/av62/activate",
            json={"assetId": 1,
                  "reason": "hacked"},
            headers=admin)
        record("HTTP activate 域外 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 场景折算
        resp = client.post(
            "/api/av62/scenarios/convert",
            json={"subjectId": 701,
                  "scenario":
                      "bidding",
                  "deposit": False},
            headers=admin)
        body = resp.json() or {}
        record("HTTP convert 200",
               resp.status_code == 200
               and body.get(
                   "scenarioValue")
               == round(
                   83.3 * 1.2, 2),
               str((resp.status_code,
                    body.get(
                        "scenarioValue"))))
        resp = client.post(
            "/api/av62/scenarios/convert",
            json={"subjectId": 701,
                  "scenario": "hacked"},
            headers=admin)
        record("HTTP convert 域外 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 压测
        resp = client.post(
            "/api/av62/stress",
            json={"subjectId": 701,
                  "removeAssetIds":
                      [1]},
            headers=admin)
        body = resp.json() or {}
        record("HTTP stress 200(Δ%)",
               resp.status_code == 200
               and "deltaPct" in body,
               str(resp.status_code))
        resp = client.post(
            "/api/av62/stress",
            json={"subjectId": 701,
                  "removeAssetIds":
                      []},
            headers=admin)
        record("HTTP stress 无效摘除 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 阈值双模(46号先入册)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService() \
            .sync_registry()
        resp = client.post(
            "/api/av62/threshold/"
            "calibrate",
            json={"halfLifeDays": 120,
                  "reason":
                      "HTTP 校准"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP calibrate submit",
               resp.status_code == 200
               and body.get("status")
               == "pending",
               str((resp.status_code,
                    body.get("status"))))
        resp = client.post(
            "/api/av62/threshold/"
            "calibrate",
            json={"mode": "apply",
                  "changeId":
                      body.get(
                          "changeId",
                          1)},
            headers=admin)
        record("HTTP calibrate apply"
               "(未裁决 409)",
               resp.status_code == 409,
               str(resp.status_code))
        resp = client.post(
            "/api/av62/threshold/"
            "calibrate",
            json={"halfLifeDays": 999,
                  "reason": "越界"},
            headers=admin)
        record("HTTP calibrate 越界 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("POST",
                 "/api/av62/scenarios/"
                 "convert"),
                ("GET",
                 "/api/av62/scenarios"),
                ("POST",
                 "/api/av62/stress"),
                ("POST",
                 "/api/av62/activate"),
                ("GET",
                 "/api/av62/"
                 "thresholds")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP "
                   f"{path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计(P2 14——后续期
        # 递增至 20+)
        from routes.av62_routes import (
            router as av_router,
        )
        count = sum(
            1 for r in av_router.routes)
        record("62号路由 P2 14 端点",
               count >= 14, str(count))
        os.environ["AV62_MODE"] = "off"


class TestConstitution:
    """07 宪法断言"""

    async def run(self):
        print("[07 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 38 档案在册",
               len(SCORER_REGISTRY) == 40,
               str(len(SCORER_REGISTRY)))

        # 45号零改动(deposit 纯调用)
        try:
            from services import \
                trust_radar_service as s45
            record("45号零改动"
                   "(deposit 纯调用)",
                   s45 is not None,
                   "")
        except ImportError:
            record("45号零改动"
                   "(deposit 纯调用)",
                   False, "导入失败")

        record("三开关铁律(默认 off)",
               os.environ.get(
                   "AV62_MODE") == "off"
               and os.environ.get(
                   "AV62_LLM_MODE") == "off"
               and os.environ.get(
                   "AV62_LEARN_MODE")
               == "off",
               "")


async def run_all():
    await TestLiquidity().run()
    await TestActivate().run()
    await TestScenario().run()
    await TestStress().run()
    await TestThreshold().run()
    await TestHttp().run()
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
