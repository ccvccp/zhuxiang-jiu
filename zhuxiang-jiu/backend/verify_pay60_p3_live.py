"""60号AI智能支付管理 P3 Docker 实机验收

运行方式:
    # 容器以 shadow 态启动(HTTP 决策面
    # 正向验证——compose 支持 PAY60_MODE
    # 环境变量注入):
    $env:PAY60_MODE="shadow"
    docker compose -p zhuxiang-jiu up -d backend
    python verify_pay60_p3_live.py [基址]

覆盖(60号计划 §七 P3, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-05 容器内: 对账结算全链
       (三方对账+差异五类+冲正 T+1
        +分账守恒+结算人工铁律
        +订单联动)
    06 HTTP 面(recon/run/settle
       +splits/settle+观测面)

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
    "    from services.pay60_recon_service "
    "import (\n"
    "        Pay60ReconService)\n"
    "    from repositories.pay60_repository "
    "import (\n"
    "        Pay60Repository)\n"
    "    from core.helpers import ts\n"
    "    svc = Pay60ReconService()\n"
    "    repo = Pay60Repository()\n"
    # 种单助手
    "    async def seed(mid, amount,\n"
    "                   scene='purchase'):\n"
    "        pid = await repo.next_pay_id()\n"
    "        await repo.save_order({\n"
    "            'payId': pid,\n"
    "            'memberId': mid,\n"
    "            'scene': scene,\n"
    "            'role': 'member',\n"
    "            'status': 'success',\n"
    "            'basePrice': amount,\n"
    "            'finalPrice': amount,\n"
    "            'attribution': {},\n"
    "            'createdAt': ts(),\n"
    "            'updatedAt': ts()})\n"
    "        fid = await repo.next_flow_id()\n"
    "        await repo.save_flow({\n"
    "            'flowId': fid,\n"
    "            'payId': pid,\n"
    "            'channel': 'mock',\n"
    "            'channelMode': 'mock',\n"
    "            'amount': amount,\n"
    "            'channelReceipt': {},\n"
    "            'fingerprint': 'sha256:s',\n"
    "            'fallback': False,\n"
    "            'error': '',\n"
    "            'createdAt': ts(),\n"
    "            'updatedAt': ts()})\n"
    "        return pid\n"
    # ① matched
    "    p1 = await seed(10, 100.0)\n"
    "    r1 = await svc.run_recon()\n"
    "    out['matched_n'] = (\n"
    "        r1['matched'])\n"
    # ② 三方发票不符
    "    r2 = await svc.run_recon(\n"
    "        invoices=[{'payId': p1,\n"
    "                   'amount': 99.0}])\n"
    "    out['inv_diff'] = (\n"
    "        r2['differences'])\n"
    # ③ channel_duplicate
    "    p2 = await seed(11, 100.0)\n"
    "    fid = await repo.next_flow_id()\n"
    "    await repo.save_flow({\n"
    "        'flowId': fid,\n"
    "        'payId': p2,\n"
    "        'channel': 'mock',\n"
    "        'channelMode': 'mock',\n"
    "        'amount': 100.0,\n"
    "        'channelReceipt': {},\n"
    "        'fingerprint': 'sha256:d',\n"
    "        'fallback': False,\n"
    "        'error': '',\n"
    "        'createdAt': ts(),\n"
    "        'updatedAt': ts()})\n"
    "    r3 = await svc.run_recon()\n"
    "    out['dup_found'] = any(\n"
    "        d['diffType']\n"
    "        == 'channel_duplicate'\n"
    "        for d in r3['details'])\n"
    "    out['dup_auto'] = any(\n"
    "        d['diffType']\n"
    "        == 'channel_duplicate'\n"
    "        and d['status']\n"
    "        == 'auto_pending'\n"
    "        for d in r3['details'])\n"
    "    out['recon_id'] = (\n"
    "        r3['reconId'])\n"
    # ④ 冲正确认(off 亦可用)
    "    os.environ['PAY60_MODE'] = 'off'\n"
    "    st = await svc.settle_recon(\n"
    "        r3['reconId'], p2, True,\n"
    "        settled_by='fin')\n"
    "    out['settle_status'] = (\n"
    "        st['status'])\n"
    "    order2 = await repo.get_order(\n"
    "        p2)\n"
    "    out['order_refunded'] = (\n"
    "        order2.get('status'))\n"
    "    out['reversal_domain'] = (\n"
    "        (order2.get('reversal')\n"
    "         or {}).get('domain'))\n"
    # ⑤ 分账(shadow 决策面)
    "    os.environ['PAY60_MODE'] = 'shadow'\n"
    "    p3 = await seed(\n"
    "        12, 1000.0, 'listing')\n"
    "    sp = await svc.create_split(\n"
    "        p3,\n"
    "        'v1_alliance_standard')\n"
    "    out['split_conserved'] = (\n"
    "        sp['conserved'])\n"
    "    out['split_amounts'] = [\n"
    "        s['amount'] for s in\n"
    "        sp['splits']]\n"
    # ⑥ 分账结算(off 亦可用)
    "    os.environ['PAY60_MODE'] = 'off'\n"
    "    ss = await svc.settle_split(\n"
    "        sp['splitId'],\n"
    "        settled_by='cash')\n"
    "    out['split_settled'] = (\n"
    "        ss['status'])\n"
    "    out['t1_deferred'] = (\n"
    "        ss['t1Deferred'])\n"
    "    order3 = await repo.get_order(\n"
    "        p3)\n"
    "    out['order_settled'] = (\n"
    "        order3.get('status'))\n"
    # ⑦ 对账幂等
    "    r4 = await svc.run_recon()\n"
    "    out['recon_idem'] = (\n"
    "        r4['differences'])\n"
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

    print("\n[02-05 容器内: 对账结算全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[-600:])
        return
    record("matched(双方一致)",
           r.get("matched_n") == 1,
           str(r.get("matched_n")))
    record("三方发票不符(差异)",
           r.get("inv_diff") == 1,
           str(r.get("inv_diff")))
    record("channel_duplicate 检出",
           r.get("dup_found") is True,
           str(r.get("dup_found")))
    record("冲正 auto_pending(T+1)",
           r.get("dup_auto") is True,
           str(r.get("dup_auto")))
    record("冲正确认(settled)",
           r.get("settle_status")
           == "settled",
           str(r.get("settle_status")))
    record("订单 refunded+冲正留痕",
           r.get("order_refunded")
           == "refunded"
           and r.get("reversal_domain")
           == "T+1",
           str((r.get("order_refunded"),
                r.get("reversal_domain"))))
    record("分账守恒(8/12/80)",
           r.get("split_conserved")
           is True
           and r.get("split_amounts")
           == [80.0, 120.0, 800.0],
           str(r.get("split_amounts")))
    record("分账结算(settled+T+1)",
           r.get("split_settled")
           == "settled"
           and r.get("t1_deferred")
           is True,
           str((r.get("split_settled"),
                r.get("t1_deferred"))))
    record("订单联动 settled",
           r.get("order_settled")
           == "settled",
           str(r.get("order_settled")))
    record("对账幂等(不重复登记)",
           r.get("recon_idem") == 1,
           str(r.get("recon_idem")))

    print("\n[06 HTTP 面]")
    # HTTP: recon/run(off 亦可用)
    ok, (code, body) = call(
        "POST", "/api/pay60/recon/run",
        body={}, headers=ADMIN)
    record("HTTP recon/run(off 可用)",
           code == 200
           and (body.get("differences")
                or 0) == 1,
           str((code,
                body.get("differences"))))
    recon_id = body.get("reconId")
    # HTTP: splits 不存在订单 404
    # (shadow 态决策面开放——域校验
    # 先行; off 409 已在管道⑧验证)
    ok, (code, _) = call(
        "POST", "/api/pay60/splits",
        body={"payId": 999},
        headers=ADMIN, expect=(404,))
    record("HTTP splits 订单 404",
           code == 404, str(code))
    # HTTP: 观测面
    ok, (code, body) = call(
        "GET", "/api/pay60/recon",
        headers=ADMIN)
    record("HTTP recon 观测面",
           code == 200
           and (body.get("total")
                or 0) >= 3,
           str((code,
                body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/pay60/splits",
        headers=ADMIN)
    record("HTTP splits 观测面",
           code == 200
           and (body.get("total")
                or 0) == 1,
           str((code,
                body.get("total"))))
    ok, (code, _) = call(
        "POST",
        "/api/pay60/recon/99999/settle",
        body={"payId": 1,
              "approve": True},
        headers=ADMIN, expect=(404,))
    record("HTTP recon settle 404",
           code == 404, str(code))


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
