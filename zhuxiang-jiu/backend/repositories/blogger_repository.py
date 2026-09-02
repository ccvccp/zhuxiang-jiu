"""40号·平台流量DV博主模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 blogger, 设计文档 §3):
    blogger_pool      博主池(平台/账号/粉丝量级/领域标签/状态/权重/游标)
    blogger_works     侦测作品(元数据/评分快照/决策/状态)
    blogger_follows   跟随内容(三段式文案/原作品快照/出处声明/短码/回执)
    blogger_audits    决策与发布流水(对齐 36号留痕口径)

设计对齐:
    - 双模式存储 + None/bool 序列化口径(38号实机修复惯例)
    - Mock-first: 8 位种子博主(设计文档 §2.1)
    - 作品指纹去重在服务层(SHA256 内存/Redis Set)
"""

import json
import os

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 博主池常量(设计文档 §2.1)
# ============================================================

BLOGGER_STATUS_ACTIVE = "active"
BLOGGER_STATUS_PAUSED = "paused"
BLOGGER_STATUSES = (BLOGGER_STATUS_ACTIVE, BLOGGER_STATUS_PAUSED)

# 平台(对齐 36号五平台)
PLATFORM_DOUYIN = "douyin"
PLATFORM_XHS = "xiaohongshu"
PLATFORM_WEIBO = "weibo"
PLATFORM_CHANNELS = "wechat_channels"
PLATFORMS = (PLATFORM_DOUYIN, PLATFORM_XHS, PLATFORM_WEIBO,
             PLATFORM_CHANNELS)

# 领域标签(入池门槛: 须与酒/美食/礼品/生活相关)
DOMAIN_WINE = "wine"          # 酒类
DOMAIN_FOOD = "food"          # 美食
DOMAIN_GIFT = "gift"          # 礼品
DOMAIN_LIFESTYLE = "lifestyle"  # 生活方式
DOMAINS = (DOMAIN_WINE, DOMAIN_FOOD, DOMAIN_GIFT, DOMAIN_LIFESTYLE)

# 风险一票否决词(复用 36号 RISK_BLOCK_WORDS 口径)
RISK_BLOCK_WORDS = ("政治", "未成年", "医疗事故", "灾害", "地震",
                    "洪水", "疫情")

# 粉丝量级分档(万) → 博主权重基准
FAN_TIER_MILLION = 100.0     # 百万级 → 权重 1.0
FAN_TIER_HUNDRED_K = 50.0    # 五十万+ → 0.8
FAN_TIER_TEN_K = 5.0         # 五万+ → 0.6

# ============================================================
# 作品状态机(侦测→决策→跟随→发布)
# ============================================================

WORK_STATUS_DETECTED = "detected"      # 已侦测待决策
WORK_STATUS_AUTO_FOLLOW = "auto_follow"  # AI 决策全自动跟随
WORK_STATUS_MANUAL_QUEUE = "manual_queue"  # 人工确认队列
WORK_STATUS_PASSED = "passed"          # 跳过留痕
WORK_STATUS_DISCARDED = "discarded"    # 风险一票否决
WORK_STATUS_FOLLOWING = "following"    # 跟随内容制作中
WORK_STATUSES = (WORK_STATUS_DETECTED,
                 WORK_STATUS_AUTO_FOLLOW, WORK_STATUS_MANUAL_QUEUE,
                 WORK_STATUS_PASSED, WORK_STATUS_DISCARDED,
                 WORK_STATUS_FOLLOWING)

# 决策三档阈值(沿用 36号阈值范式)
DECIDE_AUTO_SCORE = 70.0     # ≥70 全自动跟随
DECIDE_MANUAL_SCORE = 50.0   # 50-70 人工确认

# ============================================================
# 跟随内容状态机(三审 + 发布, 对齐 36号内容状态)
# ============================================================

FOLLOW_STATUS_PENDING = "pending"      # 待人工审核(三审)
FOLLOW_STATUS_APPROVED = "approved"    # 人工通过
FOLLOW_STATUS_REJECTED = "rejected"    # 任一审拒绝
FOLLOW_STATUS_QUEUED = "queued"        # 已入发布队列
FOLLOW_STATUS_PUBLISHED = "published"  # 已发布
FOLLOW_STATUSES = (FOLLOW_STATUS_PENDING, FOLLOW_STATUS_APPROVED,
                   FOLLOW_STATUS_REJECTED, FOLLOW_STATUS_QUEUED,
                   FOLLOW_STATUS_PUBLISHED)

