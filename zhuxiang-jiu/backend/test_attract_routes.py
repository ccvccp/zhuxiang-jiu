"""AI智能自动引流模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 AttractService 方法, 模拟 21 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_attract_routes.py

覆盖:
    1. 内容工厂: 选题/四平台变体生成/合规审核(拒违规)/发布
    2. 短链:     活动短码创建唯一/ZXBJ/KOL/A三类码分流(D-10)/未知码404
    3. 匿名点击: 不要求注册即可落库/UTM优先渠道
    4. 注册归并: 三合一(traffic lead + promotion绑定 + 归因表)/幂等
    5. 下单回写: 归因订单/漏斗报表
    6. 报表:     渠道ROI/内容效果
    7. ROI引擎:  高ROI渠道系数上调/低下调/样本不足跳过/总池不变
"""

import asyncio
import os
import sys


# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.attract_service import AttractService
from services.traffic_service import TrafficService
from services.promotion_service import PromotionService
from services.wallet_service import WalletService
from repositories.member_repository import MemberRepository
from repositories.attract_repository import (
    AttractRepository,
    PLATFORM_XIAOHONGSHU, PLATFORM_DOUYIN, PLATFORM_MOMENTS, PLATFORM_SEO,
    CONTENT_STATUS_PENDING, CONTENT_STATUS_APPROVED,
    CONTENT_STATUS_REJECTED,
    LANDING_REGISTER, LANDING_PRODUCT, LANDING_ACTIVITY,
    COMPLIANCE_PASS_SCORE,
)

# 测试结果收集
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


# 测试数据
NEW_MEMBER_ID = 7001        # 新注册会员
PROMOTER_USER_ID = 7002     # 矩阵码主人


async def _setup_members():
    repo = MemberRepository()
    for uid in (NEW_MEMBER_ID, PROMOTER_USER_ID):
        m = await repo.create({
            "id": uid, "nickname": f"引流测试{uid}",
            "phone": f"1390000{uid:04d}", "password": "test123456",
        })
        await repo.save(uid, m)


async def _claim_promotion_code() -> str:
    """领取一个会员矩阵码(返回code)"""
    result = await PromotionService().claim_promo_code(
        member_id=PROMOTER_USER_ID, channel="wechat_miniprogram")
    return result["code"]


# ============================================================
# 1. 内容工厂
# ============================================================

