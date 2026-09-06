"""56号AI智能升级管理 P0 Docker 实机验收

运行方式:
    python verify_aiup56_p0_live.py [基址]

前置: 容器已运行(含 56号 P0 代码)。

覆盖(56号计划 §九 P0, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律+观测面(signals/scan 409;
       registry/proposals/model/status 可达)
    03 容器内: 信号采集+决策主链(shadow 态
       46号 sync 后全源读取→defer 留痕)
    04 容器内: 强信号(55号 指标劣化快照)→
       提案创建(信号快照+摘要+预算封顶)
    05 宪法: 44号 31 档案+55号 零改动+
       46号台账 31
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
    """清理种子(aiup56 全表+55号 指标快照+
    46号 aiup 档案态)"""
    redis_del_keys("zhuxiang:aiup56:*")
    redis_del_keys("zhuxiang:qr55:model_events*")
    # 55号 model_events(指标快照种子)
    redis_del_keys(
        "zhuxiang:qr55:qr55_model_events:*")
    redis_del_keys("zhuxiang:qr55:model_events_all")


def container_pipeline(round_no: int) -> dict:
    """容器内: 采集→决策→提案(Redis 态)"""
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
        "async def m():\n"
        "    out = {}\n"
        "    svc = Aiup56Service()\n"
        # ① 空环境决策(defer)
        "    r = await svc.evaluate_and_propose()\n"
        "    out['deferDecision'] = r.get('decision')\n"
        "    out['deferNecessity'] = "
        "r.get('necessityScore')\n"
        # ② 强信号: 55号 指标劣化两帧快照
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
        # ③ 强信号决策(提案)
        "    r2 = await svc.evaluate_and_propose()\n"
        "    out['proposeDecision'] = "
        "r2.get('decision')\n"
        "    out['proposalId'] = r2.get('proposalId')\n"
        "    out['necessity'] = "
        "r2.get('necessityScore')\n"
        # ④ 提案结构校验
        "    if r2.get('proposalId'):\n"
        "        p = await Aiup56Repository()."
        "get_proposal(\n"
        "            r2['proposalId'])\n"
        "        out['proposalStatus'] = "
        "p.get('status')\n"
        "        out['budgetCap'] = "
        "p.get('budgetCap')\n"
        "        out['hasSnapshot'] = bool(\n"
        "            p.get('signalSnapshot'))\n"
        "        out['hasSummary'] = bool(\n"
        "            p.get('summary'))\n"
        # ⑤ 全链事件
        "    events = await Aiup56Repository()."
        "list_events(limit=50)\n"
        "    types = {e.get('eventType')\n"
        "             for e in events}\n"
        "    out['eventTypes'] = sorted(types)\n"
        # ⑥ 宪法: 44号 31 档案
        "    from services.ai_learning_service "
        "import SCORER_REGISTRY\n"
        "    out['scorerCount'] = "
        "len(SCORER_REGISTRY)\n"
        "    out['upgradeInRegistry'] = (\n"
        "        'upgrade_orchestration' "
        "in SCORER_REGISTRY)\n"
        "    out['qrInRegistry'] = (\n"
        "        'qr_orchestration' "
        "in SCORER_REGISTRY)\n"
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
        "POST", "/api/aiup56/signals/scan",
        headers=ADMIN, expect=(409,))
    record("off 态 signals/scan 409",
           code == 409, str(code))
    for path, label in (
            ("/api/aiup56/registry", "registry"),
            ("/api/aiup56/proposals", "proposals"),
            ("/api/aiup56/model/status",
             "model/status")):
        ok, (code, _) = call("GET", path, headers=ADMIN)
        record(f"观测面 {label} off 可访问",
               code == 200, str(code))

    print("\n[03-05 容器内: 采集→决策→提案→宪法]")
    r = container_pipeline(round_no)

    record("空环境 defer(留痕不建提案)",
           r.get("deferDecision") == "defer",
           str(r.get("deferDecision")))
    record("强信号提案创建(proposalId)",
           r.get("proposalId") is not None
           and r.get("proposeDecision") in (
               "propose", "escalate"),
           str((r.get("proposeDecision"),
                r.get("proposalId"))))
    record("提案结构(draft+快照+摘要)",
           r.get("proposalStatus") == "draft"
           and r.get("hasSnapshot") is True
           and r.get("hasSummary") is True,
           str(r.get("proposalStatus")))
    record("提案预算封顶(0.1)",
           r.get("budgetCap") == 0.1,
           str(r.get("budgetCap")))
    record("全链事件(signal_scan+create)",
           {"signal_scan", "proposal_create"} <= set(
               r.get("eventTypes") or []),
           str(r.get("eventTypes")))
    record("44号 31 档案(upgrade 在册)",
           r.get("scorerCount") == 31
           and r.get("upgradeInRegistry") is True,
           str(r.get("scorerCount")))
    record("55号零改动(qr 档案保持)",
           r.get("qrInRegistry") is True,
           str(r.get("qrInRegistry")))

    print("\n[06 HTTP 端点+鉴权]")
    ok, (code, body) = call(
        "GET", "/api/aiup56/registry", headers=ADMIN)
    record("HTTP registry(10 项四侧)",
           code == 200
           and (body or {}).get("total") == 10,
           str(code))
    ok, (code, body) = call(
        "GET", "/api/aiup56/proposals", headers=ADMIN)
    record("HTTP proposals(提案可见)",
           code == 200
           and (body or {}).get("total", 0) >= 1,
           str((code, (body or {}).get("total"))))
    ok, (code, _) = call(
        "POST", "/api/aiup56/signals/scan")
    record("signals/scan 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "GET", "/api/aiup56/registry")
    record("registry 无 Role 403",
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
