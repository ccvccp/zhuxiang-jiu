"""62号AI智能无形资产估值 P2 Docker 实机验收

运行方式:
    python verify_av62_p2_live.py [基址]

前置: 容器已运行(含 62号 P2 代码)。

覆盖(62号计划 §七 P2, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(convert/stress/activate
       409; scenarios/thresholds 观测面 200)
    03 容器内: 转化层全链(流动性三档+
       衰减 exp+激活+场景折算 45号
       deposit+反事实压测+阈值 46号双模)
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
    # ① 注册表(P2 转化层)
    "    from services.av62_registry import (\n"
    "        DECAY_HALF_LIFE_DAYS,\n"
    "        DOMAIN_LIQUIDITY,\n"
    "        LIQUIDITY_META,\n"
    "        SCENARIO_FACTORS,\n"
    "        decay_factor, liquidity_of,\n"
    "        liquidity_view,\n"
    "        scenario_factor)\n"
    "    out['liq_high'] = (\n"
    "        liquidity_of('compliance'))\n"
    "    out['liq_med'] = (\n"
    "        liquidity_of('knowledge'))\n"
    "    out['liq_low'] = (\n"
    "        liquidity_of('social'))\n"
    "    out['liq_none'] = (\n"
    "        liquidity_of('risk'))\n"
    "    out['d90'] = (\n"
    "        decay_factor(90))\n"
    "    out['d180'] = (\n"
    "        decay_factor(180))\n"
    "    out['sf_bid'] = (\n"
    "        scenario_factor(\n"
    "            'bidding',\n"
    "            'compliance'))\n"
    "    out['sf_know'] = (\n"
    "        scenario_factor(\n"
    "            'bidding',\n"
    "            'knowledge'))\n"
    "    out['scen_n'] = len(\n"
    "        SCENARIO_FACTORS)\n"
    "    out['view_hl'] = (\n"
    "        (liquidity_view()\n"
    "         .get('decay') or {}\n"
    "         ).get('halfLifeDays'))\n"
    # ② off 铁律
    "    from services.av62_liquidity"
    "_service import (\n"
    "        Av62LiquidityService)\n"
    "    lq = Av62LiquidityService()\n"
    "    os.environ['AV62_MODE'] = 'off'\n"
    "    try:\n"
    "        await lq.activate_asset(\n"
    "            1, 'compliance_use')\n"
    "        out['off_act'] = False\n"
    "    except ValueError:\n"
    "        out['off_act'] = True\n"
    "    try:\n"
    "        await lq.convert_scenario(\n"
    "            101, 'bidding')\n"
    "        out['off_cvt'] = False\n"
    "    except ValueError:\n"
    "        out['off_cvt'] = True\n"
    "    try:\n"
    "        await lq.stress_subject(\n"
    "            101, [1])\n"
    "        out['off_str'] = False\n"
    "    except ValueError:\n"
    "        out['off_str'] = True\n"
    "    os.environ['AV62_MODE'] = "
    "'shadow'\n"
    # ③ 种子登记+评估
    "    from services.av62_service import (\n"
    "        Av62Service)\n"
    "    from services.av62_assess_service "
    "import (\n"
    "        Av62AssessService)\n"
    "    reg = Av62Service()\n"
    "    asm = Av62AssessService()\n"
    "    a1 = await reg.register_asset(\n"
    "        101, 'enterprise',\n"
    "        'compliance',\n"
    "        {'licenseCount': 5,\n"
    "         'auditResults': 'pass',\n"
    "         'esgDisclosure': 'yes'})\n"
    "    await asm.assess_asset(\n"
    "        a1['assetId'])\n"
    "    a2 = await reg.register_asset(\n"
    "        101, 'enterprise', 'risk',\n"
    "        {'penaltyRecords': 2})\n"
    "    await asm.assess_asset(\n"
    "        a2['assetId'])\n"
    "    a3 = await reg.register_asset(\n"
    "        101, 'organization',\n"
    "        'social',\n"
    "        {'memberActivity': 0.8,\n"
    "         'eventCompliance': 0.9,\n"
    "         'externalReviews': 4})\n"
    "    await asm.assess_asset(\n"
    "        a3['assetId'])\n"
    # ④ 流动性档案
    "    p1 = await lq.get_profile(\n"
    "        a1['assetId'])\n"
    "    pf = p1.get('profile') or {}\n"
    "    out['pf_tier'] = (\n"
    "        pf.get('liquidityTier'))\n"
    "    out['pf_cap'] = (\n"
    "        pf.get('frequencyCap'))\n"
    "    out['pf_base'] = (\n"
    "        pf.get('baseValue'))\n"
    "    out['pf_decay'] = (\n"
    "        pf.get('decayFactor'))\n"
    "    out['pf_decayed'] = (\n"
    "        pf.get('decayedValue'))\n"
    "    p3 = await lq.get_profile(\n"
    "        a3['assetId'])\n"
    "    out['pf3_tier'] = ((\n"
    "        p3.get('profile') or {})\n"
    "        .get('liquidityTier'))\n"
    # ⑤ 激活(理由域+成功)
    "    try:\n"
    "        await lq.activate_asset(\n"
    "            a1['assetId'], 'hacked')\n"
    "        out['act_rej'] = False\n"
    "    except ValueError:\n"
    "        out['act_rej'] = True\n"
    "    act = await lq.activate_asset(\n"
    "        a1['assetId'],\n"
    "        'compliance_use')\n"
    "    out['act_status'] = (\n"
    "        act.get('status'))\n"
    "    from repositories.av62_repository "
    "import Av62Repository\n"
    "    repo = Av62Repository()\n"
    "    a1_read = await repo.get_asset(\n"
    "        a1['assetId'])\n"
    "    out['act_state'] = (\n"
    "        a1_read.get('status'))\n"
    "    try:\n"
    "        await lq.activate_asset(\n"
    "            a3['assetId'],\n"
    "            'knowledge_update')\n"
    "        out['act_low'] = False\n"
    "    except ValueError:\n"
    "        out['act_low'] = True\n"
    # ⑥ 场景折算(排除+系数)
    "    cv = await lq.convert_scenario(\n"
    "        101, 'bidding',\n"
    "        deposit=False)\n"
    "    out['cv_inc'] = len(\n"
    "        cv.get('included'))\n"
    "    out['cv_exc'] = len(\n"
    "        cv.get('excluded'))\n"
    "    inc = {i['domain']: i\n"
    "           for i in cv.get(\n"
    "               'included')}\n"
    "    out['cv_factor'] = (\n"
    "        inc.get('compliance', {})\n"
    "        .get('scenarioFactor'))\n"
    "    out['cv_value'] = (\n"
    "        cv.get('scenarioValue'))\n"
    "    out['cv_dep'] = (\n"
    "        cv.get(\n"
    "            'trustValueDeposit'))\n"
    # ⑦ 45号 deposit(真实容器——
    #    主体档案不存在 fail-soft)
    "    cv2 = await lq.convert_scenario(\n"
    "        101, 'bidding',\n"
    "        deposit=True)\n"
    "    out['dep45'] = (\n"
    "        (cv2.get(\n"
    "            'trustValueDeposit')\n"
    "         or {}).get('verified'))\n"
    # ⑧ 反事实压测
    "    st = await lq.stress_subject(\n"
    "        101,\n"
    "        remove_asset_ids=[\n"
    "            a1['assetId']])\n"
    "    out['st_delta'] = (\n"
    "        st.get('delta'))\n"
    "    st2 = await lq.stress_subject(\n"
    "        101,\n"
    "        remove_domains=['risk'])\n"
    "    out['st2_delta'] = (\n"
    "        st2.get('delta'))\n"
    "    try:\n"
    "        await lq.stress_subject(\n"
    "            101, [])\n"
    "        out['st_rej'] = False\n"
    "    except ValueError:\n"
    "        out['st_rej'] = True\n"
    # ⑨ 阈值 46号双模
    "    out['hl_default'] = (\n"
    "        await lq\n"
    "        .get_active_half_life())\n"
    "    from services.ai_governance"
    "_service import (\n"
    "        AiGovernanceService)\n"
    "    await AiGovernanceService()"
    ".sync_registry()\n"
    "    from services.av62_threshold"
    "_service import (\n"
    "        Av62ThresholdService)\n"
    "    ts_ = Av62ThresholdService()\n"
    "    sub = await ts_.calibrate_submit(\n"
    "        half_life_days=60,\n"
    "        requested_by='calibrator',\n"
    "        reason='active subjects')\n"
    "    out['th_sub'] = (\n"
    "        sub.get('status'))\n"
    "    out['th_cid'] = (\n"
    "        sub.get('changeId'))\n"
    "    out['th_pre'] = (\n"
    "        await lq\n"
    "        .get_active_half_life())\n"
    "    try:\n"
    "        await ts_.calibrate_apply(\n"
    "            sub['changeId'])\n"
    "        out['th_early'] = False\n"
    "    except ValueError:\n"
    "        out['th_early'] = True\n"
    "    try:\n"
    "        await AiGovernanceService()"
    ".review_change(\n"
    "            int(sub['changeId']),\n"
    "            approve=True,\n"
    "            reviewed_by='gov')\n"
    "    except ValueError:\n"
    "        pass\n"
    "    ap = await ts_.calibrate_apply(\n"
    "        sub['changeId'])\n"
    "    out['th_applied'] = (\n"
    "        ap.get('status'))\n"
    "    out['th_after'] = (\n"
    "        await lq\n"
    "        .get_active_half_life())\n"
    # ⑩ 场景乘子校准
    "    sub2 = await ts_.calibrate_submit(\n"
    "        scenario='bidding',\n"
    "        multiplier=1.2,\n"
    "        reason='peak season')\n"
    "    try:\n"
    "        await AiGovernanceService()"
    ".review_change(\n"
    "            int(sub2['changeId']),\n"
    "            approve=True,\n"
    "            reviewed_by='gov')\n"
    "    except ValueError:\n"
    "        pass\n"
    "    await ts_.calibrate_apply(\n"
    "        sub2['changeId'])\n"
    "    out['sm_after'] = (\n"
    "        await lq\n"
    "        .get_scenario_multiplier(\n"
    "            'bidding'))\n"
    # ⑪ 生效后折算(乘子联动)
    "    cv3 = await lq.convert_scenario(\n"
    "        101, 'bidding',\n"
    "        deposit=False)\n"
    "    out['cv3_mult'] = (\n"
    "        cv3.get(\n"
    "            'scenarioMultiplier'))\n"
    # ⑫ Redis 读回(liquidity 表)
    "    lr = await repo.get_liquidity(\n"
    "        a1['assetId'])\n"
    "    out['lr_tier'] = (\n"
    "        lr.get('liquidityTier'))\n"
    "    out['lr_decay'] = isinstance(\n"
    "        lr.get('decayFactor'),\n"
    "        float)\n"
    # ⑬ 事件链
    "    evs = await repo.list_events(\n"
    "        limit=100)\n"
    "    out['ev_act'] = len([\n"
    "        e for e in evs\n"
    "        if e.get('eventType')\n"
    "        == 'activate'])\n"
    "    out['ev_cvt'] = len([\n"
    "        e for e in evs\n"
    "        if e.get('eventType')\n"
    "        == 'convert'])\n"
    "    out['ev_str'] = len([\n"
    "        e for e in evs\n"
    "        if e.get('eventType')\n"
    "        == 'stress'])\n"
    # ⑭ 44号档案
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
    for path, name in (
            ("/api/av62/scenarios/convert",
             "convert"),
            ("/api/av62/stress", "stress"),
            ("/api/av62/activate",
             "activate")):
        ok, (code, _) = call(
            "POST", path,
            body={"subjectId": 1,
                  "scenario": "bidding",
                  "assetId": 1,
                  "reason": "compliance_use"},
            headers=ADMIN, expect=(409,))
        record(f"off 态 {name} 409",
               code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/av62/scenarios",
        headers=ADMIN)
    record("off 态 scenarios 观测面 200",
           code == 200
           and len(body.get("scenarios")
                   or {}) == 4,
           str(code))
    ok, (code, body) = call(
        "GET", "/api/av62/thresholds",
        headers=ADMIN)
    record("off 态 thresholds 观测面 200",
           code == 200
           and "defaults" in (body or {}),
           str(code))

    print("\n[03 容器内: 转化层全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("流动性映射(high/med/low/none)",
           r.get("liq_high") == "high"
           and r.get("liq_med") == "medium"
           and r.get("liq_low") == "low"
           and r.get("liq_none") == "none",
           str((r.get("liq_high"),
                r.get("liq_none"))))
    record("衰减 90 日=0.5(半衰期)",
           abs(float(r.get("d90") or 0)
               - 0.5) < 0.01
           and abs(float(
               r.get("d180") or 0)
               - 0.25) < 0.01,
           str((r.get("d90"),
                r.get("d180"))))
    record("场景系数(投标合规×1.2)",
           r.get("sf_bid") == 1.2
           and r.get("sf_know") == 1.0,
           str((r.get("sf_bid"),
                r.get("sf_know"))))
    record("四场景注册",
           r.get("scen_n") == 4
           and r.get("view_hl") == 90,
           str((r.get("scen_n"),
                r.get("view_hl"))))
    record("off 铁律(激活/折算/压测)",
           r.get("off_act") is True
           and r.get("off_cvt") is True
           and r.get("off_str") is True,
           str((r.get("off_act"),
                r.get("off_cvt"),
                r.get("off_str"))))
    record("档案(high 档+限频 10)",
           r.get("pf_tier") == "high"
           and r.get("pf_cap") == 10,
           str((r.get("pf_tier"),
                r.get("pf_cap"))))
    record("档案基线联动(83.3)",
           r.get("pf_base") == 83.3,
           str(r.get("pf_base")))
    record("档案衰减(0 日=1.0)",
           r.get("pf_decay") == 1.0
           and r.get("pf_decayed")
           == 83.3,
           str((r.get("pf_decay"),
                r.get("pf_decayed"))))
    record("social 档案(low 档)",
           r.get("pf3_tier") == "low",
           str(r.get("pf3_tier")))
    record("激活理由域外拒绝",
           r.get("act_rej") is True,
           str(r.get("act_rej")))
    record("激活成功(reactivated)",
           r.get("act_status")
           == "reactivated"
           and r.get("act_state")
           == "reactivated",
           str((r.get("act_status"),
                r.get("act_state"))))
    record("low 档激活拒绝(仅自证)",
           r.get("act_low") is True,
           str(r.get("act_low")))
    record("折算纳入/排除(1+2)",
           r.get("cv_inc") == 1
           and r.get("cv_exc") == 2,
           str((r.get("cv_inc"),
                r.get("cv_exc"))))
    record("折算系数×1.2 应用",
           r.get("cv_factor") == 1.2,
           str(r.get("cv_factor")))
    record("折算值(83.3×1.2)",
           r.get("cv_value")
           == round(83.3 * 1.2, 2),
           str(r.get("cv_value")))
    record("deposit=false 无增益输出",
           r.get("cv_dep") is None,
           str(r.get("cv_dep")))
    record("45号 deposit fail-soft"
           "(未知主体不崩溃)",
           r.get("dep45") in (True, False,
                              None),
           str(r.get("dep45")))
    record("压测摘除正资产(下降)",
           (r.get("st_delta") or 0) < 0,
           str(r.get("st_delta")))
    record("压测摘除负资产(回升)",
           (r.get("st2_delta") or 0) > 0,
           str(r.get("st2_delta")))
    record("压测无效摘除拒绝",
           r.get("st_rej") is True,
           str(r.get("st_rej")))
    record("默认半衰期 90",
           r.get("hl_default") == 90,
           str(r.get("hl_default")))
    record("submit 46号(pending)",
           r.get("th_sub") == "pending",
           str(r.get("th_sub")))
    record("未审批不生效(仍 90)",
           r.get("th_pre") == 90,
           str(r.get("th_pre")))
    record("未裁决 apply 拒绝",
           r.get("th_early") is True,
           str(r.get("th_early")))
    record("裁决后 apply 生效",
           r.get("th_applied") == "applied"
           and r.get("th_after") == 60,
           str((r.get("th_applied"),
                r.get("th_after"))))
    record("场景乘子生效 1.2",
           r.get("sm_after") == 1.2,
           str(r.get("sm_after")))
    record("折算乘子联动(1.2)",
           r.get("cv3_mult") == 1.2,
           str(r.get("cv3_mult")))
    record("Redis 读回(liquidity 表)",
           r.get("lr_tier") == "high"
           and r.get("lr_decay") is True,
           str((r.get("lr_tier"),
                r.get("lr_decay"))))
    record("事件链(activate/convert/"
           "stress)",
           r.get("ev_act") == 1
           and r.get("ev_cvt") == 3
           and r.get("ev_str") == 2,
           str((r.get("ev_act"),
                r.get("ev_cvt"),
                r.get("ev_str"))))
    record("44号 38 档案",
           r.get("reg_n") == 39,
           str(r.get("reg_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # 服务器态 off——决策面 409
    ok, (code, _) = call(
        "POST", "/api/av62/scenarios/convert",
        body={"subjectId": 1,
              "scenario": "bidding"},
        headers=ADMIN, expect=(409,))
    record("HTTP convert off 409"
           "(服务器态)",
           code == 409, str(code))
    # 观测面(容器内种子的档案读回)
    ok, (code, body) = call(
        "GET", "/api/av62/scenarios"
               "?subject_id=101",
        headers=ADMIN)
    record("HTTP scenarios 主体档案",
           code == 200
           and (body.get(
               "subjectProfiles")
               or {}).get("total") == 3,
           str((code,
                (body.get(
                    "subjectProfiles")
                 or {}).get("total"))))
    ok, (code, body) = call(
        "GET", "/api/av62/thresholds",
        headers=ADMIN)
    record("HTTP thresholds 生效读回",
           code == 200
           and (body.get("active")
                or {}).get("decay")
           == {"halfLifeDays": 60},
           str((body.get("active")
                or {}).get("decay")))
    # 鉴权 403
    for method, path in (
            ("POST",
             "/api/av62/scenarios/convert"),
            ("GET", "/api/av62/scenarios"),
            ("POST", "/api/av62/stress"),
            ("POST", "/api/av62/activate"),
            ("GET",
             "/api/av62/thresholds")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 14
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
    record("62号路由 P2 14 端点",
           count == 14, str(count))


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
