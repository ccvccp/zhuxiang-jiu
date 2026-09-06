"""56号AI智能升级管理 P4 Docker 实机验收

运行方式:
    python verify_aiup56_p4_live.py [基址]

前置: 容器已运行(含 56号 P0-P4 代码)。

覆盖(56号计划 §九 P4, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律+终审铁律(deliver off 亦可用)
    03 容器内: 全链→审批→交付(资产包 Redis
       序列化读回)→语义回滚+45号 L2 补偿
    04 容器内: 决策回流(七类信号+44号池
       双写+幂等)
    05 宪法: 44号 ≥31 档案保持
    06 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造)。
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}

# 运行唯一令牌(补偿对象摘要防重——跨次验收零冲突)
RUN_TOKEN = str(int(time.time()))

CONTAINER = "zhuxiang-jiu-backend-1"
REDIS = "zhuxiang-jiu-redis-1"

ALL_CONFIRMATIONS = (
    "readAuditReport", "reviewedSandbox",
    "acknowledgedRollback", "acknowledgedBudget")


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


def clear_p4_members() -> None:
    """清理上一轮 P4 验收遗留的补偿对象档案
    (按名称精确匹配——重复建档拒绝防御)"""
    out = subprocess.run(
        ["docker", "exec", REDIS, "redis-cli",
         "--scan", "--pattern",
         "zhuxiang:trust45:trust45_profiles:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for k in keys:
        o = subprocess.run(
            ["docker", "exec", REDIS, "redis-cli",
             "HGET", k, "name"],
            capture_output=True, text=True)
        if (o.stdout or "").strip() == "AIUP56-P4-MEMBER":
            d = subprocess.run(
                ["docker", "exec", REDIS, "redis-cli",
                 "HGET", k, "idDigest"],
                capture_output=True, text=True)
            digest = (d.stdout or "").strip()
            subprocess.run(
                ["docker", "exec", REDIS, "redis-cli",
                 "DEL", k,
                 f"zhuxiang:trust45:idmap:{digest}"],
                capture_output=True, text=True)


def clear_aiup56(round_no: int) -> None:
    redis_del_keys("zhuxiang:aiup56:*")
    redis_del_keys("zhuxiang:qr55:model_events*")
    redis_del_keys(
        "zhuxiang:qr55:qr55_model_events:*")
    redis_del_keys("zhuxiang:qr55:model_events_all")
    # 44号池(第31档案反馈——计数核对口径)
    redis_del_keys(
        "zhuxiang:ai_learning:feedback:"
        "upgrade_orchestration")
    # 45号补偿对象档案(遗留清理——摘要唯一防重)
    clear_p4_members()


def container_pipeline(round_no: int) -> dict:
    """容器内: 审批→交付→回滚→回流(Redis 态)"""
    script = (
        "import asyncio, json, os\n"
        "os.environ['AIUP56_MODE'] = 'shadow'\n"
        "os.environ['LLM_ENABLED'] = 'off'\n"
        "from core.helpers import ts\n"
        "from repositories.qr55_repository import "
        "Qr55Repository\n"
        "from repositories.aiup56_repository import "
        "Aiup56Repository\n"
        "from repositories.ai_learning_repository "
        "import AiLearningRepository\n"
        "from services.aiup56_service import "
        "Aiup56Service\n"
        "from services.aiup56_plan_service import "
        "Aiup56PlanService\n"
        "from services.aiup56_code_service import "
        "Aiup56CodeService\n"
        "from services.aiup56_test_service import "
        "Aiup56TestService\n"
        "from services.aiup56_audit_service import "
        "Aiup56AuditService\n"
        "from services.aiup56_review_service import "
        "Aiup56ReviewService\n"
        "from services.aiup56_deliver_service import "
        "Aiup56DeliverService\n"
        "from services.aiup56_feedback_service import "
        "Aiup56FeedbackService\n"
        "from services.trust_scoring_service import "
        "TrustProfileService\n"
        "CONFIRM = ['readAuditReport',\n"
        "           'reviewedSandbox',\n"
        "           'acknowledgedRollback',\n"
        "           'acknowledgedBudget']\n"
        "async def m():\n"
        "    out = {}\n"
        # ① 全链到 approved
        "    repo55 = Qr55Repository()\n"
        "    for snap in (\n"
        "            {'satisfactionScore': 80.0,\n"
        "             'clarifyEfficiency': 0.8,\n"
        "             'penetrationRate': 0.7},\n"
        "            {'satisfactionScore': 60.0,\n"
        "             'clarifyEfficiency': 0.5,\n"
        "             'penetrationRate': 0.4}):\n"
        "        meid = await repo55."
        "next_model_event_id()\n"
        "        await repo55.save_model_event({\n"
        "            'modelEventId': meid,\n"
        "            'eventType': "
        "'metrics_snapshot',\n"
        "            'detail': {'metrics': snap},\n"
        "            'createdAt': ts()})\n"
        "    r = await Aiup56Service()."
        "evaluate_and_propose()\n"
        "    pid = r.get('proposalId')\n"
        "    out['proposalId'] = pid\n"
        "    await Aiup56PlanService().plan(pid)\n"
        "    await Aiup56CodeService().code(pid)\n"
        "    await Aiup56TestService().test(pid)\n"
        "    await Aiup56AuditService().audit(pid)\n"
        "    rv = await Aiup56ReviewService().review(\n"
        "        pid, reviewer='admin',\n"
        "        approved=True,\n"
        "        confirmations=CONFIRM,\n"
        "        note='live-verify-p4')\n"
        "    out['reviewStatus'] = "
        "rv.get('status')\n"
        # ② 交付(versioned 出口)
        "    d = await Aiup56DeliverService()."
        "deliver(pid)\n"
        "    out['deliverStatus'] = "
        "d.get('status')\n"
        "    pkg = d.get('package') or {}\n"
        "    out['pkgAssetId'] = "
        "pkg.get('assetId')\n"
        "    out['pkgDrafts'] = len("
        "pkg.get('drafts') or [])\n"
        "    out['pkgSandbox'] = "
        "pkg.get('sandboxVerdict')\n"
        # ③ Redis 序列化读回验证(交付包结构)
        "    stored = await Aiup56Repository()"
        ".get_proposal(pid)\n"
        "    pkg_back = (stored.get("
        "'deliveryPackage') or {})\n"
        "    out['storedStatus'] = "
        "stored.get('status')\n"
        "    out['pkgBackIsDict'] = isinstance(\n"
        "        stored.get('deliveryPackage'), dict)\n"
        "    out['pkgBackDrafts'] = len(\n"
        "        pkg_back.get('drafts') or [])\n"
        # ④ 语义回滚+45号补偿
        "    member = await TrustProfileService()"
        ".create_role(\n"
        "        'person', 'AIUP56-P4-MEMBER',\n"
        "        'ID-AIUP56-" + RUN_TOKEN
        + "-R" + str(round_no) + "')\n"
        "    member_id = member.get('trustId')\n"
        "    rb = await Aiup56DeliverService()"
        ".rollback(\n"
        "        pid, reason='灰度指标异常',\n"
        "        affected_members=[member_id, 99999])\n"
        "    out['rollbackStatus'] = "
        "rb.get('status')\n"
        "    comp = rb.get('compensation') or {}\n"
        "    out['compensated'] = "
        "comp.get('compensated')\n"
        "    out['compSkipped'] = "
        "comp.get('skipped')\n"
        "    steps = rb.get('steps') or []\n"
        "    out['rollbackSteps'] = len(steps)\n"
        "    out['stepsAllExecuted'] = all(\n"
        "        s.get('executed') is True\n"
        "        for s in steps)\n"
        # ⑤ 决策回流(rolled_back → -0.8)
        "    fb = Aiup56FeedbackService()\n"
        "    c1 = await fb.collect_feedback()\n"
        "    out['collectLabeled'] = "
        "c1.get('labeled')\n"
        "    out['collectSignals'] = "
        "c1.get('signals') or {}\n"
        # ⑥ 44号池双写核对
        "    pool = await AiLearningRepository()"
        ".list_feedback(\n"
        "        'upgrade_orchestration')\n"
        "    out['poolCount'] = len(pool)\n"
        "    out['poolRewards'] = sorted(\n"
        "        float(x.get('reward') or 0)\n"
        "        for x in pool)\n"
        "    out['poolSources'] = sorted(\n"
        "        {x.get('source') for x in pool})\n"
        # ⑦ 幂等(重复补标跳过)
        "    c2 = await fb.collect_feedback()\n"
        "    out['idempotentLabeled'] = "
        "c2.get('labeled')\n"
        "    out['idempotentSkipped'] = "
        "c2.get('skipped')\n"
        # ⑧ 提案回写+事件链
        "    p = await Aiup56Repository()"
        ".get_proposal(pid)\n"
        "    out['pooledId'] = p.get("
        "'pooledFeedbackId')\n"
        "    out['poolSignal'] = "
        "p.get('poolSignal')\n"
        "    out['poolReward'] = "
        "p.get('poolReward')\n"
        "    out['compBack'] = isinstance(\n"
        "        p.get('compensation'), dict)\n"
        "    events = await Aiup56Repository()"
        ".list_events(pid, limit=100)\n"
        "    types = {e.get('eventType')\n"
        "             for e in events}\n"
        "    out['eventTypes'] = sorted(types)\n"
        # ⑨ 宪法
        "    from services.ai_learning_service "
        "import SCORER_REGISTRY\n"
        "    out['scorerCount'] = "
        "len(SCORER_REGISTRY)\n"
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
    clear_aiup56(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+终审铁律]")
    ok, (code, _) = call(
        "POST", "/api/aiup56/proposals/1/audit",
        headers=ADMIN, expect=(409,))
    record("off 态 audit 409", code == 409, str(code))
    # deliver off 亦可用: 404(不存在)——非 off 语义,
    # 证明交付链人工动作不受开关影响
    ok, (code, body) = call(
        "POST", "/api/aiup56/proposals/1/deliver",
        headers=ADMIN, expect=(404,))
    detail = str((body or {}).get("detail")
                 or (body or {}).get("error") or "")
    record("deliver off 亦可用(404 非 off)",
           code == 404 and "off" not in detail,
           str((code, detail[:50])))

    print("\n[03-05 容器内: 交付→回滚→回流→宪法]")
    r = container_pipeline(round_no)

    record("审批(approved)",
           r.get("reviewStatus") == "approved",
           str(r.get("reviewStatus")))
    record("交付(delivered+versioned 资产)",
           r.get("deliverStatus") == "delivered"
           and r.get("pkgAssetId")
           and r.get("pkgDrafts", 0) >= 1
           and r.get("pkgSandbox") == "passed",
           str((r.get("deliverStatus"),
                r.get("pkgAssetId"),
                r.get("pkgSandbox"))))
    record("Redis 序列化读回(交付包结构)",
           r.get("pkgBackIsDict") is True
           and r.get("pkgBackDrafts", 0) >= 1
           and r.get("storedStatus") == "delivered",
           str((r.get("pkgBackIsDict"),
                r.get("pkgBackDrafts"),
                r.get("storedStatus"))))
    record("语义回滚(rolled_back+分步留痕)",
           r.get("rollbackStatus") == "rolled_back"
           and r.get("rollbackSteps", 0) >= 1
           and r.get("stepsAllExecuted") is True,
           str((r.get("rollbackStatus"),
                r.get("rollbackSteps"))))
    record("45号补偿(成功 1+跳过 1)",
           r.get("compensated") == 1
           and r.get("compSkipped") == 1,
           str((r.get("compensated"),
                r.get("compSkipped"))))
    record("回流标注(rolled_back→-0.8)",
           r.get("collectLabeled") == 1
           and (r.get("collectSignals") or {})
           .get("rollback_after_deliver") == 1,
           str((r.get("collectLabeled"),
                r.get("collectSignals"))))
    record("44号池双写(1 条+来源)",
           r.get("poolCount") == 1
           and r.get("poolRewards") == [-0.8]
           and r.get("poolSources")
           == ["aiup56_pipeline"],
           str((r.get("poolCount"),
                r.get("poolRewards"),
                r.get("poolSources"))))
    record("幂等(重复补标跳过)",
           r.get("idempotentLabeled") == 0
           and r.get("idempotentSkipped") == 1,
           str((r.get("idempotentLabeled"),
                r.get("idempotentSkipped"))))
    record("提案回写(pooled+信号+奖励)",
           int(r.get("pooledId") or 0) > 0
           and r.get("poolSignal")
           == "rollback_after_deliver"
           and r.get("poolReward") == -0.8,
           str((r.get("pooledId"),
                r.get("poolSignal"),
                r.get("poolReward"))))
    record("回滚补偿仓储留痕(结构)",
           r.get("compBack") is True,
           str(r.get("compBack")))
    record("全链事件(create→…→approve→deliver"
           "→rollback→learn_signal)",
           {"proposal_create", "plan", "code",
            "test", "audit", "approve", "deliver",
            "rollback", "learn_signal"} <= set(
               r.get("eventTypes") or []),
           str(r.get("eventTypes")))
    record("44号 ≥31 档案保持",
           r.get("scorerCount") >= 31,
           str(r.get("scorerCount")))

    print("\n[06 HTTP 端点+鉴权]")
    ok, (code, body) = call(
        "GET", "/api/aiup56/feedback/stats",
        headers=ADMIN)
    record("HTTP stats 观测面",
           code == 200
           and (body.get("bySignal") or {})
           .get("rollback_after_deliver") == 1
           and body.get("poolSubmitted") == 1,
           str((code, body.get("bySignal"))))
    ok, (code, body) = call(
        "POST", "/api/aiup56/feedback/collect",
        headers=ADMIN)
    record("HTTP collect 幂等(0 标注)",
           code == 200
           and body.get("labeled") == 0,
           str((code, body.get("labeled"))))
    ok, (code, _) = call(
        "POST", "/api/aiup56/proposals/1/rollback",
        body={"reason": "x"})
    record("HTTP rollback 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/aiup56/proposals/1/deliver")
    record("HTTP deliver 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/aiup56/feedback/collect")
    record("HTTP collect 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "GET", "/api/aiup56/feedback/stats")
    record("HTTP stats 无 Role 403",
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
