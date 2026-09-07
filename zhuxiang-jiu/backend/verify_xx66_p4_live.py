"""66号·AI智能工程师大模块 P4 知识进化+看板
+自助+红队 Docker 实机验收(verify_xx66_p4_live)

运行方式:
    python verify_xx66_p4_live.py [基址]

前置: 容器已运行(含 66号 P4 代码
      ——XX66_MODE 默认 off)。

覆盖(真实容器 Redis 态):
    01 健康面+status(回归)
    02 决策面 off 409(cases/redteam)
       + 鉴权 403
    03 观测面(cases/search 空态/
       dashboard 四区/proactive 兜底)
    04 案例库管道(容器内: 四要素质量门
       +PII 脱敏+余弦去重+检索命中
       +缺口回写+归档)
    05 回流闭环(容器内: chat+settle→44号
       反馈+heal executed→44号反馈)
    06 红队七向量(容器内: 7/7 全防住
       +种子自清理)

×2 轮幂等验证(每轮清理 xx66/trust45 种子域)。
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
        with urllib.request.urlopen(req, timeout=30) as r:
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


def clear_xx66(round_no: int) -> None:
    print(f"\n—— 第 {round_no} 轮: 清理种子域 ——")
    redis_del_keys("zhuxiang:xx66:*")
    redis_del_keys("zhuxiang:trust45:*")


def run_pipeline(script: str) -> dict:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1",
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr
                          or "无输出")[-1500:]}


# ------------------------------------------------------------
# P4 管道(容器内)
# ------------------------------------------------------------
P4_PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['XX66_MODE'] = 'shadow'\n"
    "os.environ['XX66_LLM_MODE'] = 'off'\n"
    "os.environ['LLM_ENABLED'] = 'off'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.xx66_knowledge_service"
    " import (\n"
    "        Xx66KnowledgeService)\n"
    "    from repositories.xx66_repository"
    " import (\n"
    "        Xx66Repository)\n"
    "    svc = Xx66KnowledgeService()\n"
    "    repo = Xx66Repository()\n"
    # -- A 案例库质量门 --
    "    try:\n"
    "        await svc.create_case(\n"
    "            '', 'r', 's', 'o')\n"
    "        out['gate_empty'] = False\n"
    "    except ValueError:\n"
    "        out['gate_empty'] = True\n"
    "    try:\n"
    "        await svc.create_case(\n"
    "            'q', 'r', '补偿 99 TV', 'o',\n"
    "            value_linked=True)\n"
    "        out['gate_num'] = False\n"
    "    except ValueError:\n"
    "        out['gate_num'] = True\n"
    "    r_pii = await svc.create_case(\n"
    "        '手机 13812345678 无法支付',\n"
    "        '根因', '方案', '效果')\n"
    "    pii = await repo.get_case(\n"
    "        r_pii['caseId'])\n"
    "    out['pii_masked'] = (\n"
    "        '13812345678' not in pii['problem'])\n"
    # -- B 余弦去重 --
    "    r_a = await svc.create_case(\n"
    "        '支付失败怎么办请尽快处理',\n"
    "        '渠道超时', '更换支付方式', '已解决')\n"
    "    r_b = await svc.create_case(\n"
    "        '支付失败怎么办请尽快处理下',\n"
    "        '渠道超时', '更换支付方式', '已解决')\n"
    "    out['dedup_merged'] = r_b['merged']\n"
    "    out['dedup_recurrence'] = (\n"
    "        r_b['recurrence'])\n"
    # -- C 检索+缺口 --
    "    s = await svc.search_cases('支付失败')\n"
    "    out['search_hits'] = s['hitCount'] >= 1\n"
    "    out['search_sim'] = (\n"
    "        s['hits'][0]['similarity'] > 0)\n"
    "    s2 = await svc.search_cases(\n"
    "        '完全无关的查询xyzq')\n"
    "    out['gap_recorded'] = s2['gapRecorded']\n"
    # -- D 归档 --
    "    stale_id = await repo.next_case_id()\n"
    "    await repo.save_case({\n"
    "        'caseId': stale_id,\n"
    "        'problem': '陈年案例',\n"
    "        'rootCause': '旧', 'solution': '旧方案',\n"
    "        'outcome': '旧效果', 'source': 'manual',\n"
    "        'valueLinked': False, 'confidence': 0.5,\n"
    "        'recurrence': 0, 'hitCount': 0,\n"
    "        'status': 'active',\n"
    "        'createdAt': '2020-01-01T00:00:00',\n"
    "        'lastHitAt': ''})\n"
    "    arch = await svc.archive_stale()\n"
    "    out['archived'] = (\n"
    "        stale_id in arch['archived'])\n"
    # -- E 看板四区 --
    "    d = await svc.dashboard()\n"
    "    out['dash_zones'] = sorted(\n"
    "        d['zones'].keys())\n"
    "    out['dash_no_error'] = all(\n"
    "        'error' not in z\n"
    "        for z in d['zones'].values())\n"
    "    out['dash_case_total'] = (\n"
    "        d['zones']['knowledge']['caseTotal'])\n"
    # -- F 回流闭环: chat+settle → 44号 --
    "    os.environ['XX66_MODE'] = 'shadow'\n"
    "    from services.xx66_support_service"
    " import (\n"
    "        Xx66SupportService)\n"
    "    ss = Xx66SupportService()\n"
    "    r = await ss.chat('怎么修改头像')\n"
    "    await ss.settle(r['statId'], 5)\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    fbs = await AiLearningRepository() \\\n"
    "        .list_feedback('engineer_service')\n"
    "    out['settle_fb'] = any(\n"
    "        f'support stat {r[\"statId\"]}'\n"
    "        in str(f.get('note') or '')\n"
    "        for f in fbs)\n"
    "    out['settle_correct'] = any(\n"
    "        f'support stat {r[\"statId\"]}'\n"
    "        in str(f.get('note') or '')\n"
    "        and f.get('correct') is True\n"
    "        for f in fbs)\n"
    # -- G heal executed → 44号+案例沉淀 --
    "    os.environ['XX66_MODE'] = 'assist'\n"
    "    from services.maintenance_service"
    " import (\n"
    "        MaintenanceService)\n"
    "    from services.xx66_heal_service"
    " import (\n"
    "        Xx66HealService)\n"
    "    msvc = MaintenanceService()\n"
    "    rec = await msvc.detect_fault(\n"
    "        fault_type='disk_full',\n"
    "        fault_source='db-live-p4',\n"
    "        recovery_level='auto')\n"
    "    h = await Xx66HealService().heal(\n"
    "        rec['id'])\n"
    "    out['heal_route'] = h['route']\n"
    "    fbs2 = await AiLearningRepository() \\\n"
    "        .list_feedback('engineer_service')\n"
    "    out['heal_fb'] = any(\n"
    "        f'heal {rec[\"id\"]}'\n"
    "        in str(f.get('note') or '')\n"
    "        for f in fbs2)\n"
    "    out['heal_correct'] = any(\n"
    "        f'heal {rec[\"id\"]}'\n"
    "        in str(f.get('note') or '')\n"
    "        and f.get('correct') is True\n"
    "        for f in fbs2)\n"
    "    final = await msvc.get_recovery(\n"
    "        rec['id'])\n"
    "    cp = await svc.settle_case_from_recovery(\n"
    "        final)\n"
    "    out['recovery_case'] = cp['success']\n"
    # -- H 红队七向量 --
    "    os.environ['XX66_MODE'] = 'shadow'\n"
    "    rt = await svc.run_redteam()\n"
    "    out['rt_total'] = rt['total']\n"
    "    out['rt_defended'] = rt['allDefended']\n"
    "    out['rt_vectors'] = [v['vector']\n"
    "        for v in rt['vectors']]\n"
    "    cases_all = await repo.list_cases(\n"
    "        limit=500)\n"
    "    out['rt_clean'] = not any(\n"
    "        '投毒' in str(c.get('problem'))\n"
    "        or '13812345678' in str(\n"
    "            c.get('problem'))\n"
    "        for c in cases_all)\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n"
)


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮\n{'=' * 62}")
    clear_xx66(round_no)

    print("\n[01 健康面+status]")
    ok, (code, _) = call(
        "GET", "/api/decision/health")
    record("健康面 200", ok, str(code))
    ok, (code, _) = call(
        "GET", "/api/xx66/status", headers=ADMIN)
    record("status 200", ok, str(code))

    print("\n[02 决策面 off+鉴权]")
    for path, body in (
            ("/api/xx66/cases",
             {"problem": "q", "rootCause": "r",
              "solution": "s", "outcome": "o"}),
            ("/api/xx66/redteam", None)):
        ok, (code, _) = call(
            "POST", path, body=body,
            headers=ADMIN, expect=(409,))
        record(f"{path.split('/')[-1]} off 409",
               code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/xx66/cases",
        body={"problem": "q", "rootCause": "r",
              "solution": "s", "outcome": "o"})
    record("cases 无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/xx66/redteam")
    record("redteam 无 Role 403",
           code == 403, str(code))

    print("\n[03 观测面(空态)]")
    from urllib.parse import quote
    ok, (code, rep) = call(
        "GET", "/api/xx66/cases/search?q="
        + quote("支付"),
        headers=ADMIN)
    record("search 200", ok, str(code))
    if ok:
        record("空态零命中",
               rep.get("hitCount") == 0)
    ok, (code, rep) = call(
        "GET", "/api/xx66/cases", headers=ADMIN)
    record("案例列表 200", ok, str(code))
    ok, (code, rep) = call(
        "GET", "/api/xx66/dashboard", headers=ADMIN)
    record("dashboard 200", ok, str(code))
    if ok:
        record("看板四区齐备",
               set(rep.get("zones", {}).keys()) == {
                   "vitals", "service",
                   "knowledge", "trust"},
               str(list(rep.get("zones",
                                {}).keys())))
    ok, (code, rep) = call(
        "GET", "/api/xx66/proactive/999999",
        headers=ADMIN)
    record("proactive 兜底 200", ok, str(code))
    if ok:
        record("无档案兜底建议",
               rep.get("success") is True
               and len(rep.get("suggestions")
                       or []) >= 1)

    print("\n[04-06 管道(容器内)]")
    r = run_pipeline(P4_PIPELINE)
    if "error" in r:
        record("P4 管道成功", False, str(r)[:150])
    else:
        record("P4 管道成功", True)
        # 质量门
        record("缺要素拒绝",
               r.get("gate_empty") is True)
        record("valueLinked 数字红线",
               r.get("gate_num") is True)
        record("PII 脱敏入库",
               r.get("pii_masked") is True)
        # 去重
        record("相似案例合并",
               r.get("dedup_merged") is True)
        record("复发计数+1",
               r.get("dedup_recurrence") == 1,
               str(r.get("dedup_recurrence")))
        # 检索
        record("检索命中",
               r.get("search_hits") is True)
        record("相似度>0",
               r.get("search_sim") is True)
        record("缺口回写 57号",
               r.get("gap_recorded") is True)
        record("陈年案例归档",
               r.get("archived") is True)
        # 看板
        record("看板四区(容器)",
               r.get("dash_zones") == [
                   "knowledge", "service",
                   "trust", "vitals"],
               str(r.get("dash_zones")))
        record("看板 fail-soft",
               r.get("dash_no_error") is True)
        record("看板案例计数",
               isinstance(
                   r.get("dash_case_total"), int)
               and r.get("dash_case_total") >= 1)
        # 回流闭环
        record("settle 反馈落 44号",
               r.get("settle_fb") is True)
        record("settle 正确性标注",
               r.get("settle_correct") is True)
        record("heal executed 路由",
               r.get("heal_route") == "executed",
               str(r.get("heal_route")))
        record("heal 反馈落 44号",
               r.get("heal_fb") is True)
        record("heal 正确性标注",
               r.get("heal_correct") is True)
        record("自愈终态案例沉淀",
               r.get("recovery_case") is True)
        # 红队
        record("红队七向量齐备",
               r.get("rt_total") == 7
               and r.get("rt_vectors")
               == [f"RT-0{i}" for i in
                   range(1, 8)],
               str(r.get("rt_vectors")))
        record("红队全防住(7/7)",
               r.get("rt_defended") is True)
        record("红队种子自清理",
               r.get("rt_clean") is True)

    print("\n[07 HTTP 观测面复核]")
    ok, (code, rep) = call(
        "GET", "/api/xx66/dashboard", headers=ADMIN)
    if ok:
        record("看板知识区计数",
               rep.get("zones", {})
               .get("knowledge", {})
               .get("caseTotal", 0) >= 1,
               str(rep.get("zones", {})
                   .get("knowledge", {})
                   .get("caseTotal")))
    ok, (code, rep) = call(
        "GET", "/api/xx66/cases/search?q="
        + quote("支付失败"),
        headers=ADMIN)
    if ok:
        record("检索命中(HTTP)",
               rep.get("hitCount", 0) >= 1,
               str(rep.get("hitCount")))


def main() -> int:
    print("=" * 62)
    print("66号·AI智能工程师 P4 知识进化+看板"
          "+自助+红队 Docker 实机验收")
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
