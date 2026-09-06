"""64号信值兑换管理 P3 Docker 实机验收

运行方式:
    python verify_xx64_p3_live.py [基址]

前置: 容器已运行(含 64号 P3 代码)。

覆盖(64号 P3 设计 §九, 真实容器
Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(pay 409)+
       scan 决策面 off 409
    03 容器内: 五防检测全链
       (ARB-HF 高频命中/ARB-MA
       多账号 high 阻断 assist+
       shadow 不阻断/PTS-SHOCK
       量级拦截/PRICE-MANIP 涨幅
       命中+建议书不自动/
       LIQ-CRUNCH 触线仅建议+
       分级处置三档+tier 摩擦)
    04 HTTP 端点+鉴权(scan off 409
       服务器态/status 画像/
       member 403/无 Role 403)

×2 轮幂等验证(每轮清理种子重造
——xx64+points+trust45 种子键域)。
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
        "9901")
    redis_del_keys(
        "zhuxiang:trust45:trust45_profiles:"
        "9902")
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
    # ① 种子(9901 余额 500)
    "    from repositories.trust_value"
    "_repository import (\n"
    "        TrustValue45Repository)\n"
    "    repo45 = TrustValue45Repository()\n"
    "    await repo45.save_profile({\n"
    "        'trustId': 9901,\n"
    "        'role': 'person',\n"
    "        'name': 'live-p3',\n"
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
    "        'trustId': 9902,\n"
    "        'role': 'person',\n"
    "        'name': 'live-p3-seller',\n"
    "        'idDigest': 'digest-99902',\n"
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
    "        'name': 'live-p3-low',\n"
    "        'idDigest': 'digest-99903',\n"
    "        'factors': {},\n"
    "        'score': 250.0,\n"
    "        'rawScore': 250.0,\n"
    "        'grade': 'B',\n"
    "        'fused': False,\n"
    "        'frozen': False,\n"
    "        'createdAt':\n"
    "            '2026-01-01T00:00:00',\n"
    "        'updatedAt':\n"
    "            '2026-01-01T00:00:00'})\n"
    # ② ARB-HF: 12 笔买家 9901
    "    from repositories.xx64"
    "_repository import (\n"
    "        Xx64Repository)\n"
    "    from core.helpers import ts\n"
    "    repo = Xx64Repository()\n"
    "    for i in range(12):\n"
    "        oid = await repo.next_order"
    "_id()\n"
    "        await repo.save_order({\n"
    "            'orderId': oid,\n"
    "            'buyerId': 9901,\n"
    "            'sellerId': 9902,\n"
    "            'trustId': 9901,\n"
    "            'product': 'goodH',\n"
    "            'price': 100.0,\n"
    "            'trustValue': 30.0,\n"
    "            'cashValue': 70.0,\n"
    "            'balanceSnapshot': 500.0,\n"
    "            'status': 'paid',\n"
    "            'paidAt': ts(),\n"
    "            'createdAt': ts()})\n"
    "    from services.xx64_risk"
    "_service import (\n"
    "        Xx64RiskService)\n"
    "    svc = Xx64RiskService()\n"
    "    f = await svc.detect_arb_hf(\n"
    "        9901, 9901)\n"
    "    out['hf'] = (False if not f else (\n"
    "        f['severity'],\n"
    "        f['detail']['orderCount'],\n"
    "        f['detail']['trustTotal'],\n"
    "        f['detail']['quotaX3']))\n"
    # ③ ARB-MA: goodM 5 账号
    "    buyers = [9901, 9902, 9903,\n"
    "              9904, 9905]\n"
    "    for b in buyers:\n"
    "        oid = await repo.next_order"
    "_id()\n"
    "        await repo.save_order({\n"
    "            'orderId': oid,\n"
    "            'buyerId': b,\n"
    "            'sellerId': 9902,\n"
    "            'trustId': 9901,\n"
    "            'product': 'goodM',\n"
    "            'price': 100.0,\n"
    "            'trustValue': 30.0,\n"
    "            'cashValue': 70.0,\n"
    "            'balanceSnapshot': 500.0,\n"
    "            'status': 'paid',\n"
    "            'paidAt': ts(),\n"
    "            'createdAt': ts()})\n"
    "    f2 = await svc.detect_arb_ma(\n"
    "        product='goodM')\n"
    "    out['ma'] = (False if not f2 else (\n"
    "        f2['severity'],\n"
    "        f2['detail']['accountCount']))\n"
    # ④ sync gate assist 阻断
    "    order = await repo.get_order(13)\n"
    "    os.environ['XX64_MODE'] "
    "= 'assist'\n"
    "    gate = await svc.sync_gate"
    "_pay(dict(order))\n"
    "    out['gate_block'] = (\n"
    "        gate['blocked'])\n"
    "    out['gate_rid'] = (\n"
    "        gate['riskId'])\n"
    # ⑤ shadow 不阻断
    "    os.environ['XX64_MODE'] "
    "= 'shadow'\n"
    "    gate2 = await svc.sync_gate"
    "_pay(dict(order))\n"
    "    out['shadow_block'] = (\n"
    "        gate2['blocked'])\n"
    # ⑥ PTS-SHOCK 量级拦截
    "    for pv in (500.0, 600.0):\n"
    "        eid = await repo.next"
    "_exchange_id()\n"
    "        await repo.save_exchange({\n"
    "            'exchangeId': eid,\n"
    "            'buyerId': 9901,\n"
    "            'trustId': 9901,\n"
    "            'points': int(pv * 100),\n"
    "            'pointsValue': pv,\n"
    "            'exchangeRate': 0.01,\n"
    "            'status': 'pending',\n"
    "            'frozenHours': 24,\n"
    "            'releaseAt': ts(),\n"
    "            'createdAt': ts()})\n"
    "    f3 = await svc.detect_pts"
    "_shock(9901)\n"
    "    out['pts'] = (False if not f3 else (\n"
    "        f3['severity'],\n"
    "        f3['detail']['signal'],\n"
    "        f3['detail']"
    "['trustValue24h']))\n"
    "    os.environ['XX64_MODE'] "
    "= 'assist'\n"
    "    gate3 = await svc.sync_gate"
    "_exchange(9901, 9901)\n"
    "    out['pts_block'] = (\n"
    "        gate3['blocked'])\n"
    "    os.environ['XX64_MODE'] "
    "= 'shadow'\n"
    # ⑦ PRICE-MANIP 涨幅
    "    from datetime import datetime, "
    "UTC, timedelta\n"
    "    now = datetime.now(UTC)\n"
    "    old = (now - timedelta(\n"
    "        days=10)).isoformat()\n"
    "    for i in range(3):\n"
    "        oid = await repo.next_order"
    "_id()\n"
    "        await repo.save_order({\n"
    "            'orderId': oid,\n"
    "            'buyerId': 9901,\n"
    "            'sellerId': 9902,\n"
    "            'trustId': 9901,\n"
    "            'product': 'goodP',\n"
    "            'price': 100.0,\n"
    "            'trustValue': 30.0,\n"
    "            'cashValue': 70.0,\n"
    "            'balanceSnapshot': 500.0,\n"
    "            'status': 'paid',\n"
    "            'paidAt': old,\n"
    "            'createdAt': old})\n"
    "    for i in range(3):\n"
    "        oid = await repo.next_order"
    "_id()\n"
    "        await repo.save_order({\n"
    "            'orderId': oid,\n"
    "            'buyerId': 9901,\n"
    "            'sellerId': 9902,\n"
    "            'trustId': 9901,\n"
    "            'product': 'goodP',\n"
    "            'price': 150.0,\n"
    "            'trustValue': 45.0,\n"
    "            'cashValue': 105.0,\n"
    "            'balanceSnapshot': 500.0,\n"
    "            'status': 'paid',\n"
    "            'paidAt': ts(),\n"
    "            'createdAt': ts()})\n"
    "    fs = await svc.detect_price"
    "_manip()\n"
    "    pm = [x for x in fs if x['entityId']\n"
    "          == 'goodP']\n"
    "    out['pm'] = (False if not pm else (\n"
    "        pm[0]['severity'],\n"
    "        pm[0]['detail']['drift']))\n"
    # ⑧ LIQ-CRUNCH 触线
    # (debit 5×100=500/供给 1250=40%)
    "    for k in range(5):\n"
    "        eid = await repo.next"
    "_entry_id()\n"
    "        await repo.save_ledger({\n"
    "            'entryId': eid,\n"
    "            'orderId': k + 1,\n"
    "            'trustId': 9901,\n"
    "            'direction': 'debit',\n"
    "            'transferType': 'pay',\n"
    "            'amount': -100.0,\n"
    "            'source':\n"
    "                'consumption_'\n"
    "                'transfer',\n"
    "            'createdAt': ts()})\n"
    "    f4 = await svc.detect_liq"
    "_crunch()\n"
    "    out['liq'] = (False if not f4 else (\n"
    "        f4['severity'],\n"
    "        f4['detail']['projected"
    "Ratio']))\n"
    # ⑨ 分级处置+tier
    "    from services.xx64_risk"
    "_service import (\n"
    "        risk_score, disposition)\n"
    "    out['disp'] = (\n"
    "        disposition(39)['action'],\n"
    "        disposition(50)['action'],\n"
    "        disposition(80)['action'])\n"
    "    out['tier'] = (\n"
    "        risk_score([{'severity':\n"
    "                     'medium'}],\n"
    "                    'trusted'),\n"
    "        risk_score([{'severity':\n"
    "                     'medium'}],\n"
    "                    'standard'))\n"
    # ⑩ 扫描+去重+画像
    "    s1 = await svc.scan_all()\n"
    "    s2 = await svc.scan_all()\n"
    "    out['scan'] = (\n"
    "        s1['detectors']['ARB-HF'],\n"
    "        s1['detectors']['ARB-MA'],\n"
    "        s1['matched'])\n"
    "    out['scan2_same'] = (\n"
    "        s2['matched'] == s1['matched'])\n"
    "    st = await svc.user_status(9901)\n"
    "    out['status'] = (\n"
    "        st['tier'] in ('trusted',\n"
    "                       'standard',\n"
    "                       'watched',\n"
    "                       'restricted'),\n"
    "        isinstance(st['riskScore'], int),\n"
    "        st['openEvents'] >= 1)\n"
    # ⑪ 44号
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
        "POST", "/api/xx64/risk/scan",
        body={},
        headers=ADMIN, expect=(409,))
    record("off 态 scan 409(决策面)",
           code == 409, str(code))

    print("\n[03 容器内: 五防检测全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    hf = r.get("hf")
    record("ARB-HF 命中"
           "(medium 12 笔 360>300)",
           list(hf or []) ==
           ["medium", 12, 360.0, 300.0],
           str(hf))
    ma = r.get("ma")
    record("ARB-MA 命中"
           "(high 5 账号)",
           list(ma or []) == ["high", 5],
           str(ma))
    record("assist 阻断+riskId",
           r.get("gate_block") is True
           and (r.get("gate_rid") or 0) > 0,
           str((r.get("gate_block"),
                r.get("gate_rid"))))
    record("shadow 不阻断(仅观察)",
           r.get("shadow_block") is False,
           str(r.get("shadow_block")))
    pts = r.get("pts")
    record("PTS-SHOCK 量级"
           "(high 1100)",
           list(pts or []) ==
           ["high", "magnitude", 1100.0],
           str(pts))
    record("PTS-SHOCK 拦截(assist)",
           r.get("pts_block") is True,
           str(r.get("pts_block")))
    pm = r.get("pm")
    record("PRICE-MANIP 涨幅"
           "(medium 50%)",
           list(pm or []) ==
           ["medium", 0.5],
           str(pm))
    liq = r.get("liq")
    record("LIQ-CRUNCH 触线"
           "(high≥40%)",
           bool(liq) and liq[0] == "high"
           and liq[1] >= 0.4,
           str(liq))
    record("分级处置三档"
           "(pass/verify/freeze)",
           list(r.get("disp") or []) ==
           ["pass", "enhanced_verify",
            "freeze_review"],
           str(r.get("disp")))
    tier = r.get("tier")
    record("tier 摩擦"
           "(trusted 27<standard 36)",
           list(tier or []) == [27, 36],
           str(tier))
    scan = r.get("scan")
    record("扫描命中"
           "(HF+MA≥1)",
           bool(scan) and scan[0] >= 1
           and scan[1] >= 1
           and scan[2] >= 2,
           str(scan))
    record("扫描幂等(同窗去重)",
           r.get("scan2_same") is True,
           str(r.get("scan2_same")))
    status = r.get("status")
    record("画像(tier+分数+事件)",
           bool(status) and status[0]
           is True and status[1] is True
           and status[2] is True,
           str(status))
    record("44号 39 档案",
           r.get("reg_n") == 40,
           str(r.get("reg_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # scan(服务器态 off——决策面 409;
    # shadow 成功路径由容器内管道覆盖)
    ok, (code, body) = call(
        "POST", "/api/xx64/risk/scan",
        body={},
        headers=ADMIN, expect=(409,))
    record("HTTP scan off 409"
           "(服务器态决策面)",
           code == 409,
           str(code))
    # status(观测面)
    ok, (code, body) = call(
        "GET", "/api/xx64/risk/status"
               "?trust_id=9901",
        headers=MEMBER)
    record("HTTP status 200 画像",
           code == 200
           and (body or {}).get("tier")
           in ("trusted", "standard",
               "watched", "restricted")
           and "riskScore" in (body or {}),
           str((code,
                (body or {}).get("tier"))))
    # member scan 403
    ok, (code, _) = call(
        "POST", "/api/xx64/risk/scan",
        body={},
        headers=MEMBER, expect=(403,))
    record("HTTP scan member 403",
           code == 403, str(code))
    # 无 Role 403
    ok, (code, _) = call(
        "GET", "/api/xx64/risk/status"
               "?trust_id=9901")
    record("HTTP 无 Role 403",
           code == 403, str(code))
    # 路由累计 16
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
    record("64号路由 P3 16 端点",
           count == 16, str(count))


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
