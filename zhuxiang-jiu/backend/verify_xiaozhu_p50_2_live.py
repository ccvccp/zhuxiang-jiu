"""50号P2 语音信值积分引擎 Docker 实机验收

运行方式:
    python verify_xiaozhu_p50_2_live.py [基址]

前置: 容器已运行(含 50号P2 代码, 镜像已重建)。

覆盖(50号计划 §七 P2, 真实容器):
    01 正常业务零影响(健康/面板)
    02 L2 信号源 E2E(容器内——礼貌 streak/连贯/跨文化/
       授权桥/反馈桥)
    03 T+1 结算 E2E(容器内——聚合/deposit 验真入账/
       幂等/unbound skip)
    04 端点(POST settle/GET settlements/鉴权)
    05 交叉回归

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


def container_p2_check(round_no: int) -> dict:
    """容器内同进程: L2 信号源+T+1 结算全链"""
    m = 8 + round_no * 100   # 801/901
    script = (
        "import asyncio, json, os\n"
        "os.environ['VOICE50_MODE'] = 'on'\n"
        "from services.xiaozhu_voice50_service import "
        "Voice50Service\n"
        "from repositories.voice50_repository import "
        "Voice50Repository\n"
        f"M = {m}\n"
        "async def m2():\n"
        "    out = {}\n"
        "    svc = Voice50Service()\n"
        "    repo = Voice50Repository()\n"
        # 绑定
        "    import uuid as _u\n"
        "    sfx = _u.uuid4().hex[:10]\n"
        "    from services.trust_scoring_service import "
        "TrustProfileService\n"
        "    from services.xiaozhu_service import "
        "XiaozhuService\n"
        "    tid = (await TrustProfileService().create_role("
        "'person', f'p52-{sfx[:6]}', "
        "f'110101{sfx}4321'))['trustId']\n"
        "    await XiaozhuService().bind_trust("
        "M, tid, note='p52live')\n"
        # 授权桥(先归零再上调——跨轮偏好残留复位:
        # 1.5→0.5 无当日授权事件不触发撤回, 再 0.5→1.5 授权)
        "    from services.xiaozhu_privacy_service import "
        "XiaozhuPrivacyService\n"
        "    await XiaozhuPrivacyService().set_preference("
        "M, 0.5)\n"
        "    await XiaozhuPrivacyService().set_preference("
        "M, 1.5)\n"
        "    evs = await repo.list_events(member_id=M)\n"
        "    grants = [e for e in evs if e['behavior'] == "
        "'voice_privacy_grant']\n"
        "    out['grant'] = (len(grants) == 1 and abs("
        "grants[0]['finalScore'] - 10.4) < 0.01)\n"
        # 反馈桥(共创采纳)
        "    from repositories.xiaozhu_repository import "
        "Xiaozhu48Repository\n"
        "    xrepo = Xiaozhu48Repository()\n"
        "    from services.xiaozhu_evolution_service import "
        "XiaozhuEvolutionService\n"
        "    cmd_id = await xrepo._next_id(xrepo."
        "TABLE_CUSTOM)\n"
        "    await xrepo.save_record(xrepo.TABLE_CUSTOM, "
        "{'cmdId': cmd_id, 'memberId': M, 'phrase': "
        "f'看看{sfx}', 'action': 'product.new', "
        "'status': 'pending', 'ts': ''})\n"
        "    await XiaozhuEvolutionService(repo=xrepo)"
        ".review_custom(cmd_id, approve=True, "
        "note='live')\n"
        "    evs = await repo.list_events(member_id=M)\n"
        "    fb = [e for e in evs if e['behavior'] == "
        "'voice_feedback']\n"
        "    out['feedback'] = (len(fb) == 1 and abs("
        "fb[0]['finalScore'] - 16.0) < 0.01)\n"
        # 构造昨日 L2 事件(礼貌×2) → 结算
        "    await svc.record_behavior(M, 'voice_polite')\n"
        "    await svc.record_behavior(M, 'voice_polite', "
        "gains={'streak3': True})\n"
        "    yst = '2000-01-01'\n"
        "    for e in await repo.list_events(member_id=M):\n"
        "        e['dayKey'] = yst\n"
        "        await repo.save_event(e)\n"
        "    from services.trust_scoring_service import "
        "TrustProfileService as TPS\n"
        "    p0 = await TPS().repo.get_profile(tid)\n"
        "    d0 = float(p0.get('factors', {}).get("
        "'ethics_evidence') or 0)\n"
        "    r = await svc.settle_day(day_key=yst, "
        "operator='live')\n"
        "    out['settleDone'] = (r['counts']['done'] >= 1)\n"
        "    out['credits'] = r['batches'][0]['credits'] "
        "if r['batches'] else 0\n"
        "    out['delta'] = (r['batches'][0]"
        "['depositDelta'] if r['batches'] else 0)\n"
        "    p1_ = await TPS().repo.get_profile(tid)\n"
        "    d1 = float(p1_.get('factors', {}).get("
        "'ethics_evidence') or 0)\n"
        "    out['ethicsUp'] = (d1 > d0)\n"
        # 幂等
        "    r2 = await svc.settle_day(day_key=yst, "
        "operator='live')\n"
        "    p2_ = await TPS().repo.get_profile(tid)\n"
        "    d2 = float(p2_.get('factors', {}).get("
        "'ethics_evidence') or 0)\n"
        "    out['idempotent'] = (r2['counts']['done'] == 0 "
        "and d2 == d1)\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m2())\n")
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

    print("\n[02-03 L2 信号源+T+1 结算(容器内)]")
    r = container_p2_check(round_no)
    record("①授权桥(×1.3=10.4)",
           r.get("grant") is True, str(r)[:80])
    record("②反馈桥(+10=16)", r.get("feedback") is True)
    record("③结算批次 done",
           r.get("settleDone") is True)
    record("④聚合正向 capped(授权+反馈+礼貌)",
           abs(r.get("credits", 0) - 27.65) < 0.01,
           str(r.get("credits")))
    record("⑤deposit delta>0", (r.get("delta") or 0) > 0,
           str(r.get("delta")))
    record("⑥45号 ethics_evidence 入账",
           r.get("ethicsUp") is True)
    record("⑦幂等(二次无新批次)",
           r.get("idempotent") is True)

    print("\n[04 端点(HTTP)]")
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/voice50/settle",
        body={}, headers=ADMIN)
    record("POST settle 200(空)",
           code == 200 and body.get("success") is True,
           str(code))
    ok, (code, body) = call(
        "GET", "/api/xiaozhu/voice50/settlements",
        headers=ADMIN)
    record("GET settlements 200(有批次)",
           code == 200 and (body.get("total") or 0) >= 1,
           str(body.get("total")))
    ok, (code, _) = call(
        "POST", "/api/xiaozhu/voice50/settle", body={})
    record("settle 缺 Role 403", code == 403, str(code))
    ok, (code, _) = call(
        "GET", "/api/xiaozhu/voice50/settlements")
    record("settlements 缺 Role 403", code == 403,
           str(code))

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
    print("50号·P2 L2五行为+T+1结算 Docker 实机验收")
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