# 合规三审分数线(沿用 36号口径)
COMPLIANCE_PASS_SCORE = 80
COMPLIANCE_HITL_FLOOR = 60

# 搬运检测红线: 与原作文案 n-gram 重合度上限
PLAGIARISM_OVERLAP_LIMIT = 0.40

# 发布调度三限(设计文档 §2.5, 参数重定义; 环境变量可覆盖便于测试)
BLOGGER_DAILY_CAP = int(os.environ.get("BLOGGER_DAILY_CAP", "10"))
BLOGGER_FOLLOW_COOLDOWN_HOURS = int(
    os.environ.get("BLOGGER_FOLLOW_COOLDOWN_HOURS", "24"))
FOLLOW_GAP_HOURS = int(os.environ.get("BLOGGER_FOLLOW_GAP_HOURS", "4"))

# 单博主连续零引流止损线(P1 学习回流用, 常量先行)
AUTO_PAUSE_STREAK = 3

# ============================================================
# P1 博主权重自进化常量(设计文档 §2.6)
# ============================================================

# weightAdjust 边界(层2进化偏移量, 派生 weight = clamp(base+adjust))
WEIGHT_ADJUST_MAX = 0.3
WEIGHT_ADJUST_MIN = -0.3
# weight 派生值边界
WEIGHT_FLOOR = 0.1
WEIGHT_CEIL = 1.0
# 进化步长(强引流+0.05 / 有效引流+0.02 / 零引流-0.05 / 止损再罚-0.05)
WEIGHT_STEP_GMV = 0.05
WEIGHT_STEP_CLICK = 0.02
WEIGHT_STEP_ZERO = -0.05
# 反馈沉淀窗口(小时): publishedAt ≤ now-N 才批量回流(短内容流量80%
# 集中在24h内, 与同博主24h冷却周期同构)
FEEDBACK_SETTLE_HOURS = int(
    os.environ.get("BLOGGER_FEEDBACK_SETTLE_HOURS", "24"))

# 层2进化字段(float, 序列化口径)
_INT_FIELDS = ("bloggerId", "workId", "followId", "auditId",
               "fansWan", "likes", "comments", "shares",
               "durationSeconds", "publishedAtTs",
               "zeroTrafficStreak", "trafficInfluencerId")
_FLOAT_FIELDS = ("weight", "engagementRate", "score",
                 "overlapRatio", "weightBase", "weightAdjust")


def _now_iso() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()


def fan_tier_weight(fans_wan: float) -> float:
    """粉丝量级 → 博主权重基准(百万级 1.0 / 五十万+ 0.8 / 五万+ 0.6)"""
    fans = float(fans_wan or 0)
    if fans >= FAN_TIER_MILLION:
        return 1.0
    if fans >= FAN_TIER_HUNDRED_K:
        return 0.8
    if fans >= FAN_TIER_TEN_K:
        return 0.6
    return 0.3


# ============================================================
# 8 位种子博主(设计文档 §2.1, 档位覆盖各平台/领域/量级)
# ============================================================

