"""43号P4-3 威胁情报接入 Docker 实机验收

运行方式:
    python verify_security_p4_3_live.py [基址]

覆盖:
    01 正常业务零影响(健康检查+业务流量)
    02 HTTP 鉴权(缺 X-Role 403)
    03 Firehol netset 导入(注释/IP/CIDR 混合)
    04 统计(段数/来源分布)
    05 单 IP 命中查询(命中/未命中)
    06 容器内网关信誉联动(命中降档31+事件留痕)
    07 防误杀(未命中零影响)
    08 幂等重复导入
    09 清空后零影响
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


NETSET = ("# Firehol level1 sample\n"
          "203.0.113.7\n"
          "198.51.100.0/24\n")


def main():
    print("=" * 62)
    print("43号·P4-3 威胁情报接入 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 HTTP 鉴权]")
    ok, (code, _) = call("POST", "/api/security/admin/threatintel/import",
                         body={"content": "1.2.3.4\n"}, expect=(403,))
    record("import缺Role403", code == 403, str(code))
    ok, (code, _) = call("GET", "/api/security/admin/threatintel/stats",
                         expect=(403,))
    record("stats缺Role403", code == 403, str(code))

    print("\n[03 Firehol netset 导入]")
    ok, (code, body) = call(
        "POST", "/api/security/admin/threatintel/import",
        body={"content": NETSET, "source": "firehol_level1"},
        headers=ADMIN)
    record("导入成功2段", code == 200
           and body.get("imported") == 2, str(body)[:100])
    ok, (code, body) = call(
        "POST", "/api/security/admin/threatintel/import",
        body={"content": "garbage!"}, headers=ADMIN, expect=(409,))
    record("非法netset409", code == 409, str(code))

    print("\n[04 统计]")
    ok, (code, body) = call("GET", "/api/security/admin/threatintel/stats",
                            headers=ADMIN)
    record("统计段数", code == 200
           and body.get("totalCidrs") == 2, str(body)[:120])
    record("来源分布", (body.get("sources") or {}).get(
        "firehol_level1") == 2, str(body.get("sources")))

    print("\n[05 单 IP 命中查询]")
    ok, (code, body) = call(
        "GET", "/api/security/admin/threatintel/check?ip=203.0.113.7",
        headers=ADMIN)
    record("单IP命中", code == 200
           and (body.get("hit") or {}).get("cidr") == "203.0.113.7/32",
           str(body)[:120])
    ok, (code, body) = call(
        "GET", "/api/security/admin/threatintel/check?ip=198.51.100.55",
        headers=ADMIN)
    record("段内命中", code == 200
           and (body.get("hit") or {}).get("cidr") == "198.51.100.0/24",
           str(body)[:120])
    ok, (code, body) = call(
        "GET", "/api/security/admin/threatintel/check?ip=9.9.9.9",
        headers=ADMIN)
    record("段外未命中", code == 200
           and body.get("hit") is None, str(body)[:120])

    print("\n[06 容器内网关信誉联动]")
    out = docker_exec(
        "import asyncio\n"
        "from services.threatintel_service import ThreatIntelService\n"
        "from services.security_service import Security43Service\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    svc = ThreatIntelService()\n"
        "    sec = Security43Service()\n"
        "    repo = Security43Repository()\n"
        "    r = await sec.process_request('198.51.100.66', "
        "method='GET', path='/api/product/list', "
        "ua='Mozilla/5.0', hour=14)\n"
        "    rep = await repo.get_reputation('198.51.100.66')\n"
        "    evs = [e for e in await repo.list_events(limit=20)\n"
        "           if e.get('action') == 'threatintel_hit']\n"
        "    print('action=' + str(r.get('action')))\n"
        "    print('score=' + str(rep.get('score')))\n"
        "    print('status=' + str(rep.get('status')))\n"
        "    print('events=' + str(len(evs)))\n"
        "asyncio.run(m())\n")
    record("网关放行(不直封)", "action=allow" in out, out[:100])
    record("信誉降档31", "score=31.0" in out, out[:100])
    record("降档suspicious", "status=suspicious" in out, out[:100])
    record("事件留痕", "events=" in out and "events=0" not in out,
           out[:120])

    print("\n[07 防误杀]")
    out = docker_exec(
        "import asyncio\n"
        "from services.security_service import Security43Service\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    sec = Security43Service()\n"
        "    repo = Security43Repository()\n"
        "    r = await sec.process_request('6.6.6.6', "
        "method='GET', path='/api/product/list', "
        "ua='Mozilla/5.0', hour=14)\n"
        "    rep = await repo.get_reputation('6.6.6.6')\n"
        "    print('action=' + str(r.get('action')))\n"
        "    print('score=' + str(rep.get('score')))\n"
        "asyncio.run(m())\n")
    record("未命中零影响(80)", "action=allow" in out
           and "score=80.0" in out, out[:100])

    print("\n[08 幂等重复导入]")
    ok, (code, body) = call(
        "POST", "/api/security/admin/threatintel/import",
        body={"content": NETSET, "source": "firehol_level1"},
        headers=ADMIN)
    record("重复导入幂等", code == 200
           and body.get("imported") == 2
           and body.get("cleared") == 2, str(body)[:100])
    ok, (code, body) = call("GET", "/api/security/admin/threatintel/stats",
                            headers=ADMIN)
    record("重复导入后仍2段", code == 200
           and body.get("totalCidrs") == 2, str(body)[:100])

    print("\n[09 清空后零影响]")
    out = docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    print('cleared=' + str(await "
        "Security43Repository().clear_threatintel()))\n"
        "asyncio.run(m())\n")
    record("清空情报表", "cleared=2" in out, out[:80])
    ok, (code, body) = call(
        "GET", "/api/security/admin/threatintel/check?ip=203.0.113.7",
        headers=ADMIN)
    record("清空后不命中", code == 200
           and body.get("hit") is None, str(body)[:80])
    ok, (code, _) = call("GET", "/api/product/list")
    record("清空后业务正常", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
