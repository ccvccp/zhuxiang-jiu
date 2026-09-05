"""55号二维码AI智能管理 P2 Docker 实机验收

运行方式:
    python verify_qr55_p2_live.py [基址]

前置: 容器已运行(含 55号 P0+P1+P2 代码)。

覆盖(55号计划 §六 P2, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律+观测面(generate 409; codes/stats/
       collect 可达——collect off 亦可用)
    03 容器内: 七类信号全链(scan_completed/
       clarify_hit/clarify_inefficient/tamper_detected/
       expired_unscanned+pending_completion 延迟态)
    04 容器内: 44号池双写+45号信值结算(deposit 验真
       +settle 留痕)+幂等(eventId 1:1 二轮零新增)
    05 容器内: 过期清扫(状态翻转+事件挂链+幂等)
       +调度主轮(补标+指标快照留痕)
    06 容器内: 六指标管道(六指标齐备+口径校验)
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
    """清理种子(qr55 全表+轮内种子会员的 45号档案)"""
    base = 9910 + round_no * 10
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
    """容器内: 七类信号→池双写+结算→幂等→清扫→指标
    (Redis 态)"""
    base_member = 9910 + round_no * 10
    script = (
        "import asyncio, json, os, time\n"
        "os.environ['QR55_MODE'] = 'on'\n"
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
        "from services.qr55_metrics_service import "
        "Qr55MetricsService\n"
        "from services.qr55_scheduler import "
        "sweep_expired_codes\n"
        f"BASE_M = {base_member}\n"
        "async def m():\n"
        "    out = {}\n"
        # ① 种子: 45号信值档案(结算主体)
        "    trepo = TrustValue45Repository()\n"
        "    for mid in range(BASE_M, BASE_M + 5):\n"
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
        "    gen = Qr55GenerateService()\n"
        "    scan = Qr55ScanService()\n"
        "    svc = Qr55Service()\n"
        "    fb = Qr55FeedbackService()\n"
        "    repo = Qr55Repository()\n"
        # ② S1 scan_completed(生成→扫码→完成)
        "    g1 = await gen.orchestrate(\n"
        "        BASE_M, '查政策解读')\n"
        "    await scan.scan(g1['code'],\n"
        "                    member_id=BASE_M)\n"
        "    await svc.record_completion(\n"
        "        g1['codeId'])\n"
        # ③ S3 clarify_hit(二次即中)
        "    await gen.orchestrate(\n"
        "        BASE_M + 1, '看看天气怎么样')\n"
        "    g2 = await gen.orchestrate(\n"
        "        BASE_M + 1, '查政策解读')\n"
        # ④ S4 clarify_inefficient(≥3轮)
        "    await gen.orchestrate(\n"
        "        BASE_M + 2, '帮我看看那个')\n"
        "    await gen.orchestrate(\n"
        "        BASE_M + 2, '还是不明白那个')\n"
        "    g3 = await gen.orchestrate(\n"
        "        BASE_M + 2, '查政策解读')\n"
        # ⑤ S2 pending_completion(扫码未完成)
        "    g4 = await gen.orchestrate(\n"
        "        BASE_M + 3, '查信值余额')\n"
        "    await scan.scan(g4['code'],\n"
        "                    member_id=BASE_M + 3)\n"
        # ⑥ S6 tamper_detected
        "    g5 = await gen.orchestrate(\n"
        "        BASE_M + 3, '查积分明细')\n"
        "    await scan.scan(\n"
        "        g5['code'][:-2] + 'xx',\n"
        "        member_id=BASE_M + 3)\n"
        # ⑦ S5 expired_unscanned(过期未扫)
        "    g6 = await gen.orchestrate(\n"
        "        BASE_M + 4, '查政策解读')\n"
        "    rec6 = await repo.get_code(g6['codeId'])\n"
        "    rec6['expiresAt'] = int(time.time()) - 10\n"
        "    await repo.update_code(rec6)\n"
        # ⑧ collect 首轮(七类信号+池双写+结算)
        "    c1 = await fb.collect_feedback()\n"
        "    out['c1_signals'] = c1.get('signals')\n"
        "    out['c1_labeled'] = c1.get('labeled')\n"
        "    out['c1_deferred'] = c1.get('deferred')\n"
        "    out['c1_pool'] = c1.get('poolSubmitted')\n"
        "    out['c1_settled'] = c1.get('settled')\n"
        # ⑨ 幂等: 二次 collect 零新增
        "    c2 = await fb.collect_feedback()\n"
        "    out['c2_labeled'] = c2.get('labeled')\n"
        "    out['c2_signals'] = c2.get('signals')\n"
        # ⑩ settle 事件留痕(deposit 验真)
        "    events = await repo.list_events(limit=500)\n"
        "    settles = [e for e in events\n"
        "               if e.get('eventType')\n"
        "               == 'settle']\n"
        "    out['settleCount'] = len(settles)\n"
        "    out['settleVerified'] = bool(\n"
        "        settles and (settles[0].get('detail')\n"
        "                     or {}).get(\n"
        "                         'depositVerified'))\n"
        # ⑪ 清扫(g6 已被 collect 依 exp 直判——
        #    新种一码验证清扫翻转)
        "    g7 = await gen.orchestrate(\n"
        "        BASE_M + 4, '查政策解读')\n"
        "    rec7 = await repo.get_code(g7['codeId'])\n"
        "    rec7['expiresAt'] = int(time.time()) - 10\n"
        "    await repo.update_code(rec7)\n"
        "    swept = await sweep_expired_codes()\n"
        "    out['sweptCount'] = swept.get('swept')\n"
        "    after7 = await repo.get_code(\n"
        "        g7['codeId'])\n"
        "    out['after7Status'] = after7.get('status')\n"
        # ⑫ 六指标
        "    snap = await Qr55MetricsService()."
        "compute_snapshot()\n"
        "    out['metrics'] = snap.get('metrics')\n"
        "    out['basis'] = snap.get('basis')\n"
        # ⑬ 回流统计
        "    stats = await fb.feedback_stats()\n"
        "    out['statsBySource'] = stats.get('bySource')\n"
        # ⑭ 45号档案因子变动(结算落账)
        "    prof = await trepo.get_profile(BASE_M)\n"
        "    out['longtailAfter'] = float(\n"
        "        (prof.get('factors') or {}).get(\n"
        "            'longtail_good') or 0)\n"
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
        body={"memberId": 9911,
              "text": "办老年优待证"},
        headers=ADMIN, expect=(409,))
    record("off 态 generate 409", code == 409, str(code))
    for path, label in (
            ("/api/qr55/codes", "codes 观测"),
            ("/api/qr55/stats", "stats 观测"),
            ("/api/qr55/registry", "registry 观测")):
        ok, (code, _) = call("GET", path, headers=ADMIN)
        record(f"观测面 {label} off 可访问",
               code == 200, str(code))
    ok, (code, body) = call(
        "POST", "/api/qr55/feedback/collect",
        headers=ADMIN)
    record("collect off 亦可用(管理面)",
           code == 200
           and (body or {}).get("success") is True,
           str(code))

    print("\n[03-06 容器内: 七类信号→双写结算→幂等"
          "→清扫→指标]")
    r = container_pipeline(round_no)
    sig = r.get("c1_signals") or {}

    # ③ 七类信号
    record("scan_completed 标注(+1.0)",
           sig.get("scan_completed") == 1, str(sig))
    record("clarify_hit 标注(+0.8)",
           sig.get("clarify_hit") == 1, str(sig))
    record("clarify_inefficient 标注(-0.6)",
           sig.get("clarify_inefficient") == 1,
           str(sig))
    record("tamper_detected 标注(-1.0)",
           sig.get("tamper_detected") == 1, str(sig))
    record("expired_unscanned 标注(-0.4)",
           sig.get("expired_unscanned") == 1, str(sig))
    record("pending_completion 延迟态(T+1)",
           (r.get("c1_deferred") or 0) >= 1,
           str(r.get("c1_deferred")))

    # ④ 池双写+结算
    record("44号池双写(5 条终态)",
           (r.get("c1_pool") or 0) >= 5,
           str(r.get("c1_pool")))
    record("45号信值结算(deposit 验真)",
           r.get("c1_settled") == 1
           and r.get("settleCount") == 1
           and r.get("settleVerified") is True,
           str((r.get("c1_settled"),
                r.get("settleCount"),
                r.get("settleVerified"))))
    record("45号因子落账(longtail_good 增量)",
           (r.get("longtailAfter") or 0) > 0,
           str(r.get("longtailAfter")))

    # ⑨ 幂等
    record("幂等(eventId 1:1 二轮零新增)",
           r.get("c2_labeled") == 0
           and not (r.get("c2_signals") or {}),
           str((r.get("c2_labeled"),
                r.get("c2_signals"))))

    # ⑪ 清扫(g6 collect 依 exp 直判标注后仍 active+
    #    g7 新种——清扫翻转 2 码)
    record("过期清扫(状态翻转+幂等计数)",
           r.get("sweptCount") == 2
           and r.get("after7Status") == "expired",
           str((r.get("sweptCount"),
                r.get("after7Status"))))

    # ⑫ 六指标
    m = r.get("metrics") or {}
    basis = r.get("basis") or {}
    record("六指标齐备(Redis 态)",
           set(m.keys()) == {
               "intentSatisfactionRate",
               "penetrationRate", "budgetHealthRate",
               "interceptEffectiveRate",
               "satisfactionScore",
               "clarifyEfficiency"},
           str(sorted(m.keys())))
    # 扫码码: g1(policy_search)+g4(trust_balance)
    # 生成码: 7(g1-g7; 澄清不产码)
    record("渗透率口径(2/7 扫码)",
           m.get("penetrationRate") == round(
               2 / 7, 4),
           str((m.get("penetrationRate"),
                basis.get("generatedCodes"),
                basis.get("scannedCodes"))))
    record("澄清效率口径(1/2)",
           m.get("clarifyEfficiency") == 0.5,
           str(m.get("clarifyEfficiency")))
    record("满意度口径(可算)",
           m.get("satisfactionScore") is not None,
           str(m.get("satisfactionScore")))

    # ⑬ 回流统计
    record("回流统计(Redis 态)",
           (r.get("statsBySource") or {}).get(
               "scan_completed") == 1,
           str(r.get("statsBySource")))

    print("\n[07 HTTP 端点+鉴权]")
    ok, (code, body) = call(
        "GET", "/api/qr55/codes", headers=ADMIN)
    record("HTTP codes 列表(Redis 态)",
           code == 200
           and (body or {}).get("total", 0) >= 5,
           str((code, (body or {}).get("total"))))
    ok, (code, _) = call(
        "POST", "/api/qr55/feedback/collect")
    record("collect 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "GET", "/api/qr55/stats")
    record("stats 无 Role 403",
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
