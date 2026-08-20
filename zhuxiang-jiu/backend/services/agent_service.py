"""代理商业务:升级/降级

并发安全: 升级涉及 wallet RMW,使用 agent:{agentId} 锁(对齐前端 Mutex)
"""

from datetime import datetime

from core.locks import get_lock
from repositories.agent_repository import AgentRepository


class AgentService:
    def __init__(self, agent_repo: AgentRepository = AgentRepository()):
        self.agent_repo = agent_repo

    async def upgrade(self, agent_id, to_level: str, pay_amount: float) -> dict:
        """代理商升级(等级变更 + 钱包充值)

        Raises:
            KeyError: 代理商不存在
        """
        async with get_lock(f"agent:{agent_id}"):
            agent = await self.agent_repo.get(agent_id)
            if not agent:
                raise KeyError(f"代理商 {agent_id} 不存在")
            old_level = await self.agent_repo.update_level(agent_id, to_level)
            new_wallet = await self.agent_repo.add_wallet(agent_id, pay_amount)
            return {
                "success": True,
                "agentId": agent_id,
                "fromLevel": old_level,
                "toLevel": to_level,
                "wallet": new_wallet,
                "logs": [
                    {"step": "升级", "level": "INFO", "msg": f"{old_level}→{to_level}"},
                    {"step": "钱包", "level": "INFO", "msg": f"充值 ¥{pay_amount}"},
                ],
            }

    async def downgrade(self, agent_id, reason: str = "考核未达标") -> dict:
        """代理商降级(S→A→B→C→D,已为 D 则保持 D)

        Raises:
            KeyError: 代理商不存在
        """
        # 降级只读改 level,不涉及 wallet RMW;无锁即可
        agent = await self.agent_repo.get(agent_id)
        if not agent:
            raise KeyError(f"代理商 {agent_id} 不存在")
        old_level = await self.agent_repo.get_level(agent_id)
        new_level = await self.agent_repo.downgrade_level(agent_id)
        return {
            "success": True,
            "agentId": agent_id,
            "fromLevel": old_level,
            "toLevel": new_level,
            "reason": reason,
            "logs": [{"step": "降级", "level": "WARN",
                      "msg": f"{old_level}→{new_level}, 原因: {reason}"}],
        }
