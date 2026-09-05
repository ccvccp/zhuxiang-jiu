"""52号P4 信任体验代理与评估报告 Docker 实机验收

运行方式:
    python verify_us52_p4_live.py [基址]

前置: 容器已运行(含 52号P4 代码, 镜像已重建)。

覆盖(52号计划 §七 P4, 真实容器 Redis 态):
    01 正常业务零影响(健康检查/35号面板)
    02 off 铁律(HTTP: transparency/trust/
       reports/generate 409)
    03 容器内(on 进程): 透明度管道
       (Redis 种子轮次 → turnTotal/
       privacyTurns/valueTurns 增量)
    04 容器内(on 进程): 信任体验四源加权
       (corpus/裁决/事件清理重种 →
       trust_gain=0.825 精确断言)
    05 容器内(on 进程): 评估报告生成
       (五维 20 项聚合+伦理风险触发+
       留痕递增)
    06 HTTP 端点+鉴权+报告列表回读

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


def clear_us52() -> None:
    """清理种子(us52 全表+50号三表+测试轮次)"""
    patterns = (
        "zhuxiang:us52:*",
        "zhuxiang:voice50:voice50_corpus:*",
        "zhuxiang:voice50:voice50_adjudication:*",
        "zhuxiang:voice50:voice50_events:*",
        "zhuxiang:voice48:voice48_turns:9900*")
    for pattern in patterns:
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


def container_p4_check(round_no: int) -> dict:
    """容器内(on 进程): 透明度增量+信任四源+
    报告生成"""
    session = 990000 + round_no
    member = 5370 + round_no
    script = (
        "import asyncio, json, os\n"
        "os.environ['US52_MODE'] = 'on'\n"
        "from core.helpers import ts as _ts\n"
        "from repositories.xiaozhu_repository "
        "import Xiaozhu48Repository\n"
        "from repositories.voice50_repository "
        "import Voice50Repository\n"
        "from services.us52_service import "
        "Us52MetricsService\n"
        f"SESSION = {session}\n"
        f"MEM = {member}\n"
        "async def seed_turns():\n"
        "    x = Xiaozhu48Repository()\n"
        "    turns = [\n"
        "        (1, 'privacy.budget', "
        "'当前隐私预算 3 次, 数据使用已获授权'),\n"
        "        (2, 'trust.score', '您的信值分 85'),\n"
        "        (3, 'general', '抱歉, 未能理解您的指令'),\n"
        "        (4, 'voice.score', "
        "'语音积分用于身份验真')]\n"
        "    for seq, intent, reply in turns:\n"
        "        await x.save_turn({'sessionId': "
        "SESSION, 'seq': seq, 'memberId': MEM, "
        "'intent': intent, 'reply': reply, "
        "'ts': _ts()})\n"
        "async def seed_trust():\n"
        "    v = Voice50Repository()\n"
        "    statuses = ['adopted'] * 4 + "
        "['pending']\n"
        "    for i, status in enumerate(statuses, "
        "1):\n"
        "        scenario = ('语音交互流畅' if i <= 4 "
        "else '对回复很不满')\n"
        "        await v.save_corpus({\n"
        "            'corpusId': await "
        "v.next_corpus_id(), 'memberId': MEM,\n"
        "            'sessionId': SESSION, "
        "'scenario': scenario,\n"
        "            'utterance': '样本%d' % i, "
        "'status': status, 'ts': _ts()})\n"
        "    for status in ['overturned'] * 2 + "
        "['upheld'] * 2:\n"
        "        await v.save_adjudication({\n"
        "            'adjId': await "
        "v.next_adjudication_id(), 'memberId': MEM,\n"
        "            'pattern': 'manual', 'status': "
        "status, 'ts': _ts()})\n"
        "    behaviors = (['voice_privacy_grant'] * 10 "
        "+ ['voice_polite'] * 20\n"
        "                 + ['voice_feedback'] * 3)\n"
        "    for b in behaviors:\n"
        "        await v.save_event({\n"
        "            'evId': await v.next_event_id(), "
        "'memberId': MEM,\n"
        "            'dayKey': '2026-09-05', "
        "'behavior': b,\n"
        "            'baseScore': 0.1, 'finalScore': 0.1, "
        "'status': 'settled',\n"
        "            'ts': _ts()})\n"
        "    await v.save_event({\n"
        "        'evId': await v.next_event_id(), "
        "'memberId': MEM,\n"
        "        'dayKey': '2026-09-05', 'behavior': "
        "'voice_feedback',\n"
        "        'baseScore': 0.0, 'finalScore': 0.0, "
        "'status': 'settled',\n"
        "        'ts': _ts()})\n"
        "async def m():\n"
        "    out = {}\n"
        "    svc = Us52MetricsService()\n"
        "    b = await svc."
        "compute_transparency_metrics()\n"
        "    d0 = b['detail']\n"
        "    await seed_turns()\n"
        "    await seed_trust()\n"
        "    t = await svc."
        "compute_transparency_metrics()\n"
        "    d1 = t['detail']\n"
        "    out['dTurn'] = d1['turnTotal'] - "
        "d0['turnTotal']\n"
        "    out['dPrivacy'] = d1['privacyTurns'] - "
        "d0['privacyTurns']\n"
        "    out['dValue'] = d1['valueTurns'] - "
        "d0['valueTurns']\n"
        "    out['dError'] = d1['errorTurns'] - "
        "d0['errorTurns']\n"
        "    out['tMetrics'] = t['metrics']\n"
        "    tr = await svc.compute_trust_metrics()\n"
        "    out['trMetrics'] = tr['metrics']\n"
        "    out['trSources'] = "
        "tr['detail']['trustSources']\n"
        "    r1 = await svc.generate_report()\n"
        "    r2 = await svc.generate_report()\n"
        "    rep = r2['report']\n"
        "    out['rid1'] = r1['report']['reportId']\n"
        "    out['rid2'] = rep['reportId']\n"
        "    out['metricCount'] = rep['metricCount']\n"
        "    out['decision'] = rep['decision']\n"
        "    out['risks'] = rep['complianceImpact']"
        "['potentialRisks']\n"
        "    out['proxy'] = rep['proxyDisclaimer']\n"
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
    clear_us52()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 off 铁律(HTTP 主进程)]")
    for path, label in (
            ("/api/us52/metrics/transparency", "transparency"),
            ("/api/us52/metrics/trust", "trust"),
            ("/api/us52/reports/generate", "generate")):
        ok, (code, _) = call("POST", path,
                            headers=ADMIN,
                            expect=(409,))
        record(f"off 态 {label} 409",
               code == 409, str(code))

    print("\n[03-05 容器内(on 进程): P4 管道]")
    r = container_p4_check(round_no)

    # 03 透明度管道(Redis 种子增量)
    record("透明度轮次增量=4(Redis 种子读取)",
           r.get("dTurn") == 4,
           str(r.get("dTurn")))
    record("隐私域增量=1(意图分类)",
           r.get("dPrivacy") == 1,
           str(r.get("dPrivacy")))
    record("价值域增量=2(trust.score+voice.score)",
           r.get("dValue") == 2,
           str(r.get("dValue")))
    record("错误域增量=1(抱歉话术)",
           r.get("dError") == 1,
           str(r.get("dError")))
    t_metrics = r.get("tMetrics") or {}
    record("透明度四指标键齐备",
           set(t_metrics) == {
               "privacy_notice_rate",
               "attribution_rate", "error_clarity",
               "data_purpose_rate"},
           str(list(t_metrics)))

    # 04 信任体验四源加权(清理重种——精确)
    tr_metrics = r.get("trMetrics") or {}
    record("信任四源加权增益=0.825",
           abs((tr_metrics.get("trust_gain_index")
                or 0) - 0.825) < 0.001,
           str(tr_metrics.get("trust_gain_index")))
    record("伦理负面率=0.2(1/5)",
           tr_metrics.get("ethics_negative_rate") == 0.2,
           str(tr_metrics.get("ethics_negative_rate")))
    record("反馈健康度=0.75(3/4)",
           tr_metrics.get("feedback_health_ratio")
           == 0.75,
           str(tr_metrics.get(
               "feedback_health_ratio")))
    record("控制感=0.6(Redis 态内存读口径)",
           tr_metrics.get("control_sense_rate") == 0.6,
           str(tr_metrics.get("control_sense_rate")))
    src = r.get("trSources") or {}
    record("四源明细(采纳0.8+翻转0.5+授权1+礼貌1)",
           src.get("adoptRatio") == 0.8
           and src.get("overturnRatio") == 0.5
           and src.get("grantRatio") == 1.0
           and src.get("politeRatio") == 1.0,
           str(src))

    # 05 评估报告
    record("五维 20 项全量聚合",
           r.get("metricCount") == 20,
           str(r.get("metricCount")))
    record("决策字段合法",
           r.get("decision") in {
               "pass", "mandatory", "priority",
               "veto", "regression"},
           str(r.get("decision")))
    risks = r.get("risks") or []
    record("伦理风险触发(负面率>0.05)",
           any("伦理负面" in str(x) for x in risks),
           str(risks)[:60])
    record("proxy 免责声明",
           "行为代理" in str(r.get("proxy")),
           str(r.get("proxy"))[:40])
    rid1, rid2 = r.get("rid1"), r.get("rid2")
    record("报告留痕递增",
           isinstance(rid1, int)
           and isinstance(rid2, int) and rid2 > rid1,
           f"{rid1} → {rid2}")

    print("\n[06 HTTP 端点+鉴权]")
    ok, (code, _) = call(
        "POST", "/api/us52/metrics/transparency",
        headers=ADMIN, expect=(200, 409))
    record("transparency 路由可达",
           code in (200, 409), str(code))
    ok, (code, _) = call(
        "POST", "/api/us52/metrics/trust",
        expect=(403,))
    record("trust 无 Role 403",
           code == 403, str(code))
    ok, (code, body) = call(
        "GET", "/api/us52/reports", headers=ADMIN)
    record("GET /reports 回读容器内报告",
           code == 200
           and (body.get("total") or 0) >= 2,
           str(body.get("total")))
    ok, (code, _) = call("GET", "/api/us52/reports",
                         expect=(403,))
    record("reports 无 Role 403",
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
