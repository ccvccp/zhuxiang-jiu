"""市级网店模块数据访问层(双模式: 内存 + Redis)

表清单:
    P0: city_stores(网店主) + city_store_monthly_assessment(月度考核) + city_store_orders(订单关联)
    P1: city_store_ai_compliance(AI合规监控)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 序列号: CS-城市码-序号(storeCode 唯一)
    - 状态机: pending → operating → warning/suspended → cancelled
    - 锁键: 通过 core.locks.get_lock() 跨进程互斥(锁键定义在 service 层)
"""

import json

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 网店状态机
# ============================================================

STORE_STATUS_PENDING = 0        # 待审核
STORE_STATUS_OPERATING = 1      # 运营中
STORE_STATUS_WARNING = 2        # 预警
STORE_STATUS_SUSPENDED = 3      # 暂停
STORE_STATUS_CANCELLED = 4      # 已取消

STORE_STATUS_NAMES = {
    STORE_STATUS_PENDING: "待审核",
    STORE_STATUS_OPERATING: "运营中",
    STORE_STATUS_WARNING: "预警",
    STORE_STATUS_SUSPENDED: "暂停",
    STORE_STATUS_CANCELLED: "已取消",
}

# 合法状态流转(当前状态 → 可流转到的状态集合)
STORE_STATUS_FLOW = {
    STORE_STATUS_PENDING: {STORE_STATUS_OPERATING, STORE_STATUS_CANCELLED},
    STORE_STATUS_OPERATING: {STORE_STATUS_WARNING, STORE_STATUS_SUSPENDED, STORE_STATUS_CANCELLED},
    STORE_STATUS_WARNING: {STORE_STATUS_OPERATING, STORE_STATUS_SUSPENDED, STORE_STATUS_CANCELLED},
    STORE_STATUS_SUSPENDED: {STORE_STATUS_OPERATING, STORE_STATUS_CANCELLED},
    STORE_STATUS_CANCELLED: set(),  # 终态
}

# 活跃状态(未取消)
STORE_ACTIVE_STATUSES = {
    STORE_STATUS_PENDING, STORE_STATUS_OPERATING, STORE_STATUS_WARNING, STORE_STATUS_SUSPENDED,
}

# 终态
STORE_TERMINAL_STATUSES = {STORE_STATUS_CANCELLED}


# ============================================================
# 考核资格状态
# ============================================================

QUAL_STATUS_NORMAL = 1          # 正常
QUAL_STATUS_WARNING = 2         # 预警(连续1月不达标)
QUAL_STATUS_YELLOW_CARD = 3     # 黄牌(连续2月不达标)
QUAL_STATUS_CANCELLED = 4       # 取消(连续3月不达标)

QUAL_STATUS_NAMES = {
    QUAL_STATUS_NORMAL: "正常",
    QUAL_STATUS_WARNING: "预警",
    QUAL_STATUS_YELLOW_CARD: "黄牌",
    QUAL_STATUS_CANCELLED: "取消",
}


# ============================================================
# 阶梯折扣
# ============================================================

DISCOUNT_EXCELLENT = 70     # 优秀(月销>9000)
DISCOUNT_QUALIFIED = 80     # 达标(月销5000-9000)
DISCOUNT_UNQUALIFIED = 90   # 未达标(月销<5000)

# 月度考核指标
PURCHASE_TARGET = 9000.0    # 进货达标线
SALES_TARGET = 5000.0       # 销售达标线
MAX_CONSECUTIVE_BELOW = 3   # 连续不达标上限(超过则取消资格)

# 资格取消后冷静期天数(设计文档 8.3: 90 天内不可重新申请)
COOLDOWN_DAYS = 90

# 销售渠道
CHANNEL_LIVE = 1            # 直播
CHANNEL_MINIPROGRAM = 2     # 小程序
CHANNEL_COMMUNITY = 3       # 社群
CHANNEL_H5 = 4             # H5
CHANNEL_DOUYIN = 5         # 抖音引流


def calc_discount(monthly_sales: float) -> int:
    """根据月销售额计算次月进货折扣

    Args:
        monthly_sales: 月销售额

    Returns:
        折扣率(70/80/90)
    """
    if monthly_sales > 9000:
        return DISCOUNT_EXCELLENT
    elif monthly_sales >= 5000:
        return DISCOUNT_QUALIFIED
    else:
        return DISCOUNT_UNQUALIFIED


