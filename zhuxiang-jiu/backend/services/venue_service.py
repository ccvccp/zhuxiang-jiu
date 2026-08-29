"""酒店酒吧会所合作商模块业务逻辑层

核心业务:
    - 合作商申请(资质信息录入 → pending)
    - 合作商审核(pending → reviewing → signed/rejected)
    - 状态流转(状态机校验: 申请→审核→签约→合作→终止)
    - 合作商分级(D/C/B/A/S, 基于月销量自动升级或手动分级)
    - 场地管理(CRUD)
    - 铺货管理(记录产品到合作商场地的铺货)
    - 佣金结算(基于等级的差价利润分润)

锁保护:
    - 申请: lock:venue:partner:{partner_id}  (新建无锁, 更新有锁)
    - 审核/流转: lock:venue:partner:{partner_id}  (状态机原子更新)
    - 分级: lock:venue:partner:{partner_id}  (等级原子更新)
    - 铺货: lock:venue:stocking:{stocking_id}  (库存原子更新)
    - 结算: lock:venue:settle:{partner_id}  (结算串行)

异常约定:
    - KeyError → 404(合作商/场地/铺货记录不存在)
    - ValueError → 409(业务冲突: 类型非法/状态非法/状态流转非法等)
"""

from datetime import datetime

import logging

from core.locks import get_lock
from core.helpers import bc_hash
from repositories.venue_repository import (
    VenueRepository,
    PARTNER_TYPES, PARTNER_LEVELS, PARTNER_STATUSES, PARTNER_TRANSITIONS,
    PARTNER_LEVEL_D, PARTNER_LEVEL_C, PARTNER_LEVEL_B,
    PARTNER_LEVEL_A, PARTNER_LEVEL_S,
    PARTNER_STATUS_PENDING, PARTNER_STATUS_REVIEWING,
    PARTNER_STATUS_SIGNED, PARTNER_STATUS_ACTIVE,
    PARTNER_STATUS_REJECTED,
    SUPPLY_MODE_AGENT, SUPPLY_MODE_DIRECT, SUPPLY_MODES,
    STOCKING_STATUS_ACTIVE, STOCKING_STATUS_SOLDOUT, STOCKING_STATUS_OFFLINE,
    LEVEL_TASTING_RATES,
    LEVEL_MONTHLY_QTY_THRESHOLD,
)


# ============================================================
# 多级分润比例(设计文档 5.3.1, 2026-08-29 决策 D-4 以文档为准)
# ============================================================

# 有代理: 本站 60% / 代理 20% / 酒店 20%
SHARE_PLATFORM_WITH_AGENT = 0.60
SHARE_AGENT = 0.20
SHARE_PARTNER = 0.20
# 无代理: 本站 80% / 酒店 20%
SHARE_PLATFORM_NO_AGENT = 0.80


logger = logging.getLogger(__name__)


