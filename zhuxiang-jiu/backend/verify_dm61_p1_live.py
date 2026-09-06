"""61号AI智能系统升级决策 P1 Docker 实机验收

运行方式:
    $env:PAY60_MODE=""  # 无关
    # 容器以 shadow 态启动(HTTP 决策面):
    $env:DM61_MODE="shadow"
    docker compose -p zhuxiang-jiu up -d backend
    python verify_dm61_p1_live.py [基址]

覆盖(61号计划 §七 P1, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-04 容器内: 三级决策矩阵全链
       (四因子评估+L1/L2/L3+窗口升级
        +Top3 方案+人类裁决+46号总线)
    05 HTTP 面(assess/recommend/decide
       +终审不受开关影响)

×2 轮幂等验证(每轮清理种子重造——
dm61+46号 change 键域)。
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
    # 造链助手
    "    from services.dm61_service import (\n"
    "        Dm61Service)\n"
    "    from services.dm61_assess_service "
    "import (\n"
    "        Dm61AssessService)\n"
    "    from services.dm61_decision_service "
    "import (\n"
    "        Dm61DecisionService)\n"
    "    base = Dm61Service()\n"
    "    asvc = Dm61AssessService()\n"
    "    dsvc = Dm61DecisionService()\n"
    # ① L1 链(观测类——无关键词命中
    #    fallback observe)
    "    r1 = await base.create_request(\n"
    "        '文案微调', hour=3)\n"
    "    a1 = await asvc.assess(\n"
    "        r1['requestId'], tier='trusted',\n"
    "        error_budget=0.9,\n"
    "        history_fail_rate=0.0)\n"
    "    out['l1_level'] = a1.get('level')\n"
    "    out['l1_risk'] = a1.get('riskScore')\n"
    "    rec1 = await dsvc.recommend(\n"
    "        r1['requestId'])\n"
    "    out['l1_rec'] = (\n"
    "        (rec1.get('options') or [{}])[\n"
    "            rec1.get(\n"
    "                'recommendedIndex')\n"
    "            - 1].get('name'))\n"
    # ② L2 链(支付敏感)
    "    r2 = await base.create_request(\n"
    "        '支付结算费率优化', hour=3)\n"
    "    a2 = await asvc.assess(\n"
    "        r2['requestId'],\n"
    "        tier='standard',\n"
    "        error_budget=0.3,\n"
    "        history_fail_rate=0.05)\n"
    "    out['l2_level'] = a2.get('level')\n"
    "    rec2 = await dsvc.recommend(\n"
    "        r2['requestId'])\n"
    "    out['l2_n_opts'] = len(\n"
    "        rec2.get('options') or [])\n"
    "    out['l2_reason'] = str(\n"
    "        rec2.get('reason')\n"
    "        or '')[:7]\n"
    # ③ L3 链(权限变更+双人复核)
    "    r3 = await base.create_request(\n"
    "        '后台权限角色调整', hour=3)\n"
    "    a3 = await asvc.assess(\n"
    "        r3['requestId'],\n"
    "        tier='trusted',\n"
    "        error_budget=0.9,\n"
    "        history_fail_rate=0.0)\n"
    "    out['l3_level'] = a3.get('level')\n"
    "    out['l3_forced'] = a3.get(\n"
    "        'forcedL3Tag')\n"
    "    rec3 = await dsvc.recommend(\n"
    "        r3['requestId'])\n"
    # ④ L3 缺双人复核拒绝
    "    out['l3_dual'] = False\n"
    "    try:\n"
    "        await dsvc.decide(\n"
    "            rec3['decisionId'],\n"
    "            action='adopted',\n"
    "            decided_by='甲')\n"
    "    except ValueError:\n"
    "        out['l3_dual'] = True\n"
    # ⑤ L3 双人复核通过→46号总线
    "    d3 = await dsvc.decide(\n"
    "        rec3['decisionId'],\n"
    "        action='adopted',\n"
    "        decided_by='甲',\n"
    "        co_reviewer='乙')\n"
    "    out['l3_status'] = d3.get(\n"
    "        'status')\n"
    "    out['l3_change'] = (\n"
    "        d3.get('changeId'))\n"
    # ⑥ rejected 裁决(off 亦可)
    "    os.environ['DM61_MODE'] = 'off'\n"
    "    d2 = await dsvc.decide(\n"
    "        rec2['decisionId'],\n"
    "        action='rejected',\n"
    "        decided_by='风控官',\n"
    "        note='暂缓')\n"
    "    out['rej_status'] = d2.get(\n"
    "        'status')\n"
    "    out['rej_outcome'] = d2.get(\n"
    "        'outcome')\n"
    # ⑦ Redis 读回
    "    from repositories.dm61_repository "
    "import (\n"
    "        Dm61Repository)\n"
    "    repo = Dm61Repository()\n"
    "    dec3 = await repo.get_decision(\n"
    "        rec3['decisionId'])\n"
    "    out['rd_level'] = dec3.get('level')\n"
    "    out['rd_opts_list'] = isinstance(\n"
    "        dec3.get('options'), list)\n"
    "    out['rd_trail'] = isinstance(\n"
    "        dec3.get('auditTrail'), list)\n"
    "    out['rd_attr'] = isinstance(\n"
    "        dec3.get('attribution'), dict)\n"
    "    out['rd_outcome'] = dec3.get(\n"
    "        'outcome')\n"
    # ⑧ 请求联动
    "    req3 = await repo.get_request(\n"
    "        r3['requestId'])\n"
    "    out['req_status'] = req3.get(\n"
    "        'status')\n"
    "    req2 = await repo.get_request(\n"
    "        r2['requestId'])\n"
    "    out['req_rej'] = req2.get(\n"
    "        'status')\n"
    # ⑨ 事件链
    "    evs = await repo.list_events(\n"
    "        limit=100)\n"
    "    types = sorted({\n"
    "        e.get('eventType')\n"
    "        for e in evs})\n"
    "    out['ev_types'] = types\n"
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

    print("\n[02-04 容器内: 三级决策矩阵全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("观测类→L1",
           r.get("l1_level") == "L1"
           and (r.get("l1_risk") or 0)
           < 30,
           str((r.get("l1_level"),
                r.get("l1_risk"))))
    record("L1 推荐直接执行",
           r.get("l1_rec") == "直接执行",
           str(r.get("l1_rec")))
    record("支付敏感→L2",
           r.get("l2_level") == "L2",
           str(r.get("l2_level")))
    record("L2 Top3 方案",
           r.get("l2_n_opts") == 3,
           str(r.get("l2_n_opts")))
    record("推荐理由确定性(前缀)",
           r.get("l2_reason")
           == "推荐 灰度执行",
           str(r.get("l2_reason")))
    record("权限变更→强制 L3",
           r.get("l3_level") == "L3"
           and r.get("l3_forced") is True,
           str((r.get("l3_level"),
                r.get("l3_forced"))))
    record("L3 缺双人复核拒绝",
           r.get("l3_dual") is True,
           str(r.get("l3_dual")))
    record("L3 双人复核→46号总线",
           r.get("l3_status")
           == "executed_track"
           and (r.get("l3_change")
                or 0) > 0,
           str((r.get("l3_status"),
                r.get("l3_change"))))
    record("rejected 裁决(off 可用)",
           r.get("rej_status") == "decided"
           and r.get("rej_outcome")
           == "rejected",
           str((r.get("rej_status"),
                r.get("rej_outcome"))))
    record("Redis 读回(level)",
           r.get("rd_level") == "L3",
           str(r.get("rd_level")))
    record("Redis 读回(options list)",
           r.get("rd_opts_list") is True,
           str(r.get("rd_opts_list")))
    record("Redis 读回(auditTrail list)",
           r.get("rd_trail") is True,
           str(r.get("rd_trail")))
    record("Redis 读回(attribution dict)",
           r.get("rd_attr") is True,
           str(r.get("rd_attr")))
    record("请求联动(executed_track)",
           r.get("req_status")
           == "executed_track",
           str(r.get("req_status")))
    record("请求联动(closed)",
           r.get("req_rej") == "closed",
           str(r.get("req_rej")))
    record("事件链(assess+recommend+decide)",
           all(t in (r.get("ev_types") or [])
               for t in ("assess",
                         "recommend",
                         "decide")),
           str(r.get("ev_types")))

    print("\n[05 HTTP 面]")
    # 决策面 off 409(服务器态)
    ok, (code, _) = call(
        "POST", "/api/dm61/assess",
        body={"requestId": 1},
        headers=ADMIN, expect=(409,))
    record("HTTP assess off 409(服务器态)",
           code == 409, str(code))
    # 观测面(详情联动)
    ok, (code, body) = call(
        "GET", "/api/dm61/requests/1",
        headers=ADMIN)
    record("HTTP 详情联动(评估+决策)",
           code == 200
           and body.get(
               "latestAssessment")
           is not None
           and body.get(
               "latestDecision")
           is not None,
           str(code))
    # 鉴权 403
    for method, path in (
            ("POST", "/api/dm61/assess"),
            ("POST", "/api/dm61/recommend"),
            ("POST",
             "/api/dm61/decisions/1/decide")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 8
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
    record("61号路由累计 14 端点",
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
