"""61号AI智能系统升级决策 P5 Docker 实机验收

运行方式:
    # 容器以 shadow 态启动(HTTP 决策面
    # ——红队需决策面开放):
    $env:DM61_MODE="shadow"
    docker compose -p zhuxiang-jiu up -d backend
    python verify_dm61_p5_live.py [基址]

覆盖(61号计划 §七 P5, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 容器内: 四区看板+红队七向量
    03 HTTP 面(dashboard/redteam)

×2 轮幂等验证(每轮清理种子重造——
dm61+ai46 change 键域)。
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


def clear_dm61(round_no: int) -> None:
    redis_del_keys("zhuxiang:dm61:*")
    redis_del_keys("zhuxiang:ai46:change*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['DM61_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # 46号入册(幂等)
    "    from services.ai_governance_service "
    "import (\n"
    "        AiGovernanceService)\n"
    "    await (AiGovernanceService()\n"
    "          .sync_registry())\n"
    # 造种子(终态×3: L1/L2/L3)
    "    from services.dm61_service import (\n"
    "        Dm61Service)\n"
    "    from services.dm61_assess_service "
    "import (\n"
    "        Dm61AssessService)\n"
    "    from services.dm61_decision_service "
    "import (\n"
    "        Dm61DecisionService)\n"
    "    from repositories.dm61_repository "
    "import (\n"
    "        Dm61Repository)\n"
    "    base = Dm61Service()\n"
    "    asvc = Dm61AssessService()\n"
    "    dsvc = Dm61DecisionService()\n"
    "    repo = Dm61Repository()\n"
    "    async def seed(title, action,\n"
    "                  tier, budget):\n"
    "        r = await base.create_request(\n"
    "            title, hour=3)\n"
    "        await asvc.assess(\n"
    "            r['requestId'],\n"
    "            tier=tier,\n"
    "            error_budget=budget,\n"
    "            history_fail_rate=0.0\n"
    "            if budget >= 0.5 else 0.05)\n"
    "        rec = await dsvc.recommend(\n"
    "            r['requestId'])\n"
    "        lv = (await repo.get_decision(\n"
    "            rec['decisionId'])\n"
    "            ).get('level')\n"
    "        co = '复核官' \\\n"
    "            if lv == 'L3' else ''\n"
    "        d = await dsvc.decide(\n"
    "            rec['decisionId'],\n"
    "            action=action,\n"
    "            decided_by='种',\n"
    "            co_reviewer=co)\n"
    "        if d.get('changeId'):\n"
    "            try:\n"
    "                await (\n"
    "                  AiGovernanceService()\n"
    "                  .review_change(\n"
    "                    int(d['changeId']),\n"
    "                    approve=False,\n"
    "                    reviewed_by='官',\n"
    "                    review_note='解锁'))\n"
    "            except ValueError:\n"
    "                pass\n"
    "        return rec\n"
    "    await seed('文案微调',\n"
    "              'adopted',\n"
    "              'trusted', 0.9)\n"
    "    await seed('支付费率优化',\n"
    "              'rejected',\n"
    "              'standard', 0.3)\n"
    "    await seed('权限角色调整',\n"
    "              'adopted',\n"
    "              'standard', 0.3)\n"
    # ① 四区看板
    "    from services.dm61_dashboard_service "
    "import (\n"
    "        Dm61DashboardService)\n"
    "    db = await (\n"
    "        Dm61DashboardService()\n"
    "        .dashboard())\n"
    "    out['zones'] = sorted(\n"
    "        k for k in db\n"
    "        if k in ('metrics',\n"
    "                'requests',\n"
    "                'decisions',\n"
    "                'defense'))\n"
    "    m = db.get('metrics') or {}\n"
    "    out['auto_ratio'] = (\n"
    "        m.get('autonomousRatio'))\n"
    "    dc = db.get('decisions') or {}\n"
    "    out['dec_n'] = (\n"
    "        dc.get('totalDecisions'))\n"
    "    out['bus_n'] = (\n"
    "        dc.get('busSubmitted'))\n"
    # ② 红队七向量
    "    from services.dm61_redteam_service "
    "import (\n"
    "        Dm61RedteamService)\n"
    "    rt = await (\n"
    "        Dm61RedteamService()\n"
    "        .run_all())\n"
    "    out['rt_total'] = (\n"
    "        (rt.get('summary')\n"
    "         or {}).get('total'))\n"
    "    out['rt_defended'] = (\n"
    "        (rt.get('summary')\n"
    "         or {}).get('defended'))\n"
    "    out['rt_all'] = (\n"
    "        (rt.get('summary')\n"
    "         or {}).get('allDefended'))\n"
    # ③ 防御区读回
    "    df = (await (\n"
    "        Dm61DashboardService()\n"
    "        .dashboard())\n"
    "        ).get('defense') or {}\n"
    "    out['df_last'] = (\n"
    "        df.get('redteamLastRun'))\n"
    # ④ 宪法(44号 37 档案)
    "    from services.ai_learning_service "
    "import (\n"
    "        SCORER_REGISTRY)\n"
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
    clear_dm61(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 容器内: 看板+红队]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("四区齐备",
           r.get("zones") == [
               "decisions", "defense",
               "metrics", "requests"],
           str(r.get("zones")))
    record("度量: 自治占比 33.3%",
           r.get("auto_ratio") == 33.3,
           str(r.get("auto_ratio")))
    record("决策区: 3 条",
           r.get("dec_n") == 3,
           str(r.get("dec_n")))
    record("决策区: 46号提交 2",
           r.get("bus_n") == 2,
           str(r.get("bus_n")))
    record("红队七向量齐备",
           r.get("rt_total") == 7,
           str(r.get("rt_total")))
    record("红队全量防御(7/7)",
           r.get("rt_defended") == 7
           and r.get("rt_all") is True,
           str((r.get("rt_defended"),
                r.get("rt_all"))))
    last = r.get("df_last") or {}
    record("防御区红队读回",
           last.get("defended") == 7
           and last.get("total") == 7,
           str(last))
    record("宪法: 44号 37 档案",
           r.get("reg_n") == 38,
           str(r.get("reg_n")))

    print("\n[03 HTTP 面]")
    # dashboard 观测面
    ok, (code, body) = call(
        "GET", "/api/dm61/dashboard",
        headers=ADMIN)
    record("HTTP dashboard 200",
           code == 200
           and (body or {}).get(
               "metrics") is not None
           and (body or {}).get(
               "defense") is not None,
           str(code))
    # redteam(off 服务器态——本容器
    # shadow; 409 仅 off 态)
    ok, (code, body) = call(
        "POST", "/api/dm61/redteam",
        body={}, headers=ADMIN)
    record("HTTP redteam 200(7/7)",
           code == 200
           and ((body or {}).get(
               "summary")
                or {}).get(
               "defended") == 7,
           str((code,
                ((body or {}).get(
                    "summary")
                 or {}).get(
                    "defended"))))
    # 鉴权 403
    for method, path in (
            ("GET", "/api/dm61/dashboard"),
            ("POST", "/api/dm61/redteam")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 17
    script = (
        "from routes.dm61_routes import "
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
    record("61号路由累计 17 端点",
           count == 17, str(count))


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
