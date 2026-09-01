"""36号·AI智能推广模块数据访问层(双模式: 内存 + Redis)

表清单:
    promo_hotspots:       热点事件库(多平台热榜 + 评分 + 风险标记)
    promo_decisions:      蹭点决策记录(三档路由 + 审计留痕)
    promo_contents:       Agent 生成内容库(一源多态 contentGroupId)
    promo_content_links:  内容 → attract 短码映射(归因复用 attract)
    promo_publish_queue:  发布队列(黄金时段调度)
    promo_dedupe:         热点指纹去重(TTL 语义由 service 层维护)
    promo_cooldowns:      同热点内容条数冷却
    promo_daily_caps:     单日发布上限计数(按日键)

设计对齐(AI智能推广模块36_设计文档 v1.0 §4):
    - 异常约定: KeyError → 404 / ValueError → 409(service 层抛)
    - Redis Key 前缀 promo:*, 锁前缀 lock:promo:*
"""

import json
from datetime import datetime, UTC

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k

# ============================================================
# 热点源平台(雷达扫描目标)
# ============================================================

HOTSPOT_PLATFORM_BAIDU = "baidu"            # 百度热搜
HOTSPOT_PLATFORM_DOUYIN = "douyin"          # 抖音热榜
HOTSPOT_PLATFORM_WEIBO = "weibo"            # 微博热搜
HOTSPOT_PLATFORM_ZHIHU = "zhihu"            # 知乎热榜
HOTSPOT_PLATFORM_XHS = "xiaohongshu"        # 小红书热点
HOTSPOT_PLATFORMS = (HOTSPOT_PLATFORM_BAIDU, HOTSPOT_PLATFORM_DOUYIN,
                     HOTSPOT_PLATFORM_WEIBO, HOTSPOT_PLATFORM_ZHIHU,
                     HOTSPOT_PLATFORM_XHS)

# 热点源凭证环境变量(未配置走确定性模拟源, Mock-first 同 P1-2/P1-3)
HOTSPOT_API_KEY_ENV = {
    HOTSPOT_PLATFORM_BAIDU: "HOTSPOT_BAIDU_API_KEY",
    HOTSPOT_PLATFORM_DOUYIN: "HOTSPOT_DOUYIN_API_KEY",
    HOTSPOT_PLATFORM_WEIBO: "HOTSPOT_WEIBO_API_KEY",
    HOTSPOT_PLATFORM_ZHIHU: "HOTSPOT_ZHIHU_API_KEY",
    HOTSPOT_PLATFORM_XHS: "HOTSPOT_XHS_API_KEY",
}

# 热点状态
HOTSPOT_STATUS_ACTIVE = "active"        # 待决策
HOTSPOT_STATUS_ENGAGED = "engaged"      # 已跟进(进入内容工厂)
HOTSPOT_STATUS_PASSED = "passed"        # 放弃(留痕)
HOTSPOT_STATUS_DISCARDED = "discarded"  # 风险否决(永不跟进)
HOTSPOT_STATUSES = (HOTSPOT_STATUS_ACTIVE, HOTSPOT_STATUS_ENGAGED,
                    HOTSPOT_STATUS_PASSED, HOTSPOT_STATUS_DISCARDED)

# 决策三档阈值(设计文档 §3.2)
DECISION_AUTO_ENGAGE_SCORE = 70   # ≥70 自动跟进
DECISION_MANUAL_QUEUE_SCORE = 50  # 50-70 人工确认队列

# 品牌相关性关键词(命中即加分, 与热点池主题池对齐)
BRAND_RELEVANCE_WORDS = (
    "酒", "白酒", "宴", "婚宴", "寿宴", "礼", "送礼", "节", "春节",
    "中秋", "国庆", "端午", "国潮", "文化", "非遗", "聚会", "庆",
    "团圆", "家宴", "年货",
)

# 风险一票否决词(政治/未成年人/医疗/重大灾害, 设计文档 §7.5)
RISK_BLOCK_WORDS = (
    "政治", "领导人", "两会", "选举", "抗议",
    "地震", "洪水", "台风", "火灾", "灾难", "灾害", "事故",
    "疫情", "死亡", "遇难",
    "未成年", "儿童", "小学生", "中学生", "青少年",
    "治病", "疗效", "药效", "医疗", "壮阳",
)

