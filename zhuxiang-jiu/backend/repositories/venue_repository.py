"""酒店酒吧会所合作商模块数据访问层(双模式: 内存 + Redis)

表清单:
    P0: venue_partners(合作商) + venues(场地)
        + venue_stockings(铺货记录)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 合作商: 按 id 主键, status 状态机(pending/reviewing/signed/active/suspended/terminated/rejected)
    - 场地: 按 id 主键, partner_id 外键索引
    - 铺货记录: 按 id 主键, partner_id/venue_id 索引, status(铺货中/已售罄/已下架)
"""

import json
from datetime import datetime
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 合作商类型
# ============================================================

PARTNER_TYPE_HOTEL = "hotel"  # 酒店
PARTNER_TYPE_BAR = "bar"        # 酒吧
PARTNER_TYPE_CLUB = "club"       # 会所

PARTNER_TYPES = {PARTNER_TYPE_HOTEL, PARTNER_TYPE_BAR, PARTNER_TYPE_CLUB}

# 合作商等级(从低到高)
PARTNER_LEVEL_D = "D"   # 新入驻
PARTNER_LEVEL_C = "C"   # 月销≥5瓶
PARTNER_LEVEL_B = "B"   # 月销≥20瓶+一星
PARTNER_LEVEL_A = "A"   # 月销≥50瓶+二星
PARTNER_LEVEL_S = "S"   # 月销≥100瓶+三星

PARTNER_LEVELS = [PARTNER_LEVEL_D, PARTNER_LEVEL_C, PARTNER_LEVEL_B,
                  PARTNER_LEVEL_A, PARTNER_LEVEL_S]

# 合作商状态机(申请 → 审核 → 签约 → 合作 → 终止)
PARTNER_STATUS_PENDING = "pending"          # 申请中
PARTNER_STATUS_REVIEWING = "reviewing"      # 审核中
PARTNER_STATUS_SIGNED = "signed"            # 已签约
PARTNER_STATUS_ACTIVE = "active"            # 合作中
PARTNER_STATUS_SUSPENDED = "suspended"      # 暂停
PARTNER_STATUS_TERMINATED = "terminated"    # 终止
PARTNER_STATUS_REJECTED = "rejected"        # 驳回

PARTNER_STATUSES = {
    PARTNER_STATUS_PENDING, PARTNER_STATUS_REVIEWING,
    PARTNER_STATUS_SIGNED, PARTNER_STATUS_ACTIVE,
    PARTNER_STATUS_SUSPENDED, PARTNER_STATUS_TERMINATED,
    PARTNER_STATUS_REJECTED,
}

# 合法状态流转(状态机转移表)
PARTNER_TRANSITIONS = {
    PARTNER_STATUS_PENDING: {PARTNER_STATUS_REVIEWING},
    PARTNER_STATUS_REVIEWING: {PARTNER_STATUS_SIGNED, PARTNER_STATUS_REJECTED},
    PARTNER_STATUS_SIGNED: {PARTNER_STATUS_ACTIVE},
    PARTNER_STATUS_ACTIVE: {PARTNER_STATUS_SUSPENDED, PARTNER_STATUS_TERMINATED},
    PARTNER_STATUS_SUSPENDED: {PARTNER_STATUS_ACTIVE, PARTNER_STATUS_TERMINATED},
    PARTNER_STATUS_TERMINATED: set(),
    PARTNER_STATUS_REJECTED: {PARTNER_STATUS_PENDING},
}

# 供货模式
SUPPLY_MODE_AGENT = "agent"        # 代理商供货
SUPPLY_MODE_DIRECT = "direct"      # 本站直供
SUPPLY_MODE_NEIGHBOR = "neighbor"  # 邻区调货

SUPPLY_MODES = {SUPPLY_MODE_AGENT, SUPPLY_MODE_DIRECT, SUPPLY_MODE_NEIGHBOR}

