"""60号AI智能支付管理 P4 Docker 实机验收

运行方式:
    python verify_pay60_p4_live.py [基址]
    (回流/预测/调度均为不受开关影响面;
     容器任意态均可验证——默认 off)

覆盖(60号计划 §七 P4, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-04 容器内: 反哺全链
       (六类信号池双写+幂等+
        现金流预测+缺口预警+
        调度器三合一)
    05 HTTP 面(collect/forecast)

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
    redis_del_keys("zhuxiang:ai_learning:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.pay60_learn_service "
    "import (\n"
    "        Pay60LearnService)\n"
    "    from services.pay60_scheduler "
    "import (\n"
    "        scheduler_enabled,\n"
    "        run_scheduled_tasks)\n"
    "    from repositories.pay60_repository "
    "import (\n"
    "        Pay60Repository)\n"
    "    from core.helpers import ts\n"
    "    svc = Pay60LearnService()\n"
    "    repo = Pay60Repository()\n"
    # 种六类订单
    "    async def seed(status, mid,\n"
    "                  amount,\n"
    "                  intent_id=0,\n"
    "                  risk='light'):\n"
    "        pid = await repo.next_pay_id()\n"
    "        await repo.save_order({\n"
    "            'payId': pid,\n"
    "            'memberId': mid,\n"
    "            'scene': 'purchase',\n"
    "            'role': 'member',\n"
    "            'status': status,\n"
    "            'basePrice': amount,\n"
    "            'finalPrice': amount,\n"
    "            'attribution': {\n"
    "                'payId': pid,\n"
    "                'intentId': intent_id,\n"
    "                'tier': 'standard',\n"
    "                'riskTier': risk,\n"
    "                'pricing': {}},\n"
    "            'createdAt': ts(),\n"
    "            'updatedAt': ts()})\n"
    "        return pid\n"
    "    await seed('settled', 10, 100.0)\n"
    "    await seed('success', 11, 100.0,\n"
    "               intent_id=58)\n"
    "    await seed('success', 12, 100.0)\n"
    "    await seed('failed', 13, 100.0)\n"
    "    await seed('refunded', 14, 100.0)\n"
    "    await seed('priced', 15, 100.0,\n"
    "               risk='block')\n"
    # ① 六类池双写(off 亦可用)
    "    r = await svc.collect_feedback()\n"
    "    out['labeled'] = (\n"
    "        r['labeled'])\n"
    "    out['signals'] = (\n"
    "        r['signals'])\n"
    "    out['pool_n'] = (\n"
    "        r['poolSubmitted'])\n"
    # ② 幂等
    "    r2 = await svc.collect_feedback()\n"
    "    out['idem'] = (\n"
    "        r2['labeled'])\n"
    # ③ 现金流预测
    "    f = await svc.forecast()\n"
    "    out['hist_in'] = (\n"
    "        f['history']\n"
    "        ['totalInflow'])\n"
    "    out['hist_out'] = (\n"
    "        f['history']\n"
    "        ['totalOutflow'])\n"
    "    out['net'] = (\n"
    "        f['forecast']['net'])\n"
    "    out['window'] = (\n"
    "        f['window'])\n"
    # ④ 调度器(默认 off+手动轮)
    "    out['sched_off'] = (\n"
    "        scheduler_enabled())\n"
    "    sr = await run_scheduled_tasks()\n"
    "    out['sched_3in1'] = (\n"
    "        'collect' in sr\n"
    "        and 'recon' in sr\n"
    "        and 'forecast' in sr)\n"
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

    print("\n[02-04 容器内: 反哺全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[-600:])
        return
    record("六类信号全标注",
           r.get("labeled") == 6
           and r.get("signals") == {
               "compliance_streak": 1,
               "intent_positive": 1,
               "long_term_compliance": 1,
               "payment_anomaly": 1,
               "refund_dispute": 1,
               "fraud_confirmed": 1},
           str(r.get("signals")))
    record("池双写(6 笔)",
           r.get("pool_n") == 6,
           str(r.get("pool_n")))
    record("回流幂等(二轮跳过)",
           r.get("idem") == 0,
           str(r.get("idem")))
    record("历史统计(300 入/100 出)",
           r.get("hist_in") == 300.0
           and r.get("hist_out")
           == 100.0,
           str((r.get("hist_in"),
                r.get("hist_out"))))
    record("预测 7 日 net",
           r.get("window") == 7
           and isinstance(
               r.get("net"),
               (int, float)),
           str((r.get("window"),
                r.get("net"))))
    record("调度器默认 off",
           r.get("sched_off") is False,
           str(r.get("sched_off")))
    record("手动轮三合一",
           r.get("sched_3in1") is True,
           str(r.get("sched_3in1")))

    print("\n[05 HTTP 面]")
    # 造数据后 HTTP collect(off 可用)
    ok, (code, body) = call(
        "POST", "/api/pay60/feedback/collect",
        body={}, headers=ADMIN)
    record("HTTP collect(off 可用+幂等)",
           code == 200
           and (body.get("labeled")
                or 0) == 0,
           str((code,
                body.get("labeled"))))
    ok, (code, body) = call(
        "GET", "/api/pay60/forecast",
        headers=ADMIN)
    record("HTTP forecast 观测面",
           code == 200
           and "forecast" in body
           and body.get("window") == 7,
           str((code,
                body.get("window"))))
    ok, (code, _) = call(
        "POST",
        "/api/pay60/feedback/collect",
        body={})
    record("HTTP collect 无 Role 403",
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
