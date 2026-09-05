"""51号P3 溯源与验真联动 Docker 实机验收

运行方式:
    python verify_kg51_p3_live.py [基址]

前置: 容器已运行(含 51号P3 代码, 镜像已重建)。

覆盖(51号计划 §八 P3, 真实容器):
    01 正常业务零影响(健康检查/35号面板/48号看板)
    02 off 铁律(HTTP: 溯源面不受数据面开关影响
       ——未绑定期内无种子数据时链段降级)
    03 容器内(on 进程): 种子+采集+溯源链服务级断言
       (trace/credit 因子分组+完整率/
        trace/event 事件链全链齐备/
        RepairAction+verified_with 采集)
    04 HTTP 溯源端点(会员自查/属主校验/
       他人 409/不存在 404)
    05 鉴权(401)

每轮容器脚本自清理 kg51:*+99* 种子,
×2 轮幂等验证(每轮独立 member/trust 偏移)。
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


def clear_kg51() -> None:
    patterns = ("zhuxiang:kg51:*",
                "zhuxiang:voice48:voice48_bindings:920*")
    for pattern in patterns:
        out = subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "--scan", "--pattern", pattern],
            capture_output=True, text=True)
        keys = [k for k in (out.stdout or "").split() if k]
        for i in range(0, len(keys), 200):
            subprocess.run(
                ["docker", "exec",
                 "zhuxiang-jiu-redis-1", "redis-cli",
                 "DEL", *keys[i:i + 200]],
                capture_output=True, text=True)


def container_p3_check(round_no: int) -> dict:
    """容器内(on 进程): 种子+采集+溯源服务级断言"""
    member_id = 92000 + round_no
    trust_base = 99300 + round_no * 10
    ev_base = 99300 + round_no * 100
    script = (
        "import asyncio, json, os\n"
        "os.environ['KG_MODE'] = 'on'\n"
        "from repositories.backend import get_redis_client\n"
        "from repositories.trust_value_repository import "
        "TrustValue45Repository\n"
        "from repositories.voice50_repository import "
        "Voice50Repository\n"
        "from repositories.xiaozhu_repository import "
        "Xiaozhu48Repository\n"
        "from services.kg51_ingest_service import "
        "Kg51IngestService\n"
        "from services.kg51_trace_service import "
        "Kg51TraceService\n"
        "from core.helpers import ts as _ts\n"
        f"M = {member_id}\n"
        f"TID = {trust_base}\n"
        f"PEER = {trust_base + 1}\n"
        f"EV = {ev_base}\n"
        "async def m():\n"
        "    out = {}\n"
        "    client = await get_redis_client()\n"
        "    for pat in ('zhuxiang:kg51:*',\n"
        "                'zhuxiang:trust45:trust45_events:"
        "99*',\n"
        "                'zhuxiang:trust45:"
        "trust45_profiles:99*',\n"
        "                'zhuxiang:voice50:"
        "voice50_events:99*',\n"
        "                'zhuxiang:voice50:"
        "voice50_settlement:99*',\n"
        "                'zhuxiang:voice48:"
        "voice48_bindings:%d' % M):\n"
        "        keys = await client.keys(pat)\n"
        "        if keys:\n"
        "            await client.delete(*keys)\n"
        "    p45 = TrustValue45Repository()\n"
        # 45号档案(主+互证对端)
        "    for tid in (TID, PEER):\n"
        "        await p45.save_profile({\n"
        "            'trustId': tid, 'role': 'person',\n"
        "            'name': 'p3live%d' % tid,\n"
        "            'idDigest': 'd%d' % tid,\n"
        "            'factors': {}, 'l1Severity': {},\n"
        "            'score': 0.0, 'rawScore': 0.0,\n"
        "            'grade': 'C', 'fused': False,\n"
        "            'fusedLevel': 'general',\n"
        "            'frozen': False,\n"
        "            'createdAt': _ts(),\n"
        "            'updatedAt': _ts()})\n"
        "    await Xiaozhu48Repository().save_binding({\n"
        "        'memberId': M, 'trustId': TID,\n"
        "        'boundAt': _ts(), 'note': 'p3live'})\n"
        # 50号 settled 事件
        "    v50 = Voice50Repository()\n"
        "    await v50.save_event({\n"
        "        'evId': EV, 'memberId': M,\n"
        "        'sessionId': 0, 'turnSeq': 0,\n"
        "        'behavior': 'voice_polite',\n"
        "        'layer': 'L2', 'voiceFactor': '',\n"
        "        'targetFactor': 'ethics_evidence',\n"
        "        'voiceprintMode': 'proxy',\n"
        "        'baseScore': 1.0, 'multipliers': {},\n"
        "        'finalScore': 1.0, 'cappedScore': 1.0,\n"
        "        'overflowScore': 0.0,\n"
        "        'status': 'settled',\n"
        "        'ref': 'voice50:%d' % EV,\n"
        "        'note': 'p3live', 'dayKey': '2026-09-05',\n"
        "        'ts': _ts(), 'settledBatchId': EV})\n"
        # 45号 deposit(互证双方)+repair
        "    await p45.save_event({\n"
        "        'eventId': EV, 'trustId': TID,\n"
        "        'layer': 'L2',\n"
        "        'factor': 'ethics_evidence',\n"
        "        'delta': 0.5, 'severity': 'general',\n"
        "        'source': 'deposit',\n"
        "        'sources': ['voice50_engine',\n"
        "                    'trust:%d' % PEER],\n"
        "        'summary': '[存证] p3live',\n"
        "        'ts': _ts()})\n"
        "    await p45.save_event({\n"
        "        'eventId': EV+1, 'trustId': PEER,\n"
        "        'layer': 'L2',\n"
        "        'factor': 'ethics_evidence',\n"
        "        'delta': 0.4, 'severity': 'general',\n"
        "        'source': 'deposit',\n"
        "        'sources': ['self',\n"
        "                    'trust:%d' % TID],\n"
        "        'summary': '[存证] peer',\n"
        "        'ts': _ts()})\n"
        "    await p45.save_event({\n"
        "        'eventId': EV+2, 'trustId': TID,\n"
        "        'layer': 'L1',\n"
        "        'factor': 'legal_record',\n"
        "        'delta': 0.3, 'severity': 'general',\n"
        "        'source': 'repair',\n"
        "        'summary': '[修复留痕]', 'ts': _ts()})\n"
        # 50号 settlement(depositId 指向 EV)
        "    await v50.save_settlement({\n"
        "        'batchId': EV, 'dayKey': '2026-09-05',\n"
        "        'memberId': M, 'layer': 'L2',\n"
        "        'factor': 'ethics_evidence',\n"
        "        'credits': 1.0, 'eventCount': 1,\n"
        "        'status': 'done', 'reason': '',\n"
        "        'depositId': EV,\n"
        "        'depositVerified': True,\n"
        "        'depositDelta': 0.5,\n"
        "        'evidence': 'p3live',\n"
        "        'operator': 'manual', 'ts': _ts()})\n"
        # 采集
        "    r = await Kg51IngestService().run_ingest()\n"
        "    out['trace'] = r['sources'].get('trace')\n"
        # 溯源链断言
        "    tr = Kg51TraceService()\n"
        "    c = await tr.trace_credit(M)\n"
        "    out['factorCount'] = c['factorCount']\n"
        "    comp = c['completeness']\n"
        "    out['completeness'] = comp.get(\n"
        "        'completeness')\n"
        "    out['withSettlement'] = comp.get(\n"
        "        'withSettlement')\n"
        "    e = await tr.trace_event(EV, member_id=M)\n"
        "    out['eventFull'] = (\n"
        "        e['settlement'] is not None\n"
        "        and e['deposit'] is not None\n"
        "        and len(e['evidence']) >= 1\n"
        "        and len(e['mutualAttestations'])\n"
        "        >= 1)\n"
        "    try:\n"
        "        await tr.trace_event(\n"
        "            EV, member_id=999)\n"
        "        out['otherDenied'] = False\n"
        "    except ValueError:\n"
        "        out['otherDenied'] = True\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:200]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收\n{'=' * 62}")
    clear_kg51()
    member_id = 92000 + round_no
    ev_id = 99300 + round_no * 100
    member_h = {"X-Member-Id": str(member_id)}

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/xiaozhu/dashboard",
                         headers=ADMIN)
    record("48号看板回归", code == 200, str(code))

    print("\n[02 off 铁律(溯源面不受数据面开关)]")
    ok, (code, body) = call("GET", "/api/kg51/trace/credit",
                            headers=member_h,
                            expect=(409,))
    record("off 态未绑定会员 409(链段降级语义)",
           code == 409, str(code))

    print("\n[03 容器内(on 进程): 溯源链断言]")
    r = container_p3_check(round_no)
    record("溯源采集(trace 轨 scanned>=3)",
           (r.get("trace") or {}).get("scanned", 0) >= 3,
           str(r.get("trace"))[:60])
    record("trace/credit 因子分组(>=1)",
           (r.get("factorCount") or 0) >= 1,
           str(r.get("factorCount")))
    record("溯源完整率 100%",
           r.get("withSettlement", 0) >= 1
           and r.get("completeness") == 1.0,
           str(r.get("completeness")))
    record("trace/event 全链齐备"
           "(settlement+deposit+evidence+互证)",
           r.get("eventFull") is True,
           str(r.get("eventFull")))
    record("他人事件越权拒绝",
           r.get("otherDenied") is True,
           str(r.get("otherDenied")))

    print("\n[04 HTTP 溯源端点(跨进程跨表)]")
    ok, (code, body) = call("GET", "/api/kg51/trace/credit",
                            headers=member_h)
    record("HTTP trace/credit 200",
           code == 200
           and (body.get("factorCount") or 0) >= 1
           and (body.get("completeness") or {})
           .get("completeness") == 1.0,
           str(code))
    ok, (code, body) = call(
        "GET", f"/api/kg51/trace/event/{ev_id}",
        headers=member_h)
    record("HTTP trace/event 200(全链)",
           code == 200
           and (body.get("deposit") or {})
           .get("depositId") == ev_id,
           str(code))
    ok, (code, body) = call(
        "GET", f"/api/kg51/trace/event/{ev_id}",
        headers={"X-Member-Id": "999"},
        expect=(409,))
    record("HTTP trace/event 他人 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/kg51/trace/event/999999",
        headers=ADMIN, expect=(404,))
    record("HTTP trace/event 不存在 404",
           code == 404, str(code))

    print("\n[05 鉴权]")
    ok, (code, _) = call("GET", "/api/kg51/trace/credit",
                         expect=(401,))
    record("trace/credit 无身份 401",
           code == 401, str(code))
    ok, (code, _) = call(
        "GET", "/api/kg51/trace/event/1",
        expect=(401,))
    record("trace/event 无身份 401",
           code == 401, str(code))


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
