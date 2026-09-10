"""40号 P7a·雷达2.0 感知与聚合服务(设计文档《40号 P7 规划方案》§3)

全域多模态感知层: 种子频道池(12 频道测试集) + 事件流 mock
(15min 槽位推进) + 事件聚类去重(指纹级) + 情绪场域建模
(词表密度×群体聚合×刷量过滤)

架构口径:
    - 事件流 mock: 种子 = radar|{channelId}|{date}|{15min槽位} →
      同槽位确定性(事件内容/热度/情绪样本一致——可测聚类去重);
      跨槽位推进(演示"事件发酵": 同主题事件跨槽位热度/情绪演化)
    - 多模态字段确定性: ASR 转写稿/OCR 标签/BGM 指纹 = mock
      生成器字段(真实管线为 radar_source_adapter 预留轨——
      P3b 限速+熔断范式, 未配置回退 mock, Mock-first 产出不中断)
    - 事件指纹: SHA256(平台+频道+主题规范化)——同主题跨槽位/
      跨平台聚合(P0 作品指纹范式升级为事件级)
    - 情绪场域(确定性, LLM 禁入): 单事件情绪=词表密度;
      群体情绪=弹幕/评论聚合; 刷量过滤=同 IP 前缀聚簇占比>50%
      的样本降权(P2a 聚簇范式)

红线(宪法域):
    - 情绪原文即用即弃: 仅聚合密度入库, 弹幕原文不落库
    - LLM 禁入: 聚类=指纹/情绪=词表/热度=基数
    - 采集合规预留: 真实轨仅公开数据+robots 遵守(mock 轨无此问题)
"""

import hashlib
import logging
import os
import random
from datetime import datetime, UTC, timedelta

from repositories.radar_repository import (
    RadarRepository, CATEGORIES, CATEGORY_POLITICS,
    CATEGORY_MILITARY, CATEGORY_FINANCE, CATEGORY_HISTORY,
    CATEGORY_CURRENT, CHANNEL_STATUS_ACTIVE, LIFECYCLE_NEW,
    EMOTION_POSITIVE, EMOTION_NEGATIVE, EMOTION_NEUTRAL,
)

logger = logging.getLogger(__name__)


# ============================================================
# P7a 常量(设计文档 §3)
# ============================================================

# 事件槽位粒度(15 分钟——"流式"的确定性实现)
SLOT_MINUTES = 15

# 槽位环境锚点(测试确定性——P6c mock 槽位范式复用)
# RADAR_MOCK_SLOT / RADAR_MOCK_DATE 可固定槽位/日期

# 刷量过滤线(同 IP 前缀聚簇占比 > 50% 降权——P2a 聚簇范式)
BOT_CLUSTER_SHARE_LINE = 0.5

# 情绪词表(确定性——P6a 词表的事件场景扩展)
EMOTION_POSITIVE_WORDS = (
    "暖心", "感动", "点赞", "支持", "期待", "值得", "真香", "力荐",
)
EMOTION_NEGATIVE_WORDS = (
    "愤怒", "离谱", "无语", "失望", "坑", "吐槽", "焦虑", "翻车",
    "避雷", "难受",
)

# ============================================================
# 12 种子频道(用户指定测试集——类别驱动预期合规分级)
# ============================================================

SEED_CHANNELS = (
    # (平台, 频道名, 显示名, 类别)
    ("douyin", "jinmeizhujui", "金梅煮酒", CATEGORY_POLITICS),
    ("douyin", "gaozhikai", "高志凯频道", CATEGORY_POLITICS),
    ("douyin", "chenhudianbing", "陈虎点兵", CATEGORY_MILITARY),
    ("douyin", "jincanrong", "金灿荣教授", CATEGORY_POLITICS),
    ("xiaohongshu", "zhenhaihui", "震海会", CATEGORY_CURRENT),
    ("douyin", "jinrijiangtan", "今日蒋谈", CATEGORY_POLITICS),
    ("douyin", "baomingshuo", "包明说", CATEGORY_MILITARY),
    ("douyin", "renhanjuncaifu", "任汉军财富故事会",
     CATEGORY_FINANCE),
    ("douyin", "baodequan", "保德全", CATEGORY_MILITARY),
    ("bilibili", "tingfengdecan", "听风的蚕", CATEGORY_MILITARY),
    ("douyin", "yanshujun", "闫树军", CATEGORY_HISTORY),
    ("douyin", "lijiannantaiwan", "黎建南台湾",
     CATEGORY_POLITICS),
)

