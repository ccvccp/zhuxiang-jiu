"""36号·AI智能推广模块·P1 平台扩展专项测试(微博 + 视频号)

覆盖(设计文档 §3.3 P1 平台扩展):
    1. 平台注册: PROMO_PLATFORMS 含 weibo/wechat_channels, 配置全量覆盖
       (画像种子/黄金时段/亲和度矩阵/内置画像/规则轨模板)
    2. 三维匹配: 热点话题角度→微博高分 / 送礼家宴→视频号高分 /
       婚宴→微博低分(话题平台不亲和)
    3. Agent 规则轨: 两平台模板产出含警示语+年龄提示+平台特征
       (微博 #话题# 格式 / 视频号图文短句)
    4. 全链路: 两平台内容生成/审核/发布(黄金时段窗口)/平台报表聚合

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_promo_platforms_ext.py
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
from services.promo_agent_service import (
    PromoAgentService, PLATFORM_PROFILES, TRACK_RULE,
)
from repositories.promo_repository import (
    PromoRepository,
    PROMO_PLATFORMS, PROMO_PLATFORM_WEIBO, PROMO_PLATFORM_CHANNELS,
    DEFAULT_AUDIENCE_PROFILES, ANGLE_PLATFORM_AFFINITY, GOLDEN_WINDOWS,
    CONTENT_STATUS_PENDING, CONTENT_STATUS_APPROVED,
    REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
)
from services.promo_agent_service import _RULE_TEMPLATES

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


NEW_PLATFORMS = (PROMO_PLATFORM_WEIBO, PROMO_PLATFORM_CHANNELS)


class TestPlatformRegistration:
    async def run(self):
        record("注册-PROMO_PLATFORMS含两新平台",
               set(NEW_PLATFORMS) <= set(PROMO_PLATFORMS),
               f"实际{PROMO_PLATFORMS}")
        for platform in NEW_PLATFORMS:
            record(f"注册-画像种子({platform})",
                   platform in DEFAULT_AUDIENCE_PROFILES
                   and DEFAULT_AUDIENCE_PROFILES[platform].get("audience"))
            record(f"注册-黄金时段({platform})",
                   bool(GOLDEN_WINDOWS.get(platform)))
            record(f"注册-亲和度矩阵({platform})",
                   bool(ANGLE_PLATFORM_AFFINITY.get(platform)))
            record(f"注册-内置画像({platform})",
                   platform in PLATFORM_PROFILES)
            record(f"注册-规则轨模板({platform})",
                   platform in _RULE_TEMPLATES)

        # 配置一致性: 画像种子/矩阵/窗口/模板 全平台无缺漏
        all_platforms = set(PROMO_PLATFORMS)
        record("注册-配置全量无缺漏",
               all_platforms <= set(DEFAULT_AUDIENCE_PROFILES)
               and all_platforms <= set(GOLDEN_WINDOWS)
               and all_platforms <= set(ANGLE_PLATFORM_AFFINITY)
               and all_platforms <= set(PLATFORM_PROFILES)
               and all_platforms <= set(_RULE_TEMPLATES))


class TestNewPlatformMatch:
    async def run(self):
        audience = PromoAudienceService()
        await audience.ensure_profiles()

        # 热点话题角度 → 微博高亲和(0.95)
        weibo_hot = await audience.match(PROMO_PLATFORM_WEIBO,
                                         "热点话题借势", "年轻化")
        record("匹配-热点话题→微博高分",
               weibo_hot["matched"] and weibo_hot["score"] >= 0.85,
               f"score={weibo_hot['score']}")

        # 送礼家宴 → 视频号高亲和
        channels_gift = await audience.match(PROMO_PLATFORM_CHANNELS,
                                             "节庆送礼家宴", "高端礼盒")
        record("匹配-送礼家宴→视频号高分",
               channels_gift["matched"] and channels_gift["score"] >= 0.85,
               f"score={channels_gift['score']}")

        # 婚宴 → 微博低亲和(话题平台不亲和婚嫁深度内容)
        weibo_wedding = await audience.match(PROMO_PLATFORM_WEIBO,
                                             "婚宴", "礼盒")
        record("匹配-婚宴→微博低分(不通过)",
               not weibo_wedding["matched"],
               f"score={weibo_wedding['score']}")

        # 两新平台画像读取(走画像库)
        for platform in NEW_PLATFORMS:
            profile = await audience.get_profile(platform)
            record(f"匹配-画像库读取({platform})",
                   profile.get("audience")
                   == DEFAULT_AUDIENCE_PROFILES[platform]["audience"])

        # 站内回传对新平台可用
        feedback = await audience.onsite_feedback(PROMO_PLATFORM_WEIBO)
        record("匹配-站内回传(微博平台可用)",
               "onsiteMembers" in feedback
               and "calibrationSuggestion" in feedback)


class TestNewPlatformAgent:
    async def run(self):
        agent = PromoAgentService()
        hotspot = {
            "hotspotId": 1, "platform": "weibo",
            "title": "中秋团圆宴白酒清单火了",
            "summary": "[weibo热榜] 中秋团圆宴白酒清单火了",
            "heat": 450.0, "brandHits": ["中秋", "白酒"], "riskFlags": [],
        }
        analysis = {"angle": "热点话题借势", "focus": "聚会场景"}

        for platform in NEW_PLATFORMS:
            profile = DEFAULT_AUDIENCE_PROFILES[platform]
            audience_data, _ = agent.match_audience(
                platform, analysis, profile=profile)
            record(f"Agent-Step2新平台画像兜底({platform})",
                   audience_data["audience"] == profile["audience"])
            draft, track = agent.generate_draft(
                hotspot, platform, analysis, audience_data)
            record(f"Agent-Step3新平台规则轨({platform})",
                   track == TRACK_RULE)
            record(f"Agent-警示语({platform})",
                   REQUIRED_DISCLAIMER in draft["body"])
            record(f"Agent-年龄提示({platform})",
                   REQUIRED_AGE_TIP in draft["body"])

        # 平台格式特征: 微博 #话题# 开头
        weibo_draft, _ = agent.generate_draft(
            hotspot, PROMO_PLATFORM_WEIBO, analysis,
            {"audience": "x", "tone": "y"})
        record("Agent-微博#话题#格式",
               weibo_draft["body"].startswith("#"),
               weibo_draft["body"][:20])
        # 视频号图文短句(多段短句, 含空行分节)
        channels_draft, _ = agent.generate_draft(
            hotspot, PROMO_PLATFORM_CHANNELS, analysis,
            {"audience": "x", "tone": "y"})
        record("Agent-视频号图文短句格式",
               "\n\n" in channels_draft["body"],
               channels_draft["body"][:40])


class TestNewPlatformE2E:
    async def run(self):
        svc = PromoService()
        await svc.scan()
        hotspot = (await svc.list_hotspots(status="engaged"))[0]

        # 两新平台一源多态生成
        contents = await svc.generate_contents(
            hotspot["hotspotId"], platforms=NEW_PLATFORMS)
        record("E2E-两新平台生成", len(contents) == 2
               and {c["platform"] for c in contents} == set(NEW_PLATFORMS),
               f"实际{[c['platform'] for c in contents]}")
        record("E2E-同内容组", len(
            {c["contentGroupId"] for c in contents}) == 1)
        record("E2E-规则轨满分待审",
               all(c["status"] == CONTENT_STATUS_PENDING
                   and c["complianceScore"] == 100 for c in contents))
        record("E2E-短码绑定",
               all(c["shortCode"].startswith("A-") for c in contents))

        # 审核 + 发布(黄金时段窗口计算)
        approved = await svc.review_content(contents[0]["contentId"],
                                            approved=True)
        record("E2E-审核通过",
               approved["status"] == CONTENT_STATUS_APPROVED)
        scheduled = await svc.publish_content(contents[0]["contentId"])
        record("E2E-新平台黄金时段调度",
               scheduled["status"] == "queued"
               and scheduled["scheduledAt"],
               scheduled.get("scheduledAt"))

        # 平台报表含新平台行
        report = await svc.report_platform()
        report_platforms = {row["platform"] for row in report}
        record("E2E-平台报表含两新平台",
               set(NEW_PLATFORMS) <= report_platforms,
               f"实际{report_platforms}")
        weibo_row = next(r for r in report
                         if r["platform"] == PROMO_PLATFORM_WEIBO)
        record("E2E-微博行指标齐全",
               {"contents", "published", "clicks", "registered",
                "gmv"} <= set(weibo_row))


async def main():
    test_classes = [
        ("平台注册与配置", TestPlatformRegistration),
        ("新平台三维匹配", TestNewPlatformMatch),
        ("新平台Agent规则轨", TestNewPlatformAgent),
        ("新平台端到端", TestNewPlatformE2E),
    ]
    print("=" * 62)
    print("36号·AI智能推广模块 P1 平台扩展专项测试(微博+视频号)")
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
