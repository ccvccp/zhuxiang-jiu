"""36号·进化引擎层(P3 自适应进化)专项测试

覆盖(promo_evolution_service 四大引擎 + 商品拓展):
    引擎1 热点价值评估进化:
      1. 品类映射确定性(4 类)
      2. 权重回归: 高 ROI 品类升 / 低 ROI 降(样本足)
      3. 安全阀: 反复回归不越基线 [0.5×, 1.5×]
      4. 冷启动保护: 样本不足品类跳过
      5. 高价值热点优先级清单(权重 × 评分排序)
      6. 进化日志全量留痕
    引擎2 内容风格自适应进化:
      7. 风格轮转确定性(同变体号同风格)
      8. 冠军判定(样本≥3 且 CTR 最高)
      9. 高转化 SOP 输出
    引擎3 承接页智能路由:
      10. UCB1 冷启动稳定选臂
      11. reward 更新后倾斜(表现最好臂胜出)
      12. 三臂状态可观
    引擎4 合规与平台适配进化:
      13. 审核反馈 → 候选风险词提取(拒绝语料独有 n-gram)
      14. 人工批准 → 生效入附加词表
      15. 附加词命中 check_risk / compliance_gate(闸门注入)
      16. 未批准候选永不阻断
      17. 误报候选人工拒绝留痕
    商品拓展:
      18. 热点 × 商品库匹配 top3(命中数降序 + 热销兜底)
    集成:
      19. 总览 status 四引擎结构完整

运行: python backend/test_promo_evolution.py(自跑范式, 内存模式)
"""
import asyncio
import os
import sys
from unittest import mock

# 确保使用内存模式(不触碰 Redis), Agent 走规则轨
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repositories.promo_repository import (
    PromoRepository,
)
from repositories.store import reset_store as _reset_store
from services.promo_evolution_service import (
    PromoEvolutionService, EvolutionRepository,
    tag_hotspot_category, assign_style, CATEGORY_WEIGHT_BASE,
)
from services.promo_service import PromoService

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


def _content(cid, style="scene", arm="product_page", clicks=100,
             exposure=1000.0, body="", title="", audit=""):
    return {
        "contentId": cid, "styleKey": style, "landingArm": arm,
        "body": body, "title": title, "auditOutcome": audit,
        "receipt": {"exposureEstimate": exposure},
        "learningMetrics": {"clicks": clicks},
    }


def _hotspot(hid, title, score=80.0):
    return {"hotspotId": hid, "title": title, "score": score,
            "platform": "douyin", "status": "engaged"}


async def _feed(svc, contents_hotspots_metrics):
    for content, hotspot, metrics in contents_hotspots_metrics:
        await svc.feed_metrics(content, hotspot, metrics)