# ============================================================
# 发布平台(复用 attract 平台体系; P1 扩展微博/视频号)
# ============================================================

PROMO_PLATFORM_DOUYIN = "douyin"
PROMO_PLATFORM_XHS = "xiaohongshu"
PROMO_PLATFORM_MOMENTS = "wechat_moments"
PROMO_PLATFORM_WEIBO = "weibo"              # 微博(话题借势, P1)
PROMO_PLATFORM_CHANNELS = "wechat_channels"  # 视频号(熟龄信任, P1)
PROMO_PLATFORMS = (PROMO_PLATFORM_DOUYIN, PROMO_PLATFORM_XHS,
                   PROMO_PLATFORM_MOMENTS, PROMO_PLATFORM_WEIBO,
                   PROMO_PLATFORM_CHANNELS)

# 内容状态机(三审闸门 + 发布队列)
CONTENT_STATUS_PENDING = "pending"      # 待人工审核(一审二审已过)
CONTENT_STATUS_APPROVED = "approved"    # 人工通过
CONTENT_STATUS_REJECTED = "rejected"    # 任一审拒绝
CONTENT_STATUS_QUEUED = "queued"        # 已入发布队列
CONTENT_STATUS_PUBLISHED = "published"  # 已发布(模拟轨含回执)
CONTENT_STATUSES = (CONTENT_STATUS_PENDING, CONTENT_STATUS_APPROVED,
                    CONTENT_STATUS_REJECTED, CONTENT_STATUS_QUEUED,
                    CONTENT_STATUS_PUBLISHED)

# ============================================================
# 三审合规闸门(设计文档 §3.5)
# ============================================================

# 一审硬规则: 酒类广告法§23 专项(出现即拒)
DRINKING_ACTION_WORDS = (
    "干杯", "一饮而尽", "不醉不归", "开怀畅饮", "贪杯",
    "喝到", "一箱下肚", "拼酒", "灌酒",
)
# 一审硬规则: 权威背书红线(广告法§9)
AUTHORITY_BACKING_WORDS = (
    "国家机关推荐", "政府推荐", "官方推荐", "权威推荐",
    "领导推荐", "国家推荐", "部门推荐",
)
# 医疗功效暗示(广告法§23: 不得暗示消除紧张焦虑等)
EFFICACY_CLAIM_WORDS = (
    "消除紧张", "缓解焦虑", "解忧", "消愁", "助眠", "安神",
    "提高体力", "增加体力", "滋补", "保健",
)
# 复用 attract 合规口径: 极限词/警示语/年龄提示/通过线
from repositories.attract_repository import (  # noqa: E402
    BANNED_WORDS, REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
)
# AI 审(二审)通过线(对齐 ad 模块 AI 审核口径)
PROMO_COMPLIANCE_PASS_SCORE = 80
# 三审强制人工区间(二审 60-80 分 → HITL)
PROMO_HITL_FLOOR = 60

# ============================================================
# 发布调度(设计文档 §3.6)
# ============================================================

import os  # noqa: E402

# 单日全平台发布上限(防刷屏触发平台风控)
PROMO_DAILY_CAP = int(os.environ.get("PROMO_DAILY_CAP", "20"))
# 同热点内容条数冷却上限(48h 窗口内)
PROMO_HOTSPOT_COOLDOWN_LIMIT = 2
PROMO_COOLDOWN_HOURS = int(os.environ.get("PROMO_HOTSPOT_COOLDOWN_HOURS", "48"))
# 去重窗口(小时)
PROMO_DEDUPE_HOURS = 48

# 黄金时段窗口(平台 → [(起时, 止时)], 24h 制)
GOLDEN_WINDOWS = {
    PROMO_PLATFORM_DOUYIN: ((12, 13), (18, 22)),
    PROMO_PLATFORM_XHS: ((12, 14), (20, 23)),
    PROMO_PLATFORM_MOMENTS: ((19, 22),),
    PROMO_PLATFORM_WEIBO: ((12, 14), (21, 23)),      # P1: 微博(午休+晚间热榜峰)
    PROMO_PLATFORM_CHANNELS: ((18, 21),),            # P1: 视频号(晚饭后熟龄活跃)
}

