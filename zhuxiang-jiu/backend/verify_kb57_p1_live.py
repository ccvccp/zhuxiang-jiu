"""57号AI智能知识库 P1 Docker 实机验收

运行方式:
    python verify_kb57_p1_live.py [基址]

前置: 容器已运行(含 57号 P0-P1 代码)。

覆盖(57号计划 §十一 P1, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(collect/compliance 409)
    03 容器内: 采集→鉴别全链(quarantined→
       passed/blocked/quarantined/halted 四态)
    04 宪法: 44号 32 档案保持
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
    # 49号系统账号预算(幂等)
    redis_del_keys(
        "zhuxiang:voice48:voice48_privacy_budget:0")


def container_pipeline(round_no: int) -> dict:
    """容器内: 采集→鉴别四态全链(Redis 态)"""
    script = (
        "import asyncio, json, os\n"
        "os.environ['KB57_MODE'] = 'shadow'\n"
        "from core.helpers import ts\n"
        "from repositories.kb57_repository import "
        "Kb57Repository\n"
        "async def m():\n"
        "    out = {}\n"
        "    repo = Kb57Repository()\n"
        "    await repo.reset_all()\n"
        # ① 种缺口(建议源白名单内)
        "    gap_id = await repo.next_gap_id()\n"
        "    await repo.save_gap({\n"
        "        'gapId': gap_id, 'status': 'open',\n"
        "        'priority': 'high',\n"
        "        'topic': 'elderly subsidy guide',\n"
        "        'decision': 'collect',\n"
        "        'signalSnapshot': {'hits': [],\n"
        "            'necessityScore': 45.0,\n"
        "            'sideCoverage': 0.2},\n"
        "        'necessityScore': 45.0,\n"
        "        'trustScore': 60.0,\n"
        "        'suggestedSources': [\n"
        "            'gov_policy_official'],\n"
        "        'budgetCap': 0.1,\n"
        "        'budgetSpent': 0.0,\n"
        "        'llmCalls': 0,\n"
        "        'createdAt': ts(),\n"
        "        'updatedAt': ts()})\n"
        # ② 采集
        "    from services.kb57_collect_service "
        "import Kb57CollectService\n"
        "    c = await Kb57CollectService()."
        "run_collect(gap_id=gap_id)\n"
        "    out['collected'] = c.get('collected')\n"
        "    rid1 = (c.get('resources') or [{}])[0]"
        ".get('resourceId')\n"
        "    res1 = await repo.get_resource(rid1)\n"
        "    out['res1Status'] = res1.get('status')\n"
        "    out['gapStatus'] = (await repo.get_gap("
        "gap_id)).get('status')\n"
        # ③ 鉴别: 干净资源 passed
        "    from services.kb57_compliance_service "
        "import Kb57ComplianceService\n"
        "    comp = Kb57ComplianceService()\n"
        "    r1 = await comp.run_compliance(rid1)\n"
        "    out['cleanVerdict'] = r1.get('verdict')\n"
        "    out['cleanStatus'] = r1.get('status')\n"
        "    out['cleanFp'] = str(r1.get("
        "'fingerprint') or '')[:10]\n"
        "    out['budgetSpent'] = (await repo."
        "get_gap(gap_id)).get('budgetSpent')\n"
        # ④ PII 资源(身份证+手机+银行卡)
        "    rid2 = await repo.next_resource_id()\n"
        "    import hashlib\n"
        "    pii = ('contact id 110101199001011234 "
        "ph 13800138000 card "
        "6222020200112233445')\n"
        "    await repo.save_resource({\n"
        "        'resourceId': rid2, 'gapId': gap_id,\n"
        "        'sourceId': 'ops_manual',\n"
        "        'sourceType': 'internal',\n"
        "        'sourceCredibility': 0.9,\n"
        "        'license': 'internal',\n"
        "        'title': 'pii-test',\n"
        "        'contentText': pii,\n"
        "        'maskedText': '',\n"
        "        'contentHash': 'sha256:' + hashlib."
        "sha256(pii.encode()).hexdigest()[:32],\n"
        "        'status': 'quarantined',\n"
        "        'reviewRequired': False,\n"
        "        'budgetHalted': False,\n"
        "        'resourceVersion': 1,\n"
        "        'complianceReports': [],\n"
        "        'createdAt': ts(),\n"
        "        'updatedAt': ts()})\n"
        "    r2 = await comp.run_compliance(rid2)\n"
        "    out['piiVerdict'] = r2.get('verdict')\n"
        "    out['piiMasked'] = len(r2.get("
        "'maskedFields') or [])\n"
        "    res2 = await repo.get_resource(rid2)\n"
        "    mt = str(res2.get('maskedText') or '')\n"
        "    out['piiLeak'] = (\n"
        "        '110101199001011234' in mt\n"
        "        or '13800138000' in mt\n"
        "        or '6222020200112233445' in mt)\n"
        # ⑤ 高危资源(unicode escape 防编码损坏)
        "    rid3 = await repo.next_resource_id()\n"
        "    bad = 'bad content \u66b4\u6050 "
        "\u8272\u60c5 channel'\n"
        "    await repo.save_resource({\n"
        "        'resourceId': rid3, 'gapId': gap_id,\n"
        "        'sourceId': 'ops_manual',\n"
        "        'sourceType': 'internal',\n"
        "        'sourceCredibility': 0.9,\n"
        "        'license': 'internal',\n"
        "        'title': 'bad-test',\n"
        "        'contentText': bad,\n"
        "        'maskedText': '',\n"
        "        'contentHash': 'sha256:' + hashlib."
        "sha256(bad.encode()).hexdigest()[:32],\n"
        "        'status': 'quarantined',\n"
        "        'reviewRequired': False,\n"
        "        'budgetHalted': False,\n"
        "        'resourceVersion': 1,\n"
        "        'complianceReports': [],\n"
        "        'createdAt': ts(),\n"
        "        'updatedAt': ts()})\n"
        "    r3 = await comp.run_compliance(rid3)\n"
        "    out['badVerdict'] = r3.get('verdict')\n"
        "    out['badFp'] = str(r3.get("
        "'fingerprint') or '')\n"
        # ⑥ 中风险资源(谣言)
        "    rid4 = await repo.next_resource_id()\n"
        "    mid = 'rumor \u8c23\u8a00 "
        "\u672a\u7ecf\u8bc1\u5b9e claim'\n"
        "    await repo.save_resource({\n"
        "        'resourceId': rid4, 'gapId': gap_id,\n"
        "        'sourceId': 'media_whitelist',\n"
        "        'sourceType': 'media',\n"
        "        'sourceCredibility': 0.7,\n"
        "        'license': 'reprint',\n"
        "        'title': 'mid-test',\n"
        "        'contentText': mid,\n"
        "        'maskedText': '',\n"
        "        'contentHash': 'sha256:' + hashlib."
        "sha256(mid.encode()).hexdigest()[:32],\n"
        "        'status': 'quarantined',\n"
        "        'reviewRequired': False,\n"
        "        'budgetHalted': False,\n"
        "        'resourceVersion': 1,\n"
        "        'complianceReports': [],\n"
        "        'createdAt': ts(),\n"
        "        'updatedAt': ts()})\n"
        "    r4 = await comp.run_compliance(rid4)\n"
        "    out['midVerdict'] = r4.get('verdict')\n"
        "    out['midReview'] = (await repo."
        "get_resource(rid4)).get('reviewRequired')\n"
        # ⑦ 预算熔断(缺口 cap 已满)
        "    rid5 = await repo.next_resource_id()\n"
        "    gap = await repo.get_gap(gap_id)\n"
        "    gap['budgetSpent'] = 0.1\n"
        "    await repo.save_gap(gap, create=False)\n"
        "    clean2 = 'clean text for halt test'\n"
        "    await repo.save_resource({\n"
        "        'resourceId': rid5, 'gapId': gap_id,\n"
        "        'sourceId': 'ops_manual',\n"
        "        'sourceType': 'internal',\n"
        "        'sourceCredibility': 0.9,\n"
        "        'license': 'internal',\n"
        "        'title': 'halt-test',\n"
        "        'contentText': clean2,\n"
        "        'maskedText': '',\n"
        "        'contentHash': 'sha256:' + hashlib."
        "sha256(clean2.encode()).hexdigest()[:32],\n"
        "        'status': 'quarantined',\n"
        "        'reviewRequired': False,\n"
        "        'budgetHalted': False,\n"
        "        'resourceVersion': 1,\n"
        "        'complianceReports': [],\n"
        "        'createdAt': ts(),\n"
        "        'updatedAt': ts()})\n"
        "    r5 = await comp.run_compliance(rid5)\n"
        "    out['haltVerdict'] = r5.get('verdict')\n"
        "    out['haltFlag'] = (await repo."
        "get_resource(rid5)).get('budgetHalted')\n"
        # ⑧ 事件链+宪法
        "    events = await repo.list_events(limit=100)\n"
        "    types = sorted({e.get('eventType')\n"
        "                   for e in events})\n"
        "    out['eventTypes'] = types\n"
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

    print("\n[02 off 铁律]")
    ok, (code, _) = call(
        "POST", "/api/kb57/collect/run",
        headers=ADMIN, expect=(409,))
    record("off 态 collect/run 409",
           code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/kb57/resources/1/compliance",
        headers=ADMIN, expect=(409,))
    record("off 态 compliance 409",
           code == 409, str(code))

    print("\n[03 容器内: 采集→鉴别四态]")
    r = container_pipeline(round_no)

    record("采集落库(1 资源 quarantined)",
           r.get("collected") == 1
           and r.get("res1Status")
           == "quarantined",
           str((r.get("collected"),
                r.get("res1Status"))))
    record("缺口状态翻转(collecting)",
           r.get("gapStatus") == "collecting",
           str(r.get("gapStatus")))
    record("干净资源 passed+指纹",
           r.get("cleanVerdict") == "passed"
           and r.get("cleanStatus")
           == "compliant"
           and str(r.get("cleanFp")
                   or "").startswith("sha256:"),
           str((r.get("cleanVerdict"),
                r.get("cleanFp"))))
    record("缺口预算扣减(0.01)",
           r.get("budgetSpent") == 0.01,
           str(r.get("budgetSpent")))
    record("PII 资源脱敏(3 型+零泄漏)",
           r.get("piiVerdict") == "passed"
           and r.get("piiMasked") == 3
           and r.get("piiLeak") is False,
           str((r.get("piiVerdict"),
                r.get("piiMasked"),
                r.get("piiLeak"))))
    record("高危资源 blocked(无指纹)",
           r.get("badVerdict") == "blocked"
           and not r.get("badFp"),
           str((r.get("badVerdict"),
                r.get("badFp"))))
    record("中风险+低可信度 quarantined",
           r.get("midVerdict")
           == "quarantined"
           and r.get("midReview") is True,
           str((r.get("midVerdict"),
                r.get("midReview"))))
    record("预算熔断 halted(隔离保持)",
           r.get("haltVerdict") == "halted"
           and r.get("haltFlag") is True,
           str((r.get("haltVerdict"),
                r.get("haltFlag"))))
    record("事件链(collect+compliance)",
           {"collect", "compliance"} <= set(
               r.get("eventTypes") or []),
           str(r.get("eventTypes")))
    record("44号 32 档案保持",
           r.get("scorerCount") == 32,
           str(r.get("scorerCount")))

    print("\n[04 HTTP 端点+鉴权]")
    # 观测面(off 可用): gaps 含 collecting 态
    ok, (code, body) = call(
        "GET", "/api/kb57/gaps", headers=ADMIN)
    record("HTTP gaps 200(collecting 态)",
           code == 200
           and (body.get("total") or 0) >= 1,
           str((code, body.get("total"))))
    # 鉴权 403
    for method, path in (
            ("POST", "/api/kb57/collect/run"),
            ("POST",
             "/api/kb57/resources/1/compliance"),
            ("GET", "/api/kb57/compliance/1")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))


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
