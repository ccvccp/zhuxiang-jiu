"""62号AI智能无形资产估值 P1 Docker 实机验收

运行方式:
    python verify_av62_p1_live.py [基址]

前置: 容器已运行(含 62号 P1 代码)。

覆盖(62号计划 §七 P1, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(assess 409;
       registry/assessments 观测面 200)
    03 容器内: 因果估值引擎全链
       (CAUSAL_RULES+贡献度+置信度
        三档+归因+objective 46号
        双模+状态机+Redis 读回)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
av62 键域)。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}

CONTAINER = "zhuxiang-jiu-backend-1"
REDIS = "zhuxiang-jiu-redis-1"


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def call(method, path, body=None, headers=None,
         expect=(200,)):
    data = json.dumps(body).encode() if body is not None \
        else None
    req = urllib.request.Request(BASE + path, data=data,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            code, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def redis_del_keys(pattern: str) -> None:
    out = subprocess.run(
        ["docker", "exec", REDIS,
         "redis-cli", "--scan", "--pattern", pattern],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", REDIS, "redis-cli",
             "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def clear_av62(round_no: int) -> None:
    redis_del_keys("zhuxiang:av62:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AV62_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① CAUSAL_RULES 注册表
    "    from services.av62_registry import (\n"
    "        CAUSAL_RULES, RULES_VERSION,\n"
    "        TRUST_ELEMENTS,\n"
    "        get_objective_multiplier,\n"
    "        get_rule)\n"
    "    out['rules_n'] = len(\n"
    "        CAUSAL_RULES)\n"
    "    out['rules_ver'] = (\n"
    "        RULES_VERSION)\n"
    "    out['full_cover'] = (\n"
    "        all(get_rule(r, d)\n"
    "            for (r, d)\n"
    "            in TRUST_ELEMENTS))\n"
    "    r1_rule = get_rule(\n"
    "        'enterprise', 'compliance')\n"
    "    out['r1_id'] = (\n"
    "        r1_rule or {}).get('ruleId')\n"
    "    out['r1_strength'] = (\n"
    "        r1_rule or {}).get(\n"
    "            'strength')\n"
    "    out['mult_stab'] = (\n"
    "        get_objective_multiplier(\n"
    "            'stability',\n"
    "            'compliance'))\n"
    "    out['mult_risk'] = (\n"
    "        get_objective_multiplier(\n"
    "            'growth', 'risk'))\n"
    # ② off 铁律
    "    os.environ['AV62_MODE'] = 'off'\n"
    "    from services.av62_assess_service "
    "import Av62AssessService\n"
    "    svc = Av62AssessService()\n"
    "    try:\n"
    "        await svc.assess_asset(1)\n"
    "        out['off_reject'] = False\n"
    "    except ValueError:\n"
    "        out['off_reject'] = True\n"
    "    os.environ['AV62_MODE'] = "
    "'shadow'\n"
    # ③ 登记种子+评估(high)
    "    from services.av62_service import (\n"
    "        Av62Service)\n"
    "    reg = Av62Service()\n"
    "    a1 = await reg.register_asset(\n"
    "        subject_id=101,\n"
    "        role='enterprise',\n"
    "        domain='compliance',\n"
    "        evidence={'licenseCount': 5,\n"
    "                 'auditResults':\n"
    "                     'pass-ok',\n"
    "                 'esgDisclosure':\n"
    "                     'yes'})\n"
    "    r1 = await svc.assess_asset(\n"
    "        a1['assetId'])\n"
    "    out['r1_score'] = (\n"
    "        r1.get('elementScore'))\n"
    "    out['r1_weight'] = (\n"
    "        r1.get('causalWeight'))\n"
    "    out['r1_contrib'] = (\n"
    "        r1.get('contribution'))\n"
    "    out['r1_tier'] = (\n"
    "        r1.get('confidenceTier'))\n"
    "    out['r1_coef'] = (\n"
    "        r1.get('confidenceCoef'))\n"
    "    out['r1_status'] = (\n"
    "        r1.get('assetStatus'))\n"
    "    out['r1_ver'] = (\n"
    "        r1.get('version'))\n"
    # ④ medium/low 置信
    "    a2 = await reg.register_asset(\n"
    "        201, 'enterprise',\n"
    "        'compliance',\n"
    "        {'licenseCount': 5,\n"
    "         'auditResults': 'pass'})\n"
    "    r2 = await svc.assess_asset(\n"
    "        a2['assetId'])\n"
    "    out['r2_tier'] = (\n"
    "        r2.get('confidenceTier'))\n"
    "    out['r2_status'] = (\n"
    "        r2.get('assetStatus'))\n"
    "    out['r2_spot'] = (\n"
    "        r2.get('spotCheck'))\n"
    "    a3 = await reg.register_asset(\n"
    "        301, 'enterprise',\n"
    "        'compliance',\n"
    "        {'licenseCount': 5})\n"
    "    r3 = await svc.assess_asset(\n"
    "        a3['assetId'])\n"
    "    out['r3_tier'] = (\n"
    "        r3.get('confidenceTier'))\n"
    "    out['r3_status'] = (\n"
    "        r3.get('assetStatus'))\n"
    # ⑤ 负资产(fail-safe max+系数 1.0)
    "    a4 = await reg.register_asset(\n"
    "        101, 'enterprise', 'risk',\n"
    "        {'penaltyRecords': 2})\n"
    "    r4 = await svc.assess_asset(\n"
    "        a4['assetId'])\n"
    "    out['r4_score'] = (\n"
    "        r4.get('elementScore'))\n"
    "    out['r4_coef'] = (\n"
    "        r4.get('confidenceCoef'))\n"
    "    out['r4_deduct'] = (\n"
    "        r4.get('riskDeduction'))\n"
    "    out['r4_net'] = (\n"
    "        r4.get('netContribution'))\n"
    # ⑥ 主体聚合
    "    agg = await svc.assess_subject(\n"
    "        101)\n"
    "    out['agg_n'] = (\n"
    "        agg.get('assetsAssessed'))\n"
    "    out['agg_net'] = (\n"
    "        agg.get('netContribution'))\n"
    "    out['agg_base'] = (\n"
    "        agg.get('baseValue'))\n"
    "    out['agg_grounded'] = (\n"
    "        (agg.get('attribution')\n"
    "         or {}).get(\n"
    "             'groundedRate'))\n"
    # ⑦ 归因链
    "    attr = r1.get(\n"
    "        'attribution') or {}\n"
    "    out['at_rule'] = (\n"
    "        attr.get('ruleId'))\n"
    "    out['at_verified'] = (\n"
    "        attr.get('verified'))\n"
    "    out['at_refs'] = (\n"
    "        attr.get('evidenceRefs')\n"
    "        or {}).get(\n"
    "            'licenseCount')\n"
    "    entry = Av62AssessService"
    "._attribute(\n"
    "        asset={'assetId': 99,\n"
    "               'subjectId': 1,\n"
    "               'role': 'h',\n"
    "               'domain': 'x',\n"
    "               'label': 'y',\n"
    "               'negative': False},\n"
    "        rule=None,\n"
    "        element_score=50.0,\n"
    "        causal_weight=0.1,\n"
    "        tier='medium', coef=0.8,\n"
    "        contribution=0.04,\n"
    "        risk_deduction=0.0,\n"
    "        net_contribution=0.04,\n"
    "        factors=[])\n"
    "    out['unverified'] = (\n"
    "        entry.get('verified'))\n"
    # ⑧ 重估版本链
    "    r5 = await svc.assess_asset(\n"
    "        a1['assetId'])\n"
    "    out['r5_ver'] = (\n"
    "        r5.get('version'))\n"
    "    out['r5_same'] = (\n"
    "        r5.get('netContribution')\n"
    "        == r1.get(\n"
    "            'netContribution'))\n"
    # ⑨ objective 46号双模
    "    out['obj_default'] = (\n"
    "        await svc\n"
    "        .get_active_objective())\n"
    "    try:\n"
    "        await svc.objective_submit(\n"
    "            'hacked')\n"
    "        out['obj_domain'] = False\n"
    "    except ValueError:\n"
    "        out['obj_domain'] = True\n"
    "    from services.ai_governance"
    "_service import (\n"
    "        AiGovernanceService)\n"
    "    await AiGovernanceService()"
    ".sync_registry()\n"
    "    sub = await svc.objective_submit(\n"
    "        'growth',\n"
    "        requested_by='policymaker',\n"
    "        reason='growth quarter')\n"
    "    out['obj_sub'] = (\n"
    "        sub.get('status'))\n"
    "    out['obj_cid'] = (\n"
    "        sub.get('changeId'))\n"
    "    try:\n"
    "        await svc.objective_apply(\n"
    "            sub['changeId'])\n"
    "        out['obj_early'] = False\n"
    "    except ValueError:\n"
    "        out['obj_early'] = True\n"
    "    try:\n"
    "        await AiGovernanceService()"
    ".review_change(\n"
    "            int(sub['changeId']),\n"
    "            approve=True,\n"
    "            reviewed_by='gov')\n"
    "    except ValueError:\n"
    "        pass\n"
    "    ap = await svc.objective_apply(\n"
    "        sub['changeId'])\n"
    "    out['obj_applied'] = (\n"
    "        ap.get('status'))\n"
    # ⑩ 生效后 growth 乘子+risk 恒 1
    "    a6 = await reg.register_asset(\n"
    "        202, 'enterprise',\n"
    "        'knowledge',\n"
    "        {'sopDocs': 40,\n"
    "         'techContribs': 24,\n"
    "         'codeCommits': 160})\n"
    "    r6 = await svc.assess_asset(\n"
    "        a6['assetId'])\n"
    "    out['r6_weight'] = (\n"
    "        r6.get('causalWeight'))\n"
    "    out['r6_obj'] = (\n"
    "        r6.get('objective'))\n"
    "    a7 = await reg.register_asset(\n"
    "        202, 'enterprise', 'risk',\n"
    "        {'penaltyRecords': 3})\n"
    "    r7 = await svc.assess_asset(\n"
    "        a7['assetId'])\n"
    "    out['r7_weight'] = (\n"
    "        r7.get('causalWeight'))\n"
    # ⑪ Redis 读回(评估记录)
    "    from repositories.av62_repository "
    "import Av62Repository\n"
    "    repo = Av62Repository()\n"
    "    d1 = await repo.get_assessment(1)\n"
    "    out['d1_rule'] = (\n"
    "        d1.get('ruleId'))\n"
    "    out['d1_factors'] = isinstance(\n"
    "        d1.get('factors'), list)\n"
    "    out['d1_attr'] = isinstance(\n"
    "        d1.get('attribution'), dict)\n"
    "    out['d1_ver'] = (\n"
    "        d1.get('version'))\n"
    # ⑫ 事件链
    "    evs = await repo.list_events(\n"
    "        limit=100)\n"
    "    out['ev_n'] = len([\n"
    "        e for e in evs\n"
    "        if e.get('eventType')\n"
    "        == 'assess'])\n"
    # ⑬ 44号档案
    "    from services.ai_learning_service "
    "import SCORER_REGISTRY\n"
    "    out['reg_n'] = len(\n"
    "        SCORER_REGISTRY)\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


def container_pipeline(round_no: int) -> dict:
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", PIPELINE],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr
                          or "无输出")[-1500:]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_av62(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/av62/assess",
        body={"assetId": 1},
        headers=ADMIN, expect=(409,))
    record("off 态 assess 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/av62/assessments",
        headers=ADMIN)
    record("off 态 assessments 观测面 200",
           code == 200
           and "total" in (body or {}),
           str(code))
    ok, (code, body) = call(
        "GET", "/api/av62/registry",
        headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and (body.get("elements")
                or 0) == 13,
           str(code))

    print("\n[03 容器内: 因果估值引擎全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("规则 13 条(要素全覆盖)",
           r.get("rules_n") == 13
           and r.get("full_cover") is True,
           str((r.get("rules_n"),
                r.get("full_cover"))))
    record("规则版本 v1",
           r.get("rules_ver") == "v1",
           str(r.get("rules_ver")))
    record("规则锚定(CR-001+强度)",
           r.get("r1_id") == "CR-001"
           and r.get("r1_strength") == 0.9,
           str((r.get("r1_id"),
                r.get("r1_strength"))))
    record("objective 乘子(stability 1.2)",
           r.get("mult_stab") == 1.2,
           str(r.get("mult_stab")))
    record("risk 乘子恒 1.0",
           r.get("mult_risk") == 1.0,
           str(r.get("mult_risk")))
    record("off 铁律(评估拒绝)",
           r.get("off_reject") is True,
           str(r.get("off_reject")))
    record("要素得分确定性(83.3)",
           r.get("r1_score") == 83.3,
           str(r.get("r1_score")))
    record("因果权重(0.25×0.9×1.2)",
           r.get("r1_weight") == 0.27,
           str(r.get("r1_weight")))
    record("贡献度公式(0.2249)",
           abs(float(r.get("r1_contrib")
                     or 0) - 0.2249) < 0.0005,
           str(r.get("r1_contrib")))
    record("high 置信(系数 1.0→active)",
           r.get("r1_tier") == "high"
           and r.get("r1_coef") == 1.0
           and r.get("r1_status")
           == "active",
           str((r.get("r1_tier"),
                r.get("r1_status"))))
    record("medium 置信"
           "(assessed+spotCheck)",
           r.get("r2_tier") == "medium"
           and r.get("r2_status")
           == "assessed"
           and r.get("r2_spot") is True,
           str((r.get("r2_tier"),
                r.get("r2_status"))))
    record("low 置信(pending_review)",
           r.get("r3_tier") == "low"
           and r.get("r3_status")
           == "pending_review",
           str((r.get("r3_tier"),
                r.get("r3_status"))))
    record("负资产 fail-safe"
           "(max 得分+系数 1.0)",
           r.get("r4_score") == 40.0
           and r.get("r4_coef") == 1.0,
           str((r.get("r4_score"),
                r.get("r4_coef"))))
    record("负资产扣减(0.114)",
           abs(float(r.get("r4_deduct")
                     or 0) - 0.114) < 0.0005,
           str(r.get("r4_deduct")))
    record("负资产净贡献为负",
           (r.get("r4_net") or 0) < 0,
           str(r.get("r4_net")))
    record("主体聚合(2 资产)",
           r.get("agg_n") == 2
           and abs(float(
               r.get("agg_net") or 0)
               - 0.1109) < 0.0005,
           str((r.get("agg_n"),
                r.get("agg_net"))))
    record("主体基础信值(83.3)",
           r.get("agg_base") == 83.3,
           str(r.get("agg_base")))
    record("groundedRate(锚定率 1.0)",
           r.get("agg_grounded") == 1.0,
           str(r.get("agg_grounded")))
    record("归因规则 ID 锚定",
           r.get("at_rule") == "CR-001"
           and r.get("at_verified") is True,
           str((r.get("at_rule"),
                r.get("at_verified"))))
    record("归因证据引用(licenseCount)",
           r.get("at_refs") == 5,
           str(r.get("at_refs")))
    record("无锚点未验证标记",
           r.get("unverified") is False,
           str(r.get("unverified")))
    record("重估版本链(v3 同值)",
           r.get("r5_ver") == 3
           and r.get("r5_same") is True,
           str((r.get("r5_ver"),
                r.get("r5_same"))))
    record("objective 默认 stability",
           r.get("obj_default") == "stability",
           str(r.get("obj_default")))
    record("objective 域外拒绝",
           r.get("obj_domain") is True,
           str(r.get("obj_domain")))
    record("submit 46号(pending)",
           r.get("obj_sub") == "pending",
           str(r.get("obj_sub")))
    record("未经裁决 apply 拒绝",
           r.get("obj_early") is True,
           str(r.get("obj_early")))
    record("裁决后 apply 生效",
           r.get("obj_applied") == "applied",
           str(r.get("obj_applied")))
    record("生效后 growth 乘子(0.192)",
           r.get("r6_weight") == 0.192
           and r.get("r6_obj") == "growth",
           str((r.get("r6_weight"),
                r.get("r6_obj"))))
    record("growth 下 risk 权重不变"
           "(-0.285)",
           r.get("r7_weight") == -0.285,
           str(r.get("r7_weight")))
    record("Redis 读回(ruleId+factors)",
           r.get("d1_rule") == "CR-001"
           and r.get("d1_factors") is True
           and r.get("d1_attr") is True,
           str((r.get("d1_rule"),
                r.get("d1_factors"))))
    record("事件链(assess×9)",
           r.get("ev_n") == 9,
           str(r.get("ev_n")))
    record("44号 38 档案",
           r.get("reg_n") == 38,
           str(r.get("reg_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # 服务器态 off——决策面 409(铁律)
    ok, (code, _) = call(
        "POST", "/api/av62/assess",
        body={"assetId": 1},
        headers=ADMIN, expect=(409,))
    record("HTTP assess off 409"
           "(服务器态)",
           code == 409, str(code))
    # 观测面(容器内种子的评估记录读回)
    ok, (code, body) = call(
        "GET", "/api/av62/assessments",
        headers=ADMIN)
    record("HTTP assessments 列表"
           "(Redis 读回)",
           code == 200
           and (body.get("total") or 0)
           >= 6,
           str((code, body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/av62/assessments/1",
        headers=ADMIN)
    record("HTTP assessment 详情"
           "(Redis 读回)",
           code == 200
           and ((body.get("assessment")
                 or {}).get("ruleId")
                == "CR-001"),
           str(code))
    ok, (code, _) = call(
        "GET", "/api/av62/assessments/999",
        headers=ADMIN, expect=(404,))
    record("HTTP assessment 404",
           code == 404, str(code))
    # 鉴权 403
    for method, path in (
            ("POST", "/api/av62/assess"),
            ("GET", "/api/av62/assessments"),
            ("GET",
             "/api/av62/assessments/1")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 8
    script = (
        "from routes.av62_routes import "
        "router\n"
        "print(sum(1 for r in "
        "router.routes))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        count = int((out.stdout or "").strip())
    except ValueError:
        count = -1
    record("62号路由 P1 8 端点",
           count == 8, str(count))


def main() -> int:
    for i in (1, 2):
        run_round(i)
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