# 事件主题池(按类别——mock 生成器确定性抽取;
# 主题与类别对齐种子频道语料预期)
_TOPIC_POOL = {
    CATEGORY_POLITICS: (
        ("国际局势新动向", "大国博弈进入新阶段"),
        ("外交发言人最新回应", "记者会要点全记录"),
        ("台海形势专家解读", "两岸关系走向分析"),
    ),
    CATEGORY_MILITARY: (
        ("新型装备列装部队", "演训画面首度公开"),
        ("军工科技新突破", "国产装备性能解析"),
        ("周边军情动态", "海上力量部署观察"),
    ),
    CATEGORY_FINANCE: (
        ("年轻人理财新趋势", "工资到手先存三成"),
        ("消费降级还是理性回归", "性价比时代来临"),
        ("普通人财富保值指南", "通胀时代的钱包守护"),
    ),
    CATEGORY_HISTORY: (
        ("古代酒文化溯源", "从祭祀到餐桌的千年演变"),
        ("历史名人的待客之道", "礼尚往来的传统智慧"),
        ("老字号的经营哲学", "百年商道沉浮录"),
    ),
    CATEGORY_CURRENT: (
        ("极端天气自救指南", "暴雨洪涝应对手册"),
        ("节假日出行新变化", "文旅消费观察"),
        ("社区互助新模式", "邻里守望在身边"),
    ),
}

# 热度放大倍数(类别×tier——时政军事高热, 财经/生活次之)
_TIER_AMP = {3: 2.6, 2: 1.8, 1: 1.1, 0: 0.7}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def event_fingerprint(platform: str, channel_name: str,
                     topic: str) -> str:
    """事件指纹: SHA256(平台+频道+主题规范化)

    主题规范化: 去空白与标点(P6e RT-03 攻击归一范式)——
    同主题跨槽位/跨平台聚合的确定性依据。
    """
    normalized = "".join(ch for ch in topic if ch.isalnum())
    raw = f"{platform}|{channel_name}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def emotion_density(samples: list) -> tuple:
    """情绪密度(确定性词表, LLM 禁入)

    Returns:
        (负向密度, 正向密度)——原文即用即弃, 仅密度返回
    """
    texts = [str(s or "") for s in (samples or []) if str(s or "")]
    if not texts:
        return 0.0, 0.0
    neg = sum(1 for t in texts
              if any(w in t for w in EMOTION_NEGATIVE_WORDS))
    pos = sum(1 for t in texts
              if any(w in t for w in EMOTION_POSITIVE_WORDS))
    return round(neg / len(texts), 4), round(pos / len(texts), 4)


def classify_crowd_emotion(neg_density: float,
                           pos_density: float) -> str:
    """群体情绪分类(确定性阈值)"""
    if neg_density > 0.3 and neg_density > pos_density:
        return EMOTION_NEGATIVE
    if pos_density > 0.3 and pos_density > neg_density:
        return EMOTION_POSITIVE
    return EMOTION_NEUTRAL


def bot_cluster_share(samples: list) -> float:
    """刷量聚簇占比(同 IP 前缀聚簇——P2a 范式)

    mock 轨: 弹幕样本含 ip 字段; 真实轨同口径。
    聚簇占比 = 最大同前缀组 / 总样本。
    """
    entries = [s for s in (samples or []) if isinstance(s, dict)]
    if not entries:
        return 0.0
    prefixes = {}
    for s in entries:
        ip = str(s.get("ip") or "")
        prefix = ".".join(ip.split(".")[:2]) if ip else "?"
        prefixes[prefix] = prefixes.get(prefix, 0) + 1
    return round(max(prefixes.values()) / len(entries), 4)


