"""55号二维码AI智能管理 P3 Docker 实机验收

运行方式:
    python verify_qr55_p3_live.py [基址]

前置: 容器已运行(含 55号 P0-P3 代码)。

覆盖(55号计划 §六 P3, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律+观测面(generate 409;
       model/status 就绪态并入)
    03 容器内: 学习闭环种子(生成→扫码→完成 ×10
       会员→collect 回流→44号池 10 条 pending)
    04 容器内: 学习轮次(Hedge→challenger+
       权重归一化+护栏)+就绪态+影子对比
    05 容器内: 手动晋升(基线留痕)
    06 容器内: 回归检测-正常(指标未回退)
    07 容器内: 红队——负 reward 洪流→回归检测
       命中→自动回滚+46号冻结+事件留痕
    08 容器内: 冻结守卫(learn ValueError)
    09 HTTP 端点+鉴权

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
    """清理种子(qr55 全表+44号池档案/反馈/历史+
    46号治理态+轮内种子会员)"""
    base = 9930 + round_no * 10
    redis_del_keys("zhuxiang:qr55:*")
    redis_del_keys("zhuxiang:ai_learning:*")
    redis_del_keys("zhuxiang:ai46:*")
    for mid in range(base, base + 15):
        redis_del_keys(
            f"zhuxiang:trust45:trust45_profiles:{mid}")
        redis_del_keys(
            f"zhuxiang:trust45:idmap:seed-digest-{mid}")
        redis_del_keys(
            "zhuxiang:voice48:voice48_privacy_budget:"
            f"{mid}")


def container_pipeline(round_no: int) -> dict:
    """容器内: 种子→学习→晋升→回归→红队(Redis 态)"""
    base_member = 9930 + round_no * 10
    script = (
        "import asyncio, json, os\n"
        "os.environ['QR55_MODE'] = 'on'\n"
        "os.environ['LLM_ENABLED'] = 'off'\n"
        "from repositories.trust_value_repository "
        "import TrustValue45Repository\n"
        "from repositories.qr55_repository "
        "import Qr55Repository\n"
        "from repositories.ai_learning_repository "
        "import AiLearningRepository\n"
        "from services.qr55_generate_service import "
        "Qr55GenerateService\n"
        "from services.qr55_scan_service import "
        "Qr55ScanService\n"
        "from services.qr55_service import "
        "Qr55Service\n"
        "from services.qr55_feedback_service import "
        "Qr55FeedbackService\n"
        "from services.qr55_learn_service import "
        "Qr55LearnService\n"
        "from services.qr55_scorer import Qr55Scorer\n"
        "from core.helpers import ts\n"
        f"BASE_M = {base_member}\n"
        "async def m():\n"
        "    out = {}\n"
        # ① 种子: 45号档案 ×12(完成链 ×10 + 洪流 2)
        "    trepo = TrustValue45Repository()\n"
        "    for mid in range(BASE_M, BASE_M + 12):\n"
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
        # ② 学习闭环种子: 生成→扫码→完成 ×10
        "    gen = Qr55GenerateService()\n"
        "    scan = Qr55ScanService()\n"
        "    svc = Qr55Service()\n"
        "    for i in range(10):\n"
        "        mid = BASE_M + i\n"
        "        g = await gen.orchestrate(\n"
        "            mid, '查政策解读')\n"
        "        await scan.scan(g['code'],\n"
        "                        member_id=mid)\n"
        "        await svc.record_completion(\n"
        "            g['codeId'])\n"
        # ③ collect 回流(44号池 10 条 pending)
        "    os.environ['QR55_MODE'] = 'off'\n"
        "    c = await Qr55FeedbackService()"
        ".collect_feedback()\n"
        "    out['collectLabeled'] = c.get('labeled')\n"
        "    out['collectSettled'] = c.get('settled')\n"
        # ④ 学习轮次
        "    learn = Qr55LearnService()\n"
        "    ready = await learn.learning_readiness()\n"
        "    out['readyPending'] = ready.get(\n"
        "        'pendingFeedback')\n"
        "    out['ready'] = ready.get('ready')\n"
        "    lr = await learn.run_learning()\n"
        "    out['learnedFrom'] = lr.get('learnedFrom')\n"
        "    out['newVersion'] = lr.get('newVersion')\n"
        # 权重归一化+护栏
        "    from services.ai_learning_service import "
        "get_weights_view\n"
        "    view = await get_weights_view(\n"
        "        'qr_orchestration')\n"
        "    ch = ((view.get('challenger') or {})\n"
        "          .get('weights')) or {}\n"
        "    out['normalized'] = round(\n"
        "        sum(ch.values()), 6)\n"
        "    base_w = {'intent_confidence': 0.15,\n"
        "              'service_match': 0.15,\n"
        "              'template_fit': 0.10,\n"
        "              'budget_sufficiency': 0.15,\n"
        "              'member_trust': 0.15,\n"
        "              'expiry_freshness': 0.10,\n"
        "              'accessibility_need': 0.10,\n"
        "              'risk_posture': 0.10}\n"
        "    out['guardOk'] = all(\n"
        "        base_w[k] / 2.0 <= ch.get(k, 0)\n"
        "        <= base_w[k] * 2.0\n"
        "        for k in base_w) if ch else False\n"
        # ⑤ 影子对比
        "    ctx = {'intentConfidence': 0.9,\n"
        "           'serviceMatch': 'resolved',\n"
        "           'paramComplete': 1.0,\n"
        "           'budgetRemaining': 0.9,\n"
        "           'memberTrustLevel': 'L3',\n"
        "           'freshRatio': 1.0,\n"
        "           'accessibility': False,\n"
        "           'riskFlagged': False}\n"
        "    sc = await learn.shadow_compare(ctx)\n"
        "    out['shadowChampion'] = (\n"
        "        sc.get('champion') or {}).get(\n"
        "            'result', {}).get('strategy')\n"
        "    out['shadowCompare'] = bool(\n"
        "        sc.get('comparison'))\n"
        # ⑥ 手动晋升(基线留痕)
        "    promo = await learn.promote()\n"
        "    out['promotedVersion'] = promo.get(\n"
        "        'promotedVersion')\n"
        # ⑦ 回归检测-正常(补充 5 条强反馈)
        "    pool = AiLearningRepository()\n"
        "    strong = await Qr55Scorer().score(ctx)\n"
        "    for i in range(5):\n"
        "        await pool.add_feedback({\n"
        "            'scorerId': "
        "'qr_orchestration',\n"
        "            'weightVersion': 'v1',\n"
        "            'scoreAtDecision': strong.get(\n"
        "                'trustScore'),\n"
        "            'actualAction': 'direct',\n"
        "            'expectedAction': 'direct',\n"
        "            'correct': True,\n"
        "            'factors': strong.get('factors'),\n"
        "            'reward': 1.0,\n"
        "            'note': f'live-strong:{i}',\n"
        "            'source': 'qr55_pipeline',\n"
        "            'status': 'pending',\n"
        "            'createdAt': ts()})\n"
        "    rg = await learn.check_regression()\n"
        "    out['rgNormalApplicable'] = "
        "rg.get('applicable')\n"
        "    out['rgNormalRegressed'] = "
        "rg.get('regressed')\n"
        # ⑧ 红队: 负 reward 洪流 10 条弱反馈
        "    weak_ctx = {'intentConfidence': 0.1,\n"
        "                'serviceMatch': 'clarify',\n"
        "                'paramComplete': 0.2,\n"
        "                'budgetRemaining': 0.05,\n"
        "                'memberTrustLevel': 'L0',\n"
        "                'freshRatio': 0.1,\n"
        "                'accessibility': False,\n"
        "                'riskFlagged': True}\n"
        "    weak = await Qr55Scorer().score(weak_ctx)\n"
        "    for i in range(10):\n"
        "        await pool.add_feedback({\n"
        "            'scorerId': "
        "'qr_orchestration',\n"
        "            'weightVersion': 'v1',\n"
        "            'scoreAtDecision': weak.get(\n"
        "                'trustScore'),\n"
        "            'actualAction': 'clarify',\n"
        "            'expectedAction': 'direct',\n"
        "            'correct': False,\n"
        "            'factors': weak.get('factors'),\n"
        "            'reward': -1.0,\n"
        "            'note': f'live-flood:{i}',\n"
        "            'source': 'attacker_flood',\n"
        "            'status': 'pending',\n"
        "            'createdAt': ts()})\n"
        "    rg2 = await learn.check_regression()\n"
        "    out['rgFloodRegressed'] = "
        "rg2.get('regressed')\n"
        "    out['rgFloodDrop'] = rg2.get('drop')\n"
        "    out['rgFloodAction'] = rg2.get('action')\n"
        "    out['rgFloodFrozen'] = (\n"
        "        (rg2.get('freeze') or {})\n"
        "        .get('frozen'))\n"
        "    out['rgRollbackTo'] = (\n"
        "        (rg2.get('rollback') or {})\n"
        "        .get('newVersion'))\n"
        # ⑨ 冻结守卫: learn → ValueError
        "    try:\n"
        "        await learn.run_learning()\n"
        "        out['frozenLearnRejected'] = False\n"
        "    except ValueError as e:\n"
        "        out['frozenLearnRejected'] = "
        "'冻结' in str(e)\n"
        # ⑩ 模型事件留痕
        "    events = await Qr55Repository()."
        "list_model_events(limit=100)\n"
        "    types = {e.get('eventType')\n"
        "             for e in events}\n"
        "    out['modelEventTypes'] = sorted(types)\n"
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
        body={"memberId": 9931,
              "text": "办老年优待证"},
        headers=ADMIN, expect=(409,))
    record("off 态 generate 409", code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/qr55/model/status", headers=ADMIN)
    readiness = ((body or {}).get("status")
                 or {}).get("readiness") or {}
    record("model/status 就绪态并入",
           code == 200 and "ready" in readiness
           and "pendingFeedback" in readiness,
           str(readiness))
    ok, (code, _) = call(
        "POST", "/api/qr55/model/learn", headers=ADMIN,
        expect=(409,))
    record("learn 门槛不足 409(off 亦可用)",
           code == 409, str(code))

    print("\n[03-08 容器内: 学习→晋升→回归→红队]")
    r = container_pipeline(round_no)

    # ③ 回流种子(10 会员各 1 完成——按会员聚合
    #    10 次 deposit 结算)
    record("回流种子(10 条 scan_completed+10 结算)",
           r.get("collectLabeled") == 10
           and r.get("collectSettled") == 10,
           str((r.get("collectLabeled"),
                r.get("collectSettled"))))

    # ④ 学习轮次
    record("就绪态(pending=10 ready)",
           r.get("readyPending") == 10
           and r.get("ready") is True,
           str((r.get("readyPending"),
                r.get("ready"))))
    record("Hedge 学习(learnedFrom≥10)",
           (r.get("learnedFrom") or 0) >= 10
           and bool(r.get("newVersion")),
           str((r.get("learnedFrom"),
                r.get("newVersion"))))
    record("权重归一化(和=1.0)",
           abs((r.get("normalized") or 0) - 1.0)
           < 1e-6,
           str(r.get("normalized")))
    record("护栏 [0.5,2.0] 倍(QC-1)",
           r.get("guardOk") is True,
           str(r.get("guardOk")))
    record("影子对比(champion direct 轨)",
           r.get("shadowChampion") == "direct"
           and r.get("shadowCompare") is True,
           str((r.get("shadowChampion"),
                r.get("shadowCompare"))))

    # ⑥ 晋升
    record("手动晋升(promotedVersion)",
           bool(r.get("promotedVersion")),
           str(r.get("promotedVersion")))

    # ⑦ 回归检测-正常
    record("回归-正常(未回退)",
           r.get("rgNormalApplicable") is True
           and r.get("rgNormalRegressed") is False,
           str((r.get("rgNormalApplicable"),
                r.get("rgNormalRegressed"))))

    # ⑧ 红队: 负 reward 洪流
    record("红队洪流→回退命中",
           r.get("rgFloodRegressed") is True
           and (r.get("rgFloodDrop") or 0) > 0.3,
           str((r.get("rgFloodRegressed"),
                r.get("rgFloodDrop"))))
    record("自动回滚(action+新版本)",
           r.get("rgFloodAction") == "auto_rollback"
           and bool(r.get("rgRollbackTo")),
           str((r.get("rgFloodAction"),
                r.get("rgRollbackTo"))))
    record("46号冻结联动",
           r.get("rgFloodFrozen") is True,
           str(r.get("rgFloodFrozen")))

    # ⑨ 冻结守卫
    record("冻结守卫(learn ValueError)",
           r.get("frozenLearnRejected") is True,
           str(r.get("frozenLearnRejected")))

    # ⑩ 模型事件留痕
    record("模型事件留痕(learning/promoted/"
           "regression_rollback)",
           {"learning", "promoted",
            "regression_rollback"} <= set(
               r.get("modelEventTypes") or []),
           str(r.get("modelEventTypes")))

    print("\n[09 HTTP 端点+鉴权]")
    ok, (code, _) = call(
        "POST", "/api/qr55/model/promote", headers=ADMIN,
        expect=(200, 409))
    record("HTTP promote 语义正确(200/409)",
           code in (200, 409), str(code))
    ok, (code, _) = call(
        "POST", "/api/qr55/model/rollback",
        headers=ADMIN, expect=(200, 409))
    record("HTTP rollback 语义正确(200/409)",
           code in (200, 409), str(code))
    ok, (code, _) = call(
        "POST", "/api/qr55/model/learn")
    record("learn 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/qr55/model/rollback")
    record("rollback 无 Role 403",
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
