"""55号二维码AI智能管理 P5 Docker 实机验收(收官)

运行方式:
    python verify_qr55_p5_live.py [基址]

前置: 容器已运行(含 55号 P0-P5 代码)。

覆盖(55号计划 §六 P5, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律+观测面(dashboard off 亦可用)
    03 容器内: 全链种子(多服务多状态多信号源)
    04 容器内: 四区看板(码量/服务分布/回流漏斗/
       防御区+集中度)
    05 容器内: 红队六向量+投毒洪流(allDefended)
    06 容器内: 宪法断言(44/46/48/51号零改动)
    07 HTTP 端点+鉴权

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

CONTAINER = "zhuxiang-jiu-backend-1"
REDIS = "zhuxiang-jiu-redis-1"


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


def redis_del_keys(pattern: str) -> None:
    out = subprocess.run(
        ["docker", "exec", REDIS,
         "redis-cli", "--scan", "--pattern", pattern],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", REDIS, "redis-cli",
             "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def clear_qr55(round_no: int) -> None:
    base = 9970 + round_no * 10
    redis_del_keys("zhuxiang:qr55:*")
    redis_del_keys("zhuxiang:ai_learning:*")
    for mid in range(base, base + 4):
        redis_del_keys(
            f"zhuxiang:trust45:trust45_profiles:{mid}")
        redis_del_keys(
            f"zhuxiang:trust45:idmap:seed-digest-{mid}")
        redis_del_keys(
            "zhuxiang:voice48:voice48_privacy_budget:"
            f"{mid}")


def container_pipeline(round_no: int) -> dict:
    """容器内: 全链种子→看板→红队→宪法(Redis 态)"""
    base_member = 9970 + round_no * 10
    script = (
        "import asyncio, json, os\n"
        "os.environ['LLM_ENABLED'] = 'off'\n"
        "from repositories.trust_value_repository "
        "import TrustValue45Repository\n"
        "from repositories.qr55_repository "
        "import Qr55Repository\n"
        "from services.qr55_generate_service import "
        "Qr55GenerateService\n"
        "from services.qr55_scan_service import "
        "Qr55ScanService\n"
        "from services.qr55_service import "
        "Qr55Service\n"
        "from services.qr55_feedback_service import "
        "Qr55FeedbackService\n"
        "from services.qr55_dashboard_service import "
        "Qr55DashboardService\n"
        "from services.qr55_redteam_service import "
        "Qr55RedteamService\n"
        f"BASE_M = {base_member}\n"
        "async def m():\n"
        "    out = {}\n"
        # ① 种子
        "    trepo = TrustValue45Repository()\n"
        "    for mid in range(BASE_M, BASE_M + 2):\n"
        "        rec = await trepo.get_profile(mid) "
        "or {}\n"
        "        rec.update({'trustId': mid,\n"
        "                    'grade': 'healthy',\n"
        "                    'score': 80,\n"
        "                    'factors': {},\n"
        "                    'role': 'person',\n"
        "                    'l1Severity': {},\n"
        "                    'idDigest': "
        "f'seed-digest-{mid}'})\n"
        "        await trepo.save_profile(rec)\n"
        # ② 全链种子(完成+过期+篡改三信号源)
        "    os.environ['QR55_MODE'] = 'on'\n"
        "    gen = Qr55GenerateService()\n"
        "    scan = Qr55ScanService()\n"
        "    svc = Qr55Service()\n"
        "    g1 = await gen.orchestrate(\n"
        "        BASE_M, '查政策解读')\n"
        "    await scan.scan(g1['code'],\n"
        "                    member_id=BASE_M)\n"
        "    await svc.record_completion(\n"
        "        g1['codeId'])\n"
        "    g2 = await gen.orchestrate(\n"
        "        BASE_M, '查信值余额')\n"
        "    rec2 = await Qr55Repository().get_code(\n"
        "        g2['codeId'])\n"
        "    rec2['status'] = 'expired'\n"
        "    await Qr55Repository().update_code(rec2)\n"
        "    g3 = await gen.orchestrate(\n"
        "        BASE_M, '查政策解读')\n"
        "    await scan.scan(g3['code'][:-2] + 'xx',\n"
        "                    member_id=BASE_M)\n"
        "    os.environ['QR55_MODE'] = 'off'\n"
        "    c = await Qr55FeedbackService()"
        ".collect_feedback()\n"
        "    out['collectLabeled'] = c.get('labeled')\n"
        # ③ 四区看板
        "    d = await Qr55DashboardService().build()\n"
        "    zones = d.get('zones') or {}\n"
        "    out['zoneKeys'] = sorted(zones.keys())\n"
        "    vol = zones.get('volume') or {}\n"
        "    out['totalCodes'] = vol.get('totalCodes')\n"
        "    out['byStatus'] = vol.get('byStatus')\n"
        "    fun = zones.get('funnel') or {}\n"
        "    out['funnel'] = fun.get('funnel')\n"
        "    out['signals'] = fun.get('signals')\n"
        "    out['settled'] = fun.get('trustSettled')\n"
        "    dfn = zones.get('defense') or {}\n"
        "    out['guardHealthy'] = ((dfn.get(\n"
        "        'guardrail') or {}).get('healthy'))\n"
        "    conc = (dfn.get(\n"
        "        'sourceConcentration') or {})\n"
        "    out['concRatio'] = conc.get('topRatio')\n"
        "    out['concAlert'] = conc.get('alert')\n"
        "    out['championVersion'] = dfn.get(\n"
        "        'championVersion')\n"
        # ④ 红队(RT-07 洪流注入 30 条)
        "    os.environ['QR55_MODE'] = 'on'\n"
        "    rt = await Qr55RedteamService().run_all(\n"
        "        member_id=BASE_M + 1)\n"
        "    summary = rt.get('summary') or {}\n"
        "    out['rtTotal'] = summary.get('total')\n"
        "    out['rtDefended'] = summary.get('defended')\n"
        "    out['rtAll'] = summary.get('allDefended')\n"
        "    vectors = rt.get('vectors') or {}\n"
        "    out['rtKeys'] = sorted(vectors.keys())\n"
        "    os.environ['QR55_MODE'] = 'off'\n"
        # ⑤ 宪法断言数据
        "    from services.ai_learning_service import "
        "SCORER_REGISTRY as SR\n"
        "    out['scorerCount'] = len(SR)\n"
        "    out['qrInRegistry'] = (\n"
        "        'qr_orchestration' in SR)\n"
        "    from services.qr55_registry import "
        "SERVICE_REGISTRY\n"
        "    out['serviceCount'] = len(SERVICE_REGISTRY)\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:400]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态·收官)\n{'=' * 62}")
    clear_qr55(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/qr55/generate",
        body={"memberId": 9971,
              "text": "办老年优待证"},
        headers=ADMIN, expect=(409,))
    record("off 态 generate 409", code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/qr55/dashboard", headers=ADMIN)
    record("dashboard off 亦可用(观测面)",
           code == 200 and "zones" in (body or {}),
           str(code))
    ok, (code, _) = call(
        "POST", "/api/qr55/redteam", headers=ADMIN,
        expect=(409,))
    record("redteam off 409(需 on)",
           code == 409, str(code))

    print("\n[03-06 容器内: 种子→看板→红队→宪法]")
    r = container_pipeline(round_no)

    # ③ 全链种子
    record("回流种子(3 信号源)",
           r.get("collectLabeled") == 3,
           str(r.get("collectLabeled")))

    # ④ 四区看板
    record("四区齐备(Redis 态)",
           r.get("zoneKeys") == [
               "defense", "funnel", "services",
               "volume"],
           str(r.get("zoneKeys")))
    record("码量区(3 状态分布)",
           (r.get("byStatus") or {}).get("redeemed")
           == 1
           and (r.get("byStatus") or {}).get("expired")
           == 1
           and (r.get("byStatus") or {}).get("active")
           == 1,
           str(r.get("byStatus")))
    f = r.get("funnel") or {}
    record("回流漏斗(3→1→1+完成率 100%)",
           f.get("generated") == 3
           and f.get("scanned") == 1
           and f.get("completed") == 1
           and f.get("completeRate") == 1.0,
           str(f))
    record("信号分布+45号结算",
           (r.get("signals") or {}).get(
               "scan_completed") == 1
           and (r.get("signals") or {}).get(
               "tamper_detected") == 1
           and (r.get("signals") or {}).get(
               "expired_unscanned") == 1
           and r.get("settled") == 1,
           str((r.get("signals"),
                r.get("settled"))))
    record("防御区(护栏健康+集中度无告警)",
           r.get("guardHealthy") is True
           and r.get("concRatio") == 0.3333
           and r.get("concAlert") is False,
           str((r.get("guardHealthy"),
                r.get("concRatio"),
                r.get("concAlert"))))
    record("防御区(champion 版本呈现)",
           bool(r.get("championVersion")),
           str(r.get("championVersion")))

    # ⑤ 红队
    record("红队六向量+投毒(Redis 态全防御)",
           r.get("rtTotal") == 7
           and r.get("rtDefended") == 7
           and r.get("rtAll") is True,
           str((r.get("rtTotal"),
                r.get("rtDefended"),
                r.get("rtAll"))))
    record("红队向量齐备(RT-01~07)",
           r.get("rtKeys") == [
               "RT-01", "RT-02", "RT-03", "RT-04",
               "RT-05", "RT-06", "RT-07"],
           str(r.get("rtKeys")))

    # ⑥ 宪法断言
    record("44号零改动(30 档案+qr 在册)",
           r.get("scorerCount") == 30
           and r.get("qrInRegistry") is True,
           str((r.get("scorerCount"),
                r.get("qrInRegistry"))))
    record("51号零改动(自建白名单 12 项)",
           r.get("serviceCount") == 12,
           str(r.get("serviceCount")))

    print("\n[07 HTTP 端点+鉴权]")
    # 容器内直调红队(HTTP 主进程 QR55_MODE=off——
    # 红队需 on 态, 容器内环境可控)
    rt_script = (
        "import asyncio, json, os\n"
        "os.environ['QR55_MODE'] = 'on'\n"
        "os.environ['LLM_ENABLED'] = 'off'\n"
        "from services.qr55_redteam_service import "
        "Qr55RedteamService\n"
        "async def m():\n"
        "    r = await Qr55RedteamService().run_all("
        "member_id=9971)\n"
        "    print(json.dumps(r.get('summary')))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", rt_script],
        capture_output=True, text=True)
    try:
        summary = json.loads(
            (out.stdout or "").strip()
            .splitlines()[-1])
    except (ValueError, IndexError):
        summary = {}
    record("红队 on 态直调(全防御·幂等)",
           summary.get("allDefended") is True,
           str(summary))
    ok, (code, _) = call(
        "GET", "/api/qr55/dashboard")
    record("dashboard 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/qr55/redteam")
    record("redteam 无 Role 403",
           code == 403, str(code))


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
