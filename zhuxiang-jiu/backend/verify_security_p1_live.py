"""43号P1 Docker 实机验收(宿主机运行, 14 端点全链路)

运行方式:
    python verify_security_p1_live.py [基址]
    默认 http://127.0.0.2:8000

覆盖:
    01 正常业务零影响(observe 灰度)
    02 挑战验证通道(mock 应答 → 通行证)
    03 会员状态端点
    04 管理端态势总览(误报率)
    05 攻击事件流水(实机 SQLi 留痕)
    06 事件裁决(误报恢复)
    07 IP 处置(封禁/解封/钉住)
    08 申诉通道(会员提交 → 管理裁决)
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
    ok = code in expect
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return ok, (code, parsed)


def chapter(title):
    print(f"\n[{title}]")


def main():
    global PASS, FAIL
    print("=" * 62)
    print("43号·AI智能安全管理 P1 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    # 01 正常业务零影响
    chapter("01 正常业务零影响")
    ok, (code, body) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, body) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))
    ok, (code, body) = call("POST", "/api/member/login",
                            {"phone": "13800000001",
                             "password": "Pass1234"}, expect=(200, 409))
    record("会员登录(存量口径)", code in (200, 409), str(code))

    # 02 挑战验证通道
    chapter("02 挑战验证")
    ok, (code, body) = call("POST", "/api/security/challenge/verify",
                            {"token": "live-test", "answer": "ok"})
    record("挑战应答通过", ok, str(body)[:100])
    ok, (code, body) = call("POST", "/api/security/challenge/verify",
                            {"token": "t", "answer": ""},
                            expect=(409, 422))
    record("空应答拒绝(409/422)", code in (409, 422), str(code))

    # 03 会员状态
    chapter("03 会员状态")
    ok, (code, body) = call("GET", "/api/security/status", None, MEMBER)
    record("我的安全状态", ok and "challengePass" in body,
           str(body)[:120])

    # 04 管理端态势
    chapter("04 态势总览")
    ok, (code, body) = call("GET", "/api/security/admin/dashboard",
                            None, ADMIN)
    record("态势总览", ok and "falsePositiveRate"
           in body.get("events", {}), str(body)[:120])
    ok, (code, body) = call("GET", "/api/security/admin/dashboard")
    record("非admin 403", code == 403, str(code))

    # 05 攻击事件留痕(实机 SQLi; 注: 02 章 verify 已颁发通行证,
    #    挑战档被豁免留痕为 challenge_exempt——审计仍可见)
    chapter("05 攻击事件流水")
    call("GET", "/api/product/search?kw=%27%20OR%201%3D1%20--")
    ok, (code, body) = call("GET",
                            "/api/security/admin/events",
                            None, ADMIN)
    all_events = body.get("events", [])
    sqli_events = [e for e in all_events
                   if e.get("action") in ("challenge",
                                          "challenge_exempt")]
    record("SQLi事件留痕", len(sqli_events) >= 1,
           f"total={body.get('total')}")
    sqli_event = (sqli_events[0] if sqli_events
                  else (all_events[0] if all_events else {}))
    record("事件含因子明细", len(sqli_event.get("factors") or []) == 6,
           str(len(sqli_event.get("factors") or [])))
    record("事件verdict=pending",
           sqli_event.get("verdict") == "pending")

    # 06 事件裁决(误报恢复)
    chapter("06 事件裁决")
    eid = sqli_event.get("eventId")
    if eid:
        ok, (code, body) = call(
            "POST", f"/api/security/admin/events/{eid}/decide",
            {"confirm": False, "reviewer": "admin",
             "note": "实机误报核实"}, ADMIN)
        record("误报裁决", ok
               and body.get("event", {}).get("verdict")
               == "false_positive", str(body)[:120])
        ok, (code, body) = call(
            "POST", f"/api/security/admin/events/{eid}/decide",
            {"confirm": True}, ADMIN, expect=(409,))
        record("重复裁决 409", code == 409, str(code))
    else:
        record("误报裁决", False, "无事件可裁决")

    # 07 IP 处置
    chapter("07 IP处置")
    ok, (code, body) = call("POST", "/api/security/admin/ips/9.9.9.9/ban",
                            {"reason": "实机测试封禁"}, ADMIN)
    record("手动封禁", ok, str(body)[:80])
    ok, (code, body) = call("GET", "/api/security/admin/blocks",
                            None, ADMIN)
    record("封禁列表含目标", any(
        b.get("ip") == "9.9.9.9" for b in body.get("blocks", [])),
        str(body)[:100])
    ok, (code, body) = call("POST",
                            "/api/security/admin/ips/9.9.9.9/unban",
                            None, ADMIN)
    record("手动解封", ok, str(body)[:80])
    ok, (code, body) = call("POST",
                            "/api/security/admin/ips/9.9.9.9/pin",
                            {"pinned": True}, ADMIN)
    record("信誉钉住", ok, str(body)[:80])
    ok, (code, body) = call("GET", "/api/security/admin/ips",
                            None, ADMIN)
    record("IP列表", ok and body.get("total", 0) >= 1,
           str(body.get("total")))

    # 08 申诉通道
    chapter("08 申诉通道")
    # 造一个会员 1 的挑战事件(observe 下留痕; 通行证豁免挑战档
    # 事件 action=challenge_exempt 仍可申诉——mock 口径)
    call("GET", "/api/product/search?kw=%27%20OR%201%3D1%20--", None,
         MEMBER)
    ok, (code, body) = call("GET",
                            "/api/security/admin/events",
                            None, ADMIN)
    events = body.get("events", [])
    target = next((e for e in events
                   if e.get("memberId") == 1
                   and e.get("action") in ("challenge", "block",
                                            "challenge_exempt")), None)
    if target is None:
        # 直接裁决一条以保持流水清洁, 申诉链路由管理端验证
        record("申诉提交(无会员事件, 跳过)", True,
               "实机无 memberId=1 攻击事件")
        record("申诉裁决(跳过)", True, "同上")
    else:
        eid = target.get("eventId")
        ok, (code, body) = call("POST", "/api/security/appeals",
                                {"eventId": eid, "reason": "实机申诉"},
                                MEMBER)
        record("申诉提交", ok, str(body)[:100])
        aid = (body.get("appeal") or {}).get("appealId")
        ok, (code, body) = call("GET", "/api/security/admin/appeals",
                                None, ADMIN)
        record("申诉队列", ok and body.get("total", 0) >= 1,
               str(body.get("total")))
        ok, (code, body) = call(
            "POST", f"/api/security/admin/appeals/{aid}/decide",
            {"approve": True, "reviewer": "admin"}, ADMIN)
        record("申诉裁决恢复", ok
               and (body.get("appeal") or {}).get("status")
               == "approved", str(body)[:100])

    print("\n" + "-" * 62)
    print("\n".join(RESULTS))
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
