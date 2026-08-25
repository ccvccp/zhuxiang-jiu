"""订单 Repository(扩展版)

双模式(内存/Redis)透明切换:
    - 内存模式: dict 存储于 _mock_store["orders_v2"][orderId]
    - Redis 模式: Hash zhuxiang:order:{orderId} (JSON value)
                  Set  zhuxiang:order:user:{memberId} (订单ID集合)

向后兼容: 保留 create/count/list_all 方法(CheckoutService 依赖)
新增: get_by_id / get_by_member / update_status / update_fields / list_by_status / delete

锁键: order:{orderId}(并发安全由 services 层负责)
"""

import json

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


class OrderRepository:
    """订单数据访问(双模式, 扩展版)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 向后兼容接口(CheckoutService 依赖)
    # ============================================================

    async def create(self, order: dict) -> str:
        """新增订单,返回 orderId(向后兼容)"""
        if is_redis_mode():
            return await self._redis_create(order)
        return self._mem_create(order)

    async def count(self) -> int:
        """订单总数(向后兼容)"""
        if is_redis_mode():
            return await self._redis_count()
        return self._mem_count()

    async def list_all(self) -> list[dict]:
        """列出所有订单(向后兼容)"""
        if is_redis_mode():
            return await self._redis_list_all()
        return self._mem_list_all()

    # ============================================================
    # 扩展接口
    # ============================================================

    async def get_by_id(self, order_id: str) -> dict | None:
        """按订单 ID 查询"""
        if is_redis_mode():
            return await self._redis_get_by_id(order_id)
        return self._mem_get_by_id(order_id)

    async def get_by_member(self, member_id, status: str = None) -> list[dict]:
        """按会员 ID 查询订单(可按状态筛选)"""
        if is_redis_mode():
            return await self._redis_get_by_member(member_id, status)
        return self._mem_get_by_member(member_id, status)

    async def update_status(self, order_id: str, new_status: str) -> str:
        """更新订单状态,返回旧状态

        Raises:
            KeyError: 订单不存在
        """
        if is_redis_mode():
            return await self._redis_update_status(order_id, new_status)
        return self._mem_update_status(order_id, new_status)

    async def update_fields(self, order_id: str, fields: dict) -> dict:
        """部分字段更新,返回更新后的完整订单

        Raises:
            KeyError: 订单不存在
        """
        if is_redis_mode():
            return await self._redis_update_fields(order_id, fields)
        return self._mem_update_fields(order_id, fields)

    async def list_by_status(self, status: str) -> list[dict]:
        """按状态查询订单"""
        if is_redis_mode():
            return await self._redis_list_by_status(status)
        return self._mem_list_by_status(status)

    async def save(self, order_id: str, order_data: dict) -> dict:
        """覆盖保存订单(保留 orderId)"""
        if is_redis_mode():
            return await self._redis_save(order_id, order_data)
        return self._mem_save(order_id, order_data)

    async def delete(self, order_id: str) -> None:
        """删除订单

        Raises:
            KeyError: 订单不存在
        """
        if is_redis_mode():
            return await self._redis_delete(order_id)
        return self._mem_delete(order_id)

    # ============================================================
    # 内存后端
    # ============================================================

    def _ensure_store(self):
        """确保 store 包含 orders_v2 和 orders 键(扩展版 + 向后兼容)"""
        if "orders_v2" not in self.store:
            self.store["orders_v2"] = {}
        if "orders" not in self.store:  # 向后兼容: 旧 orders list
            self.store["orders"] = []

    def _mem_create(self, order: dict) -> str:
        self._ensure_store()
        order_id = order.get("orderId") or order.get("order_id")
        # 同时写入 v2 dict 和旧 list(兼容)
        self.store["orders_v2"][order_id] = order
        self.store["orders"].append(order)  # 向后兼容
        return order_id

    def _mem_count(self) -> int:
        self._ensure_store()
        return len(self.store["orders_v2"])

    def _mem_list_all(self) -> list[dict]:
        self._ensure_store()
        return list(self.store["orders_v2"].values())

    def _mem_get_by_id(self, order_id: str) -> dict | None:
        self._ensure_store()
        return self.store["orders_v2"].get(order_id)

    def _mem_get_by_member(self, member_id, status: str = None) -> list[dict]:
        self._ensure_store()
        result = []
        for order in self.store["orders_v2"].values():
            if order.get("memberId") == member_id:
                if status is None or order.get("status") == status:
                    result.append(order)
        # 按创建时间倒序
        result.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        return result

    def _mem_update_status(self, order_id: str, new_status: str) -> str:
        self._ensure_store()
        order = self.store["orders_v2"].get(order_id)
        if not order:
            raise KeyError(order_id)
        old_status = order.get("status")
        order["status"] = new_status
        return old_status

    def _mem_update_fields(self, order_id: str, fields: dict) -> dict:
        self._ensure_store()
        order = self.store["orders_v2"].get(order_id)
        if not order:
            raise KeyError(order_id)
        order.update(fields)
        return order

    def _mem_list_by_status(self, status: str) -> list[dict]:
        self._ensure_store()
        return [o for o in self.store["orders_v2"].values() if o.get("status") == status]

    def _mem_save(self, order_id: str, order_data: dict) -> dict:
        self._ensure_store()
        order_data["orderId"] = order_id
        self.store["orders_v2"][order_id] = order_data
        return order_data

    def _mem_delete(self, order_id: str) -> None:
        self._ensure_store()
        if order_id not in self.store["orders_v2"]:
            raise KeyError(order_id)
        del self.store["orders_v2"][order_id]

    # ============================================================
    # Redis 后端
    # ============================================================

    async def _redis_create(self, order: dict) -> str:
        client = await get_redis_client()
        order_id = order.get("orderId") or order.get("order_id")
        key = _k("order", order_id)
        await client.set(key, json.dumps(order, ensure_ascii=False))
        # 用户订单索引
        member_id = order.get("memberId")
        if member_id is not None:
            await client.sadd(_k("order", "user", member_id), order_id)
        return order_id

    async def _redis_count(self) -> int:
        client = await get_redis_client()
        keys = await client.keys(_k("order", "*"))
        # 排除 user: 索引
        count = sum(1 for k in keys if ":user:" not in k)
        return count

    async def _redis_list_all(self) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("order", "*"))
        result = []
        for key in keys:
            if ":user:" in key:
                continue
            raw = await client.get(key)
            if raw:
                result.append(json.loads(raw))
        return result

    async def _redis_get_by_id(self, order_id: str) -> dict | None:
        client = await get_redis_client()
        raw = await client.get(_k("order", order_id))
        if not raw:
            return None
        return json.loads(raw)

    async def _redis_get_by_member(self, member_id, status: str = None) -> list[dict]:
        client = await get_redis_client()
        order_ids = await client.smembers(_k("order", "user", member_id))
        result = []
        for oid in order_ids:
            raw = await client.get(_k("order", oid))
            if raw:
                order = json.loads(raw)
                if status is None or order.get("status") == status:
                    result.append(order)
        result.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        return result

    async def _redis_update_status(self, order_id: str, new_status: str) -> str:
        client = await get_redis_client()
        order = await self._redis_get_by_id(order_id)
        if not order:
            raise KeyError(order_id)
        old_status = order.get("status")
        order["status"] = new_status
        await client.set(_k("order", order_id), json.dumps(order, ensure_ascii=False))
        return old_status

    async def _redis_update_fields(self, order_id: str, fields: dict) -> dict:
        client = await get_redis_client()
        order = await self._redis_get_by_id(order_id)
        if not order:
            raise KeyError(order_id)
        order.update(fields)
        await client.set(_k("order", order_id), json.dumps(order, ensure_ascii=False))
        return order

    async def _redis_list_by_status(self, status: str) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("order", "*"))
        result = []
        for key in keys:
            if ":user:" in key:
                continue
            raw = await client.get(key)
            if raw:
                order = json.loads(raw)
                if order.get("status") == status:
                    result.append(order)
        return result

    async def _redis_save(self, order_id: str, order_data: dict) -> dict:
        client = await get_redis_client()
        order_data["orderId"] = order_id
        await client.set(_k("order", order_id), json.dumps(order_data, ensure_ascii=False))
        member_id = order_data.get("memberId")
        if member_id is not None:
            await client.sadd(_k("order", "user", member_id), order_id)
        return order_data

    async def _redis_delete(self, order_id: str) -> None:
        client = await get_redis_client()
        key = _k("order", order_id)
        raw = await client.get(key)
        if not raw:
            raise KeyError(order_id)
        order = json.loads(raw)
        # 删除用户索引
        member_id = order.get("memberId")
        if member_id is not None:
            await client.srem(_k("order", "user", member_id), order_id)
        await client.delete(key)
