"""流量管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    traffic_sources  - 流量来源表(douyin/kuaishou/wechat 等多平台)
    promoters        - 推广员表(5级推广等级/裂变关系)
    traffic_leads    - 引流记录表(推广员带来的流量/转化/佣金)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 推广员: 按 id 主键, promoter_code 唯一, 5级等级
    - 引流记录: 按 (promoter_id, user_id) 索引
    - 佣金记录: 关联推广员+订单
"""

import json
from datetime import datetime
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 流量来源平台
# ============================================================

SOURCE_DOUYIN = "douyin"          # 抖音
SOURCE_KUAISHOU = "kuaishou"      # 快手
SOURCE_WECHAT = "wechat"          # 微信
SOURCE_XIAOHONGSHU = "xiaohongshu"  # 小红书
SOURCE_BILIBILI = "bilibili"     # B站
SOURCE_TAOBAO = "taobao"         # 淘宝
SOURCE_DIRECT = "direct"         # 直接访问

# 引流方式
MEDIUM_VIDEO = "video"           # 视频
MEDIUM_LIVE = "live"             # 直播
MEDIUM_SHARE = "share"           # 分享
MEDIUM_AD = "ad"                 # 广告

# ============================================================
# 推广员等级(5级)
# ============================================================

LEVEL_TRAINEE = "trainee"        # 见习推广员
LEVEL_JUNIOR = "junior"          # 初级推广员
LEVEL_INTERMEDIATE = "intermediate"  # 中级推广员
LEVEL_SENIOR = "senior"          # 高级推广员
LEVEL_GOLD = "gold"              # 金牌推广员

# 等级对应佣金比例
LEVEL_COMMISSION_RATE = {
    LEVEL_TRAINEE: 0.05,         # 5%
    LEVEL_JUNIOR: 0.08,          # 8%
    LEVEL_INTERMEDIATE: 0.10,    # 10%
    LEVEL_SENIOR: 0.12,          # 12%
    LEVEL_GOLD: 0.15,            # 15%
}

# 等级升级条件(邀请人数, 下单人数)
LEVEL_UPGRADE_CONDITIONS = {
    LEVEL_TRAINEE: (0, 0),
    LEVEL_JUNIOR: (5, 0),
    LEVEL_INTERMEDIATE: (20, 10),
    LEVEL_SENIOR: (50, 30),
    LEVEL_GOLD: (100, 50),
}

# 等级对应额外奖励
LEVEL_EXTRA_REWARD = {
    LEVEL_TRAINEE: {},
    LEVEL_JUNIOR: {"coupon": 50, "desc": "¥50优惠券"},
    LEVEL_INTERMEDIATE: {"coupon": 200, "desc": "¥200优惠券"},
    LEVEL_SENIOR: {"coupon": 500, "desc": "¥500优惠券"},
    LEVEL_GOLD: {"experience": "泰山游资格", "desc": "泰山游资格"},
}

# 等级排序(用于升降级判定)
LEVEL_RANK = {
    LEVEL_TRAINEE: 1,
    LEVEL_JUNIOR: 2,
    LEVEL_INTERMEDIATE: 3,
    LEVEL_SENIOR: 4,
    LEVEL_GOLD: 5,
}

# ============================================================
# 推广员状态
# ============================================================

PROMOTER_STATUS_ACTIVE = "active"     # 活跃
PROMOTER_STATUS_PAUSED = "paused"     # 暂停
PROMOTER_STATUS_BANNED = "banned"     # 封禁

# ============================================================
# 引流记录状态
# ============================================================

LEAD_STATUS_PENDING = "pending"       # 待处理
LEAD_STATUS_REGISTERED = "registered"  # 已注册
LEAD_STATUS_ORDERED = "ordered"        # 已下单
LEAD_STATUS_INVALID = "invalid"        # 无效

# 是否有效流量
LEAD_EFFECTIVE_TRUE = 1
LEAD_EFFECTIVE_FALSE = 0

# ============================================================
# 佣金记录状态
# ============================================================

COMMISSION_PENDING = "pending"        # 待结算
COMMISSION_SETTLED = "settled"        # 已结算
COMMISSION_WITHDRAWN = "withdrawn"     # 已提现


