"""43号P2b Docker 实机验收(态势三态 + 学习回流)

运行方式:
    python verify_security_p2b_live.py [基址]

覆盖:
    01 正常业务零影响
    02 态势查询(冷启动peace+系数)
    03 态势手动切换(alert→wartime)+钉住/解除
    04 学习回流三连(collect/run/status)
    05 UEBA回归(rebuild/baselines)
    06 P1回归(挑战验证/态势总览)
"""
import json
import sys
import urllib.request
import urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
MEMBER = {"X-Member-Id": "1"}
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


def chapter(t):
    print(f"\n[{t}]")


def main():
    print("=" * 62)
    print("43号·P2b 态势三态+学习回流 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    # 01 正常业务零影响
    chapter("01 正常业务零影响")
    ok, (code, body) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, body) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    # 02 态势查询
    chapter("02 态势查询")
    ok, (code, body) = call("GET", "/api/security/admin/posture",
                            None, ADMIN)
    record("态势查询", ok and "posture" in body
           and "rateFactor" in body, str(body)[:100])
    record("含密度EMA", "densityEma" in body, str(body)[:80])

    # 03 态势切换
    chapter("03 态势切换")
    ok, (code, body) = call("POST", "/api/security/admin/posture",
                            {"posture": "alert"}, ADMIN)
    record("切alert", ok and body.get("posture") == "alert",
           str(body)[:80])
    ok, (code, body) = call("POST", "/api/security/admin/posture",
                            {"posture": "wartime"}, ADMIN)
    record("切wartime", ok and body.get("posture") == "wartime",
           str(body)[:80])
    ok, (code, body) = call("POST",
                            "/api/security/admin/posture/pin",
                            {"pinned": True}, ADMIN)
    record("钉住", ok and body.get("pinned") is True, str(body)[:80])
    ok, (code, body) = call("POST",
                            "/api/security/admin/posture/pin",
                            {"pinned": False}, ADMIN)
    record("解除钉住", ok and body.get("pinned") is False)
    ok, (code, body) = call("POST", "/api/security/admin/posture",
                            {"posture": "peace"}, ADMIN)
    record("回peace", ok and body.get("posture") == "peace")
    ok, (code, body) = call("POST", "/api/security/admin/posture",
                            {"posture": "bad"}, ADMIN, expect=(409,))
    record("非法态势409", code == 409, str(code))

    # 04 学习回流三连
    chapter("04 学习回流")
    # 造攻击事件(SQLi observe 留痕)并裁决
    call("GET", "/api/product/search?kw=%27%20OR%201%3D1%20--")
    ok, (code, body) = call("GET", "/api/security/admin/events",
                            None, ADMIN)
    events = body.get("events", [])
    record("攻击事件可查", len(events) >= 1,
           f"total={body.get('total')}")
    pending = [e for e in events if e.get("verdict") == "pending"]
    if pending:
        eid = pending[0].get("eventId")
        ok, (code, body) = call(
            "POST", f"/api/security/admin/events/{eid}/decide",
            {"confirm": True, "reviewer": "admin",
             "note": "实机SQLi确认"}, ADMIN)
        record("事件裁决(confirmed)", ok)
    else:
        record("事件裁决(confirmed)", True, "无pending事件(已裁决)")

    ok, (code, body) = call("POST",
                            "/api/security/admin/learning/collect",
                            None, ADMIN)
    record("collect回流", ok and "submitted" in body,
           str(body)[:100])
    ok, (code, body) = call("GET",
                            "/api/security/admin/learning/status",
                            None, ADMIN)
    record("status视图", ok and "events" in body
           and "weights" in body, str(body)[:100])
    ok, (code, body) = call("POST",
                            "/api/security/admin/learning/run",
                            None, ADMIN, expect=(200, 409))
    record("run学习(409=不足正常)", code in (200, 409),
           str(code))

    # 05 UEBA 回归
    chapter("05 UEBA回归")
    ok, (code, body) = call("POST",
        "/api/security/admin/behavior/rebuild", None, ADMIN)
    record("rebuild照常", ok, str(body)[:80])
    ok, (code, body) = call("GET",
        "/api/security/admin/behavior/baselines", None, ADMIN)
    record("baselines照常", ok and "baselines" in body)

    # 06 P1 回归
    chapter("06 P1回归")
    ok, (code, body) = call("POST",
                            "/api/security/challenge/verify",
                            {"token": "p2b", "answer": "ok"})
    record("挑战验证照常", ok, str(body)[:80])
    ok, (code, body) = call("GET",
                            "/api/security/admin/dashboard",
                            None, ADMIN)
    record("态势总览照常", ok and "events" in body)

    print("\n" + "-" * 62)
    print("\n".join(RESULTS))
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
