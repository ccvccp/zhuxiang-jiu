"""合作接口管理模块业务逻辑层

核心业务:
    - 合作申请(提交/审核/签约/终止)
    - 合作协议管理(创建/列表/详情/终止)
    - 合作方管理(创建/分级/状态流转)
    - AI智能资质审核(资质文件校验+风险评分)
    - 合作统计

锁保护:
    - 审核/签约: lock:cooperation:app:{application_id}
    - 合作方分级/状态: lock:cooperation:partner:{partner_id}
    - 协议终止: lock:cooperation:contract:{contract_id}

异常约定:
    - KeyError → 404(申请/协议/合作方不存在)
    - ValueError → 409(状态冲突/审核不通过/资质违规)
"""

from datetime import datetime

from core.locks import get_lock
from core.helpers import ts
from repositories.cooperation_repository import (
    CooperationRepository,
    # 合作方类型
    PARTNER_TYPE_ENTERPRISE, PARTNER_TYPE_PERSONAL,
    PARTNER_TYPE_GOVERNMENT, PARTNER_TYPE_DEALER,
    # 资质状态
    QUAL_STATUS_PENDING, QUAL_STATUS_APPROVED, QUAL_STATUS_REJECTED, QUAL_STATUS_EXPIRED,
    # 合作方分级
    PARTNER_LEVEL_BRONZE, PARTNER_LEVEL_SILVER, PARTNER_LEVEL_GOLD, PARTNER_LEVEL_STRATEGIC,
    # 合作方状态
    PARTNER_STATUS_PENDING, PARTNER_STATUS_ACTIVE, PARTNER_STATUS_SUSPENDED, PARTNER_STATUS_TERMINATED,
    # 申请类型
    APP_TYPE_NEW, APP_TYPE_RENEWAL, APP_TYPE_UPGRADE,
    # 申请状态
    APP_STATUS_PENDING, APP_STATUS_REVIEWING, APP_STATUS_APPROVED,
    APP_STATUS_REJECTED, APP_STATUS_SIGNED, APP_STATUS_TERMINATED,
    # 协议状态
    CONTRACT_STATUS_DRAFT, CONTRACT_STATUS_ACTIVE,
    CONTRACT_STATUS_EXPIRED, CONTRACT_STATUS_TERMINATED,
)


# ============================================================
# 审核规则常量
# ============================================================

# AI审核通过分数线
AI_REVIEW_PASS_SCORE = 80
# 大额合作阈值(超过需人工复核)
LARGE_AMOUNT_THRESHOLD = 500000
# 最低合作金额
MIN_AMOUNT = 1000
# 资质文件最少数量
MIN_QUALIFICATION_FILES = 1


