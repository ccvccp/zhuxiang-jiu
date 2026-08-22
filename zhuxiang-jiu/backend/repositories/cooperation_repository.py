"""合作接口管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    cooperation_applications(合作申请表)
    cooperation_contracts(合作协议表)
    cooperation_partners(合作方表)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 合作方主键: id(自增int), 业务编号 partnerNo(PT+时间戳)
    - 申请主键: id(自增), applicationNo(CA+时间戳)
    - 协议主键: id(自增), contractNo(CT+时间戳)
"""

import json
from datetime import datetime
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 合作方类型
# ============================================================

PARTNER_TYPE_ENTERPRISE = "enterprise"    # 企业
PARTNER_TYPE_PERSONAL = "personal"        # 个人
PARTNER_TYPE_GOVERNMENT = "government"    # 政府
PARTNER_TYPE_DEALER = "dealer"            # 经销商

# 合作方资质状态
QUAL_STATUS_PENDING = "pending"          # 待审核
QUAL_STATUS_APPROVED = "approved"        # 已通过
QUAL_STATUS_REJECTED = "rejected"        # 已驳回
QUAL_STATUS_EXPIRED = "expired"          # 已过期

# 合作方分级
PARTNER_LEVEL_BRONZE = "bronze"          # 青铜
PARTNER_LEVEL_SILVER = "silver"          # 白银
PARTNER_LEVEL_GOLD = "gold"              # 黄金
PARTNER_LEVEL_STRATEGIC = "strategic"    # 战略

# 合作方状态
PARTNER_STATUS_PENDING = "pending"        # 待激活
PARTNER_STATUS_ACTIVE = "active"          # 合作中
PARTNER_STATUS_SUSPENDED = "suspended"    # 已暂停
PARTNER_STATUS_TERMINATED = "terminated"  # 已终止

# 合作申请类型
APP_TYPE_NEW = "new"                      # 新合作
APP_TYPE_RENEWAL = "renewal"              # 续约
APP_TYPE_UPGRADE = "upgrade"              # 升级

# 合作申请状态
APP_STATUS_PENDING = "pending"            # 待审核
APP_STATUS_REVIEWING = "reviewing"        # 审核中
APP_STATUS_APPROVED = "approved"          # 已通过
APP_STATUS_REJECTED = "rejected"          # 已驳回
APP_STATUS_SIGNED = "signed"              # 已签约
APP_STATUS_TERMINATED = "terminated"      # 已终止

# 协议状态
CONTRACT_STATUS_DRAFT = "draft"            # 草稿
CONTRACT_STATUS_ACTIVE = "active"          # 生效中
CONTRACT_STATUS_EXPIRED = "expired"        # 已过期
CONTRACT_STATUS_TERMINATED = "terminated"  # 已终止