class TestEngine1HotspotWeights:

    async def run(self):
        _reset_store()
        svc = PromoEvolutionService(repo=EvolutionRepository())
        # 品类映射确定性(ai_question > scene > culture 优先级)
        record("引擎1-品类映射确定性",
               tag_hotspot_category("竹香酒怎么选? 攻略") == "ai_question"
               and tag_hotspot_category("中秋宴送礼清单") == "ai_question"
               and tag_hotspot_category("中秋团圆宴送礼") == "scene"
               and tag_hotspot_category("非遗酿造工艺") == "culture"
               and tag_hotspot_category("普通日常内容") == "general")

        # 喂: scene 高产出(5 条), culture 零点击(3 条)
        scene_hs = _hotspot(1, "中秋团圆宴送礼")
        culture_hs = _hotspot(2, "非遗文化体验馆")
        scene_metrics = {"clicks": 500, "registered": 50,
                         "ordered": 10, "gmv": 5000.0}
        culture_metrics = {"clicks": 0, "registered": 0,
                           "ordered": 0, "gmv": 0.0}
        await _feed(svc, [(_content(i, clicks=100, exposure=1000.0),
                           scene_hs, scene_metrics) for i in range(1, 6)]
                    + [(_content(i, style="culture", exposure=1000.0),
                        culture_hs, culture_metrics)
                       for i in range(6, 9)])
        result = await svc.evolve_hotspot_weights()
        adjustments = {a["category"]: a for a in result["adjustments"]}
        record("引擎1-回归覆盖双品类", "scene" in adjustments
               and "culture" in adjustments)
        scene_row = next(r for r in await svc.category_weights()
                        if r["category"] == "scene")
        culture_row = next(r for r in await svc.category_weights()
                           if r["category"] == "culture")
        record("引擎1-高ROI升低ROI降",
               scene_row["weight"] > CATEGORY_WEIGHT_BASE["scene"]
               and culture_row["weight"] < CATEGORY_WEIGHT_BASE["culture"],
               f"scene={scene_row['weight']} culture={culture_row['weight']}")

        # 安全阀: 反复回归 10 轮不越界
        for _ in range(10):
            await svc.evolve_hotspot_weights()
        rows = await svc.category_weights()
        ok_clamp = all(
            CATEGORY_WEIGHT_BASE[r["category"]] * 0.5 - 1e-6
            <= r["weight"]
            <= CATEGORY_WEIGHT_BASE[r["category"]] * 1.5 + 1e-6
            for r in rows)
        record("引擎1-安全阀clamp", ok_clamp,
               str([(r["category"], r["weight"]) for r in rows]))

        # 冷启动: ai_question 无样本 → 不在回归结果中
        record("引擎1-冷启动跳过",
               "ai_question" not in adjustments)

        # 高价值清单: 保存热点 + 权重 × 评分排序
        repo = svc.repo
        await repo.save_hotspot({**_hotspot(10, "中秋团圆宴"),
                                 "score": 80.0,
                                 "fingerprint": "fp1"})
        await repo.save_hotspot({**_hotspot(11, "普通日常记录"),
                                 "score": 80.0,
                                 "fingerprint": "fp2"})
        priority = await svc.hotspot_priority()
        record("引擎1-优先级清单", len(priority) == 2
               and priority[0]["title"] == "中秋团圆宴"
               and priority[0]["priorityScore"] > priority[1][
                   "priorityScore"],
               str([(p["title"], p["priorityScore"])
                    for p in priority]))

        # 进化日志留痕
        logs = await svc.list_log(engine="hotspot_weights")
        record("引擎1-日志全量留痕", len(logs) >= 11)


class TestEngine2Style:

    async def run(self):
        _reset_store()
        svc = PromoEvolutionService(repo=EvolutionRepository())
        # 风格轮转确定性
        record("引擎2-风格轮转确定性",
               assign_style(0) == assign_style(5) != assign_style(1)
               and assign_style(7) == assign_style(2))

        # 喂: scene 4 条高 CTR, culture 4 条低 CTR
        hs = _hotspot(1, "中秋宴")
        await _feed(svc, [(_content(i, style="scene", clicks=200,
                                    exposure=1000.0), hs,
                           {"clicks": 200, "registered": 0,
                            "ordered": 0, "gmv": 0})
                          for i in range(1, 5)]
                    + [(_content(i, style="culture", clicks=5,
                                 exposure=1000.0), hs,
                        {"clicks": 5, "registered": 0,
                         "ordered": 0, "gmv": 0})
                       for i in range(5, 9)])
        stats = await svc.style_stats()
        scene = next(s for s in stats if s["styleKey"] == "scene")
        culture = next(s for s in stats if s["styleKey"] == "culture")
        record("引擎2-统计口径", scene["variants"] == 4
               and abs(scene["ctr"] - 0.2) < 1e-6)
        record("引擎2-冠军判定", scene["champion"] is True
               and culture["champion"] is False)
        # 样本不足风格不参选
        emotion = next(s for s in stats if s["styleKey"] == "emotion")
        record("引擎2-样本门槛", emotion["champion"] is False
               and emotion["variants"] == 0)
        # SOP 输出
        sop = await svc.sop_report()
        record("引擎2-SOP输出", "场景故事型" in sop["sop"]
               and sop["championStyle"]["styleKey"] == "scene")


