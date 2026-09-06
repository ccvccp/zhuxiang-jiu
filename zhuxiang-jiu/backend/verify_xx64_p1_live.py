"""64号信值兑换管理 P1 Docker 实机验收

运行方式:
    python verify_xx64_p1_live.py [基址]

前置: 容器已运行(含 64号 P1 代码)。

覆盖(64号计划 §八 P1, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(pay/exchange 409;
       refund 不受影响)
    03 容器内: 支付结算全链(原子
       转移借贷对+退款反向转移+
       积分兑换管道+T+1 入账+
       观察期取消)
    04 HTTP 端点+鉴权

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
    # 45号测试档案种子域
    redis_del_keys(
        "zhuxiang:trust45:trust45_profiles:"
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
    "    await repo45.save_profile({\n"
    "        'trustId': 9901,\n"
    "        'role': 'person',\n"
    "        'name': 'live-p1',\n"
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
    "    from repositories.points"
    "_repository import (\n"
    "        PointsRepository)\n"
    "    rp = PointsRepository()\n"
    "    acct = await rp.get_or_create"
    "_account(9901)\n"
    "    acct['totalPoints'] = 1000\n"
    "    await rp.save_account(acct)\n"
    # ② 创建+支付(原子转移)
    "    from services.xx64_service "
    "import Xx64Service\n"
    "    from services.xx64_settle"
    "_service import (\n"
    "        Xx64SettleService)\n"
    "    base = Xx64Service()\n"
    "    settle = Xx64SettleService()\n"
    "    r = await base.create_order(\n"
    "        9901, 9902, 9901, 100,\n"
    "        product='live-good')\n"
    "    out['od'] = (\n"
    "        r.get('status'))\n"
    "    pay = await settle.pay_order(\n"
    "        r['orderId'])\n"
    "    out['pay_st'] = (\n"
    "        pay.get('status'))\n"
    "    out['pay_eid'] = (\n"
    "        pay.get('entryId'))\n"
    "    out['pay_tv'] = (\n"
    "        (pay.get('transfer')\n"
    "         or {}).get(\n"
    "             'sellerCredit'))\n"
    # ③ 借贷对读回
    "    from repositories.xx64"
    "_repository import (\n"
    "        Xx64Repository)\n"
    "    repo = Xx64Repository()\n"
    "    lg = await repo.list_ledger(\n"
    "        order_id=1)\n"
    "    out['lg_n'] = len(lg)\n"
    "    out['lg_dirs'] = sorted(\n"
    "        e.get('direction')\n"
    "        for e in lg)\n"
    "    out['lg_src'] = sorted(\n"
    "        set(e.get('source')\n"
    "            for e in lg))\n"
    # ④ 重复支付拒
    "    try:\n"
    "        await settle.pay_order(1)\n"
    "        out['dup_pay'] = False\n"
    "    except ValueError:\n"
    "        out['dup_pay'] = True\n"
    # ⑤ off 态退款不受影响
    "    os.environ['XX64_MODE'] = 'off'\n"
    "    refund = await settle.refund_order(1, refunded_by='gov')\n"
    "    out['rf_st'] = (\n"
    "        refund.get('status'))\n"
    "    out['rf_bc'] = (\n"
    "        (refund.get('refund')\n"
    "         or {}).get(\n"
    "             'buyerCredit'))\n"
    # ⑥ 账本平衡
    "    lv = await settle.ledger_view()\n"
    "    out['bal'] = ((\n"
    "        lv.get('totals') or {})\n"
    "        .get('balanced'))\n"
    "    out['credit'] = ((\n"
    "        lv.get('totals') or {})\n"
    "        .get('credit'))\n"
    # ⑦ 积分兑换(off 拒+正常)
    "    from services.xx64_points"
    "_service import (\n"
    "        Xx64PointsService)\n"
    "    pts = Xx64PointsService()\n"
    "    os.environ['XX64_MODE'] = 'off'\n"
    "    try:\n"
    "        await pts.exchange(\n"
    "            9901, 9901, 100)\n"
    "        out['ex_off'] = False\n"
    "    except ValueError:\n"
    "        out['ex_off'] = True\n"
    "    os.environ['XX64_MODE'] = "
    "'shadow'\n"
    "    try:\n"
    "        await pts.exchange(\n"
    "            9901, 9901, 150)\n"
    "        out['ex_mod'] = False\n"
    "    except ValueError:\n"
    "        out['ex_mod'] = True\n"
    "    ex = await pts.exchange(\n"
    "        9901, 9901, 500)\n"
    "    out['ex_st'] = (\n"
    "        ex.get('status'))\n"
    "    out['ex_val'] = (\n"
    "        ex.get('pointsValue'))\n"
    # ⑧ 积分扣减读回
    "    acct2 = await rp.get_account(\n"
    "        9901)\n"
    "    out['pts_left'] = (\n"
    "        acct2.get(\n"
    "            'totalPoints'))\n"
    # ⑨ 观察期取消(第 2 次——
    # 200 积分, 频次内)
    "    acct3 = await rp.get_account(\n"
    "        9901)\n"
    "    acct3['totalPoints'] = 700\n"
    "    await rp.save_account(acct3)\n"
    "    ex2 = await pts.exchange(\n"
    "        9901, 9901, 200)\n"
    "    cv = await pts.cancel_exchange(\n"
    "        ex2['exchangeId'])\n"
    "    out['cancel_st'] = (\n"
    "        cv.get('status'))\n"
    "    out['cancel_pts'] = (\n"
    "        cv.get(\n"
    "            'refundedPoints'))\n"
    "    acct4 = await rp.get_account(\n"
    "        9901)\n"
    "    out['pts_restored'] = (\n"
    "        acct4.get(\n"
    "            'totalPoints'))\n"
    # ⑩ 日限频(cancelled 不占频次
    # + 有效第 3 次满后第 4 次拒)
    "    await pts.exchange(\n"
    "        9901, 9901, 100)\n"
    "    await pts.exchange(\n"
    "        9901, 9901, 100)\n"
    "    try:\n"
    "        await pts.exchange(\n"
    "            9901, 9901, 100)\n"
    "        out['ex_lim'] = False\n"
    "    except ValueError:\n"
    "        out['ex_lim'] = True\n"
    # ⑪ T+1 入账(手动到期——
    # 500+100+100 三条 pending)
    "    from datetime import datetime, "
    "UTC, timedelta\n"
    "    expired = (datetime.now(UTC)\n"
    "               - timedelta(\n"
    "                   hours=1)\n"
    "               ).isoformat()\n"
    "    ex_list = await repo"
    ".list_exchanges(limit=10)\n"
    "    for e in ex_list:\n"
    "        if e.get('status') \\\n"
    "                == 'pending':\n"
    "            e['releaseAt'] = expired\n"
    "            await repo.save_exchange(\n"
    "                e, create=False)\n"
    "    s = await pts.settle_pending()\n"
    "    out['settled'] = (\n"
    "        s.get('settled'))\n"
    # ⑫ 预览
    "    pv = await pts.preview(\n"
    "        9901, needed_trust=2.0)\n"
    "    out['pv_rate'] = (\n"
    "        pv.get('rate'))\n"
    "    out['pv_credited'] = (\n"
    "        pv.get('creditedValue'))\n"
    # ⑬ 44号
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

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/xx64/orders/1/pay",
        body={},
        headers=MEMBER, expect=(409,))
    record("off 态 pay 409(服务器态)",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/xx64/ledger",
        headers=ADMIN)
    record("off 态 ledger 观测面 200",
           code == 200
           and "totals" in (body or {}),
           str(code))

    print("\n[03 容器内: 支付结算全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("订单创建(reserved)",
           r.get("od") == "reserved",
           str(r.get("od")))
    record("支付成功(paid)",
           r.get("pay_st") == "paid"
           and (r.get("pay_eid") or 0)
           > 0,
           str((r.get("pay_st"),
                r.get("pay_eid"))))
    record("转移对(卖方收入 30)",
           r.get("pay_tv") == 30.0,
           str(r.get("pay_tv")))
    record("借贷对(两笔)",
           r.get("lg_n") == 2,
           str(r.get("lg_n")))
    record("方向对(debit+credit)",
           r.get("lg_dirs")
           == ["credit", "debit"],
           str(r.get("lg_dirs")))
    record("来源标记"
           "(consumption_transfer)",
           r.get("lg_src")
           == ["consumption_transfer"],
           str(r.get("lg_src")))
    record("重复支付拒绝",
           r.get("dup_pay") is True,
           str(r.get("dup_pay")))
    record("off 态退款不受影响"
           "(refunded)",
           r.get("rf_st") == "refunded"
           and r.get("rf_bc") == 30.0,
           str((r.get("rf_st"),
                r.get("rf_bc"))))
    record("账本借贷平衡"
           "(credit 60)",
           r.get("bal") is True
           and r.get("credit") == 60.0,
           str((r.get("bal"),
                r.get("credit"))))
    record("off 态兑换拒绝",
           r.get("ex_off") is True,
           str(r.get("ex_off")))
    record("非 100 倍数拒绝",
           r.get("ex_mod") is True,
           str(r.get("ex_mod")))
    record("兑换成功(pending 5.0)",
           r.get("ex_st") == "pending"
           and r.get("ex_val") == 5.0,
           str((r.get("ex_st"),
                r.get("ex_val"))))
    record("积分扣减(1000→500)",
           r.get("pts_left") == 500,
           str(r.get("pts_left")))
    record("日限频拒绝(第 4 次)",
           r.get("ex_lim") is True,
           str(r.get("ex_lim")))
    record("T+1 入账(3 条)",
           r.get("settled") == 3,
           str(r.get("settled")))
    record("观察期取消"
           "(积分返还 200)",
           r.get("cancel_st")
           == "cancelled"
           and r.get("cancel_pts") == 200
           and r.get("pts_restored")
           == 700,
           str((r.get("cancel_st"),
                r.get("pts_restored"))))
    record("换算预览(rate+7.0 已入账)",
           r.get("pv_rate")
           == "1 信值 = 100 积分"
           and r.get("pv_credited")
           == 7.0,
           str((r.get("pv_rate"),
                r.get(
                    "pv_credited"))))
    record("44号 39 档案",
           r.get("reg_n") == 39,
           str(r.get("reg_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # off 态(服务器态)——支付 409
    ok, (code, _) = call(
        "POST", "/api/xx64/orders/1/pay",
        body={},
        headers=MEMBER, expect=(409,))
    record("HTTP pay off 409",
           code == 409, str(code))
    # 观测面读回(容器内种子的账本)
    ok, (code, body) = call(
        "GET", "/api/xx64/ledger",
        headers=ADMIN)
    record("HTTP ledger 读回"
           "(4 笔借贷平衡)",
           code == 200
           and (body.get("total")
                or 0) >= 4
           and (body.get("totals")
                or {}).get("balanced")
           is True,
           str((code,
                body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/xx64/points/preview"
               "?trust_id=9901",
        headers=MEMBER)
    record("HTTP preview 200",
           code == 200
           and (body.get(
               "creditedValue")
                or 0) == 7.0,
           str((code,
                body.get(
                    "creditedValue"))))
    # 鉴权 403
    for method, path in (
            ("POST",
             "/api/xx64/orders/1/pay"),
            ("POST",
             "/api/xx64/orders/1/"
             "refund"),
            ("POST",
             "/api/xx64/points/"
             "exchange"),
            ("GET",
             "/api/xx64/ledger")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP "
               f"{path.split('/')[-1]}"
               f" 无 Role 403",
               c == 403, str(c))
    # member 退款 403(仅 admin)
    resp_ok, (c, _) = call(
        "POST", "/api/xx64/orders/1/refund",
        body={},
        headers=MEMBER, expect=(403,))
    record("HTTP refund member 403",
           c == 403, str(c))
    # 路由累计 12
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
    record("64号路由 P1 12 端点",
           count == 12, str(count))


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
