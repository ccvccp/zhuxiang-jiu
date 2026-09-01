"""37号·AI智能网站同盟模块数据访问层(双模式: 内存 + Redis)

表清单:
    alliance_categories:   类目字典(角色定义/资质要求/密度上限, admin 可配)
    alliance_merchants:    同盟商(入盟状态机/类目/等级/信用)
    alliance_applications: 入盟申请(AI 预审报告/审核记录)
    alliance_products:     同盟商品(溯源绑定/商户价/库存/状态)
    alliance_settlements:  结算单(15% 拆账明细/幂等/冲正)
    alliance_reviews:      双向评价(星级/折叠标记)
    alliance_orders:       同盟订单(交易→分润快照)

设计对齐(AI智能网站同盟模块37_设计文档 v1.0 §3):
    - 入盟状态机: pending→ai_reviewing→manual_reviewing→signed→
      probation→active⇄suspended→terminated/rejected
    - 分润: 订单成交价 15% 抽佣, 五方拆账(参数化)
    - Redis Key 前缀 alliance:*, 锁前缀 lock:alliance:*
    - 异常约定: KeyError → 404 / ValueError → 409(service 层抛)
"""

import json
import os
from datetime import datetime, UTC

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k

# ============================================================
# 类目字典(酒水不分家: 水茶酒菜肉鱼器境, 设计文档 §1.2)
# ============================================================

CATEGORY_WATER = "water"        # 好水
CATEGORY_TEA = "tea"            # 好茶
CATEGORY_WINE = "wine"          # 好酒(非本站自营)
CATEGORY_DISH = "dish"          # 好菜
CATEGORY_MEAT = "meat"          # 肉类
CATEGORY_FISH = "fish"          # 鱼类
CATEGORY_VESSEL = "vessel"      # 酒具
CATEGORY_VENUE = "venue"        # 好境(酒店/会所)
CATEGORIES = (CATEGORY_WATER, CATEGORY_TEA, CATEGORY_WINE, CATEGORY_DISH,
              CATEGORY_MEAT, CATEGORY_FISH, CATEGORY_VESSEL, CATEGORY_VENUE)

# 溯源级别: wine 全量(复用 trace_prod 7 工段), 其余简化(凭证哈希)
TRACE_LEVEL_FULL = "full"
TRACE_LEVEL_LITE = "lite"

# 类目种子: (角色码, 名称, 溯源级别, 必备资质, 地图密度上限)
CATEGORY_SEEDS = {
    CATEGORY_WATER:  {"code": CATEGORY_WATER, "name": "好水", "traceLevel": TRACE_LEVEL_LITE,
                      "requiredCredentials": ("水源地凭证",), "gridCap": 3},
    CATEGORY_TEA:    {"code": CATEGORY_TEA, "name": "好茶", "traceLevel": TRACE_LEVEL_LITE,
                      "requiredCredentials": ("产地凭证",), "gridCap": 3},
    CATEGORY_WINE:   {"code": CATEGORY_WINE, "name": "好酒", "traceLevel": TRACE_LEVEL_FULL,
                      "requiredCredentials": ("食品经营许可证",), "gridCap": 5},
    CATEGORY_DISH:   {"code": CATEGORY_DISH, "name": "好菜", "traceLevel": TRACE_LEVEL_LITE,
                      "requiredCredentials": ("食品经营许可证",), "gridCap": 3},
    CATEGORY_MEAT:   {"code": CATEGORY_MEAT, "name": "肉类", "traceLevel": TRACE_LEVEL_LITE,
                      "requiredCredentials": ("检疫证明",), "gridCap": 3},
    CATEGORY_FISH:   {"code": CATEGORY_FISH, "name": "鱼类", "traceLevel": TRACE_LEVEL_LITE,
                      "requiredCredentials": ("冷链凭证",), "gridCap": 3},
    CATEGORY_VESSEL: {"code": CATEGORY_VESSEL, "name": "酒具", "traceLevel": TRACE_LEVEL_LITE,
                      "requiredCredentials": (), "gridCap": 3},
    CATEGORY_VENUE:  {"code": CATEGORY_VENUE, "name": "好境", "traceLevel": TRACE_LEVEL_LITE,
                      "requiredCredentials": ("食品经营许可证", "消防验收",), "gridCap": 3},
}

# ============================================================
# 入盟门槛(超级会员线, 设计文档 §1.3)
# ============================================================

