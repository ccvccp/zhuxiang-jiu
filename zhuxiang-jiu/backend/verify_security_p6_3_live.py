"""43号P6-3 enforce 就绪度自动化 Docker 实机验收

运行方式:
    python verify_security_p6_3_live.py [基址]

覆盖(计划 §四, 真实容器全链路):
    01 正常业务零影响
    02 缺 Role 403
    03 真实容器就绪度输出(五检查结构+actual 实测+三信号)
    04 制造积压(docker exec 注入 pending 事件) → holding +
       blockers 含"待裁决积压"
    05 裁决清积压(POST decide) → 该检查恢复 passed
    06 overall 与 blockers 一致性(holding 非空/恢复)
    07 铁律文案(note 只评估不切换 + enforceLevel 实况)
    08 业务回归
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
BACKLOG_EVENT_ID = 999900   # 验收专用事件 id(裁决后不残留)


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
        with urllib.request.urlopen(req, timeout=120) as resp:
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


def readiness() -> dict:
    ok, (code, body) = call(
        "GET", "/api/security/admin/enforce/readiness", headers=ADMIN)
    return body if code == 200 else {"_code": code}


def inject_pending_event() -> str:
    """docker exec 注入 1 条 pending 事件(返回 'ok' 或错误)"""
    return docker_exec(
        "import asyncio\n"
        "from core.helpers import ts\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        f"    await Security43Repository().save_event({{\n"
        f"        'eventId': {BACKLOG_EVENT_ID}, 'ip': '203.0.113.99',\n"
        "        'memberId': 0, 'method': 'GET',\n"
        "        'path': '/verify-p6-3', 'query': '', 'ua': '',\n"
        "        'action': 'challenge', 'score': None,\n"
        "        'factors': [{'name': 'payload_signature',\n"
        "                     'score': 50}],\n"
        "        'enforced': False, 'verdict': 'pending',\n"
        "        'eventFed': False, 'createdAt': ts()})\n"
        "    print('ok')\n"
        "asyncio.run(m())\n")


def main():
    print("=" * 62)
    print("43号·P6-3 enforce 就绪度自动化 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 鉴权]")
    ok, (code, _) = call(
        "GET", "/api/security/admin/enforce/readiness", expect=(403,))
    record("缺Role403", code == 403, str(code))

    print("\n[03 真实容器就绪度输出]")
    r = readiness()
    checks = r.get("checks") or []
    ids = [c.get("id") for c in checks]
    record("五检查结构", ids == ["observe_days",
                                  "false_positive_rate",
                                  "pending_backlog",
                                  "appeal_channel",
                                  "health_whitelist"], str(ids))
    record("actual实测在位", all(c.get("actual") for c in checks),
           str([{c['id']: c['actual']} for c in checks])[:160])
    sig = r.get("signals") or {}
    record("三信号在位", all(k in sig for k in (
        "d5", "threatintel", "geo", "abuseipdb")),
        str(list(sig)))

    print("\n[04 制造积压 → holding]")
    out = inject_pending_event()
    record("事件注入", "ok" in out, out[:80])
    r = readiness()
    by_id = {c.get("id"): c for c in r.get("checks") or []}
    backlog = by_id.get("pending_backlog") or {}
    record("积压检查not passed", backlog.get("passed") is False
           and "1件" in str(backlog.get("actual")), str(backlog))
    record("overall=holding", r.get("overall") == "holding",
           str(r.get("overall")))
    record("blockers含积压", any(
        "待裁决积压" in b for b in r.get("blockers") or []),
        str(r.get("blockers")))

    print("\n[05 裁决清积压 → 恢复]")
    ok, (code, body) = call(
        "POST",
        f"/api/security/admin/events/{BACKLOG_EVENT_ID}/decide",
        body={"confirm": True, "reviewer": "p6_3_verify",
              "note": "实机验收裁决"}, headers=ADMIN)
    record("裁决调用", code == 200, f"{code}/{str(body)[:80]}")
    r = readiness()
    by_id = {c.get("id"): c for c in r.get("checks") or []}
    backlog = by_id.get("pending_backlog") or {}
    record("积压检查恢复passed", backlog.get("passed") is True
           and backlog.get("actual") == "0件", str(backlog))

    print("\n[06 overall/blockers 一致性]")
    record("恢复后blockers无积压", not any(
        "待裁决积压" in b for b in r.get("blockers") or []),
        str(r.get("blockers")))
    record("一致性", (r.get("overall") == "ready")
           == (len(r.get("blockers") or []) == 0),
           f"overall={r.get('overall')} "
           f"blockers={r.get('blockers')}")

    print("\n[07 铁律与灰度态]")
    record("note只评估不切换",
           "只评估不切换" in str(r.get("note")),
           str(r.get("note"))[:100])
    record("enforceLevel实况", r.get("enforceLevel") in (
        "observe", "shadow", "enforce"), str(r.get("enforceLevel")))
    record("checkedAt在位", bool(r.get("checkedAt")),
           str(r.get("checkedAt")))

    print("\n[08 业务回归]")
    ok, (code, _) = call("GET", "/api/product/list")
    record("业务正常", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
