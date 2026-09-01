"""36号·AI智能推广模块·P1 专项测试(受众画像库 + 权威信源 RAG)

覆盖(设计文档 §3.3/§3.4 P1):
    1. 画像库: 种子幂等初始化 / 更新 / 非法平台与字段 409
    2. 三维匹配: 角度亲和分档(婚宴→小红书高/抖音低) / 通过线 / 非法参数
    3. 站内画像回传: 会员等级分布聚合 / 高价值占比建议
    4. 权威信源: 种子 / 新增(背书红线词拒绝) / 关键词过滤 / RAG 检索相关排序
    5. 数字溯源: 标准编号可溯 / 编造数据违规 / 年龄提示白名单豁免
    6. 生成集成: 内容含 authorityRefs+provenanceReport /
       注入编造数字 → 审核通过被 409 拦截

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_promo_p1_features.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.promo_service import PromoService
from services.promo_audience_service import PromoAudienceService
from services.promo_authority_service import PromoAuthorityService
from services.promo_agent_service import TRACK_RULE
from repositories.promo_repository import (
    PROMO_PLATFORMS, PROMO_PLATFORM_DOUYIN, PROMO_PLATFORM_XHS,
    PROMO_PLATFORM_WEIBO, PROMO_PLATFORM_CHANNELS,
    REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
)

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


async def _engaged_hotspot(svc: PromoService) -> dict:
    await svc.scan()
    return (await svc.list_hotspots(status="engaged"))[0]


class TestAudienceProfiles:
    async def run(self):
        audience = PromoAudienceService()

        # 种子幂等
        added = await audience.ensure_profiles()
        record("画像-种子初始化(5平台)", added == len(PROMO_PLATFORMS),
               f"实际{added}")
        added_again = await audience.ensure_profiles()
        record("画像-种子幂等", added_again == 0, f"实际{added_again}")

        # 列表
        profiles = await audience.list_profiles()
        record("画像-列表全平台", len(profiles) == len(PROMO_PLATFORMS),
               f"实际{len(profiles)}")
        platforms = {p["platform"] for p in profiles}
        record("画像-平台覆盖", platforms == set(PROMO_PLATFORMS),
               f"实际{platforms}")

        # 更新
        updated = await audience.update_profile(
            PROMO_PLATFORM_XHS, {"audience": "25-45 品质生活女性"})
        record("画像-更新生效",
               updated["audience"] == "25-45 品质生活女性"
               and updated.get("updatedAt"))
        fetched = await audience.get_profile(PROMO_PLATFORM_XHS)
        record("画像-更新持久化",
               fetched["audience"] == "25-45 品质生活女性")

        # 更新后 Agent Step2 即时生效(注入画像)
        agent_result, _ = PromoService().agent.match_audience(
            PROMO_PLATFORM_XHS, {"angle": "婚宴"},
            profile=fetched)
        record("画像-Step2注入生效",
               agent_result["audience"] == "25-45 品质生活女性",
               f"实际{agent_result['audience']}")

        # 非法平台 / 非法字段
        try:
            await audience.get_profile("kuaishou")
            record("画像-非法平台409", False)
        except ValueError:
            record("画像-非法平台409", True)
        try:
            await audience.update_profile(
                PROMO_PLATFORM_DOUYIN, {"platform": "douyin"})
            record("画像-非法字段409", False)
        except ValueError:
            record("画像-非法字段409", True)


class TestAudienceMatch:
    async def run(self):
        audience = PromoAudienceService()
        await audience.ensure_profiles()

        # 三维匹配: 婚宴角度 → 小红书亲和(0.95)远高于抖音(0.4)
        xhs = await audience.match(PROMO_PLATFORM_XHS, "婚宴", "礼盒")
        douyin = await audience.match(PROMO_PLATFORM_DOUYIN, "婚宴", "礼盒")
        record("匹配-婚宴→小红书高分",
               xhs["matched"] and xhs["score"] >= 0.6,
               f"score={xhs['score']}")
        record("匹配-婚宴→抖音低分(不通过)",
               not douyin["matched"],
               f"score={douyin['score']}")
        record("匹配-分项组件齐全",
               set(xhs["components"]) ==
               {"angleAffinity", "toneAffinity", "sceneAffinity"})
        record("匹配-建议含画像",
               xhs["recommendation"]["audience"] != "")

        # 日常小酌 → 抖音高亲和
        daily = await audience.match(PROMO_PLATFORM_DOUYIN, "日常小酌",
                                     "口粮酒")
        record("匹配-日常→抖音通过",
               daily["matched"] and daily["score"] >= 0.6,
               f"score={daily['score']}")

        # 非法参数
        try:
            await audience.match(PROMO_PLATFORM_XHS, " ", "礼盒")
            record("匹配-空角度409", False)
        except ValueError:
            record("匹配-空角度409", True)
        try:
            await audience.match(PROMO_PLATFORM_XHS, "婚宴", "超跑")
            record("匹配-非法调性409", False)
        except ValueError:
            record("匹配-非法调性409", True)


class TestOnsiteFeedback:
    async def run(self):
        # 造会员数据(高价值 level>=3 2人 / 基础 1人); 断言用增量口径
        # (store 可能已有种子会员, 绝对数不可假设)
        from repositories.member_repository import MemberRepository
        repo = MemberRepository()
        before = await repo.list_all()
        before_total = len(before)
        before_high = sum(1 for m in before if int(m.get("level", 1) or 1) >= 3)
        await repo.create({"phone": "13911100001", "name": "A", "level": 5})
        await repo.create({"phone": "13911100002", "name": "B", "level": 3})
        await repo.create({"phone": "13911100003", "name": "C", "level": 1})
        expected_total = before_total + 3
        expected_high = before_high + 2

        audience = PromoAudienceService()
        feedback = await audience.onsite_feedback(PROMO_PLATFORM_XHS)
        record("回传-会员总数(增量+3)",
               feedback["onsiteMembers"] == expected_total,
               f"实际{feedback['onsiteMembers']} 期望{expected_total}")
        record("回传-高价值数(增量+2)",
               feedback["highValueMembers"] == expected_high,
               f"实际{feedback['highValueMembers']} 期望{expected_high}")
        record("回传-高占比品质建议",
               "品质" in feedback["calibrationSuggestion"]
               if feedback["highValueRatio"] >= 0.3 else
               "性价比" in feedback["calibrationSuggestion"])

        # 低高价值占比场景(动态补足基础会员, 把高价值占比稀释到 <0.3)
        needed = 0
        while (expected_high / (expected_total + needed)) >= 0.3:
            needed += 1
        for i in range(needed):
            await repo.create({"phone": f"1391110000{i + 4}",
                               "name": f"D{i}", "level": 1})
        feedback2 = await audience.onsite_feedback(PROMO_PLATFORM_XHS)
        record("回传-低占比性价比建议",
               "性价比" in feedback2["calibrationSuggestion"]
               and feedback2["onsiteMembers"] == expected_total + needed
               and feedback2["highValueRatio"] < 0.3,
               f"suggestion={feedback2['calibrationSuggestion']} "
               f"ratio={feedback2['highValueRatio']} needed={needed}")

        # 非法平台
        try:
            await audience.onsite_feedback("kuaishou")
            record("回传-非法平台409", False)
        except ValueError:
            record("回传-非法平台409", True)


class TestAuthoritySources:
    async def run(self):
        authority = PromoAuthorityService()

        # 种子幂等
        added = await authority.ensure_sources()
        record("信源-种子初始化(4条)", added == 4, f"实际{added}")
        added_again = await authority.ensure_sources()
        record("信源-种子幂等", added_again == 0, f"实际{added_again}")

        # 新增
        source = await authority.add_source(
            title="GB/T 23545—2009《白酒检验规则》示例条目",
            category="standard", content="示例: 白酒检验须符合 GB/T 23545 要求")
        record("信源-新增成功", source["sourceId"] > 0
               and source["allowedUsage"])

        # 背书红线词拒绝
        try:
            await authority.add_source(
                title="权威推荐产品名录", category="media",
                content="本产品获官方推荐认证")
            record("信源-背书红线词409", False)
        except ValueError as e:
            record("信源-背书红线词409", "权威背书" in str(e), str(e))

        # 非法类别
        try:
            await authority.add_source(title="x", category="blog",
                                       content="y")
            record("信源-非法类别409", False)
        except ValueError:
            record("信源-非法类别409", True)

        # 关键词过滤
        sources = await authority.list_sources(keyword="GB 2757")
        record("信源-关键词过滤", len(sources) == 1
               and "2757" in sources[0]["title"], f"实际{len(sources)}")

        # RAG 检索: 相关排序(查询"白酒 浓香型 标准" → 10781.1 应第一)
        results = await authority.retrieve("浓香型白酒国家标准 GB/T 10781.1")
        record("RAG-检索非空", len(results) >= 1, f"实际{len(results)}")
        record("RAG-相关排序(10781.1第一)",
               results and "10781.1" in results[0]["title"],
               f"top1={results[0]['title'] if results else None}")
        record("RAG-相似度降序",
               all(results[i]["similarity"] >= results[i + 1]["similarity"]
                   for i in range(len(results) - 1)))
        record("RAG-结果含引用方式", all(r["allowedUsage"] for r in results))

        # 空查询
        record("RAG-空查询空结果", await authority.retrieve("") == [])


class TestProvenance:
    async def run(self):
        authority = PromoAuthorityService()
        await authority.ensure_sources()
        citations = await authority.retrieve("浓香型白酒国家标准 GB/T 10781.1")

        # 可溯源: 标准编号出现在引用池
        body = ("竹香酒符合 GB/T 10781.1 标准, 品质可靠。"
                f"（{REQUIRED_DISCLAIMER}，满{REQUIRED_AGE_TIP}周岁请适量）")
        report = authority.provenance_check(body, citations)
        record("溯源-标准编号可溯",
               len(report["claims"]) == 1
               and report["claims"][0]["traceable"]
               and not report["violations"],
               f"report={report}")

        # 编造数据: 百分比不在引用池
        body2 = ("销量同比增长300%! "
                 f"（{REQUIRED_DISCLAIMER}，{REQUIRED_AGE_TIP}周岁以下请勿饮酒）")
        report2 = authority.provenance_check(body2, citations)
        record("溯源-编造百分比违规",
               "300%" in report2["violations"],
               f"violations={report2['violations']}")

        # 年龄提示白名单豁免(18 不算业务数字声明)
        claims_of_age = [c for c in report2["claims"] if "18" in c["claim"]]
        record("溯源-年龄提示白名单豁免", not claims_of_age,
               f"claims={report2['claims']}")

        # 无数字正文 → 零声明零违规
        body3 = (f"纯粮好酒, 入口绵甜。"
                 f"（{REQUIRED_DISCLAIMER}，{REQUIRED_AGE_TIP}周岁以下请勿饮酒）")
        report3 = authority.provenance_check(body3, citations)
        record("溯源-无数字正文零声明",
               report3["claims"] == [] and report3["violations"] == [])


class TestGenerationIntegration:
    async def run(self):
        svc = PromoService()
        hotspot = await _engaged_hotspot(svc)

        # 生成: 内容含 authorityRefs + provenanceReport(规则轨无数字→零违规)
        contents = await svc.generate_contents(
            hotspot["hotspotId"], platforms=(PROMO_PLATFORM_XHS,))
        record("集成-引用池注入(authorityRefs)",
               len(contents[0]["authorityRefs"]) >= 1,
               f"refs={contents[0]['authorityRefs']}")
        record("集成-溯源报告落库",
               "claims" in contents[0]["provenanceReport"]
               and contents[0]["provenanceViolations"] == [],
               f"report={contents[0]['provenanceReport']}")
        record("集成-规则轨零违规可过审",
               contents[0]["status"] == "pending"
               and contents[0]["complianceScore"] == 100)
        approved = await svc.review_content(contents[0]["contentId"],
                                            approved=True)
        record("集成-正常内容审核通过",
               approved["status"] == "approved")

        # 注入编造数字(模拟 LLM 越线): 溯源违规 + 审核拦截
        original = svc.agent.generate_draft
        svc.agent.generate_draft = lambda *a, **k: ({
            "title": "编造数据",
            "body": (f"市场占有率提升200%! "
                     f"（{REQUIRED_DISCLAIMER}，"
                     f"{REQUIRED_AGE_TIP}周岁以下请勿饮酒）"),
            "hashtags": "", "cta": "", "coverHint": ""}, TRACK_RULE)
        try:
            engaged = await svc.list_hotspots(status="engaged")
            other = [h for h in engaged
                     if h["hotspotId"] != hotspot["hotspotId"]][0]
            fabricated = await svc.generate_contents(other["hotspotId"])
            record("集成-编造数字检出",
                   "200%" in fabricated[0]["provenanceViolations"],
                   f"violations={fabricated[0]['provenanceViolations']}")
            record("集成-编造内容仍待审(HITL)",
                   fabricated[0]["status"] == "pending"
                   and fabricated[0]["complianceScore"] == 100)
            try:
                await svc.review_content(fabricated[0]["contentId"],
                                         approved=True)
                record("集成-编造数据审核被409拦截", False)
            except ValueError as e:
                record("集成-编造数据审核被409拦截",
                       "出处" in str(e), str(e))
        finally:
            svc.agent.generate_draft = original


async def main():
    test_classes = [
        ("受众画像库", TestAudienceProfiles),
        ("三维匹配", TestAudienceMatch),
        ("站内画像回传", TestOnsiteFeedback),
        ("权威信源库与RAG", TestAuthoritySources),
        ("数字溯源校验", TestProvenance),
        ("生成集成与审核enforce", TestGenerationIntegration),
    ]
    print("=" * 62)
    print("36号·AI智能推广模块 P1 专项测试(画像库+权威信源RAG)")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
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
    sys.exit(asyncio.run(main()) and 1 or 0)