def _build_seed_bloggers() -> dict[int, dict]:
    seeds = [
        # (平台, 账号, 昵称, 粉丝万, 领域, 互动率)
        (PLATFORM_DOUYIN, "dy_lilaoshi", "老李品酒", 320.0,
         DOMAIN_WINE, 0.052),
        (PLATFORM_DOUYIN, "dy_chihaoduo", "吃好喝好研究所", 88.0,
         DOMAIN_FOOD, 0.041),
        (PLATFORM_XHS, "xhs_jiushijie", "酒小姐的微醺笔记", 56.0,
         DOMAIN_WINE, 0.063),
        (PLATFORM_XHS, "xhs_lifang", "礼尚往来的方方", 22.0,
         DOMAIN_GIFT, 0.048),
        (PLATFORM_WEIBO, "wb_baijiu_pingce", "白酒测评大叔", 130.0,
         DOMAIN_WINE, 0.028),
        (PLATFORM_WEIBO, "wb_shenghuomei", "生活美学家老梅", 45.0,
         DOMAIN_LIFESTYLE, 0.035),
        (PLATFORM_CHANNELS, "wx_yanxu", "宴席说·严选", 12.0,
         DOMAIN_GIFT, 0.057),
        (PLATFORM_CHANNELS, "wx_zhuxiang", "竹香品鉴官", 6.0,
         DOMAIN_WINE, 0.071),
    ]
    pool = {}
    for i, (platform, account, nick, fans, domain, engage) in \
            enumerate(seeds, start=1):
        weight_base = fan_tier_weight(fans)
        pool[i] = {
            "bloggerId": i,
            "platform": platform,
            "account": account,
            "nickname": nick,
            "fansWan": fans,
            "domain": domain,
            "engagementRate": engage,
            "status": BLOGGER_STATUS_ACTIVE,
            # P1 层2自进化: weight = clamp(weightBase+weightAdjust)
            "weightBase": weight_base,
            "weightAdjust": 0.0,
            "weight": weight_base,
            "pausedReason": "",
            "lastSeenWorkAt": "",
            "zeroTrafficStreak": 0,
            "trafficInfluencerId": 0,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
    return pool


def derived_weight(weight_base: float, weight_adjust: float) -> float:
    """P1 层2派生权重: clamp(base+adjust, 0.1, 1.0)"""
    return round(max(WEIGHT_FLOOR,
                     min(WEIGHT_CEIL,
                         float(weight_base or 0)
                         + float(weight_adjust or 0))), 4)


def normalize_blogger(record: dict) -> dict:
    """P1 字段向后兼容: 旧记录惰性补进化字段缺省值

    weightBase 缺省按当前 weight 回填(旧口径 weight 即静态基线)。
    """
    base = record.get("weightBase")
    if base is None or base == "":
        record["weightBase"] = float(record.get("weight") or 0)
    if record.get("weightAdjust") in (None, ""):
        record["weightAdjust"] = 0.0
    if record.get("pausedReason") is None:
        record["pausedReason"] = ""
    if record.get("zeroTrafficStreak") is None:
        record["zeroTrafficStreak"] = 0
    if record.get("trafficInfluencerId") in (None, ""):
        record["trafficInfluencerId"] = 0
    return record


class BloggerRepository:
    """40号·平台流量DV博主模块数据访问层"""

    TABLE_POOL = "blogger_pool"
    TABLE_WORKS = "blogger_works"
    TABLE_FOLLOWS = "blogger_follows"
    TABLE_AUDITS = "blogger_audits"

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号 / 序列化(口径对齐 38/39号)
    # ============================================================

    async def next_id(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("blogger", kind, "seq"))
        self._ensure_store()
        seq_key = f"_blogger_{kind}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    def _ensure_store(self):
        for key in ("blogger_pool", "blogger_works",
                    "blogger_follows", "blogger_audits"):
            self.store.setdefault(key, {})
        # 种子博主(内存模式惰性灌入; Redis 模式惰性灌入)
        if not self.store["blogger_pool"]:
            for bid, blogger in _build_seed_bloggers().items():
                self.store["blogger_pool"][bid] = dict(blogger)

    async def _ensure_pool_seeded(self) -> None:
        """博主池种子惰性灌入(幂等, 双模式)"""
        existing = await self._list(self.TABLE_POOL, limit=1000)
        if existing:
            return
        for bid, blogger in _build_seed_bloggers().items():
            await self._save(self.TABLE_POOL, bid, blogger)

    async def next_blogger_id(self) -> int:
        """博主ID(种子感知: 新增博主从 9 起, 避免与种子冲突)"""
        await self._ensure_pool_seeded()
        existing = await self._list(self.TABLE_POOL, limit=10000)
        return max((int(r.get("bloggerId") or 0) for r in existing),
                   default=0) + 1

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @staticmethod
    def _deserialize(data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in _INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in _FLOAT_FIELDS:
                try:
                    record[k] = float(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif isinstance(v, str) and v.startswith(("{", "[")):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("blogger", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("blogger", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("blogger", table, "*"))
            result = []
            for key in keys:
                if key.endswith(":seq"):
                    continue
                data = await client.hgetall(key)
                if data:
                    result.append(self._deserialize(data))
        else:
            self._ensure_store()
            result = list(self.store[table].values())
        return result[:limit]

    async def _update(self, table: str, record_id, fields: dict) -> dict:
        record = await self._get(table, record_id)
        if record is None:
            raise KeyError(record_id)
        record.update(fields)
        return await self._save(table, record_id, record)

    # ============================================================
    # 博主池
    # ============================================================

    async def save_blogger(self, record: dict) -> dict:
        return await self._save(self.TABLE_POOL,
                                record["bloggerId"], record)

    async def get_blogger(self, blogger_id: int) -> dict | None:
        await self._ensure_pool_seeded()
        record = await self._get(self.TABLE_POOL, blogger_id)
        return normalize_blogger(record) if record else record

    async def update_blogger(self, blogger_id: int,
                             fields: dict) -> dict:
        return await self._update(self.TABLE_POOL, blogger_id, fields)

    async def list_bloggers(self, status: str = None,
                            platform: str = None,
                            limit: int = 100) -> list[dict]:
        await self._ensure_pool_seeded()
        records = await self._list(self.TABLE_POOL, limit=1000)
        result = []
        for r in records:
            r = normalize_blogger(r)
            if status and r.get("status") != status:
                continue
            if platform and r.get("platform") != platform:
                continue
            result.append(r)
        return sorted(result, key=lambda x: -float(
            x.get("weight") or 0))[:limit]

    # ============================================================
    # 侦测作品
    # ============================================================

    async def save_work(self, record: dict) -> dict:
        return await self._save(self.TABLE_WORKS,
                                record["workId"], record)

    async def get_work(self, work_id: int) -> dict | None:
        return await self._get(self.TABLE_WORKS, work_id)

    async def update_work(self, work_id: int, fields: dict) -> dict:
        return await self._update(self.TABLE_WORKS, work_id, fields)

    async def list_works(self, blogger_id: int = None,
                         status: str = None,
                         limit: int = 100) -> list[dict]:
        records = await self._list(self.TABLE_WORKS, limit=1000)
        result = []
        for r in records:
            if blogger_id is not None \
                    and r.get("bloggerId") != blogger_id:
                continue
            if status and r.get("status") != status:
                continue
            result.append(r)
        return sorted(result, key=lambda x: x.get("workId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 作品指纹去重(48h 窗口)
    # ============================================================

    async def work_fingerprint_seen(self, fingerprint: str) -> bool:
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("blogger", "work_fps")
            added = await client.sadd(key, fingerprint)
            if added:
                await client.expire(key, 48 * 3600)
                return False
            return True
        self._ensure_store()
        fps = self.store.setdefault("_blogger_work_fps", set())
        if fingerprint in fps:
            return True
        fps.add(fingerprint)
        return False

    # ============================================================
    # 跟随内容
    # ============================================================

    async def save_follow(self, record: dict) -> dict:
        return await self._save(self.TABLE_FOLLOWS,
                                record["followId"], record)

    async def get_follow(self, follow_id: int) -> dict | None:
        return await self._get(self.TABLE_FOLLOWS, follow_id)

    async def update_follow(self, follow_id: int,
                            fields: dict) -> dict:
        return await self._update(self.TABLE_FOLLOWS, follow_id,
                                  fields)

    async def list_follows(self, blogger_id: int = None,
                           status: str = None,
                           limit: int = 100) -> list[dict]:
        records = await self._list(self.TABLE_FOLLOWS, limit=1000)
        result = []
        for r in records:
            if blogger_id is not None \
                    and r.get("bloggerId") != blogger_id:
                continue
            if status and r.get("status") != status:
                continue
            result.append(r)
        return sorted(result, key=lambda x: x.get("followId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 流水留痕
    # ============================================================

    async def save_audit(self, record: dict) -> dict:
        return await self._save(self.TABLE_AUDITS,
                                record["auditId"], record)

    async def list_audits(self, blogger_id: int = None,
                          limit: int = 100) -> list[dict]:
        records = await self._list(self.TABLE_AUDITS, limit=1000)
        if blogger_id is not None:
            records = [r for r in records
                       if r.get("bloggerId") == blogger_id]
        return sorted(records, key=lambda x: x.get("auditId", 0),
                      reverse=True)[:limit]