# 铺货状态
STOCKING_STATUS_ACTIVE = "active"      # 铺货中
STOCKING_STATUS_SOLDOUT = "soldout"    # 已售罄
STOCKING_STATUS_OFFLINE = "offline"    # 已下架

STOCKING_STATUSES = {
    STOCKING_STATUS_ACTIVE, STOCKING_STATUS_SOLDOUT, STOCKING_STATUS_OFFLINE,
}


class VenueRepository:
    """酒店酒吧会所合作商数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # ID 生成
    # ============================================================

    async def next_partner_id(self) -> int:
        """生成合作商ID"""
        if is_redis_mode():
            return await self._redis_next_id("partner")
        return self._mem_next_id("_venue_partner_seq")

    async def next_venue_id(self) -> int:
        """生成场地ID"""
        if is_redis_mode():
            return await self._redis_next_id("venue")
        return self._mem_next_id("_venue_venue_seq")

    async def next_stocking_id(self) -> int:
        """生成铺货记录ID"""
        if is_redis_mode():
            return await self._redis_next_id("stocking")
        return self._mem_next_id("_venue_stocking_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("venue", entity, "seq"))

    # ============================================================
    # 合作商 CRUD
    # ============================================================

    async def get_partner(self, partner_id: int) -> Optional[dict]:
        """按ID查询合作商"""
        if is_redis_mode():
            return await self._redis_get_partner(partner_id)
        return self._mem_get_partner(partner_id)

    async def save_partner(self, partner: dict) -> None:
        """保存合作商(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_partner(partner)
        else:
            self._mem_save_partner(partner)

    async def list_partners(self, partner_type: str = None,
                              status: str = None, level: str = None,
                              limit: int = 100) -> list[dict]:
        """查询合作商列表(支持按类型/状态/等级筛选)"""
        if is_redis_mode():
            return await self._redis_list_partners(partner_type, status, level, limit)
        return self._mem_list_partners(partner_type, status, level, limit)

    async def delete_partner(self, partner_id: int) -> bool:
        """删除合作商"""
        if is_redis_mode():
            return await self._redis_delete_partner(partner_id)
        return self._mem_delete_partner(partner_id)

    async def update_partner_status(self, partner_id: int, new_status: str,
                                       remark: str = "") -> None:
        """更新合作商状态(追加状态变更记录)"""
        if is_redis_mode():
            await self._redis_update_partner_status(partner_id, new_status, remark)
        else:
            self._mem_update_partner_status(partner_id, new_status, remark)

    async def update_partner_level(self, partner_id: int, new_level: str,
                                     reason: str = "") -> None:
        """更新合作商等级(追加等级变更记录)"""
        if is_redis_mode():
            await self._redis_update_partner_level(partner_id, new_level, reason)
        else:
            self._mem_update_partner_level(partner_id, new_level, reason)

    # ============================================================
    # 场地 CRUD
    # ============================================================

    async def get_venue(self, venue_id: int) -> Optional[dict]:
        """按ID查询场地"""
        if is_redis_mode():
            return await self._redis_get_venue(venue_id)
        return self._mem_get_venue(venue_id)

    async def save_venue(self, venue: dict) -> None:
        """保存场地(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_venue(venue)
        else:
            self._mem_save_venue(venue)

    async def list_venues(self, partner_id: int = None,
                            venue_type: str = None, limit: int = 100) -> list[dict]:
        """查询场地列表(支持按合作商/类型筛选)"""
        if is_redis_mode():
            return await self._redis_list_venues(partner_id, venue_type, limit)
        return self._mem_list_venues(partner_id, venue_type, limit)

    async def delete_venue(self, venue_id: int) -> bool:
        """删除场地"""
        if is_redis_mode():
            return await self._redis_delete_venue(venue_id)
        return self._mem_delete_venue(venue_id)

    # ============================================================
    # 铺货记录 CRUD
    # ============================================================

    async def get_stocking(self, stocking_id: int) -> Optional[dict]:
        """按ID查询铺货记录"""
        if is_redis_mode():
            return await self._redis_get_stocking(stocking_id)
        return self._mem_get_stocking(stocking_id)

    async def save_stocking(self, stocking: dict) -> None:
        """保存铺货记录(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_stocking(stocking)
        else:
            self._mem_save_stocking(stocking)

    async def list_stockings(self, partner_id: int = None,
                                venue_id: int = None, status: str = None,
                                limit: int = 100) -> list[dict]:
        """查询铺货记录(支持按合作商/场地/状态筛选)"""
        if is_redis_mode():
            return await self._redis_list_stockings(partner_id, venue_id, status, limit)
        return self._mem_list_stockings(partner_id, venue_id, status, limit)

    async def update_stocking_status(self, stocking_id: int, new_status: str,
                                       sold_qty: int = 0) -> None:
        """更新铺货状态(含已售数量)"""
        if is_redis_mode():
            await self._redis_update_stocking_status(stocking_id, new_status, sold_qty)
        else:
            self._mem_update_stocking_status(stocking_id, new_status, sold_qty)

    # ============================================================
    # 统计
    # ============================================================

    async def stats(self) -> dict:
        """合作统计(按类型/状态/等级聚合)"""
        if is_redis_mode():
            return await self._redis_stats()
        return self._mem_stats()

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含合作商模块的键(懒初始化)"""
        if "venue_partners" not in self.store:
            self.store["venue_partners"] = {}              # partnerId → partner
            self.store["venues"] = {}                       # venueId → venue
            self.store["venues_by_partner"] = {}             # partnerId → [venueId, ...]
            self.store["venue_stockings"] = {}              # stockingId → stocking
            self.store["venue_stockings_by_partner"] = {}    # partnerId → [stockingId, ...]
            self.store["venue_stockings_by_venue"] = {}      # venueId → [stockingId, ...]
            self.store["_venue_partner_seq"] = 0
            self.store["_venue_venue_seq"] = 0
            self.store["_venue_stocking_seq"] = 0

    # --- 合作商 ---

    def _mem_get_partner(self, partner_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["venue_partners"].get(partner_id)

    def _mem_save_partner(self, partner: dict) -> None:
        self._ensure_store()
        pid = partner["id"]
        now = datetime.utcnow().isoformat()
        partner.setdefault("createdAt", now)
        partner["updatedAt"] = now
        self.store["venue_partners"][pid] = partner

    def _mem_list_partners(self, partner_type: str = None,
                            status: str = None, level: str = None,
                            limit: int = 100) -> list[dict]:
        self._ensure_store()
        partners = list(self.store["venue_partners"].values())
        if partner_type:
            partners = [p for p in partners if p.get("partnerType") == partner_type]
        if status:
            partners = [p for p in partners if p.get("status") == status]
        if level:
            partners = [p for p in partners if p.get("partnerLevel") == level]
        partners.sort(key=lambda p: p.get("createdAt", ""), reverse=True)
        return partners[:limit]

    def _mem_delete_partner(self, partner_id: int) -> bool:
        self._ensure_store()
        existed = self.store["venue_partners"].pop(partner_id, None) is not None
        self.store["venues_by_partner"].pop(partner_id, None)
        self.store["venue_stockings_by_partner"].pop(partner_id, None)
        return existed

    def _mem_update_partner_status(self, partner_id: int, new_status: str,
                                      remark: str = "") -> None:
        self._ensure_store()
        partner = self.store["venue_partners"].get(partner_id)
        if partner is None:
            return
        old_status = partner.get("status")
        partner["status"] = new_status
        partner["statusHistory"] = partner.get("statusHistory", [])
        partner["statusHistory"].append({
            "from": old_status, "to": new_status,
            "at": datetime.utcnow().isoformat(), "remark": remark,
        })
        partner["updatedAt"] = datetime.utcnow().isoformat()

    def _mem_update_partner_level(self, partner_id: int, new_level: str,
                                     reason: str = "") -> None:
        self._ensure_store()
        partner = self.store["venue_partners"].get(partner_id)
        if partner is None:
            return
        old_level = partner.get("partnerLevel")
        partner["partnerLevel"] = new_level
        partner["levelHistory"] = partner.get("levelHistory", [])
        partner["levelHistory"].append({
            "from": old_level, "to": new_level,
            "at": datetime.utcnow().isoformat(), "reason": reason,
        })
        partner["updatedAt"] = datetime.utcnow().isoformat()
        # 同步调整品鉴酒比例
        partner["tastingRate"] = _level_tasting_rate(new_level)

    # --- 场地 ---

    def _mem_get_venue(self, venue_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["venues"].get(venue_id)

    def _mem_save_venue(self, venue: dict) -> None:
        self._ensure_store()
        vid = venue["id"]
        now = datetime.utcnow().isoformat()
        venue.setdefault("createdAt", now)
        venue["updatedAt"] = now
        self.store["venues"][vid] = venue
        pid = venue.get("partnerId")
        if pid is not None:
            if pid not in self.store["venues_by_partner"]:
                self.store["venues_by_partner"][pid] = []
            if vid not in self.store["venues_by_partner"][pid]:
                self.store["venues_by_partner"][pid].append(vid)

    def _mem_list_venues(self, partner_id: int = None,
                          venue_type: str = None, limit: int = 100) -> list[dict]:
        self._ensure_store()
        if partner_id is not None:
            vids = self.store["venues_by_partner"].get(partner_id, [])
            venues = [self.store["venues"][v] for v in vids
                       if v in self.store["venues"]]
        else:
            venues = list(self.store["venues"].values())
        if venue_type:
            venues = [v for v in venues if v.get("venueType") == venue_type]
        venues.sort(key=lambda v: v.get("createdAt", ""), reverse=True)
        return venues[:limit]

    def _mem_delete_venue(self, venue_id: int) -> bool:
        self._ensure_store()
        venue = self.store["venues"].pop(venue_id, None)
        if venue is None:
            return False
        pid = venue.get("partnerId")
        if pid is not None and pid in self.store["venues_by_partner"]:
            if venue_id in self.store["venues_by_partner"][pid]:
                self.store["venues_by_partner"][pid].remove(venue_id)
        return True

    # --- 铺货记录 ---

    def _mem_get_stocking(self, stocking_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["venue_stockings"].get(stocking_id)

    def _mem_save_stocking(self, stocking: dict) -> None:
        self._ensure_store()
        sid = stocking["id"]
        now = datetime.utcnow().isoformat()
        stocking.setdefault("createdAt", now)
        stocking["updatedAt"] = now
        self.store["venue_stockings"][sid] = stocking
        pid = stocking.get("partnerId")
        if pid is not None:
            if pid not in self.store["venue_stockings_by_partner"]:
                self.store["venue_stockings_by_partner"][pid] = []
            if sid not in self.store["venue_stockings_by_partner"][pid]:
                self.store["venue_stockings_by_partner"][pid].append(sid)
        vid = stocking.get("venueId")
        if vid is not None:
            if vid not in self.store["venue_stockings_by_venue"]:
                self.store["venue_stockings_by_venue"][vid] = []
            if sid not in self.store["venue_stockings_by_venue"][vid]:
                self.store["venue_stockings_by_venue"][vid].append(sid)

    def _mem_list_stockings(self, partner_id: int = None,
                             venue_id: int = None, status: str = None,
                             limit: int = 100) -> list[dict]:
        self._ensure_store()
        if partner_id is not None:
            sids = self.store["venue_stockings_by_partner"].get(partner_id, [])
            stockings = [self.store["venue_stockings"][s] for s in sids
                          if s in self.store["venue_stockings"]]
        elif venue_id is not None:
            sids = self.store["venue_stockings_by_venue"].get(venue_id, [])
            stockings = [self.store["venue_stockings"][s] for s in sids
                          if s in self.store["venue_stockings"]]
        else:
            stockings = list(self.store["venue_stockings"].values())
        if status:
            stockings = [s for s in stockings if s.get("status") == status]
        stockings.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return stockings[:limit]

    def _mem_update_stocking_status(self, stocking_id: int, new_status: str,
                                       sold_qty: int = 0) -> None:
        self._ensure_store()
        stocking = self.store["venue_stockings"].get(stocking_id)
        if stocking is None:
            return
        stocking["status"] = new_status
        if sold_qty:
            stocking["soldQty"] = stocking.get("soldQty", 0) + sold_qty
        stocking["updatedAt"] = datetime.utcnow().isoformat()

    def _mem_stats(self) -> dict:
        self._ensure_store()
        partners = list(self.store["venue_partners"].values())
        venues = list(self.store["venues"].values())
        stockings = list(self.store["venue_stockings"].values())
        # 按类型聚合
        by_type = {}
        by_status = {}
        by_level = {}
        for p in partners:
            t = p.get("partnerType", "unknown")
            s = p.get("status", "unknown")
            l = p.get("partnerLevel", "D")
            by_type[t] = by_type.get(t, 0) + 1
            by_status[s] = by_status.get(s, 0) + 1
            by_level[l] = by_level.get(l, 0) + 1
        # 铺货总数量
        total_stock_qty = sum(s.get("quantity", 0) for s in stockings)
        total_sold_qty = sum(s.get("soldQty", 0) for s in stockings)
        return {
            "totalPartners": len(partners),
            "totalVenues": len(venues),
            "totalStockings": len(stockings),
            "totalStockQty": total_stock_qty,
            "totalSoldQty": total_sold_qty,
            "byType": by_type,
            "byStatus": by_status,
            "byLevel": by_level,
            "generatedAt": datetime.utcnow().isoformat(),
        }

    # ============================================================
    # Redis 模式实现
    # ============================================================

    # --- 合作商 ---

    async def _redis_get_partner(self, partner_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("venue", "partner", partner_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_partner(self, partner: dict) -> None:
        client = await get_redis_client()
        pid = partner["id"]
        now = datetime.utcnow().isoformat()
        partner.setdefault("createdAt", now)
        partner["updatedAt"] = now
        await client.set(_k("venue", "partner", pid),
                        json.dumps(partner, ensure_ascii=False))
        await client.sadd(_k("venue", "partner", "ids"), pid)

    async def _redis_list_partners(self, partner_type: str = None,
                                      status: str = None, level: str = None,
                                      limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        ids = await client.smembers(_k("venue", "partner", "ids"))
        partners = []
        for pid in ids:
            data = await client.get(_k("venue", "partner", pid))
            if data:
                p = json.loads(data)
                if partner_type and p.get("partnerType") != partner_type:
                    continue
                if status and p.get("status") != status:
                    continue
                if level and p.get("partnerLevel") != level:
                    continue
                partners.append(p)
        partners.sort(key=lambda p: p.get("createdAt", ""), reverse=True)
        return partners[:limit]

    async def _redis_delete_partner(self, partner_id: int) -> bool:
        client = await get_redis_client()
        existed = await client.exists(_k("venue", "partner", partner_id))
        if not existed:
            return False
        await client.delete(_k("venue", "partner", partner_id))
        await client.srem(_k("venue", "partner", "ids"), partner_id)
        return True

    async def _redis_update_partner_status(self, partner_id: int,
                                              new_status: str,
                                              remark: str = "") -> None:
        client = await get_redis_client()
        data = await client.get(_k("venue", "partner", partner_id))
        if not data:
            return
        partner = json.loads(data)
        old_status = partner.get("status")
        partner["status"] = new_status
        partner.setdefault("statusHistory", []).append({
            "from": old_status, "to": new_status,
            "at": datetime.utcnow().isoformat(), "remark": remark,
        })
        partner["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("venue", "partner", partner_id),
                        json.dumps(partner, ensure_ascii=False))

    async def _redis_update_partner_level(self, partner_id: int, new_level: str,
                                             reason: str = "") -> None:
        client = await get_redis_client()
        data = await client.get(_k("venue", "partner", partner_id))
        if not data:
            return
        partner = json.loads(data)
        old_level = partner.get("partnerLevel")
        partner["partnerLevel"] = new_level
        partner.setdefault("levelHistory", []).append({
            "from": old_level, "to": new_level,
            "at": datetime.utcnow().isoformat(), "reason": reason,
        })
        partner["tastingRate"] = _level_tasting_rate(new_level)
        partner["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("venue", "partner", partner_id),
                        json.dumps(partner, ensure_ascii=False))

    # --- 场地 ---

    async def _redis_get_venue(self, venue_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("venue", "venue", venue_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_venue(self, venue: dict) -> None:
        client = await get_redis_client()
        vid = venue["id"]
        now = datetime.utcnow().isoformat()
        venue.setdefault("createdAt", now)
        venue["updatedAt"] = now
        await client.set(_k("venue", "venue", vid),
                        json.dumps(venue, ensure_ascii=False))
        pid = venue.get("partnerId")
        if pid is not None:
            await client.sadd(_k("venue", "venue_by_partner", pid), vid)
            await client.sadd(_k("venue", "venue", "ids"), vid)

    async def _redis_list_venues(self, partner_id: int = None,
                                    venue_type: str = None,
                                    limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        if partner_id is not None:
            vids = await client.smembers(_k("venue", "venue_by_partner", partner_id))
        else:
            vids = await client.smembers(_k("venue", "venue", "ids"))
        venues = []
        for vid in vids:
            data = await client.get(_k("venue", "venue", vid))
            if data:
                v = json.loads(data)
                if venue_type and v.get("venueType") != venue_type:
                    continue
                venues.append(v)
        venues.sort(key=lambda v: v.get("createdAt", ""), reverse=True)
        return venues[:limit]

    async def _redis_delete_venue(self, venue_id: int) -> bool:
        client = await get_redis_client()
        data = await client.get(_k("venue", "venue", venue_id))
        if not data:
            return False
        venue = json.loads(data)
        await client.delete(_k("venue", "venue", venue_id))
        await client.srem(_k("venue", "venue", "ids"), venue_id)
        pid = venue.get("partnerId")
        if pid is not None:
            await client.srem(_k("venue", "venue_by_partner", pid), venue_id)
        return True

    # --- 铺货记录 ---

    async def _redis_get_stocking(self, stocking_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("venue", "stocking", stocking_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_stocking(self, stocking: dict) -> None:
        client = await get_redis_client()
        sid = stocking["id"]
        now = datetime.utcnow().isoformat()
        stocking.setdefault("createdAt", now)
        stocking["updatedAt"] = now
        await client.set(_k("venue", "stocking", sid),
                        json.dumps(stocking, ensure_ascii=False))
        pid = stocking.get("partnerId")
        if pid is not None:
            await client.sadd(_k("venue", "stocking_by_partner", pid), sid)
        vid = stocking.get("venueId")
        if vid is not None:
            await client.sadd(_k("venue", "stocking_by_venue", vid), sid)
        await client.sadd(_k("venue", "stocking", "ids"), sid)

    async def _redis_list_stockings(self, partner_id: int = None,
                                        venue_id: int = None,
                                        status: str = None,
                                        limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        if partner_id is not None:
            sids = await client.smembers(_k("venue", "stocking_by_partner", partner_id))
        elif venue_id is not None:
            sids = await client.smembers(_k("venue", "stocking_by_venue", venue_id))
        else:
            sids = await client.smembers(_k("venue", "stocking", "ids"))
        stockings = []
        for sid in sids:
            data = await client.get(_k("venue", "stocking", sid))
            if data:
                s = json.loads(data)
                if status and s.get("status") != status:
                    continue
                stockings.append(s)
        stockings.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return stockings[:limit]

    async def _redis_update_stocking_status(self, stocking_id: int,
                                               new_status: str,
                                               sold_qty: int = 0) -> None:
        client = await get_redis_client()
        data = await client.get(_k("venue", "stocking", stocking_id))
        if not data:
            return
        stocking = json.loads(data)
        stocking["status"] = new_status
        if sold_qty:
            stocking["soldQty"] = stocking.get("soldQty", 0) + sold_qty
        stocking["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("venue", "stocking", stocking_id),
                        json.dumps(stocking, ensure_ascii=False))

    async def _redis_stats(self) -> dict:
        client = await get_redis_client()
        partner_ids = await client.smembers(_k("venue", "partner", "ids"))
        venue_ids = await client.smembers(_k("venue", "venue", "ids"))
        stocking_ids = await client.smembers(_k("venue", "stocking", "ids"))
        by_type, by_status, by_level = {}, {}, {}
        total_stock_qty = 0
        total_sold_qty = 0
        for pid in partner_ids:
            data = await client.get(_k("venue", "partner", pid))
            if data:
                p = json.loads(data)
                t = p.get("partnerType", "unknown")
                s = p.get("status", "unknown")
                l = p.get("partnerLevel", "D")
                by_type[t] = by_type.get(t, 0) + 1
                by_status[s] = by_status.get(s, 0) + 1
                by_level[l] = by_level.get(l, 0) + 1
        for sid in stocking_ids:
            data = await client.get(_k("venue", "stocking", sid))
            if data:
                s = json.loads(data)
                total_stock_qty += s.get("quantity", 0)
                total_sold_qty += s.get("soldQty", 0)
        return {
            "totalPartners": len(partner_ids),
            "totalVenues": len(venue_ids),
            "totalStockings": len(stocking_ids),
            "totalStockQty": total_stock_qty,
            "totalSoldQty": total_sold_qty,
            "byType": by_type,
            "byStatus": by_status,
            "byLevel": by_level,
            "generatedAt": datetime.utcnow().isoformat(),
        }


# ============================================================
# 业务规则常量(等级对应的品鉴酒比例)
# ============================================================

# 等级对应的品鉴酒比例(用于 SVIP 价基础上免费品鉴酒分配)
LEVEL_TASTING_RATES = {
    PARTNER_LEVEL_S: 0.03,   # S级 3%
    PARTNER_LEVEL_A: 0.03,   # A级 3%
    PARTNER_LEVEL_B: 0.02,   # B级 2%
    PARTNER_LEVEL_C: 0.01,   # C级 1%
    PARTNER_LEVEL_D: 0.00,   # D级 0%
}

# 等级对应的平台分润比例(差价利润的分成)
LEVEL_PLATFORM_SHARE = {
    PARTNER_LEVEL_S: 0.03,  # S级 平台 3% + 合作商 2%
    PARTNER_LEVEL_A: 0.03,  # A级 平台 3%
    PARTNER_LEVEL_B: 0.02,  # B级 平台 2%
    PARTNER_LEVEL_C: 0.01,  # C级 平台 1%
    PARTNER_LEVEL_D: 0.00,  # D级 平台 0%
}

# 等级对应的合作商分润比例
LEVEL_PARTNER_SHARE = {
    PARTNER_LEVEL_S: 0.02,  # S级 合作商 2%
    PARTNER_LEVEL_A: 0.00,
    PARTNER_LEVEL_B: 0.00,
    PARTNER_LEVEL_C: 0.00,
    PARTNER_LEVEL_D: 0.00,
}

# 等级对应的月销阈值(升级所需)
LEVEL_MONTHLY_QTY_THRESHOLD = {
    PARTNER_LEVEL_D: 0,    # 新入驻
    PARTNER_LEVEL_C: 5,     # ≥5瓶
    PARTNER_LEVEL_B: 20,    # ≥20瓶
    PARTNER_LEVEL_A: 50,    # ≥50瓶
    PARTNER_LEVEL_S: 100,   # ≥100瓶
}


def _level_tasting_rate(level: str) -> float:
    """获取等级对应的品鉴酒比例"""
    return LEVEL_TASTING_RATES.get(level, 0.00)
