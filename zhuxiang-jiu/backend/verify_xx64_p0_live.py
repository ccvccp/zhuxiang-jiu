"""64号信值兑换管理 P0 Docker 实机验收

运行方式:
    python verify_xx64_p0_live.py [基址]

前置: 容器已运行(含 64号 P0 代码)。

覆盖(64号计划 §八 P0, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(orders 409;
       registry/quota 观测面 200)
    03 容器内: 刚性规则底座全链
       (R1-R7+预校验四查+订单
        状态机+锁值+窗口快照
        +取消解锁+第38档案)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
xx64+trust45 种子键域)。
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
    # 45号测试档案种子(仅测试主体——
    # digest-999x 隔离域)
    redis_del_keys(
        "zhuxiang:trust45:trust45_profiles:"
        "digest-999*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['XX64_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 45号档案种子(测试隔离域)
    "    from repositories.trust_value"
    "_repository import (\n"
    "        TrustValue45Repository)\n"
    "    repo45 = TrustValue45Repository()\n"
    "    await repo45.save_profile({\n"
    "        'trustId': 9901,\n"
    "        'role': 'person',\n"
    "        'name': 'live-test',\n"
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
    # ② 注册表
    "    from services.xx64_registry import (\n"
    "        CASH_PORTION,\n"
    "        CUMULATIVE_QUOTA_RATIO,\n"
    "        ORDER_STATES,\n"
    "        POINTS_PER_TRUST,\n"
    "        SINGLE_QUOTA_RATIO,\n"
    "        TRUST_PORTION,\n"
    "        registry_view)\n"
    "    out['r1'] = (\n"
    "        TRUST_PORTION == 0.30\n"
    "        and CASH_PORTION == 0.70)\n"
    "    out['r4'] = (\n"
    "        SINGLE_QUOTA_RATIO == 0.20)\n"
    "    out['r5'] = (\n"
    "        CUMULATIVE_QUOTA_RATIO\n"
    "        == 0.40)\n"
    "    out['r6'] = (\n"
    "        POINTS_PER_TRUST == 100)\n"
    "    out['states'] = len(\n"
    "        ORDER_STATES)\n"
    "    view = registry_view()\n"
    "    out['rules_n'] = len(\n"
    "        view.get('rigidRules'))\n"
    "    out['mode'] = (\n"
    "        view.get('mode'))\n"
    # ③ off 铁律
    "    from services.xx64_service "
    "import Xx64Service\n"
    "    svc = Xx64Service()\n"
    "    os.environ['XX64_MODE'] = 'off'\n"
    "    try:\n"
    "        await svc.create_order(\n"
    "            101, 202, 9901, 100)\n"
    "        out['off_rej'] = False\n"
    "    except ValueError:\n"
    "        out['off_rej'] = True\n"
    "    os.environ['XX64_MODE'] = "
    "'shadow'\n"
    # ④ 预校验四查
    "    c = await svc.precheck(\n"
    "        9901, 100)\n"
    "    out['pc_pass'] = (\n"
    "        c.get('passed'))\n"
    "    out['pc_tv'] = (\n"
    "        c.get('trustValue'))\n"
    "    out['pc_cv'] = (\n"
    "        c.get('cashValue'))\n"
    "    out['pc_sq'] = (\n"
    "        c.get('singleQuota'))\n"
    "    out['pc_bal'] = (\n"
    "        c.get('balance'))\n"
    # ⑤ 订单创建+锁值
    "    r = await svc.create_order(\n"
    "        101, 202, 9901, 100,\n"
    "        product='live-good',\n"
    "        created_by='memberA')\n"
    "    out['od_status'] = (\n"
    "        r.get('status'))\n"
    "    out['od_tv'] = (\n"
    "        r.get('trustValue'))\n"
    "    out['od_snap'] = (\n"
    "        r.get('balanceSnapshot'))\n"
    "    out['od_fp'] = str(\n"
    "        r.get('fingerprint')\n"
    "        or '')[:7]\n"
    # ⑥ R4 超限拒绝(1000→300>100)
    "    try:\n"
    "        await svc.create_order(\n"
    "            101, 202, 9901, 1000)\n"
    "        out['r4_rej'] = False\n"
    "    except ValueError as e:\n"
    "        out['r4_rej'] = (\n"
    "            'R4_SINGLE' in str(e))\n"
    # ⑦ 窗口用量
    "    c2 = await svc.precheck(\n"
    "        9901, 100)\n"
    "    out['win_used'] = (\n"
    "        c2.get('windowUsed'))\n"
    "    out['win_quota'] = (\n"
    "        c2.get('cumulativeQuota'))\n"
    # ⑧ 取消解锁
    "    cv = await svc.cancel_order(1)\n"
    "    out['cancel'] = (\n"
    "        cv.get('status'))\n"
    "    out['released'] = (\n"
    "        cv.get('released'))\n"
    "    c3 = await svc.precheck(\n"
    "        9901, 100)\n"
    "    out['win_after'] = (\n"
    "        c3.get('windowUsed'))\n"
    # ⑨ Redis 读回
    "    from repositories.xx64"
    "_repository import (\n"
    "        Xx64Repository)\n"
    "    repo = Xx64Repository()\n"
    "    d1 = await repo.get_order(1)\n"
    "    out['rd_status'] = (\n"
    "        d1.get('status'))\n"
    "    out['rd_pc'] = isinstance(\n"
    "        d1.get('precheck'), dict)\n"
    # ⑩ 限额观测
    "    q = await svc.quota_status(\n"
    "        9901)\n"
    "    out['q_sq'] = (\n"
    "        q.get('singleQuota'))\n"
    "    out['q_cq'] = (\n"
    "        q.get('cumulativeQuota'))\n"
    # ⑪ 事件留痕
    "    evs = await repo.list_events(\n"
    "        limit=20)\n"
    "    out['ev_n'] = len(evs)\n"
    # ⑫ 第38档案
    "    from services.xx64_scorer "
    "import Xx64Scorer\n"
    "    sc = await Xx64Scorer().score({\n"
    "        'exchangeHealth': 0.95,\n"
    "        'tier': 'trusted'})\n"
    "    out['sc_factors'] = len(\n"
    "        sc.get('factors') or [])\n"
    "    out['sc_dec'] = (\n"
    "        sc.get('decision'))\n"
    # ⑬ 44号档案
    "    from services.ai_learning"
    "_service import (\n"
    "        SCORER_REGISTRY)\n"
    "    out['reg_n'] = len(\n"
    "        SCORER_REGISTRY)\n"
    "    out['ve_in'] = (\n"
    "        'value_exchange'\n"
    "        in SCORER_REGISTRY)\n"
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

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/xx64/orders",
        body={"buyerId": 101,
              "sellerId": 202,
              "trustId": 9901,
              "price": 100},
        headers=MEMBER, expect=(409,))
    record("off 态 orders 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/xx64/registry",
        headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and len(body.get("rigidRules")
                   or {}) == 7
           and body.get("mode") == "off",
           str((code,
                len(body.get(
                    "rigidRules")
                    or {}))))

    print("\n[03 容器内: 刚性规则全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("R1 占比 30/70",
           r.get("r1") is True,
           str(r.get("r1")))
    record("R4 单次 20%",
           r.get("r4") is True,
           str(r.get("r4")))
    record("R5 累计 40%",
           r.get("r5") is True,
           str(r.get("r5")))
    record("R6 积分 100:1",
           r.get("r6") is True,
           str(r.get("r6")))
    record("订单九态",
           r.get("states") == 9,
           str(r.get("states")))
    record("R1-R7 自描述(7 条)",
           r.get("rules_n") == 7,
           str(r.get("rules_n")))
    record("off 铁律(创建拒绝)",
           r.get("off_rej") is True,
           str(r.get("off_rej")))
    record("预校验通过(价 100)",
           r.get("pc_pass") is True,
           str(r.get("pc_pass")))
    record("30/70 结构(30+70)",
           r.get("pc_tv") == 30.0
           and r.get("pc_cv") == 70.0,
           str((r.get("pc_tv"),
                r.get("pc_cv"))))
    record("单次上限(500×20%=100)",
           r.get("pc_sq") == 100.0
           and r.get("pc_bal") == 500.0,
           str((r.get("pc_sq"),
                r.get("pc_bal"))))
    record("订单创建(reserved)",
           r.get("od_status")
           == "reserved",
           str(r.get("od_status")))
    record("锁值+快照(30/500)",
           r.get("od_tv") == 30.0
           and r.get("od_snap")
           == 500.0,
           str((r.get("od_tv"),
                r.get("od_snap"))))
    record("指纹链(sha256)",
           r.get("od_fp") == "sha256:",
           str(r.get("od_fp")))
    record("R4 超限拒绝"
           "(1000→300>100)",
           r.get("r4_rej") is True,
           str(r.get("r4_rej")))
    record("窗口用量(30)",
           r.get("win_used") == 30.0,
           str(r.get("win_used")))
    record("窗口累计上限"
           "(500×40%=200)",
           r.get("win_quota") == 200.0,
           str(r.get("win_quota")))
    record("取消解锁(cancelled)",
           r.get("cancel")
           == "cancelled"
           and r.get("released") == 30.0,
           str((r.get("cancel"),
                r.get("released"))))
    record("取消后窗口回退(0)",
           r.get("win_after") == 0.0,
           str(r.get("win_after")))
    record("Redis 读回(订单+"
           "precheck dict)",
           r.get("rd_status")
           == "cancelled"
           and r.get("rd_pc") is True,
           str((r.get("rd_status"),
                r.get("rd_pc"))))
    record("限额观测(100/200)",
           r.get("q_sq") == 100.0
           and r.get("q_cq") == 200.0,
           str((r.get("q_sq"),
                r.get("q_cq"))))
    record("事件链(×2)",
           r.get("ev_n") == 2,
           str(r.get("ev_n")))
    record("第38档案八因子",
           r.get("sc_factors") == 8,
           str(r.get("sc_factors")))
    record("高分决策 optimize/urgent",
           r.get("sc_dec") in (
               "optimize", "urgent"),
           str(r.get("sc_dec")))
    record("44号 39 档案",
           r.get("reg_n") == 40,
           str(r.get("reg_n")))
    record("value_exchange 在册",
           r.get("ve_in") is True,
           str(r.get("ve_in")))

    print("\n[04 HTTP 端点+鉴权]")
    # 服务器态 off——决策面 409
    ok, (code, _) = call(
        "POST", "/api/xx64/orders",
        body={"buyerId": 101,
              "sellerId": 202,
              "trustId": 9901,
              "price": 100},
        headers=MEMBER, expect=(409,))
    record("HTTP orders off 409"
           "(服务器态)",
           code == 409, str(code))
    # 观测面(容器内种子的订单读回)
    ok, (code, body) = call(
        "GET", "/api/xx64/orders",
        headers=ADMIN)
    record("HTTP orders 列表"
           "(Redis 读回)",
           code == 200
           and (body.get("total") or 0)
           >= 1,
           str((code, body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/xx64/orders/1",
        headers=MEMBER)
    record("HTTP orders 详情"
           "(Redis 读回)",
           code == 200
           and ((body.get("order") or {})
                .get("buyerId")) == 101,
           str(code))
    ok, (code, _) = call(
        "GET", "/api/xx64/orders/999",
        headers=ADMIN, expect=(404,))
    record("HTTP orders 详情 404",
           code == 404, str(code))
    ok, (code, body) = call(
        "GET", "/api/xx64/quota"
               "?trust_id=9901",
        headers=MEMBER)
    record("HTTP quota 观测面",
           code == 200
           and (body.get("singleQuota")
                or 0) == 100.0,
           str((code,
                body.get(
                    "singleQuota"))))
    # 鉴权 403
    for method, path in (
            ("GET", "/api/xx64/registry"),
            ("POST", "/api/xx64/orders"),
            ("GET", "/api/xx64/orders"),
            ("GET", "/api/xx64/quota?"
                   "trust_id=9901"),
            ("GET",
             "/api/xx64/model/status")):
        resp_ok, (c, _) = call(
            method, path, body={})
        short = path.split('/')[-1] \
            .split('?')[0]
        record(f"HTTP {short}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 6
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
    record("64号路由 P0 6 端点",
           count == 6, str(count))


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