class CooperationRepository:
    """合作接口管理数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_partner_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("partner")
        return self._mem_next_id("_cooperation_partner_seq")

    async def next_application_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("application")
        return self._mem_next_id("_cooperation_application_seq")

    async def next_contract_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("contract")
        return self._mem_next_id("_cooperation_contract_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("cooperation", entity, "seq"))

    # ============================================================
    # 合作方 CRUD
    # ============================================================

    async def get_partner(self, partner_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get_partner(partner_id)
        return self._mem_get_partner(partner_id)

    async def save_partner(self, partner: dict) -> None:
        if is_redis_mode():
            await self._redis_save_partner(partner)
        else:
            self._mem_save_partner(partner)

    async def list_partners(self, status: str = None, level: str = None,
                            limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_partners(status, level, limit)
        return self._mem_list_partners(status, level, limit)

    async def find_partner_by_name(self, name: str) -> Optional[dict]:
        """按名称查找合作方(去重)"""
        partners = await self.list_partners(limit=10000)
        for p in partners:
            if p.get("name") == name:
                return p
        return None

    # ============================================================
    # 合作申请 CRUD
    # ============================================================

    async def get_application(self, application_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get_application(application_id)
        return self._mem_get_application(application_id)

    async def save_application(self, app: dict) -> None:
        if is_redis_mode():
            await self._redis_save_application(app)
        else:
            self._mem_save_application(app)

    async def list_applications(self, status: str = None,
                                 partner_id: int = None,
                                 limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_applications(status, partner_id, limit)
        return self._mem_list_applications(status, partner_id, limit)

    # ============================================================
    # 合作协议 CRUD
    # ============================================================

    async def get_contract(self, contract_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get_contract(contract_id)
        return self._mem_get_contract(contract_id)

    async def save_contract(self, contract: dict) -> None:
        if is_redis_mode():
            await self._redis_save_contract(contract)
        else:
            self._mem_save_contract(contract)

    async def list_contracts(self, status: str = None,
                              partner_id: int = None,
                              limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_contracts(status, partner_id, limit)
        return self._mem_list_contracts(status, partner_id, limit)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含合作模块的键(懒初始化)"""
        if "cooperation_partners" not in self.store:
            self.store["cooperation_partners"] = {}          # partnerId → partner
            self.store["cooperation_applications"] = {}      # applicationId → app
            self.store["cooperation_contracts"] = {}          # contractId → contract
            self.store["cooperation_contracts_by_partner"] = {}  # partnerId → [contractId]
            self.store["cooperation_apps_by_partner"] = {}    # partnerId → [applicationId]
            self.store["_cooperation_partner_seq"] = 0
            self.store["_cooperation_application_seq"] = 0
            self.store["_cooperation_contract_seq"] = 0

    # --- 合作方 ---

    def _mem_get_partner(self, partner_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["cooperation_partners"].get(partner_id)

    def _mem_save_partner(self, partner: dict) -> None:
        self._ensure_store()
        self.store["cooperation_partners"][partner["id"]] = partner

    def _mem_list_partners(self, status: str = None, level: str = None,
                           limit: int = 100) -> list[dict]:
        self._ensure_store()
        partners = list(self.store["cooperation_partners"].values())
        if status:
            partners = [p for p in partners if p.get("status") == status]
        if level:
            partners = [p for p in partners if p.get("level") == level]
        partners.sort(key=lambda p: p.get("createdAt", ""), reverse=True)
        return partners[:limit]

    # --- 合作申请 ---

    def _mem_get_application(self, application_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["cooperation_applications"].get(application_id)

    def _mem_save_application(self, app: dict) -> None:
        self._ensure_store()
        self.store["cooperation_applications"][app["id"]] = app

    def _mem_list_applications(self, status: str = None,
                                partner_id: int = None,
                                limit: int = 100) -> list[dict]:
        self._ensure_store()
        apps = list(self.store["cooperation_applications"].values())
        if status:
            apps = [a for a in apps if a.get("status") == status]
        if partner_id:
            apps = [a for a in apps if a.get("partnerId") == partner_id]
        apps.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        return apps[:limit]

    # --- 合作协议 ---

    def _mem_get_contract(self, contract_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["cooperation_contracts"].get(contract_id)

    def _mem_save_contract(self, contract: dict) -> None:
        self._ensure_store()
        self.store["cooperation_contracts"][contract["id"]] = contract

    def _mem_list_contracts(self, status: str = None,
                             partner_id: int = None,
                             limit: int = 100) -> list[dict]:
        self._ensure_store()
        contracts = list(self.store["cooperation_contracts"].values())
        if status:
            contracts = [c for c in contracts if c.get("status") == status]
        if partner_id:
            contracts = [c for c in contracts if c.get("partnerId") == partner_id]
        contracts.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
        return contracts[:limit]

    # ============================================================
    # Redis 模式实现
    # ============================================================

    # --- 合作方 ---

    async def _redis_get_partner(self, partner_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("cooperation", "partner", partner_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_partner(self, partner: dict) -> None:
        client = await get_redis_client()
        await client.set(_k("cooperation", "partner", partner["id"]),
                         json.dumps(partner, ensure_ascii=False))

    async def _redis_list_partners(self, status: str = None, level: str = None,
                                    limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("cooperation", "partner", "*"))
        partners = []
        for key in keys:
            data = await client.get(key)
            if data:
                p = json.loads(data)
                if status and p.get("status") != status:
                    continue
                if level and p.get("level") != level:
                    continue
                partners.append(p)
        partners.sort(key=lambda p: p.get("createdAt", ""), reverse=True)
        return partners[:limit]

    # --- 合作申请 ---

    async def _redis_get_application(self, application_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("cooperation", "application", application_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_application(self, app: dict) -> None:
        client = await get_redis_client()
        await client.set(_k("cooperation", "application", app["id"]),
                         json.dumps(app, ensure_ascii=False))

    async def _redis_list_applications(self, status: str = None,
                                        partner_id: int = None,
                                        limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("cooperation", "application", "*"))
        apps = []
        for key in keys:
            data = await client.get(key)
            if data:
                a = json.loads(data)
                if status and a.get("status") != status:
                    continue
                if partner_id and a.get("partnerId") != partner_id:
                    continue
                apps.append(a)
        apps.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        return apps[:limit]

    # --- 合作协议 ---

    async def _redis_get_contract(self, contract_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("cooperation", "contract", contract_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_contract(self, contract: dict) -> None:
        client = await get_redis_client()
        await client.set(_k("cooperation", "contract", contract["id"]),
                         json.dumps(contract, ensure_ascii=False))

    async def _redis_list_contracts(self, status: str = None,
                                     partner_id: int = None,
                                     limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("cooperation", "contract", "*"))
        contracts = []
        for key in keys:
            data = await client.get(key)
            if data:
                c = json.loads(data)
                if status and c.get("status") != status:
                    continue
                if partner_id and c.get("partnerId") != partner_id:
                    continue
                contracts.append(c)
        contracts.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
        return contracts[:limit]
