"""51号P4 治理与演进 Docker 实机验收

运行方式:
    python verify_kg51_p4_live.py [基址]

前置: 容器已运行(含 51号P4 代码, 镜像已重建)。

覆盖(51号计划 §八 P4, 真实容器):
    01 正常业务零影响(健康检查/35号面板/48号看板)
    02 容器内(on 进程): 种子+采集+治理服务级断言
       (巡检三指标/版本快照回溯/公平桥 side-door/
        反馈闭环/看板五分区)
    03 HTTP 治理端点(巡检/版本/公平桥/看板——
       off 主进程治理面不受影响)
    04 HTTP 反馈端点(会员面+鉴权)
    05 鉴权(401/403)

每轮容器脚本自清理 kg51:*+99* 种子,
×2 轮幂等验证(每轮独立偏移)。
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
    for pattern in ("zhuxiang:kg51:*",
                    "zhuxiang:voice48:voice48_bindings:95*"):
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


def container_p4_check(round_no: int) -> dict:
    """容器内(on 进程): 种子+采集+治理服务级断言"""
    member_id = 95000 + round_no
    ev_base = 99400 + round_no * 100
    script = (
        "import asyncio, json, os\n"
        "os.environ['KG_MODE'] = 'on'\n"
        "from repositories.backend import get_redis_client\n"
        "from repositories.voice50_repository import "
        "Voice50Repository\n"
        "from repositories.xiaozhu_repository import "
        "Xiaozhu48Repository\n"
        "from repositories.kg51_repository import "
        "Kg51Repository\n"
        "from services.kg51_ingest_service import "
        "Kg51IngestService\n"
        "from services.kg51_governance_service import "
        "Kg51GovernanceService\n"
        "from core.helpers import ts as _ts\n"
        f"M = {member_id}\n"
        f"EV = {ev_base}\n"
        "async def m():\n"
        "    out = {}\n"
        "    client = await get_redis_client()\n"
        "    for pat in ('zhuxiang:kg51:*',\n"
        "                'zhuxiang:voice50:"
        "voice50_events:99*',\n"
        "                'zhuxiang:voice48:"
        "voice48_bindings:%d' % M):\n"
        "        keys = await client.keys(pat)\n"
        "        if keys:\n"
        "            await client.delete(*keys)\n"
        "    v50 = Voice50Repository()\n"
        "    for i, (beh, layer, factor) in enumerate(\n"
        "            (('voice_polite', 'L2',\n"
        "              'ethics_evidence'),\n"
        "             ('voice_community_qa', 'L3',\n"
        "              'longtail_good'))):\n"
        "        await v50.save_event({\n"
        "            'evId': EV+i, 'memberId': M,\n"
        "            'sessionId': 0, 'turnSeq': 0,\n"
        "            'behavior': beh, 'layer': layer,\n"
        "            'voiceFactor': '',\n"
        "            'targetFactor': factor,\n"
        "            'voiceprintMode': 'proxy',\n"
        "            'baseScore': 1.0, 'multipliers': {},\n"
        "            'finalScore': 1.0, 'cappedScore': 1.0,\n"
        "            'overflowScore': 0.0,\n"
        "            'status': 'settled',\n"
        "            'ref': 'voice50:%d' % (EV+i),\n"
        "            'note': 'p4live',\n"
        "            'dayKey': '2026-09-05',\n"
        "            'ts': _ts(), 'settledBatchId': 0})\n"
        "    await Xiaozhu48Repository().save_binding({\n"
        "        'memberId': M, 'trustId': 1,\n"
        "        'boundAt': _ts(), 'note': 'p4live'})\n"
        "    await Kg51IngestService().run_ingest()\n"
        "    gov = Kg51GovernanceService()\n"
        # 巡检三指标
        "    insp = await gov.run_inspection()\n"
        "    out['insp'] = {\n"
        "        'completeness': insp['completeness'],\n"
        "        'consistency': insp['consistency'],\n"
        "        'freshness': insp['freshness']}\n"
        "    out['inspOk'] = all(\n"
        "        0 <= insp[k] <= 1 for k in\n"
        "        ('completeness', 'consistency',\n"
        "         'freshness'))\n"
        # 版本快照+回溯
        "    ver = await gov.snapshot_version(\n"
        "        label='p4live')\n"
        "    out['verId'] = "
        "ver['version']['versionId']\n"
        "    out['verStats'] = (\n"
        "        ver['version']['entityCount'] > 0\n"
        "        and ver['version']['tripleCount']\n"
        "        > 0)\n"
        "    vl = await gov.list_versions()\n"
        "    out['verList'] = (\n"
        "        len(vl['versions']) == 1\n"
        "        and vl['versions'][0]\n"
        "        ['versionLabel'] == 'p4live')\n"
        # 公平桥
        "    fb = await gov.bridge_fairness()\n"
        "    out['fbBridged'] = fb.get('bridged')\n"
        "    out['fbGroups'] = fb.get('groups')\n"
        # 反馈闭环
        "    fd = await gov.submit_feedback(\n"
        "        member_id=M, turn_id='t-p4live',\n"
        "        target_triple='a|b|c', note='实机')\n"
        "    out['feedbackOk'] = (\n"
        "        fd.get('feedbackId') == 1\n"
        "        and (fd.get('reviewId') or 0) >= 1)\n"
        "    fl = await gov.list_feedback()\n"
        "    out['fbList'] = fl['total'] == 1\n"
        # 看板
        "    db = await gov.dashboard()\n"
        "    out['dbZones'] = all(\n"
        "        k in db for k in ('scale', 'verified',\n"
        "        'reviewBacklog', 'budget', 'versions'))\n"
        "    out['dbScale'] = (\n"
        "        db['scale']['tripleCount'] > 0)\n"
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

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/xiaozhu/dashboard",
                         headers=ADMIN)
    record("48号看板回归", code == 200, str(code))

    print("\n[02 容器内(on 进程): 治理服务级断言]")
    r = container_p4_check(round_no)
    record("巡检三指标(0-1 结构)",
           r.get("inspOk") is True,
           str(r.get("insp")))
    record("版本快照(统计五元组>0)",
           r.get("verStats") is True
           and r.get("verId") == 1,
           str(r.get("verId")))
    record("版本回溯(最新在前)",
           r.get("verList") is True,
           str(r.get("verList")))
    record("公平桥(bridged>=2 组)",
           (r.get("fbBridged") or 0) >= 2,
           str(r.get("fbGroups")))
    record("反馈闭环(feedbackId+reviewId)",
           r.get("feedbackOk") is True,
           str(r.get("feedbackOk")))
    record("反馈台账(1 条)",
           r.get("fbList") is True,
           str(r.get("fbList")))
    record("看板五分区",
           r.get("dbZones") is True
           and r.get("dbScale") is True,
           str(r.get("dbZones")))

    print("\n[03 HTTP 治理端点(off 主进程——治理面"
          "不受数据面开关影响)]")
    ok, (code, body) = call("POST", "/api/kg51/inspect/run",
                            headers=ADMIN)
    record("HTTP 巡检触发 200",
           code == 200
           and ((body.get("inspection") or {})
                .get("inspectionId") or 0) >= 1,
           str(code))
    ok, (code, body) = call(
        "GET", "/api/kg51/inspect/latest",
        headers=ADMIN)
    record("HTTP 最近巡检 200",
           code == 200
           and ((body.get("inspection") or {})
                .get("inspectionId") or 0) >= 1,
           str(code))
    ok, (code, body) = call(
        "POST", "/api/kg51/versions/snapshot",
        headers=ADMIN, body={})
    record("HTTP 版本快照 200",
           code == 200
           and ((body.get("version") or {})
                .get("versionId")) == 2,
           str(code))
    ok, (code, body) = call("GET", "/api/kg51/versions",
                            headers=ADMIN)
    record("HTTP 版本回溯 200(2 条)",
           code == 200
           and (body.get("total") or 0) == 2,
           str(code))
    ok, (code, body) = call(
        "POST", "/api/kg51/fairness-bridge",
        headers=ADMIN)
    record("HTTP 公平桥 200(实机容器)",
           code == 200
           and (body.get("bridged") or 0) >= 2,
           str(code))
    ok, (code, body) = call("GET", "/api/kg51/dashboard",
                            headers=ADMIN)
    record("HTTP 看板 200(五分区)",
           code == 200 and "budget" in body,
           str(code))

    print("\n[04 HTTP 反馈端点(会员面)]")
    ok, (code, body) = call(
        "POST", "/api/kg51/feedback",
        headers={"X-Member-Id": "9500" + str(round_no)},
        body={"turnId": "t-http4567",
              "targetTriple": "x|y|z",
              "note": "HTTP 实机"})
    record("HTTP 反馈提交 200",
           code == 200
           and (body.get("feedbackId") or 0) >= 1,
           str(code))
    ok, (code, body) = call("GET", "/api/kg51/feedback",
                            headers=ADMIN)
    record("HTTP 反馈台账 200",
           code == 200, str(code))

    print("\n[05 鉴权]")
    ok, (code, _) = call("POST", "/api/kg51/inspect/run",
                        body={}, expect=(403,))
    record("巡检无 Role 403", code == 403, str(code))
    ok, (code, _) = call("GET", "/api/kg51/dashboard",
                         expect=(403,))
    record("看板无 Role 403", code == 403, str(code))
    ok, (code, _) = call("POST", "/api/kg51/feedback",
                         body={"turnId": "t-x",
                               "targetTriple": "a|b|c"},
                         expect=(401,))
    record("反馈无身份 401", code == 401, str(code))


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
