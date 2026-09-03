"""43号P6-1 情报源聚合与规模扩展 Docker 实机验收

运行方式:
    python verify_security_p6_1_live.py [基址]

覆盖(真实 Redis 180k 段三源聚合, 计划 §六):
    01 正常业务零影响
    02 上限提额(容器 env MAX_CIDRS=200000 生效)
    03 三源聚合导入 180k(10/11/12 空间各 60k + v6 少量;
       耗时门槛 <60s + 内存增量 <50MB)
    04 区间构建 <5s + matchMode=bisect + segments=180020
    05 10k 查询 <3s(含命中样本存在断言)
    06 stats <500ms(计数器化)+ 三源分布正确
    07 匹配正确性抽样(段首/段尾/段外)
    08 按源替换: 刷新 srcB(同规模换内容)→ 其余两源段保留
       + 旧段未命中 + 新段生效 + 总数守恒
    09 v6 段命中
    10 degraded 多源: 坏源连续 3 次 → degradedSources 含该源
       / 其余源正常导入
    11 S2 信号按源触达(rule 含源名, 单源回退口径不变)
    12 P4-4 Redis 体检回归(18 万段键族计数正常)
    13 清空恢复 + 单源兼容口径
    14 业务回归
"""
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}

TOTAL_V4 = 60000            # 每源 v4 段数
V6_COUNT = 20               # srcC 附加 v6 段数
TOTAL = TOTAL_V4 * 3 + V6_COUNT   # 180020

IMPORT_GATE_S = 60.0        # 导入耗时门槛
BUILD_GATE_S = 5.0          # 区间构建门槛
QUERY_10K_GATE_S = 3.0      # 10k 查询门槛
STATS_GATE_MS = 500.0       # stats 门槛
MEM_GATE_MB = 50.0          # 容器内存增量门槛


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
        with urllib.request.urlopen(req, timeout=120) as resp:
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


def docker_exec_lines(python_code: str) -> dict:
    """docker exec → 解析 key=value 行"""
    out = {}
    for ln in docker_exec(python_code).splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            out[k] = v
    return out


def container_rss_kb() -> int:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "sh", "-c",
         "grep VmRSS /proc/1/status | awk '{print $2}'"],
        capture_output=True, text=True)
    try:
        return int((out.stdout or "0").strip() or 0)
    except ValueError:
        return 0


def gen_files() -> None:
    """容器内生成三源 netset 文件(/tmp)"""
    docker_exec(
        "lines=[]\n"
        f"for i in range({TOTAL_V4}):\n"
        "    lines.append('10.%d.%d.0/24' % (i // 256, i % 256))\n"
        "open('/tmp/srcA.netset','w').write('\\n'.join(lines))\n")
    docker_exec(
        "lines=[]\n"
        f"for i in range({TOTAL_V4}):\n"
        "    lines.append('11.%d.%d.0/24' % (i // 256, i % 256))\n"
        "open('/tmp/srcB.netset','w').write('\\n'.join(lines))\n")
    docker_exec(
        "lines=[]\n"
        f"for i in range({TOTAL_V4}):\n"
        "    lines.append('12.%d.%d.0/24' % (i // 256, i % 256))\n"
        f"for i in range({V6_COUNT}):\n"
        "    lines.append('2001:db8:%x::/48' % (0x100 + i))\n"
        "open('/tmp/srcC.netset','w').write('\\n'.join(lines))\n")


def timed_import(source: str, path: str) -> dict:
    """容器内计时导入(netset 文件 → import_netset)"""
    return docker_exec_lines(
        "import asyncio, time\n"
        "from services.threatintel_service import ThreatIntelService\n"
        "async def m():\n"
        f"    content = open({path!r}).read()\n"
        "    t0 = time.perf_counter()\n"
        f"    r = await ThreatIntelService().import_netset("
        f"content, source={source!r})\n"
        "    ms = (time.perf_counter() - t0) * 1000\n"
        "    print('elapsed_ms=%.0f' % ms)\n"
        "    print('imported=%s' % r['imported'])\n"
        "    print('cleared=%s' % r['cleared'])\n"
        "asyncio.run(m())\n")


