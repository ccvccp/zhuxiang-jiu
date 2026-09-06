"""59号AI智能服务编排 P0 Docker 实机验收

运行方式:
    python verify_ii59_p0_live.py [基址]

前置: 容器已运行(含 59号 P0 代码)。

覆盖(59号计划 §九 P0, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(sessions 409; registry/
       model 观测面 200)
    03 容器内: 注册表+状态机+第34档案全链
       (Redis 态读回——序列化五清单)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
ii59 键域)。
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


def clear_ii59(round_no: int) -> None:
    redis_del_keys("zhuxiang:ii59:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['II59_MODE'] = 'shadow'\n"
    "from core.helpers import ts\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 注册表
    "    from services.ii59_registry import (\n"
    "        SERVICE_REGISTRY, ROUTING_TABLE,\n"
    "        route_intent, registry_view)\n"
    "    out['svc_n'] = len(SERVICE_REGISTRY)\n"
    "    out['rt_n'] = len(ROUTING_TABLE)\n"
    "    out['route_price'] = route_intent(\n"
    "        'product.price_query')\n"
    "    out['route_fallback'] = route_intent(\n"
    "        'nonexistent.intent')\n"
    "    view = registry_view()\n"
    "    out['by_plane'] = view.get('byPlane')\n"
    # ② 会话状态机(Redis 态读写)
    "    from services.ii59_service import "
    "Ii59Service\n"
    "    svc = Ii59Service()\n"
    "    r1 = await svc.open_session(\n"
    "        member_id=1, channel='text')\n"
    "    sid = r1['sessionId']\n"
    "    out['opened'] = r1.get('state')\n"
    "    await svc.transition(sid, 'serving')\n"
    "    await svc.transition(sid, 'resolved')\n"
    "    await svc.transition(sid, 'closed')\n"
    # ③ Redis 读回(会话结构)
    "    from repositories.ii59_repository "
    "import Ii59Repository\n"
    "    repo = Ii59Repository()\n"
    "    s = await repo.get_session(sid)\n"
    "    out['rd_state'] = s.get('state')\n"
    "    out['rd_member'] = s.get('memberId')\n"
    "    out['rd_attr_is_dict'] = isinstance(\n"
    "        s.get('attribution'), dict)\n"
    "    out['rd_escalated'] = s.get('escalated')\n"
    # ④ 非法流转(Redis 态)
    "    try:\n"
    "        await svc.transition(sid, 'serving')\n"
    "        out['term_guard'] = False\n"
    "    except ValueError:\n"
    "        out['term_guard'] = True\n"
    # ⑤ 第34档案
    "    from services.ii59_scorer import "
    "Ii59Scorer\n"
    "    sc = await Ii59Scorer().score({\n"
    "        'sessionResolution': 0.9,\n"
    "        'searchAdoption': 0.8,\n"
    "        'tier': 'trusted'})\n"
    "    out['sc_factors'] = len(\n"
    "        sc.get('factors') or [])\n"
    "    out['sc_decision'] = sc.get('decision')\n"
    # ⑥ 44号 34 档案
    "    from services.ai_learning_service "
    "import SCORER_REGISTRY\n"
    "    out['reg_n'] = len(SCORER_REGISTRY)\n"
    "    out['so_in_reg'] = (\n"
    "        'service_orchestration'\n"
    "        in SCORER_REGISTRY)\n"
    # ⑦ 58/48号零改动
    "    from services.ii58_registry import (\n"
    "        INTENT_REGISTRY)\n"
    "    out['ii58_n'] = len(INTENT_REGISTRY)\n"
    "    from services.xiaozhu_service import (\n"
    "        COMMAND_ACTIONS)\n"
    "    out['cmd_n'] = len(COMMAND_ACTIONS)\n"
    # ⑧ 事件留痕
    "    evs = await repo.list_events(\n"
    "        event_type='session', limit=50)\n"
    "    out['ev_n'] = len(evs)\n"
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
    clear_ii59(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/ii59/sessions",
        body={"memberId": 1},
        headers=ADMIN, expect=(409,))
    record("off 态 sessions 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/ii59/registry",
        headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and (body.get("total") or 0) == 8,
           str((code, body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/ii59/model/status",
        headers=ADMIN)
    record("off 态 model/status 观测面 200",
           code == 200
           and ((body.get("status")
                 or {}).get("scorerId")
                == "service_orchestration"),
           str(code))

    print("\n[03 容器内: 注册表+状态机+档案]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("服务 8 项",
           r.get("svc_n") == 8,
           str(r.get("svc_n")))
    record("路由表 11 条",
           r.get("rt_n") == 11,
           str(r.get("rt_n")))
    record("路由 price→搜索",
           r.get("route_price")
           == ["sr.product_search"],
           str(r.get("route_price")))
    record("路由兜底 meta.unknown",
           r.get("route_fallback")
           == ["meta.unknown"],
           str(r.get("route_fallback")))
    record("byPlane 四面",
           (r.get("by_plane") or {}).get(
               "customer_service") == 2,
           str(r.get("by_plane")))
    record("开话 opened",
           r.get("opened") == "opened",
           str(r.get("opened")))
    record("Redis 读回(closed 终态)",
           r.get("rd_state") == "closed",
           str(r.get("rd_state")))
    record("Redis 读回(memberId)",
           r.get("rd_member") == 1,
           str(r.get("rd_member")))
    record("Redis 读回(attribution dict)",
           r.get("rd_attr_is_dict") is True,
           str(r.get("rd_attr_is_dict")))
    record("Redis 读回(escalated bool)",
           r.get("rd_escalated") is False,
           str(r.get("rd_escalated")))
    record("终态守卫(非法流转拒绝)",
           r.get("term_guard") is True,
           str(r.get("term_guard")))
    record("第34档案八因子",
           r.get("sc_factors") == 8,
           str(r.get("sc_factors")))
    record("高分决策 optimize",
           r.get("sc_decision")
           in ("optimize", "urgent"),
           str(r.get("sc_decision")))
    record("44号 34 档案",
           r.get("reg_n") == 34,
           str(r.get("reg_n")))
    record("service_orchestration 在册",
           r.get("so_in_reg") is True,
           str(r.get("so_in_reg")))
    record("58号 INTENT_REGISTRY 零改动",
           r.get("ii58_n") == 12,
           str(r.get("ii58_n")))
    record("48号 COMMAND_ACTIONS 零改动",
           (r.get("cmd_n") or 0) >= 15,
           str(r.get("cmd_n")))
    record("session 事件留痕",
           (r.get("ev_n") or 0) >= 4,
           str(r.get("ev_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # shadow 开话
    script = (
        "import asyncio, os, json\n"
        "os.environ['II59_MODE'] = 'shadow'\n"
        "from services.ii59_service import "
        "Ii59Service\n"
        "r = asyncio.run(Ii59Service()"
        ".open_session(member_id=9))\n"
        "print(json.dumps({'sid': "
        "r['sessionId']}))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        sid = json.loads(
            (out.stdout or "").strip()
            .splitlines()[-1])["sid"]
    except (ValueError, IndexError):
        sid = 0
    # 服务器态默认 off——HTTP 决策 409
    ok, (code, _) = call(
        "POST", "/api/ii59/sessions",
        body={"memberId": 1},
        headers=ADMIN, expect=(409,))
    record("HTTP sessions off 409(服务器态)",
           code == 409, str(code))
    # 详情(容器内种的会话经 HTTP 观测面)
    ok, (code, body) = call(
        "GET",
        f"/api/ii59/sessions/{sid}",
        headers=ADMIN)
    record("HTTP 会话详情(Redis 读回)",
           code == 200
           and ((body.get("session") or {})
                .get("sessionId")) == sid,
           str((code, sid)))
    # 鉴权 403
    for method, path in (
            ("GET", "/api/ii59/registry"),
            ("POST", "/api/ii59/sessions"),
            ("GET", "/api/ii59/sessions/1"),
            ("GET", "/api/ii59/sessions"),
            ("GET", "/api/ii59/model/status")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 5
    script2 = (
        "from routes.ii59_routes import router\n"
        "print(sum(1 for r in router.routes))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script2],
        capture_output=True, text=True)
    try:
        count = int((out.stdout or "").strip())
    except ValueError:
        count = -1
    record("59号路由累计 5 端点",
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
