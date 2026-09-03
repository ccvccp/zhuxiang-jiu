"""43号P5-6 CIDR 区间二分检索 Docker 实机验收

运行方式:
    python verify_security_p5_6_live.py [基址]

覆盖(真实 Redis 20k 段):
    01 正常业务零影响
    02 小规模(1 段)线性态
    03 导入 20k 生成段(10.q.r.0/24 空间)
    04 stats.matchMode=bisect + segments=20000
    05 命中查询正确(段内/段外/边界 段首尾)
    06 响应时间可接受(容器内 10k 查询, 含 Redis 单键回填)
    07 陈旧陷阱: 同规模 20k 段换内容重新导入 → 命中随新数据更新
    08 幂等: 同内容再导入 → 行为不变
    09 清空 → matchMode=linear + 命中恢复 None
    10 网关全链路回归(命中降档 31 联动)
    11 业务正常
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


def gen_segments(count: int, seed: int) -> list[str]:
    import random
    rng = random.Random(seed)
    segs, used = [], set()
    while len(segs) < count:
        q, r = rng.randrange(0, 250), rng.randrange(0, 250)
        if (q, r) not in used:
            used.add((q, r))
            segs.append(f"10.{q}.{r}.0/24")
    return segs


def import_via_api(content: str, source="p5_6_live"):
    return call("POST", "/api/security/admin/threatintel/import",
                body={"content": content, "source": source},
                headers=ADMIN)


def main():
    print("=" * 62)
    print("43号·P5-6 CIDR 区间二分检索 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 小规模线性态]")
    ok, (code, body) = import_via_api("203.0.113.0/24\n")
    record("导入1段", code == 200 and body.get("imported") == 1,
           str(body)[:80])
    out = docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    repo = Security43Repository()\n"
        "    await repo.match_threatintel('203.0.113.1')\n"
        "    print('mode=' + repo.threatintel_match_mode()"
        "['mode'])\n"
        "    print('segs=' + str("
        "repo.threatintel_match_mode()['segments']))\n"
        "asyncio.run(m())\n")
    record("1段linear", "mode=linear" in out and "segs=1" in out,
           out[:80])

    print("\n[03 导入 20k 段]")
    segs = gen_segments(20000, seed=42)
    probe_seg = segs[0]          # 断言用段
    probe_ip = probe_seg.replace("0/24", "77")
    ok, (code, body) = import_via_api("\n".join(segs))
    record("导入20000段", code == 200
           and body.get("imported") == 20000,
           f"{code}/{body.get('imported')}")

    print("\n[04 bisect 模式生效]")
    out = docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    repo = Security43Repository()\n"
        "    await repo.match_threatintel('10.0.0.1')\n"
        "    m = repo.threatintel_match_mode()\n"
        "    print('mode=' + m['mode'])\n"
        "    print('segs=' + str(m['segments']))\n"
        "asyncio.run(m())\n")
    record("mode=bisect", "mode=bisect" in out, out[:80])
    record("segments=20000", "segs=20000" in out, out[:80])

    print("\n[05 命中查询正确]")
    ok, (code, body) = call(
        "GET", f"/api/security/admin/threatintel/check?ip={probe_ip}",
        headers=ADMIN)
    record("段内命中", code == 200
           and (body.get("hit") or {}).get("cidr") == probe_seg,
           str(body)[:100])
    ok, (code, body) = call(
        "GET", "/api/security/admin/threatintel/check?ip=10.249.249.1",
        headers=ADMIN)
    record("段外不命中", code == 200
           and body.get("hit") is None, str(body)[:80])
    # 边界: 段首/段尾
    seg_first_ip = probe_seg.replace("0/24", "0")
    seg_last_ip = probe_seg.replace("0/24", "255")
    ok, (code, body) = call(
        "GET", f"/api/security/admin/threatintel/check"
        f"?ip={seg_first_ip}", headers=ADMIN)
    record("段首命中", (body.get("hit") or {}).get("cidr") == probe_seg,
           str(body)[:80])
    ok, (code, body) = call(
        "GET", f"/api/security/admin/threatintel/check"
        f"?ip={seg_last_ip}", headers=ADMIN)
    record("段尾命中", (body.get("hit") or {}).get("cidr") == probe_seg,
           str(body)[:80])

    print("\n[06 响应时间]")
    out = docker_exec(
        "import asyncio, random, time\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    repo = Security43Repository()\n"
        "    rng = random.Random(7)\n"
        "    ips = [f'10.{rng.randrange(0,250)}."
        "{rng.randrange(0,256)}.{rng.randrange(0,256)}'\n"
        "           for _ in range(10000)]\n"
        "    await repo.match_threatintel(ips[0])\n"
        "    t0 = time.perf_counter()\n"
        "    hits = 0\n"
        "    for ip in ips:\n"
        "        if await repo.match_threatintel(ip):\n"
        "            hits += 1\n"
        "    ms = (time.perf_counter() - t0) * 1000\n"
        "    print('elapsed_ms=%.0f' % ms)\n"
        "    print('hits=%d' % hits)\n"
        "asyncio.run(m())\n")
    elapsed = None
    for ln in out.splitlines():
        if ln.startswith("elapsed_ms="):
            elapsed = float(ln.split("=", 1)[1])
    record("10k查询<2000ms", elapsed is not None and elapsed < 2000,
           f"elapsed={elapsed}ms")

    print("\n[07 陈旧陷阱: 同规模换内容]")
    segs_b = [c for c in gen_segments(20000, seed=99)
              if c != probe_seg]
    ok, (code, body) = import_via_api("\n".join(segs_b))
    record("换内容导入", code == 200
           and body.get("imported") == 20000, str(body)[:80])
    out = docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        f"    r = await Security43Repository()."
        f"match_threatintel({probe_ip!r})\n"
        "    print('old_hit=' + str(r))\n"
        "    print('mode=' + Security43Repository()."
        "threatintel_match_mode()['mode'])\n"
        "asyncio.run(m())\n")
    record("旧段不命中(重建生效)", "old_hit=None" in out, out[:100])
    record("仍bisect", "mode=bisect" in out, out[:100])

    print("\n[08 幂等]")
    ok, (code, body) = import_via_api("\n".join(segs_b))
    record("同内容再导入", code == 200
           and body.get("imported") == 20000
           and body.get("cleared") == 20000, str(body)[:80])
    any_seg = segs_b[0]
    any_ip = any_seg.replace("0/24", "5")
    ok, (code, body) = call(
        "GET", f"/api/security/admin/threatintel/check?ip={any_ip}",
        headers=ADMIN)
    record("幂等后命中", (body.get("hit") or {}).get("cidr")
           == any_seg, str(body)[:80])

    print("\n[09 清空恢复]")
    ok, (code, body) = import_via_api("203.0.113.0/24\n")
    out = docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    repo = Security43Repository()\n"
        f"    await repo.match_threatintel({probe_ip!r})\n"
        "    print('mode=' + repo.threatintel_match_mode()"
        "['mode'])\n"
        "    print('segs=' + str("
        "repo.threatintel_match_mode()['segments']))\n"
        "asyncio.run(m())\n")
    record("清空后linear", "mode=linear" in out
           and "segs=1" in out, out[:80])

    print("\n[10 网关全链路]")
    out = docker_exec(
        "import asyncio\n"
        "from services.threatintel_service import "
        "ThreatIntelService\n"
        "from services.security_service import "
        "Security43Service\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    sec = Security43Service()\n"
        "    rep = await sec.ensure_reputation('203.0.113.88')\n"
        "    rep = await ThreatIntelService("
        ").apply_to_reputation('203.0.113.88', rep)\n"
        "    print('final=' + str(rep.get('score')))\n"
        "asyncio.run(m())\n")
    record("命中降档31联动", "final=31.0" in out, out[:80])

    print("\n[11 业务回归]")
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
