"""AI智能管理模块(角色经济中枢)业务逻辑层

核心业务(设计文档 v1.1 第四~五章):
    - 角色注册认领制(目录/AI预审/审批/签约/试用/转正)
    - 权责利三合一契约(权限包+责任条款+分润协议, 版本化)
    - AI服务调度中枢(信用×40%+技能×25%+负载×20%+满意度×15%)
    - 服务分润结算引擎(D-8: 1%×断崖满意度×信用×时效, 双封顶)
    - 信用行为总线(事件统一入账, 分发竹信分引擎)

对接模块(不合并, 总线/入账对接):
    - ticket(工单): 派单数据源+满意度/SLA 采集
    - credit(竹信分): 信用分调整与等级查询
    - wallet(钱包): 分润结算入账(deposit_reward 奖励余额)
    - chat(聊天): 转人工时调用调度中枢(建 source=ai 工单+派单)

锁保护:
    - 派单: role:dispatch:{session_id}
    - 结算: role:settle:{ticket_no}(幂等)
    - 签约: role:sign:{claim_id}

异常约定(遵循项目约定):
    - KeyError → 404(目录/申请/契约/工单不存在)
    - ValueError → 409(状态非法/重复认领/已结算等)
"""

import logging
from datetime import datetime, timedelta, timezone

