"""61号AI智能系统升级决策 P3 Docker 实机验收

运行方式:
    # 容器以 shadow 态启动(HTTP 决策面):
    $env:DM61_MODE="shadow"
    docker compose -p zhuxiang-jiu up -d backend
    python verify_dm61_p3_live.py [基址]
    (容器内管道显式切 off 验证
     dissent/feedback 不受开关影响铁律)

覆盖(61号计划 §七 P3, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-04 容器内: 反对意见全链
       (触发评估+raise/override/confirm
        +decide 自动弹窗+归因报告
        +案例库+先验+RLHF 反馈)
    05 HTTP 面(dissent/feedback/cases)

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
    "    from services.dm61_sim_service "
    "import (\n"
    "        Dm61SimService)\n"
    "    from services.dm61_dissent_service "
    "import (\n"
    "        Dm61DissentService)\n"
    "    from services.dm61_graph_service "
    "import (\n"
    "        Dm61GraphService)\n"
    "    from services.dm61_feedback_service "
    "import (\n"
    "        Dm61FeedbackService)\n"
    "    base = Dm61Service()\n"
    "    asvc = Dm61AssessService()\n"
    "    dsvc = Dm61DecisionService()\n"
    "    ssvc = Dm61SimService()\n"
    "    disvc = Dm61DissentService()\n"
    "    gsvc = Dm61GraphService()\n"
    "    fsvc = Dm61FeedbackService()\n"
    # 造链助手
    "    async def seed(title):\n"
    "        r = await base.create_request(\n"
    "            title, hour=3)\n"
    "        await asvc.assess(\n"
    "            r['requestId'],\n"
    "            tier='standard',\n"
    "            error_budget=0.3,\n"
    "            history_fail_rate=0.05)\n"
    "        rec = await dsvc.recommend(\n"
    "            r['requestId'])\n"
    "        return r, rec\n"
    # ① 造决策 A(含 sim——评估→
    #    推演→推荐内联链)
    "    ra = await base.create_request(\n"
    "        '支付结算费率优化', hour=3)\n"
    "    await asvc.assess(\n"
    "        ra['requestId'],\n"
    "        tier='standard',\n"
    "        error_budget=0.3,\n"
    "        history_fail_rate=0.05)\n"
    "    await ssvc.simulate(\n"
    "        ra['requestId'])\n"
    "    reca = await dsvc.recommend(\n"
    "        ra['requestId'])\n"
    # ② 触发评估(off 亦可用)
    "    os.environ['DM61_MODE'] = 'off'\n"
    "    ev = await disvc.evaluate(\n"
    "        reca['decisionId'])\n"
    "    out['ev_off'] = (\n"
    "        ev.get('success'))\n"
    "    out['ev_n'] = (\n"
    "        ev.get('triggerCount'))\n"
    # ③ 手动理由 raise(off 可用)
    "    raised = await disvc.raise_dissent(\n"
    "        reca['decisionId'],\n"
    "        raised_by='admin',\n"
    "        reason='实机人工质疑')\n"
    "    out['raised_flag'] = (\n"
    "        raised.get('dissentFlag'))\n"
    "    out['raised_status'] = (\n"
    "        (raised.get('dissent')\n"
    "         or {}).get('status'))\n"
    # ④ decide 被 open 阻断
    "    out['decide_blocked'] = False\n"
    "    try:\n"
    "        await dsvc.decide(\n"
    "            reca['decisionId'],\n"
    "            action='adopted',\n"
    "            decided_by='x')\n"
    "    except ValueError:\n"
    "        out['decide_blocked'] = True\n"
    # ⑤ override 缺理由拒绝
    "    out['no_reason'] = False\n"
    "    try:\n"
    "        await disvc.resolve(\n"
    "            reca['decisionId'],\n"
    "            action='override',\n"
    "            reason='')\n"
    "    except ValueError:\n"
    "        out['no_reason'] = True\n"
    # ⑥ override 留痕放行
    "    res = await disvc.resolve(\n"
    "        reca['decisionId'],\n"
    "        action='override',\n"
    "        reason='窗口紧迫',\n"
    "        resolved_by='决策长')\n"
    "    out['ov_status'] = (\n"
    "        (res.get('dissent')\n"
    "         or {}).get('status'))\n"
    "    out['ov_reason'] = (\n"
    "        (res.get('dissent')\n"
    "         or {}).get(\n"
    "            'resolutionReason'))\n"
    # ⑦ decide 放行(46号)
    "    da = await dsvc.decide(\n"
    "        reca['decisionId'],\n"
    "        action='adopted',\n"
    "        decided_by='决策长')\n"
    "    out['decide_ok'] = (\n"
    "        da.get('status'))\n"
    "    out['da_change'] = (\n"
    "        da.get('changeId'))\n"
    "    try:\n"
    "        await (AiGovernanceService()\n"
    "              .review_change(\n"
    "                  int(da['changeId']),\n"
    "                  approve=False,\n"
    "                  reviewed_by='官',\n"
    "                  review_note='解锁'))\n"
    "    except ValueError:\n"
    "        pass\n"
    # ⑧ 决策 B: decide 自动弹窗
    #    (评估→推演阻断→推荐内联链)
    "    os.environ['DM61_MODE'] = 'shadow'\n"
    "    rb = await base.create_request(\n"
    "        '界面适配调整', hour=3)\n"
    "    await asvc.assess(\n"
    "        rb['requestId'],\n"
    "        tier='standard',\n"
    "        error_budget=0.3,\n"
    "        history_fail_rate=0.05)\n"
    "    await ssvc.simulate(\n"
    "        rb['requestId'],\n"
    "        change_text='x = eval(u)')\n"
    "    recb = await dsvc.recommend(\n"
    "        rb['requestId'])\n"
    "    os.environ['DM61_MODE'] = 'off'\n"
    "    out['auto_popup'] = False\n"
    "    try:\n"
    "        await dsvc.decide(\n"
    "            recb['decisionId'],\n"
    "            action='adopted',\n"
    "            decided_by='x')\n"
    "    except ValueError:\n"
    "        out['auto_popup'] = True\n"
    # ⑨ confirm 终止
    "    conf = await disvc.resolve(\n"
    "        recb['decisionId'],\n"
    "        action='confirm',\n"
    "        reason='AI 质疑成立',\n"
    "        resolved_by='决策长')\n"
    "    out['conf_outcome'] = (\n"
    "        conf.get('outcome'))\n"
    # ⑩ 归因报告
    "    report = await (gsvc\n"
    "        .attribution_report(\n"
    "            reca['decisionId']))\n"
    "    chain = report.get('chain') or {}\n"
    "    out['chain_keys'] = sorted(\n"
    "        chain.keys())\n"
    "    out['chain_dissent'] = (\n"
    "        chain.get('dissent')\n"
    "        is not None)\n"
    # ⑪ 案例库+先验
    "    view = await gsvc.cases_view()\n"
    "    out['cases_total'] = (\n"
    "        view.get('total'))\n"
    "    prior_ui = await (gsvc\n"
    "        .prior_probability(\n"
    "            tag='ui_adapt'))\n"
    "    out['prior_fail'] = (\n"
    "        prior_ui.get('failed'))\n"
    # ⑫ RLHF 反馈
    "    fb = await fsvc.submit(\n"
    "        reca['decisionId'],\n"
    "        action='adopted',\n"
    "        outcome='good',\n"
    "        comment='灰度顺利',\n"
    "        by='运营官')\n"
    "    out['fb_id'] = (\n"
    "        fb.get('feedbackId'))\n"
    "    out['fb_dup'] = False\n"
    "    try:\n"
    "        await fsvc.submit(\n"
    "            reca['decisionId'],\n"
    "            action='rejected')\n"
    "    except ValueError:\n"
    "        out['fb_dup'] = True\n"
    # ⑬ Redis 读回
    "    from repositories.dm61_repository "
    "import (\n"
    "        Dm61Repository)\n"
    "    repo = Dm61Repository()\n"
    "    dec_a = await repo.get_decision(\n"
    "        reca['decisionId'])\n"
    "    out['rd_flag'] = (\n"
    "        dec_a.get('dissentFlag'))\n"
    "    out['rd_dissent_dict'] = isinstance(\n"
    "        dec_a.get('dissent'), dict)\n"
    # ⑭ 事件链
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

    print("\n[02-04 容器内: 反对意见全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("off 态触发评估可用(铁律)",
           r.get("ev_off") is True
           and r.get("ev_n") == 0,
           str((r.get("ev_off"),
                r.get("ev_n"))))
    record("手动理由 raise(off 可用)",
           r.get("raised_flag") is True
           and r.get("raised_status")
           == "open",
           str((r.get("raised_flag"),
                r.get("raised_status"))))
    record("open dissent 阻断裁决",
           r.get("decide_blocked") is True,
           str(r.get("decide_blocked")))
    record("override 缺理由拒绝",
           r.get("no_reason") is True,
           str(r.get("no_reason")))
    record("override 留痕(理由存证)",
           r.get("ov_status") == "overridden"
           and r.get("ov_reason")
           == "窗口紧迫",
           str((r.get("ov_status"),
                r.get("ov_reason"))))
    record("override 后裁决放行",
           r.get("decide_ok")
           == "executed_track"
           and (r.get("da_change")
                or 0) > 0,
           str((r.get("decide_ok"),
                r.get("da_change"))))
    record("decide 触发自动弹窗",
           r.get("auto_popup") is True,
           str(r.get("auto_popup")))
    record("confirm 决策终止",
           r.get("conf_outcome")
           == "dissent_confirmed",
           str(r.get("conf_outcome")))
    chain_keys = r.get("chain_keys") or []
    record("归因链九环齐备",
           set(chain_keys) >= {
               "semantic", "impact",
               "environment", "assess",
               "prior", "simulation",
               "recommendation",
               "decision", "dissent"},
           str(chain_keys))
    record("归因链含 dissent",
           r.get("chain_dissent") is True,
           str(r.get("chain_dissent")))
    record("案例库派生(2 终态)",
           r.get("cases_total") == 2,
           str(r.get("cases_total")))
    record("先验(dissent 计入失败)",
           r.get("prior_fail") == 1,
           str(r.get("prior_fail")))
    record("RLHF 反馈提交",
           (r.get("fb_id") or 0) > 0,
           str(r.get("fb_id")))
    record("反馈 1:1 重复拒绝",
           r.get("fb_dup") is True,
           str(r.get("fb_dup")))
    record("Redis 读回(dissentFlag)",
           r.get("rd_flag") is True,
           str(r.get("rd_flag")))
    record("Redis 读回(dissent dict)",
           r.get("rd_dissent_dict") is True,
           str(r.get("rd_dissent_dict")))
    record("事件链(dissent+feedback)",
           all(t in (r.get("ev_types") or [])
               for t in ("dissent",
                         "feedback")),
           str(r.get("ev_types")))

    print("\n[05 HTTP 面]")
    # HTTP 决策链构造(shadow 态容器)
    ok, (code, body) = call(
        "POST", "/api/dm61/requests",
        body={"title": "支付费率优化",
              "hour": 3},
        headers=ADMIN)
    rid = (body or {}).get("requestId") \
        if code == 200 else None
    if rid:
        ok, (code, _) = call(
            "POST", "/api/dm61/assess",
            body={"requestId": rid,
                  "errorBudget": 0.3},
            headers=ADMIN)
        ok, (code, body) = call(
            "POST", "/api/dm61/recommend",
            body={"requestId": rid},
            headers=ADMIN)
        did = (body or {}).get("decisionId")
        if did:
            ok, (code, body) = call(
                "POST",
                f"/api/dm61/decisions/{did}"
                f"/dissent",
                body={"mode": "raise",
                      "reason": "HTTP 质疑"},
                headers=ADMIN)
            record("HTTP dissent raise 200",
                   code == 200
                   and (body
                        or {}).get(
                       "dissentFlag")
                   is True,
                   str(code))
            ok, (code, body) = call(
                "POST",
                f"/api/dm61/decisions/{did}"
                f"/dissent",
                body={"mode": "override",
                      "reason": "HTTP 驳回"},
                headers=ADMIN)
            record("HTTP dissent override",
                   code == 200
                   and ((body
                         or {}).get(
                       "dissent")
                        or {}).get(
                       "status")
                   == "overridden",
                   str(code))
            ok, (code, body) = call(
                "POST", "/api/dm61/feedback",
                body={"decisionId": did,
                      "action": "rejected",
                      "outcome": "good"},
                headers=ADMIN)
            record("HTTP feedback 200",
                   code == 200,
                   str(code))
    else:
        record("HTTP 决策链构造(shadow 态)",
               False, "requests 非 200——"
                      "请以 DM61_MODE=shadow "
                      "启动容器")
    # cases 观测面
    ok, (code, body) = call(
        "GET", "/api/dm61/cases",
        headers=ADMIN)
    record("HTTP cases 观测面 200",
           code == 200
           and (body
                or {}).get("total", 0)
           >= 2,
           str((code,
                (body or {}).get(
                    "total"))))
    # 鉴权 403
    for method, path in (
            ("POST",
             "/api/dm61/decisions/1/"
             "dissent"),
            ("POST",
             "/api/dm61/feedback"),
            ("GET",
             "/api/dm61/cases")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 14
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
