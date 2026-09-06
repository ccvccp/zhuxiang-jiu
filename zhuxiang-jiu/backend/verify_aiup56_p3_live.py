"""56号AI智能升级管理 P3 Docker 实机验收

运行方式:
    python verify_aiup56_p3_live.py [基址]

前置: 容器已运行(含 56号 P0-P3 代码)。

覆盖(56号计划 §九 P3, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(audit 409; review off 亦可用)
    03 容器内: 全链 draft→…→audited(三重校验
       +一票否决 mock 归因)
    04 容器内: 审批面板(强制确认+双人复核
       +批准/驳回流)
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


def container_pipeline(round_no: int) -> dict:
    """容器内: 全链到审批(Redis 态)"""
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
        "CONFIRM = ['readAuditReport',\n"
        "           'reviewedSandbox',\n"
        "           'acknowledgedRollback',\n"
        "           'acknowledgedBudget']\n"
        "async def m():\n"
        "    out = {}\n"
        # ① 全链到 audited
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
        "    a = await Aiup56AuditService()."
        "audit(pid)\n"
        "    out['auditVerdict'] = a.get('verdict')\n"
        "    out['auditStatus'] = a.get('status')\n"
        "    layers = a.get('layers') or {}\n"
        "    out['layersPassed'] = all(\n"
        "        (layers.get(k) or {}).get('passed')\n"
        "        for k in ('code', 'logic', 'doc'))\n"
        "    out['reportMode'] = "
        "a.get('reportMode')\n"
        # ② 面板视图
        "    panel = await Aiup56ReviewService()."
        "panel(pid)\n"
        "    out['panelConfirmCount'] = len(\n"
        "        panel.get('requiredConfirmations')\n"
        "        or [])\n"
        "    out['panelHasReport'] = bool(\n"
        "        panel.get('auditReport'))\n"
        # ③ 审批拒绝: 确认不齐
        "    rs = Aiup56ReviewService()\n"
        "    try:\n"
        "        await rs.review(\n"
        "            pid, reviewer='admin',\n"
        "            approved=True,\n"
        "            confirmations=['readAuditReport'])\n"
        "        out['incompleteRejected'] = False\n"
        "    except ValueError as e:\n"
        "        out['incompleteRejected'] = (\n"
        "            '确认清单' in str(e))\n"
        # ④ 批准(全勾选)
        "    rv = await rs.review(\n"
        "        pid, reviewer='admin',\n"
        "        approved=True,\n"
        "        confirmations=CONFIRM,\n"
        "        note='live-verify')\n"
        "    out['reviewVerdict'] = "
        "rv.get('verdict')\n"
        "    out['reviewStatus'] = "
        "rv.get('status')\n"
        # ⑤ 驳回流(第二提案)
        "    r2 = await Aiup56Service()."
        "evaluate_and_propose()\n"
        "    pid2 = r2.get('proposalId')\n"
        "    await Aiup56PlanService().plan(pid2)\n"
        "    await Aiup56CodeService().code(pid2)\n"
        "    await Aiup56TestService().test(pid2)\n"
        "    await Aiup56AuditService().audit(pid2)\n"
        "    rv2 = await rs.review(\n"
        "        pid2, reviewer='admin',\n"
        "        approved=False,\n"
        "        confirmations=[],\n"
        "        note='不通过')\n"
        "    out['rejectVerdict'] = "
        "rv2.get('verdict')\n"
        "    out['rejectStatus'] = "
        "rv2.get('status')\n"
        # ⑥ 事件链+宪法
        "    events = await Aiup56Repository()"
        ".list_events(pid, limit=100)\n"
        "    types = {e.get('eventType')\n"
        "             for e in events}\n"
        "    out['eventTypes'] = sorted(types)\n"
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
    # review off 亦可用: 404(不存在)或 409(状态机)
    # ——均非 off 语义, 证明终审铁律不受开关影响
    ok, (code, body) = call(
        "POST", "/api/aiup56/proposals/1/review",
        body={"reviewer": "admin",
              "approved": True,
              "confirmations": []},
        headers=ADMIN, expect=(404, 409))
    detail = str((body or {}).get("detail")
                 or (body or {}).get("error") or "")
    record("review off 亦可用(非 off 语义)",
           code in (404, 409)
           and "off" not in detail,
           str((code, detail[:50])))

    print("\n[03-05 容器内: 审计→面板→审批→宪法]")
    r = container_pipeline(round_no)

    record("审计通过(passed+audited)",
           r.get("auditVerdict") == "passed"
           and r.get("auditStatus") == "audited",
           str((r.get("auditVerdict"),
                r.get("auditStatus"))))
    record("三层全过(代码/逻辑/文档)",
           r.get("layersPassed") is True,
           str(r.get("layersPassed")))
    record("归因报告(mock)",
           r.get("reportMode") == "mock",
           str(r.get("reportMode")))
    record("面板材料(报告+确认清单 4)",
           r.get("panelHasReport") is True
           and r.get("panelConfirmCount") == 4,
           str((r.get("panelHasReport"),
                r.get("panelConfirmCount"))))
    record("确认不齐拒绝(防形式化)",
           r.get("incompleteRejected") is True,
           str(r.get("incompleteRejected")))
    record("批准(approved)",
           r.get("reviewVerdict") == "approved"
           and r.get("reviewStatus") == "approved",
           str((r.get("reviewVerdict"),
                r.get("reviewStatus"))))
    record("驳回(回 planned)",
           r.get("rejectVerdict") == "rejected"
           and r.get("rejectStatus") == "planned",
           str((r.get("rejectVerdict"),
                r.get("rejectStatus"))))
    record("全链事件(create→…→audit→"
           "approve)",
           {"proposal_create", "plan", "code",
            "test", "audit", "approve"} <= set(
               r.get("eventTypes") or []),
           str(r.get("eventTypes")))
    record("44号 31 档案保持",
           r.get("scorerCount") == 31,
           str(r.get("scorerCount")))

    print("\n[06 HTTP 端点+鉴权]")
    ok, (code, _) = call(
        "GET", "/api/aiup56/proposals/1/panel",
        headers=ADMIN)
    record("HTTP panel 观测面",
           code == 200, str(code))
    ok, (code, _) = call(
        "POST", "/api/aiup56/proposals/1/audit")
    record("HTTP audit 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/aiup56/proposals/1/review",
        body={"reviewer": "x"})
    record("HTTP review 无 Role 403",
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
