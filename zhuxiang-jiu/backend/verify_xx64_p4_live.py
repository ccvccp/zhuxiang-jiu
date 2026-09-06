"""64号信值兑换管理 P4 Docker 实机验收

运行方式:
    python verify_xx64_p4_live.py [基址]

前置: 容器已运行(含 64号 P4 代码)。

覆盖(64号 P4 设计 §十一, 真实容器
Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(pay/audit/calibrate
       409 决策面)
    03 容器内: 价值锚定全链(指数
       快照均价+自锚+同日幂等
       +通胀预警+供需激增+校准
       均衡 fail-soft+阈值域宪法
       不可校准)
    04 容器内: 申诉全链(提交不受
       开关+重算三件套+重复拒
       +approve 翻转 disputed→paid)
    05 容器内: 回流全链(首轮
       labeled+双轮幂等 0+
       pooled 回写+learn_status)
    06 容器内: 调度手动一轮(四
       任务+scheduler_run 留痕)
    07 HTTP 端点+鉴权(anchors/
       appeal 全链/review 翻转/
       collect 幂等/learn 200/
       member 403/24 端点)

×2 轮幂等验证(每轮清理种子重造
——xx64+points+trust45 种子键域)。
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
MEMBER = {"X-Role": "member"}

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


def clear_xx64(round_no: int) -> None:
    redis_del_keys("zhuxiang:xx64:*")
    # 积分账户种子域
    redis_del_keys(
        "zhuxiang:points:account:9901")
    redis_del_keys(
        "zhuxiang:points:logs_by_user:9901")
    # 45号测试档案种子域
    for tid in ("9901", "9902"):
        redis_del_keys(
            "zhuxiang:trust45:"
            "trust45_profiles:" + tid)
    redis_del_keys(
        "zhuxiang:trust45:idmap:"
        "digest-999*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['XX64_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 种子
    "    from repositories.trust_value"
    "_repository import (\n"
    "        TrustValue45Repository)\n"
    "    repo45 = TrustValue45Repository()\n"
    "    for tid in (9901, 9902):\n"
    "        await repo45.save_profile({\n"
    "            'trustId': tid,\n"
    "            'role': 'person',\n"
    "            'name': 'live-p4',\n"
    "            'idDigest': f'digest-{tid}',\n"
    "            'factors': {},\n"
    "            'score': 500.0,\n"
    "            'rawScore': 500.0,\n"
    "            'grade': 'A',\n"
    "            'fused': False,\n"
    "            'frozen': False,\n"
    "            'createdAt':\n"
    "                '2026-01-01T00:00:00',\n"
    "            'updatedAt':\n"
    "                '2026-01-01T00:00:00'})\n"
    # ② 订单(3 paid 指数样本
    #    +1 completed 回流 ok
    #    +1 disputed 申诉翻转)
    "    from repositories.xx64"
    "_repository import (\n"
    "        Xx64Repository)\n"
    "    from core.helpers import ts\n"
    "    repo = Xx64Repository()\n"
    "    async def add(st, price, tv):\n"
    "        oid = await repo.next_order"
    "_id()\n"
    "        await repo.save_order({\n"
    "            'orderId': oid,\n"
    "            'buyerId': 9901,\n"
    "            'sellerId': 9902,\n"
    "            'trustId': 9901,\n"
    "            'product': 'gA',\n"
    "            'price': price,\n"
    "            'trustValue': tv,\n"
    "            'cashValue': price - tv,\n"
    "            'balanceSnapshot': 500.0,\n"
    "            'status': st,\n"
    "            'paidAt': ts() if st in (\n"
    "                'paid', 'settled',\n"
    "                'completed') else '',\n"
    "            'createdAt': ts()})\n"
    "        return oid\n"
    "    for _ in range(3):\n"
    "        await add('paid', 100.0, 30.0)\n"
    "    ok_id = await add(\n"
    "        'completed', 50.0, 15.0)\n"
    "    dis_id = await add(\n"
    "        'disputed', 100.0, 30.0)\n"
    # ③ 指数快照+幂等
    "    from services.xx64_anchor"
    "_service import (\n"
    "        Xx64AnchorService)\n"
    "    anc = Xx64AnchorService()\n"
    "    snap = await anc.snapshot()\n"
    "    out['avg'] = snap.get('avgPrice')\n"
    "    out['power'] = snap.get(\n"
    "        'purchasingPower')\n"
    "    out['samples'] = snap.get('samples')\n"
    "    snap2 = await anc.snapshot()\n"
    "    out['snap_idem'] = (\n"
    "        snap2.get('anchorId')\n"
    "        == snap.get('anchorId'))\n"
    "    view = await anc.anchors_view()\n"
    "    out['view_total'] = view['total']\n"
    # ④ 供需
    "    sd = await anc.supply_demand"
    "_scan()\n"
    "    out['sd_products'] = (\n"
    "        sd['scannedProducts'])\n"
    # ⑤ 校准(均衡/未入册跳过)
    "    rc = await anc.rate_check()\n"
    "    out['rate_status'] = (\n"
    "        rc['status'])\n"
    "    out['rate_submitted'] = (\n"
    "        rc['submitted'])\n"
    # ⑥ 阈值域
    "    tv = await anc.thresholds_view()\n"
    "    out['const'] = (\n"
    "        'trustPortion'\n"
    "        in tv['constitution'])\n"
    "    out['calib_n'] = len(\n"
    "        tv['calibratable'])\n"
    # ⑦ 申诉全链
    "    from services.xx64_appeal"
    "_service import (\n"
    "        Xx64AppealService)\n"
    "    aps = Xx64AppealService()\n"
    "    ap = await aps.submit(\n"
    "        dis_id, 'risk error')\n"
    "    out['ap_st'] = ap['status']\n"
    "    out['ap_recalc'] = all(\n"
    "        k in ap['recalc'] for k in (\n"
    "            'precheck',\n"
    "            'explainSteps',\n"
    "            'riskFindings'))\n"
    "    try:\n"
    "        await aps.submit(\n"
    "            dis_id, 'dup')\n"
    "        out['ap_dup'] = False\n"
    "    except ValueError:\n"
    "        out['ap_dup'] = True\n"
    "    rv = await aps.review(\n"
    "        ap['appealId'], 'approve',\n"
    "        'manual check')\n"
    "    out['rv_st'] = rv['status']\n"
    "    out['rv_unfreeze'] = (\n"
    "        rv['compensation'].get(\n"
    "            'actions')[0]['action']\n"
    "        == 'unfreeze')\n"
    "    order = await repo.get_order(\n"
    "        dis_id)\n"
    "    out['order_paid'] = (\n"
    "        order['status'] == 'paid')\n"
    # ⑧ 回流幂等
    "    from services.xx64_learn"
    "_service import (\n"
    "        Xx64LearnService)\n"
    "    lrn = Xx64LearnService()\n"
    "    c1 = await lrn.collect_feedback()\n"
    "    out['c1_labeled'] = (\n"
    "        c1['labeled'])\n"
    "    out['c1_signals'] = (\n"
    "        c1['signals'])\n"
    "    c2 = await lrn.collect_feedback()\n"
    "    out['c2_labeled'] = (\n"
    "        c2['labeled'])\n"
    "    ok_order = await repo.get_order(\n"
    "        ok_id)\n"
    "    out['pooled'] = int(\n"
    "        ok_order.get(\n"
    "            'pooledFeedbackId')\n"
    "        or 0) > 0\n"
    "    st = await lrn.learn_status()\n"
    "    out['learn_mode'] = (\n"
    "        st['learnMode'])\n"
    # ⑨ 调度手动一轮
    "    import services.xx64_scheduler"
    " as sched\n"
    "    r = await sched.run_scheduled"
    "_tasks()\n"
    "    out['sched_tasks'] = all(\n"
    "        r[k] is not None for k in (\n"
    "            'snapshot', 'collect',\n"
    "            'supplyDemand',\n"
    "            'rateCheck'))\n"
    "    out['sched_err'] = len(\n"
    "        r['errors'])\n"
    # ⑩ 44号
    "    from services.ai_learning"
    "_service import (\n"
    "        SCORER_REGISTRY)\n"
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
    clear_xx64(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+决策面]")
    ok, (code, _) = call(
        "POST", "/api/xx64/orders/1/pay",
        body={},
        headers=MEMBER, expect=(409,))
    record("off 态 pay 409(服务器态)",
           code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/xx64/anchors/audit",
        body={},
        headers=ADMIN, expect=(409,))
    record("off 态 audit 409(决策面)",
           code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/xx64/threshold/"
                "calibrate",
        body={},
        headers=ADMIN, expect=(409,))
    record("off 态 calibrate 409",
           code == 409, str(code))

    print("\n[03 容器内: 价值锚定全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("指数快照(均价 87.5"
           "+样本 4)",
           r.get("avg") == 87.5
           and r.get("samples") == 4,
           str((r.get("avg"),
                r.get("samples"))))
    record("指数自锚(1.0)",
           r.get("power") == 1.0,
           str(r.get("power")))
    record("同日重算幂等",
           r.get("snap_idem") is True,
           str(r.get("snap_idem")))
    record("序列视图(1 条)",
           r.get("view_total") == 1,
           str(r.get("view_total")))
    record("供需扫描(1 商品)",
           r.get("sd_products") == 1,
           str(r.get("sd_products")))
    record("校准判定+提交轨",
           r.get("rate_status") in (
               "balanced", "burn_fast")
           and r.get("rate_submitted")
           is False,
           str((r.get("rate_status"),
                r.get(
                    "rate_submitted"))))
    record("阈值域宪法不可校准",
           r.get("const") is True
           and r.get("calib_n") == 5,
           str((r.get("const"),
                r.get("calib_n"))))

    print("\n[04 容器内: 申诉全链]")
    record("提交不受开关",
           r.get("ap_st")
           == "recalculated",
           str(r.get("ap_st")))
    record("重算三件套",
           r.get("ap_recalc") is True,
           str(r.get("ap_recalc")))
    record("重复申诉拒",
           r.get("ap_dup") is True,
           str(r.get("ap_dup")))
    record("approve 翻转"
           "(disputed→paid)",
           r.get("rv_st") == "approved"
           and r.get("rv_unfreeze")
           is True
           and r.get("order_paid")
           is True,
           str((r.get("rv_st"),
                r.get("order_paid"))))

    print("\n[05 容器内: 回流全链]")
    record("首轮 1 信号(ok)",
           r.get("c1_labeled") == 1
           and (r.get("c1_signals")
                or {}).get(
               "exchange_ok") == 1,
           str((r.get("c1_labeled"),
                r.get("c1_signals"))))
    record("双轮幂等(0)",
           r.get("c2_labeled") == 0,
           str(r.get("c2_labeled")))
    record("pooled 回写",
           r.get("pooled") is True,
           str(r.get("pooled")))
    record("learn 观测面(off)",
           r.get("learn_mode") == "off",
           str(r.get("learn_mode")))

    print("\n[06 容器内: 调度]")
    record("手动四任务齐",
           r.get("sched_tasks") is True
           and r.get("sched_err") == 0,
           str((r.get("sched_tasks"),
                r.get("sched_err"))))
    record("44号 39 档案",
           r.get("reg_n") == 40,
           str(r.get("reg_n")))

    print("\n[07 HTTP 端点+鉴权]")
    # anchors 观测面
    ok, (code, body) = call(
        "GET", "/api/xx64/anchors",
        headers=MEMBER)
    record("HTTP anchors 200(观测面)",
           code == 200
           and "series" in (body or {}),
           str(code))
    # appeal 全链(off 不受开关)
    ok, (code, body) = call(
        "POST", "/api/xx64/appeals",
        body={"orderId": 1,
              "reason": "live appeal"},
        headers=MEMBER)
    appeal_id = (body or {}).get(
        "appealId") or 0
    record("HTTP appeal 200"
           "(off 不受开关)",
           code == 200
           and appeal_id > 0,
           str((code, appeal_id)))
    # review 翻转
    ok, (code, body) = call(
        "POST", f"/api/xx64/appeals/"
                f"{appeal_id}/review",
        body={"decision": "approve",
              "reviewNote": "live"},
        headers=ADMIN)
    record("HTTP review 200+翻转",
           code == 200
           and (body or {}).get("status")
           == "approved",
           str((code,
                (body or {}).get(
                    "status"))))
    # collect(已 pooled——幂等 0)
    ok, (code, body) = call(
        "POST", "/api/xx64/feedback/"
                "collect",
        body={},
        headers=ADMIN)
    record("HTTP collect 200 幂等",
           code == 200
           and (body or {}).get(
               "labeled") == 0,
           str((code,
                (body or {}).get(
                    "labeled"))))
    # learn/status
    ok, (code, body) = call(
        "GET", "/api/xx64/learn/status",
        headers=ADMIN)
    record("HTTP learn 200",
           code == 200
           and "factors"
           in (body or {}),
           str(code))
    # member review 403
    ok, (code, _) = call(
        "POST", "/api/xx64/appeals/1/"
                "review",
        body={"decision": "approve"},
        headers=MEMBER, expect=(403,))
    record("HTTP review member 403",
           code == 403, str(code))
    # 无 Role 403
    ok, (code, _) = call(
        "GET", "/api/xx64/anchors")
    record("HTTP 无 Role 403",
           code == 403, str(code))
    # 路由累计 24
    script = (
        "from routes.xx64_routes import "
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
    record("64号路由 P4 24 端点",
           count == 24, str(count))


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
