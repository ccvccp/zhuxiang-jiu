"""客服工单数据访问层(双模式: 内存 + Redis)

表清单:
    cs_tickets:       工单主表(工单号/类型/优先级/状态机/处理记录)
    cs_ticket_replies: 工单处理记录表(客服回复+用户补充)

设计对齐(客服管理模块设计文档 第七章):
    - 工单号: GD+时间戳
    - 状态机: pending → processing → wait_confirm → resolved → closed
    - 双模式存储: is_redis_mode() 切换内存字典/Redis
    - 超时/升级标记由 service 层动态计算, 不落库
"""

import json
from datetime import datetime

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 工单类型(5 类, 文档行 899)
# ============================================================

TICKET_TYPE_PRESALE = "presale"      # 售前
TICKET_TYPE_AFTERSALE = "aftersale"  # 售后
TICKET_TYPE_COMPLAINT = "complaint"  # 投诉
TICKET_TYPE_SUGGESTION = "suggestion"  # 建议
TICKET_TYPE_OLDWINE = "oldwine"      # 老酒
TICKET_TYPES = (
    TICKET_TYPE_PRESALE, TICKET_TYPE_AFTERSALE, TICKET_TYPE_COMPLAINT,
    TICKET_TYPE_SUGGESTION, TICKET_TYPE_OLDWINE,
)

# 优先级(4 级, 文档行 901)
PRIORITY_URGENT = "urgent"    # 紧急(投诉/VIP)
PRIORITY_HIGH = "high"        # 高(退款/破损)
PRIORITY_MEDIUM = "medium"    # 中(咨询/查询)
PRIORITY_LOW = "low"          # 低(建议/反馈)
PRIORITIES = (PRIORITY_URGENT, PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)

# 工单来源(4 种, 文档行 901)
SOURCE_AI = "ai"              # AI转人工
SOURCE_USER = "user"          # 用户直接
SOURCE_RULE = "rule"          # 规则触发
SOURCE_PHONE = "phone"        # 电话
SOURCES = (SOURCE_AI, SOURCE_USER, SOURCE_RULE, SOURCE_PHONE)

# 状态机(5 态, 文档行 907)
TICKET_STATUS_PENDING = "pending"            # 待分配
TICKET_STATUS_PROCESSING = "processing"      # 处理中
TICKET_STATUS_WAIT_CONFIRM = "wait_confirm"  # 待用户确认
TICKET_STATUS_RESOLVED = "resolved"          # 已解决
TICKET_STATUS_CLOSED = "closed"              # 已关闭
TICKET_STATUSES = (
    TICKET_STATUS_PENDING, TICKET_STATUS_PROCESSING,
    TICKET_STATUS_WAIT_CONFIRM, TICKET_STATUS_RESOLVED, TICKET_STATUS_CLOSED,
)

# 活跃状态(未终态)
ACTIVE_STATUSES = (TICKET_STATUS_PENDING, TICKET_STATUS_PROCESSING,
                   TICKET_STATUS_WAIT_CONFIRM)


