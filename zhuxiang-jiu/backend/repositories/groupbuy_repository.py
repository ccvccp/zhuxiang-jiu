"""团购模块数据访问层(双模式: 内存 + Redis)

表清单:
    P0: group_buy_orders(团购订单) + group_buy_items(订单明细) + group_buy_audit(审核记录)
    P1: group_buy_custom(定制需求) + group_buy_installment(分期付款)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 序列号: TG+时间戳+序号(orderNo 唯一)
    - 状态机: pending → approved → paying → in_production → shipped → completed
    - 锁键: 通过 core.locks.get_lock() 跨进程互斥(锁键定义在 service 层)
"""

import json
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 团购订单状态机
# ============================================================

ORDER_STATUS_PENDING = "pending"              # 待审核
ORDER_STATUS_APPROVED = "approved"             # 审核通过
ORDER_STATUS_PAYING = "paying"                 # 待付款
ORDER_STATUS_IN_PRODUCTION = "in_production"   # 生产中
ORDER_STATUS_SHIPPED = "shipped"               # 已发货
ORDER_STATUS_COMPLETED = "completed"           # 已完成
ORDER_STATUS_CANCELLED = "cancelled"           # 已取消
ORDER_STATUS_REJECTED = "rejected"             # 已驳回

ORDER_STATUS_NAMES = {
    ORDER_STATUS_PENDING: "待审核",
    ORDER_STATUS_APPROVED: "审核通过",
    ORDER_STATUS_PAYING: "待付款",
    ORDER_STATUS_IN_PRODUCTION: "生产中",
    ORDER_STATUS_SHIPPED: "已发货",
    ORDER_STATUS_COMPLETED: "已完成",
    ORDER_STATUS_CANCELLED: "已取消",
    ORDER_STATUS_REJECTED: "已驳回",
}

# 合法状态流转(当前状态 → 可流转到的状态集合)
ORDER_STATUS_FLOW = {
    ORDER_STATUS_PENDING: {ORDER_STATUS_APPROVED, ORDER_STATUS_REJECTED, ORDER_STATUS_CANCELLED},
    ORDER_STATUS_APPROVED: {ORDER_STATUS_PAYING, ORDER_STATUS_CANCELLED},
    ORDER_STATUS_PAYING: {ORDER_STATUS_IN_PRODUCTION, ORDER_STATUS_CANCELLED},
    ORDER_STATUS_IN_PRODUCTION: {ORDER_STATUS_SHIPPED},
    ORDER_STATUS_SHIPPED: {ORDER_STATUS_COMPLETED},
    ORDER_STATUS_COMPLETED: set(),   # 终态
    ORDER_STATUS_REJECTED: set(),     # 终态
    ORDER_STATUS_CANCELLED: set(),   # 终态
}

# 活跃状态(未完结, 允许取消)
ORDER_ACTIVE_STATUSES = {
    ORDER_STATUS_PENDING, ORDER_STATUS_APPROVED, ORDER_STATUS_PAYING,
}

# 终态
ORDER_TERMINAL_STATUSES = {
    ORDER_STATUS_COMPLETED, ORDER_STATUS_CANCELLED, ORDER_STATUS_REJECTED,
}


# ============================================================
# 审核结果
# ============================================================

AUDIT_RESULT_APPROVED = "approved"
AUDIT_RESULT_REJECTED = "rejected"

AUDIT_LEVEL_STAFF = "staff"                       # 专员
AUDIT_LEVEL_SUPERVISOR = "supervisor"             # 主管
AUDIT_LEVEL_DIRECTOR = "director"                 # 总监
AUDIT_LEVEL_GENERAL_MANAGER = "general_manager"   # 总经理


# ============================================================
# 阶梯折扣
# ============================================================

TIER_T1 = "T1"
TIER_T2 = "T2"
TIER_T3 = "T3"
TIER_T4 = "T4"

# 阶梯定义: (最小金额, 最大金额, 折扣率)
# 最大金额 None 表示无上限
TIER_DEFINITIONS = [
    (TIER_T1, 50000, 99999, 0.80),
    (TIER_T2, 100000, 199999, 0.75),
    (TIER_T3, 200000, 499999, 0.72),
    (TIER_T4, 500000, None, 0.70),
]

