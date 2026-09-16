"""62号·信值调整分支(P3 创新升级)专项测试

覆盖:
    1. registry 自检: 信值规则域/β 封闭域对齐/creditRules 自描述
    2. 登记接入: legalStatus 封闭域校验/缺省 unverified/落库回显
    3. 信值评估链: 前置(须先公允估值)/V_fair 锚定/
       α档位映射/β权属映射/γ置信档映射/三主体差异化(alphaCap/
       vCreditCap)/有效性判定(乘积门槛+disputed+负资产)
    4. 信值报告: 三章节结构+免责标注+审计因子链留痕
    5. 观测面: get_credit 404(无记录)/信值历史
    6. HTTP: credit/assess 决策面 off 409+特征串/
       credit 报告观测面/鉴权 403
    7. 端点计数: >=31

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    $env:AUTH_MODE="compat"; python test_av62_credit.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")
os.environ["AV62_MODE"] = "assist"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


async def seed_asset_and_assess(
        subject_id=1001, role="enterprise",
        domain="compliance", legal="clean",
        evidence=None):
    """造数: 登记(legalStatus)+公允评估(V_fair 锚定)"""
    from services.av62_service import Av62Service
    from services.av62_assess_service import (
        Av62AssessService,
    )
    svc = Av62Service()
    asset = await svc.register_asset(
        subject_id=subject_id, role=role,
        domain=domain,
        evidence=evidence
        or {"licenseCount": 3,
            "auditResults": 90,
            "esgDisclosure": 1},
        legal_status=legal)
    await Av62AssessService().assess_asset(
        asset["assetId"])
    return asset["assetId"]


async def run_service():
    from repositories.store import reset_store
    from services.av62_registry import (
        CREDIT_ROLE_RULES, CREDIT_BETA_BY_LEGAL,
        LEGAL_STATUS_VALUES,
        CREDIT_ALPHA_BY_TIER, CREDIT_GAMMA_BY_TIER,
        registry_view,
    )
    from services.av62_service import Av62Service
    from services.av62_credit_service import (
        Av62CreditService,
    )

    # ========================================================
    # 1. registry 自检
    # ========================================================
    record("注册表-信值规则角色域合法",
           all(r in ("enterprise", "organization",
                     "personal")
               for r, _ in CREDIT_ROLE_RULES))
    record("注册表-β与权属态对齐",
           set(CREDIT_BETA_BY_LEGAL)
           == set(LEGAL_STATUS_VALUES))
    view = registry_view()
    cr = view.get("creditRules") or {}
    record("注册表-creditRules自描述",
           "V_credit" in cr.get("formula", "")
           and len(cr.get("roleRules") or {})
           == len(CREDIT_ROLE_RULES)
           and cr.get("legalStatusValues")
           == list(LEGAL_STATUS_VALUES))

    # ========================================================
    # 2. 登记接入 legalStatus
    # ========================================================
    reset_store()
    svc = Av62Service()
    a = await svc.register_asset(
        subject_id=2001, role="enterprise",
        domain="compliance",
        evidence={"licenseCount": 1},
        legal_status="pledged")
    record("登记-legalStatus落库回显",
           a.get("legalStatus") == "pledged")
    try:
        await svc.register_asset(
            subject_id=2002, role="enterprise",
            domain="compliance",
            evidence={"licenseCount": 1},
            legal_status="frozen")
        record("登记-权属态封闭域拒绝", False, "未抛")
    except ValueError as e:
        record("登记-权属态封闭域拒绝",
               "权属态" in str(e))
    # 缺省 unverified
    a2 = await svc.register_asset(
        subject_id=2003, role="personal",
        domain="capability",
        evidence={"skillCerts": 2},
        legal_status="unverified")
    record("登记-缺省unverified保守",
           a2.get("legalStatus") == "unverified")

    # ========================================================
    # 3. 信值评估链
    # ========================================================
    reset_store()
    credit = Av62CreditService()

    # 3.1 前置: 未公允估值 → 409
    svc = Av62Service()
    lone = await svc.register_asset(
        subject_id=3001, role="enterprise",
        domain="compliance",
        evidence={"licenseCount": 2},
        legal_status="clean")
    try:
        await credit.credit_assess(lone["assetId"])
        record("信值-前置须先公允估值", False, "未抛")
    except ValueError as e:
        record("信值-前置须先公允估值",
               "公允估值" in str(e))

    # 3.2 基础链: clean 企业合规(high 档 α=0.95)
    aid = await seed_asset_and_assess(
        legal="clean")
    r = await credit.credit_assess(aid)
    record("信值-V_fair锚定评估记录",
           r["vFair"] > 0
           and r["assessId"] > 0)
    record("信值-α高流动档",
           abs(r["alpha"]
               - CREDIT_ALPHA_BY_TIER["high"])
           < 1e-6)
    record("信值-βclean=1.0", r["beta"] == 1.0)
    record("信值-公式合成",
           abs(r["vCredit"]
               - round(r["vFair"] * r["alpha"]
                       * r["beta"] * r["gamma"], 4))
           < 1e-4)
    record("信值-有效", r["valid"] is True)

    # 3.3 β 权属折减: pledged 0.70
    aid_p = await seed_asset_and_assess(
        subject_id=3002, legal="pledged")
    r_p = await credit.credit_assess(aid_p)
    record("信值-βpledged=0.70",
           r_p["beta"] == 0.70)

    # 3.4 主体差异化: organization compliance
    # alphaCap 0.60(特许经营公共属性剥离)
    aid_o = await seed_asset_and_assess(
        subject_id=3003, role="organization",
        domain="compliance",
        evidence={"eventCompliance": 90,
                  "auditResults": 85},
        legal="clean")
    r_o = await credit.credit_assess(aid_o)
    record("信值-事业单位合规alphaCap0.60",
           r_o["alpha"] <= 0.60
           and r_o["factors"]["roleRule"]["key"]
           == "organization.compliance")

    # 3.5 主体差异化: enterprise behavior
    # vCreditCap 0.50(客户关系高集中减半)
    aid_b = await seed_asset_and_assess(
        subject_id=3004, role="enterprise",
        domain="behavior",
        evidence={"operationCompliance": 90,
                  "collabLatency": 30,
                  "dataSharing": 80},
        legal="clean")
    r_b = await credit.credit_assess(aid_b)
    record("信值-企业行为资产vCreditCap0.50",
           r_b["vCredit"]
           <= round(r_b["vFair"] * 0.50, 4) + 1e-6
           and r_b["factors"]["roleRule"][
               "vCreditCap"] == 0.50)

    # 3.6 个人自媒体 alphaCap 0.20
    aid_w = await seed_asset_and_assess(
        subject_id=3005, role="personal",
        domain="knowledge",
        evidence={"knowledgeSharing": 10,
                  "techContribs": 3},
        legal="clean")
    r_w = await credit.credit_assess(aid_w)
    record("信值-个人自媒体alphaCap0.20",
           r_w["alpha"] <= 0.20)

    # 3.7 有效性: disputed 无效
    aid_d = await seed_asset_and_assess(
        subject_id=3006, legal="disputed")
    r_d = await credit.credit_assess(aid_d)
    record("信值-disputed无效",
           r_d["valid"] is False
           and any("争议" in s
                   for s in r_d["invalidReasons"]))

    # 3.8 负资产拒绝
    svc2 = Av62Service()
    neg = await svc2.register_asset(
        subject_id=3007, role="enterprise",
        domain="risk",
        evidence={"penaltyRecords": 1},
        legal_status="clean")
    try:
        await credit.credit_assess(neg["assetId"])
        record("信值-负资产拒绝", False, "未抛")
    except ValueError as e:
        record("信值-负资产拒绝",
               "不参与信值" in str(e))

    # ========================================================
    # 4. 信值报告(三章节+免责+审计链)
    # ========================================================
    r4 = await credit.get_credit(aid)
    rpt = r4.get("report") or {}
    secs = rpt.get("sections") or {}
    record("信值报告-三章节",
           set(secs.keys()) >= {
               "liquidityAnalysis",
               "legalDisclosure",
               "stressReference"})
    record("信值报告-免责标注",
           "信用参考" in rpt.get("disclaimer", "")
           and "交易定价" in rpt.get(
               "disclaimer", ""))
    record("信值报告-审计因子链",
           set((r4.get("factors") or {}).keys())
           >= {"vFair", "alpha", "beta",
               "gamma", "roleRule"})
    record("信值报告-模板类型",
           rpt.get("templateType") == "credit")

    # ========================================================
    # 5. 观测面
    # ========================================================
    try:
        await credit.get_credit(99999)
        record("观测-无记录404", False, "未抛")
    except KeyError:
        record("观测-无记录404", True)
    hist = await credit.list_credit_history(aid)
    record("观测-信值历史",
           len(hist) >= 1
           and hist[0]["assetId"] == aid)


async def run_http():
    import httpx
    from main import app
    from repositories.store import reset_store

    hdrs = {"X-Role": "admin"}

    # off 门控
    os.environ["AV62_MODE"] = "off"
    reset_store()
    async with httpx.AsyncClient(
            app=app, base_url="http://t") as c:
        r = await c.post(
            "/api/av62/assets/1/credit/assess",
            json={}, headers=hdrs)
        body = r.json()
        record("HTTP-决策面off409",
               r.status_code == 409
               and "AV62_MODE" in str(
                   body.get("detail")
                   or body.get("error", "")))

    # assist 全链 HTTP
    os.environ["AV62_MODE"] = "assist"
    reset_store()
    async with httpx.AsyncClient(
            app=app, base_url="http://t") as c:
        # 无 Role 403(assist 门控放行后
        # 函数体内鉴权——62号 43-61号同款口径)
        r = await c.post(
            "/api/av62/assets/1/credit/assess",
            json={})
        record("HTTP-鉴权403", r.status_code == 403)
        # 登记+公允估值
        r = await c.post("/api/av62/assets", json={
            "subjectId": 9001, "role": "enterprise",
            "domain": "compliance",
            "evidence": {"licenseCount": 2,
                         "auditResults": 85,
                         "esgDisclosure": 1},
            "legalStatus": "pledged",
        }, headers=hdrs)
        aid = r.json().get("assetId")
        record("HTTP-登记带权属态",
               r.status_code == 200
               and r.json().get("legalStatus")
               == "pledged")
        await c.post("/api/av62/assess",
                     json={"assetId": aid},
                     headers=hdrs)
        # 信值评估(决策面 assist 放行+标记)
        r = await c.post(
            f"/api/av62/assets/{aid}/credit/assess",
            json={}, headers=hdrs)
        body = r.json()
        record("HTTP-信值评估放行+标记",
               r.status_code == 200
               and body.get("av62Mode") == "assist"
               and body.get("beta") == 0.70)
        # 信值报告(观测面)
        r = await c.get(
            f"/api/av62/assets/{aid}/credit",
            headers=hdrs)
        body = r.json()
        record("HTTP-信值报告观测面",
               r.status_code == 200
               and (body.get("report") or {})
               .get("templateType") == "credit")

    # 端点计数
    paths = [(route.path, m)
             for route in app.routes
             for m in getattr(route, "methods", [])
             if route.path.startswith("/api/av62/")]
    record("HTTP-端点计数>=31",
           len(paths) >= 31, f"got {len(paths)}")


async def main():
    print("=" * 60)
    print("62号·信值调整分支(P3) 专项测试")
    print("=" * 60)
    await run_service()
    await run_http()
    print("-" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
