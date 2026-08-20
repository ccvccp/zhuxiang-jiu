"""代理商 Repository

双模式(内存/Redis)透明切换:
    - 内存模式: 直接操作 _mock_store["agents"] 字典
    - Redis 模式: Hash 存储 zhuxiang:agent:{id}

锁键: agent:{agentId}(并发安全由 services 层负责)
"""

from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


class AgentRepository:
    """代理商数据访问(双模式)"""

    def __init__(self, store: dict = None):
        # store 参数仅用于内存模式兼容(测试可能注入)
        # Redis 模式下忽略 store, 走 Redis 客户端
        self.store = store if store is not None else get_in_memory_store()

    async def get(self, agent_id) -> Optional[dict]:
        """按 ID 查询代理商,不存在返回 None"""
        if is_redis_mode():
            return await self._redis_get(agent_id)
        return self._mem_get(agent_id)

    async def list_all(self) -> list[dict]:
        """列出所有代理商"""
        if is_redis_mode():
            return await self._redis_list_all()
        return self._mem_list_all()

    async def save(self, agent_id, agent_data: dict) -> dict:
        """新增/覆盖代理商记录"""
        if is_redis_mode():
            return await self._redis_save(agent_id, agent_data)
        return self._mem_save(agent_id, agent_data)

    async def update_level(self, agent_id, new_level: str) -> str:
        """更新等级,返回旧等级

        Raises:
            KeyError: 代理商不存在
        """
        if is_redis_mode():
            return await self._redis_update_level(agent_id, new_level)
        return self._mem_update_level(agent_id, new_level)

    async def downgrade_level(self, agent_id) -> str:
        """按 S→A→B→C→D 规则降一级,返回新等级

        Raises:
            KeyError: 代理商不存在
        """
        if is_redis_mode():
            return await self._redis_downgrade_level(agent_id)
        return self._mem_downgrade_level(agent_id)

    async def add_wallet(self, agent_id, amount: float) -> float:
        """钱包累加(amount>=0),返回新余额

        Raises:
            KeyError: 代理商不存在
        """
        if is_redis_mode():
            return await self._redis_add_wallet(agent_id, amount)
        return self._mem_add_wallet(agent_id, amount)

    async def get_wallet(self, agent_id) -> float:
        """查询钱包余额"""
        if is_redis_mode():
            return await self._redis_get_wallet(agent_id)
        return self._mem_get_wallet(agent_id)

    async def get_level(self, agent_id) -> str:
        """查询等级"""
        if is_redis_mode():
            return await self._redis_get_level(agent_id)
        return self._mem_get_level(agent_id)

    # ---------- 内存后端(原逻辑, 保持不变) ----------

    def _mem_get(self, agent_id) -> Optional[dict]:
        return self.store["agents"].get(agent_id)

    def _mem_list_all(self) -> list[dict]:
        return list(self.store["agents"].values())

    def _mem_save(self, agent_id, agent_data: dict) -> dict:
        self.store["agents"][agent_id] = agent_data
        return agent_data

    def _mem_update_level(self, agent_id, new_level: str) -> str:
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        old_level = agent.get("level", "D")
        agent["level"] = new_level
        return old_level

    def _mem_downgrade_level(self, agent_id) -> str:
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        old_level = agent.get("level", "D")
        new_level = {"S": "A", "A": "B", "B": "C", "C": "D", "D": "D"}.get(old_level, "D")
        agent["level"] = new_level
        return new_level

    def _mem_add_wallet(self, agent_id, amount: float) -> float:
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        agent["wallet"] = agent.get("wallet", 0) + amount
        return agent["wallet"]

    def _mem_get_wallet(self, agent_id) -> float:
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        return agent.get("wallet", 0)

    def _mem_get_level(self, agent_id) -> str:
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        return agent.get("level", "D")

    # ---------- Redis 后端 ----------

    async def _redis_get(self, agent_id) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hgetall(_k("agent", agent_id))
        if not data:
            return None
        return {
            "id": int(data["id"]),
            "name": data["name"],
            "level": data["level"],
            "wallet": float(data["wallet"]),
        }

    async def _redis_list_all(self) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("agent", "*"))
        result = []
        for key in keys:
            data = await client.hgetall(key)
            if data:
                result.append({
                    "id": int(data["id"]),
                    "name": data["name"],
                    "level": data["level"],
                    "wallet": float(data["wallet"]),
                })
        return result

    async def _redis_save(self, agent_id, agent_data: dict) -> dict:
        client = await get_redis_client()
        await client.hset(_k("agent", agent_id), mapping={
            "id": agent_data["id"],
            "name": agent_data["name"],
            "level": agent_data["level"],
            "wallet": agent_data["wallet"],
        })
        return agent_data

    async def _redis_update_level(self, agent_id, new_level: str) -> str:
        client = await get_redis_client()
        key = _k("agent", agent_id)
        if not await client.exists(key):
            raise KeyError(agent_id)
        old_level = await client.hget(key, "level") or "D"
        await client.hset(key, "level", new_level)
        return old_level

    async def _redis_downgrade_level(self, agent_id) -> str:
        client = await get_redis_client()
        key = _k("agent", agent_id)
        if not await client.exists(key):
            raise KeyError(agent_id)
        old_level = await client.hget(key, "level") or "D"
        new_level = {"S": "A", "A": "B", "B": "C", "C": "D", "D": "D"}.get(old_level, "D")
        await client.hset(key, "level", new_level)
        return new_level

    async def _redis_add_wallet(self, agent_id, amount: float) -> float:
        client = await get_redis_client()
        key = _k("agent", agent_id)
        if not await client.exists(key):
            raise KeyError(agent_id)
        # HINCRBYFLOAT 原子累加
        new_wallet = await client.hincrbyfloat(key, "wallet", amount)
        return new_wallet

    async def _redis_get_wallet(self, agent_id) -> float:
        client = await get_redis_client()
        key = _k("agent", agent_id)
        if not await client.exists(key):
            raise KeyError(agent_id)
        wallet = await client.hget(key, "wallet")
        return float(wallet) if wallet else 0.0

    async def _redis_get_level(self, agent_id) -> str:
        client = await get_redis_client()
        key = _k("agent", agent_id)
        if not await client.exists(key):
            raise KeyError(agent_id)
        return await client.hget(key, "level") or "D"