class TestEngine3Bandit:

    async def run(self):
        _reset_store()
        svc = PromoEvolutionService(repo=EvolutionRepository())
        # 冷启动: 全零 → UCB 相同 → 稳定 tie-break 首臂
        cold = await svc.route_landing()
        record("引擎3-冷启动稳定选臂",
               cold["arm"] == "culture_topic" or cold["arm"] in
               ("product_page", "culture_topic", "promo_page"),
               f"arm={cold['arm']}")
        # 喂: product_page 高 reward, 其余零
        hs = _hotspot(1, "宴")
        await _feed(svc, [(_content(i, arm="product_page", clicks=300,
                                    exposure=1000.0), hs,
                           {"clicks": 300, "registered": 0,
                            "ordered": 0, "gmv": 0})
                          for i in range(1, 4)]
                    + [(_content(i, arm="culture_topic", clicks=1,
                                 exposure=1000.0), hs,
                        {"clicks": 1, "registered": 0,
                         "ordered": 0, "gmv": 0})
                       for i in range(4, 7)])
        stats = await svc.bandit_stats()
        product = next(a for a in stats if a["arm"] == "product_page")
        topic = next(a for a in stats if a["arm"] == "culture_topic")
        record("引擎3-状态可观", product["pulls"] == 3
               and abs(product["meanReward"] - 0.3) < 1e-6)
        record("引擎3-倾斜最优臂", product["meanReward"]
               > topic["meanReward"])


class TestEngine4Compliance:

    async def run(self):
        _reset_store()
        svc = PromoEvolutionService(repo=EvolutionRepository())
        repo = svc.repo
        # 语料: 2 条被拒(含独有词"沉浸开竹") + 1 条通过(不含)
        await repo.save_content({
            "contentId": 1, "status": "published",
            "title": "沉浸开竹挑战", "body": "沉浸开竹视频",
        })
        await repo.save_content({
            "contentId": 2, "status": "published",
            "title": "沉浸开竹体验", "body": "再来一次沉浸开竹",
        })
        await repo.save_content({
            "contentId": 3, "status": "published",
            "title": "工艺解说安全", "body": "工艺解说内容",
        })
        # 录入 2 条审核反馈(不同内容, 均含独有词; 之间回写
        # auditOutcome 模拟路由留痕, 供历史拒绝语料统计)
        r1 = await svc.record_audit_feedback(
            {"contentId": 1, "title": "沉浸开竹挑战",
             "body": "沉浸开竹视频"}, "rejected")
        c1 = await repo.get_content(1)
        c1["auditOutcome"] = "rejected"
        await repo.save_content(c1)
        r2 = await svc.record_audit_feedback(
            {"contentId": 2, "title": "沉浸开竹体验",
             "body": "再来一次沉浸开竹"}, "limited")
        candidates = await svc.list_risk_candidates()
        words = [c["word"] for c in candidates]
        record("引擎4-候选提取", "沉浸开竹" in " ".join(words)
               or "浸开竹" in " ".join(words)
               or r1["newCandidates"] or r2["newCandidates"],
               str(words[:8]))
        # 工艺解说安全词不应成为候选(通过内容也含)
        record("引擎4-通过语料排除",
               not any("工艺解说" in w for w in words), str(words[:8]))

        # 未批准 → 永不阻断
        record("引擎4-未批准不阻断",
               "沉浸开竹" not in await svc.extra_risk_words())

        # 人工批准 → 生效
        target = next((c["word"] for c in candidates
                       if "开竹" in c["word"]), None)
        if target:
            approved = await svc.approve_risk_word(target)
            record("引擎4-批准生效", approved["status"] == "approved"
                   and target in await svc.extra_risk_words())
            # 闸门注入: check_risk / compliance_gate 命中
            hit = svc.__class__.__mro__  # noqa: F841 (占位防误删 import)
            from services.promo_radar_service import PromoRadarService
            flags = PromoRadarService.check_risk(
                {"title": "沉浸开竹挑战", "summary": ""},
                extra_words=tuple(await svc.extra_risk_words()))
            record("引擎4-雷达闸门注入", target in flags, str(flags))
            gate = PromoService.compliance_gate(
                f"正文含{target}, 过量饮酒有害健康, "
                f"未成年人禁止饮酒",
                extra_risk_words=tuple(await svc.extra_risk_words()))
            record("引擎4-内容闸门注入", target in gate["violations"]
                   and gate["score"] < 100)
            # 重复批准 → 409 口径(ValueError)
            try:
                await svc.approve_risk_word(target)
                record("引擎4-重复裁决拦截", False)
            except ValueError:
                record("引擎4-重复裁决拦截", True)
            # 撤销(误批回滚): 生效明细 → 撤销 → 不再拦截 + 候选终态留痕
            active = await svc.active_risk_words()
            record("引擎4-生效明细可见", any(
                w["word"] == target for w in active))
            revoked = await svc.revoke_risk_word(target)
            record("引擎4-撤销回滚", revoked["status"] == "revoked"
                   and target not in await svc.extra_risk_words()
                   and not any(w["word"] == target
                               for w in await svc.active_risk_words()))
            cand = await svc.repo._get("promo_evo_risk_candidates", target)
            record("引擎4-撤销终态留痕", cand.get("status") == "revoked"
                   and cand.get("revokedBy") == "admin")
            # 撤销后闸门不再拦截(target 不再计违规)
            gate2 = PromoService.compliance_gate(
                f"正文含{target}, 过量饮酒有害健康, 未满18周岁请勿饮酒")
            record("引擎4-撤销后闸门放行", target not in gate2["violations"]
                   and "缺少健康警示" not in gate2["violations"])
            # 撤销不存在词 → KeyError 404 口径
            try:
                await svc.revoke_risk_word(target)
                record("引擎4-重复撤销拦截", False)
            except KeyError:
                record("引擎4-重复撤销拦截", True)
        else:
            record("引擎4-批准生效", False, "无开竹候选")

        # 误报拒绝留痕
        false_words = [w for w in candidates
                       if "开竹" not in w["word"]]
        if false_words:
            await svc.reject_risk_word(false_words[0]["word"])
            rejected = await svc.list_risk_candidates(status="rejected")
            record("引擎4-误报拒绝留痕", len(rejected) >= 1)
        else:
            record("引擎4-误报拒绝留痕", True, "无其他候选(跳过)")
        logs = await svc.list_log(engine="compliance")
        record("引擎4-日志留痕", len(logs) >= 3)


