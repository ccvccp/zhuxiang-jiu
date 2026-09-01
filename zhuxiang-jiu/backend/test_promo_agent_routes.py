"""36号·AI智能推广模块·Agent 内容工厂与合规闸门专项测试(Service 层)

覆盖(设计文档 §3.4/§3.5):
    1. 三级降级: 未配置 LLM_API_KEY → 四步全部走规则轨, 产出不中断
    2. 一源多态: 同热点 N 平台版本 + agentTrace 轨迹留痕
    3. 三审闸门: 饮酒动作/权威背书/功效暗示硬拒 + 极限词/缺警示扣分
       + 60-80 强制人工区间 + 生成即预审(拒绝/待审分流)
    4. Step4 自查自纠: 缺警示语自动补齐
    5. 冷却: 同热点第 3 次生成 409

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_promo_agent_routes.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.promo_service import PromoService
from services.promo_agent_service import (
    PromoAgentService, TRACK_RULE, PLATFORM_PROFILES,
)
from repositories.promo_repository import (
    CONTENT_STATUS_PENDING, CONTENT_STATUS_REJECTED,
    PROMO_PLATFORM_DOUYIN, PROMO_PLATFORM_XHS, PROMO_PLATFORM_MOMENTS,
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
    """扫描并返回第一个已跟进(auto_engage)热点"""
    await svc.scan()
    hotspots = await svc.list_hotspots(status="engaged")
    return hotspots[0]


class TestAgentChain:
    async def run(self):
        agent = PromoAgentService()
        hotspot = {
            "hotspotId": 1, "platform": "weibo",
            "title": "中秋团圆宴白酒清单火了",
            "summary": "[weibo热榜] 中秋团圆宴白酒清单火了",
            "heat": 450.0, "brandHits": ["中秋", "白酒"], "riskFlags": [],
        }
        # 四步链: 规则轨(LLM 关闭)
        analysis, t1 = agent.analyze_hotspot(hotspot)
        record("Step1-规则轨", t1 == TRACK_RULE, f"实际{t1}")
        record("Step1-输出角度", bool(analysis.get("angle")))

        audience, t2 = agent.match_audience(PROMO_PLATFORM_DOUYIN, analysis)
        record("Step2-规则轨", t2 == TRACK_RULE, f"实际{t2}")
        record("Step2-画像命中平台",
               audience.get("audience") ==
               PLATFORM_PROFILES[PROMO_PLATFORM_DOUYIN]["audience"])

        draft, t3 = agent.generate_draft(
            hotspot, PROMO_PLATFORM_DOUYIN, analysis, audience)
        record("Step3-规则轨", t3 == TRACK_RULE, f"实际{t3}")
        record("Step3-正文含警示语",
               REQUIRED_DISCLAIMER in draft["body"])
        record("Step3-正文含年龄提示", REQUIRED_AGE_TIP in draft["body"])

        # Step4 自查补齐: 构造缺警示草稿
        bad_draft = {"title": "x", "body": "纯粮好酒, 聚会走起"}
        checked, t4 = agent.self_check(bad_draft, PROMO_PLATFORM_DOUYIN)
        record("Step4-规则轨", t4 == TRACK_RULE, f"实际{t4}")
        record("Step4-缺警示自动补齐",
               REQUIRED_DISCLAIMER in checked["revisedBody"]
               and REQUIRED_AGE_TIP in checked["revisedBody"])
        record("Step4-自查报告",
               checked["selfCheck"].get("disclaimerOk") is True)

        # 四步链编排: 一源多态
        results = await agent.generate_platform_contents(
            hotspot, platforms=(PROMO_PLATFORM_DOUYIN, PROMO_PLATFORM_XHS,
                                PROMO_PLATFORM_MOMENTS))
        record("编排-三平台产出", len(results) == 3, f"实际{len(results)}")
        record("编排-平台覆盖",
               {r["platform"] for r in results} ==
               {PROMO_PLATFORM_DOUYIN, PROMO_PLATFORM_XHS,
                PROMO_PLATFORM_MOMENTS})
        record("编排-轨迹四步齐全",
               all(set(r["agentTrace"]) ==
                   {"step1Analysis", "step2Audience",
                    "step3Generate", "step4SelfCheck"} for r in results))
        record("编排-降级轨迹全规则轨",
               all(all(step == TRACK_RULE for step in r["agentTrace"].values())
                   for r in results))


class TestComplianceGate:
    async def run(self):
        svc = PromoService()
        gate = svc.compliance_gate

        clean = (f"竹香型白酒, 入口绵甜。"
                 f"（{REQUIRED_DISCLAIMER}，满{REQUIRED_AGE_TIP}周岁请适量）")
        result = gate(clean)
        record("闸门-合规文案满分100", result["score"] == 100,
               f"实际{result['score']}")
        record("闸门-合规文案无硬违规", result["hardFail"] == [])
        record("闸门-无需强制人工", not result["requiresManualReview"])

        # 一审硬规则: 饮酒动作 / 权威背书 / 功效暗示
        for word, label in (("干杯", "饮酒动作"), ("国家机关推荐", "权威背书"),
                            ("消除紧张", "功效暗示")):
            body = f"这个酒{word}! {REQUIRED_DISCLAIMER} {REQUIRED_AGE_TIP}周岁"
            result = gate(body)
            record(f"闸门-硬拒·{label}", word in result["hardFail"],
                   f"hardFail={result['hardFail']}")

        # 二审评分: 极限词 + 缺项
        result = gate("史上最好喝的酒!")
        record("闸门-极限词+双缺项(<60拒)",
               result["score"] < 60 and not result["hardFail"],
               f"score={result['score']}")
        record("闸门-违规清单记录", len(result["violations"]) >= 3,
               f"violations={result['violations']}")

        # 仅缺年龄提示 → 65 分强制人工区间
        body = f"竹香好酒。{REQUIRED_DISCLAIMER}"
        result = gate(body)
        record("闸门-60-80强制人工区间", result["requiresManualReview"]
               and 60 <= result["score"] < 80,
               f"score={result['score']}")


class TestGenerateFlow:
    async def run(self):
        svc = PromoService()
        hotspot = await _engaged_hotspot(svc)

        # 一源多态生成: 双平台
        contents = await svc.generate_contents(
            hotspot["hotspotId"],
            platforms=(PROMO_PLATFORM_DOUYIN, PROMO_PLATFORM_XHS))
        record("生成-双平台内容", len(contents) == 2, f"实际{len(contents)}")
        record("生成-同内容组(一源多态)",
               len({c["contentGroupId"] for c in contents}) == 1)
        record("生成-规则轨满分预审通过",
               all(c["status"] == CONTENT_STATUS_PENDING
                   and c["complianceScore"] == 100 for c in contents),
               f"scores={[c['complianceScore'] for c in contents]}")
        record("生成-短码已绑定",
               all(c["shortCode"].startswith("A-") for c in contents),
               f"codes={[c['shortCode'] for c in contents]}")

        # 冷却上限(默认 2 次)
        await svc.generate_contents(hotspot["hotspotId"],
                                    platforms=(PROMO_PLATFORM_DOUYIN,))
        try:
            await svc.generate_contents(hotspot["hotspotId"],
                                        platforms=(PROMO_PLATFORM_DOUYIN,))
            record("生成-冷却期满409", False)
        except ValueError as e:
            record("生成-冷却期满409", "冷却" in str(e), str(e))

        # 未跟进热点生成 → 冲突
        active = await svc.list_hotspots(status="active")
        try:
            await svc.generate_contents(active[0]["hotspotId"])
            record("生成-未跟进热点409", False)
        except ValueError:
            record("生成-未跟进热点409", True)

        # 无效平台
        try:
            await svc.generate_contents(hotspot["hotspotId"],
                                        platforms=("kuaishou",))
            record("生成-无效平台409", False)
        except ValueError:
            record("生成-无效平台409", True)

        # 热点不存在
        try:
            await svc.generate_contents(999999)
            record("生成-热点不存在404", False)
        except KeyError:
            record("生成-热点不存在404", True)

        # 硬拒内容预审: 注入违规 Agent 产出(模拟 LLM 越线)
        original = svc.agent.generate_draft
        svc.agent.generate_draft = lambda *a, **k: ({
            "title": "违规", "body": "兄弟们干杯不醉不归!",
            "hashtags": "", "cta": "", "coverHint": ""}, TRACK_RULE)
        try:
            engaged = await svc.list_hotspots(status="engaged")
            other = [h for h in engaged
                     if h["hotspotId"] != hotspot["hotspotId"]][0]
            contents = await svc.generate_contents(other["hotspotId"])
            record("生成-硬违规预审拒绝",
                   all(c["status"] == CONTENT_STATUS_REJECTED
                       and c["hardFail"] for c in contents),
                   f"status={[c['status'] for c in contents]}")
            record("生成-硬拒无短码",
                   all(not c["shortCode"] for c in contents))
        finally:
            svc.agent.generate_draft = original


async def main():
    test_classes = [
        ("Agent 四步链与降级", TestAgentChain),
        ("三审合规闸门", TestComplianceGate),
        ("生成流程与冷却", TestGenerateFlow),
    ]
    print("=" * 62)
    print("36号·AI智能推广模块 Agent工厂与合规闸门专项测试")
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