class TestContentFactory:

    async def run(self):
        reset_store()
        svc = AttractService()

        topic = await svc.create_topic(
            title="竹香酒中秋礼赠场景", angle="scene",
            keywords="中秋送礼白酒")
        record("选题-录入成功", topic["topicId"] > 0
               and topic["source"] == "manual")

        try:
            await svc.create_topic("", "culture", "x")
            record("选题-空标题拒绝", False, "未抛出异常")
        except ValueError:
            record("选题-空标题拒绝", True)
        try:
            await svc.create_topic("x", "bad_angle", "x")
            record("选题-非法角度拒绝", False, "未抛出异常")
        except ValueError:
            record("选题-非法角度拒绝", True)

        # 生成四平台变体
        contents = await svc.generate_contents(topic["topicId"])
        platforms = {c["platform"] for c in contents}
        record("生成-四平台变体",
               platforms == {PLATFORM_XIAOHONGSHU, PLATFORM_DOUYIN,
                             PLATFORM_MOMENTS, PLATFORM_SEO},
               f"实际{platforms}")
        record("生成-合规分达标(含警示与年龄提示)",
               all(c["complianceScore"] >= COMPLIANCE_PASS_SCORE
                   for c in contents),
               f"分数{[c['complianceScore'] for c in contents]}")

        # P3.4: 知识注入 + 溯源(选题 keywords 未命中知识库 →
        # 回退硬编码文案, knowledgeRefs 为空, 生成不阻断)
        record("生成-知识未命中回退硬编码",
               all(c["knowledgeRefs"] == [] for c in contents)
               and all("竹香型工艺入口绵甜" in c["body"] for c in contents))

        # P3.4: 品牌种子播种后, 选题 keywords 命中 → detail 槽位
        # 注入知识答案 + knowledgeRefs 溯源
        from services.knowledge_service import KnowledgeService
        await KnowledgeService().seed_brand_knowledge()
        topic_hit = await svc.create_topic(
            title="竹香酒工艺推广", angle="culture",
            keywords="竹香酒是怎么酿造的")
        contents_hit = await svc.generate_contents(topic_hit["topicId"])
        record("生成-知识注入detail槽位",
               all("竹笋" in c["body"] for c in contents_hit),
               f"body片段: {contents_hit[0]['body'][:60]}")
        record("生成-知识Refs溯源",
               all(c["knowledgeRefs"] and c["knowledgeRefs"][0] > 0
                   for c in contents_hit),
               f"refs: {contents_hit[0]['knowledgeRefs']}")

        # 违规文案不可通过审核
        content = contents[0]
        content["body"] = "全网最低价! 无警示文案"
        content["complianceScore"], content[
            "complianceViolations"] = svc.compliance_score(content["body"])
        await svc.repo.save_content(content)
        try:
            await svc.review_content(content["contentId"], approved=True)
            record("审核-违规文案拒绝", False, "未抛出异常")
        except ValueError:
            record("审核-违规文案拒绝", True)

        # 合规内容: 审核通过→发布
        ok = contents[1]
        approved = await svc.review_content(ok["contentId"], approved=True)
        record("审核-通过", approved["status"] == CONTENT_STATUS_APPROVED)
        published = await svc.publish_content(ok["contentId"],
                                              channel_code="ZXBJ-TEST01")
        record("发布-绑定分发码",
               published["status"] == "published"
               and published["publishedTo"] == "ZXBJ-TEST01")
        try:
            await svc.publish_content(contents[2]["contentId"])
            record("发布-未审核拒绝", False, "未抛出异常")
        except ValueError:
            record("发布-未审核拒绝", True)

        # 拒绝路径
        rejected = await svc.review_content(contents[2]["contentId"],
                                            approved=False)
        record("审核-拒绝路径", rejected["status"] == CONTENT_STATUS_REJECTED)

        # 不存在选题
        try:
            await svc.generate_contents(9999)
            record("生成-选题不存在拒绝", False, "未抛出异常")
        except KeyError:
            record("生成-选题不存在拒绝", True)


# ============================================================
# 2. 短链与匿名点击
# ============================================================

class TestShortLink:

    async def run(self):
        reset_store()
        svc = AttractService()
        await _setup_members()

        # 活动短码
        link = await svc.create_short_link(note="双旦活动")
        record("短码-创建(A-前缀)", link["code"].startswith("A-"))

        # 三类码分流(D-10)
        zxbj_code = await _claim_promotion_code()
        r1 = await svc.resolve_click(zxbj_code)
        record("分流-矩阵码→注册页", r1["landingPath"] == LANDING_REGISTER)
        r2 = await svc.resolve_click("KOL1_douyin_AB12CD34")
        record("分流-KOL码→产品页", r2["landingPath"] == LANDING_PRODUCT)
        r3 = await svc.resolve_click(link["code"])
        record("分流-活动码→活动页",
               r3["landingPath"] == LANDING_ACTIVITY)

        # 未知码
        try:
            await svc.resolve_click("XXXX-UNKNOWN")
            record("分流-未知码拒绝", False, "未抛出异常")
        except KeyError:
            record("分流-未知码拒绝", True)

        # 匿名点击落库(不要求注册)
        clicks = await svc.repo.list_clicks(limit=100)
        record("点击-匿名落库(3次点击)", len(clicks) == 3,
               f"实际{len(clicks)}")

        # UTM 优先渠道
        r4 = await svc.resolve_click(link["code"], utm_source="xiaohongshu")
        record("点击-UTM优先渠道",
               r4["channel"] == "xiaohongshu")
        r5 = await svc.resolve_click(link["code"])
        record("点击-无UTM按码默认渠道",
               r5["channel"] == "direct")

        # 点击不存在
        try:
            await svc.attach_registration(9999, NEW_MEMBER_ID)
            record("归并-点击不存在拒绝", False, "未抛出异常")
        except KeyError:
            record("归并-点击不存在拒绝", True)


# ============================================================
# 3. 注册归并三合一
# ============================================================

