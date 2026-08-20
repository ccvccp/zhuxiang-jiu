"""代理商区域认领 Repository

双模式(内存/Redis)透明切换:
    - 内存模式: 字典存储于 _mock_store["shipping_claims"]
    - Redis 模式: Hash 存储 zhuxiang:shipping_claims (field=region, value=agent_id)

key: region(区域字符串)
value: agent_id
"""

import logging
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k

logger = logging.getLogger(__name__)


class ShippingClaimRepository:
    """区域认领数据访问(双模式)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    async def get_claim(self, region: str):
        """查询区域认领者,无人认领返回 None"""
        if is_redis_mode():
            return await self._redis_get_claim(region)
        return self._mem_get_claim(region)

    async def is_claimed(self, region: str) -> bool:
        """区域是否已被认领"""
        if is_redis_mode():
            return await self._redis_is_claimed(region)
        return self._mem_is_claimed(region)

    async def set_claim(self, region: str, agent_id) -> None:
        """设置区域认领(直接覆盖,业务校验由 services 层负责)"""
        if is_redis_mode():
            return await self._redis_set_claim(region, agent_id)
        return self._mem_set_claim(region, agent_id)

    async def list_all(self) -> dict:
        """列出所有认领记录(返回副本避免外部修改)"""
        if is_redis_mode():
            return await self._redis_list_all()
        return self._mem_list_all()

    # ---------- 内存后端(原逻辑, 保持不变) ----------

    def _mem_get_claim(self, region: str):
        return self.store["shipping_claims"].get(region)

    def _mem_is_claimed(self, region: str) -> bool:
        return region in self.store["shipping_claims"]

    def _mem_set_claim(self, region: str, agent_id) -> None:
        self.store["shipping_claims"][region] = agent_id

    def _mem_list_all(self) -> dict:
        return dict(self.store["shipping_claims"])

    # ---------- Redis 后端 ----------

    async def _redis_get_claim(self, region: str):
        client = await get_redis_client()
        value = await client.hget(_k("shipping_claims"), region)
        if value is None:
            logger.debug("redis_get_claim_miss region=%s", region)
            return None
        # Redis Hash 值恒为 str, 数字型 agent_id 还原为 int 以对齐内存模式
        converted = int(value) if value.isdigit() else value
        logger.debug("redis_get_claim region=%s raw=%r type=%s -> %r type=%s%s",
                     region, value, "str", converted, type(converted).__name__,
                     " (int还原)" if value.isdigit() else " (非数字,原样)")
        return converted

    async def _redis_is_claimed(self, region: str) -> bool:
        client = await get_redis_client()
        return await client.hexists(_k("shipping_claims"), region)

    async def _redis_set_claim(self, region: str, agent_id) -> None:
        client = await get_redis_client()
        await client.hset(_k("shipping_claims"), region, agent_id)

    async def _redis_list_all(self) -> dict:
        client = await get_redis_client()
        claims = await client.hgetall(_k("shipping_claims"))
        # 数字型 agent_id 还原为 int, 与内存模式保持类型一致
        result = {k: int(v) if v and v.isdigit() else v for k, v in claims.items()}
        converted_n = sum(1 for v in claims.values() if v and v.isdigit())
        logger.debug("redis_list_all total=%d converted_to_int=%d raw=%r",
                     len(result), converted_n, claims)
        return result
