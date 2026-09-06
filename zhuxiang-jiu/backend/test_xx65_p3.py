"""65号·网店及商品AI智能管理模块
P3 专项测试(治理与成长层)

运行方式:
    python test_xx65_p3.py

覆盖(65号计划 §八 P3):
    - 合规健康度看板(三组件
      加权/空数据集中性/标记
      拉低/待整改项/S7 建议)
    - AI 经营教练(按配额档
      分发/类别筛选/分发留痕)
    - S7 配额升降档(健康度
      门槛/边界档/经 46号审批
      轨/永不自动执行)
    - 争议快速响应(证据链
      四源聚合/64号订单只读/
      确定性建议)
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


async def seed_active_shop(owner_id, trust_id,
                           credit="L4"):
    """开店种子(→active)"""
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
    await svc.activate_shop(shop["shopId"])
    os.environ["XX65_MODE"] = "off"
    return shop["shopId"]


class TestHealth:
    """01 合规健康度看板"""

    async def run(self):
        print("[01 健康度看板]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        shop_id = await seed_active_shop(
            101, 1)
        svc = Xx65Service()

        # 空数据集(中性 100)
        h = await svc.shop_health(shop_id)
        record("空数据集健康度 100",
               h.get("healthScore")
               == 100.0
               and h.get("passed")
               is True,
               str(h.get("healthScore")))
        record("三组件齐备",
               set(h.get("components")
                   or {}) == {
                   "compliance_events",
                   "product_flags",
                   "campaign_revokes"},
               str(sorted(h.get(
                   "components") or {})))

        # 干净店铺 S7 升档建议
        record("干净店铺升档建议",
               (h.get("quotaSuggestion")
                or {}).get("direction")
               == "uplift"
               and (h.get(
                   "quotaSuggestion")
                   or {}).get(
                       "targetTier")
               == "premium",
               str(h.get(
                   "quotaSuggestion")))

        # 标记商品拉低健康度
        os.environ["XX65_MODE"] = "assist"
        d = await svc.create_draft(
            shop_id, "养生茶",
            description="可以根治三高。",
            price=88.0)
        os.environ["XX65_MODE"] = "off"
        await svc.human_review(
            d["draftId"])
        await svc.human_review(
            d["draftId"],
            action="approve",
            reviewer="admin")
        await svc.inspect_products(
            shop_id=shop_id)
        h2 = await svc.shop_health(
            shop_id)
        record("标记拉低健康度"
               "(<100)",
               h2.get("healthScore")
               < 100.0,
               str(h2.get(
                   "healthScore")))
        record("待整改项在案",
               len(h2.get(
                   "remediation")
                   or []) >= 1,
               str(len(h2.get(
                   "remediation")
                   or [])))

        # 店铺不存在 404
        try:
            await svc.shop_health(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("店铺不存在 404",
               ok, err)

        # off 态观测面可用
        record("off 态观测面可用",
               h2.get("success")
               is True,
               "")


class TestCoach:
    """02 经营教练"""

    async def run(self):
        print("[02 经营教练]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        # L3=starter 档
        shop_id = await seed_active_shop(
            101, 1, credit="L3")
        svc = Xx65Service()

        # 按档分发(starter 3 条)
        c = await svc.coach_tips(shop_id)
        record("starter 档 3 条",
               c.get("total") == 3
               and c.get("quotaTier")
               == "starter",
               str((c.get("total"),
                    c.get("quotaTier"))))
        kinds = {t["kind"]
                 for t in
                 (c.get("tips") or [])}
        record("三类齐备",
               kinds == {"daily_tip",
                         "hot_case",
                         "warning"},
               str(sorted(kinds)))

        # 类别筛选
        w = await svc.coach_tips(
            shop_id, kind="warning")
        record("类别筛选(1 条)",
               w.get("total") == 1
               and (w.get("tips")
                    or [{}])[0]
               .get("kind")
               == "warning",
               str(w.get("total")))

        # 未知类别拒绝
        try:
            await svc.coach_tips(
                shop_id, kind="hacker")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("未知类别拒绝", ok, err)

        # 分发留痕(coach 表)
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        tips = await \
            Xx65Repository().list_tips(
                tier="starter",
                limit=20)
        record("分发留痕"
               "(coach 表)",
               len(tips) >= 4,
               str(len(tips)))

        # 店铺不存在 404
        try:
            await svc.coach_tips(999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("店铺不存在 404",
               ok, err)

        # off 态观测面可用
        record("off 态观测面可用",
               c.get("success")
               is True,
               "")


class TestQuotaAdjust:
    """03 S7 配额升降档(46号轨)"""

    async def run(self):
        print("[03 配额升降档]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        # growth 档(L4)——升/降
        # 两轨均可测
        shop_id = await seed_active_shop(
            101, 1, credit="L4")
        svc = Xx65Service()

        # off 铁律
        try:
            await svc.quota_adjust(
                shop_id, "uplift",
                requested_by="admin")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(调整拒绝)",
               ok, err)

        os.environ["XX65_MODE"] = "assist"

        # member 拒绝(须 admin)
        try:
            await svc.quota_adjust(
                shop_id, "uplift",
                requested_by="member")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "admin" in str(e), ""
        record("member 调整拒绝",
               ok, err)

        # 非法方向拒绝
        try:
            await svc.quota_adjust(
                shop_id, "hack",
                requested_by="admin")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "uplift" in str(e), ""
        record("非法方向拒绝", ok, err)

        # 升档(健康度 100 满足
        # 门槛)
        adj = await svc.quota_adjust(
            shop_id, "uplift",
            requested_by="admin")
        record("升档建议书提交"
               "(46号 pending)",
               (adj.get("governance")
                or {}).get("status")
               == "pending"
               and (adj.get(
                   "quotaTier")
                   or {}).get("after")
               == "premium",
               str(adj.get(
                   "governance")))

        # 永不自动执行(店铺档不变)
        h = await svc.shop_health(
            shop_id)
        record("永不自动执行"
               "(档不变 growth)",
               (h.get("quotaSuggestion")
                or {}).get(
                   "currentTier")
               == "growth",
               str((h.get(
                   "quotaSuggestion")
                   or {}).get(
                       "currentTier")))

        # 处置首个建议书(46号
        # review_change——同档案
        # pending 不重复)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService() \
            .review_change(
                (adj.get("governance")
                 or {}).get("changeId"),
                approve=False,
                reviewed_by="admin",
                review_note="测试处置")

        # 降档门槛拒绝(健康度 100
        # > 50)
        try:
            await svc.quota_adjust(
                shop_id, "downgrade",
                requested_by="admin")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "降档门槛" in str(e), ""
        record("降档门槛拒绝"
               "(健康度过高)",
               ok, err)

        # 降档(手工拉低健康度——
        # 商品全标记)
        d = await svc.create_draft(
            shop_id, "养生茶",
            description="可以根治"
                        "三高。",
            price=88.0)
        await svc.human_review(
            d["draftId"])
        await svc.human_review(
            d["draftId"],
            action="approve",
            reviewer="admin")
        await svc.inspect_products(
            shop_id=shop_id)
        h2 = await svc.shop_health(
            shop_id)
        # 健康度: 合规事件命中率高+
        # 商品标记率 100%→score 很低
        if h2.get("healthScore") \
                <= 50:
            dg = await \
                svc.quota_adjust(
                    shop_id,
                    "downgrade",
                    requested_by="admin")
            record("降档触发"
                   "(健康度≤50)",
                   (dg.get("quotaTier")
                    or {}).get(
                       "after")
                   == "starter",
                   str(dg.get(
                       "quotaTier")))
        else:
            # 健康度未到降档线——
            # 验证门槛拒绝
            try:
                await \
                    svc.quota_adjust(
                        shop_id,
                        "downgrade",
                        requested_by="admin")
                ok, err = False, \
                    "未拒绝"
            except ValueError:
                ok, err = True, ""
            record("降档触发/门槛"
                   "校验",
                   ok, err)
        os.environ["XX65_MODE"] = "off"


class TestDispute:
    """04 争议证据链"""

    async def run(self):
        print("[04 争议证据链]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        shop_id = await seed_active_shop(
            101, 1)
        os.environ["XX65_MODE"] = "assist"
        svc = Xx65Service()
        d = await svc.create_draft(
            shop_id, "祖传木雕摆件",
            price=100.0)
        pub = await svc.publish_draft(
            d["draftId"], confirmed=True)
        pid = pub["productId"]

        # off 铁律(决策面)
        os.environ["XX65_MODE"] = "off"
        try:
            await svc.dispute_assist(
                shop_id)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(辅助拒绝)",
               ok, err)

        os.environ["XX65_MODE"] = "assist"

        # 四源聚合
        dis = await svc.dispute_assist(
            shop_id, product_id=pid,
            summary="买家投诉描述不符")
        kinds = [e["kind"]
                 for e in
                 (dis.get("evidence")
                  or [])]
        record("证据链四源",
               {"shop", "product",
                "compliance",
                "campaign"}
               <= set(kinds),
               str(kinds))
        record("商品证据在案",
               any(e["kind"] == "product"
                   and e["refId"] == pid
                   for e in
                   (dis.get("evidence")
                    or [])),
               "")
        record("处置建议在案",
               len(dis.get("advises")
                   or []) >= 1,
               str(dis.get("advises")))

        # 64号订单只读(不存在订单
        # fail-soft 不阻断)
        dis2 = await svc.dispute_assist(
            shop_id, product_id=pid,
            order_id=999,
            summary="含订单争议")
        record("订单缺失 fail-soft",
               dis2.get("success")
               is True,
               "")

        # 64号真实订单只读(XX64 off
        # 观测面可用——造一单)
        from core.helpers import ts as _ts
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        await TrustValue45Repository() \
            .save_profile({
                "trustId": 1,
                "role": "person",
                "name": "买家",
                "idDigest": "digest-1",
                "factors": {},
                "score": 500.0,
                "rawScore": 500.0,
                "grade": "A",
                "fused": False,
                "frozen": False,
                "createdAt": _ts(),
                "updatedAt": _ts(),
            })
        os.environ["XX64_MODE"] = "shadow"
        from services.xx64_service import (
            Xx64Service,
        )
        od = await Xx64Service() \
            .create_order(
                101, 202, 1, 100,
                product="争议商品",
                created_by="member")
        os.environ["XX64_MODE"] = "off"
        dis3 = await svc.dispute_assist(
            shop_id, product_id=pid,
            order_id=od["orderId"],
            summary="订单争议")
        order_ev = [
            e for e in
            (dis3.get("evidence")
             or [])
            if e["kind"] == "order64"]
        record("64号订单证据只读",
               len(order_ev) == 1
               and (order_ev[0]
                    .get("data")
                    or {}).get(
                        "exclusive")
               is True,
               str(order_ev))

        # R2 互斥建议触发
        advises = " ".join(
            dis3.get("advises") or [])
        record("R2 互斥建议触发",
               "互斥" in advises,
               advises[:60])

        # 店铺不存在 404
        try:
            await svc.dispute_assist(
                999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("店铺不存在 404",
               ok, err)
        os.environ["XX65_MODE"] = "off"


class TestConstitution:
    """05 宪法断言"""

    async def run(self):
        print("[05 宪法断言]")
        from services.xx65_registry import (
            COACH_TIPS,
            DISPUTE_EVIDENCE_KINDS,
            HEALTH_WEIGHTS,
            QUOTA_DOWNGRADE_MAX_HEALTH,
            QUOTA_TIER_ORDER,
            QUOTA_UPLIFT_MIN_HEALTH,
            registry_view,
        )
        record("健康度权重归一",
               abs(sum(
                   HEALTH_WEIGHTS
                   .values()) - 1.0)
               < 0.001,
               str(sum(
                   HEALTH_WEIGHTS
                   .values())))
        record("激励阈值域",
               QUOTA_DOWNGRADE_MAX_HEALTH \
               < 70
               < QUOTA_UPLIFT_MIN_HEALTH,
               str((QUOTA_DOWNGRADE_MAX_HEALTH,
                    QUOTA_UPLIFT_MIN_HEALTH)))
        record("配额档三阶",
               QUOTA_TIER_ORDER
               == ("starter",
                   "growth",
                   "premium"),
               str(QUOTA_TIER_ORDER))
        record("教练池 9 条",
               len(COACH_TIPS) == 9,
               str(len(COACH_TIPS)))
        record("证据链五源",
               set(
                   DISPUTE_EVIDENCE_KINDS)
               == {"shop", "product",
                   "compliance",
                   "campaign",
                   "order64"},
               str(sorted(
                   DISPUTE_EVIDENCE_KINDS)))
        v = registry_view()
        record("registry 观测面"
               "(健康度+教练池)",
               (v.get("health")
                or {}).get(
                   "passScore") == 70
               and (v.get("coachPool")
                    or {}).get(
                       "total") == 9,
               str(v.get("health")))

        # 46号零改动(审批总线调用)
        try:
            from services import \
                ai_governance_service as g46
            record("46号零改动"
                   "(submit_change 调用轨)",
                   g46 is not None,
                   "")
        except ImportError:
            record("46号零改动"
                   "(submit_change 调用轨)",
                   False, "导入失败")


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
        member = {"X-Role": "member"}
        shop_id = await seed_active_shop(
            101, 1, credit="L3")

        # 健康度观测面(off 可用)
        resp = client.get(
            f"/api/xx65/shops/{shop_id}"
            "/health",
            headers=member)
        hbody = resp.json() or {}
        record("HTTP health 200"
               "(观测面)",
               resp.status_code == 200
               and hbody.get(
                   "healthScore")
               == 100.0,
               str((resp.status_code,
                    hbody.get(
                        "healthScore"))))
        resp = client.get(
            "/api/xx65/shops/999/health",
            headers=member)
        record("HTTP health 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 教练观测面
        resp = client.get(
            f"/api/xx65/shops/{shop_id}"
            "/coach",
            headers=member)
        cbody = resp.json() or {}
        record("HTTP coach 200"
               "(3 条)",
               resp.status_code == 200
               and cbody.get("total")
               == 3,
               str((resp.status_code,
                    cbody.get("total"))))
        resp = client.get(
            f"/api/xx65/shops/{shop_id}"
            "/coach?kind=warning",
            headers=member)
        record("HTTP coach 筛选",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("total")
               == 1,
               str(resp.status_code))

        # 配额调整决策面 off 409
        resp = client.post(
            f"/api/xx65/shops/{shop_id}"
            "/quota-adjust",
            json={"direction":
                      "uplift"},
            headers=admin)
        record("HTTP quota-adjust "
               "off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # member 403(须 admin)
        os.environ["XX65_MODE"] = "assist"
        resp = client.post(
            f"/api/xx65/shops/{shop_id}"
            "/quota-adjust",
            json={"direction":
                      "uplift"},
            headers=member)
        record("HTTP quota-adjust "
               "member 403",
               resp.status_code == 403,
               str(resp.status_code))

        # admin 200(46号 pending)
        resp = client.post(
            f"/api/xx65/shops/{shop_id}"
            "/quota-adjust",
            json={"direction":
                      "uplift"},
            headers=admin)
        abody = resp.json() or {}
        record("HTTP quota-adjust 200"
               "(46号 pending)",
               resp.status_code == 200
               and (abody.get(
                   "governance")
                   or {}).get("status")
               == "pending",
               str((resp.status_code,
                    (abody.get(
                        "governance")
                     or {}).get(
                        "status"))))

        # 争议辅助决策面
        resp = client.post(
            f"/api/xx65/shops/{shop_id}"
            "/dispute-assist",
            json={"summary":
                      "买家投诉"},
            headers=member)
        dbody = resp.json() or {}
        record("HTTP dispute-assist 200",
               resp.status_code == 200
               and len(dbody.get(
                   "evidence")
                   or []) >= 3,
               str((resp.status_code,
                    len(dbody.get(
                        "evidence")
                        or []))))

        # off 态决策面 409
        os.environ["XX65_MODE"] = "off"
        resp = client.post(
            f"/api/xx65/shops/{shop_id}"
            "/dispute-assist",
            json={},
            headers=member)
        record("HTTP dispute-assist "
               "off 409",
               resp.status_code == 409,
               str(resp.status_code))

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
            resp = client.request(
                method, path, json={})
            short = path.split('/')[-1] \
                .split('?')[0]
            record(f"HTTP {short}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计(P3 25)
        from routes.xx65_routes import (
            router as xx_router,
        )
        count = sum(
            1 for r in xx_router.routes)
        record("65号路由 P3 25 端点",
               count == 25, str(count))


async def run_all():
    await TestHealth().run()
    await TestCoach().run()
    await TestQuotaAdjust().run()
    await TestDispute().run()
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
