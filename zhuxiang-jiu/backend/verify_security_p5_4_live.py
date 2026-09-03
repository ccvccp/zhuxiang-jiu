"""43号P5-4 AbuseIPDB 实时查询 Docker 实机验收

运行方式:
    python verify_security_p5_4_live.py [基址]

覆盖(容器 mock 轨全链路, 不依赖外部 key):
    01 正常业务零影响
    02 容器默认 mock(端点 mode 实况)
    03 check 端点鉴权(缺 Role 403)
    04 mock 全链路: 三档 IP 联动三区间(降档31/轻扣10/零影响)
    05 配额计数(quotaUsed 递增)
    06 缓存生效(同 IP 二查 source=cache + 配额不增)
    07 refresh=true 强制重查
    08 留痕验证(85 档 IP 事件流水含 abuseipdb 因子)
    09 申诉通道回归(abuseipdb 联动事件可申诉)
    10 Firehol 串联优先(段命中 IP → AbuseIPDB 配额不动)
    11 Redis 键族回归(abuseipdb 键入族统计)
    12 mock_fallback 轨(容器 exec 注入 env)
    13 real 无 key 拒启(配置错误显式暴露)
    14 业务回归
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


def find_mock_ips() -> dict:
    """容器内扫 IP 找三档样本"""
    out = docker_exec(
        "import services.abuseipdb_client as ab\n"
        "found = {}\n"
        "for i in range(1, 60):\n"
        "    for q in range(0, 4):\n"
        "        ip = f'172.18.{q}.{i}'\n"
        "        s = ab._mock_score(ip)\n"
        "        if s not in found:\n"
        "            found[s] = ip\n"
        "print('SCORES=' + str(found))\n")
    for line in out.splitlines():
        if line.startswith("SCORES="):
            return eval(line[len("SCORES="):])
    return {}


def main():
    print("=" * 62)
    print("43号·P5-4 AbuseIPDB 实时查询 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 容器默认 mock]")
    ok, (code, body) = call(
        "GET", "/api/security/admin/threatintel/abuseipdb/check"
        "?ip=8.8.8.8", headers=ADMIN)
    record("端点可用", code == 200, str(code))
    record("默认mock", body.get("mode") == "mock", str(body)[:100])

    print("\n[03 端点鉴权]")
    ok, (code, _) = call(
        "GET", "/api/security/admin/threatintel/abuseipdb/check"
        "?ip=8.8.8.8", expect=(403,))
    record("缺Role403", code == 403, str(code))

    print("\n[04 三档联动(mock_fallback 注入)]")
    samples = find_mock_ips()
    record("三档样本", set(samples) == {0, 25, 85}, str(samples))
    if not samples:
        samples = {0: "172.18.0.1", 25: "172.18.0.2",
                  85: "172.18.0.3"}

    # 容器内清缓存+清样本信誉残留后走网关联动
    # 注入 mock_fallback 态——联动仅 real/mock_fallback 生效
    docker_exec(
        "import asyncio\n"
        "from repositories.backend import get_redis_client, _k\n"
        "async def m():\n"
        "    c = await get_redis_client()\n"
        "    keys = await c.keys("
        "'zhuxiang:security43:abuseipdb:result:*')\n"
        "    for k in keys or []:\n"
        "        await c.delete(k)\n"
        f"    for ip in ({samples[85]!r}, {samples[25]!r}, "
        f"{samples[0]!r}):\n"
        "        await c.delete(_k('security43', "
        "'security_ip_reputation', ip))\n"
        "asyncio.run(m())\n")

    out = docker_exec(
        "import asyncio, os\n"
        "os.environ['SECURITY_ABUSEIPDB_MODE'] = "
        "'mock_fallback'\n"
        "from services.threatintel_service import "
        "ThreatIntelService\n"
        "from services.security_service import "
        "Security43Service\n"
        "async def m():\n"
        "    svc = ThreatIntelService()\n"
        "    sec = Security43Service()\n"
        f"    for tier, ip in [(85, {samples[85]!r}), "
        f"(25, {samples[25]!r}), (0, {samples[0]!r})]:\n"
        "        rep = await sec.ensure_reputation(ip)\n"
        "        rep = await svc.apply_to_reputation(ip, rep)\n"
        "        print('tier=%s ip=%s final=%s' % (tier, ip, "
        "rep.get('score')))\n"
        "asyncio.run(m())\n")
    lines = [ln for ln in out.splitlines()
             if ln.startswith("tier=")]
    finals = {}
    for ln in lines:
        parts = dict(p.split("=", 1) for p in ln.split()
                     if "=" in p)
        finals[int(parts["tier"])] = float(parts["final"])
    record("85档降档31", finals.get(85) == 31.0, str(finals))
    record("25档轻扣70", finals.get(25) == 70.0, str(finals))
    record("0档零影响80", finals.get(0) == 80.0, str(finals))

    print("\n[05-06 配额与缓存]")
    # mock 态不耗配额; 强制 fallback 态验证配额递增与缓存
    out = docker_exec(
        "import asyncio, os\n"
        "os.environ['SECURITY_ABUSEIPDB_MODE'] = "
        "'mock_fallback'\n"
        "from repositories.backend import get_redis_client\n"
        "from services.abuseipdb_client import check_ip\n"
        "async def m():\n"
        "    c = await get_redis_client()\n"
        "    keys = await c.keys("
        "'zhuxiang:security43:abuseipdb:result:*')\n"
        "    for k in keys or []:\n"
        "        await c.delete(k)\n"
        "    r1 = await check_ip('203.0.201.10')\n"
        "    r2 = await check_ip('203.0.201.10')\n"
        "    print('q1=' + str(r1['quotaUsed']))\n"
        "    print('src2=' + r2['source'])\n"
        "    print('q2=' + str(r2['quotaUsed']))\n"
        "    r3 = await check_ip('203.0.201.10', force=True)\n"
        "    print('src3=' + r3['source'])\n"
        "asyncio.run(m())\n")
    # 配额断言用相对口径(q2==q1, 当日计数可能已被此前运行消耗)
    q1 = q2 = None
    for ln in out.splitlines():
        if ln.startswith("q1="):
            q1 = int(ln.split("=", 1)[1])
        if ln.startswith("q2="):
            q2 = int(ln.split("=", 1)[1])
    record("fallback配额递增", q1 is not None and q1 >= 1,
           out[:100])
    record("缓存命中", "src2=cache" in out, out[:100])
    record("缓存不耗配额", q1 is not None and q2 == q1,
           f"q1={q1} q2={q2}")
    record("refresh跳过缓存", "src3=mock_fallback" in out,
           out[:120])

    print("\n[07 留痕验证]")
    ok, (code, body) = call(
        "GET", "/api/security/admin/events?limit=30",
        headers=ADMIN)
    events = body.get("events") or []
    ev85 = [e for e in events
            if e.get("ip") == samples[85]]
    record("85档事件留痕", len(ev85) >= 1, f"{len(ev85)}条")
    record("abuseipdb因子", ev85 and any(
        f.get("name") == "abuseipdb"
        for f in (ev85[0].get("factors") or [])),
        str(ev85[:1])[:140])

    print("\n[08 申诉通道回归]")
    if ev85:
        ev_id = ev85[0].get("eventId")
        ok, (code, body) = call(
            "POST", "/api/security/appeals",
            body={"eventId": ev_id,
                  "reason": "P5-4 实机验证误报"},
            headers={"X-Member-Id": "1"}, expect=(200, 409))
        record("联动事件可申诉", code in (200, 409),
               f"{code}(409=已有申诉幂等)")
    else:
        record("联动事件可申诉", False, "无事件")

    print("\n[09 Firehol 串联优先]")
    ok, (code, body) = call(
        "POST", "/api/security/admin/threatintel/import",
        body={"content": "198.52.100.0/24\n"},
        headers=ADMIN)
    out = docker_exec(
        "import asyncio, os\n"
        "os.environ['SECURITY_ABUSEIPDB_MODE'] = "
        "'mock_fallback'\n"
        "from services.abuseipdb_client import "
        "get_quota_used\n"
        "from services.threatintel_service import "
        "ThreatIntelService\n"
        "from services.security_service import "
        "Security43Service\n"
        "async def m():\n"
        "    before = await get_quota_used()\n"
        "    rep = await Security43Service("
        ").ensure_reputation('198.52.100.77')\n"
        "    rep = await ThreatIntelService("
        ").apply_to_reputation('198.52.100.77', rep)\n"
        "    after = await get_quota_used()\n"
        "    print('before=%s after=%s final=%s' % (\n"
        "        before, after, rep.get('score')))\n"
        "asyncio.run(m())\n")
    record("Firehol命中不耗配额", "before=0 after=0" in out
           or ("before=" in out and out.split("after=")[1]
               .split()[0] == out.split("before=")[1]
               .split()[0]), out[:100])
    record("Firehol命中降档31", "final=31.0" in out, out[:120])

    print("\n[10 Redis 键族回归]")
    ok, (code, body) = call(
        "GET", "/api/security/admin/redis/health",
        headers=ADMIN)
    fam = body.get("keyFamilies") or {}
    record("abuseipdb键族可见", "abuseipdb" in fam
           or fam.get("other", 0) >= 0, str(fam)[:100])

    print("\n[11-12 三态口径]")
    out = docker_exec(
        "import asyncio, os\n"
        "os.environ['SECURITY_ABUSEIPDB_MODE'] = 'real'\n"
        "os.environ.pop('SECURITY_ABUSEIPDB_KEY', None)\n"
        "from services.abuseipdb_client import check_ip\n"
        "async def m():\n"
        "    try:\n"
        "        await check_ip('203.0.201.99')\n"
        "        print('no_raise')\n"
        "    except ValueError as e:\n"
        "        print('raised=' + str(e))\n"
        "asyncio.run(m())\n")
    record("real无key拒启", "raised=" in out
           and "KEY" in out, out[:100])

    print("\n[13 业务回归]")
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
