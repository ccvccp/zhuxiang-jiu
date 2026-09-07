"""全站批次六·长尾七模块 AI 升级
Docker 实机验收(verify_batch6_ai_live)

运行方式:
    python verify_batch6_ai_live.py [基址]

前置: 容器已运行(含批次六 longtail 代码
      ——AI_ENFORCE_MODE 默认 observe)。

覆盖(真实容器 Redis 态):
    01 健康面+55 档案注册(batch33-39)
    02 07客服: 建单门+快照+确认回流(管道)
    03 15合作: 审核门+签约回流(管道)
    04 16代理: 审核门+快照(管道)
    05 20位置: 配送点门+即时回流(管道)
    06 21酒店: 审核门+快照(管道)
    07 26监控: 告警门+解决回流(管道)
    08 27维护: 自愈门+终态回流(管道)
    09 enforce 七门高风险拦截+低风险放行(管道)
    10 AI 观测面 HTTP(七评分器)

×2 轮幂等验证。
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

SCORERS = ("ticket_quality", "partner_review",
           "agent_risk", "delivery_zone",
           "venue_partner", "ops_alert",
           "self_healing")


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


def clear_batch6(round_no: int) -> None:
    for pfx in ("ticket", "cooperation", "agent",
                "loc", "venue", "monitor",
                "maintenance"):
        redis_del_keys(f"zhuxiang:{pfx}:*")
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


# ------------------------------------------------------------
# 主管道: 七模块全链路(observe)
# ------------------------------------------------------------
MAIN_PIPELINE = (
    "import asyncio, json\n"
    "async def m():\n"
    "    out = {}\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    from services.ai_learning"
    "_service import (\n"
    "        SCORER_REGISTRY)\n"
    "    out['reg_n'] = len(SCORER_REGISTRY)\n"
    "    repo = AiLearningRepository()\n"
    # ---- 07 客服 ----
    "    from services.ticket_service import (\n"
    "        TicketService)\n"
    "    tsvc = TicketService()\n"
    "    t = await tsvc.create_ticket(\n"
    "        99501, 'aftersale', 'medium',\n"
    "        'B6L测试咨询', 'user')\n"
    "    tno = t['ticketNo']\n"
    "    out['tk_created'] = bool(tno)\n"
    "    snap = await repo.get_decision"
    "_snapshot(\n"
    "        'ticket_quality',\n"
    "        f'ticket:{tno}')\n"
    "    out['tk_snap'] = snap is not None\n"
    "    if snap:\n"
    "        out['tk_dec'] = snap.get("
    "'decision')\n"
    "        out['tk_factors'] = len(\n"
    "            snap.get('factors') or [])\n"
    "    await tsvc.assign_ticket(tno, 8801)\n"
    "    await tsvc.resolve_ticket(\n"
    "        tno, 8801, '已解答')\n"
    "    await tsvc.confirm_ticket(tno, 99501, 5)\n"
    "    fbs = await repo.list_feedback(\n"
    "        'ticket_quality')\n"
    "    out['tk_fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and (f.get('actualAction') or '')\n"
    "            .startswith('confirmed')])\n"
    # ---- 15 合作 ----
    "    from services.cooperation_service import (\n"
    "        CooperationService)\n"
    "    csvc = CooperationService()\n"
    "    app = await csvc.create_application(\n"
    "        'B6L合作方', 'supply', 'product',\n"
    "        '竹叶青供应', 50000.0,\n"
    "        contact_name='B6L联系人',\n"
    "        contact_phone='13900000001',\n"
    "        qualification_files=['执照.pdf'])\n"
    "    r = await csvc.review_application(\n"
    "        app['id'])\n"
    "    out['cp_result'] = r.get('result')\n"
    "    if r.get('result') == 'pass':\n"
    "        await csvc.sign_application(\n"
    "            app['id'], 'B6L合作协议')\n"
    "    fbs = await repo.list_feedback(\n"
    "        'partner_review')\n"
    "    out['cp_fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and f.get('actualAction')\n"
    "            == 'signed'])\n"
    # ---- 16 代理 ----
    "    from services.agent_service import (\n"
    "        AgentService)\n"
    "    agsvc = AgentService()\n"
    "    ap = await agsvc.apply(\n"
    "        'B6L代理公司', 'B6L联系人',\n"
    "        '13900000002', '山东', 'C')\n"
    "    r = await agsvc.audit(\n"
    "        ap['applyId'], 'approved')\n"
    "    out['ag_dec'] = r.get('decision')\n"
    "    snap = await repo.get_decision"
    "_snapshot(\n"
    "        'agent_risk',\n"
    "        f\"audit:{ap['applyId']}\")\n"
    "    out['ag_snap'] = snap is not None\n"
    # ---- 20 位置 ----
    "    from services.location_service import (\n"
    "        LocationService)\n"
    "    lsvc = LocationService()\n"
    "    await lsvc.add_delivery_zone(\n"
    "        'B6L济南仓', 'circle',\n"
    "        center_lng=117.0, center_lat=36.6,\n"
    "        radius=50)\n"
    "    r = await lsvc.check_delivery_point(\n"
    "        117.0, 36.6)\n"
    "    out['lc_matched'] = (\n"
    "        r.get('inDeliveryRange') is True)\n"
    "    fbs = await repo.list_feedback(\n"
    "        'delivery_zone')\n"
    "    out['lc_fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and f.get('actualAction')\n"
    "            == 'checked'])\n"
    # ---- 21 酒店 ----
    "    from services.venue_service import (\n"
    "        VenueService)\n"
    "    vsvc = VenueService()\n"
    "    p = await vsvc.apply_partner(\n"
    "        'hotel', 'B6L大酒店',\n"
    "        '91370000B6LCODE',\n"
    "        contact_phone='13900000003',\n"
    "        star_level=4, agent_id=9801)\n"
    "    r = await vsvc.audit_partner(\n"
    "        p['id'], 'approve',\n"
    "        contract_start='2026-09-07',\n"
    "        contract_end='2027-09-07',\n"
    "        partner_level='B')\n"
    "    out['vn_status'] = r.get('newStatus')\n"
    "    snap = await repo.get_decision"
    "_snapshot(\n"
    "        'venue_partner',\n"
    "        f\"partner:{p['id']}\")\n"
    "    out['vn_snap'] = snap is not None\n"
    # ---- 26 监控 ----
    "    from services.monitor_service import (\n"
    "        MonitorService)\n"
    "    msvc = MonitorService()\n"
    "    al = await msvc.raise_alert(\n"
    "        'B6LCPU告警', 'cpu', 'warning',\n"
    "        'server-1', threshold={'value': 80},\n"
    "        current_value=85)\n"
    "    out['mn_id'] = bool(al.get('id'))\n"
    "    await msvc.acknowledge_alert(\n"
    "        al['id'])\n"
    "    await msvc.resolve_alert(al['id'])\n"
    "    fbs = await repo.list_feedback(\n"
    "        'ops_alert')\n"
    "    out['mn_fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and f.get('actualAction')\n"
    "            == 'resolved'])\n"
    # ---- 27 维护 ----
    "    from services.maintenance_service import (\n"
    "        MaintenanceService)\n"
    "    mtsvc = MaintenanceService()\n"
    "    rec = await mtsvc.detect_fault(\n"
    "        'performance', 'server-1',\n"
    "        'B6L性能故障', 'auto')\n"
    "    out['mt_status'] = rec.get(\n"
    "        'recoveryStatus')\n"
    "    await mtsvc.diagnose_fault(rec['id'])\n"
    "    await mtsvc.attempt_recovery(\n"
    "        rec['id'], success=True)\n"
    "    fbs = await repo.list_feedback(\n"
    "        'self_healing')\n"
    "    out['mt_fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'\n"
    "        and f.get('actualAction')\n"
    "            == 'recovered'])\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


# ------------------------------------------------------------
# enforce 管道(喂料+七门拦截)
# ------------------------------------------------------------
ENFORCE_PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AI_ENFORCE_MODE'] = 'enforce'\n"
    "os.environ['AI_ENFORCE_SCOPES'] = "
    "','.join(['ticket_quality',"
    "'partner_review','agent_risk',"
    "'delivery_zone','venue_partner',"
    "'ops_alert','self_healing'])\n"
    "async def m():\n"
    "    out = {}\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    "    for sid in ('ticket_quality',\n"
    "                'partner_review',\n"
    "                'agent_risk',\n"
    "                'delivery_zone',\n"
    "                'venue_partner',\n"
    "                'ops_alert',\n"
    "                'self_healing'):\n"
    "        for _ in range(55):\n"
    "            await repo.add_feedback({\n"
    "                'scorerId': sid,\n"
    "                'factors': [{'name': 'x',\n"
    "                             'score': 10.0,\n"
    "                             'weight': 0.5}],\n"
    "                'correct': True,\n"
    "                'createdAt':\n"
    "                    '2026-01-01T00:00:00'})\n"
    "    from services.ai_enforcement"
    "_longtail import (\n"
    "        enforce_ticket_create,\n"
    "        enforce_partner_review,\n"
    "        enforce_agent_audit,\n"
    "        enforce_delivery_point,\n"
    "        enforce_venue_audit,\n"
    "        enforce_ops_alert,\n"
    "        enforce_self_healing)\n"
    "    cases = [\n"
    "        ('tk', enforce_ticket_create,\n"
    "         ('T-X', {'ticketType':\n"
    "                        'complaint',\n"
    "                  'priority': 'urgent',\n"
    "                  'userLevel': 5,\n"
    "                  'slaOverdueHours': 50,\n"
    "                  'compensationLevel':\n"
    "                      'severe',\n"
    "                  'escalated': True,\n"
    "                  'reopened': True})),\n"
    "        ('cp', enforce_partner_review,\n"
    "         ('A-X', {'partnerType': 'supply',\n"
    "                  'partnerLevel': 'bronze',\n"
    "                  'qualificationGap': 3,\n"
    "                  'reviewScore': 20,\n"
    "                  'estimatedAmount': 600000,\n"
    "                  'partnerViolations': 2,\n"
    "                  'contractTerminated': True,\n"
    "                  'multiRegion': True})),\n"
    "        ('ag', enforce_agent_audit,\n"
    "         (1, {'creditScore': 20,\n"
    "              'returnRate': 0.3,\n"
    "              'paymentDelayRate': 0.5,\n"
    "              'level': 'D',\n"
    "              'totalPurchases': 2000000,\n"
    "              'crossRegion': True,\n"
    "              'walletRatio': 0.9,\n"
    "              'activeMonths': 1})),\n"
    "        ('lc', enforce_delivery_point,\n"
    "         ('P-X', {'distanceKm': 100,\n"
    "                  'radiusKm': 5,\n"
    "                  'zoneStatus': 'disabled',\n"
    "                  'shippingFee': 20,\n"
    "                  'hasEvidence': False})),\n"
    "        ('vn', enforce_venue_audit,\n"
    "         (1, {'partnerType': 'club',\n"
    "              'partnerLevel': 'D',\n"
    "              'starLevel': 0,\n"
    "              'supplyMode':\n"
    "                  'consignment',\n"
    "              'agentId': None,\n"
    "              'unsettledAmount': 60000,\n"
    "              'paylaterQuota': 10000,\n"
    "              'paylaterUsed': 9000,\n"
    "              'suspendedCount': 2})),\n"
    "        ('mn', enforce_ops_alert,\n"
    "         (1, {'alertLevel': 'fatal',\n"
    "              'currentValue': 300,\n"
    "              'threshold': 100,\n"
    "              'unresolvedCount': 5,\n"
    "              'ackDelayMinutes': 120,\n"
    "              'source': 'database',\n"
    "              'incidentLinked': True})),\n"
    "        ('mt', enforce_self_healing,\n"
    "         (1, {'faultType': 'data_loss',\n"
    "              'recoveryLevel': 'manual',\n"
    "              'recentFailures': 3,\n"
    "              'diagnoseConfidence': 10,\n"
    "              'target': 'database',\n"
    "              'downtimeMinutes': 300,\n"
    "              'taskFailureRate': 0.5,\n"
    "              'multiTarget': True})),\n"
    "    ]\n"
    "    for k, fn, (key, ctx) in cases:\n"
    "        blocked = False\n"
    "        try:\n"
    "            await fn(key, ctx)\n"
    "        except ValueError:\n"
    "            blocked = True\n"
    "        out[k + '_blocked'] = blocked\n"
    "    gate = await enforce_ticket_create(\n"
    "        'T-OK', {'ticketType': 'aftersale',\n"
    "                 'priority': 'low',\n"
    "                 'userLevel': 1})\n"
    "    out['low_ok'] = (\n"
    "        gate.get('blocked') is False)\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_batch6(round_no)

    print("\n[01 健康面+注册表]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    r = run_pipeline(MAIN_PIPELINE)
    record("55 档案注册(batch33-39)",
           r.get("reg_n") == 55, str(r.get("reg_n")))

    print("\n[02 07客服工单]")
    record("建单 observe 兼容",
           r.get("tk_created") is True, str(r))
    record("快照低风险+八因子",
           r.get("tk_snap") is True
           and r.get("tk_dec") == "low"
           and r.get("tk_factors") == 8,
           str(r))
    record("确认回流",
           (r.get("tk_fb") or 0) >= 1, str(r))

    print("\n[03 15合作接口]")
    record("审核 observe 兼容",
           r.get("cp_result") in ("pass", "reject"),
           str(r.get("cp_result")))
    record("审核回流",
           (r.get("cp_fb") or 0) >= 1, str(r))

    print("\n[04 16代理商]")
    record("审核 observe 兼容+快照",
           r.get("ag_dec") == "approved"
           and r.get("ag_snap") is True,
           str(r))

    print("\n[05 20位置地图]")
    record("配送判定兼容+即时回流",
           r.get("lc_matched") is True
           and (r.get("lc_fb") or 0) >= 1,
           str(r))

    print("\n[06 21酒店合作商]")
    record("审核兼容+快照",
           r.get("vn_status") == "signed"
           and r.get("vn_snap") is True,
           str(r))

    print("\n[07 26智能监控]")
    record("告警兼容+解决回流",
           r.get("mn_id") is True
           and (r.get("mn_fb") or 0) >= 1,
           str(r))

    print("\n[08 27智能维护]")
    record("自愈兼容+终态回流",
           r.get("mt_status") == "detected"
           and (r.get("mt_fb") or 0) >= 1,
           str(r))

    print("\n[09 enforce 拦截(容器内)]")
    e = run_pipeline(ENFORCE_PIPELINE)
    for k in ("tk", "cp", "ag", "lc",
              "vn", "mn", "mt"):
        record(f"enforce {k} 拦截",
               e.get(f"{k}_blocked") is True,
               str(e))
    record("enforce 低风险放行",
           e.get("low_ok") is True, str(e))

    print("\n[10 AI 观测面 HTTP]")
    for scorer in SCORERS:
        ok, (code, body) = call(
            "GET",
            f"/api/ai-learning/enforcement/"
            f"{scorer}/overview",
            headers=ADMIN)
        record(f"{scorer} overview 200",
               code == 200, str(code))


def main() -> int:
    print("=" * 62)
    print("全站批次六·长尾七模块 AI 升级"
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
