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
# 发布平台(复用 attract 平台体系)
# ============================================================

PROMO_PLATFORM_DOUYIN = "douyin"
PROMO_PLATFORM_XHS = "xiaohongshu"
PROMO_PLATFORM_MOMENTS = "wechat_moments"
PROMO_PLATFORMS = (PROMO_PLATFORM_DOUYIN, PROMO_PLATFORM_XHS,
                   PROMO_PLATFORM_MOMENTS)

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
}

# Agent 模型档位与三级降级(设计文档 §3.4)
PROMO_LLM_MODEL = os.environ.get("LLM_MODEL_PROMO", "glm-5.3")
PROMO_LLM_FALLBACK_MODEL = "glm-4-flash"

# 发布通道模式(mock 模拟轨 / real 真实平台 API, P2)
PROMO_CHANNEL_MODE = os.environ.get("PROMO_CHANNEL_MODE", "mock")

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
            self.store["_promo_hotspot_seq"] = 0
            self.store["_promo_decision_seq"] = 0
            self.store["_promo_content_seq"] = 0
