"""权限AI智能管理模块业务逻辑层(P0 核心闭环)

核心机制:
    - 权限树: 生产流程 7 环节 × 4 操作级 = 28 权限点(种子内置)
    - 超管直授: role=admin 会员即超级管理员, 可直授主要权限(SoD 硬拦截)
    - 申请审批流: 按敏感级 1-3 级逐级审批
        · normal  一般: [环节主管(该环节 manage 持有者)] 1 级
        · important 重要: [直属主管(approve 持有者), 环节主管(manage 持有者)] 2 级
        · core   核心: [直属主管, 环节主管, 超管] 3 级
        (持有者集合为空时兜底超管; 申请人本人命中当前级审批人时该级自动通过)
    - 权责共存: 授权生效前置条件为签署电子责任书(未签署阻断权限校验)
    - 限时回收: 权限带有效期(normal/important 默认 30 天, core 默认 7 天),
      访问时惰性过期 + 手动清扫接口
    - SoD 职责分离: 互斥权限对(如 收付款操作↔收款审核)直授/申请双向预检
    - P2 超时升级: 当前级审批人 48h 未处理 → 该级追加上一级候选审批人
      (末级追加超管), 候选人任一可批, 链上标注 escalated
    - P2 代理审批: 审批人可设代理人, 代理人可代批(标注 delegatedFrom)

异常约定:
    - KeyError   → 404(权限点/授权/申请单不存在)
    - ValueError → 409(参数非法/SoD 冲突/状态非法/越级审批)
    - PermissionError → 403(无权限/未签责任书/非审批人)
"""

import logging
from datetime import datetime, timedelta, UTC

from core.locks import get_lock
from repositories.perm_repository import PermRepository, STAGES, LEVELS
from repositories.member_repository import MemberRepository

logger = logging.getLogger(__name__)

ROLE_ADMIN = "admin"

# 申请期限上限(天): 一般/重要 90, 核心 30
_MAX_DAYS = {"normal": 90, "important": 90, "core": 30}