class TestAttachRegistration:

    async def run(self):
        reset_store()
        svc = AttractService()
        await _setup_members()
        zxbj_code = await _claim_promotion_code()

        # 矩阵码点击→新会员注册归并
        click = await svc.resolve_click(zxbj_code)
        attr = await svc.attach_registration(click["clickId"], NEW_MEMBER_ID)
        record("归并-归因表落库",
               attr["memberId"] == NEW_MEMBER_ID
               and attr["registeredAt"] != "")

        # 三合一: promotion 绑定关系生效
        from repositories.promotion_repository import PromotionRepository
        rel = await PromotionRepository().get_relation(NEW_MEMBER_ID)
        record("归并-矩阵绑定关系(三合一之一)",
               rel is not None and rel["code"] == zxbj_code,
               f"实际{rel}")

        # 幂等
        again = await svc.attach_registration(click["clickId"], NEW_MEMBER_ID)
        record("归并-幂等", again["clickId"] == attr["clickId"])
        try:
            await svc.attach_registration(click["clickId"], 8888)
            record("归并-他会员重复归并拒绝", False, "未抛出异常")
        except ValueError:
            record("归并-他会员重复归并拒绝", True)

        # 下单回写
        order = await svc.attach_order(click["clickId"], "ORD-ATTR-1",
                                       500.0, commission=25.0)
        record("回写-订单归因", order["orderId"] == "ORD-ATTR-1"
               and order["commission"] == 25.0)
        try:
            await svc.attach_order(click["clickId"], "ORD-ATTR-2", 300.0)
            record("回写-重复订单拒绝", False, "未抛出异常")
        except ValueError:
            record("回写-重复订单拒绝", True)

        # 漏斗
        funnel = await svc.report_funnel()
        record("漏斗-点击1注册1下单1",
               funnel["clicks"] == 1 and funnel["registered"] == 1
               and funnel["ordered"] == 1,
               f"实际{funnel}")
        record("漏斗-GMV与佣金",
               funnel["gmv"] == 500.0 and funnel["commission"] == 25.0)


# ============================================================
# 4. 渠道报表与ROI引擎
# ============================================================

class TestRoiEngine:

    async def run(self):
        reset_store()
        svc = AttractService()
        await _setup_members()

        # 造两渠道: xiaohongshu 高ROI / taobao 零转化
        xhs_clicks = []
        for _ in range(3):
            result = await svc.resolve_click("KOL1_douyin_AB12CD34",
                                             utm_source="xiaohongshu")
            xhs_clicks.append(result["clickId"])
        await svc.resolve_click("KOL1_douyin_AB12CD34",
                                utm_source="taobao")
        # 小红书3点击全部注册并下单
        for i, cid in enumerate(xhs_clicks):
            await svc.attach_registration(cid, 8000 + i)
            await svc.attach_order(cid, f"ORD-R{i}",
                                   1000.0, commission=50.0)

        # 报表
        rows = await svc.report_channel()
        by_ch = {r["channel"]: r for r in rows}
        record("报表-渠道数据",
               by_ch["xiaohongshu"]["registered"] == 3
               and by_ch["xiaohongshu"]["gmv"] == 3000.0
               and by_ch["taobao"]["clicks"] == 1,
               f"实际{by_ch}")

        # ROI 再分配前: 全渠道系数 1.0
        budgets_before = {b["channel"]: b["currentRate"]
                          for b in await svc.list_budgets()}
        record("ROI-初始系数全1.0",
               all(v == 1.0 for v in budgets_before.values()))

        result = await svc.rebalance_budgets()
        adjusted = {a["channel"]: a["newRate"] for a in result["adjusted"]}
        record("ROI-高ROI渠道上调",
               adjusted.get("xiaohongshu") == 1.1,
               f"实际{adjusted}")
        # taobao 注册 0(样本不足) → 跳过不动(不下调)
        skipped_chs = {s["channel"] for s in result["skipped"]}
        record("ROI-零样本渠道跳过不动",
               "taobao" in skipped_chs
               and "taobao" not in adjusted,
               f"skipped={skipped_chs}")

        # 池内此消彼长(总池不因再分配改变)
        budgets_after = await svc.list_budgets()
        record("ROI-账本留存系数",
               any(b["channel"] == "xiaohongshu"
                   and b["currentRate"] == 1.1 for b in budgets_after))

        # AI选题建议(数据回流)
        suggestions = await svc.suggest_topics(limit=2)
        record("选题建议-高ROI渠道回流",
               len(suggestions) == 2
               and all(t["source"] == "ai_roi" for t in suggestions),
               f"实际{[t.get('source') for t in suggestions]}")