class VenueService:
    """合作商业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: VenueRepository = VenueRepository()):
        self.repo = repo

    # ============================================================
    # 1. 合作商申请
    # ============================================================

    async def apply_partner(self, partner_type: str, partner_name: str,
                              credit_code: str, legal_person: str = "",
                              contact_phone: str = "",
                              contact_address: str = "",
                              longitude: float = 0.0, latitude: float = 0.0,
                              star_level: int = 0,
                              agent_id: int = None) -> dict:
        """合作商申请入驻

        规则:
            - partner_type 必须为 hotel/bar/club
            - partner_name 不能为空
            - credit_code 不能为空(统一社会信用代码)
            - 默认等级 D, 默认状态 pending

        Returns:
            新合作商信息(含 ID)

        Raises:
            ValueError: 类型/名称/信用代码非法
        """
        if partner_type not in PARTNER_TYPES:
            raise ValueError(
                f"合作商类型非法(partner_type={partner_type}, "
                f"合法值: {sorted(PARTNER_TYPES)})")
        if not partner_name or not partner_name.strip():
            raise ValueError("合作商名称不能为空")
        if not credit_code or not credit_code.strip():
            raise ValueError("统一社会信用代码不能为空")

        partner_id = await self.repo.next_partner_id()
        now = datetime.utcnow().isoformat()
        supply_mode = SUPPLY_MODE_AGENT if agent_id else SUPPLY_MODE_DIRECT
        partner = {
            "id": partner_id,
            "partnerType": partner_type,
            "partnerName": partner_name,
            "creditCode": credit_code,
            "legalPerson": legal_person,
            "contactPhone": contact_phone,
            "contactAddress": contact_address,
            "longitude": longitude,
            "latitude": latitude,
            "starLevel": star_level,
            "partnerLevel": PARTNER_LEVEL_D,
            "agentId": agent_id,
            "supplyMode": supply_mode,
            "svipPriceUsed": True,
            "tastingRate": LEVEL_TASTING_RATES[PARTNER_LEVEL_D],
            "paylaterQuota": 0.0,
            "status": PARTNER_STATUS_PENDING,
            "contractStart": "",
            "contractEnd": "",
            "blockchainHash": bc_hash(),
            "statusHistory": [],
            "levelHistory": [],
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repo.save_partner(partner)
        return partner

    async def get_partner(self, partner_id: int) -> dict:
        """查询合作商详情

        Raises:
            KeyError: 合作商不存在
        """
        partner = await self.repo.get_partner(partner_id)
        if partner is None:
            raise KeyError(f"合作商不存在(partnerId={partner_id})")
        return partner

    async def list_partners(self, partner_type: str = None,
                              status: str = None, level: str = None,
                              limit: int = 100) -> list[dict]:
        """查询合作商列表(支持按类型/状态/等级筛选)"""
        if partner_type and partner_type not in PARTNER_TYPES:
            raise ValueError(f"合作商类型非法(partner_type={partner_type})")
        if status and status not in PARTNER_STATUSES:
            raise ValueError(f"合作商状态非法(status={status})")
        if level and level not in PARTNER_LEVELS:
            raise ValueError(f"合作商等级非法(level={level})")
        return await self.repo.list_partners(partner_type=partner_type,
                                                status=status, level=level,
                                                limit=limit)

    async def list_admin_partners(self, partner_type: str = None,
                                      status: str = None, level: str = None,
                                      limit: int = 100) -> list[dict]:
        """管理端列表(同 list_partners, 但补充最近铺货统计)"""
        partners = await self.list_partners(partner_type=partner_type,
                                              status=status, level=level,
                                              limit=limit)
        # 为每个合作商补充场地数/铺货数
        result = []
        for p in partners:
            pid = p["id"]
            venues = await self.repo.list_venues(partner_id=pid, limit=1000)
            stockings = await self.repo.list_stockings(partner_id=pid,
                                                            limit=1000)
            p_copy = dict(p)
            p_copy["venueCount"] = len(venues)
            p_copy["stockingCount"] = len(stockings)
            p_copy["totalStockQty"] = sum(s.get("quantity", 0)
                                            for s in stockings)
            p_copy["totalSoldQty"] = sum(s.get("soldQty", 0)
                                          for s in stockings)
            result.append(p_copy)
        return result

    # ============================================================
    # 2. 合作商审核
    # ============================================================

    async def audit_partner(self, partner_id: int, action: str,
                              auditor_id: int = 0,
                              contract_start: str = "",
                              contract_end: str = "",
                              partner_level: str = None,
                              reject_reason: str = "") -> dict:
        """合作商审核

        Args:
            action: approve(通过)/reject(驳回)
            合同起止日期 + 初始等级(通过时)

        规则:
            - 当前状态必须为 pending 或 reviewing
            - approve → signed(签约)
            - reject → rejected

        Raises:
            KeyError: 合作商不存在
            ValueError: 当前状态不允许审核 / action 非法
        """
        if action not in ("approve", "reject"):
            raise ValueError(f"审核动作非法(action={action}, 合法: approve/reject)")

        lock_key = f"venue:partner:{partner_id}"
        async with get_lock(lock_key):
            partner = await self.repo.get_partner(partner_id)
            if partner is None:
                raise KeyError(f"合作商不存在(partnerId={partner_id})")

            current_status = partner.get("status")
            if current_status not in (PARTNER_STATUS_PENDING,
                                        PARTNER_STATUS_REVIEWING):
                raise ValueError(
                    f"当前状态不允许审核(当前={current_status}, "
                    f"需 pending/reviewing)")

            if action == "approve":
                # 校验合同日期
                if not contract_start or not contract_end:
                    raise ValueError("通过审核需提供合同起止日期")
                # 校验等级
                final_level = partner_level or PARTNER_LEVEL_D
                if final_level not in PARTNER_LEVELS:
                    raise ValueError(f"等级非法(level={final_level})")

                # 更新合作商信息
                partner["contractStart"] = contract_start
                partner["contractEnd"] = contract_end
                partner["partnerLevel"] = final_level
                partner["tastingRate"] = LEVEL_TASTING_RATES[final_level]
                partner["blockchainHash"] = bc_hash()
                await self.repo.save_partner(partner)
                # 状态流转 pending/reviewing → signed
                await self.repo.update_partner_status(
                    partner_id, PARTNER_STATUS_SIGNED,
                    remark=f"审核通过(auditorId={auditor_id})")
                return {
                    "partnerId": partner_id, "action": "approve",
                    "newStatus": PARTNER_STATUS_SIGNED,
                    "partnerLevel": final_level,
                    "contractStart": contract_start,
                    "contractEnd": contract_end,
                }
            else:
                # 驳回
                await self.repo.update_partner_status(
                    partner_id, PARTNER_STATUS_REJECTED,
                    remark=f"审核驳回(auditorId={auditor_id}, "
                           f"reason={reject_reason})")
                return {
                    "partnerId": partner_id, "action": "reject",
                    "newStatus": PARTNER_STATUS_REJECTED,
                    "rejectReason": reject_reason,
                }

    # ============================================================
    # 3. 状态流转
    # ============================================================

    async def transition(self, partner_id: int, target_status: str,
                            operator_id: int = 0,
                            remark: str = "") -> dict:
        """合作商状态流转(状态机校验)

        合法流转:
            pending → reviewing
            reviewing → signed / rejected
            signed → active
            active → suspended / terminated
            suspended → active / terminated
            rejected → pending

        Raises:
            KeyError: 合作商不存在
            ValueError: 目标状态非法 / 当前状态不可流转到目标
        """
        if target_status not in PARTNER_STATUSES:
            raise ValueError(f"目标状态非法(status={target_status})")

        lock_key = f"venue:partner:{partner_id}"
        async with get_lock(lock_key):
            partner = await self.repo.get_partner(partner_id)
            if partner is None:
                raise KeyError(f"合作商不存在(partnerId={partner_id})")

            current_status = partner.get("status")
            allowed = PARTNER_TRANSITIONS.get(current_status, set())
            if target_status not in allowed:
                raise ValueError(
                    f"状态流转非法(当前={current_status}, "
                    f"目标={target_status}, 合法目标={sorted(allowed) or '无'})")

            await self.repo.update_partner_status(
                partner_id, target_status,
                remark=remark or f"状态流转(operatorId={operator_id})")
            return {
                "partnerId": partner_id,
                "fromStatus": current_status,
                "toStatus": target_status,
                "operatorId": operator_id,
                "at": datetime.utcnow().isoformat(),
            }

    # ============================================================
    # 4. 合作商分级
    # ============================================================

    async def grade_partner(self, partner_id: int, new_level: str,
                              reason: str = "",
                              operator_id: int = 0) -> dict:
        """手动分级(升降级)

        规则:
            - 等级必须为 D/C/B/A/S
            - 同步更新品鉴酒比例

        Raises:
            KeyError: 合作商不存在
            ValueError: 等级非法
        """
        if new_level not in PARTNER_LEVELS:
            raise ValueError(f"等级非法(level={new_level})")

        lock_key = f"venue:partner:{partner_id}"
        async with get_lock(lock_key):
            partner = await self.repo.get_partner(partner_id)
            if partner is None:
                raise KeyError(f"合作商不存在(partnerId={partner_id})")

            old_level = partner.get("partnerLevel", PARTNER_LEVEL_D)
            await self.repo.update_partner_level(
                partner_id, new_level,
                reason=reason or f"手动分级(operatorId={operator_id})")
            return {
                "partnerId": partner_id,
                "fromLevel": old_level,
                "toLevel": new_level,
                "tastingRate": LEVEL_TASTING_RATES[new_level],
                "operatorId": operator_id,
                "at": datetime.utcnow().isoformat(),
            }

    async def auto_grade_by_sales(self, partner_id: int,
                                     monthly_qty: int) -> dict:
        """基于月销量自动升级

        规则:
            - 月销≥100 → S
            - 月销≥50 → A
            - 月销≥20 → B
            - 月销≥5 → C
            - 否则保持 D

        Raises:
            KeyError: 合作商不存在
        """
        # 计算目标等级
        if monthly_qty >= LEVEL_MONTHLY_QTY_THRESHOLD[PARTNER_LEVEL_S]:
            target = PARTNER_LEVEL_S
        elif monthly_qty >= LEVEL_MONTHLY_QTY_THRESHOLD[PARTNER_LEVEL_A]:
            target = PARTNER_LEVEL_A
        elif monthly_qty >= LEVEL_MONTHLY_QTY_THRESHOLD[PARTNER_LEVEL_B]:
            target = PARTNER_LEVEL_B
        elif monthly_qty >= LEVEL_MONTHLY_QTY_THRESHOLD[PARTNER_LEVEL_C]:
            target = PARTNER_LEVEL_C
        else:
            target = PARTNER_LEVEL_D

        return await self.grade_partner(
            partner_id, target,
            reason=f"自动升级(月销={monthly_qty})")

    # ============================================================
    # 5. 场地管理
    # ============================================================

    async def create_venue(self, partner_id: int, venue_name: str,
                             venue_type: str, address: str,
                             capacity: int = 0,
                             manager_name: str = "",
                             manager_phone: str = "",
                             business_hours: str = "") -> dict:
        """创建场地

        Raises:
            KeyError: 合作商不存在
            ValueError: 名称/类型为空
        """
        partner = await self.repo.get_partner(partner_id)
        if partner is None:
            raise KeyError(f"合作商不存在(partnerId={partner_id})")

        if not venue_name or not venue_name.strip():
            raise ValueError("场地名称不能为空")
        if not venue_type or not venue_type.strip():
            raise ValueError("场地类型不能为空")

        venue_id = await self.repo.next_venue_id()
        now = datetime.utcnow().isoformat()
        venue = {
            "id": venue_id,
            "partnerId": partner_id,
            "venueName": venue_name,
            "venueType": venue_type,
            "address": address,
            "capacity": capacity,
            "managerName": manager_name,
            "managerPhone": manager_phone,
            "businessHours": business_hours,
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repo.save_venue(venue)
        return venue

    async def list_venues(self, partner_id: int = None,
                             venue_type: str = None,
                             limit: int = 100) -> list[dict]:
        """查询场地列表(支持按合作商/类型筛选)"""
        return await self.repo.list_venues(partner_id=partner_id,
                                              venue_type=venue_type,
                                              limit=limit)

    async def get_venue(self, venue_id: int) -> dict:
        """查询场地详情

        Raises:
            KeyError: 场地不存在
        """
        venue = await self.repo.get_venue(venue_id)
        if venue is None:
            raise KeyError(f"场地不存在(venueId={venue_id})")
        return venue

    async def update_venue(self, venue_id: int, venue_name: str = None,
                             address: str = None, capacity: int = None,
                             manager_name: str = None,
                             manager_phone: str = None,
                             business_hours: str = None,
                             status: str = None) -> dict:
        """更新场地

        Raises:
            KeyError: 场地不存在
        """
        venue = await self.repo.get_venue(venue_id)
        if venue is None:
            raise KeyError(f"场地不存在(venueId={venue_id})")

        if venue_name is not None:
            venue["venueName"] = venue_name
        if address is not None:
            venue["address"] = address
        if capacity is not None:
            venue["capacity"] = capacity
        if manager_name is not None:
            venue["managerName"] = manager_name
        if manager_phone is not None:
            venue["managerPhone"] = manager_phone
        if business_hours is not None:
            venue["businessHours"] = business_hours
        if status is not None:
            venue["status"] = status
        venue["updatedAt"] = datetime.utcnow().isoformat()
        await self.repo.save_venue(venue)
        return venue

    async def delete_venue(self, venue_id: int) -> dict:
        """删除场地

        Raises:
            KeyError: 场地不存在
        """
        venue = await self.repo.get_venue(venue_id)
        if venue is None:
            raise KeyError(f"场地不存在(venueId={venue_id})")
        ok = await self.repo.delete_venue(venue_id)
        if not ok:
            raise KeyError(f"场地删除失败(venueId={venue_id})")
        return {"venueId": venue_id, "deleted": True}

    # ============================================================
    # 6. 铺货管理
    # ============================================================

    async def add_stocking(self, partner_id: int, venue_id: int,
                              product_id: str, product_name: str,
                              quantity: int,
                              svip_price: float, retail_price: float,
                              supply_mode: str = SUPPLY_MODE_DIRECT,
                              agent_id: int = None,
                              stockings_date: str = "") -> dict:
        """新增铺货记录

        规则:
            - 合作商必须存在且为 active 状态
            - 场地必须存在且属于该合作商
            - 数量必须 > 0

        Raises:
            KeyError: 合作商/场地不存在
            ValueError: 合作商非合作中状态/数量非法/场地不属于合作商
        """
        partner = await self.repo.get_partner(partner_id)
        if partner is None:
            raise KeyError(f"合作商不存在(partnerId={partner_id})")
        if partner.get("status") != PARTNER_STATUS_ACTIVE:
            raise ValueError(
                f"合作商状态非合作中(当前={partner.get('status')}, 需 active)")

        venue = await self.repo.get_venue(venue_id)
        if venue is None:
            raise KeyError(f"场地不存在(venueId={venue_id})")
        if venue.get("partnerId") != partner_id:
            raise ValueError(
                f"场地不属于该合作商(venueId={venue_id}, "
                f"partnerId={venue.get('partnerId')})")

        if quantity <= 0:
            raise ValueError(f"铺货数量必须>0(quantity={quantity})")
        if supply_mode not in SUPPLY_MODES:
            raise ValueError(f"供货模式非法(supply_mode={supply_mode})")

        stocking_id = await self.repo.next_stocking_id()
        now = datetime.utcnow().isoformat()
        # 差价利润 = (零售价 - SVIP进货价) × 数量
        profit_diff = (retail_price - svip_price) * quantity
        stocking = {
            "id": stocking_id,
            "partnerId": partner_id,
            "venueId": venue_id,
            "productId": product_id,
            "productName": product_name,
            "quantity": quantity,
            "soldQty": 0,
            "svipPrice": svip_price,
            "retailPrice": retail_price,
            "profitDiff": round(profit_diff, 2),
            "supplyMode": supply_mode,
            "agentId": agent_id,
            "stockingsDate": stockings_date or now[:10],
            "status": STOCKING_STATUS_ACTIVE,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repo.save_stocking(stocking)
        return stocking

    async def list_stockings(self, partner_id: int = None,
                                venue_id: int = None, status: str = None,
                                limit: int = 100) -> list[dict]:
        """查询铺货记录"""
        return await self.repo.list_stockings(partner_id=partner_id,
                                                  venue_id=venue_id,
                                                  status=status, limit=limit)

    async def update_stocking_status(self, stocking_id: int, new_status: str,
                                        sold_qty: int = 0) -> dict:
        """更新铺货状态(可追加已售数量)

        Raises:
            KeyError: 铺货记录不存在
            ValueError: 状态非法
        """
        if new_status not in {STOCKING_STATUS_ACTIVE, STOCKING_STATUS_SOLDOUT,
                                STOCKING_STATUS_OFFLINE}:
            raise ValueError(f"铺货状态非法(status={new_status})")

        stocking = await self.repo.get_stocking(stocking_id)
        if stocking is None:
            raise KeyError(f"铺货记录不存在(stockingId={stocking_id})")

        lock_key = f"venue:stocking:{stocking_id}"
        async with get_lock(lock_key):
            await self.repo.update_stocking_status(stocking_id, new_status,
                                                       sold_qty=sold_qty)
            stocking = await self.repo.get_stocking(stocking_id)
            return stocking

    # ============================================================
    # 7. 佣金结算
    # ============================================================

    async def settle_commission(self, partner_id: int,
                                   stockings_ids: list = None) -> dict:
        """合作商多级分润结算(基于差价利润, 2026-08-29 决策 D-4 以文档 5.3.1 为准)

        规则:
            - 有代理: 本站 60% / 代理 20% / 酒店 20%
            - 无代理: 本站 80% / 酒店 20%
            - 代理判定: 铺货记录携带 agentId(代理供货模式)
            - 品鉴酒成本由本站承担(数量按等级比例分配)
            - 仅结算 active 状态的铺货记录

        Returns:
            {partnerId, partnerLevel, totalProfitDiff, platformShare,
             agentShare, partnerShare, hasAgent, stockingsCount, settleDate}

        Raises:
            KeyError: 合作商不存在
            ValueError: 合作商非合作中状态
        """
        partner = await self.repo.get_partner(partner_id)
        if partner is None:
            raise KeyError(f"合作商不存在(partnerId={partner_id})")
        if partner.get("status") != PARTNER_STATUS_ACTIVE:
            raise ValueError(
                f"合作商状态非合作中(当前={partner.get('status')}, 需 active)")

        lock_key = f"venue:settle:{partner_id}"
        async with get_lock(lock_key):
            level = partner.get("partnerLevel", PARTNER_LEVEL_D)
            tasting_rate = LEVEL_TASTING_RATES.get(level, 0.0)

            # 取铺货记录(全部或指定)
            if stockings_ids:
                stockings = []
                for sid in stockings_ids:
                    s = await self.repo.get_stocking(sid)
                    if s is not None:
                        stockings.append(s)
            else:
                stockings = await self.repo.list_stockings(
                    partner_id=partner_id,
                    status=STOCKING_STATUS_ACTIVE, limit=10000)

            total_profit = sum(s.get("profitDiff", 0) for s in stockings)
            # 代理判定(铺货记录含 agentId 即有代理)
            has_agent = any(s.get("agentId") for s in stockings)
            agent_id = next((s.get("agentId") for s in stockings
                             if s.get("agentId")), None)
            if has_agent:
                platform_share = total_profit * SHARE_PLATFORM_WITH_AGENT
                agent_share = total_profit * SHARE_AGENT
            else:
                platform_share = total_profit * SHARE_PLATFORM_NO_AGENT
                agent_share = 0.0
            partner_share = total_profit * SHARE_PARTNER
            # 品鉴酒数量(免费分配, 成本本站承担)
            total_qty = sum(s.get("quantity", 0) for s in stockings)
            tasting_qty = int(total_qty * tasting_rate)

            # 将结算后的铺货记录标记为已下架(模拟"已结算")
            for s in stockings:
                await self.repo.update_stocking_status(
                    s["id"], STOCKING_STATUS_OFFLINE)

            settle_date = datetime.utcnow().isoformat()
            result = {
                "partnerId": partner_id,
                "partnerLevel": level,
                "stockingsCount": len(stockings),
                "totalQuantity": total_qty,
                "totalProfitDiff": round(total_profit, 2),
                "platformShare": round(platform_share, 2),
                "agentShare": round(agent_share, 2),
                "partnerShare": round(partner_share, 2),
                "hasAgent": has_agent,
                "tastingQty": tasting_qty,
                "tastingRate": tasting_rate,
                "settleDate": settle_date,
                "blockchainHash": bc_hash(),
            }

            # 统一分润总账记账(P1: AI智能管理模块, D-7 diff_profit 轨道)
            # 记账失败不阻断结算主流程(旁路对账)
            try:
                from services.role_service import RoleService
                from repositories.role_repository import (
                    ROLE_PARTNER, ROLE_AGENT, ROLE_PLATFORM,
                    PROFIT_BASIS_DIFF_PROFIT,
                )
                role_svc = RoleService()
                ledger_prefix = f"VEN-{partner_id}-{settle_date}"
                await role_svc.record_external_settlement(
                    ledger_no=f"{ledger_prefix}-platform",
                    source_module="venue", role_code=ROLE_PLATFORM,
                    user_id=0, basis=PROFIT_BASIS_DIFF_PROFIT,
                    base=total_profit, rate=(
                        0.60 if has_agent else 0.80),
                    amount=round(platform_share, 2), ref_no=str(partner_id),
                    note=f"合作商结算本站份额(差价利润)")
                await role_svc.record_external_settlement(
                    ledger_no=f"{ledger_prefix}-partner",
                    source_module="venue", role_code=ROLE_PARTNER,
                    user_id=partner_id, basis=PROFIT_BASIS_DIFF_PROFIT,
                    base=total_profit, rate=0.20,
                    amount=round(partner_share, 2), ref_no=str(partner_id),
                    note=f"合作商结算份额(差价利润)")
                if has_agent and agent_share > 0:
                    await role_svc.record_external_settlement(
                        ledger_no=f"{ledger_prefix}-agent",
                        source_module="venue", role_code=ROLE_AGENT,
                        user_id=agent_id or 0,
                        basis=PROFIT_BASIS_DIFF_PROFIT,
                        base=total_profit, rate=0.20,
                        amount=round(agent_share, 2), ref_no=str(partner_id),
                        note="合作商结算代理份额(差价利润)")
                result["ledgerRecorded"] = True
            except Exception as e:
                logger.warning("venue_ledger_record_failed partner=%r: %s",
                              partner_id, e)
                result["ledgerRecorded"] = False

            return result

    # ============================================================
    # 8. 合作统计
    # ============================================================

    async def get_stats(self) -> dict:
        """合作统计(按类型/状态/等级聚合)"""
        return await self.repo.stats()
