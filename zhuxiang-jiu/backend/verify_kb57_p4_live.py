"""57号AI智能知识库 P4 Docker 实机验收

运行方式:
    python verify_kb57_p4_live.py [基址]

前置: 容器已运行(含 57号 P0-P4 代码)。

覆盖(57号计划 §十一 P4, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 回流不依赖 KB57_MODE(off 亦可用)
    03 容器内: 全链种子→回流→补偿→调度
       (Redis 序列化读回)
    04 宪法: 44号 32 档案保持
    05 HTTP 端点+鉴权

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

CONTAINER = "zhuxiang-jiu-backend-1"
REDIS = "zhuxiang-jiu-redis-1"

# 运行唯一令牌(45号补偿档案摘要防重)
RUN_TOKEN = str(int(time.time()))


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
    # 44号池(第32档案反馈——计数核对口径)
    redis_del_keys(
        "zhuxiang:ai_learning:feedback:"
        "knowledge_orchestration")
    # 45号补偿对象档案使用轮次唯一摘要
    # (ID-KB57-P4-R{round})——无需全局清理


def container_pipeline(round_no: int) -> dict:
    """容器内: 种子→回流→补偿→调度全链"""
    script = (
        "import asyncio, hashlib, json, os\n"
        "os.environ['KB57_MODE'] = 'assist'\n"
        "from core.helpers import ts\n"
        "from repositories.kb57_repository import "
        "Kb57Repository\n"
        "async def m():\n"
        "    out = {}\n"
        "    repo = Kb57Repository()\n"
        "    await repo.reset_all()\n"
        # ① 种五类信号种子
        "    async def mk(status, views, pos, neg):\n"
        "        gap_id = await repo.next_gap_id()\n"
        "        await repo.save_gap({\n"
        "            'gapId': gap_id,\n"
        "            'status': 'resolved',\n"
        "            'priority': 'medium',\n"
        "            'topic': 'helper',\n"
        "            'signalSnapshot': {\n"
        "                'hits': []},\n"
        "            'suggestedSources': [],\n"
        "            'budgetCap': 0.1,\n"
        "            'budgetSpent': 0.0,\n"
        "            'createdAt': ts(),\n"
        "            'updatedAt': ts()})\n"
        "        seed_id = await repo.next_seed_id()\n"
        "        raw = f'p4-{seed_id}'.encode()\n"
        "        await repo.save_seed({\n"
        "            'seedId': seed_id,\n"
        "            'seedVersion': 1,\n"
        "            'type': 'text',\n"
        "            'title': 'p4-seed',\n"
        "            'content': {'text': 'c',\n"
        "                'mediaRef': None,\n"
        "                'transcript': None,\n"
        "                'keyframes': None,\n"
        "                'alt': None},\n"
        "            'contentHash': 'sha256:x',\n"
        "            'complianceFingerprint':\n"
        "                'sha256:' + hashlib.sha256("
        "raw).hexdigest()[:32],\n"
        "            'valueTags': ['policy'],\n"
        "            'sourceId': "
        "'gov_policy_official',\n"
        "            'sourceCredibility': 0.95,\n"
        "            'privacyCost': 0.002,\n"
        "            'knowledgeReason': 'live-p4',\n"
        "            'humanVerified': True,\n"
        "            'validUntil': '2099-01-01',\n"
        "            'abTest': {'active': False,\n"
        "                       'variantOf': None},\n"
        "            'status': status,\n"
        "            'gapId': gap_id,\n"
        "            'resourceId': 0,\n"
        "            'viewCount': views,\n"
        "            'positiveCount': pos,\n"
        "            'negativeCount': neg,\n"
        "            'pooledFeedbackId': 0,\n"
        "            'llmCalls': 0,\n"
        "            'createdAt': ts(),\n"
        "            'updatedAt': ts()})\n"
        "        return seed_id\n"
        "    s1 = await mk('published', 5, 1, 0)\n"
        "    s2 = await mk('published', 5, 4, 1)\n"
        "    s3 = await mk('published', 5, 1, 3)\n"
        "    s4 = await mk('recalled', 3, 0, 0)\n"
        # ② 回流(四类种子信号)
        "    from services."
        "kb57_feedback_loop_service import (\n"
        "        Kb57FeedbackLoopService)\n"
        "    loop = Kb57FeedbackLoopService()\n"
        "    r = await loop.collect_feedback()\n"
        "    out['scanned'] = r.get('scanned')\n"
        "    out['labeled'] = r.get('labeled')\n"
        "    out['signals'] = r.get('signals')\n"
        "    out['poolSubmitted'] = "
        "r.get('poolSubmitted')\n"
        # ③ 44号池核对
        "    from repositories."
        "ai_learning_repository import (\n"
        "        AiLearningRepository)\n"
        "    pool = await AiLearningRepository()"
        ".list_feedback(\n"
        "        'knowledge_orchestration')\n"
        "    out['poolCount'] = len(pool)\n"
        "    out['poolRewards'] = sorted(\n"
        "        float(x.get('reward') or 0)\n"
        "        for x in pool)\n"
        # ④ 种子回写(Redis 读回)
        "    stored = await repo.get_seed(s4)\n"
        "    out['pooledId'] = "
        "stored.get('pooledFeedbackId')\n"
        "    out['poolSignal'] = "
        "stored.get('poolSignal')\n"
        "    out['poolReward'] = "
        "stored.get('poolReward')\n"
        # ⑤ 幂等
        "    r2 = await loop.collect_feedback()\n"
        "    out['idemLabeled'] = r2.get('labeled')\n"
        "    out['idemSkipped'] = r2.get('skipped')\n"
        # ⑥ 补偿(45号档案)
        "    from services.trust_scoring_service "
        "import (\n"
        "        TrustProfileService)\n"
        "    member = await TrustProfileService()"
        ".create_role(\n"
        "        'person', 'KB57-P4-MEMBER',\n"
        "        'ID-KB57-"
        + RUN_TOKEN
        + "-R" + str(round_no) + "')\n"
        "    comp = await loop."
        "compensate_recall(\n"
        "        [member.get('trustId'), 99999],\n"
        "        seed_id=s4, reason='misleading')\n"
        "    out['compAttempted'] = "
        "comp.get('attempted')\n"
        "    out['compCompensated'] = "
        "comp.get('compensated')\n"
        # ⑦ 45号 L2 存证
        "    from repositories."
        "trust_value_repository import (\n"
        "        TrustValue45Repository)\n"
        "    ev45 = await TrustValue45Repository()"
        ".list_events_by_trust(\n"
        "        member.get('trustId'))\n"
        "    out['l2Deposits'] = sum(\n"
        "        1 for e in ev45\n"
        "        if e.get('layer') == 'L2'\n"
        "        and str(e.get('factor') or '')\n"
        "        == 'platform_conduct')\n"
        # ⑧ 调度器独立执行
        "    from services.kb57_scheduler import "
        "(\n"
        "        run_scheduled_tasks)\n"
        "    sched = await run_scheduled_tasks()\n"
        "    out['schedCollect'] = (\n"
        "        sched.get('collect') or {})"
        ".get('labeled')\n"
        "    out['schedFresh'] = (\n"
        "        sched.get('freshness') or {})"
        ".get('scanned')\n"
        # ⑨ 事件链+宪法
        "    events = await repo.list_events(limit=200)\n"
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

    print("\n[02 回流不依赖开关]")
    ok, (code, body) = call(
        "POST", "/api/kb57/feedback/collect",
        headers=ADMIN)
    record("off 态 collect 200(回流管理面)",
           code == 200,
           str((code, body.get("scanned"))))

    print("\n[03 容器内: 回流→补偿→调度]")
    r = container_pipeline(round_no)

    record("回流标注(4 种子信号)",
           r.get("scanned") == 4
           and r.get("labeled") == 4,
           str((r.get("scanned"),
                r.get("labeled"))))
    record("信号分布(四类)",
           (r.get("signals") or {})
           == {"seed_effective": 1,
               "seed_high_value": 1,
               "seed_weak": 1,
               "seed_recalled": 1},
           str(r.get("signals")))
    record("44号池双写(4 条+谱系)",
           r.get("poolCount") == 4
           and r.get("poolRewards")
           == sorted([1.0, 0.8, 0.3, -0.8]),
           str((r.get("poolCount"),
                r.get("poolRewards"))))
    record("Redis 回写(pooled+信号+奖励)",
           int(r.get("pooledId") or 0) > 0
           and r.get("poolSignal")
           == "seed_recalled"
           and r.get("poolReward") == -0.8,
           str((r.get("pooledId"),
                r.get("poolSignal"),
                r.get("poolReward"))))
    record("幂等(重复补标跳过)",
           r.get("idemLabeled") == 0
           and r.get("idemSkipped") == 4,
           str((r.get("idemLabeled"),
                r.get("idemSkipped"))))
    record("补偿(成功 1+失败 1)",
           r.get("compAttempted") == 2
           and r.get("compCompensated") == 1,
           str((r.get("compAttempted"),
                r.get("compCompensated"))))
    record("45号 L2 存证(≥1 条——deposit+score 双留痕)",
           (r.get("l2Deposits") or 0) >= 1,
           str(r.get("l2Deposits")))
    record("调度器独立执行(0 新增+4 扫描)",
           r.get("schedCollect") == 0
           and r.get("schedFresh") == 4,
           str((r.get("schedCollect"),
                r.get("schedFresh"))))
    record("事件链(learn_signal+compensate+"
           "scheduler_run)",
           {"learn_signal", "recall_compensate",
            "scheduler_run"} <= set(
               r.get("eventTypes") or []),
           str(r.get("eventTypes")))
    record("44号 32 档案保持",
           r.get("scorerCount") == 32,
           str(r.get("scorerCount")))

    print("\n[04 HTTP 端点+鉴权]")
    ok, (code, body) = call(
        "GET", "/api/kb57/feedback/stats",
        headers=ADMIN)
    record("HTTP stats 观测面(池 4)",
           code == 200
           and (body.get("poolSubmitted")
                or 0) >= 4,
           str((code,
                body.get("poolSubmitted"))))
    # 鉴权 403
    for method, path in (
            ("POST", "/api/kb57/feedback/collect"),
            ("GET", "/api/kb57/feedback/stats")):
        resp_ok, (c, _) = call(method, path)
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
