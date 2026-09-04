"""50号P0 语音信值积分引擎 Docker 实机验收

运行方式:
    python verify_xiaozhu_p50_0_live.py [基址]

前置: 容器已运行(含 50号P0 代码, 镜像已重建)。

覆盖(50号计划 §七 P0, 真实容器):
    01 正常业务零影响(健康/35号面板/48号指令集含第17条)
    02 off 零影响(轮次零事件/my 视图可用)
    03 L1 实时轨(计分/风控状态/冻结恢复——容器内同进程)
    04 防刷封顶数学(容器内)
    05 端点鉴权与规则热更新(HTTP)
    06 交叉回归(45/46号面板)

每轮验收前清理 zhuxiang:voice50:* 残留, ×2 轮幂等验证。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

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


def clear_voice50() -> None:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:voice50:*"],
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


def container_engine_check(round_no: int) -> dict:
    """容器内同进程: L1 计分+封顶+冻结恢复全链"""
    script = (
        "import asyncio, json\n"
        "from services.xiaozhu_voice50_service import "
        "Voice50Service\n"
        "from repositories.voice50_repository import "
        "Voice50Repository\n"
        f"MEMBER = 6{round_no}01\n"
        "async def m():\n"
        "    svc = Voice50Service()\n"
        "    out = {}\n"
        # L1 计分(proxy 声纹半程)——base 从注册表动态读
        "    from services.xiaozhu_voice50_rules import "
        "VOICE_RULES\n"
        "    base = VOICE_RULES['voice_login']['base']\n"
        "    r = await svc.record_behavior(\n"
        f"        MEMBER, 'voice_login', "
        "voiceprint='proxy')\n"
        "    out['loginScore'] = r['finalScore']\n"
        "    out['loginMult'] = r['multipliers']"
        "['voiceprint']\n"
        "    out['expectLogin'] = round(base * 1.25, 2)\n"
        # ref 格式
        "    out['refOk'] = r['ref'].startswith("
        "'exp-voice50-')\n"
        # 防刷封顶(灌爆首日下限 30)
        "    for _ in range(12):\n"
        "        r2 = await svc.record_behavior(\n"
        f"            MEMBER, 'voice_login', "
        "voiceprint='real')\n"
        "    out['overflow'] = r2['overflowScore']\n"
        "    out['capped'] = r2['cappedScore']\n"
        # L2 pending
        "    r3 = await svc.record_behavior(\n"
        f"        MEMBER, 'voice_clear_intent', "
        "quality=0.9)\n"
        "    out['l2Pending'] = (\n"
        "        (await Voice50Repository().list_events("
        f"member_id=MEMBER))[-1]['status'])\n"
        # 池对账(动态: 事件 capped+overflow 合计)
        "    evs = await Voice50Repository().list_events("
        f"member_id=MEMBER)\n"
        "    expect_pool = round(sum(\n"
        "        float(e['cappedScore']) + float("
        "e['overflowScore']) for e in evs), 2)\n"
        "    v = await svc.my_view(MEMBER)\n"
        "    out['pool'] = v['poolBalance']\n"
        "    out['expectPool'] = expect_pool\n"
        # 冻结/恢复
        "    for _ in range(5):\n"
        "        r4 = await svc.record_behavior(\n"
        f"            6{round_no}02, 'voice_login', "
        "penalty=True)\n"
        "    out['froze'] = r4['frozen']\n"
        "    state = await svc.risk_state(6"
        f"{round_no}02)\n"
        "    out['penaltyTotal'] = state["
        "'l1PenaltyTotal']\n"
        "    rec = await svc.unfreeze(6"
        f"{round_no}02, note='live-verify')\n"
        "    out['unfroze'] = (rec['frozen'] is False)\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:150]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收\n{'=' * 62}")
    clear_voice50()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))
    ok, (code, body) = call("GET",
                            "/api/xiaozhu/commands")
    cmds = body.get("commands") or body or []
    record("48号指令集含第 17 条(语音积分)",
           code == 200
           and any(c.get("action") == "voice.score"
                   for c in cmds
                   if isinstance(c, dict)),
           str(code))

    print("\n[02 off 零影响(默认态)]")
    member = 600 + round_no
    h = {"X-Member-Id": str(member)}
    ok, (code, body) = call("POST", "/api/xiaozhu/sessions",
                            body={}, headers=h)
    sid = body.get("sessionId")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，看新品"}, headers=h)
    record("轮次照常(off 零影响)",
           code == 200 and body.get("success") is True,
           str(code))
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，我的语音积分"}, headers=h)
    record("第 17 指令 off 提示",
           body.get("turn", {}).get("intent")
           == "voice.score"
           and "未启用" in (body.get("reply") or ""),
           str(body.get("reply"))[:30])
    ok, (code, body) = call("GET", "/api/xiaozhu/voice50/my",
                            headers=h)
    record("my 视图可用(池 0)",
           code == 200
           and body.get("poolBalance") == 0
           and body.get("frozen") is False, str(code))

    print("\n[03 引擎全链(容器内同进程)]")
    r = container_engine_check(round_no)
    record("①L1 计分(proxy ×1.25)",
           abs(r.get("loginScore", 0)
               - r.get("expectLogin", -1)) < 0.01
           and r.get("loginMult") == 1.25,
           f"{r.get('loginScore')} vs "
           f"{r.get('expectLogin')}")
    record("②ref 格式(exp-voice50-*)",
           r.get("refOk") is True)
    record("③防刷封顶(溢出 ×0.1)",
           r.get("capped") == 0.0
           and (r.get("overflow") or 0) > 0,
           f"capped={r.get('capped')} "
           f"overflow={r.get('overflow')}")
    record("④L2 事件 pending(T+1)",
           r.get("l2Pending") == "pending",
           str(r.get("l2Pending")))
    record("⑤池=事件封顶+溢出合计",
           abs(r.get("pool", -1)
               - r.get("expectPool", -2)) < 0.05,
           f"{r.get('pool')} vs "
           f"{r.get('expectPool')}")
    record("⑥L1 降级(-25 冻结)",
           r.get("froze") is True
           and r.get("penaltyTotal") == 25.0,
           str(r.get("penaltyTotal")))
    record("⑦人工恢复", r.get("unfroze") is True)

    print("\n[04 端点鉴权与热更新(HTTP)]")
    ok, (code, body) = call(
        "GET", "/api/xiaozhu/voice50/rules", headers=ADMIN)
    record("GET rules(admin, 14 行为)",
           code == 200 and body.get("total") == 14,
           str(code))
    ok, (code, _) = call("GET",
                         "/api/xiaozhu/voice50/rules")
    record("rules 缺 Role 403", code == 403, str(code))
    ok, (code, body) = call(
        "PUT", "/api/xiaozhu/voice50/rules/voice_login",
        body={"base": 2.5}, headers=ADMIN)
    record("PUT 热更新(base→2.5)",
           code == 200
           and (body.get("changes") or {}).get("base",
                                               {}).get("to")
           == 2.5, str(code))
    ok, (code, body) = call(
        "GET", "/api/xiaozhu/voice50/rules", headers=ADMIN)
    record("热更新留痕(recentUpdates)",
           code == 200
           and (body.get("recentUpdates") or [{}])[
               -1].get("behavior") == "voice_login")
    ok, (code, _) = call(
        "PUT", "/api/xiaozhu/voice50/rules/voice_login",
        body={"base": 2.0}, headers=ADMIN)   # 复原
    record("热更新复原(base→2.0)", code == 200)
    ok, (code, _) = call(
        "POST", "/api/xiaozhu/voice50/unfreeze",
        body={"memberId": 999}, headers=ADMIN,
        expect=(409,))
    record("unfreeze 未冻结 409", code == 409, str(code))
    ok, (code, _) = call("GET",
                         "/api/xiaozhu/voice50/my")
    record("my 缺头 401", code == 401, str(code))

    print("\n[05 交叉回归]")
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))
    ok, (code, _) = call(
        "GET", "/api/ai-gov/dashboard", headers=ADMIN)
    record("46号治理看板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))


def main():
    print("=" * 62)
    print("50号·P0 语音信值积分引擎 Docker 实机验收")
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
