"""61号AI智能系统升级决策 P0 Docker 实机验收

运行方式:
    python verify_dm61_p0_live.py [基址]

前置: 容器已运行(含 61号 P0 代码)。

覆盖(61号计划 §七 P0, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(requests 409;
       registry/model 观测面 200)
    03 容器内: 请求底座全链
       (三源+语义标签+影响面+环境感知
        +状态机+Redis 读回)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
dm61 键域)。
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


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['DM61_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 注册表
    "    from services.dm61_registry import (\n"
    "        SEMANTIC_TAGS, DEPENDENCY_MAP,\n"
    "        parse_semantic_tag,\n"
    "        predict_impact, check_window,\n"
    "        registry_view)\n"
    "    out['tags'] = len(SEMANTIC_TAGS)\n"
    "    out['deps'] = len(DEPENDENCY_MAP)\n"
    "    view = registry_view()\n"
    "    out['reg_tags'] = (\n"
    "        view.get('semanticTags'))\n"
    # ② 语义标签轨(确定性)
    "    s1 = parse_semantic_tag(\n"
    "        '支付结算费率优化', '')\n"
    "    out['s1_tag'] = s1.get('tag')\n"
    "    out['s1_sens'] = (\n"
    "        s1.get('sensitivity'))\n"
    "    s2 = parse_semantic_tag(\n"
    "        '后台权限角色调整', '')\n"
    "    out['s2_tag'] = s2.get('tag')\n"
    "    out['s2_sens'] = (\n"
    "        s2.get('sensitivity'))\n"
    # ③ 影响面+窗口
    "    i1 = predict_impact('payment_opt')\n"
    "    out['i1_pct'] = (\n"
    "        i1.get('impactPct'))\n"
    "    out['i1_roles'] = len(\n"
    "        i1.get('roles'))\n"
    "    w1 = check_window(3)\n"
    "    out['w1_level'] = (\n"
    "        w1.get('level'))\n"
    "    w2 = check_window(20)\n"
    "    out['w2_level'] = (\n"
    "        w2.get('level'))\n"
    # ④ 请求底座(三源——Redis 态)
    "    from services.dm61_service import (\n"
    "        Dm61Service)\n"
    "    svc = Dm61Service()\n"
    "    r1 = await svc.create_request(\n"
    "        '支付结算费率优化',\n"
    "        source='manual', hour=3)\n"
    "    out['r1_status'] = (\n"
    "        r1.get('status'))\n"
    "    out['r1_tag'] = (\n"
    "        (r1.get('semantic') or {})\n"
    "        .get('tag'))\n"
    "    out['r1_fp'] = str(\n"
    "        r1.get('fingerprint')\n"
    "        or '')[:7]\n"
    "    r2 = await svc.create_request(\n"
    "        '核心链路重构提案',\n"
    "        source='proposal',\n"
    "        proposal_id=101, hour=20)\n"
    "    out['r2_tag'] = (\n"
    "        (r2.get('semantic') or {})\n"
    "        .get('tag'))\n"
    "    out['r2_sens'] = (\n"
    "        (r2.get('semantic') or {})\n"
    "        .get('sensitivity'))\n"
    "    out['r2_window'] = (\n"
    "        (r2.get('environment') or {})\n"
    "        .get('level'))\n"
    "    r3 = await svc.create_request(\n"
    "        '算法权重调整信号',\n"
    "        source='signal', signal_id=55,\n"
    "        hour=8)\n"
    "    out['r3_tag'] = (\n"
    "        (r3.get('semantic') or {})\n"
    "        .get('tag'))\n"
    # ⑤ Redis 读回(request 结构)
    "    from repositories.dm61_repository "
    "import Dm61Repository\n"
    "    repo = Dm61Repository()\n"
    "    d1 = await repo.get_request(1)\n"
    "    out['d1_tag'] = d1.get('tag')\n"
    "    out['d1_sem_dict'] = isinstance(\n"
    "        d1.get('semantic'), dict)\n"
    "    out['d1_impact_dict'] = isinstance(\n"
    "        d1.get('impact'), dict)\n"
    "    out['d1_env_dict'] = isinstance(\n"
    "        d1.get('environment'), dict)\n"
    # ⑥ 列表+过滤
    "    lv = await svc.list_requests()\n"
    "    out['lv_total'] = lv.get('total')\n"
    "    lf = await svc.list_requests(\n"
    "        source='proposal')\n"
    "    out['lf_total'] = lf.get('total')\n"
    # ⑦ 第36档案
    "    from services.dm61_scorer import (\n"
    "        Dm61Scorer)\n"
    "    sc = await Dm61Scorer().score({\n"
    "        'decisionAccuracy': 0.95,\n"
    "        'tier': 'trusted'})\n"
    "    out['sc_factors'] = len(\n"
    "        sc.get('factors') or [])\n"
    "    out['sc_decision'] = (\n"
    "        sc.get('decision'))\n"
    # ⑧ 44号 37 档案
    "    from services.ai_learning_service "
    "import SCORER_REGISTRY\n"
    "    out['reg_n'] = len(SCORER_REGISTRY)\n"
    "    out['do_in_reg'] = (\n"
    "        'decision_orchestration'\n"
    "        in SCORER_REGISTRY)\n"
    # ⑨ 56号零改动
    "    import services.aiup56_service "
    "as s56\n"
    "    out['s56_ok'] = s56 is not None\n"
    # ⑩ 事件留痕
    "    evs = await repo.list_events(\n"
    "        limit=50)\n"
    "    out['ev_n'] = len([\n"
    "        e for e in evs\n"
    "        if e.get('eventType')\n"
    "        == 'request'])\n"
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

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/dm61/requests",
        body={"title": "支付费率优化"},
        headers=ADMIN, expect=(409,))
    record("off 态 requests 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/dm61/registry",
        headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and (body.get("semanticTags")
                or 0) == 6,
           str((code,
                body.get("semanticTags"))))
    ok, (code, body) = call(
        "GET", "/api/dm61/model/status",
        headers=ADMIN)
    record("off 态 model/status 观测面 200",
           code == 200
           and ((body.get("status")
                 or {}).get("scorerId")
                == "decision_orchestration"),
           str(code))

    print("\n[03 容器内: 请求底座全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("语义标签六类",
           r.get("tags") == 6
           and r.get("reg_tags") == 6,
           str((r.get("tags"),
                r.get("reg_tags"))))
    record("依赖映射六条",
           r.get("deps") == 6,
           str(r.get("deps")))
    record("语义轨: 支付→payment_opt",
           r.get("s1_tag") == "payment_opt"
           and r.get("s1_sens")
           == "sensitive",
           str((r.get("s1_tag"),
                r.get("s1_sens"))))
    record("语义轨: 权限→critical 铁律",
           r.get("s2_tag")
           == "permission_change"
           and r.get("s2_sens")
           == "critical",
           str((r.get("s2_tag"),
                r.get("s2_sens"))))
    record("影响面(支付 3%+两角色)",
           r.get("i1_pct") == 3.0
           and r.get("i1_roles") == 2,
           str((r.get("i1_pct"),
                r.get("i1_roles"))))
    record("窗口(深夜适宜/高峰收紧)",
           r.get("w1_level") == "suitable"
           and r.get("w2_level")
           == "caution",
           str((r.get("w1_level"),
                r.get("w2_level"))))
    record("人工源创建(tagged)",
           r.get("r1_status") == "tagged"
           and r.get("r1_tag")
           == "payment_opt",
           str((r.get("r1_status"),
                r.get("r1_tag"))))
    record("指纹链(sha256)",
           r.get("r1_fp") == "sha256:",
           str(r.get("r1_fp")))
    record("提案源创建(critical)",
           r.get("r2_tag") == "core_refactor"
           and r.get("r2_sens")
           == "critical",
           str((r.get("r2_tag"),
                r.get("r2_sens"))))
    record("提案源窗口 caution",
           r.get("r2_window") == "caution",
           str(r.get("r2_window")))
    record("信号源创建(algo_param)",
           r.get("r3_tag") == "algo_param",
           str(r.get("r3_tag")))
    record("Redis 读回(tag 字段)",
           r.get("d1_tag") == "payment_opt",
           str(r.get("d1_tag")))
    record("Redis 读回(semantic dict)",
           r.get("d1_sem_dict") is True,
           str(r.get("d1_sem_dict")))
    record("Redis 读回(impact dict)",
           r.get("d1_impact_dict") is True,
           str(r.get("d1_impact_dict")))
    record("Redis 读回(environment dict)",
           r.get("d1_env_dict") is True,
           str(r.get("d1_env_dict")))
    record("列表观测(3 条)",
           r.get("lv_total") == 3,
           str(r.get("lv_total")))
    record("列表来源过滤(1 条)",
           r.get("lf_total") == 1,
           str(r.get("lf_total")))
    record("第36档案八因子",
           r.get("sc_factors") == 8,
           str(r.get("sc_factors")))
    record("高分决策 optimize",
           r.get("sc_decision")
           in ("optimize", "urgent"),
           str(r.get("sc_decision")))
    record("44号 37 档案",
           r.get("reg_n") == 37,
           str(r.get("reg_n")))
    record("decision_orchestration 在册",
           r.get("do_in_reg") is True,
           str(r.get("do_in_reg")))
    record("56号零改动(模块在册)",
           r.get("s56_ok") is True,
           str(r.get("s56_ok")))
    record("事件链(request×3)",
           r.get("ev_n") == 3,
           str(r.get("ev_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # 服务器态 off——决策面 409(铁律)
    ok, (code, _) = call(
        "POST", "/api/dm61/requests",
        body={"title": "支付费率优化"},
        headers=ADMIN, expect=(409,))
    record("HTTP requests off 409(服务器态)",
           code == 409, str(code))
    # 观测面(容器内种子的请求记录读回)
    ok, (code, body) = call(
        "GET", "/api/dm61/requests",
        headers=ADMIN)
    record("HTTP requests 列表(Redis 读回)",
           code == 200
           and (body.get("total") or 0)
           >= 3,
           str((code, body.get("total"))))
    # 鉴权 403
    for method, path in (
            ("GET", "/api/dm61/registry"),
            ("POST", "/api/dm61/requests"),
            ("GET", "/api/dm61/requests"),
            ("GET",
             "/api/dm61/model/status")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 5
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
