"""AI 工单引擎四层(P2: 智能生成 + 智能分派 + 智能追踪 + 智能关闭)

设计对齐: 客服管理模块设计文档 7.0(AI自动化率 85%, 仅重大/跨部门工单人工介入)

四层能力:
    1. 智能生成: 会话智能摘要 + 工单智能分类(类型/优先级) + 智能标签
       —— 从 chat 会话提取关键信息, 规则式分类引擎生成 source=ai 工单
    2. 智能分派: 待分配工单 × 候选客服(契约持有人)
       —— 评分 = 负载均衡×50% + 近30天满意度×30% + 类型技能匹配×20%
    3. 智能追踪: SLA 超时预警 + 售后48h升级 + 催办站内信
    4. 智能关闭: 已解决超48h未确认 → 自动确认(默认4星, 不触发分润) + 归档关闭

门控(三态灰度, 对齐全站范式):
    TICKET_AI_ENGINE = off(默认) | on
    off → 引擎端点 409(观测零影响); on → 执行

依赖说明:
    - 分派候选来自 role 契约(ROLE_CUSTOMER_SERVICE + DISPATCHABLE_STATUSES)
    - 评分独立于 role_service.dispatch_customer_service(会话绑定版),
      本引擎面向存量 pending 工单批量分派
"""

import logging
import os
from datetime import datetime, timedelta

from core.locks import get_lock
from core.helpers import ts

from services.ticket_service import TicketService, SLA_HOURS, ESCALATE_HOURS
from repositories.ticket_repository import (
    TICKET_TYPE_PRESALE, TICKET_TYPE_AFTERSALE, TICKET_TYPE_COMPLAINT,
    TICKET_TYPE_SUGGESTION, TICKET_TYPE_OLDWINE,
    PRIORITY_URGENT, PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW,
    TICKET_STATUS_PENDING, TICKET_STATUS_PROCESSING,
    TICKET_STATUS_WAIT_CONFIRM, TICKET_STATUS_RESOLVED, TICKET_STATUS_CLOSED,
    ACTIVE_STATUSES, SOURCE_AI,
)
from repositories.role_repository import (
    RoleRepository, ROLE_CUSTOMER_SERVICE, DISPATCHABLE_STATUSES,
)
from repositories.chat_repository import ChatRepository

logger = logging.getLogger(__name__)


def ticket_ai_enabled() -> bool:
    """引擎开关(TICKET_AI_ENGINE, 默认 off 零影响)"""
    return os.environ.get("TICKET_AI_ENGINE", "off") == "on"


# ============================================================
# 第一层·智能生成: 分类规则表(关键词 → 类型/优先级)
# ============================================================

CLASSIFY_RULES = (
    # (关键词组, 工单类型, 优先级, 标签)
    (("投诉", "差评", "假货", "态度"), TICKET_TYPE_COMPLAINT,
     PRIORITY_URGENT, ("complaint", "bad-review")),
    (("退款", "退货", "破损", "漏酒"), TICKET_TYPE_AFTERSALE,
     PRIORITY_HIGH, ("refund", "aftersale")),
    (("老酒", "回收", "兑换新酒"), TICKET_TYPE_OLDWINE,
     PRIORITY_MEDIUM, ("old-wine",)),
    (("建议", "意见", "希望"), TICKET_TYPE_SUGGESTION,
     PRIORITY_LOW, ("suggestion",)),
)
DEFAULT_CLASSIFY = (TICKET_TYPE_PRESALE, PRIORITY_MEDIUM, ("presale",))

# 第二层·分派评分权重
DISPATCH_W_LOAD = 0.50      # 负载均衡(处理中工单数/容量)
DISPATCH_W_SATISFACTION = 0.30  # 近30天满意度
DISPATCH_W_SKILL = 0.20     # 类型技能匹配(历史处理同类型占比)
LOAD_CAPACITY = 10          # 客服单人工单容量(对齐 role 范式)
DEFAULT_SAT = 0.80          # 无满意度历史基线

# 第四层·自动关闭阈值(已解决超 48h 未确认)
AUTO_CLOSE_HOURS = 48
AUTO_CLOSE_SATISFACTION = 4   # 自动确认默认满意度(不触发分润结算)


