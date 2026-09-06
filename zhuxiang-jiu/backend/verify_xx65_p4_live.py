"""65号网店及商品AI智能管理 P4 Docker
实机验收(回流+看板+红队+收官)

运行方式:
    python verify_xx65_p4_live.py [基址]

前置: 容器已运行(含 65号 P4 代码)。

覆盖(65号计划 §八 P4, 真实容器
Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(redteam 409;
       collect 观测宪法可用)
    03 容器内: 回流+调度+看板
       +红队全链(productId 1:1
        幂等双轮+scheduler_run
        留痕+四区聚合+RT 七向量)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子
重造——xx65+trust45+44号池
种子键域)。
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
    # 44号池种子(shop_operation
    # 反馈重造)
    redis_del_keys(
        "zhuxiang:ai_learning:feedback:"
        "shop_operation")
    redis_del_keys(
        "zhuxiang:ai_learning:feedback:seq")


# 容器内管道(纯 ASCII——中文经
# \u 转义)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['XX65_MODE'] = 'assist'\n"
    "os.environ['XX65_LLM_MODE'] = 'off'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 种子(45号档案 1000/
    #    23号 L4)
    "    from repositories.trust_value"
    "_repository import (\n"
    "        TrustValue45Repository)\n"
    "    repo45 = TrustValue45Repository()\n"
    "    await repo45.save_profile({\n"
    "        'trustId': 9901,\n"
    "        'role': 'person',\n"
    "        'name': 'live-p4',\n"
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
    # ② 开店+发布 2 件商品
    #    (1 干净+1 严重词放行
    #    巡检标记)
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
    "    d1 = await svc.create_draft(\n"
    "        sh['shopId'],\n"
    "        '\\u7956\\u4f20\\u6728"
    "\\u96d5\\u6446\\u4ef6',\n"
    "        price=100.0)\n"
    "    pub1 = await svc.publish"
    "_draft(\n"
    "        d1['draftId'],\n"
    "        confirmed=True)\n"
    "    d2 = await svc.create_draft(\n"
    "        sh['shopId'],\n"
    "        '\\u517b\\u751f\\u8336',\n"
    "        description="
    "'\\u53ef\\u4ee5\\u6839"
    "\\u6cbb\\u4e09\\u9ad8"
    "\\u3002',\n"
    "        price=88.0)\n"
    "    await svc.human_review(\n"
    "        d2['draftId'])\n"
    "    pub2 = await svc.human"
    "_review(\n"
    "        d2['draftId'],\n"
    "        action='approve',\n"
    "        reviewer='admin')\n"
    "    await svc.inspect_products(\n"
    "        shop_id=sh['shopId'])\n"
    "    out['shop'] = sh.get(\n"
    "        'shopId')\n"
    # ③ 回流首轮(2 信号)
    "    from services.xx65_learn"
    "_service import (\n"
    "        Xx65LearnService)\n"
    "    learn = Xx65LearnService()\n"
    "    c1 = await learn.collect"
    "_feedback()\n"
    "    out['c1_labeled'] = (\n"
    "        c1.get('labeled'))\n"
    "    out['c1_signals'] = (\n"
    "        c1.get('signals'))\n"
    "    out['c1_pool'] = (\n"
    "        c1.get('poolSubmitted'))\n"
    # ④ 双轮幂等(labeled=0)
    "    c2 = await learn.collect"
    "_feedback()\n"
    "    out['c2_labeled'] = (\n"
    "        c2.get('labeled'))\n"
    "    out['c2_skipped'] = (\n"
    "        c2.get('skipped'))\n"
    # ⑤ 幂等键留库(
    #    Redis 读回)
    "    from repositories.xx65"
    "_repository import (\n"
    "        Xx65Repository)\n"
    "    repo = Xx65Repository()\n"
    "    p1 = await repo.get_product(\n"
    "        pub1['productId'])\n"
    "    p2 = await repo.get_product(\n"
    "        pub2['productId'])\n"
    "    out['p1_pooled'] = (\n"
    "        int(p1.get(\n"
    "            'pooledFeedbackId')\n"
    "            or 0) > 0)\n"
    "    out['p2_pooled'] = (\n"
    "        int(p2.get(\n"
    "            'pooledFeedbackId')\n"
    "            or 0) > 0)\n"
    "    out['p1_reward'] = (\n"
    "        p1.get('poolReward'))\n"
    "    out['p2_reward'] = (\n"
    "        p2.get('poolReward'))\n"
    # ⑥ learn/status
    "    st = await learn.learn"
    "_status()\n"
    "    out['st_pooled'] = (\n"
    "        st.get(\n"
    "            'pooledProducts'))\n"
    "    out['st_rate'] = (\n"
    "        (st.get('factors')\n"
    "         or {}).get(\n"
    "            'content"
    "Compliance'))\n"
    # ⑦ 调度四任务
    "    from services.xx65"
    "_scheduler import (\n"
    "        run_scheduled_tasks)\n"
    "    sched = await run"
    "_scheduled_tasks()\n"
    "    out['sc_insp'] = (\n"
    "        (sched.get('inspect')\n"
    "         or {}).get('scanned'))\n"
    "    out['sc_collect'] = (\n"
    "        (sched.get('collect')\n"
    "         or {}).get('labeled'))\n"
    "    out['sc_coach'] = (\n"
    "        (sched.get('coach')\n"
    "         or {}).get('shops'))\n"
    "    out['sc_err'] = len(\n"
    "        sched.get('errors') or [])\n"
    # ⑧ scheduler_run 留痕
    "    evs = await repo.list_events(\n"
    "        limit=50)\n"
    "    out['ev_run'] = sum(\n"
    "        1 for e in evs\n"
    "        if e.get('eventType')\n"
    "        == 'scheduler_run')\n"
    # ⑨ 四区看板
    "    from services.xx65"
    "_dashboard_service import (\n"
    "        Xx65DashboardService)\n"
    "    dash = await (\n"
    "        Xx65DashboardService()\n"
    "        .dashboard())\n"
    "    zones = dash.get('zones')\n"
    "    out['dz_zones'] = sorted(\n"
    "        zones or {})\n"
    "    out['dz_pub'] = (\n"
    "        (zones.get('content')\n"
    "         or {}).get(\n"
    "            'published'))\n"
    "    out['dz_flag'] = (\n"
    "        (zones.get('content')\n"
    "         or {}).get(\n"
    "            'complianceFlagged'))\n"
    "    out['dz_pooled'] = (\n"
    "        (zones.get('governance')\n"
    "         or {}).get(\n"
    "            'pooledProducts'))\n"
    "    out['dz_mode'] = (\n"
    "        (dash.get(\n"
    "            'constitution')\n"
    "         or {}).get('mode'))\n"
    # ⑩ 红队七向量
    "    from services.xx65"
    "_redteam_service import (\n"
    "        Xx65RedteamService)\n"
    "    rt = await (\n"
    "        Xx65RedteamService()\n"
    "        .run_all())\n"
    "    out['rt_total'] = (\n"
    "        rt.get('total'))\n"
    "    out['rt_defended'] = (\n"
    "        rt.get('defended'))\n"
    "    out['rt_all'] = (\n"
    "        rt.get('allDefended'))\n"
    "    out['rt_vec'] = sorted(\n"
    "        v['vector'] for v in\n"
    "        (rt.get('vectors')\n"
    "         or []))\n"
    # ⑪ 种子自清理验证
    "    shops = await repo.list"
    "_shops(limit=500)\n"
    "    out['rt_residue'] = sum(\n"
    "        1 for s in shops\n"
    "        if 9881 <= s.get(\n"
    "            'ownerId') < 9901\n"
    "        and s.get('status')\n"
    "        != 'closed')\n"
    # ⑫ 44号档案
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
    # 日志行可能混入 stdout——
    # 取最后一个以 { 开头的行
    lines = (out.stdout or "").strip() \
        .splitlines()
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {"error": (out.stderr
                      or "无输出")[-1500:]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_xx65(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+回流宪法]")
    # 服务器态 off——红队 409
    ok, (code, _) = call(
        "POST", "/api/xx65/redteam",
        headers=ADMIN, expect=(409,))
    record("off 态 redteam 409",
           code == 409, str(code))
    # 回流通道 off 可用(宪法)
    ok, (code, _) = call(
        "POST", "/api/xx65/feedback/collect",
        headers=ADMIN)
    record("off 态 collect 可用"
           "(宪法通道)",
           code == 200, str(code))

    print("\n[03 容器内: 回流+调度+"
          "看板+红队全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("开店+2 商品",
           (r.get("shop") or 0) == 1,
           str(r.get("shop")))
    record("首轮 2 信号"
           "(ok+flagged)",
           r.get("c1_labeled") == 2
           and (r.get("c1_signals")
                or {}).get(
                    "shop_ok") == 1
           and (r.get("c1_signals")
                or {}).get(
                    "shop_flagged") == 1,
           str(r.get("c1_signals")))
    record("池双写提交(2)",
           r.get("c1_pool") == 2,
           str(r.get("c1_pool")))
    record("双轮幂等"
           "(labeled=0/skipped=2)",
           r.get("c2_labeled") == 0
           and r.get("c2_skipped") == 2,
           str((r.get("c2_labeled"),
                r.get("c2_skipped"))))
    record("幂等键 Redis 读回"
           "(pooled>0)",
           r.get("p1_pooled") is True
           and r.get("p2_pooled")
           is True,
           str((r.get("p1_pooled"),
                r.get("p2_pooled"))))
    record("奖惩符号读回"
           "(+1/-1)",
           r.get("p1_reward") == 1.0
           and r.get("p2_reward")
           == -1.0,
           str((r.get("p1_reward"),
                r.get("p2_reward"))))
    record("learn/status"
           "(pooled=2/率 0.5)",
           r.get("st_pooled") == 2
           and r.get("st_rate") == 0.5,
           str((r.get("st_pooled"),
                r.get("st_rate"))))
    record("调度四任务"
           "(巡检/回流/教练)",
           r.get("sc_insp") == 2
           and r.get("sc_collect") == 0
           and r.get("sc_coach") == 1
           and (r.get("sc_err")
                or 0) == 0,
           str((r.get("sc_insp"),
                r.get("sc_collect"),
                r.get("sc_coach"),
                r.get("sc_err"))))
    record("scheduler_run 留痕",
           (r.get("ev_run") or 0) >= 1,
           str(r.get("ev_run")))
    record("四区看板齐备",
           r.get("dz_zones")
           == ["campaigns",
               "content",
               "governance",
               "shops"],
           str(r.get("dz_zones")))
    record("看板内容区"
           "(published=2/标记=1)",
           r.get("dz_pub") == 2
           and r.get("dz_flag") == 1,
           str((r.get("dz_pub"),
                r.get("dz_flag"))))
    record("看板治理区"
           "(pooled=2)",
           r.get("dz_pooled") == 2,
           str(r.get("dz_pooled")))
    record("看板宪法 mode",
           r.get("dz_mode") == "assist",
           str(r.get("dz_mode")))
    record("红队七向量齐备",
           r.get("rt_total") == 7
           and r.get("rt_vec")
           == [f"RT-0{i}"
               for i in
               range(1, 8)],
           str(r.get("rt_vec")))
    record("红队全防住"
           "(7/7)",
           r.get("rt_all") is True
           and r.get("rt_defended")
           == 7,
           str((r.get("rt_defended"),
                r.get("rt_total"))))
    record("红队种子自清理",
           (r.get("rt_residue")
            or 0) == 0,
           str(r.get("rt_residue")))
    record("44号 40 档案",
           r.get("reg_n") == 41,
           str(r.get("reg_n")))

    print("\n[04 HTTP 端点+鉴权]")
    # 回流(服务器态 off 可用
    # ——已入池幂等 0)
    ok, (code, body) = call(
        "POST", "/api/xx65/feedback/collect",
        headers=ADMIN)
    record("HTTP collect 幂等"
           "(labeled=0)",
           code == 200
           and (body or {}).get(
               "labeled") == 0,
           str((code,
                (body or {}).get(
                    "labeled"))))
    # learn/status 观测面
    ok, (code, body) = call(
        "GET", "/api/xx65/learn/status",
        headers=ADMIN)
    record("HTTP learn/status 200",
           code == 200
           and (body or {}).get(
               "pooledProducts") == 2,
           str((code,
                (body or {}).get(
                    "pooledProducts"))))
    # dashboard 观测面
    ok, (code, body) = call(
        "GET", "/api/xx65/dashboard",
        headers=ADMIN)
    record("HTTP dashboard 200"
           "(四区)",
           code == 200
           and set((body or {})
                   .get("zones")
                   or {})
           == {"shops", "content",
               "campaigns",
               "governance"},
           str((code,
                sorted((body or {})
                       .get("zones")
                       or {}))))
    # 鉴权 403(无 Role)
    for method, path in (
            ("POST",
             "/api/xx65/feedback/collect"),
            ("GET",
             "/api/xx65/learn/status"),
            ("GET",
             "/api/xx65/dashboard"),
            ("POST",
             "/api/xx65/redteam")):
        resp_ok, (c, _) = call(
            method, path, body={})
        short = path.split('/')[-1] \
            .split('?')[0]
        record(f"HTTP {short}"
               f" 无 Role 403",
               c == 403, str(c))
    # 路由累计 29
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
    record("65号路由 P4 29 端点",
           count == 29, str(count))


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
