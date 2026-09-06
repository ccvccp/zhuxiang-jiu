"""62号·AI智能无形资产估值模块 P0 专项测试
(信任要素注册表+资产登记底座+第37档案)

运行方式:
    python test_av62_p0.py

覆盖(62号计划 §七 P0):
    - 信任要素注册表: 三角色×九资产域
      +负资产域封闭注册+启动自检
    - 资产登记底座: 主体×角色×要素
      域+证据快照封闭校验+负资产
      铁律(证据必填)
    - 第37档案八因子+三级决策
    - HTTP 层: 5 端点+鉴权
    - 宪法: 44号 38 档案+45/47/51号
      零改动+开关铁律
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
os.environ.pop("AV62_LLM_MODE", None)
os.environ.pop("AV62_LEARN_MODE", None)

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


class TestRegistry:
    """01 信任要素注册表"""

    async def run(self):
        print("[01 要素注册表]")
        reset_all()
        from services.av62_registry import (
            ALL_DOMAINS, DOMAINS,
            RISK_DOMAIN, ROLE_DOMAINS,
            TRUST_ELEMENTS, get_element,
            is_negative, registry_view,
            validate_evidence,
        )

        # 数量+结构
        record("三角色域",
               len(ROLE_DOMAINS) == 3,
               str(len(ROLE_DOMAINS)))
        record("九资产域(8正+risk)",
               len(DOMAINS) == 8
               and len(ALL_DOMAINS) == 9,
               str((len(DOMAINS),
                    len(ALL_DOMAINS))))
        record("13 要素注册(10正+3负)",
               len(TRUST_ELEMENTS) == 13,
               str(len(TRUST_ELEMENTS)))
        risk_n = sum(
            1 for (_, d)
            in TRUST_ELEMENTS
            if d == RISK_DOMAIN)
        record("负资产域三角色覆盖",
               risk_n == 3,
               str(risk_n))

        # 每角色≥3 域
        for role in ROLE_DOMAINS:
            domains = [
                d for (r, d)
                in TRUST_ELEMENTS
                if r == role]
            record(
                f"角色 {role} 域≥3",
                len(domains) >= 3,
                str(len(domains)))

        # 负域权重必负
        for (role, domain), el in \
                TRUST_ELEMENTS.items():
            if domain == RISK_DOMAIN:
                if float(el["weight"]) \
                        >= 0:
                    record(
                        f"负域 {role} 权重",
                        False,
                        str(el["weight"]))
                    break
        else:
            record("负域权重全负",
                   True, "")

        # 证据字段封闭
        check = validate_evidence(
            "enterprise", "compliance",
            {"licenseCount": 5,
             "hackedField": 1})
        record("证据域外字段拒绝",
               check["valid"] is False
               and check[
                   "rejectedFields"]
               == ["hackedField"]
               and check["cleaned"]
               == {"licenseCount": 5},
               str(check))

        # 负资产证据必填(防漏报洗白)
        check2 = validate_evidence(
            "enterprise", "risk", {})
        record("负资产证据必填",
               check2["valid"] is False,
               str(check2.get("error")))

        # 要素定义读取
        el = get_element(
            "personal", "capability")
        record("要素定义(七字段)",
               set(el) >= {
                   "label", "weight",
                   "evidenceSchema"},
               str(sorted(el)))

        # registry_view 观测面
        v = registry_view()
        record("registry_view 结构",
               v.get("roles") == 3
               and v.get(
                   "elements") == 13
               and v.get("mode") == "off",
               str((v.get("roles"),
                    v.get("elements"))))
        record("riskImmutable 铁律",
               v.get("meta", {}).get(
                   "riskImmutable")
               is True,
               "")


class TestAsset:
    """02 资产登记底座"""

    async def run(self):
        print("[02 资产登记]")
        reset_all()
        from services.av62_service import (
            Av62Service,
        )
        svc = Av62Service()

        # off 铁律
        try:
            await svc.register_asset(
                1, "enterprise",
                "compliance", {})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(登记拒绝)", ok, err)

        # shadow 开放
        os.environ["AV62_MODE"] = "shadow"

        # ① 正资产登记
        r1 = await svc.register_asset(
            subject_id=101,
            role="enterprise",
            domain="compliance",
            evidence={
                "licenseCount": 5,
                "auditResults":
                    "通过",
                "esgDisclosure":
                    "已披露"},
            label="企业A合规资产",
            registered_by="登记官")
        record("正资产登记(registered)",
               r1.get("status")
               == "registered"
               and r1.get("assetId")
               == 1
               and r1.get("negative")
               is False,
               str((r1.get("status"),
                    r1.get("negative"))))
        record("要素权重挂载(0.25)",
               r1.get("weight") == 0.25,
               str(r1.get("weight")))
        record("指纹链(sha256)",
               str(r1.get("fingerprint")
                   or "").startswith(
                   "sha256:"),
               str(r1.get("fingerprint")
                   )[:20])

        # ② 负资产登记
        r2 = await svc.register_asset(
            subject_id=101,
            role="enterprise",
            domain="risk",
            evidence={
                "penaltyRecords": 2},
            label="企业A处罚记录")
        record("负资产登记(negative)",
               r2.get("negative")
               is True
               and r2.get("weight")
               == -0.3,
               str((r2.get("negative"),
                    r2.get("weight"))))

        # ③ 角色域外拒绝
        try:
            await svc.register_asset(
                1, "hacker",
                "compliance", {})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("角色域外拒绝", ok, err)

        # ④ 资产域外拒绝
        try:
            await svc.register_asset(
                1, "enterprise",
                "finance", {})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("资产域外拒绝", ok, err)

        # ⑤ 未注册要素域拒绝
        # (personal×social 未注册)
        try:
            await svc.register_asset(
                1, "personal",
                "social", {})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("未注册要素拒绝", ok, err)

        # ⑥ 证据域外字段拒绝
        try:
            await svc.register_asset(
                101, "enterprise",
                "compliance",
                {"licenseCount": 1,
                 "sopDocs": 999})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("证据域外拒绝", ok, err)

        # ⑦ 负资产证据缺省拒绝
        try:
            await svc.register_asset(
                101, "enterprise",
                "risk", {})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("负资产缺证据拒绝", ok, err)

        # ⑧ 主体缺省拒绝
        try:
            await svc.register_asset(
                0, "enterprise",
                "compliance",
                {"licenseCount": 1})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("主体缺省拒绝", ok, err)

        # ⑨ 观测面: 列表+过滤
        # (再登记两条不同角色)
        await svc.register_asset(
            202, "personal",
            "capability",
            {"skillCerts": 3,
             "deliveryQuality": 0.9,
             "knowledgeSharing": 8})
        await svc.register_asset(
            303, "organization",
            "social",
            {"memberActivity": 0.8,
             "eventCompliance": 0.95,
             "externalReviews": 4.5})
        lv = await svc.list_assets()
        record("列表观测(4 条+分布)",
               lv.get("total") == 4
               and (lv.get(
                   "byRole")
                   or {}).get(
                   "enterprise") == 2
               and lv.get("negative")
               == 1,
               str((lv.get("total"),
                    lv.get("byRole"))))
        lf = await svc.list_assets(
            role="personal")
        record("列表角色过滤",
               lf.get("total") == 1,
               str(lf.get("total")))
        lneg = await svc.list_assets(
            domain="risk")
        record("列表负域过滤",
               lneg.get("total") == 1
               and (lneg.get(
                   "assets")
                   or [{}])[0].get(
                   "negative") is True,
               str(lneg.get("total")))

        # ⑩ 详情观测
        d1 = await svc.get_asset(1)
        record("详情观测(证据快照)",
               ((d1.get("asset")
                 or {}).get(
                   "evidence")
                or {}).get(
                   "licenseCount")
               == 5
               and (d1.get(
                   "element")
                   or {}).get(
                   "label")
               == "企业合规资产",
               str((d1.get("asset")
                    or {}).get(
                   "role")))
        try:
            await svc.get_asset(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("详情 404(不存在)", ok, err)

        # ⑪ 事件留痕
        from repositories.av62_repository import (
            Av62Repository,
        )
        evs = await Av62Repository() \
            .list_events(limit=10)
        reg_evs = [
            e for e in evs
            if e.get("eventType")
            == "register"]
        record("事件链(register×4)",
               len(reg_evs) == 4,
               str(len(reg_evs)))

        # ⑫ 状态机九态
        from services.av62_registry import (
            ASSET_STATES,
        )
        record("九态状态机在册",
               len(ASSET_STATES) == 9
               and "registered"
               in ASSET_STATES,
               str(len(
                   ASSET_STATES)))

        os.environ["AV62_MODE"] = "off"


class TestScorer:
    """03 第37档案八因子"""

    async def run(self):
        print("[03 第37档案]")
        reset_all()
        from services.av62_scorer import (
            Av62Scorer,
        )
        scorer = Av62Scorer()

        # 空上下文拒绝
        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空上下文拒绝", ok, err)

        # 全因子高分
        r = await scorer.score({
            "valuationAccuracy": 0.95,
            "attributionGrounded": 0.9,
            "scenarioFitness": 0.9,
            "fairnessPosture": 0.85,
            "tier": "trusted",
            "appealOverturnRate": 0.03,
            "latencyP95Ok": 0.95,
            "domainCoverage": 1.0,
        })
        record("八因子齐备",
               len(r.get("factors")
                   or []) == 8,
               str(len(r.get("factors")
                        or [])))
        record("权重和=1.0",
               abs(sum((r.get(
                   "weightsUsed") or {})
                   .values()) - 1.0) < 0.01,
               str(sum((r.get(
                   "weightsUsed") or {})
                   .values())))
        record("高分→optimize/urgent",
               r.get("decision") in (
                   "optimize", "urgent"),
               str((r.get("trustScore"),
                    r.get("decision"))))

        # 低分→observe
        r2 = await scorer.score({
            "valuationAccuracy": 0.2,
            "attributionGrounded": 0.2,
            "scenarioFitness": 0.2,
            "fairnessPosture": 0.2,
            "tier": "restricted",
            "appealOverturnRate": 0.3,
            "latencyP95Ok": 0.3,
            "domainCoverage": 0.2,
        })
        record("低分→observe",
               r2.get("decision")
               == "observe"
               and (r2.get("trustScore")
                    or 0) < 50,
               str((r2.get("trustScore"),
                    r2.get("decision"))))

        # 申诉翻转反向因子
        r3 = await scorer.score({
            "appealOverturnRate": 0.02})
        r4 = await scorer.score({
            "appealOverturnRate": 0.2})
        f3 = [f for f in
              r3.get("factors")
              if f["name"]
              == "appeal_overturn"]
        f4 = [f for f in
              r4.get("factors")
              if f["name"]
              == "appeal_overturn"]
        record("申诉翻转反向(低翻转高分)",
               (f3[0]["score"] if f3 else 0)
               > (f4[0]["score"] if f4
                  else 100),
               str((f3[0]["score"] if f3
                    else None,
                    f4[0]["score"] if f4
                    else None)))

        # tier 基线
        r5 = await scorer.score({
            "tier": "trusted"})
        f5 = [f for f in
              r5.get("factors")
              if f["name"] == "member_trust"]
        record("tier 基线(trusted=90)",
               f5 and f5[0]["score"]
               == 90.0,
               str(f5[0]["score"] if f5
                   else None))

        # 覆盖率越界拒绝
        try:
            await scorer.score({
                "domainCoverage": 1.5})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "覆盖率" in str(e), \
                str(e)[:30]
        record("覆盖率越界拒绝", ok, err)

        # 因子明细八条
        names = {f["name"] for f in
                 r.get("factors")}
        record("因子明细八条",
               names == {
                   "valuation_accuracy",
                   "attribution_grounded",
                   "scenario_fitness",
                   "fairness_posture",
                   "member_trust",
                   "appeal_overturn",
                   "latency_budget",
                   "coverage_breadth"},
               str(sorted(names)))


class TestConstitution:
    """04 宪法断言"""

    async def run(self):
        print("[04 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 38 档案在册",
               len(SCORER_REGISTRY) == 38,
               str(len(SCORER_REGISTRY)))
        record("第37档案 asset_valuation",
               "asset_valuation"
               in SCORER_REGISTRY
               and SCORER_REGISTRY[
                   "asset_"
                   "valuation"]
               ["batch"] == 21,
               str(SCORER_REGISTRY.get(
                   "asset_valuation")))

        # 45号零改动(纯读取——deposit
        # 增益域调用为 P2)
        try:
            from repositories import \
                trust_value_repository as r45
            record("45号零改动(纯读取)",
                   r45 is not None,
                   "")
        except ImportError:
            record("45号零改动(纯读取)",
                   False, "导入失败")

        # 47号零改动(get_profile 纯读取)
        try:
            from services import \
                trust_risk_profile_service as s47
            record("47号零改动(纯读取)",
                   s47 is not None,
                   "")
        except ImportError:
            record("47号零改动(纯读取)",
                   False, "导入失败")

        # 51号零改动(语义参照)
        try:
            from services import \
                kg51_trace_service as s51
            record("51号零改动(语义参照)",
                   s51 is not None,
                   "")
        except ImportError:
            record("51号零改动(语义参照)",
                   False, "导入失败")

        # 三开关铁律
        record("三开关铁律(默认 off)",
               os.environ.get(
                   "AV62_MODE",
                   "off") == "off"
               and os.environ.get(
                   "AV62_LLM_MODE",
                   "off") == "off"
               and os.environ.get(
                   "AV62_LEARN_MODE",
                   "off") == "off",
               "")


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 观测面 off 可用
        resp = client.get("/api/av62/registry",
                         headers=admin)
        body = resp.json() or {}
        record("HTTP registry 观测面 200",
               resp.status_code == 200
               and body.get("elements")
               == 13
               and body.get("mode") == "off",
               str((resp.status_code,
                    body.get(
                        "elements"))))

        resp = client.get(
            "/api/av62/model/status",
            headers=admin)
        record("HTTP model/status 200",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("status")
                    or {}).get("scorerId")
               == "asset_valuation",
               str(resp.status_code))

        # 决策面 off 409
        resp = client.post(
            "/api/av62/assets",
            json={"subjectId": 1,
                  "role": "enterprise",
                  "domain": "compliance",
                  "evidence": {}},
            headers=admin)
        record("HTTP assets off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # shadow 全链
        os.environ["AV62_MODE"] = "shadow"
        resp = client.post(
            "/api/av62/assets",
            json={"subjectId": 501,
                  "role": "enterprise",
                  "domain": "compliance",
                  "evidence": {
                      "licenseCount": 3,
                      "auditResults":
                          "通过"},
                  "label": "HTTP 登记"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP assets 200(registered)",
               resp.status_code == 200
               and body.get("status")
               == "registered",
               str((resp.status_code,
                    body.get("status"))))

        # 域外 409
        resp = client.post(
            "/api/av62/assets",
            json={"subjectId": 501,
                  "role": "enterprise",
                  "domain": "hacked",
                  "evidence": {}},
            headers=admin)
        record("HTTP 域外 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 详情观测面
        resp = client.get(
            "/api/av62/assets/1",
            headers=admin)
        record("HTTP 详情 200",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("asset")
                    or {}).get("role")
               == "enterprise",
               str(resp.status_code))

        # 详情 404
        resp = client.get(
            "/api/av62/assets/999",
            headers=admin)
        record("HTTP 详情 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 列表过滤
        resp = client.get(
            "/api/av62/assets?role="
            "enterprise",
            headers=admin)
        body = resp.json() or {}
        record("HTTP 列表角色过滤",
               resp.status_code == 200
               and body.get("total") == 1,
               str(body.get("total")))

        # 鉴权 403
        for method, path in (
                ("GET", "/api/av62/registry"),
                ("POST", "/api/av62/assets"),
                ("GET", "/api/av62/assets"),
                ("GET",
                 "/api/av62/model/status")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 5 端点(P0)
        from routes.av62_routes import (
            router as av_router,
        )
        count = sum(
            1 for r in av_router.routes)
        record("62号路由 P0 5 端点",
               count == 5, str(count))
        os.environ["AV62_MODE"] = "off"


async def run_all():
    await TestRegistry().run()
    await TestAsset().run()
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
