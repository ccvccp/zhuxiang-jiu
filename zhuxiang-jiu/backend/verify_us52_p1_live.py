"""52号P1 功能可信度管道 Docker 实机验收

运行方式:
    python verify_us52_p1_live.py [基址]

前置: 容器已运行(含 52号P1 代码, 镜像已重建)。

覆盖(52号计划 §七 P1, 真实容器):
    01 正常业务零影响(健康检查/35号面板)
    02 off 铁律(HTTP: tests/functional 409)
    03 容器内(on 进程): 测试集执行+五指标
       (12 任务跑真管道——独立号段 5300)
    04 HTTP 测试端点(tests/run 12 任务+
       functional 五指标+tests 历史)
    05 鉴权(403)

×2 轮幂等验证(每轮独立 member 号段偏移)。
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
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:us52:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def container_p1_check(round_no: int) -> dict:
    """容器内(on 进程): 绑定+测试集+五指标"""
    member_id = 5300 + round_no - 1
    script = (
        "import asyncio, json, os\n"
        "os.environ['US52_MODE'] = 'on'\n"
        "from services.us52_task_engine import "
        "Us52TaskEngine\n"
        "from services.us52_service import "
        "Us52MetricsService\n"
        "from services.trust_scoring_service import "
        "TrustProfileService\n"
        "from services.xiaozhu_service import "
        "XiaozhuService\n"
        "import uuid\n"
        f"M = {member_id}\n"
        "async def m():\n"
        "    out = {}\n"
        "    suffix = uuid.uuid4().hex[:10]\n"
        "    role = await TrustProfileService()"
        ".create_role(\n"
        "        'person',\n"
        "        'us52live-' + suffix[:6],\n"
        "        '110101' + suffix + '4321')\n"
        "    tid = role['trustId']\n"
        "    await XiaozhuService().bind_trust(\n"
        "        M, tid, note='us52live')\n"
        "    r = await Us52TaskEngine().run_tests(\n"
        "        member_id=M)\n"
        "    out['testId'] = r['testId']\n"
        "    out['taskCount'] = r['taskCount']\n"
        "    out['passedCount'] = r['passedCount']\n"
        "    svc = Us52MetricsService()\n"
        "    m = await svc."
        "compute_functional_metrics(\n"
        "        test_id=r['testId'])\n"
        "    out['metrics'] = m['metrics']\n"
        "    out['detail'] = m['detail']\n"
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
    ok, (code, _) = call("POST", "/api/us52/tests/run",
                         headers=ADMIN, body={},
                         expect=(409,))
    record("off 态 tests/run 409", code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/us52/metrics/functional",
        headers=ADMIN, body={}, expect=(409,))
    record("off 态 functional 409", code == 409,
           str(code))

    print("\n[03 容器内(on 进程): 测试集+五指标]")
    r = container_p1_check(round_no)
    record("测试集执行(12 任务)",
           (r.get("taskCount") or 0) == 12,
           str(r.get("taskCount")))
    record("任务通过率(passedCount)",
           (r.get("passedCount") or 0) >= 10,
           str(r.get("passedCount")))
    metrics = r.get("metrics") or {}
    record("五指标齐备",
           set(metrics) == {
               "fc_success_rate", "explain_ref_rate",
               "budget_accuracy", "confirm_rate",
               "intent_accuracy"},
           str(list(metrics)))
    record("预算准确性=1.0(静态值)",
           metrics.get("budget_accuracy") == 1.0,
           str(metrics.get("budget_accuracy")))
    detail = r.get("detail") or {}
    record("审计样本采集(>0)",
           (detail.get("auditTotal") or 0) > 0,
           str(detail.get("auditTotal")))

    print("\n[04 HTTP 端点(off 主进程——治理面)]")
    ok, (code, body) = call("GET", "/api/us52/tests",
                            headers=ADMIN)
    record("tests 历史 200(脚本库 12)",
           code == 200
           and len((body.get("taskLibrary")
                    or {})) == 12,
           str(code))
    ok, (code, body) = call("GET", "/api/us52/registry",
                            headers=ADMIN)
    record("registry 200(20 项)",
           code == 200
           and (body.get("metricCount") or 0) == 20,
           str(code))

    print("\n[05 鉴权]")
    ok, (code, _) = call("POST", "/api/us52/tests/run",
                         body={}, expect=(403,))
    record("tests/run 无 Role 403", code == 403, str(code))
    ok, (code, _) = call(
        "GET", "/api/us52/tests", expect=(403,))
    record("tests 无 Role 403", code == 403, str(code))


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
