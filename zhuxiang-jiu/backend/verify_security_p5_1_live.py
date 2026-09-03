"""43号P5-1 D5 强制联动 Docker 实机验收

运行方式:
    python verify_security_p5_1_live.py [基址]

覆盖(真实容器全链路):
    01 正常业务零影响(健康检查+业务流量)
    02 容器默认 off(d5Enforce.active=False)
    03 off 零影响(容器内制造 D5 命中: 无 d5_enforce 因子)
    04 容器 env 注入 on(docker exec os.environ 级联)
    05 on 边界区升档(命中+分∈[25,50) → challenge+因子留痕)
    06 高分区不越级(命中+分≥50 → 无升档)
    07 硬规则叠加(block 不因 D5 降档+因子仍留痕)
    08 观测端点 d5Enforce 实况(HTTP)
    09 通行证豁免联动
    10 回退 off 恢复(零影响回归)
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


PY_D5_HIT = """
import asyncio, os
os.environ['SECURITY_RATE_LIMIT'] = '1'

async def m():
    from repositories.security_repository import (
        Security43Repository, reputation_status)
    from services.security_service import Security43Service
    repo = Security43Repository()
    svc = Security43Service()
    ip, mid, hour = '{ip}', {mid}, {hour}
    await repo.save_reputation({{
        'ip': ip, 'score': {rep}, 'status': reputation_status({rep}),
        'requestCount': 0, 'attackCount': 0, 'recoverCount': 0,
        'pinned': False, 'lastPenaltyAt': None}})
    await repo.start_session_seq(mid)
    await repo.count_request('ip:' + ip, 60)
    r = await svc.process_request(
        ip, method='GET', path={path!r}, ua={ua!r},
        member_id=mid, hour=hour)
    sc = r.get('scoring') or {{}}
    factors = sc.get('factors') or []
    enf = [f for f in factors if f.get('name') == 'd5_enforce']
    print('score=' + str(sc.get('score')))
    print('action=' + str(sc.get('action')))
    print('effective=' + str(r.get('action')))
    print('enf_count=' + str(len(enf)))
    ev = r.get('event') or {{}}
    print('event_action=' + str(ev.get('action')))