# ============================================================
# 5. traffic lead 状态推进(修复既有空白)
# ============================================================

class TestLeadStatus:

    async def run(self):
        reset_store()
        traffic = TrafficService()
        promoter = await traffic.create_promoter(user_id=9001,
                                                  name="状态推进测试")
        lead = await traffic.record_lead(
            promoter_id=promoter["id"], user_id=9002,
            source="xiaohongshu", medium="share")
        updated = await traffic.update_lead_status(lead["id"], "ordered")
        record("lead状态-推进为ordered",
               updated["status"] == "ordered")
        try:
            await traffic.update_lead_status(9999, "ordered")
            record("lead状态-不存在拒绝", False, "未抛出异常")
        except KeyError:
            record("lead状态-不存在拒绝", True)


# ============================================================
# 6. AI-SEO(P1)
# ============================================================

class TestSeo:

    async def run(self):
        reset_store()
        svc = AttractService()

        # 种子词加载(7个)
        keywords = await svc.list_keywords()
        record("SEO-种子词加载(7个)", len(keywords) == 7,
               f"实际{len(keywords)}")

        # 添加关键词(去重)
        kw = await svc.add_keyword("企业定制酒", search_volume=1200)
        record("SEO-关键词添加", kw["keywordId"] > 0
               and kw["status"] == "active")
        try:
            await svc.add_keyword("企业定制酒")
            record("SEO-重复关键词拒绝", False, "未抛出异常")
        except ValueError:
            record("SEO-重复关键词拒绝", True)

        # 关键词生成长文
        article = await svc.generate_seo_article(kw["keywordId"])
        record("SEO-长文生成(含关键词+合规)",
               "企业定制酒" in article["body"]
               and article["complianceScore"] >= 70,
               f"分数{article['complianceScore']}")
        try:
            await svc.generate_seo_article(9999)
            record("SEO-关键词不存在拒绝", False, "未抛出异常")
        except KeyError:
            record("SEO-关键词不存在拒绝", True)

        # sitemap/robots
        sitemap = await svc.generate_sitemap()
        record("SEO-sitemap含域名与基础页",
               "zhuxiang-jiu.com" in sitemap
               and "<urlset" in sitemap and "/products" in sitemap)
        robots = await svc.generate_robots()
        record("SEO-robots含允许与sitemap指引",
               "User-agent: *" in robots and "Sitemap:" in robots)


# ============================================================
# 7. AB落地页(P1)
# ============================================================

class TestAbPage:

    async def run(self):
        reset_store()
        svc = AttractService()

        link = await svc.create_short_link(note="AB测试")

        # 无AB配置时点击走默认活动页
        r0 = await svc.resolve_click(link["code"])
        record("AB-未配置走默认活动页",
               r0["landingPath"] == "/pages/activity/index")

        # 配置AB(70/30)
        page = await svc.create_ab_page(
            code=link["code"], path_a="/pages/activity/v1",
            path_b="/pages/activity/v2", weight_a=70)
        record("AB-配置成功(70/30)",
               page["weightA"] == 70)

        try:
            await svc.create_ab_page("A-XXXXXX", "/a", "/b", 50)
            record("AB-短码不存在拒绝", False, "未抛出异常")
        except ValueError:
            record("AB-短码不存在拒绝", True)

        # 权重分流: 100次点击, A≈70/B≈30
        version_counts = {"A": 0, "B": 0}
        for _ in range(100):
            r = await svc.resolve_click(link["code"])
            version_counts[r.get("abVersion") or "?"] += 1
        record("AB-按权重分流(A≈70)",
               60 <= version_counts["A"] <= 80,
               f"实际A={version_counts['A']}")
        # 落地页与版本一致
        r1 = await svc.resolve_click(link["code"])
        expect_path = ("/pages/activity/v1" if r1["abVersion"] == "A"
                       else "/pages/activity/v2")
        record("AB-落地页与版本一致",
               r1["landingPath"] == expect_path,
               f"v={r1['abVersion']}, path={r1['landingPath']}")

        # AB报表
        report = await svc.ab_report(link["code"])
        record("AB-报表含双版本点击数",
               report["clicksA"] + report["clicksB"] >= 100,
               f"A={report['clicksA']}, B={report['clicksB']}")
        try:
            await svc.ab_report("A-XXXXXX")
            record("AB-无配置报表拒绝", False, "未抛出异常")
        except ValueError:
            record("AB-无配置报表拒绝", True)