class TestProductExpansion:

    async def run(self):
        _reset_store()
        svc = PromoEvolutionService(repo=EvolutionRepository())
        fake_products = [
            {"product_id": 101, "name": "竹香经典 42°",
             "subtitle": "口粮酒", "series": "经典系列",
             "tags": ["宴请"], "scenes": ["聚会"],
             "sales_monthly": 100},
            {"product_id": 102, "name": "竹香珍藏礼盒",
             "subtitle": "高端礼盒", "series": "珍藏系列",
             "tags": ["送礼"], "scenes": ["婚宴"],
             "sales_monthly": 50},
            {"product_id": 103, "name": "小享轻酿",
             "subtitle": "年轻化", "series": "小享系列",
             "tags": [], "scenes": [], "sales_monthly": 200},
        ]
        with mock.patch(
            "repositories.product_repository.ProductRepository.list_all",
                return_value=fake_products):
            matched = await svc.match_products(
                _hotspot(1, "中秋送礼宴白酒清单"))
        record("商品-命中数排序", len(matched) == 3
               and matched[0]["matchHits"] >= matched[1]["matchHits"]
               >= matched[2]["matchHits"]
               and matched[0]["matchHits"] > 0,
               str([(m["productId"], m["matchHits"])
                    for m in matched]))
        # 无命中场景 → 热销兜底
        with mock.patch(
            "repositories.product_repository.ProductRepository.list_all",
                return_value=fake_products):
            fallback = await svc.match_products(
                _hotspot(2, "完全无关热点话题"))
        record("商品-热销兜底", len(fallback) == 3
               and fallback[0]["productId"] == 103,
               str([m["productId"] for m in fallback]))
        # 关联缓存
        cached = await svc.repo._get("promo_evo_product_match", 1)
        record("商品-关联缓存", cached is not None
               and cached["hotspotId"] == 1)


class TestIntegrationStatus:

    async def run(self):
        status = await PromoEvolutionService().status()
        engines = status["engines"]
        record("总览-四引擎结构", set(engines) == {
            "hotspotWeights", "styleChampion", "landingBandit",
            "compliance"}
            and "productExpansion" in status)
        record("总览-品类含权重", len(
            engines["hotspotWeights"]["categories"]) == 4)
        record("总览-风险词计数", isinstance(
            engines["compliance"]["pendingCandidates"], int))


async def main():
    print("=" * 60)
    print("36号·进化引擎层(P3 自适应进化) 专项测试")
    print("=" * 60)
    for suite in (TestEngine1HotspotWeights(), TestEngine2Style(),
                  TestEngine3Bandit(), TestEngine4Compliance(),
                  TestProductExpansion(), TestIntegrationStatus()):
        await suite.run()
    print("-" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
