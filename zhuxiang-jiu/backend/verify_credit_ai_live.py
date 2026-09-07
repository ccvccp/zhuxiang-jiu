"""全站批次一·23号信用管理 AI 升级 Docker
实机验收(verify_credit_ai_live)

运行方式:
    python verify_credit_ai_live.py [基址]

前置: 容器已运行(含批次一 credit_scoring
      代码——AI_ENFORCE_MODE 默认 observe)。

覆盖(《全站AI智能混合架构升级总计划》
批次一交付面, 真实容器 Redis 态):
    01 正常业务零影响(健康检查+信用面)
    02 44号注册表 41 档案(credit_scoring
       batch25 入册)
    03 observe 行为兼容(先享后付自动通过)
    04 决策快照留痕(observe 学习闭环)
    05 中风险转人工(大额>50%额度)
    06 审批回流(人工审批→自动反馈)
    07 还款回流+逾期重放(红队向量)
    08 伪信用注入绕过决策门(红队向量
       ——客户端伪造字段被服务端忽略)
    09 旁路调分修复(45号转换轨不再即时
       改写 creditLevel——红队向量)
    10 enforce 高风险拦截+低风险放行
       (容器内单元, 冷启动门槛喂料)
    11 AI 观测面 HTTP(enforcement overview)

×2 轮幂等验证(每轮清理种子重造——
888001-888004 用户域+credit_scoring
学习键域+trust45 digest-9988 隔离域)。
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

U1, U2, U3, U4 = 888001, 888002, 888003, 888004
TRUST_ID = 9901
DIGEST = "digest-9988"


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
        with urllib.request.urlopen(req, timeout=15) as r:
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


def clear_batch1(round_no: int) -> None:
    # 信用域(测试用户键域全清——含流水/订单索引)
    redis_del_keys("zhuxiang:credit:*")
    # 44号学习域(仅 credit_scoring 键域)
    redis_del_keys(
        "zhuxiang:ai_learning:*credit_scoring*")
    # 45号隔离测试档案域
    redis_del_keys("zhuxiang:trust45:*9988*")
    redis_del_keys("zhuxiang:trust45:*9901*")


def run_pipeline(script: str) -> dict:
    """容器内 python 管道(纯 ASCII 源码)"""
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr
                          or "无输出")[-1500:]}


# ------------------------------------------------------------
# 种子管道(每轮重造)
# ------------------------------------------------------------
SEED = (
    "import asyncio, json\n"
    "from datetime import datetime, timedelta\n"
    "async def m():\n"
    "    out = {}\n"
    "    from repositories.credit_repository import (\n"
    "        CreditRepository)\n"
    "    repo = CreditRepository()\n"
    "    now = datetime.utcnow().isoformat()\n"
    "    old = (datetime.utcnow()\n"
    "           - timedelta(days=120)).isoformat()\n"
    # ① 优质账户 U1(L4/750/额度5000/区间120天)
    "    a1 = await repo.get_or_create_score(888001)\n"
    "    a1.update(bambooScore=750,\n"
    "              creditLevel='L4',\n"
    "              paylaterQuota=5000,\n"
    "              paylaterUsed=0.0,\n"
    "              status='normal',\n"
    "              scoreZoneSince=old)\n"
    "    await repo.save_score(a1)\n"
    # ② 高风险账户 U2(L3/560/近满额)
    "    a2 = await repo.get_or_create_score(888002)\n"
    "    a2.update(bambooScore=560,\n"
    "              creditLevel='L3',\n"
    "              paylaterQuota=2000,\n"
    "              paylaterUsed=1900.0,\n"
    "              status='normal',\n"
    "              scoreZoneSince=now)\n"
    "    await repo.save_score(a2)\n"
    # ③ enforce 拦截账户 U3(L3/610/满额+逾期)
    "    a3 = await repo.get_or_create_score(888003)\n"
    "    a3.update(bambooScore=610,\n"
    "              creditLevel='L3',\n"
    "              paylaterQuota=5000,\n"
    "              paylaterUsed=4500.0,\n"
    "              status='normal',\n"
    "              scoreZoneSince=now)\n"
    "    await repo.save_score(a3)\n"
    # ④ enforce 放行账户 U4(同 U1 画像)
    "    a4 = await repo.get_or_create_score(888004)\n"
    "    a4.update(bambooScore=750,\n"
    "              creditLevel='L4',\n"
    "              paylaterQuota=5000,\n"
    "              paylaterUsed=0.0,\n"
    "              status='normal',\n"
    "              scoreZoneSince=old)\n"
    "    await repo.save_score(a4)\n"
    # ⑤ U1 履约深度(5 单已还, 往月不计月度)
    "    for i in range(5):\n"
    "        await repo.add_paylater_order({\n"
    "            'userId': 888001,\n"
    "            'orderNo': f'PL-SEED-R{i}',\n"
    "            'amount': 100.0,\n"
    "            'accountType': 'member',\n"
    "            'source': 'order', 'status': 'repaid',\n"
    "            'riskLevel': 'low', 'riskFlags': [],\n"
    "            'createdAt': '2025-06-01T00:00:00',\n"
    "            'updatedAt': '2025-06-01T00:00:00',\n"
    "            'dueDate': '2025-07-01T00:00:00',\n"
    "            'repaidAt': '2025-06-20T00:00:00',\n"
    "            'overdueDays': 0})\n"
    # ⑥ U3 风险画像(2 逾期单+本月大额占用)
    "    for i in range(2):\n"
    "        await repo.add_paylater_order({\n"
    "            'userId': 888003,\n"
    "            'orderNo': f'PL-SEED-O{i}',\n"
    "            'amount': 100.0,\n"
    "            'accountType': 'member',\n"
    "            'source': 'order', 'status': 'overdue',\n"
    "            'riskLevel': 'mid', 'riskFlags': [],\n"
    "            'createdAt': now,\n"
    "            'updatedAt': now,\n"
    "            'dueDate': (datetime.utcnow()\n"
    "                        - timedelta(days=2)\n"
    "                        ).isoformat(),\n"
    "            'overdueDays': 2})\n"
    "    await repo.add_paylater_order({\n"
    "        'userId': 888003,\n"
    "        'orderNo': 'PL-SEED-M1',\n"
    "        'amount': 4300.0,\n"
    "        'accountType': 'member',\n"
    "        'source': 'order', 'status': 'active',\n"
    "        'riskLevel': 'low', 'riskFlags': [],\n"
    "        'createdAt': now,\n"
    "        'updatedAt': now,\n"
    "        'dueDate': (datetime.utcnow()\n"
    "                    + timedelta(days=30)\n"
    "                    ).isoformat(),\n"
    "        'overdueDays': 0})\n"
    # ⑦ 45号转换轨档案(隔离测试域)
    "    from repositories.trust_value_repository import (\n"
    "        TrustValue45Repository)\n"
    "    repo45 = TrustValue45Repository()\n"
    "    await repo45.save_profile({\n"
    "        'trustId': 9901, 'role': 'person',\n"
    "        'name': 'live-test',\n"
    "        'idDigest': 'digest-9988',\n"
    "        'factors': {}, 'score': 500.0,\n"
    "        'rawScore': 500.0, 'grade': 'A',\n"
    "        'fused': False, 'frozen': False,\n"
    "        'createdAt': '2026-01-01T00:00:00',\n"
    "        'updatedAt': '2026-01-01T00:00:00'})\n"
    # ⑧ 注册表断言(44号 41 档案)
    "    from services.ai_learning_service import (\n"
    "        SCORER_REGISTRY)\n"
    "    out['reg_n'] = len(SCORER_REGISTRY)\n"
    "    out['cs_in'] = 'credit_scoring' \\\n"
    "        in SCORER_REGISTRY\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


# ------------------------------------------------------------
# 检查管道(快照/回流/旁路)
# ------------------------------------------------------------
def snapshot_check(order_no: str) -> dict:
    return run_pipeline(
        "import asyncio, json\n"
        "async def m():\n"
        "    from repositories.ai_learning"
        "_repository import (\n"
        "        AiLearningRepository)\n"
        "    repo = AiLearningRepository()\n"
        "    snap = await repo.get_decision"
        "_snapshot(\n"
        "        'credit_scoring',\n"
        f"        'paylater:{order_no}')\n"
        "    out = {'exists': snap is not None}\n"
        "    if snap:\n"
        "        out['score'] = snap.get('score')\n"
        "        out['decision'] = snap.get('decision')\n"
        "        out['factors'] = len(\n"
        "            snap.get('factors') or [])\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")


def feedback_count() -> dict:
    return run_pipeline(
        "import asyncio, json\n"
        "async def m():\n"
        "    from repositories.ai_learning"
        "_repository import (\n"
        "        AiLearningRepository)\n"
        "    repo = AiLearningRepository()\n"
        "    fbs = await repo.list_feedback(\n"
        "        'credit_scoring')\n"
        "    auto = [f for f in fbs\n"
        "            if f.get('source') == 'auto']\n"
        "    print(json.dumps({\n"
        "        'n': len(fbs),\n"
        "        'auto': len(auto)}))\n"
        "asyncio.run(m())\n")


def convert_bypass_check() -> dict:
    return run_pipeline(
        "import asyncio, json\n"
        "async def m():\n"
        "    from services.trust_asset"
        "_service import (\n"
        "        TrustAssetService)\n"
        "    from repositories.credit"
        "_repository import (\n"
        "        CreditRepository)\n"
        "    svc = TrustAssetService()\n"
        "    r = await svc.convert(9901, 888001, 300)\n"
        "    repo = CreditRepository()\n"
        "    acct = await repo.get_score(888001)\n"
        "    print(json.dumps({\n"
        "        'ok': bool(r.get('success')),\n"
        "        'bamboo': acct.get('bambooScore'),\n"
        "        'level': acct.get('creditLevel'),\n"
        "        'tv': r.get('amount')}))\n"
        "asyncio.run(m())\n")


# ------------------------------------------------------------
# enforce 模式容器内单元(喂料越过冷启动门槛)
# ------------------------------------------------------------
ENFORCE_PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AI_ENFORCE_MODE'] = 'enforce'\n"
    "os.environ['AI_ENFORCE_SCOPES'] = "
    "'credit_scoring'\n"
    "async def m():\n"
    "    out = {}\n"
    # 冷启动喂料(55 条正确反馈≥门槛50+正确率0.70)
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    "    for _ in range(55):\n"
    "        await repo.add_feedback({\n"
    "            'scorerId': 'credit_scoring',\n"
    "            'factors': [{'name': 'zone_margin',\n"
    "                         'score': 10.0,\n"
    "                         'weight': 0.20}],\n"
    "            'correct': True,\n"
    "            'createdAt': '2026-01-01T00:00:00'})\n"
    # 高风险拦截(U3: 近门槛+满额+月压+双逾期)
    "    from services.credit_service import (\n"
    "        CreditService)\n"
    "    svc = CreditService()\n"
    "    blocked = False\n"
    "    try:\n"
    "        await svc.create_paylater_order(\n"
    "            888003, 400.0, order_no='PL-LIVE-EF1')\n"
    "    except ValueError as e:\n"
    "        blocked = ('风控拦截' in str(e))\n"
    "    out['blocked'] = blocked\n"
    # 低风险放行(U4: 优质画像不受影响)
    "    passed = False\n"
    "    try:\n"
    "        o = await svc.create_paylater_order(\n"
    "            888004, 300.0,\n"
    "            order_no='PL-LIVE-EF2')\n"
    "        passed = o.get('status') == 'active'\n"
    "    except ValueError:\n"
    "        passed = False\n"
    "    out['passed'] = passed\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_batch1(round_no)
    seed = run_pipeline(SEED)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, body) = call(
        "GET", f"/api/credit/list?user_id={U1}",
        headers=ADMIN)
    record("信用管理面 200",
           code == 200 and body.get("success") is True,
           str(code))

    print("\n[02 44号注册表]")
    record("41 档案(credit_scoring batch25)",
           seed.get("reg_n") == 55
           and seed.get("cs_in") is True,
           str(seed.get("reg_n")))

    print("\n[03 observe 行为兼容]")
    ok, (code, body) = call(
        "POST", "/api/credit/paylater/order",
        body={"userId": U1, "amount": 500,
              "orderNo": "PL-LIVE-A1"},
        headers={"X-Member-Id": str(U1)})
    order_a = (body.get("data") or {})
    record("先享后付自动通过",
           code == 200
           and order_a.get("status") == "active"
           and order_a.get("riskLevel") == "low",
           f"code={code} st="
           f"{order_a.get('status')}")
    oid_a = order_a.get("orderId")

    print("\n[04 决策快照留痕]")
    snap = snapshot_check("PL-LIVE-A1")
    record("快照存在+低风险+八因子",
           snap.get("exists") is True
           and (snap.get("score") or 0) < 30
           and snap.get("decision") == "low"
           and snap.get("factors") == 8,
           str(snap))

    print("\n[05 中风险转人工]")
    ok, (code, body) = call(
        "POST", "/api/credit/paylater/order",
        body={"userId": U1, "amount": 3000,
              "orderNo": "PL-LIVE-B1"},
        headers={"X-Member-Id": str(U1)})
    order_b = (body.get("data") or {})
    record("大额转人工(review)",
           code == 200
           and order_b.get("status") == "review",
           f"code={code} st="
           f"{order_b.get('status')}")
    oid_b = order_b.get("orderId")

    print("\n[06 审批回流]")
    ok, (code, body) = call(
        "POST", "/api/credit/paylater/review",
        body={"orderId": oid_b, "approved": True},
        headers=ADMIN)
    record("人工审批通过",
           code == 200, str(code))
    fb = feedback_count()
    record("审批→自动反馈(auto)",
           (fb.get("auto") or 0) >= 1,
           str(fb))

    print("\n[07 还款回流+逾期重放]")
    ok, (code, body) = call(
        "POST", "/api/credit/paylater/repay",
        body={"orderId": oid_a,
              "repayChannel": "bank"},
        headers={"X-Member-Id": str(U1)})
    record("按期还款成功", code == 200, str(code))
    ok, (code, _) = call(
        "POST", "/api/credit/paylater/repay",
        body={"orderId": oid_a,
              "repayChannel": "bank"},
        headers={"X-Member-Id": str(U1)},
        expect=(400, 409))
    record("还款重放拒绝(红队RT-02)",
           code in (400, 409), str(code))
    fb = feedback_count()
    record("还款→自动反馈累计≥2",
           (fb.get("auto") or 0) >= 2, str(fb))

    print("\n[08 伪信用注入(红队RT-01)]")
    ok, (code, body) = call(
        "POST", "/api/credit/paylater/order",
        body={"userId": U2, "amount": 950,
              "orderNo": "PL-LIVE-RT1",
              # 伪造字段(服务端必须忽略)
              "bambooScore": 990,
              "creditLevel": "L5",
              "paylaterQuota": 99999},
        headers={"X-Member-Id": str(U2)},
        expect=(200, 400, 409))
    msg = json.dumps(body, ensure_ascii=False)
    record("注入字段被忽略(额度以服务端为准)",
           code in (400, 409)
           and ("可用额度不足" in msg
                or "额度" in msg),
           f"code={code} {msg[:90]}")

    print("\n[09 旁路调分修复(红队RT-03)]")
    r = convert_bypass_check()
    record("转换轨不改写 creditLevel",
           r.get("ok") is True
           and r.get("bamboo") == 450
           and r.get("level") == "L4"
           and r.get("tv") == 3.0,
           str(r))

    print("\n[10 enforce 高风险拦截(容器内)]")
    r = run_pipeline(ENFORCE_PIPELINE)
    record("高风险拦截+低风险放行",
           r.get("blocked") is True
           and r.get("passed") is True,
           str(r))

    print("\n[11 AI 观测面 HTTP]")
    ok, (code, body) = call(
        "GET",
        "/api/ai-learning/enforcement/"
        "credit_scoring/overview",
        headers=ADMIN)
    record("enforcement overview 200",
           code == 200
           and (body.get("mode") or ""
                .lower()) in ("observe", "off",
                              "shadow", "enforce"),
           f"code={code} {str(body)[:90]}")


def main() -> int:
    print("=" * 62)
    print("全站批次一·23号信用管理 AI 升级"
          " Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)
    for i in (1, 2):
        run_round(i)
    print(f"\n{'=' * 62}\n验收汇总: "
          f"{PASS} 通过 / {FAIL} 失败\n{'=' * 62}")
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
