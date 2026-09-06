"""65号网店及商品AI智能管理 P0 Docker
实机验收

运行方式:
    python verify_xx65_p0_live.py [基址]

前置: 容器已运行(含 65号 P0 代码)。

覆盖(65号计划 §八 P0, 真实容器
Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(intents 409;
       registry 观测面 200)
    03 容器内: 刚性规则底座全链
       (S1-S8+意图路由+准入预检
        +六态状态机+认领合规承诺
        +第39档案)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
xx65+trust45 种子键域)。
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
    # 23号信用种子+47号画像种子
    redis_del_keys(
        "zhuxiang:credit:score:9901")
    redis_del_keys(
        "zhuxiang:trust47:trust47_profiles:"
        "9901")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['XX65_MODE'] = 'assist'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 45号档案种子(测试隔离域)
    "    from repositories.trust_value"
    "_repository import (\n"
    "        TrustValue45Repository)\n"
    "    repo45 = TrustValue45Repository()\n"
    "    await repo45.save_profile({\n"
    "        'trustId': 9901,\n"
    "        'role': 'person',\n"
    "        'name': 'live-test',\n"
    "        'idDigest': 'digest-99901',\n"
    "        'factors': {},\n"
    "        'score': 500.0,\n"
    "        'rawScore': 500.0,\n"
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
    # ③ 注册表
    "    from services.xx65_registry import (\n"
    "        REVOKE_WINDOW_SECONDS,\n"
    "        SHOP_MIN_CREDIT_LEVEL,\n"
    "        SHOP_STATES,\n"
    "        required_level,\n"
    "        registry_view)\n"
    "    out['s2'] = (\n"
    "        SHOP_MIN_CREDIT_LEVEL\n"
    "        == 'L3')\n"
    "    out['s5'] = (\n"
    "        REVOKE_WINDOW_SECONDS\n"
    "        == 300)\n"
    "    out['states'] = len(\n"
    "        SHOP_STATES)\n"
    "    out['watched'] = (\n"
    "        required_level('watched')\n"
    "        == 'L4')\n"
    "    view = registry_view()\n"
    "    out['rules_n'] = len(\n"
    "        view.get('rules'))\n"
    "    out['mode'] = (\n"
    "        view.get('mode'))\n"
    # ④ off 铁律
    "    from services.xx65_service "
    "import Xx65Service\n"
    "    svc = Xx65Service()\n"
    "    os.environ['XX65_MODE'] = 'off'\n"
    "    try:\n"
    "        await svc.parse_intent(\n"
    "            9901, 'shougong mutou')\n"
    "        out['off_rej'] = False\n"
    "    except ValueError:\n"
    "        out['off_rej'] = True\n"
    "    os.environ['XX65_MODE'] = "
    "'assist'\n"
    # ⑤ 意图路由(命中+回退——中文
    #    经 \u 转义保管道纯 ASCII)
    "    it = await svc.parse_intent(\n"
    "        9901,\n"
    "        '\\u6211\\u60f3\\u505a"
    "\\u5b9a\\u5236\\u6728\\u96d5"
    "\\u548c\\u624b\\u5de5"
    "\\u76ae\\u5177',\n"
    "        audience='nianqingren')\n"
    "    out['it_cat'] = (\n"
    "        it.get('category'))\n"
    "    out['it_fb'] = (\n"
    "        it.get('fallback'))\n"
    "    it2 = await svc.parse_intent(\n"
    "        9901, 'mai dian xiao"
    " wanr')\n"
    "    out['it2_fb'] = (\n"
    "        it2.get('fallback'))\n"
    # ⑥ 准入预检
    "    pc = await svc.admission"
    "_precheck(9901, 9901)\n"
    "    out['pc_pass'] = (\n"
    "        pc.get('passed'))\n"
    "    out['pc_req'] = (\n"
    "        pc.get('requiredLevel'))\n"
    "    out['pc_quota'] = (\n"
    "        pc.get('quotaTier'))\n"
    "    out['pc_checks'] = len(\n"
    "        pc.get('checks') or {})\n"
    # ⑦ 低信用拒绝(23号 L1)
    "    acct['creditLevel'] = 'L1'\n"
    "    await repo23.save_score(acct)\n"
    "    try:\n"
    "        await svc.apply_shop(\n"
    "            9901, 9901)\n"
    "        out['low_rej'] = False\n"
    "    except ValueError as e:\n"
    "        out['low_rej'] = (\n"
    "            'S2_CREDIT' in str(e))\n"
    "    acct['creditLevel'] = 'L4'\n"
    "    await repo23.save_score(acct)\n"
    # ⑧ 开店全链(六态)
    "    r = await svc.apply_shop(\n"
    "        9901, 9901,\n"
    "        intent_id=it.get(\n"
    "            'intentId'))\n"
    "    out['sh_status'] = (\n"
    "        r.get('status'))\n"
    "    out['sh_cat'] = (\n"
    "        r.get('category'))\n"
    "    out['sh_qn'] = len(\n"
    "        r.get(\n"
    "            'complianceQuestions')\n"
    "        or [])\n"
    # ⑨ 重复开店拒绝
    "    try:\n"
    "        await svc.apply_shop(\n"
    "            9901, 9901)\n"
    "        out['dup_rej'] = False\n"
    "    except ValueError:\n"
    "        out['dup_rej'] = True\n"
    # ⑩ 认领(合规承诺——否=\u5426
    #    /是=\u662f 经转义)
    "    qs = r.get(\n"
    "        'complianceQuestions')\n"
    "    try:\n"
    "        await svc.claim_shop(\n"
    "            1, {qs[0]:"
    " '\\u662f'})\n"
    "        out['susp_rej'] = False\n"
    "    except ValueError:\n"
    "        out['susp_rej'] = True\n"
    "    cl = await svc.claim_shop(\n"
    "        1, {q: '\\u5426'"
    " for q in qs})\n"
    "    out['cl_status'] = (\n"
    "        cl.get('status'))\n"
    "    out['cl_fp'] = str(\n"
    "        cl.get('fingerprint')\n"
    "        or '')[:7]\n"
    # ⑪ 激活+非法迁移
    "    ac = await svc.activate_shop(1)\n"
    "    out['ac_status'] = (\n"
    "        ac.get('status'))\n"
    "    try:\n"
    "        await svc.activate_shop(1)\n"
    "        out['re_act'] = False\n"
    "    except ValueError:\n"
    "        out['re_act'] = True\n"
    # ⑫ 关店(off 亦可)
    "    os.environ['XX65_MODE'] = 'off'\n"
    "    cv = await svc.close_shop(1)\n"
    "    out['clz_status'] = (\n"
    "        cv.get('status'))\n"
    "    try:\n"
    "        await svc.activate_shop(1)\n"
    "        out['dead'] = False\n"
    "    except ValueError:\n"
    "        out['dead'] = True\n"
    "    os.environ['XX65_MODE'] = "
    "'assist'\n"
    # ⑬ Redis 读回
    "    from repositories.xx65"
    "_repository import (\n"
    "        Xx65Repository)\n"
    "    repo = Xx65Repository()\n"
    "    d1 = await repo.get_shop(1)\n"
    "    out['rd_status'] = (\n"
    "        d1.get('status'))\n"
    "    out['rd_snap'] = isinstance(\n"
    "        d1.get(\n"
    "            'precheckSnapshot'),\n"
    "        dict)\n"
    # ⑭ 事件留痕
    "    evs = await repo.list_events(\n"
    "        limit=50)\n"
    "    out['ev_n'] = len(evs)\n"
    # ⑮ 第39档案
    "    from services.xx65_scorer "
    "import Xx65Scorer\n"
    "    sc = await Xx65Scorer().score({\n"
    "        'shopHealth': 0.95,\n"
    "        'tier': 'trusted'})\n"
    "    out['sc_factors'] = len(\n"
    "        sc.get('factors') or [])\n"
    "    out['sc_dec'] = (\n"
    "        sc.get('decision'))\n"
    # ⑯ 44号档案
    "    from services.ai_learning"
    "_service import (\n"
    "        SCORER_REGISTRY)\n"
    "    out['reg_n'] = len(\n"
    "        SCORER_REGISTRY)\n"
    "    out['so_in'] = (\n"
    "        'shop_operation'\n"
    "        in SCORER_REGISTRY)\n"
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
        "POST", "/api/xx65/intents/parse",
        body={"ownerId": 9901,
              "text": "手工木雕"},
        headers=MEMBER, expect=(409,))
    record("off 态 intents 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/xx65/registry",
        headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and len(body.get("rules")
                   or {}) == 8
           and body.get("mode") == "off",
           str((code,
                len(body.get("rules")
                    or {}))))

    print("\n[03 容器内: 刚性规则全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("S2 门槛 L3",
           r.get("s2") is True,
           str(r.get("s2")))
    record("S5 撤销窗口 300s",
           r.get("s5") is True,
           str(r.get("s5")))
    record("店铺六态",
           r.get("states") == 6,
           str(r.get("states")))
    record("tier 加严(watched→L4)",
           r.get("watched") is True,
           str(r.get("watched")))
    record("S1-S8 自描述(8 条)",
           r.get("rules_n") == 8,
           str(r.get("rules_n")))
    record("off 铁律(解析拒绝)",
           r.get("off_rej") is True,
           str(r.get("off_rej")))
    record("意图路由命中(handicraft)",
           r.get("it_cat") == "handicraft"
           and r.get("it_fb") is False,
           str((r.get("it_cat"),
                r.get("it_fb"))))
    record("意图回退 general",
           r.get("it2_fb") is True,
           str(r.get("it2_fb")))
    record("准入预检通过",
           r.get("pc_pass") is True
           and r.get("pc_req") == "L3",
           str((r.get("pc_pass"),
                r.get("pc_req"))))
    record("配额档 growth(L4)",
           r.get("pc_quota") == "growth",
           str(r.get("pc_quota")))
    record("三查结构",
           r.get("pc_checks") == 3,
           str(r.get("pc_checks")))
    record("低信用拒绝(S2)",
           r.get("low_rej") is True,
           str(r.get("low_rej")))
    record("开店成功(prechecked)",
           r.get("sh_status")
           == "prechecked"
           and r.get("sh_cat")
           == "handicraft",
           str((r.get("sh_status"),
                r.get("sh_cat"))))
    record("合规问题随单",
           (r.get("sh_qn") or 0) >= 1,
           str(r.get("sh_qn")))
    record("重复开店拒绝",
           r.get("dup_rej") is True,
           str(r.get("dup_rej")))
    record("存疑转人工(S6)",
           r.get("susp_rej") is True,
           str(r.get("susp_rej")))
    record("认领成功(claimed)",
           r.get("cl_status")
           == "claimed",
           str(r.get("cl_status")))
    record("S8 溯源指纹(sha256)",
           r.get("cl_fp") == "sha256:",
           str(r.get("cl_fp")))
    record("激活成功(active)",
           r.get("ac_status")
           == "active",
           str(r.get("ac_status")))
    record("重复激活拒绝",
           r.get("re_act") is True,
           str(r.get("re_act")))
    record("关店成功(off 亦可)",
           r.get("clz_status")
           == "closed",
           str(r.get("clz_status")))
    record("closed 无出边",
           r.get("dead") is True,
           str(r.get("dead")))
    record("Redis 读回(店铺+"
           "快照 dict)",
           r.get("rd_status")
           == "closed"
           and r.get("rd_snap") is True,
           str((r.get("rd_status"),
                r.get("rd_snap"))))
    record("事件链(≥4)",
           (r.get("ev_n") or 0) >= 4,
           str(r.get("ev_n")))
    record("第39档案八因子",
           r.get("sc_factors") == 8,
           str(r.get("sc_factors")))
    record("高分决策 optimize/urgent",
           r.get("sc_dec") in (
               "optimize", "urgent"),
           str(r.get("sc_dec")))
    record("44号 40 档案",
           r.get("reg_n") == 40,
           str(r.get("reg_n")))
    record("shop_operation 在册",
           r.get("so_in") is True,
           str(r.get("so_in")))

    print("\n[04 HTTP 端点+鉴权]")
    # 服务器态 off——决策面 409
    ok, (code, _) = call(
        "POST", "/api/xx65/intents/parse",
        body={"ownerId": 9901,
              "text": "手工木雕"},
        headers=MEMBER, expect=(409,))
    record("HTTP intents off 409"
           "(服务器态)",
           code == 409, str(code))
    # 观测面(容器内种子的店铺读回)
    ok, (code, body) = call(
        "GET", "/api/xx65/shops",
        headers=ADMIN)
    record("HTTP shops 列表"
           "(Redis 读回)",
           code == 200
           and (body.get("total") or 0)
           >= 1,
           str((code, body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/xx65/shops/1",
        headers=MEMBER)
    record("HTTP shops 详情"
           "(Redis 读回)",
           code == 200
           and ((body.get("shop") or {})
                .get("ownerId")) == 9901,
           str(code))
    ok, (code, _) = call(
        "GET", "/api/xx65/shops/999",
        headers=MEMBER, expect=(404,))
    record("HTTP shops 详情 404",
           code == 404, str(code))
    ok, (code, body) = call(
        "GET", "/api/xx65/model/status",
        headers=ADMIN)
    record("HTTP model/status 观测面",
           code == 200
           and ((body.get("scorer") or {})
                .get("scorerId"))
           == "shop_operation",
           str((code,
                (body.get("scorer")
                 or {}).get(
                    "scorerId"))))
    # 鉴权 403
    for method, path in (
            ("GET", "/api/xx65/registry"),
            ("POST",
             "/api/xx65/intents/parse"),
            ("POST", "/api/xx65/shops/apply"),
            ("GET", "/api/xx65/shops"),
            ("GET",
             "/api/xx65/model/status")):
        resp_ok, (c, _) = call(
            method, path, body={})
        short = path.split('/')[-1] \
            .split('?')[0]
        record(f"HTTP {short}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 9
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
    record("65号路由 P0 9 端点",
           count == 9, str(count))


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