class CityStoreRepository:
    """市级网店数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_store_code(self, city_code: str) -> str:
        """生成网店编号: CS-{城市码}-{3位序号}"""
        if is_redis_mode():
            return await self._redis_next_store_code(city_code)
        return self._mem_next_store_code(city_code)

    def _mem_next_store_code(self, city_code: str) -> str:
        self._ensure_store()
        seq = self.store.get("_citystore_seq", 0) + 1
        self.store["_citystore_seq"] = seq
        return f"CS-{city_code}-{seq:03d}"

    async def _redis_next_store_code(self, city_code: str) -> str:
        client = await get_redis_client()
        seq = await client.incr(_k("citystore", "seq"))
        return f"CS-{city_code}-{seq:03d}"

    # ============================================================
    # 网店主表 CRUD
    # ============================================================

    async def save_store(self, store: dict) -> None:
        """保存网店(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_store(store)
        else:
            self._mem_save_store(store)

    async def get_store(self, store_code: str) -> dict | None:
        """按网店编号查询"""
        if is_redis_mode():
            return await self._redis_get_store(store_code)
        return self._mem_get_store(store_code)

    async def get_by_city(self, city_code: str) -> dict | None:
        """按城市码查询(一城一店校验)"""
        if is_redis_mode():
            return await self._redis_get_by_city(city_code)
        return self._mem_get_by_city(city_code)

    async def get_by_member(self, member_id: int) -> dict | None:
        """按会员ID查询(防止重复开店)"""
        if is_redis_mode():
            return await self._redis_get_by_member(member_id)
        return self._mem_get_by_member(member_id)

    async def list_stores(self, member_id: int = None, status: int = None,
                          limit: int = 50) -> list[dict]:
        """查询网店列表(支持按会员/状态筛选)"""
        if is_redis_mode():
            return await self._redis_list_stores(member_id, status, limit)
        return self._mem_list_stores(member_id, status, limit)

    async def list_history_stores_by_member(self, member_id: int) -> list[dict]:
        """查询会员全部历史网店(含已取消, 冷静期校验用)

        注意: member_index 单值索引会被新店覆盖, 须全量扫描过滤。
        """
        if is_redis_mode():
            return await self._redis_list_history_stores_by_member(member_id)
        self._ensure_store()
        return [s for s in self.store["city_stores"].values()
                if s.get("memberId") == member_id]

    async def _redis_list_history_stores_by_member(
            self, member_id: int) -> list[dict]:
        client = await get_redis_client()
        all_data = await client.hgetall(_k("citystore", "stores"))
        return [json.loads(v) for v in all_data.values()
                if json.loads(v).get("memberId") == member_id]

    async def list_occupied_cities(self) -> list[str]:
        """查询已被独占的城市码列表"""
        if is_redis_mode():
            return await self._redis_list_occupied_cities()
        return self._mem_list_occupied_cities()

    # ============================================================
    # 月度考核 CRUD
    # ============================================================

    async def save_assessment(self, assessment: dict) -> None:
        """保存月度考核(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_assessment(assessment)
        else:
            self._mem_save_assessment(assessment)

    async def get_assessment(self, store_code: str, month: str) -> dict | None:
        """按网店+月份查询考核"""
        if is_redis_mode():
            return await self._redis_get_assessment(store_code, month)
        return self._mem_get_assessment(store_code, month)

    async def list_assessments(self, store_code: str) -> list[dict]:
        """查询网店所有考核记录"""
        if is_redis_mode():
            return await self._redis_list_assessments(store_code)
        return self._mem_list_assessments(store_code)

    # ============================================================
    # 网店订单关联 CRUD
    # ============================================================

    async def add_order(self, order: dict) -> None:
        """新增网店订单关联"""
        if is_redis_mode():
            await self._redis_add_order(order)
        else:
            self._mem_add_order(order)

    async def list_orders(self, store_code: str, month: str = None) -> list[dict]:
        """查询网店订单(支持按月份筛选)"""
        if is_redis_mode():
            return await self._redis_list_orders(store_code, month)
        return self._mem_list_orders(store_code, month)

    async def sum_monthly_sales(self, store_code: str, month: str) -> float:
        """统计网店月度销售额"""
        if is_redis_mode():
            return await self._redis_sum_monthly_sales(store_code, month)
        return self._mem_sum_monthly_sales(store_code, month)

    async def sum_monthly_purchase(self, store_code: str, month: str) -> float:
        """统计网店月度进货额(按订单总金额)"""
        if is_redis_mode():
            return await self._redis_sum_monthly_purchase(store_code, month)
        return self._mem_sum_monthly_purchase(store_code, month)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含市级网店模块的键(懒初始化)"""
        if "city_stores" not in self.store:
            self.store["city_stores"] = {}                       # storeCode → store
            self.store["city_store_assessments"] = {}              # storeCode → [assessment, ...]
            self.store["city_store_orders"] = {}                  # storeCode → [order, ...]
            self.store["_citystore_seq"] = 0
            self.store["_citystore_city_index"] = {}               # cityCode → storeCode
            self.store["_citystore_member_index"] = {}              # memberId → storeCode

    def _mem_save_store(self, store: dict) -> None:
        self._ensure_store()
        store_code = store["storeCode"]
        self.store["city_stores"][store_code] = store
        # 维护索引
        city_code = store.get("cityCode")
        if city_code:
            self.store["_citystore_city_index"][city_code] = store_code
        member_id = store.get("memberId")
        if member_id is not None:
            self.store["_citystore_member_index"][member_id] = store_code

    def _mem_get_store(self, store_code: str) -> dict | None:
        self._ensure_store()
        return self.store["city_stores"].get(store_code)

    def _mem_get_by_city(self, city_code: str) -> dict | None:
        self._ensure_store()
        store_code = self.store["_citystore_city_index"].get(city_code)
        if not store_code:
            return None
        store = self.store["city_stores"].get(store_code)
        # 已取消的网店不阻止重新申请(城市释放)
        if store and store.get("status") == STORE_STATUS_CANCELLED:
            return None
        return store

    def _mem_get_by_member(self, member_id: int) -> dict | None:
        self._ensure_store()
        store_code = self.store["_citystore_member_index"].get(member_id)
        if not store_code:
            return None
        store = self.store["city_stores"].get(store_code)
        # 已取消的网店不阻止重新申请
        if store and store.get("status") == STORE_STATUS_CANCELLED:
            return None
        return store

    def _mem_list_stores(self, member_id: int = None, status: int = None,
                          limit: int = 50) -> list[dict]:
        self._ensure_store()
        stores = list(self.store["city_stores"].values())
        if member_id is not None:
            stores = [s for s in stores if s.get("memberId") == member_id]
        if status is not None:
            stores = [s for s in stores if s.get("status") == status]
        stores.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return stores[:limit]

    def _mem_list_occupied_cities(self) -> list[str]:
        self._ensure_store()
        occupied = []
        for store in self.store["city_stores"].values():
            if store.get("status") != STORE_STATUS_CANCELLED:
                occupied.append(store.get("cityCode"))
        return occupied

    def _mem_save_assessment(self, assessment: dict) -> None:
        self._ensure_store()
        store_code = assessment["storeCode"]
        month = assessment["assessmentMonth"]
        if store_code not in self.store["city_store_assessments"]:
            self.store["city_store_assessments"][store_code] = {}
        self.store["city_store_assessments"][store_code][month] = assessment

    def _mem_get_assessment(self, store_code: str, month: str) -> dict | None:
        self._ensure_store()
        return self.store["city_store_assessments"].get(store_code, {}).get(month)

    def _mem_list_assessments(self, store_code: str) -> list[dict]:
        self._ensure_store()
        assessments = self.store["city_store_assessments"].get(store_code, {})
        return list(assessments.values())

    def _mem_add_order(self, order: dict) -> None:
        self._ensure_store()
        store_code = order["storeCode"]
        if store_code not in self.store["city_store_orders"]:
            self.store["city_store_orders"][store_code] = []
        self.store["city_store_orders"][store_code].append(order)

    def _mem_list_orders(self, store_code: str, month: str = None) -> list[dict]:
        self._ensure_store()
        orders = self.store["city_store_orders"].get(store_code, [])
        if month:
            # month 格式 YYYY-MM, createdAt 格式 ISO
            orders = [o for o in orders if o.get("createdAt", "").startswith(month)]
        return orders

    def _mem_sum_monthly_sales(self, store_code: str, month: str) -> float:
        self._ensure_store()
        orders = self._mem_list_orders(store_code, month)
        return sum(float(o.get("totalAmount", 0)) for o in orders)

    def _mem_sum_monthly_purchase(self, store_code: str, month: str) -> float:
        self._ensure_store()
        orders = self._mem_list_orders(store_code, month)
        # 进货额 = 订单总金额(平台向网店主的结算价)
        return sum(float(o.get("totalAmount", 0)) for o in orders)

    # ============================================================
    # Redis 模式实现
    # ============================================================

    async def _redis_save_store(self, store: dict) -> None:
        client = await get_redis_client()
        store_code = store["storeCode"]
        await client.hset(_k("citystore", "stores"), store_code,
                          json.dumps(store, ensure_ascii=False))
        city_code = store.get("cityCode")
        if city_code:
            await client.hset(_k("citystore", "city_index"), city_code, store_code)
        member_id = store.get("memberId")
        if member_id is not None:
            await client.hset(_k("citystore", "member_index"), str(member_id), store_code)

    async def _redis_get_store(self, store_code: str) -> dict | None:
        client = await get_redis_client()
        data = await client.hget(_k("citystore", "stores"), store_code)
        if not data:
            return None
        return json.loads(data)

    async def _redis_get_by_city(self, city_code: str) -> dict | None:
        client = await get_redis_client()
        store_code = await client.hget(_k("citystore", "city_index"), city_code)
        if not store_code:
            return None
        store = await self._redis_get_store(store_code)
        if store and store.get("status") == STORE_STATUS_CANCELLED:
            return None
        return store

    async def _redis_get_by_member(self, member_id: int) -> dict | None:
        client = await get_redis_client()
        store_code = await client.hget(_k("citystore", "member_index"), str(member_id))
        if not store_code:
            return None
        store = await self._redis_get_store(store_code)
        if store and store.get("status") == STORE_STATUS_CANCELLED:
            return None
        return store

    async def _redis_list_stores(self, member_id: int = None, status: int = None,
                                  limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        if member_id is not None:
            store_code = await client.hget(_k("citystore", "member_index"), str(member_id))
            if not store_code:
                return []
            data = await client.hget(_k("citystore", "stores"), store_code)
            stores = [json.loads(data)] if data else []
        else:
            all_data = await client.hgetall(_k("citystore", "stores"))
            stores = [json.loads(v) for v in all_data.values()]
        if status is not None:
            stores = [s for s in stores if s.get("status") == status]
        stores.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return stores[:limit]

    async def _redis_list_occupied_cities(self) -> list[str]:
        stores = await self._redis_list_stores(limit=10000)
        return [s["cityCode"] for s in stores if s.get("status") != STORE_STATUS_CANCELLED]

    async def _redis_save_assessment(self, assessment: dict) -> None:
        client = await get_redis_client()
        store_code = assessment["storeCode"]
        month = assessment["assessmentMonth"]
        key = _k("citystore", "assessment", store_code)
        await client.hset(key, month, json.dumps(assessment, ensure_ascii=False))

    async def _redis_get_assessment(self, store_code: str, month: str) -> dict | None:
        client = await get_redis_client()
        data = await client.hget(_k("citystore", "assessment", store_code), month)
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_assessments(self, store_code: str) -> list[dict]:
        client = await get_redis_client()
        all_data = await client.hgetall(_k("citystore", "assessment", store_code))
        return [json.loads(v) for v in all_data.values()]

    async def _redis_add_order(self, order: dict) -> None:
        client = await get_redis_client()
        store_code = order["storeCode"]
        # 生成自增 ID
        order_id = await client.incr(_k("citystore", "order_seq"))
        order["id"] = order_id
        await client.hset(_k("citystore", "orders", store_code), str(order_id),
                          json.dumps(order, ensure_ascii=False))

    async def _redis_list_orders(self, store_code: str, month: str = None) -> list[dict]:
        client = await get_redis_client()
        all_data = await client.hgetall(_k("citystore", "orders", store_code))
        orders = [json.loads(v) for v in all_data.values()]
        if month:
            orders = [o for o in orders if o.get("createdAt", "").startswith(month)]
        return orders

    async def _redis_sum_monthly_sales(self, store_code: str, month: str) -> float:
        orders = await self._redis_list_orders(store_code, month)
        return sum(float(o.get("totalAmount", 0)) for o in orders)

    async def _redis_sum_monthly_purchase(self, store_code: str, month: str) -> float:
        orders = await self._redis_list_orders(store_code, month)
        return sum(float(o.get("totalAmount", 0)) for o in orders)
