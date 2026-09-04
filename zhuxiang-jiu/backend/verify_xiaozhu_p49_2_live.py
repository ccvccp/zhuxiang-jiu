"""49号P2 隐私预算 Docker 实机验收

运行方式:
    python verify_xiaozhu_p49_2_live.py [基址]

前置: 容器已运行(含 49号P2 代码, 镜像已重建)。

覆盖(49号计划 §六 P2, 真实容器):
    01 正常业务零影响
    02 预算视图/偏好端点 E2E
    03 语音指令"我的隐私预算"
    04 网关管道扣减 E2E(高敏挑战扣减+超限 fallback)
    05 鉴权与业务回归

每轮验收前清理 zhuxiang:voice48:* 残留, ×2 轮幂等验证。
"""
import json
import subprocess
import sys
import urllib.error
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


def clear_voice48() -> None:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:voice48:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def call(method, path, body=None, headers=None,
         expect=(200,)):
    data = json.dumps(body).encode() if body is not None \
        else None
    req = urllib.request.Request(BASE + path, data=data,
                                  method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            code, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def container_budget_flow(member: int) -> dict:
    """容器内: 挑战扣减→超限 fallback(全链同进程)"""
    script = (
        "import asyncio, json\n"
        "from services.xiaozhu_fc_gateway import "
        "XiaozhuFcGateway\n"
        "from services.xiaozhu_privacy_service import "
        "XiaozhuPrivacyService\n"
        f"MEMBER = {member}\n"
        "async def m():\n"
        "    out = {}\n"
        "    gw = XiaozhuFcGateway()\n"
        "    session = {'sessionId': 1, 'memberId': MEMBER}\n"
        # ① 高敏挑战 → 扣减 0.08
        "    r = await gw.call_tool(session, "
        "'trust.convert', {'creditPoints': 100})\n"
        "    out['challenge'] = r.get('confirmRequired') "
        "is True\n"
        "    v = await XiaozhuPrivacyService().budget_view("
        "MEMBER)\n"
        "    out['afterChallenge'] = v['usedToday']\n"
        "    out['remaining'] = v['remaining']\n"
        # ② 超限: 直写顶满(至限额-0.03, 使高敏 0.08 超限)
        "    rec = await gw.repo.get_privacy_budget(MEMBER)\n"
        "    limit = float(rec['dailyBudget']) * float("
        "rec['preference'])\n"
        "    rec['usedToday'] = round(limit - 0.03, 2)\n"
        "    await gw.repo.save_privacy_budget(rec)\n"
        "    r2 = await gw.call_tool(session, "
        "'trust.convert', {'creditPoints': 50})\n"
        "    out['fallback'] = r2.get('fallback') is True\n"
        "    out['budgetMsg'] = '隐私预算不足' in ("
        "r2.get('safeMessage') or '')\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:120]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收\n{'=' * 62}")
    clear_voice48()
    member = 500
    h = {"X-Member-Id": str(member)}

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 预算视图/偏好端点]")
    ok, (code, body) = call(
        "GET", "/api/xiaozhu/privacy/budget", headers=h)
    record("GET budget 200(默认 1.0)",
           code == 200
           and body.get("dailyBudget") == 1.0
           and body.get("remaining") == 1.0,
           str(body.get("dailyBudget")))
    record("视图含均等声明",
           "信值等级" in body.get("note", ""))
    ok, (code, body) = call(
        "PUT", "/api/xiaozhu/privacy/preferences",
        body={"preference": 1.5}, headers=h)
    record("PUT preference 200(限额 1.5)",
           code == 200
           and body.get("effectiveLimit") == 1.5,
           str(body.get("effectiveLimit")))
    ok, (code, _) = call(
        "PUT", "/api/xiaozhu/privacy/preferences",
        body={"preference": 5}, headers=h,
        expect=(409,))
    record("偏好越界 409", code == 409, str(code))
    ok, (code, _) = call(
        "GET", "/api/xiaozhu/privacy/budget")
    record("budget 缺 Member 401", code == 401, str(code))

    print("\n[03 语音指令]")
    ok, (code, body) = call("POST", "/api/xiaozhu/sessions",
                            body={}, headers=h)
    sid = body.get("sessionId")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，我的隐私预算"}, headers=h)
    record("语音指令直达(privacy.budget)",
           (body.get("turn") or {}).get("intent")
           == "privacy.budget",
           str((body.get("turn") or {}).get("intent")))
    card = body.get("card") or {}
    record("卡片含偏好/限额",
           card.get("preference") == 1.5
           and card.get("effectiveLimit") == 1.5,
           str(card.get("preference")))
    record("播报含余额",
           "剩余 1.5" in body.get("reply", "")
           or "1.5" in body.get("reply", ""),
           body.get("reply", "")[:50])

    print("\n[04 网关管道扣减 E2E]")
    r = container_budget_flow(member)
    record("①高敏挑战扣减 0.08",
           r.get("afterChallenge") == 0.08,
           str(r.get("afterChallenge")))
    record("①余额 1.42(限额 1.5)",
           r.get("remaining") == 1.42,
           str(r.get("remaining")))
    record("②超限 fallback",
           r.get("fallback") is True)
    record("②预算话术透传(剩余/需求)",
           r.get("budgetMsg") is True)
    # 只读零成本验证(语音指令已用——再次调用不扣减)
    ok, (code, body) = call(
        "GET", "/api/xiaozhu/privacy/budget", headers=h)
    # 容器流已顶满(限额-0.03); 语音指令只读不扣 → 不变
    record("只读零成本不扣减",
           body.get("remaining") == 0.03,
           str(body.get("remaining")))

    print("\n[05 鉴权与业务回归]")
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))


def main():
    print("=" * 62)
    print("49号·P2 隐私预算 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)
    for r in (1, 2):
        run_round(r)
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
