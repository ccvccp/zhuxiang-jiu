"""62号AI智能无形资产估值 P5 Docker 实机验收

运行方式:
    python verify_av62_p5_live.py [基址]

前置: 容器已运行(含 62号 P5 代码)。

覆盖(62号计划 §七 P5, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律+观测面(dashboard
       200; redteam 409)
    03 容器内: 看板四区+红队七向量
       全链(全防御断言+留痕读回)
    04 HTTP 端点+鉴权+收官三件套

×2 轮幂等验证(每轮清理种子重造——
av62+ai46 变更键域)。
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
    redis_del_keys(
        "zhuxiang:ai46:ai46_changes:*")
    redis_del_keys(
        "zhuxiang:ai46:changes_all")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AV62_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 种子: 全链数据
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
    "    r1 = await asm.assess_asset(\n"
    "        a1['assetId'])\n"
    "    a2 = await reg.register_asset(\n"
    "        101, 'enterprise', 'risk',\n"
    "        {'penaltyRecords': 5})\n"
    "    await asm.assess_asset(\n"
    "        a2['assetId'])\n"
    # ② 验证+回流
    "    from services.ai_governance_service "
    "import (\n"
    "        AiGovernanceService)\n"
    "    await AiGovernanceService()"
    ".sync_registry()\n"
    "    from services.av62_learn_service "
    "import (\n"
    "        Av62LearnService)\n"
    "    learn = Av62LearnService()\n"
    "    await learn.submit_verification(\n"
    "        r1['assessId'], 85)\n"
    "    await learn.collect_verification()\n"
    # ③ 申诉+裁决
    "    from services.av62_appeal_service "
    "import (\n"
    "        Av62AppealService)\n"
    "    aps = Av62AppealService()\n"
    "    ap = await aps.submit_appeal(\n"
    "        a1['assetId'], 'reason')\n"
    "    await aps.review_appeal(\n"
    "        ap['appealId'],\n"
    "        decision='overturn',\n"
    "        reviewed_by='gov',\n"
    "        review_note='accepted')\n"
    # ④ 公平审计
    "    from services.av62_fairness"
    "_service import (\n"
    "        Av62FairnessService)\n"
    "    await Av62FairnessService()"
    ".run_audit()\n"
    # ⑤ 红队七向量(off 拒绝+全量)
    "    from services.av62_redteam"
    "_service import (\n"
    "        Av62RedteamService)\n"
    "    rt = Av62RedteamService()\n"
    "    os.environ['AV62_MODE'] = 'off'\n"
    "    try:\n"
    "        await rt.run_all()\n"
    "        out['rt_off_rej'] = False\n"
    "    except ValueError:\n"
    "        out['rt_off_rej'] = True\n"
    "    os.environ['AV62_MODE'] = "
    "'shadow'\n"
    "    r = await rt.run_all()\n"
    "    out['rt_defended'] = (\n"
    "        r.get('defendedAll'))\n"
    "    out['rt_n'] = len(\n"
    "        r.get('vectors') or [])\n"
    "    out['rt_vecs'] = sorted(\n"
    "        v.get('vector')\n"
    "        for v in r.get(\n"
    "            'vectors') or [])\n"
    # ⑥ 看板四区
    "    from services.av62_dashboard"
    "_service import (\n"
    "        Av62DashboardService)\n"
    "    d = await Av62DashboardService()"
    ".get_dashboard()\n"
    "    zones = d.get('zones') or {}\n"
    "    out['db_zones'] = sorted(\n"
    "        zones)\n"
    "    m = zones.get('metrics') or {}\n"
    "    out['db_acc'] = (\n"
    "        m.get(\n"
    "            'valuationAccuracy'))\n"
    "    out['db_grounded'] = (\n"
    "        m.get(\n"
    "            'attributionGrounded'))\n"
    "    out['db_overturn'] = (\n"
    "        m.get(\n"
    "            'appealOverturnRate'))\n"
    "    out['db_fair'] = (\n"
    "        (m.get('fairness') or {})\n"
    "        .get('insufficient'))\n"
    "    a = zones.get('assets') or {}\n"
    "    out['db_assets'] = (\n"
    "        a.get('total'))\n"
    "    out['db_neg'] = (\n"
    "        a.get('negativeCount'))\n"
    "    out['db_liq'] = (\n"
    "        a.get('byLiquidity'))\n"
    "    asmz = zones.get(\n"
    "        'assessments') or {}\n"
    "    out['db_conf'] = (\n"
    "        asmz.get('byConfidence'))\n"
    "    out['db_obj'] = (\n"
    "        asmz.get('objective'))\n"
    "    out['db_pooled'] = (\n"
    "        asmz.get('pooled'))\n"
    "    df = zones.get('defense') or {}\n"
    "    out['db_rt_runs'] = (\n"
    "        df.get('redteamRuns'))\n"
    "    out['db_rt_all'] = ((\n"
    "        df.get('redteamLatest')\n"
    "        or {}).get(\n"
    "            'defendedAll'))\n"
    # ⑦ 红队留痕读回
    "    from repositories.av62_repository "
    "import Av62Repository\n"
    "    repo = Av62Repository()\n"
    "    evs = await repo.list_events(\n"
    "        limit=100)\n"
    "    out['rt_ev'] = len([\n"
    "        e for e in evs\n"
    "        if e.get('eventType')\n"
    "        == 'redteam_run'])\n"
    # ⑧ 44号档案+收官三件套
    "    from services.ai_learning_service "
    "import SCORER_REGISTRY\n"
    "    out['reg_n'] = len(\n"
    "        SCORER_REGISTRY)\n"
    "    out['tables_n'] = len(\n"
    "        Av62Repository._ALL_TABLES)\n"
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
    ok, (code, body) = call(
        "GET", "/api/av62/dashboard",
        headers=ADMIN)
    record("off 态 dashboard 观测面 200",
           code == 200
           and set((body.get("zones")
                    or {})) == {
               "metrics", "assets",
               "assessments",
               "defense"},
           str(code))
    ok, (code, _) = call(
        "POST", "/api/av62/redteam",
        body={},
        headers=ADMIN, expect=(409,))
    record("off 态 redteam 409",
           code == 409, str(code))

    print("\n[03 容器内: 看板+红队全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("红队 off 态拒绝",
           r.get("rt_off_rej") is True,
           str(r.get("rt_off_rej")))
    record("红队七向量全防御",
           r.get("rt_defended") is True
           and r.get("rt_n") == 7,
           str((r.get("rt_defended"),
                r.get("rt_n"))))
    record("红队向量清单",
           r.get("rt_vecs") == [
               "RT-01 证据伪造",
               "RT-02 权重操纵",
               "RT-03 归因幻觉",
               "RT-04 流动性滥用",
               "RT-05 估值套利",
               "RT-06 申诉刷分",
               "RT-07 负资产洗白"],
           str(r.get("rt_vecs")))
    record("看板四区结构",
           r.get("db_zones") == [
               "assessments", "assets",
               "defense", "metrics"],
           str(r.get("db_zones")))
    record("度量区(准确率 1.0)",
           r.get("db_acc") == 1.0,
           str(r.get("db_acc")))
    record("度量区(锚定率 1.0)",
           r.get("db_grounded") == 1.0,
           str(r.get("db_grounded")))
    record("度量区(翻转率 1.0)",
           r.get("db_overturn") == 1.0,
           str(r.get("db_overturn")))
    record("度量区(公平报告读回)",
           r.get("db_fair") is True,
           str(r.get("db_fair")))
    record("资产区(≥4 条+红队种子)",
           (r.get("db_assets") or 0) >= 4
           and (r.get("db_neg") or 0)
           >= 2,
           str((r.get("db_assets"),
                r.get("db_neg"))))
    record("资产区(流动性分布)",
           (r.get("db_liq") or {})
           .get("high") >= 1
           and (r.get("db_liq") or {})
           .get("none") >= 2,
           str(r.get("db_liq")))
    record("评估区(置信度分布)",
           (r.get("db_conf") or {})
           .get("high") >= 1,
           str(r.get("db_conf")))
    record("评估区(objective stability)",
           r.get("db_obj") == "stability",
           str(r.get("db_obj")))
    record("评估区(池化标记)",
           (r.get("db_pooled") or 0) >= 1,
           str(r.get("db_pooled")))
    record("防御区(红队读回+全防)",
           r.get("db_rt_runs") == 1
           and r.get("db_rt_all")
           is True,
           str((r.get("db_rt_runs"),
                r.get("db_rt_all"))))
    record("红队留痕读回(×1)",
           r.get("rt_ev") == 1,
           str(r.get("rt_ev")))
    record("44号 38 档案",
           r.get("reg_n") == 39,
           str(r.get("reg_n")))
    record("收官表 7 全量",
           r.get("tables_n") == 7,
           str(r.get("tables_n")))

    print("\n[04 HTTP 端点+鉴权+收官]")
    ok, (code, body) = call(
        "GET", "/api/av62/dashboard",
        headers=ADMIN)
    defense = ((body.get("zones") or {})
               .get("defense") or {})
    record("HTTP dashboard 防御区读回",
           code == 200
           and defense.get("redteamRuns")
           >= 1,
           str((code,
                defense.get(
                    "redteamRuns"))))
    # 鉴权 403
    for method, path in (
            ("GET", "/api/av62/dashboard"),
            ("POST", "/api/av62/redteam")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 收官: 路由 25
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
    record("收官路由 25 端点",
           count == 25, str(count))


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
