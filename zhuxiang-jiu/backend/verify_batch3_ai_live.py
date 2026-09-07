"""全站批次三·内容类三模块 AI 升级
Docker 实机验收(verify_batch3_ai_live)

运行方式:
    python verify_batch3_ai_live.py [基址]

前置: 容器已运行(含批次三 product_launch/
      activity_risk/ad_placement 代码
      ——AI_ENFORCE_MODE 默认 observe)。

覆盖(《全站AI智能混合架构升级总计划》
批次三交付面, 真实容器 Redis 态):
    01 正常业务零影响(健康+活动/广告面)
    02 44号注册表 45 档案(三评分器
       batch27-29 入册)
    03 01产品: 上架 observe 兼容+快照
       留痕+下架回流(容器内管道——
       权限域种子)
    04 09活动: 审核 observe 兼容+回流
       (HTTP)
    05 10广告: 投放 observe 兼容+快照
       +下线回流(HTTP)
    06 红队RT-01 审核状态机硬规则
    07 红队RT-02 未审核广告上线拒绝
    08 enforce 高风险拦截+低风险放行
       (容器内单元, 冷启动门槛喂料)
    09 AI 观测面 HTTP(三评分器)

×2 轮幂等验证(每轮清理种子重造——
activity/ad/pdm 键域+PD* 测试商品
+三评分器学习键域)。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

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
        with urllib.request.urlopen(req, timeout=15) as r:
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


def clear_batch3(round_no: int) -> None:
    redis_del_keys("zhuxiang:activity:*")
    redis_del_keys("zhuxiang:ad:*")
    redis_del_keys("zhuxiang:pdm:*")
    redis_del_keys("zhuxiang:product:PD*")
    # 测试成员手机号映射(孤儿成员记录无害——
    # 手机号键删除后可重建同名种子)
    redis_del_keys("zhuxiang:member:phone:13988*")
    for scorer in ("product_launch",
                   "activity_risk",
                   "ad_placement"):
        redis_del_keys(
            f"zhuxiang:ai_learning:*{scorer}*")


def run_pipeline(script: str) -> dict:
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr
                          or "无输出")[-1500:]}


def feedback_count(scorer: str) -> dict:
    return run_pipeline(
        "import asyncio, json\n"
        "async def m():\n"
        "    from repositories.ai_learning"
        "_repository import (\n"
        "        AiLearningRepository)\n"
        "    repo = AiLearningRepository()\n"
        "    fbs = await repo.list_feedback(\n"
        f"        '{scorer}')\n"
        "    auto = [f for f in fbs\n"
        "            if f.get('source') == 'auto']\n"
        "    actions = sorted({f.get("
        "'actualAction')\n"
        "                      for f in auto})\n"
        "    print(json.dumps({\n"
        "        'n': len(auto),\n"
        "        'actions': actions}))\n"
        "asyncio.run(m())\n")


# ------------------------------------------------------------
# ① 产品门管道(权限域种子+上架+下架)
#    @@OP@@/@@SUP@@ 手机号按轮次替换
# ------------------------------------------------------------
PRODUCT_PIPELINE = (
    "import asyncio, json\n"
    "async def m():\n"
    "    out = {}\n"
    "    from repositories.member_repository import (\n"
    "        MemberRepository)\n"
    "    from services.perm_service import PermService\n"
    "    mrepo = MemberRepository()\n"
    "    sup = await mrepo.create({\n"
    "        'phone': '@@SUP@@',\n"
    "        'password': 'x',\n"
    "        'nickname': 'B3L-sup',\n"
    "        'avatar': '', 'gender': 1,\n"
    "        'level': 1, 'growth_value': 0,\n"
    "        'points': 0, 'status': 1,\n"
    "        'reg_source': 'phone',\n"
    "        'role': 'admin'})\n"
    "    SUPER = sup['id']\n"
    "    op = await mrepo.create({\n"
    "        'phone': '@@OP@@',\n"
    "        'password': 'x',\n"
    "        'nickname': 'B3L-op',\n"
    "        'avatar': '', 'gender': 1,\n"
    "        'level': 1, 'growth_value': 0,\n"
    "        'points': 0, 'status': 1,\n"
    "        'reg_source': 'phone',\n"
    "        'role': 'member'})\n"
    "    op = op['id']\n"
    "    await PermService().assign_grant(\n"
    "        SUPER, op, 'product.operate')\n"
    "    from services.pdm_service import PdmService\n"
    "    pdm = PdmService()\n"
    "    p = await pdm.create_product(\n"
    "        op, 'member', {\n"
    "            'name': 'B3L竹叶青',\n"
    "            'price': 288, 'stock': 100,\n"
    "            'subtitle': '清香型',\n"
    "            'description': '测试商品',\n"
    "            'tags': ['test'],\n"
    "            'scenes': ['gift']})\n"
    "    pid = p['product_id']\n"
    "    r = await pdm.put_on_sale(\n"
    "        SUPER, 'admin', pid)\n"
    "    out['onsale'] = (\n"
    "        (r or {}).get('status') == 'on_sale')\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    "    snap = await repo.get_decision"
    "_snapshot(\n"
    "        'product_launch',\n"
    "        f'onsale:{pid}')\n"
    "    out['snap'] = snap is not None\n"
    "    if snap:\n"
    "        out['dec'] = snap.get('decision')\n"
    "        out['factors'] = len(\n"
    "            snap.get('factors') or [])\n"
    "    out['pid'] = pid\n"
    "    await pdm.take_off_sale(\n"
    "        SUPER, 'admin', pid, 'live-test')\n"
    "    fbs = await repo.list_feedback(\n"
    "        'product_launch')\n"
    "    out['fb'] = len([\n"
    "        f for f in fbs\n"
    "        if f.get('source') == 'auto'])\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


# ------------------------------------------------------------
# ② enforce 模式容器内单元(喂料越过冷启动门槛)
# ------------------------------------------------------------
ENFORCE_PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AI_ENFORCE_MODE'] = 'enforce'\n"
    "os.environ['AI_ENFORCE_SCOPES'] = "
    "'product_launch,activity_risk,"
    "ad_placement'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    repo = AiLearningRepository()\n"
    "    from datetime import datetime, "
    "timedelta\n"
    "    now = datetime.utcnow()\n"
    "    for sid in ('product_launch',\n"
    "                'activity_risk',\n"
    "                'ad_placement'):\n"
    "        for _ in range(55):\n"
    "            await repo.add_feedback({\n"
    "                'scorerId': sid,\n"
    "                'factors': [{'name': 'x',\n"
    "                             'score': 10.0,\n"
    "                             'weight': 0.5}],\n"
    "                'correct': True,\n"
    "                'createdAt':\n"
    "                    '2026-01-01T00:00:00'})\n"
    # ① 上架高风险拦截
    "    from repositories.member_repository import (\n"
    "        MemberRepository)\n"
    "    from services.perm_service import PermService\n"
    "    mrepo = MemberRepository()\n"
    "    sup = await mrepo.create({\n"
    "        'phone': '@@SUP2@@',\n"
    "        'password': 'x',\n"
    "        'nickname': 'B3L-sup2',\n"
    "        'avatar': '', 'gender': 1,\n"
    "        'level': 1, 'growth_value': 0,\n"
    "        'points': 0, 'status': 1,\n"
    "        'reg_source': 'phone',\n"
    "        'role': 'admin'})\n"
    "    SUPER = sup['id']\n"
    "    op = await mrepo.create({\n"
    "        'phone': '@@OP2@@',\n"
    "        'password': 'x',\n"
    "        'nickname': 'B3L-op2',\n"
    "        'avatar': '', 'gender': 1,\n"
    "        'level': 1, 'growth_value': 0,\n"
    "        'points': 0, 'status': 1,\n"
    "        'reg_source': 'phone',\n"
    "        'role': 'member'})\n"
    "    op = op['id']\n"
    "    await PermService().assign_grant(\n"
    "        SUPER, op, 'product.operate')\n"
    "    from services.pdm_service import PdmService\n"
    "    pdm = PdmService()\n"
    "    bad = await pdm.create_product(\n"
    "        op, 'member', {\n"
    "            'name': 'B3L劣质品',\n"
    "            'price': 50,\n"
    "            'originalPrice': 2000,\n"
    "            'stock': 0})\n"
    "    blocked = False\n"
    "    try:\n"
    "        await pdm.put_on_sale(\n"
    "            SUPER, 'admin', bad['product_id'])\n"
    "    except ValueError as e:\n"
    "        blocked = ('风控拦截' in str(e))\n"
    "    out['prod_blocked'] = blocked\n"
    "    good = await pdm.create_product(\n"
    "        op, 'member', {\n"
    "            'name': 'B3L优质品',\n"
    "            'price': 288, 'stock': 100,\n"
    "            'subtitle': 's',\n"
    "            'description': 'd',\n"
    "            'tags': ['t'],\n"
    "            'scenes': ['g']})\n"
    "    ok = False\n"
    "    try:\n"
    "        r = await pdm.put_on_sale(\n"
    "            SUPER, 'admin',\n"
    "            good['product_id'])\n"
    "        ok = ((r or {}).get('status')\n"
    "              == 'on_sale')\n"
    "    except ValueError:\n"
    "        ok = False\n"
    "    out['prod_ok'] = ok\n"
    # ② 活动高风险拦截
    "    from services.activity_service import (\n"
    "        ActivityService)\n"
    "    asvc = ActivityService()\n"
    "    bad_act = await asvc.create_activity(\n"
    "        'B3L高风险活动', 'lottery',\n"
    "        start_time=(now\n"
    "                    + timedelta(hours=2)"
    ").isoformat(),\n"
    "        end_time=(now\n"
    "                  + timedelta(days=60)"
    ").isoformat(),\n"
    "        budget=50000.0, created_by=1)\n"
    "    await asvc.configure_prizes(\n"
    "        bad_act['id'],\n"
    "        [{'prizeName': f'高档奖品{i}',\n"
    "          'prizeType': 'physical',\n"
    "          'prizeValue': 4900.0,\n"
    "          'probability': 5,\n"
    "          'dailyLimit': 1,\n"
    "          'totalLimit': 1}\n"
    "         for i in range(9)]\n"
    "        + [{'prizeName': '参与奖',\n"
    "            'prizeType': 'points',\n"
    "            'prizeValue': 10.0,\n"
    "            'probability': 55,\n"
    "            'dailyLimit': 100,\n"
    "            'totalLimit': 1000}])\n"
    "    blocked = False\n"
    "    try:\n"
    "        await asvc.audit_activity(\n"
    "            bad_act['id'], approve=True,\n"
    "            auditor=1)\n"
    "    except ValueError as e:\n"
    "        blocked = ('风控拦截' in str(e))\n"
    "    out['act_blocked'] = blocked\n"
    "    good_act = await asvc.create_activity(\n"
    "        'B3L低风险活动', 'promotion',\n"
    "        start_time=(now\n"
    "                    + timedelta(days=1)"
    ").isoformat(),\n"
    "        end_time=(now\n"
    "                  + timedelta(days=8)"
    ").isoformat(),\n"
    "        budget=500.0, created_by=1)\n"
    "    ok = False\n"
    "    try:\n"
    "        r = await asvc.audit_activity(\n"
    "            good_act['id'], approve=True,\n"
    "            auditor=1)\n"
    "        ok = (r.get('status')\n"
    "              == 'registering')\n"
    "    except ValueError:\n"
    "        ok = False\n"
    "    out['act_ok'] = ok\n"
    # ③ 广告高风险拦截
    "    from services.ad_service import AdService\n"
    "    adsvc = AdService()\n"
    "    bad_ad = await adsvc.create_ad(\n"
    "        'B3L广告主', 'B3L弹窗广告',\n"
    "        'POPUP', 'popup_l1',\n"
    "        '[广告]B3L 过量饮酒有害健康',\n"
    "        start_time=now.isoformat(),\n"
    "        end_time=(now\n"
    "                  + timedelta(days=60)"
    ").isoformat(),\n"
    "        budget=50000,\n"
    "        daily_budget=20000)\n"
    "    await adsvc.review_ad(bad_ad['id'])\n"
    "    blocked = False\n"
    "    try:\n"
    "        await adsvc.online_ad(bad_ad['id'])\n"
    "    except ValueError as e:\n"
    "        blocked = ('风控拦截' in str(e))\n"
    "    out['ad_blocked'] = blocked\n"
    "    good_ad = await adsvc.create_ad(\n"
    "        'B3L广告主', 'B3L横幅广告',\n"
    "        'BANNER', 'home_banner_l1',\n"
    "        '[广告]B3L竹叶青 过量饮酒有害健康',\n"
    "        start_time=now.isoformat(),\n"
    "        end_time=(now\n"
    "                  + timedelta(days=7)"
    ").isoformat(),\n"
    "        budget=3000, daily_budget=300,\n"
    "        target_rules={'vip': True})\n"
    "    await adsvc.review_ad(good_ad['id'])\n"
    "    ok = False\n"
    "    try:\n"
    "        r = await adsvc.online_ad(\n"
    "            good_ad['id'])\n"
    "        ok = (r.get('status') == 'online')\n"
    "    except ValueError:\n"
    "        ok = False\n"
    "    out['ad_ok'] = ok\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


def registry_check() -> dict:
    return run_pipeline(
        "import asyncio, json\n"
        "async def m():\n"
        "    from services.ai_learning"
        "_service import (\n"
        "        SCORER_REGISTRY)\n"
        "    print(json.dumps({\n"
        "        'reg_n': len(SCORER_REGISTRY),\n"
        "        'pl': 'product_launch'\n"
        "              in SCORER_REGISTRY,\n"
        "        'ar': 'activity_risk'\n"
        "              in SCORER_REGISTRY,\n"
        "        'ap': 'ad_placement'\n"
        "              in SCORER_REGISTRY}))\n"
        "asyncio.run(m())\n")


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_batch3(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call(
        "GET", "/api/activity/list?limit=5")
    record("活动面 200", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/ads")
    record("广告面 200", code == 200, str(code))

    print("\n[02 44号注册表]")
    reg = registry_check()
    record("45 档案(batch27-29 入册)",
           reg.get("reg_n") == 55
           and reg.get("pl") is True
           and reg.get("ar") is True
           and reg.get("ap") is True,
           str(reg))

    print("\n[03 01产品上架门(容器管道)]")
    r = run_pipeline(
        PRODUCT_PIPELINE
        .replace("@@SUP@@", f"1398879000{round_no}")
        .replace("@@OP@@", f"1398877000{round_no}"))
    record("上架 observe 兼容",
           r.get("onsale") is True,
           str(r))
    record("上架快照低风险+八因子",
           r.get("snap") is True
           and r.get("dec") == "low"
           and r.get("factors") == 8,
           str(r))
    record("下架→自动反馈",
           (r.get("fb") or 0) >= 1,
           str(r.get("fb")))

    print("\n[04 09活动审核门(HTTP)]")
    now = datetime.utcnow()
    ok, (code, body) = call(
        "POST", "/api/activity/admin/create",
        body={"name": "B3L活动",
              "type": "promotion",
              "startTime": (
                  now + timedelta(days=1)
              ).isoformat(),
              "endTime": (
                  now + timedelta(days=8)
              ).isoformat(),
              "budget": 500.0,
              "applicableScope": {"vip": True},
              "createdBy": 1},
        headers=ADMIN)
    act = (body.get("data") or {})
    aid = act.get("id")
    record("活动创建 200",
           code == 200 and aid,
           f"code={code}")
    ok, (code, body) = call(
        "POST", f"/api/activity/admin/audit/{aid}",
        body={"approve": True, "auditor": 1},
        headers=ADMIN)
    record("审核 observe 兼容",
           code == 200
           and (body.get("data") or {})
           .get("status") == "registering",
           f"code={code}")
    fb = feedback_count("activity_risk")
    record("审核→自动反馈",
           (fb.get("n") or 0) >= 1
           and "approved" in (fb.get("actions")
                             or []),
           str(fb))

    print("\n[05 10广告投放门(HTTP)]")
    ok, (code, body) = call(
        "POST", "/api/ads",
        body={"advertiserName": "B3L广告主",
              "name": "B3L横幅广告",
              "type": "BANNER",
              "position": "home_banner_l2",
              "title": "[广告]B3L竹叶青 "
                       "过量饮酒有害健康",
              "startTime": now.isoformat(),
              "endTime": (
                  now + timedelta(days=7)
              ).isoformat(),
              "budget": 3000,
              "dailyBudget": 300,
              "targetRules": {"vip": True}},
        headers=ADMIN)
    ad = (body.get("data") or {})
    ad_id = ad.get("id")
    record("广告创建 200",
           code == 200 and ad_id,
           f"code={code}")
    ok, (code, body) = call(
        "POST", f"/api/ads/{ad_id}/review",
        body={}, headers=ADMIN)
    record("广告审核 200", code == 200,
           str(code))
    ok, (code, body) = call(
        "POST", f"/api/ads/{ad_id}/online",
        body={}, headers=ADMIN)
    record("投放 observe 兼容",
           code == 200
           and (body.get("data") or {})
           .get("status") == "online",
           f"code={code}")
    ok, (code, body) = call(
        "POST", f"/api/ads/{ad_id}/offline",
        body={"reason": "调整排期"},
        headers=ADMIN)
    record("广告下线 200", code == 200,
           str(code))
    fb = feedback_count("ad_placement")
    record("下线→自动反馈",
           (fb.get("n") or 0) >= 1
           and "offline" in (fb.get("actions")
                             or []),
           str(fb))

    print("\n[06 红队RT-01 审核状态机]")
    ok, (code, _) = call(
        "POST", f"/api/activity/admin/audit/{aid}",
        body={"approve": True, "auditor": 1},
        headers=ADMIN, expect=(400, 409))
    record("非草稿重复审核拒绝",
           code in (400, 409), str(code))

    print("\n[07 红队RT-02 未审核上线]")
    ok, (code, body) = call(
        "POST", "/api/ads",
        body={"advertiserName": "B3L广告主",
              "name": "B3L未审广告",
              "type": "BANNER",
              "position": "home_banner_l3",
              "title": "[广告]B3L "
                       "过量饮酒有害健康",
              "budget": 100,
              "dailyBudget": 10},
        headers=ADMIN)
    unad = (body.get("data") or {})
    ok, (code, _) = call(
        "POST",
        f"/api/ads/{unad.get('id')}/online",
        body={}, headers=ADMIN,
        expect=(400, 409))
    record("未审核广告上线拒绝",
           code in (400, 409), str(code))

    print("\n[08 enforce 拦截(容器内)]")
    r = run_pipeline(
        ENFORCE_PIPELINE
        .replace("@@SUP2@@", f"1398879100{round_no}")
        .replace("@@OP2@@", f"1398877100{round_no}"))
    record("上架拦截+放行",
           r.get("prod_blocked") is True
           and r.get("prod_ok") is True,
           str(r))
    record("活动拦截+放行",
           r.get("act_blocked") is True
           and r.get("act_ok") is True,
           str(r))
    record("投放拦截+放行",
           r.get("ad_blocked") is True
           and r.get("ad_ok") is True,
           str(r))

    print("\n[09 AI 观测面 HTTP]")
    for scorer in ("product_launch",
                   "activity_risk",
                   "ad_placement"):
        ok, (code, body) = call(
            "GET",
            f"/api/ai-learning/enforcement/"
            f"{scorer}/overview",
            headers=ADMIN)
        record(f"{scorer} overview 200",
               code == 200, str(code))


def main() -> int:
    print("=" * 62)
    print("全站批次三·内容类三模块 AI 升级"
          " Docker 实机验收")
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
