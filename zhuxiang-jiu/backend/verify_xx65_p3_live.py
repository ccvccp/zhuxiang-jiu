"""65号网店及商品AI智能管理 P3 Docker
实机验收(治理与成长层)

运行方式:
    python verify_xx65_p3_live.py [基址]

前置: 容器已运行(含 65号 P3 代码)。

覆盖(65号计划 §八 P3, 真实容器
Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(quota-adjust/
       dispute-assist 409; health/
       coach 观测面 200)
    03 容器内: 治理与成长全链
       (健康度看板+教练分发
        +S7 配额升降 46号轨
        +争议证据链)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子
重造——xx65+trust45+46号
change 种子键域)。
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
    # 46号治理台账+变更单(重造)
    redis_del_keys(
        "zhuxiang:ai46:*")


# 容器内管道(纯 ASCII——中文经
# \u 转义)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['XX65_MODE'] = 'assist'\n"
    "os.environ['XX65_LLM_MODE'] = 'off'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 种子(45号档案 1000/23号
    #    L4=growth 档)
    "    from repositories.trust_value"
    "_repository import (\n"
    "        TrustValue45Repository)\n"
    "    repo45 = TrustValue45Repository()\n"
    "    await repo45.save_profile({\n"
    "        'trustId': 9901,\n"
    "        'role': 'person',\n"
    "        'name': 'live-p3',\n"
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
    "    from repositories.credit"
    "_repository import (\n"
    "        CreditRepository)\n"
    "    repo23 = CreditRepository()\n"
    "    acct = await repo23.get_or_create"
    "_score(9901)\n"
    "    acct['creditLevel'] = 'L4'\n"
    "    await repo23.save_score(acct)\n"
    # ② 注册表自检
    "    from services.xx65_registry import (\n"
    "        COACH_TIPS,\n"
    "        HEALTH_WEIGHTS,\n"
    "        QUOTA_TIER_ORDER,\n"
    "        registry_view)\n"
    "    out['coach_n'] = len(\n"
    "        COACH_TIPS)\n"
    "    out['h_w'] = round(sum(\n"
    "        HEALTH_WEIGHTS"
    ".values()), 4)\n"
    "    out['tiers'] = len(\n"
    "        QUOTA_TIER_ORDER)\n"
    "    view = registry_view()\n"
    "    out['rules_n'] = len(\n"
    "        view.get('rules'))\n"
    "    out['coach_pool'] = (view.get(\n"
    "        'coachPool') or {}).get(\n"
    "        'total')\n"
    # ③ 开店+发布商品
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
    # ④ 健康度(干净店铺 100)
    "    h = await svc.shop_health(\n"
    "        sh['shopId'])\n"
    "    out['h_score'] = (\n"
    "        h.get('healthScore'))\n"
    "    out['h_pass'] = (\n"
    "        h.get('passed'))\n"
    "    out['h_comp'] = sorted(\n"
    "        (h.get('components')\n"
    "         or {}).keys())\n"
    "    out['h_sug'] = (\n"
    "        (h.get('quotaSuggestion')\n"
    "         or {}).get(\n"
    "            'direction'))\n"
    "    out['h_target'] = (\n"
    "        (h.get('quotaSuggestion')\n"
    "         or {}).get(\n"
    "            'targetTier'))\n"
    # ⑤ 教练(growth 档 3 条)
    "    c = await svc.coach_tips(\n"
    "        sh['shopId'])\n"
    "    out['c_n'] = c.get('total')\n"
    "    out['c_tier'] = (\n"
    "        c.get('quotaTier'))\n"
    "    out['c_kinds'] = sorted(\n"
    "        {t['kind'] for t in\n"
    "         (c.get('tips') or [])})\n"
    # ⑥ 配额升档(46号轨)
    "    adj = await svc.quota"
    "_adjust(\n"
    "        sh['shopId'], 'uplift',\n"
    "        requested_by='admin')\n"
    "    out['adj_status'] = (\n"
    "        (adj.get('governance')\n"
    "         or {}).get('status'))\n"
    "    out['adj_after'] = (\n"
    "        (adj.get('quotaTier')\n"
    "         or {}).get('after'))\n"
    "    out['adj_cid'] = (\n"
    "        (adj.get('governance')\n"
    "         or {}).get('changeId'))\n"
    # ⑦ 永不自动执行(档不变)
    "    h2 = await svc.shop_health(\n"
    "        sh['shopId'])\n"
    "    out['auto_exec'] = (\n"
    "        (h2.get('quotaSuggestion')\n"
    "         or {}).get(\n"
    "            'currentTier')\n"
    "        == 'growth')\n"
    # ⑧ 降档门槛拒绝(健康 100)
    "    try:\n"
    "        await svc.quota_adjust(\n"
    "            sh['shopId'],\n"
    "            'downgrade',\n"
    "            requested_by='admin')\n"
    "        out['dg_rej'] = False\n"
    "    except ValueError:\n"
    "        out['dg_rej'] = True\n"
    # ⑨ 争议证据链(含商品)
    "    dis = await svc.dispute"
    "_assist(\n"
    "        sh['shopId'],\n"
    "        product_id=pub[\n"
    "            'productId'],\n"
    "        summary='\\u4e70\\u5bb6"
    "\\u6295\\u8bc9')\n"
    "    out['d_kinds'] = sorted(\n"
    "        {e['kind'] for e in\n"
    "         (dis.get('evidence')\n"
    "          or [])})\n"
    "    out['d_adv'] = len(\n"
    "        dis.get('advises') or [])\n"
    # ⑩ 教练分发留痕(
    #    Redis 读回)
    "    from repositories.xx65"
    "_repository import (\n"
    "        Xx65Repository)\n"
    "    repo = Xx65Repository()\n"
    "    tips = await repo.list_tips(\n"
    "        tier='growth', limit=10)\n"
    "    out['tips_n'] = len(tips)\n"
    # ⑪ 44号档案
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
    # 服务器态 off——决策面 409
    ok, (code, _) = call(
        "POST", "/api/xx65/shops/1/"
                "quota-adjust",
        body={"direction": "uplift"},
        headers=ADMIN, expect=(409,))
    record("off 态 quota-adjust 409",
           code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/xx65/shops/1/"
                "dispute-assist",
        body={},
        headers=MEMBER, expect=(409,))
    record("off 态 dispute-assist 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/xx65/registry",
        headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and (body or {}).get(
               "coachPool", {}).get(
               "total") == 9,
           str((code,
                (body or {}).get(
                    "coachPool"))))

    print("\n[03 容器内: 治理与成长全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("S1-S8 自描述",
           r.get("rules_n") == 8,
           str(r.get("rules_n")))
    record("教练池 9 条",
           r.get("coach_n") == 9
           and r.get("coach_pool") == 9,
           str((r.get("coach_n"),
                r.get("coach_pool"))))
    record("健康度权重归一",
           r.get("h_w") == 1.0,
           str(r.get("h_w")))
    record("配额档三阶",
           r.get("tiers") == 3,
           str(r.get("tiers")))
    record("开店+商品"
           "(shop=1/product=1)",
           (r.get("shop") or 0) == 1
           and (r.get("product")
                or 0) == 1,
           str((r.get("shop"),
                r.get("product"))))
    record("健康度 100(干净店铺)",
           r.get("h_score") == 100.0
           and r.get("h_pass") is True,
           str(r.get("h_score")))
    record("健康度三组件",
           r.get("h_comp")
           == ["campaign_revokes",
               "compliance_events",
               "product_flags"],
           str(r.get("h_comp")))
    record("S7 升档建议"
           "(growth→premium)",
           r.get("h_sug") == "uplift"
           and r.get("h_target")
           == "premium",
           str((r.get("h_sug"),
                r.get("h_target"))))
    record("教练 growth 档 3 条",
           r.get("c_n") == 3
           and r.get("c_tier")
           == "growth",
           str((r.get("c_n"),
                r.get("c_tier"))))
    record("教练三类齐备",
           r.get("c_kinds")
           == ["daily_tip",
               "hot_case",
               "warning"],
           str(r.get("c_kinds")))
    record("升档建议书"
           "(46号 pending)",
           r.get("adj_status")
           == "pending"
           and r.get("adj_after")
           == "premium"
           and (r.get("adj_cid")
                or 0) >= 1,
           str((r.get("adj_status"),
                r.get("adj_after"))))
    record("永不自动执行"
           "(档不变 growth)",
           r.get("auto_exec") is True,
           str(r.get("auto_exec")))
    record("降档门槛拒绝"
           "(健康度过高)",
           r.get("dg_rej") is True,
           str(r.get("dg_rej")))
    record("争议证据链四源",
           set(r.get("d_kinds")
               or []) >= {
               "shop", "product",
               "compliance",
               "campaign"},
           str(r.get("d_kinds")))
    record("处置建议在案",
           (r.get("d_adv") or 0) >= 1,
           str(r.get("d_adv")))
    record("教练分发留痕"
           "(Redis 读回 3 条)",
           r.get("tips_n") == 3,
           str(r.get("tips_n")))
    record("44号 40 档案",
           r.get("reg_n") == 40,
           str(r.get("reg_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # 健康度观测面(Redis 读回)
    ok, (code, body) = call(
        "GET", "/api/xx65/shops/1/health",
        headers=MEMBER)
    record("HTTP health 200"
           "(观测面)",
           code == 200
           and (body or {}).get(
               "healthScore") == 100.0,
           str((code,
                (body or {}).get(
                    "healthScore"))))
    ok, (code, _) = call(
        "GET", "/api/xx65/shops/999"
               "/health",
        headers=MEMBER, expect=(404,))
    record("HTTP health 404",
           code == 404, str(code))
    # 教练观测面
    ok, (code, body) = call(
        "GET", "/api/xx65/shops/1/coach",
        headers=MEMBER)
    record("HTTP coach 200"
           "(3 条)",
           code == 200
           and (body or {}).get(
               "total") == 3,
           str((code,
                (body or {}).get(
                    "total"))))
    # 决策面 off 409(服务器态)
    ok, (code, _) = call(
        "POST", "/api/xx65/shops/1/"
                "quota-adjust",
        body={"direction": "uplift"},
        headers=ADMIN, expect=(409,))
    record("HTTP quota-adjust off"
           " 409(服务器态)",
           code == 409, str(code))
    # 鉴权 403(无 Role)
    for method, path in (
            ("GET",
             "/api/xx65/shops/1/health"),
            ("GET",
             "/api/xx65/shops/1/coach"),
            ("POST",
             "/api/xx65/shops/1/"
             "quota-adjust"),
            ("POST",
             "/api/xx65/shops/1/"
             "dispute-assist")):
        resp_ok, (c, _) = call(
            method, path, body={})
        short = path.split('/')[-1] \
            .split('?')[0]
        record(f"HTTP {short}"
               f" 无 Role 403",
               c == 403, str(c))
    # 路由累计 25
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
    record("65号路由 P3 25 端点",
           count == 25, str(count))


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
