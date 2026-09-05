"""52号P3 包容性公平分组分析 Docker 实机验收

运行方式:
    python verify_us52_p3_live.py [基址]

前置: 容器已运行(含 52号P3 代码, 镜像已重建)。

覆盖(52号计划 §七 P3, 真实容器):
    01 正常业务零影响(健康检查/35号面板)
    02 off 铁律(HTTP: inclusion 409)
    03 容器内(on 进程): 双组种子+组间差
       (elder 低命中+none 高命中 → gap>0.05)
    04 组间差大 → mandatory 决策
    05 HTTP inclusion 端点+鉴权

×2 轮幂等验证(每轮清理种子重造)。
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


def clear_us52() -> None:
    for pattern in ("zhuxiang:us52:*",
                    "zhuxiang:voice50:"
                    "voice50_group_profile:53*"):
        out = subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "--scan", "--pattern",
             pattern],
            capture_output=True, text=True)
        keys = [k for k in (out.stdout or "").split() if k]
        for i in range(0, len(keys), 200):
            subprocess.run(
                ["docker", "exec",
                 "zhuxiang-jiu-redis-1", "redis-cli",
                 "DEL", *keys[i:i + 200]],
                capture_output=True, text=True)


def container_p3_check(round_no: int) -> dict:
    """容器内(on 进程): 双组种子+组间差计算"""
    m_elder = 5340 + round_no
    m_none = 5360 + round_no
    script = (
        "import asyncio, json, os\n"
        "os.environ['US52_MODE'] = 'on'\n"
        "from core.helpers import ts as _ts\n"
        "from repositories.us52_repository import "
        "Us52Repository\n"
        "from repositories.voice50_repository import "
        "Voice50Repository\n"
        "from services.us52_service import "
        "Us52MetricsService\n"
        "from services.us52_registry import decide\n"
        f"ME = {m_elder}\n"
        f"MN = {m_none}\n"
        "async def seed(m, hits, total, group):\n"
    "    repo = Us52Repository()\n"
    "    tid = await repo.next_test_id()\n"
    "    await repo.save_session({\n"
    "        'testId': tid, 'mode': 'on',\n"
    "        'memberId': m,\n"
    "        'taskIds': ['T-01'] * total,\n"
    "        'status': 'completed',\n"
    "        'taskCount': total,\n"
    "        'passedCount': hits,\n"
    "        'startedAt': _ts(),\n"
    "        'completedAt': _ts()})\n"
    "    from repositories.backend import (\n"
    "        get_redis_client, _k)\n"
    "    client = await get_redis_client()\n"
    "    for i in range(total):\n"
    "        hit = i < hits\n"
    "        rec = {\n"
    "            'resultId': '%d-%d' % (\n"
    "                tid, i+1),\n"
    "            'testId': tid, 'taskId': 'T-01',\n"
    "            'kind': 'positive',\n"
    "            'expectedIntent': 'trust.score',\n"
    "            'actualIntent': (\n"
    "                'trust.score' if hit\n"
    "                else 'general'),\n"
    "            'pass': hit, 'detail': '',\n"
    "            'ts': _ts()}\n"
    "        await client.hset(\n"
    "            _k('us52', repo.TABLE_RESULTS,\n"
    "               rec['resultId']),\n"
    "            mapping=repo._serialize(rec))\n"
        "    if group:\n"
        "        v50 = Voice50Repository()\n"
        "        await v50.save_group_profile({\n"
        "            'memberId': m, 'group': group,\n"
        "            'note': 'us52live',\n"
        "            'ts': _ts()})\n"
        "    return tid\n"
        "async def m():\n"
        "    out = {}\n"
        "    await seed(ME, 3, 10, 'elder')\n"
        "    await seed(MN, 9, 10, None)\n"
        "    svc = Us52MetricsService()\n"
        "    r = await svc."
        "compute_inclusion_metrics()\n"
        "    out['metrics'] = r['metrics']\n"
        "    out['byGroup'] = (\n"
        "        r['detail']['byGroup'])\n"
        "    g = decide(r['metrics'])\n"
        "    out['decision'] = g['decision']\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:200]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收\n{'=' * 62}")
    clear_us52()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 off 铁律(HTTP 主进程)]")
    ok, (code, _) = call(
        "POST", "/api/us52/metrics/inclusion",
        headers=ADMIN, expect=(409,))
    record("off 态 inclusion 409", code == 409, str(code))

    print("\n[03 容器内(on 进程): 双组组间差]")
    r = container_p3_check(round_no)
    metrics = r.get("metrics") or {}
    record("两指标齐备",
           set(metrics) == {
               "intent_parity_gap",
               "low_value_service_parity"},
           str(list(metrics)))
    record("组间差=0.6(elder 0.3 vs none 0.9)",
           abs((metrics.get("intent_parity_gap")
                or 0) - 0.6) < 0.001,
           str(metrics.get("intent_parity_gap")))
    record("低信值平等=0(静态统一红线)",
           metrics.get(
               "low_value_service_parity") == 0.0,
           str(metrics.get(
               "low_value_service_parity")))
    bg = r.get("byGroup") or {}
    record("elder 组采集(3/10)",
           (bg.get("elder") or {}).get("hit") == 3,
           str(bg.get("elder")))
    record("none 组采集(9/10)",
           (bg.get("none") or {}).get("hit") == 9,
           str(bg.get("none")))

    print("\n[04 组间差大 → mandatory 决策]")
    record("decision=mandatory(包容性未达)",
           r.get("decision") == "mandatory",
           str(r.get("decision")))

    print("\n[05 HTTP inclusion 端点]")
    ok, (code, body) = call(
        "POST", "/api/us52/metrics/inclusion",
        headers=ADMIN, expect=(200, 409))
    record("HTTP inclusion 路由可达",
           code in (200, 409), str(code))
    ok, (code, _) = call(
        "POST", "/api/us52/metrics/inclusion",
        expect=(403,))
    record("inclusion 无 Role 403",
           code == 403, str(code))


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
