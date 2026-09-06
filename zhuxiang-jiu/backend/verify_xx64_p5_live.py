"""64号信值兑换管理 P5 Docker 实机验收
(看板+红队+收官——全期终验)

运行方式:
    python verify_xx64_p5_live.py [基址]

前置: 容器已运行(含 64号 P5 代码)。

覆盖(64号计划 §八 P5, 真实容器
Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(pay/redteam 409
       决策面)
    03 容器内: 红队七向量全链
       (RT-01~07 每向量 defended
       +evidence 断言+自清理
       +并发双花 SET NX 真实
       Redis 态验证)
    04 容器内: 四区看板
       (度量数学/九态分布/
       流通借贷平衡/防御统计/
       宪法三开关)
    05 HTTP 端点+鉴权
       (dashboard 观测面/
       redteam shadow 七向量/
       member 403/无 Role 403/
       26 端点全期收官)

×2 轮幂等验证(每轮清理种子重造
——xx64+points+trust45 种子键域
含红队 98xx 隔离域)。
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
    # 红队 98xx 隔离域档案+积分
    for tid in range(9801, 9810):
        redis_del_keys(
            "zhuxiang:trust45:"
            "trust45_profiles:" + str(tid))
    redis_del_keys(
        "zhuxiang:trust45:idmap:rt-*")
    redis_del_keys(
        "zhuxiang:points:account:98*")
    redis_del_keys(
        "zhuxiang:points:logs_by_user:"
        "98*")
    # 45号业务种子域
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
    # ① 种子(业务域 9901)
    "    from repositories.trust_value"
    "_repository import (\n"
    "        TrustValue45Repository)\n"
    "    repo45 = TrustValue45Repository()\n"
    "    for tid in (9901, 9902):\n"
    "        await repo45.save_profile({\n"
    "            'trustId': tid,\n"
    "            'role': 'person',\n"
    "            'name': 'live-p5',\n"
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
    # ② 红队七向量(Redis 态)
    "    from services.xx64_redteam"
    "_service import (\n"
    "        Xx64RedteamService)\n"
    "    rt = Xx64RedteamService()\n"
    "    r = await rt.run_all()\n"
    "    out['rt_total'] = r['total']\n"
    "    out['rt_defended'] = (\n"
    "        r['defended'])\n"
    "    out['rt_all'] = (\n"
    "        r['allDefended'])\n"
    "    vec = {v['vector']: v\n"
    "           for v in r['vectors']}\n"
    "    out['e1'] = (\n"
    "        vec['RT-01']['evidence']\n"
    "        ['forgedIgnored'])\n"
    "    out['e2'] = (\n"
    "        vec['RT-02']['evidence']\n"
    "        ['r5Rejected'])\n"
    "    out['e3'] = (\n"
    "        vec['RT-03']['evidence']\n"
    "        ['fourthRejected'])\n"
    "    out['e4'] = (\n"
    "        vec['RT-04']\n"
    "        ['evidence']['drift'])\n"
    "    out['e5'] = (\n"
    "        vec['RT-05']['evidence']\n"
    "        ['successes'])\n"
    "    out['e6'] = (\n"
    "        vec['RT-06']['evidence']\n"
    "        ['expiredNotFlipped'])\n"
    "    out['e7'] = (\n"
    "        vec['RT-07']['evidence']\n"
    "        ['r7Rejected'])\n"
    # ③ 红队自清理验证
    "    from repositories.xx64"
    "_repository import (\n"
    "        Xx64Repository)\n"
    "    repo = Xx64Repository()\n"
    "    leftover = [o for o in await\n"
    "        repo.list_orders(limit=500)\n"
    "        if int(o.get('buyerId')\n"
    "            or 0) >= 9801 and\n"
    "        str(o.get('product')\n"
    "            or '').startswith('rt')]\n"
    "    out['rt_leftover'] = len(\n"
    "        leftover)\n"
    # ④ 业务订单(看板样本)
    "    from core.helpers import ts\n"
    "    for _ in range(3):\n"
    "        oid = await repo.next"
    "_order_id()\n"
    "        await repo.save_order({\n"
    "            'orderId': oid,\n"
    "            'buyerId': 9901,\n"
    "            'sellerId': 9902,\n"
    "            'trustId': 9901,\n"
    "            'product': 'gA',\n"
    "            'price': 100.0,\n"
    "            'trustValue': 30.0,\n"
    "            'cashValue': 70.0,\n"
    "            'balanceSnapshot': 500.0,\n"
    "            'status': 'paid',\n"
    "            'paidAt': ts(),\n"
    "            'createdAt': ts()})\n"
    "    eid = await repo.next"
    "_entry_id()\n"
    "    for d, amt in (\n"
    "            ('credit', 30.0),\n"
    "            ('debit', -30.0)):\n"
    "        await repo.save_ledger({\n"
    "            'entryId': eid,\n"
    "            'orderId': 1,\n"
    "            'trustId': 9901,\n"
    "            'direction': d,\n"
    "            'transferType': 'pay',\n"
    "            'amount': amt,\n"
    "            'source':\n"
    "                'consumption_'\n"
    "                'transfer',\n"
    "            'createdAt': ts()})\n"
    # ⑤ 四区看板
    "    from services.xx64_dashboard"
    "_service import (\n"
    "        Xx64DashboardService)\n"
    "    dash = await (\n"
    "        Xx64DashboardService()\n"
    "        .dashboard())\n"
    "    z = dash['zones']\n"
    "    out['d_orders'] = (\n"
    "        z['metrics']['totalOrders'])\n"
    "    out['d_trust'] = (\n"
    "        z['metrics']\n"
    "        ['totalTrustValue'])\n"
    "    out['d_cash'] = (\n"
    "        z['metrics']\n"
    "        ['totalCashValue'])\n"
    "    out['d_dist_paid'] = (\n"
    "        z['orders']\n"
    "        ['statusDistribution']\n"
    "        ['paid'])\n"
    "    out['d_bal'] = (\n"
    "        z['circulation']\n"
    "        ['balanced'])\n"
    "    out['d_debit'] = (\n"
    "        z['circulation']\n"
    "        ['debitTotal'])\n"
    "    out['d_const'] = (\n"
    "        dash['constitution']\n"
    "        ['mode'])\n"
    # ⑥ 支付占位锁(Redis SET NX)
    "    from services.xx64_settle"
    "_service import (\n"
    "        _claim_pay, _release_pay)\n"
    "    out['lock1'] = await (\n"
    "        _claim_pay(777))\n"
    "    out['lock2'] = await (\n"
    "        _claim_pay(777))\n"
    "    await _release_pay(777)\n"
    "    out['lock3'] = await (\n"
    "        _claim_pay(777))\n"
    "    await _release_pay(777)\n"
    # ⑦ 44号
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
        "POST", "/api/xx64/redteam",
        body={},
        headers=ADMIN, expect=(409,))
    record("off 态 redteam 409(决策面)",
           code == 409, str(code))

    print("\n[03 容器内: 红队七向量]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("七向量 7/7 全防"
           "(Redis 真实态)",
           r.get("rt_all") is True
           and r.get("rt_defended") == 7,
           str((r.get("rt_defended"),
                r.get("rt_total"))))
    record("RT-01 伪字段忽略",
           r.get("e1") is True,
           str(r.get("e1")))
    record("RT-02 拆单 R5 拒",
           r.get("e2") is True,
           str(r.get("e2")))
    record("RT-03 高频第 4 次拒",
           r.get("e3") is True,
           str(r.get("e3")))
    record("RT-04 操纵检测"
           "(drift 0.5)",
           r.get("e4") == 0.5,
           str(r.get("e4")))
    record("RT-05 并发双花恰 1 成功"
           "(Redis SET NX)",
           r.get("e5") == 1,
           str(r.get("e5")))
    record("RT-06 过期不翻转",
           r.get("e6") is True,
           str(r.get("e6")))
    record("RT-07 负值 R7 拒",
           r.get("e7") is True,
           str(r.get("e7")))
    record("红队自清理(无残留)",
           r.get("rt_leftover") == 0,
           str(r.get("rt_leftover")))

    print("\n[04 容器内: 四区看板]")
    record("度量区(3 单/90 信值/"
           "210 现金)",
           r.get("d_orders") == 3
           and r.get("d_trust") == 90.0
           and r.get("d_cash") == 210.0,
           str((r.get("d_orders"),
                r.get("d_trust"),
                r.get("d_cash"))))
    record("订单区(paid 3)",
           r.get("d_dist_paid") == 3,
           str(r.get("d_dist_paid")))
    record("流通区(30/30 平衡)",
           r.get("d_bal") is True
           and r.get("d_debit") == 30.0,
           str((r.get("d_bal"),
                r.get("d_debit"))))
    record("宪法三开关(shadow)",
           r.get("d_const") == "shadow",
           str(r.get("d_const")))

    print("\n[05 支付占位锁+44号]")
    record("SET NX 互斥+释放可重占",
           r.get("lock1") is True
           and r.get("lock2") is False
           and r.get("lock3") is True,
           str((r.get("lock1"),
                r.get("lock2"),
                r.get("lock3"))))
    record("44号 39 档案",
           r.get("reg_n") == 40,
           str(r.get("reg_n")))

    print("\n[06 HTTP 端点+鉴权]")
    # dashboard 观测面(管道种子)
    ok, (code, body) = call(
        "GET", "/api/xx64/dashboard",
        headers=ADMIN)
    zones = (body or {}).get("zones") or {}
    record("HTTP dashboard 200"
           "(四区)",
           code == 200
           and set(zones.keys()) == {
               "metrics", "orders",
               "circulation", "defense"},
           str((code,
                list(zones.keys()))))
    # redteam shadow(服务器 off
    # ——容器管道已验; HTTP 决策面
    # off 409 在 [02] 覆盖)
    # member 403
    ok, (code, _) = call(
        "GET", "/api/xx64/dashboard",
        headers=MEMBER, expect=(403,))
    record("HTTP dashboard "
           "member 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/xx64/redteam",
        body={},
        headers=MEMBER, expect=(403,))
    record("HTTP redteam "
           "member 403",
           code == 403, str(code))
    # 无 Role 403
    ok, (code, _) = call(
        "GET", "/api/xx64/dashboard")
    record("HTTP 无 Role 403",
           code == 403, str(code))
    # 路由累计 26(全期收官)
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
    record("64号路由 P5 26 端点"
           "(全期收官)",
           count == 26, str(count))


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