# 会员等级门槛(≥ Lv4 超级会员)
ONBOARD_MEMBER_LEVEL_MIN = int(os.environ.get("ALLIANCE_MEMBER_LEVEL_MIN", "4"))
# role 信用分门槛(缺省 80; P0 用简化信用口径: 100 起步按违规扣减)
ONBOARD_CREDIT_MIN = int(os.environ.get("ALLIANCE_CREDIT_MIN", "80"))
# 清退冷却期(天)
REJECT_COOLDOWN_DAYS = 90

# 入盟状态机
STATUS_PENDING = "pending"                # 已申请(待 AI 预审)
STATUS_AI_REVIEWING = "ai_reviewing"      # AI 预审中
STATUS_MANUAL_REVIEWING = "manual_reviewing"  # 人工审核中
STATUS_SIGNED = "signed"                  # 已签约(待激活试用)
STATUS_PROBATION = "probation"            # 试用期(90 天)
STATUS_ACTIVE = "active"                  # 正式同盟商
STATUS_SUSPENDED = "suspended"            # 暂停(整改)
STATUS_TERMINATED = "terminated"          # 已终止(主动退出/清退)
STATUS_REJECTED = "rejected"              # 审核拒绝
MERCHANT_STATUSES = (STATUS_PENDING, STATUS_AI_REVIEWING, STATUS_MANUAL_REVIEWING,
                     STATUS_SIGNED, STATUS_PROBATION, STATUS_ACTIVE,
                     STATUS_SUSPENDED, STATUS_TERMINATED, STATUS_REJECTED)

# 状态转移表(设计文档 §2.1)
STATUS_TRANSITIONS = {
    STATUS_PENDING: (STATUS_AI_REVIEWING, STATUS_REJECTED),
    STATUS_AI_REVIEWING: (STATUS_MANUAL_REVIEWING, STATUS_REJECTED),
    STATUS_MANUAL_REVIEWING: (STATUS_SIGNED, STATUS_REJECTED),
    STATUS_SIGNED: (STATUS_PROBATION, STATUS_TERMINATED),
    STATUS_PROBATION: (STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_TERMINATED),
    STATUS_ACTIVE: (STATUS_SUSPENDED, STATUS_TERMINATED),
    STATUS_SUSPENDED: (STATUS_ACTIVE, STATUS_TERMINATED),
    STATUS_TERMINATED: (),    # 终态(90 天冷却后可重新申请, 新建记录)
    STATUS_REJECTED: (),      # 终态(同上)
}

# 试用天数
PROBATION_DAYS = 90

# AI 预审三档(设计文档 §2.1)
AI_PASS_SCORE = 80          # ≥80 → 人工终审快车道
AI_REVIEW_SCORE = 60        # 60-79 → 人工重点审
# <60 → 拒

# ============================================================
# 商品(三道门禁, 设计文档 §2.3)
# ============================================================

PRODUCT_STATUS_PENDING = "pending"    # 待审核(三道门禁)
PRODUCT_STATUS_ACTIVE = "active"      # 在售
PRODUCT_STATUS_OFFLINE = "offline"    # 商户下架
PRODUCT_STATUS_BLOCKED = "blocked"    # 平台下架(资质过期/违规)
PRODUCT_STATUSES = (PRODUCT_STATUS_PENDING, PRODUCT_STATUS_ACTIVE,
                    PRODUCT_STATUS_OFFLINE, PRODUCT_STATUS_BLOCKED)

# 商品合规门禁: 复用 attract 禁用词口径 + 酒类警示
from repositories.attract_repository import BANNED_WORDS  # noqa: E402
PRODUCT_BANNED_EXTRA = ("治病", "疗效", "药用", "保健功效", "壮阳")
PRODUCT_BANNED_WORDS = BANNED_WORDS + PRODUCT_BANNED_EXTRA

# ============================================================
# 分润(15% 抽佣五方拆账, 设计文档 §2.4; admin 参数化)
# ============================================================

# 平台抽佣率(占成交价)
PLATFORM_COMMISSION_RATE = 0.15
# 抽佣内部分账(占抽佣额, 合计=1.0)
DEFAULT_SHARE_RATES = {
    "platform": 0.40,       # 平台(即成交价 6%)
    "category_service": 0.20,  # 类目服务商
    "referrer": 0.15,       # 推荐人(拉商户入盟者)
    "city_store": 0.15,     # 就近市店
    "development_fund": 0.10,  # 同盟发展基金
}
# 结算周期: 订单成交 T+1 分润; 商户货款 T+7 可提现
SETTLE_DELAY_HOURS = int(os.environ.get("ALLIANCE_SETTLE_DELAY_HOURS", "24"))