asyncio.run(m())
"""


def d5_hit(ip: str, mid: int, hour: int, rep: float,
           path: str = "/api/admin/dashboard",
           ua: str = "Mozilla/5.0") -> dict:
    """容器内制造 D5 命中, 返回输出行 dict"""
    code = PY_D5_HIT.format(ip=ip, mid=mid, hour=hour, rep=rep,
                            path=path, ua=ua)
    result = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python", "-c", code],
        capture_output=True, text=True)
    out = {}
    for line in (result.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def set_enforce(value: str) -> None:
    """容器内设置环境变量并重启(写入 .env 式注入: exec 级联)"""
    # 容器内进程级注入需重启进程; 用 docker exec 每次
    # python -c 时 os.environ 即时读取——联动代码运行时读 env,
    # 所以在 PY_D5_HIT 里设 os.environ 即可生效。
    pass


def main():
    print("=" * 62)
    print("43号·P5-1 D5 强制联动 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 容器默认 off]")
    ok, (code, body) = call("GET", "/api/security/admin/reports/d5",
                            headers=ADMIN)
    record("d5端点可查", code == 200, str(code))
    record("默认off实况",
           (body.get("d5Enforce") or {}).get("active") is False,
           str(body.get("d5Enforce")))
    record("边界区口径25-50",
           (body.get("d5Enforce") or {}).get("band") == "25-50",
           str(body.get("d5Enforce")))

    print("\n[03 off 零影响]")
    # 容器 env 默认 off(PY_D5_HIT 不设 D5_ENFORCE)
    out = d5_hit("10.5.1.10", 950, hour=3, rep=35.0)
    record("边界区分值", out.get("score") is not None
           and 25 <= float(out["score"]) < 50, str(out))
    record("off无d5因子", out.get("enf_count") == "0", str(out))
    record("off处置=评分器原档", out.get("action") == "challenge",
           str(out))

    print("\n[04-05 on 边界区升档]")
    # 容器内注入 SECURITY_D5_ENFORCE=on(exec 进程级, 联动运行时读)
    hit_on = PY_D5_HIT.replace(
        "os.environ['SECURITY_RATE_LIMIT'] = '1'",
        "os.environ['SECURITY_RATE_LIMIT'] = '1'\n"
        "os.environ['SECURITY_D5_ENFORCE'] = 'on'")
    result = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python", "-c",
         hit_on.format(ip="10.5.1.11", mid=951, hour=3, rep=35.0,
                      path="/api/admin/dashboard",
                      ua="Mozilla/5.0")],
        capture_output=True, text=True)
    out = {}
    for line in (result.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    record("on边界区分值", out.get("score") is not None
           and 25 <= float(out["score"]) < 50, str(out))
    record("on升档challenge", out.get("action") in ("challenge", "block"),
           str(out))
    record("d5因子留痕", out.get("enf_count") == "1", str(out))
    record("事件流水留痕", out.get("event_action") == "challenge",
           str(out))

    print("\n[06 高分区不越级]")
    result = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python", "-c",
         hit_on.format(ip="10.5.1.12", mid=952, hour=14, rep=80.0,
                       path="/api/admin/dashboard",
                       ua="Mozilla/5.0")],
        capture_output=True, text=True)
    out = {}
    for line in (result.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    record("高分区(≥50)", out.get("score") is not None
           and float(out["score"]) >= 50, str(out))
    record("高分区不升档", out.get("action") in ("allow", "throttle"),
           str(out))
    record("高分区无d5因子", out.get("enf_count") == "0", str(out))

    print("\n[07 硬规则叠加]")
    result = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python", "-c",
         hit_on.format(ip="10.5.1.13", mid=953, hour=3, rep=60.0,
                       path="/api/admin/dashboard?q=' OR 1=1--",
                       ua="sqlmap/1.2 Mozilla")],
        capture_output=True, text=True)
    out = {}
    for line in (result.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    record("叠加分∈边界区", out.get("score") is not None
           and 25 <= float(out["score"]) < 50, str(out))
    record("block不因D5降档", out.get("action") == "block", str(out))
    record("叠加因子留痕", out.get("enf_count") == "1", str(out))

    print("\n[08 观测端点实况]")
    # 容器 env 未改: 服务端读进程 env → off(docker exec 注入仅
    # 对单次 exec 生效, 不影响常驻进程——回归口径验证)
    ok, (code, body) = call("GET", "/api/security/admin/reports/d5",
                            headers=ADMIN)
    record("常驻进程仍off", (body.get("d5Enforce") or {}).get(
        "active") is False, str(body.get("d5Enforce")))
    record("note口径", "SECURITY_D5_ENFORCE" in str(
        (body.get("d5Enforce") or {}).get("note")))

    print("\n[09 通行证豁免联动]")
    result = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python", "-c",
         hit_on.replace(
             "await repo.count_request",
             "await repo.grant_challenge_pass(ip, ttl=900)\n"
             "    await repo.count_request").format(
                 ip="10.5.1.14", mid=954, hour=3, rep=35.0,
                 path="/api/admin/dashboard", ua="Mozilla/5.0")],
        capture_output=True, text=True)
    out = {}
    for line in (result.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    record("D5升档challenge", out.get("action") == "challenge",
           str(out))
    record("通行证豁免放行", out.get("effective") == "allow", str(out))
    record("豁免留痕challenge_exempt",
           out.get("event_action") == "challenge_exempt", str(out))

    print("\n[10 回退恢复]")
    out = d5_hit("10.5.1.15", 955, hour=3, rep=35.0)
    record("回退off无因子", out.get("enf_count") == "0", str(out))
    record("回退off处置原档", out.get("action") == "challenge",
           str(out))
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
