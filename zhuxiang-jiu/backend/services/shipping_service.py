"""代理商区域认领业务(P4.4 扩展: 富契约 + release + 发货方解析 + 服务费计提)

认领状态机(对齐 js/agent-shipping-service.js mock):
    已认领 --release--> 已退出(区域可再被认领)

存储双轨:
    - shipping_claims: {region: agent_id}  活跃认领映射(旧契约, 兼容保留)
    - shipping_claim_details: {region: 富记录}   含 claimId/状态/时间(新契约)

响应契约(对齐前端 mock):
    claim 成功: {success, claimId, details:{claimId, agentId, agentName,
        region, status, serviceRate}, logs, asyncOps} + 兼容顶层 agentId/
        region/agentName/message
    release 成功: {success, claimId, details:{..., status:'已退出'}, logs,
        asyncOps:['agent_notify']}

Raises(旧契约保留, 路由层映射 404/409):
    KeyError: 代理商不存在
    ValueError: 区域已被其他代理商认领 / 认领记录不存在
"""

import logging

from core.locks import get_lock
from repositories.agent_repository import AgentRepository
from repositories.shipping_repository import ShippingClaimRepository
from repositories.supply_chain_repository import SupplyChainRepository
from services.tx_utils import TxLog, gen_no, now_iso

logger = logging.getLogger(__name__)

# 同品分润服务费率(厂家按订单金额 5% 计提给认领代理商, 对齐前端 CONFIG)
SERVICE_FEE_RATE = 0.05
CLAIM_ACTIVE = "已认领"
CLAIM_RELEASED = "已退出"


