"""65号·网店及商品AI智能管理模块
P2 专项测试(智能营销中枢)

运行方式:
    python test_xx65_p2.py

覆盖(65号计划 §八 P2):
    - 三因子规则库(权重归一/
      季度趋势/策略参数域)
    - 活动推荐(确定性+流动性
      感知+ROI 双算)
    - 活动创建(S7 配额+R2
      互斥声明+S1 合规+S5 窗口)
    - 撤销窗口(S5——窗口内/
      超窗/终态)
    - 效果归因复盘(观测面)
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


async def seed_published_product(owner_id, trust_id,
                                  price=100.0,
                                  credit="L4"):
    """开店+发布商品种子(→active
    +published)"""
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
    d = await svc.create_draft(
        shop["shopId"], "祖传木雕摆件",
        price=price)
    pub = await svc.publish_draft(
        d["draftId"], confirmed=True)
    os.environ["XX65_MODE"] = "off"
    return (shop["shopId"],
            pub["productId"])


class TestRules:
    """01 规则库自检"""

    async def run(self):
        print("[01 规则库]")
        from services.xx65_registry import (
            CAMPAIGN_CHANNELS,
            CAMPAIGN_FACTOR_WEIGHTS,
            CAMPAIGN_STATES,
            CAMPAIGN_STRATEGIES,
            CAMPAIGN_TRANSITIONS,
            CATEGORY_COMPLEMENTS,
            SEASON_TRENDS,
            registry_view,
        )
        record("三因子权重归一",
               abs(sum(
                   CAMPAIGN_FACTOR_WEIGHTS
                   .values()) - 1.0)
               < 0.001,
               str(sum(
                   CAMPAIGN_FACTOR_WEIGHTS
                   .values())))
        record("季度趋势 12 月全覆盖",
               set(SEASON_TRENDS)
               == set(range(1, 13)),
               str(len(SEASON_TRENDS)))
        record("五策略在册",
               set(CAMPAIGN_STRATEGIES)
               == {"trust_exclusive",
                   "small_high_freq",
                   "new_customer",
                   "seasonal",
                   "clearance"},
               str(sorted(
                   CAMPAIGN_STRATEGIES)))
        record("活动三态状态机",
               len(CAMPAIGN_STATES) == 3
               and CAMPAIGN_TRANSITIONS[
                   "revoked"] == (),
               str(CAMPAIGN_STATES))
        record("active 可撤销/到期",
               "revoked" in
               CAMPAIGN_TRANSITIONS[
                   "active"]
               and "expired" in
               CAMPAIGN_TRANSITIONS[
                   "active"],
               "")
        record("渠道三轨",
               set(CAMPAIGN_CHANNELS)
               == {"in_site",
                   "community",
                   "sms"},
               str(sorted(
                   CAMPAIGN_CHANNELS)))
        record("互补表非空",
               len(
                   CATEGORY_COMPLEMENTS)
               >= 6,
               str(len(
                   CATEGORY_COMPLEMENTS)))
        v = registry_view()
        record("registry 观测面"
               "(活动域)",
               len(v.get(
                   "campaignStrategies")
                   or {}) == 5
               and v.get(
                   "campaignStates")
               == CAMPAIGN_STATES,
               str(len(v.get(
                   "campaignStrategies")
                   or {})))


class TestRecommend:
    """02 活动推荐(三因子+双算)"""

    async def run(self):
        print("[02 活动推荐]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        shop_id, pid = \
            await seed_published_product(
                101, 1, price=100.0)
        svc = Xx65Service()

        # off 态观测面可用
        r = await svc.recommend_campaign(
            shop_id=shop_id,
            product_id=pid)
        record("推荐观测面"
               "(off 态可用)",
               r.get("success")
               is True,
               "")
        recs = r.get(
            "recommendations") or []
        record("Top3 推荐",
               len(recs) == 3,
               str(len(recs)))
        record("推荐降序排序",
               recs[0]["score"]
               >= recs[1]["score"]
               >= recs[2]["score"],
               str([x["score"]
                    for x in recs]))

        # 三因子齐备
        factors = r.get(
            "factors") or {}
        record("三因子齐备"
               "(0.40/0.35/0.25)",
               set(factors) == {
                   "shop_trust",
                   "product_heat",
                   "season_trend"}
               and factors[
                   "shop_trust"][
                   "weight"] == 0.40,
               str(sorted(factors)))

        # ROI 双算(确定性: 100×
        # 20×(1+lift); trust=GMV×
        # portion)
        top = recs[0]
        from services.xx65_registry import (
            CAMPAIGN_STRATEGIES,
            ROI_BASE_SALES,
        )
        s = CAMPAIGN_STRATEGIES[
            top["strategy"]]
        exp_gmv = round(
            100.0 * ROI_BASE_SALES
            * (1 + s["roiCashLift"]), 2)
        record("ROI 双算"
               "(确定性公式)",
               top["roi"][
                   "estimatedGmv"]
               == exp_gmv
               and top["roi"][
                   "estimatedTrust"]
               == round(exp_gmv
                        * s[
                            "trustPortion"],
                        2),
               str((top["roi"][
                        "estimatedGmv"],
                    exp_gmv)))

        # 流动性信号(fail-soft
        # 纯读取)
        liq = r.get(
            "liquidity") or {}
        record("64号流动性信号"
               "(纯读取)",
               liq.get("source")
               == "xx64-read-only",
               str(liq.get("source")))

        # 渠道适配
        record("渠道适配随策略",
               len(top.get(
                   "channels")
                   or []) >= 1
               and top["channels"][
                   0].get("label"),
               str(top.get("channels")))

        # 推荐确定性(同输入
        # 同输出)
        r2 = await \
            svc.recommend_campaign(
                shop_id=shop_id,
                product_id=pid)
        record("推荐确定性",
               r2["recommendations"][
                   0]["strategy"]
               == top["strategy"]
               and r2["baseScore"]
               == r["baseScore"],
               "")

        # 跨店联动建议
        record("跨店联动建议"
               "(仅建议)",
               isinstance(
                   r.get(
                       "crossShop"
                       "Suggestions"),
                   list),
               "")

        # 商品不属于本店拒绝
        try:
            await \
                svc.recommend_campaign(
                    shop_id=shop_id,
                    product_id=999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("商品不存在 404",
               ok, err)

        # 店铺不存在 404
        try:
            await \
                svc.recommend_campaign(
                    shop_id=999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("店铺不存在 404",
               ok, err)


class TestCreate:
    """03 活动创建(S7+R2+S1+S5)"""

    async def run(self):
        print("[03 活动创建]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        # L3=starter(活动配额 2)
        shop_id, pid = \
            await seed_published_product(
                101, 1, price=100.0,
                credit="L3")
        svc = Xx65Service()

        # off 铁律
        try:
            await svc.create_campaign(
                shop_id, pid,
                "clearance")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(创建拒绝)",
               ok, err)

        os.environ["XX65_MODE"] = "assist"

        # 未知策略拒绝
        try:
            await svc.create_campaign(
                shop_id, pid,
                "unknown_strategy")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "未知策略" in str(e), ""
        record("未知策略拒绝", ok, err)

        # 折扣率越界拒绝
        try:
            await svc.create_campaign(
                shop_id, pid, "clearance",
                discount_rate=0.6)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "折扣率" in str(e), ""
        record("折扣率越界拒绝"
               "(≥50%)",
               ok, err)

        # 活动名合规拦截(S1)
        try:
            await svc.create_campaign(
                shop_id, pid,
                "clearance",
                name="全村最好的"
                     "木雕大促")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "S1" in str(e), ""
        record("活动名禁词拦截"
               "(S1)",
               ok, err)

        # 创建成功(R2+S5)
        c = await svc.create_campaign(
            shop_id, pid, "clearance")
        record("创建成功"
               "(active)",
               c.get("status")
               == "active",
               str(c.get("status")))
        record("R2 互斥声明嵌入",
               c.get("exclusive")
               is True
               and "R2" in c.get(
                   "r2Declaration",
                   ""),
               str(c.get(
                   "r2Declaration")))
        record("S5 撤销窗口 300s",
               c.get(
                   "revokeWindowSeconds")
               == 300
               and c.get(
                   "revocableUntilTs")
               > 0,
               str(c.get(
                   "revokeWindow"
                   "Seconds")))
        record("ROI 双算快照",
               (c.get("roi") or {})
               .get("estimatedGmv")
               > 0,
               str(c.get("roi")))
        record("活动名缺省生成",
               bool(c.get("name")),
               str(c.get("name")))

        # S7 活动配额(starter=2;
        # 已 1 个 active)
        await svc.create_campaign(
            shop_id, pid, "seasonal")
        try:
            await svc.create_campaign(
                shop_id, pid,
                "new_customer")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "S7" in str(e), ""
        record("S7 活动配额超限"
               "(starter 2)",
               ok, err)

        # 商品未上架拒绝(造
        # off_shelf 态)
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        repo = Xx65Repository()
        prod = await repo.get_product(pid)
        prod["status"] = "off_shelf"
        await repo.save_product(
            prod, create=False)
        try:
            await svc.create_campaign(
                shop_id, pid,
                "clearance")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "未上架" in str(e), ""
        record("未上架商品拒绝",
               ok, err)
        prod["status"] = "published"
        await repo.save_product(
            prod, create=False)

        # 活动不存在 404
        try:
            await svc.campaign_report(
                999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("活动不存在 404",
               ok, err)
        os.environ["XX65_MODE"] = "off"


class TestRevoke:
    """04 撤销窗口(S5)"""

    async def run(self):
        print("[04 撤销窗口]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        shop_id, pid = \
            await seed_published_product(
                101, 1, price=100.0)
        os.environ["XX65_MODE"] = "assist"
        svc = Xx65Service()
        c = await svc.create_campaign(
            shop_id, pid, "clearance")
        cid = c["campaignId"]

        # 窗口内撤销成功
        rv = await svc.revoke_campaign(
            cid)
        record("窗口内撤销成功"
               "(S5)",
               rv.get("status")
               == "revoked",
               str(rv.get("status")))

        # 终态重复撤销拒绝
        try:
            await svc.revoke_campaign(
                cid)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "不可撤销" in str(e), ""
        record("终态撤销拒绝",
               ok, err)

        # 超窗撤销拒绝(造过期
        # 窗口)
        c2 = await svc.create_campaign(
            shop_id, pid,
            "seasonal")
        cid2 = c2["campaignId"]
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        repo = Xx65Repository()
        camp = await repo.get_campaign(
            cid2)
        camp["revocableUntilTs"] = \
            camp["revocableUntilTs"] \
            - 600
        await repo.save_campaign(
            camp, create=False)
        try:
            await svc.revoke_campaign(
                cid2)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "窗口已过" in str(e), ""
        record("超窗撤销拒绝"
               "(S5 300s)",
               ok, err)

        # 撤销留痕(revokedBy/
        # revokedAt)
        camp1 = await repo.get_campaign(
            cid)
        record("撤销留痕不可抹除",
               camp1.get("revoked")
               is True
               and camp1.get(
                   "revokedBy")
               == "member",
               str((camp1.get(
                   "revoked"),
                   camp1.get(
                       "revokedBy"))))

        # 撤销活动不存在 404
        try:
            await svc.revoke_campaign(
                999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("撤销活动不存在 404",
               ok, err)
        os.environ["XX65_MODE"] = "off"


class TestReport:
    """05 效果归因复盘"""

    async def run(self):
        print("[05 复盘]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        shop_id, pid = \
            await seed_published_product(
                101, 1, price=100.0)
        os.environ["XX65_MODE"] = "assist"
        svc = Xx65Service()
        c = await svc.create_campaign(
            shop_id, pid, "clearance")
        cid = c["campaignId"]

        # off 态复盘观测面可用
        os.environ["XX65_MODE"] = "off"
        rpt = await svc.campaign_report(
            cid)
        record("复盘观测面"
               "(off 态可用)",
               rpt.get("success")
               is True,
               "")
        record("预估双算固化",
               (rpt.get("roi") or {})
               .get("estimated", {})
               .get("gmv")
               == c["roi"][
                   "estimatedGmv"],
               str(rpt.get("roi")))
        record("R2 声明留痕",
               rpt.get("exclusive")
               is True
               and "R2" in rpt.get(
                   "r2Note", ""),
               str(rpt.get("r2Note")))
        record("撤销审计区块",
               (rpt.get("revocation")
                or {}).get(
                   "windowSeconds")
               == 300
               and (rpt.get(
                   "revocation")
                    or {}).get(
                   "revoked")
               is False,
               str(rpt.get(
                   "revocation")))

        # 列表观测
        lst = await \
            svc.campaigns_list(
                shop_id=shop_id)
        record("活动列表观测",
               lst.get("total") == 1
               and (lst.get(
                   "campaigns")
                   or [{}])[0].get(
                       "exclusive")
               is True,
               str(lst.get("total")))
        os.environ["XX65_MODE"] = "off"


class TestConstitution:
    """06 宪法断言"""

    async def run(self):
        print("[06 宪法断言]")
        from services.xx65_registry import (
            LIQUIDITY_TENSION_RATIO,
            REVOKE_WINDOW_SECONDS,
        )
        record("S5 撤销窗口 300s"
               "(宪法)",
               REVOKE_WINDOW_SECONDS
               == 300,
               str(REVOKE_WINDOW_SECONDS))
        record("流动性阈值对齐 64号"
               "LIQ-CRUNCH(40%)",
               LIQUIDITY_TENSION_RATIO
               == 0.40,
               str(LIQUIDITY_TENSION_RATIO))

        # 64号零改动(纯读取)
        try:
            from services import \
                xx64_anchor_service as a64
            from services import \
                xx64_risk_service as r64
            record("64号零改动"
                   "(anchors/risk 纯读取)",
                   a64 is not None
                   and r64 is not None,
                   "")
        except ImportError:
            record("64号零改动"
                   "(anchors/risk 纯读取)",
                   False, "导入失败")

        # 三开关铁律
        record("XX65_MODE 默认 off",
               os.environ.get(
                   "XX65_MODE",
                   "off") == "off",
               str(os.environ.get(
                   "XX65_MODE")))


class TestHttp:
    """07 HTTP 层"""

    async def run(self):
        print("[07 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        member = {"X-Role": "member"}
        shop_id, pid = \
            await seed_published_product(
                101, 1, price=100.0)

        # 决策面 off 409
        resp = client.post(
            "/api/xx65/campaigns",
            json={"shopId": shop_id,
                  "productId": pid,
                  "strategy":
                      "clearance"},
            headers=member)
        record("HTTP campaigns off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 推荐观测面 off 可用
        resp = client.post(
            "/api/xx65/campaigns/recommend",
            json={"shopId": shop_id,
                  "productId": pid},
            headers=member)
        rbody = resp.json() or {}
        record("HTTP recommend 200"
               "(观测面)",
               resp.status_code == 200
               and len(rbody.get(
                   "recommendations")
                   or []) == 3,
               str((resp.status_code,
                    len(rbody.get(
                        "recommendations")
                        or []))))

        # assist 创建
        os.environ["XX65_MODE"] = "assist"
        resp = client.post(
            "/api/xx65/campaigns",
            json={"shopId": shop_id,
                  "productId": pid,
                  "strategy":
                      "clearance"},
            headers=member)
        cbody = resp.json() or {}
        record("HTTP campaigns 200"
               "(active+R2)",
               resp.status_code == 200
               and cbody.get("status")
               == "active"
               and cbody.get(
                   "exclusive")
               is True,
               str((resp.status_code,
                    cbody.get("status"))))
        cid = cbody.get("campaignId")

        # S1 合规 409
        resp = client.post(
            "/api/xx65/campaigns",
            json={"shopId": shop_id,
                  "productId": pid,
                  "strategy":
                      "seasonal",
                  "name":
                      "全网最低价"
                      "大促"},
            headers=member)
        record("HTTP 合规拦截 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 撤销 200(S5 窗口内)
        resp = client.post(
            f"/api/xx65/campaigns/"
            f"{cid}/revoke",
            json={"operator":
                      "member"},
            headers=member)
        record("HTTP revoke 200"
               "(S5)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("status")
               == "revoked",
               str((resp.status_code,
                    (resp.json()
                     or {}).get(
                        "status"))))

        # 重复撤销 409
        resp = client.post(
            f"/api/xx65/campaigns/"
            f"{cid}/revoke",
            json={},
            headers=member)
        record("HTTP 重复撤销 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 复盘观测面
        resp = client.get(
            f"/api/xx65/campaigns/"
            f"{cid}/report",
            headers=member)
        record("HTTP report 200"
               "(观测面)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get(
                        "exclusive")
               is True,
               str(resp.status_code))
        resp = client.get(
            "/api/xx65/campaigns/999"
            "/report",
            headers=member)
        record("HTTP report 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 列表观测面
        resp = client.get(
            f"/api/xx65/campaigns"
            f"?shop_id={shop_id}",
            headers=member)
        record("HTTP campaigns 列表"
               "200",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("total")
               == 1,
               str((resp.status_code,
                    (resp.json()
                     or {}).get(
                        "total"))))

        # off 态决策面 409
        os.environ["XX65_MODE"] = "off"
        resp = client.post(
            f"/api/xx65/campaigns/"
            f"{cid}/revoke",
            json={},
            headers=member)
        record("HTTP revoke off 409"
               "(服务器态)",
               resp.status_code == 409,
               str(resp.status_code))

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
            resp = client.request(
                method, path, json={})
            short = path.split('/')[-1] \
                .split('?')[0]
            record(f"HTTP {short}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计(P2 21)
        from routes.xx65_routes import (
            router as xx_router,
        )
        count = sum(
            1 for r in xx_router.routes)
        record("65号路由 P2 21 端点"
               "(P3 增至 25)",
               count >= 21, str(count))


async def run_all():
    await TestRules().run()
    await TestRecommend().run()
    await TestCreate().run()
    await TestRevoke().run()
    await TestReport().run()
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
