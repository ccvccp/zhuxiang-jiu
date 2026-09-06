"""57号AI智能知识库 P5 Docker 实机验收

运行方式:
    python verify_kb57_p5_live.py [基址]

前置: 容器已运行(含 57号 P0-P5 代码)。

覆盖(57号计划 §九 P5, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(dashboard 观测面/redteam 409)
    03 容器内: 全链种子→看板四区数字→红队七向量
    04 宪法: 44号 32 档案保持+注册表封闭
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


def clear_kb57(round_no: int) -> None:
    redis_del_keys("zhuxiang:kb57:*")
    # 信号种子清理(幂等)
    redis_del_keys("zhuxiang:qr55:model_events*")
    redis_del_keys(
        "zhuxiang:qr55:qr55_model_events:*")
    redis_del_keys("zhuxiang:qr55:model_events_all")
    # 红队会员预算(RT_MEMBER 9901)
    redis_del_keys(
        "zhuxiang:voice48:voice48_privacy_budget:9901")


def container_pipeline(round_no: int) -> dict:
    """容器内: 全链种子→看板→红队(Redis 态)"""
    script = (
        "import asyncio, hashlib, json, os\n"
        "from core.helpers import ts\n"
        "from repositories.kb57_repository import "
        "Kb57Repository\n"
        "async def m():\n"
        "    out = {}\n"
        "    repo = Kb57Repository()\n"
        "    await repo.reset_all()\n"
        # ① 全链种子(gap→resource→compliance→
        #    published seed+feedback+path)
        "    gap_id = await repo.next_gap_id()\n"
        "    await repo.save_gap({\n"
        "        'gapId': gap_id, 'status': 'open',\n"
        "        'priority': 'high',\n"
        "        'topic': 'live p5 gap',\n"
        "        'signalSnapshot': {'hits': [\n"
        "            {'signalId': 'kb_gap_open'}]},\n"
        "        'suggestedSources': [\n"
        "            'gov_policy_official'],\n"
        "        'budgetCap': 0.1,\n"
        "        'budgetSpent': 0.01,\n"
        "        'createdAt': ts(),\n"
        "        'updatedAt': ts()})\n"
        "    rid = await repo.next_resource_id()\n"
        "    raw = f'p5-{rid}'.encode()\n"
        "    fp = 'sha256:' + hashlib.sha256("
        "raw).hexdigest()[:32]\n"
        "    await repo.save_resource({\n"
        "        'resourceId': rid,\n"
        "        'gapId': gap_id,\n"
        "        'sourceId': "
        "'gov_policy_official',\n"
        "        'sourceType': 'authority',\n"
        "        'sourceCredibility': 0.95,\n"
        "        'license': 'public',\n"
        "        'title': 'p5-res',\n"
        "        'contentText': 'content',\n"
        "        'maskedText': '',\n"
        "        'contentHash': 'sha256:x',\n"
        "        'status': 'compliant',\n"
        "        'reviewRequired': False,\n"
        "        'budgetHalted': False,\n"
        "        'resourceVersion': 1,\n"
        "        'complianceReports': [],\n"
        "        'fingerprint': fp,\n"
        "        'createdAt': ts(),\n"
        "        'updatedAt': ts()})\n"
        "    cid = await repo.next_compliance_id()\n"
        "    await repo.save_compliance({\n"
        "        'complianceId': cid,\n"
        "        'resourceId': rid,\n"
        "        'gapId': gap_id,\n"
        "        'verdict': 'passed',\n"
        "        'copyright': {'passed': True},\n"
        "        'privacy': {'piiFound': 0},\n"
        "        'contentSafety': {\n"
        "            'riskLevel': 'low'},\n"
        "        'gate': {'halted': False},\n"
        "        'fingerprint': fp,\n"
        "        'maskedFields': [],\n"
        "        'budgetSpent': 0.01,\n"
        "        'createdAt': ts()})\n"
        "    sid = await repo.next_seed_id()\n"
        "    await repo.save_seed({\n"
        "        'seedId': sid,\n"
        "        'seedVersion': 1,\n"
        "        'type': 'text',\n"
        "        'title': 'p5-seed',\n"
        "        'content': {'text': 'c',\n"
        "            'mediaRef': None,\n"
        "            'transcript': None,\n"
        "            'keyframes': None,\n"
        "            'alt': None},\n"
        "        'contentHash': 'sha256:x',\n"
        "        'complianceFingerprint': fp,\n"
        "        'valueTags': ['policy'],\n"
        "        'sourceId': "
        "'gov_policy_official',\n"
        "        'sourceCredibility': 0.95,\n"
        "        'privacyCost': 0.002,\n"
        "        'knowledgeReason': 'live',\n"
        "        'humanVerified': True,\n"
        "        'validUntil': '2099-01-01',\n"
        "        'abTest': {'active': False,\n"
        "                   'variantOf': None},\n"
        "        'status': 'published',\n"
        "        'gapId': gap_id,\n"
        "        'resourceId': rid,\n"
        "        'viewCount': 3,\n"
        "        'positiveCount': 2,\n"
        "        'negativeCount': 0,\n"
        "        'pooledFeedbackId': 1,\n"
        "        'poolSignal': 'seed_high_value',\n"
        "        'poolReward': 0.8,\n"
        "        'llmCalls': 0,\n"
        "        'createdAt': ts(),\n"
        "        'updatedAt': ts()})\n"
        "    fid = await repo.next_feedback_id()\n"
        "    await repo.save_feedback({\n"
        "        'feedbackId': fid,\n"
        "        'seedId': sid,\n"
        "        'memberId': 5001,\n"
        "        'kind': 'positive',\n"
        "        'pooled': False,\n"
        "        'createdAt': ts()})\n"
        "    pid = await repo.next_path_id()\n"
        "    await repo.save_path({\n"
        "        'pathId': pid,\n"
        "        'memberId': 5001,\n"
        "        'title': 'course',\n"
        "        'seedIds': [sid],\n"
        "        'progress': {'completed': [sid],\n"
        "                     'current': None},\n"
        "        'completed': True,\n"
        "        'createdAt': ts(),\n"
        "        'updatedAt': ts()})\n"
        # ② 看板四区
        "    from services."
        "kb57_dashboard_service import (\n"
        "        Kb57DashboardService)\n"
        "    board = await Kb57DashboardService()"
        ".build()\n"
        "    zones = board.get('zones') or {}\n"
        "    metrics = zones.get('metrics') or {}\n"
        "    out['coverage'] = metrics.get("
        "'coverageRate')\n"
        "    out['passRate'] = metrics.get("
        "'compliancePassRate')\n"
        "    out['effective'] = metrics.get("
        "'seedEffectiveRate')\n"
        "    out['conversion'] = metrics.get("
        "'learningConversionRate')\n"
        "    out['valueGain'] = metrics.get("
        "'valueGainTotal')\n"
        "    seeds_z = zones.get('seeds') or {}\n"
        "    out['seedTotal'] = seeds_z.get("
        "'totalSeeds')\n"
        "    out['seedPublished'] = (\n"
        "        seeds_z.get('byStatus') or {})"
        ".get('published')\n"
        "    comp_z = zones.get('compliance') or {}\n"
        "    out['compPassed'] = (\n"
        "        comp_z.get('byVerdict') or {})"
        ".get('passed')\n"
        "    defense_z = zones.get('defense') or {}\n"
        "    out['poolSubmitted'] = (\n"
        "        defense_z.get('feedbackSignals')"
        " or {}).get('poolSubmitted')\n"
        "    out['guardHealthy'] = (\n"
        "        defense_z.get('guardrail') or {})"
        ".get('healthy')\n"
        # ③ 红队七向量(shadow 态)
        "    os.environ['KB57_MODE'] = 'shadow'\n"
        "    from services."
        "kb57_redteam_service import (\n"
        "        Kb57RedteamService)\n"
        "    rt = await Kb57RedteamService()"
        ".run_all()\n"
        "    out['rtTotal'] = (\n"
        "        rt.get('summary') or {})"
        ".get('total')\n"
        "    out['rtDefended'] = (\n"
        "        rt.get('summary') or {})"
        ".get('defended')\n"
        "    out['rtAll'] = (\n"
        "        rt.get('summary') or {})"
        ".get('allDefended')\n"
        # ④ 红队后注册表+宪法
        "    from services.kb57_registry import (\n"
        "        GAP_SIGNAL_REGISTRY)\n"
        "    out['registryCount'] = len(\n"
        "        GAP_SIGNAL_REGISTRY)\n"
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
        return {"error": (out.stderr
                          or "无输出")[-1500:]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_kb57(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/kb57/redteam",
        headers=ADMIN, expect=(409,))
    record("off 态 redteam 409(无攻击面)",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/kb57/dashboard",
        headers=ADMIN)
    record("off 态 dashboard 观测面 200",
           code == 200
           and (body.get("zones") or {})
           .get("metrics") is not None,
           str(code))

    print("\n[03 容器内: 看板→红队→宪法]")
    r = container_pipeline(round_no)

    record("度量区(五指标全值)",
           r.get("coverage") == 0.0
           and r.get("passRate") == 1.0
           and r.get("effective") == 1.0
           and r.get("conversion") == 1.0
           and r.get("valueGain") == 1,
           str((r.get("coverage"),
                r.get("passRate"),
                r.get("valueGain"))))
    record("种子库(1 published)",
           r.get("seedTotal") == 1
           and r.get("seedPublished") == 1,
           str((r.get("seedTotal"),
                r.get("seedPublished"))))
    record("合规区(passed 1)",
           r.get("compPassed") == 1,
           str(r.get("compPassed")))
    record("防御区(池提交 1+护栏健康)",
           r.get("poolSubmitted") == 1
           and r.get("guardHealthy") is True,
           str((r.get("poolSubmitted"),
                r.get("guardHealthy"))))
    record("红队七向量全防御",
           r.get("rtTotal") == 7
           and r.get("rtDefended") == 7
           and r.get("rtAll") is True,
           str((r.get("rtTotal"),
                r.get("rtDefended"),
                r.get("rtAll"))))
    record("红队后注册表完整(10 项)",
           r.get("registryCount") == 10,
           str(r.get("registryCount")))
    record("44号 32 档案保持",
           r.get("scorerCount") == 32,
           str(r.get("scorerCount")))

    print("\n[04 HTTP 端点+鉴权]")
    ok, (code, body) = call(
        "GET", "/api/kb57/dashboard",
        headers=ADMIN)
    zones = (body.get("zones") or {})
    record("HTTP dashboard 200(四区)",
           code == 200
           and set(zones.keys()) == {
               "metrics", "seeds",
               "compliance", "defense"},
           str((code, sorted(zones.keys()))))
    ok, (code, _) = call(
        "GET", "/api/kb57/dashboard")
    record("HTTP dashboard 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/kb57/redteam")
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
