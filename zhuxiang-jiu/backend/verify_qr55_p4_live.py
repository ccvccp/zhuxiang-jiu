"""55号二维码AI智能管理 P4 Docker 实机验收

运行方式:
    python verify_qr55_p4_live.py [基址]

前置: 容器已运行(含 55号 P0-P4 代码)。

覆盖(55号计划 §六 P4, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律+观测面(generate 409;
       governance/attribution 可达)
    03 容器内: 治理联动(46号 sync+三检测器条目
       +冻结守卫翻转+解冻恢复)
    04 容器内: 拨测(12 项白名单 route 全量可达
       +probe 事件留痕)
    05 容器内: 篡改受害者信值补偿(45号 L2 落账
       +幂等 1:1)
    06 容器内: 儿童简化模式(apply 二次确认→
       confirmed 生成; query 直通)
    07 容器内: LLM 归因(学习种子→mock 归因
       数字来自数据层)
    08 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造)。
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


def clear_qr55(round_no: int) -> None:
    """清理种子(qr55 全表+46号 qr_orchestration 态
    +44号池+种子会员)"""
    base = 9950 + round_no * 10
    redis_del_keys("zhuxiang:qr55:*")
    redis_del_keys(
        "zhuxiang:ai46:ai46_registry:qr_orchestration")
    redis_del_keys("zhuxiang:ai_learning:*")
    for mid in range(base, base + 3):
        redis_del_keys(
            f"zhuxiang:trust45:trust45_profiles:{mid}")
        redis_del_keys(
            f"zhuxiang:trust45:idmap:seed-digest-{mid}")


def container_pipeline(round_no: int) -> dict:
    """容器内: 治理→拨测→补偿→儿童→归因(Redis 态)"""
    base_member = 9950 + round_no * 10
    script = (
        "import asyncio, json, os\n"
        "os.environ['QR55_MODE'] = 'on'\n"
        "os.environ['LLM_ENABLED'] = 'off'\n"
        "from repositories.trust_value_repository "
        "import TrustValue45Repository\n"
        "from repositories.qr55_repository "
        "import Qr55Repository\n"
        "from services.qr55_generate_service import "
        "Qr55GenerateService\n"
        "from services.qr55_governance_service import "
        "Qr55GovernanceService\n"
        "from services.qr55_probe_service import "
        "Qr55ProbeService\n"
        "from services.qr55_attribution_service import "
        "Qr55AttributionService\n"
        "from services.ai_governance_service import "
        "AiGovernanceService\n"
        "from core.helpers import ts\n"
        f"BASE_M = {base_member}\n"
        "async def m():\n"
        "    out = {}\n"
        # ① 种子会员
        "    trepo = TrustValue45Repository()\n"
        "    for mid in range(BASE_M, BASE_M + 2):\n"
        "        rec = await trepo.get_profile(mid) "
        "or {}\n"
        "        rec.update({'trustId': mid,\n"
        "                    'grade': 'healthy',\n"
        "                    'score': 80,\n"
        "                    'factors': {},\n"
        "                    'role': 'person',\n"
        "                    'l1Severity': {},\n"
        "                    'idDigest': "
        "f'seed-digest-{mid}'})\n"
        "        await trepo.save_profile(rec)\n"
        # ② 治理联动(46号 sync+健康视图+冻结翻转)
        "    gov = Qr55GovernanceService()\n"
        "    await AiGovernanceService()."
        "sync_registry()\n"
        "    gh = await gov.governance_health()\n"
        "    g = gh.get('governance') or {}\n"
        "    out['healthScore'] = g.get('healthScore')\n"
        "    out['healthLevel'] = g.get('healthLevel')\n"
        "    out['govSignals'] = g.get('signals')\n"
        "    out['frozenBefore'] = (\n"
        "        (gh.get('freezeGuard') or {})\n"
        "        .get('frozen'))\n"
        # 冻结→守卫翻转→解冻恢复
        "    ch = await AiGovernanceService()."
        "submit_change(\n"
        "        'qr_orchestration', 'freeze', {},\n"
        "        'p4-live 冻结验证')\n"
        "    await AiGovernanceService().review_change(\n"
        "        ch['changeId'], True, 'p4-live')\n"
        "    fg = await gov.freeze_guard()\n"
        "    out['frozenDuring'] = fg.get('frozen')\n"
        "    out['freezeReason'] = bool(\n"
        "        fg.get('reason'))\n"
        "    ch2 = await AiGovernanceService()."
        "submit_change(\n"
        "        'qr_orchestration', 'unfreeze', {},\n"
        "        'p4-live 解冻恢复')\n"
        "    await AiGovernanceService().review_change(\n"
        "        ch2['changeId'], True, 'p4-live')\n"
        "    out['frozenAfter'] = (\n"
        "        await gov.freeze_guard()).get('frozen')\n"
        # ③ 拨测(全量 12 项)
        "    os.environ['QR55_MODE'] = 'off'\n"
        "    probe = Qr55ProbeService()\n"
        "    pr = await probe.run_probe()\n"
        "    out['probed'] = pr.get('probed')\n"
        "    out['reachable'] = pr.get('reachable')\n"
        "    out['failed'] = pr.get('failed')\n"
        # ④ 信值补偿(tamper 种子→补偿→幂等)
        "    repo = Qr55Repository()\n"
        "    eid = await repo.next_event_id()\n"
        "    await repo.add_event({\n"
        "        'eventId': eid, 'codeId': 0,\n"
        "        'memberId': BASE_M,\n"
        "        'eventType': 'tamper',\n"
        "        'detail': {'reason': 'p4-live'},\n"
        "        'createdAt': ts()})\n"
        "    comp = await probe."
        "compensate_tamper_victims()\n"
        "    out['compensated'] = comp.get('compensated')\n"
        "    comp2 = await probe."
        "compensate_tamper_victims()\n"
        "    out['comp2'] = comp2.get('compensated')\n"
        "    prof = await trepo.get_profile(BASE_M)\n"
        "    out['pcFactor'] = float(\n"
        "        (prof.get('factors') or {}).get(\n"
        "            'platform_conduct') or 0)\n"
        # ⑤ 儿童模式(apply 二次确认→confirmed 生成)
        "    os.environ['QR55_MODE'] = 'on'\n"
        "    gen = Qr55GenerateService()\n"
        "    r1 = await gen.orchestrate(\n"
        "        BASE_M + 1, '我要给老人办优待证',\n"
        "        child_mode=True)\n"
        "    out['childStatus'] = r1.get('status')\n"
        "    r2 = await gen.orchestrate(\n"
        "        BASE_M + 1, '我要给老人办优待证',\n"
        "        child_mode=True, confirmed=True)\n"
        "    out['childGen'] = r2.get('status')\n"
        "    out['childMark'] = bool(\n"
        "        ((r2.get('personalization') or {})\n"
        "         .get('childMode') or {}).get(\n"
        "             'guardianConfirmed'))\n"
        "    r3 = await gen.orchestrate(\n"
        "        BASE_M + 1, '查政策解读',\n"
        "        child_mode=True)\n"
        "    out['queryDirect'] = r3.get('status')\n"
        # ⑥ LLM 归因(种学习事件→mock 归因)
        "    meid = await repo.next_model_event_id()\n"
        "    await repo.save_model_event({\n"
        "        'modelEventId': meid,\n"
        "        'eventType': 'learning',\n"
        "        'detail': {\n"
        "            'scorerId': "
        "'qr_orchestration',\n"
        "            'learnedFrom': 12,\n"
        "            'parentVersion': 'v1',\n"
        "            'newVersion': 'v2',\n"
        "            'promoted': False,\n"
        "            'weightDelta': {\n"
        "                'intent_confidence': 0.02}},\n"
        "        'createdAt': ts()})\n"
        "    os.environ['QR55_MODE'] = 'off'\n"
        "    att = await Qr55AttributionService()."
        "attribution()\n"
        "    out['attMode'] = att.get('mode')\n"
        "    out['attHasNums'] = (\n"
        "        '12 条' in str(att.get('attribution'))\n"
        "        and 'v1' in str(\n"
        "            att.get('attribution')))\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:400]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_qr55(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/qr55/generate",
        body={"memberId": 9951,
              "text": "办老年优待证"},
        headers=ADMIN, expect=(409,))
    record("off 态 generate 409", code == 409, str(code))
    for path, label in (
            ("/api/qr55/governance/health",
             "governance"),
            ("/api/qr55/registry", "registry")):
        ok, (code, _) = call("GET", path, headers=ADMIN)
        record(f"观测面 {label} off 可访问",
               code == 200, str(code))
    ok, (code, _) = call("GET", "/api/qr55/attribution",
                         headers=ADMIN, expect=(409,))
    record("attribution 无事件 409(语义)",
           code == 409, str(code))

    print("\n[03-07 容器内: 治理→拨测→补偿→儿童→归因]")
    r = container_pipeline(round_no)

    # ③ 治理联动
    record("46号三检测器条目(Redis 态)",
           r.get("healthScore") is not None
           and r.get("healthLevel") in (
               "healthy", "watch", "warning", "risk"),
           str((r.get("healthScore"),
                r.get("healthLevel"))))
    record("冻结守卫翻转(frozen)",
           r.get("frozenBefore") is False
           and r.get("frozenDuring") is True
           and r.get("frozenAfter") is False,
           str((r.get("frozenBefore"),
                r.get("frozenDuring"),
                r.get("frozenAfter"))))
    record("冻结原因追溯(changes 队列)",
           r.get("freezeReason") is True,
           str(r.get("freezeReason")))

    # ④ 拨测
    record("拨测 12 项白名单全量可达",
           r.get("probed") == 12
           and r.get("reachable") == 12
           and r.get("failed") == 0,
           str((r.get("probed"),
                r.get("reachable"),
                r.get("failed"))))

    # ⑤ 信值补偿
    record("篡改受害者补偿(45号 L2 落账)",
           r.get("compensated") == 1
           and (r.get("pcFactor") or 0) > 0,
           str((r.get("compensated"),
                r.get("pcFactor"))))
    record("补偿幂等(1:1 二轮零新增)",
           r.get("comp2") == 0,
           str(r.get("comp2")))

    # ⑥ 儿童模式
    record("apply 类儿童二次确认(Redis 态)",
           r.get("childStatus")
           == "child_confirm_required",
           str(r.get("childStatus")))
    record("confirmed 回传生成+千面标记",
           r.get("childGen") == "generated"
           and r.get("childMark") is True,
           str((r.get("childGen"),
                r.get("childMark"))))
    record("query 类儿童直通",
           r.get("queryDirect") == "generated",
           str(r.get("queryDirect")))

    # ⑦ LLM 归因
    record("mock 归因(数字来自数据层)",
           r.get("attMode") == "mock"
           and r.get("attHasNums") is True,
           str((r.get("attMode"),
                r.get("attHasNums"))))

    print("\n[08 HTTP 端点+鉴权]")
    ok, (code, body) = call(
        "POST", "/api/qr55/probe", headers=ADMIN)
    record("HTTP probe(12 项)",
           code == 200
           and (body or {}).get("probed") == 12,
           str(code))
    ok, (code, body) = call(
        "GET", "/api/qr55/attribution",
        headers=ADMIN)
    record("HTTP attribution 200(有事件)",
           code == 200
           and (body or {}).get("mode") == "mock",
           str(code))
    for method, path in (
            ("POST", "/api/qr55/probe"),
            ("GET", "/api/qr55/attribution"),
            ("GET",
             "/api/qr55/governance/health")):
        resp_ok, (code, _) = call(
            method, path, expect=(403,))
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403",
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