def setup_mock_source() -> None:
    """容器内建 mock netset + http.server(P5-3 范式)"""
    docker_exec(
        "lines = ['172.20.%d.0/24' % i for i in range(150)]\n"
        "open('/tmp/mocknetset.txt','w').write('\\n'.join(lines))\n")
    subprocess.run(
        ["docker", "exec", "-d", "zhuxiang-jiu-backend-1",
         "python", "-m", "http.server", "8099",
         "--directory", "/tmp"],
        capture_output=True, text=True)


def main():
    print("=" * 62)
    print("43号·P6-1 情报源聚合与规模扩展 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))
    rss_before = container_rss_kb()

    print("\n[02 上限提额]")
    out = docker_exec(
        "from services.threatintel_service import max_import_cidrs\n"
        "print('limit=%s' % max_import_cidrs())")
    record("容器env上限200000", "limit=200000" in out, out[:60])

    print("\n[03 三源聚合导入 180k]")
    # 先清残留(历史 live 验证的段+计数器), 保证总量口径
    docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    await Security43Repository().clear_threatintel()\n"
        "asyncio.run(m())\n")
    gen_files()
    t_import_max = 0.0
    for src, path in (("p6_1_srcA", "/tmp/srcA.netset"),
                      ("p6_1_srcB", "/tmp/srcB.netset"),
                      ("p6_1_srcC", "/tmp/srcC.netset")):
        r = timed_import(src, path)
        elapsed = float(r.get("elapsed_ms", "999999")) / 1000
        t_import_max = max(t_import_max, elapsed)
        expect = TOTAL_V4 + V6_COUNT if src.endswith("srcC") \
            else TOTAL_V4
        record(f"{src}导入{expect}段",
               r.get("imported") == str(expect),
               str(r))
    record(f"导入耗时<60s(实测{t_import_max:.1f}s)",
           t_import_max < IMPORT_GATE_S,
           f"max={t_import_max:.1f}s")

    # 区间构建触发(常驻进程缓存刷新) + 内存增量
    ok, (code, body) = call(
        "GET", "/api/security/admin/threatintel/check?ip=10.0.0.1",
        headers=ADMIN)
    rss_after = container_rss_kb()
    delta_mb = (rss_after - rss_before) / 1024
    record(f"内存增量<50MB(实测{delta_mb:.1f}MB)",
           delta_mb < MEM_GATE_MB, f"delta={delta_mb:.1f}MB")

    print("\n[04 区间构建 + bisect 模式]")
    out = docker_exec(
        "import asyncio, time\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    repo = Security43Repository()\n"
        "    t0 = time.perf_counter()\n"
        "    await repo.match_threatintel('10.0.0.1')\n"
        "    ms = (time.perf_counter() - t0) * 1000\n"
        "    m = repo.threatintel_match_mode()\n"
        "    print('build_ms=%.0f' % ms)\n"
        "    print('mode=' + m['mode'])\n"
        "    print('segs=' + str(m['segments']))\n"
        "asyncio.run(m())\n")
    build_ms = None
    for ln in out.splitlines():
        if ln.startswith("build_ms="):
            build_ms = float(ln.split("=", 1)[1])
    record(f"区间构建<5s(实测{(build_ms or 0)/1000:.1f}s)",
           build_ms is not None and build_ms < BUILD_GATE_S * 1000,
           f"build={build_ms}ms")
    record("mode=bisect", "mode=bisect" in out, out[:80])
    record(f"segments={TOTAL}", f"segs={TOTAL}" in out, out[:80])

    print("\n[05 10k 查询]")
    out = docker_exec(
        "import asyncio, random, time\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    repo = Security43Repository()\n"
        "    await repo.match_threatintel('10.0.0.1')\n"
        "    rng = random.Random(7)\n"
        "    ips = [f'10.{rng.randrange(0,250)}."
        "{rng.randrange(0,256)}.{rng.randrange(0,256)}'\n"
        "           for _ in range(10000)]\n"
        "    t0 = time.perf_counter()\n"
        "    hits = 0\n"
        "    for ip in ips:\n"
        "        if await repo.match_threatintel(ip):\n"
        "            hits += 1\n"
        "    ms = (time.perf_counter() - t0) * 1000\n"
        "    print('elapsed_ms=%.0f' % ms)\n"
        "    print('hits=%d' % hits)\n"
        "asyncio.run(m())\n")
    q_ms = None
    for ln in out.splitlines():
        if ln.startswith("elapsed_ms="):
            q_ms = float(ln.split("=", 1)[1])
    record(f"10k查询<3s(实测{(q_ms or 0)/1000:.2f}s)",
           q_ms is not None and q_ms < QUERY_10K_GATE_S * 1000,
           f"elapsed={q_ms}ms")
    record("命中样本存在", "hits=" in out
           and int(out.split("hits=")[1].split()[0]) > 0, out[:60])

    print("\n[06 stats 计数器化]")
    out = docker_exec(
        "import asyncio, time, json\n"
        "from services.threatintel_service import ThreatIntelService\n"
        "async def m():\n"
        "    svc = ThreatIntelService()\n"
        "    await svc.stats()\n"   # 预热(重建写回)
        "    t0 = time.perf_counter()\n"
        "    s = await svc.stats()\n"
        "    ms = (time.perf_counter() - t0) * 1000\n"
        "    print('stats_ms=%.0f' % ms)\n"
        "    print('total=%s' % s['totalCidrs'])\n"
        "    print('srcA=%s' % s['sources'].get('p6_1_srcA'))\n"
        "    print('srcB=%s' % s['sources'].get('p6_1_srcB'))\n"
        "    print('srcC=%s' % s['sources'].get('p6_1_srcC'))\n"
        "asyncio.run(m())\n")
    s_ms = None
    kv = {}
    for ln in out.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            if k == "stats_ms":
                s_ms = float(v)
            else:
                kv[k] = v
    record(f"stats<500ms(实测{s_ms or 0:.0f}ms)",
           s_ms is not None and s_ms < STATS_GATE_MS,
           f"stats={s_ms}ms")
    record(f"totalCidrs={TOTAL}",
           kv.get("total") == str(TOTAL), str(kv))
    record("三源分布正确",
           kv.get("srcA") == str(TOTAL_V4)
           and kv.get("srcB") == str(TOTAL_V4)
           and kv.get("srcC") == str(TOTAL_V4 + V6_COUNT),
           str(kv))

    print("\n[07 匹配正确性抽样]")
    checks = [
        ("段首命中", "10.5.9.0", "10.5.9.0/24"),
        ("段尾命中", "10.5.9.255", "10.5.9.0/24"),
        ("段外不命中", "13.5.9.1", None),
    ]
    for name, ip, expect_cidr in checks:
        out = docker_exec(
            "import asyncio\n"
            "from repositories.security_repository import "
            "Security43Repository\n"
            "async def m():\n"
            f"    r = await Security43Repository()."
            f"match_threatintel({ip!r})\n"
            "    print('cidr=' + str((r or {}).get('cidr')))\n"
            "asyncio.run(m())\n")
        got = None
        for ln in out.splitlines():
            if ln.startswith("cidr="):
                got = ln.split("=", 1)[1]
        record(name, got == str(expect_cidr),
               f"got={got}")

    print("\n[08 按源替换(同规模换内容)]")
    # srcB 刷新: 11.x(60k) → 13.x(60k); 其余源段保留
    docker_exec(
        "lines=[]\n"
        f"for i in range({TOTAL_V4}):\n"
        "    lines.append('13.%d.%d.0/24' % (i // 256, i % 256))\n"
        "open('/tmp/srcB2.netset','w').write('\\n'.join(lines))\n")
    r = timed_import("p6_1_srcB", "/tmp/srcB2.netset")
    record("同源刷新cleared=60000",
           r.get("imported") == str(TOTAL_V4)
           and r.get("cleared") == str(TOTAL_V4), str(r))
    out = docker_exec(
        "import asyncio, json\n"
        "from services.threatintel_service import ThreatIntelService\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    s = await ThreatIntelService().stats()\n"
        "    print('total=%s' % s['totalCidrs'])\n"
        "    print('srcA=%s' % s['sources'].get('p6_1_srcA'))\n"
        "    print('srcC=%s' % s['sources'].get('p6_1_srcC'))\n"
        "    old = await Security43Repository()."
        "match_threatintel('11.0.0.1')\n"
        "    new = await Security43Repository()."
        "match_threatintel('13.0.0.1')\n"
        "    keep = await Security43Repository()."
        "match_threatintel('12.0.0.1')\n"
        "    print('old_hit=' + str(old))\n"
        "    print('new_hit=' + str((new or {}).get('cidr')))\n"
        "    print('keep_hit=' + str((keep or {}).get('cidr')))\n"
        "asyncio.run(m())\n")
    kv = {}
    for ln in out.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            kv[k] = v
    record("总数守恒180020", kv.get("total") == str(TOTAL),
           str(kv))
    record("其余源段保留",
           kv.get("srcA") == str(TOTAL_V4)
           and kv.get("srcC") == str(TOTAL_V4 + V6_COUNT),
           str(kv))
    record("旧段未命中", kv.get("old_hit") == "None", str(kv))
    record("新段生效", kv.get("new_hit") == "13.0.0.0/24", str(kv))
    record("未刷新源命中不变", kv.get("keep_hit") == "12.0.0.0/24",
           str(kv))

    print("\n[09 v6 段命中]")
    out = docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    r = await Security43Repository()."
        "match_threatintel('2001:db8:105::1')\n"
        "    print('cidr=' + str((r or {}).get('cidr')))\n"
        "asyncio.run(m())\n")
    record("v6段命中", "cidr=2001:db8:105::/48" in out, out[:60])

    print("\n[10 degraded 多源]")
    setup_mock_source()
    bad = "http://127.0.0.1:1/bad.netset"
    good = "http://127.0.0.1:8099/mocknetset.txt"
    # 重置两源状态(防历史残留计数)
    docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    repo = Security43Repository()\n"
        "    await repo.save_threatintel_auto_state("
        "{'lastAutoImportAt': '', 'lastAutoStatus': '', "
        "'consecutiveFailures': 0, 'lastError': ''}, "
        "source='bad')\n"
        "    await repo.save_threatintel_auto_state("
        "{'lastAutoImportAt': '', 'lastAutoStatus': '', "
        "'consecutiveFailures': 0, 'lastError': ''}, "
        "source='good')\n"
        "asyncio.run(m())\n")
    out = docker_exec(
        "import asyncio, os\n"
        "os.environ['SECURITY_THREATINTEL_URLS'] = "
        f"'good={good},bad={bad}'\n"
        "from services.threatintel_feed import (maybe_refresh, "
        "degraded_sources)\n"
        "async def m():\n"
        "    for _ in range(3):\n"
        f"        r = await maybe_refresh(source={{'name': 'bad', "
        f"'url': {bad!r}}}, force=True)\n"
        "    print('bad_failures=%s' % r['consecutiveFailures'])\n"
        f"    r = await maybe_refresh(source={{'name': 'good', "
        f"'url': {good!r}}}, force=True)\n"
        "    print('good_executed=%s' % r['executed'])\n"
        "    print('good_imported=%s' % r.get('imported'))\n"
        "    d = await degraded_sources()\n"
        "    print('degraded=%s' % ','.join(d['degradedSources']))\n"
        "asyncio.run(m())\n")
    bad_failures = 0
    for ln in out.splitlines():
        if ln.startswith("bad_failures="):
            try:
                bad_failures = int(ln.split("=", 1)[1])
            except ValueError:
                pass
    record("坏源连续3次失败", bad_failures >= 3,
           out[:120])
    record("其余源正常导入", "good_executed=True" in out
           and "good_imported=150" in out, out[:120])
    record("degradedSources含坏源", "degraded=bad" in out,
           out[:120])

    print("\n[11 S2 信号按源触达]")
    out = docker_exec(
        "import asyncio, os\n"
        "os.environ['SECURITY_THREATINTEL_URLS'] = "
        f"'good={good},bad={bad}'\n"
        "from services.security_alert_service import "
        "SecurityAlertService\n"
        "async def m():\n"
        "    alerts = await SecurityAlertService()."
        "_collect_intel_degraded()\n"
        "    print('rules=%s' % '|'.join("
        "a['rule'] for a in alerts))\n"
        "    print('has_src=%s' % any("
        "'威胁情报源 bad' in a['message'] for a in alerts))\n"
        "asyncio.run(m())\n")
    record("S2按源rule", "threatintel_degraded:bad" in out,
           out[:120])
    record("S2含源名消息", "has_src=True" in out, out[:150])
    # 单源回退口径(清 env 后)
    out = docker_exec(
        "import asyncio\n"
        "from services.security_alert_service import "
        "SecurityAlertService\n"
        "async def m():\n"
        "    alerts = await SecurityAlertService()."
        "_collect_intel_degraded()\n"
        "    print('rules=%s' % '|'.join("
        "a['rule'] for a in alerts))\n"
        "asyncio.run(m())\n")
    record("单源回退不含源名rule",
           "threatintel_degraded:" not in out, out[:120])

    print("\n[12 P4-4 Redis 体检回归]")
    ok, (code, body) = call(
        "GET", "/api/security/admin/redis/health", headers=ADMIN)
    alerts = body.get("alerts") or []
    huge = [a for a in alerts if "单键" in str(a.get("rule", ""))]
    record("体检端点200", code == 200, str(code))
    record("18万段无大key告警", len(huge) == 0, str(alerts)[:120])

    print("\n[13 清空恢复 + 单源兼容]")
    out = docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    n = await Security43Repository().clear_threatintel()\n"
        "    print('cleared=%s' % n)\n"
        "asyncio.run(m())\n")
    record("全清量对齐", "cleared=" in out
           and int(out.split("cleared=")[1].split()[0]) >= TOTAL,
           out[:60])
    ok, (code, body) = call(
        "POST", "/api/security/admin/threatintel/import",
        body={"content": "203.0.113.0/24\n"}, headers=ADMIN)
    record("单源导入恢复", code == 200
           and body.get("imported") == 1, str(body)[:80])
    ok, (code, body) = call(
        "GET", "/api/security/admin/threatintel/stats",
        headers=ADMIN)
    record("清空后计数器归零再导入",
           body.get("totalCidrs") == 1, str(body)[:120])
    out = docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    repo = Security43Repository()\n"
        "    await repo.match_threatintel('203.0.113.9')\n"
        "    m = repo.threatintel_match_mode()\n"
        "    print('mode=' + m['mode'])\n"
        "    print('segs=' + str(m['segments']))\n"
        "asyncio.run(m())\n")
    record("清空后linear态", "mode=linear" in out
           and "segs=1" in out, out[:60])
    # 单源兼容(状态键 legacy 口径在位)
    out = docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    s = (await Security43Repository()."
        "get_threatintel_auto_state()) or {}\n"
        "    print('legacy_status=%s' % s.get('lastAutoStatus'))\n"
        "asyncio.run(m())\n")
    record("单源兼容键在位", "legacy_status=" in out, out[:60])

    print("\n[14 业务回归]")
    ok, (code, _) = call("GET", "/api/product/list")
    record("业务正常", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
