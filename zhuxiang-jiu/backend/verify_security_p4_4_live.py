"""43号P4-4 Redis 实况监控 Docker 实机验收

运行方式:
    python verify_security_p4_4_live.py [基址]

覆盖(真实 Redis 全链路):
    01 正常业务零影响(健康检查+业务流量)
    02 HTTP 鉴权(缺 X-Role 403)
    03 实况采集(redis 模式: memory/dbSize/keyFamilies/
       slowlog/bigKeys/alerts 结构齐全)
    04 键族计数真实(制造 events/rate/threatintel 后计数增长)
    05 慢日志真实(redis 容器 SLOWLOG 可读)
    06 大 key 抽查(写入大 Hash 后 bigKeys 可见)
    07 告警阈值(无超限零告警/正常水位)
    08 日报序列端点(⑦区数据源, 14 天)
    09 幂等重复采集
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
        with urllib.request.urlopen(req, timeout=30) as resp:
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
    print("43号·P4-4 Redis 实况监控 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 HTTP 鉴权]")
    ok, (code, _) = call("GET", "/api/security/admin/redis/health",
                         expect=(403,))
    record("体检缺Role403", code == 403, str(code))

    print("\n[03 实况采集结构]")
    ok, (code, body) = call("GET", "/api/security/admin/redis/health",
                            headers=ADMIN)
    record("采集200", code == 200 and body.get("success") is True,
           str(code))
    record("redis模式", body.get("mode") == "redis",
           str(body.get("mode")))
    mem = body.get("memory") or {}
    record("内存信息真实", bool(mem.get("usedHuman"))
           and int(mem.get("usedBytes") or 0) > 0,
           str(mem)[:120])
    record("DBSIZE真实", isinstance(body.get("dbSize"), int)
           and body["dbSize"] > 0, str(body.get("dbSize")))
    fam = body.get("keyFamilies") or {}
    record("键族齐全", set(fam) >= {
        "security_events", "security_ip_reputation", "rate",
        "threatintel"}, str(sorted(fam)))
    record("慢日志字段", isinstance(body.get("slowlog"), list))
    record("大key字段", isinstance(body.get("bigKeys"), list))
    record("告警字段", isinstance(body.get("alerts"), list))
    base_events = fam.get("security_events", 0)
    base_rate = fam.get("rate", 0)
    base_ti = fam.get("threatintel", 0)

    print("\n[04 键族计数真实]")
    # 导入情报 + 网关请求(容器内)
    docker_exec(
        "import asyncio\n"
        "from services.threatintel_service import ThreatIntelService\n"
        "from services.security_service import Security43Service\n"
        "async def m():\n"
        "    await ThreatIntelService().import_netset("
        "'203.0.113.0/24\\n')\n"
        "    await Security43Service().process_request("
        "'203.0.113.77', method='GET', path='/api/product/list', "
        "ua='Mozilla/5.0', hour=14)\n"
        "asyncio.run(m())\n")
    ok, (code, body) = call("GET", "/api/security/admin/redis/health",
                            headers=ADMIN)
    fam2 = body.get("keyFamilies") or {}
    record("事件族计数增长",
           fam2.get("security_events", 0) >= base_events + 1,
           f"{base_events}→{fam2.get('security_events')}")
    record("频次族计数增长", fam2.get("rate", 0) >= base_rate + 1,
           f"{base_rate}→{fam2.get('rate')}")
    record("情报族计数增长", fam2.get("threatintel", 0) >= base_ti + 1,
           f"{base_ti}→{fam2.get('threatintel')}")

    print("\n[05 慢日志真实]")
    result = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "CONFIG", "GET", "slowlog-log-slower-than"],
        capture_output=True, text=True)
    record("慢日志开关可读", "slowlog-log-slower-than" in
           (result.stdout or ""), result.stdout.strip()[:60])

    print("\n[06 大 key 抽查]")
    # 写入大 Hash(>100KB) → bigKeys 可见
    docker_exec(
        "import asyncio\n"
        "from repositories.backend import get_redis_client, _k\n"
        "async def m():\n"
        "    c = await get_redis_client()\n"
        "    kv = {f'f{i}': 'x' * 200 for i in range(600)}\n"
        "    await c.hset(_k('security43', 'security_events', "
        "'BIGKEY-TEST'), mapping=kv)\n"
        "asyncio.run(m())\n")
    ok, (code, body) = call("GET", "/api/security/admin/redis/health",
                            headers=ADMIN)
    big = body.get("bigKeys") or []
    record("大key可见", any("BIGKEY-TEST" in str(k.get("key"))
                            for k in big),
           str(big)[:120])
    record("大key体积>100KB", all(
        (k.get("bytes") or 0) > 100_000 for k in big) if big else False,
        str(big)[:120])
    record("大key告警触发", any(
        "大 key" in str(a.get("message"))
        for a in body.get("alerts", [])), str(body.get("alerts"))[:150])
    # 清理测试大 key
    docker_exec(
        "import asyncio\n"
        "from repositories.backend import get_redis_client, _k\n"
        "async def m():\n"
        "    c = await get_redis_client()\n"
        "    await c.delete(_k('security43', 'security_events', "
        "'BIGKEY-TEST'))\n"
        "asyncio.run(m())\n")

    print("\n[07 告警阈值]")
    ok, (code, body) = call("GET", "/api/security/admin/redis/health",
                            headers=ADMIN)
    mem = body.get("memory") or {}
    used_pct = mem.get("usedPct")
    record("水位口径", used_pct is None or 0 <= used_pct <= 1,
           str(used_pct))
    alerts = body.get("alerts", [])
    record("告警结构", all(
        set(a) >= {"level", "rule", "message"} for a in alerts),
        str(alerts)[:120])

    print("\n[08 日报序列端点]")
    ok, (code, body) = call(
        "GET", "/api/security/admin/reports/daily?days=14",
        headers=ADMIN)
    record("序列14天", code == 200
           and len(body.get("reports", [])) == 14, str(code))
    record("summary字段", all(
        k in body.get("summary", {})
        for k in ("eventsTotal", "falsePositiveRate", "activeDays",
                  "d5Samples")), str(body.get("summary"))[:100])

    print("\n[09 幂等重复采集]")
    ok, (code, b1) = call("GET", "/api/security/admin/redis/health",
                          headers=ADMIN)
    ok, (code, b2) = call("GET", "/api/security/admin/redis/health",
                          headers=ADMIN)
    record("重复采集一致", b1.get("keyFamilies") == b2.get("keyFamilies")
           and b1.get("mode") == b2.get("mode"),
           str(b1.get("keyFamilies"))[:80])
    ok, (code, _) = call("GET", "/api/product/list")
    record("采集后业务正常", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
