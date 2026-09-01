"""AI智能中枢模块(35号)数据访问层(双模式: 内存 + Redis)

键清单(设计文档 v1.0 第六章):
    hub:capabilities:        AI能力注册表(Hash, field=cap_id, value=JSON)
    hub:intent_stats:{date}: 意图日计数(Hash, field=intent, value=n)
    hub:asr:usage:{mid}:     会员日语音用量(String, TTL 24h, 限流)
    hub:route:health:{cid}:  能力健康滚动窗口(Hash: success/fail/p95样本)

设计对齐(AI智能中枢模块设计文档 v1.0):
    - P0: 能力注册表种子 + 意图统计 + ASR限流存储
    - 能力注册表为 P1 路由器的数据地基, P0 先落存储与种子
"""

import json
from datetime import datetime, UTC

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 意图集(v1, 设计文档 5.2.3)
# ============================================================

INTENT_PRODUCT_PRICE = "product.price"
INTENT_PRODUCT_RECOMMEND = "product.recommend"
INTENT_ORDER_QUERY = "order.query"
INTENT_ORDER_AFTERSALE = "order.aftersale"
INTENT_KNOWLEDGE_QA = "knowledge.qa"
INTENT_CHAT_HUMAN = "chat.human"
INTENT_ROLE_PROFIT = "role.profit"
INTENT_ROLE_DISPATCH = "role.dispatch"
INTENT_CREDIT_QUERY = "credit.query"
INTENT_OPS_HEALTH = "ops.health"
INTENT_MEDIA_IMAGE_QA = "media.image_qa"
INTENT_CHAT_GENERAL = "chat.general"

INTENTS = (
    INTENT_PRODUCT_PRICE, INTENT_PRODUCT_RECOMMEND,
    INTENT_ORDER_QUERY, INTENT_ORDER_AFTERSALE,
    INTENT_KNOWLEDGE_QA, INTENT_CHAT_HUMAN,
    INTENT_ROLE_PROFIT, INTENT_ROLE_DISPATCH,
    INTENT_CREDIT_QUERY, INTENT_OPS_HEALTH,
    INTENT_MEDIA_IMAGE_QA, INTENT_CHAT_GENERAL,
)

# 意图 → 规则轨关键词(正则, 命中即路由; 顺序即优先级)
INTENT_RULES: list[tuple[str, list[str]]] = [
    (INTENT_CHAT_HUMAN, ["转人工", "人工客服", "找真人", "真人客服"]),
    (INTENT_ORDER_QUERY, ["查订单", "我的订单", "订单查询", "物流", "快递",
                           "发货了吗", "到哪了"]),
    (INTENT_ORDER_AFTERSALE, ["退货", "退款", "售后", "换货", "投诉"]),
    (INTENT_ROLE_PROFIT, ["分润", "结算", "我的收益", "收益查询", "佣金"]),
    (INTENT_ROLE_DISPATCH, ["派单", "接单", "抢单", "工单队列"]),
    (INTENT_CREDIT_QUERY, ["积分", "信用", "竹信分", "等级查询"]),
    (INTENT_PRODUCT_PRICE, ["多少钱", "价格", "怎么卖", "售价", "报价"]),
    (INTENT_PRODUCT_RECOMMEND, ["推荐", "哪种好", "介绍下", "适合我"]),
    (INTENT_OPS_HEALTH, ["AI健康", "AI状态", "系统状态", "运维状态"]),
    (INTENT_KNOWLEDGE_QA, ["是什么", "为什么", "怎么回",
                            "竹香", "酿造", "储藏", "酒厂"]),
]


def classify_intent_rule(text: str) -> str:
    """规则轨意图分类(纯函数, <5ms): 关键词命中, 未命中回退 chat.general"""
    if not text:
        return INTENT_CHAT_GENERAL
    for intent, keywords in INTENT_RULES:
        for kw in keywords:
            if kw in text:
                return intent
    return INTENT_CHAT_GENERAL


# ============================================================
# 角色面板配置(设计文档 5.1.2, P0 静态定义)
# ============================================================

ROLE_GUEST = "guest"
ROLE_MEMBER = "member"
ROLE_STAFF = "cs_staff"
ROLE_ADMIN = "admin"
HUB_ROLES = (ROLE_GUEST, ROLE_MEMBER, ROLE_STAFF, ROLE_ADMIN)

