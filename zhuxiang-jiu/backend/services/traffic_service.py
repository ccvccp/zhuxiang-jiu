"""流量管理模块业务逻辑层

核心业务:
    - 创建推广员(5级等级体系/推广码生成)
    - 引流记录(多平台引流/有效流量标记)
    - 推广员裂变关系(上级推广员/裂变层级)
    - 佣金计算(按订单金额+等级佣金比例)
    - 推广员等级自动升级
    - 流量统计与流量分发

锁保护:
    - 创建推广员: lock:traffic:promoter:create:{user_id}  (推广码唯一)
    - 引流记录: lock:traffic:lead:{promoter_id}:{user_id}  (幂等防重)
    - 佣金计算: lock:traffic:commission:{promoter_id}  (佣金累加原子)
    - 等级升级: lock:traffic:promoter:{promoter_id}  (等级原子切换)

异常约定:
    - KeyError → 404(推广员不存在)
    - ValueError → 409(业务冲突: 推广码已存在/封禁/重复引流)
"""

from datetime import datetime
from typing import Optional

from core.locks import get_lock
from core.helpers import ts
from repositories.traffic_repository import (
    TrafficRepository,
    # 流量来源
    SOURCE_DOUYIN, SOURCE_KUAISHOU, SOURCE_WECHAT,
    SOURCE_XIAOHONGSHU, SOURCE_BILIBILI, SOURCE_TAOBAO, SOURCE_DIRECT,
    # 引流方式
    MEDIUM_VIDEO, MEDIUM_LIVE, MEDIUM_SHARE, MEDIUM_AD,
    # 推广员等级
    LEVEL_TRAINEE, LEVEL_JUNIOR, LEVEL_INTERMEDIATE, LEVEL_SENIOR, LEVEL_GOLD,
    LEVEL_COMMISSION_RATE, LEVEL_UPGRADE_CONDITIONS, LEVEL_EXTRA_REWARD,
    LEVEL_RANK,
    # 推广员状态
    PROMOTER_STATUS_ACTIVE, PROMOTER_STATUS_PAUSED, PROMOTER_STATUS_BANNED,
    # 引流记录状态
    LEAD_STATUS_PENDING, LEAD_STATUS_REGISTERED, LEAD_STATUS_ORDERED,
    LEAD_STATUS_INVALID, LEAD_EFFECTIVE_TRUE, LEAD_EFFECTIVE_FALSE,
    # 佣金状态
    COMMISSION_PENDING, COMMISSION_SETTLED, COMMISSION_WITHDRAWN,
)


