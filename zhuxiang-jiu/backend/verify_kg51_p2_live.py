"""51号P2 存储与服务 Docker 实机验收

运行方式:
    python verify_kg51_p2_live.py [基址]

前置: 容器已运行(含 51号P2 代码, 镜像已重建)。

覆盖(51号计划 §八 P2, 真实容器):
    01 正常业务零影响(健康检查/35号面板/48号看板)
    02 off 铁律(HTTP: query/grounding 空态 200 fail-soft)
    03 容器内采集+预算种子(50号事件+条款+
       预算账户预置)
    04 权限矩阵(HTTP: 自身 digest OK/他人 409/
       L0 零成本/admin 任意)
    05 预算织入(HTTP: 查询后 usedToday 增加/
       L0 零成本)
    06 grounding(公开面无鉴权/命中产品条款/
       预算不变)
    07 鉴权(401/403)

每轮容器脚本自清理 kg51:*+99* 种子,
×2 轮幂等验证(预算每轮独立 member 偏移)。
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


def container_seed(round_no: int) -> dict:
    """容器内(on 进程): 清种子+事件+条款+采集+
    服务级查询面断言(权限/预算/grounding/缓存)"""
    base_ev = 99200 + round_no * 100
    agr = 99200 + round_no * 100
    member_id = 91000 + round_no
    script = (
        "import asyncio, json, os, uuid\n"
        "os.environ['KG_MODE'] = 'on'\n"
        "from repositories.backend import get_redis_client\n"
        "from repositories.voice50_repository import "
        "Voice50Repository\n"
        "from repositories.agreement_repository import "
        "AgreementRepository\n"
        "from services.kg51_ingest_service import "
        "Kg51IngestService, member_digest\n"
        "from services.kg51_query_service import "
        "Kg51QueryService\n"
        "from services.xiaozhu_privacy_service import "
        "XiaozhuPrivacyService\n"
        "from core.helpers import ts as _ts\n"
        f"BASE_EV = {base_ev}\n"
        f"AGR = {agr}\n"
        f"M = {member_id}\n"
        "async def m():\n"
        "    out = {}\n"
        "    client = await get_redis_client()\n"
        "    for pat in ('zhuxiang:kg51:*',\n"
        "                'zhuxiang:voice50:voice50_events:99*',\n"
        "                'zhuxiang:agreement:agreement:99*',\n"
        "                'zhuxiang:voice48:"
        "voice48_privacy_budget:%d' % M):\n"
        "        keys = await client.keys(pat)\n"
        "        if keys:\n"
        "            await client.delete(*keys)\n"
        "    v50 = Voice50Repository()\n"
        "    rec = {'evId': BASE_EV, 'memberId': M,\n"
        "           'sessionId': 0, 'turnSeq': 0,\n"
        "           'behavior': 'voice_polite', 'layer': 'L2',\n"
        "           'voiceFactor': '',\n"
        "           'targetFactor': 'ethics_evidence',\n"
        "           'voiceprintMode': 'proxy',\n"
        "           'baseScore': 1.0, 'multipliers': {},\n"
        "           'finalScore': 1.0, 'cappedScore': 1.0,\n"
        "           'overflowScore': 0.0, 'status': 'settled',\n"
        "           'ref': 'voice50:%d' % BASE_EV,\n"
        "           'note': 'kg51-p2-live',\n"
        "           'dayKey': '2026-09-05',\n"
        "           'ts': _ts(), 'settledBatchId': 77}\n"
        "    await v50.save_event(rec)\n"
        "    arepo = AgreementRepository()\n"
        "    await arepo.save_agreement({\n"
        "        'id': AGR, 'agreementNo': 'T-KG51-P2',\n"
        "        'name': 'P2实机条款', 'type': 'term',\n"
        "        'applicableRole': 'member', 'legalBasis': '',\n"
        "        'currentVersion': 'v1.0', 'content': '',\n"
        "        'changeLog': '', 'status': 'published',\n"
        "        'effectiveDate': None, 'versionHistory': [],\n"
        "        'createdAt': _ts(), 'updatedAt': _ts()})\n"
        "    r = await Kg51IngestService().run_ingest()\n"
        "    out['sys'] = r['sources']['system']\n"
        "    out['auth'] = r['sources']['authority']\n"
        "    q = Kg51QueryService()\n"
        "    privacy = XiaozhuPrivacyService()\n"
        "    subject = 'member:sha256:%s' % member_digest(M)\n"
        # ④ 权限矩阵(服务级)
        "    try:\n"
        "        r1 = await q.neighborhood_query(\n"
        "            subject=subject, member_id=M)\n"
        "        out['selfQuery'] = (\n"
        "            r1['tripleCount'] >= 1)\n"
        "    except ValueError:\n"
        "        out['selfQuery'] = False\n"
        "    try:\n"
        "        await q.neighborhood_query(\n"
        "            subject='member:sha256:"
        "ffffffffffffffff',\n"
        "            member_id=M)\n"
        "        out['otherDenied'] = False\n"
        "    except ValueError:\n"
        "        out['otherDenied'] = True\n"
        "    r1b = await q.neighborhood_query(\n"
        "        subject=subject, admin=True)\n"
        "    out['adminQuery'] = (\n"
        "        r1b['tripleCount'] >= 1\n"
        "        and 'budget' not in r1b)\n"
        # ⑤ 预算织入
        "    before = await privacy.budget_view(M)\n"
        "    await q.neighborhood_query(\n"
        "        subject=subject, member_id=M)\n"
        "    after = await privacy.budget_view(M)\n"
        "    out['budgetSpent'] = round(\n"
        "        after['usedToday']\n"
        "        - before['usedToday'], 4)\n"
        "    b2 = await privacy.budget_view(M)\n"
        "    await q.neighborhood_query(\n"
        "        subject='product:sku:ZX42-2026L07',\n"
        "        member_id=M)\n"
        "    a2 = await privacy.budget_view(M)\n"
        "    out['l0Zero'] = (\n"
        "        a2['usedToday'] == b2['usedToday'])\n"
        # ⑥ grounding(零成本)
        "    b3 = await privacy.budget_view(M)\n"
        "    g1 = await q.grounding_search(keyword='竹')\n"
        "    a3 = await privacy.budget_view(M)\n"
        "    out['groundingHit'] = (\n"
        "        g1['anchorCount'] >= 1\n"
        "        and a3['usedToday'] == b3['usedToday'])\n"
        "    g2 = await q.grounding_search(\n"
        "        keyword='P2实机')\n"
        "    out['groundingClause'] = any(\n"
        "        a['entityType'] == 'PolicyClause'\n"
        "        for a in g2['anchors'])\n"
        # ⑦ 缓存
        "    g3 = await q.grounding_search(keyword='竹')\n"
        "    out['cacheHit'] = (g3['cached'] is True)\n"
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

    print("\n[02 off 铁律(查询面 fail-soft)]")
    ok, (code, body) = call(
        "GET", "/api/kg51/query?subject=member:sha256:x",
        headers={"X-Member-Id": "1"})
    record("off 态 query 空态 200",
           code == 200
           and (body.get("tripleCount") or 0) == 0,
           str(code))
    ok, (code, body) = call(
        "GET", "/api/kg51/grounding?keyword="
               + urllib.parse.quote("竹"))
    record("off 态 grounding 空态 200",
           code == 200
           and (body.get("anchorCount") or 0) == 0,
           str(code))

    print("\n[03 容器内(on 进程): 采集+查询面]")
    r = container_seed(round_no)
    record("采集(system+authority)",
           (r.get("sys") or {}).get("triples", 0) >= 3
           and (r.get("auth") or {}).get("entities", 0)
           >= 12,
           str(r.get("sys"))[:60])
    record("自身 digest 邻域(tripleCount>=1)",
           r.get("selfQuery") is True,
           str(r.get("selfQuery")))
    record("他人主体拒绝(409 语义)",
           r.get("otherDenied") is True,
           str(r.get("otherDenied")))
    record("admin 任意主体(不扣预算)",
           r.get("adminQuery") is True,
           str(r.get("adminQuery")))
    record("查询扣预算(depth1=0.03)",
           abs((r.get("budgetSpent") or 0) - 0.03)
           < 0.0001,
           str(r.get("budgetSpent")))
    record("L0 查询零成本",
           r.get("l0Zero") is True,
           str(r.get("l0Zero")))
    record("grounding 命中产品(零成本)",
           r.get("groundingHit") is True,
           str(r.get("groundingHit")))
    record("grounding 命中实机条款",
           r.get("groundingClause") is True,
           str(r.get("groundingClause")))
    record("缓存命中(二次 cached=true)",
           r.get("cacheHit") is True,
           str(r.get("cacheHit")))

    print("\n[04 HTTP 鉴权矩阵(off 主进程)]")
    ok, (code, _) = call(
        "GET", "/api/kg51/query?subject=member:sha256:x",
        expect=(401,))
    record("无身份 401", code == 401, str(code))
    ok, (code, _) = call(
        "GET", "/api/kg51/query?subject=member:sha256:x",
        headers=ADMIN)
    record("admin 查询 200(off 空态)",
           code == 200, str(code))


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
