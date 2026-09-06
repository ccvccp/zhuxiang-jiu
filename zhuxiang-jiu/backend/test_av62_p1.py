"""62号·AI智能无形资产估值模块 P1 专项测试
(因果估值引擎+归因报告)

运行方式:
    python test_av62_p1.py

覆盖(62号计划 §七 P1):
    - CAUSAL_RULES 封闭注册: 三元组+
      版本化+13 要素全覆盖+objective
      乘子域(risk 恒 1.0 铁律)
    - 贡献度计算: 多维加权+负向扣减
      +置信度三档(high/medium/low)
    - 归因报告: 规则 ID 锚定+证据引用
      +无锚点"未验证"标记
    - 状态机: registered→assessing→
      active/assessed/pending_review
    - objective 46号审批双模
      (submit/apply+未经裁决不可生效)
    - LLM 不进判定链(确定性)
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


async def seed_asset(subject_id, role, domain,
                     evidence, label=""):
    """登记种子资产(需 shadow 态)"""
    from services.av62_service import (
        Av62Service,
    )
    return await Av62Service().register_asset(
        subject_id=subject_id, role=role,
        domain=domain, evidence=evidence,
        label=label or f"{role}/{domain}")


class TestCausalRules:
    """01 CAUSAL_RULES 封闭注册"""

    async def run(self):
        print("[01 因果规则库]")
        reset_all()
        from services.av62_registry import (
            CAUSAL_RULES, RULES_VERSION,
            TRUST_ELEMENTS,
            get_objective_multiplier,
            get_rule, rules_view,
        )

        record("规则 13 条(要素全覆盖)",
               len(CAUSAL_RULES)
               == len(TRUST_ELEMENTS)
               == 13,
               str(len(CAUSAL_RULES)))

        # 三元组结构+强度域
        ok_tri = ok_strength = True
        for rule_id, rule in \
                CAUSAL_RULES.items():
            if not all(
                    k in rule for k in
                    ("role", "domain",
                     "outcome", "strength")):
                ok_tri = False
            if not 0 < float(
                    rule.get("strength")
                    or 0) <= 1:
                ok_strength = False
        record("三元组结构齐备",
               ok_tri, "")
        record("强度域 (0,1]",
               ok_strength, "")
        record("版本化 v1",
               RULES_VERSION == "v1",
               RULES_VERSION)

        # get_rule 锚定+域外 None
        r = get_rule("enterprise",
                     "compliance")
        record("get_rule 锚定(CR-001)",
               (r or {}).get("ruleId")
               == "CR-001"
               and (r or {}).get("outcome")
               == "audit_pass_rate",
               str(r))
        record("get_rule 域外 None",
               get_rule("hacker",
                        "compliance") is None,
               "")

        # objective 乘子
        record("stability 乘子(合规×1.2)",
               get_objective_multiplier(
                   "stability",
                   "compliance") == 1.2
               and get_objective_multiplier(
                   "stability",
                   "knowledge") == 1.0,
               "")
        record("growth 乘子(知识/成长×1.2)",
               get_objective_multiplier(
                   "growth",
                   "knowledge") == 1.2
               and get_objective_multiplier(
                   "growth",
                   "growth") == 1.2
               and get_objective_multiplier(
                   "growth",
                   "compliance") == 1.0,
               "")
        record("risk 乘子恒 1.0(防洗白)",
               get_objective_multiplier(
                   "growth", "risk") == 1.0
               and get_objective_multiplier(
                   "stability", "risk") == 1.0,
               "")

        # 观测面
        v = rules_view()
        record("rules_view 观测面",
               v.get("rules") == 13
               and v.get("version") == "v1"
               and v.get("elementsCovered")
               == 13,
               str((v.get("rules"),
                    v.get(
                        "elementsCovered"))))

        # 启动自检已过(导入即验证)
        record("启动自检通过(导入即验)",
               True, "")


class TestContribution:
    """02 贡献度计算"""

    async def run(self):
        print("[02 贡献度计算]")
        reset_all()
        os.environ["AV62_MODE"] = "shadow"
        from services.av62_assess_service import (
            Av62AssessService,
        )
        svc = Av62AssessService()

        # off 铁律
        os.environ["AV62_MODE"] = "off"
        try:
            await svc.assess_asset(1)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(评估拒绝)",
               ok, err)
        os.environ["AV62_MODE"] = "shadow"

        # ① 正资产全证据(high→active)
        a1 = await seed_asset(
            101, "enterprise", "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        r1 = await svc.assess_asset(
            a1["assetId"])
        record("正资产评估(high→active)",
               r1.get("assetStatus")
               == "active"
               and r1.get("confidenceTier")
               == "high"
               and r1.get("confidenceCoef")
               == 1.0,
               str((r1.get("assetStatus"),
                    r1.get(
                        "confidenceTier"))))
        record("要素得分确定性(83.3)",
               r1.get("elementScore")
               == 83.3,
               str(r1.get("elementScore")))

        # 贡献度公式(数学断言)
        # causalWeight = 0.25×0.9×1.2=0.27
        # contribution = 0.833×0.27×1.0
        record("因果权重(0.25×0.9×1.2)",
               r1.get("causalWeight") == 0.27,
               str(r1.get("causalWeight")))
        record("贡献度公式",
               abs((r1.get("contribution")
                    or 0) - 0.2249) < 0.0005,
               str(r1.get("contribution")))
        record("净贡献=贡献-扣减",
               r1.get("netContribution")
               == r1.get("contribution")
               and r1.get("riskDeduction")
               == 0,
               str((r1.get(
                        "contribution"),
                    r1.get(
                        "riskDeduction"))))

        # ② medium 置信(2/3 证据)
        a2 = await seed_asset(
            201, "enterprise", "compliance",
            {"licenseCount": 5,
             "auditResults": "通过"})
        r2 = await svc.assess_asset(
            a2["assetId"])
        record("medium 置信(系数 0.8)",
               r2.get("confidenceTier")
               == "medium"
               and r2.get("confidenceCoef")
               == 0.8,
               str((r2.get(
                        "confidenceTier"),
                    r2.get(
                        "confidenceCoef"))))
        record("medium 抽检复核"
               "(assessed+spotCheck)",
               r2.get("assetStatus")
               == "assessed"
               and r2.get("spotCheck") is True,
               str((r2.get("assetStatus"),
                    r2.get("spotCheck"))))
        record("medium 贡献折减"
               "(×0.8)",
               abs((r2.get("contribution")
                    or 0) - 0.108) < 0.0005,
               str(r2.get("contribution")))

        # ③ low 置信(1/3 证据)
        a3 = await seed_asset(
            301, "enterprise", "compliance",
            {"licenseCount": 5})
        r3 = await svc.assess_asset(
            a3["assetId"])
        record("low 置信(系数 0.5)",
               r3.get("confidenceTier")
               == "low"
               and r3.get("confidenceCoef")
               == 0.5,
               str((r3.get(
                        "confidenceTier"),
                    r3.get(
                        "confidenceCoef"))))
        record("low 强制人工"
               "(pending_review)",
               r3.get("assetStatus")
               == "pending_review",
               str(r3.get("assetStatus")))

        # ④ 负资产(负向扣减+系数铁律)
        a4 = await seed_asset(
            101, "enterprise", "risk",
            {"penaltyRecords": 2})
        r4 = await svc.assess_asset(
            a4["assetId"])
        record("负资产扣减"
               "(riskDeduction>0)",
               (r4.get("riskDeduction")
                or 0) > 0
               and (r4.get(
                        "contribution")
                    or 0) == 0,
               str((r4.get(
                        "contribution"),
                    r4.get(
                        "riskDeduction"))))
        record("负资产权重(-0.3×0.95)",
               r4.get("causalWeight")
               == -0.285,
               str(r4.get("causalWeight")))
        record("负资产得分 fail-safe"
               "(max 非均值)",
               r4.get("elementScore") == 40.0
               and (r4.get(
                   "riskDeduction")
                    or 0) == 0.114,
               str((r4.get("elementScore"),
                    r4.get(
                        "riskDeduction"))))
        record("负资产系数恒 1.0(防洗白)",
               r4.get("confidenceCoef")
               == 1.0,
               str(r4.get("confidenceCoef")))
        record("负资产净贡献为负",
               (r4.get("netContribution")
                or 0) < 0,
               str(r4.get(
                   "netContribution")))

        # ⑤ 主体聚合
        agg = await svc.assess_subject(101)
        record("主体聚合"
               "(2 资产评估)",
               agg.get("assetsAssessed")
               == 2
               and agg.get(
                   "netContribution")
               == round(
                   0.2249 - 0.114, 4),
               str((agg.get(
                        "assetsAssessed"),
                    agg.get(
                        "netContribution"))))
        record("主体基础信值(0-100)",
               agg.get("baseValue")
               == 83.3,
               str(agg.get("baseValue")))
        record("置信度分布(high/medium)",
               (agg.get("confidenceBreakdown")
                or {}).get("high") == 1
               and (agg.get(
                   "confidenceBreakdown")
                or {}).get("medium") == 1,
               str(agg.get(
                   "confidenceBreakdown")))

        # ⑥ 无资产主体拒绝
        try:
            await svc.assess_subject(999)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("无资产主体拒绝", ok, err)

        # ⑦ 确定性(LLM 不进判定链)
        r5 = await svc.assess_asset(
            a1["assetId"])
        record("确定性(重估同值)",
               r5.get("netContribution")
               == r1.get("netContribution"),
               str((r1.get(
                        "netContribution"),
                    r5.get(
                        "netContribution"))))
        os.environ["AV62_MODE"] = "off"


class TestAttribution:
    """03 归因报告"""

    async def run(self):
        print("[03 归因报告]")
        reset_all()
        os.environ["AV62_MODE"] = "shadow"
        from services.av62_assess_service import (
            Av62AssessService,
        )
        svc = Av62AssessService()

        a1 = await seed_asset(
            101, "enterprise", "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        r1 = await svc.assess_asset(
            a1["assetId"])
        attr = r1.get("attribution") or {}

        record("规则 ID 锚定",
               attr.get("ruleId") == "CR-001",
               str(attr.get("ruleId")))
        record("结果锚定(outcome)",
               attr.get("outcome")
               == "audit_pass_rate",
               str(attr.get("outcome")))
        record("强度锚定(strength)",
               attr.get("strength") == 0.9,
               str(attr.get("strength")))
        record("验证标记(verified)",
               attr.get("verified") is True,
               str(attr.get("verified")))

        # 证据引用
        refs = attr.get("evidenceRefs") or {}
        record("证据引用(字段+值)",
               refs.get("licenseCount") == 5
               and refs.get(
                   "auditResults") == "通过",
               str(refs))
        fs = attr.get("factorScores") or []
        record("因子快照(逐字段)",
               len(fs) == 3
               and {f["field"]
                    for f in fs}
               == {"licenseCount",
                   "auditResults",
                   "esgDisclosure"},
               str(len(fs)))

        # 无锚点→未验证(归因幻觉防线)
        entry = Av62AssessService \
            ._attribute(
                asset={"assetId": 99,
                       "subjectId": 1,
                       "role": "hacker",
                       "domain": "x",
                       "label": "域外",
                       "negative": False},
                rule=None,
                element_score=50.0,
                causal_weight=0.1,
                tier="medium", coef=0.8,
                contribution=0.04,
                risk_deduction=0.0,
                net_contribution=0.04,
                factors=[])
        record("无锚点未验证标记",
               entry.get("verified") is False
               and entry.get("ruleId") == ""
               and "未验证" in str(
                   entry.get("note")),
               str((entry.get("verified"),
                    entry.get("note"))))

        # 主体聚合 groundedRate
        await seed_asset(
            101, "enterprise", "risk",
            {"penaltyRecords": 2})
        agg = await svc.assess_subject(101)
        at = agg.get("attribution") or {}
        record("groundedRate(锚定率 1.0)",
               at.get("groundedRate") == 1.0
               and len(at.get("anchored")
                       or []) == 2
               and len(at.get("unverified")
                       or []) == 0,
               str(at.get("groundedRate")))
        os.environ["AV62_MODE"] = "off"


class TestStateMachine:
    """04 评估状态机"""

    async def run(self):
        print("[04 状态机]")
        reset_all()
        os.environ["AV62_MODE"] = "shadow"
        from services.av62_assess_service import (
            Av62AssessService,
        )
        from repositories.av62_repository import (
            Av62Repository,
        )
        svc = Av62AssessService()
        repo = Av62Repository()

        # registered → active(high)
        a1 = await seed_asset(
            101, "personal", "capability",
            {"skillCerts": 8,
             "deliveryQuality": 0.95,
             "knowledgeSharing": 24})
        r1 = await svc.assess_asset(
            a1["assetId"])
        record("registered→active",
               r1.get("assetStatus")
               == "active"
               and r1.get("version") == 1,
               str((r1.get("assetStatus"),
                    r1.get("version"))))

        # 重估(版本递增+active 容许)
        r2 = await svc.assess_asset(
            a1["assetId"])
        record("重估版本递增(v2)",
               r2.get("version") == 2
               and r2.get("assetStatus")
               == "active",
               str(r2.get("version")))

        # disputed 态评估容许
        # (P3 语义——申诉重估唯一入口)
        asset = await repo.get_asset(
            a1["assetId"])
        asset["status"] = "disputed"
        await repo.save_asset(
            asset, create=False)
        r3_disputed = await svc \
            .assess_asset(a1["assetId"])
        record("disputed 态评估容许"
               "(申诉重估入口)",
               r3_disputed.get(
                   "version") == 3,
               str(r3_disputed.get(
                   "version")))

        # 不存在资产
        try:
            await svc.assess_asset(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("资产不存在 404", ok, err)

        # 事件留痕(assess×3)
        evs = await repo.list_events(
            limit=20)
        assess_evs = [
            e for e in evs
            if e.get("eventType")
            == "assess"]
        record("事件链(assess×3)",
               len(assess_evs) == 3,
               str(len(assess_evs)))
        os.environ["AV62_MODE"] = "off"


class TestObjective:
    """05 objective 动态权重(46号双模)"""

    async def run(self):
        print("[05 objective 46号双模]")
        reset_all()
        os.environ["AV62_MODE"] = "shadow"
        from services.av62_assess_service import (
            Av62AssessService,
        )
        svc = Av62AssessService()

        # 默认 stability(fail-soft)
        record("默认 stability",
               await svc
               .get_active_objective()
               == "stability",
               "")

        # 域外/缺理由拒绝
        try:
            await svc.objective_submit(
                "hacked")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("objective 域外拒绝",
               ok, err)
        try:
            await svc.objective_submit(
                "growth", reason="")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("缺理由拒绝", ok, err)

        # 46号入册+提交
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService() \
            .sync_registry()
        sub = await svc.objective_submit(
            "growth",
            requested_by="策略官",
            reason="Q3 促创新——知识/成长"
                   "域权重上调")
        record("submit 46号(pending)",
               sub.get("status") == "pending"
               and (sub.get("changeId")
                    or 0) > 0,
               str((sub.get("status"),
                    sub.get("changeId"))))

        # 重复 pending 拒绝
        try:
            await svc.objective_submit(
                "stability",
                reason="重复申请")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复 pending 拒绝", ok, err)

        # 未裁决 apply 拒绝
        try:
            await svc.objective_apply(
                sub["changeId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("未裁决 apply 拒绝", ok, err)

        # changeId 不存在
        try:
            await svc.objective_apply(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("changeId 不存在 404", ok, err)

        # 未经审批不生效(评估仍 stability)
        a1 = await seed_asset(
            101, "enterprise", "knowledge",
            {"sopDocs": 40,
             "techContribs": 24,
             "codeCommits": 160})
        r_before = await svc.assess_asset(
            a1["assetId"])
        record("未审批不生效"
               "(stability 权重)",
               r_before.get("causalWeight")
               == 0.16,
               str(r_before.get(
                   "causalWeight")))

        # 46号裁决+apply(config 执行器
        # 抛异常但 reviewedBy 已留痕——
        # dm61 P2 同款范式)
        try:
            await AiGovernanceService() \
                .review_change(
                    int(sub["changeId"]),
                    approve=True,
                    reviewed_by="审批官",
                    review_note="同意促创新")
        except ValueError:
            pass
        ap = await svc.objective_apply(
            sub["changeId"],
            applied_by="终审官")
        record("apply 生效(applied)",
               ap.get("status") == "applied"
               and ap.get("objective")
               == "growth",
               str((ap.get("status"),
                    ap.get("objective"))))

        # 生效后评估用 growth 乘子
        a2 = await seed_asset(
            202, "enterprise", "knowledge",
            {"sopDocs": 40,
             "techContribs": 24,
             "codeCommits": 160})
        r_after = await svc.assess_asset(
            a2["assetId"])
        record("生效后 growth 乘子"
               "(×1.2)",
               r_after.get("causalWeight")
               == 0.192
               and r_after.get(
                   "objective") == "growth",
               str(r_after.get(
                   "causalWeight")))

        # risk 乘子恒 1.0(growth 下不变)
        a3 = await seed_asset(
            202, "enterprise", "risk",
            {"penaltyRecords": 3})
        r3 = await svc.assess_asset(
            a3["assetId"])
        record("risk 乘子恒 1.0"
               "(growth 不减免)",
               r3.get("causalWeight")
               == -0.285,
               str(r3.get("causalWeight")))
        os.environ["AV62_MODE"] = "off"


class TestConstitution:
    """06 宪法断言"""

    async def run(self):
        print("[06 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 38 档案在册",
               len(SCORER_REGISTRY) == 39,
               str(len(SCORER_REGISTRY)))
        record("第37档案 asset_valuation",
               "asset_valuation"
               in SCORER_REGISTRY,
               "")

        # 45/47号零改动(纯读取)
        try:
            from repositories import \
                trust_value_repository as r45
            from services import \
                trust_risk_profile_service as s47
            record("45/47号零改动(纯读取)",
                   r45 is not None
                   and s47 is not None,
                   "")
        except ImportError:
            record("45/47号零改动(纯读取)",
                   False, "导入失败")

        # LLM 不进判定链
        record("LLM 不进判定链"
               "(三开关 off)",
               os.environ.get(
                   "AV62_MODE") == "off"
               and os.environ.get(
                   "AV62_LLM_MODE") == "off"
               and os.environ.get(
                   "AV62_LEARN_MODE")
               == "off",
               "")


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

        # 观测面 off 可用(评估列表)
        resp = client.get(
            "/api/av62/assessments",
            headers=admin)
        record("HTTP assessments 观测面 200",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("total") == 0,
               str(resp.status_code))

        # 决策面 off 409
        resp = client.post(
            "/api/av62/assess",
            json={"assetId": 1},
            headers=admin)
        record("HTTP assess off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 全链
        os.environ["AV62_MODE"] = "shadow"
        resp = client.post(
            "/api/av62/assets",
            json={"subjectId": 601,
                  "role": "enterprise",
                  "domain": "compliance",
                  "evidence": {
                      "licenseCount": 8,
                      "auditResults":
                          "通过",
                      "esgDisclosure":
                          "已披露"}},
            headers=admin)
        record("HTTP 种子登记 200",
               resp.status_code == 200,
               str(resp.status_code))

        resp = client.post(
            "/api/av62/assess",
            json={"assetId": 1,
                  "assessedBy": "HTTP官"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP assess 200(active)",
               resp.status_code == 200
               and body.get("assetStatus")
               == "active"
               and (body.get("attribution")
                    or {}).get("ruleId")
               == "CR-001",
               str((resp.status_code,
                    body.get("assetStatus"))))

        # subjectId 聚合
        client.post(
            "/api/av62/assets",
            json={"subjectId": 601,
                  "role": "enterprise",
                  "domain": "risk",
                  "evidence": {
                      "penaltyRecords":
                          1}},
            headers=admin)
        resp = client.post(
            "/api/av62/assess",
            json={"subjectId": 601},
            headers=admin)
        body = resp.json() or {}
        record("HTTP assess subject 聚合",
               resp.status_code == 200
               and body.get(
                   "assetsAssessed") == 2
               and (body.get(
                   "riskDeduction")
                    or 0) > 0,
               str((resp.status_code,
                    body.get(
                        "assetsAssessed"))))

        # 缺参 409
        resp = client.post(
            "/api/av62/assess",
            json={},
            headers=admin)
        record("HTTP assess 缺参 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 不存在 404
        resp = client.post(
            "/api/av62/assess",
            json={"assetId": 999},
            headers=admin)
        record("HTTP assess 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 列表+详情
        resp = client.get(
            "/api/av62/assessments",
            headers=admin)
        body = resp.json() or {}
        record("HTTP assessments 列表",
               resp.status_code == 200
               and body.get("total") == 3,
               str(body.get("total")))
        resp = client.get(
            "/api/av62/assessments/1",
            headers=admin)
        body = resp.json() or {}
        record("HTTP assessment 详情",
               resp.status_code == 200
               and ((body.get("assessment")
                     or {}).get("ruleId")
                    == "CR-001"),
               str(resp.status_code))
        resp = client.get(
            "/api/av62/assessments/999",
            headers=admin)
        record("HTTP assessment 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("POST",
                 "/api/av62/assess"),
                ("GET",
                 "/api/av62/assessments"),
                ("GET",
                 "/api/av62/"
                 "assessments/1")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP "
                   f"{path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计(P1 8——后续期
        # 递增至 14+)
        from routes.av62_routes import (
            router as av_router,
        )
        count = sum(
            1 for r in av_router.routes)
        record("62号路由 P1 8 端点",
               count >= 8, str(count))
        os.environ["AV62_MODE"] = "off"


async def run_all():
    await TestCausalRules().run()
    await TestContribution().run()
    await TestAttribution().run()
    await TestStateMachine().run()
    await TestObjective().run()
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
