"""58号AI智能优化意图识别 P2 Docker 实机验收

运行方式:
    python verify_ii58_p2_live.py [基址]

前置: 容器已运行(含 58号 P2 代码)。

覆盖(58号计划 §九 P2, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(calibrate 409; thresholds
       观测面 200)
    03 容器内: 识别即合规全链(guest 越界拦截
       +member 放行+sensitive 二次确认+deny
       沙箱+clarify/partial 三态纯度)
    04 槽位上下文预填(指代消解+48号 turns
       纯读取零写入)
    05 阈值配置域闭环(calibrate→46号留痕→
       人工终审唯一生效出口→驳回轨)
    06 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
ii58+ai46 键域)。
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


def clear_ii58(round_no: int) -> None:
    redis_del_keys("zhuxiang:ii58:*")
    redis_del_keys("zhuxiang:ai46:*")
    redis_del_keys("zhuxiang:voice48:*")


# 容器内管道(纯 ASCII+中文 unicode 转义——编码防御)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['II58_MODE'] = 'shadow'\n"
    "from core.helpers import ts\n"
    "async def m():\n"
    "    out = {}\n"
    # 语料种子(合规域)
    "    from repositories.ii58_repository import "
    "Ii58Repository\n"
    "    repo = Ii58Repository()\n"
    "    async def seed(intent, text):\n"
    "        cid = await repo.next_corpus_id()\n"
    "        await repo.save_corpus({\n"
    "            'corpusId': cid, 'corpusVersion': 1,\n"
    "            'intentId': intent,\n"
    "            'sampleType': 'positive',\n"
    "            'text': text, 'weight': 1.0,\n"
    "            'source': 'manual', 'originRef': '',\n"
    "            'confusableTarget': None,\n"
    "            'humanVerified': True,\n"
    "            'humanSuggested': False,\n"
    "            'status': 'active',\n"
    "            'createdAt': ts(), 'updatedAt': ts()})\n"
    "    await seed('product.price_query', "
    "'how much')\n"
    "    await seed('trust.convert_intent', "
    "'convert')\n"
    "    await seed('boundary.unauthorized', "
    "'delete all data')\n"
    # partial 构造域(中文 unicode 转义)
    "    await seed('promo.query', "
    "'\\u4f18\\u60e0\\u591a\\u5c11')\n"
    "    await seed('promo.query', "
    "'\\u4f18\\u60e0\\u591a\\u5c11\\u5440')\n"
    "    await seed('promo.query', "
    "'\\u4f18\\u60e0\\u591a\\u5c11\\u5462')\n"
    "    await seed('product.price_query', "
    "'\\u591a\\u5c11\\u94b1')\n"
    # ① 识别即合规
    "    from services.ii58_service import "
    "Ii58Service\n"
    "    svc = Ii58Service()\n"
    "    r1 = await svc.evaluate('how much',\n"
    "                            member_role='guest')\n"
    "    c1 = r1.get('compliance') or {}\n"
    "    out['g_state'] = r1.get('state')\n"
    "    out['g_decision'] = c1.get('decision')\n"
    "    out['g_intercept'] = "
    "r1.get('boundaryIntercepted')\n"
    "    out['g_intent'] = r1.get('intentId')\n"
    "    out['g_original'] = "
    "c1.get('originalIntentId')\n"
    "    r2 = await svc.evaluate('how much',\n"
    "                            member_role='member')\n"
    "    out['m_decision'] = (r2.get('compliance') "
    "or {}).get('decision')\n"
    "    r3 = await svc.evaluate('convert',\n"
    "                            member_role='member')\n"
    "    c3 = r3.get('compliance') or {}\n"
    "    out['s_decision'] = c3.get('decision')\n"
    "    out['s_confirm'] = "
    "r3.get('requireConfirm')\n"
    "    r4 = await svc.evaluate('delete all data',\n"
    "                            member_role='admin')\n"
    "    out['d_decision'] = (r4.get('compliance') "
    "or {}).get('decision')\n"
    "    r5 = await svc.evaluate('xyzzyx',\n"
    "                            member_role='guest')\n"
    "    out['cl_state'] = r5.get('state')\n"
    "    out['cl_comp'] = 'compliance' in r5\n"
    "    r6 = await svc.evaluate("
    "'\\u4f18\\u60e0\\u591a\\u5c11\\u54c8',\n"
    "                            member_role='guest')\n"
    "    out['p_state'] = r6.get('state')\n"
    "    out['p_comp'] = 'compliance' in r6\n"
    # ② 槽位预填(48号 turn 种子+指代词)
    "    from repositories.xiaozhu_repository "
    "import Xiaozhu48Repository\n"
    "    xz = Xiaozhu48Repository()\n"
    "    sid = 77\n"
    "    seq = await xz.next_turn_seq(sid)\n"
    "    await xz.save_turn({\n"
    "        'turnId': 't-live', 'sessionId': sid,\n"
    "        'seq': seq, 'channel': 'text',\n"
    "        'audioMeta': {},\n"
    "        'rawText': 'see product', 'wake': True,\n"
    "        'intent': 'product.new',\n"
    "        'action': 'product.new', 'reply': 'ok',\n"
    "        'card': {'subject': "
    "'\\u98de\\u5929\\u8305\\u53f0'},\n"
    "        'jump': None, 'latencyMs': 100.0,\n"
    "        'ts': ts(), 'executed': True})\n"
    "    r7 = await svc.evaluate("
    "'\\u8fd9\\u4e2a\\u591a\\u5c11\\u94b1',\n"
    "                            session_id=sid)\n"
    "    out['kw'] = (r7.get('slots') "
    "or {}).get('keyword')\n"
    "    out['kw_src'] = ((r7.get('attribution') "
    "or {}).get(\n"
    "        'slotSources') or {}).get('keyword')\n"
    "    turns = await xz.list_turns(sid, limit=50)\n"
    "    out['turns_n'] = len(turns)\n"
    # ③ 阈值闭环
    "    from services.ai_governance_service "
    "import AiGovernanceService\n"
    "    gov = AiGovernanceService()\n"
    "    if await gov.repo.get_gov(\n"
    "            'intent_orchestration') is None:\n"
    "        await gov.sync_registry()\n"
    "    cal = await svc.calibrate(0.92, 0.72,\n"
    "                              'live P2')\n"
    "    out['cal_status'] = cal.get('status')\n"
    "    out['cal_cid'] = cal.get('changeId')\n"
    "    ev0 = await svc.evaluate('anything')\n"
    "    out['th_before'] = ((ev0.get('attribution')\n"
    "        or {}).get('thresholds') "
    "or {}).get('upper')\n"
    # 重复申请拒绝
    "    try:\n"
    "        await svc.calibrate(0.95, 0.75, 'dup')\n"
    "        out['dup_ok'] = False\n"
    "    except ValueError:\n"
    "        out['dup_ok'] = True\n"
    # off 态人工终审
    "    os.environ['II58_MODE'] = 'off'\n"
    "    rv = await svc.review_calibration(\n"
    "        int(cal.get('changeId')), approve=True)\n"
    "    out['rv_status'] = rv.get('status')\n"
    "    os.environ['II58_MODE'] = 'shadow'\n"
    "    ev1 = await svc.evaluate('anything')\n"
    "    out['th_after'] = ((ev1.get('attribution')\n"
    "        or {}).get('thresholds') "
    "or {}).get('upper')\n"
    # 驳回轨
    "    cal2 = await svc.calibrate(0.95, 0.75,\n"
    "                               'live reject')\n"
    "    os.environ['II58_MODE'] = 'off'\n"
    "    rv2 = await svc.review_calibration(\n"
    "        int(cal2.get('changeId')), approve=False)\n"
    "    os.environ['II58_MODE'] = 'shadow'\n"
    "    ev2 = await svc.evaluate('anything')\n"
    "    out['th_reject'] = ((ev2.get('attribution')\n"
    "        or {}).get('thresholds') "
    "or {}).get('upper')\n"
    "    out['rv2_status'] = rv2.get('status')\n"
    # 46号队列收口
    "    pend = await gov.repo.list_changes(\n"
    "        status='pending',\n"
    "        scorer_id='intent_orchestration')\n"
    "    out['pending_left'] = len(pend)\n"
    # 阈值全景
    "    view = await svc.thresholds_view()\n"
    "    b = view.get('baseline') or {}\n"
    "    out['view_upper'] = b.get('upper')\n"
    "    out['view_source'] = b.get('source')\n"
    "    out['by_tier'] = sorted(\n"
    "        (view.get('byTier') or {}).keys())\n"
    # 46号 change 留痕(payload)
    "    chs = await gov.repo.list_changes(\n"
    "        scorer_id='intent_orchestration')\n"
    "    cfg = [c for c in chs\n"
    "           if (c.get('payload') or {}).get(\n"
    "               'scope') == 'threshold_baseline']\n"
    "    out['cfg_n'] = len(cfg)\n"
    "    out['cfg_payload'] = bool(\n"
    "        (cfg[0].get('payload') or {}).get(\n"
    "            'after') or {}) if cfg else False\n"
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
    clear_ii58(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/ii58/threshold/calibrate",
        body={"upper": 0.92, "lower": 0.72,
              "reason": "off 测试"},
        headers=ADMIN, expect=(409,))
    record("off 态 calibrate 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/ii58/thresholds",
        headers=ADMIN)
    record("off 态 thresholds 观测面 200",
           code == 200
           and (body.get("baseline")
                or {}).get("source")
           == "code_default",
           str((code,
                (body.get("baseline")
                 or {}).get("source"))))

    print("\n[03-05 容器内: 合规+预填+阈值闭环]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    # ① 识别即合规
    record("guest 越界拦截(denied+改判)",
           r.get("g_decision") == "denied"
           and r.get("g_intercept") is True
           and r.get("g_intent")
           == "boundary.unauthorized",
           str((r.get("g_decision"),
                r.get("g_intent"))))
    record("归因保留原始意图",
           r.get("g_original")
           == "product.price_query",
           str(r.get("g_original")))
    record("member 放行(allow)",
           r.get("m_decision") == "allow",
           str(r.get("m_decision")))
    record("sensitive 二次确认(confirm)",
           r.get("s_decision")
           == "confirm_required"
           and r.get("s_confirm") is True,
           str((r.get("s_decision"),
                r.get("s_confirm"))))
    record("deny 沙箱(admin 亦拦截)",
           r.get("d_decision") == "denied",
           str(r.get("d_decision")))
    record("clarify 纯度(不校验权限)",
           r.get("cl_state") == "clarify"
           and r.get("cl_comp") is False,
           str((r.get("cl_state"),
                r.get("cl_comp"))))
    record("partial 纯度(不校验权限)",
           r.get("p_state") == "partial"
           and r.get("p_comp") is False,
           str((r.get("p_state"),
                r.get("p_comp"))))
    # ② 槽位预填
    record("指代词预填(上轮 card.subject)",
           r.get("kw") == "飞天茅台",
           str(r.get("kw")))
    record("预填来源标记(context_prefill)",
           (r.get("kw_src") or {}).get("source")
           == "context_prefill",
           str(r.get("kw_src")))
    record("48号 turns 纯读取(1 条保持)",
           r.get("turns_n") == 1,
           str(r.get("turns_n")))
    # ③ 阈值闭环
    record("校准申请(pending 不生效)",
           r.get("cal_status") == "pending"
           and r.get("th_before") == 0.9,
           str((r.get("cal_status"),
                r.get("th_before"))))
    record("重复申请拒绝(队列纪律)",
           r.get("dup_ok") is True,
           str(r.get("dup_ok")))
    record("off 态终审(active 唯一出口)",
           r.get("rv_status") == "active"
           and r.get("th_after") == 0.92,
           str((r.get("rv_status"),
                r.get("th_after"))))
    record("驳回轨(rejected 基线不变)",
           r.get("rv2_status") == "rejected"
           and r.get("th_reject") == 0.92,
           str((r.get("rv2_status"),
                r.get("th_reject"))))
    record("46号队列收口(无 pending)",
           r.get("pending_left") == 0,
           str(r.get("pending_left")))
    record("46号 config 留痕(payload 快照)",
           (r.get("cfg_n") or 0) >= 2
           and r.get("cfg_payload") is True,
           str((r.get("cfg_n"),
                r.get("cfg_payload"))))
    record("阈值全景(镜像基线+四 tier)",
           r.get("view_upper") == 0.92
           and r.get("view_source") == "mirror"
           and r.get("by_tier") == [
               "restricted", "standard",
               "trusted", "watched"],
           str((r.get("view_upper"),
                r.get("view_source"))))

    print("\n[06 HTTP 端点+鉴权]")
    ok, (code, body) = call(
        "GET", "/api/ii58/thresholds",
        headers=ADMIN)
    record("HTTP thresholds 200(镜像基线)",
           code == 200
           and (body.get("baseline")
                or {}).get("upper") == 0.92,
           str((code,
                (body.get("baseline")
                 or {}).get("upper"))))
    # 鉴权 403
    for method, path in (
            ("POST",
             "/api/ii58/threshold/calibrate"),
            ("GET", "/api/ii58/thresholds")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 13
    script = (
        "from routes.ii58_routes import router\n"
        "print(sum(1 for r in router.routes))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        count = int((out.stdout or "").strip())
    except ValueError:
        count = -1
    record("58号路由累计 13 端点",
           count == 13, str(count))


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