class TicketRepository:
    """客服工单数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # ID / 工单号生成
    # ============================================================

    async def next_ticket_id(self) -> int:
        """生成工单ID"""
        if is_redis_mode():
            return await self._redis_next_id("ticket")
        return self._mem_next_id("_cs_ticket_seq")

    async def next_reply_id(self) -> int:
        """生成处理记录ID"""
        if is_redis_mode():
            return await self._redis_next_id("ticket_reply")
        return self._mem_next_id("_cs_ticket_reply_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("ticket", entity, "seq"))

    def generate_ticket_no(self) -> str:
        """生成工单号: GD+时间戳+序号(文档: GD+时间戳)"""
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        seq = self.store.get("_cs_ticket_seq", 0) + 1
        return f"GD{ts}{seq:04d}"

    # ============================================================
    # 工单 CRUD
    # ============================================================

    async def create_ticket(self, ticket: dict) -> str:
        """新增工单(返回工单号)"""
        if is_redis_mode():
            await self._redis_create_ticket(ticket)
        else:
            self._mem_create_ticket(ticket)
        return ticket["ticketNo"]

    async def get_ticket(self, ticket_no: str) -> dict | None:
        """按工单号查询"""
        if is_redis_mode():
            return await self._redis_get_ticket(ticket_no)
        return self._mem_get_ticket(ticket_no)

    async def update_ticket(self, ticket_no: str, updates: dict) -> None:
        """更新工单字段"""
        if is_redis_mode():
            await self._redis_update_ticket(ticket_no, updates)
        else:
            self._mem_update_ticket(ticket_no, updates)

    async def list_tickets(self, status: str = None, ticket_type: str = None,
                            priority: str = None, user_id: int = None,
                            limit: int = 50) -> list[dict]:
        """查询工单列表(按创建时间倒序, 支持筛选)"""
        if is_redis_mode():
            return await self._redis_list_tickets(status, ticket_type,
                                                   priority, user_id, limit)
        return self._mem_list_tickets(status, ticket_type, priority,
                                       user_id, limit)

    # ============================================================
    # 处理记录 CRUD
    # ============================================================

    async def add_reply(self, reply: dict) -> int:
        """新增处理记录(返回记录ID)"""
        if is_redis_mode():
            await self._redis_add_reply(reply)
        else:
            self._mem_add_reply(reply)
        return reply["id"]

    async def list_replies(self, ticket_no: str) -> list[dict]:
        """查询工单处理记录(按时间正序)"""
        if is_redis_mode():
            return await self._redis_list_replies(ticket_no)
        return self._mem_list_replies(ticket_no)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含工单模块的键(懒初始化)"""
        if "cs_tickets" not in self.store:
            self.store["cs_tickets"] = {}                    # ticketNo → ticket
            self.store["cs_ticket_replies"] = {}              # replyId → reply
            self.store["cs_ticket_replies_by_ticket"] = {}    # ticketNo → [replyId]
            self.store["_cs_ticket_seq"] = 0
            self.store["_cs_ticket_reply_seq"] = 0

    def _mem_create_ticket(self, ticket: dict) -> None:
        self._ensure_store()
        self.store["cs_tickets"][ticket["ticketNo"]] = ticket

    def _mem_get_ticket(self, ticket_no: str) -> dict | None:
        self._ensure_store()
        return self.store["cs_tickets"].get(ticket_no)

    def _mem_update_ticket(self, ticket_no: str, updates: dict) -> None:
        self._ensure_store()
        ticket = self.store["cs_tickets"].get(ticket_no)
        if ticket:
            ticket.update(updates)

    def _mem_list_tickets(self, status: str = None, ticket_type: str = None,
                           priority: str = None, user_id: int = None,
                           limit: int = 50) -> list[dict]:
        self._ensure_store()
        tickets = list(self.store["cs_tickets"].values())
        if status:
            tickets = [t for t in tickets if t.get("status") == status]
        if ticket_type:
            tickets = [t for t in tickets if t.get("type") == ticket_type]
        if priority:
            tickets = [t for t in tickets if t.get("priority") == priority]
        if user_id is not None:
            tickets = [t for t in tickets
                       if t.get("userId") == user_id]
        tickets.sort(key=lambda t: t.get("createdAt", ""), reverse=True)
        return tickets[:limit]

    def _mem_add_reply(self, reply: dict) -> None:
        self._ensure_store()
        reply_id = reply["id"]
        ticket_no = reply.get("ticketNo")
        self.store["cs_ticket_replies"][reply_id] = reply
        if ticket_no:
            self.store["cs_ticket_replies_by_ticket"].setdefault(
                ticket_no, []).append(reply_id)

    def _mem_list_replies(self, ticket_no: str) -> list[dict]:
        self._ensure_store()
        ids = self.store["cs_ticket_replies_by_ticket"].get(ticket_no, [])
        replies = [self.store["cs_ticket_replies"][rid] for rid in ids
                   if rid in self.store["cs_ticket_replies"]]
        replies.sort(key=lambda r: r.get("createdAt", ""))
        return replies

    # ============================================================
    # Redis 模式实现
    # ============================================================

    async def _redis_create_ticket(self, ticket: dict) -> None:
        client = await get_redis_client()
        await client.set(_k("ticket", "ticket", ticket["ticketNo"]),
                         json.dumps(ticket, ensure_ascii=False))

    async def _redis_get_ticket(self, ticket_no: str) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("ticket", "ticket", ticket_no))
        return json.loads(data) if data else None

    async def _redis_update_ticket(self, ticket_no: str, updates: dict) -> None:
        client = await get_redis_client()
        data = await client.get(_k("ticket", "ticket", ticket_no))
        if data:
            ticket = json.loads(data)
            ticket.update(updates)
            await client.set(_k("ticket", "ticket", ticket_no),
                             json.dumps(ticket, ensure_ascii=False))

    async def _redis_list_tickets(self, status: str = None,
                                   ticket_type: str = None,
                                   priority: str = None,
                                   user_id: int = None,
                                   limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("ticket", "ticket", "*"))
        tickets = []
        for key in keys:
            data = await client.get(key)
            if not data:
                continue
            t = json.loads(data)
            if status and t.get("status") != status:
                continue
            if ticket_type and t.get("type") != ticket_type:
                continue
            if priority and t.get("priority") != priority:
                continue
            if user_id is not None and t.get("userId") != user_id:
                continue
            tickets.append(t)
        tickets.sort(key=lambda t: t.get("createdAt", ""), reverse=True)
        return tickets[:limit]

    async def _redis_add_reply(self, reply: dict) -> None:
        client = await get_redis_client()
        reply_id = reply["id"]
        ticket_no = reply.get("ticketNo", "")
        await client.set(_k("ticket", "reply", reply_id),
                         json.dumps(reply, ensure_ascii=False))
        if ticket_no:
            await client.lpush(_k("ticket", "replies_by_ticket", ticket_no),
                               reply_id)

    async def _redis_list_replies(self, ticket_no: str) -> list[dict]:
        client = await get_redis_client()
        ids = await client.lrange(
            _k("ticket", "replies_by_ticket", ticket_no), 0, -1)
        replies = []
        for rid in ids:
            data = await client.get(_k("ticket", "reply", rid))
            if data:
                replies.append(json.loads(data))
        replies.sort(key=lambda r: r.get("createdAt", ""))
        return replies
