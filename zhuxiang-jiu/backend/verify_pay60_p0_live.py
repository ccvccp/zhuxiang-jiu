"""60号AI智能支付管理 P0 Docker 实机验收

运行方式:
    # 容器以 shadow 态启动(HTTP 决策面
    # 正向验证——compose 支持 PAY60_MODE
    # 环境变量注入):
    $env:PAY60_MODE="shadow"
    docker compose -p zhuxiang-jiu up -d backend
    python verify_pay60_p0_live.py [基址]

覆盖(60号计划 §七 P0, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-05 容器内: 支付底座全链
       (定价三因子+封顶+分账守恒
        +状态机流转/拒绝+归因链指纹
        +渠道三态 mock/real/fallback
        +第35档案评分)
    06 HTTP 面(registry/orders 观测
       +model/status 第35档案)

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
    "    from services.pay60_registry import (\n"
    "        compute_price, compute_split,\n"
    "        assert_transition)\n"
    "    from services.pay60_service import (\n"
    "        Pay60Service)\n"
    "    from repositories.pay60_repository "
    "import (\n"
    "        Pay60Repository)\n"
    "    from core.helpers import ts\n"
    "    svc = Pay60Service()\n"
    "    repo = Pay60Repository()\n"
    # ① 定价三因子
    "    p1 = compute_price(100.0)\n"
    "    out['price_base'] = (\n"
    "        p1['finalPrice'])\n"
    "    p2 = compute_price(\n"
    "        100.0, tier='trusted',\n"
    "        compliance_months=6,\n"
    "        promo_factor=0.5)\n"
    "    out['price_floor'] = (\n"
    "        p2['finalPrice'])\n"
    "    out['price_floored'] = (\n"
    "        p2['floored'])\n"
    # ② 分账守恒
    "    s1 = compute_split(\n"
    "        1000.0,\n"
    "        'v1_alliance_standard')\n"
    "    out['split_ok'] = (\n"
    "        s1['conserved'])\n"
    "    out['split_amounts'] = [\n"
    "        s['amount'] for s in\n"
    "        s1['splits']]\n"
    # ③ 状态机
    "    ok_chain = True\n"
    "    for c, t in ((\n"
    "            'created', 'priced'),\n"
    "            ('priced', 'verified'),\n"
    "            ('verified', 'executing'),\n"
    "            ('executing', 'success'),\n"
    "            ('success', 'settled')):\n"
    "        try:\n"
    "            assert_transition(c, t)\n"
    "        except ValueError:\n"
    "            ok_chain = False\n"
    "    out['chain_ok'] = ok_chain\n"
    "    jump_reject = False\n"
    "    try:\n"
    "        assert_transition(\n"
    "            'created', 'success')\n"
    "    except ValueError:\n"
    "        jump_reject = True\n"
    "    out['jump_reject'] = jump_reject\n"
    # ④ 订单流转+归因链
    "    pay_id = await repo.next_pay_id()\n"
    "    attr = svc.build_attribution(\n"
    "        pay_id, intent_id=58,\n"
    "        session_id=48, tier='trusted',\n"
    "        risk_tier='pass',\n"
    "        pricing={'finalPrice': 95.0})\n"
    "    await repo.save_order({\n"
    "        'payId': pay_id,\n"
    "        'memberId': 300,\n"
    "        'status': 'created',\n"
    "        'attribution': attr,\n"
    "        'createdAt': ts(),\n"
    "        'updatedAt': ts()})\n"
    "    adv = await svc.advance(\n"
    "        pay_id, 'priced')\n"
    "    out['adv_ok'] = (\n"
    "        adv['success'] is True\n"
    "        and adv['to'] == 'priced')\n"
    "    out['adv_fp'] = str(\n"
    "        adv['fingerprint']\n"
    "    ).startswith('sha256:')\n"
    "    order = await repo.get_order(\n"
    "        pay_id)\n"
    "    out['redis_status'] = (\n"
    "        order.get('status'))\n"
    # ⑤ 渠道三态
    "    ch1 = await svc.execute_channel(\n"
    "        pay_id, 100.0, mode='mock')\n"
    "    out['ch_mock'] = (\n"
    "        ch1['receipt']['channel']\n"
    "        == 'mock')\n"
    "    ch2_reject = False\n"
    "    try:\n"
    "        await svc.execute_channel(\n"
    "            pay_id, 100.0,\n"
    "            mode='real')\n"
    "    except ValueError:\n"
    "        ch2_reject = True\n"
    "    out['ch_real_reject'] = (\n"
    "        ch2_reject)\n"
    "    ch3 = await svc.execute_channel(\n"
    "        pay_id, 100.0,\n"
    "        mode='mock_fallback')\n"
    "    out['ch_fallback'] = (\n"
    "        ch3['fallback'] is True)\n"
    "    flows = await repo.list_flows(\n"
    "        pay_id=pay_id)\n"
    "    out['flows_n'] = len(flows)\n"
    # ⑥ 第35档案评分
    "    from services.pay60_scorer "
    "import (\n"
    "        Pay60Scorer)\n"
    "    sc = await Pay60Scorer().score(\n"
    "        {'tier': 'standard'})\n"
    "    out['scorer_id'] = (\n"
    "        sc['scorerId'])\n"
    "    out['scorer_dec'] = (\n"
    "        sc['decision'])\n"
    # ⑦ off 铁律(服务级)
    "    os.environ['PAY60_MODE'] = 'off'\n"
    "    off_reject = False\n"
    "    try:\n"
    "        await svc.advance(\n"
    "            pay_id, 'verified')\n"
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

    print("\n[02-05 容器内: 支付底座全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("定价基准(100)",
           r.get("price_base") == 100.0,
           str(r.get("price_base")))
    record("叠加封顶(0.7 防击穿)",
           r.get("price_floor") == 70.0
           and r.get("price_floored") is True,
           str((r.get("price_floor"),
                r.get("price_floored"))))
    record("分账守恒(8/12/80)",
           r.get("split_ok") is True
           and r.get("split_amounts")
           == [80.0, 120.0, 800.0],
           str(r.get("split_amounts")))
    record("状态机主链(created→settled)",
           r.get("chain_ok") is True,
           str(r.get("chain_ok")))
    record("跳跃流转拒绝",
           r.get("jump_reject") is True,
           str(r.get("jump_reject")))
    record("订单流转(created→priced)",
           r.get("adv_ok") is True
           and r.get("redis_status")
           == "priced",
           str((r.get("adv_ok"),
                r.get("redis_status"))))
    record("流转指纹(sha256)",
           r.get("adv_fp") is True,
           str(r.get("adv_fp")))
    record("渠道 mock 回执",
           r.get("ch_mock") is True,
           str(r.get("ch_mock")))
    record("渠道 real 无凭证 fail-hard",
           r.get("ch_real_reject") is True,
           str(r.get("ch_real_reject")))
    record("渠道 fallback 回退",
           r.get("ch_fallback") is True,
           str(r.get("ch_fallback")))
    record("渠道流水留痕(2 条——"
           "real 拒绝不落痕)",
           r.get("flows_n") == 2,
           str(r.get("flows_n")))
    record("第35档案评分(standard→optimize)",
           r.get("scorer_id")
           == "payment_orchestration"
           and r.get("scorer_dec")
           == "optimize",
           str((r.get("scorer_id"),
                r.get("scorer_dec"))))
    record("off 铁律(流转拒绝)",
           r.get("off_reject") is True,
           str(r.get("off_reject")))

    print("\n[06 HTTP 面]")
    ok, (code, body) = call(
        "GET", "/api/pay60/registry",
        headers=ADMIN)
    record("HTTP registry 观测面",
           code == 200
           and body.get("splitContracts")
           == 3
           and (body.get("meta")
                or {}).get("channelModes")
           == ["mock", "real",
               "mock_fallback"],
           str((code,
                body.get(
                    "splitContracts"))))
    ok, (code, body) = call(
        "GET", "/api/pay60/orders",
        headers=ADMIN)
    record("HTTP orders 观测面",
           code == 200
           and (body.get("total")
                or 0) == 1,
           str((code,
                body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/pay60/model/status",
        headers=ADMIN)
    record("HTTP model/status 第35档案",
           code == 200
           and ((body.get("status")
                 or {}).get("scorerId")
                == "payment_orchestration"),
           str(code))
    ok, (code, _) = call(
        "GET", "/api/pay60/orders/99999",
        headers=ADMIN, expect=(404,))
    record("HTTP order 404",
           code == 404, str(code))
    ok, (code, _) = call(
        "GET", "/api/pay60/registry")
    record("HTTP registry 无 Role 403",
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
