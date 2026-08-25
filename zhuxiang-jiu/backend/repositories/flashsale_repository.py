"""限时秒杀模块数据访问层(双模式: 内存 + Redis)

表清单:
    flash_sessions  秒杀场次(draft→published→cancelled, 运行时状态由时间推导)
    flash_items     秒杀商品(场次×产品, 秒杀价/库存/限购)
    flash_orders    秒杀订单(pending_payment/paid/cancelled)
    flash_settings  参数配置单例(管理端可改)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 序列号: 内存计数器 / Redis INCR
    - 库存扣减/回补的并发安全由 services 层通过 core.locks.get_lock 保证
"""

import json
from datetime import datetime, UTC

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 常量与默认参数
# ============================================================

# 场次存储状态
SESSION_STATUS_DRAFT = "draft"
SESSION_STATUS_PUBLISHED = "published"
SESSION_STATUS_CANCELLED = "cancelled"

# 运行时状态(由时间推导, 不落库)
RUNTIME_NOT_STARTED = "not_started"
RUNTIME_IN_PROGRESS = "in_progress"
RUNTIME_ENDED = "ended"

# 商品状态
ITEM_STATUS_ACTIVE = "active"
ITEM_STATUS_REMOVED = "removed"

# 订单状态
ORDER_STATUS_PENDING = "pending_payment"
ORDER_STATUS_PAID = "paid"
ORDER_STATUS_CANCELLED = "cancelled"

