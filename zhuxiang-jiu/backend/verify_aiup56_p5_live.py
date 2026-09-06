"""56号AI智能升级管理 P5 Docker 实机验收

运行方式:
    python verify_aiup56_p5_live.py [基址]

前置: 容器已运行(含 56号 P0-P5 代码)。

覆盖(56号计划 §九 P5, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(dashboard 观测面/redteam 409)
    03 容器内: 四区看板(全链种子后四区数字)
    04 容器内: 红队六向量(全防御+注册表恢复)
    05 宪法: 44号 31 档案保持
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


def container_pipeline(round_no: int) -> dict:
    """容器内: 全链种子→回流→看板→红队(Redis 态)"""
    script = (
        "import asyncio, json, os\n"
        "os.environ['AIUP56_MODE'] = 'shadow'\n"
        "os.environ['LLM_ENABLED'] = 'off'\n"
        "from core.helpers import ts\n"
        "from repositories.qr55_repository import "
        "Qr55Repository\n"
        "from repositories.aiup56_repository import "
        "Aiup56Repository\n"
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
        "from services.aiup56_dashboard_service import "
        "Aiup56DashboardService\n"
        "from services.aiup56_redteam_service import "
        "Aiup56RedteamService\n"
        "CONFIRM = ['readAuditReport',\n"
        "           'reviewedSandbox',\n"
        "           'acknowledgedRollback',\n"
        "           'acknowledgedBudget']\n"
        "async def m():\n"
        "    out = {}\n"
        # ① 全链种子×2(1 交付+1 回滚)
        "    async def chain(rollback):\n"
        "        repo55 = Qr55Repository()\n"
        "        for snap in (\n"
        "                {'satisfactionScore': 80.0,\n"
        "                 'clarifyEfficiency': 0.8,\n"
        "                 'penetrationRate': 0.7},\n"
        "                {'satisfactionScore': 60.0,\n"
        "                 'clarifyEfficiency': 0.5,\n"
        "                 'penetrationRate': 0.4}):\n"
        "            meid = await repo55."
        "next_model_event_id()\n"
        "            await repo55.save_model_event({\n"
        "                'modelEventId': meid,\n"
        "                'eventType': "
        "'metrics_snapshot',\n"
        "                'detail': {'metrics': snap},\n"
        "                'createdAt': ts()})\n"
        "        r = await Aiup56Service()."
        "evaluate_and_propose()\n"
        "        pid = r.get('proposalId')\n"
        "        await Aiup56PlanService().plan(pid)\n"
        "        await Aiup56CodeService().code(pid)\n"
        "        await Aiup56TestService().test(pid)\n"
        "        await Aiup56AuditService().audit(pid)\n"
        "        await Aiup56ReviewService().review(\n"
        "            pid, reviewer='admin',\n"
        "            approved=True,\n"
        "            confirmations=CONFIRM,\n"
        "            note='live-p5')\n"
        "        await Aiup56DeliverService()."
        "deliver(pid)\n"
        "        if rollback:\n"
        "            await Aiup56DeliverService()"
        ".rollback(\n"
        "                pid, reason='live-p5-回滚')\n"
        "        return pid\n"
        "    await chain(False)\n"
        "    await chain(True)\n"
        # ② 回流补标(2 信号入池)
        "    c = await Aiup56FeedbackService()"
        ".collect_feedback()\n"
        "    out['collectLabeled'] = "
        "c.get('labeled')\n"
        # ③ 四区看板
        "    board = await Aiup56DashboardService()"
        ".build()\n"
        "    zones = board.get('zones') or {}\n"
        "    funnel = zones.get('funnel') or {}\n"
        "    out['funnelTotal'] = funnel.get('total')\n"
        "    out['funnelDelivered'] = (\n"
        "        funnel.get('conversion') or {})"
        ".get('delivered')\n"
        "    out['funnelRolledBack'] = (\n"
        "        funnel.get('conversion') or {})"
        ".get('rolledBack')\n"
        "    out['funnelDecisions'] = (\n"
        "        funnel.get('decisions') or {})"
        ".get('propose')\n"
        "    assets = zones.get('assets') or {}\n"
        "    out['assetsTotal'] = assets.get("
        "'totalAssets')\n"
        "    out['sandboxPassed'] = (\n"
        "        assets.get('sandboxByVerdict') "
        "or {}).get('passed')\n"
        "    comp = zones.get('compliance') or {}\n"
        "    out['auditPassed'] = (\n"
        "        comp.get('auditVerdicts') or {})"
        ".get('passed')\n"
        "    out['confirmRate'] = comp.get("
        "'confirmationCompleteRate')\n"
        "    defense = zones.get('defense') or {}\n"
        "    out['rollbacks'] = defense.get("
        "'rollbacks')\n"
        "    out['defenseSignals'] = (\n"
        "        defense.get('feedbackSignals') "
        "or {}).get('bySignal')\n"
        "    out['guardHealthy'] = (\n"
        "        defense.get('guardrail') or {})"
        ".get('healthy')\n"
        # ④ 红队六向量
        "    rt = await Aiup56RedteamService()"
        ".run_all()\n"
        "    out['rtTotal'] = (\n"
        "        rt.get('summary') or {})"
        ".get('total')\n"
        "    out['rtDefended'] = (\n"
        "        rt.get('summary') or {})"
        ".get('defended')\n"
        "    out['rtAllDefended'] = (\n"
        "        rt.get('summary') or {})"
        ".get('allDefended')\n"
        # ⑤ 红队后注册表完整+宪法
        "    from services.aiup56_registry "
        "import SIGNAL_REGISTRY\n"
        "    out['registryCount'] = "
        "len(SIGNAL_REGISTRY)\n"
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

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/aiup56/proposals/1/audit",
        headers=ADMIN, expect=(409,))
    record("off 态 audit 409", code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/aiup56/dashboard",
        headers=ADMIN)
    record("off 态 dashboard 观测面 200",
           code == 200
           and (body.get("zones") or {})
           .get("funnel") is not None,
           str(code))
    ok, (code, _) = call(
        "POST", "/api/aiup56/redteam",
        headers=ADMIN, expect=(409,))
    record("off 态 redteam 409(无攻击面)",
           code == 409, str(code))

    print("\n[03-05 容器内: 看板→红队→宪法]")
    r = container_pipeline(round_no)

    record("回流补标(2 信号入池)",
           r.get("collectLabeled") == 2,
           str(r.get("collectLabeled")))
    record("漏斗区(2 提案+决策 propose×2)",
           r.get("funnelTotal") == 2
           and r.get("funnelDecisions") == 2,
           str((r.get("funnelTotal"),
                r.get("funnelDecisions"))))
    record("漏斗转化(曾交付 1.0+回滚 0.5)",
           r.get("funnelDelivered") == 1.0
           and r.get("funnelRolledBack") == 0.5,
           str((r.get("funnelDelivered"),
                r.get("funnelRolledBack"))))
    record("资产区(2 资产+沙箱 passed×2)",
           r.get("assetsTotal") == 2
           and r.get("sandboxPassed") == 2,
           str((r.get("assetsTotal"),
                r.get("sandboxPassed"))))
    record("合规区(审计 passed×2+确认完整率)",
           r.get("auditPassed") == 2
           and r.get("confirmRate") == 1.0,
           str((r.get("auditPassed"),
                r.get("confirmRate"))))
    record("防御区(回滚 1+双信号分布)",
           r.get("rollbacks") == 1
           and (r.get("defenseSignals") or {})
           .get("rollback_after_deliver") == 1
           and (r.get("defenseSignals") or {})
           .get("deliver_success") == 1,
           str((r.get("rollbacks"),
                r.get("defenseSignals"))))
    record("防御区(护栏健康第31档案)",
           r.get("guardHealthy") is True,
           str(r.get("guardHealthy")))
    record("红队六向量全防御",
           r.get("rtTotal") == 6
           and r.get("rtDefended") == 6
           and r.get("rtAllDefended") is True,
           str((r.get("rtTotal"),
                r.get("rtDefended"),
                r.get("rtAllDefended"))))
    record("红队后注册表完整(10 项)",
           r.get("registryCount") == 10,
           str(r.get("registryCount")))
    record("44号 31 档案保持",
           r.get("scorerCount") == 31,
           str(r.get("scorerCount")))

    print("\n[06 HTTP 端点+鉴权]")
    ok, (code, body) = call(
        "GET", "/api/aiup56/dashboard",
        headers=ADMIN)
    zones = (body.get("zones") or {})
    # 2 全链种子+7 红队种子(RT-02 1+RT-03 3
    # +RT-04 1+RT-06 2)=9 提案
    record("HTTP dashboard 200(四区数字 9)",
           code == 200
           and (zones.get("funnel") or {})
           .get("total") == 9,
           str((code,
                (zones.get("funnel") or {})
                .get("total"))))
    ok, (code, body) = call(
        "POST", "/api/aiup56/redteam",
        headers=ADMIN, expect=(409,))
    detail = str(
        (body or {}).get("error")
        or (body or {}).get("detail") or "")
    record("HTTP redteam off 409(服务器态默认 off)",
           code == 409 and "红队" in detail,
           str((code, detail[:40])))
    # 200 全防御路径已由容器内管道验证
    # (shadow 态+Redis 真实存储)
    ok, (code, _) = call(
        "GET", "/api/aiup56/dashboard")
    record("HTTP dashboard 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/aiup56/redteam")
    record("HTTP redteam 无 Role 403",
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