TIER_NAMES = {
    TIER_T1: "T1(8折)",
    TIER_T2: "T2(7.5折)",
    TIER_T3: "T3(7.2折)",
    TIER_T4: "T4(7折)",
}

# 团购类型
GROUP_TYPE_ENTERPRISE = "enterprise"   # 企业团购
GROUP_TYPE_WEDDING = "wedding"         # 婚宴团购
GROUP_TYPE_FESTIVAL = "festival"       # 节日团购
GROUP_TYPE_CUSTOM = "custom"           # 定制团购

GROUP_TYPE_NAMES = {
    GROUP_TYPE_ENTERPRISE: "企业团购",
    GROUP_TYPE_WEDDING: "婚宴团购",
    GROUP_TYPE_FESTIVAL: "节日团购",
    GROUP_TYPE_CUSTOM: "定制团购",
}

SUPPORTED_GROUP_TYPES = {GROUP_TYPE_ENTERPRISE, GROUP_TYPE_WEDDING, GROUP_TYPE_FESTIVAL, GROUP_TYPE_CUSTOM}

# 团购门槛
MIN_AMOUNT = 50000.0          # 单笔最低金额(企业/节日/定制)
MIN_AMOUNT_WEDDING = 30000.0  # 婚宴团购门槛略低
MIN_AMOUNT_CUSTOM = 100000.0  # 定制团购门槛
MIN_QUANTITY = 50             # 最低数量
MAX_AMOUNT = 500000.0         # 单次上限(超出需额外审批)
ANNUAL_LIMIT = 2000000.0      # 年度限额
MONTHLY_FREQ_LIMIT = 4        # 月度频次限制


def match_tier(original_total: float) -> tuple:
    """根据原价合计匹配阶梯

    Returns:
        (tier, discount) 例如 ("T1", 0.80)
    """
    for tier, min_amt, max_amt, discount in TIER_DEFINITIONS:
        if original_total >= min_amt and (max_amt is None or original_total <= max_amt):
            return tier, discount
    return None, None