# Agent 模型档位与三级降级(设计文档 §3.4)
PROMO_LLM_MODEL = os.environ.get("LLM_MODEL_PROMO", "glm-5.3")
PROMO_LLM_FALLBACK_MODEL = "glm-4-flash"

# 发布通道模式(mock 模拟轨 / real 真实平台 API, P2)
PROMO_CHANNEL_MODE = os.environ.get("PROMO_CHANNEL_MODE", "mock")

# 发布通道凭证环境变量(未配置该平台回退确定性 mock 回执)
PROMO_CHANNEL_API_KEY_ENV = {
    PROMO_PLATFORM_DOUYIN: "PROMO_CHANNEL_DOUYIN_KEY",
    PROMO_PLATFORM_XHS: "PROMO_CHANNEL_XHS_KEY",
    PROMO_PLATFORM_MOMENTS: "PROMO_CHANNEL_MOMENTS_KEY",
    PROMO_PLATFORM_WEIBO: "PROMO_CHANNEL_WEIBO_KEY",
    PROMO_PLATFORM_CHANNELS: "PROMO_CHANNEL_CHANNELS_KEY",
}

# P2: 百度普通收录推送(SITEMAP ping / urls 主动推送)
BAIDU_PUSH_SITE = os.environ.get("BAIDU_PUSH_SITE", "")
BAIDU_PUSH_TOKEN = os.environ.get("BAIDU_PUSH_TOKEN", "")
# 推送记录状态
SEO_PUSH_STATUS_OK = "ok"
SEO_PUSH_STATUS_FAILED = "failed"

# ============================================================
# P1: 受众画像库(设计文档 §3.3, admin 可配)
# ============================================================

# 默认平台画像种子(字段: audience 受众/tone 基调/format 格式约束/
# scenes 擅长场景/productTones 亲和产品调性)
DEFAULT_AUDIENCE_PROFILES = {
    PROMO_PLATFORM_DOUYIN: {
        "platform": PROMO_PLATFORM_DOUYIN,
        "audience": "18-35 大众娱乐人群",
        "tone": "快节奏、剧情钩子、口语化",
        "format": "15-45s 短视频脚本(钩子-卖点-行动)",
        "scenes": ("日常小酌", "朋友聚会", "国潮打卡"),
        "productTones": ("口粮酒", "年轻化", "高性价比"),
    },
    PROMO_PLATFORM_XHS: {
        "platform": PROMO_PLATFORM_XHS,
        "audience": "20-40 女性种草人群",
        "tone": "真实体验、生活美学、闺蜜分享",
        "format": "标题≤20字 + 正文≤800字 + 标签",
        "scenes": ("婚宴", "寿宴", "送礼", "家宴布置"),
        "productTones": ("礼盒", "颜值款", "轻奢"),
    },
    PROMO_PLATFORM_MOMENTS: {
        "platform": PROMO_PLATFORM_MOMENTS,
        "audience": "30+ 熟龄社交圈",
        "tone": "信任、情怀、简短",
        "format": "朋友圈文案(≤140字)",
        "scenes": ("节庆送礼", "商务宴请", "老友重聚"),
        "productTones": ("高端礼盒", "陈酿", "收藏款"),
    },
    PROMO_PLATFORM_WEIBO: {
        "platform": PROMO_PLATFORM_WEIBO,
        "audience": "18-40 话题互动人群",
        "tone": "热点话题借势、互动感强、会玩梗",
        "format": "#话题# + 正文≤140字 + 互动引导",
        "scenes": ("节庆话题", "热点讨论", "抽奖转发"),
        "productTones": ("年轻化", "高性价比", "颜值款"),
    },
    PROMO_PLATFORM_CHANNELS: {
        "platform": PROMO_PLATFORM_CHANNELS,
        "audience": "30+ 熟龄信任消费人群",
        "tone": "信任、情怀、真实克制",
        "format": "图文短句 + 封面文案(公众号生态)",
        "scenes": ("节庆送礼", "家宴", "商务宴请"),
        "productTones": ("高端礼盒", "陈酿", "礼盒"),
    },
}

