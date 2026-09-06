"""65号网店及商品AI智能管理 P1 Docker
实机验收(AI 内容工坊)

运行方式:
    python verify_xx65_p1_live.py [基址]

前置: 容器已运行(含 65号 P1 代码)。

覆盖(65号计划 §八 P1, 真实容器
Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(draft 409;
       registry 观测面 200)
    03 容器内: 内容工坊全链
       (禁词替换+发布流+
        人工兜底+下单窗口+
        巡检——三道防线)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子
重造——xx65+trust45 种子键域)。
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
MEMBER = {"X-Role": "member"}

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


def clear_xx65(round_no: int) -> None:
    redis_del_keys("zhuxiang:xx65:*")
    # 45号测试档案种子(仅测试主体
    # 9901 隔离域+idmap)
    redis_del_keys(
        "zhuxiang:trust45:trust45_profiles:"
        "9901")
    redis_del_keys(
        "zhuxiang:trust45:idmap:"
        "digest-99901")
    # 23号信用种子
    redis_del_keys(
        "zhuxiang:credit:score:9901")


# 容器内管道(纯 ASCII——中文经
# \u 转义)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['XX65_MODE'] = 'assist'\n"
    "os.environ['XX65_LLM_MODE'] = 'off'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 45号档案种子(余额 1000)
    "    from repositories.trust_value"
    "_repository import (\n"
    "        TrustValue45Repository)\n"
    "    repo45 = TrustValue45Repository()\n"
    "    await repo45.save_profile({\n"
    "        'trustId': 9901,\n"
    "        'role': 'person',\n"
    "        'name': 'live-p1',\n"
    "        'idDigest': 'digest-99901',\n"
    "        'factors': {},\n"
    "        'score': 1000.0,\n"
    "        'rawScore': 1000.0,\n"
    "        'grade': 'A',\n"
    "        'fused': False,\n"
    "        'frozen': False,\n"
    "        'createdAt':\n"
    "            '2026-01-01T00:00:00',\n"
    "        'updatedAt':\n"
    "            '2026-01-01T00:00:00'})\n"
    # ② 23号信用种子(L4)
    "    from repositories.credit"
    "_repository import (\n"
    "        CreditRepository)\n"
    "    repo23 = CreditRepository()\n"
    "    acct = await repo23.get_or_create"
    "_score(9901)\n"
    "    acct['creditLevel'] = 'L4'\n"
    "    await repo23.save_score(acct)\n"
    # ③ 注册表自检
    "    from services.xx65_registry "
    "import (\n"
    "        BANNED_REPLACEMENTS,\n"
    "        DRAFT_STATES,\n"
    "        SEVERE_WORDS,\n"
    "        registry_view)\n"
    "    out['draft_states'] = len(\n"
    "        DRAFT_STATES)\n"
    "    out['banned_n'] = len(\n"
    "        BANNED_REPLACEMENTS)\n"
    "    out['severe_n'] = len(\n"
    "        SEVERE_WORDS)\n"
    "    view = registry_view()\n"
    "    out['rules_n'] = len(\n"
    "        view.get('rules'))\n"
    "    out['llm_mode'] = view.get(\n"
    "        'llmMode')\n"
    # ④ 开店全链(老年受众)
    "    from services.xx65_service "
    "import Xx65Service\n"
    "    svc = Xx65Service()\n"
    "    it = await svc.parse_intent(\n"
    "        9901,\n"
    "        '\\u6211\\u60f3\\u505a"
    "\\u5b9a\\u5236\\u6728\\u96d5"
    "\\u548c\\u624b\\u5de5"
    "\\u76ae\\u5177',\n"
    "        audience="
    "'\\u8001\\u5e74\\u624b"
    "\\u5de5\\u827a\\u7231"
    "\\u597d\\u8005')\n"
    "    sh = await svc.apply_shop(\n"
    "        9901, 9901,\n"
    "        intent_id=it.get(\n"
    "            'intentId'))\n"
    "    await svc.claim_shop(\n"
    "        sh['shopId'],\n"
    "        {q: '\\u5426' for q in\n"
    "         sh['compliance"
    "Questions']})\n"
    "    await svc.activate_shop(\n"
    "        sh['shopId'])\n"
    "    out['shop'] = sh.get(\n"
    "        'shopId')\n"
    # ⑤ 草稿生成(含禁词——
    #    验证替换留痕)
    "    d = await svc.create_draft(\n"
    "        sh['shopId'],\n"
    "        '\\u7956\\u4f20\\u6728"
    "\\u96d5\\u6446\\u4ef6',\n"
    "        description="
    "'\\u5168\\u6751\\u6700"
    "\\u597d\\u7684\\u624b"
    "\\u827a, \\u9876\\u7ea7"
    "\\u6728\\u6599\\u3002',\n"
    "        price=100.0)\n"
    "    out['d_track'] = (\n"
    "        d.get('llmTrack'))\n"
    "    out['d_repl_n'] = len(\n"
    "        d.get('replacements')\n"
    "        or [])\n"
    "    out['d_hits'] = (\n"
    "        d.get('wordHits'))\n"
    "    out['d_clean'] = (\n"
    "        '\\u6700\\u597d' not in\n"
    "        (d.get('copy') or ''))\n"
    "    out['d_pass'] = (\n"
    "        d.get('compliance')\n"
    "        or {}).get('passed')\n"
    "    out['d_tq'] = (\n"
    "        d.get('trustQuota'))\n"
    "    out['d_fp'] = str(\n"
    "        d.get('fingerprint')\n"
    "        or '')[:7]\n"
    # ⑥ 未确认拒绝
    "    try:\n"
    "        await svc.publish_draft(\n"
    "            d['draftId'])\n"
    "        out['unconf'] = False\n"
    "    except ValueError:\n"
    "        out['unconf'] = True\n"
    # ⑦ 发布成功
    "    pub = await svc.publish_draft(\n"
    "        d['draftId'],\n"
    "        confirmed=True)\n"
    "    out['pub_status'] = (\n"
    "        pub.get('status'))\n"
    "    out['pub_pid'] = (\n"
    "        pub.get('productId'))\n"
    # ⑧ 下单窗口(双轨+
    #    额度+无障碍)
    "    w = await svc.order_window(\n"
    "        pub['productId'],\n"
    "        trust_id=9901)\n"
    "    out['w_tv'] = (w.get(\n"
    "        'dualTrack') or {}).get(\n"
    "        'trustValue')\n"
    "    out['w_cv'] = (w.get(\n"
    "        'dualTrack') or {}).get(\n"
    "        'cashValue')\n"
    "    out['w_sr'] = (w.get(\n"
    "        'quotaProgress')\n"
    "        or {}).get(\n"
    "        'singleRatio')\n"
    "    out['w_warn'] = len(\n"
    "        w.get('warnings') or [])\n"
    "    out['w_conf'] = (\n"
    "        w.get('confirmRequired'))\n"
    "    out['w_pts'] = (w.get(\n"
    "        'pointsHint') or {}).get(\n"
    "        'estimatedPoints')\n"
    "    out['w_elder'] = (w.get(\n"
    "        'accessibility')\n"
    "        or {}).get('largeFont')\n"
    # ⑨ 严重词草稿(发布拦截
    #    +人工兜底)
    "    d2 = await svc.create_draft(\n"
    "        sh['shopId'],\n"
    "        '\\u517b\\u751f\\u8336',\n"
    "        description="
    "'\\u53ef\\u4ee5\\u6839"
    "\\u6cbb\\u4e09\\u9ad8"
    "\\u3002',\n"
    "        price=88.0)\n"
    "    out['d2_flag'] = (\n"
    "        d2.get(\n"
    "            'requiresHuman"
    "Review'))\n"
    "    try:\n"
    "        await svc.publish_draft(\n"
    "            d2['draftId'],\n"
    "            confirmed=True)\n"
    "        out['sev_rej'] = False\n"
    "    except ValueError:\n"
    "        out['sev_rej'] = True\n"
    # ⑩ off 态转人工+admin
    #    终审放行(S6)
    "    os.environ['XX65_MODE'] = 'off'\n"
    "    await svc.human_review(\n"
    "        d2['draftId'])\n"
    "    ap = await svc.human_review(\n"
    "        d2['draftId'],\n"
    "        action='approve',\n"
    "        reviewer='admin')\n"
    "    out['ap_status'] = (\n"
    "        ap.get('status'))\n"
    "    out['ap_flag'] = (\n"
    "        ap.get('complianceFlag'))\n"
    # ⑪ 巡检(off 态——
    #    合规防线永不关停)
    "    ins = await svc.inspect"
    "_products(\n"
    "        shop_id=sh['shopId'])\n"
    "    out['ins_scan'] = (\n"
    "        ins.get('scanned'))\n"
    "    out['ins_flag'] = (\n"
    "        ins.get('flagged'))\n"
    # ⑫ Redis 读回(草稿替换
    #    记录+商品标记)
    "    from repositories.xx65"
    "_repository import (\n"
    "        Xx65Repository)\n"
    "    repo = Xx65Repository()\n"
    "    rd = await repo.get_draft(\n"
    "        d['draftId'])\n"
    "    out['rd_repl'] = isinstance(\n"
    "        rd.get('replacements'),\n"
    "        list)\n"
    "    out['rd_comp'] = isinstance(\n"
    "        rd.get('compliance'),\n"
    "        dict)\n"
    "    rp = await repo.get_product(\n"
    "        pub['productId'])\n"
    "    out['rp_flag'] = (\n"
    "        rp.get('complianceFlag'))\n"
    # ⑬ 合规事件(三道防线
    #    统一留痕)
    "    evs = await repo.list"
    "_compliance(limit=50)\n"
    "    out['ev_n'] = len(evs)\n"
    "    out['ev_lines'] = sorted(\n"
    "        {e.get('line')\n"
    "         for e in evs})\n"
    # ⑭ 44号档案
    "    from services.ai_learning"
    "_service import (\n"
    "        SCORER_REGISTRY)\n"
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
    clear_xx65(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/xx65/products/draft",
        body={"shopId": 1,
              "productName": "木雕",
              "price": 10},
        headers=MEMBER, expect=(409,))
    record("off 态 draft 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/xx65/registry",
        headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and len(body.get("rules")
                   or {}) == 8
           and (body.get("llmMode")
                == "off"),
           str((code,
                body.get("llmMode"))))

    print("\n[03 容器内: 内容工坊全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("草稿四态",
           r.get("draft_states") == 4,
           str(r.get("draft_states")))
    record("禁词库≥15/严重≥8",
           (r.get("banned_n") or 0) >= 15
           and (r.get("severe_n")
                or 0) >= 8,
           str((r.get("banned_n"),
                r.get("severe_n"))))
    record("S1-S8 自描述",
           r.get("rules_n") == 8,
           str(r.get("rules_n")))
    record("LLM 轨默认 off",
           r.get("llm_mode") == "off",
           str(r.get("llm_mode")))
    record("开店成功(→active)",
           (r.get("shop") or 0) == 1,
           str(r.get("shop")))
    record("草稿生成(rule 轨)",
           r.get("d_track") == "rule",
           str(r.get("d_track")))
    record("替换留痕(2 条)",
           r.get("d_repl_n") == 2
           and r.get("d_hits") == 2,
           str((r.get("d_repl_n"),
                r.get("d_hits"))))
    record("替换后无残留",
           r.get("d_clean") is True,
           str(r.get("d_clean")))
    record("替换后合规通过",
           r.get("d_pass") is True,
           str(r.get("d_pass")))
    record("双轨价格(30)",
           r.get("d_tq") == 30.0,
           str(r.get("d_tq")))
    record("S8 溯源指纹",
           r.get("d_fp") == "sha256:",
           str(r.get("d_fp")))
    record("未确认发布拒绝",
           r.get("unconf") is True,
           str(r.get("unconf")))
    record("发布成功(published)",
           r.get("pub_status")
           == "published"
           and (r.get("pub_pid")
                or 0) == 1,
           str((r.get("pub_status"),
                r.get("pub_pid"))))
    record("下单窗口双轨 30/70",
           r.get("w_tv") == 30.0
           and r.get("w_cv") == 70.0,
           str((r.get("w_tv"),
                r.get("w_cv"))))
    record("额度进度(15%)",
           r.get("w_sr") == 0.15,
           str(r.get("w_sr")))
    record("预警+二次确认",
           (r.get("w_warn") or 0) >= 1
           and r.get("w_conf") is True,
           str((r.get("w_warn"),
                r.get("w_conf"))))
    record("积分提示(3000)",
           r.get("w_pts") == 3000,
           str(r.get("w_pts")))
    record("老年受众无障碍",
           r.get("w_elder") is True,
           str(r.get("w_elder")))
    record("严重词标记人工",
           r.get("d2_flag") is True,
           str(r.get("d2_flag")))
    record("严重词发布拦截"
           "(防御②)",
           r.get("sev_rej") is True,
           str(r.get("sev_rej")))
    record("off 态人工兜底+"
           "终审放行(S6)",
           r.get("ap_status")
           == "published"
           and r.get("ap_flag")
           is True,
           str((r.get("ap_status"),
                r.get("ap_flag"))))
    record("巡检标记(off 态"
           "永不关停)",
           (r.get("ins_scan")
            or 0) == 2
           and (r.get("ins_flag")
                or 0) == 1,
           str((r.get("ins_scan"),
                r.get("ins_flag"))))
    record("Redis 读回(替换"
           "记录+合规 dict)",
           r.get("rd_repl") is True
           and r.get("rd_comp")
           is True,
           str((r.get("rd_repl"),
                r.get("rd_comp"))))
    record("商品合规标记读回",
           r.get("rp_flag") is False,
           str(r.get("rp_flag")))
    record("合规事件留痕(≥5)",
           (r.get("ev_n") or 0) >= 5,
           str(r.get("ev_n")))
    record("三道防线口径",
           r.get("ev_lines")
           == ["gen_filter",
               "post_inspect",
               "publish_recheck"],
           str(r.get("ev_lines")))
    record("44号 40 档案",
           r.get("reg_n") == 40,
           str(r.get("reg_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # 决策面 off 409
    ok, (code, _) = call(
        "POST", "/api/xx65/products/draft",
        body={"shopId": 1,
              "productName": "木雕",
              "price": 10},
        headers=MEMBER, expect=(409,))
    record("HTTP draft off 409"
           "(服务器态)",
           code == 409, str(code))
    # 观测面(容器内种子的商品
    # 读回)
    ok, (code, body) = call(
        "GET", "/api/xx65/products",
        headers=MEMBER)
    record("HTTP products 列表"
           "(Redis 读回)",
           code == 200
           and (body.get("total")
                or 0) == 2,
           str((code, body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/xx65/drafts/1",
        headers=MEMBER)
    record("HTTP 草稿详情"
           "(Redis 读回)",
           code == 200
           and ((body.get("draft")
                 or {}).get("status")
                == "published"),
           str(code))
    ok, (code, body) = call(
        "GET", "/api/xx65/products/1"
               "/order-window"
               "?trust_id=9901",
        headers=MEMBER)
    record("HTTP order-window 200",
           code == 200
           and ((body.get(
               "dualTrack")
                 or {}).get(
               "trustValue")
                == 30.0),
           str(code))
    ok, (code, _) = call(
        "GET", "/api/xx65/products/999"
               "/order-window"
               "?trust_id=9901",
        headers=MEMBER, expect=(404,))
    record("HTTP order-window 404",
           code == 404, str(code))
    # 巡检(off 态可用)
    ok, (code, body) = call(
        "POST", "/api/xx65/products/inspect",
        body={"shopId": 1},
        headers=ADMIN)
    record("HTTP inspect 200"
           "(off 态·admin)",
           code == 200
           and (body.get("flagged")
                or 0) >= 1,
           str((code, body.get(
               "flagged"))))
    # 鉴权 403(无 Role)
    for method, path in (
            ("POST",
             "/api/xx65/products/draft"),
            ("GET",
             "/api/xx65/drafts/1"),
            ("POST",
             "/api/xx65/drafts/1/"
             "publish"),
            ("POST",
             "/api/xx65/drafts/1/"
             "human-review"),
            ("GET",
             "/api/xx65/products"),
            ("GET",
             "/api/xx65/products/1/"
             "order-window"
             "?trust_id=9901"),
            ("POST",
             "/api/xx65/products/"
             "inspect")):
        resp_ok, (c, _) = call(
            method, path, body={})
        short = path.split('/')[-1] \
            .split('?')[0]
        record(f"HTTP {short}"
               f" 无 Role 403",
               c == 403, str(c))
    # 路由累计 16
    script = (
        "from routes.xx65_routes import "
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
    record("65号路由 P1 16 端点",
           count == 16, str(count))


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
