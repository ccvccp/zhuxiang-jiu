"""网站条款及角色协议管理模块业务逻辑层

核心业务:
    - 条款版本管理(创建/更新/版本递增/历史版本)
    - 条款生效(草稿→发布, 归档旧版本)
    - 用户同意记录(签署/查询/幂等)
    - 角色协议配置(关联条款与角色/必选项)
    - 条款统计

锁保护:
    - 发布/更新: lock:agreement:{agreement_id}
    - 同意记录: lock:agreement:consent:{user_id}:{agreement_id}
    - 角色协议: lock:agreement:protocol:{protocol_id}

异常约定:
    - KeyError → 404(条款/协议不存在)
    - ValueError → 409(状态冲突/未发布/重复同意)
"""


from core.locks import get_lock
from core.helpers import ts
from repositories.agreement_repository import (
    AgreementRepository,
    # 条款类型
    AGREEMENT_STATUS_DRAFT, AGREEMENT_STATUS_PUBLISHED, SIGN_METHOD_CHECKBOX, PROTOCOL_STATUS_ACTIVE,
)


# ============================================================
# 业务规则常量
# ============================================================

# 初始版本号
INITIAL_VERSION = "v1.0"


class AgreementService:
    """网站条款及角色协议管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: AgreementRepository = AgreementRepository()):
        self.repo = repo

    # ============================================================
    # 1. 条款管理
    # ============================================================

    async def create_agreement(self, agreement_no: str, name: str,
                                 atype: str, applicable_role: str,
                                 content: str = "", legal_basis: str = "",
                                 change_log: str = "") -> dict:
        """创建条款(初始版本 v1.0, 状态=草稿)

        Raises:
            ValueError: 编号已存在
        """
        existing = await self.repo.find_by_no(agreement_no)
        if existing is not None:
            raise ValueError(f"条款编号已存在(agreementNo={agreement_no})")

        agreement_id = await self.repo.next_agreement_id()
        now = ts()
        agreement = {
            "id": agreement_id,
            "agreementNo": agreement_no,
            "name": name,
            "type": atype,
            "applicableRole": applicable_role,
            "legalBasis": legal_basis,
            "currentVersion": INITIAL_VERSION,
            "content": content,
            "changeLog": change_log,
            "status": AGREEMENT_STATUS_DRAFT,
            "effectiveDate": None,
            "versionHistory": [],  # 发布后归档旧版本
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repo.save_agreement(agreement)
        return agreement

    async def get_agreement(self, agreement_id: int) -> dict:
        """查询条款详情

        Raises:
            KeyError: 条款不存在
        """
        agreement = await self.repo.get_agreement(agreement_id)
        if agreement is None:
            raise KeyError(f"条款不存在(id={agreement_id})")
        return agreement

    async def list_agreements(self, status: str = None, atype: str = None,
                               role: str = None, limit: int = 100) -> list[dict]:
        """查询条款列表"""
        return await self.repo.list_agreements(status, atype, role, limit)

    async def update_agreement(self, agreement_id: int, updates: dict) -> dict:
        """更新条款(仅草稿状态可改)

        Raises:
            KeyError: 条款不存在
            ValueError: 状态不允许修改
        """
        lock_key = f"agreement:{agreement_id}"
        async with get_lock(lock_key):
            agreement = await self.repo.get_agreement(agreement_id)
            if agreement is None:
                raise KeyError(f"条款不存在(id={agreement_id})")
            if agreement["status"] != AGREEMENT_STATUS_DRAFT:
                raise ValueError(
                    f"当前状态({agreement['status']})不允许修改, 仅草稿可改"
                )
            safe_updates = {k: v for k, v in updates.items()
                            if k not in ("id", "agreementNo", "versionHistory",
                                         "createdAt", "currentVersion")}
            agreement.update(safe_updates)
            agreement["updatedAt"] = ts()
            await self.repo.save_agreement(agreement)
            return agreement

    # ============================================================
    # 2. 条款生效(发布)
    # ============================================================

    async def publish_agreement(self, agreement_id: int,
                                  effective_date: str = None) -> dict:
        """条款生效(发布)

        规则:
            - 状态必须为 draft
            - 归档当前版本到 versionHistory
            - 状态 → published
            - 设置 effectiveDate

        Raises:
            KeyError: 条款不存在
            ValueError: 状态不允许发布
        """
        lock_key = f"agreement:{agreement_id}"
        async with get_lock(lock_key):
            agreement = await self.repo.get_agreement(agreement_id)
            if agreement is None:
                raise KeyError(f"条款不存在(id={agreement_id})")
            if agreement["status"] != AGREEMENT_STATUS_DRAFT:
                raise ValueError(
                    f"当前状态({agreement['status']})不允许发布, 仅草稿可发布"
                )

            # 归档当前版本(若有内容)
            if agreement.get("content"):
                archived = {
                    "version": agreement["currentVersion"],
                    "content": agreement.get("content", ""),
                    "changeLog": agreement.get("changeLog", ""),
                    "publishedAt": ts(),
                }
                agreement["versionHistory"].append(archived)

            agreement["status"] = AGREEMENT_STATUS_PUBLISHED
            agreement["effectiveDate"] = effective_date or ts()
            agreement["updatedAt"] = ts()
            await self.repo.save_agreement(agreement)

            return {
                "agreementId": agreement_id,
                "status": AGREEMENT_STATUS_PUBLISHED,
                "version": agreement["currentVersion"],
                "effectiveDate": agreement["effectiveDate"],
                "publishedAt": ts(),
            }

    async def new_version(self, agreement_id: int, content: str,
                            change_log: str = "") -> dict:
        """创建新版本(已发布条款的版本递增)

        规则:
            - 条款必须为 published
            - 归档旧版本到 versionHistory
            - 版本号递增(v1.0 → v1.1)
            - 状态重置为 draft(需重新发布)

        Raises:
            KeyError: 条款不存在
            ValueError: 状态不允许创建新版本
        """
        lock_key = f"agreement:{agreement_id}"
        async with get_lock(lock_key):
            agreement = await self.repo.get_agreement(agreement_id)
            if agreement is None:
                raise KeyError(f"条款不存在(id={agreement_id})")
            if agreement["status"] != AGREEMENT_STATUS_PUBLISHED:
                raise ValueError(
                    f"当前状态({agreement['status']})不允许创建新版本, 仅已发布可创建"
                )

            # 归档当前版本
            archived = {
                "version": agreement["currentVersion"],
                "content": agreement.get("content", ""),
                "changeLog": agreement.get("changeLog", ""),
                "publishedAt": agreement.get("effectiveDate", ""),
            }
            agreement["versionHistory"].append(archived)

            # 版本递增
            old_version = agreement["currentVersion"]
            new_ver = self._bump_version(old_version)
            agreement["currentVersion"] = new_ver
            agreement["content"] = content
            agreement["changeLog"] = change_log
            agreement["status"] = AGREEMENT_STATUS_DRAFT
            agreement["effectiveDate"] = None
            agreement["updatedAt"] = ts()
            await self.repo.save_agreement(agreement)

            return {
                "agreementId": agreement_id,
                "oldVersion": old_version,
                "newVersion": new_ver,
                "status": AGREEMENT_STATUS_DRAFT,
            }

    def _bump_version(self, version: str) -> str:
        """版本号递增(v1.0 → v1.1)"""
        try:
            parts = version.lstrip("v").split(".")
            major = int(parts[0])
            minor = int(parts[1]) + 1
            return f"v{major}.{minor}"
        except (ValueError, IndexError):
            return INITIAL_VERSION

    # ============================================================
    # 3. 条款历史版本
    # ============================================================

    async def get_version_history(self, agreement_id: int) -> list[dict]:
        """查询条款历史版本

        Raises:
            KeyError: 条款不存在
        """
        agreement = await self.repo.get_agreement(agreement_id)
        if agreement is None:
            raise KeyError(f"条款不存在(id={agreement_id})")
        return agreement.get("versionHistory", [])

    # ============================================================
    # 4. 用户同意记录
    # ============================================================

    async def consent(self, user_id: int, agreement_id: int,
                        sign_method: str = SIGN_METHOD_CHECKBOX,
                        ip: str = "", device: str = "") -> dict:
        """用户同意(签署)条款

        规则:
            - 条款必须为 published
            - 记录当前版本号
            - 幂等: 重复同意更新记录(不报错)

        Raises:
            KeyError: 条款不存在
            ValueError: 条款未发布
        """
        lock_key = f"agreement:consent:{user_id}:{agreement_id}"
        async with get_lock(lock_key):
            agreement = await self.repo.get_agreement(agreement_id)
            if agreement is None:
                raise KeyError(f"条款不存在(id={agreement_id})")
            if agreement["status"] != AGREEMENT_STATUS_PUBLISHED:
                raise ValueError(
                    f"条款未发布(状态={agreement['status']}), 不可同意"
                )

            consent = {
                "userId": user_id,
                "agreementId": agreement_id,
                "agreementNo": agreement.get("agreementNo"),
                "version": agreement["currentVersion"],
                "signMethod": sign_method,
                "ip": ip,
                "device": device,
                "signedAt": ts(),
            }
            consent_id = await self.repo.add_consent(consent)
            consent["id"] = consent_id
            return consent

    async def list_consents(self, user_id: int = None,
                              agreement_id: int = None,
                              limit: int = 100) -> list[dict]:
        """查询同意记录"""
        return await self.repo.list_consents(user_id, agreement_id, limit)

    async def check_consent(self, user_id: int,
                              agreement_id: int) -> dict:
        """检查用户是否已同意某条款

        Returns:
            {agreed: bool, version: str, signedAt: str}
        """
        consent = await self.repo.find_consent(user_id, agreement_id)
        if consent is None:
            return {"agreed": False, "version": None, "signedAt": None}
        return {
            "agreed": True,
            "version": consent.get("version"),
            "signedAt": consent.get("signedAt"),
        }

    # ============================================================
    # 5. 角色协议配置
    # ============================================================

    async def create_protocol(self, role: str, agreement_id: int,
                                required: bool = True) -> dict:
        """创建角色协议(关联条款与角色)

        Raises:
            KeyError: 条款不存在
            ValueError: 角色协议已存在
        """
        agreement = await self.repo.get_agreement(agreement_id)
        if agreement is None:
            raise KeyError(f"条款不存在(id={agreement_id})")

        existing = await self.repo.find_protocol(role, agreement_id)
        if existing is not None:
            raise ValueError(
                f"角色协议已存在(role={role}, agreementId={agreement_id})"
            )

        protocol_id = await self.repo.next_protocol_id()
        now = ts()
        protocol = {
            "id": protocol_id,
            "role": role,
            "agreementId": agreement_id,
            "agreementNo": agreement.get("agreementNo"),
            "agreementName": agreement.get("name"),
            "required": required,
            "status": PROTOCOL_STATUS_ACTIVE,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repo.save_protocol(protocol)
        return protocol

    async def list_protocols(self, role: str = None,
                              status: str = None,
                              limit: int = 100) -> list[dict]:
        """查询角色协议列表"""
        return await self.repo.list_protocols(role, status, limit)

    async def update_protocol(self, protocol_id: int,
                                updates: dict) -> dict:
        """更新角色协议

        Raises:
            KeyError: 协议不存在
        """
        lock_key = f"agreement:protocol:{protocol_id}"
        async with get_lock(lock_key):
            protocol = await self.repo.get_protocol(protocol_id)
            if protocol is None:
                raise KeyError(f"角色协议不存在(id={protocol_id})")
            safe_updates = {k: v for k, v in updates.items()
                            if k not in ("id", "role", "agreementId",
                                         "agreementNo", "createdAt")}
            protocol.update(safe_updates)
            protocol["updatedAt"] = ts()
            await self.repo.save_protocol(protocol)
            return protocol

    # ============================================================
    # 6. 条款统计
    # ============================================================

    async def get_stats(self) -> dict:
        """条款模块总览统计

        返回:
            - 条款总数/按状态分布/按类型分布
            - 已发布条款数
            - 用户同意记录总数
            - 角色协议总数/按角色分布
        """
        all_agreements = await self.repo.list_agreements(limit=10000)
        all_protocols = await self.repo.list_protocols(limit=10000)

        status_dist = {}
        type_dist = {}
        published_count = 0
        for a in all_agreements:
            s = a.get("status", "unknown")
            status_dist[s] = status_dist.get(s, 0) + 1
            t = a.get("type", "unknown")
            type_dist[t] = type_dist.get(t, 0) + 1
            if s == AGREEMENT_STATUS_PUBLISHED:
                published_count += 1

        role_dist = {}
        active_protocols = 0
        for p in all_protocols:
            r = p.get("role", "unknown")
            role_dist[r] = role_dist.get(r, 0) + 1
            if p.get("status") == PROTOCOL_STATUS_ACTIVE:
                active_protocols += 1

        return {
            "totalAgreements": len(all_agreements),
            "publishedAgreements": published_count,
            "statusDistribution": status_dist,
            "typeDistribution": type_dist,
            "totalProtocols": len(all_protocols),
            "activeProtocols": active_protocols,
            "roleDistribution": role_dist,
        }