class CooperationService:
    """合作接口管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: CooperationRepository = CooperationRepository()):
        self.repo = repo

    # ============================================================
    # 1. 合作申请
    # ============================================================

    async def create_application(self, partner_name: str, partner_type: str,
                                  app_type: str, business_scope: str,
                                  estimated_amount: float,
                                  contact_name: str = "",
                                  contact_phone: str = "",
                                  contact_email: str = "",
                                  qualification_files: list = None,
                                  delivery_date: str = None) -> dict:
        """提交合作申请

        规则:
            - 校验合作金额(>= MIN_AMOUNT)
            - 创建或复用合作方(状态=pending)
            - 申请状态=pending

        Raises:
            ValueError: 金额不合法
        """
        if estimated_amount < MIN_AMOUNT:
            raise ValueError(
                f"合作金额({estimated_amount})低于最低标准({MIN_AMOUNT})"
            )

        # 创建或复用合作方
        existing = await self.repo.find_partner_by_name(partner_name)
        if existing is not None:
            partner_id = existing["id"]
            partner_no = existing["partnerNo"]
        else:
            partner_id = await self.repo.next_partner_id()
            partner_no = f"PT{int(datetime.utcnow().timestamp())}{partner_id:06d}"
            partner = {
                "id": partner_id,
                "partnerNo": partner_no,
                "name": partner_name,
                "type": partner_type,
                "contactName": contact_name,
                "contactPhone": contact_phone,
                "contactEmail": contact_email,
                "qualStatus": QUAL_STATUS_PENDING,
                "level": PARTNER_LEVEL_BRONZE,
                "status": PARTNER_STATUS_PENDING,
                "totalAmount": 0,
                "contractCount": 0,
                "createdAt": ts(),
                "updatedAt": ts(),
            }
            await self.repo.save_partner(partner)

        # 创建申请
        app_id = await self.repo.next_application_id()
        app_no = f"CA{int(datetime.utcnow().timestamp())}{app_id:06d}"
        now = ts()
        application = {
            "id": app_id,
            "applicationNo": app_no,
            "partnerId": partner_id,
            "partnerName": partner_name,
            "partnerNo": partner_no,
            "partnerType": partner_type,
            "type": app_type,
            "businessScope": business_scope,
            "estimatedAmount": estimated_amount,
            "contactName": contact_name,
            "contactPhone": contact_phone,
            "contactEmail": contact_email,
            "qualificationFiles": qualification_files or [],
            "deliveryDate": delivery_date,
            "status": APP_STATUS_PENDING,
            "reviewScore": 0,
            "reviewRemark": "",
            "contractId": None,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repo.save_application(application)
        return application

    async def get_application(self, application_id: int) -> dict:
        """查询申请详情

        Raises:
            KeyError: 申请不存在
        """
        app = await self.repo.get_application(application_id)
        if app is None:
            raise KeyError(f"合作申请不存在(id={application_id})")
        return app

    async def list_applications(self, status: str = None,
                                 partner_id: int = None,
                                 limit: int = 100) -> list[dict]:
        """查询申请列表"""
        return await self.repo.list_applications(status, partner_id, limit)

    # ============================================================
    # 2. AI资质审核
    # ============================================================

    async def review_application(self, application_id: int) -> dict:
        """AI审核合作申请(资质审核)

        规则:
            - 状态必须为 pending
            - 资质文件数量校验(>= MIN_QUALIFICATION_FILES)
            - 金额合理性校验(大额需人工复核提示)
            - 合规评分: 100 - 违规项×20(下限0)
            - 评分>=80 → 通过(approved)
            - 评分<80 → 驳回(rejected)
            - 同步更新合作方资质状态

        Raises:
            KeyError: 申请不存在
            ValueError: 状态不允许审核
        """
        lock_key = f"cooperation:app:{application_id}"
        async with get_lock(lock_key):
            app = await self.repo.get_application(application_id)
            if app is None:
                raise KeyError(f"合作申请不存在(id={application_id})")
            if app["status"] != APP_STATUS_PENDING:
                raise ValueError(
                    f"当前状态({app['status']})不可审核, 仅待审核可审核"
                )

            # 进入审核中
            app["status"] = APP_STATUS_REVIEWING
            app["updatedAt"] = ts()
            await self.repo.save_application(app)

            # AI资质审核
            issues = []
            files = app.get("qualificationFiles", [])

            # 1. 资质文件数量
            if len(files) < MIN_QUALIFICATION_FILES:
                issues.append({
                    "type": "missing_qualification",
                    "detail": f"资质文件不足(需>={MIN_QUALIFICATION_FILES}, 实际{len(files)})",
                })

            # 2. 金额合理性(大额需人工复核)
            amount = app.get("estimatedAmount", 0)
            if amount >= LARGE_AMOUNT_THRESHOLD:
                issues.append({
                    "type": "large_amount_review",
                    "detail": f"大额合作({amount})需人工复核",
                })

            # 3. 联系信息完整性
            if not app.get("contactPhone"):
                issues.append({
                    "type": "missing_contact",
                    "detail": "缺少联系电话",
                })

            # 合规评分
            score = max(0, 100 - len(issues) * 20)
            # 缺少资质文件为严重违规, 直接驳回(不论评分)
            has_critical = any(i["type"] == "missing_qualification" for i in issues)
            if has_critical:
                result = "reject"
            else:
                result = "pass" if score >= AI_REVIEW_PASS_SCORE else "reject"
            new_status = APP_STATUS_APPROVED if result == "pass" else APP_STATUS_REJECTED

            # 更新申请状态
            app["status"] = new_status
            app["reviewScore"] = score
            app["reviewRemark"] = (
                "" if result == "pass" else "请补充资质材料后重新提交"
            )
            app["updatedAt"] = ts()
            await self.repo.save_application(app)

            # 同步合作方资质状态
            partner = await self.repo.get_partner(app["partnerId"])
            if partner is not None:
                partner["qualStatus"] = (
                    QUAL_STATUS_APPROVED if result == "pass" else QUAL_STATUS_REJECTED
                )
                partner["updatedAt"] = ts()
                await self.repo.save_partner(partner)

            return {
                "applicationId": application_id,
                "result": result,
                "score": score,
                "status": new_status,
                "issues": issues,
            }

    # ============================================================
    # 3. 签约(状态流转: approved → signed)
    # ============================================================

    async def sign_application(self, application_id: int,
                                 contract_title: str = "",
                                 start_date: str = None,
                                 end_date: str = None,
                                 deposit_amount: float = 0) -> dict:
        """签约(创建协议+激活合作方)

        规则:
            - 申请状态必须为 approved
            - 创建合作协议(status=active)
            - 激活合作方(status=active)
            - 申请状态 → signed

        Raises:
            KeyError: 申请不存在
            ValueError: 状态不允许签约
        """
        lock_key = f"cooperation:app:{application_id}"
        async with get_lock(lock_key):
            app = await self.repo.get_application(application_id)
            if app is None:
                raise KeyError(f"合作申请不存在(id={application_id})")
            if app["status"] != APP_STATUS_APPROVED:
                raise ValueError(
                    f"当前状态({app['status']})不允许签约, 仅审核通过可签约"
                )

            # 创建协议
            contract_id = await self.repo.next_contract_id()
            contract_no = f"CT{int(datetime.utcnow().timestamp())}{contract_id:06d}"
            now = ts()
            contract = {
                "id": contract_id,
                "contractNo": contract_no,
                "partnerId": app["partnerId"],
                "partnerName": app["partnerName"],
                "applicationId": application_id,
                "title": contract_title or f"{app['partnerName']}合作协议",
                "content": f"业务范围: {app.get('businessScope', '')}",
                "startDate": start_date,
                "endDate": end_date,
                "amount": app.get("estimatedAmount", 0),
                "depositAmount": deposit_amount,
                "status": CONTRACT_STATUS_ACTIVE,
                "signedAt": now,
                "createdAt": now,
                "updatedAt": now,
            }
            await self.repo.save_contract(contract)

            # 激活合作方
            partner = await self.repo.get_partner(app["partnerId"])
            if partner is not None:
                partner["status"] = PARTNER_STATUS_ACTIVE
                partner["contractCount"] = partner.get("contractCount", 0) + 1
                partner["totalAmount"] = round(
                    partner.get("totalAmount", 0) + app.get("estimatedAmount", 0), 2
                )
                partner["updatedAt"] = ts()
                await self.repo.save_partner(partner)

            # 更新申请状态
            app["status"] = APP_STATUS_SIGNED
            app["contractId"] = contract_id
            app["updatedAt"] = ts()
            await self.repo.save_application(app)

            return {
                "applicationId": application_id,
                "status": APP_STATUS_SIGNED,
                "contractId": contract_id,
                "contractNo": contract_no,
                "partnerStatus": PARTNER_STATUS_ACTIVE,
                "signedAt": now,
            }

    # ============================================================
    # 4. 合作协议管理
    # ============================================================

    async def create_contract(self, partner_id: int, title: str,
                                content: str = "", amount: float = 0,
                                start_date: str = None, end_date: str = None,
                                deposit_amount: float = 0) -> dict:
        """独立创建合作协议

        Raises:
            KeyError: 合作方不存在
            ValueError: 合作方已终止
        """
        partner = await self.repo.get_partner(partner_id)
        if partner is None:
            raise KeyError(f"合作方不存在(id={partner_id})")
        if partner["status"] == PARTNER_STATUS_TERMINATED:
            raise ValueError("合作方已终止, 不可创建协议")

        contract_id = await self.repo.next_contract_id()
        contract_no = f"CT{int(datetime.utcnow().timestamp())}{contract_id:06d}"
        now = ts()
        contract = {
            "id": contract_id,
            "contractNo": contract_no,
            "partnerId": partner_id,
            "partnerName": partner.get("name"),
            "applicationId": None,
            "title": title,
            "content": content,
            "startDate": start_date,
            "endDate": end_date,
            "amount": amount,
            "depositAmount": deposit_amount,
            "status": CONTRACT_STATUS_ACTIVE,
            "signedAt": now,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repo.save_contract(contract)

        # 更新合作方合同数与累计金额
        partner["contractCount"] = partner.get("contractCount", 0) + 1
        partner["totalAmount"] = round(partner.get("totalAmount", 0) + amount, 2)
        partner["updatedAt"] = ts()
        await self.repo.save_partner(partner)

        return contract

    async def get_contract(self, contract_id: int) -> dict:
        """查询协议详情

        Raises:
            KeyError: 协议不存在
        """
        contract = await self.repo.get_contract(contract_id)
        if contract is None:
            raise KeyError(f"合作协议不存在(id={contract_id})")
        return contract

    async def list_contracts(self, status: str = None,
                              partner_id: int = None,
                              limit: int = 100) -> list[dict]:
        """查询协议列表"""
        return await self.repo.list_contracts(status, partner_id, limit)

    async def terminate_contract(self, contract_id: int,
                                  reason: str = "") -> dict:
        """终止协议

        Raises:
            KeyError: 协议不存在
            ValueError: 状态不允许终止
        """
        lock_key = f"cooperation:contract:{contract_id}"
        async with get_lock(lock_key):
            contract = await self.repo.get_contract(contract_id)
            if contract is None:
                raise KeyError(f"合作协议不存在(id={contract_id})")
            if contract["status"] not in (CONTRACT_STATUS_ACTIVE, CONTRACT_STATUS_EXPIRED):
                raise ValueError(
                    f"当前状态({contract['status']})不允许终止"
                )

            contract["status"] = CONTRACT_STATUS_TERMINATED
            contract["updatedAt"] = ts()
            await self.repo.save_contract(contract)

            return {
                "contractId": contract_id,
                "status": CONTRACT_STATUS_TERMINATED,
                "reason": reason,
                "terminatedAt": ts(),
            }

    # ============================================================
    # 5. 合作方管理
    # ============================================================

    async def get_partner(self, partner_id: int) -> dict:
        """查询合作方详情

        Raises:
            KeyError: 合作方不存在
        """
        partner = await self.repo.get_partner(partner_id)
        if partner is None:
            raise KeyError(f"合作方不存在(id={partner_id})")
        return partner

    async def list_partners(self, status: str = None, level: str = None,
                             limit: int = 100) -> list[dict]:
        """查询合作方列表"""
        return await self.repo.list_partners(status, level, limit)

    async def update_partner(self, partner_id: int, updates: dict) -> dict:
        """更新合作方(分级/状态/联系信息)

        规则:
            - 状态流转: active → suspended → terminated(不可跳级)
            - 分级调整: bronze/silver/gold/strategic
            - 不可变字段: id, partnerNo, createdAt

        Raises:
            KeyError: 合作方不存在
            ValueError: 状态流转非法
        """
        lock_key = f"cooperation:partner:{partner_id}"
        async with get_lock(lock_key):
            partner = await self.repo.get_partner(partner_id)
            if partner is None:
                raise KeyError(f"合作方不存在(id={partner_id})")

            # 状态流转校验
            new_status = updates.get("status")
            if new_status is not None:
                self._validate_status_transition(partner["status"], new_status)

            safe_updates = {k: v for k, v in updates.items()
                            if k not in ("id", "partnerNo", "createdAt")}
            partner.update(safe_updates)
            partner["updatedAt"] = ts()
            await self.repo.save_partner(partner)
            return partner

    def _validate_status_transition(self, current: str, target: str) -> None:
        """校验合作方状态流转合法性

        合法流转:
            pending → active(签约时自动)
            active → suspended
            suspended → active(恢复)
            suspended → terminated
            active → terminated

        Raises:
            ValueError: 非法状态流转
        """
        allowed = {
            PARTNER_STATUS_PENDING: {PARTNER_STATUS_ACTIVE},
            PARTNER_STATUS_ACTIVE: {PARTNER_STATUS_SUSPENDED, PARTNER_STATUS_TERMINATED},
            PARTNER_STATUS_SUSPENDED: {PARTNER_STATUS_ACTIVE, PARTNER_STATUS_TERMINATED},
            PARTNER_STATUS_TERMINATED: set(),  # 终态不可变更
        }
        if target not in allowed.get(current, set()):
            raise ValueError(
                f"合作方状态流转非法({current} → {target})"
            )

    # ============================================================
    # 6. 合作统计
    # ============================================================

    async def get_stats(self) -> dict:
        """合作模块总览统计

        返回:
            - 合作方总数/按状态分布/按分级分布
            - 申请总数/按状态分布
            - 协议总数/按状态分布
            - 累计合作金额/合同总数
        """
        all_partners = await self.repo.list_partners(limit=10000)
        all_apps = await self.repo.list_applications(limit=10000)
        all_contracts = await self.repo.list_contracts(limit=10000)

        partner_status_dist = {}
        partner_level_dist = {}
        total_amount = 0.0
        for p in all_partners:
            s = p.get("status", "unknown")
            partner_status_dist[s] = partner_status_dist.get(s, 0) + 1
            lv = p.get("level", "unknown")
            partner_level_dist[lv] = partner_level_dist.get(lv, 0) + 1
            total_amount += p.get("totalAmount", 0)

        app_status_dist = {}
        for a in all_apps:
            s = a.get("status", "unknown")
            app_status_dist[s] = app_status_dist.get(s, 0) + 1

        contract_status_dist = {}
        active_contracts = 0
        for c in all_contracts:
            s = c.get("status", "unknown")
            contract_status_dist[s] = contract_status_dist.get(s, 0) + 1
            if s == CONTRACT_STATUS_ACTIVE:
                active_contracts += 1

        return {
            "totalPartners": len(all_partners),
            "totalApplications": len(all_apps),
            "totalContracts": len(all_contracts),
            "activeContracts": active_contracts,
            "totalAmount": round(total_amount, 2),
            "partnerStatusDistribution": partner_status_dist,
            "partnerLevelDistribution": partner_level_dist,
            "applicationStatusDistribution": app_status_dist,
            "contractStatusDistribution": contract_status_dist,
        }
