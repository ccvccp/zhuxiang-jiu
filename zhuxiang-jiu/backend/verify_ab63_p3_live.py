"""63号AI智能后台管理 P3 Docker 实机验收

运行方式:
    # 容器以 shadow 态启动(HTTP 决策面
    # 正向验证——compose 支持 AB63_MODE
    # 环境变量注入):
    $env:AB63_MODE="shadow"
    docker compose -p zhuxiang-jiu up -d backend
    python verify_ab63_p3_live.py [基址]

覆盖(63号计划 §九 P3, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-05 容器内: 审核网关全链
       (L1 自动过审+抽检/L2 确认/
        L3 双人+合规官终审/证据链指纹/
        驳回反馈/申诉翻转/阈值 46号双模)
    06 HTTP 面(submit 分流+review 终审
       +队列+阈值观测)

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


CLEAN = ("居家养老服务 服务有效期90天"
         " 退改政策可退")

# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AB63_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.ab63_submission_service "
    "import (\n"
    "        Ab63SubmissionService)\n"
    "    svc = Ab63SubmissionService()\n"
    "    CLEAN = ('居家养老服务 服务有效期90天'\n"
    "             ' 退改政策可退')\n"
    # ① L1 自动过审
    "    r1 = await svc.submit(\n"
    "        10, 'ally_merchant',\n"
    "        content=CLEAN,\n"
    "        tier='trusted')\n"
    "    out['l1_status'] = (\n"
    "        r1.get('status'))\n"
    "    out['l1_score'] = (\n"
    "        r1.get('publishScore'))\n"
    "    out['l1_auto'] = (\n"
    "        r1.get('routing', {})\n"
    "        .get('autoPublished'))\n"
    # ② L2 分流+AI 预审
    "    r2 = await svc.submit(\n"
    "        11, 'ally_merchant',\n"
    "        content=CLEAN,\n"
    "        tier='standard')\n"
    "    out['l2_status'] = (\n"
    "        r2.get('status'))\n"
    "    out['l2_pre'] = (\n"
    "        r2.get('aiPreReview')\n"
    "        is not None)\n"
    # ③ L3 高危域强制(满分亦然)
    "    r3 = await svc.submit(\n"
    "        12, 'ally_merchant',\n"
    "        content=CLEAN,\n"
    "        tags=['medical'],\n"
    "        tier='trusted')\n"
    "    out['l3_status'] = (\n"
    "        r3.get('status'))\n"
    "    out['l3_forced'] = (\n"
    "        r3.get('routing', {})\n"
    "        .get('forcedBy'))\n"
    # ④ L3 三步(first→second→final)
    "    a1 = await svc.review(\n"
    "        r3['subId'], approve=True,\n"
    "        reviewer='甲')\n"
    "    out['l3_first'] = (\n"
    "        a1.get('reviewType'))\n"
    "    dup = False\n"
    "    try:\n"
    "        await svc.review(\n"
    "            r3['subId'], approve=True,\n"
    "            reviewer='甲')\n"
    "    except ValueError:\n"
    "        dup = True\n"
    "    out['l3_dup_reject'] = dup\n"
    "    a2 = await svc.review(\n"
    "        r3['subId'], approve=True,\n"
    "        reviewer='乙')\n"
    "    out['l3_second'] = (\n"
    "        a2.get('reviewType'))\n"
    "    final_reject = False\n"
    "    try:\n"
    "        await svc.review(\n"
    "            r3['subId'],\n"
    "            approve=True,\n"
    "            reviewer='甲')\n"
    "    except ValueError:\n"
    "        final_reject = True\n"
    "    out['l3_final_reject'] = (\n"
    "        final_reject)\n"
    "    a3 = await svc.review(\n"
    "        r3['subId'], approve=True,\n"
    "        reviewer='合规官')\n"
    "    out['l3_final'] = (\n"
    "        a3.get('reviewType'))\n"
    "    out['l3_pub'] = (\n"
    "        a3.get('status'))\n"
    # ⑤ 证据链读回
    "    detail = await (\n"
    "        svc.get_submission(\n"
    "            r3['subId']))\n"
    "    chain = detail.get('chain') or []\n"
    "    out['chain_n'] = len(chain)\n"
    "    out['chain_sha'] = all(\n"
    "        str(c).startswith(\n"
    "            'sha256:') for c in chain)\n"
    # ⑥ 驳回反馈闭环
    "    r4 = await svc.submit(\n"
    "        13, 'ally_merchant',\n"
    "        content='全市最好的服务',\n"
    "        tier='standard')\n"
    "    rej = await svc.review(\n"
    "        r4['subId'], approve=False,\n"
    "        reviewer='丙')\n"
    "    fb = rej.get('feedback') or {}\n"
    "    out['rej_status'] = (\n"
    "        rej.get('status'))\n"
    "    out['rej_fmap'] = len(\n"
    "        fb.get('fieldMap') or [])\n"
    "    out['rej_training'] = (\n"
    "        fb.get('pendingTraining'))\n"
    # ⑦ 申诉翻转
    "    os.environ['AB63_MODE'] = 'off'\n"
    "    ap = await svc.appeal(\n"
    "        r4['subId'],\n"
    "        appellant='member')\n"
    "    out['appeal_status'] = (\n"
    "        ap.get('status'))\n"
    "    ov = await svc.resolve_appeal(\n"
    "        r4['subId'], overturn=True,\n"
    "        adjudicator='合规官')\n"
    "    out['flip_to'] = (\n"
    "        ov.get('adjustedTo'))\n"
    # ⑧ 阈值 46号双模
    "    from services.ai_governance_service "
    "import (\n"
    "        AiGovernanceService)\n"
    "    gov = AiGovernanceService()\n"
    "    await gov.sync_registry()\n"
    "    cal = await svc.calibrate_submit(\n"
    "        92, 75,\n"
    "        requested_by='运营')\n"
    "    out['cal_status'] = (\n"
    "        cal.get('status'))\n"
    "    early = False\n"
    "    try:\n"
    "        await svc.calibrate_apply(\n"
    "            cal['changeId'])\n"
    "    except ValueError:\n"
    "        early = True\n"
    "    out['cal_early_reject'] = early\n"
    "    try:\n"
    "        await gov.review_change(\n"
    "            int(cal['changeId']),\n"
    "            approve=True,\n"
    "            reviewed_by='治理官')\n"
    "    except ValueError:\n"
    "        pass\n"
    "    app = await svc.calibrate_apply(\n"
    "        cal['changeId'],\n"
    "        applied_by='运营总监')\n"
    "    out['cal_applied'] = (\n"
    "        app.get('config'))\n"
    "    view = await svc.thresholds_view()\n"
    "    out['cal_view'] = (\n"
    "        view.get('active'))\n"
    # ⑨ 队列观测面
    "    q = await svc.queue_view()\n"
    "    out['queue_pending'] = (\n"
    "        q.get('pending'))\n"
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

    print("\n[02-05 容器内: 审核网关全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("L1 自动过审(100 分)",
           r.get("l1_status")
           == "auto_published"
           and r.get("l1_score") == 100.0
           and r.get("l1_auto") is True,
           str((r.get("l1_status"),
                r.get("l1_score"))))
    record("L2 分流+AI 预审",
           r.get("l2_status")
           == "pending_review"
           and r.get("l2_pre") is True,
           str((r.get("l2_status"),
                r.get("l2_pre"))))
    record("L3 高危域强制(满分亦然)",
           r.get("l3_status") == "deep_review"
           and r.get("l3_forced")
           == "highRiskTag",
           str((r.get("l3_status"),
                r.get("l3_forced"))))
    record("L3 first/second/final 三步",
           r.get("l3_first") == "first"
           and r.get("l3_second") == "second"
           and r.get("l3_final") == "final"
           and r.get("l3_pub")
           == "published",
           str((r.get("l3_first"),
                r.get("l3_second"),
                r.get("l3_final"),
                r.get("l3_pub"))))
    record("L3 同人重复拒绝",
           r.get("l3_dup_reject") is True,
           str(r.get("l3_dup_reject")))
    record("终审须第三人拒绝",
           r.get("l3_final_reject") is True,
           str(r.get("l3_final_reject")))
    record("证据链指纹(3 条 sha256)",
           r.get("chain_n") == 3
           and r.get("chain_sha") is True,
           str((r.get("chain_n"),
                r.get("chain_sha"))))
    record("驳回反馈闭环(fieldMap)",
           r.get("rej_status") == "rejected"
           and (r.get("rej_fmap") or 0) >= 1
           and r.get("rej_training") is True,
           str((r.get("rej_status"),
                r.get("rej_fmap"),
                r.get("rej_training"))))
    record("申诉翻转(adjusted)",
           r.get("appeal_status")
           == "disputed"
           and r.get("flip_to")
           == "published",
           str((r.get("appeal_status"),
                r.get("flip_to"))))
    record("阈值提交 46号(pending)",
           r.get("cal_status") == "pending",
           str(r.get("cal_status")))
    record("未经裁决不可生效",
           r.get("cal_early_reject") is True,
           str(r.get("cal_early_reject")))
    record("裁决后生效(apply)",
           r.get("cal_applied") == {
               "l1Threshold": 92.0,
               "l2Threshold": 75.0},
           str(r.get("cal_applied")))
    record("阈值视图(生效值)",
           r.get("cal_view") == {
               "l1Threshold": 92.0,
               "l2Threshold": 75.0},
           str(r.get("cal_view")))
    record("队列观测面(pending ≥1)",
           (r.get("queue_pending") or 0) >= 1,
           str(r.get("queue_pending")))

    print("\n[06 HTTP 面]")
    ok, (code, body) = call(
        "POST", "/api/ab63/submissions",
        body={"memberId": 90,
              "role": "ally_merchant",
              "content": CLEAN,
              "tier": "standard"},
        headers=ADMIN)
    sid = body.get("subId")
    record("HTTP submit L2 分流",
           code == 200
           and body.get("reviewTier") == "L2"
           and bool(sid),
           str((code,
                body.get("reviewTier"))))
    ok, (code, body) = call(
        "POST",
        f"/api/ab63/submissions/{sid}/review",
        body={"approve": True,
              "reviewer": "审核员"},
        headers=ADMIN)
    record("HTTP review 发布",
           code == 200
           and body.get("status")
           == "published",
           str((code,
                body.get("status"))))
    ok, (code, body) = call(
        "GET", "/api/ab63/reviews/queue",
        headers=ADMIN)
    record("HTTP 审核队列",
           code == 200
           and (body.get("total") or 0)
           >= 1,
           str((code,
                body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/ab63/thresholds",
        headers=ADMIN)
    record("HTTP 阈值视图",
           code == 200
           and (body.get("active")
                or {}).get("l1Threshold")
           == 92.0,
           str((code,
                body.get("active"))))


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