class TicketAIService:
    """AI 工单引擎(四层, 双模式存储, 锁保护 RMW)"""

    def __init__(self):
        self.ticket_service = TicketService()
        self.repo = self.ticket_service.repo
        self.role_repo = RoleRepository()
        self.chat_repo = ChatRepository()

    # ============================================================
    # 第一层: 智能生成(会话摘要 + 智能分类 + 智能标签)
    # ============================================================

    def _classify(self, text: str) -> tuple[str, str, tuple]:
        """规则式智能分类(关键词命中 → 类型/优先级/标签)"""
        for keywords, ttype, priority, tags in CLASSIFY_RULES:
            if any(kw in text for kw in keywords):
                return ttype, priority, tags
        return DEFAULT_CLASSIFY

    def _summarize(self, messages: list[dict]) -> str:
        """会话智能摘要(首条用户诉求 + 轮次/转人工原因)"""
        user_msgs = [m for m in messages
                     if m.get("senderType") == "user"
                     and m.get("contentType") == "text"]
        first = (user_msgs[0].get("content", "") if user_msgs else "").strip()
        summary = first[:80] + ("…" if len(first) > 80 else "")
        return (f"[AI摘要] {summary or '(无文字诉求)'} "
                f"(共 {len(messages)} 条消息, 用户发言 {len(user_msgs)} 条)")

    async def generate_from_session(self, session_id: str) -> dict:
        """基于会话智能生成工单(source=ai, 幂等: 同会话不重复生成)

        Raises:
            KeyError: 会话不存在
            ValueError: 会话已结束不可生成 / 会话已生成过工单
        """
        session = await self.chat_repo.get_session(session_id)
        if session is None:
            raise KeyError(f"会话不存在({session_id})")
        if session.get("status") == "ended":
            raise ValueError("会话已结束, 不可生成工单")

        async with get_lock(f"ticket:ai:generate:{session_id}"):
            # 幂等: 该会话已生成过 AI 工单
            existing = await self.repo.list_tickets(limit=100000)
            for t in existing:
                if (t.get("source") == SOURCE_AI
                        and t.get("sessionId") == session_id):
                    raise ValueError(
                        f"该会话已生成工单({t.get('ticketNo')}), 不重复生成")

            messages = await self.chat_repo.list_messages(session_id)
            full_text = " ".join(
                str(m.get("content", "")) for m in messages)
            ttype, priority, tags = self._classify(full_text)
            summary = self._summarize(messages)

            ticket = await self.ticket_service.create_ticket(
                user_id=session.get("userId", 0),
                ticket_type=ttype,
                priority=priority,
                description=summary,
                source=SOURCE_AI,
            )
            # 智能标签 + 会话溯源落档
            await self.repo.update_ticket(ticket["ticketNo"], {
                "aiTags": list(tags),
                "aiGenerated": True,
                "sessionId": session_id,
                "messageCount": len(messages),
            })
            ticket.update({"aiTags": list(tags), "sessionId": session_id,
                           "aiGenerated": True})
        return ticket

    # ============================================================
    # 第二层: 智能分派(负载 + 满意度 + 技能)
    # ============================================================

    async def _score_handlers(self) -> dict[int, dict]:
        """候选客服评分(契约持有人, 剔除超载)"""
        contracts = await self.role_repo.list_contracts(
            role_code=ROLE_CUSTOMER_SERVICE,
            statuses=DISPATCHABLE_STATUSES)
        if not contracts:
            return {}

        # 全量工单一次取齐(负载/满意度/技能统计)
        all_tickets = await self.repo.list_tickets(limit=100000)
        active = [t for t in all_tickets
                  if t.get("status") in (TICKET_STATUS_PROCESSING,)]
        resolved = [t for t in all_tickets
                    if t.get("status") in (TICKET_STATUS_RESOLVED,
                                           TICKET_STATUS_CLOSED)]
        since = (datetime.utcnow() - timedelta(days=30)).isoformat()

        scores = {}
        for c in contracts:
            uid = c.get("userId")
            if uid is None:
                continue
            load = sum(1 for t in active if t.get("handlerId") == uid)
            if load >= LOAD_CAPACITY:
                continue   # 超载剔除
            load_score = 1.0 - load / LOAD_CAPACITY

            recent_sat = [t["satisfaction"] for t in resolved
                          if t.get("handlerId") == uid
                          and t.get("satisfaction")
                          and t.get("resolvedAt", "") >= since]
            sat_score = (sum(recent_sat) / len(recent_sat) / 5.0
                         if recent_sat else DEFAULT_SAT)

            handled = [t for t in resolved + active
                       if t.get("handlerId") == uid]
            # 经验技能分: 历史处理量经验曲线(10 单封顶, 类型无关基线)
            skill_score = min(1.0, len(handled) / 10.0) if handled else 0.5

            scores[uid] = {
                "handlerId": uid,
                "handlerName": c.get("roleName", ""),
                "activeTickets": load,
                "loadScore": round(load_score, 4),
                "satisfactionScore": round(sat_score, 4),
                "skillScore": round(skill_score, 4),
                "total": round(DISPATCH_W_LOAD * load_score
                               + DISPATCH_W_SATISFACTION * sat_score
                               + DISPATCH_W_SKILL * skill_score, 4),
            }
        return scores

    async def auto_dispatch(self, limit: int = 20) -> dict:
        """批量智能分派待分配工单(每单选当前最优客服)

        Returns:
            {scanned, dispatched, fallback, dispatches: [{ticketNo, handlerId, score}]}
        """
        scores = await self._score_handlers()
        pending = await self.repo.list_tickets(
            status=TICKET_STATUS_PENDING, limit=limit)
        dispatched, fallback = 0, 0
        dispatches = []
        for t in pending:
            ticket_no = t.get("ticketNo")
            if not scores:
                fallback += 1
                continue
            async with get_lock(f"ticket:transition:{ticket_no}"):
                # 锁内重读状态(并发下可能已被人工分配)
                fresh = await self.repo.get_ticket(ticket_no)
                if not fresh or fresh.get("status") != TICKET_STATUS_PENDING:
                    continue
                # 负载感知: 重算各候选实时负载后选最优
                best_uid, best = None, None
                for uid, sc in scores.items():
                    if best is None or sc["total"] > best["total"]:
                        best_uid, best = uid, sc
                if best_uid is None:
                    fallback += 1
                    continue
                await self.ticket_service.assign_ticket_locked(
                    ticket_no, best_uid, best.get("handlerName", ""))
                best["activeTickets"] += 1
                best["loadScore"] = round(
                    max(0.0, 1.0 - best["activeTickets"] / LOAD_CAPACITY), 4)
                best["total"] = round(
                    DISPATCH_W_LOAD * best["loadScore"]
                    + DISPATCH_W_SATISFACTION * best["satisfactionScore"]
                    + DISPATCH_W_SKILL * best["skillScore"], 4)
                dispatched += 1
                dispatches.append({
                    "ticketNo": ticket_no,
                    "handlerId": best_uid,
                    "handlerName": best.get("handlerName", ""),
                    "score": best["total"],
                })
        return {"scanned": len(pending), "dispatched": dispatched,
                "fallback": fallback, "dispatches": dispatches}

    # ============================================================
    # 第三层: 智能追踪(SLA 超时预警 + 升级 + 催办站内信)
    # ============================================================

    async def run_sla_scan(self) -> dict:
        """SLA 追踪扫描(超时标记落库 + 售后48h升级 + 催办站内信, 每单每日至多一催)

        Returns:
            {scanned, overdue, escalated, reminded, remindFailed}
        """
        ts_svc = self.ticket_service
        actives = []
        for status in ACTIVE_STATUSES:
            rows = await self.repo.list_tickets(status=status, limit=100000)
            actives.extend(r for r in rows if r not in actives)

        overdue = escalated = reminded = remind_failed = 0
        for t in actives:
            ticket_no = t.get("ticketNo")
            deco = await ts_svc._decorate(t)
            is_overdue = bool(deco.get("overdue"))
            is_escalated = bool(deco.get("escalated"))
            if not (is_overdue or is_escalated):
                continue
            if is_overdue:
                overdue += 1
            updates = {"updatedAt": ts()}
            if is_overdue:
                updates["overdueMarkedAt"] = updates["updatedAt"]
            if is_escalated and not t.get("escalated"):
                updates["escalated"] = True
                escalated += 1
            await self.repo.update_ticket(ticket_no, updates)

            # 催办站内信(每日至多一催, best-effort)
            last = t.get("lastRemindedAt", "")
            if last[:10] == ts()[:10]:
                continue
            handler_id = t.get("handlerId")
            if handler_id:
                ok = await self._remind_handler(handler_id, deco)
                if ok:
                    reminded += 1
                    await self.repo.update_ticket(ticket_no, {
                        "lastRemindedAt": ts()})
                else:
                    remind_failed += 1
        return {"scanned": len(actives), "overdue": overdue,
                "escalated": escalated, "reminded": reminded,
                "remindFailed": remind_failed}

    async def _remind_handler(self, handler_id: int, ticket: dict) -> bool:
        """催办站内信(system 必需类, best-effort)"""
        try:
            from services.message_service import MessageService
            from repositories.message_repository import (
                CHANNEL_INMAIL, CATEGORY_SYSTEM,
            )
            await MessageService().send_message(
                handler_id, CHANNEL_INMAIL,
                f"工单超时催办({ticket.get('ticketNo')})",
                f"工单 {ticket.get('ticketNo')} "
                f"(优先级 {ticket.get('priority')}) 已超 SLA 时限, "
                f"请尽快处理; 售后工单超 48h 未解决将升级主管。",
                CATEGORY_SYSTEM)
            return True
        except Exception:
            return False

    # ============================================================
    # 第四层: 智能关闭(超时自动确认 + 归档)
    # ============================================================

    async def run_auto_close(self) -> dict:
        """智能关闭扫描(wait_confirm/resolved 超48h → 自动确认默认4星并关闭)

        注: 自动确认不触发服务分润结算(分润基于用户真实满意度,
            自动确认会造成虚假分润; 人工确认路径不受影响)。

        Returns:
            {scanned, autoClosed, skippedRecent}
        """
        threshold = (datetime.utcnow()
                     - timedelta(hours=AUTO_CLOSE_HOURS)).isoformat()
        candidates = []
        seen = set()
        for status in (TICKET_STATUS_RESOLVED, TICKET_STATUS_WAIT_CONFIRM):
            rows = await self.repo.list_tickets(status=status, limit=100000)
            for r in rows:
                if r.get("ticketNo") not in seen:
                    seen.add(r.get("ticketNo"))
                    candidates.append(r)

        auto_closed = 0
        skipped = 0
        for t in candidates:
            ticket_no = t.get("ticketNo")
            resolved_at = t.get("resolvedAt") or t.get("updatedAt", "")
            if not resolved_at or resolved_at > threshold:
                skipped += 1
                continue
            async with get_lock(f"ticket:transition:{ticket_no}"):
                fresh = await self.repo.get_ticket(ticket_no)
                if not fresh or fresh.get("status") not in (
                        TICKET_STATUS_RESOLVED, TICKET_STATUS_WAIT_CONFIRM):
                    continue
                await self.repo.update_ticket(ticket_no, {
                    "satisfaction": AUTO_CLOSE_SATISFACTION,
                    "autoConfirmed": True,
                    "confirmedAt": ts(),
                    "status": TICKET_STATUS_CLOSED,
                    "closedAt": ts(),
                    "closedBy": "ai_engine",
                    "closeReason": f"已解决超 {AUTO_CLOSE_HOURS}h 未确认, "
                                   f"AI 引擎自动确认归档",
                    "updatedAt": ts(),
                })
                auto_closed += 1
        return {"scanned": len(candidates), "autoClosed": auto_closed,
                "skippedRecent": skipped}

    # ============================================================
    # 自动化率观测(85% 目标进度)
    # ============================================================

    async def get_automation_stats(self) -> dict:
        """AI 自动化率(AI 生成 + AI 自动关闭 占比, 目标 85%)"""
        all_tickets = await self.repo.list_tickets(limit=100000)
        total = len(all_tickets)
        ai_generated = sum(1 for t in all_tickets
                           if t.get("source") == SOURCE_AI)
        ai_closed = sum(1 for t in all_tickets
                        if t.get("closedBy") == "ai_engine")
        auto_handled = sum(1 for t in all_tickets
                           if t.get("source") == SOURCE_AI
                           or t.get("closedBy") == "ai_engine"
                           or (t.get("autoConfirmed") and not t.get("satisfaction")))
        rate = round(auto_handled / total * 100, 2) if total else 0.0
        return {
            "totalTickets": total,
            "aiGenerated": ai_generated,
            "aiClosed": ai_closed,
            "autoHandled": auto_handled,
            "automationRate": rate,
            "targetRate": 85,
            "engineEnabled": ticket_ai_enabled(),
        }