class TrafficRepository:
    """流量管理数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_promoter_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("promoter")
        return self._mem_next_id("_promoter_seq")

    async def next_lead_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("traffic_lead")
        return self._mem_next_id("_traffic_lead_seq")

    async def next_commission_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("traffic_commission")
        return self._mem_next_id("_traffic_commission_seq")

    async def next_source_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("traffic_source")
        return self._mem_next_id("_traffic_source_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("traffic", entity, "seq"))

    # ============================================================
    # 流量来源 CRUD
    # ============================================================

    async def get_source(self, source_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get_source(source_id)
        return self._mem_get_source(source_id)

    async def save_source(self, source: dict) -> None:
        if is_redis_mode():
            await self._redis_save_source(source)
        else:
            self._mem_save_source(source)

    async def list_sources(self, limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_sources(limit)
        return self._mem_list_sources(limit)

    async def get_source_by_code(self, code: str) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get_source_by_code(code)
        return self._mem_get_source_by_code(code)

    # ============================================================
    # 推广员 CRUD
    # ============================================================

    async def get_promoter(self, promoter_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get_promoter(promoter_id)
        return self._mem_get_promoter(promoter_id)

    async def get_promoter_by_code(self, promoter_code: str) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get_promoter_by_code(promoter_code)
        return self._mem_get_promoter_by_code(promoter_code)

    async def save_promoter(self, promoter: dict) -> None:
        if is_redis_mode():
            await self._redis_save_promoter(promoter)
        else:
            self._mem_save_promoter(promoter)

    async def list_promoters(self, status: str = None, level: str = None,
                             limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_promoters(status, level, limit)
        return self._mem_list_promoters(status, level, limit)

    # ============================================================
    # 引流记录 CRUD
    # ============================================================

    async def add_lead(self, lead: dict) -> int:
        lead_id = await self.next_lead_id()
        lead["id"] = lead_id
        if "createdAt" not in lead:
            lead["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_lead(lead)
        else:
            self._mem_add_lead(lead)
        return lead_id

    async def get_lead(self, lead_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get_lead(lead_id)
        return self._mem_get_lead(lead_id)

    async def update_lead_status(self, lead_id: int, status: str,
                                  is_effective: int = None) -> None:
        if is_redis_mode():
            await self._redis_update_lead_status(lead_id, status, is_effective)
        else:
            self._mem_update_lead_status(lead_id, status, is_effective)

    async def list_leads(self, promoter_id: int = None, source: str = None,
                         status: str = None, limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_leads(promoter_id, source, status, limit)
        return self._mem_list_leads(promoter_id, source, status, limit)

    # ============================================================
    # 佣金记录 CRUD
    # ============================================================

    async def add_commission(self, commission: dict) -> int:
        commission_id = await self.next_commission_id()
        commission["id"] = commission_id
        if "createdAt" not in commission:
            commission["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_commission(commission)
        else:
            self._mem_add_commission(commission)
        return commission_id

    async def list_commissions(self, promoter_id: int, status: str = None,
                               limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_commissions(promoter_id, status, limit)
        return self._mem_list_commissions(promoter_id, status, limit)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        if "traffic_sources" not in self.store:
            self.store["traffic_sources"] = {}                  # sourceId → source
            self.store["traffic_sources_by_code"] = {}           # code → sourceId
            self.store["promoters"] = {}                         # promoterId → promoter
            self.store["promoters_by_code"] = {}                 # promoterCode → promoterId
            self.store["traffic_leads"] = {}                     # leadId → lead
            self.store["traffic_leads_by_promoter"] = {}         # promoterId → [leadId, ...]
            self.store["traffic_commissions"] = {}               # commissionId → commission
            self.store["traffic_commissions_by_promoter"] = {}   # promoterId → [commissionId, ...]
            self.store["_promoter_seq"] = 0
            self.store["_traffic_lead_seq"] = 0
            self.store["_traffic_commission_seq"] = 0
            self.store["_traffic_source_seq"] = 0

    # --- 流量来源 ---

    def _mem_get_source(self, source_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["traffic_sources"].get(source_id)

    def _mem_save_source(self, source: dict) -> None:
        self._ensure_store()
        source_id = source["id"]
        source["updatedAt"] = datetime.utcnow().isoformat()
        self.store["traffic_sources"][source_id] = source
        if "code" in source:
            self.store["traffic_sources_by_code"][source["code"]] = source_id

    def _mem_list_sources(self, limit: int = 100) -> list[dict]:
        self._ensure_store()
        sources = list(self.store["traffic_sources"].values())
        sources.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return sources[:limit]

    def _mem_get_source_by_code(self, code: str) -> Optional[dict]:
        self._ensure_store()
        source_id = self.store["traffic_sources_by_code"].get(code)
        if source_id is None:
            return None
        return self.store["traffic_sources"].get(source_id)

    # --- 推广员 ---

    def _mem_get_promoter(self, promoter_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["promoters"].get(promoter_id)

    def _mem_get_promoter_by_code(self, promoter_code: str) -> Optional[dict]:
        self._ensure_store()
        promoter_id = self.store["promoters_by_code"].get(promoter_code)
        if promoter_id is None:
            return None
        return self.store["promoters"].get(promoter_id)

    def _mem_save_promoter(self, promoter: dict) -> None:
        self._ensure_store()
        promoter_id = promoter["id"]
        promoter["updatedAt"] = datetime.utcnow().isoformat()
        self.store["promoters"][promoter_id] = promoter
        if "promoterCode" in promoter:
            self.store["promoters_by_code"][promoter["promoterCode"]] = promoter_id

    def _mem_list_promoters(self, status: str = None, level: str = None,
                            limit: int = 100) -> list[dict]:
        self._ensure_store()
        promoters = list(self.store["promoters"].values())
        if status:
            promoters = [p for p in promoters if p.get("status") == status]
        if level:
            promoters = [p for p in promoters if p.get("level") == level]
        promoters.sort(key=lambda p: p.get("totalCommission", 0), reverse=True)
        return promoters[:limit]

    # --- 引流记录 ---

    def _mem_add_lead(self, lead: dict) -> None:
        self._ensure_store()
        lead_id = lead["id"]
        promoter_id = lead.get("promoterId")
        self.store["traffic_leads"][lead_id] = lead
        if promoter_id:
            if promoter_id not in self.store["traffic_leads_by_promoter"]:
                self.store["traffic_leads_by_promoter"][promoter_id] = []
            self.store["traffic_leads_by_promoter"][promoter_id].append(lead_id)

    def _mem_get_lead(self, lead_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["traffic_leads"].get(lead_id)

    def _mem_update_lead_status(self, lead_id: int, status: str,
                                 is_effective: int = None) -> None:
        self._ensure_store()
        lead = self.store["traffic_leads"].get(lead_id)
        if lead:
            lead["status"] = status
            if is_effective is not None:
                lead["isEffective"] = is_effective
            lead["updatedAt"] = datetime.utcnow().isoformat()

    def _mem_list_leads(self, promoter_id: int = None, source: str = None,
                        status: str = None, limit: int = 100) -> list[dict]:
        self._ensure_store()
        if promoter_id:
            lead_ids = self.store["traffic_leads_by_promoter"].get(promoter_id, [])
            leads = [self.store["traffic_leads"][lid] for lid in lead_ids
                     if lid in self.store["traffic_leads"]]
        else:
            leads = list(self.store["traffic_leads"].values())
        if source:
            leads = [l for l in leads if l.get("source") == source]
        if status:
            leads = [l for l in leads if l.get("status") == status]
        leads.sort(key=lambda l: l.get("createdAt", ""), reverse=True)
        return leads[:limit]

    # --- 佣金记录 ---

    def _mem_add_commission(self, commission: dict) -> None:
        self._ensure_store()
        commission_id = commission["id"]
        promoter_id = commission.get("promoterId")
        self.store["traffic_commissions"][commission_id] = commission
        if promoter_id:
            if promoter_id not in self.store["traffic_commissions_by_promoter"]:
                self.store["traffic_commissions_by_promoter"][promoter_id] = []
            self.store["traffic_commissions_by_promoter"][promoter_id].append(commission_id)

    def _mem_list_commissions(self, promoter_id: int, status: str = None,
                              limit: int = 100) -> list[dict]:
        self._ensure_store()
        commission_ids = self.store["traffic_commissions_by_promoter"].get(promoter_id, [])
        commissions = [self.store["traffic_commissions"][cid] for cid in commission_ids
                       if cid in self.store["traffic_commissions"]]
        if status:
            commissions = [c for c in commissions if c.get("status") == status]
        commissions.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
        return commissions[:limit]

    # ============================================================
    # Redis 模式实现
    # ============================================================

    async def _redis_get_source(self, source_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("traffic", "source", source_id))
        return json.loads(data) if data else None

    async def _redis_save_source(self, source: dict) -> None:
        client = await get_redis_client()
        source_id = source["id"]
        source["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("traffic", "source", source_id),
                         json.dumps(source, ensure_ascii=False))
        if "code" in source:
            await client.set(_k("traffic", "source_by_code", source["code"]), source_id)

    async def _redis_list_sources(self, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("traffic", "source", "*"))
        sources = []
        for key in keys:
            if "source_by_code" in key:
                continue
            data = await client.get(key)
            if data:
                sources.append(json.loads(data))
        sources.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return sources[:limit]

    async def _redis_get_source_by_code(self, code: str) -> Optional[dict]:
        client = await get_redis_client()
        source_id = await client.get(_k("traffic", "source_by_code", code))
        if not source_id:
            return None
        data = await client.get(_k("traffic", "source", source_id))
        return json.loads(data) if data else None

    async def _redis_get_promoter(self, promoter_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("traffic", "promoter", promoter_id))
        return json.loads(data) if data else None

    async def _redis_get_promoter_by_code(self, promoter_code: str) -> Optional[dict]:
        client = await get_redis_client()
        promoter_id = await client.get(_k("traffic", "promoter_by_code", promoter_code))
        if not promoter_id:
            return None
        data = await client.get(_k("traffic", "promoter", promoter_id))
        return json.loads(data) if data else None

    async def _redis_save_promoter(self, promoter: dict) -> None:
        client = await get_redis_client()
        promoter_id = promoter["id"]
        promoter["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("traffic", "promoter", promoter_id),
                         json.dumps(promoter, ensure_ascii=False))
        if "promoterCode" in promoter:
            await client.set(_k("traffic", "promoter_by_code",
                                promoter["promoterCode"]), promoter_id)

    async def _redis_list_promoters(self, status: str = None, level: str = None,
                                   limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("traffic", "promoter", "*"))
        promoters = []
        for key in keys:
            if "promoter_by_code" in key:
                continue
            data = await client.get(key)
            if data:
                p = json.loads(data)
                if status and p.get("status") != status:
                    continue
                if level and p.get("level") != level:
                    continue
                promoters.append(p)
        promoters.sort(key=lambda p: p.get("totalCommission", 0), reverse=True)
        return promoters[:limit]

    async def _redis_add_lead(self, lead: dict) -> None:
        client = await get_redis_client()
        lead_id = lead["id"]
        promoter_id = lead.get("promoterId")
        await client.set(_k("traffic", "lead", lead_id),
                         json.dumps(lead, ensure_ascii=False))
        if promoter_id:
            await client.lpush(_k("traffic", "leads_by_promoter", promoter_id), lead_id)

    async def _redis_get_lead(self, lead_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("traffic", "lead", lead_id))
        return json.loads(data) if data else None

    async def _redis_update_lead_status(self, lead_id: int, status: str,
                                          is_effective: int = None) -> None:
        client = await get_redis_client()
        data = await client.get(_k("traffic", "lead", lead_id))
        if data:
            lead = json.loads(data)
            lead["status"] = status
            if is_effective is not None:
                lead["isEffective"] = is_effective
            lead["updatedAt"] = datetime.utcnow().isoformat()
            await client.set(_k("traffic", "lead", lead_id),
                             json.dumps(lead, ensure_ascii=False))

    async def _redis_list_leads(self, promoter_id: int = None, source: str = None,
                                status: str = None, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        if promoter_id:
            lead_ids = await client.lrange(_k("traffic", "leads_by_promoter", promoter_id), 0, -1)
            leads = []
            for lid in lead_ids:
                data = await client.get(_k("traffic", "lead", lid))
                if data:
                    leads.append(json.loads(data))
        else:
            keys = await client.keys(_k("traffic", "lead", "*"))
            leads = []
            for key in keys:
                data = await client.get(key)
                if data:
                    leads.append(json.loads(data))
        if source:
            leads = [l for l in leads if l.get("source") == source]
        if status:
            leads = [l for l in leads if l.get("status") == status]
        leads.sort(key=lambda l: l.get("createdAt", ""), reverse=True)
        return leads[:limit]

    async def _redis_add_commission(self, commission: dict) -> None:
        client = await get_redis_client()
        commission_id = commission["id"]
        promoter_id = commission.get("promoterId")
        await client.set(_k("traffic", "commission", commission_id),
                         json.dumps(commission, ensure_ascii=False))
        if promoter_id:
            await client.lpush(_k("traffic", "commissions_by_promoter", promoter_id), commission_id)

    async def _redis_list_commissions(self, promoter_id: int, status: str = None,
                                      limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        commission_ids = await client.lrange(_k("traffic", "commissions_by_promoter", promoter_id), 0, -1)
        commissions = []
        for cid in commission_ids:
            data = await client.get(_k("traffic", "commission", cid))
            if data:
                c = json.loads(data)
                if status and c.get("status") != status:
                    continue
                commissions.append(c)
        commissions.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
        return commissions[:limit]