from core.locks import get_lock
from core.helpers import ts
from repositories.role_repository import (
    RoleRepository,
    # 角色
    ROLE_CUSTOMER_SERVICE, ROLE_PRODUCTION_WORKER, ROLE_PLATFORM,
    # 契约状态
    CONTRACT_STATUS_PROBATION, CONTRACT_STATUS_ACTIVE,
    CONTRACT_STATUS_SUSPENDED, CONTRACT_STATUS_TERMINATED,
    DISPATCHABLE_STATUSES,
    # 认领状态
    CLAIM_STATUS_PENDING, CLAIM_STATUS_APPROVED, CLAIM_STATUS_REJECTED,
    # 分润口径
    PROFIT_BASIS_SALE_PRICE, PROFIT_BASIS_PURCHASE_AMOUNT,
    LEDGER_STATUS_PENDING, LEDGER_STATUS_SETTLED, LEDGER_STATUS_REVERSED,
    # 服务分润参数(D-8)
    SERVICE_PROFIT_RATE, SATISFACTION_COEFF, CREDIT_LEVEL_COEFF,
    TIMELINESS_SLA_OK, TIMELINESS_OVERDUE, TIMELINESS_ESCALATED,
    SINGLE_CAP, MONTHLY_CAP, PROBATION_RATE, PROBATION_DAYS,
    CONTRACT_VALID_DAYS, CLAWBACK_FREEZE_THRESHOLD,
    # 工人分润(P1)
    STAGE_PROFIT_RATES, WORKER_QUALITY_COEFF, WORKER_LEDGER_PREFIX,
    # 派单
    DISPATCH_WEIGHTS, SKILL_BASE, DEFAULT_SATISFACTION, LOAD_CAPACITY,
    # 信用行为
    BEHAVIOR_CS_SATISFACTION_GOOD, BEHAVIOR_CS_SATISFACTION_BAD,
    BEHAVIOR_CS_SLA_OVERDUE, BEHAVIOR_CS_ESCALATED,
    BEHAVIOR_CLAIM_APPROVED, BEHAVIOR_DELTAS,
    BEHAVIOR_WORKER_QUALITY_PREMIUM, BEHAVIOR_WORKER_QUALITY_ACCIDENT,
)
from services.ticket_service import TicketService, SLA_HOURS
from services.credit_service import CreditService
from services.wallet_service import WalletService
from repositories.credit_repository import level_from_score
from repositories.trace_prod_repository import (
    TraceProdRepository, RESULT_PASS,
)
from repositories.ticket_repository import (
    PRIORITY_MEDIUM, SOURCE_AI, TICKET_STATUS_PROCESSING,
    TICKET_STATUS_RESOLVED,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """当前 UTC 时间(与 core.helpers.ts() 同为 offset-aware)"""
    return datetime.now(timezone.utc)


class RoleService:
    """AI智能管理模块业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: RoleRepository = RoleRepository()):
        self.repo = repo
        self.ticket_service = TicketService()
        self.credit_service = CreditService()
        self.wallet_service = WalletService()
        self.trace_prod_repo = TraceProdRepository()

    # ============================================================
    # 1. 角色目录
    # ============================================================

    async def list_catalog(self, status: str = None) -> list[dict]:
        """角色目录(含认领条件/配额/分润说明)"""
        return await self.repo.list_catalog(status=status)

    async def upsert_catalog_role(self, role_code: str, role_name: str,
                                  category: str = "other",
                                  claim_conditions: str = "",
                                  credit_threshold: int = 400,
                                  quota: int = 0, profit_desc: str = "",
                                  duty_terms: str = "",
                                  status: str = "active") -> dict:
        """维护角色目录(超管)"""
        role = {
            "roleCode": role_code, "roleName": role_name,
            "category": category, "claimConditions": claim_conditions,
            "creditThreshold": credit_threshold, "quota": quota,
            "profitDesc": profit_desc, "dutyTerms": duty_terms,
            "status": status, "updatedAt": ts(),
        }
        existing = await self.repo.get_catalog_role(role_code)
        if existing:
            role["createdAt"] = existing.get("createdAt", ts())
        else:
            role["createdAt"] = ts()
        return await self.repo.upsert_catalog_role(role)

    # ============================================================
    # 2. 角色认领(注册认领制)
    # ============================================================

    async def create_claim(self, user_id: int, role_code: str,
                           statement: str = "") -> dict:
        """提交认领申请(AI预审: 角色状态/配额/信用门槛/重复认领)

        规则:
            - 角色须存在且 active
            - 同一用户对同一角色不可重复认领(存在有效契约或未决申请)
            - AI预审: 竹信分 ≥ 角色信用门槛; 配额未满
            - 预审不通过 → 直接 rejected; 通过 → pending 待管理员审批

        Raises:
            KeyError: 角色不存在
            ValueError: 重复认领/角色停用
        """
        role = await self.repo.get_catalog_role(role_code)
        if role is None:
            raise KeyError(f"角色不存在(roleCode={role_code})")
        if role.get("status") != "active":
            raise ValueError(f"角色已停用(roleCode={role_code})")

        # 重复认领检查(有效契约或未决申请)
        existing_contract = await self.repo.get_active_contract(user_id, role_code)
        if existing_contract:
            raise ValueError(
                f"已持有该角色有效契约(contractNo={existing_contract['contractNo']})")
        pending = await self.repo.list_claims(
            user_id=user_id, role_code=role_code,
            status=CLAIM_STATUS_PENDING, limit=1)
        if pending:
            raise ValueError(f"存在未审批的认领申请(claimId={pending[0]['id']})")

        # AI 预审
        reasons = []
        score = await self.credit_service.get_score(user_id)
        bamboo = score.get("bambooScore", 0)
        threshold = role.get("creditThreshold", 0)
        if bamboo < threshold:
            reasons.append(
                f"竹信分不足(当前{bamboo} < 门槛{threshold})")
        if role.get("quota", 0) > 0:
            held = await self.repo.list_contracts(
                role_code=role_code, statuses=DISPATCHABLE_STATUSES,
                limit=100000)
            if len(held) >= role["quota"]:
                reasons.append(f"角色配额已满(quota={role['quota']})")
        ai_precheck = {"passed": len(reasons) == 0, "reasons": reasons,
                       "bambooScore": bamboo}

        claim_id = await self.repo.next_id("claim")
        claim = {
            "id": claim_id,
            "userId": user_id,
            "roleCode": role_code,
            "roleName": role.get("roleName", role_code),
            "statement": (statement or "").strip(),
            "aiPrecheck": ai_precheck,
            "status": (CLAIM_STATUS_PENDING if ai_precheck["passed"]
                       else CLAIM_STATUS_REJECTED),
            "reviewer": "ai_precheck" if not ai_precheck["passed"] else "",
            "reviewComment": "; ".join(reasons),
            "createdAt": ts(),
            "reviewedAt": "" if ai_precheck["passed"] else ts(),
        }
        await self.repo.create_claim(claim)
        return claim

    async def list_my_claims(self, user_id: int) -> list[dict]:
        """我的认领申请"""
        return await self.repo.list_claims(user_id=user_id)

    async def admin_list_claims(self, status: str = None,
                                role_code: str = None) -> list[dict]:
        """管理端认领申请列表"""
        return await self.repo.list_claims(status=status, role_code=role_code)

    async def admin_review_claim(self, claim_id: int, approved: bool,
                                 reviewer: str = "admin",
                                 comment: str = "") -> dict:
        """审批认领申请(pending → approved/rejected)

        Raises:
            KeyError: 申请不存在
            ValueError: 状态非法
        """
        claim = await self.repo.get_claim(claim_id)
        if claim is None:
            raise KeyError(f"认领申请不存在(claimId={claim_id})")
        if claim["status"] != CLAIM_STATUS_PENDING:
            raise ValueError(
                f"申请状态非法(当前{claim['status']}, 须为{CLAIM_STATUS_PENDING})")
        updates = {
            "status": CLAIM_STATUS_APPROVED if approved else CLAIM_STATUS_REJECTED,
            "reviewer": reviewer,
            "reviewComment": comment,
            "reviewedAt": ts(),
        }
        await self.repo.update_claim(claim_id, updates)
        claim.update(updates)
        return claim

    # ============================================================
    # 3. 契约(权责利三合一)
    # ============================================================

    async def sign_contract(self, claim_id: int, user_id: int) -> dict:
        """签署契约(approved 申请 → probation 契约)

        契约 = 权限包(映射角色) + 责任条款(dutyTerms)
             + 分润协议(profitDesc) + 信用条款, 版本化绑定。
        签约即触发信用事件 claim_approved(+3)。

        Raises:
            KeyError: 申请不存在
            ValueError: 非本人/未批准/已签署
        """
        async with get_lock(f"role:sign:{claim_id}"):
            claim = await self.repo.get_claim(claim_id)
            if claim is None:
                raise KeyError(f"认领申请不存在(claimId={claim_id})")
            if claim.get("userId") != user_id:
                raise ValueError("仅申请人本人可签署契约")
            if claim.get("status") != CLAIM_STATUS_APPROVED:
                raise ValueError(
                    f"申请未获批准(当前{claim.get('status')})")
            if claim.get("contractId"):
                raise ValueError("该申请已签署契约")

            role = await self.repo.get_catalog_role(claim["roleCode"]) or {}
            now = _utcnow()
            contract_id = await self.repo.next_id("contract")
            contract = {
                "id": contract_id,
                "contractNo": self.repo.generate_contract_no(),
                "userId": user_id,
                "roleCode": claim["roleCode"],
                "roleName": claim.get("roleName", claim["roleCode"]),
                "claimId": claim_id,
                # 权责利三合一(引用目录条款, 版本快照)
                "dutyTerms": role.get("dutyTerms", ""),
                "profitTerms": role.get("profitDesc", ""),
                "profitTemplateVersion": "v1.1",
                "status": CONTRACT_STATUS_PROBATION,   # 试用期(分润减半)
                "signedAt": now.isoformat(),
                "probationEndsAt": (now + timedelta(days=PROBATION_DAYS)).isoformat(),
                "expireAt": (now + timedelta(days=CONTRACT_VALID_DAYS)).isoformat(),
                "terminatedAt": "",
            }
            await self.repo.create_contract(contract)
            await self.repo.update_claim(claim_id,
                                          {"contractId": contract_id})
            # 信用事件: 认领通过 +3
            await self.publish_credit_event(
                user_id=user_id, role_code=claim["roleCode"],
                behavior=BEHAVIOR_CLAIM_APPROVED,
                source_module="role", ref_id=str(contract_id))
            return contract

    async def list_contracts(self, user_id: int = None,
                             role_code: str = None,
                             status: str = None) -> list[dict]:
        """契约列表(含试用期到期动态转正标记)"""
        statuses = (status,) if status else None
        contracts = await self.repo.list_contracts(
            user_id=user_id, role_code=role_code, statuses=statuses)
        return [self._decorate_contract(c) for c in contracts]

    def _decorate_contract(self, contract: dict) -> dict:
        """注入动态字段: 试用期是否已满(可转正)"""
        result = dict(contract)
        effective = contract.get("status")
        if contract.get("status") == CONTRACT_STATUS_PROBATION:
            try:
                ends = datetime.fromisoformat(contract["probationEndsAt"])
                if _utcnow() >= ends:
                    effective = CONTRACT_STATUS_ACTIVE + "_eligible"
            except (ValueError, KeyError):
                pass
        result["effectiveStatus"] = effective
        return result

    async def admin_contract_action(self, contract_id: int,
                                     action: str) -> dict:
        """契约管理动作: activate(转正)/suspend(冻结)/terminate(清退)

        Raises:
            KeyError: 契约不存在
            ValueError: 动作非法
        """
        contract = await self.repo.get_contract(contract_id)
        if contract is None:
            raise KeyError(f"契约不存在(contractId={contract_id})")
        if action == "activate":
            if contract["status"] not in (CONTRACT_STATUS_PROBATION,
                                          CONTRACT_STATUS_SUSPENDED):
                raise ValueError(
                    f"契约状态非法(当前{contract['status']}, "
                    f"须为{CONTRACT_STATUS_PROBATION}/{CONTRACT_STATUS_SUSPENDED})")
            updates = {"status": CONTRACT_STATUS_ACTIVE}
        elif action == "suspend":
            if contract["status"] not in (CONTRACT_STATUS_PROBATION,
                                          CONTRACT_STATUS_ACTIVE):
                raise ValueError("契约已终止, 不可冻结")
            updates = {"status": CONTRACT_STATUS_SUSPENDED}
        elif action == "terminate":
            if contract["status"] == CONTRACT_STATUS_TERMINATED:
                raise ValueError("契约已终止")
            updates = {"status": CONTRACT_STATUS_TERMINATED,
                       "terminatedAt": ts()}
        else:
            raise ValueError(f"动作无效(须为activate/suspend/terminate)")
        await self.repo.update_contract(contract_id, updates)
        contract.update(updates)
        return contract

    async def terminate_contract(self, contract_id: int, user_id: int) -> dict:
        """用户主动退出契约(未入账分润留待人工结算)"""
        contract = await self.repo.get_contract(contract_id)
        if contract is None:
            raise KeyError(f"契约不存在(contractId={contract_id})")
        if contract.get("userId") != user_id:
            raise ValueError("仅契约持有人可退出")
        if contract["status"] == CONTRACT_STATUS_TERMINATED:
            raise ValueError("契约已终止")
        updates = {"status": CONTRACT_STATUS_TERMINATED,
                   "terminatedAt": ts(), "terminateBy": "self"}
        await self.repo.update_contract(contract_id, updates)
        contract.update(updates)
        return contract

    async def admin_probation_sweep(self) -> dict:
        """试用期满自动转正扫描(D-8 细化, 对齐 perm 模块 expire-sweep 风格)

        规则(文档4.1 试用期):
            - probationEndsAt 已过 → 转正(active)
            - 但近30天存在负面信用事件(delta<0)者不自动转正, 保持试用,
              留待管理员人工处置(admin_contract_action activate)

        Returns:
            {activated, held, total, details}
        """
        contracts = await self.repo.list_contracts(
            role_code=None, statuses=(CONTRACT_STATUS_PROBATION,),
            limit=100000)
        now = _utcnow()
        activated, held, details = [], [], []
        for contract in contracts:
            try:
                ends = datetime.fromisoformat(contract["probationEndsAt"])
            except (ValueError, KeyError):
                continue
            if now < ends:
                continue
            # 近30天负面信用事件检查
            since = (now - timedelta(days=30)).isoformat()
            events = await self.repo.list_events(
                user_id=contract["userId"], role_code=contract["roleCode"],
                limit=100000)
            negative = [e for e in events
                        if e.get("delta", 0) < 0 and e.get("ts", "") >= since]
            if negative:
                held.append(contract["contractNo"])
                details.append({
                    "contractNo": contract["contractNo"],
                    "userId": contract["userId"],
                    "decision": "held",
                    "negativeEvents": len(negative),
                })
                continue
            await self.repo.update_contract(contract["id"], {
                "status": CONTRACT_STATUS_ACTIVE,
                "activatedAt": ts(),
                "activation": "probation_sweep",
            })
            activated.append(contract["contractNo"])
            details.append({
                "contractNo": contract["contractNo"],
                "userId": contract["userId"],
                "decision": "activated",
            })
        return {"activated": activated, "held": held,
                "total": len(activated) + len(held), "details": details}

    # ============================================================
    # 4. AI服务调度中枢(派单)
    # ============================================================

    async def dispatch_customer_service(self, session: dict,
                                        reason: str = "") -> dict:
        """AI转人工调度: 自动建 source=ai 工单 + 最优客服派单

        派单算法: 调度得分 = 竹信分×40% + 技能×25% + 负载×20% + 近30天满意度×15%

        Returns:
            {ticketNo, assigneeId, dispatchId, scoreDetail, fallback}
            fallback=True 表示无可用客服(工单留在待分配池)

        注意: 由 chat 模块 _do_transfer 调用, 失败时调用方回退默认客服。
        """
        async with get_lock(f"role:dispatch:{session['sessionId']}"):
            # 幂等: 同一会话重复转人工不重复建单
            existing = await self.repo.get_dispatch_by_session(
                session["sessionId"])
            if existing:
                return {"ticketNo": existing.get("ticketNo", ""),
                        "assigneeId": existing.get("assigneeId"),
                        "dispatchId": existing["id"],
                        "scoreDetail": existing.get("scoreDetail"),
                        "fallback": not existing.get("assigneeId")}

            # 1. 自动创建 source=ai 工单(修复 chat-ticket 断链)
            ticket = await self.ticket_service.create_ticket(
                user_id=session.get("userId", 0),
                ticket_type="presale", priority=PRIORITY_MEDIUM,
                description=f"AI转人工: {reason or '用户请求人工服务'}",
                source=SOURCE_AI)

            # 2. 候选客服(试用+转正契约持有人; 剔除负账冻结者)
            candidates = await self.repo.list_contracts(
                role_code=ROLE_CUSTOMER_SERVICE,
                statuses=DISPATCHABLE_STATUSES)
            best, best_detail = None, None
            for contract in candidates:
                # 负账超阈冻结接单(退款追回风控, D-8 细化)
                clawback = await self.repo.get_clawback(
                    contract["userId"], ROLE_CUSTOMER_SERVICE)
                if clawback > CLAWBACK_FREEZE_THRESHOLD:
                    continue
                detail = await self._score_candidate(contract)
                if best_detail is None or detail["total"] > best_detail["total"]:
                    best, best_detail = contract, detail

            dispatch_id = await self.repo.next_id("dispatch")
            dispatch = {
                "id": dispatch_id,
                "sessionId": session["sessionId"],
                "ticketNo": ticket["ticketNo"],
                "orderId": "",
                "assigneeId": best["userId"] if best else None,
                "scoreDetail": best_detail,
                "mode": "dispatch",
                "createdAt": ts(),
            }
            await self.repo.create_dispatch(dispatch)

            # 3. 派单(有候选时分配最优客服)
            if best:
                await self.ticket_service.assign_ticket(
                    ticket["ticketNo"], best["userId"],
                    best.get("roleName", ""))
            return {"ticketNo": ticket["ticketNo"],
                    "assigneeId": best["userId"] if best else None,
                    "dispatchId": dispatch_id,
                    "scoreDetail": best_detail,
                    "fallback": best is None}

    async def _score_candidate(self, contract: dict) -> dict:
        """候选客服打分(信用/技能/负载/满意度四维)"""
        user_id = contract["userId"]
        # 信用(竹信分/1000)
        score = await self.credit_service.get_score(user_id)
        credit_score = score.get("bambooScore", 0) / 1000.0
        # 技能(P0 无技能库, 取基线)
        skill_score = SKILL_BASE
        # 负载(处理中工单数/容量)
        processing = await self.ticket_service.repo.list_tickets(
            status=TICKET_STATUS_PROCESSING, limit=100000)
        active = sum(1 for t in processing
                     if t.get("handlerId") == user_id)
        load_score = max(0.0, 1.0 - active / LOAD_CAPACITY)
        # 近30天平均满意度
        since = (_utcnow() - timedelta(days=30)).isoformat()
        resolved = await self.ticket_service.repo.list_tickets(
            status=TICKET_STATUS_RESOLVED, limit=100000)
        recent = [t for t in resolved
                  if t.get("handlerId") == user_id
                  and t.get("satisfaction")
                  and t.get("resolvedAt", "") >= since]
        if recent:
            sat_score = sum(t["satisfaction"] for t in recent) / len(recent) / 5.0
        else:
            sat_score = DEFAULT_SATISFACTION
        total = (credit_score * DISPATCH_WEIGHTS["credit"]
                 + skill_score * DISPATCH_WEIGHTS["skill"]
                 + load_score * DISPATCH_WEIGHTS["load"]
                 + sat_score * DISPATCH_WEIGHTS["satisfaction"])
        return {"userId": user_id, "creditScore": round(credit_score, 4),
                "skillScore": skill_score, "loadScore": round(load_score, 4),
                "satisfactionScore": round(sat_score, 4),
                "activeTickets": active,
                "total": round(total, 4)}

    # ============================================================
    # 5. 服务分润结算引擎(D-8)
    # ============================================================

    async def settle_service_profit(self, ticket_no: str,
                                    order_amount: float = None) -> dict:
        """工单确认满意度后即时结算服务分润(幂等)

        公式(设计文档 5.3):
            分润 = min(订单实售价 × 1% × 满意度系数 × 信用系数
                       × 时效系数 × 试用期系数, ¥50)
            月度累计封顶 ¥3000(超出滚入季度考核奖励池)

        规则:
            - 工单须已确认(resolved)且带满意度
            - ≤3星: 0分润 + 信用-5
            - 超时: 分润减半 + 信用-10; 被升级: 分润取消 + 信用-15
            - 结算入钱包奖励余额(deposit_reward, 仅限购物)
            - 钱包未开通 → 总账 pending, 可重试入账

        Raises:
            KeyError: 工单不存在
            ValueError: 状态非法/已结算/缺少订单金额
        """
        async with get_lock(f"role:settle:{ticket_no}"):
            ticket = await self.ticket_service.repo.get_ticket(ticket_no)
            if ticket is None:
                raise KeyError(f"工单不存在(ticketNo={ticket_no})")
            if ticket["status"] != TICKET_STATUS_RESOLVED:
                raise ValueError(
                    f"工单未确认解决(当前{ticket['status']})")
            satisfaction = ticket.get("satisfaction")
            if not satisfaction:
                raise ValueError("工单缺少满意度评分")
            handler_id = ticket.get("handlerId")
            if not handler_id:
                raise ValueError("工单未分配客服, 无法结算服务分润")

            # 防套利: 会员与客服同人自导(文档5.3 恶意刷满意度风控)
            if handler_id == ticket.get("userId"):
                raise ValueError(
                    f"客服与下单会员同人(userId={handler_id}), 疑似自导服务, 拒绝结算")

            # 幂等
            ledger_no = self.repo.generate_ledger_no(ticket_no)
            existing = await self.repo.get_ledger(ledger_no)
            if existing is not None:
                raise ValueError(
                    f"该工单服务分润已结算(ledgerNo={ledger_no}, "
                    f"status={existing['status']})")

            # 订单金额(实际销售价格): 显式传入 > 订单模块查询
            base = order_amount
            if base is None and ticket.get("orderId"):
                from services.order_service import OrderService
                try:
                    order_result = await OrderService().get_by_id(
                        ticket["orderId"])
                    base = order_result["order"]["priceDetail"]["actualAmount"]
                except (KeyError, Exception):
                    base = None
            if base is None or base <= 0:
                raise ValueError(
                    "缺少订单金额(工单未关联订单或金额非法, "
                    "请通过 orderAmount 显式提供)")

            # 满意度系数(断崖式)
            sat_coeff = SATISFACTION_COEFF.get(satisfaction, 0.0)
            # 信用系数(按当前分数区间即时计算: 分数即货币;
            # 注: 账户 creditLevel 是持续天数评估结果, 用于先享后付等长期权益,
            #     服务分润系数按竹信分档位即时映射, 新升高分用户即时受益)
            credit_account = await self.credit_service.get_score(handler_id)
            bamboo_score = credit_account.get("bambooScore", 0)
            credit_level = level_from_score(bamboo_score)
            credit_coeff = CREDIT_LEVEL_COEFF.get(credit_level, 0.0)
            # 时效系数
            if ticket.get("escalated"):
                timeliness = TIMELINESS_ESCALATED
            else:
                timeliness = self._calc_timeliness(ticket)
            # 试用期系数
            contract = await self.repo.get_active_contract(
                handler_id, ROLE_CUSTOMER_SERVICE)
            probation = bool(
                contract and contract.get("status") == CONTRACT_STATUS_PROBATION)
            probation_coeff = PROBATION_RATE if probation else 1.0

            raw_amount = (base * SERVICE_PROFIT_RATE * sat_coeff
                          * credit_coeff * timeliness * probation_coeff)
            # 单笔封顶
            amount = round(min(raw_amount, SINGLE_CAP), 2)

            # 月度封顶(超出部分滚入季度考核奖励池, 记 deferredAmount)
            now = ts()
            monthly_used = await self.repo.sum_monthly_settled(
                handler_id, ROLE_CUSTOMER_SERVICE, now)
            monthly_room = round(max(0.0, MONTHLY_CAP - monthly_used), 2)
            capped = amount > monthly_room
            deferred_amount = 0.0
            if capped:
                deferred_amount = round(amount - monthly_room, 2)
                amount = monthly_room

            # 负账抵扣(退款追回): 结算额先抵扣负账余额, 抵扣部分不发放
            clawback_balance = await self.repo.get_clawback(
                handler_id, ROLE_CUSTOMER_SERVICE)
            clawback_deducted = round(
                min(amount, clawback_balance), 2) if amount > 0 else 0.0
            if clawback_deducted > 0:
                await self.repo.adjust_clawback(
                    handler_id, ROLE_CUSTOMER_SERVICE, -clawback_deducted)
            payable = round(amount - clawback_deducted, 2)

            # 建总账(即时结算 → 直接尝试入钱包)
            ledger = {
                "ledgerNo": ledger_no,
                "ticketNo": ticket_no,
                "orderId": ticket.get("orderId", ""),
                "roleCode": ROLE_CUSTOMER_SERVICE,
                "userId": handler_id,
                "basis": PROFIT_BASIS_SALE_PRICE,   # D-7: 新口径轨道
                "base": base,
                "rate": SERVICE_PROFIT_RATE,
                "coefficients": {
                    "satisfaction": satisfaction, "satCoeff": sat_coeff,
                    "creditLevel": credit_level, "creditCoeff": credit_coeff,
                    "timeliness": timeliness, "probation": probation,
                },
                "rawAmount": round(raw_amount, 2),
                "amount": amount,
                "payable": payable,
                "clawbackDeducted": clawback_deducted,
                "clawbackAfter": round(clawback_balance - clawback_deducted, 2),
                "deferredAmount": deferred_amount,
                "capped": capped,
                "status": LEDGER_STATUS_PENDING,
                "sourceModule": "role",
                "walletTxNo": "",
                "walletError": "",
                "createdAt": now,
                "settledAt": "",
            }
            await self.repo.create_ledger(ledger)

            # 入钱包(奖励余额, 仅限购物不可提现); 零分润直接闭环为 settled
            wallet_result = None
            if payable > 0:
                try:
                    wallet_result = await self.wallet_service.deposit_reward(
                        handler_id, payable,
                        description=f"服务分润({ticket_no})")
                    await self.repo.update_ledger(ledger_no, {
                        "status": LEDGER_STATUS_SETTLED,
                        "walletTxNo": wallet_result.get("txNo", ""),
                        "settledAt": ts()})
                    ledger["status"] = LEDGER_STATUS_SETTLED
                    ledger["walletTxNo"] = wallet_result.get("txNo", "")
                except Exception as e:
                    # 钱包未开通等 → 总账 pending, 可重试入账
                    await self.repo.update_ledger(ledger_no,
                                                  {"walletError": str(e)})
                    ledger["walletError"] = str(e)
            else:
                await self.repo.update_ledger(ledger_no, {
                    "status": LEDGER_STATUS_SETTLED, "settledAt": ts()})
                ledger["status"] = LEDGER_STATUS_SETTLED

            # 信用事件(对等约束: 责的对价)
            events = []
            if satisfaction >= 4:
                events.append(await self.publish_credit_event(
                    handler_id, ROLE_CUSTOMER_SERVICE,
                    BEHAVIOR_CS_SATISFACTION_GOOD, "role", ticket_no))
            else:
                events.append(await self.publish_credit_event(
                    handler_id, ROLE_CUSTOMER_SERVICE,
                    BEHAVIOR_CS_SATISFACTION_BAD, "role", ticket_no))
            if timeliness == TIMELINESS_OVERDUE:
                events.append(await self.publish_credit_event(
                    handler_id, ROLE_CUSTOMER_SERVICE,
                    BEHAVIOR_CS_SLA_OVERDUE, "role", ticket_no))
            if ticket.get("escalated"):
                events.append(await self.publish_credit_event(
                    handler_id, ROLE_CUSTOMER_SERVICE,
                    BEHAVIOR_CS_ESCALATED, "role", ticket_no))

            ledger["creditEvents"] = [e["id"] for e in events]
            return ledger

    def _calc_timeliness(self, ticket: dict) -> float:
        """时效系数: 首次响应是否在 SLA 截止前"""
        try:
            created = datetime.fromisoformat(ticket["createdAt"])
            sla_hours = SLA_HOURS.get(ticket.get("priority", "low"), 24)
            deadline = created + timedelta(hours=sla_hours)
            first = ticket.get("firstResponseAt") or ticket.get("assignedAt")
            if not first:
                return TIMELINESS_OVERDUE
            return (TIMELINESS_SLA_OK
                    if datetime.fromisoformat(first) <= deadline
                    else TIMELINESS_OVERDUE)
        except (ValueError, KeyError):
            return TIMELINESS_OVERDUE

    async def retry_ledger_settlement(self, ledger_no: str) -> dict:
        """重试 pending 总账入钱包(钱包开通后)"""
        ledger = await self.repo.get_ledger(ledger_no)
        if ledger is None:
            raise KeyError(f"分润流水不存在(ledgerNo={ledger_no})")
        if ledger["status"] != LEDGER_STATUS_PENDING:
            raise ValueError(
                f"流水状态非法(当前{ledger['status']}, 须为{LEDGER_STATUS_PENDING})")
        if ledger.get("amount", 0) <= 0:
            raise ValueError("分润金额为零, 无需入账")
        result = await self.wallet_service.deposit_reward(
            ledger["userId"], ledger["amount"],
            description=f"服务分润({ledger['ticketNo']})重试入账")
        await self.repo.update_ledger(ledger_no, {
            "status": LEDGER_STATUS_SETTLED,
            "walletTxNo": result.get("txNo", ""), "settledAt": ts()})
        ledger.update({"status": LEDGER_STATUS_SETTLED,
                       "walletTxNo": result.get("txNo", "")})
        return ledger

    # ============================================================
    # 5.1 退款追回(D-8 细化: 文档5.3 追回规则)
    # ============================================================

    async def reverse_service_profit(self, ticket_no: str,
                                     reason: str = "订单退款",
                                     operator: str = "admin") -> dict:
        """订单退款后追回该工单已结算的服务分润

        规则(文档5.3):
            - 仅 settled/pending 流水可追回(reversed 幂等)
            - 追回额 = 已结算金额(payable)
            - 追回记入负账(role_clawbacks), 从该客服下月分润中等额抵扣
            - 负账累计超 ¥500 → 冻结接单(派单候选剔除)

        Raises:
            KeyError: 流水不存在
            ValueError: 状态非法
        """
        async with get_lock(f"role:settle:{ticket_no}"):
            ledger_no = self.repo.generate_ledger_no(ticket_no)
            ledger = await self.repo.get_ledger(ledger_no)
            if ledger is None:
                raise KeyError(
                    f"服务分润流水不存在(ticketNo={ticket_no})")
            if ledger["status"] == LEDGER_STATUS_REVERSED:
                raise ValueError("该工单服务分润已追回")
            if ledger["status"] != LEDGER_STATUS_SETTLED:
                raise ValueError(
                    f"流水状态非法(当前{ledger['status']}, "
                    f"须为{LEDGER_STATUS_SETTLED})")

            clawback_amount = ledger.get("payable", ledger.get("amount", 0))
            balance_after = await self.repo.adjust_clawback(
                ledger["userId"], ledger["roleCode"], clawback_amount)

            updates = {
                "status": LEDGER_STATUS_REVERSED,
                "reversedAt": ts(),
                "reverseReason": reason,
                "reverseOperator": operator,
                "clawbackAfter": balance_after,
            }
            await self.repo.update_ledger(ledger_no, updates)
            ledger.update(updates)
            return ledger

    async def get_clawback(self, user_id: int,
                           role_code: str = ROLE_CUSTOMER_SERVICE) -> dict:
        """查询负账余额与接单冻结状态"""
        balance = await self.repo.get_clawback(user_id, role_code)
        return {
            "userId": user_id,
            "roleCode": role_code,
            "clawbackBalance": balance,
            "freezeThreshold": CLAWBACK_FREEZE_THRESHOLD,
            "dispatchFrozen": balance > CLAWBACK_FREEZE_THRESHOLD,
        }

    # ============================================================
    # 5.2 统一分润总账记账(P1: D-7 口径对齐)
    # ============================================================

    async def record_external_settlement(self, ledger_no: str,
                                         source_module: str, role_code: str,
                                         user_id: int, basis: str,
                                         base: float, rate: float,
                                         amount: float, ref_no: str = "",
                                         note: str = "") -> dict:
        """外部模块结算回写统一总账(venue/agent/traffic 等)

        规则(设计文档§4.4):
            - 计算层仍由各业务模块负责, 本方法只做记账与对账统一
            - 幂等: 同 ledgerNo 已存在 → 直接返回既有流水, 不重复记账
            - 资金流由来源模块自身处理(venue不落钱包/agent写代理钱包/
              traffic累计待提现), 故本流水 status 直接为 settled

        Returns:
            {ledger, created(bool)}
        """
        existing = await self.repo.get_ledger(ledger_no)
        if existing is not None:
            return {"ledger": existing, "created": False}
        ledger = {
            "ledgerNo": ledger_no,
            "ticketNo": "",
            "orderId": "",
            "refNo": ref_no,
            "roleCode": role_code,
            "userId": user_id,
            "basis": basis,
            "base": round(base, 2),
            "rate": rate,
            "coefficients": {},
            "rawAmount": round(amount, 2),
            "amount": round(amount, 2),
            "payable": round(amount, 2),
            "clawbackDeducted": 0.0,
            "clawbackAfter": 0.0,
            "deferredAmount": 0.0,
            "capped": False,
            "status": LEDGER_STATUS_SETTLED,
            "sourceModule": source_module,
            "walletTxNo": "",
            "walletError": "",
            "note": note,
            "createdAt": ts(),
            "settledAt": ts(),
        }
        await self.repo.create_ledger(ledger)
        return {"ledger": ledger, "created": True}

    # ============================================================
    # 5.3 生产工人分润(P1: 设计文档5.4, 生命码联动)
    # ============================================================

    async def settle_worker_profit(self, batch_no: str,
                                   order_amount: float,
                                   quality_grade: str = "pass") -> dict:
        """批次订单生产工人分润结算(幂等: 每批次仅一次)

        公式(设计文档5.4):
            工人环节分润 = 订单实际销售价格 × 环节分润率(合计≤15%子池)
                         × 质量系数(pass 1.0 / premium 1.2 / accident 0)

        规则:
            - 批次须已放行(released, 7工段完成+瓶码绑定)
            - 工人取每工段最后一条 pass 打卡的 memberId(生命码生产链留痕)
            - accident: 全员零分润 + 信用-15; premium: ×1.2 + 信用+5
            - 入账: 钱包奖励余额(未开通→pending可重试) + 总账(sale_price轨道)

        Raises:
            KeyError: 批次不存在
            ValueError: 参数非法/批次未放行/无打卡记录/已结算
        """
        async with get_lock(f"role:worker-settle:{batch_no}"):
            shares, batch, quality_coeff = await self._calc_worker_shares(
                batch_no, order_amount, quality_grade)
            # 幂等: 批次维度一次结算
            prefix = f"{WORKER_LEDGER_PREFIX}{batch_no}-"
            if await self.repo.ledger_exists_prefix(prefix):
                raise ValueError(f"该批次工人分润已结算(batchNo={batch_no})")

            now = ts()
            results = []
            for share in shares:
                ledger_no = f"{prefix}{share['stageCode']}"
                amount = share["amount"]
                ledger = {
                    "ledgerNo": ledger_no,
                    "ticketNo": "",
                    "orderId": "",
                    "refNo": batch_no,
                    "roleCode": ROLE_PRODUCTION_WORKER,
                    "userId": share["workerId"],
                    "basis": PROFIT_BASIS_SALE_PRICE,
                    "base": order_amount,
                    "rate": share["rate"],
                    "coefficients": {
                        "stageCode": share["stageCode"],
                        "qualityGrade": quality_grade,
                        "qualityCoeff": quality_coeff,
                    },
                    "rawAmount": amount,
                    "amount": amount,
                    "payable": amount,
                    "clawbackDeducted": 0.0,
                    "clawbackAfter": 0.0,
                    "deferredAmount": 0.0,
                    "capped": False,
                    "status": LEDGER_STATUS_PENDING,
                    "sourceModule": "role",
                    "walletTxNo": "",
                    "walletError": "",
                    "createdAt": now,
                    "settledAt": "",
                }
                await self.repo.create_ledger(ledger)
                # 入钱包(奖励余额); 零分润直接闭环
                if amount > 0:
                    try:
                        wallet_result = await (
                            self.wallet_service.deposit_reward(
                                share["workerId"], amount,
                                description=f"生产分润({batch_no}/"
                                            f"{share['stageName']})"))
                        await self.repo.update_ledger(ledger_no, {
                            "status": LEDGER_STATUS_SETTLED,
                            "walletTxNo": wallet_result.get("txNo", ""),
                            "settledAt": ts()})
                        ledger["status"] = LEDGER_STATUS_SETTLED
                        ledger["walletTxNo"] = wallet_result.get("txNo", "")
                    except Exception as e:
                        await self.repo.update_ledger(ledger_no,
                                                      {"walletError": str(e)})
                        ledger["walletError"] = str(e)
                else:
                    await self.repo.update_ledger(ledger_no, {
                        "status": LEDGER_STATUS_SETTLED, "settledAt": ts()})
                    ledger["status"] = LEDGER_STATUS_SETTLED
                results.append(ledger)

            # 信用事件(优质加分/质量事故扣分, 按工人去重: 责的对价)
            behavior = None
            if quality_grade == "premium":
                behavior = BEHAVIOR_WORKER_QUALITY_PREMIUM
            elif quality_grade == "accident":
                behavior = BEHAVIOR_WORKER_QUALITY_ACCIDENT
            if behavior:
                for worker_id in {s["workerId"] for s in shares}:
                    await self.publish_credit_event(
                        worker_id, ROLE_PRODUCTION_WORKER,
                        behavior, "role", batch_no)

            return {
                "batchNo": batch_no,
                "orderId": batch.get("batchNo", ""),
                "orderAmount": order_amount,
                "qualityGrade": quality_grade,
                "qualityCoeff": quality_coeff,
                "workersCount": len({s["workerId"] for s in shares}),
                "stagesCount": len(shares),
                "totalAmount": round(sum(s["amount"] for s in shares), 2),
                "shares": shares,
                "ledgers": results,
            }

    async def preview_worker_profit(self, batch_no: str,
                                     order_amount: float,
                                     quality_grade: str = "pass") -> dict:
        """工人分润预演(只读, 不写账不入钱包)

        Raises:
            KeyError: 批次不存在
            ValueError: 参数非法/批次未放行/无打卡记录
        """
        shares, batch, quality_coeff = await self._calc_worker_shares(
            batch_no, order_amount, quality_grade)
        return {
            "batchNo": batch_no,
            "orderAmount": order_amount,
            "qualityGrade": quality_grade,
            "qualityCoeff": quality_coeff,
            "batchStatus": batch.get("status"),
            "workersCount": len({s["workerId"] for s in shares}),
            "stagesCount": len(shares),
            "totalAmount": round(sum(s["amount"] for s in shares), 2),
            "shares": shares,
        }

    async def _calc_worker_shares(self, batch_no: str, order_amount: float,
                                  quality_grade: str) -> tuple:
        """工人份额计算(内部): (shares, batch, quality_coeff)

        Raises:
            KeyError: 批次不存在
            ValueError: 参数非法/批次未放行/无打卡记录
        """
        if order_amount is None or order_amount <= 0:
            raise ValueError("订单实际销售价格必须大于 0")
        if quality_grade not in WORKER_QUALITY_COEFF:
            raise ValueError(
                f"质量等级无效(须为{'/'.join(WORKER_QUALITY_COEFF)})")
        quality_coeff = WORKER_QUALITY_COEFF[quality_grade]

        batch = await self.trace_prod_repo.get_batch(batch_no)
        if batch is None:
            raise KeyError(f"生产批次不存在(batchNo={batch_no})")
        if batch.get("status") != "released":
            raise ValueError(
                f"批次未放行(当前{batch.get('status')}, 须 released; "
                f"7工段完成且瓶码绑定后方可分润)")

        punches = await self.trace_prod_repo.list_punches(batch_no=batch_no)
        if not punches:
            raise ValueError(f"批次无生产打卡记录(batchNo={batch_no})")

        # 每工段取最后一条 pass 打卡的责任人
        last_pass = {}
        for p in sorted(punches, key=lambda x: x.get("punchedAt", "")):
            if p.get("result") == RESULT_PASS:
                last_pass[p.get("stageCode")] = p

        shares = []
        for stage_code, rate in STAGE_PROFIT_RATES.items():
            punch = last_pass.get(stage_code)
            if punch is None:
                continue
            stage = await self.trace_prod_repo.get_stage_by_code(stage_code)
            shares.append({
                "stageCode": stage_code,
                "stageName": stage.get("name", stage_code) if stage else stage_code,
                "workerId": punch.get("memberId"),
                "rate": rate,
                "amount": round(order_amount * rate * quality_coeff, 2),
            })
        if not shares:
            raise ValueError("批次打卡记录均未命中有效工段, 无法计算份额")
        return shares, batch, quality_coeff

    # ============================================================
    # 6. 信用行为总线
    # ============================================================

    async def publish_credit_event(self, user_id: int, role_code: str,
                                   behavior: str, source_module: str,
                                   ref_id: str = "") -> dict:
        """发布信用行为事件(统一入账, 分发竹信分引擎)

        规则:
            - behavior 须为已定义行为码
            - 事件落 credit_events(总线流水)
            - 同步调用竹信分引擎 adjust_score(delta, operator=role_module)
        """
        delta = BEHAVIOR_DELTAS.get(behavior)
        if delta is None:
            raise ValueError(f"未定义的信用行为码({behavior})")
        event_id = await self.repo.next_id("event")
        event = {
            "id": event_id, "userId": user_id, "roleCode": role_code,
            "behaviorCode": behavior, "delta": delta,
            "sourceModule": source_module, "refId": ref_id, "ts": ts(),
        }
        await self.repo.create_event(event)
        # 分发至竹信分引擎(总分维度; 岗位子分由 perm 权责信用分承接, P1 桥接)
        await self.credit_service.adjust_score(
            user_id, delta, reason=f"[{source_module}] {behavior}",
            operator="role_module")
        return event

    async def list_my_credit_events(self, user_id: int) -> list[dict]:
        """我的信用行为记录"""
        return await self.repo.list_events(user_id=user_id)

    # ============================================================
    # 7. 收益查询 / 管理端
    # ============================================================

    async def list_my_earnings(self, user_id: int) -> list[dict]:
        """我的分润流水(含系数明细)"""
        return await self.repo.list_ledgers(user_id=user_id)

    async def admin_list_ledger(self, user_id: int = None,
                                role_code: str = None, basis: str = None,
                                status: str = None) -> list[dict]:
        """分润总账查询(按用户/角色/口径/状态筛选)"""
        return await self.repo.list_ledgers(
            user_id=user_id, role_code=role_code, basis=basis, status=status)

    async def get_risk_summary(self) -> dict:
        """AI风控汇总(P0: 基础统计; 异常检测 P2)"""
        ledgers = await self.repo.list_ledgers(limit=100000)
        events = await self.repo.list_events(limit=100000)
        contracts = await self.repo.list_contracts(limit=100000)
        claims = await self.repo.list_claims(limit=100000)

        status_count = {}
        for l in ledgers:
            status_count[l.get("status")] = \
                status_count.get(l.get("status"), 0) + 1
        contract_count = {}
        for c in contracts:
            contract_count[c.get("status")] = \
                contract_count.get(c.get("status"), 0) + 1

        settled = [l for l in ledgers
                   if l.get("status") == LEDGER_STATUS_SETTLED]
        deferred_pool = round(sum(
            l.get("deferredAmount", 0) for l in ledgers), 2)
        clawback_total = 0.0
        for c in contracts:
            if c.get("roleCode") == ROLE_CUSTOMER_SERVICE:
                clawback_total += await self.repo.get_clawback(
                    c["userId"], ROLE_CUSTOMER_SERVICE)
        return {
            "ledgerTotal": len(ledgers),
            "ledgerStatusCount": status_count,
            "settledAmountTotal": round(
                sum(l.get("amount", 0) for l in settled), 2),
            "reversedCount": status_count.get("reversed", 0),
            "cappedCount": sum(1 for l in ledgers if l.get("capped")),
            "deferredPoolTotal": deferred_pool,
            "clawbackTotal": round(clawback_total, 2),
            "contractCount": contract_count,
            "claimTotal": len(claims),
            "claimPending": sum(
                1 for c in claims if c.get("status") == CLAIM_STATUS_PENDING),
            "negativeCreditEvents": sum(
                1 for e in events if e.get("delta", 0) < 0),
            "basisSplit": self._basis_split(ledgers),
            "caps": {"single": SINGLE_CAP, "monthly": MONTHLY_CAP,
                     "clawbackFreeze": CLAWBACK_FREEZE_THRESHOLD},
        }

    @staticmethod
    def _basis_split(ledgers: list[dict]) -> dict:
        """按口径动态统计流水分布(D-7 三轨道: sale_price/diff_profit/purchase_amount)"""
        split = {}
        for l in ledgers:
            basis = l.get("basis", "unknown")
            split[basis] = split.get(basis, 0) + 1
        return split
