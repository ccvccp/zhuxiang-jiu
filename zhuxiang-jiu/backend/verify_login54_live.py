"""54号小竹AI智能登录引擎大模型 Docker 实机验收

运行方式:
    python verify_login54_live.py [基址]

前置: 容器已运行(含 54号 P0-P5 代码)。

覆盖(54号计划 §六, 真实容器 Redis 态):
    01 正常业务零影响(健康检查/35号面板)
    02 off 铁律+观测面(preview 409/registry
       /status/dashboard/stats 可达)
    03 容器内: 决策回流(53号事件种子→collect
       →七类信号真值标注+44号池双写)
    04 容器内: 自主学习(Hedge 轮次→挑战者)→
       手动晋升(promoted)→版本回滚(权重还原)
    05 容器内: 回归检测+看板四区+防御区
    06 HTTP 端点+鉴权
    07 红队复验(RT 投毒洪流→护栏约束+
       集中度告警)

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


def clear_login54() -> None:
    """清理种子(login54 全表+login53 事件/驻留)"""
    for pattern in ("zhuxiang:login54:*",
                    "zhuxiang:login53:*",
                    "zhuxiang:ai_learning:*"):
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


def container_pipeline(round_no: int) -> dict:
    """容器内: 回流→学习→晋升→回滚→回归检测→看板"""
    base_member = 7700 + round_no * 20
    script = (
        "import asyncio, json\n"
        "from core.helpers import ts as _ts\n"
        "from datetime import datetime, timedelta\n"
        "from repositories.login53_repository import "
        "Login53Repository\n"
        "from services.login54_feedback_service import "
        "Login54FeedbackService\n"
        "from services.login54_learn_service import "
        "Login54LearnService\n"
        "from services.login54_dashboard_service import "
        "Login54DashboardService\n"
        f"BASE_M = {base_member}\n"
        "async def m():\n"
        "    out = {}\n"
        "    # ① 种子: 10 会员成功+驻留(回流数据源)\n"
        "    repo53 = Login53Repository()\n"
        "    old = (datetime.now().astimezone()\n"
        "           - timedelta(hours=1)).isoformat()\n"
        "    for m in range(BASE_M, BASE_M + 10):\n"
        "        eid = await repo53.next_event_id()\n"
        "        await repo53.save_event({\n"
        "            'eventId': eid, 'memberId': m,\n"
        "            'method': 'passkey', 'riskScore': 20.0,\n"
        "            'decision': 'silent',\n"
        "            'durationMs': 100.0,\n"
        "            'privacyCost': 0.01,\n"
        "            'explainRef': '', 'success': True,\n"
        "            'detail': '', 'createdAt': old})\n"
        "        await repo53.save_retention({\n"
        "            'memberId': m, 'dayKey': _ts()[:10],\n"
        "            'rewardPoints': 1, 'streakDays': 1,\n"
        "            'greeting': 'live',\n"
        "            'claimedAt': _ts(),\n"
        "            'milestoneUnlocked': 0,\n"
        "            'eventNote': ''})\n"
        # ② 红队洪流源(RT: 投毒 30 条单源)
        "    from services.login54_scorer import "
        "Login54Scorer\n"
        "    from repositories.ai_learning_repository "
        "import AiLearningRepository\n"
        "    ctx = {'channelSuccess': 0.1,\n"
        "           'channel': 'qr',\n"
        "           'baselineMatch': 0.1,\n"
        "           'channelFailCount': 4,\n"
        "           'portalState': 'high_risk'}\n"
        "    r = await Login54Scorer().score(ctx)\n"
        "    pool = AiLearningRepository()\n"
        "    for i in range(50):\n"
        "        await pool.add_feedback({\n"
        "            'scorerId': 'login_orchestration',\n"
        "            'weightVersion': 'v1',\n"
        "            'scoreAtDecision': "
        "r.get('trustScore'),\n"
        "            'actualAction': 'enhanced',\n"
        "            'expectedAction': 'enhanced',\n"
        "            'correct': True,\n"
        "            'factors': r.get('factors'),\n"
        "            'reward': 1.0,\n"
        "            'note': f'rt-live:{i}',\n"
        "            'source': 'attacker_flood',\n"
        "            'status': 'pending',\n"
        "            'createdAt': _ts()})\n"
        # ③ 决策回流(Redis 态)
        "    c = await Login54FeedbackService()"
        ".collect_feedback()\n"
        "    out['labeled'] = c.get('labeled')\n"
        "    out['poolSubmitted'] = c.get('poolSubmitted')\n"
        "    out['signals'] = c.get('signals')\n"
        # ④ 学习+晋升+回滚(Redis 态)
        "    learn = await Login54LearnService()"
        ".run_learning()\n"
        "    out['learnedFrom'] = learn.get('learnedFrom')\n"
        "    out['newVersion'] = learn.get('newVersion')\n"
        "    out['status'] = learn.get('newStatus')\n"
        "    w = learn.get('weights') or {}\n"
        "    out['normalized'] = round(sum(w.values()), 6)\n"
        "    out['guardOk'] = all(\n"
        "        0.10/2.0 <= w.get(k, 0) <= 0.10*2.0\n"
        "        if k in ('budget_sufficiency',\n"
        "                 'member_maturity',\n"
        "                 'voice_confidence',\n"
        "                 'portal_state') else\n"
        "        0.15/2.0 <= w.get(k, 0) <= 0.15*2.0\n"
        "        for k in w)\n"
        "    promo = await Login54LearnService().promote()\n"
        "    out['promotedVersion'] = "
        "promo.get('promotedVersion')\n"
        "    rb = await Login54LearnService().rollback(\n"
        "        reason='live-verify')\n"
        "    out['rollbackTarget'] = "
        "rb.get('targetVersion')\n"
        "    out['rbWeights'] = "
        "round(sum((rb.get('weights') or {})"
        ".values()), 6)\n"
        # ⑤ 看板(Redis 态)
        "    d = await Login54DashboardService().build()\n"
        "    zones = d.get('zones') or {}\n"
        "    out['zoneKeys'] = sorted(zones.keys())\n"
        "    v = (zones.get('version') or {})\n"
        "    out['champion'] = ((v.get('champion') or {})"
        ".get('version'))\n"
        "    fb = (zones.get('feedback') or {})\n"
        "    out['fbBySource'] = "
        "fb.get('bySource')\n"
        "    defense = (zones.get('defense') or {})\n"
        "    out['guardHealthy'] = ((defense.get(\n"
        "        'guardrail') or {}).get('healthy'))\n"
        "    conc = (defense.get(\n"
        "        'sourceConcentration') or {})\n"
        "    out['concTop'] = conc.get('topSource')\n"
        "    out['concRatio'] = conc.get('topRatio')\n"
        "    out['concAlert'] = conc.get('alert')\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1",
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:400]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收\n{'=' * 62}")
    clear_login54()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/login54/score/preview",
        body={"ctx": {"channelSuccess": 0.9,
                      "channel": "passkey",
                      "portalState": "active"}},
        headers=ADMIN, expect=(409,))
    record("off 态 preview 409", code == 409, str(code))
    for path, label in (
            ("/api/login54/registry", "registry"),
            ("/api/login54/model/status", "status"),
            ("/api/login54/dashboard", "dashboard"),
            ("/api/login54/feedback/stats", "stats")):
        ok, (code, _) = call("GET", path, headers=ADMIN)
        record(f"观测面 {label} off 可访问",
               code == 200, str(code))

    print("\n[03-05 容器内: 回流→学习→晋升→回滚→看板]")
    r = container_pipeline(round_no)

    # ③ 回流(Redis 态)
    record("回流标注(10 条 retention_dwell)",
           r.get("labeled") == 10
           and ((r.get("signals") or {})
                .get("retention_dwell") == 10),
           str((r.get("labeled"),
                r.get("signals"))))
    record("44号池双写(10 条)",
           r.get("poolSubmitted") == 10,
           str(r.get("poolSubmitted")))

    # ④ 学习+晋升+回滚(Redis 态)
    record("Hedge 学习(learnedFrom≥10)",
           (r.get("learnedFrom") or 0) >= 10
           and bool(r.get("newVersion")),
           str((r.get("learnedFrom"),
                r.get("newVersion"))))
    record("权重归一化(和=1.0)",
           abs((r.get("normalized") or 0) - 1.0)
           < 1e-6,
           str(r.get("normalized")))
    record("RT-01/02 洪流护栏约束(Redis 态)",
           r.get("guardOk") is True,
           str(r.get("guardOk")))
    record("手动晋升(promotedVersion)",
           bool(r.get("promotedVersion")),
           str(r.get("promotedVersion")))
    record("回滚(权重还原和=1.0)",
           abs((r.get("rbWeights") or 0) - 1.0)
           < 1e-6
           and r.get("rollbackTarget"),
           str((r.get("rollbackTarget"),
                r.get("rbWeights"))))

    # ⑤ 看板(Redis 态)
    record("看板五区齐备",
           r.get("zoneKeys") == [
               "defense", "drift", "factors",
               "feedback", "version"],
           str(r.get("zoneKeys")))
    record("看板版本区(回滚后冠军)",
           bool(r.get("champion")),
           str(r.get("champion")))
    fb_src = r.get("fbBySource") or {}
    record("看板回流区(retention_dwell 呈现)",
           fb_src.get("retention_dwell", 0) >= 10,
           str(fb_src))
    record("看板防御区护栏健康",
           r.get("guardHealthy") is True,
           str(r.get("guardHealthy")))
    record("RT-04 洪流集中度告警(Redis 态)",
           r.get("concAlert") is True
           and r.get("concTop") == "attacker_flood"
           and (r.get("concRatio") or 0) > 0.8,
           str((r.get("concTop"),
                r.get("concRatio"),
                r.get("concAlert"))))

    print("\n[06 HTTP 端点+鉴权]")
    ok, (code, _) = call("GET", "/api/login54/dashboard")
    record("dashboard 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/login54/model/learn", headers=ADMIN,
        expect=(200, 409))
    record("learn 语义正确(200/409)",
           code in (200, 409), str(code))

    print("\n[07 HTTP 回流幂等]")
    ok, (code, body) = call(
        "POST", "/api/login54/feedback/collect",
        headers=ADMIN)
    labeled = (body or {}).get("labeled")
    record("HTTP collect 幂等(已标注跳过)",
           code == 200 and labeled == 0,
           str((code, labeled)))


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
