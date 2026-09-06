"""63号AI智能后台管理 P0 Docker 实机验收

运行方式:
    python verify_ab63_p0_live.py [基址]

前置: 容器已运行(含 63号 P0 代码)。

覆盖(63号计划 §九 P0, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(grants/render 409;
       registry/model 观测面 200)
    03 容器内: 权限裁决+工作台全链
       (Redis 读回——五清单)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
ab63 键域)。
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


def clear_ab63(round_no: int) -> None:
    redis_del_keys("zhuxiang:ab63:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AB63_MODE'] = 'shadow'\n"
    "from core.helpers import ts\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 注册表
    "    from services.ab63_registry import (\n"
    "        ROLE_ACTION_BASE,\n"
    "        WORKBENCH_TEMPLATES,\n"
    "        registry_view, evaluate_permission)\n"
    "    out['rules'] = len(ROLE_ACTION_BASE)\n"
    "    out['templates'] = len(\n"
    "        WORKBENCH_TEMPLATES)\n"
    "    view = registry_view()\n"
    "    out['rule_entries'] = (\n"
    "        view.get('ruleEntries'))\n"
    # ② 权限裁决(Redis 态)
    "    from services.ab63_service import "
    "Ab63Service\n"
    "    svc = Ab63Service()\n"
    "    r1 = await svc.evaluate_grant(\n"
    "        1, 'ally_merchant', 'basic_crud',\n"
    "        tier='trusted',\n"
    "        compliance_rate=0.95)\n"
    "    out['g1_granted'] = (\n"
    "        r1.get('granted'))\n"
    "    out['g1_id'] = r1.get('grantId')\n"
    "    r2 = await svc.evaluate_grant(\n"
    "        2, 'ally_merchant', 'batch_ops',\n"
    "        tier='restricted',\n"
    "        compliance_rate=0.3,\n"
    "        period='peak',\n"
    "        sensitivity='high')\n"
    "    out['g2_granted'] = (\n"
    "        r2.get('granted'))\n"
    "    out['g2_score'] = (\n"
    "        r2.get('score'))\n"
    # ③ Redis 读回(grant 结构)
    "    from repositories.ab63_repository "
    "import Ab63Repository\n"
    "    repo = Ab63Repository()\n"
    "    g1 = await repo.get_grant(\n"
    "        r1.get('grantId'))\n"
    "    out['g1_ctx_tier'] = (\n"
    "        (g1.get('context') or {})\n"
    "        .get('tier'))\n"
    "    out['g1_reason_dict'] = isinstance(\n"
    "        g1.get('reason'), dict)\n"
    "    out['g1_granted_rd'] = (\n"
    "        g1.get('granted'))\n"
    # ④ 工作台渲染
    "    w1 = await svc.render_workbench(\n"
    "        1, 'ally_merchant', novice=True)\n"
    "    out['wb_view'] = w1.get('view')\n"
    "    out['wb_opts_dict'] = isinstance(\n"
    "        (await repo.get_workbench(\n"
    "            w1.get('wbId')))\n"
    "        .get('renderOptions'), dict)\n"
    # ⑤ 第38档案
    "    from services.ab63_scorer import "
    "Ab63Scorer\n"
    "    sc = await Ab63Scorer().score({\n"
    "        'guardEffectiveness': 0.9,\n"
    "        'tier': 'trusted'})\n"
    "    out['sc_factors'] = len(\n"
    "        sc.get('factors') or [])\n"
    "    out['sc_decision'] = (\n"
    "        sc.get('decision'))\n"
    # ⑥ 44号 35 档案
    "    from services.ai_learning_service "
    "import SCORER_REGISTRY\n"
    "    out['reg_n'] = len(SCORER_REGISTRY)\n"
    "    out['ao_in_reg'] = (\n"
    "        'admin_orchestration'\n"
    "        in SCORER_REGISTRY)\n"
    # ⑦ 58号零改动
    "    from services.ii58_registry import (\n"
    "        INTENT_REGISTRY)\n"
    "    out['ii58_n'] = len(INTENT_REGISTRY)\n"
    # ⑧ 事件留痕
    "    evs = await repo.list_events(\n"
    "        limit=50)\n"
    "    types = sorted({e.get('eventType')\n"
    "                   for e in evs})\n"
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
    clear_ab63(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/ab63/grants",
        body={"memberId": 1,
              "role": "ally_merchant",
              "action": "basic_crud"},
        headers=ADMIN, expect=(409,))
    record("off 态 grants 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/ab63/registry",
        headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and (body.get("ruleEntries")
                or 0) == 20,
           str((code,
                body.get("ruleEntries"))))
    ok, (code, body) = call(
        "GET", "/api/ab63/model/status",
        headers=ADMIN)
    record("off 态 model/status 观测面 200",
           code == 200
           and ((body.get("status")
                 or {}).get("scorerId")
                == "admin_orchestration"),
           str(code))

    print("\n[03 容器内: 裁决+工作台全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("四轴规则 20 条",
           r.get("rules") == 20
           and r.get("rule_entries") == 20,
           str((r.get("rules"),
                r.get("rule_entries"))))
    record("四角色模板",
           r.get("templates") == 4,
           str(r.get("templates")))
    record("裁决 trusted+高合规(granted)",
           r.get("g1_granted") is True
           and int(r.get("g1_id")
                   or 0) > 0,
           str((r.get("g1_granted"),
                r.get("g1_id"))))
    record("裁决 restricted+高危拒绝",
           r.get("g2_granted") is False
           and (r.get("g2_score") or 0)
           < 70,
           str((r.get("g2_granted"),
                r.get("g2_score"))))
    record("Redis 读回(上下文 tier)",
           r.get("g1_ctx_tier") == "trusted",
           str(r.get("g1_ctx_tier")))
    record("Redis 读回(reason dict)",
           r.get("g1_reason_dict") is True,
           str(r.get("g1_reason_dict")))
    record("Redis 读回(granted bool)",
           r.get("g1_granted_rd") is True,
           str(r.get("g1_granted_rd")))
    record("工作台 novice 视图",
           r.get("wb_view") == "noviceView",
           str(r.get("wb_view")))
    record("渲染读回(renderOptions dict)",
           r.get("wb_opts_dict") is True,
           str(r.get("wb_opts_dict")))
    record("第38档案八因子",
           r.get("sc_factors") == 8,
           str(r.get("sc_factors")))
    record("高分决策 optimize",
           r.get("sc_decision")
           in ("optimize", "urgent"),
           str(r.get("sc_decision")))
    record("44号 35 档案",
           r.get("reg_n") == 36,
           str(r.get("reg_n")))
    record("admin_orchestration 在册",
           r.get("ao_in_reg") is True,
           str(r.get("ao_in_reg")))
    record("58号 INTENT_REGISTRY 零改动",
           r.get("ii58_n") == 12,
           str(r.get("ii58_n")))
    record("事件链(grant+render)",
           all(t in (r.get("ev_types") or [])
               for t in ("grant", "render")),
           str(r.get("ev_types")))

    print("\n[04 HTTP 端点+鉴权]")
    # 服务器态 off——决策面 409(铁律)
    ok, (code, _) = call(
        "POST", "/api/ab63/grants",
        body={"memberId": 1,
              "role": "ally_merchant",
              "action": "basic_crud"},
        headers=ADMIN, expect=(409,))
    record("HTTP grants off 409(服务器态)",
           code == 409, str(code))
    # 观测面(容器内种子的裁决记录读回)
    ok, (code, body) = call(
        "GET", "/api/ab63/grants",
        headers=ADMIN)
    record("HTTP grants 列表(Redis 读回)",
           code == 200
           and (body.get("total") or 0)
           >= 1,
           str((code, body.get("total"))))
    # 鉴权 403
    for method, path in (
            ("GET", "/api/ab63/registry"),
            ("POST", "/api/ab63/grants"),
            ("GET", "/api/ab63/grants"),
            ("POST",
             "/api/ab63/workbench/render"),
            ("GET",
             "/api/ab63/model/status")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 5
    script = (
        "from routes.ab63_routes import "
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
    record("63号路由累计 5 端点",
           count == 5, str(count))


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
