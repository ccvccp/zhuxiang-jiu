"""43号P4-1 Docker 实机验收(SOC 安全运营日报)

运行方式:
    python verify_security_p4_1_live.py [基址]

覆盖:
    01 正常业务零影响
    02 单日日报(实机已有事件数据: SQLi/verify/auth_event 等)
    03 近 N 天序列
    04 D5 联动观测(硬标准输出)
    05 全链路回归
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


def main():
    print("=" * 62)
    print("43号·P4-1 SOC安全运营日报 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, body) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, body) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 单日日报]")
    # 制造当日事件(SQLi 攻击留痕)
    call("GET", "/api/product/search?kw=%27%20OR%201%3D1%20--")
    ok, (code, body) = call("GET",
                            "/api/security/admin/reports/daily",
                            None, ADMIN)
    r = body
    record("单日日报", ok and r.get("success") is True,
           str(r)[:80])
    record("事件分布字段", "eventsByAction" in r
           and isinstance(r["eventsByAction"], dict))
    record("裁决统计字段", "verdicts" in r
           and set(r["verdicts"].keys()) >= {
               "confirmed", "falsePositive", "pending"})
    record("误报率字段", "falsePositiveRate" in r)
    record("D5专项字段", "d5" in r
           and "samples" in r["d5"])
    record("态势时间线", "postureTimeline" in r)
    record("当日有事件", r.get("eventsTotal", 0) >= 1,
           str(r.get("eventsTotal")))

    print("\n[03 近N天序列]")
    ok, (code, body) = call(
        "GET", "/api/security/admin/reports/daily?days=7", None,
        ADMIN)
    record("7天序列", ok and len(body.get("reports", [])) == 7,
           str(body.get("days")))
    record("序列汇总", "summary" in body
           and "eventsTotal" in body["summary"],
           str(body.get("summary"))[:80])

    print("\n[04 D5联动观测]")
    ok, (code, body) = call("GET",
                            "/api/security/admin/reports/d5",
                            None, ADMIN)
    record("D5观测", ok and "recommendation" in body,
           str(body)[:80])
    record("硬标准三条件", set(body.get("criteria", {}).keys()) == {
        "observeDays", "falsePositiveRate", "samples"})
    record("建议中文名", "recommendationName" in body,
           str(body.get("recommendationName")))

    print("\n[05 全链路回归]")
    ok, (code, body) = call("POST",
                            "/api/security/challenge/verify",
                            {"captchaToken": "p4-1-live-ticket"})
    record("挑战验证照常", ok)
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
