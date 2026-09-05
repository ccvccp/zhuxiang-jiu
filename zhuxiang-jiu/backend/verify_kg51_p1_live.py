"""51号P1 证据链采集与抽取 Docker 实机验收

运行方式:
    python verify_kg51_p1_live.py [基址]

前置: 容器已运行(含 51号P1 代码, 镜像已重建)。

覆盖(51号计划 §八 P1, 真实容器):
    01 正常业务零影响(健康检查/35号面板/48号看板)
    02 off 铁律(HTTP 主进程——采集拒绝 409/观测空态)
    03 三源采集(容器内: 50号 events 种子+18号条款
       +48号 turns → 实体/三元组/证据链/分级)
    04 观测面(HTTP: 状态/三元组过滤/unverified 隔离)
    05 复核闭环(HTTP: 队列可见+裁决 approve→verified)
    06 鉴权(403 门槛)

每轮容器脚本自清理 kg51:* 与本轮种子(大 evId 偏移),
×2 轮幂等验证(两轮采集结果一致)。
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


def clear_kg51() -> None:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:kg51:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


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


def container_p1_check(round_no: int) -> dict:
    """容器内: 切 on+种子+三源采集+服务级断言"""
    base_ev = 99000 + round_no * 100
    sid = 99000 + round_no * 100
    agr = 99000 + round_no * 100
    script = (
        "import asyncio, json, os, uuid\n"
        "os.environ['KG_MODE'] = 'on'\n"
        "from repositories.backend import get_redis_client\n"
        "from repositories.voice50_repository import "
        "Voice50Repository\n"
        "from repositories.xiaozhu_repository import "
        "Xiaozhu48Repository\n"
        "from repositories.agreement_repository import "
        "AgreementRepository\n"
        "from repositories.kg51_repository import "
        "Kg51Repository\n"
        "from services.kg51_ingest_service import "
        "Kg51IngestService\n"
        "from core.helpers import ts as _ts\n"
        f"BASE_EV = {base_ev}\n"
        f"SID = {sid}\n"
        f"AGR = {agr}\n"
        "async def m():\n"
        "    out = {}\n"
        "    client = await get_redis_client()\n"
        "    for pat in ('zhuxiang:kg51:*',\n"
        "                'zhuxiang:voice50:voice50_events:99*',\n"
        "                'zhuxiang:voice48:voice48_turns:99*',\n"
        "                'zhuxiang:agreement:agreement:99*',\n"
        "                'zhuxiang:voice48:turns:99:*'):\n"
        "        keys = await client.keys(pat)\n"
        "        if keys:\n"
        "            await client.delete(*keys)\n"
        "    v50 = Voice50Repository()\n"
        "    specs = [\n"
        "        (BASE_EV+1, 9901, 'voice_polite', 'L2',\n"
        "         'ethics_evidence', 'settled'),\n"
        "        (BASE_EV+2, 9902, 'voice_login', 'L1',\n"
        "         '', 'settled'),\n"
        "        (BASE_EV+3, 9903, 'voice_community_qa',\n"
        "         'L3', 'longtail_good', 'pending'),\n"
        "    ]\n"
        "    for ev_id, mid, beh, layer, factor, st "
        "in specs:\n"
        "        rec = {'evId': ev_id, 'memberId': mid,\n"
        "               'sessionId': 0, 'turnSeq': 0,\n"
        "               'behavior': beh, 'layer': layer,\n"
        "               'voiceFactor':\n"
        "                   'voice_login' if layer == 'L1'"
        " else '',\n"
        "               'targetFactor': factor,\n"
        "               'voiceprintMode': 'proxy',\n"
        "               'baseScore': 1.0, 'multipliers': {},\n"
        "               'finalScore': 1.0, 'cappedScore': 1.0,\n"
        "               'overflowScore': 0.0, 'status': st,\n"
        "               'ref': 'voice50:%d' % ev_id,\n"
        "               'note': 'kg51-live',\n"
        "               'dayKey': '2026-09-05',\n"
        "               'ts': _ts()}\n"
        "        if st == 'settled':\n"
        "            rec['settledBatchId'] = 77\n"
        "        await v50.save_event(rec)\n"
        "    arepo = AgreementRepository()\n"
        "    for i, (st, no) in enumerate(\n"
        "            (('published', 'T-KG51-PUB'),\n"
        "             ('draft', 'T-KG51-DRF')), 1):\n"
        "        await arepo.save_agreement({\n"
        "            'id': AGR+i, 'agreementNo': no,\n"
        "            'name': 'live' + no, 'type': 'term',\n"
        "            'applicableRole': 'member',\n"
        "            'legalBasis': '', 'currentVersion':\n"
        "            'v1.0', 'content': '', 'changeLog': '',\n"
        "            'status': st, 'effectiveDate': None,\n"
        "            'versionHistory': [],\n"
        "            'createdAt': _ts(),\n"
        "            'updatedAt': _ts()})\n"
        "    xrepo = Xiaozhu48Repository()\n"
        "    turn_id = 't-' + uuid.uuid4().hex[:8]\n"
        "    await xrepo.save_turn({\n"
        "        'turnId': turn_id, 'sessionId': SID,\n"
        "        'seq': 1, 'channel': 'text', 'audioMeta': {},\n"
        "        'rawText': 'live(已脱敏)', 'wake': True,\n"
        "        'intent': 'product.query', 'action': None,\n"
        "        'reply': 'ok', 'card': {}, 'jump': None,\n"
        "        'latencyMs': 10.0, 'ts': _ts()})\n"
        "    svc = Kg51IngestService()\n"
        "    r = await svc.run_ingest()\n"
        "    out['sys'] = r['sources']['system']\n"
        "    out['auth'] = r['sources']['authority']\n"
        "    out['user'] = r['sources']['user']\n"
        "    repo = Kg51Repository()\n"
        "    ents = await repo.list_entities(limit=100000)\n"
        "    out['entCount'] = len(ents)\n"
        "    clauses = [e for e in ents\n"
        "               if e['entityType'] == 'PolicyClause']\n"
        "    out['pubClause'] = (len(clauses) == 1\n"
        "                        and clauses[0]['sourceRef']\n"
        "                        == 'agreement:%d' % (AGR+1))\n"
        "    answers = [e for e in ents\n"
        "               if e['entityType'] == 'VoiceAnswer']\n"
        "    out['userAnswer'] = (len(answers) == 1\n"
        "                         and 'rawText' not in\n"
        "                         answers[0]['attrs'])\n"
        "    triples = await repo.list_triples(limit=100000)\n"
        "    out['triCount'] = len(triples)\n"
        "    by = {}\n"
        "    for t in triples:\n"
        "        by[t['status']] = by.get(t['status'], 0) + 1\n"
        "    out['byStatus'] = by\n"
        "    l1_sub = 'ev:voice50:%d' % (BASE_EV+2)\n"
        "    out['l1NoMap'] = all(not (\n"
        "        t['subject'] == l1_sub and t['predicate']\n"
        "        == 'contributes_to_credit')\n"
        "        for t in triples)\n"
        "    att = [t for t in triples\n"
        "           if t['subject']\n"
        "           == 'ev:voice50:%d' % (BASE_EV+1)\n"
        "           and t['predicate'] == 'attested_by']\n"
        "    out['attested'] = (len(att) == 1 and\n"
        "                       att[0]['evidence'].get(\n"
        "                           'verifier') == 'settle')\n"
        "    r2 = await svc.run_ingest()\n"
        "    s2 = r2['sources']['system']\n"
        "    out['idempotent'] = (s2['entities'] == 0\n"
        "                         and s2['triples'] == 0)\n"
        "    evs = [e for e in await v50.list_events(\n"
        "        limit=100000)\n"
        "        if e['evId'] == BASE_EV+3]\n"
        "    evs[0]['status'] = 'settled'\n"
        "    evs[0]['settledBatchId'] = 88\n"
        "    await v50.save_event(evs[0])\n"
        "    r3 = await svc.run_ingest()\n"
        "    out['updated'] = (r3['sources']['system']\n"
        "                      ['updated'] >= 3)\n"
        "    up = await repo.list_triples(\n"
        "        subject='ev:voice50:%d' % (BASE_EV+3),\n"
        "        limit=10)\n"
        "    out['upVerified'] = all(\n"
        "        t['status'] == 'verified' for t in up)\n"
        "    trip3 = await repo.list_triples(\n"
        "        limit=100000)\n"
        "    by3 = {}\n"
        "    for t in trip3:\n"
        "        by3[t['status']] = by3.get(t['status'],\n"
        "                                   0) + 1\n"
        "    out['byStatusAfter'] = by3\n"
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

    print("\n[02 off 铁律(HTTP 主进程)]")
    ok, (code, body) = call("POST", "/api/kg51/ingest/run",
                            headers=ADMIN, body={},
                            expect=(409,))
    record("off 态采集拒绝(409)", code == 409, str(code))
    ok, (code, body) = call("GET", "/api/kg51/ingest/status",
                            headers=ADMIN)
    record("off 态观测空态",
           code == 200
           and (body.get("tripleCount") or 0) == 0,
           str(code))

    print("\n[03 三源采集(容器内)]")
    r = container_p1_check(round_no)
    record("系统源采集(scanned>=3)",
           (r.get("sys") or {}).get("scanned", 0) >= 3,
           str(r)[:120])
    record("实体规模(>=12)",
           r.get("entCount", 0) >= 12,
           str(r.get("entCount")))
    record("三元组规模(>=8)", r.get("triCount", 0) >= 8,
           str(r.get("triCount")))
    by = r.get("byStatus") or {}
    record("verified/unverified 分级(5/3)",
           by.get("verified") == 5
           and by.get("unverified") == 3, str(by))
    record("L1 不映射 45号因子(红线)",
           r.get("l1NoMap") is True)
    record("attested_by 证据链(settle verifier)",
           r.get("attested") is True, str(r.get("attested")))
    record("published 条款入库(draft 不入)",
           r.get("pubClause") is True)
    record("用户源候选(rawText 不入属性)",
           r.get("userAnswer") is True)
    record("重复采集幂等(entities=0/triples=0)",
           r.get("idempotent") is True)
    record("pending→settled 重采走更新",
           r.get("updated") is True)
    record("升级后三元组全 verified",
           r.get("upVerified") is True)
    by_after = r.get("byStatusAfter") or {}
    record("升级后 unverified 清零(5/3→8/0)",
           by_after.get("verified") == 8
           and by_after.get("unverified", 0) == 0,
           str(by_after))

    print("\n[04 观测面(HTTP)]")
    ok, (code, body) = call("GET", "/api/kg51/ingest/status",
                            headers=ADMIN)
    record("状态视图(tripleCount>0)",
           code == 200
           and (body.get("tripleCount") or 0) > 0,
           str(code))
    ok, (code, body) = call(
        "GET", "/api/kg51/triples?status=unverified",
        headers=ADMIN)
    unv = (body.get("triples") or [])
    record("unverified 视图一致(升级后空)",
           code == 200
           and len(unv) == 0
           and all(t.get("status") == "unverified"
                   for t in unv), str(len(unv)))
    ok, (code, body) = call(
        "GET", "/api/kg51/triples?predicate=attested_by",
        headers=ADMIN)
    record("predicate 过滤(attested_by)",
           code == 200
           and all(t.get("predicate") == "attested_by"
                   for t in (body.get("triples")
                             or [])), str(code))

    print("\n[05 复核闭环(HTTP)]")
    ok, (code, body) = call("GET", "/api/kg51/reviews",
                            headers=ADMIN)
    pending = [x for x in (body.get("reviews") or [])
               if x.get("status") == "pending"
               and "|" in (x.get("target") or "")]
    record("复核队列可见(三元组目标)",
           len(pending) >= 1, str(len(pending)))
    if pending:
        before = (body.get("byStatus")
                  or {}).get("approved") or 0
        ok, (code, dbody) = call(
            "POST",
            f"/api/kg51/reviews/"
            f"{pending[0]['reviewId']}/decide",
            headers=ADMIN,
            body={"approve": True,
                  "decisionNote": "实机采信"})
        record("裁决 approve→verified",
               code == 200
               and (dbody.get("flipped")
                    or {}).get("status") == "verified",
               str(code))
        ok, (code, tbody) = call(
            "GET", "/api/kg51/triples?status=verified",
            headers=ADMIN)
        ok2, (code2, body2) = call(
            "GET", "/api/kg51/reviews",
            headers=ADMIN)
        after = (body2.get("byStatus")
                 or {}).get("approved") or 0
        record("复核后 verified 增量(+1)",
               code == 200
               and after == before + 1,
               f"{before}->{after}")

    print("\n[06 鉴权]")
    ok, (code, _) = call("POST", "/api/kg51/ingest/run",
                        body={}, expect=(403,))
    record("ingest 无 Role 403", code == 403, str(code))
    ok, (code, _) = call("GET", "/api/kg51/triples",
                         expect=(403,))
    record("triples 无 Role 403", code == 403, str(code))


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
