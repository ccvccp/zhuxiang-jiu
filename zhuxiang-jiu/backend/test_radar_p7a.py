"""40号·雷达2.0·P7a 感知与聚合专项测试

覆盖(设计文档《40号 P7 规划方案》§3):
    1. 种子频道池: 12 频道惰性灌入/类别分布/幂等/增量入库/
       重复拒绝/非法类别 409
    2. 事件流采集: 槽位确定性/多模态字段/事件创建
    3. 聚类去重: 同指纹跨槽位聚合(热度 max 演化+槽位观测)/
       同槽位重复采集幂等
    4. 情绪场域: 词表密度/群体分类/刷量过滤(聚簇>50% 降权)/
       原文即用即弃
    5. 查询面: 热度降序/过滤/详情多模态/404

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_radar_p7a.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
# 雷达槽位锚点(测试确定性: 跨槽演化测试内显式改)
os.environ["RADAR_MOCK_SLOT"] = "40"
os.environ["RADAR_MOCK_DATE"] = "20260910"

from services.radar_hub_service import (
    RadarHubService, SEED_CHANNELS, event_fingerprint,
    emotion_density, classify_crowd_emotion, bot_cluster_share,
    BOT_CLUSTER_SHARE_LINE, SLOT_MINUTES,
)
from repositories.radar_repository import (
    CATEGORY_POLITICS, CATEGORY_MILITARY, CATEGORY_FINANCE,
    CATEGORY_HISTORY, CATEGORY_CURRENT, LIFECYCLE_NEW,
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


# ============================================================
# 1. 种子频道池(5 断言)
# ============================================================

class TestChannels:
    async def run(self):
        reset_store()
        svc = RadarHubService()

        # 1) 12 频道惰性灌入
        channels = await svc.seed_channels()
        record("频道-12种子灌入",
               len(channels) == 12
               and len(SEED_CHANNELS) == 12,
               f"n={len(channels)}")

        # 2) 类别分布(政治 5/军事 4/财经 1/历史 1/时事 1)
        by_cat = {}
        for c in channels:
            by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
        record("频道-类别分布",
               by_cat.get("politics") == 5
               and by_cat.get("military") == 4
               and by_cat.get("finance") == 1
               and by_cat.get("history") == 1
               and by_cat.get("current") == 1,
               f"cat={by_cat}")

        # 3) 幂等(重复 seed 不重复灌入)
        await svc.seed_channels()
        channels2 = await svc.repo.list_channels(limit=100)
        record("频道-幂等", len(channels2) == 12,
               f"n={len(channels2)}")

        # 4) 增量入库 + 重复拒绝 + 非法类别
        new_c = await svc.register_channel(
            "douyin", "test_channel", "测试频道", "finance")
        ok_dup = ok_cat = False
        try:
            await svc.register_channel(
                "douyin", "test_channel", "重复", "finance")
        except ValueError:
            ok_dup = True
        try:
            await svc.register_channel(
                "douyin", "bad_cat", "非法", "crypto")
        except ValueError:
            ok_cat = True
        record("频道-增量与拒绝",
               new_c["channelId"] > 0 and ok_dup and ok_cat,
               f"id={new_c['channelId']}")

        # 5) 指定频道采集 404(不存在)
        try:
            await svc.collect_events(channel_id=99999)
            ok = False
        except KeyError:
            ok = True
        record("频道-404语义", ok)


# ============================================================
# 2. 事件流采集(4 断言)
# ============================================================

class TestCollect:
    async def run(self):
        reset_store()
        svc = RadarHubService()

        # 6) 批次采集(12 频道 → collected ≥ 12, created)
        r = await svc.collect_events()
        record("采集-批次产出",
               r["channels"] == 12 and r["collected"] >= 12
               and r["aggregated"] >= 12
               and all(e["action"] == "created"
                       for e in r["events"]),
               f"c={r['collected']} a={r['aggregated']}")

        # 7) 多模态字段落库(ASR/OCR/BGM 指纹)
        events = await svc.repo.list_events(limit=50)
        sample = events[0] if events else {}
        record("采集-多模态字段",
               bool(sample.get("asrTranscript"))
               and isinstance(sample.get("ocrTags"), list)
               and len(sample.get("bgmFingerprint") or "") == 16,
               f"e={bool(sample.get('asrTranscript'))}")

        # 8) 时政/军事事件在流中(种子语料预期)
        cats = {e.get("category") for e in events}
        record("采集-时政军事在流",
               "politics" in cats and "military" in cats,
               f"cats={cats}")

        # 9) 生命周期初值(new) + firstSeen
        record("采集-生命周期初值",
               all(e.get("lifecycle") == LIFECYCLE_NEW
                   for e in events)
               and all(e.get("firstSeenAt") for e in events),
               "lifecycle/firstSeen")


# ============================================================
# 3. 聚类去重(5 断言)
# ============================================================

class TestClustering:
    async def run(self):
        reset_store()
        svc = RadarHubService()
        await svc.collect_events()
        events_before = await svc.repo.list_events(limit=100)
        n_before = len(events_before)

        # 10) 同槽位重复采集 → 全幂等跳过
        r2 = await svc.collect_events()
        record("聚类-同槽幂等",
               r2["duplicates"] == r2["collected"]
               and r2["aggregated"] == 0,
               f"d={r2['duplicates']} c={r2['collected']}")

        # 11) 跨槽位推进 → 同指纹聚合(旧事件聚合不重建;
        #     新主题事件合法新增——"全网新事件出现"语义)
        os.environ["RADAR_MOCK_SLOT"] = "41"
        try:
            r3 = await svc.collect_events()
            events_after = await svc.repo.list_events(limit=100)
            n_after = len(events_after)
            aggregated_n = sum(
                1 for e in r3["events"]
                if e["action"] == "aggregated")
            created_n = sum(
                1 for e in r3["events"]
                if e["action"] == "created")
            record("聚类-跨槽聚合",
                   n_after >= n_before and aggregated_n > 0
                   and created_n == n_after - n_before,
                   f"before={n_before} after={n_after} "
                   f"agg={aggregated_n} new={created_n}")

            # 12) 聚合演化(热度 max + totalSlots 递增 + 槽位时序)
            evolved = [e for e in r3["events"]
                       if e["action"] == "aggregated"]
            if evolved:
                eid = evolved[0]["eventId"]
                detail = await svc.event_detail(eid)
                record("聚类-演化更新",
                       detail["totalSlots"] >= 2
                       and len(detail["slots"]) >= 2,
                       f"slots={detail['totalSlots']}")
            else:
                record("聚类-演化更新", False, "无聚合事件")

            # 13) 事件指纹确定性(同输入同指纹)
            fp1 = event_fingerprint(
                "douyin", "jinmeizhujui", "国际局势新动向")
            fp2 = event_fingerprint(
                "douyin", "jinmeizhujui", "国际局势 新动向!")
            record("聚类-指纹规范化",
                   fp1 == fp2 and len(fp1) == 64,
                   "主题去标点归一")
        finally:
            os.environ["RADAR_MOCK_SLOT"] = "40"

        # 14) 指定频道采集(单频道)
        channels = await svc.repo.list_channels(limit=20)
        r4 = await svc.collect_events(
            channel_id=channels[0]["channelId"])
        record("聚类-单频道采集",
               r4["channels"] == 1,
               f"ch={r4['channels']}")


# ============================================================
# 4. 情绪场域(5 断言)
# ============================================================

class TestEmotion:
    async def run(self):
        reset_store()

        # 15) 词表密度(负/正——词表内词)
        neg, pos = emotion_density(
            ["离谱", "愤怒", "点赞"])
        record("情绪-词表密度", neg == round(2 / 3, 4)
               and pos == round(1 / 3, 4),
               f"n={neg} p={pos}")

        # 16) 群体分类(阈值 0.3)
        record("情绪-群体分类",
               classify_crowd_emotion(0.5, 0.1) == "negative"
               and classify_crowd_emotion(0.1, 0.5)
               == "positive"
               and classify_crowd_emotion(0.2, 0.2)
               == "neutral")

        # 17) 刷量聚簇占比(两个 /16 前缀各半)
        samples = ([{"ip": f"10.88.1.{i}"} for i in range(4)]
                   + [{"ip": f"172.16.2.{i}"} for i in range(4)])
        share = bot_cluster_share(samples)
        record("情绪-聚簇占比", share == 0.5,
               f"s={share}")

        # 18) 刷量过滤线(>50% 降权——采集轨验证)
        svc = RadarHubService()
        await svc.collect_events()
        events = await svc.repo.list_events(limit=100)
        ok_flag = all(
            (not bool(e.get("botFiltered")))
            or (float(e.get("botShare") or 0)
                > BOT_CLUSTER_SHARE_LINE)
            for e in events)
        record("情绪-刷量过滤标记",
               ok_flag, "botFiltered 仅在聚簇超线时")

        # 19) 原文即用即弃(列表/详情均无弹幕原文)
        if events:
            eid = events[0]["eventId"]
            detail = await svc.event_detail(eid)
            blob = str(detail)
            record("情绪-原文即用即弃",
                   "danmakuSample" not in blob
                   and "danmaku" not in str(
                       await svc.list_events(limit=5)),
                   "原文不落查询面")
        else:
            record("情绪-原文即用即弃", False, "无事件")


# ============================================================
# 5. 查询面(4 断言)
# ============================================================

class TestQuery:
    async def run(self):
        reset_store()
        svc = RadarHubService()
        await svc.collect_events()

        # 20) 热度降序
        events = await svc.list_events(limit=50)
        heats = [e["heatBase"] for e in events]
        record("查询-热度降序",
               heats == sorted(heats, reverse=True),
               f"h={heats[:5]}")

        # 21) 类别过滤
        pol = await svc.list_events(category="politics",
                                     limit=50)
        record("查询-类别过滤",
               len(pol) > 0
               and all(e["category"] == "politics"
                       for e in pol),
               f"n={len(pol)}")

        # 22) 详情多模态+槽位
        if events:
            d = await svc.event_detail(events[0]["eventId"])
            record("查询-详情多模态",
                   "asrTranscript" in d and "slots" in d
                   and "ocrTags" in d,
                   f"keys={list(d.keys())[:6]}")
        else:
            record("查询-详情多模态", False, "无事件")

        # 23) 404
        try:
            await svc.event_detail(99999)
            ok = False
        except KeyError:
            ok = True
        record("查询-404语义", ok)

        # 24) 槽位粒度常量(15min)
        record("常量-15min槽位", SLOT_MINUTES == 15)


async def main():
    tests = [TestChannels(), TestCollect(), TestClustering(),
             TestEmotion(), TestQuery()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P7a 雷达2.0 感知与聚合专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
