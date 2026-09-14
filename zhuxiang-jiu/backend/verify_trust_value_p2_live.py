"""45号P2 即时修复引擎 Docker 实机验收

运行方式:
    python verify_trust_value_p2_live.py [基址]

前置: 容器已运行。

覆盖(计划 §五, 真实容器):
    01 正常业务零影响
    02 违规建档(general 扣分)
    03 修复计划(违规即列/β 清单/时效窗口提示)
    04 修复全链(提交→验真→天花板→分数回升)
    05 severe 天花板修复(α=0.3)
    06 修复明细/验真回放
    07 业务回归
"""
import json
import sys
import urllib.parse
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


def clear_trust45() -> None:
    import subprocess
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:trust45:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def call(method, path, body=None, headers=None, expect=(200,)):
    if "?" in path:
        p, q = path.split("?", 1)
        parts = []
        for kv in q.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                parts.append(f"{urllib.parse.quote(k)}="
                             f"{urllib.parse.quote(v)}")
            else:
                parts.append(urllib.parse.quote(kv))
        path = p + "?" + "&".join(parts)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                  method=method)
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


GOOD_EVIDENCE = "整改验收报告 编号ZG2026-088 2026-09-01"


def main():
    print("=" * 62)
    print("45号·P2 即时修复引擎 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_trust45()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 违规建档]")
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person", "name": "实机修复测试",
        "idNumber": "LIVE-REPAIR-001"})
    tid = body.get("trustId")
    record("建档55", code == 200 and body.get("score") == 55.0,
           str(body)[:60])
    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid}/events",
        body={"layer": "L1", "factor": "regulatory",
              "delta": -20, "severity": "general",
              "summary": "实机行政处罚"}, headers=ADMIN)
    record("违规扣分", code == 200
           and body.get("score") == 55.0 - 3.4,
           str(body.get("score")))
    # regulatory 80-20=60 → L1 层 (80+60+80)/3=73.33
    # 贡献变化 = 0.5×(73.33-80) = -3.33
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    events = body.get("recentEvents") or []
    vid = events[-1].get("eventId")
    record("违规事件定位", vid is not None, str(vid))

    print("\n[03 修复计划]")
    ok, (code, body) = call(
        "GET", f"/api/trust/repairs/{tid}/plan")
    record("计划200违规即列", code == 200
           and body.get("violationsRepairable") == 1,
           str(body)[:80])
    plan = (body.get("plans") or [{}])[0]
    rec_items = plan.get("recommendedRepairs") or []
    record("建议清单β降序", len(rec_items) >= 3
           and rec_items[0]["beta"] >= rec_items[-1]["beta"],
           str([i.get("beta") for i in rec_items]))
    record("时效窗口提示", "18 倍" in (body.get("note") or ""),
           str(body.get("note"))[:60])

    print("\n[04 修复全链]")
    before = None
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    before = body.get("score")
    ok, (code, body) = call("POST", "/api/trust/repairs", body={
        "trustId": tid, "violationEventId": vid,
        "repairs": [{"kind": "regulatory_rectification",
                     "value": 40,
                     "evidence": GOOD_EVIDENCE}],
        "sources": ["gov_penalty", "media"]})
    record("修复200生效", code == 200
           and body.get("applied") is True
           and body.get("gain") == 20.0,
           str(body)[:90])
    # raw=57(×0.95 intent), cap=20 → 20
    record("天花板保护", body.get("cap") == 20.0
           and body.get("rawGain") == 57.0,
           f"raw={body.get('rawGain')} cap={body.get('cap')}")
    rid = body.get("repairId")
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    record("分数回升", body.get("score") > before,
           f"{before} → {body.get('score')}")

    print("\n[05 severe 天花板]")
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person", "name": "实机severe",
        "idNumber": "LIVE-REPAIR-002"})
    tid2 = body.get("trustId")
    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid2}/events",
        body={"layer": "L1", "factor": "regulatory",
              "delta": -40, "severity": "severe",
              "summary": "实机严重违法"}, headers=ADMIN)
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid2}")
    events2 = body.get("recentEvents") or []
    vid2 = events2[-1].get("eventId")
    ok, (code, body) = call("POST", "/api/trust/repairs", body={
        "trustId": tid2, "violationEventId": vid2,
        "repairs": [{"kind": "regulatory_rectification",
                     "value": 40,
                     "evidence": GOOD_EVIDENCE}],
        "sources": ["gov_penalty", "media"]})
    # α=0.3: raw=0.3×1.5×40×0.95=17.1; cap=40×0.3=12 → 12
    record("severe修复α=0.3", code == 200
           and body.get("alpha") == 0.3,
           str(body)[:80])
    record("severe天花板12", body.get("gain") == 12.0,
           f"gain={body.get('gain')} cap={body.get('cap')}")
    # severe 熔断仍锁(修复未过熔断判定——熔断计数在)
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid2}")
    record("severe仍锁critical", body.get("grade") == "critical"
           and body.get("score") == 29.9,
           f"{body.get('score')}/{body.get('grade')}")

    print("\n[06 明细与回放]")
    ok, (code, body) = call(
        "GET", f"/api/trust/repairs/detail/{rid}")
    record("明细200applied", code == 200
           and body.get("status") == "applied"
           and body.get("delta") > 0, str(body)[:80])
    ok, (code, body) = call(
        "POST", f"/api/trust/repairs/{rid}/verify")
    record("验真回放200", code == 200
           and "verifyNote" in body, str(code))
    ok, (code, _) = call(
        "GET", "/api/trust/repairs/detail/99999", expect=(404,))
    record("明细404", code == 404, str(code))

    print("\n[07 业务回归]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
