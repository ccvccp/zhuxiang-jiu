"""57号AI智能知识库 P0 Docker 实机验收

运行方式:
    python verify_kb57_p0_live.py [基址]

前置: 容器已运行(含 57号 P0 代码)。

覆盖(57号计划 §十一 P0, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(gaps/scan 409; 观测面可用)
    03 容器内: 注册表自检+强信号缺口创建
    04 宪法: 44号 32 档案+knowledge_*零改动
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
    # 信号种子(57号写入既有模块的只读域——清理防幂等)
    redis_del_keys("zhuxiang:knowledge:gap:*")
    redis_del_keys("zhuxiang:us52:*")
    redis_del_keys("zhuxiang:qr55:model_events*")
    redis_del_keys(
        "zhuxiang:qr55:qr55_model_events:*")
    redis_del_keys("zhuxiang:qr55:model_events_all")


def container_pipeline(round_no: int) -> dict:
    """容器内: 种信号→诊断→缺口结构核对(Redis 态)"""
    script = (
        "import asyncio, json, os\n"
        "os.environ['KB57_MODE'] = 'shadow'\n"
        "from core.helpers import ts\n"
        "from repositories.kb57_repository import "
        "Kb57Repository\n"
        "async def m():\n"
        "    out = {}\n"
        # 清理
        "    await Kb57Repository().reset_all()\n"
        # ① 种强信号(纯 ASCII——PowerShell→docker
        #    中文编码损坏防御, 56号 P4 教训)
        "    from repositories.knowledge_repository "
        "import KnowledgeRepository\n"
        "    krepo = KnowledgeRepository()\n"
        "    gid = await krepo.next_gap_id()\n"
        "    await krepo.save_gap({\n"
        "        'id': gid,\n"
        "        'question': "
        "'elderly subsidy online apply?',\n"
        "        'normQuestion': "
        "'elderly subsidy online apply',\n"
        "        'sessionId': 0, 'askCount': 3,\n"
        "        'status': 'open', 'entryId': 0,\n"
        "        'createdAt': ts(), 'lastAskedAt': ts(),\n"
        "        'resolvedAt': ''})\n"
        "    from repositories.us52_repository "
        "import Us52Repository\n"
        "    urepo = Us52Repository()\n"
        "    for inclusion in (0.8, 0.4):\n"
        "        sid = await urepo.next_snap_id()\n"
        "        await urepo.save_snapshot({\n"
        "            'snapId': sid, 'mode': 'kb57-live',\n"
        "            'sampleCount': 10, 'passedCount': 8,\n"
        "            'metrics': {'inclusion': {\n"
        "                'value': inclusion,\n"
        "                'baseline': 0.8,\n"
        "                'direction': 'higher_better'}},\n"
        "            'decision': 'pass', 'createdAt': ts()})\n"
        "    from repositories.qr55_repository "
        "import Qr55Repository\n"
        "    qrepo = Qr55Repository()\n"
        "    for sat in (80.0, 60.0):\n"
        "        meid = await qrepo."
        "next_model_event_id()\n"
        "        await qrepo.save_model_event({\n"
        "            'modelEventId': meid,\n"
        "            'eventType': 'metrics_snapshot',\n"
        "            'detail': {'metrics': {\n"
        "                'satisfactionScore': sat,\n"
        "                'clarifyEfficiency': 0.8,\n"
        "                'penetrationRate': 0.7}},\n"
        "            'createdAt': ts()})\n"
        # ② 诊断主链
        "    from services.kb57_service import "
        "Kb57Service\n"
        "    r = await Kb57Service()."
        "diagnose_and_plan()\n"
        "    out['decision'] = r.get('decision')\n"
        "    out['gapId'] = r.get('gapId')\n"
        "    out['necessity'] = r.get('necessityScore')\n"
        "    out['hitCount'] = r.get('hitCount')\n"
        # ③ 缺口结构(Redis 读回)
        "    gap = await Kb57Repository().get_gap(\n"
        "        int(r.get('gapId') or 0))\n"
        "    out['gapStatus'] = gap.get('status')\n"
        "    out['gapPriority'] = gap.get('priority')\n"
        "    out['snapHits'] = len(\n"
        "        (gap.get('signalSnapshot') or {})"
        ".get('hits') or [])\n"
        "    out['suggested'] = gap.get("
        "'suggestedSources')\n"
        "    out['budgetCap'] = gap.get('budgetCap')\n"
        # ④ 事件链
        "    events = await Kb57Repository()"
        ".list_events(limit=50)\n"
        "    types = sorted({e.get('eventType')\n"
        "                   for e in events})\n"
        "    out['eventTypes'] = types\n"
        # ⑤ 宪法
        "    from services.ai_learning_service "
        "import SCORER_REGISTRY\n"
        "    out['scorerCount'] = "
        "len(SCORER_REGISTRY)\n"
        "    out['knowledgeInReg'] = (\n"
        "        'knowledge_orchestration'\n"
        "        in SCORER_REGISTRY)\n"
        # ⑥ 既有 knowledge_* 零改动(缺口在册)
        "    out['kbGapCount'] = len(\n"
        "        await krepo.list_gaps(\n"
        "            status='open', limit=10))\n"
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
                          or "无输出")[-2000:]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_kb57(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/kb57/gaps/scan",
        headers=ADMIN, expect=(409,))
    record("off 态 gaps/scan 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/kb57/registry", headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and (body.get("total") or 0) == 10,
           str((code, body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/kb57/sources", headers=ADMIN)
    record("off 态 sources 观测面 200",
           code == 200
           and (body.get("total") or 0) == 6,
           str((code, body.get("total"))))

    print("\n[03 容器内: 诊断→缺口→事件]")
    r = container_pipeline(round_no)

    record("强信号 collect 决策",
           r.get("decision") == "collect",
           str((r.get("decision"),
                r.get("necessity"))))
    record("缺口创建(gapId>0)",
           int(r.get("gapId") or 0) > 0,
           str(r.get("gapId")))
    record("信号命中(≥3)",
           int(r.get("hitCount") or 0) >= 3,
           str(r.get("hitCount")))
    record("缺口结构(open+medium+cap 0.1)",
           r.get("gapStatus") == "open"
           and r.get("gapPriority") == "medium"
           and r.get("budgetCap") == 0.1,
           str((r.get("gapStatus"),
                r.get("gapPriority"),
                r.get("budgetCap"))))
    record("Redis 快照读回(≥3 命中)",
           int(r.get("snapHits") or 0) >= 3,
           str(r.get("snapHits")))
    record("建议采集源(白名单)",
           isinstance(r.get("suggested"), list)
           and len(r.get("suggested") or []) >= 1,
           str(r.get("suggested")))
    record("事件链(gap_create)",
           "gap_create" in (r.get("eventTypes")
                            or []),
           str(r.get("eventTypes")))
    record("44号 32 档案",
           r.get("scorerCount") == 32,
           str(r.get("scorerCount")))
    record("第32档案在册",
           r.get("knowledgeInReg") is True,
           str(r.get("knowledgeInReg")))
    record("既有 knowledge_* 零改动(缺口在册)",
           r.get("kbGapCount") == 1,
           str(r.get("kbGapCount")))

    print("\n[04 HTTP 端点+鉴权]")
    # 服务器态默认 off——决策面 409 已在 [02] 验证;
    # shadow 决策全链由容器内管道覆盖
    ok, (code, body) = call(
        "GET", "/api/kb57/gaps", headers=ADMIN)
    record("HTTP gaps 200(≥1 缺口)",
           code == 200
           and (body.get("total") or 0) >= 1,
           str((code, body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/kb57/model/status",
        headers=ADMIN)
    record("HTTP model/status 200(第32档案)",
           code == 200
           and ((body.get("status") or {})
                .get("scorerId")
                == "knowledge_orchestration"),
           str(code))
    # 鉴权 403
    for method, path in (
            ("GET", "/api/kb57/registry"),
            ("GET", "/api/kb57/gaps"),
            ("POST", "/api/kb57/gaps/scan"),
            ("GET", "/api/kb57/model/status")):
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
