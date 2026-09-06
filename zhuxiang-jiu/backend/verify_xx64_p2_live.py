"""64号信值兑换管理 P2 Docker 实机验收

运行方式:
    python verify_xx64_p2_live.py [基址]

前置: 容器已运行(含 64号 P2 代码)。

覆盖(64号计划 §八 P2, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(pay 409)+
       P2 观测面 off 可用(plan)
    03 容器内: 智能体验全链(最优
       支付组合方案 A/B 互斥对比+
       积分缺口换算+智能凑单密度
       排序限额组合+订单创建支付
       +规则可视化解释 R1-R7)
    04 HTTP 端点+鉴权(plan 凑单
       candidates 串+explain 404
       +plan 非法 409+无 Role 403)

×2 轮幂等验证(每轮清理种子重造——
xx64+points+trust45 种子键域)。
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
    # 积分账户种子域(测试用户 9901)
    redis_del_keys(
        "zhuxiang:points:account:9901")
    redis_del_keys(
        "zhuxiang:points:logs_by_user:"
        "9901")
    # 45号测试档案种子域(9901/9903)
    redis_del_keys(
        "zhuxiang:trust45:trust45_profiles:"
        "9901")
    redis_del_keys(
        "zhuxiang:trust45:trust45_profiles:"
        "9903")
    redis_del_keys(
        "zhuxiang:trust45:idmap:digest-999*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['XX64_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 种子(9901 余额足/9903 低余额)
    "    from repositories.trust_value"
    "_repository import (\n"
    "        TrustValue45Repository)\n"
    "    repo45 = TrustValue45Repository()\n"
    "    await repo45.save_profile({\n"
    "        'trustId': 9901,\n"
    "        'role': 'person',\n"
    "        'name': 'live-p2',\n"
    "        'idDigest': 'digest-99901',\n"
    "        'factors': {},\n"
    "        'score': 500.0,\n"
    "        'rawScore': 500.0,\n"
    "        'grade': 'A',\n"
    "        'fused': False,\n"
    "        'frozen': False,\n"
    "        'createdAt':\n"
    "            '2026-01-01T00:00:00',\n"
    "        'updatedAt':\n"
    "            '2026-01-01T00:00:00'})\n"
    "    await repo45.save_profile({\n"
    "        'trustId': 9903,\n"
    "        'role': 'person',\n"
    "        'name': 'live-p2-low',\n"
    "        'idDigest': 'digest-99903',\n"
    "        'factors': {},\n"
    "        'score': 5.0,\n"
    "        'rawScore': 5.0,\n"
    "        'grade': 'C',\n"
    "        'fused': False,\n"
    "        'frozen': False,\n"
    "        'createdAt':\n"
    "            '2026-01-01T00:00:00',\n"
    "        'updatedAt':\n"
    "            '2026-01-01T00:00:00'})\n"
    # ② 最优支付组合(方案 A/B 对比)
    "    from services.xx64_experience"
    "_service import (\n"
    "        Xx64ExperienceService)\n"
    "    exp = Xx64ExperienceService()\n"
    "    p = await exp.payment_plan(\n"
    "        9901, 100, discount_value=15)\n"
    "    out['planA_tv'] = ((\n"
    "        p.get('planA') or {})\n"
    "        .get('trustValue'))\n"
    "    out['planA_cash'] = ((\n"
    "        p.get('planA') or {})\n"
    "        .get('cash'))\n"
    "    out['planA_feas'] = ((\n"
    "        p.get('planA') or {})\n"
    "        .get('feasible'))\n"
    "    out['planB_cash'] = ((\n"
    "        p.get('planB') or {})\n"
    "        .get('cash'))\n"
    "    out['cmp_better'] = ((\n"
    "        p.get('comparison') or {})\n"
    "        .get('betterPlan'))\n"
    "    out['cmp_diff'] = ((\n"
    "        p.get('comparison') or {})\n"
    "        .get('cashDiff'))\n"
    "    out['gap'] = ((\n"
    "        p.get('planA') or {})\n"
    "        .get('gap'))\n"
    # ③ 大优惠 B 更省(35>30)
    "    p2 = await exp.payment_plan(\n"
    "        9901, 100, discount_value=35)\n"
    "    out['cmp2_better'] = ((\n"
    "        p2.get('comparison') or {})\n"
    "        .get('betterPlan'))\n"
    # ④ 低余额缺口换算(缺 25→2500)
    "    p3 = await exp.payment_plan(\n"
    "        9903, 100)\n"
    "    out['gap3'] = ((\n"
    "        p3.get('planA') or {})\n"
    "        .get('gap'))\n"
    "    out['gap_pts'] = ((\n"
    "        p3.get('planA') or {})\n"
    "        .get('gapPoints'))\n"
    "    out['feas3'] = ((\n"
    "        p3.get('planA') or {})\n"
    "        .get('feasible'))\n"
    # ⑤ 智能凑单(密度排序+限额组合)
    "    sf = await exp.smart_fill(\n"
    "        9901, candidates=[\n"
    "            {'name': 'itemA',\n"
    "             'price': 200},\n"
    "            {'name': 'itemB',\n"
    "             'price': 100},\n"
    "            {'name': 'itemC',\n"
    "             'price': 50}])\n"
    "    out['sf_rank'] = [\n"
    "        i['name'] for i in (\n"
    "            sf.get('ranked') or [])]\n"
    "    out['sf_quota'] = (\n"
    "        sf.get('singleQuota'))\n"
    "    out['sf_aff'] = [\n"
    "        i['name'] for i in (\n"
    "            sf.get('affordable') or [])]\n"
    "    out['sf_used'] = (\n"
    "        sf.get('affordableTrustTotal'))\n"
    # ⑥ 订单创建+支付
    "    from services.xx64_service import (\n"
    "        Xx64Service)\n"
    "    from services.xx64_settle"
    "_service import (\n"
    "        Xx64SettleService)\n"
    "    r = await Xx64Service().create_order(\n"
    "        9901, 9902, 9901, 100,\n"
    "        product='live-p2-good')\n"
    "    pay = await Xx64SettleService"
    "().pay_order(\n"
    "        r['orderId'])\n"
    "    out['pay_st'] = (\n"
    "        pay.get('status'))\n"
    # ⑦ 规则可视化解释(R1-R7)
    "    ex = await exp.explain_order(\n"
    "        r['orderId'])\n"
    "    steps = ex.get('steps') or []\n"
    "    out['exp_n'] = len(steps)\n"
    "    out['exp_rules'] = [\n"
    "        s.get('rule') for s in steps]\n"
    "    out['exp_st'] = ((\n"
    "        ex.get('order') or {})\n"
    "        .get('status'))\n"
    "    out['exp_src'] = all(\n"
    "        s.get('source') for s in steps)\n"
    "    out['exp_r1'] = next(\n"
    "        (s.get('calc') for s in steps\n"
    "         if s.get('rule') == 'R1'), '')\n"
    # ⑧ explain 不存在
    "    try:\n"
    "        await exp.explain_order(99999)\n"
    "        out['exp_404'] = False\n"
    "    except KeyError:\n"
    "        out['exp_404'] = True\n"
    # ⑨ 44号
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

    print("\n[02 off 铁律]")
    ok, (code, _) = call(
        "POST", "/api/xx64/orders/1/pay",
        body={},
        headers=MEMBER, expect=(409,))
    record("off 态 pay 409(服务器态)",
           code == 409, str(code))

    print("\n[03 容器内: 智能体验全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("方案 A 结构(30 信值+70 现金)",
           r.get("planA_tv") == 30.0
           and r.get("planA_cash") == 70.0,
           str((r.get("planA_tv"),
                r.get("planA_cash"))))
    record("方案 A 可行",
           r.get("planA_feas") is True,
           str(r.get("planA_feas")))
    record("方案 B 优惠(85 现金)",
           r.get("planB_cash") == 85.0,
           str(r.get("planB_cash")))
    record("互斥对比(A 现金更省 15)",
           r.get("cmp_better") == "A"
           and r.get("cmp_diff") == 15.0,
           str((r.get("cmp_better"),
                r.get("cmp_diff"))))
    record("余额足无缺口",
           r.get("gap") == 0.0,
           str(r.get("gap")))
    record("大优惠 B 更省(35>30)",
           r.get("cmp2_better") == "B",
           str(r.get("cmp2_better")))
    record("低余额缺口(25→2500 积分)",
           r.get("gap3") == 25.0
           and r.get("gap_pts") == 2500.0,
           str((r.get("gap3"),
                r.get("gap_pts"))))
    record("低余额方案 A 不可行",
           r.get("feas3") is False,
           str(r.get("feas3")))
    record("凑单密度排序(价格降序)",
           r.get("sf_rank")
           == ["itemA", "itemB", "itemC"],
           str(r.get("sf_rank")))
    record("单次限额(500×20%=100)",
           r.get("sf_quota") == 100.0,
           str(r.get("sf_quota")))
    record("限额内组合(A+B 不含 C)",
           r.get("sf_aff")
           == ["itemA", "itemB"]
           and r.get("sf_used") == 90.0,
           str((r.get("sf_aff"),
                r.get("sf_used"))))
    record("订单支付(paid)",
           r.get("pay_st") == "paid",
           str(r.get("pay_st")))
    record("解释六步(R1-R7)",
           r.get("exp_n") == 6
           and r.get("exp_rules")
           == ["R1", "R2", "R4",
               "R5", "R6", "R7"],
           str((r.get("exp_n"),
                r.get("exp_rules"))))
    record("解释订单摘要(paid)",
           r.get("exp_st") == "paid",
           str(r.get("exp_st")))
    record("溯源字段齐备",
           r.get("exp_src") is True,
           str(r.get("exp_src")))
    record("R1 计算可溯源(100×30%)",
           "30.0" in str(r.get("exp_r1"))
           and "70.0" in str(r.get("exp_r1")),
           str(r.get("exp_r1"))[:80])
    record("订单不存在 KeyError",
           r.get("exp_404") is True,
           str(r.get("exp_404")))
    record("44号 39 档案",
           r.get("reg_n") == 39,
           str(r.get("reg_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # off 态(服务器态)P2 观测面可用
    # (管道已种档案)
    ok, (code, body) = call(
        "GET", "/api/xx64/plan"
               "?trust_id=9901&price=100",
        headers=MEMBER)
    record("off 态 plan 观测面 200",
           code == 200
           and "planA" in (body or {}),
           str(code))
    # plan 凑单(candidates 串)
    ok, (code, body) = call(
        "GET", "/api/xx64/plan"
               "?trust_id=9901&price=100"
               "&candidates=itemA:200,"
               "itemB:100,itemC:50",
        headers=MEMBER)
    sf = (body.get("smartFill") or {})
    record("HTTP plan 凑单"
           "(密度+限额组合)",
           code == 200
           and [i.get("name") for i in (
               sf.get("affordable") or [])]
           == ["itemA", "itemB"],
           str((code,
                [i.get("name") for i in (
                    sf.get("affordable")
                    or [])])))
    # explain 观测面(订单 1——管道所建)
    ok, (code, body) = call(
        "GET", "/api/xx64/orders/1/explain",
        headers=MEMBER)
    record("HTTP explain 200(六步)",
           code == 200
           and len(body.get("steps") or [])
           == 6,
           str((code,
                len(body.get("steps")
                    or []))))
    # explain 不存在 404
    ok, (code, _) = call(
        "GET", "/api/xx64/orders/99999/explain",
        headers=MEMBER, expect=(404,))
    record("HTTP explain 404",
           code == 404, str(code))
    # plan 非法 409
    ok, (code, _) = call(
        "GET", "/api/xx64/plan"
               "?trust_id=9901&price=0",
        headers=MEMBER, expect=(409,))
    record("HTTP plan 非法 409",
           code == 409, str(code))
    # 鉴权 403
    for path in ("/api/xx64/plan"
                 "?trust_id=9901&price=100",
                 "/api/xx64/orders/1/explain"):
        resp_ok, (c, _) = call("GET", path)
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403",
               c == 403, str(c))
    # 路由累计 14
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
    record("64号路由 P2 14 端点",
           count == 14, str(count))


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