# 内容角度 → 平台亲和度(三维匹配第一维; 0-1)
ANGLE_PLATFORM_AFFINITY = {
    PROMO_PLATFORM_DOUYIN: {
        "场景": 0.9, "日常": 0.9, "聚会": 0.9, "小酌": 0.85,
        "婚宴": 0.4, "送礼": 0.4, "工艺": 0.5, "文化": 0.6, "优惠": 0.8,
    },
    PROMO_PLATFORM_XHS: {
        "婚宴": 0.95, "送礼": 0.9, "家宴": 0.9, "场景": 0.85,
        "文化": 0.7, "颜值": 0.9, "日常": 0.5, "工艺": 0.6, "优惠": 0.5,
    },
    PROMO_PLATFORM_MOMENTS: {
        "节庆": 0.9, "送礼": 0.85, "商务": 0.85, "文化": 0.7,
        "婚宴": 0.6, "场景": 0.6, "日常": 0.5, "优惠": 0.6, "工艺": 0.5,
    },
    PROMO_PLATFORM_WEIBO: {
        "热点": 0.95, "话题": 0.95, "节庆": 0.85, "日常": 0.8,
        "文化": 0.7, "优惠": 0.75, "聚会": 0.7, "场景": 0.6,
        "婚宴": 0.4, "送礼": 0.5, "工艺": 0.4,
    },
    PROMO_PLATFORM_CHANNELS: {
        "节庆": 0.9, "送礼": 0.9, "家宴": 0.9, "商务": 0.85,
        "文化": 0.75, "婚宴": 0.65, "场景": 0.6, "优惠": 0.55,
        "日常": 0.5, "工艺": 0.55,
    },
}

# 产品调性(三维匹配第二维; 与画像 productTones 匹配)
PRODUCT_TONES = ("口粮酒", "礼盒", "高端礼盒", "陈酿", "年轻化",
                 "高性价比", "颜值款", "轻奢", "收藏款")

# 三维匹配通过线(≥该分值判定为"适合投放")
MATCH_PASS_SCORE = 0.6

# ============================================================
# P1: 权威信源库(设计文档 §3.4 权威导向, 仅可公开引用条目)
# ============================================================

# 信源类别(白名单)
AUTHORITY_CATEGORY_STANDARD = "standard"      # 国家标准
AUTHORITY_CATEGORY_ASSOCIATION = "association"  # 行业协会公开数据
AUTHORITY_CATEGORY_MEDIA = "media"            # 权威媒体公开报道
AUTHORITY_CATEGORIES = (AUTHORITY_CATEGORY_STANDARD,
                        AUTHORITY_CATEGORY_ASSOCIATION,
                        AUTHORITY_CATEGORY_MEDIA)

# 权威信源种子: 只收录可公开引用条目, content 为可引用客观事实
# (含标准编号/条款要点), allowedUsage 限定引用方式(广告法§9 红线)
AUTHORITY_SOURCE_SEEDS = (
    {
        "title": "GB/T 10781.1—2021《白酒质量要求 第1部分:浓香型白酒》",
        "category": AUTHORITY_CATEGORY_STANDARD,
        "content": ("国家标准 GB/T 10781.1—2021 规定了浓香型白酒的"
                    "术语和定义、产品分类、要求、检验方法等, "
                    "适用于浓香型白酒的生产、检验与销售。"),
        "allowedUsage": "仅可作客观事实引用(如\"符合 GB/T 10781.1 标准\"), 不得用于推荐背书",
    },
    {
        "title": "GB/T 15109—2021《白酒工业术语》",
        "category": AUTHORITY_CATEGORY_STANDARD,
        "content": ("国家标准 GB/T 15109—2021 界定了白酒工业的基本术语,"
                    "明确白酒以粮谷为主要原料, 以大曲、小曲或麸曲等为糖化"
                    "发酵剂, 经蒸煮、糖化、发酵、蒸馏、陈酿、勾调而成。"),
        "allowedUsage": "仅可作工艺客观事实引用, 不得用于推荐背书",
    },
    {
        "title": "GB 2757《食品安全国家标准 蒸馏酒及其配制酒》",
        "category": AUTHORITY_CATEGORY_STANDARD,
        "content": ("强制性食品安全国家标准 GB 2757 规定了蒸馏酒及其配制酒的"
                    "原料、感官、污染物限量、食品添加剂等食品安全要求, "
                    "所有在售蒸馏酒须符合该标准。"),
        "allowedUsage": "仅可作食品安全合规事实引用, 不得暗示官方推荐",
    },
    {
        "title": "中国酒业协会公开行业数据",
        "category": AUTHORITY_CATEGORY_ASSOCIATION,
        "content": ("据中国酒业协会公开发布的行业数据, 白酒产业向优势产区、"
                    "优势品牌集中, 消费结构持续升级, 具体数据以协会官方"
                    "最新发布为准。"),
        "allowedUsage": "仅可作趋势性客观表述, 引用具体数据须以协会官方最新发布为准",
    },
)

