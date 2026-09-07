"""全站批次四·治理类三模块 AI 升级
Docker 实机验收(verify_batch4_ai_live)

运行方式:
    python verify_batch4_ai_live.py [基址]

前置: 容器已运行(含批次四 trace_integrity/
      compliance_inspection/citystore_health
      代码——AI_ENFORCE_MODE 默认 observe)。

覆盖(《全站AI智能混合架构升级总计划》
批次四交付面, 真实容器 Redis 态):
    01 正常业务零影响(健康+三模块面)
    02 44号注册表 48 档案(三评分器
       batch30-32 入册)
    03 22追溯: 激活 observe 兼容+快照
       留痕+orderId 分润契约(容器管道)
    04 24合规: 巡检 observe 兼容+回流
       (HTTP)
    05 25市级网店: 考核 observe 兼容+
       回流(容器管道)
    06 红队RT-01 一瓶一激活硬规则
    07 红队RT-02 非法风险等级拒绝
    08 enforce 高风险拦截(容器内单元)
    09 AI 观测面 HTTP(三评分器)

×2 轮幂等验证(每轮清理种子重造——
trace/compliance/citystore 键域+三
评分器学习键域)。
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
        with urllib.request.urlopen(req, timeout=15) as r:
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


def clear_batch4(round_no: int) -> None:
    redis_del_keys("zhuxiang:trace:*")
    redis_del_keys("zhuxiang:compliance:*")
    redis_del_keys("zhuxiang:citystore:*")
    for scorer in ("trace_integrity",
                   "compliance_inspection",
                   "citystore_health"):
        redis_del_keys(
            f"zhuxiang:ai_learning:*{scorer}*")


def run_pipeline(script: str) -> dict:
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr
                          or "无输出")[-1500:]}


# ------------------------------------------------------------
# ① 追溯门管道(生成→激活→重复激活硬规则)
# ------------------------------------------------------------
TRACE_PIPELINE = (
    "import asyncio, json\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.trace_service import (\n"
    "        TraceService)\n"
    "    tsvc = TraceService()\n"
    "    codes = await tsvc.generate_life_codes(\n"
    "        'ZX42-B4L', 'B4L批次', 3)\n"
    "    life_code = codes['lifeCodes'][0][\n"
    "        'lifeCode']\n"
    "    r = await tsvc.activate_life_code(\n"
    "        life_code, user_id=884001,\n"
    "        user_name='B4L用户',\n"
    "        purchase_channel='online',\n"
    "        purchase_price=288.0,\n"
    "        order_id='O-B4L-1')\n"
    "    out['active'] = (\n"
    "        (r or {}).get('status') == 'active')\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    "    snap = await repo.get_decision"
    "_snapshot(\n"
    "        'trace_integrity',\n"
    "        f'activate:{life_code}')\n"
    "    out['snap'] = snap is not None\n"
    "    if snap:\n"
    "        out['dec'] = snap.get('decision')\n"
    "        out['factors'] = len(\n"
    "            snap.get('factors') or [])\n"
    "    life = await tsvc.repo.get_life"
    "_by_code(life_code)\n"
    "    out['order'] = (\n"
    "        (life or {}).get('orderId')\n"
    "        == 'O-B4L-1')\n"
    "    out['life_code'] = life_code\n"
    "    reactivated = False\n"
    "    try:\n"
    "        await tsvc.activate_life_code(\n"
    "            life_code, user_id=884001)\n"
    "    except ValueError:\n"
    "        reactivated = True\n"
    "    out['react_blocked'] = reactivated\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


# ------------------------------------------------------------
# ② 市级网店考核管道(开店→审核→订单→考核)
# ------------------------------------------------------------
CITYSTORE_PIPELINE = (
    "import asyncio, json\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.citystore_service import (\n"
    "        CityStoreService)\n"
    "    ssvc = CityStoreService()\n"
    "    store = await ssvc.apply(\n"
    "        member_id=884101, member_level=5,\n"
    "        store_name='B4L测试网店',\n"
    "        city_code='370100',\n"
    "        city_name='济南市',\n"
    "        province_code='370000',\n"
    "        province_name='山东省',\n"
    "        business_license='91370100B4LXX',\n"
    "        food_license='JY1370100B4L',\n"
    "        tax_reg_no='91370100B4LXX')\n"
    "    sc = store['storeCode']\n"
    "    await ssvc.audit_store(sc, 'admin', True)\n"
    "    await ssvc.add_order(\n"
    "        sc, 'O-B4L-S1', 'P1', 'B4L酒',\n"
    "        50, 288.0, 14400.0,\n"
    "        sales_channel=1)\n"
    "    r = await ssvc.run_assessment(\n"
    "        sc, '2026-08')\n"
    "    out['qualified'] = (\n"
    "        r.get('qualificationStatus') in\n"
    "        (1, 2, 3))\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    "    fbs = await repo.list_feedback(\n"
    "        'citystore_health')\n"
    "    out['fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and (f.get('actualAction') or ''\n"
    "             ).startswith('qual_')])\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


# ------------------------------------------------------------
# ③ enforce 模式容器内单元
# ------------------------------------------------------------
ENFORCE_PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AI_ENFORCE_MODE'] = 'enforce'\n"
    "os.environ['AI_ENFORCE_SCOPES'] = "
    "'trace_integrity,compliance_inspection,"
    "citystore_health'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    "    for sid in ('trace_integrity',\n"
    "                'compliance_inspection',\n"
    "                'citystore_health'):\n"
    "        for _ in range(55):\n"
    "            await repo.add_feedback({\n"
    "                'scorerId': sid,\n"
    "                'factors': [{'name': 'x',\n"
    "                             'score': 10.0,\n"
    "                             'weight': 0.5}],\n"
    "                'correct': True,\n"
    "                'createdAt':\n"
    "                    '2026-01-01T00:00:00'})\n"
    "    from services.ai_enforcement_governance"
    " import (\n"
    "        enrich_trace_activation,\n"
    "        enforce_trace_activation,\n"
    "        enrich_behavior_monitor,\n"
    "        enforce_behavior_monitor,\n"
    "        enrich_citystore_health,\n"
    "        enforce_citystore_assessment)\n"
    # ① 激活高风险拦截
    "    ctx = await enrich_trace_activation(\n"
    "        {'lifeCode': 'BLC-B4L-HIGH',\n"
    "         'orderId': '', 'boxCodeId': None},\n"
    "        884002)\n"
    "    ctx.update(crossRegion=True,\n"
    "               purchaseChannel='',\n"
    "               purchasePrice=0,\n"
    "               boxCode='', boxOpened=True,\n"
    "               userActivationCount=5)\n"
    "    blocked = False\n"
    "    try:\n"
    "        await enforce_trace_activation(\n"
    "            'BLC-B4L-HIGH', ctx)\n"
    "    except ValueError:\n"
    "        blocked = True\n"
    "    out['tr_blocked'] = blocked\n"
    # ② 巡检高风险拦截
    "    ctx2 = await enrich_behavior_monitor(\n"
    "        'B4L模块', 'withdraw',\n"
    "        {'amount': 60000})\n"
    "    ctx2.update(\n"
    "        dailyTotal=250000.0,\n"
    "        entityViolations=3, frequency=5,\n"
    "        hasEvidence=False, manualFlag=True)\n"
    "    blocked = False\n"
    "    try:\n"
    "        await enforce_behavior_monitor(\n"
    "            'B4L-key-1', ctx2)\n"
    "    except ValueError:\n"
    "        blocked = True\n"
    "    out['cp_blocked'] = blocked\n"
    # ③ 考核高风险拦截
    "    ctx3 = await enrich_citystore_health(\n"
    "        {'storeCode': 'CS-B4L',\n"
    "         'consecutiveBelowPurchase': 3,\n"
    "         'consecutiveBelowSales': 3,\n"
    "         'currentDiscount': 70,\n"
    "         'status': 3}, 100, 200)\n"
    "    blocked = False\n"
    "    try:\n"
    "        await enforce_citystore"
    "_assessment(\n"
    "            'CS-B4L', '2026-08', ctx3)\n"
    "    except ValueError:\n"
    "        blocked = True\n"
    "    out['cs_blocked'] = blocked\n"
    # ④ 低风险放行(激活门)
    "    ctx_ok = await enrich_trace"
    "_activation(\n"
    "        {'lifeCode': 'BLC-B4L-LOW',\n"
    "         'orderId': 'O1',\n"
    "         'boxCodeId': None}, 884003)\n"
    "    ok = False\n"
    "    try:\n"
    "        r = await enforce_trace"
    "_activation(\n"
    "            'BLC-B4L-LOW', ctx_ok)\n"
    "        ok = r.get('blocked') is False\n"
    "    except ValueError:\n"
    "        ok = False\n"
    "    out['tr_ok'] = ok\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


def registry_check() -> dict:
    return run_pipeline(
        "import asyncio, json\n"
        "async def m():\n"
        "    from services.ai_learning"
        "_service import (\n"
        "        SCORER_REGISTRY)\n"
        "    print(json.dumps({\n"
        "        'reg_n': len(SCORER_REGISTRY),\n"
        "        'ti': 'trace_integrity'\n"
        "              in SCORER_REGISTRY,\n"
        "        'ci': 'compliance_inspection'\n"
        "              in SCORER_REGISTRY,\n"
        "        'ch': 'citystore_health'\n"
        "              in SCORER_REGISTRY}))\n"
        "asyncio.run(m())\n")


def feedback_count(scorer: str) -> dict:
    return run_pipeline(
        "import asyncio, json\n"
        "async def m():\n"
        "    from repositories.ai_learning"
        "_repository import (\n"
        "        AiLearningRepository)\n"
        "    repo = AiLearningRepository()\n"
        "    fbs = await repo.list_feedback(\n"
        f"        '{scorer}')\n"
        "    auto = [f for f in fbs\n"
        "            if f.get('source') == 'auto']\n"
        "    print(json.dumps({'n': len(auto)}))\n"
        "asyncio.run(m())\n")


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_batch4(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/trace/stats",
                         headers=ADMIN)
    record("追溯面 200", code == 200, str(code))
    ok, (code, _) = call(
        "GET", "/api/compliance/stats", headers=ADMIN)
    record("合规面 200", code == 200, str(code))

    print("\n[02 44号注册表]")
    reg = registry_check()
    record("48 档案(batch30-32 入册)",
           reg.get("reg_n") == 48
           and reg.get("ti") is True
           and reg.get("ci") is True
           and reg.get("ch") is True,
           str(reg))

    print("\n[03 22追溯激活门(容器管道)]")
    r = run_pipeline(TRACE_PIPELINE)
    record("激活 observe 兼容",
           r.get("active") is True, str(r))
    record("激活快照低风险+八因子",
           r.get("snap") is True
           and r.get("dec") == "low"
           and r.get("factors") == 8,
           str(r))
    record("orderId 分润契约",
           r.get("order") is True, str(r))
    record("红队RT-01 一瓶一激活",
           r.get("react_blocked") is True,
           str(r))

    print("\n[04 24合规巡检门(HTTP)]")
    ok, (code, body) = call(
        "POST", "/api/compliance/behavior/monitor",
        body={"moduleName": "B4L模块",
              "behaviorType": "browse",
              "behaviorData": {"amount": 100},
              "riskLevel": "low"},
        headers=ADMIN)
    record("巡检 observe 兼容",
           code == 200
           and (body.get("riskLevel") or
                (body.get("data") or {})
                .get("riskLevel")) == "low",
           f"code={code}")
    fb = feedback_count("compliance_inspection")
    record("巡检→自动反馈",
           (fb.get("n") or 0) >= 1, str(fb))
    ok, (code, _) = call(
        "POST", "/api/compliance/behavior/monitor",
        body={"moduleName": "B4L模块",
              "behaviorType": "browse",
              "riskLevel": "invalid"},
        headers=ADMIN, expect=(400, 422, 409))
    record("红队RT-02 非法等级拒绝",
           code in (400, 422, 409), str(code))

    print("\n[05 25市级网店考核门(容器管道)]")
    r = run_pipeline(CITYSTORE_PIPELINE)
    record("考核 observe 兼容",
           r.get("qualified") is True, str(r))
    record("考核→自动反馈",
           (r.get("fb") or 0) >= 1, str(r))

    print("\n[06 enforce 拦截(容器内)]")
    r = run_pipeline(ENFORCE_PIPELINE)
    record("激活拦截+低风险放行",
           r.get("tr_blocked") is True
           and r.get("tr_ok") is True,
           str(r))
    record("巡检高风险拦截",
           r.get("cp_blocked") is True, str(r))
    record("考核高风险拦截",
           r.get("cs_blocked") is True, str(r))

    print("\n[07 AI 观测面 HTTP]")
    for scorer in ("trace_integrity",
                   "compliance_inspection",
                   "citystore_health"):
        ok, (code, body) = call(
            "GET",
            f"/api/ai-learning/enforcement/"
            f"{scorer}/overview",
            headers=ADMIN)
        record(f"{scorer} overview 200",
               code == 200, str(code))


def main() -> int:
    print("=" * 62)
    print("全站批次四·治理类三模块 AI 升级"
          " Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)
    for i in (1, 2):
        run_round(i)
    print(f"\n{'=' * 62}\n验收汇总: "
          f"{PASS} 通过 / {FAIL} 失败\n{'=' * 62}")
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
