"""58号AI智能优化意图识别 P5 Docker 实机验收

运行方式:
    python verify_ii58_p5_live.py [基址]

前置: 容器已运行(含 58号 P5 代码)。

覆盖(58号计划 §九 P5, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(redteam 409; dashboard
       观测面 200)
    03 容器内: 红队七向量全量
       (allDefended——确定性离线可复现)
    04 容器内: 四区看板(度量+意图+语料+
       防御——含宪法断言)
    05 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
ii58+ai46+voice48+ai_learning 键域)。
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


def clear_ii58(round_no: int) -> None:
    redis_del_keys("zhuxiang:ii58:*")
    redis_del_keys("zhuxiang:ai46:*")
    redis_del_keys("zhuxiang:voice48:*")
    redis_del_keys("zhuxiang:ai_learning:*")


# 容器内管道(纯 ASCII——看板先红队后:
# 红队向量自产生评估记录不污染度量断言)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['II58_MODE'] = 'shadow'\n"
    "from core.helpers import ts\n"
    "async def m():\n"
    "    out = {}\n"
    "    from repositories.ii58_repository import "
    "Ii58Repository\n"
    "    repo = Ii58Repository()\n"
    "    async def seed(intent, text, stype="
    "'positive'):\n"
    "        cid = await repo.next_corpus_id()\n"
    "        await repo.save_corpus({\n"
    "            'corpusId': cid, 'corpusVersion': 1,\n"
    "            'intentId': intent,\n"
    "            'sampleType': stype,\n"
    "            'text': text, 'weight': 1.0,\n"
    "            'source': 'manual', 'originRef': '',\n"
    "            'confusableTarget': None,\n"
    "            'humanVerified': True,\n"
    "            'humanSuggested': False,\n"
    "            'status': 'active',\n"
    "            'createdAt': ts(), 'updatedAt': ts()})\n"
    "    await seed('product.price_query', "
    "'how much')\n"
    "    await seed('boundary.unauthorized', "
    "'delete all data')\n"
    # ① 度量场景(干净态——红队之前)
    "    from services.ii58_service import "
    "Ii58Service\n"
    "    svc = Ii58Service()\n"
    "    ev1 = await svc.evaluate('how much')\n"
    "    ev2 = await svc.evaluate("
    "'delete all data',\n"
    "                            member_role='guest')\n"
    "    from services.ii58_dashboard_service "
    "import (\n"
    "        Ii58DashboardService)\n"
    "    dash = await (\n"
    "        Ii58DashboardService().dashboard())\n"
    "    out['metrics'] = dash.get('metrics')\n"
    "    out['by_state'] = (\n"
    "        (dash.get('intents') or {})"
    ".get('byState'))\n"
    "    out['corpus_types'] = (\n"
    "        (dash.get('corpus') or {})"
    ".get('byType'))\n"
    "    defense = dash.get('defense') or {}\n"
    "    out['intercepted'] = (\n"
    "        defense.get('boundaryIntercepted'))\n"
    "    out['constitution'] = (\n"
    "        defense.get('constitution'))\n"
    # ② 红队七向量(看板断言后——
    #    向量自产生评估记录不再污染)
    "    from services.ii58_redteam_service import (\n"
    "        Ii58RedteamService)\n"
    "    rt = await Ii58RedteamService().run_all()\n"
    "    out['rt_summary'] = rt.get('summary')\n"
    "    out['rt_vectors'] = {\n"
    "        k: v.get('defended') for k, v in\n"
    "        (rt.get('vectors') or {}).items()}\n"
    "    out['mode_restored'] = (\n"
    "        os.environ.get('II58_MODE'))\n"
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
    clear_ii58(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/ii58/redteam",
        body={}, headers=ADMIN, expect=(409,))
    record("off 态 redteam 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/ii58/dashboard",
        headers=ADMIN)
    record("off 态 dashboard 观测面 200",
           code == 200
           and (body.get("metrics")
                or {}).get("total") == 0,
           str((code,
                (body.get("metrics")
                 or {}).get("total"))))

    print("\n[03-04 容器内: 红队+看板]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    rt_summary = r.get("rt_summary") or {}
    record("红队七向量(total=7)",
           rt_summary.get("total") == 7,
           str(rt_summary))
    record("红队 allDefended",
           rt_summary.get("allDefended") is True,
           str(rt_summary))
    vectors = r.get("rt_vectors") or {}
    record("RT-01~07 全 defended",
           all(vectors.get(f"RT-0{i}")
               is True for i in range(1, 8)),
           str(vectors))
    metrics = r.get("metrics") or {}
    record("度量区(总数 2)",
           metrics.get("total") == 2,
           str(metrics.get("total")))
    record("任务完成率(1.0——越界拦截亦"
           "resolved 态)",
           metrics.get(
               "taskCompletionRate") == 1.0,
           str(metrics.get(
               "taskCompletionRate")))
    record("信值增益(0——未 collect)",
           metrics.get("trustGain") == 0,
           str(metrics.get("trustGain")))
    by_state = r.get("by_state") or {}
    record("意图区(resolved 2)",
           by_state.get("resolved") == 2,
           str(by_state))
    corpus_types = r.get("corpus_types") or {}
    record("语料区(红队残留 retired 不计)",
           (corpus_types.get("positive") or 0) >= 1,
           str(corpus_types))
    record("防御区(越界拦截 1)",
           r.get("intercepted") == 1,
           str(r.get("intercepted")))
    constitution = r.get("constitution") or {}
    record("宪法断言(33档案+48号零改动)",
           constitution.get("scorer33") is True
           and constitution.get(
               "xiaozhuZeroChange") is True,
           str(constitution))
    record("红队模式还原(shadow 入口态)",
           r.get("mode_restored") == "shadow",
           str(r.get("mode_restored")))

    print("\n[05 HTTP 端点+鉴权]")
    # 容器态 off——HTTP redteam 409(铁律正确:
    # 红队需决策面开放; 全量语义由容器内管道覆盖)
    ok, (code, body) = call(
        "POST", "/api/ii58/redteam",
        body={}, headers=ADMIN, expect=(409,))
    record("HTTP redteam off 409(铁律)",
           code == 409, str(code))
    # 鉴权 403
    for method, path in (
            ("GET", "/api/ii58/dashboard"),
            ("POST", "/api/ii58/redteam")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 19
    script = (
        "from routes.ii58_routes import router\n"
        "print(sum(1 for r in router.routes))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        count = int((out.stdout or "").strip())
    except ValueError:
        count = -1
    record("58号路由累计 19 端点",
           count == 19, str(count))


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
