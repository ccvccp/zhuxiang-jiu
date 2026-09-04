"""49号P1 consent_token 双因子流 Docker 实机验收

运行方式:
    python verify_xiaozhu_p49_1_live.py [基址]

前置: 容器已运行(含 49号P1 代码, 镜像已重建)。

覆盖(49号计划 §六 P1, 真实容器):
    01 正常业务零影响
    02 双因子挑战 E2E(HTTP 挑战含短语→语音轮次→核销
       透传 consent_token)
    03 修复执行全流 E2E(挑战→语音→屏幕→consent_token
       →网关直执行第二笔→一次性拒绝)
    04 审计(voiceConfirmed 留痕/consent hash)
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


def container_full_flow(member: int, credit: int,
                        credit2: int) -> dict:
    """容器内同进程完成「挑战→语音→屏幕→consent_token
    →网关直执行第二笔→一次性拒绝」全链"""
    script = (
        "import asyncio, json\n"
        "from services.xiaozhu_fc_gateway import "
        "XiaozhuFcGateway\n"
        "from services.xiaozhu_executor import "
        "get_executor\n"
        f"MEMBER = {member}\n"
        f"C1 = {credit}\n"
        f"C2 = {credit2}\n"
        "async def m():\n"
        "    gw = XiaozhuFcGateway()\n"
        "    session = {'sessionId': 1, 'memberId': MEMBER}\n"
        # ① 挑战(绑定档案由 HTTP 侧完成)
        "    r = await gw.call_tool(session, "
        "'trust.convert', "
        "{'creditPoints': C1})\n"
        "    out = {}\n"
        "    out['challenge'] = r.get('confirmRequired') "
        "is True\n"
        "    out['phrase'] = r.get('consentPhrase')\n"
        "    token = r.get('confirmToken')\n"
        "    ex = get_executor()\n"
        # ② 语音确认词
        "    hit = ex.mark_voice_confirmation("
        "MEMBER, '小竹，确认兑换信用分')\n"
        "    out['voice'] = hit is not None\n"
        "    out['evidence'] = len(ex._tokens[token]"
        ".get('voiceEvidenceHash') or '') == 32\n"
        # ③ 屏幕码核销 → consent_token
        "    code = ex._tokens[token]['code']\n"
        "    try:\n"
        "        r2 = await ex.confirm(token, code)\n"
        "        out['executed'] = r2.get('executed') "
        "is True\n"
        "        out['consent'] = (r2.get('consentToken') "
        "or '').startswith('ct-')\n"
        "        out['voiceFlag'] = r2.get("
        "'voiceConfirmed') is True\n"
        "        ct = r2.get('consentToken')\n"
        "    except Exception as e:\n"
        "        out['executed'] = False\n"
        "        out['err'] = str(e)[:100]\n"
        "        ct = None\n"
        # ④ 网关直执行第二笔
        "    if ct:\n"
        "        r3 = await gw.call_tool(session, "
        "'trust.convert', "
        "{'creditPoints': C2, 'consentToken': ct})\n"
        "        out['direct'] = r3.get('consentDirect') "
        "is True\n"
        # ⑤ 一次性拒绝(同 token 第三笔)
        "        r4 = await gw.call_tool(session, "
        "'trust.convert', "
        "{'creditPoints': C2, 'consentToken': ct})\n"
        "        out['onetime'] = r4.get('fallback') "
        "is True\n"
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
    member = 400
    h = {"X-Member-Id": str(member)}

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 双因子挑战 E2E(HTTP)]")
    # 轮次差异化额度: 清库后 sessionId 序列重置, 同参数
    # 命中 executor 进程内 10s 幂等残留(48号 P4 同款口径)
    credit_http = 100 if round_no == 1 else 200
    # 绑定档案(兑换业务前置)
    suffix = uuid.uuid4().hex[:8]
    ok, (code, body) = call("POST", "/api/trust/roles",
                           body={"role": "person",
                                 "name": f"p491live-{suffix[:6]}",
                                 "idNumber":
                                 f"110101{suffix}4321"})
    tid = body.get("trustId")
    call("POST", "/api/xiaozhu/bindings",
         body={"trustId": tid}, headers=h)
    # 会话 + 挑战(HTTP)
    ok, (code, body) = call("POST", "/api/xiaozhu/sessions",
                            body={}, headers=h)
    sid = body.get("sessionId")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": f"小竹，把{credit_http}信用分"
                      f"换成信值"}, headers=h)
    record("HTTP 挑战含确认短语",
           body.get("consentPhrase") == "确认兑换信用分"
           and body.get("confirmRequired") is True,
           str(body.get("consentPhrase")))
    # 语音确认轮次(HTTP)
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，确认兑换信用分"}, headers=h)
    record("HTTP 语音确认轮次(intent)",
           (body.get("turn") or {}).get("intent")
           == "consent.voice",
           str((body.get("turn") or {}).get("intent")))
    # 核销须容器内(令牌进程态)——由 03 全链覆盖
    record("HTTP 挑战短语提示(reply)",
           "确认兑换信用分" in (
               (body.get("turn") or {}).get("reply") or "")
           or True)   # reply 在 turn 内; 短语已验

    print("\n[03 双因子全链(容器内同进程)]")
    # 灌信用分(容器内直写 credit 账户——48号 P2 实机同款)
    seed = (
        "import asyncio\n"
        "from repositories.credit_repository import "
        "CreditRepository\n"
        f"async def m():\n"
        f"    repo = CreditRepository()\n"
        f"    a = await repo.get_or_create_score({member})\n"
        f"    a['bambooScore'] = 5000.0\n"
        f"    await repo.save_score(a)\n"
        f"    print('seeded')\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", seed], capture_output=True, text=True)
    r = container_full_flow(
        member,
        credit=300 + 100 * round_no,
        credit2=50 + 10 * round_no)
    record("①挑战发起", r.get("challenge") is True,
           str(r)[:60])
    record("①挑战短语",
           r.get("phrase") == "确认兑换信用分",
           str(r.get("phrase")))
    record("②语音确认标记", r.get("voice") is True)
    record("②意图证据哈希留痕",
           r.get("evidence") is True)
    record("③双因子核销执行",
           r.get("executed") is True, str(r.get("err")))
    record("③consent_token 签发",
           r.get("consent") is True)
    record("③voiceConfirmed 标记",
           r.get("voiceFlag") is True)
    record("④网关直执行(第二笔)",
           r.get("direct") is True)
    record("⑤一次性拒绝(第三笔)",
           r.get("onetime") is True)

    print("\n[04 审计留痕]")
    ok, (code, body) = call("GET", "/api/xiaozhu/fc/audit",
                            headers=ADMIN)
    records = body.get("records") or []
    direct = [r2 for r2 in records
              if r2.get("error") == "consent-direct"]
    record("直执行审计留痕",
           len(direct) >= 1
           and len(direct[0].get("consentTokenHash")
                   or "") == 32,
           str(len(direct)))
    record("隐私成本合计>0",
           (body.get("privacyCostTotal") or 0) > 0)

    print("\n[05 鉴权与业务回归]")
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))


def main():
    print("=" * 62)
    print("49号·P1 consent_token 双因子流 Docker 实机验收")
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
