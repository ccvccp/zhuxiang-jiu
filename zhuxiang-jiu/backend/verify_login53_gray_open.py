"""53号灰度开启验证(部署清单 §五——开启操作后验证)

运行方式(容器 LOGIN53_MODE=on 后):
    python verify_login53_gray_open.py

覆盖:
    ① 数据重造(seed 清库后): 注册会员+bio 凭证全链
    ② 态势感知(authLevel/intent/budget)
    ③ 编排端到端(silent 档+tokens+话术)
    ④ 语音唤醒登录(双因子+导览)
    ⑤ 驻留领取(幂等)
    ⑥ 门户钩子
    ⑦ 存量登录对照(零接管)
    ⑧ 编排失败优雅降级(凭证失败→failCounts 防御
       修复验证——Redis 态二次调用不再 dict 崩溃)
    ⑨ 首份指标快照+看板
"""
import hashlib
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.2:8000"
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name} — {detail}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}


def main() -> int:
    # ① 数据重造: 会员+bio 凭证
    phone = "13900005353"
    code, _ = call("POST", "/api/auth/register", {
        "phone": phone, "password": "Gray#53x",
        "nickname": "灰度验证"})
    record("① 会员注册(409=已注册幂等)",
           code in (200, 409), str(code))
    c, b = call("POST", "/api/entry/login", {
        "mode": "password", "phone": phone,
        "password": "Gray#53x"})
    mid = str(((b.get("data") or {}).get("memberId")))
    record("① 会员登录取 ID", bool(mid), mid)
    h = {"X-Member-Id": mid}
    dev = "dev-gray-verify"
    _, b1 = call("POST", "/api/entry/bio/enroll",
                 {"bioType": "face", "deviceId": dev}, headers=h)
    ch = (b1.get("data") or {}).get("enrollChallenge") or ""
    _, b2 = call("POST", "/api/entry/bio/bind", {
        "bioType": "face", "deviceId": dev,
        "enrollChallenge": ch,
        "publicKeyHash": "c" * 40,
        "credentialName": "gray-verify"}, headers=h)
    cid = (b2.get("data") or {}).get("credentialId") or ""
    record("① bio 凭证绑定", bool(cid), cid[-8:])

    # ② 态势感知
    c, b = call("POST", "/api/login53/prelogin/sense",
                {"fingerprint": "fp-gray-01",
                 "visitSource": "shopping"}, headers=h)
    s = b.get("sense") or {}
    record("② 态势感知(意图+预算)",
           c == 200 and s.get("intent") == "shopping"
           and (s.get("budget") or {}).get("remaining") == 1.0,
           f"{c} intent={s.get('intent')}")

    # ③ 编排端到端
    c, b = call("POST", "/api/login53/auth/orchestrate", {
        "channel": "passkey",
        "credential": {"credentialId": cid},
        "hour": 12}, headers=h)
    o = b.get("orchestration") or {}
    record("③ 编排端到端(silent+tokens+话术)",
           c == 200 and o.get("status") == "authenticated"
           and bool((o.get("tokens") or {}).get("accessToken"))
           and (o.get("script") or {}).get("key")
           == "passkey_silent",
           f"{c} {o.get('tier')}")

    # ④ 语音唤醒登录
    c, b = call("POST", "/api/login53/voice/wake-login", {
        "utterance": "小竹，我回来了", "hour": 12}, headers=h)
    w = b.get("wakeLogin") or {}
    record("④ 语音唤醒(登录+导览)",
           c == 200 and w.get("status") == "authenticated"
           and bool(w.get("briefing")),
           f"{c} {w.get('status')}")

    # ⑤ 驻留领取(幂等)
    c, b = call("POST", "/api/login53/retention/claim",
                {"greeting": "小竹你好"}, headers=h)
    record("⑤ 驻留领取", c == 200
           and (b.get("claim") or {}).get("status")
           in ("claimed", "already_claimed"),
           str(c))
    c, b = call("POST", "/api/login53/retention/claim",
                {}, headers=h)
    record("⑤ 驻留幂等", (b.get("claim") or {})
           .get("status") == "already_claimed",
           str((b.get("claim") or {}).get("status")))

    # ⑥ 门户钩子
    c, b = call("POST", "/api/login53/portal/hook", headers=h)
    record("⑥ 门户钩子", c == 200 and
           ((b.get("portalHook") or {}).get("hook") or {})
           .get("type") in ("value_demo", "todo_summary"),
           str(c))

    # ⑦ 存量登录对照(零接管)
    c, b = call("POST", "/api/entry/login", {
        "mode": "password", "phone": phone,
        "password": "Gray#53x"})
    record("⑦ 存量登录零接管",
           c == 200 and (b.get("data") or {}).get("status")
           in ("authenticated", "step_up_required"),
           f"{c} {(b.get('data') or {}).get('status')}")

    # ⑧ failCounts 防御修复验证(凭证失败两次——
    #    Redis 态第二次不崩: 返回 409 而非 500)
    for _ in range(2):
        call("POST", "/api/login53/auth/orchestrate", {
            "channel": "passkey",
            "credential": {
                "credentialId": "BIOnope0000"},
            "hour": 12}, headers=h)
    c, b = call("POST", "/api/login53/auth/orchestrate", {
        "channel": "passkey",
        "credential": {
            "credentialId": "BIOnope0000"},
        "hour": 12}, headers=h)
    record("⑧ 失败降级防御(failCounts 不崩)",
           c == 404 and "BIOnope0000" in str(b),
           f"{c} {str(b.get('error'))[:24]}")

    # ⑨ 首份指标快照+看板
    admin = {"X-Role": "admin"}
    c, b = call("POST", "/api/login53/metrics/compute",
                headers=admin)
    snap = b.get("snapshot") or {}
    record("⑨ 首份指标快照(六指标)",
           c == 200 and len(snap.get("metrics") or {}) == 6,
           f"{c} passed={snap.get('passedCount')}/6")
    c, b = call("GET", "/api/login53/dashboard",
                headers=admin)
    d = b.get("byChannel") or {}
    record("⑨ 看板(通道占比+快照绑定)",
           c == 200 and d.get("passkey", 0) >= 1
           and d.get("voice", 0) >= 1
           and (b.get("latestSnapshot") or {})
           .get("snapId"),
           f"{c} {d}")

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"灰度开启验证: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
