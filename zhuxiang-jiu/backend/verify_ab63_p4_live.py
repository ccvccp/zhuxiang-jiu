"""63号AI智能后台管理 P4 Docker 实机验收

运行方式:
    # 容器以 shadow 态启动(HTTP 决策面
    # 正向验证——compose 支持 AB63_MODE
    # 环境变量注入):
    $env:AB63_MODE="shadow"
    docker compose -p zhuxiang-jiu up -d backend
    python verify_ab63_p4_live.py [基址]

覆盖(63号计划 §九 P4, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-05 容器内: 反馈闭环全链
       (高频驳回→培训推送→完成→
        六类信号池双写幂等→
        自动过审错误率预警经 46号→
        调度器手动轮)
    06 HTTP 面(training/push+
       complete+视图+collect)

×2 轮幂等验证(每轮清理种子重造——
ab63 键域)。
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


def clear_ab63(round_no: int) -> None:
    redis_del_keys("zhuxiang:ab63:*")
    redis_del_keys("zhuxiang:ai_learning:*")
    redis_del_keys("zhuxiang:ai_governance:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AB63_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    "    CLEAN = ('居家养老服务 服务有效期90天'\n"
    "             ' 退改政策可退')\n"
    "    EXAG = ('全市最好的居家养老服务 '\n"
    "            '服务有效期90天 退改政策可退')\n"
    "    from services.ab63_submission_service "
    "import (\n"
    "        Ab63SubmissionService)\n"
    "    from services.ab63_training_service "
    "import (\n"
    "        Ab63TrainingService)\n"
    "    from services.ab63_learn_service "
    "import (\n"
    "        Ab63LearnService)\n"
    "    from services.ai_governance_service "
    "import (\n"
    "        AiGovernanceService)\n"
    "    await AiGovernanceService(\n"
    "    ).sync_registry()\n"
    "    sub = Ab63SubmissionService()\n"
    "    train = Ab63TrainingService()\n"
    "    learn = Ab63LearnService()\n"
    # ① 造高频驳回(单规则 EXAG×2)
    "    for _ in range(2):\n"
    "        s = await sub.submit(\n"
    "            10, 'ally_merchant',\n"
    "            content=EXAG,\n"
    "            tier='standard')\n"
    "        await sub.review(\n"
    "            s['subId'], approve=False,\n"
    "            reviewer='审核员')\n"
    # ② 培训推送
    "    p = await train.push()\n"
    "    out['push_n'] = p.get('pushed')\n"
    "    out['push_rule'] = (\n"
    "        (p.get('pushes') or [{}])[0]\n"
    "        .get('ruleId')\n"
    "        if p.get('pushes') else None)\n"
    "    out['tid'] = (\n"
    "        (p.get('pushes') or [{}])[0]\n"
    "        .get('trainingId')\n"
    "        if p.get('pushes') else None)\n"
    # ③ 推送幂等
    "    p2 = await train.push()\n"
    "    out['push_idem'] = (\n"
    "        p2.get('pushed'))\n"
    # ④ 培训完成
    "    c = await train.complete(\n"
    "        out['tid'], member_id=10)\n"
    "    out['complete_status'] = (\n"
    "        c.get('status'))\n"
    # ⑤ 造四类信号(L1/human/approved
    #    /rejected/appeal_overturn)
    "    await sub.submit(\n"
    "        11, 'ally_merchant',\n"
    "        content=CLEAN, tier='trusted')\n"
    "    s2 = await sub.submit(\n"
    "        12, 'ally_merchant',\n"
    "        content=CLEAN, tier='standard')\n"
    "    await sub.review(\n"
    "        s2['subId'], approve=True,\n"
    "        reviewer='审核员')\n"
    "    s3 = await sub.submit(\n"
    "        13, 'ally_merchant',\n"
    "        content=EXAG, tier='standard')\n"
    "    await sub.review(\n"
    "        s3['subId'], approve=False,\n"
    "        reviewer='审核员')\n"
    "    await sub.appeal(\n"
    "        s3['subId'], appellant='member')\n"
    "    await sub.resolve_appeal(\n"
    "        s3['subId'], overturn=True,\n"
    "        adjudicator='合规官')\n"
    # ⑥ 池双写(off 亦可用——铁律)
    "    os.environ['AB63_MODE'] = 'off'\n"
    "    try:\n"
    "        await train.push()\n"
    "        out['push_off_reject'] = False\n"
    "    except ValueError:\n"
    "        out['push_off_reject'] = True\n"
    "    r = await learn.collect_feedback()\n"
    "    out['labeled'] = r.get('labeled')\n"
    "    out['signals'] = r.get('signals')\n"
    "    out['pool_n'] = r.get(\n"
    "        'poolSubmitted')\n"
    # ⑦ 幂等(二轮全跳过)
    "    r2 = await learn.collect_feedback()\n"
    "    out['collect_idem'] = (\n"
    "        r2.get('labeled'))\n"
    # ⑧ 培训视图(完成率 100%)
    "    v = await train.training_view()\n"
    "    out['view_total'] = v.get('total')\n"
    "    out['conversion'] = v.get(\n"
    "        'conversionRate')\n"
    # ⑨ 调度器手动轮
    "    from services.ab63_scheduler "
    "import (\n"
    "        run_scheduled_tasks,\n"
    "        scheduler_enabled)\n"
    "    out['sched_off'] = (\n"
    "        scheduler_enabled())\n"
    "    sr = await run_scheduled_tasks()\n"
    "    out['sched_ok'] = (\n"
    "        'collect' in sr\n"
    "        and 'training' in sr)\n"
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
    clear_ab63(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02-05 容器内: 反馈闭环全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("高频驳回推送(≥2 次)",
           r.get("push_n") == 1
           and r.get("push_rule")
           == "GUARD_EXAGGERATION",
           str((r.get("push_n"),
                r.get("push_rule"))))
    record("推送幂等(不重复)",
           r.get("push_idem") == 0,
           str(r.get("push_idem")))
    record("培训完成(completed)",
           r.get("complete_status")
           == "completed",
           str(r.get("complete_status")))
    record("六类信号池双写(5 信号)",
           r.get("labeled") == 5
           and r.get("signals", {}).get(
               "l1_auto_clean") == 1
           and r.get("signals", {}).get(
               "human_approved") == 1
           and r.get("signals", {}).get(
               "human_rejected") == 2
           and r.get("signals", {}).get(
               "appeal_overturn") == 1
           and r.get("pool_n") == 5,
           str((r.get("labeled"),
                r.get("signals"),
                r.get("pool_n"))))
    record("off 态推送拒绝(铁律)",
           r.get("push_off_reject") is True,
           str(r.get("push_off_reject")))
    record("回流幂等(二轮跳过)",
           r.get("collect_idem") == 0,
           str(r.get("collect_idem")))
    record("培训视图(100% 转化)",
           r.get("view_total") == 1
           and r.get("conversion")
           == 100.0,
           str((r.get("view_total"),
                r.get("conversion"))))
    record("调度器默认 off+手动轮",
           r.get("sched_off") is False
           and r.get("sched_ok") is True,
           str((r.get("sched_off"),
                r.get("sched_ok"))))

    print("\n[06 HTTP 面]")
    # 回流 off 亦可用(容器 shadow 态下
    # HTTP 决策面开放; off 铁律已在
    # 管道内服务级验证)
    ok, (code, body) = call(
        "POST", "/api/ab63/feedback/collect",
        body={}, headers=ADMIN)
    record("HTTP collect(off 亦可用)",
           code == 200
           and (body.get("labeled")
                or 0) == 0,
           str((code,
                body.get("labeled"))))
    # 培训视图(观测面)
    ok, (code, body) = call(
        "GET", "/api/ab63/training",
        headers=ADMIN)
    record("HTTP training 视图",
           code == 200
           and (body.get("total")
                or 0) == 1,
           str((code,
                body.get("total"))))
    # 培训完成 404(不存在)
    ok, (code, _) = call(
        "POST",
        "/api/ab63/training/99999/complete",
        body={}, headers=ADMIN,
        expect=(404,))
    record("HTTP training 404",
           code == 404, str(code))


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
