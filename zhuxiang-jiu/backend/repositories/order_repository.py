"""订单 Repository

双模式(内存/Redis)透明切换:
    - 内存模式: 列表存储于 _mock_store["orders"]
    - Redis 模式: List 存储 zhuxiang:orders (RPUSH JSON)
"""

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


class OrderRepository:
    """订单数据访问(双模式)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    async def create(self, order: dict) -> str:
        """追加订单,返回 orderId"""
        if is_redis_mode():
            return await self._redis_create(order)
        return self._mem_create(order)

    async def count(self) -> int:
        """订单总数"""
        if is_redis_mode():
            return await self._redis_count()
        return self._mem_count()

    async def list_all(self) -> list[dict]:
        """列出所有订单"""
        if is_redis_mode():
            return await self._redis_list_all()
        return self._mem_list_all()

    # ---------- 内存后端(原逻辑, 保持不变) ----------

    def _mem_create(self, order: dict) -> str:
        self.store["orders"].append(order)
        return order.get("orderId")

    def _mem_count(self) -> int:
        return len(self.store["orders"])

    def _mem_list_all(self) -> list[dict]:
        return list(self.store["orders"])

    # ---------- Redis 后端 ----------

    async def _redis_create(self, order: dict) -> str:
        import json
        client = await get_redis_client()
        await client.rpush(_k("orders"), json.dumps(order))
        return order.get("orderId")

    async def _redis_count(self) -> int:
        client = await get_redis_client()
        return await client.llen(_k("orders"))

    async def _redis_list_all(self) -> list[dict]:
        import json
        client = await get_redis_client()
        raw = await client.lrange(_k("orders"), 0, -1)
        return [json.loads(item) for item in raw]
