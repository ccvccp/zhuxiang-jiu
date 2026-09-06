"""60号AI智能支付管理 P2 Docker 实机验收

运行方式:
    # 容器以 assist 态启动(会员 confirm 面
    # 正向验证——compose 支持 PAY60_MODE
    # 环境变量注入):
    $env:PAY60_MODE="assist"
    docker compose -p zhuxiang-jiu up -d backend
    python verify_pay60_p2_live.py [基址]

覆盖(60号计划 §七 P2, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-05 容器内: 风控全链
       (riskTier 四级评估+行为跳跃升档
        +AML 快进快出+令牌签发核销
        +execute 全链+归因链回执
        +阈值 46号双模+fail-soft)
    06 HTTP 面(verify/confirm/execute
       +thresholds/verifications 观测)

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
    redis_del_keys("zhuxiang:ai_governance:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['PAY60_MODE'] = 'assist'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.pay60_risk_service "
    "import (\n"
    "        Pay60RiskService)\n"
    "    from repositories.pay60_repository "
    "import (\n"
    "        Pay60Repository)\n"
    "    from services.ai_governance_service "
    "import (\n"
    "        AiGovernanceService)\n"
    "    from core.helpers import ts\n"
    "    await AiGovernanceService(\n"
    "    ).sync_registry()\n"
    "    svc = Pay60RiskService()\n"
    "    repo = Pay60Repository()\n"
    # 种单助手
    "    async def seed(mid, amount):\n"
    "        pid = await repo.next_pay_id()\n"
    "        await repo.save_order({\n"
    "            'payId': pid,\n"
    "            'memberId': mid,\n"
    "            'scene': 'purchase',\n"
    "            'role': 'member',\n"
    "            'status': 'priced',\n"
    "            'basePrice': amount,\n"
    "            'finalPrice': amount,\n"
    "            'attribution': {},\n"
    "            'createdAt': ts(),\n"
    "            'updatedAt': ts()})\n"
    "        return pid\n"
    # ① light 档+全链
    "    p1 = await seed(10, 100.0)\n"
    "    v1 = await svc.verify(\n"
    "        p1, behavior_sequence=[\n"
    "            'browse', 'pay'])\n"
    "    out['light_tier'] = (\n"
    "        v1['riskTier'])\n"
    "    out['light_method'] = (\n"
    "        v1['verifyMethod'])\n"
    "    out['token_prefix'] = str(\n"
    "        v1['verifyToken']\n"
    "    ).startswith('VT')\n"
    "    c1 = await svc.confirm(\n"
    "        p1, v1['verifyToken'])\n"
    "    out['confirm_status'] = (\n"
    "        c1['status'])\n"
    "    e1 = await svc.execute(p1)\n"
    "    out['exec_status'] = (\n"
    "        e1['status'])\n"
    "    order1 = await repo.get_order(\n"
    "        p1)\n"
    "    out['attr_receipt'] = (\n"
    "        (order1.get('attribution')\n"
    "         or {}).get(\n"
    "            'channelReceipt', {})\n"
    "        .get('channel'))\n"
    # ② strong 档(大额)
    "    p2 = await seed(11, 6000.0)\n"
    "    v2 = await svc.verify(p2)\n"
    "    out['strong_tier'] = (\n"
    "        v2['riskTier'])\n"
    "    out['strong_method'] = (\n"
    "        v2['verifyMethod'])\n"
    # ③ block 档(合规禁令)
    "    p3 = await seed(12, 100.0)\n"
    "    v3 = await svc.verify(\n"
    "        p3, compliance_flags=[\n"
    "            'sanction_list'])\n"
    "    out['block_tier'] = (\n"
    "        v3['riskTier'])\n"
    "    out['block_stay'] = (\n"
    "        (await repo.get_order(\n"
    "            p3)).get('status'))\n"
    # ④ AML 快进快出(15 万无设备)
    "    p4 = await seed(13, 150000.0)\n"
    "    v4 = await svc.verify(p4)\n"
    "    out['aml_tier'] = (\n"
    "        v4['riskTier'])\n"
    "    out['aml_hits'] = (\n"
    "        v4.get('amlHits'))\n"
    # ⑤ 跳跃升档(trusted 语义
    #    通过 tier 参数——直接构造)
    "    p5 = await seed(14, 100.0)\n"
    "    v5 = await svc.verify(\n"
    "        p5, device_trusted=True,\n"
    "        behavior_sequence=['pay'])\n"
    "    out['jump_tier'] = (\n"
    "        v5['riskTier'])\n"
    "    out['jump_by'] = (\n"
    "        v5.get('escalatedBy'))\n"
    # ⑥ 阈值 46号双模
    "    cal = await svc.calibrate_submit(\n"
    "        8000, 3000,\n"
    "        requested_by='rt')\n"
    "    out['cal_status'] = (\n"
    "        cal.get('status'))\n"
    "    early = False\n"
    "    try:\n"
    "        await svc.calibrate_apply(\n"
    "            cal['changeId'])\n"
    "    except ValueError:\n"
    "        early = True\n"
    "    out['cal_early_reject'] = early\n"
    "    try:\n"
    "        await AiGovernanceService(\n"
    "        ).review_change(\n"
    "            int(cal['changeId']),\n"
    "            approve=True,\n"
    "            reviewed_by='gov')\n"
    "    except ValueError:\n"
    "        pass\n"
    "    app = await svc.calibrate_apply(\n"
    "        cal['changeId'])\n"
    "    out['cal_applied'] = (\n"
    "        app.get('config'))\n"
    # ⑦ fail-soft(未建档会员)
    "    p6 = await seed(99999, 100.0)\n"
    "    v6 = await svc.verify(p6)\n"
    "    out['failsoft_tier'] = (\n"
    "        v6['riskTier'])\n"
    # ⑧ off 铁律(服务级)
    "    os.environ['PAY60_MODE'] = 'off'\n"
    "    off_reject = False\n"
    "    try:\n"
    "        await svc.verify(p1)\n"
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

    print("\n[02-05 容器内: 风控全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("light 档(OTP 令牌 VT)",
           r.get("light_tier") == "light"
           and r.get("light_method")
           == "otp_mock"
           and r.get("token_prefix")
           is True,
           str((r.get("light_tier"),
                r.get("light_method"))))
    record("令牌核销(confirmed)",
           r.get("confirm_status")
           == "confirmed",
           str(r.get("confirm_status")))
    record("执行成功(mock 回执)",
           r.get("exec_status") == "success"
           and r.get("attr_receipt")
           == "mock",
           str((r.get("exec_status"),
                r.get("attr_receipt"))))
    record("strong 档(屏幕码)",
           r.get("strong_tier") == "strong"
           and r.get("strong_method")
           == "screen_code",
           str((r.get("strong_tier"),
                r.get("strong_method"))))
    record("block 档(留 priced)",
           r.get("block_tier") == "block"
           and r.get("block_stay")
           == "priced",
           str((r.get("block_tier"),
                r.get("block_stay"))))
    record("AML 快进快出→block",
           r.get("aml_tier") == "block"
           and "fast_in_fast_out"
           in (r.get("aml_hits")
               or []),
           str(r.get("aml_hits")))
    record("跳跃升档(behavior_jump)",
           r.get("jump_tier") == "strong"
           and r.get("jump_by")
           == "behavior_jump",
           str((r.get("jump_tier"),
                r.get("jump_by"))))
    record("阈值提交 46号(pending)",
           r.get("cal_status") == "pending",
           str(r.get("cal_status")))
    record("未经裁决不可生效",
           r.get("cal_early_reject") is True,
           str(r.get("cal_early_reject")))
    record("裁决后生效(apply)",
           r.get("cal_applied") == {
               "passMaxAmount": 8000.0,
               "lightMaxAmount": 3000.0},
           str(r.get("cal_applied")))
    record("fail-soft(未建档→light)",
           r.get("failsoft_tier")
           == "light",
           str(r.get("failsoft_tier")))
    record("off 铁律(验证拒绝)",
           r.get("off_reject") is True,
           str(r.get("off_reject")))

    print("\n[06 HTTP 面]")
    # 开单+验证(light)
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
           code == 200 and bool(pay_id),
           str(code))
    ok, (code, body) = call(
        "POST",
        f"/api/pay60/orders/{pay_id}/verify",
        body={"behaviorSequence": [
            "browse", "pay"]},
        headers=ADMIN)
    record("HTTP verify 200(light)",
           code == 200
           and body.get("riskTier")
           == "light",
           str((code,
                body.get("riskTier"))))
    # assist 全链 confirm+execute
    ok, (code, body2) = call(
        "POST",
        f"/api/pay60/orders/{pay_id}/confirm",
        body={"verifyToken":
              body.get("verifyToken")},
        headers=ADMIN)
    record("HTTP confirm 200(assist)",
           code == 200
           and body2.get("status")
           == "confirmed",
           str(code))
    ok, (code, body3) = call(
        "POST",
        f"/api/pay60/orders/{pay_id}/execute",
        body={}, headers=ADMIN)
    record("HTTP execute 200",
           code == 200
           and body3.get("status")
           == "success",
           str((code,
                body3.get("status"))))
    ok, (code, body) = call(
        "GET", "/api/pay60/thresholds",
        headers=ADMIN)
    record("HTTP thresholds 观测面",
           code == 200
           and (body.get("active")
                or {}).get(
                    "passMaxAmount")
           == 8000.0,
           str(code))
    ok, (code, body) = call(
        "GET", "/api/pay60/verifications",
        headers=ADMIN)
    record("HTTP verifications 观测面",
           code == 200
           and (body.get("total")
                or 0) >= 2,
           str((code,
                body.get("total"))))
    ok, (code, _) = call(
        "POST",
        "/api/pay60/orders/99999/verify",
        body={}, headers=ADMIN,
        expect=(404,))
    record("HTTP verify 404",
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