# 结算状态
SETTLEMENT_STATUS_SETTLED = "settled"
SETTLEMENT_STATUS_REVERSED = "reversed"

# ============================================================
# 评价(设计文档 §2.6)
# ============================================================

REVIEW_MIN_SCORE = 1
REVIEW_MAX_SCORE = 5


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AllianceRepository:
    """37号·AI智能网站同盟数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # ID 生成
    # ============================================================

    async def next_id(self, entity: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("alliance", entity, "seq"))
        self._ensure_store()
        seq_key = f"_alliance_{entity}_seq"
        self.store[seq_key] = self.store.get(seq_key, 0) + 1
        return self.store[seq_key]

    # ============================================================
    # 类目字典
    # ============================================================

    async def save_category(self, category: dict) -> dict:
        return await self._save("alliance_categories",
                                category["code"], category)

    async def get_category(self, code: str) -> dict | None:
        return await self._get("alliance_categories", code)

    async def list_categories(self, limit: int = 50) -> list[dict]:
        categories = await self._list("alliance_categories", limit)
        return sorted(categories, key=lambda c: c.get("code", ""))

    async def ensure_categories(self) -> int:
        """初始化类目种子(幂等, 返回新增数)"""
        count = 0
        for code, seed in CATEGORY_SEEDS.items():
            if await self.get_category(code) is None:
                await self.save_category({**seed, "createdAt": _now_iso()})
                count += 1
        return count

    # ============================================================
    # 同盟商
    # ============================================================

    async def save_merchant(self, merchant: dict) -> dict:
        return await self._save("alliance_merchants",
                                merchant["merchantId"], merchant)

    async def get_merchant(self, merchant_id: int) -> dict | None:
        return await self._get("alliance_merchants", merchant_id)

    async def list_merchants(self, status: str = None, category: str = None,
                             limit: int = 200) -> list[dict]:
        merchants = await self._list("alliance_merchants", limit * 10)
        if status:
            merchants = [m for m in merchants if m.get("status") == status]
        if category:
            merchants = [m for m in merchants
                         if m.get("category") == category]
        return sorted(merchants, key=lambda m: m.get("merchantId", 0),
                      reverse=True)[:limit]

    async def find_merchant_by_member(self, member_id: int,
                                      status: str = None) -> dict | None:
        merchants = await self._list("alliance_merchants", 2000)
        for m in merchants:
            if m.get("memberId") == member_id:
                if status is None or m.get("status") == status:
                    return m
        return None

    async def find_terminated_recent(self, member_id: int,
                                      cooldown_days: int) -> dict | None:
        """清退冷却期检查: 近 N 天内终止/拒绝的申请(设计文档 §1.3)"""
        from datetime import timedelta
        threshold = (datetime.now(UTC)
                     - timedelta(days=cooldown_days)).isoformat()
        merchants = await self._list("alliance_merchants", 2000)
        for m in merchants:
            if (m.get("memberId") == member_id
                    and m.get("status") in (STATUS_TERMINATED, STATUS_REJECTED)
                    and m.get("updatedAt", "") >= threshold):
                return m
        return None

    # ============================================================
    # 入盟申请
    # ============================================================

    async def save_application(self, application: dict) -> dict:
        return await self._save("alliance_applications",
                                application["applicationId"], application)

    async def get_application(self, application_id: int) -> dict | None:
        return await self._get("alliance_applications", application_id)

    async def list_applications(self, status: str = None,
                                limit: int = 200) -> list[dict]:
        applications = await self._list("alliance_applications", limit * 10)
        if status:
            applications = [a for a in applications
                            if a.get("status") == status]
        return sorted(applications, key=lambda a: a.get("applicationId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 同盟商品
    # ============================================================

    async def save_product(self, product: dict) -> dict:
        return await self._save("alliance_products",
                                product["productId"], product)

    async def get_product(self, product_id: int) -> dict | None:
        return await self._get("alliance_products", product_id)

    async def list_products(self, merchant_id: int = None, category: str = None,
                            status: str = None,
                            limit: int = 200) -> list[dict]:
        products = await self._list("alliance_products", limit * 10)
        if merchant_id is not None:
            products = [p for p in products
                        if p.get("merchantId") == merchant_id]
        if category:
            products = [p for p in products if p.get("category") == category]
        if status:
            products = [p for p in products if p.get("status") == status]
        return sorted(products, key=lambda p: p.get("productId", 0),
                      reverse=True)[:limit]

    async def find_product_by_sku(self, sku: str) -> dict | None:
        products = await self._list("alliance_products", 5000)
        for p in products:
            if p.get("sku") == sku:
                return p
        return None

    # ============================================================
    # 同盟订单(交易→分润快照)
    # ============================================================

    async def save_order(self, order: dict) -> dict:
        return await self._save("alliance_orders",
                                order["orderId"], order)

    async def get_order(self, order_id: str) -> dict | None:
        return await self._get("alliance_orders", order_id)

    async def list_orders(self, merchant_id: int = None,
                          status: str = None,
                          limit: int = 500) -> list[dict]:
        orders = await self._list("alliance_orders", limit * 5)
        if merchant_id is not None:
            orders = [o for o in orders
                      if o.get("merchantId") == merchant_id]
        if status:
            orders = [o for o in orders if o.get("status") == status]
        return sorted(orders, key=lambda o: o.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 结算单
    # ============================================================

    async def save_settlement(self, settlement: dict) -> dict:
        return await self._save("alliance_settlements",
                                settlement["settlementId"], settlement)

    async def get_settlement(self, settlement_id: int) -> dict | None:
        return await self._get("alliance_settlements", settlement_id)

    async def find_settlement_by_order(self, order_id: str) -> dict | None:
        settlements = await self._list("alliance_settlements", 5000)
        for s in settlements:
            if s.get("orderId") == order_id:
                return s
        return None

    async def list_settlements(self, status: str = None,
                               limit: int = 200) -> list[dict]:
        settlements = await self._list("alliance_settlements", limit * 5)
        if status:
            settlements = [s for s in settlements
                           if s.get("status") == status]
        return sorted(settlements,
                      key=lambda s: s.get("settlementId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 分润配置(admin 可调)
    # ============================================================

    async def get_share_settings(self) -> dict:
        settings = await self._get("alliance_share_settings", "global")
        if settings is None:
            settings = {"commissionRate": PLATFORM_COMMISSION_RATE,
                        "shareRates": dict(DEFAULT_SHARE_RATES),
                        "updatedAt": ""}
            await self._save("alliance_share_settings", "global", settings)
        return settings

    async def save_share_settings(self, settings: dict) -> dict:
        return await self._save("alliance_share_settings", "global", settings)

    # ============================================================
    # 评价
    # ============================================================

    async def save_review(self, review: dict) -> dict:
        return await self._save("alliance_reviews",
                                review["reviewId"], review)

    async def get_review(self, review_id: int) -> dict | None:
        return await self._get("alliance_reviews", review_id)

    async def list_reviews(self, merchant_id: int = None,
                           order_id: str = None, folded: bool = None,
                           limit: int = 500) -> list[dict]:
        reviews = await self._list("alliance_reviews", limit * 5)
        if merchant_id is not None:
            reviews = [r for r in reviews
                       if r.get("merchantId") == merchant_id]
        if order_id:
            reviews = [r for r in reviews if r.get("orderId") == order_id]
        if folded is not None:
            reviews = [r for r in reviews
                       if bool(r.get("folded")) == folded]
        return sorted(reviews, key=lambda r: r.get("reviewId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 通用存储(内存/Redis)
    # ============================================================

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            key_id = record_id if isinstance(record_id, str) else str(record_id)
            await client.set(_k("alliance", table, key_id),
                             json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            key_id = record_id if isinstance(record_id, str) else str(record_id)
            data = await client.get(_k("alliance", table, key_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("alliance", table, "*"))
            records = []
            for key in keys:
                # 排除自增序列键(36号 attract click:seq 教训)
                if str(key).endswith(":seq"):
                    continue
                data = await client.get(key)
                if not data:
                    continue
                record = json.loads(data)
                if isinstance(record, dict):
                    records.append(record)
            return records[:limit]
        self._ensure_store()
        return list(self.store[table].values())[:limit]

    # ============================================================
    # 内存模式初始化
    # ============================================================

    def _ensure_store(self) -> None:
        if "alliance_categories" not in self.store:
            self.store["alliance_categories"] = {}
            self.store["alliance_merchants"] = {}
            self.store["alliance_applications"] = {}
            self.store["alliance_products"] = {}
            self.store["alliance_orders"] = {}
            self.store["alliance_settlements"] = {}
            self.store["alliance_reviews"] = {}
            self.store["alliance_share_settings"] = {}   # 分润配置
            self.store["_alliance_merchant_seq"] = 0
            self.store["_alliance_application_seq"] = 0
            self.store["_alliance_product_seq"] = 0
            self.store["_alliance_settlement_seq"] = 0
            self.store["_alliance_review_seq"] = 0