class ShippingClaimService:
    def __init__(
        self,
        agent_repo: AgentRepository = AgentRepository(),
        shipping_repo: ShippingClaimRepository = ShippingClaimRepository(),
        sc_repo: SupplyChainRepository = SupplyChainRepository(),
    ):
        self.agent_repo = agent_repo
        self.shipping_repo = shipping_repo
        self.sc_repo = sc_repo

    async def _get_detail(self, region: str) -> dict | None:
        """读取区域认领富记录"""
        return await self.sc_repo.hget("shipping_claim_details", region)

    async def claim(self, agent_id, region: str) -> dict:
        """代理商区域认领(富契约, 顶层保留旧兼容字段)

        并发安全: shipping:claim:{region} 锁序列化同一区域的并发认领

        Raises:
            KeyError: 代理商不存在
            ValueError: 区域已被其他代理商认领
        """
        log = TxLog()
        logger.info("claim_start agent_id=%r region=%s", agent_id, region)
        async with get_lock(f"shipping:claim:{region}"):
            agent = await self.agent_repo.get(agent_id)
            if not agent:
                logger.warning("claim_agent_not_found agent_id=%r", agent_id)
                raise KeyError(f"代理商 {agent_id} 不存在")
            agent_name = agent.get("name", f"代理商{agent_id}")
            existing = await self.shipping_repo.get_claim(region)
            idempotent = existing is not None
            if existing is not None and str(existing) != str(agent_id):
                logger.warning("claim_conflict region=%s existing=%r", region, existing)
                raise ValueError(f"区域 {region} 已被代理商 {existing} 认领")

            log.info("阶段3-认领校验", f"校验通过: {agent_name} → {region}")
            # 写入认领(活跃映射 + 富记录)
            detail = await self._get_detail(region)
            if not idempotent or detail is None:
                claim_id = gen_no("SC-")
                detail = {
                    "claimId": claim_id, "agentId": agent_id,
                    "agentName": agent_name, "region": region,
                    "status": CLAIM_ACTIVE, "serviceRate": SERVICE_FEE_RATE,
                    "claimedAt": now_iso(), "releasedAt": "",
                    "shippedQty": 0, "serviceFeeAccrued": 0.0,
                }
                await self.sc_repo.hset("shipping_claim_details", region, detail)
            else:
                claim_id = detail.get("claimId") or gen_no("SC-")
            await self.shipping_repo.set_claim(region, agent_id)
            log.info("阶段4-写入认领", f"认领记录已写入: {claim_id}")
            logger.info("claim_success region=%s agent_id=%r idempotent=%s",
                        region, agent_id, idempotent)
            return {
                "success": True,
                "claimId": claim_id,
                "details": {
                    "claimId": claim_id, "agentId": agent_id,
                    "agentName": agent_name, "region": region,
                    "status": CLAIM_ACTIVE, "serviceRate": SERVICE_FEE_RATE,
                },
                "logs": log.logs,
                "asyncOps": ["agent_notify", "blockchain_notarize"],
                # ---- 旧契约兼容字段 ----
                "agentId": agent_id,
                "region": region,
                "agentName": agent_name,
                "message": f"{agent_name} 已认领 {region} 区域",
            }

    async def release(self, agent_id, region: str) -> dict:
        """释放区域认领(状态机: 已认领 → 已退出, 区域可再被认领)

        Raises:
            ValueError: 认领记录不存在 / 非本代理商认领
        """
        log = TxLog()
        async with get_lock(f"shipping:claim:{region}"):
            existing = await self.shipping_repo.get_claim(region)
            if existing is None:
                raise ValueError(f"区域 {region} 无有效认领记录")
            if str(existing) != str(agent_id):
                raise ValueError(f"区域 {region} 由代理商 {existing} 认领, 非代理商 {agent_id}")
            detail = await self._get_detail(region) or {}
            claim_id = detail.get("claimId") or gen_no("SC-")
            agent_name = detail.get("agentName") or f"代理商{agent_id}"
            log.info("阶段3-释放校验", f"校验通过: {agent_name} 释放 {region}")
            # 活跃映射移除 + 富记录置为已退出
            await self.shipping_repo.remove_claim(region)
            detail.update({"status": CLAIM_RELEASED, "releasedAt": now_iso()})
            await self.sc_repo.hset("shipping_claim_details", region, detail)
            log.info("阶段4-释放认领", f"认领已置为已退出: {claim_id}")
            return {
                "success": True,
                "claimId": claim_id,
                "details": {
                    "claimId": claim_id, "agentId": agent_id,
                    "agentName": agent_name, "region": region,
                    "status": CLAIM_RELEASED, "serviceRate": SERVICE_FEE_RATE,
                },
                "logs": log.logs,
                "asyncOps": ["agent_notify"],
                # ---- 旧契约兼容字段 ----
                "agentId": agent_id,
                "region": region,
                "agentName": agent_name,
                "message": f"{agent_name} 已退出 {region} 区域认领",
            }

    async def resolve_shipper(self, region: str | None) -> dict:
        """按区域解析发货方(只读, checkout 阶段3调用)

        已认领 → 该代理商发货; 未认领/无 region → 厂家直供
        """
        if not region:
            return {"shipper": "manufacturer", "agentId": None,
                    "agentName": "厂家直供", "claimId": None, "region": ""}
        existing = await self.shipping_repo.get_claim(region)
        if existing is None:
            return {"shipper": "manufacturer", "agentId": None,
                    "agentName": "厂家直供", "claimId": None, "region": region}
        detail = await self._get_detail(region) or {}
        return {
            "shipper": "agent",
            "agentId": detail.get("agentId", existing),
            "agentName": detail.get("agentName") or f"代理商{existing}",
            "claimId": detail.get("claimId"),
            "region": region,
        }

    async def accrue_service_fee(self, payload: dict, log: TxLog) -> dict:
        """厂家→代理商 5% 同品分润服务费计提(checkout 阶段8调用)

        fee = round(orderAmount × SERVICE_FEE_RATE, 2), 随调用方事务
        攒批提交(本方法只生成记录, 由调用方统一 append, 保证原子性)。
        """
        order_amount = float(payload.get("orderAmount", 0))
        fee = round(order_amount * SERVICE_FEE_RATE, 2)
        record = {
            "id": gen_no("SF-"),
            "agent_id": payload.get("agentId"),
            "agent_name": payload.get("agentName"),
            "order_no": payload.get("orderNo"),
            "region": payload.get("region"),
            "shipped_qty": payload.get("shippedQty", 0),
            "order_amount": order_amount,
            "service_fee": fee,
            "service_rate": SERVICE_FEE_RATE,
            "settled_as": "同品",
            "status": "待发放",
            "created_at": now_iso(),
        }
        log.info("阶段8-服务费计提",
                 f"厂家按 5% 计提服务费 ¥{fee} 给代理商 {payload.get('agentName')}")
        return {"serviceFee": fee, "record": record}

    async def list_claims(self, detail: bool = False) -> dict:
        """列出所有区域认领记录

        detail=False(默认): {success, claims: {region: agent_id}} 旧契约(兼容保留)
        detail=True: {success, claims: [富记录数组]} 对齐前端 mock 契约
            (含已退出认领, 字段: claimId/agentId/agentName/region/status/serviceRate/
             claimedAt/releasedAt/shippedQty/serviceFeeAccrued)
        """
        claims_map = await self.shipping_repo.list_all()
        if not detail:
            return {"success": True, "claims": claims_map}
        details = await self.sc_repo.hgetall("shipping_claim_details")
        records = list(details.values())
        records.sort(key=lambda r: r.get("claimedAt") or "", reverse=True)
        return {"success": True, "claims": records}

    async def get_service_fee_settlement(self, agent_id) -> dict:
        """按代理商聚合服务费结算统计(P5.1: 对齐前端 getServiceFeeSettlement mock 契约)

        从 service_fees 域聚合该代理商的 待发放/已发放 统计。
        """
        fees = await self.sc_repo.list_all("service_fees")
        mine = [f for f in fees if str(f.get("agent_id")) == str(agent_id)]
        pending = [f for f in mine if f.get("status") == "待发放"]
        settled = [f for f in mine if f.get("status") == "已发放"]

        def _sum(records):
            return round(sum(float(r.get("service_fee", 0)) for r in records), 2)

        return {
            "success": True,
            "details": {
                "agentId": agent_id,
                "totalCount": len(mine),
                "pendingCount": len(pending),
                "pendingAmount": _sum(pending),
                "settledAmount": _sum(settled),
                "settledAs": "同品",
            },
            # ---- 兼容顶层字段(mock 契约直接返回平铺对象) ----
            "agentId": agent_id,
            "totalCount": len(mine),
            "pendingCount": len(pending),
            "pendingAmount": _sum(pending),
            "settledAmount": _sum(settled),
            "settledAs": "同品",
        }
