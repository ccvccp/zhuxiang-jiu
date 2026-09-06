"""61号AI智能系统升级决策 P4 Docker 实机验收

运行方式:
    python verify_dm61_p4_live.py [基址]
    (回流/预警均为不受开关影响面;
     容器任意态均可验证——默认 off)

覆盖(61号计划 §七 P4, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-04 容器内: 反哺全链
       (七类终态信号池双写+幂等+
        置信度校准预警 46号 pending+
        调度器手动轮)
    05 HTTP 面(collect off 可用+幂等)

×2 轮幂等验证(每轮清理种子重造——
dm61+ai46 change 键域)。
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


def clear_dm61(round_no: int) -> None:
    redis_del_keys("zhuxiang:dm61:*")
    redis_del_keys("zhuxiang:ai46:change*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['DM61_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # 46号入册(幂等)
    "    from services.ai_governance_service "
    "import (\n"
    "        AiGovernanceService)\n"
    "    await (AiGovernanceService()\n"
    "          .sync_registry())\n"
    "    from services.dm61_service import (\n"
    "        Dm61Service)\n"
    "    from services.dm61_assess_service "
    "import (\n"
    "        Dm61AssessService)\n"
    "    from services.dm61_decision_service "
    "import (\n"
    "        Dm61DecisionService)\n"
    "    from services.dm61_feedback_service "
    "import (\n"
    "        Dm61FeedbackService)\n"
    "    from services.dm61_dissent_service "
    "import (\n"
    "        Dm61DissentService)\n"
    "    from services.dm61_learn_service "
    "import (\n"
    "        Dm61LearnService)\n"
    "    base = Dm61Service()\n"
    "    asvc = Dm61AssessService()\n"
    "    dsvc = Dm61DecisionService()\n"
    "    fsvc = Dm61FeedbackService()\n"
    "    disvc = Dm61DissentService()\n"
    # 终态种子助手
    "    async def seed(title, action,\n"
    "                  fb_action=None,\n"
    "                  fb_outcome=None):\n"
    "        r = await base.create_request(\n"
    "            title, hour=3)\n"
    "        await asvc.assess(\n"
    "            r['requestId'],\n"
    "            tier='standard',\n"
    "            error_budget=0.3,\n"
    "            history_fail_rate=0.05)\n"
    "        rec = await dsvc.recommend(\n"
    "            r['requestId'])\n"
    "        os.environ['DM61_MODE'] "
    "= 'off'\n"
    "        md = '追加观察' \\\n"
    "            if action == 'modified' \\\n"
    "            else ''\n"
    "        d = await dsvc.decide(\n"
    "            rec['decisionId'],\n"
    "            action=action,\n"
    "            decided_by='种子',\n"
    "            modified_detail=md)\n"
    "        if d.get('changeId'):\n"
    "            try:\n"
    "                await (\n"
    "                  AiGovernanceService()\n"
    "                  .review_change(\n"
    "                    int(d['changeId']),\n"
    "                    approve=False,\n"
    "                    reviewed_by='官',\n"
    "                    review_note='解锁'))\n"
    "            except ValueError:\n"
    "                pass\n"
    "        if fb_action:\n"
    "            await fsvc.submit(\n"
    "                rec['decisionId'],\n"
    "                action=fb_action,\n"
    "                outcome=fb_outcome)\n"
    "        os.environ['DM61_MODE'] "
    "= 'shadow'\n"
    "        return rec\n"
    # 种七类各一(播种期 shadow——种子
    # 助手内部切 off 裁决后还原)
    "    await seed('支付费率优化',\n"
    "              'adopted', 'adopted',\n"
    "              'good')\n"
    "    await seed('支付费率优化二',\n"
    "              'adopted', 'adopted',\n"
    "              'bad')\n"
    "    await seed('界面优化一',\n"
    "              'adopted')\n"
    "    await seed('界面优化二',\n"
    "              'modified',\n"
    "              'modified', 'good')\n"
    "    await seed('界面优化三',\n"
    "              'modified',\n"
    "              'modified', 'bad')\n"
    "    await seed('权重调整一',\n"
    "              'rejected')\n"
    # dissent_confirmed
    "    r7 = await base.create_request(\n"
    "        '合规规则调整', hour=3)\n"
    "    await asvc.assess(\n"
    "        r7['requestId'],\n"
    "        tier='standard',\n"
    "        error_budget=0.3,\n"
    "        history_fail_rate=0.05)\n"
    "    rec7 = await dsvc.recommend(\n"
    "        r7['requestId'])\n"
    "    await disvc.raise_dissent(\n"
    "        rec7['decisionId'],\n"
    "        reason='存疑')\n"
    "    await disvc.resolve(\n"
    "        rec7['decisionId'],\n"
    "        action='confirm',\n"
    "        reason='AI 质疑成立',\n"
    "        resolved_by='官')\n"
    # ① 七类池双写(off 亦可用——回流
    #    铁律验证)
    "    os.environ['DM61_MODE'] = 'off'\n"
    "    lsvc = Dm61LearnService()\n"
    "    r = await lsvc.collect_feedback()\n"
    "    out['labeled'] = r.get('labeled')\n"
    "    out['signals'] = r.get('signals')\n"
    "    out['pool_n'] = (\n"
    "        r.get('poolSubmitted'))\n"
    "    out['alert'] = (\n"
    "        r.get('calibrationAlert')\n"
    "        is not None)\n"
    # ② 幂等(二轮全跳过)
    "    r2 = await (\n"
    "        lsvc.collect_feedback())\n"
    "    out['idem'] = (\n"
    "        r2.get('labeled'))\n"
    "    out['idem_skip'] = (\n"
    "        r2.get('skipped'))\n"
    # ③ 调度器(默认 off+手动轮)
    "    from services.dm61_scheduler "
    "import (\n"
    "        scheduler_enabled,\n"
    "        run_scheduled_tasks)\n"
    "    out['sched_off'] = (\n"
    "        scheduler_enabled())\n"
    "    sr = await (\n"
    "        run_scheduled_tasks())\n"
    "    out['sched_collect'] = (\n"
    "        (sr.get('collect') or {})\n"
    "        .get('labeled'))\n"
    # ④ Redis 读回(pooled 标记)
    "    from repositories.dm61_repository "
    "import (\n"
    "        Dm61Repository)\n"
    "    repo = Dm61Repository()\n"
    "    decs = await repo.list_decisions(\n"
    "        limit=20)\n"
    "    pooled = [d for d in decs\n"
    "              if int(d.get(\n"
    "                'pooledFeedbackId')\n"
    "                or 0) > 0]\n"
    "    out['pooled_n'] = len(pooled)\n"
    "    sig_rewards = {\n"
    "        d.get('poolSignal'):\n"
    "        d.get('poolReward')\n"
    "        for d in decs}\n"
    "    out['rw_good'] = sig_rewards.get(\n"
    "        'adopted_good')\n"
    "    out['rw_bad'] = sig_rewards.get(\n"
    "        'adopted_bad')\n"
    "    out['rw_dissent'] = (\n"
    "        sig_rewards.get(\n"
    "            'dissent_validated'))\n"
    # ⑤ 事件链
    "    evs = await repo.list_events(\n"
    "        limit=200)\n"
    "    types = sorted({\n"
    "        e.get('eventType')\n"
    "        for e in evs})\n"
    "    out['ev_types'] = types\n"
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
    clear_dm61(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02-04 容器内: 反哺全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("七类信号全标注",
           r.get("labeled") == 7
           and set((r.get("signals")
                    or {})) == {
               "adopted_good",
               "adopted_bad",
               "adopted_unverified",
               "modified_good",
               "modified_bad",
               "recommendation_rejected",
               "dissent_validated"},
           str(r.get("signals")))
    record("池双写(7 笔)",
           r.get("pool_n") == 7,
           str(r.get("pool_n")))
    record("校准预警(偏差触发)",
           r.get("alert") is True,
           str(r.get("alert")))
    record("回流幂等(二轮跳过)",
           r.get("idem") == 0
           and r.get("idem_skip") == 7,
           str((r.get("idem"),
                r.get("idem_skip"))))
    record("调度器默认 off",
           r.get("sched_off") is False,
           str(r.get("sched_off")))
    record("手动轮执行(collect 幂等)",
           r.get("sched_collect") == 0,
           str(r.get("sched_collect")))
    record("pooled 标记回写(7)",
           r.get("pooled_n") == 7,
           str(r.get("pooled_n")))
    record("奖励映射(good+1/bad-1)",
           r.get("rw_good") == 1.0
           and r.get("rw_bad") == -1.0,
           str((r.get("rw_good"),
                r.get("rw_bad"))))
    record("dissent 奖励(+1)",
           r.get("rw_dissent") == 1.0,
           str(r.get("rw_dissent")))
    record("事件链(learn_signal+"
           "scheduler_run)",
           all(t in (r.get("ev_types") or [])
               for t in ("learn_signal",
                         "scheduler_run")),
           str(r.get("ev_types")))

    print("\n[05 HTTP 面]")
    # HTTP collect(off 可用+幂等)
    ok, (code, body) = call(
        "POST", "/api/dm61/feedback/collect",
        body={}, headers=ADMIN)
    record("HTTP collect off 可用",
           code == 200
           and (body or {}).get("labeled")
           == 0,
           str((code,
                (body or {}).get(
                    "labeled"))))
    # 鉴权 403
    ok, (code, _) = call(
        "POST", "/api/dm61/feedback/collect",
        body={})
    record("HTTP collect 无 Role 403",
           code == 403, str(code))
    # 路由累计 15
    script = (
        "from routes.dm61_routes import "
        "router\n"
        "print(sum(1 for r in "
        "router.routes))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        count = int((out.stdout or "").strip())
    except ValueError:
        count = -1
    record("61号路由累计 15 端点",
           count == 15, str(count))


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
