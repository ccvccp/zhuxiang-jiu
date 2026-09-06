"""62号AI智能无形资产估值 P4 Docker 实机验收

运行方式:
    python verify_av62_p4_live.py [基址]

前置: 容器已运行(含 62号 P4 代码)。

覆盖(62号计划 §七 P4, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律+观测面(learn/status
       200)
    03 容器内: 验证回流全链(偏差三档
       信号+44号池双写 assessId 1:1
       幂等+偏差预警经 46号+衰减批量
       结算+T+1 调度手动轮)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
av62 键域)。
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


def clear_av62(round_no: int) -> None:
    redis_del_keys("zhuxiang:av62:*")
    # 46号变更域残留清理(asset_valuation
    # pending 防重复拦截——仅变更表)
    redis_del_keys(
        "zhuxiang:ai46:ai46_changes:*")
    redis_del_keys(
        "zhuxiang:ai46:changes_all")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AV62_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 种子: 4 评估(1 准确+1 部分
    # +2 严重——severe 50% 触发预警)
    "    from services.av62_service import (\n"
    "        Av62Service)\n"
    "    from services.av62_assess_service "
    "import (\n"
    "        Av62AssessService)\n"
    "    reg = Av62Service()\n"
    "    asm = Av62AssessService()\n"
    "    async def seed(sid, role, dom, ev):\n"
    "        a = await reg.register_asset(\n"
    "            sid, role, dom, ev)\n"
    "        return await asm.assess_asset(\n"
    "            a['assetId'])\n"
    "    r1 = await seed(101, 'enterprise',\n"
    "        'compliance',\n"
    "        {'licenseCount': 5,\n"
    "         'auditResults': 'pass',\n"
    "         'esgDisclosure': 'yes'})\n"
    "    r2 = await seed(101, 'personal',\n"
    "        'capability',\n"
    "        {'skillCerts': 8,\n"
    "         'deliveryQuality': 0.95,\n"
    "         'knowledgeSharing': 24})\n"
    "    r3 = await seed(101, 'organization',\n"
    "        'social',\n"
    "        {'memberActivity': 0.8,\n"
    "         'eventCompliance': 0.9,\n"
    "         'externalReviews': 4})\n"
    "    r4 = await seed(101, 'personal',\n"
    "        'growth',\n"
    "        {'learningInvest': 0.9,\n"
    "         'errorCorrection': 0.85,\n"
    "         'crossAdapt': 0.8})\n"
    # ② 验证信号(偏差三档)
    "    from services.av62_learn_service "
    "import (\n"
    "        Av62LearnService,\n"
    "        classify_deviation)\n"
    "    svc = Av62LearnService()\n"
    "    out['cls_ok'] = (\n"
    "        classify_deviation(0.05)\n"
    "        == 'within_tolerance'\n"
    "        and classify_deviation(0.2)\n"
    "        == 'moderate_deviation'\n"
    "        and classify_deviation(0.5)\n"
    "        == 'severe_deviation')\n"
    "    v1 = await svc.submit_verification(r1['assessId'], 85)\n"
    "    out['v1_sig'] = (\n"
    "        v1.get('signal'))\n"
    "    out['v1_dev'] = (\n"
    "        v1.get('deviation'))\n"
    "    v2 = await svc.submit_verification(r2['assessId'], 105)\n"
    "    out['v2_sig'] = (\n"
    "        v2.get('signal'))\n"
    "    v3 = await svc.submit_verification(r3['assessId'], 150)\n"
    "    out['v3_sig'] = (\n"
    "        v3.get('signal'))\n"
    "    v4 = await svc.submit_verification(r4['assessId'], 10)\n"
    "    out['v4_sig'] = (\n"
    "        v4.get('signal'))\n"
    # ③ 负资产验证拒绝
    "    a5 = await reg.register_asset(\n"
    "        101, 'enterprise', 'risk',\n"
    "        {'penaltyRecords': 5})\n"
    "    r5 = await asm.assess_asset(\n"
    "        a5['assetId'])\n"
    "    try:\n"
    "        await svc.submit_verification(r5['assessId'], 50)\n"
    "        out['neg_rej'] = False\n"
    "    except ValueError:\n"
    "        out['neg_rej'] = True\n"
    # ④ off 态回流不受影响(46号先入册)
    "    os.environ['AV62_MODE'] = 'off'\n"
    "    from services.ai_governance_service "
    "import (\n"
    "        AiGovernanceService)\n"
    "    await AiGovernanceService()"
    ".sync_registry()\n"
    "    c1 = await svc.collect_verification()\n"
    "    out['c1_scan'] = (\n"
    "        c1.get('scanned'))\n"
    "    out['c1_label'] = (\n"
    "        c1.get('labeled'))\n"
    "    out['c1_pool'] = (\n"
    "        c1.get('poolSubmitted'))\n"
    "    out['c1_signals'] = (\n"
    "        c1.get('signals'))\n"
    # ⑤ 偏差预警(severe 2/4=50%)
    "    alert = c1.get(\n"
    "        'deviationAlert') or {}\n"
    "    out['al_status'] = (\n"
    "        alert.get('status'))\n"
    "    out['al_ratio'] = (\n"
    "        alert.get('severeRatio'))\n"
    "    out['al_cid'] = (\n"
    "        alert.get('changeId'))\n"
    # ⑥ 幂等二轮
    "    c2 = await svc.collect_verification()\n"
    "    out['c2_skip'] = (\n"
    "        c2.get('skipped'))\n"
    "    out['c2_label'] = (\n"
    "        c2.get('labeled'))\n"
    # ⑦ pooled 回写
    "    from repositories.av62_repository "
    "import Av62Repository\n"
    "    repo = Av62Repository()\n"
    "    rec1 = await repo.get_assessment(\n"
    "        r1['assessId'])\n"
    "    out['pw_pooled'] = (\n"
    "        rec1.get('pooled'))\n"
    "    out['pw_signal'] = (\n"
    "        rec1.get('poolSignal'))\n"
    "    out['pw_fid'] = (\n"
    "        rec1.get(\n"
    "            'pooledFeedbackId'))\n"
    # ⑧ weight_review 落库
    "    wr = await repo.get_threshold(\n"
    "        'weight_review')\n"
    "    out['wr_status'] = (\n"
    "        (wr or {}).get('status'))\n"
    # ⑨ 衰减批量结算
    "    s = await svc.settle_decay()\n"
    "    out['decay_n'] = (\n"
    "        s.get('refreshed'))\n"
    # ⑩ T+1 调度手动轮(幂等零新)
    "    from services import "
    "av62_scheduler as sch\n"
    "    out['sch_enabled'] = (\n"
    "        sch.scheduler_enabled())\n"
    "    out['sch_interval'] = (\n"
    "        sch.scheduler_interval_seconds())\n"
    "    r = await sch.run_scheduled_tasks()\n"
    "    out['sch_collect'] = (\n"
    "        (r.get('collect') or {})\n"
    "        .get('skipped'))\n"
    "    out['sch_decay'] = (\n"
    "        (r.get('decaySettle') or {})\n"
    "        .get('refreshed'))\n"
    "    out['sch_err'] = (\n"
    "        r.get('errors'))\n"
    # ⑪ 调度留痕
    "    evs = await repo.list_events(\n"
    "        limit=100)\n"
    "    out['sch_ev'] = len([\n"
    "        e for e in evs\n"
    "        if e.get('eventType')\n"
    "        == 'scheduler_run'])\n"
    # ⑫ learn_status
    "    st = await svc.learn_status()\n"
    "    out['st_verified'] = (\n"
    "        st.get('verified'))\n"
    "    out['st_pooled'] = (\n"
    "        st.get('pooled'))\n"
    "    out['st_pending'] = (\n"
    "        st.get('pendingCollect'))\n"
    # ⑬ 44号档案
    "    from services.ai_learning_service "
    "import SCORER_REGISTRY\n"
    "    out['reg_n'] = len(\n"
    "        SCORER_REGISTRY)\n"
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
    clear_av62(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, body) = call(
        "GET", "/api/av62/learn/status",
        headers=ADMIN)
    record("off 态 learn/status 观测面 200",
           code == 200
           and "thresholds" in (body or {}),
           str(code))

    print("\n[03 容器内: 验证回流全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("偏差三档纯函数",
           r.get("cls_ok") is True,
           str(r.get("cls_ok")))
    record("准确信号(2% 偏差)",
           r.get("v1_sig")
           == "within_tolerance"
           and abs(float(
               r.get("v1_dev") or 1)
               - 0.0204) < 0.0005,
           str((r.get("v1_sig"),
                r.get("v1_dev"))))
    record("部分偏差信号(23.5%)",
           r.get("v2_sig")
           == "moderate_deviation",
           str(r.get("v2_sig")))
    record("严重偏差信号(80%)",
           r.get("v3_sig")
           == "severe_deviation",
           str(r.get("v3_sig")))
    record("低值严重信号",
           r.get("v4_sig")
           == "severe_deviation",
           str(r.get("v4_sig")))
    record("负资产验证拒绝",
           r.get("neg_rej") is True,
           str(r.get("neg_rej")))
    record("回流扫描(4 条)",
           r.get("c1_scan") == 4,
           str(r.get("c1_scan")))
    record("44号池双写(4 提交)",
           r.get("c1_pool") == 4,
           str(r.get("c1_pool")))
    record("信号分布(1+1+2)",
           (r.get("c1_signals") or {})
           .get("within_tolerance") == 1
           and (r.get("c1_signals")
                or {}).get(
                    "moderate_deviation") == 1
           and (r.get("c1_signals")
                or {}).get(
                    "severe_deviation") == 2,
           str(r.get("c1_signals")))
    record("off 态回流不受影响",
           r.get("c1_label") == 4,
           str(r.get("c1_label")))
    record("偏差预警触发(50%)",
           r.get("al_status") == "pending"
           and r.get("al_ratio") == 0.5
           and (r.get("al_cid") or 0) > 0,
           str((r.get("al_status"),
                r.get("al_ratio"))))
    record("幂等二轮(全跳过)",
           r.get("c2_skip") == 4
           and r.get("c2_label") == 0,
           str((r.get("c2_skip"),
                r.get("c2_label"))))
    record("pooled 回写(幂等标记)",
           r.get("pw_pooled") is True
           and r.get("pw_signal")
           == "within_tolerance"
           and (r.get("pw_fid") or 0)
           > 0,
           str((r.get("pw_pooled"),
                r.get("pw_signal"))))
    record("weight_review 落库"
           "(46号 pending)",
           r.get("wr_status")
           == "pending",
           str(r.get("wr_status")))
    record("衰减批量结算(5 资产)",
           r.get("decay_n") == 5,
           str(r.get("decay_n")))
    record("调度默认关闭",
           r.get("sch_enabled") is False,
           str(r.get("sch_enabled")))
    record("调度默认 24h",
           r.get("sch_interval") == 86400,
           str(r.get("sch_interval")))
    record("调度手动轮(幂等零新)",
           r.get("sch_collect") == 4
           and r.get("sch_decay") == 5,
           str((r.get("sch_collect"),
                r.get("sch_decay"))))
    record("调度手动轮零错误",
           r.get("sch_err") == [],
           str(r.get("sch_err")))
    record("调度留痕(scheduler_run)",
           r.get("sch_ev") == 1,
           str(r.get("sch_ev")))
    record("learn_status(4 验证全池化)",
           r.get("st_verified") == 4
           and r.get("st_pooled") == 4
           and r.get("st_pending") == 0,
           str((r.get("st_verified"),
                r.get("st_pooled"))))
    record("44号 38 档案",
           r.get("reg_n") == 38,
           str(r.get("reg_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # 回流(服务器 off 态——不受影响)
    ok, (code, body) = call(
        "POST", "/api/av62/feedback/collect",
        body={},
        headers=ADMIN)
    record("HTTP collect 200(off 不受影响)",
           code == 200
           and (body.get("skipped")
                or 0) == 4,
           str((code,
                body.get("skipped"))))
    # 观测面读回
    ok, (code, body) = call(
        "GET", "/api/av62/learn/status",
        headers=ADMIN)
    record("HTTP learn/status 读回",
           code == 200
           and (body.get("verified")
                or 0) == 4,
           str((code,
                body.get("verified"))))
    # 验证提交(种子已有——改值 404
    # 场景验证)
    ok, (code, _) = call(
        "POST", "/api/av62/verifications",
        body={"assessId": 999,
              "actualValue": 85},
        headers=ADMIN, expect=(404,))
    record("HTTP verifications 404",
           code == 404, str(code))
    # 鉴权 403
    for method, path in (
            ("POST",
             "/api/av62/verifications"),
            ("POST",
             "/api/av62/feedback/collect"),
            ("GET",
             "/api/av62/learn/status")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 23
    script = (
        "from routes.av62_routes import "
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
    record("62号路由 P4 23 端点",
           count == 23, str(count))


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