def _slot_key_now() -> tuple:
    """当前槽位键(UTC 15min 粒度)+ 日期键"""
    now = datetime.now(UTC)
    slot = int(os.environ.get("RADAR_MOCK_SLOT")
               if "RADAR_MOCK_SLOT" in os.environ
               else now.minute // SLOT_MINUTES
               + (now.hour * 60 // SLOT_MINUTES))
    date_key = os.environ.get(
        "RADAR_MOCK_DATE") or f"{now:%Y%m%d}"
    return slot, date_key


class RadarHubService:
    """40号 P7a·雷达2.0 感知与聚合(频道/事件流/聚类/情绪)"""

    def __init__(self, repo: RadarRepository = None):
        self.repo = repo if repo is not None else RadarRepository()

    # ============================================================
    # 1. 种子频道池
    # ============================================================

    async def seed_channels(self) -> list[dict]:
        """种子频道惰性灌入(幂等; 12 频道测试集)"""
        for platform, name, display, category in SEED_CHANNELS:
            existing = await self.repo.find_channel_by_name(name)
            if existing is not None:
                continue
            channel_id = await self.repo.next_id("channel")
            await self.repo.save_channel({
                "channelId": channel_id,
                "platform": platform,
                "name": name,
                "displayName": display,
                "category": category,
                "status": CHANNEL_STATUS_ACTIVE,
                "createdAt": _now_iso(),
            })
        return await self.repo.list_channels(
            status=CHANNEL_STATUS_ACTIVE)

    async def register_channel(self, platform: str, name: str,
                               display_name: str, category: str
                               ) -> dict:
        """频道入库(增量扩展)

        Raises:
            ValueError: 类别/平台非法 / 重复登记
        """
        if category not in CATEGORIES:
            raise ValueError(
                f"频道类别无效({category}, "
                f"须为{'/'.join(CATEGORIES)})")
        if not (name or "").strip() or not (display_name or "").strip():
            raise ValueError("频道名与显示名必填")
        if await self.repo.find_channel_by_name(name.strip()):
            raise ValueError(
                f"频道已登记(name={name.strip()})——勿重复")
        channel_id = await self.repo.next_id("channel")
        return await self.repo.save_channel({
            "channelId": channel_id,
            "platform": platform or "douyin",
            "name": name.strip(),
            "displayName": display_name.strip(),
            "category": category,
            "status": CHANNEL_STATUS_ACTIVE,
            "createdAt": _now_iso(),
        })

    # ============================================================
    # 2. 事件流采集(15min 槽位确定性 mock)
    # ============================================================

    def _mock_fetch(self, channel: dict, slot: int,
                    date_key: str) -> list[dict]:
        """确定性模拟事件批次: 每频道每槽位 1-2 条事件

        种子 = radar|{channelId}|{date}|{slot} → 同槽位确定性;
        跨槽位推进(同主题事件跨槽位热度演化——演示"发酵")。
        """
        rng = random.Random(
            f"radar|{channel['channelId']}|{date_key}|{slot}")
        topics = _TOPIC_POOL.get(channel.get("category"),
                                 _TOPIC_POOL[CATEGORY_CURRENT])
        n_events = 1 + (rng.random() > 0.5)   # 1-2 条
        idx_list = sorted(rng.sample(
            range(len(topics)), min(n_events, len(topics))))
        events = []
        for seq, idx in enumerate(idx_list):
            title, summary = topics[idx]
            tier = rng.choice((3, 2, 2, 1))
            amp = _TIER_AMP.get(tier, 1.5) * (0.85 + 0.3 *
                                              rng.random())
            # 热度基数: 类别基础(时政军事高)×放大
            base = {"politics": 900, "military": 850,
                    "finance": 400, "history": 250,
                    "current": 500}.get(
                channel.get("category"), 300)
            heat = int(base * amp)
            # 多模态确定性字段(mock 生成器——真实轨为适配器)
            ext = (f"ev{date_key}s{slot}"
                   f"c{channel['channelId']:03d}t{idx:02d}")
            asr = (f"本节目讨论{title}。{summary}。"
                   "更多内容请关注频道主页。")
            ocr_tags = [title[:4], channel.get("category", ""),
                        "字幕", "封面"]
            bgm_fp = hashlib.sha256(
                f"bgm|{ext}".encode()).hexdigest()[:16]
            # 弹幕样本(含 ip——聚簇判定; 真实轨同口径)
            danmaku = [
                {"ip": f"10.{rng.randint(1, 9)}.{seq % 3}.{i}",
                 "text": rng.choice((
                     "讲得好", "支持", "离谱", "有道理",
                     "期待下期", "无语了", "涨知识", "焦虑"))}
                for i in range(rng.randint(8, 14))]
            events.append({
                "extEventId": ext,
                "title": title,
                "summary": summary,
                "asrTranscript": asr,
                "ocrTags": ocr_tags,
                "bgmFingerprint": bgm_fp,
                "danmakuSample": danmaku,
                "heatBase": heat,
                "category": channel.get("category", ""),
                "publishedAt": _now_iso(),
            })
        return events

    async def collect_events(self,
                             channel_id: int = None) -> dict:
        """流式采集触发(当前槽位事件批次→聚类去重→情绪聚合)

        - channel_id 指定: 单频道采集
        - channel_id 空: 全量 active 频道批次采集

        Returns:
            {channels, collected, aggregated, duplicates,
             botFiltered, events: [...摘要]}
        """
        await self.seed_channels()
        channels = await self.repo.list_channels(
            status=CHANNEL_STATUS_ACTIVE, limit=1000)
        if channel_id is not None:
            channels = [c for c in channels
                        if c["channelId"] == channel_id]
            if not channels:
                raise KeyError(f"频道不存在或已停用"
                               f"(channelId={channel_id})")
        slot, date_key = _slot_key_now()
        slot_key = f"{date_key}s{slot:04d}"
        collected, aggregated, duplicates = 0, 0, 0
        bot_filtered = 0
        summaries = []
        for channel in channels:
            for item in self._mock_fetch(channel, slot, date_key):
                collected += 1
                fp = event_fingerprint(
                    channel.get("platform", ""),
                    channel.get("name", ""),
                    item["title"])
                # 幂等: 同指纹+同槽位已观测 → 跳过(重复采集)
                if await self.repo.find_slot(fp, slot_key):
                    duplicates += 1
                    continue
                # 刷量过滤(聚簇占比>50% → 情绪降权标记)
                cluster_share = bot_cluster_share(
                    item.get("danmakuSample"))
                bot_flag = cluster_share > BOT_CLUSTER_SHARE_LINE
                if bot_flag:
                    bot_filtered += 1
                # 情绪场域(原文即用即弃——仅密度入库)
                neg, pos = emotion_density(
                    item.get("danmakuSample"))
                crowd = classify_crowd_emotion(neg, pos)
                if bot_flag:
                    # 刷量降权: 情绪密度×0.3(水军不是真实场域)
                    neg, pos = round(neg * 0.3, 4), round(pos * 0.3, 4)
                    crowd = classify_crowd_emotion(neg, pos)
                # 事件聚类: 同指纹事件已有 → 聚合(槽位观测+热度演化)
                existing = await self.repo.find_event_by_fingerprint(
                    fp)
                if existing is not None:
                    await self._record_slot(
                        existing, slot_key, item, fp, neg, pos,
                        bot_flag)
                    aggregated += 1
                    summaries.append({
                        "eventId": existing["eventId"],
                        "title": item["title"],
                        "action": "aggregated",
                        "totalSlots": existing.get("totalSlots", 0)
                        + 1})
                else:
                    event = await self._create_event(
                        channel, slot_key, item, fp, neg, pos,
                        cluster_share, bot_flag)
                    aggregated += 1
                    summaries.append({
                        "eventId": event["eventId"],
                        "title": item["title"],
                        "action": "created",
                        "totalSlots": 1})
        return {"channels": len(channels),
                "collected": collected, "aggregated": aggregated,
                "duplicates": duplicates,
                "botFiltered": bot_filtered,
                "events": summaries}

    async def _create_event(self, channel: dict, slot_key: str,
                            item: dict, fp: str, neg: float,
                            pos: float, cluster_share: float,
                            bot_flag: bool) -> dict:
        """新事件入库(生命周期 new + 首槽位观测)"""
        event_id = await self.repo.next_id("event")
        event = {
            "eventId": event_id,
            "fingerprint": fp,
            "channelId": channel["channelId"],
            "channelName": channel.get("displayName", ""),
            "platform": channel.get("platform", ""),
            "title": item["title"],
            "summary": item["summary"],
            "asrTranscript": item.get("asrTranscript", ""),
            "ocrTags": item.get("ocrTags", []),
            "bgmFingerprint": item.get("bgmFingerprint", ""),
            "category": item.get("category",
                                 channel.get("category", "")),
            "heatBase": int(item.get("heatBase") or 0),
            "emotionDensity": round(neg + pos, 4),
            "crowdEmotion": classify_crowd_emotion(neg, pos),
            "botFiltered": bot_flag,
            "botShare": cluster_share,
            "lifecycle": LIFECYCLE_NEW,
            "totalSlots": 1,
            "firstSeenAt": _now_iso(),
            "lastSeenAt": _now_iso(),
        }
        await self.repo.save_event(event)
        await self._put_slot(event, slot_key, item, neg, pos)
        return event

    async def _record_slot(self, event: dict, slot_key: str,
                           item: dict, fp: str, neg: float,
                           pos: float, bot_flag: bool) -> None:
        """既有事件聚合: 槽位观测入库 + 热度/情绪滚动更新"""
        await self._put_slot(event, slot_key, item, neg, pos)
        heat = int(item.get("heatBase") or 0)
        # 演化更新: 热度取 max(单调不减), 情绪密度滑动更新
        updates = {
            "heatBase": max(int(event.get("heatBase") or 0), heat),
            "emotionDensity": round(neg + pos, 4),
            "crowdEmotion": classify_crowd_emotion(neg, pos),
            "totalSlots": int(event.get("totalSlots") or 0) + 1,
            "lastSeenAt": _now_iso(),
            "botFiltered": bool(event.get("botFiltered")
                                or bot_flag),
        }
        await self.repo.update_event(event["eventId"], updates)

    async def _put_slot(self, event: dict, slot_key: str,
                        item: dict, neg: float, pos: float) -> None:
        """槽位观测入库(指纹+槽位幂等)"""
        slot_id = await self.repo.next_id("slot")
        danmaku = item.get("danmakuSample") or []
        await self.repo.save_slot({
            "slotId": slot_id,
            "eventId": event["eventId"],
            "fingerprint": event["fingerprint"],
            "slotKey": slot_key,
            "heatValue": int(item.get("heatBase") or 0),
            "danmakuCount": len(danmaku),
            "commentCount": len(danmaku) * 3,
            "botClusterCount": sum(
                1 for d in danmaku
                if isinstance(d, dict)
                and str(d.get("ip") or "").startswith("10.1")),
            "emotionDensity": round(neg + pos, 4),
            "createdAt": _now_iso(),
        })

    # ============================================================
    # 3. 事件流查询(观测面)
    # ============================================================

    async def list_events(self, category: str = None,
                          lifecycle: str = None,
                          limit: int = 50) -> list[dict]:
        """事件流查询(热度降序; 弹幕原文不返回——即用即弃铁律)"""
        events = await self.repo.list_events(
            category=category, lifecycle=lifecycle, limit=limit)
        # 摘要化输出(多模态详情留 debug 面)
        return [{
            "eventId": e["eventId"],
            "title": e.get("title", ""),
            "channelName": e.get("channelName", ""),
            "platform": e.get("platform", ""),
            "category": e.get("category", ""),
            "heatBase": e.get("heatBase", 0),
            "crowdEmotion": e.get("crowdEmotion", ""),
            "emotionDensity": e.get("emotionDensity", 0),
            "botFiltered": bool(e.get("botFiltered")),
            "lifecycle": e.get("lifecycle", ""),
            "totalSlots": e.get("totalSlots", 0),
            "lastSeenAt": e.get("lastSeenAt", ""),
        } for e in events]

    async def event_detail(self, event_id: int) -> dict:
        """事件详情(多模态字段; 弹幕原文仍不返回)"""
        event = await self.repo.get_event(event_id)
        if event is None:
            raise KeyError(f"事件不存在(eventId={event_id})")
        event = dict(event)
        event.pop("danmakuSample", None)
        event["slots"] = await self.repo.list_slots(
            event_id=event_id, limit=50)
        return event
