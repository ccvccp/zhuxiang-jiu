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
import logging

from repositories.agreement_repository import (
    AgreementRepository,
    # 条款类型
    AGREEMENT_STATUS_DRAFT, AGREEMENT_STATUS_PUBLISHED, SIGN_METHOD_CHECKBOX, PROTOCOL_STATUS_ACTIVE,
)

logger = logging.getLogger(__name__)


# ============================================================
# 业务规则常量
# ============================================================

# 初始版本号
INITIAL_VERSION = "v1.0"

# ============================================================
# 变更通知规则(P2: 7/30/14 天提前告知机制)
# ============================================================

# 变更分级: 重大(免责/违约金/价格/隐私等核心权利义务)/重要(一般
# 权利义务调整)/一般(文字性表述优化)——提前通知期对应 30/14/7 天
CHANGE_LEVEL_MAJOR = "major"
CHANGE_LEVEL_IMPORTANT = "important"
CHANGE_LEVEL_MINOR = "minor"
CHANGE_NOTICE_DAYS = {
    CHANGE_LEVEL_MAJOR: 30,
    CHANGE_LEVEL_IMPORTANT: 14,
    CHANGE_LEVEL_MINOR: 7,
}
# 通知状态: pending(通知期中) → effective(已生效) / cancelled(已撤回)
NOTICE_STATUS_PENDING = "pending"
NOTICE_STATUS_EFFECTIVE = "effective"
NOTICE_STATUS_CANCELLED = "cancelled"


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

            # AI 发布门(全站批次五——18号条款接线补全)
            # agreement_risk 阈值类评分器: observe 模式仅评分快照
            # (行为 100% 兼容); enforce 模式 high 拦截(免责密度/
            # 违约金异常/关键条款缺失)。状态机硬规则已前置,
            # AI 门为叠加层; agr:{agreementId} 贯穿快照与发布
            # 终态——配对键一致; fail-open 兜底。
            try:
                from services.ai_enforcement_wiring import (
                    enrich_agreement_publish,
                    enforce_agreement_publish,
                )
                ctx = await enrich_agreement_publish(
                    agreement)
                await enforce_agreement_publish(
                    agreement_id, ctx)
            except ValueError:
                raise  # AI 拦截(enforce 模式)
            except Exception:
                pass  # fail-open: 富化异常不阻塞发布

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

            # 回流钩子(18号条款发布决策门——终态自动反馈)
            try:
                from services.ai_feedback_hooks import (
                    on_agreement_settled,
                )
                await on_agreement_settled(
                    agreement_id, True)
            except Exception:
                pass

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

    async def consent_role_required(self, user_id: int, role: str,
                                     ip: str = "",
                                     sign_method: str =
                                     SIGN_METHOD_CHECKBOX) -> list[dict]:
        """签署角色全部必签协议(2026-10-03 智法: 注册/入驻自动留痕)

        对该角色 status=active 且 required=true 的协议逐个落
        同意记录(勾选行为已在业务流程发生, 此处补签署留痕);
        单份失败不阻断(fail-soft), 记 warning 供对账。

        Returns:
            已签署的 consent 记录列表
        """
        signed = []
        try:
            protocols = await self.repo.list_protocols(
                role, PROTOCOL_STATUS_ACTIVE, 100)
        except Exception as exc:
            logger.warning("角色协议查询失败(role=%s): %s", role, exc)
            return signed
        for p in protocols:
            if not p.get("required"):
                continue
            try:
                c = await self.consent(
                    user_id, p["agreementId"],
                    sign_method=sign_method, ip=ip)
                signed.append(c)
            except Exception as exc:
                logger.warning(
                    "协议签署留痕失败(userId=%s role=%s agreement=%s):"
                    " %s", user_id, role, p.get("agreementNo"), exc)
        return signed

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

    # ============================================================
    # 7. 变更通知(P2: 7/30/14 天提前告知机制)
    # ============================================================

    async def create_change_notice(self, agreement_id: int,
                                     change_level: str,
                                     change_log: str = "",
                                     effective_date: str = None) -> dict:
        """登记条款变更通知(新版发布后)

        规则:
            - 变更分级合法(major/important/minor)
            - 条款必须为 published(先完成 new_version+publish 再登记)
            - 提前通知期: major=30 天 / important=14 天 / minor=7 天
            - effectiveDate 缺省 = 登记时刻 + 通知期; 显式指定时
              不得早于该下限(合规红线, 拒绝而非截断)
            - 站内信触达已同意旧版会员(best-effort, system 必需类
              不受静默时段/频率限制)

        Raises:
            KeyError: 条款不存在
            ValueError: 分级非法/条款未发布/提前期不足
        """
        from datetime import datetime, timedelta

        if change_level not in CHANGE_NOTICE_DAYS:
            raise ValueError(
                f"变更分级非法({change_level}), 应为 "
                f"{'/'.join(CHANGE_NOTICE_DAYS)}")
        notice_days = CHANGE_NOTICE_DAYS[change_level]

        lock_key = f"agreement:notice:{agreement_id}"
        async with get_lock(lock_key):
            agreement = await self.repo.get_agreement(agreement_id)
            if agreement is None:
                raise KeyError(f"条款不存在(id={agreement_id})")
            if agreement["status"] != AGREEMENT_STATUS_PUBLISHED:
                raise ValueError(
                    f"条款未发布(状态={agreement['status']}), "
                    f"请先完成新版本发布再登记变更通知")

            created_at = ts()
            earliest = (datetime.fromisoformat(created_at)
                        + timedelta(days=notice_days))
            if effective_date:
                try:
                    eff = datetime.fromisoformat(effective_date)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"生效日期格式非法({effective_date}), 应为 ISO8601")
                if eff < earliest:
                    raise ValueError(
                        f"{change_level} 级变更须提前至少 {notice_days} 天"
                        f"通知(最早 {earliest.isoformat()})")
            else:
                effective_date = earliest.isoformat()

            history = agreement.get("versionHistory") or []
            # fromVersion = 最近一个不同于现行版本的归档版本
            # (publish 也会归档当前版本, 直接取 history[-1] 可能与
            #  toVersion 相同而失去"从哪版变更而来"的信息量)
            from_version = INITIAL_VERSION
            for h in reversed(history):
                if h.get("version") != agreement["currentVersion"]:
                    from_version = h["version"]
                    break
            notice = {
                "agreementId": agreement_id,
                "agreementNo": agreement.get("agreementNo"),
                "name": agreement.get("name"),
                "fromVersion": from_version,
                "toVersion": agreement["currentVersion"],
                "changeLevel": change_level,
                "changeLog": change_log or agreement.get("changeLog", ""),
                "noticeDays": notice_days,
                "createdAt": created_at,
                "effectiveDate": effective_date,
                "status": NOTICE_STATUS_PENDING,
                "notifiedCount": 0,
            }
            notice_id = await self.repo.next_change_notice_id()
            notice["id"] = notice_id
            await self.repo.save_change_notice(notice)

        # 站内信触达已同意旧版会员(best-effort 不阻断)
        notice["notifiedCount"] = await self._notify_consented_members(
            notice)
        await self.repo.save_change_notice(notice)
        return notice

    async def _notify_consented_members(self, notice: dict) -> int:
        """给已同意该条款的会员发站内信(system 必需类, best-effort)

        Returns:
            触达人数(发送失败不影响主流程, 计数为成功数)
        """
        try:
            from services.message_service import MessageService
            from repositories.message_repository import (
                CHANNEL_INMAIL, CATEGORY_SYSTEM,
            )
            consents = await self.repo.list_consents(
                agreement_id=notice["agreementId"], limit=10000)
            member_ids = list({c.get("userId") for c in consents
                               if c.get("userId") is not None})
            msg = MessageService()
            title = (f"《{notice.get('name') or notice.get('agreementNo')}"
                     f"》条款变更通知")
            content = (f"条款 {notice.get('name')} 将由 "
                       f"{notice['fromVersion']} 更新至 "
                       f"{notice['toVersion']}(自 "
                       f"{notice['effectiveDate'][:10]} 起生效), "
                       f"变更说明: {notice.get('changeLog') or '见条款正文'}")
            sent = 0
            for mid in member_ids:
                try:
                    await msg.send_message(
                        mid, CHANNEL_INMAIL, title, content, CATEGORY_SYSTEM)
                    sent += 1
                except Exception:
                    continue
            return sent
        except Exception:
            return 0

    async def list_change_notices(self, status: str = None,
                                    change_level: str = None,
                                    agreement_id: int = None,
                                    limit: int = 100) -> list[dict]:
        """变更通知列表(查询时惰性推进到期状态)"""
        notices = await self.repo.list_change_notices(
            status, change_level, agreement_id, limit)
        now = ts()
        for n in notices:
            if (n.get("status") == NOTICE_STATUS_PENDING
                    and n.get("effectiveDate", "") <= now):
                n["status"] = NOTICE_STATUS_EFFECTIVE
                await self.repo.save_change_notice(n)
        return notices

    async def get_change_notice(self, notice_id: int) -> dict:
        """变更通知详情(到期惰性推进)

        Raises:
            KeyError: 通知不存在
        """
        notice = await self.repo.get_change_notice(notice_id)
        if notice is None:
            raise KeyError(f"变更通知不存在(id={notice_id})")
        if (notice.get("status") == NOTICE_STATUS_PENDING
                and notice.get("effectiveDate", "") <= ts()):
            notice["status"] = NOTICE_STATUS_EFFECTIVE
            await self.repo.save_change_notice(notice)
        return notice

    async def cancel_change_notice(self, notice_id: int) -> dict:
        """撤回变更通知(仅通知期中可撤回)

        Raises:
            KeyError: 通知不存在
            ValueError: 已生效/已撤回
        """
        lock_key = f"agreement:notice-cancel:{notice_id}"
        async with get_lock(lock_key):
            notice = await self.repo.get_change_notice(notice_id)
            if notice is None:
                raise KeyError(f"变更通知不存在(id={notice_id})")
            if notice.get("status") != NOTICE_STATUS_PENDING:
                raise ValueError(
                    f"当前状态({notice.get('status')})不可撤回, "
                    f"仅通知期中(pending)可撤回")
            # 已过生效时刻的不可撤回(合规: 已生效变更须走新变更流程)
            if notice.get("effectiveDate", "") <= ts():
                raise ValueError("变更已过生效时刻, 不可撤回")
            notice["status"] = NOTICE_STATUS_CANCELLED
            notice["updatedAt"] = ts()
            await self.repo.save_change_notice(notice)
            return notice

    async def run_change_notice_scan(self) -> dict:
        """变更通知到期扫描(批量 pending→effective, 可独立调用)

        Returns:
            {"scanned": 扫描数, "effective": 本次生效数}
        """
        notices = await self.repo.list_change_notices(
            status=NOTICE_STATUS_PENDING, limit=10000)
        now = ts()
        effective = 0
        for n in notices:
            if n.get("effectiveDate", "") <= now:
                n["status"] = NOTICE_STATUS_EFFECTIVE
                n["updatedAt"] = now
                await self.repo.save_change_notice(n)
                effective += 1
        return {"scanned": len(notices), "effective": effective}
