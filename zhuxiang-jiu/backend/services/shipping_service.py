"""代理商区域认领业务

冲突规则: 同一区域被其他代理商认领时返回 409,同代理商重复认领幂等成功
"""

import logging

from core.locks import get_lock
from repositories.agent_repository import AgentRepository
from repositories.shipping_repository import ShippingClaimRepository

logger = logging.getLogger(__name__)


class ShippingClaimService:
    def __init__(
        self,
        agent_repo: AgentRepository = AgentRepository(),
        shipping_repo: ShippingClaimRepository = ShippingClaimRepository(),
    ):
        self.agent_repo = agent_repo
        self.shipping_repo = shipping_repo

    async def claim(self, agent_id, region: str) -> dict:
        """代理商区域认领

        并发安全: 使用 shipping:claim:{region} 锁序列化同一区域的并发认领

        Raises:
            KeyError: 代理商不存在
            ValueError: 区域已被其他代理商认领
        """
        logger.info("claim_start agent_id=%r region=%s type(agent_id)=%s",
                    agent_id, region, type(agent_id).__name__)
        async with get_lock(f"shipping:claim:{region}"):
            logger.debug("claim_lock_acquired key=shipping:claim:%s", region)
            agent = await self.agent_repo.get(agent_id)
            if not agent:
                logger.warning("claim_agent_not_found agent_id=%r", agent_id)
                raise KeyError(f"代理商 {agent_id} 不存在")
            existing = await self.shipping_repo.get_claim(region)
            # str() 归一化比较, 兼容 Redis(str) ↔ 内存(int) 类型漂移
            if existing is not None and str(existing) != str(agent_id):
                logger.warning(
                    "claim_conflict region=%s existing=%r type(existing)=%s "
                    "agent_id=%r type(agent_id)=%s",
                    region, existing, type(existing).__name__,
                    agent_id, type(agent_id).__name__,
                )
                raise ValueError(f"区域 {region} 已被代理商 {existing} 认领")
            idempotent = existing is not None
            await self.shipping_repo.set_claim(region, agent_id)
            logger.info("claim_success region=%s agent_id=%r idempotent=%s",
                        region, agent_id, idempotent)
            return {
                "success": True,
                "agentId": agent_id,
                "region": region,
                "agentName": agent.get("name", f"代理商{agent_id}"),
                "message": f"{agent.get('name', f'代理商{agent_id}')} 已认领 {region} 区域",
            }

    async def list_claims(self) -> dict:
        """列出所有区域认领记录"""
        claims = await self.shipping_repo.list_all()
        return {"success": True, "claims": claims}
