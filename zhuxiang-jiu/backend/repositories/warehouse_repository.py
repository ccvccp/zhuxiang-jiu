"""仓储 Repository

双模式(内存/Redis)透明切换:
    - 内存模式: 操作 _mock_store["warehouse"] 字典(slots/inbound_log/outbound_log)
    - Redis 模式:
        - slots: Hash zhuxiang:warehouse:slots (field=slot, value=productId)
        - inbound_log: List zhuxiang:warehouse:inbound_log (RPUSH JSON)
        - outbound_log: List zhuxiang:warehouse:outbound_log (RPUSH JSON)
"""

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


class WarehouseRepository:
    """仓储数据访问(双模式)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    @property
    def _warehouse(self) -> dict:
        """内存后端辅助属性(Redis 模式下不使用)"""
        return self.store["warehouse"]

    async def get_slots(self) -> dict:
        """返回所有库位映射"""
        if is_redis_mode():
            return await self._redis_get_slots()
        return self._mem_get_slots()

    async def append_inbound_log(self, log: dict) -> dict:
        """追加入库日志"""
        if is_redis_mode():
            return await self._redis_append_inbound_log(log)
        return self._mem_append_inbound_log(log)

    async def append_outbound_log(self, log: dict) -> dict:
        """追加出库日志"""
        if is_redis_mode():
            return await self._redis_append_outbound_log(log)
        return self._mem_append_outbound_log(log)

    async def count_inbound(self) -> int:
        """入库日志条数"""
        if is_redis_mode():
            return await self._redis_count_inbound()
        return self._mem_count_inbound()

    async def count_outbound(self) -> int:
        """出库日志条数"""
        if is_redis_mode():
            return await self._redis_count_outbound()
        return self._mem_count_outbound()

    async def count_inbound_before(self, count: int) -> int:
        """辅助:返回当前入库条数与给定基线的差(测试用)"""
        current = await self.count_inbound()
        return current - count

    async def count_outbound_before(self, count: int) -> int:
        """辅助:返回当前出库条数与给定基线的差(测试用)"""
        current = await self.count_outbound()
        return current - count

    # ---------- 内存后端(原逻辑, 保持不变) ----------

    def _mem_get_slots(self) -> dict:
        return self._warehouse["slots"]

    def _mem_append_inbound_log(self, log: dict) -> dict:
        self._warehouse["inbound_log"].append(log)
        return log

    def _mem_append_outbound_log(self, log: dict) -> dict:
        self._warehouse["outbound_log"].append(log)
        return log

    def _mem_count_inbound(self) -> int:
        return len(self._warehouse["inbound_log"])

    def _mem_count_outbound(self) -> int:
        return len(self._warehouse["outbound_log"])

    # ---------- Redis 后端 ----------

    async def _redis_get_slots(self) -> dict:
        client = await get_redis_client()
        return await client.hgetall(_k("warehouse", "slots"))

    async def _redis_append_inbound_log(self, log: dict) -> dict:
        import json
        client = await get_redis_client()
        await client.rpush(_k("warehouse", "inbound_log"), json.dumps(log))
        return log

    async def _redis_append_outbound_log(self, log: dict) -> dict:
        import json
        client = await get_redis_client()
        await client.rpush(_k("warehouse", "outbound_log"), json.dumps(log))
        return log

    async def _redis_count_inbound(self) -> int:
        client = await get_redis_client()
        return await client.llen(_k("warehouse", "inbound_log"))

    async def _redis_count_outbound(self) -> int:
        client = await get_redis_client()
        return await client.llen(_k("warehouse", "outbound_log"))
