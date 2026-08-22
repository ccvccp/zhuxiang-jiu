"""网站条款及角色协议管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    agreements(条款表, 含版本管理)
    agreement_consents(用户同意记录表)
    role_protocols(角色协议表)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 条款主键: id(自增int), 业务编号 agreementNo(T01/T02...)
    - 同意记录: id(自增), consentNo(SIG+时间戳)
    - 角色协议: id(自增), 关联 agreementId + role
"""

import json
from datetime import datetime
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 条款类型
# ============================================================

AGREEMENT_TYPE_TERM = "term"          # 条款
AGREEMENT_TYPE_RULE = "rule"          # 规则
AGREEMENT_TYPE_CONTRACT = "contract"  # 合同

# 条款状态
AGREEMENT_STATUS_DRAFT = "draft"              # 草稿
AGREEMENT_STATUS_REVIEWING = "reviewing"      # 审核中
AGREEMENT_STATUS_PUBLISHED = "published"     # 已发布
AGREEMENT_STATUS_INACTIVE = "inactive"       # 已停用

# 签署方式
SIGN_METHOD_CHECKBOX = "checkbox"      # 勾选确认
SIGN_METHOD_POPUP = "popup"            # 弹窗确认
SIGN_METHOD_ESIGN = "e-sign"           # 电子签章

# 角色协议状态
PROTOCOL_STATUS_ACTIVE = "active"      # 生效
PROTOCOL_STATUS_INACTIVE = "inactive"  # 已停用

# 适用角色
ROLE_USER = "user"          # 用户
ROLE_MEMBER = "member"      # 会员
ROLE_AGENT = "agent"        # 代理商
ROLE_MERCHANT = "merchant"  # 商家
ROLE_ADMIN = "admin"        # 管理员


