"""56号AI智能升级管理 P1 Docker 实机验收

运行方式:
    python verify_aiup56_p1_live.py [基址]

前置: 容器已运行(含 56号 P0+P1 代码)。

覆盖(56号计划 §九 P1, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(plan/code 409)
    03 容器内: 全链 draft→planned→coded(规划
       任务拆解/依赖/回滚预案→编码草稿
       /VALUE_REASON/资产版本化)
    04 宪法: 44号 ≥31 档案保持
    05 HTTP 端点+鉴权

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


def clear_aiup56(round_no: int) -> None:
    redis_del_keys("zhuxiang:aiup56:*")
    redis_del_keys("zhuxiang:qr55:model_events*")
    redis_del_keys(
        "zhuxiang:qr55:qr55_model_events:*")
    redis_del_keys("zhuxiang:qr55:model_events_all")


def container_pipeline(round_no: int) -> dict:
    """容器内: 决策→规划→编码(Redis 态)"""
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
        "async def m():\n"
        "    out = {}\n"
        # ① 强信号→提案
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
        # ② 规划
        "    p = await Aiup56PlanService().plan(pid)\n"
        "    out['planStatus'] = p.get('status')\n"
        "    out['planMode'] = p.get('mode')\n"
        "    out['taskCount'] = len(p.get('tasks'))\n"
        "    t0 = (p.get('tasks') or [{}])[0]\n"
        "    out['hasRollback'] = bool(\n"
        "        (t0.get('rollbackPlan') or {})\n"
        "        .get('strategy'))\n"
        "    out['hasDeps'] = bool(\n"
        "        t0.get('dependencies'))\n"
        "    out['estimatedGain'] = "
        "p.get('estimatedGain')\n"
        # ③ 编码
        "    c = await Aiup56CodeService().code(pid)\n"
        "    out['codeStatus'] = c.get('status')\n"
        "    out['codeMode'] = c.get('mode')\n"
        "    out['assetVersion'] = "
        "c.get('assetVersion')\n"
        "    out['valueReasons'] = "
        "c.get('valueReasonCount')\n"
        # ④ 资产结构
        "    assets = await Aiup56Repository()"
        ".list_assets(pid)\n"
        "    a = (assets or [{}])[0]\n"
        "    out['draftCount'] = len(\n"
        "        a.get('drafts') or [])\n"
        "    out['testPlanCount'] = len(\n"
        "        a.get('testPlans') or [])\n"
        # ⑤ 事件链
        "    events = await Aiup56Repository()"
        ".list_events(pid, limit=50)\n"
        "    types = {e.get('eventType')\n"
        "             for e in events}\n"
        "    out['eventTypes'] = sorted(types)\n"
        # ⑥ 宪法
        "    from services.ai_learning_service "
        "import SCORER_REGISTRY\n"
        "    out['scorerCount'] = "
        "len(SCORER_REGISTRY)\n"
        # ⑦ 状态机: 重复编码拒绝
        "    try:\n"
        "        await Aiup56CodeService().code(pid)\n"
        "        out['recodeRejected'] = False\n"
        "    except ValueError as e:\n"
        "        out['recodeRejected'] = "
        "'coded' in str(e)\n"
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

    print("\n[02 off 铁律]")
    ok, (code, _) = call(
        "POST", "/api/aiup56/proposals/1/plan",
        headers=ADMIN, expect=(409,))
    record("off 态 plan 409", code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/aiup56/proposals/1/code",
        headers=ADMIN, expect=(409,))
    record("off 态 code 409", code == 409, str(code))

    print("\n[03-04 容器内: 决策→规划→编码→宪法]")
    r = container_pipeline(round_no)

    record("提案创建(强信号)",
           (r.get("proposalId") or 0) > 0,
           str(r.get("proposalId")))
    record("规划执行(mock+任务拆解)",
           r.get("planStatus") == "planned"
           and r.get("planMode") == "mock"
           and r.get("taskCount") == 2,
           str((r.get("planStatus"),
                r.get("taskCount"))))
    record("任务结构(依赖+回滚预案)",
           r.get("hasDeps") is True
           and r.get("hasRollback") is True,
           str((r.get("hasDeps"),
                r.get("hasRollback"))))
    record("信值预估汇总",
           (r.get("estimatedGain") or 0) > 0,
           str(r.get("estimatedGain")))
    record("编码执行(mock+资产 v1)",
           r.get("codeStatus") == "coded"
           and r.get("codeMode") == "mock"
           and r.get("assetVersion") == 1,
           str((r.get("codeStatus"),
                r.get("assetVersion"))))
    record("VALUE_REASON 注释即证据",
           (r.get("valueReasons") or 0) >= 2,
           str(r.get("valueReasons")))
    record("资产结构(草稿+测试计划)",
           r.get("draftCount") == 2
           and r.get("testPlanCount") == 2,
           str((r.get("draftCount"),
                r.get("testPlanCount"))))
    record("全链事件(signal_scan→create→"
           "plan→code)",
           {"proposal_create", "plan", "code"} <= set(
               r.get("eventTypes") or []),
           str(r.get("eventTypes")))
    record("44号 ≥31 档案保持",
           r.get("scorerCount") >= 31,
           str(r.get("scorerCount")))
    record("重复编码拒绝(状态机)",
           r.get("recodeRejected") is True,
           str(r.get("recodeRejected")))

    print("\n[05 HTTP 端点+鉴权]")
    # HTTP 观测面(tasks/assets——off 可用)
    ok, (code, _) = call(
        "GET", "/api/aiup56/proposals/99999/tasks",
        headers=ADMIN, expect=(404,))
    record("HTTP tasks 404(观测面)",
           code == 404, str(code))
    ok, (code, _) = call(
        "GET", "/api/aiup56/proposals/1/assets")
    record("HTTP assets 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/aiup56/proposals/1/plan")
    record("HTTP plan 无 Role 403",
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
