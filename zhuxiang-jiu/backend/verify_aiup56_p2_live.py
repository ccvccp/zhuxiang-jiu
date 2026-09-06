"""56号AI智能升级管理 P2 Docker 实机验收

运行方式:
    python verify_aiup56_p2_live.py [基址]

前置: 容器已运行(含 56号 P0-P2 代码)。

覆盖(56号计划 §九 P2, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(test 409)
    03 容器内: 全链 draft→planned→coded→tested
       (用例矩阵+三关全过)
    04 容器内: 预算熔断流(budget_halted→blocked)
    05 宪法: 44号 ≥31 档案保持
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
    """容器内: 决策→规划→编码→测试+熔断流(Redis)"""
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
        "async def m():\n"
        "    out = {}\n"
        # ① 全链到 coded
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
        # ② 测试(三关全过)
        "    t = await Aiup56TestService().test(pid)\n"
        "    out['verdict'] = t.get('verdict')\n"
        "    out['status'] = t.get('status')\n"
        "    out['caseCount'] = len(\n"
        "        t.get('caseMatrix'))\n"
        "    out['allCasesPassed'] = all(\n"
        "        c.get('passed')\n"
        "        for c in t.get('caseMatrix'))\n"
        "    gates = t.get('gates') or {}\n"
        "    out['staticPassed'] = (\n"
        "        (gates.get('static') or {})\n"
        "        .get('passed'))\n"
        "    out['budgetMode'] = (\n"
        "        (gates.get('budget') or {})\n"
        "        .get('mode'))\n"
        "    out['valuePassed'] = (\n"
        "        (gates.get('value') or {})\n"
        "        .get('passed'))\n"
        # ③ 沙箱留痕
        "    sandboxes = await Aiup56Repository()"
        ".list_sandboxes(pid)\n"
        "    out['sandboxCount'] = len(sandboxes)\n"
        "    out['sandboxVerdict'] = (\n"
        "        sandboxes[0].get('verdict')\n"
        "        if sandboxes else None)\n"
        # ④ 熔断流(第二提案耗尽预算)
        "    r2 = await Aiup56Service()."
        "evaluate_and_propose()\n"
        "    pid2 = r2.get('proposalId')\n"
        "    await Aiup56PlanService().plan(pid2)\n"
        "    await Aiup56CodeService().code(pid2)\n"
        "    p2 = await Aiup56Repository()."
        "get_proposal(pid2)\n"
        "    p2['budgetSpent'] = 0.5\n"
        "    await Aiup56Repository().save_proposal(\n"
        "        p2, create=False)\n"
        "    t2 = await Aiup56TestService()."
        "test(pid2)\n"
        "    out['haltedVerdict'] = "
        "t2.get('verdict')\n"
        "    out['haltedStatus'] = "
        "t2.get('status')\n"
        # ⑤ 事件链+宪法
        "    events = await Aiup56Repository()"
        ".list_events(pid, limit=50)\n"
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

    print("\n[02 off 铁律]")
    ok, (code, _) = call(
        "POST", "/api/aiup56/proposals/1/test",
        headers=ADMIN, expect=(409,))
    record("off 态 test 409", code == 409, str(code))

    print("\n[03-05 容器内: 全链→三关→熔断→宪法]")
    r = container_pipeline(round_no)

    record("三关通过(passed+tested)",
           r.get("verdict") == "passed"
           and r.get("status") == "tested",
           str((r.get("verdict"),
                r.get("status"))))
    record("用例矩阵(6 用例全过)",
           r.get("caseCount") == 6
           and r.get("allCasesPassed") is True,
           str((r.get("caseCount"),
                r.get("allCasesPassed"))))
    record("静态关+预算关+价值关",
           r.get("staticPassed") is True
           and r.get("budgetMode") == "within_cap"
           and r.get("valuePassed") is True,
           str((r.get("staticPassed"),
                r.get("budgetMode"),
                r.get("valuePassed"))))
    record("沙箱留痕(passed)",
           r.get("sandboxCount") == 1
           and r.get("sandboxVerdict") == "passed",
           str((r.get("sandboxCount"),
                r.get("sandboxVerdict"))))
    record("预算熔断流(budget_halted→blocked)",
           r.get("haltedVerdict") == "budget_halted"
           and r.get("haltedStatus") == "blocked",
           str((r.get("haltedVerdict"),
                r.get("haltedStatus"))))
    record("全链事件(create→plan→code→test)",
           {"proposal_create", "plan", "code",
            "test"} <= set(
               r.get("eventTypes") or []),
           str(r.get("eventTypes")))
    record("44号 ≥31 档案保持",
           r.get("scorerCount") >= 31,
           str(r.get("scorerCount")))

    print("\n[06 HTTP 端点+鉴权]")
    # 容器内已全链——HTTP 观测面
    ok, (code, body) = call(
        "GET", "/api/aiup56/proposals",
        headers=ADMIN)
    record("HTTP proposals(两提案可见)",
           code == 200
           and (body or {}).get("total", 0) >= 2,
           str((code, (body or {}).get("total"))))
    ok, (code, _) = call(
        "GET", "/api/aiup56/proposals/1/sandboxes")
    record("HTTP sandboxes 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/aiup56/proposals/1/test")
    record("HTTP test 无 Role 403",
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
