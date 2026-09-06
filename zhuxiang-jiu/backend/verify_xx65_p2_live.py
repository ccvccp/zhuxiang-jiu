"""65号网店及商品AI智能管理 P2 Docker
实机验收(智能营销中枢)

运行方式:
    python verify_xx65_p2_live.py [基址]

前置: 容器已运行(含 65号 P2 代码)。

覆盖(65号计划 §八 P2, 真实容器
Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(campaigns 409;
       recommend 观测面 200)
    03 容器内: 营销中枢全链
       (三因子推荐+ROI 双算
        +R2 互斥+S5 撤销窗口
        +复盘——64号流动性
        纯读取)
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
    "        'name': 'live-p2',\n"
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
    "        CAMPAIGN_FACTOR_WEIGHTS,\n"
    "        CAMPAIGN_STATES,\n"
    "        CAMPAIGN_STRATEGIES,\n"
    "        SEASON_TRENDS,\n"
    "        registry_view)\n"
    "    out['strategies'] = len(\n"
    "        CAMPAIGN_STRATEGIES)\n"
    "    out['states'] = len(\n"
    "        CAMPAIGN_STATES)\n"
    "    out['months'] = len(\n"
    "        SEASON_TRENDS)\n"
    "    w_sum = sum(\n"
    "        CAMPAIGN_FACTOR_WEIGHTS"
    ".values())\n"
    "    out['w_sum'] = round(\n"
    "        w_sum, 4)\n"
    "    view = registry_view()\n"
    "    out['rules_n'] = len(\n"
    "        view.get('rules'))\n"
    # ④ 开店+发布商品
    "    from services.xx65_service "
    "import Xx65Service\n"
    "    svc = Xx65Service()\n"
    "    it = await svc.parse_intent(\n"
    "        9901,\n"
    "        '\\u6211\\u60f3\\u505a"
    "\\u5b9a\\u5236\\u6728\\u96d5"
    "\\u548c\\u624b\\u5de5"
    "\\u76ae\\u5177')\n"
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
    "    d = await svc.create_draft(\n"
    "        sh['shopId'],\n"
    "        '\\u7956\\u4f20\\u6728"
    "\\u96d5\\u6446\\u4ef6',\n"
    "        price=100.0)\n"
    "    pub = await svc.publish_draft(\n"
    "        d['draftId'],\n"
    "        confirmed=True)\n"
    "    out['shop'] = sh.get(\n"
    "        'shopId')\n"
    "    out['product'] = pub.get(\n"
    "        'productId')\n"
    # ⑤ 推荐(三因子+双算)
    "    rec = await svc.recommend"
    "_campaign(\n"
    "        shop_id=sh['shopId'],\n"
    "        product_id=pub[\n"
    "            'productId'])\n"
    "    recs = rec.get(\n"
    "        'recommendations') or []\n"
    "    out['rec_n'] = len(recs)\n"
    "    out['rec_top'] = (\n"
    "        recs[0]['strategy']\n"
    "        if recs else '')\n"
    "    out['rec_score'] = (\n"
    "        recs[0]['score']\n"
    "        if recs else 0)\n"
    "    out['base_score'] = (\n"
    "        rec.get('baseScore'))\n"
    "    out['factors'] = sorted(\n"
    "        (rec.get('factors')\n"
    "         or {}).keys())\n"
    "    out['liq_src'] = (\n"
    "        (rec.get('liquidity')\n"
    "         or {}).get('source'))\n"
    "    out['roi_gmv'] = (\n"
    "        recs[0]['roi']\n"
    "        ['estimatedGmv']\n"
    "        if recs else 0)\n"
    "    out['roi_trust'] = (\n"
    "        recs[0]['roi']\n"
    "        ['estimatedTrust']\n"
    "        if recs else 0)\n"
    # ⑥ 推荐确定性(二次调用
    #    同结果)
    "    rec2 = await svc.recommend"
    "_campaign(\n"
    "        shop_id=sh['shopId'],\n"
    "        product_id=pub[\n"
    "            'productId'])\n"
    "    out['rec_det'] = (\n"
    "        rec2.get('baseScore')\n"
    "        == rec.get('baseScore')\n"
    "        and (rec2.get(\n"
    "            'recommendations')\n"
    "            or [{}])[0]\n"
    "        .get('strategy')\n"
    "        == out['rec_top'])\n"
    # ⑦ 创建活动(R2+S5)
    "    c = await svc.create"
    "_campaign(\n"
    "        sh['shopId'],\n"
    "        pub['productId'],\n"
    "        'clearance')\n"
    "    out['c_status'] = (\n"
    "        c.get('status'))\n"
    "    out['c_excl'] = (\n"
    "        c.get('exclusive'))\n"
    "    out['c_window'] = (\n"
    "        c.get(\n"
    "            'revokeWindow"
    "Seconds'))\n"
    "    out['c_gmv'] = (\n"
    "        (c.get('roi') or {})\n"
    "        .get('estimatedGmv'))\n"
    "    cid = c.get('campaignId')\n"
    # ⑧ S1 活动名合规拦截
    "    try:\n"
    "        await svc.create"
    "_campaign(\n"
    "            sh['shopId'],\n"
    "            pub['productId'],\n"
    "            'seasonal',\n"
    "            name='\\u5168\\u7f51"
    "\\u6700\\u4f4e\\u4ef7')\n"
    "        out['s1_rej'] = False\n"
    "    except ValueError:\n"
    "        out['s1_rej'] = True\n"
    # ⑨ 撤销(S5 窗口内)
    "    rv = await svc.revoke"
    "_campaign(cid)\n"
    "    out['rv_status'] = (\n"
    "        rv.get('status'))\n"
    # ⑩ 终态撤销拒绝
    "    try:\n"
    "        await svc.revoke"
    "_campaign(cid)\n"
    "        out['rv_term'] = False\n"
    "    except ValueError:\n"
    "        out['rv_term'] = True\n"
    # ⑪ 超窗撤销拒绝(改仓储
    #    revocableUntilTs)
    "    c2 = await svc.create"
    "_campaign(\n"
    "        sh['shopId'],\n"
    "        pub['productId'],\n"
    "        'new_customer')\n"
    "    from repositories.xx65"
    "_repository import (\n"
    "        Xx65Repository)\n"
    "    repo = Xx65Repository()\n"
    "    camp = await repo.get"
    "_campaign(\n"
    "        c2['campaignId'])\n"
    "    camp['revocableUntilTs'] = (\n"
    "        camp['revocableUntilTs']\n"
    "        - 600)\n"
    "    await repo.save_campaign(\n"
    "        camp, create=False)\n"
    "    try:\n"
    "        await svc.revoke"
    "_campaign(\n"
    "            c2['campaignId'])\n"
    "        out['rv_late'] = False\n"
    "    except ValueError:\n"
    "        out['rv_late'] = True\n"
    # ⑫ 复盘(R2+撤销审计)
    "    rpt = await svc.campaign"
    "_report(cid)\n"
    "    out['rp_excl'] = (\n"
    "        rpt.get('exclusive'))\n"
    "    out['rp_revoked'] = (\n"
    "        (rpt.get('revocation')\n"
    "         or {}).get('revoked'))\n"
    "    out['rp_gmv'] = (\n"
    "        (rpt.get('roi') or {})\n"
    "        .get('estimated', {})\n"
    "        .get('gmv'))\n"
    # ⑬ Redis 读回(R2 bool+
    #    channels list+撤销留痕)
    "    camp_rd = await repo.get"
    "_campaign(cid)\n"
    "    out['rd_excl'] = (\n"
    "        camp_rd.get('exclusive'))\n"
    "    out['rd_channels'] = (\n"
    "        camp_rd.get('channels'))\n"
    "    out['rd_revoked'] = (\n"
    "        camp_rd.get('revoked'))\n"
    "    out['rd_factors'] = (\n"
    "        isinstance(\n"
    "            camp_rd.get(\n"
    "                'factors'), dict))\n"
    # ⑭ 事件留痕
    "    evs = await repo.list_events(\n"
    "        limit=50)\n"
    "    camp_evs = [e for e in evs\n"
    "                if (e.get(\n"
    "                    'detail') or {})\n"
    "                .get('action')\n"
    "                in ('create',\n"
    "                    'revoke')]\n"
    "    out['ev_n'] = len(camp_evs)\n"
    # ⑮ 44号档案
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
        "POST", "/api/xx65/campaigns",
        body={"shopId": 1,
              "productId": 1,
              "strategy": "clearance"},
        headers=MEMBER, expect=(409,))
    record("off 态 campaigns 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/xx65/registry",
        headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and len((body or {}).get(
               "campaignStrategies")
                   or {}) == 5,
           str((code,
                len((body or {}).get(
                    "campaignStrategies")
                    or {}))))

    print("\n[03 容器内: 营销中枢全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("五策略在册",
           r.get("strategies") == 5,
           str(r.get("strategies")))
    record("活动三态状态机",
           r.get("states") == 3,
           str(r.get("states")))
    record("季度趋势 12 月",
           r.get("months") == 12,
           str(r.get("months")))
    record("三因子权重归一(1.0)",
           r.get("w_sum") == 1.0,
           str(r.get("w_sum")))
    record("S1-S8 自描述",
           r.get("rules_n") == 8,
           str(r.get("rules_n")))
    record("开店+商品"
           "(shop=1/product=1)",
           (r.get("shop") or 0) == 1
           and (r.get("product")
                or 0) == 1,
           str((r.get("shop"),
                r.get("product"))))
    record("Top3 推荐",
           r.get("rec_n") == 3,
           str(r.get("rec_n")))
    record("推荐确定性"
           "(同输入同输出)",
           r.get("rec_det") is True,
           str(r.get("rec_det")))
    record("三因子齐备",
           r.get("factors")
           == ["product_heat",
               "season_trend",
               "shop_trust"],
           str(r.get("factors")))
    record("64号流动性纯读取",
           r.get("liq_src")
           == "xx64-read-only",
           str(r.get("liq_src")))
    record("ROI 双算"
               "(GMV>0+信值>0)",
           (r.get("roi_gmv")
            or 0) > 0
           and (r.get("roi_trust")
                or 0) > 0,
           str((r.get("roi_gmv"),
                r.get("roi_trust"))))
    record("创建成功(active)",
           r.get("c_status")
           == "active",
           str(r.get("c_status")))
    record("R2 互斥声明嵌入",
           r.get("c_excl") is True,
           str(r.get("c_excl")))
    record("S5 撤销窗口 300s",
           r.get("c_window") == 300,
           str(r.get("c_window")))
    record("ROI 快照一致"
               "(clearance 2500)",
           r.get("c_gmv")
           == 2500.0,
           str(r.get("c_gmv")))
    record("S1 活动名禁词拦截",
           r.get("s1_rej") is True,
           str(r.get("s1_rej")))
    record("窗口内撤销成功"
           "(S5)",
           r.get("rv_status")
           == "revoked",
           str(r.get("rv_status")))
    record("终态撤销拒绝",
           r.get("rv_term") is True,
           str(r.get("rv_term")))
    record("超窗撤销拒绝"
           "(300s 窗口)",
           r.get("rv_late") is True,
           str(r.get("rv_late")))
    record("复盘 R2 留痕",
           r.get("rp_excl") is True,
           str(r.get("rp_excl")))
    record("复盘撤销审计",
           r.get("rp_revoked") is True,
           str(r.get("rp_revoked")))
    record("复盘预估双算"
               "(clearance 2500)",
           r.get("rp_gmv") == 2500.0,
           str(r.get("rp_gmv")))
    record("Redis 读回"
           "(exclusive bool)",
           r.get("rd_excl") is True,
           str(r.get("rd_excl")))
    record("Redis 读回"
           "(channels list)",
           r.get("rd_channels")
           == ["in_site"],
           str(r.get("rd_channels")))
    record("Redis 读回"
           "(revoked 留痕)",
           r.get("rd_revoked") is True,
           str(r.get("rd_revoked")))
    record("Redis 读回"
           "(factors dict)",
           r.get("rd_factors")
           is True,
           str(r.get("rd_factors")))
    record("事件链(create+revoke)",
           (r.get("ev_n") or 0) >= 3,
           str(r.get("ev_n")))
    record("44号 40 档案",
           r.get("reg_n") == 40,
           str(r.get("reg_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # 决策面 off 409
    ok, (code, _) = call(
        "POST", "/api/xx65/campaigns",
        body={"shopId": 1,
              "productId": 1,
              "strategy": "clearance"},
        headers=MEMBER, expect=(409,))
    record("HTTP campaigns off 409"
           "(服务器态)",
           code == 409, str(code))
    # 推荐观测面(容器内种子的
    # 店铺读回)
    ok, (code, body) = call(
        "POST",
        "/api/xx65/campaigns/recommend",
        body={"shopId": 1,
              "productId": 1},
        headers=MEMBER)
    record("HTTP recommend 200"
           "(Redis 读回)",
           code == 200
           and len((body or {}).get(
               "recommendations")
                   or []) == 3,
           str((code,
                len((body or {}).get(
                    "recommendations")
                    or []))))
    # 复盘观测面
    ok, (code, body) = call(
        "GET", "/api/xx65/campaigns/1"
               "/report",
        headers=MEMBER)
    record("HTTP report 200",
           code == 200
           and (body or {}).get(
               "exclusive") is True,
           str(code))
    ok, (code, _) = call(
        "GET", "/api/xx65/campaigns/999"
               "/report",
        headers=MEMBER, expect=(404,))
    record("HTTP report 404",
           code == 404, str(code))
    # 列表观测面
    ok, (code, body) = call(
        "GET", "/api/xx65/campaigns",
        headers=MEMBER)
    record("HTTP campaigns 列表"
           "(Redis 读回)",
           code == 200
           and (body or {}).get(
               "total") == 2,
           str((code, (body or {}).get(
               "total"))))
    # 鉴权 403(无 Role)
    for method, path in (
            ("POST",
             "/api/xx65/campaigns/"
             "recommend"),
            ("POST",
             "/api/xx65/campaigns"),
            ("POST",
             "/api/xx65/campaigns/1/"
             "revoke"),
            ("GET",
             "/api/xx65/campaigns"),
            ("GET",
             "/api/xx65/campaigns/1/"
             "report")):
        resp_ok, (c, _) = call(
            method, path, body={})
        short = path.split('/')[-1] \
            .split('?')[0]
        record(f"HTTP {short}"
               f" 无 Role 403",
               c == 403, str(c))
    # 路由累计 21
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
    record("65号路由 P2 21 端点"
           "(P3 增至 25)",
           count >= 21, str(count))


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
