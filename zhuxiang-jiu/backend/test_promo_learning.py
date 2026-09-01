"""36号·AI智能推广模块·P2 专项测试(Hedge 效果回流对接 ai_learning)

覆盖(设计文档 §3.6 效果回流):
    1. 评分器注册: promo_hotspot 入注册表 / 默认权重四因子 / 因子和=1
    2. 反馈提交: 引流量→决策正确性 / 因子快照(四分量+贡献) /
       learningFed 幂等 / 未发布内容 409 / clicks 自动归因聚合
    3. 批量回流: 已发布未回流内容自动采集 / 重复采集幂等
    4. 学习周期: 反馈不足 409 / Hedge 更新产出挑战者 / 晋升冠军 /
       生效权重切换(雷达评分权重即时变化)
    5. 雷达应用: 人工覆盖权重后扫描 scoreContributions 反映新权重 /
       相关度优先权重使排序翻转

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_promo_learning.py
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
from services.promo_radar_service import (
    PromoRadarService, DEFAULT_RADAR_WEIGHTS,
)
from services.ai_learning_service import (
    SCORER_REGISTRY, default_weights, invalidate_weight_cache,
    manual_override_weights, promote_challenger, update_learning_config,
)
from services.attract_service import AttractService
from repositories.ai_learning_repository import AiLearningRepository
from repositories.promo_repository import (
    CONTENT_STATUS_PUBLISHED, PROMO_PLATFORM_DOUYIN,
)
from datetime import datetime, UTC, timedelta

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
    invalidate_weight_cache("promo_hotspot")   # 权重缓存跨测试必须清


async def _publish_one(svc: PromoService, platform=PROMO_PLATFORM_DOUYIN,
                       hotspot_index=0):
    """全链路: 扫描→跟进→生成→审核→入队(过去时间)→发布, 返回发布内容

    hotspot_index 轮换热点(同热点冷却上限 2 条, 多次发布须换热点)。
    """
    await svc.scan()
    hotspot = (await svc.list_hotspots(
        status="engaged"))[hotspot_index % 10]
    contents = await svc.generate_contents(hotspot["hotspotId"],
                                           platforms=(platform,))
    approved = await svc.review_content(contents[0]["contentId"],
                                        approved=True)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await svc.publish_content(approved["contentId"], publish_at=past)
    published = await svc.process_publish_queue()
    return published[0]


class TestRegistration:
    async def run(self):
        record("注册-promo_hotspot入注册表",
               "promo_hotspot" in SCORER_REGISTRY)
        weights = default_weights("promo_hotspot")
        record("注册-默认权重四因子",
               set(weights) == {"heat", "velocity", "brandRelevance",
                                "persistence"},
               f"实际{set(weights)}")
        record("注册-权重与雷达常量一致",
               weights == DEFAULT_RADAR_WEIGHTS)
        record("注册-因子和=1", abs(sum(weights.values()) - 1.0) < 1e-9)

        # 评分含 contributions(供 Hedge 因子影响度)
        scoring = PromoRadarService.score_hotspot({
            "title": "中秋团圆宴白酒清单火了", "summary": "",
            "heat": 450.0, "velocity": 0.8, "persistenceHours": 36})
        record("注册-评分输出贡献快照",
               set(scoring["contributions"]) == set(weights),
               f"实际{set(scoring['contributions'])}")
        expected = round(weights["brandRelevance"]
                         * scoring["components"]["brandRelevance"], 4)
        record("注册-贡献=权重×分项",
               abs(scoring["contributions"]["brandRelevance"] - expected)
               < 1e-6,
               f"{scoring['contributions']['brandRelevance']} vs {expected}")


class TestFeedback:
    async def run(self):
        svc = PromoService()
        published = await _publish_one(svc)

        # 未发布内容 → 409(从另一 engaged 热点生成待审内容)
        engaged = await svc.list_hotspots(status="engaged")
        other = [h for h in engaged
                 if h["hotspotId"] != published["hotspotId"]][0]
        pending_contents = await svc.generate_contents(other["hotspotId"])
        try:
            await svc.submit_learning_feedback(
                pending_contents[0]["contentId"])
            record("反馈-未发布内容409", False)
        except ValueError:
            record("反馈-未发布内容409", True)

        # 正向反馈(手动指标)
        result = await svc.submit_learning_feedback(
            published["contentId"], clicks=5, registrations=2, orders=1)
        record("反馈-提交成功", result.get("success") is True
               and result.get("feedbackId") > 0, f"实际{result}")
        record("反馈-有点击判定正确", result.get("correct") is True)

        # 内容幂等标记 + 指标留痕
        content = await svc.repo.get_content(published["contentId"])
        record("反馈-learningFed幂等标记", content.get("learningFed") is True)
        record("反馈-指标留痕",
               content.get("learningMetrics") ==
               {"clicks": 5, "registrations": 2, "orders": 1},
               f"实际{content.get('learningMetrics')}")

        # 重复提交 → 409
        try:
            await svc.submit_learning_feedback(published["contentId"],
                                               clicks=9)
            record("反馈-重复提交409", False)
        except ValueError as e:
            record("反馈-重复提交409", "已回流" in str(e), str(e))

        # 负向反馈(零点击) + 因子快照校验(换热点避冷却)
        published2 = await _publish_one(svc, hotspot_index=1)
        result2 = await svc.submit_learning_feedback(
            published2["contentId"], clicks=0)
        record("反馈-零点击判定失误", result2.get("correct") is False)

        repo = AiLearningRepository()
        feedback = await repo.list_feedback("promo_hotspot", limit=10)
        record("反馈-落库两条", len(feedback) == 2, f"实际{len(feedback)}")
        record("反馈-因子快照四分量",
               len(feedback[0]["factors"]) == 4
               and {f["name"] for f in feedback[0]["factors"]}
               == set(DEFAULT_RADAR_WEIGHTS))
        record("反馈-来源标记promo",
               all(f.get("source") == "promo" for f in feedback))
        record("反馈-note含内容溯源",
               all("contentId=" in (f.get("note") or "") for f in feedback))

        # clicks 缺省 → 自动归因聚合(attract 点击; 再换热点)
        published3 = await _publish_one(svc, hotspot_index=2)
        attract = AttractService()
        click = await attract.resolve_click(code=published3["shortCode"],
                                            utm_source="douyin")
        await attract.attach_registration(click_id=click["clickId"],
                                          member_id=99001)
        result3 = await svc.submit_learning_feedback(
            published3["contentId"])
        record("反馈-clicks自动归因聚合(1点击)",
               result3.get("correct") is True,
               f"correct={result3.get('correct')}")
        content3 = await svc.repo.get_content(published3["contentId"])
        record("反馈-自动指标留痕",
               content3.get("learningMetrics", {}).get("clicks") == 1,
               f"实际{content3.get('learningMetrics')}")

        # 内容不存在 → 404
        try:
            await svc.submit_learning_feedback(999999)
            record("反馈-内容不存在404", False)
        except KeyError:
            record("反馈-内容不存在404", True)


class TestCollect:
    async def run(self):
        svc = PromoService()
        published = await _publish_one(svc)
        # 制造点击(该内容将判 correct=True; 无点击内容判 False)
        attract = AttractService()
        click = await attract.resolve_click(code=published["shortCode"])
        await attract.attach_registration(click_id=click["clickId"],
                                          member_id=99002)

        result = await svc.collect_learning_feedback()
        record("采集-提交1条", result["submitted"] == 1,
               f"实际{result['submitted']}")
        repo = AiLearningRepository()
        feedback = await repo.list_feedback("promo_hotspot", limit=10)
        record("采集-有点击内容判正确",
               any(f.get("correct") for f in feedback))

        # 再次采集: 幂等(全部 skip)
        result2 = await svc.collect_learning_feedback()
        record("采集-重复采集幂等",
               result2["submitted"] == 0 and result2["skipped"] == 1,
               f"实际{result2}")


class TestLearningCycle:
    async def run(self):
        svc = PromoService()
        published = await _publish_one(svc)

        # 反馈不足(默认 min_feedback=10) → 409
        await svc.submit_learning_feedback(published["contentId"], clicks=3)
        try:
            await svc.run_learning()
            record("学习-反馈不足409", False)
        except ValueError as e:
            record("学习-反馈不足409", "不足" in str(e), str(e))

        # 调低门槛 → 学习产出挑战者(默认影子模式)
        await update_learning_config("promo_hotspot", {"min_feedback": 1})
        learned = await svc.run_learning()
        record("学习-一轮Hedge更新", learned.get("success") is True
               and learned.get("learnedFrom") == 1, f"实际{learned}")
        record("学习-影子模式产出挑战者",
               learned.get("newStatus") == "challenger"
               and not learned.get("promoted"))
        record("学习-权重变化记录", bool(learned.get("weightDelta")))

        # 待学习反馈清零 → 再学 409
        try:
            await svc.run_learning()
            record("学习-反馈已消费409", False)
        except ValueError:
            record("学习-反馈已消费409", True)

        # 晋升挑战者 → 冠军, 生效权重切换
        promoted = await promote_challenger("promo_hotspot")
        record("学习-晋升冠军", promoted.get("success") is True
               and promoted.get("promotedVersion") == learned["newVersion"])
        effective = await svc.radar.get_effective_weights()
        record("学习-生效权重=晋升权重",
               effective == promoted["weights"],
               f"effective={effective} promoted={promoted['weights']}")
        record("学习-权重变化(非默认)",
               effective != dict(DEFAULT_RADAR_WEIGHTS),
               f"effective={effective}")

        # 状态视图
        status = await svc.learning_status()
        record("学习-状态视图结构",
               {"scorerId", "weights", "drift", "feedback", "contents",
                "effectiveWeights", "totalFedClicks"} <= set(status))
        record("学习-状态反馈统计",
               status["feedback"]["total"] == 1
               and status["feedback"]["pending"] == 0,
               f"实际{status['feedback']}")
        record("学习-状态回流统计",
               status["contents"]["fed"] == 1
               and status["contents"]["unfed"] == 0,
               f"实际{status['contents']}")


class TestRadarUsesLearnedWeights:
    async def run(self):
        svc = PromoService()

        # 确定性翻转: 高热度低相关 vs 低热度高相关
        h1 = {"title": "某明星官宣新剧", "summary": "", "heat": 500.0,
              "velocity": 1.0, "persistenceHours": 48}   # 相关度0.05
        h2 = {"title": "中秋团圆宴白酒清单", "summary": "", "heat": 0.0,
              "velocity": 0.0, "persistenceHours": 0}    # 相关度0.9
        default_s1 = PromoRadarService.score_hotspot(h1)["score"]
        default_s2 = PromoRadarService.score_hotspot(h2)["score"]
        record("雷达-默认权重热度优先", default_s1 > default_s2,
               f"h1={default_s1} h2={default_s2}")

        # 相关度优先权重(护栏内: 各因子默认值 0.5~2 倍, 和=1)
        relevance_weights = {"heat": 0.2, "velocity": 0.1,
                             "brandRelevance": 0.6, "persistence": 0.1}
        learned_s1 = PromoRadarService.score_hotspot(
            h1, weights=relevance_weights)["score"]
        learned_s2 = PromoRadarService.score_hotspot(
            h2, weights=relevance_weights)["score"]
        record("雷达-相关度权重排序翻转", learned_s2 > learned_s1,
               f"h1={learned_s1} h2={learned_s2}")

        # 人工覆盖为冠军 → 扫描即时用新权重
        await manual_override_weights("promo_hotspot", relevance_weights,
                                      reason="P2测试: 相关度优先")
        effective = await svc.radar.get_effective_weights()
        record("雷达-生效权重=人工覆盖",
               effective == relevance_weights, f"实际{effective}")

        await svc.scan()
        engaged = await svc.list_hotspots(status="engaged")
        # tier-3 热点(相关度0.9): brandRelevance 贡献 = 0.6×0.9=0.54
        tier3 = [h for h in engaged
                 if h.get("scoreComponents", {}).get("brandRelevance") == 0.9]
        record("雷达-扫描贡献快照用新权重",
               bool(tier3) and all(
                   abs(h["scoreContributions"]["brandRelevance"] - 0.54)
                   < 0.01 for h in tier3),
               f"实际{[h['scoreContributions'].get('brandRelevance') for h in tier3]}")
        # 默认权重下该贡献应为 0.3×0.9=0.27
        record("雷达-新权重贡献≠默认(0.27)",
               all(abs(h["scoreContributions"]["brandRelevance"] - 0.27)
                   > 0.1 for h in tier3))


async def main():
    test_classes = [
        ("评分器注册", TestRegistration),
        ("效果反馈提交", TestFeedback),
        ("批量回流采集", TestCollect),
        ("Hedge学习周期", TestLearningCycle),
        ("雷达应用学习权重", TestRadarUsesLearnedWeights),
    ]
    print("=" * 62)
    print("36号·AI智能推广模块 P2 专项测试(Hedge效果回流)")
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