# P2: 审批超时升级时限(小时)
APPROVAL_TIMEOUT_HOURS = 48


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class PermService:
    """权限AI智能管理模块业务逻辑层"""

    def __init__(self, perm_repo: PermRepository = None,
                 member_repo: MemberRepository = None):
        self.repo = perm_repo or PermRepository()
        self.member_repo = member_repo or MemberRepository()

    # ============================================================
    # 内部辅助
    # ============================================================

    async def _log(self, member_id: int, action: str, node_code: str = "",
                   risk_level: str = "low", detail: dict = None,
                   handled: str = "none") -> dict:
        """落 AI 监控审计日志(全部权限行为留痕)"""
        log_id = await self.repo.next_id("log")
        log = {
            "logId": log_id,
            "memberId": member_id,
            "action": action,
            "nodeCode": node_code,
            "riskLevel": risk_level,
            "riskScore": {"low": 10, "medium": 50,
                          "high": 75, "extreme": 95}[risk_level],
            "detail": detail or {},
            "handled": handled,
            "createdAt": _now().isoformat(),
        }
        await self.repo.save_log(log)
        return log

    async def _require_node(self, node_code: str) -> dict:
        node = await self.repo.get_node_by_code(node_code)
        if not node:
            raise KeyError(f"权限点不存在: {node_code}")
        return node

    async def _get_super_admin_ids(self) -> list[int]:
        """全部超级管理员(role=admin 会员)"""
        members = await self.member_repo.list_all()
        return [m["id"] for m in members if m.get("role") == ROLE_ADMIN]

    async def _is_super_admin(self, member_id: int) -> bool:
        member = await self.member_repo.get_by_id(member_id)
        return bool(member and member.get("role") == ROLE_ADMIN)

    async def _stage_manager_ids(self, stage: str) -> list[int]:
        """环节主管 = 持有该环节 manage 级权限(生效且已签责任书)的会员"""
        grants = await self.repo.list_grants(
            node_code=f"{stage}.manage", status="active")
        ids = []
        for g in grants:
            if g.get("dutySigned") and not self._is_expired(g):
                ids.append(g.get("memberId"))
        # 去重保序
        return list(dict.fromkeys(ids))

    async def _approver_ids(self, stage: str, level: str) -> list[int]:
        """直属主管 = 持有该环节 approve 级权限(生效且已签责任书)的会员"""
        grants = await self.repo.list_grants(
            node_code=f"{stage}.{level}", status="active")
        ids = []
        for g in grants:
            if g.get("dutySigned") and not self._is_expired(g):
                ids.append(g.get("memberId"))
        return list(dict.fromkeys(ids))

    @staticmethod
    def _is_expired(grant: dict) -> bool:
        exp = _parse_iso(grant.get("expiresAt", ""))
        return bool(exp and exp <= _now())

    async def _sod_check(self, member_id: int, node: dict,
                         exclude_grant_id: int = None) -> None:
        """SoD 职责分离预检: 申请人已持有互斥权限则拒绝

        Raises:
            ValueError: 存在互斥权限冲突
        """
        conflicts = node.get("conflictWith") or []
        if not conflicts:
            return
        grants = await self.repo.list_grants(member_id=member_id,
                                             status="active")
        for g in grants:
            if exclude_grant_id and g.get("grantId") == exclude_grant_id:
                continue
            if g.get("nodeCode") in conflicts:
                conflict_node = await self.repo.get_node_by_code(
                    g["nodeCode"])
                name = conflict_node["name"] if conflict_node else g["nodeCode"]
                raise ValueError(
                    f"SoD 职责分离拦截: 已持有互斥权限「{name}」, "
                    f"不可同时持有「{node['name']}」")

    async def _build_chain(self, node: dict,
                           applicant_id: int) -> list[dict]:
        """按敏感级构建逐级审批链

        每级: {role(角色名), approverIds(候选审批人), approvedBy, opinion,
               decidedAt, auto(是否自动通过)}
        """
        stage = node["stage"]
        supers = await self._get_super_admin_ids()
        if node["sensitivity"] == "normal":
            # 1 级: 环节主管(兜底超管)
            steps_spec = [("环节主管", await self._stage_manager_ids(stage))]
        elif node["sensitivity"] == "important":
            # 2 级: 直属主管 → 环节主管
            steps_spec = [
                ("直属主管",
                 await self._approver_ids(stage, "approve")
                 or await self._stage_manager_ids(stage)),
                ("环节主管", await self._stage_manager_ids(stage)),
            ]
        else:
            # 3 级: 直属主管 → 环节主管 → 超管
            steps_spec = [
                ("直属主管",
                 await self._approver_ids(stage, "approve")
                 or await self._stage_manager_ids(stage)),
                ("环节主管", await self._stage_manager_ids(stage)),
                ("超级管理员", supers),
            ]
        chain = []
        for role_name, approver_ids in steps_spec:
            if not approver_ids:
                approver_ids = list(supers)  # 兜底超管
            auto = applicant_id in approver_ids and len(approver_ids) == 1
            chain.append({
                "role": role_name,
                "approverIds": approver_ids,
                "approvedBy": None,
                "opinion": "",
                "decidedAt": "",
                "auto": auto,
                "autoNote": "申请人本人即该级审批人, 自动通过" if auto else "",
            })
        # 自动通过的级直接落结论
        for step in chain:
            if step["auto"]:
                step["approvedBy"] = applicant_id
                step["decidedAt"] = _now().isoformat()
        return chain

    def _current_step(self, req: dict) -> int:
        """当前待审批级(0 基); 全部通过返回 len(chain)"""
        chain = req.get("approvals") or []
        for idx, step in enumerate(chain):
            if not step.get("approvedBy"):
                return idx
        return len(chain)

    # ============================================================
    # 权限树 / 角色模板
    # ============================================================

    async def list_nodes(self) -> list[dict]:
        nodes = await self.repo._list("perm_nodes", limit=500)
        return sorted(nodes, key=lambda x: x.get("nodeId", 0))

    async def list_roles(self) -> list[dict]:
        return await self.repo.list_roles()

    async def create_role(self, admin_id: int, name: str, stage: str,
                          node_codes: list[str]) -> dict:
        """创建角色模板(仅超管)

        Raises:
            ValueError: 名称/环节/权限码非法
        """
        if not name or len(name.strip()) > 30:
            raise ValueError("角色名称非法(1-30字)")
        if stage not in STAGES:
            raise ValueError(f"生产环节非法(须为 {'/'.join(STAGES)})")
        if not node_codes:
            raise ValueError("权限码集合不能为空")
        for code in node_codes:
            node = await self.repo.get_node_by_code(code)
            if not node:
                raise ValueError(f"权限码不存在: {code}")
            if node["stage"] != stage:
                raise ValueError(f"权限码 {code} 不属于环节 {STAGES[stage]}")
        role_id = await self.repo.next_id("role")
        role = {
            "roleId": role_id,
            "name": name.strip(),
            "stage": stage,
            "stageName": STAGES[stage],
            "nodeCodes": node_codes,
            "createdBy": admin_id,
            "createdAt": _now().isoformat(),
        }
        await self.repo.save_role(role)
        await self._log(admin_id, "role_create", "",
                        detail={"roleId": role_id, "name": name})
        return role

    # ============================================================
    # 超管直授
    # ============================================================

    async def assign_grant(self, admin_id: int, member_id: int,
                           node_code: str,
                           duration_days: int = None) -> dict:
        """超管直授主要权限(免除申请流, 仍需签责任书+限时)

        Raises:
            PermissionError: 操作者非超管
            KeyError: 会员/权限点不存在
            ValueError: SoD 冲突/已有生效授权/期限非法
        """
        if not await self._is_super_admin(admin_id):
            raise PermissionError("仅超级管理员可分配权限")
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员不存在(id={member_id})")
        node = await self._require_node(node_code)

        await self._sod_check(member_id, node)

        existing = await self.repo.list_grants(
            member_id=member_id, node_code=node_code, status="active")
        if existing:
            raise ValueError(f"会员已持有生效权限「{node['name']}」, 勿重复授权")

        days = duration_days or node["defaultDays"]
        if not isinstance(days, int) or not (1 <= days <= 90):
            raise ValueError("授权期限非法(1-90天)")

        async with get_lock(f"perm:grant:{member_id}:{node_code}"):
            grant_id = await self.repo.next_id("grant")
            grant = {
                "grantId": grant_id,
                "memberId": member_id,
                "nodeCode": node_code,
                "nodeName": node["name"],
                "source": "assign",
                "status": "active",
                "dutySigned": False,
                "expiresAt": (_now() + timedelta(days=days)).isoformat(),
                "grantedBy": admin_id,
                "createdAt": _now().isoformat(),
            }
            await self.repo.save_grant(grant)
        await self._log(admin_id, "grant_assign", node_code,
                        detail={"grantId": grant_id, "memberId": member_id,
                                "days": days})
        return grant

    async def revoke_grant(self, admin_id: int, grant_id: int) -> dict:
        """吊销授权(仅超管)"""
        if not await self._is_super_admin(admin_id):
            raise PermissionError("仅超级管理员可吊销权限")
        grant = await self.repo.get_grant(grant_id)
        if not grant:
            raise KeyError(f"授权不存在(id={grant_id})")
        if grant["status"] not in ("active", "frozen"):
            raise ValueError(f"授权状态({grant['status']})不可吊销")
        updated = await self.repo.update_grant(grant_id, {
            "status": "revoked",
            "revokedBy": admin_id,
            "revokedAt": _now().isoformat(),
        })
        await self._log(admin_id, "grant_revoke", grant["nodeCode"],
                        detail={"grantId": grant_id,
                                "memberId": grant["memberId"]})
        return updated

    # ============================================================
    # 我的权限 / 责任书
    # ============================================================

    async def list_my_grants(self, member_id: int) -> list[dict]:
        """我的权限列表(含到期倒计时, 惰性过期)"""
        grants = await self.repo.list_grants(member_id=member_id)
        result = []
        for g in grants:
            if g["status"] == "active" and self._is_expired(g):
                g = await self.repo.update_grant(
                    g["grantId"], {"status": "expired"})
                await self._log(member_id, "grant_expire", g["nodeCode"],
                                detail={"grantId": g["grantId"]})
            exp = _parse_iso(g.get("expiresAt", ""))
            days_left = max(0, (exp - _now()).days) if exp else 0
            node = await self.repo.get_node_by_code(g["nodeCode"])
            item = dict(g)
            item["daysLeft"] = days_left
            item["duties"] = (node or {}).get("duties", [])
            item["sensitivity"] = (node or {}).get("sensitivity", "")
            result.append(item)
        # 生效在前, 其余按 grantId 倒序
        result.sort(key=lambda x: (x["status"] != "active",
                                   -x.get("grantId", 0)))
        return result

    async def sign_duty(self, member_id: int, grant_id: int) -> dict:
        """签署责任书(权责共存: 未签署则权限校验阻断)

        Raises:
            KeyError: 授权不存在
            PermissionError: 非被授权人
            ValueError: 状态非法
        """
        grant = await self.repo.get_grant(grant_id)
        if not grant:
            raise KeyError(f"授权不存在(id={grant_id})")
        if grant["memberId"] != member_id:
            raise PermissionError("仅被授权人本人可签署责任书")
        if grant["status"] != "active":
            raise ValueError(f"授权状态({grant['status']})不可签署")
        if grant.get("dutySigned"):
            return grant
        updated = await self.repo.update_grant(grant_id, {
            "dutySigned": True,
            "dutySignedAt": _now().isoformat(),
        })
        await self._log(member_id, "duty_sign", grant["nodeCode"],
                        detail={"grantId": grant_id})
        return updated

    # ============================================================
    # 申请-审批流
    # ============================================================

    async def submit_request(self, applicant_id: int, node_code: str,
                             reason: str,
                             duration_days: int = None) -> dict:
        """提交权限申请(AI 预检: SoD/重复申请/重复持有 → 拒绝)

        Raises:
            KeyError: 权限点不存在
            ValueError: 理由缺失/期限非法/SoD 冲突/重复申请/已持有
        """
        node = await self._require_node(node_code)
        if not reason or len(reason.strip()) < 5:
            raise ValueError("申请理由不能少于 5 个字")
        reason = reason.strip()[:200]

        # AI 预检 1: SoD 冲突
        await self._sod_check(applicant_id, node)
        # AI 预检 2: 已持有生效权限
        existing = await self.repo.list_grants(
            member_id=applicant_id, node_code=node_code, status="active")
        if existing:
            raise ValueError(f"已持有生效权限「{node['name']}」, 无需申请")
        # AI 预检 3: 重复在途申请
        pending = await self.repo.list_requests(
            applicant_id=applicant_id, status="pending")
        for r in pending:
            if r.get("nodeCode") == node_code:
                raise ValueError(f"权限「{node['name']}」已有在途申请"
                                 f"(单号 {r['requestId']}), 请等待审批")

        max_days = _MAX_DAYS[node["sensitivity"]]
        days = duration_days or node["defaultDays"]
        if not isinstance(days, int) or not (1 <= days <= max_days):
            raise ValueError(
                f"申请期限非法(1-{max_days}天, "
                f"敏感级 {node['sensitivityName']})")

        chain = await self._build_chain(node, applicant_id)
        request_id = await self.repo.next_id("request")
        req = {
            "requestId": request_id,
            "applicantId": applicant_id,
            "nodeCode": node_code,
            "nodeName": node["name"],
            "reason": reason,
            "durationDays": days,
            "sensitivity": node["sensitivity"],
            "sensitivityName": node["sensitivityName"],
            "status": "pending",
            "approvals": chain,
            "currentStep": 0,
            "aiPrecheck": {
                "sodConflict": False,
                "duplicate": False,
                "riskLevel": "low",
                "notes": ["AI 预检通过: 无 SoD 冲突, 无重复申请"],
            },
            "createdAt": _now().isoformat(),
            "decidedAt": "",
        }
        await self.repo.save_request(req)
        await self._log(applicant_id, "apply_submit", node_code,
                        detail={"requestId": request_id, "days": days})
        return req

    async def list_requests(self, member_id: int) -> dict:
        """我的申请 + 待我审批(按身份聚合, P2: 含受托代批的单)"""
        mine = await self.repo.list_requests(applicant_id=member_id)
        supers = await self._get_super_admin_ids()
        # P2: 我代理的委托人(生效中) → 其待审批单我也可批
        my_principals = [
            d["delegatorId"] for d in await self.repo.list_delegates(
                delegate_to_id=member_id, status="active")
        ]
        to_approve = []
        for r in await self.repo.list_requests(status="pending"):
            step = self._current_step(r)
            chain = r.get("approvals") or []
            if step >= len(chain):
                continue
            eligible = chain[step].get("approverIds") or []
            if (member_id in eligible or member_id in supers
                    or any(p in eligible for p in my_principals)):
                to_approve.append(r)
        to_approve.sort(key=lambda x: x.get("requestId", 0))
        return {"mine": mine, "toApprove": to_approve}

    async def approve_request(self, operator_id: int, request_id: int,
                              action: str, opinion: str = "") -> dict:
        """审批(同意/驳回): 当前级候选审批人或其代理人或超管可操作

        P2 代理审批: operator 为候选审批人生效委托的代理人时允许代批,
        链上标注 delegatedFrom(原审批人)。

        Raises:
            KeyError: 申请单不存在
            ValueError: action 非法/状态非 pending/非当前级审批人
        """
        if action not in ("approve", "reject"):
            raise ValueError("action 非法(须为 approve/reject)")
        async with get_lock(f"perm:request:{request_id}"):
            req = await self.repo.get_request(request_id)
            if not req:
                raise KeyError(f"申请单不存在(id={request_id})")
            if req["status"] != "pending":
                raise ValueError(f"申请单状态({req['status']})不可审批")

            # P2: 懒惰超时升级(48h 未批追加上一级候选)
            req = await self._maybe_escalate(req)

            chain = req.get("approvals") or []
            step = self._current_step(req)
            if step >= len(chain):
                raise ValueError("审批链已走完, 状态异常")
            is_super = await self._is_super_admin(operator_id)
            eligible = chain[step].get("approverIds") or []

            # P2: 代理人判定(委托生效中 → 可代批)
            delegated_from = None
            if operator_id not in eligible and not is_super:
                for candid in eligible:
                    if await self._is_active_delegate(
                            delegator_id=candid, delegate_to=operator_id):
                        delegated_from = candid
                        break
                if delegated_from is None:
                    raise ValueError(
                        f"非当前级({chain[step]['role']})候选审批人, "
                        f"越级审批被拒绝")

            now_iso = _now().isoformat()
            decided_note = {}
            if delegated_from is not None:
                decided_note = {"delegatedFrom": delegated_from}
            if action == "reject":
                chain[step].update({
                    "approvedBy": operator_id,
                    "opinion": opinion[:200] or "驳回",
                    "decidedAt": now_iso,
                    "rejected": True,
                    **decided_note,
                })
                req.update({"status": "rejected", "decidedAt": now_iso,
                            "approvals": chain})
                await self.repo.save_request(req)
                await self._log(operator_id, "apply_reject",
                                req["nodeCode"],
                                detail={"requestId": request_id,
                                        "step": step + 1,
                                        **decided_note})
                return req

            # 同意: 落本级结论
            chain[step].update({
                "approvedBy": operator_id,
                "opinion": opinion[:200],
                "decidedAt": now_iso,
                **decided_note,
            })
            await self._log(operator_id, "apply_approve",
                            req["nodeCode"],
                            detail={"requestId": request_id,
                                    "step": step + 1,
                                    **decided_note})
            # 是否全部通过
            if self._current_step({"approvals": chain}) >= len(chain):
                # 终审通过 → 生成授权(限时, 待签责任书)
                grant_id = await self.repo.next_id("grant")
                grant = {
                    "grantId": grant_id,
                    "memberId": req["applicantId"],
                    "nodeCode": req["nodeCode"],
                    "nodeName": req["nodeName"],
                    "source": "apply",
                    "status": "active",
                    "dutySigned": False,
                    "expiresAt": (_now() + timedelta(
                        days=req["durationDays"])).isoformat(),
                    "grantedBy": operator_id,
                    "requestId": request_id,
                    "createdAt": now_iso,
                }
                await self.repo.save_grant(grant)
                req.update({"status": "approved", "decidedAt": now_iso,
                            "approvals": chain,
                            "grantId": grant_id})
            else:
                req.update({"approvals": chain,
                            "currentStep": step + 1})
            await self.repo.save_request(req)
            return req

    async def cancel_request(self, member_id: int, request_id: int) -> dict:
        """撤回申请(仅申请人本人, 仅 pending)"""
        req = await self.repo.get_request(request_id)
        if not req:
            raise KeyError(f"申请单不存在(id={request_id})")
        if req["applicantId"] != member_id:
            raise PermissionError("仅申请人本人可撤回")
        if req["status"] != "pending":
            raise ValueError(f"申请单状态({req['status']})不可撤回")
        updated = await self.repo.update_request(request_id, {
            "status": "cancelled", "decidedAt": _now().isoformat()})
        await self._log(member_id, "apply_cancel", req["nodeCode"],
                        detail={"requestId": request_id})
        return updated

    # ============================================================
    # 权限校验(供其他模块复用)
    # ============================================================

    async def check_permission(self, member_id: int,
                               node_code: str) -> dict:
        """校验会员是否持有权限(超管直通; 需生效+已签责任书+未过期)

        Raises:
            KeyError: 权限点不存在
            PermissionError: 无权限/未签责任书/已过期/已冻结
        """
        node = await self._require_node(node_code)
        if await self._is_super_admin(member_id):
            return {"allowed": True, "via": "super_admin",
                    "nodeName": node["name"]}

        grants = await self.repo.list_grants(
            member_id=member_id, node_code=node_code)
        active = [g for g in grants if g["status"] == "active"]
        frozen = [g for g in grants if g["status"] == "frozen"]
        if frozen and not active:
            raise PermissionError(f"权限「{node['name']}」已被冻结")
        if not active:
            # 越权尝试记录(AI 监控: low 级留痕)
            await self._log(member_id, "deny_access", node_code,
                            risk_level="low",
                            detail={"reason": "no_grant"})
            # P1: 越权 1h ≥3 次升级冻结(延迟导入避免循环依赖)
            from services.perm_ai_service import PermAiService
            await PermAiService(perm_repo=self.repo,
                                member_repo=self.member_repo,
                                perm_service=self).escalate_denials(
                member_id, node_code)
            raise PermissionError(f"无权限「{node['name']}」, 请先申请")

        grant = active[0]
        if self._is_expired(grant):
            await self.repo.update_grant(grant["grantId"],
                                         {"status": "expired"})
            await self._log(member_id, "grant_expire", node_code,
                            detail={"grantId": grant["grantId"]})
            raise PermissionError(f"权限「{node['name']}」已到期, 请续签")
        if not grant.get("dutySigned"):
            await self._log(member_id, "deny_access", node_code,
                            risk_level="medium",
                            detail={"reason": "duty_unsigned"})
            # P1: 越权 1h ≥3 次升级冻结
            from services.perm_ai_service import PermAiService
            await PermAiService(perm_repo=self.repo,
                                member_repo=self.member_repo,
                                perm_service=self).escalate_denials(
                member_id, node_code)
            raise PermissionError(
                f"权限「{node['name']}」未签署责任书, 签署后方可行使")
        return {"allowed": True, "via": "grant",
                "grantId": grant["grantId"],
                "nodeName": node["name"]}

    # ============================================================
    # P2: 代理审批(委托管理)
    # ============================================================

    async def _is_active_delegate(self, delegator_id: int,
                                  delegate_to: int) -> bool:
        """delegate_to 是否为 delegator_id 的生效代理人"""
        if delegator_id == delegate_to:
            return False
        delegates = await self.repo.list_delegates(
            delegator_id=delegator_id, delegate_to_id=delegate_to,
            status="active")
        return len(delegates) > 0

    async def set_delegate(self, delegator_id: int, delegate_to: int) -> dict:
        """设置代理审批人(每人同时仅 1 个生效代理人, 覆盖式)

        Raises:
            KeyError: 代理人会员不存在
            ValueError: 代理人不能是本人/已是别人的生效代理人(防代理环)
        """
        if delegator_id == delegate_to:
            raise ValueError("代理人不能是本人")
        target = await self.member_repo.get_by_id(delegate_to)
        if not target:
            raise KeyError(f"代理人会员不存在(id={delegate_to})")
        # 防代理环: 代理人不能已委托给本人或本人已委托的链路
        existing_of_target = await self.repo.list_delegates(
            delegator_id=delegate_to, status="active")
        if existing_of_target:
            raise ValueError(
                f"会员 {delegate_to} 已设有自己的代理人, 不可再接受委托(防代理环)")
        async with get_lock(f"perm:delegate:{delegator_id}"):
            # 覆盖旧委托(撤销旧的, 建新的)
            for old in await self.repo.list_delegates(
                    delegator_id=delegator_id, status="active"):
                await self.repo.save_delegate({
                    **old, "status": "cancelled",
                    "cancelledAt": _now().isoformat()})
            delegate_id = await self.repo.next_id("delegate")
            record = {
                "delegateId": delegate_id,
                "delegatorId": delegator_id,
                "delegateToId": delegate_to,
                "status": "active",
                "createdAt": _now().isoformat(),
                "cancelledAt": "",
            }
            await self.repo.save_delegate(record)
        await self._log(delegator_id, "delegate_set", "",
                        detail={"delegateTo": delegate_to})
        return record

    async def cancel_delegate(self, delegator_id: int) -> dict:
        """取消我的代理委托

        Raises:
            ValueError: 无生效委托
        """
        actives = await self.repo.list_delegates(
            delegator_id=delegator_id, status="active")
        if not actives:
            raise ValueError("无生效的代理委托")
        record = actives[0]
        updated = await self.repo.save_delegate({
            **record, "status": "cancelled",
            "cancelledAt": _now().isoformat()})
        await self._log(delegator_id, "delegate_cancel", "",
                        detail={"delegateId": record["delegateId"]})
        return updated

    async def my_delegates(self, member_id: int) -> dict:
        """我的委托(我设的代理人) + 我受托的(别人委托我代批的)"""
        members = {m["id"]: m for m
                   in await self.member_repo.list_all()}
        mine = await self.repo.list_delegates(
            delegator_id=member_id, status="active")
        entrusted = await self.repo.list_delegates(
            delegate_to_id=member_id, status="active")
        for d in mine:
            m = members.get(d.get("delegateToId"))
            d["counterpartNickname"] = m.get("nickname", "") if m else ""
        for d in entrusted:
            m = members.get(d.get("delegatorId"))
            d["counterpartNickname"] = m.get("nickname", "") if m else ""
        return {"mine": mine, "entrusted": entrusted}

    # ============================================================
    # P2: 审批超时升级(48h 未批 → 追加上一级候选)
    # ============================================================

    def _step_deadline(self, req: dict, step_idx: int) -> datetime | None:
        """当前级起算时间: 上一级结论时间(或首级取申请创建时间)+48h"""
        chain = req.get("approvals") or []
        if step_idx <= 0:
            base = _parse_iso(req.get("createdAt", ""))
        else:
            base = _parse_iso(chain[step_idx - 1].get("decidedAt", "")) \
                or _parse_iso(req.get("createdAt", ""))
        if not base:
            return None
        return base + timedelta(hours=APPROVAL_TIMEOUT_HOURS)

    async def _maybe_escalate(self, req: dict) -> dict:
        """懒惰超时升级: 当前级超时未批 → 追加上一级候选审批人

        升级规则: 第 N 级超时 → 把第 N+1 级候选(末级则超管)追加进
        第 N 级 approverIds, 标注 escalated; 幂等(已升级不重复)。
        """
        if req.get("status") != "pending":
            return req
        chain = req.get("approvals") or []
        step = self._current_step(req)
        if step >= len(chain) or chain[step].get("escalated"):
            return req
        deadline = self._step_deadline(req, step)
        if not deadline or _now() < deadline:
            return req
        # 升级候选: 下一级候选人; 末级 → 超管
        supers = await self._get_super_admin_ids()
        if step + 1 < len(chain):
            extra = [i for i in (chain[step + 1].get("approverIds") or [])
                     if i not in (chain[step].get("approverIds") or [])]
        else:
            extra = [i for i in supers
                     if i not in (chain[step].get("approverIds") or [])]
        if not extra:
            # 无可追加候选(如末级已是超管单候选人), 标记已尝试防重复扫描
            chain[step]["escalated"] = True
            chain[step]["escalatedNote"] = "无可升级候选"
            req["approvals"] = chain
            return await self.repo.save_request(req)
        chain[step].update({
            "approverIds": (chain[step].get("approverIds") or []) + extra,
            "escalated": True,
            "escalatedAt": _now().isoformat(),
            "escalatedNote": f"超时未批, 已升级追加 {len(extra)} 名候选审批人",
        })
        req["approvals"] = chain
        updated = await self.repo.save_request(req)
        log_by = (supers[0] if supers else req["applicantId"])
        await self._log(log_by, "approval_timeout_escalate",
                        req.get("nodeCode", ""),
                        detail={"requestId": req.get("requestId"),
                                "step": step + 1, "added": extra})
        return updated

    async def escalation_sweep(self, operator_id: int = 0) -> dict:
        """批量超时升级扫描(管理端触发; 审批时亦有懒惰升级)"""
        escalated = []
        for r in await self.repo.list_requests(status="pending"):
            before = bool((r.get("approvals") or []) and
                          r["approvals"][self._current_step(r)].get(
                              "escalated"))
            after = await self._maybe_escalate(r)
            step = self._current_step(after)
            chain = after.get("approvals") or []
            if (step < len(chain) and chain[step].get("escalated")
                    and not before):
                escalated.append(after["requestId"])
        logger.info("perm_escalation_sweep escalated=%d by=%r",
                    len(escalated), operator_id)
        return {"escalated": escalated, "count": len(escalated)}

    # ============================================================
    # 到期回收 / 管理端查询
    # ============================================================

    async def expire_sweep(self, admin_id: int = 0) -> dict:
        """清扫全部到期授权(手动触发; 访问时亦有惰性过期)"""
        grants = await self.repo.list_grants(status="active")
        expired = []
        for g in grants:
            if self._is_expired(g):
                await self.repo.update_grant(g["grantId"],
                                             {"status": "expired"})
                await self._log(g["memberId"], "grant_expire",
                                g["nodeCode"],
                                detail={"grantId": g["grantId"]})
                expired.append(g["grantId"])
        logger.info("perm_expire_sweep expired=%d by=%r", len(expired),
                    admin_id)
        return {"swept": len(expired), "expiredGrantIds": expired}

    async def admin_list_grants(self, admin_id: int,
                                status: str = None) -> list[dict]:
        if not await self._is_super_admin(admin_id):
            raise PermissionError("仅超级管理员可查看全部授权")
        grants = await self.repo.list_grants(status=status)
        # 附会员昵称便于管理端展示
        members = {m["id"]: m for m in await self.member_repo.list_all()}
        for g in grants:
            m = members.get(g.get("memberId"))
            g["memberNickname"] = m.get("nickname", "") if m else ""
        return sorted(grants, key=lambda x: x.get("grantId", 0),
                      reverse=True)

    async def admin_list_logs(self, admin_id: int,
                              limit: int = 50) -> list[dict]:
        if not await self._is_super_admin(admin_id):
            raise PermissionError("仅超级管理员可查看审计日志")
        return await self.repo.list_logs(limit=limit)
