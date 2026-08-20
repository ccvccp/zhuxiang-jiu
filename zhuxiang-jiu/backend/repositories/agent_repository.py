"""代理商 Repository

封装 _mock_store["agents"] 的访问。
锁键: agent:{agentId}(并发安全由 services 层负责)
"""

from typing import Optional

from repositories.store import _mock_store


class AgentRepository:
    """代理商数据访问"""

    def __init__(self, store: dict = _mock_store):
        self.store = store

    def get(self, agent_id) -> Optional[dict]:
        """按 ID 查询代理商,不存在返回 None"""
        return self.store["agents"].get(agent_id)

    def list_all(self) -> list[dict]:
        """列出所有代理商"""
        return list(self.store["agents"].values())

    def save(self, agent_id, agent_data: dict) -> dict:
        """新增/覆盖代理商记录(测试中常见模式:_mock_store["agents"][3] = {...})"""
        self.store["agents"][agent_id] = agent_data
        return agent_data

    def update_level(self, agent_id, new_level: str) -> str:
        """更新等级,返回旧等级

        Raises:
            KeyError: 代理商不存在
        """
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        old_level = agent.get("level", "D")
        agent["level"] = new_level
        return old_level

    def downgrade_level(self, agent_id) -> str:
        """按 S→A→B→C→D 规则降一级,返回新等级

        Raises:
            KeyError: 代理商不存在
        """
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        old_level = agent.get("level", "D")
        new_level = {"S": "A", "A": "B", "B": "C", "C": "D", "D": "D"}.get(old_level, "D")
        agent["level"] = new_level
        return new_level

    def add_wallet(self, agent_id, amount: float) -> float:
        """钱包累加(amount>=0),返回新余额

        Raises:
            KeyError: 代理商不存在
        """
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        agent["wallet"] = agent.get("wallet", 0) + amount
        return agent["wallet"]

    def get_wallet(self, agent_id) -> float:
        """查询钱包余额"""
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        return agent.get("wallet", 0)

    def get_level(self, agent_id) -> str:
        """查询等级"""
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        return agent.get("level", "D")
