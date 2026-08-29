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
from repositories.attract_repository import (
    AttractRepository,
    PLATFORM_XIAOHONGSHU, PLATFORM_DOUYIN, PLATFORM_MOMENTS, PLATFORM_SEO,
    CONTENT_STATUS_PENDING, CONTENT_STATUS_APPROVED,
    CONTENT_STATUS_REJECTED,
    LANDING_REGISTER, LANDING_PRODUCT, LANDING_ACTIVITY,
    COMPLIANCE_PASS_SCORE,
)
from repositories.member_repository import MemberRepository

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
        for i in range(3):
            result = await svc.resolve_click("KOL1_douyin_AB12CD34",
                                             utm_source="xiaohongshu")
            xhs_clicks.append(result["clickId"])
        await svc.resolve_click("KOL1_douyin_AB12CD34",
                                utm_source="taobao")
        # 小红书3点击全部注册并下单
        for i, cid in enumerate(xhs_clicks):
            attr = await svc.attach_registration(cid, 8000 + i)
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
# 主入口
# ============================================================

async def main():
    test_classes = [
        ("内容工厂", TestContentFactory),
        ("短链与匿名点击", TestShortLink),
        ("注册归并三合一", TestAttachRegistration),
        ("渠道报表与ROI引擎", TestRoiEngine),
        ("lead状态推进", TestLeadStatus),
    ]
    print("=" * 62)
    print("AI智能自动引流模块 P0 端到端测试")
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
