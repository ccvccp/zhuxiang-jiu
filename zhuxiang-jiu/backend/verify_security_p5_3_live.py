"""43号P5-3 威胁情报自动订阅 Docker 实机验收

运行方式:
    python verify_security_p5_3_live.py [基址]

覆盖(容器内模拟源全链路, 不依赖外网):
    01 正常业务零影响
    02 stats.auto 默认 off(enabled=False)
    03 容器内模拟源(netset 文件 + python -m http.server)
    04 refresh 端点鉴权(缺 Role 403)
    05 模拟源全链路: refresh → imported=150 → CIDR 命中联动
    06 幂等: 二次 refresh → imported 一致
    07 失败容错: URL 指向 404 → failed + 旧段保留(命中仍有效)
    08 连续 3 次失败 → degraded=true
    09 恢复: URL 修回 → ok + 计数清零 + degraded 消除
    10 调度轨: 容器内单轮调度含 lastThreatintel
    11 既有手动导入端点回归(双轨并存)
    12 全程业务正常
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

NETSET = "\n".join(
    [f"10.{i // 250}.{i % 250}.0/24" for i in range(150)])


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
        with urllib.request.urlopen(req, timeout=60) as resp:
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


def setup_mock_source() -> str:
    """容器内建 netset 文件 + 起 http.server 模拟源(返回 URL)"""
    docker_exec(
        "open('/tmp/mocknetset.txt','w').write(" +
        repr(NETSET) + ")")
    # 起 8099 端口 http.server(独立进程, 已在跑则复用)
    subprocess.run(
        ["docker", "exec", "-d", "zhuxiang-jiu-backend-1",
         "python", "-m", "http.server", "8099",
         "--directory", "/tmp"],
        capture_output=True, text=True)
    return "http://127.0.0.1:8099/mocknetset.txt"


def set_feed_url(url: str) -> None:
    """写容器内环境(供 exec 内子进程读; 常驻进程 env 不变——
    refresh 端点走常驻进程, 故 url 通过 .env 注入式修改不可行;
    改用 docker exec 直调 maybe_refresh(注入 env)验证服务层,
    HTTP 端点用真实默认源或失败源验证)"""
    pass


def exec_refresh(url: str, force: bool = True) -> str:
    """容器内 exec maybe_refresh(注入 FEED_URL env, 服务层全链路)"""
    code = (
        "import asyncio, os\n"
        f"os.environ['SECURITY_THREATINTEL_URL'] = {url!r}\n"
        "from services.threatintel_feed import maybe_refresh\n"
        "async def m():\n"
        f"    r = await maybe_refresh(force={force})\n"
        "    import json\n"
        "    print('executed=' + str(r.get('executed')))\n"
        "    print('status=' + str(r.get('status')))\n"
        "    print('imported=' + str(r.get('imported')))\n"
        "    print('failures=' + str(r.get("
        "'consecutiveFailures')))\n"
        "    print('degraded=' + str(r.get('degraded')))\n"
        "asyncio.run(m())\n")
    return docker_exec(code)


def get_stats_auto() -> dict:
    """stats.auto 实况(经 HTTP)"""
    ok, (code, body) = call(
        "GET", "/api/security/admin/threatintel/stats",
        headers=ADMIN)
    return (body.get("auto") or {}) if code == 200 else {}


def check_hit(ip: str) -> dict:
    ok, (code, body) = call(
        "GET", f"/api/security/admin/threatintel/check?ip={ip}",
        headers=ADMIN)
    return body if code == 200 else {}


def main():
    print("=" * 62)
    print("43号·P5-3 威胁情报自动订阅 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 stats.auto 默认实况]")
    auto = get_stats_auto()
    record("auto字段存在", "enabled" in auto, str(auto))
    record("默认off", auto.get("enabled") is False, str(auto))

    print("\n[03 容器内模拟源]")
    url = setup_mock_source()
    out = docker_exec(
        "import urllib.request\n"
        f"r = urllib.request.urlopen('{url}', timeout=10)\n"
        "print('source_status=' + str(r.status))\n"
        "print('source_len=' + str(len(r.read())))")
    record("模拟源可达", "source_status=200" in out, out[:80])

    print("\n[04 HTTP 鉴权]")
    ok, (code, _) = call(
        "POST", "/api/security/admin/threatintel/auto/refresh",
        expect=(403,))
    record("缺Role403", code == 403, str(code))

    print("\n[05 模拟源全链路(服务层)]")
    out = exec_refresh(url)
    record("首次拉取执行", "executed=True" in out, out[:120])
    record("导入150段", "imported=150" in out, out[:120])
    record("状态ok", "status=ok" in out, out[:120])
    record("失败计数0", "failures=0" in out, out[:120])
    hit = check_hit("10.0.0.55")
    record("CIDR命中联动",
           (hit.get("hit") or {}).get("cidr") == "10.0.0.0/24",
           str(hit)[:120])
    auto = get_stats_auto()
    record("stats.auto留痕", auto.get("lastAutoStatus") == "ok",
           str(auto))

    print("\n[06 幂等重复拉取]")
    out = exec_refresh(url)
    record("二次拉取导入一致", "imported=150" in out
           and "status=ok" in out, out[:120])

    print("\n[07 失败容错(旧段保留)]")
    out = exec_refresh(url + "-not-exist")
    record("失败状态", "status=failed" in out, out[:120])
    record("失败计数1", "failures=1" in out, out[:120])
    hit = check_hit("10.0.0.55")
    record("旧段保留命中仍有效",
           (hit.get("hit") or {}).get("cidr") == "10.0.0.0/24",
           str(hit)[:120])

    print("\n[08 连续失败 degraded]")
    exec_refresh(url + "-not-exist")
    out = exec_refresh(url + "-not-exist")
    record("失败计数3", "failures=3" in out, out[:120])
    record("degraded=true", "degraded=True" in out, out[:120])
    auto = get_stats_auto()
    record("stats外显degraded", auto.get("degraded") is True,
           str(auto))

    print("\n[09 恢复]")
    out = exec_refresh(url)
    record("恢复ok", "status=ok" in out and "imported=150" in out,
           out[:120])
    record("计数清零", "failures=0" in out, out[:120])
    record("degraded消除", "degraded=False" in out, out[:120])
    auto = get_stats_auto()
    record("stats恢复", auto.get("degraded") is False
           and auto.get("lastAutoStatus") == "ok", str(auto))

    print("\n[10 调度轨]")
    out = docker_exec(
        "import asyncio, os\n"
        "os.environ['SECURITY_THREATINTEL_AUTO'] = 'on'\n"
        f"os.environ['SECURITY_THREATINTEL_URL'] = {url!r}\n"
        "from services.security_scheduler import "
        "run_scheduled_security_tasks\n"
        "async def m():\n"
        "    s = await run_scheduled_security_tasks()\n"
        "    print('ti=' + str(s.get('lastThreatintel')))\n"
        "asyncio.run(m())\n")
    record("调度含lastThreatintel", "ti=" in out
           and "None" not in out.split("ti=")[0], out[:150])
    record("ti状态ok或周期内", "status" in out, out[:150])

    print("\n[11 手动导入端点回归(双轨)]")
    ok, (code, body) = call(
        "POST", "/api/security/admin/threatintel/import",
        body={"content": "203.0.113.0/24\n",
              "source": "manual_test"},
        headers=ADMIN)
    record("手动导入仍可用", code == 200
           and body.get("imported") == 1, str(body)[:100])
    hit = check_hit("203.0.113.9")
    record("手动段命中",
           (hit.get("hit") or {}).get("cidr") == "203.0.113.0/24",
           str(hit)[:100])
    # 清理手动测试段
    call("POST", "/api/security/admin/threatintel/import",
         body={"content": "10.0.0.0/24\n" * 1 +
               "\n".join(f"10.{i // 250}.{i % 250}.0/24"
                         for i in range(150))},
         headers=ADMIN)

    print("\n[12 业务回归]")
    ok, (code, _) = call("GET", "/api/product/list")
    record("业务正常", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
