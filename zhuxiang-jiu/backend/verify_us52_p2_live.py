"""52号P2 安全韧性评估 Docker 实机验收

运行方式:
    python verify_us52_p2_live.py [基址]

前置: 容器已运行(含 52号P2 代码, 镜像已重建)。

覆盖(52号计划 §七 P2, 真实容器):
    01 正常业务零影响(健康检查/35号面板)
    02 off 铁律(HTTP: resilience 409)
    03 容器内(on 进程): 韧性五指标
       (注入抵御复用 49+51号红队真跑)
    04 HTTP resilience 端点(veto 域)
    05 鉴权(403)

×2 轮幂等验证。
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
        with urllib.request.urlopen(req, timeout=300) as r:
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


def container_p2_check() -> dict:
    """容器内(on 进程): 韧性五指标"""
    script = (
        "import asyncio, json, os\n"
        "os.environ['US52_MODE'] = 'on'\n"
        "from services.us52_service import "
        "Us52MetricsService\n"
        "async def m():\n"
        "    svc = Us52MetricsService()\n"
        "    r = await svc."
        "compute_resilience_metrics()\n"
        "    print(json.dumps(r))\n"
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
        "POST", "/api/us52/metrics/resilience",
        headers=ADMIN, expect=(409,))
    record("off 态 resilience 409", code == 409, str(code))

    print("\n[03 容器内(on 进程): 韧性五指标]")
    r = container_p2_check()
    metrics = r.get("metrics") or {}
    record("五指标齐备",
           set(metrics) == {
               "injection_defense_rate",
               "voiceprint_spoof_rate",
               "degrade_compliance_rate",
               "budget_exhausted_guide_rate",
               "session_isolation_rate"},
           str(list(metrics)))
    record("注入抵御=1.0(红队复用零突破)",
           metrics.get(
               "injection_defense_rate") == 1.0,
           str(metrics.get(
               "injection_defense_rate")))
    detail = r.get("detail") or {}
    inj = detail.get("injection") or {}
    record("49号红队报告嵌入",
           isinstance(inj.get("v49"), dict)
           and inj["v49"].get("breached") == 0,
           str(inj.get("v49")))
    record("51号红队报告嵌入",
           isinstance(inj.get("v51"), dict)
           and inj["v51"].get("breached") == 0,
           str(inj.get("v51")))
    record("声纹伪造识别=1.0(proxy)",
           metrics.get(
               "voiceprint_spoof_rate") == 1.0,
           str(metrics.get(
               "voiceprint_spoof_rate")))
    record("降级合规=1.0",
           metrics.get(
               "degrade_compliance_rate") == 1.0,
           str(metrics.get(
               "degrade_compliance_rate")))
    record("跨会话隔离=1.0(观测)",
           metrics.get(
               "session_isolation_rate") == 1.0,
           str(metrics.get(
               "session_isolation_rate")))

    print("\n[04 HTTP resilience 端点(off 主进程——"
          "治理面路由存在性验证)")
    ok, (code, body) = call(
        "POST", "/api/us52/metrics/resilience",
        headers=ADMIN, expect=(200, 409))
    record("HTTP resilience 路由可达"
           "(off 主进程 409 计算停)",
           code in (200, 409),
           str(code))

    print("\n[05 鉴权]")
    ok, (code, _) = call(
        "POST", "/api/us52/metrics/resilience",
        expect=(403,))
    record("resilience 无 Role 403",
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