class AgreementRepository:
    """网站条款及角色协议管理数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_agreement_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("agreement")
        return self._mem_next_id("_agreement_seq")

    async def next_consent_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("consent")
        return self._mem_next_id("_agreement_consent_seq")

    async def next_protocol_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("protocol")
        return self._mem_next_id("_agreement_protocol_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("agreement", entity, "seq"))

    # ============================================================
    # 条款 CRUD
    # ============================================================

    async def get_agreement(self, agreement_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get_agreement(agreement_id)
        return self._mem_get_agreement(agreement_id)

    async def save_agreement(self, agreement: dict) -> None:
        if is_redis_mode():
            await self._redis_save_agreement(agreement)
        else:
            self._mem_save_agreement(agreement)

    async def list_agreements(self, status: str = None, atype: str = None,
                               role: str = None, limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_agreements(status, atype, role, limit)
        return self._mem_list_agreements(status, atype, role, limit)

    async def find_by_no(self, agreement_no: str) -> Optional[dict]:
        """按编号查找条款"""
        agreements = await self.list_agreements(limit=10000)
        for a in agreements:
            if a.get("agreementNo") == agreement_no:
                return a
        return None

    # ============================================================
    # 用户同意记录 CRUD
    # ============================================================

    async def add_consent(self, consent: dict) -> int:
        """新增同意记录(返回ID)"""
        consent_id = await self.next_consent_id()
        consent["id"] = consent_id
        if "signedAt" not in consent:
            consent["signedAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_consent(consent)
        else:
            self._mem_add_consent(consent)
        return consent_id

    async def list_consents(self, user_id: int = None,
                             agreement_id: int = None,
                             limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_consents(user_id, agreement_id, limit)
        return self._mem_list_consents(user_id, agreement_id, limit)

    async def find_consent(self, user_id: int, agreement_id: int) -> Optional[dict]:
        """查询用户对某条款的最新同意记录"""
        consents = await self.list_consents(user_id=user_id,
                                             agreement_id=agreement_id, limit=1)
        return consents[0] if consents else None

    # ============================================================
    # 角色协议 CRUD
    # ============================================================

    async def get_protocol(self, protocol_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get_protocol(protocol_id)
        return self._mem_get_protocol(protocol_id)

    async def save_protocol(self, protocol: dict) -> None:
        if is_redis_mode():
            await self._redis_save_protocol(protocol)
        else:
            self._mem_save_protocol(protocol)

    async def list_protocols(self, role: str = None, status: str = None,
                              limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_protocols(role, status, limit)
        return self._mem_list_protocols(role, status, limit)

    async def find_protocol(self, role: str, agreement_id: int) -> Optional[dict]:
        """查询角色-条款关联(去重)"""
        protocols = await self.list_protocols(role=role, limit=10000)
        for p in protocols:
            if p.get("agreementId") == agreement_id:
                return p
        return None

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含条款模块的键(懒初始化)"""
        if "agreements" not in self.store:
            self.store["agreements"] = {}                # agreementId → agreement
            self.store["agreement_consents"] = {}         # consentId → consent
            self.store["agreement_consents_by_user"] = {}  # userId → [consentId]
            self.store["agreement_consents_by_agreement"] = {}  # agreementId → [consentId]
            self.store["role_protocols"] = {}             # protocolId → protocol
            self.store["_agreement_seq"] = 0
            self.store["_agreement_consent_seq"] = 0
            self.store["_agreement_protocol_seq"] = 0

    # --- 条款 ---

    def _mem_get_agreement(self, agreement_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["agreements"].get(agreement_id)

    def _mem_save_agreement(self, agreement: dict) -> None:
        self._ensure_store()
        self.store["agreements"][agreement["id"]] = agreement

    def _mem_list_agreements(self, status: str = None, atype: str = None,
                              role: str = None, limit: int = 100) -> list[dict]:
        self._ensure_store()
        agreements = list(self.store["agreements"].values())
        if status:
            agreements = [a for a in agreements if a.get("status") == status]
        if atype:
            agreements = [a for a in agreements if a.get("type") == atype]
        if role:
            agreements = [a for a in agreements if a.get("applicableRole") == role]
        agreements.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        return agreements[:limit]

    # --- 同意记录 ---

    def _mem_add_consent(self, consent: dict) -> None:
        self._ensure_store()
        cid = consent["id"]
        uid = consent["userId"]
        aid = consent["agreementId"]
        self.store["agreement_consents"][cid] = consent
        if uid not in self.store["agreement_consents_by_user"]:
            self.store["agreement_consents_by_user"][uid] = []
        self.store["agreement_consents_by_user"][uid].append(cid)
        if aid not in self.store["agreement_consents_by_agreement"]:
            self.store["agreement_consents_by_agreement"][aid] = []
        self.store["agreement_consents_by_agreement"][aid].append(cid)

    def _mem_list_consents(self, user_id: int = None,
                            agreement_id: int = None,
                            limit: int = 100) -> list[dict]:
        self._ensure_store()
        consents = list(self.store["agreement_consents"].values())
        if user_id:
            consents = [c for c in consents if c.get("userId") == user_id]
        if agreement_id:
            consents = [c for c in consents if c.get("agreementId") == agreement_id]
        consents.sort(key=lambda c: c.get("signedAt", ""), reverse=True)
        return consents[:limit]

    # --- 角色协议 ---

    def _mem_get_protocol(self, protocol_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["role_protocols"].get(protocol_id)

    def _mem_save_protocol(self, protocol: dict) -> None:
        self._ensure_store()
        self.store["role_protocols"][protocol["id"]] = protocol

    def _mem_list_protocols(self, role: str = None, status: str = None,
                             limit: int = 100) -> list[dict]:
        self._ensure_store()
        protocols = list(self.store["role_protocols"].values())
        if role:
            protocols = [p for p in protocols if p.get("role") == role]
        if status:
            protocols = [p for p in protocols if p.get("status") == status]
        protocols.sort(key=lambda p: p.get("createdAt", ""), reverse=True)
        return protocols[:limit]

    # ============================================================
    # Redis 模式实现
    # ============================================================

    # --- 条款 ---

    async def _redis_get_agreement(self, agreement_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("agreement", "agreement", agreement_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_agreement(self, agreement: dict) -> None:
        client = await get_redis_client()
        await client.set(_k("agreement", "agreement", agreement["id"]),
                         json.dumps(agreement, ensure_ascii=False))

    async def _redis_list_agreements(self, status: str = None, atype: str = None,
                                      role: str = None, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("agreement", "agreement", "*"))
        agreements = []
        for key in keys:
            data = await client.get(key)
            if data:
                a = json.loads(data)
                if status and a.get("status") != status:
                    continue
                if atype and a.get("type") != atype:
                    continue
                if role and a.get("applicableRole") != role:
                    continue
                agreements.append(a)
        agreements.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        return agreements[:limit]

    # --- 同意记录 ---

    async def _redis_add_consent(self, consent: dict) -> None:
        client = await get_redis_client()
        cid = consent["id"]
        uid = consent["userId"]
        await client.set(_k("agreement", "consent", cid),
                         json.dumps(consent, ensure_ascii=False))
        await client.lpush(_k("agreement", "consents_by_user", uid), cid)

    async def _redis_list_consents(self, user_id: int = None,
                                    agreement_id: int = None,
                                    limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        if user_id:
            ids = await client.lrange(
                _k("agreement", "consents_by_user", user_id), 0, limit - 1)
            consents = []
            for cid in ids:
                data = await client.get(_k("agreement", "consent", cid))
                if data:
                    c = json.loads(data)
                    if agreement_id and c.get("agreementId") != agreement_id:
                        continue
                    consents.append(c)
        else:
            keys = await client.keys(_k("agreement", "consent", "*"))
            consents = []
            for key in keys:
                data = await client.get(key)
                if data:
                    c = json.loads(data)
                    if agreement_id and c.get("agreementId") != agreement_id:
                        continue
                    consents.append(c)
        consents.sort(key=lambda c: c.get("signedAt", ""), reverse=True)
        return consents[:limit]

    # --- 角色协议 ---

    async def _redis_get_protocol(self, protocol_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hget(_k("agreement", "protocols"), protocol_id)
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_protocol(self, protocol: dict) -> None:
        client = await get_redis_client()
        await client.hset(_k("agreement", "protocols"), protocol["id"],
                          json.dumps(protocol, ensure_ascii=False))

    async def _redis_list_protocols(self, role: str = None, status: str = None,
                                     limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        all_protocols = await client.hgetall(_k("agreement", "protocols"))
        protocols = [json.loads(v) for v in all_protocols.values()]
        if role:
            protocols = [p for p in protocols if p.get("role") == role]
        if status:
            protocols = [p for p in protocols if p.get("status") == status]
        protocols.sort(key=lambda p: p.get("createdAt", ""), reverse=True)
        return protocols[:limit]
