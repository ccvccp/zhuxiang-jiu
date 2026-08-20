"""库存 Repository

双模式(内存/Redis)透明切换:
    - 内存模式: 直接操作 _mock_store["inventory"] 字典
    - Redis 模式: Hash 存储 zhuxiang:inventory:{productId}

锁键: stock:{productId}(deduct/restock 共享, 由 services 层负责)
"""

from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


class InventoryRepository:
    """库存数据访问(双模式, product_id 内部统一转 str)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    async def get(self, product_id) -> Optional[dict]:
        """按 product_id 查询库存"""
        if is_redis_mode():
            return await self._redis_get(product_id)
        return self._mem_get(product_id)

    async def get_stock(self, product_id) -> int:
        """查询库存量,产品不存在返回 0"""
        if is_redis_mode():
            return await self._redis_get_stock(product_id)
        return self._mem_get_stock(product_id)

    async def set_stock(self, product_id, stock: int) -> int:
        """直接设置库存量(测试辅助:模拟初始库存)"""
        if is_redis_mode():
            return await self._redis_set_stock(product_id, stock)
        return self._mem_set_stock(product_id, stock)

    async def deduct(self, product_id, quantity: int) -> int:
        """扣减库存,返回扣减后余额

        Raises:
            KeyError: 产品不存在
            ValueError: 库存不足
        """
        if is_redis_mode():
            return await self._redis_deduct(product_id, quantity)
        return self._mem_deduct(product_id, quantity)

    async def restock(self, product_id, quantity: int) -> int:
        """回补库存,返回回补后余额

        Raises:
            KeyError: 产品不存在
        """
        if is_redis_mode():
            return await self._redis_restock(product_id, quantity)
        return self._mem_restock(product_id, quantity)

    # ---------- 内存后端(原逻辑, 保持不变) ----------

    def _mem_get(self, product_id) -> Optional[dict]:
        return self.store["inventory"].get(str(product_id))

    def _mem_get_stock(self, product_id) -> int:
        product = self.store["inventory"].get(str(product_id))
        return product["stock"] if product else 0

    def _mem_set_stock(self, product_id, stock: int) -> int:
        key = str(product_id)
        if key not in self.store["inventory"]:
            self.store["inventory"][key] = {"stock": 0, "reserved": 0}
        self.store["inventory"][key]["stock"] = stock
        return stock

    def _mem_deduct(self, product_id, quantity: int) -> int:
        key = str(product_id)
        product = self.store["inventory"].get(key)
        if not product:
            raise KeyError(product_id)
        if product["stock"] < quantity:
            raise ValueError(f"库存不足: 当前 {product['stock']}, 需要 {quantity}")
        product["stock"] -= quantity
        return product["stock"]

    def _mem_restock(self, product_id, quantity: int) -> int:
        key = str(product_id)
        product = self.store["inventory"].get(key)
        if not product:
            raise KeyError(product_id)
        product["stock"] += quantity
        return product["stock"]

    # ---------- Redis 后端 ----------

    async def _redis_get(self, product_id) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hgetall(_k("inventory", product_id))
        if not data:
            return None
        return {
            "stock": int(data["stock"]),
            "reserved": int(data["reserved"]),
        }

    async def _redis_get_stock(self, product_id) -> int:
        client = await get_redis_client()
        stock = await client.hget(_k("inventory", product_id), "stock")
        return int(stock) if stock else 0

    async def _redis_set_stock(self, product_id, stock: int) -> int:
        client = await get_redis_client()
        key = _k("inventory", product_id)
        # 若不存在 reserved 字段, 初始化为 0
        await client.hsetnx(key, "reserved", 0)
        await client.hset(key, "stock", stock)
        return stock

    async def _redis_deduct(self, product_id, quantity: int) -> int:
        client = await get_redis_client()
        key = _k("inventory", product_id)
        if not await client.exists(key):
            raise KeyError(product_id)
        # check-then-act(锁由 services 层持有, 这里假设已加锁)
        current = int(await client.hget(key, "stock") or 0)
        if current < quantity:
            raise ValueError(f"库存不足: 当前 {current}, 需要 {quantity}")
        new_stock = await client.hincrby(key, "stock", -quantity)
        return new_stock

    async def _redis_restock(self, product_id, quantity: int) -> int:
        client = await get_redis_client()
        key = _k("inventory", product_id)
        if not await client.exists(key):
            raise KeyError(product_id)
        new_stock = await client.hincrby(key, "stock", quantity)
        return new_stock
