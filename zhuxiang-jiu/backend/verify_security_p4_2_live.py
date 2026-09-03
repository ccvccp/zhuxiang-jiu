"""43号P4-2 Docker 实机验收(UEBA 基线日度调度器)

运行方式:
    python verify_security_p4_2_live.py [基址]

覆盖:
    01 正常业务零影响(默认 off 不调度)
    02 容器内调度器模块健康(off 默认)
    03 容器内手动触发单轮调度(重建+姿态+统计留痕)
    04 统计入 Redis 可查
    05 全链路回归(日报/挑战/态势照常)
"""
import json
import subprocess
import sys
import urllib.request
import urllib.error

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


def call(method, path, body=None, headers=None, expect=(200,)):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            code, text = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def docker_exec(python_code: str) -> str:
    result = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python", "-c",
         python_code],
        capture_output=True, text=True)
    return (result.stdout or "").strip()


def main():
    print("=" * 62)
    print("43号·P4-2 UEBA基线调度器 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, body) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, body) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 调度器模块健康]")
    out = docker_exec(
        "import services.security_scheduler as s; "
        "print('enabled=' + str(s.scheduler_enabled())); "
        "print('interval=' + str(s.scheduler_interval_seconds()))")
    record("默认off", "enabled=False" in out, out[:80])
    record("周期86400", "interval=86400" in out, out[:80])

    print("\n[03 容器内单轮调度]")
    out = docker_exec(
        "import asyncio\n"
        "from services.security_scheduler import "
        "run_scheduled_security_tasks\n"
        "r = asyncio.run(run_scheduled_security_tasks())\n"
        "print('runs=' + str(r.get('runs')))\n"
        "print('baselines=' + str((r.get('lastBaselines') or {})"
        ".get('personal')))\n"
        "print('posture=' + str(r.get('lastPosture')))")
    record("单轮执行", "runs=" in out, out[:100])
    record("基线重建留痕", "baselines=" in out, out[:100])

    print("\n[04 统计入Redis]")
    result = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "hget", "zhuxiang:security43:security_posture:scheduler:stats",
         "runs"],
        capture_output=True, text=True)
    runs = (result.stdout or "").strip()
    record("调度统计入Redis", runs not in ("", "(nil)")
           and int(runs) >= 1, f"runs={runs}")

    print("\n[05 全链路回归]")
    ok, (code, body) = call("GET",
                            "/api/security/admin/reports/daily",
                            None, ADMIN)
    record("SOC日报照常", ok and body.get("success") is True)
    ok, (code, body) = call("POST",
                            "/api/security/challenge/verify",
                            {"captchaToken": "p4-2-live-ticket"})
    record("挑战验证照常", ok)
    ok, (code, body) = call("GET", "/api/security/admin/posture",
                            None, ADMIN)
    record("态势查询照常", ok and "posture" in body)

    print("\n" + "-" * 62)
    print("\n".join(RESULTS))
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("注: 生产启用设 SECURITY_SCHEDULER_MODE=on"
          "(compose 已暴露, 默认 off 保守)")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
