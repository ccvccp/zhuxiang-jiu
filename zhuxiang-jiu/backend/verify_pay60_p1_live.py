"""60号AI智能支付管理 P1 Docker 实机验收

运行方式:
    # 容器以 shadow 态启动(HTTP 决策面
    # 正向验证——compose 支持 PAY60_MODE
    # 环境变量注入):
    $env:PAY60_MODE="shadow"
    docker compose -p zhuxiang-jiu up -d backend
    python verify_pay60_p1_live.py [基址]

覆盖(60号计划 §七 P1, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-05 容器内: 智能收银台全链
       (意图联动开单+归因链+
        三因子定价+上下文感知渲染
        老年优先/高信值续费免密+
        失败智能恢复四类建议集+
        recovering→executing)
    06 HTTP 面(开单/渲染/恢复/
       checkouts 观测)

×2 轮幂等验证(每轮清理种子重造——
pay60 键域)。
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


def clear_pay60(round_no: int) -> None:
    redis_del_keys("zhuxiang:pay60:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['PAY60_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.pay60_checkout_service "
    "import (\n"
    "        Pay60CheckoutService)\n"
    "    from services.pay60_service import (\n"
    "        Pay60Service)\n"
    "    from repositories.pay60_repository "
    "import (\n"
    "        Pay60Repository)\n"
    "    from core.helpers import ts\n"
    "    svc = Pay60CheckoutService()\n"
    "    repo = Pay60Repository()\n"
    # ① 意图联动开单
    "    r1 = await svc.create_order(\n"
    "        10, 'purchase', 'member',\n"
    "        100.0, tier='trusted',\n"
    "        compliance_months=6,\n"
    "        promo_factor=0.9)\n"
    "    out['create_status'] = (\n"
    "        r1['status'])\n"
    "    out['create_price'] = (\n"
    "        r1['finalPrice'])\n"
    "    out['create_floor'] = (\n"
    "        r1['pricing'].get('floored'))\n"
    "    out['attr_tier'] = (\n"
    "        r1['attribution'].get('tier'))\n"
    "    out['pay_id'] = r1['payId']\n"
    # ② 上下文渲染(老年)
    "    r2 = await svc.render_checkout(\n"
    "        10, 'purchase', 'member',\n"
    "        senior=True)\n"
    "    out['senior_first'] = (\n"
    "        r2['methods'][0])\n"
    # ③ 渲染(高信值续费)
    "    r3 = await svc.render_checkout(\n"
    "        10, 'renewal', 'member')\n"
    "    out['renew_defaults'] = (\n"
    "        r3['defaults'])\n"
    # ④ 恢复链(种 failed 态)
    "    pid = await repo.next_pay_id()\n"
    "    await repo.save_order({\n"
    "        'payId': pid,\n"
    "        'memberId': 10,\n"
    "        'scene': 'purchase',\n"
    "        'role': 'member',\n"
    "        'status': 'failed',\n"
    "        'basePrice': 100.0,\n"
    "        'finalPrice': 100.0,\n"
    "        'attribution': {},\n"
    "        'createdAt': ts(),\n"
    "        'updatedAt': ts()})\n"
    "    r4 = await svc.recover(\n"
    "        pid, 'insufficient_balance')\n"
    "    out['rec_status'] = (\n"
    "        r4['status'])\n"
    "    out['rec_sugg'] = [\n"
    "        s['action'] for s in\n"
    "        r4['suggestions']]\n"
    "    out['rec_advisory'] = all(\n"
    "        s['advisory'] for s in\n"
    "        r4['suggestions'])\n"
    # ⑤ recovering→executing
    "    r5 = await Pay60Service(\n"
    "    ).advance(\n"
    "        pid, 'executing',\n"
    "        note='retry')\n"
    "    out['retry_ok'] = (\n"
    "        r5['success'] is True\n"
    "        and r5['to'] == 'executing')\n"
    # ⑥ Redis 读回(订单+归因链)
    "    order = await repo.get_order(\n"
    "        r1['payId'])\n"
    "    out['redis_attr'] = (\n"
    "        (order.get('attribution')\n"
    "         or {}).get('scene'))\n"
    # ⑦ checkout 留痕
    "    recs = await repo.list_checkouts(\n"
    "        member_id=10)\n"
    "    out['checkout_n'] = len(recs)\n"
    # ⑧ off 铁律(服务级)
    "    os.environ['PAY60_MODE'] = 'off'\n"
    "    off_reject = False\n"
    "    try:\n"
    "        await svc.create_order(\n"
    "            99, 'purchase', 'member',\n"
    "            10.0)\n"
    "    except ValueError:\n"
    "        off_reject = True\n"
    "    out['off_reject'] = off_reject\n"
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
    clear_pay60(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02-05 容器内: 智能收银台全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("开单 priced+封顶(70)",
           r.get("create_status")
           == "priced"
           and r.get("create_price") == 70.0
           and r.get("create_floor") is True,
           str((r.get("create_status"),
                r.get("create_price"))))
    record("归因链(tier 快照)",
           r.get("attr_tier") == "trusted"
           and r.get("redis_attr")
           == "purchase",
           str((r.get("attr_tier"),
                r.get("redis_attr"))))
    record("老年优先(child_pay 前置)",
           r.get("senior_first")
           == "child_pay",
           str(r.get("senior_first")))
    record("续费非 trusted(无默认)",
           r.get("renew_defaults") == {},
           str(r.get("renew_defaults")))
    record("恢复建议集(4 有序)",
           r.get("rec_status")
           == "recovering"
           and r.get("rec_sugg") == [
               "split_payment",
               "switch_channel",
               "temporary_credit",
               "retry_later"],
           str(r.get("rec_sugg")))
    record("建议性(advisory)",
           r.get("rec_advisory") is True,
           str(r.get("rec_advisory")))
    record("recovering→executing",
           r.get("retry_ok") is True,
           str(r.get("retry_ok")))
    record("checkout 留痕(2 渲染)",
           r.get("checkout_n") == 2,
           str(r.get("checkout_n")))
    record("off 铁律(开单拒绝)",
           r.get("off_reject") is True,
           str(r.get("off_reject")))

    print("\n[06 HTTP 面]")
    ok, (code, body) = call(
        "POST", "/api/pay60/orders",
        body={"memberId": 90,
              "scene": "purchase",
              "role": "member",
              "basePrice": 100.0,
              "tier": "standard"},
        headers=ADMIN)
    pay_id = body.get("payId")
    record("HTTP 开单 200",
           code == 200
           and body.get("status")
           == "priced"
           and bool(pay_id),
           str((code,
                body.get("status"))))
    ok, (code, body) = call(
        "POST", "/api/pay60/checkout/render",
        body={"memberId": 90,
              "scene": "purchase",
              "role": "member",
              "senior": True},
        headers=ADMIN)
    record("HTTP 渲染 200(老年)",
           code == 200
           and (body.get("methods")
                or [None])[0]
           == "child_pay",
           str((code,
                body.get("methods"))))
    # 造 failed 态走 HTTP 恢复
    import subprocess as sp
    sp.run(
        ["docker", "exec", CONTAINER,
         "python", "-c",
         "import asyncio\n"
         "from repositories.pay60_repository"
         " import Pay60Repository\n"
         "from core.helpers import ts\n"
         "async def m():\n"
         f"    repo = Pay60Repository()\n"
         f"    await repo.save_order({{\n"
         f"        'payId': {pay_id},\n"
         "        'memberId': 90,\n"
         "        'status': 'failed',\n"
         "        'basePrice': 100.0,\n"
         "        'finalPrice': 100.0,\n"
         "        'attribution': {},\n"
         "        'createdAt': ts(),\n"
         "        'updatedAt': ts()},"
         "        create=False)\n"
         "asyncio.run(m())"],
        capture_output=True, text=True)
    ok, (code, body) = call(
        "POST",
        f"/api/pay60/orders/{pay_id}/recover",
        body={"failureReason":
              "channel_timeout"},
        headers=ADMIN)
    record("HTTP 恢复 200",
           code == 200
           and body.get("status")
           == "recovering",
           str((code,
                body.get("status"))))
    ok, (code, body) = call(
        "GET", "/api/pay60/checkouts",
        headers=ADMIN)
    record("HTTP checkouts 观测面",
           code == 200
           and (body.get("total")
                or 0) >= 1,
           str((code,
                body.get("total"))))
    ok, (code, _) = call(
        "POST", "/api/pay60/orders",
        body={"memberId": 1,
              "scene": "purchase",
              "role": "member",
              "basePrice": 10.0})
    record("HTTP 开单无 Role 403",
           code == 403, str(code))


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
