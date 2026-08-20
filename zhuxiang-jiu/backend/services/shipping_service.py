"""代理商区域认领业务

冲突规则: 同一区域被其他代理商认领时返回 409,同代理商重复认领幂等成功
"""

from repositories.agent_repository import AgentRepository
from repositories.shipping_repository import ShippingClaimRepository


class ShippingClaimService:
    def __init__(
        self,
        agent_repo: AgentRepository = AgentRepository(),
        shipping_repo: ShippingClaimRepository = ShippingClaimRepository(),
    ):
        self.agent_repo = agent_repo
        self.shipping_repo = shipping_repo

    def claim(self, agent_id, region: str) -> dict:
        """代理商区域认领

        Raises:
            KeyError: 代理商不存在
            ValueError: 区域已被其他代理商认领
        """
        agent = self.agent_repo.get(agent_id)
        if not agent:
            raise KeyError(f"代理商 {agent_id} 不存在")
        existing = self.shipping_repo.get_claim(region)
        if existing is not None and existing != agent_id:
            raise ValueError(f"区域 {region} 已被代理商 {existing} 认领")
        self.shipping_repo.set_claim(region, agent_id)
        return {
            "success": True,
            "agentId": agent_id,
            "region": region,
            "agentName": agent.get("name", f"代理商{agent_id}"),
            "message": f"{agent.get('name', f'代理商{agent_id}')} 已认领 {region} 区域",
        }

    def list_claims(self) -> dict:
        """列出所有区域认领记录"""
        return {"success": True, "claims": self.shipping_repo.list_all()}
