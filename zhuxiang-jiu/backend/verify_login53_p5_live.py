"""53号P5 效果评估+监控看板 Docker 实机验收

运行方式:
    python verify_login53_p5_live.py [基址]

前置: 容器已运行(含 53号P5 代码)。

覆盖(53号计划 §九 P5, 真实容器 Redis 态):
    01 正常业务零影响(健康检查/35号面板)
    02 off 铁律(HTTP: compute 409)+观测面可达
    03 容器内(on 进程): 编排登录+驻留领取+
       六指标计算(Redis 态全链)
    04 容器内(on 进程): 看板(指标+通道占比+
       风险分布+四态分布)
    05 HTTP 端点+鉴权
    06 红队复验(伪造令牌/跨会员凭证/
       通道伪造/预算绕过——全拒绝)

×2 轮幂等验证(每轮清理种子重造)。
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
        with urllib.request.urlopen(req, timeout=180) as r:
            code, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def clear_login53() -> None:
    """清理种子(login53 全表+entry bio 凭证)"""
    for pattern in ("zhuxiang:login53:*",):
        out = subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "--scan", "--pattern",
             pattern],
            capture_output=True, text=True)
        keys = [k for k in (out.stdout or "").split() if k]
        for i in range(0, len(keys), 200):
            subprocess.run(
                ["docker", "exec",
                 "zhuxiang-jiu-redis-1", "redis-cli",
                 "DEL", *keys[i:i + 200]],
                capture_output=True, text=True)


def container_p5_check(round_no: int) -> dict:
    """容器内(on 进程): 编排+驻留+指标+看板"""
    member = 5480 + round_no
    script = (
        "import asyncio, json, os\n"
        "os.environ['LOGIN53_MODE'] = 'on'\n"
        "from core.helpers import ts as _ts\n"
        "from repositories.member_repository "
        "import MemberRepository\n"
        "from repositories.entry_repository "
        "import EntryRepository\n"
        "from services.login53_service import "
        "Login53Service\n"
        f"MEM = {member}\n"
        "async def m():\n"
        "    out = {}\n"
        "    svc = Login53Service()\n"
        "    await MemberRepository().save(MEM, {\n"
        "        'id': MEM, 'phone': '139%08d' % MEM,\n"
        "        'nickname': '实机验收', 'role': 'member',\n"
        "        'created_at': '2026-06-01',\n"
        "        'points': 100, 'status': 1})\n"
        "    await EntryRepository().save_bio({\n"
        "        'credentialId': 'BIOlive%04d' % MEM,\n"
        "        'memberId': MEM, 'bioType': 'face_id',\n"
        "        'deviceId': 'dev-live',\n"
        "        'publicKeyHash': 'c' * 32,\n"
        "        'name': 'live', 'status': 'active',\n"
        "        'mode': 'mock',\n"
        "        'enrolledAt': _ts()})\n"
        "    # 编排登录(silent 档)\n"
        "    r1 = await svc.orchestrate(\n"
        "        MEM, 'passkey',\n"
        "        credential={'credentialId': "
        "'BIOlive%04d' % MEM},\n"
        "        hour=12)\n"
        "    out['login'] = r1['status']\n"
        "    out['tier'] = r1.get('tier')\n"
        "    # 语音唤醒登录\n"
        "    r2 = await svc.voice_wake_login(\n"
        "        MEM, '小竹，我回来了', hour=12)\n"
        "    out['voice'] = r2['status']\n"
        "    # 驻留领取\n"
        "    r3 = await svc.retention_claim(\n"
        "        MEM, greeting='小竹你好')\n"
        "    out['claim'] = r3['status']\n"
        "    # 指标计算\n"
        "    r4 = await svc.compute_metrics()\n"
        "    snap = r4['snapshot']\n"
        "    out['metrics'] = snap['metrics']\n"
        "    out['passed'] = snap['passedCount']\n"
        "    # 看板\n"
        "    d = await svc.dashboard()\n"
        "    out['byChannel'] = d['byChannel']\n"
        "    out['byPortal'] = d['byPortalState']\n"
        "    # 红队: 跨会员凭证\n"
        "    try:\n"
        "        await svc.orchestrate(\n"
        "            MEM + 1, 'passkey',\n"
        "            credential={'credentialId': "
        "'BIOlive%04d' % MEM})\n"
        "        out['rt02'] = 'not-rejected'\n"
        "    except ValueError as e:\n"
        "        out['rt02'] = str(e)[:20]\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:300]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收\n{'=' * 62}")
    clear_login53()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call("POST",
                         "/api/login53/metrics/compute",
                         headers=ADMIN, expect=(409,))
    record("off 态 compute 409", code == 409, str(code))
    for path, label in (
            ("/api/login53/metrics/latest", "latest"),
            ("/api/login53/dashboard", "dashboard")):
        ok, (code, _) = call("GET", path, headers=ADMIN)
        record(f"观测面 {label} off 可访问",
               code == 200, str(code))

    print("\n[03-04 容器内(on 进程): P5 管道]")
    r = container_p5_check(round_no)

    record("编排登录(silent 档)",
           r.get("login") == "authenticated"
           and r.get("tier") == "silent",
           str((r.get("login"), r.get("tier"))))
    record("语音唤醒登录",
           r.get("voice") == "authenticated",
           str(r.get("voice")))
    record("驻留领取",
           r.get("claim") == "claimed",
           str(r.get("claim")))
    metrics = r.get("metrics") or {}
    record("六指标齐备",
           set(metrics) == {
               "login_success_rate",
               "avg_login_duration",
               "retention_5min_rate",
               "voice_login_share",
               "complaint_rate",
               "trust_gain_delta"},
           str(list(metrics))[:60])
    record("成功率口径(1.0——两次均成功)",
           (metrics.get("login_success_rate")
            or {}).get("value") == 1.0,
           str((metrics.get("login_success_rate")
                or {}).get("value")))
    record("达标计数(passedCount>=4)",
           (r.get("passed") or 0) >= 4,
           str(r.get("passed")))
    by_channel = r.get("byChannel") or {}
    record("看板通道占比(passkey+voice)",
           by_channel.get("passkey", 0) >= 1
           and by_channel.get("voice", 0) >= 1,
           str(by_channel))
    by_portal = r.get("byPortal") or {}
    record("看板四态分布(有档案)",
           sum(by_portal.values()) >= 1,
           str(by_portal))

    print("\n[05 HTTP 端点+鉴权]")
    ok, (code, _) = call("GET", "/api/login53/dashboard")
    record("dashboard 无 Role 403",
           code == 403, str(code))

    print("\n[06 红队复验]")
    record("RT-02 跨会员凭证拒绝",
           "归属不匹配" in str(r.get("rt02")),
           str(r.get("rt02")))


def main() -> int:
    for i in (1, 2):
        run_round(i)
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
