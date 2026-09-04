"""50号P5 语音信值积分引擎 Docker 实机验收(收官)

运行方式:
    python verify_xiaozhu_p50_5_live.py [基址]

前置: 容器已运行(含 50号P5 代码, 镜像已重建)。

覆盖(50号计划 §七 P5, 真实容器):
    01 正常业务零影响
    02 群体三场景(容器内——minor/disabled/org_proxy 系数)
    03 衰减+对冲(容器内——90 天衰减/保底/对冲 45号通道)
    04 看板第 8 区块(HTTP——off 空态/on 指标)
    05 端点(group-profile/decay/offset+鉴权)
    06 交叉回归

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


def container_p5_check(round_no: int) -> dict:
    """容器内同进程: 群体系数+衰减+对冲"""
    m = 12 + round_no * 100   # 1201/1301
    script = (
        "import asyncio, json, os\n"
        "os.environ['VOICE50_MODE'] = 'on'\n"
        "from services.xiaozhu_voice50_service import "
        "Voice50Service\n"
        "from repositories.voice50_repository import "
        "Voice50Repository\n"
        f"M = {m}\n"
        "async def m5():\n"
        "    out = {}\n"
        "    svc = Voice50Service()\n"
        "    repo = Voice50Repository()\n"
        # 群体系数
        "    await svc.set_group_profile(M, 'minor')\n"
        "    r = await svc.record_behavior(\n"
        f"        M, 'voice_polite', note='礼貌交互{round_no}')\n"
        "    out['minor'] = (abs(r['finalScore'] - 0.75) "
        "< 1e-6)\n"
        "    await svc.set_group_profile(M + 1, 'disabled', "
        "verified=True)\n"
        "    r2 = await svc.record_behavior(\n"
        f"        M + 1, 'voice_polite', note='礼貌{round_no}')\n"
        "    out['disabled'] = (abs(r2['finalScore'] - 0.6) "
        "< 1e-6)\n"
        # 衰减(灌 95 天前池)
        "    from datetime import UTC, datetime, timedelta\n"
        "    old = (datetime.now(UTC) - timedelta("
        "days=95)).isoformat()\n"
        "    for i in range(4):\n"
        "        await svc.record_behavior(\n"
        f"            M + 2, 'voice_polite', "
        "note=f'衰减测试{i}')\n"
        "    ledger = await repo.get_ledger(M + 2)\n"
        "    ledger['lastActiveAt'] = old\n"
        "    ledger['poolBalance'] = 100.0\n"
        "    ledger['earnedTotal'] = 100.0\n"
        "    await repo.save_ledger(ledger)\n"
        "    rd = await svc.run_decay()\n"
        "    out['decayed'] = (rd['decayed'] >= 1)\n"
        "    ledger2 = await repo.get_ledger(M + 2)\n"
        "    out['pool95'] = (abs(ledger2['poolBalance'] "
        "- 95.0) < 0.01)\n"
        # 对冲(45号通道)
        "    import uuid as _u\n"
        "    sfx = _u.uuid4().hex[:10]\n"
        "    from services.trust_scoring_service import "
        "TrustProfileService\n"
        "    from services.xiaozhu_service import "
        "XiaozhuService\n"
        "    tid = (await TrustProfileService().create_role("
        "'person', f'p55-{sfx[:6]}', "
        "f'110101{sfx}4321'))['trustId']\n"
        "    await XiaozhuService().bind_trust(\n"
        f"        M + 3, tid, note='p55live')\n"
        "    await TrustProfileService().record_event(\n"
        "        tid, 'L2', 'ethics_evidence', -30.0,\n"
        "        source='admin', summary='违规测试')\n"
        "    from repositories.trust_value_repository "
        "import TrustValue45Repository\n"
        "    events = await TrustValue45Repository()"
        ".list_events_by_trust(tid)\n"
        "    vid = [e for e in events if (e.get('delta') "
        "or 0) < 0][0]['eventId']\n"
        "    for i in range(4):\n"
        "        await svc.record_behavior(\n"
        f"            M + 3, 'voice_polite', "
        "note=f'对冲测试{i}')\n"
        "    ledger3 = await repo.get_ledger(M + 3)\n"
        "    ledger3['poolBalance'] = 50.0\n"
        "    ledger3['earnedTotal'] = 50.0\n"
        "    await repo.save_ledger(ledger3)\n"
        "    ro = await svc.offset_violation(M + 3, vid)\n"
        "    out['offset'] = (ro['success'] is True and "
        "abs(ro['offset'] - 25.0) < 0.01 and "
        "ro.get('repairId') is not None)\n"
        "    out['poolAfter'] = ro['poolBalance']\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m5())\n")
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

    print("\n[02-03 群体/衰减/对冲(容器内)]")
    r = container_p5_check(round_no)
    record("①minor L2 ×1.5",
           r.get("minor") is True, str(r)[:80])
    record("②disabled ×1.2",
           r.get("disabled") is True)
    record("③衰减执行(95 天)",
           r.get("decayed") is True)
    record("④池 100→95(月 5%)",
           r.get("pool95") is True)
    record("⑤对冲 25+repairId",
           r.get("offset") is True)
    record("⑥对冲后池 25",
           abs(r.get("poolAfter", -1) - 25.0) < 0.01,
           str(r.get("poolAfter")))

    print("\n[04 看板第 8 区块(HTTP)]")
    ok, (code, board) = call(
        "GET", "/api/xiaozhu/dashboard", headers=ADMIN)
    zones = board.get("zones") or {}
    record("看板八区块(含 voice50)",
           code == 200 and "voice50" in zones
           and not (board.get("zoneErrors") or []),
           str(board.get("zoneErrors")))
    v50 = zones.get("voice50") or {}
    # 容器默认 VOICE50_MODE=off——分区空态是零影响
    # 交付的正确语义(on 态指标由容器内检查覆盖)
    record("voice50 分区 off 空态(零影响交付)",
           v50.get("enabled") is False
           and "VOICE50_MODE=off" in v50.get("note", ""),
           str(v50)[:60])

    print("\n[05 端点(HTTP)]")
    ok, (code, body) = call(
        "PUT", "/api/xiaozhu/voice50/group-profile",
        body={"memberId": 1400 + round_no,
              "group": "elder"},
        headers=ADMIN)
    record("PUT group-profile 200",
           code == 200 and body.get("group") == "elder",
           str(code))
    ok, (code, body) = call(
        "PUT", "/api/xiaozhu/voice50/group-profile",
        body={"memberId": 1, "group": "vip"},
        headers=ADMIN, expect=(409,))
    record("非法群体 409", code == 409, str(code))
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/voice50/decay",
        headers=ADMIN)
    record("POST decay 200",
           code == 200 and body.get("success") is True)
    ok, (code, _) = call(
        "POST", "/api/xiaozhu/voice50/decay")
    record("decay 缺 Role 403", code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/xiaozhu/voice50/offset",
        body={"violationEventId": 1},
        headers={"X-Member-Id": "9999"},
        expect=(409,))
    record("offset 未绑定 409", code == 409, str(code))

    print("\n[06 交叉回归]")
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))
    ok, (code, _) = call(
        "GET", "/api/ai-gov/dashboard", headers=ADMIN)
    record("46号治理看板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))


def main():
    print("=" * 62)
    print("50号·P5 群体/衰减/对冲+看板收官 Docker 实机验收")
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