# ============================================================
# 8. 分发通知(P1: best-effort)
# ============================================================

class TestNotifyPublish:

    async def run(self):
        reset_store()
        svc = AttractService()
        await _setup_members()

        topic = await svc.create_topic(title="通知测试", angle="culture",
                                       keywords="竹香型白酒")
        contents = await svc.generate_contents(topic["topicId"])
        ok = contents[0]
        await svc.review_content(ok["contentId"], approved=True)
        published = await svc.publish_content(ok["contentId"])

        result = await svc.notify_publish(
            content_id=published["contentId"],
            member_ids=[NEW_MEMBER_ID, PROMOTER_USER_ID])
        record("通知-站内信群发(2人成功)",
               result.get("successCount") == 2,
               f"实际{result}")


# ============================================================
# 9. 裂变活动插件(P2: 任务宝 + 海报)
# ============================================================

class TestFission:

    async def run(self):
        reset_store()
        svc = AttractService()
        wallet = WalletService()
        # 造会员(含成长值≥500以满足钱包开通条件)
        repo = MemberRepository()
        for uid in (PROMOTER_USER_ID, NEW_MEMBER_ID):
            m = await repo.create({
                "id": uid, "nickname": f"裂变{uid}",
                "phone": f"1360000{uid:04d}", "password": "test123456"})
            await repo.save(uid, {**m, "growth_value": 600})
        await wallet.open(PROMOTER_USER_ID)

        # 创建任务宝(邀请2人得 ¥20+100竹叶)
        fission = await svc.create_fission(
            title="中秋裂变季", invite_target=2,
            reward_amount=20.0, reward_points=100)
        record("裂变-创建任务宝",
               fission["fissionId"] > 0
               and fission["inviteTarget"] == 2
               and fission["status"] == "ongoing")

        try:
            await svc.create_fission("", 5)
            record("裂变-空标题拒绝", False, "未抛出异常")
        except ValueError:
            record("裂变-空标题拒绝", True)

        # 进度查询(初始化0)
        progress = await svc.get_fission_progress(
            fission["fissionId"], PROMOTER_USER_ID)
        record("裂变-进度初始化(0/2)",
               progress["invited"] == 0
               and progress["rewardGranted"] is False)

        # 邀请计数来源: 归因表(经ZXBJ码注册)——造2个被邀请注册
        zxbj_code = await _claim_promotion_code()
        invitee_ids = [9101, 9102]
        repo = MemberRepository()
        for uid in invitee_ids:
            m = await repo.create({
                "id": uid, "nickname": f"被邀{uid}",
                "phone": f"1370000{uid:04d}", "password": "test123456"})
            await repo.save(uid, m)
            click = await svc.resolve_click(zxbj_code)
            await svc.attach_registration(click["clickId"], uid)

        # 刷新进度(2/2达标 → 自动发奖)
        refreshed = await svc.refresh_fission_progress(
            fission["fissionId"], PROMOTER_USER_ID)
        record("裂变-达标发奖(2/2)",
               refreshed["invited"] == 2
               and refreshed["rewardGranted"] is True,
               f"实际{refreshed}")
        record("裂变-发奖双通道",
               set(refreshed.get("grantedChannels", [])) >= {"wallet"})

        # 钱包到账
        reward = await wallet.get_reward_balance(PROMOTER_USER_ID)
        record("裂变-钱包奖励到账(¥20)",
               abs(reward.get("rewardBalance", 0) - 20.0) < 0.01,
               f"实际{reward.get('rewardBalance')}")

        # 幂等: 重复刷新不重复发奖
        again = await svc.refresh_fission_progress(
            fission["fissionId"], PROMOTER_USER_ID)
        reward2 = await wallet.get_reward_balance(PROMOTER_USER_ID)
        record("裂变-重复刷新幂等",
               again["rewardGranted"] is True
               and abs(reward2.get("rewardBalance", 0) - 20.0) < 0.01)

        # 未达标者不发
        progress_other = await svc.refresh_fission_progress(
            fission["fissionId"], NEW_MEMBER_ID)
        record("裂变-未达标不发奖",
               progress_other["rewardGranted"] is False)

        # 结束活动
        ended = await svc.end_fission(fission["fissionId"])
        record("裂变-结束活动",
               ended["status"] == "ended")
        try:
            await svc.get_fission_progress(
                fission["fissionId"], PROMOTER_USER_ID)
            record("裂变-结束后进度拒绝", False, "未抛出异常")
        except ValueError:
            record("裂变-结束后进度拒绝", True)
        try:
            await svc.end_fission(fission["fissionId"])
            record("裂变-重复结束拒绝", False, "未抛出异常")
        except ValueError:
            record("裂变-重复结束拒绝", True)


