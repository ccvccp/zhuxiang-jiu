"""47号P0 角色风险画像 Docker 实机验收

运行方式:
    python verify_trust_risk_p0_live.py [基址]

前置: 容器已运行。

覆盖(计划 §三, 真实容器):
    01 正常业务零影响
    02 建档+画像视图(冷启动 trusted)
    03 守门命中回流 E2E(consistency 透传→画像沉淀→
       hitCounts/riskEMA/信任降档)
    04 再犯回流 E2E(5 次违规+修复→recurrence 沉淀)
    05 排行+人工校准 E2E(覆盖生效→清除恢复)
    06 业务回归

每轮验收前清理 zhuxiang:trust47:* 残留, ×2 轮幂等验证。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

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


def _ev(base: str) -> str:
    return f"{base}({uuid.uuid4().hex[:8]})"


def clear_trust47() -> None:
    """清理上轮验收残留(zhuxiang:trust47:*)"""
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:trust47:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def clear_trust45_test_roles() -> None:
    """清理上轮验收建的测试档案(trust45)"""
    # 直接清整库测试角色成本高——改用唯一证件号建档,
    # 每轮新档案零冲突; 画像键已清即足够(排行只看 trust47)
    return


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


def new_role(role: str = "person") -> int:
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": role, "name": f"r0live-{uuid.uuid4().hex[:6]}",
        "idNumber": f"110101{uuid.uuid4().hex[:10]}"})
    return body.get("trustId")


def main():
    print("=" * 62)
    print("47号·P0 角色风险画像 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_trust47()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))

    print("\n[02 建档+画像冷启动]")
    tid = new_role()
    ok, (code, body) = call(
        "GET", f"/api/trust/risk/{tid}", headers=ADMIN)
    record("画像冷启动trusted",
           code == 200
           and body.get("tier") == "trusted"
           and body.get("riskEMA") == 0.0
           and body.get("eventCount") == 0,
           f"tier={body.get('tier')} "
           f"ema={body.get('riskEMA')}")
    # 鉴权
    ok, (code, _) = call("GET", f"/api/trust/risk/{tid}")
    record("画像缺Role403", code == 403, str(code))

    print("\n[03 守门命中回流 E2E]")
    # 四次守门命中(consistency=0.1)——EMA 收敛 0.5904 → watched
    for _ in range(4):
        call("POST", f"/api/trust/roles/{tid}/events",
             body={"layer": "L2",
                   "factor": "ethics_evidence",
                   "delta": 20.0, "consistency": 0.1},
             headers=ADMIN)
    ok, (code, body) = call(
        "GET", f"/api/trust/risk/{tid}", headers=ADMIN)
    record("hitCounts沉淀(4次伪善)",
           (body.get("hitCounts") or {})
           .get("hypocrisy") == 4,
           str(body.get("hitCounts")))
    record("riskEMA收敛(4次1.0=0.5904)",
           body.get("riskEMA") == 0.5904,
           f"ema={body.get('riskEMA')}")
    record("信任降档(watched)",
           body.get("tier") == "watched",
           str(body.get("tier")))
    record("事件计数4",
           body.get("eventCount") == 4,
           str(body.get("eventCount")))
    record("风险历史4条",
           len(body.get("riskHistory") or []) == 4,
           str(len(body.get("riskHistory") or [])))

    print("\n[04 再犯回流 E2E]")
    tid2 = new_role()
    for _ in range(5):
        call("POST", f"/api/trust/roles/{tid2}/events",
             body={"layer": "L1", "factor": "legal_record",
                   "delta": -10.0}, headers=ADMIN)
    ok, (code, plan) = call(
        "GET", f"/api/trust/repairs/{tid2}/plan")
    vid = ((plan.get("plans") or [{}])[0]
           .get("violationEventId"))
    ok, (code, body) = call(
        "POST", "/api/trust/repairs", body={
            "trustId": tid2, "violationEventId": vid,
            "repairs": [{
                "kind": "legal_restitution",
                "value": 30.0,
                "evidence": _ev("法院执行和解证明材料"
                               "原件")}],
            "sources": ["gov_penalty", "media"]})
    record("修复提交", code == 200
           and body.get("applied") is True,
           str(code))
    ok, (code, body) = call(
        "GET", f"/api/trust/risk/{tid2}", headers=ADMIN)
    record("再犯沉淀画像",
           (body.get("hitCounts") or {})
           .get("recurrence") == 1
           and (body.get("riskEMA") or 0) > 0,
           str(body.get("hitCounts")))

    print("\n[05 排行+人工校准 E2E]")
    ok, (code, body) = call("GET", "/api/trust/risk",
                            headers=ADMIN)
    profiles = body.get("profiles") or []
    record("排行含两档案",
           body.get("total") >= 2
           and profiles[0]["trustId"] in (tid, tid2),
           str(body.get("total")))
    record("分层统计",
           (body.get("byTier") or {}).get("watched", 0)
           >= 1,
           str(body.get("byTier")))
    # 校准
    ok, (code, body) = call(
        "POST", f"/api/trust/risk/{tid}/calibrate",
        body={"trustLevel": 0.85,
              "note": "实机复核: 高频申报系业务正常"},
        headers=ADMIN)
    record("校准覆盖", code == 200
           and body.get("trustLevel") == 0.85,
           str(body.get("trustLevel")))
    ok, (code, body) = call(
        "GET", f"/api/trust/risk/{tid}", headers=ADMIN)
    record("校准反映(trusted)",
           body.get("tier") == "trusted"
           and body.get("trustLevel")
           != body.get("trustLevelComputed"),
           str(body.get("tier")))
    # 回流不冲掉校准
    call("POST", f"/api/trust/roles/{tid}/events",
         body={"layer": "L2", "factor": "ethics_evidence",
               "delta": 5.0, "consistency": 0.1},
         headers=ADMIN)
    ok, (code, body) = call(
        "GET", f"/api/trust/risk/{tid}", headers=ADMIN)
    record("回流不冲掉校准",
           body.get("trustLevel") == 0.85,
           str(body.get("trustLevel")))
    # 清除校准
    ok, (code, body) = call(
        "POST", f"/api/trust/risk/{tid}/calibrate/clear",
        body={"note": "复核期满恢复自动"},
        headers=ADMIN)
    record("清除恢复计算值",
           code == 200
           and body.get("calibrated") is False
           and body.get("trustLevel")
           == body.get("trustLevelComputed"),
           str(body.get("trustLevel")))
    # 校准参数
    ok, (code, _) = call(
        "POST", f"/api/trust/risk/{tid}/calibrate",
        body={"trustLevel": 2.0, "note": "x"},
        headers=ADMIN, expect=(409,))
    record("校准越界409", code == 409, str(code))

    print("\n[06 业务回归]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))
    # 45号业务不受画像影响(信值分照常)
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    record("45号档案回归", code == 200
           and body.get("constitution", {}).get("L1")
           == 0.5,
           str(code))
    # 未建档 404
    ok, (code, _) = call("GET", "/api/trust/risk/99999",
                         headers=ADMIN, expect=(404,))
    record("画像404", code == 404, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
