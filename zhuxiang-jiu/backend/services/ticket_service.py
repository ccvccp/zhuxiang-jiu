"""客服工单业务逻辑层

核心业务(客服管理模块设计文档 第七章):
    - 工单创建(5类/4优先级/4来源, 投诉自动升紧急)
    - 工单分配(客服认领/指派, 待分配→处理中)
    - 处理记录(客服回复+用户补充, 多轮)
    - 状态机(待分配→处理中→待用户确认→已解决/已关闭)
    - SLA时限(紧急立即/高2h/中4h/低24h)与超时标记
    - 超时升级(售后工单48h未解决 → 升级主管)
    - 用户确认+满意度(1-5星)
    - 工单统计(状态/类型/超时/SLA达标)

锁保护:
    - 创建: ticket:create:{user_id}   (工单号生成)
    - 流转: ticket:transition:{ticket_no} (状态机原子流转)

异常约定:
    - KeyError → 404(工单不存在)
    - ValueError → 409(状态非法/类型非法/越权确认等)
"""

from datetime import datetime, timedelta, timezone

from core.locks import get_lock
from core.helpers import ts
from repositories.ticket_repository import (
    TicketRepository,
    # 工单类型
    TICKET_TYPES, TICKET_TYPE_COMPLAINT, TICKET_TYPE_AFTERSALE,
    # 优先级
    PRIORITIES, PRIORITY_URGENT, PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW,
    # 来源
    SOURCES,
    # 状态机
    TICKET_STATUS_PENDING, TICKET_STATUS_PROCESSING,
    TICKET_STATUS_WAIT_CONFIRM, TICKET_STATUS_RESOLVED, TICKET_STATUS_CLOSED,
    ACTIVE_STATUSES,
)


# ============================================================
# SLA 时限(小时, 文档行 886-887)
# ============================================================

SLA_HOURS = {
    PRIORITY_URGENT: 0,    # 紧急: 立即处理
    PRIORITY_HIGH: 2,      # 高: 2小时内
    PRIORITY_MEDIUM: 4,    # 中: 4小时内
    PRIORITY_LOW: 24,      # 低: 24小时内
}

# 售后工单超时升级阈值(48小时未解决 → 升级主管, 文档行 783)
ESCALATE_HOURS = 48

# 状态流转图
STATUS_TRANSITIONS = {
    TICKET_STATUS_PENDING: {TICKET_STATUS_PROCESSING},
    TICKET_STATUS_PROCESSING: {TICKET_STATUS_WAIT_CONFIRM,
                                TICKET_STATUS_RESOLVED},
    TICKET_STATUS_WAIT_CONFIRM: {TICKET_STATUS_RESOLVED,
                                  TICKET_STATUS_PROCESSING},
    TICKET_STATUS_RESOLVED: {TICKET_STATUS_CLOSED},
    TICKET_STATUS_CLOSED: set(),
}


def _utcnow() -> datetime:
    """当前 UTC 时间(与 core.helpers.ts() 同为 offset-aware)"""
    return datetime.now(timezone.utc)