# 角色 → 能力 chips(≤6, 会话顶部横条)
ROLE_PANELS: dict[str, list[dict]] = {
    ROLE_GUEST: [
        {"id": "product.qa", "label": "商品问答", "quick": "竹香酒有什么产品？"},
        {"id": "product.price", "label": "问价格", "quick": "竹韵佳酿多少钱一瓶？"},
        {"id": "knowledge.qa", "label": "酒知识", "quick": "酱香型白酒是什么工艺？"},
        {"id": "chat.human", "label": "转人工", "quick": "转人工"},
    ],
    ROLE_MEMBER: [
        {"id": "order.query", "label": "查订单", "quick": "查我的最近订单"},
        {"id": "product.price", "label": "问价格", "quick": "竹韵佳酿多少钱一瓶？"},
        {"id": "credit.query", "label": "我的积分", "quick": "查我的积分和等级"},
        {"id": "product.recommend", "label": "推荐", "quick": "推荐一款适合送长辈的酒"},
        {"id": "order.aftersale", "label": "售后", "quick": "我要退货"},
        {"id": "chat.human", "label": "转人工", "quick": "转人工"},
    ],
    ROLE_STAFF: [
        {"id": "role.dispatch", "label": "工单队列", "quick": "查看待接工单"},
        {"id": "role.profit", "label": "我的分润", "quick": "查我的分润预估"},
        {"id": "knowledge.qa", "label": "知识库", "quick": "查询产品知识"},
        {"id": "order.query", "label": "查订单", "quick": "查我的最近订单"},
        {"id": "chat.human", "label": "转人工", "quick": "转人工"},
    ],
    ROLE_ADMIN: [
        {"id": "ops.health", "label": "AI健康", "quick": "查看AI系统健康状态"},
        {"id": "role.dispatch", "label": "派单总览", "quick": "查看派单总览"},
        {"id": "role.profit", "label": "分润总账", "quick": "查分润总账"},
        {"id": "knowledge.qa", "label": "知识库", "quick": "查询产品知识"},
        {"id": "order.query", "label": "查订单", "quick": "查我的最近订单"},
        {"id": "chat.human", "label": "转人工", "quick": "转人工"},
    ],
}


# ============================================================
# 能力注册表种子(设计文档 5.3.1, P0 静态注册)
# ============================================================

CAPABILITY_SEED: list[dict] = [
    {"id": "hub.asr", "name": "语音识别", "module": "hub",
     "intents": [], "roles": list(HUB_ROLES),
     "endpoint": "internal:hub_service.transcribe_upload",
     "health": {"success_rate_7d": 1.0, "p95_ms": 900, "fallback_rate": 0.0},
     "cost_weight": 1.0, "enabled": True},
    {"id": "knowledge.rag", "name": "知识库问答", "module": "knowledge",
     "intents": [INTENT_KNOWLEDGE_QA, INTENT_PRODUCT_PRICE,
                 INTENT_PRODUCT_RECOMMEND],
     "roles": list(HUB_ROLES),
     "endpoint": "internal:knowledge_service",
     "health": {"success_rate_7d": 0.93, "p95_ms": 820, "fallback_rate": 0.07},
     "cost_weight": 1.0, "enabled": True},
    {"id": "chat.human", "name": "人工转接", "module": "chat",
     "intents": [INTENT_CHAT_HUMAN], "roles": list(HUB_ROLES),
     "endpoint": "internal:chat_service",
     "health": {"success_rate_7d": 1.0, "p95_ms": 50, "fallback_rate": 0.0},
     "cost_weight": 0.1, "enabled": True},
    {"id": "order.query", "name": "订单查询", "module": "order",
     "intents": [INTENT_ORDER_QUERY], "roles": [ROLE_MEMBER, ROLE_STAFF, ROLE_ADMIN],
     "endpoint": "internal:order_service",
     "health": {"success_rate_7d": 1.0, "p95_ms": 100, "fallback_rate": 0.0},
     "cost_weight": 0.2, "enabled": True},
    {"id": "role.dispatch", "name": "工单派单", "module": "role",
     "intents": [INTENT_ROLE_DISPATCH], "roles": [ROLE_STAFF, ROLE_ADMIN],
     "endpoint": "internal:role_service",
     "health": {"success_rate_7d": 0.99, "p95_ms": 200, "fallback_rate": 0.01},
     "cost_weight": 0.3, "enabled": True},
    {"id": "role.profit", "name": "分润结算", "module": "role",
     "intents": [INTENT_ROLE_PROFIT], "roles": [ROLE_STAFF, ROLE_ADMIN],
     "endpoint": "internal:role_service",
     "health": {"success_rate_7d": 0.99, "p95_ms": 300, "fallback_rate": 0.01},
     "cost_weight": 0.3, "enabled": True},
    {"id": "hub.ops", "name": "AI治理", "module": "hub",
     "intents": [INTENT_OPS_HEALTH], "roles": [ROLE_ADMIN],
     "endpoint": "internal:hub_service",
     "health": {"success_rate_7d": 1.0, "p95_ms": 80, "fallback_rate": 0.0},
     "cost_weight": 0.1, "enabled": True},
]