class TrafficService:
    """流量管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: TrafficRepository = TrafficRepository()):
        self.repo = repo

    # ============================================================
    # 1. 创建推广员
    # ============================================================

    async def create_promoter(self, user_id: int, name: str = "",
                                level: str = LEVEL_TRAINEE,
                                parent_promoter_id: int = 0) -> dict:
        """创建推广员(初始等级见习, 5%佣金)

        Returns:
            推广员详情

        Raises:
            ValueError: 等级无效/已存在
        """
        if level not in LEVEL_RANK:
            raise ValueError(f"无效推广员等级: {level}")

        lock_key = f"traffic:promoter:create:{user_id}"

        async with get_lock(lock_key):
            promoter_id = await self.repo.next_promoter_id()
            now = datetime.utcnow().isoformat()
            # 推广码: P + 时间戳后8位 + 自增ID
            promoter_code = f"P{now[:10].replace('-', '')[-8:]}{promoter_id:04d}"

            promoter = {
                "id": promoter_id,
                "userId": user_id,
                "name": name,
                "promoterCode": promoter_code,
                "level": level,
                "commissionRate": LEVEL_COMMISSION_RATE.get(level, 0.05),
                "totalInvited": 0,
                "totalRegistered": 0,
                "totalOrdered": 0,
                "totalCommission": 0.0,
                "pendingCommission": 0.0,
                "parentPromoterId": parent_promoter_id,
                "fissionLevel": 1,  # 默认1级裂变(自身)
                "status": PROMOTER_STATUS_ACTIVE,
                "createdAt": now,
                "updatedAt": now,
            }
            await self.repo.save_promoter(promoter)
            return promoter

    # ============================================================
    # 2. 查询推广员
    # ============================================================

    async def get_promoter(self, promoter_id: int) -> dict:
        """查询推广员详情

        Raises:
            KeyError: 推广员不存在
        """
        promoter = await self.repo.get_promoter(promoter_id)
        if promoter is None:
            raise KeyError(f"推广员不存在(promoterId={promoter_id})")
        return promoter

    async def get_promoter_by_code(self, promoter_code: str) -> dict:
        """按推广码查询推广员

        Raises:
            KeyError: 推广员不存在
        """
        promoter = await self.repo.get_promoter_by_code(promoter_code)
        if promoter is None:
            raise KeyError(f"推广码无效(code={promoter_code})")
        return promoter

    async def list_promoters(self, status: str = None, level: str = None,
                             limit: int = 100) -> list[dict]:
        """查询推广员列表(支持按状态/等级筛选)"""
        return await self.repo.list_promoters(status=status, level=level, limit=limit)

    # ============================================================
    # 3. 引流记录
    # ============================================================

    async def record_lead(self, promoter_id: int, user_id: int,
                           source: str = SOURCE_DIRECT, medium: str = MEDIUM_SHARE,
                           utm_params: str = "", is_effective: int = LEAD_EFFECTIVE_TRUE,
                           status: str = LEAD_STATUS_PENDING) -> dict:
        """记录引流(推广员带来的流量)

        Returns:
            引流记录

        Raises:
            KeyError: 推广员不存在
            ValueError: 推广员被封禁
        """
        promoter = await self.repo.get_promoter(promoter_id)
        if promoter is None:
            raise KeyError(f"推广员不存在(promoterId={promoter_id})")

        if promoter.get("status") == PROMOTER_STATUS_BANNED:
            raise ValueError(f"推广员已被封禁(promoterId={promoter_id})")

        lead_id = await self.repo.add_lead({
            "promoterId": promoter_id,
            "userId": user_id,
            "source": source,
            "medium": medium,
            "utmParams": utm_params,
            "isEffective": is_effective,
            "status": status,
        })

        # 更新推广员累计邀请数
        lock_key = f"traffic:promoter:{promoter_id}"
        async with get_lock(lock_key):
            promoter = await self.repo.get_promoter(promoter_id)
            if promoter:
                promoter["totalInvited"] = promoter.get("totalInvited", 0) + 1
                if status in (LEAD_STATUS_REGISTERED, LEAD_STATUS_ORDERED):
                    promoter["totalRegistered"] = promoter.get("totalRegistered", 0) + 1
                if status == LEAD_STATUS_ORDERED:
                    promoter["totalOrdered"] = promoter.get("totalOrdered", 0) + 1
                await self.repo.save_promoter(promoter)

                # 自动等级升级
                await self._maybe_upgrade_level(promoter_id)

        lead = await self.repo.get_lead(lead_id)
        return lead

    async def list_leads(self, promoter_id: int = None, source: str = None,
                         status: str = None, limit: int = 100) -> list[dict]:
        """查询引流记录(支持按推广员/来源/状态筛选)"""
        return await self.repo.list_leads(promoter_id=promoter_id, source=source,
                                          status=status, limit=limit)

    async def update_lead_status(self, lead_id: int, status: str,
                                  is_effective: int = None) -> dict:
        """更新引流记录状态

        Raises:
            KeyError: 引流记录不存在
        """
        lead = await self.repo.get_lead(lead_id)
        if lead is None:
            raise KeyError(f"引流记录不存在(leadId={lead_id})")
        await self.repo.update_lead_status(lead_id, status, is_effective)
        lead["status"] = status
        if is_effective is not None:
            lead["isEffective"] = is_effective
        lead["updatedAt"] = datetime.utcnow().isoformat()
        return lead

    # ============================================================
    # 4. 流量统计
    # ============================================================

    async def get_stats(self, promoter_id: int = None) -> dict:
        """流量统计(按推广员或全局)

        Raises:
            KeyError: 推广员不存在(当指定promoter_id时)
        """
        if promoter_id is not None:
            promoter = await self.repo.get_promoter(promoter_id)
            if promoter is None:
                raise KeyError(f"推广员不存在(promoterId={promoter_id})")
            leads = await self.repo.list_leads(promoter_id=promoter_id, limit=10000)
            commissions = await self.repo.list_commissions(promoter_id, limit=10000)
            effective_count = sum(1 for l in leads if l.get("isEffective") == LEAD_EFFECTIVE_TRUE)
            ordered_count = sum(1 for l in leads if l.get("status") == LEAD_STATUS_ORDERED)
            settled_commission = sum(c.get("commission", 0) for c in commissions
                                       if c.get("status") == COMMISSION_SETTLED)
            pending_commission = sum(c.get("commission", 0) for c in commissions
                                      if c.get("status") == COMMISSION_PENDING)
            return {
                "promoterId": promoter_id,
                "promoterCode": promoter.get("promoterCode"),
                "level": promoter.get("level"),
                "totalInvited": promoter.get("totalInvited", 0),
                "totalRegistered": promoter.get("totalRegistered", 0),
                "totalOrdered": promoter.get("totalOrdered", 0),
                "totalCommission": promoter.get("totalCommission", 0.0),
                "pendingCommission": promoter.get("pendingCommission", 0.0),
                "effectiveLeads": effective_count,
                "orderedLeads": ordered_count,
                "settledCommission": round(settled_commission, 2),
                "pendingCommissionRecords": round(pending_commission, 2),
            }

        # 全局统计
        all_promoters = await self.repo.list_promoters(limit=10000)
        all_leads = await self.repo.list_leads(limit=10000)
        active_promoters = sum(1 for p in all_promoters if p.get("status") == PROMOTER_STATUS_ACTIVE)
        total_commission = sum(p.get("totalCommission", 0) for p in all_promoters)
        total_invited = sum(p.get("totalInvited", 0) for p in all_promoters)
        effective_leads = sum(1 for l in all_leads if l.get("isEffective") == LEAD_EFFECTIVE_TRUE)
        return {
            "totalPromoters": len(all_promoters),
            "activePromoters": active_promoters,
            "totalInvited": total_invited,
            "totalLeads": len(all_leads),
            "effectiveLeads": effective_leads,
            "totalCommission": round(total_commission, 2),
            "statsAt": ts(),
        }

    # ============================================================
    # 5. 佣金计算
    # ============================================================

    async def calculate_commission(self, promoter_id: int, order_id: str,
                                    order_amount: float, user_id: int = 0) -> dict:
        """计算佣金(按订单金额×等级佣金比例)

        规则:
            - 推广员封禁状态不可计算佣金
            - 自购不计佣金(下单用户=推广员用户Id)
            - 佣金 = 订单金额 × 佣金比例

        Returns:
            佣金计算结果

        Raises:
            KeyError: 推广员不存在
            ValueError: 推广员封禁/自购/订单金额无效
        """
        if order_amount <= 0:
            raise ValueError("订单金额必须大于0")

        lock_key = f"traffic:commission:{promoter_id}"

        async with get_lock(lock_key):
            promoter = await self.repo.get_promoter(promoter_id)
            if promoter is None:
                raise KeyError(f"推广员不存在(promoterId={promoter_id})")

            if promoter.get("status") == PROMOTER_STATUS_BANNED:
                raise ValueError(f"推广员已被封禁(promoterId={promoter_id})")

            if user_id and user_id == promoter.get("userId"):
                raise ValueError("自购不计佣金")

            rate = promoter.get("commissionRate", 0.05)
            commission = round(order_amount * rate, 2)

            commission_id = await self.repo.add_commission({
                "promoterId": promoter_id,
                "orderId": order_id,
                "userId": user_id,
                "orderAmount": order_amount,
                "commissionRate": rate,
                "commission": commission,
                "status": COMMISSION_PENDING,
            })

            # 累加到推广员账户
            promoter["totalCommission"] = round(
                promoter.get("totalCommission", 0) + commission, 2
            )
            promoter["pendingCommission"] = round(
                promoter.get("pendingCommission", 0) + commission, 2
            )
            await self.repo.save_promoter(promoter)

            return {
                "commissionId": commission_id,
                "promoterId": promoter_id,
                "orderId": order_id,
                "userId": user_id,
                "orderAmount": order_amount,
                "commissionRate": rate,
                "commission": commission,
                "status": COMMISSION_PENDING,
            }

    # ============================================================
    # 6. 推广员等级查询与升级
    # ============================================================

    async def get_promoter_level(self, promoter_id: int) -> dict:
        """查询推广员等级与升级条件

        Raises:
            KeyError: 推广员不存在
        """
        promoter = await self.repo.get_promoter(promoter_id)
        if promoter is None:
            raise KeyError(f"推广员不存在(promoterId={promoter_id})")

        current_level = promoter.get("level", LEVEL_TRAINEE)
        current_rank = LEVEL_RANK.get(current_level, 1)
        next_level = None
        next_condition = None
        for lvl, rank in LEVEL_RANK.items():
            if rank == current_rank + 1:
                next_level = lvl
                next_condition = LEVEL_UPGRADE_CONDITIONS.get(lvl)
                break

        return {
            "promoterId": promoter_id,
            "currentLevel": current_level,
            "currentRank": current_rank,
            "commissionRate": promoter.get("commissionRate", 0.05),
            "totalInvited": promoter.get("totalInvited", 0),
            "totalRegistered": promoter.get("totalRegistered", 0),
            "totalOrdered": promoter.get("totalOrdered", 0),
            "nextLevel": next_level,
            "nextCondition": next_condition,
            "extraReward": LEVEL_EXTRA_REWARD.get(current_level, {}),
        }

    async def _maybe_upgrade_level(self, promoter_id: int) -> bool:
        """自动等级升级(根据邀请数+下单数)"""
        promoter = await self.repo.get_promoter(promoter_id)
        if not promoter:
            return False

        current_level = promoter.get("level", LEVEL_TRAINEE)
        current_rank = LEVEL_RANK.get(current_level, 1)
        total_invited = promoter.get("totalInvited", 0)
        total_ordered = promoter.get("totalOrdered", 0)

        # 检查所有更高等级
        for lvl, rank in sorted(LEVEL_RANK.items(), key=lambda x: x[1]):
            if rank <= current_rank:
                continue
            required_invited, required_ordered = LEVEL_UPGRADE_CONDITIONS.get(lvl, (999, 999))
            if total_invited >= required_invited and total_ordered >= required_ordered:
                # 升级
                promoter["level"] = lvl
                promoter["commissionRate"] = LEVEL_COMMISSION_RATE.get(lvl, 0.05)
                await self.repo.save_promoter(promoter)
                return True
        return False

    # ============================================================
    # 7. 裂变关系
    # ============================================================

    async def get_fission_tree(self, promoter_id: int) -> dict:
        """查询推广员裂变关系树

        Raises:
            KeyError: 推广员不存在
        """
        promoter = await self.repo.get_promoter(promoter_id)
        if promoter is None:
            raise KeyError(f"推广员不存在(promoterId={promoter_id})")

        # 查询所有以当前推广员为上级的推广员(1级下线)
        all_promoters = await self.repo.list_promoters(limit=10000)
        direct_subordinates = [p for p in all_promoters
                               if p.get("parentPromoterId") == promoter_id]

        # 计算裂变层级
        subordinate_count = len(direct_subordinates)
        # 递归统计下线的下线数(简化: 仅2层)
        indirect_count = 0
        for sub in direct_subordinates:
            sub_id = sub["id"]
            indirect = [p for p in all_promoters
                        if p.get("parentPromoterId") == sub_id]
            indirect_count += len(indirect)

        return {
            "promoterId": promoter_id,
            "promoterCode": promoter.get("promoterCode"),
            "level": promoter.get("level"),
            "parentPromoterId": promoter.get("parentPromoterId", 0),
            "fissionLevel": promoter.get("fissionLevel", 1),
            "directSubordinates": subordinate_count,
            "indirectSubordinates": indirect_count,
            "totalSubordinates": subordinate_count + indirect_count,
            "subordinates": [
                {
                    "promoterId": s["id"],
                    "name": s.get("name", ""),
                    "level": s.get("level"),
                    "promoterCode": s.get("promoterCode"),
                    "totalInvited": s.get("totalInvited", 0),
                    "totalCommission": s.get("totalCommission", 0),
                }
                for s in direct_subordinates
            ],
        }

    # ============================================================
    # 8. 流量来源管理
    # ============================================================

    async def create_source(self, code: str, name: str,
                             description: str = "") -> dict:
        """创建流量来源

        Raises:
            ValueError: 推广码已存在
        """
        existing = await self.repo.get_source_by_code(code)
        if existing is not None:
            raise ValueError(f"流量来源已存在(code={code})")

        source_id = await self.repo.next_source_id()
        now = datetime.utcnow().isoformat()
        source = {
            "id": source_id,
            "code": code,
            "name": name,
            "description": description,
            "totalVisits": 0,
            "totalConversions": 0,
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repo.save_source(source)
        return source

    async def list_sources(self, limit: int = 100) -> list[dict]:
        """查询流量来源列表"""
        return await self.repo.list_sources(limit=limit)

    # ============================================================
    # 9. 流量分发
    # ============================================================

    async def distribute_traffic(self, total_traffic: int,
                                  strategy: str = "proportional") -> dict:
        """流量分发(按策略分配到各推广员)

        Args:
            total_traffic: 待分发流量总数
            strategy: 分发策略(proportional=按比例, average=平均, weighted=按等级加权)

        Returns:
            分发结果(每个推广员分配的流量数)
        """
        promoters = await self.repo.list_promoters(status=PROMOTER_STATUS_ACTIVE, limit=10000)
        if not promoters:
            return {"totalTraffic": total_traffic, "distributions": [],
                    "strategy": strategy, "distributedAt": ts()}

        distributions = []
        if strategy == "average":
            # 平均分配
            per_promoter = total_traffic // len(promoters)
            remaining = total_traffic - per_promoter * len(promoters)
            for idx, p in enumerate(promoters):
                amount = per_promoter + (1 if idx < remaining else 0)
                distributions.append({
                    "promoterId": p["id"],
                    "promoterCode": p.get("promoterCode"),
                    "level": p.get("level"),
                    "traffic": amount,
                })
        elif strategy == "weighted":
            # 按等级加权
            weights = {LEVEL_GOLD: 5, LEVEL_SENIOR: 4, LEVEL_INTERMEDIATE: 3,
                       LEVEL_JUNIOR: 2, LEVEL_TRAINEE: 1}
            total_weight = sum(weights.get(p.get("level"), 1) for p in promoters)
            for p in promoters:
                weight = weights.get(p.get("level"), 1)
                amount = int(total_traffic * weight / total_weight)
                distributions.append({
                    "promoterId": p["id"],
                    "promoterCode": p.get("promoterCode"),
                    "level": p.get("level"),
                    "traffic": amount,
                })
        else:
            # 按比例(根据已邀请人数)
            total_invited = sum(p.get("totalInvited", 0) for p in promoters) or 1
            for p in promoters:
                ratio = p.get("totalInvited", 0) / total_invited
                amount = int(total_traffic * ratio)
                distributions.append({
                    "promoterId": p["id"],
                    "promoterCode": p.get("promoterCode"),
                    "level": p.get("level"),
                    "traffic": amount,
                })

        return {
            "totalTraffic": total_traffic,
            "strategy": strategy,
            "distributorCount": len(distributions),
            "distributions": distributions,
            "distributedAt": ts(),
        }

    # ============================================================
    # 10. 管理端统计
    # ============================================================

    async def get_admin_stats(self) -> dict:
        """管理端统计(全局)"""
        all_promoters = await self.repo.list_promoters(limit=10000)
        all_leads = await self.repo.list_leads(limit=10000)
        all_sources = await self.repo.list_sources(limit=10000)

        # 按等级统计
        level_stats = {}
        for p in all_promoters:
            lvl = p.get("level", LEVEL_TRAINEE)
            level_stats[lvl] = level_stats.get(lvl, 0) + 1

        # 按来源统计
        source_stats = {}
        for l in all_leads:
            src = l.get("source", "unknown")
            source_stats[src] = source_stats.get(src, 0) + 1

        # 状态统计
        status_stats = {}
        for p in all_promoters:
            s = p.get("status", "unknown")
            status_stats[s] = status_stats.get(s, 0) + 1

        total_commission = sum(p.get("totalCommission", 0) for p in all_promoters)
        total_pending = sum(p.get("pendingCommission", 0) for p in all_promoters)

        return {
            "totalPromoters": len(all_promoters),
            "activePromoters": status_stats.get(PROMOTER_STATUS_ACTIVE, 0),
            "pausedPromoters": status_stats.get(PROMOTER_STATUS_PAUSED, 0),
            "bannedPromoters": status_stats.get(PROMOTER_STATUS_BANNED, 0),
            "levelDistribution": level_stats,
            "sourceDistribution": source_stats,
            "totalLeads": len(all_leads),
            "totalSources": len(all_sources),
            "totalCommission": round(total_commission, 2),
            "totalPendingCommission": round(total_pending, 2),
            "statsAt": ts(),
        }
