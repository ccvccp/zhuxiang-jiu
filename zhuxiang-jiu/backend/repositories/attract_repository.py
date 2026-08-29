"""AI智能自动引流模块数据访问层(双模式: 内存 + Redis)

表清单:
    attract_topics:          选题库(manual/ai_roi 双源)
    attract_contents:       内容库(多平台变体+合规分)
    attract_short_links:     活动短码(A-xxxx; ZXBJ/KOL码复用现有, 不重发)
    attract_clicks:         匿名点击流(click_id 体系, 核心增量)
    attract_attributions:   统一归因总表(点击→注册→下单→佣金)
    attract_channel_budgets: ROI 分配账本(渠道奖励系数)

设计对齐(AI智能自动引流模块设计文档 v1.0 第五~六章):
    - 短链 /r/{code} 按码类型分流(D-10)
    - 内容生成规则引擎 B 级 + 大模型接口抽象(D-11)
    - ROI 再分配作用于 promotion 奖励与 traffic 佣金双轨(D-12)
"""

import json
import secrets
import string
from datetime import datetime, timezone

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 平台与渠道
# ============================================================

PLATFORM_XIAOHONGSHU = "xiaohongshu"   # 小红书笔记
PLATFORM_DOUYIN = "douyin"             # 抖音15s脚本
PLATFORM_MOMENTS = "wechat_moments"    # 朋友圈文案
PLATFORM_SEO = "seo_article"           # SEO长文(P1)
PLATFORMS = (PLATFORM_XIAOHONGSHU, PLATFORM_DOUYIN,
             PLATFORM_MOMENTS, PLATFORM_SEO)

# 选题角度
ANGLE_CULTURE = "culture"      # 文化
ANGLE_SCENE = "scene"          # 场景
ANGLE_CRAFT = "craft"          # 工艺
ANGLE_OFFER = "offer"          # 优惠
ANGLES = (ANGLE_CULTURE, ANGLE_SCENE, ANGLE_CRAFT, ANGLE_OFFER)

# 选题来源
TOPIC_SOURCE_MANUAL = "manual"
TOPIC_SOURCE_AI_ROI = "ai_roi"   # ROI数据回流选题

# 内容状态
CONTENT_STATUS_PENDING = "pending"     # 待审核
CONTENT_STATUS_APPROVED = "approved"   # 审核通过
CONTENT_STATUS_REJECTED = "rejected"   # 审核拒绝
CONTENT_STATUSES = (CONTENT_STATUS_PENDING, CONTENT_STATUS_APPROVED,
                    CONTENT_STATUS_REJECTED, "published")

# ============================================================
# 短码体系(D-10: 按码类型分流)
# ============================================================

SHORT_CODE_PREFIX = "A"          # 活动短码前缀 A-{6位}
# 码类型识别
CODE_TYPE_PROMOTION = "promotion"   # 会员矩阵码(ZXBJ-xxx → 注册页)
CODE_TYPE_INFLUENCER = "influencer"  # KOL码(KOLxxx → 产品页)
CODE_TYPE_ACTIVITY = "activity"      # 活动短码(A-xxxx → 活动页)
# 跳转目标(D-10 决策: 分流)
LANDING_REGISTER = "/pages/register/index"   # 注册页(拉新优先)
LANDING_PRODUCT = "/pages/product/index"     # 产品页
LANDING_ACTIVITY = "/pages/activity/index"   # 活动页

# ============================================================
# 合规审核(复用广告禁用词口径 + 酒类强制项)
# ============================================================

# 禁用词(极限词/绝对化/医疗暗示, 对齐 ad 模块审核体系)
BANNED_WORDS = (
    "最好", "最佳", "第一", "顶级", "极品", "绝无仅有", "史上最",
    "包治", "疗效", "药效", "养生治百病", "延年益寿", "壮阳",
    "百分百", "绝对", "永久", "全网最低", "喝了对身体好",
)
# 酒类强制警示(缺项直接不通过)
REQUIRED_DISCLAIMER = "过量饮酒有害健康"
REQUIRED_AGE_TIP = "18"
# 合规评分: 100 - 命中词×30 - 缺项×35(警示/年龄), ≥70 通过
COMPLIANCE_PASS_SCORE = 70
BANNED_WORD_PENALTY = 30
MISSING_DISCLAIMER_PENALTY = 35
MISSING_AGE_PENALTY = 35

# ============================================================
# ROI 分配(D-12: promotion 奖励 + traffic 佣金双轨)
# ============================================================

# 奖励系数上下限(相对基准 1.0 的乘数)
RATE_FLOOR = 0.5
RATE_CEIL = 1.5
# 单次再分配调整步长
REBALANCE_STEP = 0.1
# 月度奖励总预算池(演示基线, 管理端可调)
DEFAULT_MONTHLY_POOL = 10000.0
# ROI 参与判定的渠道最低转化样本(注册数, 低于则不动该渠道)
ROI_MIN_SAMPLE = 1

