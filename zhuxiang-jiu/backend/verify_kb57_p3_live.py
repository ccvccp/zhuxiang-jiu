"""57号AI智能知识库 P3 Docker 实机验收

运行方式:
    python verify_kb57_p3_live.py [基址]

前置: 容器已运行(含 57号 P0-P3 代码)。

覆盖(57号计划 §十一 P3, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(会员面 409)
    03 容器内: 推荐→浏览→反馈→路径→触发全链
       (Redis 序列化读回)
    04 宪法: 44号 32 档案保持
    05 HTTP 会员面+鉴权

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
MEMBER = {"X-Member-Id": "9001"}

CONTAINER = "zhuxiang-jiu-backend-1"
REDIS = "zhuxiang-jiu-redis-1"


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f" ✗ {name} — {detail}".replace(
            " ", " ", 1))


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
    # 会员 9001 预算(幂等)
    redis_del_keys(
        "zhuxiang:voice48:voice48_privacy_budget:9001")


def container_pipeline(round_no: int) -> dict:
    """容器内: 推荐→浏览→反馈→路径→触发全链"""
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
        "    member = 9001\n"
        # ① 种 published 种子×2(citizen 向/staff 向)
        "    async def mk_seed(tags, title):\n"
        "        gap_id = await repo.next_gap_id()\n"
        "        await repo.save_gap({\n"
        "            'gapId': gap_id,\n"
        "            'status': 'resolved',\n"
        "            'priority': 'medium',\n"
        "            'topic': 'helper',\n"
        "            'signalSnapshot': {'hits': []},\n"
        "            'suggestedSources': [],\n"
        "            'budgetCap': 0.1,\n"
        "            'budgetSpent': 0.0,\n"
        "            'createdAt': ts(),\n"
        "            'updatedAt': ts()})\n"
        "        seed_id = await repo.next_seed_id()\n"
        "        raw = f's{seed_id}'.encode()\n"
        "        await repo.save_seed({\n"
        "            'seedId': seed_id,\n"
        "            'seedVersion': 1,\n"
        "            'type': 'text',\n"
        "            'title': title,\n"
        "            'content': {'text': 'c',\n"
        "                'mediaRef': None,\n"
        "                'transcript': None,\n"
        "                'keyframes': None,\n"
        "                'alt': None},\n"
        "            'contentHash': 'sha256:x',\n"
        "            'complianceFingerprint':\n"
        "                'sha256:' + hashlib.sha256("
        "raw).hexdigest()[:32],\n"
        "            'valueTags': tags,\n"
        "            'sourceId': "
        "'gov_policy_official',\n"
        "            'sourceCredibility': 0.95,\n"
        "            'privacyCost': 0.002,\n"
        "            'knowledgeReason': 'live-p3',\n"
        "            'humanVerified': True,\n"
        "            'validUntil': '2099-01-01',\n"
        "            'abTest': {'active': False,\n"
        "                       'variantOf': None},\n"
        "            'status': 'published',\n"
        "            'gapId': gap_id,\n"
        "            'resourceId': 0,\n"
        "            'viewCount': 0,\n"
        "            'positiveCount': 0,\n"
        "            'negativeCount': 0,\n"
        "            'pooledFeedbackId': 0,\n"
        "            'llmCalls': 0,\n"
        "            'createdAt': ts(),\n"
        "            'updatedAt': ts()})\n"
        "        return seed_id\n"
        "    s1 = await mk_seed(\n"
        "        ['elderly_service', 'policy'],\n"
        "        'elderly subsidy guide')\n"
        "    s2 = await mk_seed(\n"
        "        ['sop', 'workflow'],\n"
        "        'staff sop manual')\n"
        # ② 推荐流(citizen×service 置顶)
        "    from services.kb57_feed_service import "
        "Kb57FeedService\n"
        "    fs = Kb57FeedService()\n"
        "    fd = await fs.feed(member, role='citizen',\n"
        "                       scene='service')\n"
        "    recs = fd.get('recommendations') or []\n"
        "    out['feedTotal'] = fd.get('total')\n"
        "    out['feedTop'] = (recs[0].get('seedId')\n"
        "                      if recs else None)\n"
        "    out['s1'] = s1\n"
        "    out['s2'] = s2\n"
        # ③ 浏览(指纹校验+预算+计数)
        "    v = await fs.view(member, s1)\n"
        "    out['viewOk'] = v.get('success')\n"
        "    out['viewCount'] = (await repo.get_seed("
        "s1)).get('viewCount')\n"
        "    out['budgetSpent'] = v.get("
        "'budgetSpent')\n"
        # ④ 已学折减(再 feed s1 出池)
        "    fd2 = await fs.feed(member,\n"
        "                        role='citizen',\n"
        "                        scene='service')\n"
        "    recs2 = fd2.get('recommendations') or []\n"
        "    out['s1Dropped'] = s1 not in [\n"
        "        x.get('seedId') for x in recs2]\n"
        # ⑤ 反馈(正向)
        "    fb = await fs.feedback(\n"
        "        member, s2, kind='positive')\n"
        "    out['fbKind'] = fb.get('kind')\n"
        "    out['fbRecall'] = fb.get("
        "'suggestRecall')\n"
        # ⑥ 路径(创建+推进×2 全完成)
        "    p = await fs.create_path(\n"
        "        member, seed_ids=[s1, s2],\n"
        "        title='live course')\n"
        "    pid = p.get('pathId')\n"
        "    out['pathId'] = pid\n"
        "    a1 = await fs.advance_path(\n"
        "        member, pid, s1)\n"
        "    a2 = await fs.advance_path(\n"
        "        member, pid, s2)\n"
        "    out['advDone'] = a2.get('completed')\n"
        "    out['advSeeds'] = a2.get("
        "'completedSeeds')\n"
        "    stored_p = await repo.get_path(pid)\n"
        "    out['progDict'] = isinstance(\n"
        "        stored_p.get('progress'), dict)\n"
        # ⑦ 情境触发(query 匹配)
        "    ct = await fs.context_trigger(\n"
        "        member, trigger_type='search_miss',\n"
        "        query='elderly policy')\n"
        "    out['trigSeeds'] = ct.get("
        "'matchedSeeds')\n"
        # ⑧ 我的学习
        "    ml = await fs.my_learning(member)\n"
        "    out['mlHistory'] = len(\n"
        "        ml.get('history') or [])\n"
        "    out['mlPaths'] = len(\n"
        "        ml.get('paths') or [])\n"
        # ⑨ 事件链+宪法
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

    print("\n[02 off 铁律(会员面)]")
    ok, (code, _) = call(
        "GET", "/api/kb57/feed", headers=MEMBER,
        expect=(409,))
    record("off 态 feed 409",
           code == 409, str(code))

    print("\n[03 容器内: 推荐→浏览→反馈→路径→触发]")
    r = container_pipeline(round_no)

    record("推荐流(citizen 置顶 s1)",
           r.get("feedTotal") == 2
           and r.get("feedTop") == r.get("s1"),
           str((r.get("feedTotal"),
                r.get("feedTop"),
                r.get("s1"))))
    record("浏览成功(指纹校验+预算 0.01)",
           r.get("viewOk") is True
           and r.get("viewCount") == 1
           and r.get("budgetSpent") == 0.01,
           str((r.get("viewOk"),
                r.get("viewCount"),
                r.get("budgetSpent"))))
    record("已学折减(s1 出池)",
           r.get("s1Dropped") is True,
           str(r.get("s1Dropped")))
    record("正向反馈(无召回建议)",
           r.get("fbKind") == "positive"
           and r.get("fbRecall") is False,
           str((r.get("fbKind"),
                r.get("fbRecall"))))
    record("路径全完成(2/2)",
           r.get("advDone") is True
           and r.get("advSeeds") == 2,
           str((r.get("advSeeds"),
                r.get("advDone"))))
    record("Redis 进度读回(dict)",
           r.get("progDict") is True,
           str(r.get("progDict")))
    record("情境触发(query 匹配 s1)",
           r.get("s1") in (
               r.get("trigSeeds") or []),
           str(r.get("trigSeeds")))
    record("我的学习(历史+路径)",
           (r.get("mlHistory") or 0) >= 2
           and (r.get("mlPaths") or 0) == 1,
           str((r.get("mlHistory"),
                r.get("mlPaths"))))
    record("事件链(view/feedback/path/trigger)",
           {"seed_view", "seed_feedback",
            "path_create", "path_complete",
            "context_trigger"} <= set(
               r.get("eventTypes") or []),
           str(r.get("eventTypes")))
    record("44号 32 档案保持",
           r.get("scorerCount") == 32,
           str(r.get("scorerCount")))

    print("\n[04 HTTP 会员面+鉴权]")
    # off 态 409 已在 [02]; HTTP 全链
    # 由容器内管道覆盖(服务器态默认 off)
    for method, path in (
            ("GET", "/api/kb57/feed"),
            ("GET", "/api/kb57/my/learning")):
        ok, (c, _) = call(method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Member 403", c == 403, str(c))


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
