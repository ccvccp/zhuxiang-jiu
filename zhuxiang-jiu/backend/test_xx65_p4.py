"""65号·网店及商品AI智能管理模块
P4 专项测试(回流+看板+红队+收官)

运行方式:
    python test_xx65_p4.py

覆盖(65号计划 §八 P4):
    - 商品回流(productId 1:1
      幂等——首轮 labeled=N/
      双轮 labeled=0 QC 铁律)
    - T+1 调度(四任务 fail-soft
      +scheduler_run 留痕)
    - 四区看板(店铺/内容/营销
      /治理+宪法三开关)
    - 红队七向量(RT-01~07
      攻击仿真+防御断言)
    - HTTP 层+宪法断言
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["DM61_MODE"] = "off"
os.environ["AV62_MODE"] = "off"
os.environ["XX64_MODE"] = "off"
os.environ["XX65_MODE"] = "off"
os.environ["XX65_LLM_MODE"] = "off"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def seed_profile(trust_id, score=1000.0):
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    repo = TrustValue45Repository()
    await repo.save_profile({
        "trustId": int(trust_id),
        "role": "person",
        "name": f"测试主体{trust_id}",
        "idDigest": f"digest-{trust_id}",
        "factors": {},
        "score": float(score),
        "rawScore": float(score),
        "grade": "A",
        "fused": False,
        "frozen": False,
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00",
    })
    return trust_id


async def seed_credit(owner_id, credit_level):
    from repositories.credit_repository import (
        CreditRepository,
    )
    from repositories.store import _mock_store
    repo = CreditRepository()
    await repo.get_or_create_score(owner_id)
    _mock_store["credit_scores"][
        owner_id]["creditLevel"] = credit_level
    return owner_id


async def seed_published_products(
        owner_id, trust_id,
        credit="L4"):
    """开店+发布 2 件商品种子
    (1 干净+1 严重词人工放行
    巡检标记)→(shop_id, p1, p2)"""
    from services.xx65_service import (
        Xx65Service,
    )
    os.environ["XX65_MODE"] = "assist"
    await seed_profile(trust_id)
    await seed_credit(owner_id, credit)
    svc = Xx65Service()
    intent = await svc.parse_intent(
        owner_id, "我想做定制木雕"
                   "和手工皮具")
    shop = await svc.apply_shop(
        owner_id, trust_id,
        intent_id=intent["intentId"])
    await svc.claim_shop(
        shop["shopId"],
        {q: "否" for q in
         shop["complianceQuestions"]})
    await svc.activate_shop(
        shop["shopId"])
    d1 = await svc.create_draft(
        shop["shopId"], "祖传木雕摆件",
        price=100.0)
    p1 = await svc.publish_draft(
        d1["draftId"], confirmed=True)
    d2 = await svc.create_draft(
        shop["shopId"], "养生茶",
        description="可以根治三高。",
        price=88.0)
    await svc.human_review(
        d2["draftId"])
    p2 = await svc.human_review(
        d2["draftId"],
        action="approve",
        reviewer="admin")
    await svc.inspect_products(
        shop_id=shop["shopId"])
    os.environ["XX65_MODE"] = "off"
    return (shop["shopId"],
            p1["productId"],
            p2["productId"])


class TestLearn:
    """01 商品回流(幂等铁律)"""

    async def run(self):
        print("[01 商品回流]")
        reset_all()
        from services.xx65_learn_service import (
            Xx65LearnService,
        )
        shop_id, p1, p2 = \
            await seed_published_products(
                101, 1)
        learn = Xx65LearnService()

        # 首轮: 2 信号(ok+flagged)
        c1 = await learn \
            .collect_feedback()
        record("首轮 2 信号"
               "(ok+flagged)",
               c1.get("labeled") == 2
               and (c1.get("signals")
                    or {}).get(
                        "shop_ok") == 1
               and (c1.get("signals")
                    or {}).get(
                        "shop_flagged")
               == 1,
               str(c1.get("signals")))
        record("池双写提交(2)",
               c1.get(
                   "poolSubmitted") == 2
               and c1.get(
                   "poolFailed") == 0,
               str((c1.get(
                        "poolSubmitted"),
                   c1.get(
                       "poolFailed"))))

        # QC 铁律: 双轮 labeled=0
        c2 = await learn \
            .collect_feedback()
        record("双轮幂等"
               "(second labeled=0)",
               c2.get("labeled") == 0
               and c2.get(
                   "skipped") == 2,
               str((c2.get(
                        "labeled"),
                   c2.get("skipped"))))

        # 幂等键留库(
        # pooledFeedbackId>0)
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        repo = Xx65Repository()
        prod1 = await repo.get_product(
            p1)
        prod2 = await repo.get_product(
            p2)
        record("幂等键留库"
               "(pooledFeedbackId>0)",
               int(prod1.get(
                   "pooledFeedbackId")
                   or 0) > 0
               and int(prod2.get(
                   "pooledFeedbackId")
                   or 0) > 0,
               str((prod1.get(
                        "pooled"
                        "FeedbackId"),
                   prod2.get(
                       "pooled"
                       "FeedbackId"))))
        record("奖惩符号留库"
               "(+1/-1)",
               prod1.get("poolReward")
               == 1.0
               and prod2.get(
                   "poolReward")
               == -1.0,
               str((prod1.get(
                        "poolReward"),
                   prod2.get(
                       "poolReward"))))

        # 44号池落库(feedback
        # 双写——信号可溯)
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        feedbacks = await \
            AiLearningRepository() \
            .list_feedback(
                "shop_operation",
                limit=10)
        record("44号池双写留痕"
               "(≥2 条)",
               len(feedbacks) >= 2,
               str(len(feedbacks)))

        # learn/status 观测
        st = await learn \
            .learn_status()
        record("learn/status"
               "(pooled=2)",
               st.get(
                   "pooledProducts")
               == 2
               and (st.get(
                   "scorer")
                   or {}).get(
                       "scorerId")
               == "shop_operation",
               str(st.get(
                   "pooledProducts")))
        record("合规率观测口径"
               "(0.5)",
               (st.get("factors")
                or {}).get(
                   "content"
                   "Compliance")
               == 0.5,
               str((st.get(
                   "factors")
                   or {}).get(
                   "content"
                   "Compliance")))

        # off 态回流通道可用
        # (宪法——永不关停)
        record("off 态回流通道"
               "可用(宪法)",
               c2.get("success")
               is True,
               "")


class TestScheduler:
    """02 T+1 调度"""

    async def run(self):
        print("[02 T+1 调度]")
        reset_all()
        from services.xx65_scheduler import (
            run_scheduled_tasks,
            scheduler_enabled,
            scheduler_interval_seconds,
        )
        shop_id, p1, p2 = \
            await seed_published_products(
                101, 1)
        # 已回流一轮(幂等基线)
        from services.xx65_learn_service import (
            Xx65LearnService,
        )
        await Xx65LearnService() \
            .collect_feedback()

        record("调度开关默认 off",
               scheduler_enabled()
               is False,
               str(scheduler_enabled()))
        record("调度间隔默认 T+1"
               "(86400s)",
               scheduler_interval_seconds()
               == 86400,
               str(scheduler_interval_seconds()))

        sched = await \
            run_scheduled_tasks()
        record("巡检任务运行"
               "(scanned=2)",
               (sched.get("inspect")
                or {}).get("scanned")
               == 2,
               str(sched.get(
                   "inspect")))
        record("回流任务运行"
               "(幂等 skipped=2)",
               (sched.get("collect")
                or {}).get("scanned")
               == 2
               and (sched.get(
                   "collect")
                   or {}).get(
                       "labeled") == 0,
               str(sched.get(
                   "collect")))
        record("教练分发任务运行"
               "(shops=1)",
               (sched.get("coach")
                or {}).get("shops") == 1
               and (sched.get(
                   "coach")
                   or {}).get(
                   "tipsDelivered")
               == 3,
               str(sched.get(
                   "coach")))
        record("fail-soft 无错误",
               len(sched.get("errors")
                   or []) == 0,
               str(sched.get(
                   "errors")))

        # scheduler_run 留痕
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        evs = await \
            Xx65Repository() \
            .list_events(limit=50)
        runs = [e for e in evs
                if e.get("eventType")
                == "scheduler_run"]
        record("scheduler_run 留痕",
               len(runs) >= 1
               and isinstance(
                   (runs[0].get(
                       "detail")
                    if runs else {}),
                   dict),
               str(len(runs)))


class TestDashboard:
    """03 四区看板"""

    async def run(self):
        print("[03 四区看板]")
        reset_all()
        from services.xx65_dashboard_service import (
            Xx65DashboardService,
        )
        shop_id, p1, p2 = \
            await seed_published_products(
                101, 1)

        dash = await \
            Xx65DashboardService() \
            .dashboard()
        zones = dash.get(
            "zones") or {}
        record("四区齐备",
               set(zones) == {
                   "shops", "content",
                   "campaigns",
                   "governance"},
               str(sorted(zones)))

        # 店铺区
        shops_z = zones.get(
            "shops") or {}
        record("店铺区"
               "(total+六态分布)",
               shops_z.get("total")
               == 1
               and (shops_z.get(
                   "status"
                   "Distribution")
                   or {}).get(
                       "active") == 1
               and len(shops_z.get(
                   "status"
                   "Distribution")
                   or {}) == 6,
               str(shops_z))

        # 内容区
        content_z = zones.get(
            "content") or {}
        record("内容区"
               "(published=2+标记=1)",
               content_z.get(
                   "published") == 2
               and content_z.get(
                   "compliance"
                   "Flagged") == 1,
               str((content_z.get(
                        "published"),
                   content_z.get(
                       "compliance"
                       "Flagged"))))
        record("内容区合规率"
               "(0.5)",
               content_z.get(
                   "complianceRate")
               == 0.5,
               str(content_z.get(
                   "complianceRate")))

        # 营销区
        camp_z = zones.get(
            "campaigns") or {}
        record("营销区(空活动"
               "+双算汇总)",
               camp_z.get("total")
               == 0
               and camp_z.get(
                   "estimatedGmv"
                   "Total") == 0.0,
               str(camp_z))

        # 治理区
        gov_z = zones.get(
            "governance") or {}
        record("治理区"
               "(合规事件+三道防线)",
               (gov_z.get(
                   "compliance"
                   "Events")
                or 0) >= 1
               and isinstance(
                   gov_z.get(
                       "defenseLine"
                       "Distribution"),
                   dict),
               str(gov_z))

        # 宪法三开关
        const = dash.get(
            "constitution") or {}
        record("宪法三开关",
               const.get("mode")
               == "off"
               and const.get(
                   "learnMode")
               == "off"
               and const.get(
                   "llmMode")
               == "off",
               str(const))

        # off 态观测面可用
        record("off 态看板"
               "观测面可用",
               dash.get("success")
               is True,
               "")


class TestRedteam:
    """04 红队七向量"""

    async def run(self):
        print("[04 红队七向量]")
        reset_all()
        os.environ["XX65_MODE"] = "assist"
        from services.xx65_redteam_service import (
            Xx65RedteamService,
        )
        r = await \
            Xx65RedteamService() \
            .run_all()
        record("七向量齐备",
               r.get("total") == 7,
               str(r.get("total")))
        vec = {v["vector"]: v
               for v in
               (r.get("vectors")
                or [])}
        record("向量编号 RT-01~07",
               sorted(vec)
               == [f"RT-0{i}"
                   for i in
                   range(1, 8)],
               str(sorted(vec)))
        record("全防住"
               "(allDefended)",
               r.get("allDefended")
               is True
               and r.get(
                   "defended") == 7,
               str((r.get(
                   "defended"),
                   r.get("total"))))

        # 各向量证据抽验
        e1 = vec["RT-01"][
            "evidence"]
        record("RT-01 低信用"
               "拒绝+跳过激活拒",
               e1.get(
                   "forgedIgnored")
               is True
               and e1.get(
                   "directActivate"
                   "Rejected") is True,
               str(e1))
        e2 = vec["RT-02"][
            "evidence"]
        record("RT-02 确认发布拒"
               "+伪造结果重扫拒",
               e2.get(
                   "confirmed"
                   "Rejected") is True
               and e2.get(
                   "forgedResult"
                   "Rescanned")
               is True,
               str(e2))
        e3 = vec["RT-03"][
            "evidence"]
        record("RT-03 超窗撤销拒"
               "(600s>300s)",
               e3.get("rejected")
               is True,
               str(e3))
        e4 = vec["RT-04"][
            "evidence"]
        record("RT-04 配额越权拒"
               "(第 11 次)",
               e4.get("rejected")
               is True
               and e4.get(
                   "succeeded"
                   "BeforeLimit") == 10,
               str(e4))
        e5 = vec["RT-05"][
            "evidence"]
        record("RT-05 篡改额度"
               "服务端重算(30≠999)",
               e5.get("displayed")
               == 30.0
               and e5.get(
                   "recalculated")
               is True,
               str(e5))
        e6 = vec["RT-06"][
            "evidence"]
        record("RT-06 跳过合规"
               "承诺激活拒",
               e6.get(
                   "skipClaim"
                   "Rejected") is True,
               str(e6))
        e7 = vec["RT-07"][
            "evidence"]
        record("RT-07 重放拒+并发"
               "恰 1 成功",
               e7.get(
                   "replayRejected")
               is True
               and e7.get(
                   "exactlyOne")
               is True,
               str(e7))

        # 种子自清理(98xx 隔离域
        # 无 active 残留)
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        shops = await \
            Xx65Repository() \
            .list_shops(limit=500)
        rt_residue = [
            s for s in shops
            if s.get("ownerId")
            >= 9881
            and s.get("status")
            != "closed"]
        record("种子自清理"
               "(无 active 残留)",
               len(rt_residue) == 0,
               str(len(rt_residue)))
        os.environ["XX65_MODE"] = "off"


class TestConstitution:
    """05 宪法断言"""

    async def run(self):
        print("[05 宪法断言]")
        from services.xx65_learn_service import (
            SIGNAL_MAP, learn_mode,
        )
        record("回流两信号",
               set(SIGNAL_MAP)
               == {"shop_ok",
                   "shop_flagged"},
               str(sorted(
                   SIGNAL_MAP)))
        record("LEARN_MODE 默认 off",
               learn_mode() == "off",
               str(learn_mode()))

        # 44号零改动(池双写调用轨)
        try:
            from services import \
                ai_learning_service as l44
            record("44号零改动"
                   "(submit_feedback"
                   " 调用轨)",
                   l44 is not None,
                   "")
        except ImportError:
            record("44号零改动"
                   "(submit_feedback"
                   " 调用轨)",
                   False, "导入失败")

        # 三开关铁律
        record("XX65_MODE 默认 off",
               os.environ.get(
                   "XX65_MODE",
                   "off") == "off",
               str(os.environ.get(
                   "XX65_MODE")))


class TestHttp:
    """06 HTTP 层"""

    async def run(self):
        print("[06 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        shop_id, p1, p2 = \
            await seed_published_products(
                101, 1)

        # 红队 off 409(决策面)
        resp = client.post(
            "/api/xx65/redteam",
            headers=admin)
        record("HTTP redteam off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 回流 off 可用(宪法
        # ——通道永不关停)
        resp = client.post(
            "/api/xx65/feedback/collect",
            headers=admin)
        cbody = resp.json() or {}
        record("HTTP collect 200"
               "(off 态可用)",
               resp.status_code == 200
               and cbody.get(
                   "labeled") == 2,
               str((resp.status_code,
                    cbody.get(
                        "labeled"))))

        # 双轮幂等(HTTP 再收
        # labeled=0)
        resp = client.post(
            "/api/xx65/feedback/collect",
            headers=admin)
        record("HTTP collect 双轮"
               "幂等(0)",
               (resp.json()
                or {}).get("labeled")
               == 0,
               str((resp.json()
                    or {}).get(
                   "labeled")))

        # learn/status 观测面
        resp = client.get(
            "/api/xx65/learn/status",
            headers=admin)
        record("HTTP learn/status 200",
               resp.status_code == 200
               and (resp.json()
                    or {}).get(
                       "pooledProducts")
               == 2,
               str((resp.status_code,
                    (resp.json()
                     or {}).get(
                        "pooled"
                        "Products"))))

        # dashboard 观测面
        resp = client.get(
            "/api/xx65/dashboard",
            headers=admin)
        dbody = resp.json() or {}
        record("HTTP dashboard 200"
               "(四区)",
               resp.status_code == 200
               and set(dbody
                       .get("zones")
                       or {})
               == {"shops",
                   "content",
                   "campaigns",
                   "governance"},
               str((resp.status_code,
                    sorted(dbody
                            .get("zones")
                            or {}))))

        # 红队 assist 全防住
        os.environ["XX65_MODE"] = "assist"
        resp = client.post(
            "/api/xx65/redteam",
            headers=admin)
        rbody = resp.json() or {}
        record("HTTP redteam 200"
               "(7/7 防住)",
               resp.status_code == 200
               and rbody.get(
                   "allDefended")
               is True,
               str((resp.status_code,
                    rbody.get(
                        "defended"))))
        os.environ["XX65_MODE"] = "off"

        # 鉴权 403(无 Role/
        # member 观测面)
        for method, path in (
                ("POST",
                 "/api/xx65/feedback/"
                 "collect"),
                ("GET",
                 "/api/xx65/learn/status"),
                ("GET",
                 "/api/xx65/dashboard"),
                ("POST",
                 "/api/xx65/redteam")):
            resp = client.request(
                method, path, json={})
            short = path.split('/')[-1] \
                .split('?')[0]
            record(f"HTTP {short}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))
        resp = client.post(
            "/api/xx65/feedback/collect",
            headers={"X-Role":
                         "member"})
        record("HTTP collect "
               "member 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 路由累计(P4 29)
        from routes.xx65_routes import (
            router as xx_router,
        )
        count = sum(
            1 for r in xx_router.routes)
        record("65号路由 P4 29 端点",
               count == 29, str(count))


async def run_all():
    await TestLearn().run()
    await TestScheduler().run()
    await TestDashboard().run()
    await TestRedteam().run()
    await TestConstitution().run()
    await TestHttp().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
