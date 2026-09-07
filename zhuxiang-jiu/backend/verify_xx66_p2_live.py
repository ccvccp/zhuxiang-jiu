"""66号·AI智能工程师大模块 P2 诊断与自愈编排
Docker 实机验收(verify_xx66_p2_live)

运行方式:
    python verify_xx66_p2_live.py [基址]

前置: 容器已运行(含 66号 P2 代码
      ——XX66_MODE 默认 off)。

覆盖(真实容器 Redis 态):
    01 健康面+status(P0/P1 回归)
    02 决策面 off 409 ×3 + 鉴权 403
    03 观测面(predictions 空态/log/verify 空链)
    04 根因+预演+白名单管道(容器内 shadow:
       规则命中/白名单外降级/预演结构)
    05 manual 强制人工管道(容器内 assist:
       manual_required 零编排)
    06 shadow 建议书管道(容器内: 诊断补链+
       建议书留痕+不执行)
    07 assist 执行轨管道(容器内: 白名单+预演
       →27号任务轨+attempt 补链终态)
    08 故障预测管道(容器内: 三条件命中/幂等/
       特征留痕)+log/verify 链校验

×2 轮幂等验证(每轮清理 xx66/maintenance/monitor 种子域)。
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
        with urllib.request.urlopen(req, timeout=30) as r:
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


def clear_xx66(round_no: int) -> None:
    print(f"\n—— 第 {round_no} 轮: 清理种子域 ——")
    redis_del_keys("zhuxiang:xx66:*")
    redis_del_keys("zhuxiang:maintenance:*")
    redis_del_keys("zhuxiang:monitor:*")


def run_pipeline(script: str) -> dict:
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


# ------------------------------------------------------------
# P2 管道(容器内)
# ------------------------------------------------------------
P2_PIPELINE = (
    "import asyncio, json, os\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.xx66_heal_service import (\n"
    "        Xx66HealService)\n"
    "    from services.maintenance_service import (\n"
    "        MaintenanceService)\n"
    "    svc = Xx66HealService()\n"
    "    msvc = MaintenanceService()\n"
    # -- A 规则诊断+白名单+预演 --
    "    out['rc_disk'] = '磁盘' in svc._rule_diagnose(\n"
    "        'disk_full', 'db-1')['rootCause']\n"
    "    p = svc._dry_run(['restart_task'], 'db-1')\n"
    "    out['dr_pass'] = p['passable']\n"
    "    out['dr_keys'] = sorted(\n"
    "        p['details'][0].keys())\n"
    "    p2 = svc._dry_run(['trust_modify'], 'db-1')\n"
    "    out['dr_block'] = (\n"
    "        p2['passable'] is False)\n"
    # -- B manual 强制人工(assist) --
    "    os.environ['XX66_MODE'] = 'assist'\n"
    "    m_rec = await msvc.detect_fault(\n"
    "        'data_loss', 'db-critical',\n"
    "        recovery_level='manual')\n"
    "    mh = await svc.heal(m_rec['id'])\n"
    "    out['manual_route'] = mh['route']\n"
    "    out['manual_state'] = (\n"
    "        (await msvc.get_recovery(\n"
    "            m_rec['id']))['recoveryStatus'])\n"
    # -- C shadow 建议书 --
    "    os.environ['XX66_MODE'] = 'shadow'\n"
    "    s_rec = await msvc.detect_fault(\n"
    "        'disk_full', 'db-primary',\n"
    "        recovery_level='auto')\n"
    "    sh = await svc.heal(s_rec['id'])\n"
    "    out['shadow_route'] = sh['route']\n"
    "    out['shadow_state'] = (\n"
    "        (await msvc.get_recovery(\n"
    "            s_rec['id']))['recoveryStatus'])\n"
    "    out['shadow_cause'] = sh.get('rootCause')\n"
    # -- D assist 执行轨 --
    "    os.environ['XX66_MODE'] = 'assist'\n"
    "    a_rec = await msvc.detect_fault(\n"
    "        'disk_full', 'db-primary',\n"
    "        recovery_level='auto')\n"
    "    ah = await svc.heal(a_rec['id'])\n"
    "    out['assist_route'] = ah['route']\n"
    "    out['assist_tasks'] = [\n"
    "        t['taskStatus']\n"
    "        for t in ah.get('tasks') or []]\n"
    "    out['assist_final'] = ah.get('finalStatus')\n"
    # -- E 预测(三条件) --
    "    from repositories.monitor_repository"
    " import (\n"
    "        MonitorRepository)\n"
    "    repo = MonitorRepository()\n"
    "    vals = [50 + ((i % 3) - 1)\n"
    "            for i in range(50)]\n"
    "    vals += [200, 200, 200]\n"
    "    for i, v in enumerate(vals):\n"
    "        t = f'2026-01-01T00:{i:02d}:00'\n"
    "        await repo.create_metric({\n"
    "            'metricName': 'cpu',\n"
    "            'metricType': 'system',\n"
    "            'metricValue': v,\n"
    "            'source': 'app-1',\n"
    "            'metricUnit': '', 'tags': {},\n"
    "            'anomalyDetect': {\n"
    "                'score': 0.0},\n"
    "            'collectedAt': t,\n"
    "            'createdAt': t})\n"
    "    pr = await svc.predict()\n"
    "    out['pred_n'] = pr['triggered']\n"
    "    out['pred_feats'] = sorted(\n"
    "        (pr['predictions'][0]['features']\n"
    "         .keys()) if pr['predictions']\n"
    "        else [])\n"
    "    pr2 = await svc.predict()\n"
    "    out['pred_idem'] = (\n"
    "        pr2['triggered'] == 0)\n"
    # -- F 指纹链 --
    "    v = await svc.verify_log_chain()\n"
    "    out['chain_ok'] = v['chainIntact']\n"
    "    out['chain_n'] = v['total']\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n"
)


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮\n{'=' * 62}")
    clear_xx66(round_no)

    print("\n[01 健康面+status]")
    ok, (code, _) = call(
        "GET", "/api/decision/health")
    record("健康面 200", ok, str(code))
    ok, (code, st) = call(
        "GET", "/api/xx66/status", headers=ADMIN)
    record("status 200", ok, str(code))
    if ok:
        record("评分器入册",
               st.get("scorerRegistered") is True)

    print("\n[02 决策面 off+鉴权]")
    for path, body in (
            ("/api/xx66/diagnose",
             {"recoveryId": 1}),
            ("/api/xx66/heal", {"recoveryId": 1}),
            ("/api/xx66/predict", None)):
        ok, (code, _) = call(
            "POST", path, body=body,
            headers=ADMIN, expect=(409,))
        record(f"{path.split('/')[-1]} off 409",
               code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/xx66/heal",
        body={"recoveryId": 1})
    record("heal 无 Role 403",
           code == 403, str(code))

    print("\n[03 观测面(空态)]")
    ok, (code, ps) = call(
        "GET", "/api/xx66/predictions",
        headers=ADMIN)
    record("predictions 200", ok, str(code))
    if ok:
        record("predictions 空态结构",
               ps.get("count") == 0
               and isinstance(
                   ps.get("predictions"), list))
    ok, (code, lv) = call(
        "GET", "/api/xx66/log/verify",
        headers=ADMIN)
    record("log/verify 200(空链完好)",
           ok and lv.get("chainIntact") is True,
           str(lv)[:80])

    print("\n[04 管道: 规则+白名单+预演]")
    r = run_pipeline(P2_PIPELINE)
    if "error" in r:
        record("P2 管道成功", False, str(r)[:150])
    else:
        record("P2 管道成功", True)
        record("disk 规则命中",
               r.get("rc_disk") is True)
        record("白名单内预演通过",
               r.get("dr_pass") is True)
        record("预演含影响面字段",
               "affectedModules" in (
                   r.get("dr_keys") or []),
               str(r.get("dr_keys")))
        record("白名单外预演阻断",
               r.get("dr_block") is True)

    print("\n[05 manual 强制人工]")
    if "error" not in r:
        record("manual 路由 manual_handoff",
               r.get("manual_route") == "manual_handoff")
        record("manual 状态零推进",
               r.get("manual_state")
               == "manual_required")

    print("\n[06 shadow 建议书]")
    if "error" not in r:
        record("shadow 建议书路由",
               r.get("shadow_route") == "advice_book")
        record("shadow 诊断补链 diagnosing",
               r.get("shadow_state") == "diagnosing")
        record("shadow 根因写入",
               "磁盘" in str(r.get("shadow_cause")))

    print("\n[07 assist 执行轨]")
    if "error" not in r:
        record("assist 执行路由",
               r.get("assist_route") == "executed")
        record("27号任务全部 success",
               (r.get("assist_tasks") or [])
               and all(t == "success"
                       for t in r.get("assist_tasks")))
        record("27号终态 recovered",
               r.get("assist_final") == "recovered")

    print("\n[08 预测+指纹链]")
    if "error" not in r:
        record("三条件命中预警",
               r.get("pred_n") == 1,
               str(r.get("pred_n")))
        record("特征全量留痕",
               {"zscore", "streak", "pctile"}
               <= set(r.get("pred_feats") or []),
               str(r.get("pred_feats")))
        record("预警幂等",
               r.get("pred_idem") is True)
        record("指纹链完好",
               r.get("chain_ok") is True)
        record("链留痕数>=3",
               (r.get("chain_n") or 0) >= 3,
               str(r.get("chain_n")))


def main() -> int:
    print("=" * 62)
    print("66号·AI智能工程师 P2 诊断与自愈编排"
          " Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)
    for i in (1, 2):
        run_round(i)
    print(f"\n{'=' * 62}\n验收汇总: "
          f"{PASS} 通过 / {FAIL} 失败\n{'=' * 62}")
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