# 默认参数(管理端可修改)
DEFAULT_SETTINGS = {
    "enabled": True,
    "minRegisterHours": 0,      # 抢购需注册满 N 小时(0=不限制)
    "minMemberLevel": 0,        # 抢购需会员等级 >= N(0=不限制)
    "orderExpireMinutes": 15,   # 待支付订单超时分钟数
    "maxQuantityPerOrder": 5,   # 单笔订单最大数量
    "updatedAt": "",
    "updatedBy": "",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_iso(value: str) -> datetime:
    """解析 ISO8601 时间; 无时区视为 UTC"""
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class FlashSaleRepository:
    """限时秒杀模块数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 存储初始化
    # ============================================================

    def _ensure_store(self):
        for key in ("flash_sessions", "flash_items", "flash_orders", "flash_settings"):
            self.store.setdefault(key, {})

    # ============================================================
    # 序列号生成(带日期的编号: FS20260822-001)
    # ============================================================

    async def next_session_id(self) -> str:
        return f"FS{datetime.now(UTC).strftime('%Y%m%d')}-{await self._next_seq('session')}"

    async def next_item_id(self) -> str:
        return f"FI{datetime.now(UTC).strftime('%Y%m%d')}-{await self._next_seq('item')}"

    async def next_order_no(self) -> str:
        return f"FO{datetime.now(UTC).strftime('%Y%m%d')}-{await self._next_seq('order')}"

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _next_seq(self, entity: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("flash", entity, "seq"))
        return self._mem_next_id(f"_flash_{entity}_seq")

    # ============================================================
    # 场次 CRUD
    # ============================================================

    async def save_session(self, session: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("flash", "session", session["sessionId"]),
                              mapping=self._to_redis(session))
            await client.sadd(_k("flash", "sessions"), session["sessionId"])
            return session
        self._ensure_store()
        self.store["flash_sessions"][session["sessionId"]] = session
        return session

    async def get_session(self, session_id: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("flash", "session", session_id))
            return self._from_redis(data) if data else None
        self._ensure_store()
        return self.store["flash_sessions"].get(session_id)

    async def list_sessions(self) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.smembers(_k("flash", "sessions"))
            sessions = []
            for sid in ids:
                data = await client.hgetall(_k("flash", "session", sid))
                if data:
                    sessions.append(self._from_redis(data))
            sessions.sort(key=lambda s: s.get("startTime", ""), reverse=True)
            return sessions
        self._ensure_store()
        sessions = list(self.store["flash_sessions"].values())
        sessions.sort(key=lambda s: s.get("startTime", ""), reverse=True)
        return sessions

    async def update_session_fields(self, session_id: str, fields: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("flash", "session", session_id),
                              mapping=self._to_redis(fields))
            return
        self._ensure_store()
        session = self.store["flash_sessions"].get(session_id)
        if session:
            session.update(fields)

    # ============================================================
    # 秒杀商品 CRUD
    # ============================================================

    async def save_item(self, item: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("flash", "item", item["itemId"]),
                              mapping=self._to_redis(item))
            await client.sadd(_k("flash", "session_items", item["sessionId"]),
                              item["itemId"])
            return item
        self._ensure_store()
        self.store["flash_items"][item["itemId"]] = item
        return item

    async def get_item(self, item_id: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("flash", "item", item_id))
            return self._from_redis(data) if data else None
        self._ensure_store()
        return self.store["flash_items"].get(item_id)

    async def list_items_by_session(self, session_id: str) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.smembers(_k("flash", "session_items", session_id))
            items = []
            for iid in ids:
                data = await client.hgetall(_k("flash", "item", iid))
                if data:
                    items.append(self._from_redis(data))
            items.sort(key=lambda i: i.get("createdAt", ""))
            return items
        self._ensure_store()
        items = [i for i in self.store["flash_items"].values()
                 if i.get("sessionId") == session_id]
        items.sort(key=lambda i: i.get("createdAt", ""))
        return items

    async def update_item_fields(self, item_id: str, fields: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("flash", "item", item_id),
                              mapping=self._to_redis(fields))
            return
        self._ensure_store()
        item = self.store["flash_items"].get(item_id)
        if item:
            item.update(fields)

    # ============================================================
    # 秒杀订单 CRUD
    # ============================================================

    async def save_order(self, order: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("flash", "order", order["orderNo"]),
                              mapping=self._to_redis(order))
            await client.sadd(_k("flash", "member_orders", order["memberId"]),
                              order["orderNo"])
            await client.sadd(_k("flash", "orders"), order["orderNo"])
            return order
        self._ensure_store()
        self.store["flash_orders"][order["orderNo"]] = order
        return order

    async def get_order(self, order_no: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("flash", "order", order_no))
            return self._from_redis(data) if data else None
        self._ensure_store()
        return self.store["flash_orders"].get(order_no)

    async def update_order_fields(self, order_no: str, fields: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("flash", "order", order_no),
                              mapping=self._to_redis(fields))
            return
        self._ensure_store()
        order = self.store["flash_orders"].get(order_no)
        if order:
            order.update(fields)

    async def list_orders_by_member(self, member_id: int) -> list[dict]:
        """按会员查询订单(倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            nos = await client.smembers(_k("flash", "member_orders", member_id))
            orders = []
            for no in nos:
                data = await client.hgetall(_k("flash", "order", no))
                if data:
                    orders.append(self._from_redis(data))
        else:
            self._ensure_store()
            orders = [o for o in self.store["flash_orders"].values()
                      if o.get("memberId") == member_id]
        orders.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        return orders

    async def list_orders_by_item(self, item_id: str,
                                  statuses: tuple = ()) -> list[dict]:
        """按秒杀商品查询订单(可按状态过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            nos = await client.smembers(_k("flash", "orders"))
            orders = []
            for no in nos:
                data = await client.hgetall(_k("flash", "order", no))
                if data:
                    orders.append(self._from_redis(data))
        else:
            self._ensure_store()
            orders = list(self.store["flash_orders"].values())
        if statuses:
            orders = [o for o in orders if o.get("status") in statuses]
        orders = [o for o in orders if o.get("itemId") == item_id]
        orders.sort(key=lambda o: o.get("createdAt", ""))
        return orders

    async def list_pending_orders(self) -> list[dict]:
        """全部待支付订单"""
        if is_redis_mode():
            client = await get_redis_client()
            nos = await client.smembers(_k("flash", "orders"))
            orders = []
            for no in nos:
                data = await client.hgetall(_k("flash", "order", no))
                if data and data.get("status") == ORDER_STATUS_PENDING:
                    orders.append(self._from_redis(data))
        else:
            self._ensure_store()
            orders = [o for o in self.store["flash_orders"].values()
                      if o.get("status") == ORDER_STATUS_PENDING]
        orders.sort(key=lambda o: o.get("createdAt", ""))
        return orders

    async def list_all_orders(self) -> list[dict]:
        """全部订单(统计用)"""
        if is_redis_mode():
            client = await get_redis_client()
            nos = await client.smembers(_k("flash", "orders"))
            orders = []
            for no in nos:
                data = await client.hgetall(_k("flash", "order", no))
                if data:
                    orders.append(self._from_redis(data))
        else:
            self._ensure_store()
            orders = list(self.store["flash_orders"].values())
        orders.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        return orders

    # ============================================================
    # 参数配置(单例)
    # ============================================================

    async def get_settings(self) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("flash", "settings"))
            if not data:
                settings = dict(DEFAULT_SETTINGS)
                await client.hset(_k("flash", "settings"),
                                  mapping=self._to_redis(settings))
                return settings
            return self._from_redis(data)
        self._ensure_store()
        settings = self.store["flash_settings"].get("singleton")
        if not settings:
            settings = dict(DEFAULT_SETTINGS)
            self.store["flash_settings"]["singleton"] = settings
        return settings

    async def save_settings(self, settings: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("flash", "settings"),
                              mapping=self._to_redis(settings))
            return settings
        self._ensure_store()
        self.store["flash_settings"]["singleton"] = settings
        return settings

    # ============================================================
    # Redis 序列化辅助
    # ============================================================

    @staticmethod
    def _to_redis(data: dict) -> dict:
        """dict → Redis Hash mapping(容器类型转 JSON 字符串)"""
        return {
            k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
            for k, v in data.items()
        }

    @staticmethod
    def _from_redis(data: dict) -> dict:
        """Redis Hash mapping → dict(尝试 JSON 反序列化容器字段)"""
        result = {}
        for k, v in data.items():
            if isinstance(v, str) and v[:1] in ("{", "["):
                try:
                    result[k] = json.loads(v)
                    continue
                except (json.JSONDecodeError, ValueError):
                    pass
            result[k] = v
        return result