class HubRepository:
    """AI智能中枢模块数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    def _ensure_store(self) -> None:
        """确保内存存储包含中枢模块的键(懒初始化+能力种子)"""
        if "hub_capabilities" not in self.store:
            self.store["hub_capabilities"] = {}
            self.store["hub_intent_stats"] = {}      # {date: {intent: n}}
            self.store["hub_asr_usage"] = {}          # {(mid, date): n}
            for cap in CAPABILITY_SEED:
                self.store["hub_capabilities"][cap["id"]] = dict(cap)

    # ============================================================
    # 能力注册表
    # ============================================================

    async def list_capabilities(self) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("hub", "capabilities"))
            caps = [json.loads(v) for v in data.values()] if data else []
            if not caps:  # 首次访问: 写入种子
                for cap in CAPABILITY_SEED:
                    await client.hset(_k("hub", "capabilities"), cap["id"],
                                     json.dumps(cap, ensure_ascii=False))
                return [dict(c) for c in CAPABILITY_SEED]
        else:
            self._ensure_store()
            caps = list(self.store["hub_capabilities"].values())
        caps.sort(key=lambda c: c.get("id", ""))
        return caps

    async def get_capability(self, cap_id: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hget(_k("hub", "capabilities"), cap_id)
            if data:
                return json.loads(data)
            for cap in CAPABILITY_SEED:  # 种子兜底
                if cap["id"] == cap_id:
                    await client.hset(_k("hub", "capabilities"), cap_id,
                                      json.dumps(cap, ensure_ascii=False))
                    return dict(cap)
            return None
        self._ensure_store()
        return self.store["hub_capabilities"].get(cap_id)

    async def upsert_capability(self, cap: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("hub", "capabilities"), cap["id"],
                              json.dumps(cap, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["hub_capabilities"][cap["id"]] = cap
        return cap

    # ============================================================
    # 意图统计(日计数)
    # ============================================================

    async def bump_intent(self, intent: str) -> int:
        """意图日计数+1, 返回当日该意图累计值"""
        today = datetime.now(UTC).strftime("%Y%m%d")
        if is_redis_mode():
            client = await get_redis_client()
            return await client.hincrby(_k("hub", "intent_stats", today),
                                        intent, 1)
        self._ensure_store()
        day = self.store["hub_intent_stats"].setdefault(today, {})
        day[intent] = day.get(intent, 0) + 1
        return day[intent]

    async def get_intent_stats(self, date: str) -> dict[str, int]:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("hub", "intent_stats", date))
            return {k: int(v) for k, v in data.items()} if data else {}
        self._ensure_store()
        return dict(self.store["hub_intent_stats"].get(date, {}))

    # ============================================================
    # ASR 日用量限流
    # ============================================================

    async def bump_asr_usage(self, member_id: int, daily_limit: int) -> tuple[int, bool]:
        """会员日语音用量+1; 返回 (当日已用, 是否超限)"""
        today = datetime.now(UTC).strftime("%Y%m%d")
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("hub", "asr", "usage", f"{member_id}:{today}")
            used = await client.incr(key)
            if used == 1:
                await client.expire(key, 86400)
            return used, used > daily_limit
        self._ensure_store()
        k = (member_id, today)
        used = self.store["hub_asr_usage"].get(k, 0) + 1
        self.store["hub_asr_usage"][k] = used
        return used, used > daily_limit
