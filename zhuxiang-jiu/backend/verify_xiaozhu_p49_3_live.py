"""49号P3 可解释性绑定 Docker 实机验收

运行方式:
    python verify_xiaozhu_p49_3_live.py [基址]

前置: 容器已运行(含 49号P3 代码, 镜像已重建)。

覆盖(49号计划 §六 P3, 真实容器):
    01 正常业务零影响
    02 修复全流 ref E2E(挑战→语音→屏幕→核销→ref+
       会话留痕)
    03 "打开修复说明"指令(ref 落地卡片+45号归因全文)
    04 兑换 confirm 响应 ref 透传(HTTP)
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


def container_repair_flow(member: int, round_no: int
                          ) -> dict:
    """容器内同进程: 修复全流(挑战→语音→屏幕→ref
    →会话留痕→打开修复说明)"""
    script = (
        "import asyncio, json\n"
        "from services.xiaozhu_fc_gateway import "
        "XiaozhuFcGateway\n"
        "from services.xiaozhu_service import "
        "XiaozhuService\n"
        "from services.xiaozhu_executor import "
        "get_executor\n"
        "from services.trust_scoring_service import "
        "TrustProfileService\n"
        "from repositories.trust_value_repository "
        "import TrustValue45Repository\n"
        f"MEMBER = {member}\n"
        f"R = {round_no}\n"
        "async def m():\n"
        "    out = {}\n"
        # 建档+灌违规(轮次差异化 delta 防幂等)
        "    import uuid as _u\n"
        "    suffix = _u.uuid4().hex[:10]\n"
        "    t = TrustProfileService()\n"
        "    tid = (await t.create_role("
        "'person', f'p3live-{suffix[:6]}', "
        "f'110101{suffix}4321'))['trustId']\n"
        f"    await t.record_event(tid, 'L2', "
        f"'ethics_evidence', {-30.0 - round_no}, "
        "source='admin', summary='违规测试')\n"
        "    events = await TrustValue45Repository()"
        ".list_events_by_trust(tid)\n"
        "    vid = [e for e in events if (e.get('delta') "
        "or 0) < 0][0]['eventId']\n"
        "    svc = XiaozhuService()\n"
        "    await svc.bind_trust(MEMBER, tid, "
        "note='p3live')\n"
        "    sid = (await svc.open_session(MEMBER))"
        "['sessionId']\n"
        # 修复全流(网关挑战→语音→屏幕核销)
        "    gw = XiaozhuFcGateway()\n"
        "    session = await svc._require_open(sid)\n"
        "    r = await gw.call_tool(session, "
        "'repair.execute', "
        "{'violationEventId': vid, 'repairs': "
        "[{'kind': 'community_service', 'value': 80, "
        "'evidence': '社区公益服务八小时' + suffix}]})\n"
        "    token = r.get('confirmToken')\n"
        "    get_executor().mark_voice_confirmation("
        f"MEMBER, '小竹，确认执行修复')\n"
        "    rc = await svc.confirm_action(token, "
        "get_executor()._tokens[token]['code'])\n"
        "    out['ref'] = rc.get('explainabilityRef') "
        "or ''\n"
        "    out['refOk'] = out['ref'].startswith("
        "'exp-repair.execute-')\n"
        "    out['broadcast'] = '打开修复说明' in ("
        "rc.get('reply') or '')\n"
        # 会话留痕 + 打开修复说明
        "    s = await svc.repo.get_session(sid)\n"
        "    out['lastRef'] = s.get('lastRef') or ''\n"
        "    r2 = await svc.handle_text(sid, "
        "'小竹，打开修复说明')\n"
        "    card = r2.get('card') or {}\n"
        "    out['intent'] = (r2.get('turn') or {}).get("
        "'intent')\n"
        "    out['cardRef'] = card.get('ref') or ''\n"
        "    out['mode'] = card.get('mode') or ''\n"
        "    out['reportHasConst'] = '禁止黑箱' in ("
        "card.get('report') or '')\n"
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
    clear_voice48()
    member = 600
    h = {"X-Member-Id": str(member)}

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 修复全流 ref E2E(容器内)]")
    r = container_repair_flow(member, round_no)
    record("①核销响应含 ref",
           r.get("refOk") is True, str(r.get("ref"))[:36])
    record("②归因播报指引",
           r.get("broadcast") is True)
    record("③会话 lastRef 留痕",
           (r.get("lastRef") or "")
           .startswith("exp-repair.execute-"),
           str(r.get("lastRef"))[:30])
    record("④指令直达(explanation.report)",
           r.get("intent") == "explanation.report",
           str(r.get("intent")))
    record("⑤归因卡片 ref 落地",
           r.get("cardRef") == r.get("ref"),
           str(r.get("cardRef"))[:30])
    record("⑥45号源桥接(mode)",
           str(r.get("mode", "")).startswith("trust45+"),
           str(r.get("mode")))
    record("⑦归因全文含宪法声明",
           r.get("reportHasConst") is True)

    print("\n[03 兑换 ref E2E(HTTP 挑战+容器内核销)]")
    # HTTP 挑战(验"未落笔不绑 ref"); 兑换全链核销在
    # 容器内同进程(令牌进程态——docker exec 读不到
    # uvicorn 进程内令牌, 48号 P2 实机同款口径)
    suffix = uuid.uuid4().hex[:8]
    ok, (code, body) = call("POST", "/api/trust/roles",
                           body={"role": "person",
                                 "name": f"p3http-{suffix[:6]}",
                                 "idNumber":
                                 f"110101{suffix}4321"})
    tid = body.get("trustId")
    call("POST", "/api/xiaozhu/bindings",
         body={"trustId": tid}, headers=h)
    ok, (code, body) = call("POST", "/api/xiaozhu/sessions",
                            body={}, headers=h)
    sid = body.get("sessionId")
    credit = 100 + 10 * round_no
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": f"小竹，把{credit}信用分换成信值"},
        headers=h)
    record("HTTP 挑战回包不含 ref(未落笔)",
           body.get("confirmRequired") is True
           and "explainabilityRef" not in body)
    # 容器内兑换全链(挑战→语音→核销→ref)
    script = (
        "import asyncio, json\n"
        "from services.xiaozhu_fc_gateway import "
        "XiaozhuFcGateway\n"
        "from services.xiaozhu_service import "
        "XiaozhuService\n"
        "from services.xiaozhu_executor import "
        "get_executor\n"
        "from repositories.credit_repository import "
        "CreditRepository\n"
        f"MEMBER = {member}\n"
        f"CREDIT = {credit}\n"
        "async def m():\n"
        "    repo = CreditRepository()\n"
        "    a = await repo.get_or_create_score(MEMBER)\n"
        "    a['bambooScore'] = 5000.0\n"
        "    await repo.save_score(a)\n"
        "    svc = XiaozhuService()\n"
        "    gw = XiaozhuFcGateway()\n"
        "    session = {'sessionId': 1, 'memberId': MEMBER}\n"
        "    r = await gw.call_tool(session, "
        "'trust.convert', "
        "{'creditPoints': CREDIT})\n"
        "    token = r.get('confirmToken')\n"
        "    code = get_executor()._tokens[token]"
        "['code']\n"
        "    rc = await svc.confirm_action(token, code)\n"
        "    print(json.dumps({'ref': rc.get("
        "'explainabilityRef') or '', 'ok': True}))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    try:
        rc = json.loads((out.stdout or "").strip()
                        .splitlines()[-1])
    except (ValueError, IndexError):
        rc = {"ok": False}
    record("兑换核销响应 ref 透传",
           rc.get("ok") is True
           and (rc.get("ref") or "")
           .startswith("exp-trust.convert-"),
           str(rc)[:50])

    print("\n[04 鉴权与业务回归]")
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))


def main():
    print("=" * 62)
    print("49号·P3 可解释性绑定 Docker 实机验收")
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
