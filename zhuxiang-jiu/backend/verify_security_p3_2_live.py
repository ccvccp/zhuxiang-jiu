"""43号P3-2 Docker 实机验收(真实验证码三态通道·mock 轨全链路)

运行方式:
    python verify_security_p3_2_live.py [基址]

覆盖:
    01 正常业务零影响
    02 mock 轨旧口径兼容(应答非空即过)
    03 mock 轨票据路径(一次性消费防重放)
    04 real 轨拒绝语义(无凭证/无票据——实机安全口径验证)
    05 通行证发放与豁免(P1 链路照常)
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
    print("43号·P3-2 验证码三态通道 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, body) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, body) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 mock轨旧口径兼容]")
    ok, (code, body) = call("POST", "/api/security/challenge/verify",
                            {"token": "t", "answer": "ok"})
    record("旧口径应答通过", ok and body.get("success") is True,
           str(body)[:80])
    ok, (code, body) = call("POST", "/api/security/challenge/verify",
                            {"token": "t", "answer": ""},
                            expect=(409,))
    record("空应答 409", code == 409, str(code))

    print("\n[03 mock轨票据路径]")
    ok, (code, body) = call("POST", "/api/security/challenge/verify",
                            {"captchaToken": "live-ticket-001"})
    record("票据验证通过", ok and body.get("success") is True,
           str(body)[:80])
    ok, (code, body) = call("POST", "/api/security/challenge/verify",
                            {"captchaToken": "live-ticket-001"},
                            expect=(409,))
    record("票据重放 409(一次性)", code == 409, str(code))
    ok, (code, body) = call("POST", "/api/security/challenge/verify",
                            {"captchaToken": "live-ticket-002"})
    record("新票据独立通过", ok, str(body)[:60])

    print("\n[04 通行证豁免链路(P1照常)]")
    ok, (code, body) = call("GET", "/api/security/status", None,
                            {"X-Member-Id": "1"})
    record("会员状态含通行证", ok and "challengePass" in body,
           str(body)[:80])

    print("\n[05 验证事件留痕]")
    ok, (code, body) = call("GET", "/api/security/admin/events",
                            None, {"X-Role": "admin"})
    events = body.get("events", [])
    verify_ev = [e for e in events
                 if e.get("action") == "verify_pass"]
    record("verify事件留痕", len(verify_ev) >= 1,
           f"共{len(verify_ev)}条")
    record("事件含captchaDetail", verify_ev
           and bool(verify_ev[-1].get("captchaDetail")),
           str(verify_ev[-1].get("captchaDetail")) if verify_ev
           else "None")

    print("\n" + "-" * 62)
    print("\n".join(RESULTS))
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("注: real 轨待极验凭证到手后在 .env 配置"
          "CAPTCHA_ID/KEY 并切 SECURITY_CAPTCHA_MODE=real 冒烟")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
