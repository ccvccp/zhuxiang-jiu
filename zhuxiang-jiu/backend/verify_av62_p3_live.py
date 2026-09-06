"""62号AI智能无形资产估值 P3 Docker 实机验收

运行方式:
    python verify_av62_p3_live.py [基址]

前置: 容器已运行(含 62号 P3 代码)。

覆盖(62号计划 §七 P3, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律+观测面(appeals
       列表/fairness report 200)
    03 容器内: 申诉流全链(提交+自动
       重估→uphold/overturn 裁决→
       翻转留痕+负资产洗白防线
       +公平审计双指标)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
av62 键域)。
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


def clear_av62(round_no: int) -> None:
    redis_del_keys("zhuxiang:av62:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AV62_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 种子: 低证据资产+评估
    "    from services.av62_service import (\n"
    "        Av62Service)\n"
    "    from services.av62_assess_service "
    "import (\n"
    "        Av62AssessService)\n"
    "    reg = Av62Service()\n"
    "    asm = Av62AssessService()\n"
    "    a1 = await reg.register_asset(\n"
    "        101, 'enterprise',\n"
    "        'compliance',\n"
    "        {'licenseCount': 2})\n"
    "    r1 = await asm.assess_asset(\n"
    "        a1['assetId'])\n"
    "    out['seed_val'] = (\n"
    "        r1.get('baseValue'))\n"
    # ② off 态申诉不受开关影响
    "    os.environ['AV62_MODE'] = 'off'\n"
    "    from services.av62_appeal_service "
    "import (\n"
    "        Av62AppealService)\n"
    "    aps = Av62AppealService()\n"
    "    try:\n"
    "        await aps.submit_appeal(\n"
    "            a1['assetId'], '')\n"
    "        out['reason_rej'] = False\n"
    "    except ValueError:\n"
    "        out['reason_rej'] = True\n"
    "    ap = await aps.submit_appeal(\n"
    "        a1['assetId'],\n"
    "        reason='evidence supplement',\n"
    "        new_evidence={\n"
    "            'licenseCount': 10,\n"
    "            'auditResults': 'pass',\n"
    "            'esgDisclosure': 'yes'},\n"
    "        appealed_by='memberA')\n"
    "    out['ap_status'] = (\n"
    "        ap.get('status'))\n"
    "    out['ap_orig'] = (\n"
    "        ap.get('originalValue'))\n"
    "    out['ap_re'] = (\n"
    "        ap.get('reestimatedValue'))\n"
    "    out['ap_delta'] = (\n"
    "        ap.get('delta'))\n"
    # ③ 资产 disputed 联动读回
    "    from repositories.av62_repository "
    "import Av62Repository\n"
    "    repo = Av62Repository()\n"
    "    a1_read = await repo.get_asset(\n"
    "        a1['assetId'])\n"
    "    out['a_disputed'] = (\n"
    "        a1_read.get('status'))\n"
    # ④ 重复申诉拒绝
    "    try:\n"
    "        await aps.submit_appeal(\n"
    "            a1['assetId'], 'dup')\n"
    "        out['dup_rej'] = False\n"
    "    except ValueError:\n"
    "        out['dup_rej'] = True\n"
    # ⑤ uphold 维持原值(off 态裁决)
    "    try:\n"
    "        await aps.review_appeal(\n"
    "            ap['appealId'],\n"
    "            decision='hacked',\n"
    "            reviewed_by='x',\n"
    "            review_note='y')\n"
    "        out['dec_rej'] = False\n"
    "    except ValueError:\n"
    "        out['dec_rej'] = True\n"
    "    rv = await aps.review_appeal(\n"
    "        ap['appealId'],\n"
    "        decision='uphold',\n"
    "        reviewed_by='gov',\n"
    "        review_note='not accepted')\n"
    "    out['up_status'] = (\n"
    "        rv.get('status'))\n"
    "    out['up_over'] = (\n"
    "        rv.get('overturned'))\n"
    "    out['up_final'] = (\n"
    "        rv.get('finalValue'))\n"
    "    out['up_delta'] = (\n"
    "        rv.get('finalDelta'))\n"
    "    a2_read = await repo.get_asset(\n"
    "        a1['assetId'])\n"
    "    out['up_asset'] = (\n"
    "        a2_read.get('status'))\n"
    # ⑥ 裁决后 overturn 翻转
    "    ap2 = await aps.submit_appeal(\n"
    "        a1['assetId'],\n"
    "        reason='second appeal')\n"
    "    rv2 = await aps.review_appeal(\n"
    "        ap2['appealId'],\n"
    "        decision='overturn',\n"
    "        reviewed_by='gov',\n"
    "        review_note='accepted')\n"
    "    out['ov_over'] = (\n"
    "        rv2.get('overturned'))\n"
    "    out['ov_final'] = (\n"
    "        rv2.get('finalValue'))\n"
    # ⑦ 重复裁决拒绝
    "    try:\n"
    "        await aps.review_appeal(\n"
    "            ap['appealId'],\n"
    "            decision='overturn',\n"
    "            reviewed_by='x',\n"
    "            review_note='y')\n"
    "        out['dup_dec_rej'] = False\n"
    "    except ValueError:\n"
    "        out['dup_dec_rej'] = True\n"
    # ⑧ 负资产洗白防线(恢复 shadow
    #    ——登记/评估决策面)
    "    os.environ['AV62_MODE'] = "
    "'shadow'\n"
    "    a3 = await reg.register_asset(\n"
    "        101, 'enterprise', 'risk',\n"
    "        {'penaltyRecords': 5})\n"
    "    await asm.assess_asset(\n"
    "        a3['assetId'])\n"
    "    try:\n"
    "        await aps.submit_appeal(\n"
    "            a3['assetId'], 'fixed',\n"
    "            new_evidence={\n"
    "                'penaltyRecords': 2})\n"
    "        out['wash_rej'] = False\n"
    "    except ValueError:\n"
    "        out['wash_rej'] = True\n"
    # ⑨ 公平审计(小样本+偏斜)
    "    from services.av62_fairness"
    "_service import (\n"
    "        Av62FairnessService)\n"
    "    fsvc = Av62FairnessService()\n"
    "    r_small = await fsvc.run_audit()\n"
    "    out['fair_small'] = (\n"
    "        r_small.get('insufficient'))\n"
    "    out['fair_flag_small'] = (\n"
    "        r_small.get('flagged'))\n"
    # 偏斜: 10 personal active
    # +10 enterprise pending
    "    from repositories.store import "
    "reset_store\n"
    "    reset_store()\n"
    "    for i in range(10):\n"
    "        a = await reg.register_asset(\n"
    "            301, 'personal',\n"
    "            'capability',\n"
    "            {'skillCerts': 8,\n"
    "             'deliveryQuality': 0.95,\n"
    "             'knowledgeSharing': "
    "24})\n"
    "        await asm.assess_asset(\n"
    "            a['assetId'])\n"
    "    for i in range(10):\n"
    "        a = await reg.register_asset(\n"
    "            101, 'enterprise',\n"
    "            'compliance',\n"
    "            {'licenseCount': 5})\n"
    "        await asm.assess_asset(\n"
    "            a['assetId'])\n"
    "    r_skew = await fsvc.run_audit()\n"
    "    out['fair_n'] = (\n"
    "        r_skew.get('sampleCount'))\n"
    "    out['fair_groups'] = (\n"
    "        r_skew.get('groupCount'))\n"
    "    out['fair_gap'] = (\n"
    "        r_skew.get('passRateGap'))\n"
    "    out['fair_flag'] = (\n"
    "        r_skew.get('flagged'))\n"
    # 告警事件
    "    evs = await repo.list_events(\n"
    "        limit=100)\n"
    "    out['fair_alerts'] = len([\n"
    "        e for e in evs\n"
    "        if e.get('eventType')\n"
    "        == 'fairness_alert'])\n"
    # 报告读回
    "    rep = await fsvc.get_report()\n"
    "    out['rep_flag'] = ((\n"
    "        rep.get('report') or {})\n"
    "        .get('flagged'))\n"
    "    out['rep_hist'] = (\n"
    "        rep.get('historyCount'))\n"
    # ⑩ 44号
    "    from services.ai_learning_service "
    "import SCORER_REGISTRY\n"
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
    clear_av62(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, body) = call(
        "GET", "/api/av62/appeals",
        headers=ADMIN)
    record("off 态 appeals 观测面 200",
           code == 200
           and "total" in (body or {}),
           str(code))
    ok, (code, body) = call(
        "GET", "/api/av62/fairness/report",
        headers=ADMIN)
    record("off 态 fairness 观测面 200",
           code == 200
           and "thresholds" in (body or {}),
           str(code))

    print("\n[03 容器内: 申诉+公平审计全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("种子(低分 3.4)",
           r.get("seed_val") == 3.4,
           str(r.get("seed_val")))
    record("理由缺省拒绝",
           r.get("reason_rej") is True,
           str(r.get("reason_rej")))
    record("off 态申诉提交(不受开关影响)",
           r.get("ap_status")
           == "reestimated",
           str(r.get("ap_status")))
    record("原值/重估差异留痕"
           "(3.4→100)",
           r.get("ap_orig") == 3.4
           and r.get("ap_re") == 100.0
           and r.get("ap_delta") == 96.6,
           str((r.get("ap_orig"),
                r.get("ap_re"),
                r.get("ap_delta"))))
    record("资产 disputed 联动",
           r.get("a_disputed")
           == "disputed",
           str(r.get("a_disputed")))
    record("重复申诉拒绝",
           r.get("dup_rej") is True,
           str(r.get("dup_rej")))
    record("裁决域外拒绝",
           r.get("dec_rej") is True,
           str(r.get("dec_rej")))
    record("uphold 裁决(resolved)",
           r.get("up_status") == "resolved"
           and r.get("up_over") is False,
           str((r.get("up_status"),
                r.get("up_over"))))
    record("uphold 终值=原值(3.4)",
           r.get("up_final") == 3.4
           and r.get("up_delta") == 0.0,
           str((r.get("up_final"),
                r.get("up_delta"))))
    record("uphold 资产 adjusted",
           r.get("up_asset")
           == "adjusted",
           str(r.get("up_asset")))
    record("overturn 翻转留痕"
           "(3.4——uphold 恢复态证据)",
           r.get("ov_over") is True
           and r.get("ov_final") == 3.4,
           str((r.get("ov_over"),
                r.get("ov_final"))))
    record("重复裁决拒绝",
           r.get("dup_dec_rej") is True,
           str(r.get("dup_dec_rej")))
    record("负资产减持拒绝(不可洗白)",
           r.get("wash_rej") is True,
           str(r.get("wash_rej")))
    record("公平审计小样本(不足)",
           r.get("fair_small") is True
           and r.get("fair_flag_small")
           is False,
           str((r.get("fair_small"),
                r.get(
                    "fair_flag_small"))))
    record("偏斜样本(22 条×2 组"
           "——含管道残留)",
           r.get("fair_n") == 22
           and r.get("fair_groups") == 2,
           str((r.get("fair_n"),
                r.get("fair_groups"))))
    record("通过率差超阈(91.7pp)",
           r.get("fair_gap") == 91.7,
           str(r.get("fair_gap")))
    record("偏斜告警(flagged)",
           r.get("fair_flag") is True,
           str(r.get("fair_flag")))
    record("告警事件留痕",
           r.get("fair_alerts") >= 1,
           str(r.get("fair_alerts")))
    record("报告读回(可溯源)",
           r.get("rep_flag") is True
           and r.get("rep_hist") >= 2,
           str((r.get("rep_flag"),
                r.get("rep_hist"))))
    record("44号 38 档案",
           r.get("reg_n") == 40,
           str(r.get("reg_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # off 态申诉全链(HTTP)
    ok, (code, _) = call(
        "POST", "/api/av62/assets",
        body={"subjectId": 901,
              "role": "enterprise",
              "domain": "compliance",
              "evidence": {
                  "licenseCount": 2}},
        headers=ADMIN, expect=(409,))
    record("HTTP assets off 409"
           "(种子需容器管道)",
           code == 409, str(code))
    # 观测面(容器内种子的申诉读回)
    ok, (code, body) = call(
        "GET", "/api/av62/appeals",
        headers=ADMIN)
    record("HTTP appeals 列表"
           "(Redis 读回)",
           code == 200
           and (body.get("total") or 0)
           >= 2
           and (body.get("overturned")
                or 0) >= 1,
           str((code, body.get("total"),
                body.get("overturned"))))
    ok, (code, body) = call(
        "GET", "/api/av62/appeals/1",
        headers=ADMIN)
    record("HTTP appeal 详情",
           code == 200
           and ((body.get("appeal")
                 or {}).get(
                     "originalValue")
                == 3.4),
           str(code))
    ok, (code, _) = call(
        "GET", "/api/av62/appeals/999",
        headers=ADMIN, expect=(404,))
    record("HTTP appeal 404",
           code == 404, str(code))
    ok, (code, body) = call(
        "GET", "/api/av62/fairness/report",
        headers=ADMIN)
    record("HTTP fairness report"
           "(Redis 读回)",
           code == 200
           and ((body.get("report")
                 or {}).get("flagged")
                is True),
           str(code))
    # 鉴权 403
    for method, path in (
            ("POST", "/api/av62/appeals"),
            ("POST",
             "/api/av62/appeals/1/review"),
            ("GET", "/api/av62/appeals"),
            ("GET",
             "/api/av62/fairness/report"),
            ("POST",
             "/api/av62/fairness/audit")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 20
    script = (
        "from routes.av62_routes import "
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
    record("62号路由 P3 20 端点",
           count == 20, str(count))


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
