"""全站批次五·半 AI 模块接线补全
Docker 实机验收(verify_batch5_ai_live)

运行方式:
    python verify_batch5_ai_live.py [基址]

前置: 容器已运行(含批次五 wiring 代码
      ——AI_ENFORCE_MODE 默认 observe)。

覆盖(《全站AI智能混合架构升级总计划》
批次五交付面, 真实容器 Redis 态):
    01 正常业务零影响(健康+三模块面)
    02 05收款: create_pay 路由门
       (评分+快照永不阻断)+paid 回流
    03 06物流: create_order 路由门
       +签收终态回流(容器管道)
    04 08信息: send_message 内容门
       +发送回流(HTTP)
    05 14团购: apply 资格门+审核回流
       (容器管道)
    06 17后台: assign_permissions 操作门
       +终态回流(容器管道)
    07 18条款: publish_agreement 门
       +终态回流(容器管道)
    08 19财务: audit_voucher 门
       +过账终态回流(容器管道)
    09 红队RT-01 支付渠道硬规则
    10 enforce 高风险拦截+路由类不拦
       (容器内单元, 冷启动门槛喂料)
    11 AI 观测面 HTTP(五评分器)

×2 轮幂等验证(每轮清理种子重造——
payment/logistics/message/groupbuy/
admin/agreement/finance 键域+五评分器
学习键域)。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}

CONTAINER = "zhuxiang-jiu-backend-1"
REDIS = "zhuxiang-jiu-redis-1"

SCORERS = ("payment_routing",
           "logistics_routing:balanced",
           "message_content",
           "groupbuy_qualify",
           "admin_operation",
           "agreement_risk",
           "finance_anomaly")


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


def clear_batch5(round_no: int) -> None:
    redis_del_keys("zhuxiang:payment:*")
    redis_del_keys("zhuxiang:logistics:*")
    redis_del_keys("zhuxiang:message:*")
    redis_del_keys("zhuxiang:groupbuy:*")
    redis_del_keys("zhuxiang:admin:*")
    redis_del_keys("zhuxiang:agreement:*")
    redis_del_keys("zhuxiang:finance:*")
    for scorer in SCORERS:
        redis_del_keys(
            f"zhuxiang:ai_learning:*{scorer}*")


def run_pipeline(script: str) -> dict:
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


def feedback_count(scorer: str) -> int:
    r = run_pipeline(
        "import asyncio, json\n"
        "async def m():\n"
        "    from repositories.ai_learning"
        "_repository import (\n"
        "        AiLearningRepository)\n"
        "    repo = AiLearningRepository()\n"
        "    fbs = await repo.list_feedback(\n"
        f"        '{scorer}')\n"
        "    print(json.dumps({\n"
        "        'n': len([\n"
        "            f for f in fbs\n"
        "            if f.get('source')\n"
        "                == 'auto'])}))\n"
        "asyncio.run(m())\n")
    return r.get("n") or 0


# ------------------------------------------------------------
# ① 05收款管道(创建→启动→回调)
# ------------------------------------------------------------
PAY_PIPELINE = (
    "import asyncio, json\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.payment_service import (\n"
    "        PaymentService)\n"
    "    psvc = PaymentService()\n"
    "    r = await psvc.create_pay(\n"
    "        99601, 'ORD-B5L-P1', 'retail',\n"
    "        288.0, 'wechat', 'native',\n"
    "        'order_pay')\n"
    "    pay_no = r['payNo']\n"
    "    out['created'] = bool(pay_no)\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    "    snap = await repo.get_decision"
    "_snapshot(\n"
    "        'payment_routing',\n"
    "        f'pay:{pay_no}')\n"
    "    out['snap'] = snap is not None\n"
    "    await psvc.start_pay(pay_no)\n"
    "    cb = await psvc.pay_callback(\n"
    "        'TRADE-B5L-1',\n"
    "        {'result': 'SUCCESS'}, pay_no)\n"
    "    out['paid'] = (\n"
    "        cb.get('success') is True)\n"
    "    fbs = await repo.list_feedback(\n"
    "        'payment_routing')\n"
    "    out['fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and f.get('actualAction')\n"
    "            == 'paid'])\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


# ------------------------------------------------------------
# ② 06物流管道(下单→状态链→签收)
# ------------------------------------------------------------
WB_PIPELINE = (
    "import asyncio, json\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.logistics_service import (\n"
    "        LogisticsService)\n"
    "    lsvc = LogisticsService()\n"
    "    sender = {'name': '发货人',\n"
    "              'phone': '13800000001',\n"
    "              'address': '济南市历下区',\n"
    "              'city': '济南'}\n"
    "    receiver = {'name': '收货人',\n"
    "                'phone': '13800000002',\n"
    "                'address': '济南市市中区',\n"
    "                'city': '济南',\n"
    "                'province': '山东省'}\n"
    "    wb = await lsvc.create_order(\n"
    "        'ORD-B5L-L1', 'retail', 'SF',\n"
    "        'standard', sender, receiver,\n"
    "        2.5, 1, 0.0, 100.0, 'monthly')\n"
    "    waybill_no = wb['waybillNo']\n"
    "    out['created'] = bool(waybill_no)\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    "    snap = await repo.get_decision"
    "_snapshot(\n"
    "        'logistics_routing:balanced',\n"
    "        f'wb:{waybill_no}')\n"
    "    out['snap'] = snap is not None\n"
    "    for status in ('booked', 'picked',\n"
    "                   'transporting',\n"
    "                   'delivering'):\n"
    "        await lsvc.update_status(\n"
    "            waybill_no, status)\n"
    "    await lsvc.update_status(\n"
    "        waybill_no, 'signed',\n"
    "        sign_info={\n"
    "            'signerName': '收货人'})\n"
    "    fbs = await repo.list_feedback(\n"
    "        'logistics_routing:balanced')\n"
    "    out['fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and f.get('actualAction')\n"
    "            == 'signed'])\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


# ------------------------------------------------------------
# ③ 14团购+17后台+18条款+19财务 管道
# ------------------------------------------------------------
MIXED_PIPELINE = (
    "import asyncio, json\n"
    "async def m():\n"
    "    out = {}\n"
    "    from core.helpers import ts\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    # ---- 14 团购 ----
    "    from services.groupbuy_service import (\n"
    "        GroupBuyService)\n"
    "    gsvc = GroupBuyService()\n"
    "    gb = await gsvc.apply(\n"
    "        99604, 5, 'enterprise',\n"
    "        [{'productId': 'ZX42-2026L07',\n"
    "          'quantity': 200}],\n"
    "        purpose='B5L测试')\n"
    "    order_no = gb['orderNo']\n"
    "    out['gb_created'] = bool(order_no)\n"
    "    snap = await repo.get_decision"
    "_snapshot(\n"
    "        'groupbuy_qualify',\n"
    "        f'gb:{order_no}')\n"
    "    out['gb_snap'] = snap is not None\n"
    "    await gsvc.audit_order(\n"
    "        order_no, 'admin', 'approved',\n"
    "        'B5L测试审核')\n"
    "    fbs = await repo.list_feedback(\n"
    "        'groupbuy_qualify')\n"
    "    out['gb_fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and f.get('actualAction')\n"
    "            == 'approved'])\n"
    # ---- 17 后台 ----
    "    from services.admin_service import (\n"
    "        AdminService)\n"
    "    asvc = AdminService()\n"
    "    role = await asvc.create_role(\n"
    "        'B5L_ROLE', 'B5L测试角色')\n"
    "    user = await asvc.create_user(\n"
    "        'b5l_admin', 'P@ssw0rd123456',\n"
    "        real_name='B5L管理员')\n"
    "    await asvc.assign_permissions(\n"
    "        user['id'], [role['id']],\n"
    "        operator_id=996)\n"
    "    fbs = await repo.list_feedback(\n"
    "        'admin_operation')\n"
    "    out['adm_fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and f.get('actualAction')\n"
    "            == 'executed'])\n"
    # ---- 18 条款 ----
    "    from services.agreement_service import (\n"
    "        AgreementService)\n"
    "    agsvc = AgreementService()\n"
    "    ag = await agsvc.create_agreement(\n"
    "        'AGR-B5L-001', 'B5L条款',\n"
    "        'user', 'all',\n"
    "        content=('含交付条款、付款条款、'\n"
    "                 '违约责任、争议解决、'\n"
    "                 '保密条款。被告住所地法院'\n"
    "                 '管辖。'))\n"
    "    r = await agsvc.publish_agreement(\n"
    "        ag['id'])\n"
    "    out['agr_pub'] = (\n"
    "        r.get('status') == 'published')\n"
    "    fbs = await repo.list_feedback(\n"
    "        'agreement_risk')\n"
    "    out['agr_fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and f.get('actualAction')\n"
    "            == 'published'])\n"
    # ---- 19 财务 ----
    "    from repositories.finance_repository import (\n"
    "        FinanceRepository)\n"
    "    voucher = {\n"
    "        'voucherNo': 'V-B5L-0001',\n"
    "        'period': '202609',\n"
    "        'date': '2026-09-07',\n"
    "        'type': 'income', 'source': 'order',\n"
    "        'sourceId': 'ORD-B5L-F1',\n"
    "        'status': 'draft',\n"
    "        'amount': 113.0, 'entries': [\n"
    "            {'direction': 'debit',\n"
    "             'subject': '银行存款',\n"
    "             'amount': 113.0,\n"
    "             'summary': '收 wechat'},\n"
    "            {'direction': 'credit',\n"
    "             'subject': '主营业务收入',\n"
    "             'amount': 100.0,\n"
    "             'summary': '销售'},\n"
    "            {'direction': 'credit',\n"
    "             'subject': '销项税',\n"
    "             'amount': 13.0,\n"
    "             'summary': '税'}],\n"
    "        'createdAt': ts(),\n"
    "        'updatedAt': ts()}\n"
    "    await FinanceRepository().save_voucher(\n"
    "        voucher)\n"
    "    from services.finance_service import (\n"
    "        FinanceService)\n"
    "    fsvc = FinanceService()\n"
    "    r1 = await fsvc.audit_voucher(\n"
    "        'V-B5L-0001')\n"
    "    out['fin_audited'] = (\n"
    "        r1.get('status') == 'audited')\n"
    "    r2 = await fsvc.audit_voucher(\n"
    "        'V-B5L-0001')\n"
    "    out['fin_posted'] = (\n"
    "        r2.get('status') == 'posted')\n"
    "    fbs = await repo.list_feedback(\n"
    "        'finance_anomaly')\n"
    "    out['fin_fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and f.get('actualAction')\n"
    "            == 'posted'])\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


# ------------------------------------------------------------
# ④ enforce 模式容器内单元(喂料越过冷启动门槛)
# ------------------------------------------------------------
ENFORCE_PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AI_ENFORCE_MODE'] = 'enforce'\n"
    "os.environ['AI_ENFORCE_SCOPES'] = "
    "'message_content,groupbuy_qualify,"
    "agreement_risk,finance_anomaly'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    "    for sid in ('message_content',\n"
    "                'groupbuy_qualify',\n"
    "                'agreement_risk',\n"
    "                'finance_anomaly'):\n"
    "        for _ in range(55):\n"
    "            await repo.add_feedback({\n"
    "                'scorerId': sid,\n"
    "                'factors': [{'name': 'x',\n"
    "                             'score': 10.0,\n"
    "                             'weight': 0.5}],\n"
    "                'correct': True,\n"
    "                'createdAt':\n"
    "                    '2026-01-01T00:00:00'})\n"
    # ① 消息高危拦截
    "    from services.message_service import (\n"
    "        MessageService)\n"
    "    msvc = MessageService()\n"
    "    for _ in range(10):\n"
    "        await msvc.send_message(\n"
    "            99605, 'inmail', '预热',\n"
    "            '系统通知预热', 'system')\n"
    "    blocked = False\n"
    "    try:\n"
    "        await msvc.send_message(\n"
    "            99605, 'inmail', '垃圾广告',\n"
    "            '代开发票 博彩 贷款包过 '\n"
    "            '添加微信 兼职日结 刷单 返现 '\n"
    "            '高利贷 股票内幕 '\n"
    "            'http://a.com http://b.com '\n"
    "            'http://c.com')\n"
    "    except ValueError:\n"
    "        blocked = True\n"
    "    out['msg_blocked'] = blocked\n"
    # ② 团购 rejected 档拦截
    "    from services.ai_enforcement_wiring import (\n"
    "        enforce_groupbuy_apply,\n"
    "        enforce_pay_create)\n"
    "    blocked = False\n"
    "    try:\n"
    "        await enforce_groupbuy_apply(\n"
    "            'GB-B5L-X1',\n"
    "            {'qualificationDocs': 0,\n"
    "             'annualPurchaseAmount': 0.0,\n"
    "             'onTimePaymentRatio': 0.5,\n"
    "             'violationCount': 3,\n"
    "             'targetQuantity': 1})\n"
    "    except ValueError:\n"
    "        blocked = True\n"
    "    out['gb_blocked'] = blocked\n"
    # ③ 条款高危拦截
    "    from services.ai_enforcement_wiring import (\n"
    "        enrich_agreement_publish,\n"
    "        enforce_agreement_publish)\n"
    "    ctx = await enrich_agreement_publish(\n"
    "        {'content': '免责免责免责单方单方'\n"
    "                    '管辖'})\n"
    "    blocked = False\n"
    "    try:\n"
    "        await enforce_agreement_publish(\n"
    "            99607, ctx)\n"
    "    except ValueError:\n"
    "        blocked = True\n"
    "    out['agr_blocked'] = blocked\n"
    # ④ 财务高危拦截
    "    from services.ai_enforcement_wiring import (\n"
    "        enforce_finance_audit)\n"
    "    blocked = False\n"
    "    try:\n"
    "        await enforce_finance_audit(\n"
    "            'V-B5L-X1',\n"
    "            {'amount': 1000.0,\n"
    "             'accountAverageAmount': 1.0,\n"
    "             'summaryMatchScore': 0.0,\n"
    "             'unbalanceAmount': 20.0})\n"
    "    except ValueError:\n"
    "        blocked = True\n"
    "    out['fin_blocked'] = blocked\n"
    # ⑤ 路由类永不阻断
    "    gate = await enforce_pay_create(\n"
    "        'PAY-B5L-X1',\n"
    "        {'amount': 100.0,\n"
    "         'sceneType': 'order_pay'})\n"
    "    out['pay_ok'] = (\n"
    "        gate.get('blocked') is False)\n"
    # ⑥ 低风险放行(消息)
    "    msg_ok = False\n"
    "    try:\n"
    "        r = await msvc.send_message(\n"
    "            99606, 'inmail', '正常',\n"
    "            '正常通知内容', 'system')\n"
    "        msg_ok = bool(\n"
    "            r.get('messageId')\n"
    "            or r.get('id'))\n"
    "    except ValueError:\n"
    "        msg_ok = False\n"
    "    out['msg_ok'] = msg_ok\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_batch5(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call(
        "GET", "/api/payment/channels",
        headers=ADMIN)
    record("收款面 200", code == 200, str(code))
    ok, (code, _) = call(
        "GET", "/api/groupbuy/list?limit=5",
        headers={"X-Member-Id": "99604"})
    record("团购面 200", code == 200, str(code))

    print("\n[02 05收款路由门(容器管道)]")
    r = run_pipeline(PAY_PIPELINE)
    record("支付创建 observe 兼容",
           r.get("created") is True, str(r))
    record("支付快照已存(路由类)",
           r.get("snap") is True, str(r))
    record("paid 回流",
           (r.get("fb") or 0) >= 1, str(r))

    print("\n[03 06物流路由门(容器管道)]")
    r = run_pipeline(WB_PIPELINE)
    record("运单创建 observe 兼容",
           r.get("created") is True, str(r))
    record("运单快照已存(路由类)",
           r.get("snap") is True, str(r))
    record("签收回流",
           (r.get("fb") or 0) >= 1, str(r))

    print("\n[04 08信息内容门(HTTP)]")
    ok, (code, body) = call(
        "POST", "/api/message/send",
        body={"userId": 99603,
              "channel": "inmail",
              "title": "B5L通知",
              "content": "正常消息内容",
              "category": "system"},
        headers=ADMIN)
    record("消息 observe 兼容",
           code == 200, f"code={code}")
    fb = feedback_count("message_content")
    record("消息发送回流",
           fb >= 1, str(fb))

    print("\n[05 14/17/18/19 四门(容器管道)]")
    r = run_pipeline(MIXED_PIPELINE)
    record("团购申请+快照+审核回流",
           r.get("gb_created") is True
           and r.get("gb_snap") is True
           and (r.get("gb_fb") or 0) >= 1,
           str(r))
    record("后台操作回流",
           (r.get("adm_fb") or 0) >= 1, str(r))
    record("条款发布+回流",
           r.get("agr_pub") is True
           and (r.get("agr_fb") or 0) >= 1,
           str(r))
    record("凭证审核+过账+回流",
           r.get("fin_audited") is True
           and r.get("fin_posted") is True
           and (r.get("fin_fb") or 0) >= 1,
           str(r))

    print("\n[06 红队RT-01 支付硬规则]")
    ok, (code, _) = call(
        "POST", "/api/payment/pay",
        body={"orderId": "ORD-B5L-BAD",
              "orderType": "retail",
              "totalAmount": 288.0,
              "payChannel": "invalid_channel",
              "payMethod": "native",
              "sceneType": "order_pay"},
        headers={"X-Member-Id": "99601"},
        expect=(400, 409, 422))
    record("非法渠道拒绝",
           code in (400, 409, 422), str(code))

    print("\n[07 enforce 拦截(容器内)]")
    r = run_pipeline(ENFORCE_PIPELINE)
    record("消息拦截+低风险放行",
           r.get("msg_blocked") is True
           and r.get("msg_ok") is True,
           str(r))
    record("团购 rejected 档拦截",
           r.get("gb_blocked") is True, str(r))
    record("条款高危拦截",
           r.get("agr_blocked") is True, str(r))
    record("财务高危拦截",
           r.get("fin_blocked") is True, str(r))
    record("路由类永不阻断",
           r.get("pay_ok") is True, str(r))

    print("\n[08 AI 观测面 HTTP]")
    for scorer in ("message_content",
                   "groupbuy_qualify",
                   "admin_operation",
                   "agreement_risk",
                   "finance_anomaly"):
        ok, (code, body) = call(
            "GET",
            f"/api/ai-learning/enforcement/"
            f"{scorer}/overview",
            headers=ADMIN)
        record(f"{scorer} overview 200",
               code == 200, str(code))


def main() -> int:
    print("=" * 62)
    print("全站批次五·半 AI 模块接线补全"
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
