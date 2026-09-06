"""57号AI智能知识库 P2 Docker 实机验收

运行方式:
    python verify_kb57_p2_live.py [基址]

前置: 容器已运行(含 57号 P0-P2 代码)。

覆盖(57号计划 §十一 P2, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(craft 409; review off 亦可用)
    03 容器内: 锻造→终审→版本化→召回→
       有效期全链(Redis 序列化读回)
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
    redis_del_keys(
        "zhuxiang:voice48:voice48_privacy_budget:0")


def container_pipeline(round_no: int) -> dict:
    """容器内: 锻造→终审→版本化→召回→有效期"""
    script = (
        "import asyncio, hashlib, json, os\n"
        "from datetime import datetime, timedelta\n"
        "os.environ['KB57_MODE'] = 'shadow'\n"
        "from core.helpers import ts\n"
        "from repositories.kb57_repository import "
        "Kb57Repository\n"
        "async def m():\n"
        "    out = {}\n"
        "    repo = Kb57Repository()\n"
        "    await repo.reset_all()\n"
        # ① 种缺口+compliant 资源×2
        "    gap_id = await repo.next_gap_id()\n"
        "    await repo.save_gap({\n"
        "        'gapId': gap_id, 'status': "
        "'collecting',\n"
        "        'priority': 'high',\n"
        "        'topic': 'elderly subsidy guide',\n"
        "        'decision': 'collect',\n"
        "        'signalSnapshot': {'hits': [\n"
        "            {'signalId': 'kb_gap_open',\n"
        "             'value': 1,\n"
        "             'evidence': 'open gap'}],\n"
        "            'necessityScore': 35.0,\n"
        "            'sideCoverage': 0.2},\n"
        "        'necessityScore': 35.0,\n"
        "        'trustScore': 62.0,\n"
        "        'suggestedSources': [\n"
        "            'gov_policy_official'],\n"
        "        'budgetCap': 0.1,\n"
        "        'budgetSpent': 0.0,\n"
        "        'llmCalls': 0,\n"
        "        'createdAt': ts(),\n"
        "        'updatedAt': ts()})\n"
        "    async def mk_resource():\n"
        "        rid = await repo."
        "next_resource_id()\n"
        "        raw_fp = f'p2-{rid}'.encode()\n"
        "        raw_ch = f'ch-{rid}'.encode()\n"
        "        fp = 'sha256:' + hashlib.sha256("
        "raw_fp).hexdigest()[:32]\n"
        "        ch = 'sha256:' + hashlib.sha256("
        "raw_ch).hexdigest()[:32]\n"
        "        await repo.save_resource({\n"
        "            'resourceId': rid,\n"
        "            'gapId': gap_id,\n"
        "            'sourceId': "
        "'gov_policy_official',\n"
        "            'sourceType': 'authority',\n"
        "            'sourceCredibility': 0.95,\n"
        "            'license': 'public policy',\n"
        "            'title': 'subsidy guide',\n"
        "            'contentText': 'step 1 apply',\n"
        "            'maskedText': '',\n"
        "            'contentHash': ch,\n"
        "            'status': 'compliant',\n"
        "            'reviewRequired': False,\n"
        "            'budgetHalted': False,\n"
        "            'resourceVersion': 1,\n"
        "            'complianceReports': [],\n"
        "            'fingerprint': fp,\n"
        "            'createdAt': ts(),\n"
        "            'updatedAt': ts()})\n"
        "        return rid\n"
        "    rid1 = await mk_resource()\n"
        "    rid2 = await mk_resource()\n"
        # ② 锻造 v1+v2(text)
        "    from services.kb57_seed_service import "
        "Kb57SeedService\n"
        "    ws = Kb57SeedService()\n"
        "    s1 = await ws.craft(gap_id, rid1,\n"
        "                        seed_type='text')\n"
        "    s2 = await ws.craft(gap_id, rid2,\n"
        "                        seed_type='text')\n"
        "    out['v1'] = s1.get('seedVersion')\n"
        "    out['v2'] = s2.get('seedVersion')\n"
        # ③ 种子 Redis 读回(结构完整)
        "    seed1 = await repo.get_seed("
        "s1.get('seedId'))\n"
        "    out['seedStatus'] = seed1.get('status')\n"
        "    out['contentIsDict'] = isinstance(\n"
        "        seed1.get('content'), dict)\n"
        "    out['abIsDict'] = isinstance(\n"
        "        seed1.get('abTest'), dict)\n"
        "    out['fpInherited'] = (\n"
        "        seed1.get('complianceFingerprint')\n"
        "        .startswith('sha256:'))\n"
        "    out['hasReason'] = 'kb_gap_open' in str(\n"
        "        seed1.get('knowledgeReason'))\n"
        "    out['hasValidUntil'] = bool(\n"
        "        seed1.get('validUntil'))\n"
        # ④ 终审: v1 发布→v2 发布(旧版降权)
        "    from services.kb57_review_service import "
        "Kb57ReviewService\n"
        "    rv = Kb57ReviewService()\n"
        "    r1 = await rv.review(s1.get('seedId'),\n"
        "        reviewer='admin', approved=True)\n"
        "    out['publishStatus'] = r1.get('status')\n"
        "    out['humanVerified'] = (await repo."
        "get_seed(s1.get('seedId'))"
        ").get('humanVerified')\n"
        "    r2 = await rv.review(s2.get('seedId'),\n"
        "        reviewer='admin', approved=True)\n"
        "    out['demotedPrior'] = "
        "r2.get('demotedPrior')\n"
        "    seed1b = await repo.get_seed("
        "s1.get('seedId'))\n"
        "    out['v1After'] = seed1b.get('status')\n"
        "    out['variantOf'] = (seed1b.get("
        "'abTest') or {}).get('variantOf')\n"
        # ⑤ 召回 v2
        "    seed2 = await repo.get_seed("
        "s2.get('seedId'))\n"
        "    seed2['viewCount'] = 3\n"
        "    await repo.save_seed(seed2, "
        "create=False)\n"
        "    rc = await rv.recall(s2.get('seedId'),\n"
        "        reason='misleading risk',\n"
        "        affected_members=[201])\n"
        "    out['recallStatus'] = rc.get('status')\n"
        "    out['recallComp'] = (rc.get("
        "'compensation') or {}).get('attempted')\n"
        # ⑥ 有效期(另种缺口隔离——published 态
        #    置过期触发降权)
        "    gap_id3 = await repo.next_gap_id()\n"
        "    await repo.save_gap({\n"
        "        'gapId': gap_id3, 'status': "
        "'collecting',\n"
        "        'priority': 'medium',\n"
        "        'topic': 'freshness test',\n"
        "        'decision': 'collect',\n"
        "        'signalSnapshot': {'hits': [],\n"
        "            'necessityScore': 30.0,\n"
        "            'sideCoverage': 0.2},\n"
        "        'necessityScore': 30.0,\n"
        "        'trustScore': 60.0,\n"
        "        'suggestedSources': [],\n"
        "        'budgetCap': 0.1,\n"
        "        'budgetSpent': 0.0,\n"
        "        'llmCalls': 0,\n"
        "        'createdAt': ts(),\n"
        "        'updatedAt': ts()})\n"
        "    rid3 = await mk_resource()\n"
        "    s3 = await ws.craft(gap_id3, rid3,\n"
        "                        seed_type='text')\n"
        "    await rv.review(s3.get('seedId'),\n"
        "        reviewer='admin', approved=True)\n"
        "    seed3 = await repo.get_seed("
        "s3.get('seedId'))\n"
        "    seed3['validUntil'] = (\n"
        "        datetime.utcnow() - timedelta(days=1)"
        ").strftime('%Y-%m-%d')\n"
        "    await repo.save_seed(seed3, "
        "create=False)\n"
        "    fr = await ws.freshness_check()\n"
        "    out['freshDemoted'] = fr.get('demoted')\n"
        "    out['v3Final'] = (await repo.get_seed("
        "s3.get('seedId'))).get('status')\n"
        # ⑦ 事件链+宪法
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

    print("\n[02 off 铁律+终审铁律]")
    ok, (code, _) = call(
        "POST", "/api/kb57/seeds/craft", body={},
        headers=ADMIN, expect=(409,))
    record("off 态 craft 409", code == 409, str(code))
    # review off 亦可用: 404(不存在)——非 off 语义
    ok, (code, body) = call(
        "POST", "/api/kb57/seeds/999/review",
        body={"reviewer": "admin",
              "approved": True},
        headers=ADMIN, expect=(404, 409))
    detail = str((body or {}).get("error")
                 or (body or {}).get("detail") or "")
    record("review off 亦可用(404 非 off)",
           code == 404 and "off" not in detail,
           str((code, detail[:40])))

    print("\n[03 容器内: 锻造→终审→版本化→召回→有效期]")
    r = container_pipeline(round_no)

    record("版本化(v1/v2 递增)",
           r.get("v1") == 1 and r.get("v2") == 2,
           str((r.get("v1"), r.get("v2"))))
    record("锻造 sandbox 态",
           r.get("seedStatus") == "sandbox",
           str(r.get("seedStatus")))
    record("Redis 读回(content/abTest 结构)",
           r.get("contentIsDict") is True
           and r.get("abIsDict") is True,
           str((r.get("contentIsDict"),
                r.get("abIsDict"))))
    record("指纹继承+KNOWLEDGE_REASON+有效期",
           r.get("fpInherited") is True
           and r.get("hasReason") is True
           and r.get("hasValidUntil") is True,
           str((r.get("fpInherited"),
                r.get("hasReason"))))
    record("发布 published+humanVerified",
           r.get("publishStatus") == "published"
           and r.get("humanVerified") is True,
           str((r.get("publishStatus"),
                r.get("humanVerified"))))
    record("版本化联动(旧版降权 1)",
           r.get("demotedPrior") == 1
           and r.get("v1After") == "downgraded",
           str((r.get("demotedPrior"),
                r.get("v1After"))))
    record("A/B 关联(variantOf 指向 v2)",
           int(r.get("variantOf") or 0) == 2,
           str(r.get("variantOf")))
    record("召回 recalled+补偿接口",
           r.get("recallStatus") == "recalled"
           and r.get("recallComp") == 1,
           str((r.get("recallStatus"),
                r.get("recallComp"))))
    record("有效期降权(downgraded)",
           r.get("freshDemoted") == 1
           and r.get("v3Final") == "downgraded",
           str((r.get("freshDemoted"),
                r.get("v3Final"))))
    record("事件链(craft/publish/recall/expire)",
           {"seed_craft", "seed_publish",
            "seed_recall", "seed_expire"} <= set(
               r.get("eventTypes") or []),
           str(r.get("eventTypes")))
    record("44号 32 档案保持",
           r.get("scorerCount") == 32,
           str(r.get("scorerCount")))

    print("\n[04 HTTP 端点+鉴权]")
    ok, (code, body) = call(
        "GET", "/api/kb57/seeds", headers=ADMIN)
    record("HTTP seeds 列表 200",
           code == 200
           and (body.get("total") or 0) >= 1,
           str((code, body.get("total"))))
    ok, (code, _) = call(
        "GET", "/api/kb57/seeds/1", headers=ADMIN)
    record("HTTP seeds 详情 200",
           code == 200, str(code))
    # 鉴权 403
    for method, path in (
            ("POST", "/api/kb57/seeds/craft"),
            ("GET", "/api/kb57/seeds"),
            ("POST", "/api/kb57/seeds/1/review"),
            ("POST", "/api/kb57/seeds/1/recall")):
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