# 初始渠道账本(基准系数 1.0)
CHANNEL_SEEDS = (
    "douyin", "kuaishou", "wechat", "xiaohongshu",
    "bilibili", "taobao", "direct",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_code(code: str) -> str:
    """识别码类型(D-10 分流依据)

    ZXBJ- 开头 → promotion(注册页)
    KOL 开头   → influencer(产品页)
    A- 开头    → activity(活动页, 本模块短码)
    """
    upper = (code or "").strip().upper()
    if upper.startswith("ZXBJ"):
        return CODE_TYPE_PROMOTION
    if upper.startswith("KOL"):
        return CODE_TYPE_INFLUENCER
    if upper.startswith("A-"):
        return CODE_TYPE_ACTIVITY
    return ""


def landing_for_code_type(code_type: str) -> str:
    """码类型 → 落地页路径(D-10)"""
    return {
        CODE_TYPE_PROMOTION: LANDING_REGISTER,
        CODE_TYPE_INFLUENCER: LANDING_PRODUCT,
        CODE_TYPE_ACTIVITY: LANDING_ACTIVITY,
    }.get(code_type, LANDING_REGISTER)


# ============================================================
# 内容生成模板(规则引擎 B 级, D-11; 大模型接口在 service 层抽象)
# ============================================================

GEN_TEMPLATES = {
    PLATFORM_XIAOHONGSHU: {
        "title": "{kw}｜{angle_word}：竹香酒的{scene_word}",
        "body": ("发现一款宝藏{kw}！{hook}\n\n"
                 "🎋 竹香型白酒，入口绵甜、落口回甘\n"
                 "🍶 {detail}\n"
                 "🎁 {offer}\n\n"
                 "姐妹们冲！评论区蹲一个酒友～\n"
                 "#{kw} #竹香型白酒 #好酒推荐\n"
                 "（{disclaimer}，{age_tip}周岁以下请勿饮酒）"),
    },
    PLATFORM_DOUYIN: {
        "title": "15s口播脚本",
        "body": ("【钩子0-3s】还在找{kw}？别刷走了！\n"
                 "【卖点3-10s】竹香型白酒，{detail}，{offer}！\n"
                 "【行动10-15s】点击下方链接，新人还有惊喜～"
                 "（{disclaimer}，未成年人禁止饮酒，满{age_tip}周岁请适量）"),
    },
    PLATFORM_MOMENTS: {
        "title": "朋友圈文案",
        "body": ("{hook}{kw}，竹香型白酒{detail}\n"
                 "{offer}👉 {link}\n"
                 "（{disclaimer}，{age_tip}+ 请适量饮用）"),
    },
    PLATFORM_SEO: {
        "title": "{kw}怎么选？竹香型白酒{angle_word}指南",
        "body": ("一、什么是{kw}\n竹香型白酒以竹子发酵工艺……\n"
                 "二、{detail}\n三、{offer}\n……\n"
                 "（本文适合{age_tip}周岁以上读者，{disclaimer}）"),
    },
}

# 角度词库(选题角度 → 生成用词)
ANGLE_WORDS = {
    ANGLE_CULTURE: ("文化之选", "传承百年", "匠心独运"),
    ANGLE_SCENE: ("聚会必备", "礼赠佳品", "小酌怡情"),
    ANGLE_CRAFT: ("工艺揭秘", "纯粮酿造", "竹香密码"),
    ANGLE_OFFER: ("限时优惠", "新人专享", "老友回馈"),
}


class AttractRepository:
    """AI智能自动引流模块数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # ID/短码生成
    # ============================================================

    async def next_id(self, entity: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("attract", entity, "seq"))
        return self._mem_next_id(f"_attract_{entity}_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    def generate_short_code(self) -> str:
        """活动短码: A-{6位大写字母数字}"""
        alphabet = string.ascii_uppercase + string.digits
        suffix = "".join(secrets.choice(alphabet) for _ in range(6))
        return f"{SHORT_CODE_PREFIX}-{suffix}"

    # ============================================================
    # 选题库
    # ============================================================

    async def save_topic(self, topic: dict) -> dict:
        return await self._save("attract_topics", topic["topicId"], topic)

    async def get_topic(self, topic_id: int) -> dict | None:
        return await self._get("attract_topics", topic_id)

    async def list_topics(self, status: str = None,
                           limit: int = 200) -> list[dict]:
        topics = await self._list("attract_topics", limit)
        if status:
            topics = [t for t in topics if t.get("status") == status]
        return sorted(topics, key=lambda t: t.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 内容库
    # ============================================================

    async def save_content(self, content: dict) -> dict:
        return await self._save("attract_contents", content["contentId"],
                                content)

    async def get_content(self, content_id: int) -> dict | None:
        return await self._get("attract_contents", content_id)

    async def list_contents(self, platform: str = None,
                            topic_id: int = None, status: str = None,
                            limit: int = 200) -> list[dict]:
        contents = await self._list("attract_contents", limit * 10)
        if platform:
            contents = [c for c in contents if c.get("platform") == platform]
        if topic_id is not None:
            contents = [c for c in contents if c.get("topicId") == topic_id]
        if status:
            contents = [c for c in contents if c.get("status") == status]
        return sorted(contents, key=lambda c: c.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 活动短码
    # ============================================================

    async def save_short_link(self, link: dict) -> dict:
        return await self._save("attract_short_links", link["code"], link)

    async def get_short_link(self, code: str) -> dict | None:
        return await self._get("attract_short_links", code)

    async def list_short_links(self, active: bool = None,
                               limit: int = 200) -> list[dict]:
        links = await self._list("attract_short_links", limit)
        if active is not None:
            links = [l for l in links if bool(l.get("active")) == active]
        return sorted(links, key=lambda l: l.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 匿名点击流(核心增量: 不要求注册)
    # ============================================================

    async def save_click(self, click: dict) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("attract", "click", click["clickId"]),
                             json.dumps(click, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["attract_clicks"][click["clickId"]] = click
        return click["clickId"]

    async def get_click(self, click_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("attract", "click", click_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["attract_clicks"].get(click_id)

    async def list_clicks(self, code: str = None, channel: str = None,
                          limit: int = 500) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("attract", "click", "*"))
            clicks = []
            for key in keys:
                data = await client.get(key)
                if data:
                    clicks.append(json.loads(data))
        else:
            self._ensure_store()
            clicks = list(self.store["attract_clicks"].values())
        if code:
            clicks = [c for c in clicks if c.get("code") == code]
        if channel:
            clicks = [c for c in clicks if c.get("channel") == channel]
        return sorted(clicks, key=lambda c: c.get("at", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 统一归因总表
    # ============================================================

    async def save_attribution(self, attr: dict) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("attract", "attr", attr["clickId"]),
                             json.dumps(attr, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["attract_attributions"][attr["clickId"]] = attr
        return attr["clickId"]

    async def get_attribution(self, click_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("attract", "attr", click_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["attract_attributions"].get(click_id)

    async def update_attribution(self, click_id: int, updates: dict) -> None:
        attr = await self.get_attribution(click_id)
        if attr is None:
            return
        attr.update(updates)
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("attract", "attr", click_id),
                             json.dumps(attr, ensure_ascii=False))

    async def list_attributions(self, channel: str = None,
                                promoter_id: int = None,
                                influencer_id: int = None,
                                member_id: int = None,
                                limit: int = 1000) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("attract", "attr", "*"))
            attrs = []
            for key in keys:
                data = await client.get(key)
                if data:
                    attrs.append(json.loads(data))
        else:
            self._ensure_store()
            attrs = list(self.store["attract_attributions"].values())
        if channel:
            attrs = [a for a in attrs if a.get("channel") == channel]
        if promoter_id is not None:
            attrs = [a for a in attrs if a.get("promoterId") == promoter_id]
        if influencer_id is not None:
            attrs = [a for a in attrs if a.get("influencerId") == influencer_id]
        if member_id is not None:
            attrs = [a for a in attrs if a.get("memberId") == member_id]
        return sorted(attrs, key=lambda a: a.get("registeredAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # ROI 分配账本
    # ============================================================

    async def save_budget(self, budget: dict) -> dict:
        return await self._save("attract_channel_budgets",
                                budget["channel"], budget)

    async def get_budget(self, channel: str) -> dict | None:
        return await self._get("attract_channel_budgets", channel)

    async def list_budgets(self, limit: int = 100) -> list[dict]:
        budgets = await self._list("attract_channel_budgets", limit)
        return sorted(budgets, key=lambda b: b.get("channel", ""))

    async def ensure_budgets(self) -> None:
        """初始化渠道账本(幂等: 已存在不覆盖)"""
        for channel in CHANNEL_SEEDS:
            existing = await self.get_budget(channel)
            if existing is None:
                await self.save_budget(self._new_budget(channel))

    @staticmethod
    def _new_budget(channel: str) -> dict:
        return {
            "channel": channel,
            "monthlyPool": DEFAULT_MONTHLY_POOL,
            "baseRate": 1.0,          # 基准奖励系数
            "currentRate": 1.0,        # 当前生效系数(ROI再分配调节)
            "roi": 0.0,
            "lastAdjustedAt": "",
            "createdAt": _now_iso(),
        }

    # ============================================================
    # 通用存储(内存/Redis)
    # ============================================================

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            key_id = record_id if isinstance(record_id, str) else str(record_id)
            await client.set(_k("attract", table, key_id),
                             json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            key_id = record_id if isinstance(record_id, str) else str(record_id)
            data = await client.get(_k("attract", table, key_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("attract", table, "*"))
            records = []
            for key in keys:
                data = await client.get(key)
                if data:
                    records.append(json.loads(data))
            return records[:limit]
        self._ensure_store()
        return list(self.store[table].values())[:limit]

    # ============================================================
    # 内存模式
    # ============================================================

    def _ensure_store(self) -> None:
        if "attract_topics" not in self.store:
            self.store["attract_topics"] = {}
            self.store["attract_contents"] = {}
            self.store["attract_short_links"] = {}
            self.store["attract_clicks"] = {}
            self.store["attract_attributions"] = {}
            self.store["attract_channel_budgets"] = {}
            self.store["_attract_topic_seq"] = 0
            self.store["_attract_content_seq"] = 0
            self.store["_attract_click_seq"] = 0