class GroupBuyRepository:
    """团购模块数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_order_no(self) -> str:
        """生成团购订单号: TG + YYYYMMDD + 4位序号"""
        if is_redis_mode():
            return await self._redis_next_order_no()
        return self._mem_next_order_no()

    def _mem_next_order_no(self) -> str:
        """内存模式: 基于 store 计数器生成"""
        self._ensure_store()
        seq = self.store.get("_groupbuy_seq", 0) + 1
        self.store["_groupbuy_seq"] = seq
        from datetime import datetime
        date_str = datetime.now().strftime("%Y%m%d")
        return f"TG{date_str}{seq:04d}"

    async def _redis_next_order_no(self) -> str:
        """Redis 模式: INCR 原子自增"""
        client = await get_redis_client()
        seq = await client.incr(_k("groupbuy", "seq"))
        from datetime import datetime
        date_str = datetime.now().strftime("%Y%m%d")
        return f"TG{date_str}{seq:04d}"

    # ============================================================
    # 团购订单 CRUD
    # ============================================================

    async def save_order(self, order: dict) -> None:
        """保存团购订单(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_order(order)
        else:
            self._mem_save_order(order)

    async def get_order(self, order_no: str) -> Optional[dict]:
        """按订单号查询团购订单"""
        if is_redis_mode():
            return await self._redis_get_order(order_no)
        return self._mem_get_order(order_no)

    async def list_orders(self, user_id: int = None, status: str = None,
                          limit: int = 50) -> list[dict]:
        """查询团购订单列表(支持按用户/状态筛选)"""
        if is_redis_mode():
            return await self._redis_list_orders(user_id, status, limit)
        return self._mem_list_orders(user_id, status, limit)

    async def find_active_by_user(self, user_id: int) -> list[dict]:
        """查询用户的活跃订单(未完结)"""
        if is_redis_mode():
            return await self._redis_find_active_by_user(user_id)
        return self._mem_find_active_by_user(user_id)

    async def count_user_orders_in_period(self, user_id: int,
                                           start_date: str, end_date: str) -> int:
        """统计用户在指定时间段内的团购订单数(用于频次限制)"""
        if is_redis_mode():
            return await self._redis_count_user_orders_in_period(user_id, start_date, end_date)
        return self._mem_count_user_orders_in_period(user_id, start_date, end_date)

    async def sum_user_annual_amount(self, user_id: int, year: int) -> float:
        """统计用户年度团购总额(用于年度限额校验)"""
        if is_redis_mode():
            return await self._redis_sum_user_annual_amount(user_id, year)
        return self._mem_sum_user_annual_amount(user_id, year)

    # ============================================================
    # 团购订单明细
    # ============================================================

    async def save_items(self, order_no: str, items: list[dict]) -> None:
        """保存订单明细(覆盖式)"""
        if is_redis_mode():
            await self._redis_save_items(order_no, items)
        else:
            self._mem_save_items(order_no, items)

    async def get_items(self, order_no: str) -> list[dict]:
        """按订单号查询明细"""
        if is_redis_mode():
            return await self._redis_get_items(order_no)
        return self._mem_get_items(order_no)

    # ============================================================
    # 审核记录
    # ============================================================

    async def add_audit(self, audit: dict) -> None:
        """新增审核记录"""
        if is_redis_mode():
            await self._redis_add_audit(audit)
        else:
            self._mem_add_audit(audit)

    async def list_audits(self, order_no: str) -> list[dict]:
        """按订单号查询审核流水"""
        if is_redis_mode():
            return await self._redis_list_audits(order_no)
        return self._mem_list_audits(order_no)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含团购模块的键(懒初始化)"""
        if "group_buy_orders" not in self.store:
            self.store["group_buy_orders"] = {}          # orderNo → order
            self.store["group_buy_items"] = {}            # orderNo → [item, ...]
            self.store["group_buy_audit"] = {}             # orderNo → [audit, ...]
            self.store["_groupbuy_seq"] = 0
            self.store["_groupbuy_user_index"] = {}        # userId → set(orderNo)

    def _mem_save_order(self, order: dict) -> None:
        self._ensure_store()
        order_no = order["orderNo"]
        self.store["group_buy_orders"][order_no] = order
        # 维护用户索引
        user_id = order.get("userId")
        if user_id is not None:
            if user_id not in self.store["_groupbuy_user_index"]:
                self.store["_groupbuy_user_index"][user_id] = set()
            self.store["_groupbuy_user_index"][user_id].add(order_no)

    def _mem_get_order(self, order_no: str) -> Optional[dict]:
        self._ensure_store()
        return self.store["group_buy_orders"].get(order_no)

    def _mem_list_orders(self, user_id: int = None, status: str = None,
                         limit: int = 50) -> list[dict]:
        self._ensure_store()
        orders = list(self.store["group_buy_orders"].values())
        if user_id is not None:
            orders = [o for o in orders if o.get("userId") == user_id]
        if status:
            orders = [o for o in orders if o.get("status") == status]
        # 按申请时间降序
        orders.sort(key=lambda o: o.get("applyTime", ""), reverse=True)
        return orders[:limit]

    def _mem_find_active_by_user(self, user_id: int) -> list[dict]:
        self._ensure_store()
        orders = self.store["group_buy_orders"].values()
        return [o for o in orders
                if o.get("userId") == user_id
                and o.get("status") in ORDER_ACTIVE_STATUSES]

    def _mem_count_user_orders_in_period(self, user_id: int,
                                           start_date: str, end_date: str) -> int:
        self._ensure_store()
        count = 0
        for o in self.store["group_buy_orders"].values():
            if o.get("userId") != user_id:
                continue
            apply_time = o.get("applyTime", "")
            if apply_time and start_date <= apply_time[:10] <= end_date:
                count += 1
        return count

    def _mem_sum_user_annual_amount(self, user_id: int, year: int) -> float:
        self._ensure_store()
        total = 0.0
        year_str = str(year)
        for o in self.store["group_buy_orders"].values():
            if o.get("userId") != user_id:
                continue
            apply_time = o.get("applyTime", "")
            if apply_time and apply_time[:4] == year_str:
                # 仅统计非取消/驳回的订单
                if o.get("status") not in (ORDER_STATUS_CANCELLED, ORDER_STATUS_REJECTED):
                    total += float(o.get("groupPrice", 0))
        return total

    def _mem_save_items(self, order_no: str, items: list[dict]) -> None:
        self._ensure_store()
        self.store["group_buy_items"][order_no] = items

    def _mem_get_items(self, order_no: str) -> list[dict]:
        self._ensure_store()
        return self.store["group_buy_items"].get(order_no, [])

    def _mem_add_audit(self, audit: dict) -> None:
        self._ensure_store()
        order_no = audit["orderNo"]
        if order_no not in self.store["group_buy_audit"]:
            self.store["group_buy_audit"][order_no] = []
        self.store["group_buy_audit"][order_no].append(audit)

    def _mem_list_audits(self, order_no: str) -> list[dict]:
        self._ensure_store()
        return self.store["group_buy_audit"].get(order_no, [])

    # ============================================================
    # Redis 模式实现
    # ============================================================

    async def _redis_save_order(self, order: dict) -> None:
        client = await get_redis_client()
        order_no = order["orderNo"]
        await client.hset(_k("groupbuy", "orders"), order_no, json.dumps(order, ensure_ascii=False))
        # 维护用户索引
        user_id = order.get("userId")
        if user_id is not None:
            await client.sadd(_k("groupbuy", "user_index", str(user_id)), order_no)

    async def _redis_get_order(self, order_no: str) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hget(_k("groupbuy", "orders"), order_no)
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_orders(self, user_id: int = None, status: str = None,
                                  limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        if user_id is not None:
            # 通过用户索引查询
            order_nos = await client.smembers(_k("groupbuy", "user_index", str(user_id)))
            orders = []
            for no in order_nos:
                data = await client.hget(_k("groupbuy", "orders"), no)
                if data:
                    orders.append(json.loads(data))
        else:
            # 全量查询
            all_data = await client.hgetall(_k("groupbuy", "orders"))
            orders = [json.loads(v) for v in all_data.values()]
        if status:
            orders = [o for o in orders if o.get("status") == status]
        orders.sort(key=lambda o: o.get("applyTime", ""), reverse=True)
        return orders[:limit]

    async def _redis_find_active_by_user(self, user_id: int) -> list[dict]:
        orders = await self._redis_list_orders(user_id=user_id, limit=1000)
        return [o for o in orders if o.get("status") in ORDER_ACTIVE_STATUSES]

    async def _redis_count_user_orders_in_period(self, user_id: int,
                                                    start_date: str, end_date: str) -> int:
        orders = await self._redis_list_orders(user_id=user_id, limit=10000)
        count = 0
        for o in orders:
            apply_time = o.get("applyTime", "")
            if apply_time and start_date <= apply_time[:10] <= end_date:
                count += 1
        return count

    async def _redis_sum_user_annual_amount(self, user_id: int, year: int) -> float:
        orders = await self._redis_list_orders(user_id=user_id, limit=10000)
        total = 0.0
        year_str = str(year)
        for o in orders:
            apply_time = o.get("applyTime", "")
            if apply_time and apply_time[:4] == year_str:
                if o.get("status") not in (ORDER_STATUS_CANCELLED, ORDER_STATUS_REJECTED):
                    total += float(o.get("groupPrice", 0))
        return total

    async def _redis_save_items(self, order_no: str, items: list[dict]) -> None:
        client = await get_redis_client()
        await client.hset(_k("groupbuy", "items"), order_no,
                          json.dumps(items, ensure_ascii=False))

    async def _redis_get_items(self, order_no: str) -> list[dict]:
        client = await get_redis_client()
        data = await client.hget(_k("groupbuy", "items"), order_no)
        if not data:
            return []
        return json.loads(data)

    async def _redis_add_audit(self, audit: dict) -> None:
        client = await get_redis_client()
        order_no = audit["orderNo"]
        # 读取现有审核列表
        existing = await self._redis_list_audits(order_no)
        existing.append(audit)
        await client.hset(_k("groupbuy", "audit"), order_no,
                          json.dumps(existing, ensure_ascii=False))

    async def _redis_list_audits(self, order_no: str) -> list[dict]:
        client = await get_redis_client()
        data = await client.hget(_k("groupbuy", "audit"), order_no)
        if not data:
            return []
        return json.loads(data)
