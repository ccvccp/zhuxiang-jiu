"""全站批次二·03积分主通道+13老酒兑换
AI 升级 Docker 实机验收(verify_batch2_ai_live)

运行方式:
    python verify_batch2_ai_live.py [基址]

前置: 容器已运行(含批次二 recycle_valuation
      代码——AI_ENFORCE_MODE 默认 observe)。

覆盖(《全站AI智能混合架构升级总计划》
批次二交付面, 真实容器 Redis 态):
  13号老酒兑换:
    01 正常业务零影响(健康+回收面)
    02 44号注册表 42 档案(recycle_valuation
       batch26 入册)
    03 observe 行为兼容(议价出价)
    04 决策快照留痕(八因子)
    05 接受议价→自动反馈
    06 拒绝议价→自动反馈
    07 红队RT-01 议价硬规则(±10%系数)
    08 红队RT-02 出价注入字段忽略
  03号积分主通道:
    09 返分通道 observe 兼容+回流
    10 抵现通道+回流
    11 退款通道+回流
    12 红队RT-03 积分硬规则(30%上限)
  通用:
    13 enforce 高风险拦截+低风险放行
       (容器内单元, 冷启动门槛喂料)
    14 AI 观测面 HTTP(双评分器)

×2 轮幂等验证(每轮清理种子重造——
889 用户域+points/recycle/credit 隔离键域
+双评分器学习键域)。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}
MEMBER = {"X-Member-Id": "889001"}

CONTAINER = "zhuxiang-jiu-backend-1"
REDIS = "zhuxiang-jiu-redis-1"

U_GOOD, U_BAD, U_PTS = 889001, 889003, 889011


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
        with urllib.request.urlopen(req, timeout=15) as r:
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


def clear_batch2(round_no: int) -> None:
    redis_del_keys("zhuxiang:points:*")
    redis_del_keys("zhuxiang:recycle:*")
    redis_del_keys("zhuxiang:credit:*889*")
    redis_del_keys(
        "zhuxiang:ai_learning:*points_risk*")
    redis_del_keys(
        "zhuxiang:ai_learning:*recycle"
        "_valuation*")


def run_pipeline(script: str) -> dict:
    """容器内 python 管道(纯 ASCII 源码)"""
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
# 种子管道(每轮重造)
# ------------------------------------------------------------
SEED = (
    "import asyncio, json\n"
    "async def m():\n"
    "    out = {}\n"
    # 信用账户(889001 优质/889003 低信任)
    "    from repositories.credit_repository import (\n"
    "        CreditRepository)\n"
    "    crepo = CreditRepository()\n"
    "    a1 = await crepo.get_or_create_score(889001)\n"
    "    a1.update(bambooScore=800,\n"
    "              creditLevel='L4',\n"
    "              status='normal')\n"
    "    await crepo.save_score(a1)\n"
    "    a3 = await crepo.get_or_create_score(889003)\n"
    "    a3.update(bambooScore=300,\n"
    "              creditLevel='L2',\n"
    "              status='normal')\n"
    "    await crepo.save_score(a3)\n"
    # 积分账户(889011)
    "    from repositories.points_repository import (\n"
    "        PointsRepository)\n"
    "    prepo = PointsRepository()\n"
    "    acct = await prepo.get_or_create"
    "_account(889011)\n"
    "    acct['totalPoints'] = 10000\n"
    "    acct['totalEarned'] = 10000\n"
    "    await prepo.save_account(acct)\n"
    # 889003 高频议价画像(5 单)
    "    from repositories.recycle_repository import (\n"
    "        RecycleRepository)\n"
    "    from core.helpers import ts\n"
    "    rrepo = RecycleRepository()\n"
    "    for i in range(5):\n"
    "        await rrepo.create_negotiation({\n"
    "            'userId': 889003,\n"
    "            'productId': 'ZX42-B2SEED',\n"
    "            'aiBasePrice': 900.0,\n"
    "            'currentPrice': 900.0,\n"
    "            'negotiationRound': 0,\n"
    "            'maxRounds': 3,\n"
    "            'status': 'rejected',\n"
    "            'history': [],\n"
    "            'createdAt': ts(),\n"
    "            'updatedAt': ts()})\n"
    # 注册表断言
    "    from services.ai_learning_service import (\n"
    "        SCORER_REGISTRY)\n"
    "    out['reg_n'] = len(SCORER_REGISTRY)\n"
    "    out['rv_in'] = ('recycle_valuation'\n"
    "                    in SCORER_REGISTRY)\n"
    "    out['pr_in'] = ('points_risk'\n"
    "                    in SCORER_REGISTRY)\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


def recycle_snapshot_check(neg_id: int) -> dict:
    return run_pipeline(
        "import asyncio, json\n"
        "async def m():\n"
        "    from repositories.ai_learning"
        "_repository import (\n"
        "        AiLearningRepository)\n"
        "    repo = AiLearningRepository()\n"
        "    snap = await repo.get_decision"
        "_snapshot(\n"
        "        'recycle_valuation',\n"
        f"        'negotiation:{neg_id}')\n"
        "    out = {'exists': snap is not None}\n"
        "    if snap:\n"
        "        out['score'] = snap.get('score')\n"
        "        out['decision'] = snap.get("
        "'decision')\n"
        "        out['factors'] = len(\n"
        "            snap.get('factors') or [])\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")


def feedback_check(scorer: str) -> dict:
    return run_pipeline(
        "import asyncio, json\n"
        "async def m():\n"
        "    from repositories.ai_learning"
        "_repository import (\n"
        "        AiLearningRepository)\n"
        "    repo = AiLearningRepository()\n"
        "    fbs = await repo.list_feedback(\n"
        f"        '{scorer}')\n"
        "    auto = [f for f in fbs\n"
        "            if f.get('source') == 'auto']\n"
        "    actions = sorted({f.get("
        "'actualAction')\n"
        "                      for f in auto})\n"
        "    print(json.dumps({\n"
        "        'n': len(auto),\n"
        "        'actions': actions}))\n"
        "asyncio.run(m())\n")


# ------------------------------------------------------------
# enforce 模式容器内单元(喂料越过冷启动门槛)
# ------------------------------------------------------------
ENFORCE_PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AI_ENFORCE_MODE'] = 'enforce'\n"
    "os.environ['AI_ENFORCE_SCOPES'] = "
    "'recycle_valuation,points_risk'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    "    for _ in range(55):\n"
    "        await repo.add_feedback({\n"
    "            'scorerId': 'recycle_valuation',\n"
    "            'factors': [{'name': "
    "'deviation_ratio',\n"
    "                         'score': 10.0,\n"
    "                         'weight': 0.22}],\n"
    "            'correct': True,\n"
    "            'createdAt': "
    "'2026-01-01T00:00:00'})\n"
    "        await repo.add_feedback({\n"
    "            'scorerId': 'points_risk',\n"
    "            'factors': [{'name': "
    "'earn_burst',\n"
    "                         'score': 10.0,\n"
    "                         'weight': 0.25}],\n"
    "            'correct': True,\n"
    "            'createdAt': "
    "'2026-01-01T00:00:00'})\n"
    # ① recycle 高风险拦截(889003: D级+
    # 3瓶+低信任+高频议价, 出价×1.10)
    "    from services.recycle_service import (\n"
    "        RecycleService)\n"
    "    from datetime import date, timedelta\n"
    "    rsvc = RecycleService()\n"
    "    pd = (date.today()\n"
    "          - timedelta(days=1058)"
    ").isoformat()\n"
    "    neg = await rsvc.submit_new_wine"
    "_valuation(\n"
    "        889003, 'ZX42-B2EF', 1000.0,\n"
    "        pd, 'D', 3)\n"
    "    blocked = False\n"
    "    try:\n"
    "        await rsvc.user_propose_price(\n"
    "            neg['id'],\n"
    "            round(neg['aiBasePrice']"
    " * 1.10, 2))\n"
    "    except ValueError as e:\n"
    "        blocked = ('风控拦截' in str(e))\n"
    "    out['rv_blocked'] = blocked\n"
    # ② points 高风险拦截(单元级)
    "    from services.ai_enforcement_points import (\n"
    "        enrich_points_channel,\n"
    "        enforce_points_action)\n"
    "    ctx = dict(await enrich_points"
    "_channel(889011, 'earn', 100))\n"
    "    ctx.update(todayEarned=500.0,\n"
    "               dailyRedeemCount=8,\n"
    "               singleChannelRatio=0.9,\n"
    "               sameDeviceAccounts=3,\n"
    "               violationCount=4,\n"
    "               nightActionRatio=0.5)\n"
    "    pts_blocked = False\n"
    "    try:\n"
    "        await enforce_points_action(\n"
    "            889011, 'earn', ctx, 'O-B2EF1')\n"
    "    except ValueError:\n"
    "        pts_blocked = True\n"
    "    out['pts_blocked'] = pts_blocked\n"
    # ③ 低风险放行(双模块)
    "    neg2 = await rsvc.submit_new_wine"
    "_valuation(\n"
    "        889001, 'ZX42-B2EF', 1000.0,\n"
    "        (date.today()\n"
    "         - timedelta(days=10)).isoformat(),\n"
    "        'A', 1)\n"
    "    rv_ok = False\n"
    "    try:\n"
    "        r = await rsvc.user_propose_price(\n"
    "            neg2['id'],\n"
    "            round(neg2['aiBasePrice']"
    " * 1.02, 2))\n"
    "        rv_ok = (r.get('status')\n"
    "                 == 'user_proposed')\n"
    "    except ValueError:\n"
    "        rv_ok = False\n"
    "    out['rv_ok'] = rv_ok\n"
    "    from services.points_service import (\n"
    "        PointsService)\n"
    "    pts_ok = False\n"
    "    try:\n"
    "        psvc = PointsService()\n"
    "        r = await psvc.earn_order_points(\n"
    "            889011, 'O-B2EF2', 100.0, 1)\n"
    "        pts_ok = (r.get('earnedPoints')\n"
    "                  == 150)\n"
    "    except ValueError:\n"
    "        pts_ok = False\n"
    "    out['pts_ok'] = pts_ok\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


def new_valuation(user_id, product_id, years_ago,
                  grade="A", bottles=1):
    purchase_date = (
            date.today()
            - timedelta(days=365 * years_ago + 10)
    ).isoformat()
    ok, (code, body) = call(
        "POST", "/api/recycle/new-wine/valuation",
        body={"userId": user_id,
              "productId": product_id,
              "purchasePrice": 1000.0,
              "purchaseDate": purchase_date,
              "conditionGrade": grade,
              "bottleCount": bottles},
        headers={"X-Member-Id": str(user_id)})
    return code, (body.get("data") or {})


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_batch2(round_no)
    seed = run_pipeline(SEED)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call(
        "GET", "/api/recycle/negotiations?limit=5")
    record("回收管理面 200", code == 200, str(code))

    print("\n[02 44号注册表]")
    record("42 档案(batch26 入册)",
           seed.get("reg_n") == 55
           and seed.get("rv_in") is True
           and seed.get("pr_in") is True,
           str(seed.get("reg_n")))

    print("\n[03 observe 行为兼容(议价)]")
    code, neg = new_valuation(
        U_GOOD, "ZX42-B2L1", 0)
    neg_id = neg.get("id")
    ai_base = neg.get("aiBasePrice") or 0
    record("新酒估价 200",
           code == 200 and neg_id
           and ai_base > 0,
           f"code={code}")
    ok, (code, body) = call(
        "POST",
        f"/api/recycle/negotiation/{neg_id}/propose",
        body={"proposedPrice": round(ai_base * 1.02, 2),
              "reason": "品质良好"},
        headers={"X-Member-Id": str(U_GOOD)})
    record("observe 出价兼容",
           code == 200
           and (body.get("data") or {})
           .get("status") == "user_proposed",
           f"code={code}")

    print("\n[04 决策快照留痕]")
    snap = recycle_snapshot_check(neg_id)
    record("快照存在+低风险+八因子",
           snap.get("exists") is True
           and snap.get("decision") == "low"
           and snap.get("factors") == 8,
           str(snap))

    print("\n[05 接受议价→自动反馈]")
    ok, (code, body) = call(
        "POST",
        f"/api/recycle/negotiation/{neg_id}/accept",
        body={"acceptedBy": "user"},
        headers={"X-Member-Id": str(U_GOOD)})
    record("接受议价 200", code == 200, str(code))
    fb = feedback_check("recycle_valuation")
    record("接受→自动反馈(auto)",
           (fb.get("n") or 0) >= 1
           and "accepted" in (fb.get("actions")
                              or []),
           str(fb))

    print("\n[06 拒绝议价→自动反馈]")
    code, neg2 = new_valuation(
        U_GOOD, "ZX42-B2L2", 0)
    ok, (code, _) = call(
        "POST",
        f"/api/recycle/negotiation/"
        f"{neg2.get('id')}/propose",
        body={"proposedPrice":
                  round((neg2.get("aiBasePrice")
                         or 900) * 1.05, 2)},
        headers={"X-Member-Id": str(U_GOOD)})
    ok, (code, _) = call(
        "POST",
        f"/api/recycle/negotiation/"
        f"{neg2.get('id')}/reject",
        body={"rejectedBy": "user",
              "reason": "价格不合适"},
        headers={"X-Member-Id": str(U_GOOD)})
    record("拒绝议价 200", code == 200, str(code))
    fb = feedback_check("recycle_valuation")
    record("拒绝→自动反馈(累计≥2)",
           (fb.get("n") or 0) >= 2
           and "rejected" in (fb.get("actions")
                              or []),
           str(fb))

    print("\n[07 红队RT-01 议价硬规则]")
    code, neg3 = new_valuation(
        U_GOOD, "ZX42-B2L3", 0)
    ok, (code, body) = call(
        "POST",
        f"/api/recycle/negotiation/"
        f"{neg3.get('id')}/propose",
        body={"proposedPrice":
                  round((neg3.get("aiBasePrice")
                         or 900) * 1.2, 2)},
        headers={"X-Member-Id": str(U_GOOD)},
        expect=(400, 409))
    record("系数超±10%拒绝",
           code in (400, 409), str(code))

    print("\n[08 红队RT-02 出价注入]")
    code, neg4 = new_valuation(
        U_GOOD, "ZX42-B2L4", 0)
    ai_base4 = neg4.get("aiBasePrice") or 0
    ok, (code, body) = call(
        "POST",
        f"/api/recycle/negotiation/"
        f"{neg4.get('id')}/propose",
        body={"proposedPrice":
                  round(ai_base4 * 1.05, 2),
              # 伪造字段(服务端必须忽略)
              "aiBasePrice": 1.0,
              "bambooScore": 1000,
              "conditionGrade": "A"},
        headers={"X-Member-Id": str(U_GOOD)})
    ok, (code, neg4_after) = call(
        "GET",
        f"/api/recycle/negotiation/"
        f"{neg4.get('id')}")
    record("注入字段被忽略(基准价不变)",
           code == 200
           and (neg4_after.get("data") or {})
           .get("aiBasePrice") == ai_base4,
           str((neg4_after.get("data") or {})
               .get("aiBasePrice")))

    print("\n[09 积分返分通道]")
    ok, (code, body) = call(
        "POST", "/api/points/earn/order",
        body={"userId": U_PTS,
              "orderId": "O-B2-E1",
              "orderAmount": 100.0,
              "memberLevel": 1},
        headers={"X-Member-Id": str(U_PTS)})
    record("返分 observe 兼容",
           code == 200
           and (body.get("data") or {})
           .get("earnedPoints") == 150,
           f"code={code}")

    print("\n[10 积分抵现通道]")
    ok, (code, body) = call(
        "POST", "/api/points/deduct",
        body={"userId": U_PTS,
              "orderId": "O-B2-D1",
              "orderAmount": 1000.0,
              "deductPoints": 100},
        headers={"X-Member-Id": str(U_PTS)})
    record("抵现 observe 兼容",
           code == 200
           and (body.get("data") or {})
           .get("deductPoints") == 100,
           f"code={code}")

    print("\n[11 积分退款通道+三通道回流]")
    ok, (code, body) = call(
        "POST", "/api/points/refund",
        body={"userId": U_PTS,
              "orderId": "O-B2-E1",
              "refundPoints": 100},
        headers={"X-Member-Id": str(U_PTS)})
    record("退款 observe 兼容",
           code == 200, str(code))
    fb = feedback_check("points_risk")
    actions = fb.get("actions") or []
    record("三通道各回流一条",
           {"earn_settled",
            "deduct_settled",
            "refund_settled"} <= set(actions),
           str(actions))

    print("\n[12 红队RT-03 积分硬规则]")
    ok, (code, _) = call(
        "POST", "/api/points/deduct",
        body={"userId": U_PTS,
              "orderId": "O-B2-D2",
              "orderAmount": 100.0,
              "deductPoints": 5000},
        headers={"X-Member-Id": str(U_PTS)},
        expect=(400, 409))
    record("抵现超30%上限拒绝",
           code in (400, 409), str(code))

    print("\n[13 enforce 拦截(容器内)]")
    r = run_pipeline(ENFORCE_PIPELINE)
    record("议价高风险拦截+低风险放行",
           r.get("rv_blocked") is True
           and r.get("rv_ok") is True,
           str(r))
    record("积分高风险拦截+低风险放行",
           r.get("pts_blocked") is True
           and r.get("pts_ok") is True,
           str(r))

    print("\n[14 AI 观测面 HTTP]")
    for scorer in ("recycle_valuation",
                   "points_risk"):
        ok, (code, body) = call(
            "GET",
            f"/api/ai-learning/enforcement/"
            f"{scorer}/overview",
            headers=ADMIN)
        record(f"{scorer} overview 200",
               code == 200, str(code))


def main() -> int:
    print("=" * 62)
    print("全站批次二·03积分+13老酒兑换 AI 升级"
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
