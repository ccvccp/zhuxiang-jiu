"""55号二维码AI智能管理 P1 Docker 实机验收

运行方式:
    python verify_qr55_p1_live.py [基址]

前置: 容器已运行(含 55号 P0+P1 代码)。

覆盖(55号计划 §六 P1, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律+观测面(generate/scan 409;
       registry/model/status 可达; clarify 规则轨)
    03 容器内: 智能生码编排(意图→评分→策略→生成
       direct/confirm/clarify 三态+千面适配
       healthy→full / critical→minimal)
    04 容器内: 扫码核销四态(ok→redeemed/expired/
       tampered 阻断+留痕/replayed 防重放
       ——nonce 一次性)
    05 容器内: 预算联动(49号——L0 零成本永不降级/
       正常扣减/超预算降级公开版)
    06 HTTP 端点+鉴权

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
    """按模式清理 Redis 键(分批 DEL)"""
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
    """清理种子(qr55 全表+轮内种子会员的 45/49号键)"""
    base = 9900 + round_no * 10
    redis_del_keys("zhuxiang:qr55:*")
    for mid in range(base, base + 5):
        redis_del_keys(
            f"zhuxiang:trust45:trust45_profiles:{mid}")
        redis_del_keys(
            f"zhuxiang:trust45:idmap:seed-digest-{mid}")
        redis_del_keys(
            "zhuxiang:voice48:voice48_privacy_budget:"
            f"{mid}")


def container_pipeline(round_no: int) -> dict:
    """容器内: 生码三态→核销四态→预算联动(Redis 态)"""
    base_member = 9900 + round_no * 10
    script = (
        "import asyncio, json, os\n"
        "os.environ['QR55_MODE'] = 'on'\n"
        "os.environ['LLM_ENABLED'] = 'off'\n"
        "from repositories.trust_value_repository "
        "import TrustValue45Repository\n"
        "from repositories.qr55_repository "
        "import Qr55Repository\n"
        "from repositories.xiaozhu_repository "
        "import Xiaozhu48Repository\n"
        "from services.qr55_generate_service import "
        "Qr55GenerateService\n"
        "from services.qr55_scan_service import "
        "Qr55ScanService\n"
        "from services.xiaozhu_privacy_service import "
        "XiaozhuPrivacyService\n"
        "from services.qr55_crypto import "
        "generate_code as gen_crypto\n"
        f"BASE_M = {base_member}\n"
        "async def m():\n"
        "    out = {}\n"
        # ① 种子: 45号信值档案(grade 千面数据源)
        "    trepo = TrustValue45Repository()\n"
        "    grades = {BASE_M: 'healthy',\n"
        "              BASE_M + 1: 'watch',\n"
        "              BASE_M + 2: 'healthy',\n"
        "              BASE_M + 3: 'watch',\n"
        "              BASE_M + 4: 'critical'}\n"
        "    for mid, g in grades.items():\n"
        "        rec = await trepo.get_profile(mid) or {}\n"
        "        rec.update({'trustId': mid, 'grade': g,\n"
        "                    'score': 80 if g == 'healthy'"
        " else 40,\n"
        "                    'factors': {}, 'role': 'person',\n"
        "                    'l1Severity': {},\n"
        "                    'idDigest': "
        "f'seed-digest-{mid}'})\n"
        "        await trepo.save_profile(rec)\n"
        "    gen = Qr55GenerateService()\n"
        "    scan = Qr55ScanService()\n"
        # ② direct 生码(healthy 高信任)
        "    r = await gen.orchestrate(\n"
        "        BASE_M, '我要给老人办优待证')\n"
        "    out['directStatus'] = r.get('status')\n"
        "    out['directStrategy'] = r.get('strategy')\n"
        "    out['directCodeId'] = r.get('codeId')\n"
        "    out['trustScore'] = ((r.get('scoring')"
        " or {}).get('trustScore'))\n"
        "    out['depthHealthy'] = ((r.get("
        "'personalization') or {})\n"
        "                          .get('displayDepth'))\n"
        # ③ clarify 生码(零命中)
        "    r2 = await gen.orchestrate(\n"
        "        BASE_M, '看看今天天气怎么样')\n"
        "    out['clarifyStatus'] = r2.get('status')\n"
        "    out['clarifyQuestion'] = "
        "bool(r2.get('question'))\n"
        # ④ confirm 流(watch 中信任)
        "    r3 = await gen.orchestrate(\n"
        "        BASE_M + 1, '办出生登记')\n"
        "    out['confirmStatus'] = r3.get('status')\n"
        "    if r3.get('status') == 'confirm_required':\n"
        "        r3b = await gen.orchestrate(\n"
        "            BASE_M + 1, '办出生登记',\n"
        "            confirm_params={'region': '杭州'})\n"
        "        out['confirmDone'] = "
        "r3b.get('status')\n"
        "    else:\n"
        "        out['confirmDone'] = 'n/a'\n"
        # ⑤ 千面 critical → minimal
        "    r5 = await gen.orchestrate(\n"
        "        BASE_M + 4, '我要给老人办优待证')\n"
        "    out['depthCritical'] = ((r5.get("
        "'personalization') or {})\n"
        "                           .get('displayDepth'))\n"
        # ⑥ 核销四态(L0 零成本)
        "    g1 = await gen.orchestrate(\n"
        "        BASE_M + 2, '查政策解读')\n"
        "    s1 = await scan.scan(g1.get('code'),\n"
        "                         member_id=BASE_M + 2)\n"
        "    out['scanStatus'] = s1.get('status')\n"
        "    out['budgetModeL0'] = ((s1.get('budget')"
        " or {}).get('mode'))\n"
        "    out['landingDepth'] = ((s1.get('landing')"
        " or {}).get('depth'))\n"
        "    out['landingRoute'] = bool(\n"
        "        (s1.get('landing') or {}).get('route'))\n"
        "    out['continueOn'] = ((s1.get('crossDevice')"
        " or {}).get('continueOn'))\n"
        # replayed: 同码二次扫
        "    s2 = await scan.scan(g1.get('code'),\n"
        "                         member_id=BASE_M + 2)\n"
        "    out['replayStatus'] = s2.get('status')\n"
        # expired: 负 ttl 码
        "    expired = gen_crypto(\n"
        "        'policy_search', {}, BASE_M + 2,\n"
        "        ttl_seconds=-10)['code']\n"
        "    s3 = await scan.scan(expired,\n"
        "                         member_id=BASE_M + 2)\n"
        "    out['expireStatus'] = s3.get('status')\n"
        # tampered: 改尾部
        "    g2 = await gen.orchestrate(\n"
        "        BASE_M + 2, '查信值余额')\n"
        "    bad = (g2.get('code') or '')[:-2] + 'xx'\n"
        "    s4 = await scan.scan(bad,\n"
        "                         member_id=BASE_M + 2)\n"
        "    out['tamperStatus'] = s4.get('status')\n"
        # ⑦ 预算联动(49号——L1 成本)
        "    g3 = await gen.orchestrate(\n"
        "        BASE_M + 3, '查积分明细记录')\n"
        "    out['budgetGenStatus'] = g3.get('status')\n"
        "    if g3.get('status') == 'generated':\n"
        "        before = await XiaozhuPrivacyService()"
        ".budget_view(BASE_M + 3)\n"
        "        s5 = await scan.scan(g3.get('code'),\n"
        "                             member_id=BASE_M+3)\n"
        "        out['budgetModeSpent'] = (\n"
        "            (s5.get('budget') or {})"
        ".get('mode'))\n"
        "        after = await XiaozhuPrivacyService()"
        ".budget_view(BASE_M + 3)\n"
        "        out['usedDelta'] = (\n"
        "            float(after.get('usedToday') or 0)\n"
        "            - float(before.get('usedToday')"
        " or 0))\n"
        # 超预算降级: 耗尽余量后扫新码
        "        g4 = await gen.orchestrate(\n"
        "            BASE_M + 3, '查积分明细')\n"
        "        if g4.get('status') == 'generated':\n"
        "            pref = await "
        "XiaozhuPrivacyService().budget_view(\n"
        "                BASE_M + 3)\n"
        "            limit = float(\n"
        "                pref.get('effectiveLimit') or 1.0)\n"
        "            repo = Xiaozhu48Repository()\n"
        "            rec = await repo.get_privacy_budget(\n"
        "                BASE_M + 3) or {}\n"
        "            rec['usedToday'] = round(limit, 2)\n"
        "            rec['dayKey'] = (\n"
        "                rec.get('dayKey') or 'x')\n"
        "            await repo.save_privacy_budget(rec)\n"
        "            s6 = await scan.scan(\n"
        "                g4.get('code'),\n"
        "                member_id=BASE_M + 3)\n"
        "            out['budgetModeDegrade'] = (\n"
        "                (s6.get('budget') or {})"
        ".get('mode'))\n"
        "            out['landingDegraded'] = (\n"
        "                (s6.get('landing') or {})"
        ".get('degraded'))\n"
        # ⑧ 状态翻转+全链埋点
        "    codes = await Qr55Repository().list_codes(\n"
        "        limit=100)\n"
        "    out['redeemedCount'] = len(\n"
        "        [c for c in codes\n"
        "         if c.get('status') == 'redeemed'])\n"
        "    events = await Qr55Repository().list_events(\n"
        "        limit=200)\n"
        "    out['eventTypes'] = sorted(\n"
        "        {e.get('eventType') for e in events})\n"
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
          f"(Redis 态)\n{'=' * 62}")
    clear_qr55(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/qr55/generate",
        body={"memberId": 9901,
              "text": "办老年优待证"},
        headers=ADMIN, expect=(409,))
    record("off 态 generate 409", code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/qr55/scan",
        body={"code": "x"},
        headers=ADMIN, expect=(409,))
    record("off 态 scan 409", code == 409, str(code))
    for path, label in (
            ("/api/qr55/registry", "registry"),
            ("/api/qr55/model/status", "status")):
        ok, (code, _) = call("GET", path, headers=ADMIN)
        record(f"观测面 {label} off 可访问",
               code == 200, str(code))
    ok, (code, body) = call(
        "POST", "/api/qr55/clarify",
        body={"text": "帮我看看天气", "memberId": 9901},
        headers=ADMIN)
    record("clarify 规则轨(off 亦可用)",
           code == 200
           and (body or {}).get("needClarify") is True
           and bool((body or {}).get("question")),
           str((code, (body or {}).get("needClarify"))))

    print("\n[03-05 容器内: 生码三态→核销四态→预算联动]")
    r = container_pipeline(round_no)

    # ③ 生码编排三态
    record("direct 直接生成",
           r.get("directStatus") == "generated"
           and r.get("directStrategy") == "direct"
           and (r.get("directCodeId") or 0) > 0,
           str((r.get("directStatus"),
                r.get("directStrategy"),
                r.get("directCodeId"))))
    record("信任分摘要(≥70)",
           (r.get("trustScore") or 0) >= 70,
           str(r.get("trustScore")))
    record("千面适配(healthy→full)",
           r.get("depthHealthy") == "full",
           str(r.get("depthHealthy")))
    record("千面适配(critical→minimal)",
           r.get("depthCritical") == "minimal",
           str(r.get("depthCritical")))
    record("clarify 澄清分派",
           r.get("clarifyStatus") == "clarify_required"
           and r.get("clarifyQuestion") is True,
           str((r.get("clarifyStatus"),
                r.get("clarifyQuestion"))))
    record("confirm 流三态合法+确认闭环",
           r.get("confirmStatus") in (
               "generated", "confirm_required",
               "clarify_required")
           and (r.get("confirmDone") in (
               "generated", "n/a")),
           str((r.get("confirmStatus"),
                r.get("confirmDone"))))

    # ④ 核销四态
    record("核销 ok→redeemed",
           r.get("scanStatus") == "redeemed",
           str(r.get("scanStatus")))
    record("L0 零成本永不降级",
           r.get("budgetModeL0") == "zero_cost",
           str(r.get("budgetModeL0")))
    record("千面落地页(深度+路由)",
           r.get("landingDepth") in ("full", "standard")
           and r.get("landingRoute") is True,
           str((r.get("landingDepth"),
                r.get("landingRoute"))))
    record("跨端续接标记(continueOn)",
           r.get("continueOn") == "mobile",
           str(r.get("continueOn")))
    record("防重放 replayed(nonce 一次性)",
           r.get("replayStatus") == "replayed",
           str(r.get("replayStatus")))
    record("过期 expired",
           r.get("expireStatus") == "expired",
           str(r.get("expireStatus")))
    record("篡改 tampered 阻断",
           r.get("tamperStatus") == "tampered",
           str(r.get("tamperStatus")))
    record("码状态翻转(redeemed)",
           (r.get("redeemedCount") or 0) >= 1,
           str(r.get("redeemedCount")))
    record("全链埋点(generate/clarify/scan/"
           "tamper/expire/replay)",
           {"generate", "clarify", "scan", "tamper",
            "expire", "replay"}
           <= set(r.get("eventTypes") or []),
           str(r.get("eventTypes")))

    # ⑤ 预算联动
    record("L1 服务生成+正常扣减(spent)",
           r.get("budgetGenStatus") == "generated"
           and r.get("budgetModeSpent") == "spent",
           str((r.get("budgetGenStatus"),
                r.get("budgetModeSpent"))))
    record("扣减生效(usedToday 增加)",
           (r.get("usedDelta") or 0) > 0,
           str(r.get("usedDelta")))
    record("超预算降级公开版(degraded)",
           r.get("budgetModeDegrade") == "degraded"
           and r.get("landingDegraded") is True,
           str((r.get("budgetModeDegrade"),
                r.get("landingDegraded"))))

    print("\n[06 HTTP 端点+鉴权]")
    ok, (code, _) = call(
        "POST", "/api/qr55/generate",
        body={"memberId": 9901, "text": "x"})
    record("generate 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/qr55/scan", body={"code": "x"})
    record("scan 无 Role 403",
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