class TicketService:
    """客服工单业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: TicketRepository = TicketRepository()):
        self.repo = repo

    # ============================================================
    # 1. 工单创建
    # ============================================================

    async def create_ticket(self, user_id: int, ticket_type: str,
                             priority: str, description: str,
                             source: str = "user", order_id: str = "",
                             user_phone: str = "", user_level: int = 1,
                             member_vip: bool = False) -> dict:
        """创建工单

        规则:
            - 类型/优先级/来源枚举校验
            - 投诉工单自动升级为紧急(文档: 投诉→工单升级优先级)
            - VIP 用户自动升级为紧急(文档: 投诉/VIP→立即处理)
            - 工单号: GD+时间戳+序号

        Raises:
            ValueError: 类型/优先级/来源非法/描述为空
        """
        if ticket_type not in TICKET_TYPES:
            raise ValueError(f"工单类型无效(须为{'/'.join(TICKET_TYPES)})")
        if priority not in PRIORITIES:
            raise ValueError(f"优先级无效(须为{'/'.join(PRIORITIES)})")
        if source not in SOURCES:
            raise ValueError(f"工单来源无效(须为{'/'.join(SOURCES)})")
        if not description or not description.strip():
            raise ValueError("问题描述不能为空")

        # 投诉/VIP 自动升级紧急(SLA: 立即处理)
        if ticket_type == TICKET_TYPE_COMPLAINT or member_vip:
            priority = PRIORITY_URGENT

        async with get_lock(f"ticket:create:{user_id}"):
            ticket = {
                "ticketNo": "",
                "userId": user_id,
                "userPhone": user_phone,
                "userLevel": user_level,
                "type": ticket_type,
                "priority": priority,
                "source": source,
                "orderId": order_id,
                "description": description.strip(),
                "handlerId": None,       # 客服ID(待分配)
                "handlerName": "",
                "status": TICKET_STATUS_PENDING,
                "satisfaction": None,    # 1-5星(用户确认时采集)
                "escalated": False,      # 超时升级标记
                "createdAt": ts(),
                "updatedAt": ts(),
                "resolvedAt": "",
                "closedAt": "",
            }
            ticket_id = await self.repo.next_ticket_id()
            ticket["id"] = ticket_id
            ticket["ticketNo"] = self.repo.generate_ticket_no()
            await self.repo.create_ticket(ticket)
            return ticket

    # ============================================================
    # 2. 工单查询(含超时/SLA动态标记)
    # ============================================================

    async def get_ticket(self, ticket_no: str) -> dict:
        """查询工单详情(含处理记录+动态超时/SLA标记)

        Raises:
            KeyError: 工单不存在
        """
        ticket = await self.repo.get_ticket(ticket_no)
        if ticket is None:
            raise KeyError(f"工单不存在(ticketNo={ticket_no})")
        result = await self._decorate(ticket)
        result["replies"] = await self.repo.list_replies(ticket_no)
        return result

    async def list_tickets(self, status: str = None, ticket_type: str = None,
                            priority: str = None, user_id: int = None,
                            limit: int = 50) -> list[dict]:
        """查询工单列表(含动态超时/SLA标记)"""
        tickets = await self.repo.list_tickets(
            status=status, ticket_type=ticket_type,
            priority=priority, user_id=user_id, limit=limit)
        return [await self._decorate(t) for t in tickets]

    async def _decorate(self, ticket: dict) -> dict:
        """注入动态 SLA/超时标记(不落库, 查询时计算)"""
        result = dict(ticket)
        if result["status"] in ACTIVE_STATUSES:
            result["slaDeadline"] = self._sla_deadline(result)
            result["overdue"] = self._is_overdue(result)
            result["escalated"] = (
                result.get("escalated", False) or self._should_escalate(result))
        else:
            result["slaDeadline"] = ""
            result["overdue"] = False
        return result

    def _sla_deadline(self, ticket: dict) -> str:
        """计算 SLA 截止时间(创建时间+时限)"""
        try:
            created = datetime.fromisoformat(ticket["createdAt"])
        except (ValueError, KeyError):
            return ""
        deadline = created + timedelta(
            hours=SLA_HOURS.get(ticket.get("priority", PRIORITY_LOW), 24))
        return deadline.isoformat()

    def _is_overdue(self, ticket: dict) -> bool:
        """是否 SLA 超时(未在时限内开始处理)"""
        deadline = self._sla_deadline(ticket)
        if not deadline:
            return False
        try:
            return _utcnow() > datetime.fromisoformat(deadline)
        except ValueError:
            return False

    def _should_escalate(self, ticket: dict) -> bool:
        """售后工单 48h 未解决 → 升级主管(文档行 783)"""
        if ticket.get("type") != TICKET_TYPE_AFTERSALE:
            return False
        if ticket["status"] in (TICKET_STATUS_RESOLVED, TICKET_STATUS_CLOSED):
            return False
        try:
            created = datetime.fromisoformat(ticket["createdAt"])
        except (ValueError, KeyError):
            return False
        return _utcnow() > created + timedelta(hours=ESCALATE_HOURS)

    # ============================================================
    # 3. 工单流转
    # ============================================================

    async def assign_ticket(self, ticket_no: str, handler_id: int,
                             handler_name: str = "") -> dict:
        """分配客服(待分配 → 处理中)

        Raises:
            KeyError: 工单不存在
            ValueError: 状态非法
        """
        return await self._transition(
            ticket_no, TICKET_STATUS_PROCESSING,
            extra={"handlerId": handler_id, "handlerName": handler_name,
                   "assignedAt": ts()},
            log_action="assign",
            log_detail=f"分配客服({handler_name or handler_id})")

    async def reply_ticket(self, ticket_no: str, replier_id,
                            replier_role: str, content: str) -> dict:
        """工单回复/处理记录(客服回复+用户补充)

        规则:
            - 首次客服回复记录首次响应时间(KPI: 首次响应<2分钟)
            - 回复不清调状态(状态由 assign/resolve/confirm 显式流转)

        Raises:
            KeyError: 工单不存在
            ValueError: 内容为空/已关闭
        """
        async with get_lock(f"ticket:transition:{ticket_no}"):
            ticket = await self._get_or_404(ticket_no)
            if ticket["status"] == TICKET_STATUS_CLOSED:
                raise ValueError("工单已关闭, 不可回复")
            content = (content or "").strip()
            if not content:
                raise ValueError("回复内容不能为空")

            reply_id = await self.repo.next_reply_id()
            reply = {
                "id": reply_id,
                "ticketNo": ticket_no,
                "replierId": replier_id,
                "replierRole": replier_role,   # staff/user
                "content": content,
                "createdAt": ts(),
            }
            await self.repo.add_reply(reply)

            # 首次客服回复 → 记录首次响应时间
            updates = {"updatedAt": ts()}
            if (replier_role == "staff"
                    and not ticket.get("firstResponseAt")):
                updates["firstResponseAt"] = ts()
            await self.repo.update_ticket(ticket_no, updates)
            ticket.update(updates)
            return reply

    async def resolve_ticket(self, ticket_no: str, handler_id,
                              resolution: str) -> dict:
        """标记已处理待确认(处理中/待用户确认 → 待用户确认)

        规则:
            - 处理中 → 待用户确认(用户确认后才算已解决)
            - 若尚未分配(待分配)自动先流转处理中再解决

        Raises:
            KeyError: 工单不存在
            ValueError: 已解决/已关闭/解决方案为空
        """
        if not resolution or not resolution.strip():
            raise ValueError("解决方案不能为空")
        async with get_lock(f"ticket:transition:{ticket_no}"):
            ticket = await self._get_or_404(ticket_no)
            if ticket["status"] not in (TICKET_STATUS_PENDING,
                                         TICKET_STATUS_PROCESSING):
                raise ValueError(
                    f"工单状态非法(当前{ticket['status']}, "
                    f"须为{TICKET_STATUS_PENDING}/{TICKET_STATUS_PROCESSING})")
            # 待分配 → 处理中 → 待用户确认(未分配时两跳自动完成)
            if ticket["status"] == TICKET_STATUS_PENDING:
                ticket["handlerId"] = ticket.get("handlerId") or handler_id
            updates = {
                "status": TICKET_STATUS_WAIT_CONFIRM,
                "handlerId": ticket.get("handlerId") or handler_id,
                "resolution": resolution.strip(),
                "resolvedAt": ts(),
                "updatedAt": ts(),
            }
            await self.repo.update_ticket(ticket_no, updates)
            ticket.update(updates)
            return self._public(ticket)

    async def confirm_ticket(self, ticket_no: str, user_id: int,
                              satisfaction: int) -> dict:
        """用户确认解决+满意度(待用户确认 → 已解决)

        规则:
            - 仅工单所有者可确认
            - 满意度 1-5 星
            - 确认后状态: 已解决(可关闭)

        Raises:
            KeyError: 工单不存在
            ValueError: 状态非法/越权确认/满意度越界
        """
        if not isinstance(satisfaction, int) or not (1 <= satisfaction <= 5):
            raise ValueError("满意度须为 1-5 的整数")
        async with get_lock(f"ticket:transition:{ticket_no}"):
            ticket = await self._get_or_404(ticket_no)
            if ticket.get("userId") != user_id:
                raise ValueError("仅工单所有者可确认")
            if ticket["status"] != TICKET_STATUS_WAIT_CONFIRM:
                raise ValueError(
                    f"工单状态非法(当前{ticket['status']}, 须为{TICKET_STATUS_WAIT_CONFIRM})")
            updates = {
                "status": TICKET_STATUS_RESOLVED,
                "satisfaction": satisfaction,
                "confirmedAt": ts(),
                "updatedAt": ts(),
            }
            await self.repo.update_ticket(ticket_no, updates)
            ticket.update(updates)
            return self._public(ticket)

    async def close_ticket(self, ticket_no: str, operator: str = "admin",
                            reason: str = "") -> dict:
        """关闭工单(已解决 → 已关闭; 未解决工单 48h+ 亦可强制关闭)

        Raises:
            KeyError: 工单不存在
            ValueError: 状态非法
        """
        async with get_lock(f"ticket:transition:{ticket_no}"):
            ticket = await self._get_or_404(ticket_no)
            if ticket["status"] == TICKET_STATUS_CLOSED:
                raise ValueError("工单已关闭")
            if ticket["status"] != TICKET_STATUS_RESOLVED:
                # 未走完确认流程的强制关闭: 须超48h
                if not self._over_48h(ticket):
                    raise ValueError(
                        "仅已解决工单可关闭(未解决须满48小时方可强制关闭)")
            updates = {
                "status": TICKET_STATUS_CLOSED,
                "closedAt": ts(),
                "closeReason": reason,
                "updatedAt": ts(),
            }
            await self.repo.update_ticket(ticket_no, updates)
            ticket.update(updates)
            return self._public(ticket)

    def _over_48h(self, ticket: dict) -> bool:
        """创建是否已超48小时"""
        try:
            created = datetime.fromisoformat(ticket["createdAt"])
        except (ValueError, KeyError):
            return False
        return _utcnow() > created + timedelta(hours=ESCALATE_HOURS)

    async def _transition(self, ticket_no: str, target: str,
                           extra: dict = None, log_action: str = "",
                           log_detail: str = "") -> dict:
        """通用状态流转(校验流转图)"""
        async with get_lock(f"ticket:transition:{ticket_no}"):
            ticket = await self._get_or_404(ticket_no)
            current = ticket["status"]
            if target not in STATUS_TRANSITIONS.get(current, set()):
                raise ValueError(
                    f"工单状态流转非法({current} → {target})")
            updates = {"status": target, "updatedAt": ts()}
            if extra:
                updates.update(extra)
            await self.repo.update_ticket(ticket_no, updates)
            ticket.update(updates)
            return self._public(ticket)

    async def _get_or_404(self, ticket_no: str) -> dict:
        ticket = await self.repo.get_ticket(ticket_no)
        if ticket is None:
            raise KeyError(f"工单不存在(ticketNo={ticket_no})")
        return ticket

    @staticmethod
    def _public(ticket: dict) -> dict:
        """返回工单副本"""
        return dict(ticket)

    # ============================================================
    # 4. 工单统计
    # ============================================================

    async def get_stats(self) -> dict:
        """工单统计(状态/类型/优先级分布+超时+升级)"""
        tickets = await self.repo.list_tickets(limit=100000)
        status_count = {}
        type_count = {}
        priority_count = {}
        overdue_count = 0
        escalated_count = 0
        satisfaction_scores = []
        for t in tickets:
            status_count[t["status"]] = status_count.get(t["status"], 0) + 1
            type_count[t["type"]] = type_count.get(t["type"], 0) + 1
            priority_count[t["priority"]] = priority_count.get(
                t["priority"], 0) + 1
            if t["status"] in ACTIVE_STATUSES:
                if self._is_overdue(t):
                    overdue_count += 1
                if self._should_escalate(t):
                    escalated_count += 1
            if t.get("satisfaction"):
                satisfaction_scores.append(t["satisfaction"])

        avg_satisfaction = (
            round(sum(satisfaction_scores) / len(satisfaction_scores), 2)
            if satisfaction_scores else 0)

        return {
            "totalTickets": len(tickets),
            "statusCount": status_count,
            "typeCount": type_count,
            "priorityCount": priority_count,
            "activeCount": sum(status_count.get(s, 0)
                                for s in ACTIVE_STATUSES),
            "overdueCount": overdue_count,
            "escalatedCount": escalated_count,
            "avgSatisfaction": avg_satisfaction,
            "slaHours": SLA_HOURS,
            "escalateHours": ESCALATE_HOURS,
        }
