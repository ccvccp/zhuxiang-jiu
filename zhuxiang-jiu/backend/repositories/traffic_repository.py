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


# ============================================================
# 博主(KOL)相关常量
# ============================================================

# 博主等级
INFLUENCER_LEVEL_S = "S"            # S级(头部,100万+粉丝)
INFLUENCER_LEVEL_A = "A"            # A级(腰部,50万+)
INFLUENCER_LEVEL_B = "B"            # B级(中腰部,10万+)
INFLUENCER_LEVEL_C = "C"            # C级(尾部,1万+)

# 博主等级默认佣金比例
INFLUENCER_LEVEL_COMMISSION_RATE = {
    INFLUENCER_LEVEL_S: 0.20,        # 20%
    INFLUENCER_LEVEL_A: 0.15,        # 15%
    INFLUENCER_LEVEL_B: 0.12,        # 12%
    INFLUENCER_LEVEL_C: 0.10,        # 10%
}

# 博主合作状态
INFLUENCER_STATUS_COOPERATING = "cooperating"  # 合作中
INFLUENCER_STATUS_SUSPENDED = "suspended"      # 暂停合作
INFLUENCER_STATUS_ENDED = "ended"              # 合作结束

# 推广码状态
PROMO_CODE_ACTIVE = "active"        # 生效中
PROMO_CODE_EXPIRED = "expired"      # 已过期


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

    async def next_influencer_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("traffic_influencer")
        return self._mem_next_id("_traffic_influencer_seq")

    async def next_influencer_platform_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("traffic_influencer_platform")
        return self._mem_next_id("_traffic_influencer_platform_seq")

    async def next_influencer_code_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("traffic_influencer_code")
        return self._mem_next_id("_traffic_influencer_code_seq")

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

    async def get_source(self, source_id: int) -> dict | None:
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

    async def get_source_by_code(self, code: str) -> dict | None:
        if is_redis_mode():
            return await self._redis_get_source_by_code(code)
        return self._mem_get_source_by_code(code)

    # ============================================================
    # 推广员 CRUD
    # ============================================================

    async def get_promoter(self, promoter_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get_promoter(promoter_id)
        return self._mem_get_promoter(promoter_id)

    async def get_promoter_by_code(self, promoter_code: str) -> dict | None:
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

    async def get_lead(self, lead_id: int) -> dict | None:
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
    # 博主(KOL) CRUD
    # ============================================================

    async def create_influencer(self, influencer: dict) -> int:
        """新增博主(返回博主ID)"""
        inf_id = await self.next_influencer_id()
        influencer["id"] = inf_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in influencer:
            influencer["createdAt"] = now
        if "totalTraffic" not in influencer:
            influencer["totalTraffic"] = 0
        if "totalOrders" not in influencer:
            influencer["totalOrders"] = 0
        if "totalGmv" not in influencer:
            influencer["totalGmv"] = 0.0
        if "status" not in influencer:
            influencer["status"] = INFLUENCER_STATUS_COOPERATING
        if is_redis_mode():
            await self._redis_save_influencer(influencer)
        else:
            self._mem_save_influencer(influencer)
        return inf_id

    async def get_influencer(self, influencer_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get_influencer(influencer_id)
        return self._mem_get_influencer(influencer_id)

    async def save_influencer(self, influencer: dict) -> None:
        if is_redis_mode():
            await self._redis_save_influencer(influencer)
        else:
            self._mem_save_influencer(influencer)

    async def list_influencers(self, status: str = None, level: str = None,
                               limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_influencers(status, level, limit)
        return self._mem_list_influencers(status, level, limit)

    async def update_influencer_stats(self, influencer_id: int,
                                      traffic_delta: int = 0,
                                      order_delta: int = 0,
                                      gmv_delta: float = 0) -> None:
        if is_redis_mode():
            await self._redis_update_influencer_stats(influencer_id, traffic_delta,
                                                       order_delta, gmv_delta)
        else:
            self._mem_update_influencer_stats(influencer_id, traffic_delta,
                                                order_delta, gmv_delta)

    # ============================================================
    # 博主平台账号 CRUD
    # ============================================================

    async def add_influencer_platform(self, platform: dict) -> int:
        """新增博主平台账号(返回平台ID)"""
        plat_id = await self.next_influencer_platform_id()
        platform["id"] = plat_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in platform:
            platform["createdAt"] = now
        if "syncedAt" not in platform:
            platform["syncedAt"] = now
        if "verified" not in platform:
            platform["verified"] = False
        if is_redis_mode():
            await self._redis_add_influencer_platform(platform)
        else:
            self._mem_add_influencer_platform(platform)
        return plat_id

    async def get_influencer_platform(self, platform_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get_influencer_platform(platform_id)
        return self._mem_get_influencer_platform(platform_id)

    async def list_influencer_platforms(self, influencer_id: int) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_influencer_platforms(influencer_id)
        return self._mem_list_influencer_platforms(influencer_id)

    async def get_influencer_platform_by_inf_platform(self, influencer_id: int,
                                                       platform: str) -> dict | None:
        """按博主ID+平台查询(唯一约束: 博主+平台)"""
        if is_redis_mode():
            return await self._redis_get_influencer_platform_by_inf_platform(influencer_id, platform)
        return self._mem_get_influencer_platform_by_inf_platform(influencer_id, platform)

    async def update_influencer_platform(self, platform_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update_influencer_platform(platform_id, updates)
        else:
            self._mem_update_influencer_platform(platform_id, updates)

    # ============================================================
    # 博主推广码 CRUD
    # ============================================================

    async def add_influencer_code(self, code: dict) -> int:
        """新增推广码(返回推广码ID)"""
        code_id = await self.next_influencer_code_id()
        code["id"] = code_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in code:
            code["createdAt"] = now
        if "clickCount" not in code:
            code["clickCount"] = 0
        if "leadCount" not in code:
            code["leadCount"] = 0
        if "orderCount" not in code:
            code["orderCount"] = 0
        if "gmv" not in code:
            code["gmv"] = 0.0
        if "status" not in code:
            code["status"] = PROMO_CODE_ACTIVE
        if is_redis_mode():
            await self._redis_add_influencer_code(code)
        else:
            self._mem_add_influencer_code(code)
        return code_id

    async def get_influencer_code_by_code(self, promo_code: str) -> dict | None:
        """按推广码字符串查询"""
        if is_redis_mode():
            return await self._redis_get_influencer_code_by_code(promo_code)
        return self._mem_get_influencer_code_by_code(promo_code)

    async def list_influencer_codes(self, influencer_id: int) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_influencer_codes(influencer_id)
        return self._mem_list_influencer_codes(influencer_id)

    async def update_influencer_code_stats(self, code_id: int,
                                            click_delta: int = 0,
                                            lead_delta: int = 0,
                                            order_delta: int = 0,
                                            gmv_delta: float = 0) -> None:
        if is_redis_mode():
            await self._redis_update_influencer_code_stats(code_id, click_delta,
                                                            lead_delta, order_delta, gmv_delta)
        else:
            self._mem_update_influencer_code_stats(code_id, click_delta,
                                                     lead_delta, order_delta, gmv_delta)

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
            # 博主(KOL)相关存储
            self.store["traffic_influencers"] = {}               # influencerId → influencer
            self.store["traffic_influencer_platforms"] = {}      # platformId → platform
            self.store["traffic_influencer_platforms_by_inf"] = {}  # influencerId → [platformId, ...]
            self.store["traffic_influencer_codes"] = {}         # codeId → promoCode
            self.store["traffic_influencer_codes_by_inf"] = {}   # influencerId → [codeId, ...]
            self.store["traffic_influencer_codes_by_code"] = {} # promoCode → codeId
            self.store["_traffic_influencer_seq"] = 0
            self.store["_traffic_influencer_platform_seq"] = 0
            self.store["_traffic_influencer_code_seq"] = 0
            self.store["_promoter_seq"] = 0
            self.store["_traffic_lead_seq"] = 0
            self.store["_traffic_commission_seq"] = 0
            self.store["_traffic_source_seq"] = 0

    # --- 流量来源 ---

    def _mem_get_source(self, source_id: int) -> dict | None:
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

    def _mem_get_source_by_code(self, code: str) -> dict | None:
        self._ensure_store()
        source_id = self.store["traffic_sources_by_code"].get(code)
        if source_id is None:
            return None
        return self.store["traffic_sources"].get(source_id)

    # --- 推广员 ---

    def _mem_get_promoter(self, promoter_id: int) -> dict | None:
        self._ensure_store()
        return self.store["promoters"].get(promoter_id)

    def _mem_get_promoter_by_code(self, promoter_code: str) -> dict | None:
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

    def _mem_get_lead(self, lead_id: int) -> dict | None:
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

    # --- 博主(KOL) ---

    def _mem_get_influencer(self, influencer_id: int) -> dict | None:
        self._ensure_store()
        return self.store["traffic_influencers"].get(influencer_id)

    def _mem_save_influencer(self, influencer: dict) -> None:
        self._ensure_store()
        influencer_id = influencer["id"]
        influencer["updatedAt"] = datetime.utcnow().isoformat()
        self.store["traffic_influencers"][influencer_id] = influencer

    def _mem_list_influencers(self, status: str = None, level: str = None,
                              limit: int = 100) -> list[dict]:
        self._ensure_store()
        influencers = list(self.store["traffic_influencers"].values())
        if status:
            influencers = [i for i in influencers if i.get("status") == status]
        if level:
            influencers = [i for i in influencers if i.get("level") == level]
        influencers.sort(key=lambda i: i.get("totalGmv", 0), reverse=True)
        return influencers[:limit]

    def _mem_update_influencer_stats(self, influencer_id: int,
                                     traffic_delta: int = 0,
                                     order_delta: int = 0,
                                     gmv_delta: float = 0) -> None:
        self._ensure_store()
        inf = self.store["traffic_influencers"].get(influencer_id)
        if inf:
            inf["totalTraffic"] = inf.get("totalTraffic", 0) + traffic_delta
            inf["totalOrders"] = inf.get("totalOrders", 0) + order_delta
            inf["totalGmv"] = round(inf.get("totalGmv", 0) + gmv_delta, 2)
            inf["updatedAt"] = datetime.utcnow().isoformat()

    # --- 博主平台账号 ---

    def _mem_get_influencer_platform(self, platform_id: int) -> dict | None:
        self._ensure_store()
        return self.store["traffic_influencer_platforms"].get(platform_id)

    def _mem_add_influencer_platform(self, platform: dict) -> None:
        self._ensure_store()
        platform_id = platform["id"]
        influencer_id = platform.get("influencerId")
        self.store["traffic_influencer_platforms"][platform_id] = platform
        if influencer_id:
            if influencer_id not in self.store["traffic_influencer_platforms_by_inf"]:
                self.store["traffic_influencer_platforms_by_inf"][influencer_id] = []
            self.store["traffic_influencer_platforms_by_inf"][influencer_id].append(platform_id)

    def _mem_list_influencer_platforms(self, influencer_id: int) -> list[dict]:
        self._ensure_store()
        platform_ids = self.store["traffic_influencer_platforms_by_inf"].get(influencer_id, [])
        return [self.store["traffic_influencer_platforms"][pid] for pid in platform_ids
                if pid in self.store["traffic_influencer_platforms"]]

    def _mem_get_influencer_platform_by_inf_platform(self, influencer_id: int,
                                                      platform: str) -> dict | None:
        self._ensure_store()
        for p in self._mem_list_influencer_platforms(influencer_id):
            if p.get("platform") == platform:
                return p
        return None

    def _mem_update_influencer_platform(self, platform_id: int,
                                         updates: dict) -> None:
        self._ensure_store()
        p = self.store["traffic_influencer_platforms"].get(platform_id)
        if p:
            p.update(updates)
            p["syncedAt"] = datetime.utcnow().isoformat()

    # --- 博主推广码 ---

    def _mem_add_influencer_code(self, code: dict) -> None:
        self._ensure_store()
        code_id = code["id"]
        influencer_id = code.get("influencerId")
        self.store["traffic_influencer_codes"][code_id] = code
        if influencer_id:
            if influencer_id not in self.store["traffic_influencer_codes_by_inf"]:
                self.store["traffic_influencer_codes_by_inf"][influencer_id] = []
            self.store["traffic_influencer_codes_by_inf"][influencer_id].append(code_id)
        if "promoCode" in code:
            self.store["traffic_influencer_codes_by_code"][code["promoCode"]] = code_id

    def _mem_get_influencer_code_by_code(self, promo_code: str) -> dict | None:
        self._ensure_store()
        code_id = self.store["traffic_influencer_codes_by_code"].get(promo_code)
        if code_id is None:
            return None
        return self.store["traffic_influencer_codes"].get(code_id)

    def _mem_list_influencer_codes(self, influencer_id: int) -> list[dict]:
        self._ensure_store()
        code_ids = self.store["traffic_influencer_codes_by_inf"].get(influencer_id, [])
        return [self.store["traffic_influencer_codes"][cid] for cid in code_ids
                if cid in self.store["traffic_influencer_codes"]]

    def _mem_update_influencer_code_stats(self, code_id: int,
                                           click_delta: int = 0,
                                           lead_delta: int = 0,
                                           order_delta: int = 0,
                                           gmv_delta: float = 0) -> None:
        self._ensure_store()
        code = self.store["traffic_influencer_codes"].get(code_id)
        if code:
            code["clickCount"] = code.get("clickCount", 0) + click_delta
            code["leadCount"] = code.get("leadCount", 0) + lead_delta
            code["orderCount"] = code.get("orderCount", 0) + order_delta
            code["gmv"] = round(code.get("gmv", 0) + gmv_delta, 2)
            code["updatedAt"] = datetime.utcnow().isoformat()

    # ============================================================
    # Redis 模式实现
    # ============================================================

    async def _redis_get_source(self, source_id: int) -> dict | None:
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

    async def _redis_get_source_by_code(self, code: str) -> dict | None:
        client = await get_redis_client()
        source_id = await client.get(_k("traffic", "source_by_code", code))
        if not source_id:
            return None
        data = await client.get(_k("traffic", "source", source_id))
        return json.loads(data) if data else None

    async def _redis_get_promoter(self, promoter_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("traffic", "promoter", promoter_id))
        return json.loads(data) if data else None

    async def _redis_get_promoter_by_code(self, promoter_code: str) -> dict | None:
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

    async def _redis_get_lead(self, lead_id: int) -> dict | None:
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

    # --- 博主(KOL) Redis 实现 ---

    async def _redis_get_influencer(self, influencer_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("traffic", "influencer", influencer_id))
        return json.loads(data) if data else None

    async def _redis_save_influencer(self, influencer: dict) -> None:
        client = await get_redis_client()
        influencer_id = influencer["id"]
        influencer["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("traffic", "influencer", influencer_id),
                         json.dumps(influencer, ensure_ascii=False))

    async def _redis_list_influencers(self, status: str = None, level: str = None,
                                       limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("traffic", "influencer", "*"))
        influencers = []
        for key in keys:
            if "influencer_platform" in key or "influencer_code" in key:
                continue
            data = await client.get(key)
            if data:
                i = json.loads(data)
                if status and i.get("status") != status:
                    continue
                if level and i.get("level") != level:
                    continue
                influencers.append(i)
        influencers.sort(key=lambda i: i.get("totalGmv", 0), reverse=True)
        return influencers[:limit]

    async def _redis_update_influencer_stats(self, influencer_id: int,
                                              traffic_delta: int = 0,
                                              order_delta: int = 0,
                                              gmv_delta: float = 0) -> None:
        inf = await self._redis_get_influencer(influencer_id)
        if inf:
            inf["totalTraffic"] = inf.get("totalTraffic", 0) + traffic_delta
            inf["totalOrders"] = inf.get("totalOrders", 0) + order_delta
            inf["totalGmv"] = round(inf.get("totalGmv", 0) + gmv_delta, 2)
            inf["updatedAt"] = datetime.utcnow().isoformat()
            await self._redis_save_influencer(inf)

    # --- 博主平台账号 Redis 实现 ---

    async def _redis_add_influencer_platform(self, platform: dict) -> None:
        client = await get_redis_client()
        platform_id = platform["id"]
        influencer_id = platform.get("influencerId")
        await client.set(_k("traffic", "influencer_platform", platform_id),
                         json.dumps(platform, ensure_ascii=False))
        if influencer_id:
            await client.lpush(_k("traffic", "inf_platforms_by_inf", influencer_id), platform_id)

    async def _redis_get_influencer_platform(self, platform_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("traffic", "influencer_platform", platform_id))
        return json.loads(data) if data else None

    async def _redis_list_influencer_platforms(self, influencer_id: int) -> list[dict]:
        client = await get_redis_client()
        platform_ids = await client.lrange(_k("traffic", "inf_platforms_by_inf", influencer_id), 0, -1)
        platforms = []
        for pid in platform_ids:
            data = await client.get(_k("traffic", "influencer_platform", pid))
            if data:
                platforms.append(json.loads(data))
        return platforms

    async def _redis_get_influencer_platform_by_inf_platform(self, influencer_id: int,
                                                                platform: str) -> dict | None:
        platforms = await self._redis_list_influencer_platforms(influencer_id)
        for p in platforms:
            if p.get("platform") == platform:
                return p
        return None

    async def _redis_update_influencer_platform(self, platform_id: int, updates: dict) -> None:
        client = await get_redis_client()
        data = await client.get(_k("traffic", "influencer_platform", platform_id))
        if data:
            p = json.loads(data)
            p.update(updates)
            p["syncedAt"] = datetime.utcnow().isoformat()
            await client.set(_k("traffic", "influencer_platform", platform_id),
                             json.dumps(p, ensure_ascii=False))

    # --- 博主推广码 Redis 实现 ---

    async def _redis_add_influencer_code(self, code: dict) -> None:
        client = await get_redis_client()
        code_id = code["id"]
        influencer_id = code.get("influencerId")
        await client.set(_k("traffic", "influencer_code", code_id),
                         json.dumps(code, ensure_ascii=False))
        if influencer_id:
            await client.lpush(_k("traffic", "inf_codes_by_inf", influencer_id), code_id)
        if "promoCode" in code:
            await client.set(_k("traffic", "inf_code_by_code", code["promoCode"]), code_id)

    async def _redis_get_influencer_code_by_code(self, promo_code: str) -> dict | None:
        client = await get_redis_client()
        code_id = await client.get(_k("traffic", "inf_code_by_code", promo_code))
        if not code_id:
            return None
        data = await client.get(_k("traffic", "influencer_code", code_id))
        return json.loads(data) if data else None

    async def _redis_list_influencer_codes(self, influencer_id: int) -> list[dict]:
        client = await get_redis_client()
        code_ids = await client.lrange(_k("traffic", "inf_codes_by_inf", influencer_id), 0, -1)
        codes = []
        for cid in code_ids:
            data = await client.get(_k("traffic", "influencer_code", cid))
            if data:
                codes.append(json.loads(data))
        return codes

    async def _redis_update_influencer_code_stats(self, code_id: int,
                                                   click_delta: int = 0,
                                                   lead_delta: int = 0,
                                                   order_delta: int = 0,
                                                   gmv_delta: float = 0) -> None:
        client = await get_redis_client()
        data = await client.get(_k("traffic", "influencer_code", code_id))
        if data:
            code = json.loads(data)
            code["clickCount"] = code.get("clickCount", 0) + click_delta
            code["leadCount"] = code.get("leadCount", 0) + lead_delta
            code["orderCount"] = code.get("orderCount", 0) + order_delta
            code["gmv"] = round(code.get("gmv", 0) + gmv_delta, 2)
            code["updatedAt"] = datetime.utcnow().isoformat()
            await client.set(_k("traffic", "influencer_code", code_id),
                             json.dumps(code, ensure_ascii=False))