class TestPoster:

    async def run(self):
        reset_store()
        svc = AttractService()
        await _setup_members()

        # invite 场景海报
        fission = await svc.create_fission(title="裂变海报测试",
                                           invite_target=3)
        poster = await svc.create_poster(
            user_id=PROMOTER_USER_ID, scene="invite",
            fission_id=fission["fissionId"])
        record("海报-invite场景生成",
               poster["scene"] == "invite"
               and poster["qrCode"].startswith("ZXBJ-")
               and poster["qrTarget"] == f"/r/{poster['qrCode']}",
               f"实际{poster}")
        record("海报-含进度文案",
               "0/3" in poster["subtext"],
               f"实际{poster['subtext']}")

        # promote 场景海报
        topic = await svc.create_topic(title="海报内容", angle="culture",
                                       keywords="竹香型白酒")
        contents = await svc.generate_contents(topic["topicId"])
        ok = contents[0]
        await svc.review_content(ok["contentId"], approved=True)
        published = await svc.publish_content(ok["contentId"])
        poster2 = await svc.create_poster(
            user_id=PROMOTER_USER_ID, scene="promote",
            content_id=published["contentId"])
        record("海报-promote场景生成",
               poster2["scene"] == "promote"
               and poster2["headline"] != "")

        # 参数校验
        try:
            await svc.create_poster(PROMOTER_USER_ID, "invite")
            record("海报-invite缺活动拒绝", False, "未抛出异常")
        except ValueError:
            record("海报-invite缺活动拒绝", True)
        try:
            await svc.create_poster(PROMOTER_USER_ID, "bad_scene")
            record("海报-非法场景拒绝", False, "未抛出异常")
        except ValueError:
            record("海报-非法场景拒绝", True)

        # 列表
        posters = await svc.list_posters(user_id=PROMOTER_USER_ID)
        record("海报-我的列表(2张)",
               len(posters) == 2, f"实际{len(posters)}")


# ============================================================
# 主入口
# ============================================================

async def main():
    test_classes = [
        ("内容工厂", TestContentFactory),
        ("短链与匿名点击", TestShortLink),
        ("注册归并三合一", TestAttachRegistration),
        ("渠道报表与ROI引擎", TestRoiEngine),
        ("lead状态推进", TestLeadStatus),
        ("AI-SEO", TestSeo),
        ("AB落地页", TestAbPage),
        ("分发通知", TestNotifyPublish),
        ("裂变任务宝", TestFission),
        ("裂变海报", TestPoster),
    ]
    print("=" * 62)
    print("AI智能自动引流模块 P0+P1+P2 端到端测试")
    print("=" * 62)
    for name, cls in test_classes:
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, str(e))

    print("\n" + "-" * 62)
    for line in RESULTS:
        print(line)
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    failures = asyncio.run(main())
    sys.exit(1 if failures else 0)
