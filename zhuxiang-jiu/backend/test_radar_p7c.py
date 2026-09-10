"""40号·雷达2.0·P7c 事件演化预测与干预专项测试

覆盖(设计文档《40号 P7 规划方案》§5):
    1. 生命周期分段: 爆发期(0-6h)/发酵期(6-24h)/峰值期(24-48h)/
       衰退期(48h+)/峰值拐点预警(连续 2 槽位下滑)/早期信号(环比>2×)
    2. 跨平台关联: 机会窗(热议×沉默)/叙事变异推荐(调性对齐)/
       联动造势标记(≥3 平台环比>5×)+情绪降权
    3. 合规预演沙盘: L1 门槛/预案四件套/素材仅授权库/
       合规校验通过回写/钩子方向映射/404
    4. 集成: 预测后重评降级(L1→L2——生命周期门)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_radar_p7c.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.radar_forecast_service import (
    RadarForecastService, topic_fingerprint, slot_ordinal,
    domain_alignment, banned_guardrail_words,
    CROSS_HOT_LINE, COORDINATED_SURGE_RATIO, HYPE_EMOTION_FACTOR,
    BURST_WINDOW_H, FERMENT_WINDOW_H, DECAY_WINDOW_H,
)
from services.radar_score_service import RadarScoreService
from services.radar_hub_service import RadarHubService
from repositories.radar_repository import (
    RadarRepository, CATEGORY_CURRENT, LIFECYCLE_NEW,
    LIFECYCLE_RISING, LIFECYCLE_PEAK, LIFECYCLE_DECAY,
    GRADE_L1, GRADE_L2, GRADE_L4,
)
from repositories.blogger_repository import BloggerRepository

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


async def seed_event(repo, **kw) -> dict:
    """构造受控测试事件并直接入库"""
    event_id = await repo.next_id("event")
    event = {
        "eventId": event_id,
        "fingerprint": f"fp-{event_id:04d}",
        "channelId": kw.get("channelId", 1),
        "channelName": kw.get("channelName", "测试频道"),
        "platform": kw.get("platform", "douyin"),
        "title": kw["title"],
        "summary": kw.get("summary", ""),
        "asrTranscript": kw.get("asrTranscript", ""),
        "ocrTags": kw.get("ocrTags", []),
        "bgmFingerprint": kw.get("bgmFingerprint", ""),
        "category": kw.get("category", CATEGORY_CURRENT),
        "heatBase": kw.get("heatBase", 500),
        "emotionDensity": kw.get("emotionDensity", 0.8),
        "crowdEmotion": kw.get("crowdEmotion", "neutral"),
        "botFiltered": False, "botShare": 0.0,
        "lifecycle": kw.get("lifecycle", LIFECYCLE_NEW),
        "totalSlots": kw.get("totalSlots", 0),
        "grade": kw.get("grade", ""),
        "firstSeenAt": "2026-09-11T00:00:00+00:00",
        "lastSeenAt": "2026-09-11T00:00:00+00:00",
    }
    return await repo.save_event(event)


async def seed_slot(repo, event: dict, slot_key: str,
                    heat: int) -> None:
    """构造槽位观测(受控热度序列)"""
    slot_id = await repo.next_id("slot")
    await repo.save_slot({
        "slotId": slot_id,
        "eventId": event["eventId"],
        "fingerprint": event["fingerprint"],
        "slotKey": slot_key,
        "heatValue": heat,
        "danmakuCount": 10, "commentCount": 30,
        "botClusterCount": 0,
        "emotionDensity": 0.3,
        "createdAt": "2026-09-11T00:00:00+00:00",
    })


async def seed_license(kind: str, name: str,
                       status: str = "active") -> None:
    """播种 P6b 授权(预演素材建议源)"""
    license_id = await BloggerRepository().next_id("license")
    await BloggerRepository().save_license({
        "licenseId": license_id, "kind": kind, "name": name,
        "grantor": "测试授权方", "scope": "全平台",
        "expiresAt": "", "status": status,
        "evidenceHash": f"hash-{license_id:04d}",
        "createdAt": "2026-09-11T00:00:00+00:00",
    })


async def seed_conversion(repo, category, clicks, registered,
                          activated) -> None:
    """播种转化历史样本(radar_scores 漏斗字段口径)"""
    score_id = await repo.next_id("score")
    await repo.save_score({
        "scoreId": score_id, "eventId": 0,
        "fingerprint": f"hist-{score_id:04d}",
        "category": category, "title": "历史样本",
        "fit": 0, "fitModules": [], "safety": 1.0,
        "safetyReasons": [], "conversion": 0.0,
        "conversionSamples": 0, "valueScore": 0,
        "grade": "historical", "blockedReasons": [],
        "lifecycle": "decay", "heatBase": 0,
        "clicks": clicks, "registered": registered,
        "activated": activated,
        "scoredAt": "2026-09-11T00:00:00+00:00",
    })


# ============================================================
# 1. 生命周期分段(7 断言)
# ============================================================

class TestLifecycle:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        svc = RadarForecastService()

        # 1) 爆发期: 相邻槽位(0.25h <6h)热度上升
        evt = await seed_event(repo, title="极端天气自救指南",
                               summary="暴雨应对手册")
        await seed_slot(repo, evt, "20260911s0000", 500)
        await seed_slot(repo, evt, "20260911s0001", 800)
        r = await svc.predict_event(evt["eventId"])
        record("分段-爆发期",
               r["lifecycle"] == LIFECYCLE_RISING
               and r["phase"] == "爆发期"
               and "黄金介入窗" in r["advice"],
               f"ph={r['phase']}")

        # 2) 发酵期: 32 槽位差(8h ∈ 6-24h)
        evt = await seed_event(repo, title="节假日囤货指南",
                               summary="理性消费观察")
        await seed_slot(repo, evt, "20260911s0000", 500)
        await seed_slot(repo, evt, "20260911s0032", 600)
        r = await svc.predict_event(evt["eventId"])
        record("分段-发酵期",
               r["lifecycle"] == LIFECYCLE_RISING
               and r["phase"] == "发酵期",
               f"ph={r['phase']}")

        # 3) 峰值期: 120 槽位差(30h ∈ 24-48h)
        evt = await seed_event(repo, title="社区互助新模式",
                               summary="邻里守望在身边")
        await seed_slot(repo, evt, "20260911s0000", 500)
        await seed_slot(repo, evt, "20260911s0120", 600)
        r = await svc.predict_event(evt["eventId"])
        record("分段-峰值期",
               r["lifecycle"] == LIFECYCLE_PEAK
               and r["phase"] == "峰值期",
               f"ph={r['phase']}")

        # 4) 峰值拐点预警: 连续 2 槽位下滑 → 衰退期(模式优先)
        evt = await seed_event(repo, title="美食探店新去处",
                               summary="好店推荐")
        await seed_slot(repo, evt, "20260911s0000", 800)
        await seed_slot(repo, evt, "20260911s0001", 500)
        await seed_slot(repo, evt, "20260911s0002", 300)
        r = await svc.predict_event(evt["eventId"])
        record("分段-拐点预警衰退",
               r["lifecycle"] == LIFECYCLE_DECAY
               and r["phase"] == "衰退期"
               and any("峰值拐点预警" in s and "避免在下滑期投入"
                       in s for s in r["signals"]),
               f"sig={r['signals']}")

        # 5) 时间窗衰退: 200 槽位差(50h ≥48h)
        evt = await seed_event(repo, title="礼盒选购攻略",
                               summary="送礼指南")
        await seed_slot(repo, evt, "20260911s0000", 500)
        await seed_slot(repo, evt, "20260911s0200", 600)
        r = await svc.predict_event(evt["eventId"])
        record("分段-时间窗衰退",
               r["lifecycle"] == LIFECYCLE_DECAY
               and r["phase"] == "衰退期",
               f"ph={r['phase']}")

        # 6) 早期信号: 槽位热度环比>2×(300→700)
        evt = await seed_event(repo, title="暴雨防灾手册",
                               summary="极端天气自救")
        await seed_slot(repo, evt, "20260911s0000", 300)
        await seed_slot(repo, evt, "20260911s0001", 700)
        r = await svc.predict_event(evt["eventId"])
        record("分段-早期信号",
               any("早期爆发信号" in s and "环比>2×"
                   in s for s in r["signals"]),
               f"sig={r['signals']}")

        # 7) 生命周期回写(事件记录) + 404 语义
        stored = await repo.get_event(evt["eventId"])
        ok404 = False
        try:
            await svc.predict_event(99999)
        except KeyError:
            ok404 = True
        record("分段-回写与404",
               stored.get("lifecycle") == LIFECYCLE_RISING
               and stored.get("predictedPhase") == "爆发期"
               and stored.get("predictedAt")
               and ok404,
               f"lc={stored.get('lifecycle')}")


# ============================================================
# 2. 跨平台关联(5 断言)
# ============================================================

class TestCrossPlatform:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        svc = RadarForecastService()
        hub = RadarHubService()
        await hub.seed_channels()   # 平台全集: 3 平台

        # 8) 机会窗: A 平台热议而 B/C 平台沉默
        evt = await seed_event(repo, title="暴雨自救指南",
                               summary="应急手册",
                               platform="douyin", heatBase=900)
        r = await svc.predict_event(evt["eventId"])
        opp = r["crossPlatform"]["opportunity"]
        record("跨平台-机会窗",
               opp["hotPlatforms"] == ["douyin"]
               and opp["silentPlatforms"] == [
                   "bilibili", "xiaohongshu"]
               and opp["windows"] == ["bilibili", "xiaohongshu"]
               and "差异化切入窗" in opp["note"],
               f"opp={opp}")

        # 9) 叙事变异推荐: 调性对齐(邻里互助 > 通用)
        evt_a = await seed_event(
            repo, title="台风防灾囤货指南", summary="全民必备手册",
            platform="douyin", heatBase=300)
        evt_b = await seed_event(
            repo, title="台风防灾囤货指南", summary="邻里互助囤货攻略",
            platform="xiaohongshu", heatBase=300)
        r = await svc.predict_event(evt_a["eventId"])
        nar = r["crossPlatform"]["narrative"]
        record("跨平台-叙事变异推荐",
               len(nar["variants"]) == 2
               and nar["recommended"]["platform"]
               == "xiaohongshu"
               and nar["recommended"]["alignment"] == 2
               and "互助" in nar["wordDiff"],
               f"rec={nar['recommended']} diff="
               f"{nar['wordDiff']}")

        # 10) 联动造势: 3 平台同步陡增(环比 6×>5×)+情绪降权
        coords = []
        for plat, fp in (("douyin", "c1"), ("xiaohongshu", "c2"),
                         ("bilibili", "c3")):
            e = await seed_event(
                repo, title="联动造势测试主题", summary="同步陡增",
                platform=plat, heatBase=600,
                fingerprint=fp)
            await seed_slot(repo, e, "20260911s0000", 100)
            await seed_slot(repo, e, "20260911s0001", 600)
            coords.append(e)
        r = await svc.predict_event(coords[0]["eventId"])
        hype = r["crossPlatform"]["coordinatedHype"]
        stored = await repo.get_event(coords[1]["eventId"])
        record("跨平台-联动造势标记",
               hype["suspected"] is True
               and hype["platforms"] == 3
               and all(x > COORDINATED_SURGE_RATIO
                       for x in hype["surgeRatios"])
               and stored.get("coordinatedHype") is True
               and stored.get("emotionDensity")
               == round(0.8 * HYPE_EMOTION_FACTOR, 4),
               f"hype={hype} ed="
               f"{stored.get('emotionDensity')}")

        # 11) 非联动: 环比 3×≤5× 不标记
        resets = []
        for plat, fp in (("douyin", "n1"), ("xiaohongshu", "n2"),
                         ("bilibili", "n3")):
            e = await seed_event(
                repo, title="正常热度主题测试", summary="平稳",
                platform=plat, heatBase=300,
                fingerprint=fp)
            await seed_slot(repo, e, "20260911s0000", 100)
            await seed_slot(repo, e, "20260911s0001", 300)
            resets.append(e)
        r = await svc.predict_event(resets[0]["eventId"])
        hype = r["crossPlatform"]["coordinatedHype"]
        record("跨平台-非联动不标记",
               hype["suspected"] is False,
               f"hype={hype}")

        # 12) 无窗口: 无热议平台(热度<阈值)
        evt = await seed_event(repo, title="冷门主题观察",
                               summary="低热度",
                               platform="douyin", heatBase=300)
        r = await svc.predict_event(evt["eventId"])
        opp = r["crossPlatform"]["opportunity"]
        record("跨平台-无热议无窗口",
               opp["windows"] == [] and opp["hotPlatforms"] == []
               and "无差异化机会窗" in opp["note"],
               f"opp={opp}")


# ============================================================
# 3. 合规预演沙盘(7 断言)
# ============================================================

class TestRehearse:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        svc = RadarForecastService()

        # 13) L1 门槛: L2 → 拒绝
        l2 = await seed_event(repo, title="节日囤货指南",
                              summary="礼盒消费", grade=GRADE_L2)
        ok_l2 = False
        try:
            await svc.rehearse_event(l2["eventId"])
        except ValueError:
            ok_l2 = True
        record("预演-L2拒绝", ok_l2)

        # 14) 未评分 → 拒绝; L4 → 拒绝
        ungraded = await seed_event(repo, title="未评分事件",
                                    summary="观察")
        ok_un = False
        try:
            await svc.rehearse_event(ungraded["eventId"])
        except ValueError:
            ok_un = True
        l4 = await seed_event(repo, title="国际局势新动向",
                              summary="专家解读", grade=GRADE_L4)
        ok_l4 = False
        try:
            await svc.rehearse_event(l4["eventId"])
        except ValueError:
            ok_l4 = True
        record("预演-未评分与L4拒绝", ok_un and ok_l4)

        # 15) 预案四件套完整(授权库播种: 2 active + 1 revoked)
        await seed_license("bgm", "授权BGM-清风")
        await seed_license("material", "授权素材-餐桌")
        await seed_license("material", "过期素材-旧片",
                           status="revoked")
        l1 = await seed_event(repo, title="极端天气自救指南",
                              summary="暴雨应对手册",
                              grade=GRADE_L1, heatBase=900)
        r = await svc.rehearse_event(l1["eventId"])
        plan = r["plan"]
        record("预演-四件套完整",
               r["passed"] is True
               and len(plan["recommendedAngles"]) == 3
               and "政治" in plan["bannedPhrasings"]
               and "辟谣" in plan["bannedPhrasings"]
               and bool(plan["materialSuggestions"])
               and bool(plan["hookDirection"]),
               f"keys={list(plan.keys())}")

        # 16) 素材建议仅授权库(active 出现/revoked 不出现)
        mats = plan["materialSuggestions"]
        names = [n for v in mats.values() for n in v]
        record("预演-素材仅授权库",
               "授权BGM-清风" in mats.get("bgm", [])
               and "授权素材-餐桌" in mats.get("material", [])
               and "过期素材-旧片" not in names,
               f"mats={mats}")

        # 17) 合规校验通过+事件回写(rehearsalPlan/Passed/At)
        stored = await repo.get_event(l1["eventId"])
        record("预演-校验通过回写",
               isinstance(stored.get("rehearsalPlan"), dict)
               and stored.get("rehearsalPassed") is True
               and stored.get("rehearsedAt"),
               "plan/passed/at")

        # 18) 钩子方向映射(应急/互助 → 情感陪伴+场景种草)
        record("预演-钩子方向映射",
               "情感陪伴钩" in plan["hookDirection"]
               and "场景种草钩" in plan["hookDirection"],
               f"hook={plan['hookDirection']}")

        # 19) 404 语义(事件不存在)
        ok404 = False
        try:
            await svc.rehearse_event(99999)
        except KeyError:
            ok404 = True
        record("预演-404语义", ok404)


# ============================================================
# 4. 集成: 预测后重评降级 + 纯函数口径(2 断言)
# ============================================================

class TestIntegration:
    async def run(self):
        reset_store()
        repo = RadarRepository()
        forecast = RadarForecastService()
        scorer = RadarScoreService()

        # 20) L1 → 拐点预测 → 重评降级 L2(生命周期门)
        for _ in range(3):
            await seed_conversion(repo, CATEGORY_CURRENT,
                                  clicks=100, registered=95,
                                  activated=90)
        evt = await seed_event(repo, title="极端天气自救指南",
                               summary="暴雨应对手册",
                               heatBase=900)
        await seed_slot(repo, evt, "20260911s0000", 800)
        await seed_slot(repo, evt, "20260911s0001", 500)
        await seed_slot(repo, evt, "20260911s0002", 300)
        s1 = await scorer.score_events(
            event_ids=[evt["eventId"]])
        await forecast.predict_event(evt["eventId"])
        s2 = await scorer.score_events(
            event_ids=[evt["eventId"]])
        record("集成-预测后重评降级",
               s1["results"][0]["grade"] == GRADE_L1
               and s2["results"][0]["grade"] == GRADE_L2
               and s2["results"][0]["valueScore"] == 78.2,
               f"g1={s1['results'][0]['grade']} "
               f"g2={s2['results'][0]['grade']}")

        # 21) 纯函数口径: 主题指纹/槽位序数/调性对齐/护栏词
        record("集成-纯函数口径",
               topic_fingerprint("暴雨自救指南")
               == topic_fingerprint("暴雨 自救指南!")
               != topic_fingerprint("别的主题")
               and slot_ordinal("20260911s0001")
               - slot_ordinal("20260910s0095") == 2
               and domain_alignment("邻里互助送礼") == 4
               and "未成年" in banned_guardrail_words()
               and BURST_WINDOW_H == 6
               and FERMENT_WINDOW_H == 24
               and DECAY_WINDOW_H == 48
               and CROSS_HOT_LINE == 500,
               "纯函数矩阵")


async def main():
    tests = [TestLifecycle(), TestCrossPlatform(),
             TestRehearse(), TestIntegration()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P7c 雷达2.0 事件演化预测与干预专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
