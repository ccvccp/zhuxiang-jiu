"""58号AI智能优化意图识别 P4 Docker 实机验收

运行方式:
    python verify_ii58_p4_live.py [基址]

前置: 容器已运行(含 58号 P4 代码)。

覆盖(58号计划 §九 P4, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 调度器开关(容器态默认 off——
       II58_LEARN_MODE 未注入)
    03 容器内: 六类真值信号全链
       (resolved/越界/对抗/澄清/高置信错误/
       弱满足→44号池双写+幂等)
    04 校准预警闭环(3 条高置信错误→
       pending 建议→人工终审生效)
    05 调度手动触发轨(scheduler_run 留痕)
    06 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
ii58+ai46+voice48+ai_learning 键域)。
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


def clear_ii58(round_no: int) -> None:
    redis_del_keys("zhuxiang:ii58:*")
    redis_del_keys("zhuxiang:ai46:*")
    redis_del_keys("zhuxiang:voice48:*")
    redis_del_keys("zhuxiang:ai_learning:*")


# 容器内管道(纯 ASCII+中文 unicode 转义)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['II58_MODE'] = 'shadow'\n"
    "from core.helpers import ts\n"
    "async def m():\n"
    "    out = {}\n"
    "    from repositories.ii58_repository import "
    "Ii58Repository\n"
    "    repo = Ii58Repository()\n"
    "    async def seed(intent, text, stype="
    "'positive'):\n"
    "        cid = await repo.next_corpus_id()\n"
    "        await repo.save_corpus({\n"
    "            'corpusId': cid, 'corpusVersion': 1,\n"
    "            'intentId': intent,\n"
    "            'sampleType': stype,\n"
    "            'text': text, 'weight': 1.0,\n"
    "            'source': 'manual', 'originRef': '',\n"
    "            'confusableTarget': None,\n"
    "            'humanVerified': True,\n"
    "            'humanSuggested': False,\n"
    "            'status': 'active',\n"
    "            'createdAt': ts(), 'updatedAt': ts()})\n"
    "    await seed('product.price_query', 'how much')\n"
    "    await seed('product.price_query', "
    "'modify price')\n"
    "    await seed('product.price_query', "
    "'modify price', 'adversarial')\n"
    "    await seed('boundary.unauthorized', "
    "'delete all data')\n"
    # 44号 sync(池前置)
    "    from services.ai_governance_service "
    "import AiGovernanceService\n"
    "    gov = AiGovernanceService()\n"
    "    if await gov.repo.get_gov(\n"
    "            'intent_orchestration') is None:\n"
    "        await gov.sync_registry()\n"
    # ① 六类信号评估
    "    from services.ii58_service import "
    "Ii58Service\n"
    "    svc = Ii58Service()\n"
    "    ev1 = await svc.evaluate('how much')\n"
    "    ev2 = await svc.evaluate('delete all data',\n"
    "                            member_role='guest')\n"
    "    ev3 = await svc.evaluate('modify price')\n"
    "    ev4 = await svc.evaluate('xyzzyx')\n"
    # ② 高置信错误(显式反馈×3——预警触发)
    "    os.environ['II58_MODE'] = 'assist'\n"
    "    from services.ii58_feedback_service "
    "import Ii58FeedbackService\n"
    "    fb = Ii58FeedbackService()\n"
    "    for i in (1, 2, 3):\n"
    "        ev = await svc.evaluate('how much',\n"
    "                                member_id=i)\n"
    "        await fb.submit_feedback(\n"
    "            member_id=i, eval_id=ev['evalId'],\n"
    "            text=f'wrong {i}',\n"
    "            corrected_intent_id="
    "'product.new_query')\n"
    "    os.environ['II58_MODE'] = 'off'\n"
    # ③ collect(回流管理面 off 亦可用)
    "    from services.ii58_learn_service import "
    "Ii58LearnService\n"
    "    learn = Ii58LearnService()\n"
    "    r = await learn.collect_feedback()\n"
    "    out['signals'] = r.get('signals')\n"
    "    out['labeled'] = r.get('labeled')\n"
    "    out['skipped'] = r.get('skipped')\n"
    "    out['alert'] = r.get('calibrationAlert')\n"
    # ④ 池双写验证
    "    from repositories.ai_learning_repository "
    "import AiLearningRepository\n"
    "    pool = AiLearningRepository()\n"
    "    pool_fbs = await pool.list_feedback(\n"
    "        'intent_orchestration', limit=100)\n"
    "    out['pool_n'] = len(pool_fbs)\n"
    "    out['pool_rewards'] = sorted(\n"
    "        {float(f.get('reward') or 0)\n"
    "         for f in pool_fbs})\n"
    # ⑤ pooled 回写
    "    stored1 = await repo.get_evaluation(\n"
    "        ev1['evalId'])\n"
    "    out['pooled'] = (\n"
    "        stored1.get('pooledFeedbackId'),\n"
    "        stored1.get('poolSignal'),\n"
    "        stored1.get('poolReward'))\n"
    # ⑥ 幂等(重复 collect)
    "    r2 = await learn.collect_feedback()\n"
    "    out['idem_labeled'] = r2.get('labeled')\n"
    # ⑦ 预警终审生效
    "    mirror = await repo.get_threshold(\n"
    "        'baseline')\n"
    "    out['mirror_status'] = (\n"
    "        mirror or {}).get('status')\n"
    "    out['mirror_cid'] = (\n"
    "        mirror or {}).get('changeId')\n"
    "    rv = await svc.review_calibration(\n"
    "        int((mirror or {}).get('changeId')),\n"
    "        approve=True, reviewer='admin')\n"
    "    out['rv_status'] = rv.get('status')\n"
    "    os.environ['II58_MODE'] = 'shadow'\n"
    "    ev_after = await svc.evaluate('anything')\n"
    "    out['th_after'] = (\n"
    "        (ev_after.get('attribution') or {})"
    ".get(\n"
    "            'thresholds') or {}).get('upper')\n"
    "    os.environ['II58_MODE'] = 'off'\n"
    # ⑧ learn_signal 事件
    "    evs = await repo.list_events(\n"
    "        event_type='learn_signal', limit=50)\n"
    "    out['signal_evs'] = len(evs)\n"
    # ⑨ 调度手动触发
    "    from services.ii58_scheduler import (\n"
    "        run_scheduled_tasks,\n"
    "        scheduler_enabled)\n"
    "    out['sched_off'] = not scheduler_enabled()\n"
    "    sched = await run_scheduled_tasks()\n"
    "    out['sched_collect'] = (\n"
    "        (sched.get('collect') or {})"
    ".get('labeled'))\n"
    "    sched_evs = await repo.list_events(\n"
    "        event_type='scheduler_run', limit=10)\n"
    "    out['sched_evs'] = len(sched_evs)\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


def container_pipeline(round_no: int) -> dict:
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", PIPELINE],
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
    clear_ii58(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02-05 容器内: 回流+预警+调度]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    signals = r.get("signals") or {}
    record("识别正确+执行成功",
           signals.get("correct_executed") == 1,
           str(signals))
    record("越界拦截正确",
           signals.get("boundary_correct") == 1,
           str(signals))
    record("对抗混淆命中",
           signals.get("adversarial_confusion")
           == 1,
           str(signals))
    record("澄清/覆盖缺口",
           signals.get("coverage_gap") == 1,
           str(signals))
    record("高置信错误(3 条预警触发)",
           signals.get("high_conf_error") == 3,
           str(signals))
    record("池双写(第33档案 7 条)",
           r.get("pool_n") == 7,
           str(r.get("pool_n")))
    record("池 reward 域(五信号值)",
           r.get("pool_rewards")
           == [-0.8, -0.6, -0.5,
               0.6, 1.0],
           str(r.get("pool_rewards")))
    pooled = r.get("pooled") or [0, "", 0]
    record("pooled 回写(标记+信号)",
           pooled[0] > 0
           and pooled[1] == "correct_executed",
           str(r.get("pooled")))
    record("幂等(重复 collect 0 新增)",
           r.get("idem_labeled") == 0,
           str(r.get("idem_labeled")))
    alert = r.get("alert") or {}
    record("预警触发(pending 建议)",
           alert.get("triggered") is True
           and alert.get("proposedUpper") == 0.92,
           str(alert)[:60])
    record("预警镜像 pending",
           r.get("mirror_status") == "pending",
           str(r.get("mirror_status")))
    record("预警终审生效(0.92)",
           r.get("rv_status") == "active"
           and r.get("th_after") == 0.92,
           str((r.get("rv_status"),
                r.get("th_after"))))
    record("learn_signal 事件(7 条)",
           r.get("signal_evs") == 7,
           str(r.get("signal_evs")))
    record("调度开关(容器态 off)",
           r.get("sched_off") is True,
           str(r.get("sched_off")))
    # 'anything' 评估(clarify)在调度轨入池
    record("调度手动触发(collect 轨)",
           r.get("sched_collect") == 1,
           str(r.get("sched_collect")))
    record("scheduler_run 留痕",
           r.get("sched_evs") == 1,
           str(r.get("sched_evs")))

    print("\n[06 HTTP 端点+鉴权]")
    ok, (code, body) = call(
        "POST", "/api/ii58/feedback/collect",
        body={}, headers=ADMIN)
    record("HTTP collect 200(幂等 0 新增)",
           code == 200
           and (body.get("labeled") or 0) == 0,
           str((code, body.get("labeled"))))
    ok, (code, _) = call(
        "POST", "/api/ii58/feedback/collect",
        body={}, expect=(403,))
    record("HTTP collect 无 Role 403",
           code == 403, str(code))
    # 路由累计 17
    script = (
        "from routes.ii58_routes import router\n"
        "print(sum(1 for r in router.routes))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        count = int((out.stdout or "").strip())
    except ValueError:
        count = -1
    record("58号路由累计 ≥17 端点",
           count >= 17, str(count))


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
