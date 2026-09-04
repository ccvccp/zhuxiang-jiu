"""50号P1 语音信值积分引擎 Docker 实机验收

运行方式:
    python verify_xiaozhu_p50_1_live.py [基址]

前置: 容器已运行(含 50号P1 代码, 镜像已重建)。

覆盖(50号计划 §七 P1, 真实容器):
    01 正常业务零影响
    02 声纹验证器(容器内——绑定检查/双态/文本未验证)
    03 日限 enforcement(容器内——6 次后 skip/
       penalty 豁免)
    04 反欺诈配合(容器内——47号灌入 watched tier+
       coop 计分/非问询拒绝)
    05 extra_mult+撤销(容器内)
    06 HTTP 层(my/rules 复验)
    07 交叉回归

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


def container_p1_check(round_no: int) -> dict:
    """容器内同进程: 声纹验证器+日限+反欺诈+extra_mult"""
    m1 = 7 + round_no * 100   # 701/801
    m2 = m1 + 1
    script = (
        "import asyncio, json, os\n"
        "os.environ['VOICE50_MODE'] = 'on'\n"
        "from services.xiaozhu_voice50_service import "
        "Voice50Service\n"
        "from services.xiaozhu_voice50_voiceprint import "
        "verify as vp_verify\n"
        "from repositories.voice50_repository import "
        "Voice50Repository\n"
        f"M1, M2 = {m1}, {m2}\n"
        "async def m():\n"
        "    out = {}\n"
        "    svc = Voice50Service()\n"
        # 跨轮残留清理(voice48 绑定不在 voice50 清理范围)
        "    from repositories.xiaozhu_repository import "
        "Xiaozhu48Repository\n"
        "    await Xiaozhu48Repository().delete_binding(M1)\n"
        "    await Xiaozhu48Repository().delete_binding(M2)\n"
        # 绑定 M1
        "    import uuid as _u\n"
        "    sfx = _u.uuid4().hex[:10]\n"
        "    from services.trust_scoring_service import "
        "TrustProfileService\n"
        "    from services.xiaozhu_service import "
        "XiaozhuService\n"
        "    tid = (await TrustProfileService().create_role("
        "'person', f'p51-{sfx[:6]}', "
        "f'110101{sfx}4321'))['trustId']\n"
        "    await XiaozhuService().bind_trust("
        "M1, tid, note='p51live')\n"
        # 声纹验证器(绑定/未绑定)
        "    vp1 = await vp_verify(M1, {'sessionId': 1}, "
        "'voice')\n"
        "    vp2 = await vp_verify(M2, {'sessionId': 2}, "
        "'voice')\n"
        "    out['vpBound'] = (vp1['verified'] is True and "
        "vp1['multiplier'] == 1.25)\n"
        "    out['vpUnbound'] = (vp2['verified'] is False "
        "and vp2['multiplier'] == 0.3)\n"
        # 日限: 灌 6 次 login → 第 7 次 skip
        "    for _ in range(6):\n"
        "        r = await svc.record_behavior("
        "M1, 'voice_login', voiceprint='proxy')\n"
        "    r7 = await svc.record_behavior("
        "M1, 'voice_login', voiceprint='proxy')\n"
        "    out['capSkip'] = (r7.get('skipped') == "
        "'dailyCapReached')\n"
        # penalty 豁免日限
        "    rp = await svc.record_behavior("
        "M1, 'voice_login', voiceprint='proxy', "
        "penalty=True)\n"
        "    out['penaltyExempt'] = ("
        "rp.get('skipped') is None and "
        "rp['finalScore'] == -5.0)\n"
        # 撤销 -1
        "    ru = await svc.record_confirm_undo(M1)\n"
        "    out['undo'] = (ru['finalScore'] == -1.0)\n"
        # extra_mult(0.5)
        "    re_ = await svc.record_behavior("
        "M2, 'voice_env_verify', voiceprint='proxy', "
        "extra_mult=0.5)\n"
        "    out['extraHalf'] = (abs(re_['finalScore'] "
        "- 2.5) < 1e-6)\n"
        # 反欺诈: M2 绑定+灌 47号 watched
        "    tid2 = tid\n"   # M2 用另一档案更准——简化共用绑定
        "    await XiaozhuService().bind_trust("
        "M2, tid2, note='p51live2')\n"
        "    from services.trust_risk_profile_service "
        "import TrustRiskProfileService\n"
        "    for _ in range(4):\n"
        "        await TrustRiskProfileService()"
        ".record_risk_event(tid2, source='p51live', "
        "signals=['semantic_reuse'])\n"
        "    rc = await svc.record_antifraud_coop("
        "M2, consistency_passed=True)\n"
        "    out['coop'] = (abs(rc['finalScore'] - 5.2) "
        "< 1e-6)\n"
        "    try:\n"
        "        await svc.record_antifraud_coop("
        f"700 + {round_no}, consistency_passed=True)\n"
        "        out['coopReject'] = False\n"
        "    except ValueError:\n"
        "        out['coopReject'] = True\n"
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

    print("\n[02-05 引擎 P1 全链(容器内)]")
    r = container_p1_check(round_no)
    record("①声纹验证器(绑定 ×1.25)",
           r.get("vpBound") is True, str(r)[:80])
    record("②声纹验证器(未绑定 ×0.3)",
           r.get("vpUnbound") is True)
    record("③日限 skip(第 7 次)",
           r.get("capSkip") is True)
    record("④扣分豁免日限(-5)",
           r.get("penaltyExempt") is True)
    record("⑤确认后撤销(-1)",
           r.get("undo") is True)
    record("⑥extra_mult(×0.5)",
           r.get("extraHalf") is True)
    record("⑦反欺诈 coop(×1.3=5.2)",
           r.get("coop") is True)
    record("⑧非问询场景拒绝",
           r.get("coopReject") is True)

    print("\n[06 HTTP 层复验]")
    h = {"X-Member-Id": "7001"}
    ok, (code, body) = call("GET", "/api/xiaozhu/voice50/my",
                            headers=h)
    record("GET my(池视图)", code == 200
           and "poolBalance" in body, str(code))
    ok, (code, body) = call(
        "GET", "/api/xiaozhu/voice50/rules", headers=ADMIN)
    record("GET rules(14 行为)", code == 200
           and body.get("total") == 14, str(code))

    print("\n[07 交叉回归]")
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))
    ok, (code, _) = call(
        "GET", "/api/ai-gov/dashboard", headers=ADMIN)
    record("46号治理看板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))


def main():
    print("=" * 62)
    print("50号·P1 L1 信号源+声纹双态+日限 Docker 实机验收")
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