# RAG 检索参数
AUTHORITY_TOP_K = 3                 # 生成时注入引用池条数
AUTHORITY_MIN_SIMILARITY = 0.05     # 2-gram 余弦召回下限(信源池小, 阈值低)
# 溯源白名单短语(强制警示/年龄提示中的数字不作数字声明处理)
PROVENANCE_WHITELIST_PHRASES = (
    "18周岁以下请勿饮酒", "满18周岁请适量", "18+ 请适量饮用",
    "过量饮酒有害健康", "未成年人禁止饮酒",
)

# 决策档位
DECISION_AUTO_ENGAGE = "auto_engage"
DECISION_MANUAL_QUEUE = "manual_queue"
DECISION_PASS = "pass"
DECISIONS = (DECISION_AUTO_ENGAGE, DECISION_MANUAL_QUEUE, DECISION_PASS)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def hotspot_fingerprint(platform: str, title: str) -> str:
    """热点指纹(规范化标题后哈希, 去重依据)

    规范化: 去空白/全角空格/首尾标点, 统一小写。
    """
    import hashlib
    normalized = "".join((title or "").split()).strip(" 　，。！？!?,.#【】[]")
    raw = f"{platform}:{normalized.lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class PromoRepository:
    """36号·AI智能推广模块数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # ID 生成
    # ============================================================

    async def next_id(self, entity: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("promo", entity, "seq"))
        self._ensure_store()
        seq_key = f"_promo_{entity}_seq"
        self.store[seq_key] = self.store.get(seq_key, 0) + 1
        return self.store[seq_key]

    # ============================================================
    # 热点库
    # ============================================================

    async def save_hotspot(self, hotspot: dict) -> dict:
        return await self._save("promo_hotspots", hotspot["hotspotId"], hotspot)

    async def get_hotspot(self, hotspot_id: int) -> dict | None:
        return await self._get("promo_hotspots", hotspot_id)

    async def find_hotspot_by_fingerprint(self, fingerprint: str) -> dict | None:
        hotspots = await self._list("promo_hotspots", 2000)
        for h in hotspots:
            if h.get("fingerprint") == fingerprint:
                return h
        return None

    async def list_hotspots(self, status: str = None, platform: str = None,
                            limit: int = 200) -> list[dict]:
        hotspots = await self._list("promo_hotspots", limit * 10)
        if status:
            hotspots = [h for h in hotspots if h.get("status") == status]
        if platform:
            hotspots = [h for h in hotspots if h.get("platform") == platform]
        return sorted(hotspots, key=lambda h: (
            -float(h.get("score", 0)), h.get("createdAt", "")))[:limit]

    # ============================================================
    # 决策记录
    # ============================================================

    async def save_decision(self, decision: dict) -> dict:
        return await self._save("promo_decisions",
                                decision["decisionId"], decision)

    async def get_decision(self, decision_id: int) -> dict | None:
        return await self._get("promo_decisions", decision_id)

    async def find_decision_by_hotspot(self, hotspot_id: int) -> dict | None:
        decisions = await self._list("promo_decisions", 2000)
        for d in decisions:
            if d.get("hotspotId") == hotspot_id:
                return d
        return None

    async def list_decisions(self, decision: str = None,
                             limit: int = 200) -> list[dict]:
        decisions = await self._list("promo_decisions", limit * 10)
        if decision:
            decisions = [d for d in decisions if d.get("decision") == decision]
        return sorted(decisions, key=lambda d: d.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 内容库(Agent 产出)
    # ============================================================

    async def save_content(self, content: dict) -> dict:
        return await self._save("promo_contents", content["contentId"], content)

    async def get_content(self, content_id: int) -> dict | None:
        return await self._get("promo_contents", content_id)

    async def list_contents(self, platform: str = None, status: str = None,
                            hotspot_id: int = None, group_id: int = None,
                            limit: int = 200) -> list[dict]:
        contents = await self._list("promo_contents", limit * 10)
        if platform:
            contents = [c for c in contents if c.get("platform") == platform]
        if status:
            contents = [c for c in contents if c.get("status") == status]
        if hotspot_id is not None:
            contents = [c for c in contents if c.get("hotspotId") == hotspot_id]
        if group_id is not None:
            contents = [c for c in contents if c.get("contentGroupId") == group_id]
        return sorted(contents, key=lambda c: c.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 内容 → attract 短码映射(归因复用)
    # ============================================================

    async def save_content_link(self, link: dict) -> dict:
        return await self._save("promo_content_links",
                                link["contentId"], link)

    async def get_content_link(self, content_id: int) -> dict | None:
        return await self._get("promo_content_links", content_id)

    async def list_content_links(self, limit: int = 500) -> list[dict]:
        links = await self._list("promo_content_links", limit)
        return sorted(links, key=lambda l: l.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 发布队列(ZSet 语义: score = scheduledAt 时间戳)
    # ============================================================

    async def enqueue_publish(self, content_id: int, scheduled_at: str,
                              platform: str) -> None:
        record = {
            "contentId": content_id,
            "scheduledAt": scheduled_at,
            "platform": platform,
            "enqueuedAt": _now_iso(),
        }
        if is_redis_mode():
            client = await get_redis_client()
            ts = datetime.fromisoformat(scheduled_at).timestamp()
            await client.zadd(_k("promo", "publish", "queue"),
                              {json.dumps(record, ensure_ascii=False): ts})
        else:
            self._ensure_store()
            self.store["promo_publish_queue"][content_id] = record

    async def dequeue_publish(self, content_id: int) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            members = await client.zrange(
                _k("promo", "publish", "queue"), 0, -1)
            for member in members:
                record = json.loads(member)
                if record.get("contentId") == content_id:
                    await client.zrem(_k("promo", "publish", "queue"), member)
                    return
        else:
            self._ensure_store()
            self.store["promo_publish_queue"].pop(content_id, None)

    async def list_publish_queue(self, limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            members = await client.zrange(
                _k("promo", "publish", "queue"), 0, limit - 1)
            return [json.loads(m) for m in members]
        self._ensure_store()
        records = list(self.store["promo_publish_queue"].values())
        return sorted(records, key=lambda r: r.get("scheduledAt", ""))[:limit]

    # ============================================================
    # 去重 / 冷却 / 单日上限
    # ============================================================

    async def check_and_mark_fingerprint(self, fingerprint: str) -> bool:
        """热点指纹去重: 首见返回 True 并标记, 重复返回 False"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("promo", "dedupe", fingerprint)
            marked = await client.set(key, "1", nx=True,
                                      ex=PROMO_DEDUPE_HOURS * 3600)
            return bool(marked)
        self._ensure_store()
        if fingerprint in self.store["promo_dedupe"]:
            return False
        self.store["promo_dedupe"][fingerprint] = _now_iso()
        return True

    async def incr_cooldown(self, fingerprint: str) -> int:
        """同热点内容条数 +1, 返回当前计数"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("promo", "cooldown", fingerprint)
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, PROMO_COOLDOWN_HOURS * 3600)
            return int(count)
        self._ensure_store()
        count = self.store["promo_cooldowns"].get(fingerprint, 0) + 1
        self.store["promo_cooldowns"][fingerprint] = count
        return count

    async def get_cooldown(self, fingerprint: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            count = await client.get(_k("promo", "cooldown", fingerprint))
            return int(count) if count else 0
        self._ensure_store()
        return self.store["promo_cooldowns"].get(fingerprint, 0)

    async def get_daily_published(self, date_key: str) -> int:
        """读取当日已发布数(计数在发布时 incr)"""
        if is_redis_mode():
            client = await get_redis_client()
            count = await client.get(_k("promo", "daily", date_key))
            return int(count) if count else 0
        self._ensure_store()
        return self.store["promo_daily_caps"].get(date_key, 0)

    async def incr_daily_published(self, date_key: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("promo", "daily", date_key)
            count = await client.incr(key)
            await client.expire(key, 172800)   # 48h 过期, 过了当日不再膨胀
            return int(count)
        self._ensure_store()
        count = self.store["promo_daily_caps"].get(date_key, 0) + 1
        self.store["promo_daily_caps"][date_key] = count
        return count

    # ============================================================
    # P1: 受众画像库
    # ============================================================

    async def save_audience_profile(self, profile: dict) -> dict:
        return await self._save("promo_audience_profiles",
                                profile["platform"], profile)

    async def get_audience_profile(self, platform: str) -> dict | None:
        return await self._get("promo_audience_profiles", platform)

    async def list_audience_profiles(self, limit: int = 50) -> list[dict]:
        profiles = await self._list("promo_audience_profiles", limit)
        return sorted(profiles, key=lambda p: p.get("platform", ""))

    # ============================================================
    # P1: 权威信源库
    # ============================================================

    async def save_authority_source(self, source: dict) -> dict:
        return await self._save("promo_authority_sources",
                                source["sourceId"], source)

    async def get_authority_source(self, source_id: int) -> dict | None:
        return await self._get("promo_authority_sources", source_id)

    async def list_authority_sources(self, keyword: str = None,
                                     limit: int = 200) -> list[dict]:
        sources = await self._list("promo_authority_sources", limit)
        if keyword:
            kw = keyword.strip()
            sources = [s for s in sources
                       if kw in (s.get("title", "") + s.get("content", ""))]
        return sorted(sources, key=lambda s: s.get("sourceId", 0))

    # ============================================================
    # P2: 百度 SEO 推送记录
    # ============================================================

    async def save_seo_push(self, record: dict) -> dict:
        return await self._save("promo_seo_pushes",
                                record["pushId"], record)

    async def list_seo_pushes(self, status: str = None,
                              limit: int = 200) -> list[dict]:
        pushes = await self._list("promo_seo_pushes", limit * 5)
        if status:
            pushes = [p for p in pushes if p.get("status") == status]
        return sorted(pushes, key=lambda p: p.get("createdAt", ""),
                      reverse=True)[:limit]

    async def pushed_urls_today(self, date_key: str) -> set[str]:
        """当日已推送 URL 集合(推送幂等: 同 URL 当日不重推)"""
        pushes = await self._list("promo_seo_pushes", 1000)
        urls = set()
        for push in pushes:
            if push.get("dateKey") == date_key:
                urls.update(push.get("urls") or [])
        return urls

    # ============================================================
    # 通用存储(内存/Redis)
    # ============================================================

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            key_id = record_id if isinstance(record_id, str) else str(record_id)
            await client.set(_k("promo", table, key_id),
                             json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            key_id = record_id if isinstance(record_id, str) else str(record_id)
            data = await client.get(_k("promo", table, key_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("promo", table, "*"))
            records = []
            for key in keys:
                data = await client.get(key)
                if data:
                    records.append(json.loads(data))
            return records[:limit]
        self._ensure_store()
        return list(self.store[table].values())[:limit]

    # ============================================================
    # 内存模式初始化
    # ============================================================

    def _ensure_store(self) -> None:
        if "promo_hotspots" not in self.store:
            self.store["promo_hotspots"] = {}
            self.store["promo_decisions"] = {}
            self.store["promo_contents"] = {}
            self.store["promo_content_links"] = {}
            self.store["promo_publish_queue"] = {}
            self.store["promo_dedupe"] = {}
            self.store["promo_cooldowns"] = {}
            self.store["promo_daily_caps"] = {}
            self.store["promo_audience_profiles"] = {}     # P1: 受众画像库
            self.store["promo_authority_sources"] = {}     # P1: 权威信源库
            self.store["promo_seo_pushes"] = {}            # P2: SEO推送记录
            self.store["_promo_hotspot_seq"] = 0
            self.store["_promo_decision_seq"] = 0
            self.store["_promo_content_seq"] = 0
            self.store["_promo_authority_seq"] = 0
            self.store["_promo_seo_push_seq"] = 0
