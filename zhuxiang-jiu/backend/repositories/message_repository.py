"""信息管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    messages          - 消息表(站内信/短信/邮件/小程序订阅消息)
    message_templates - 消息模板表(模板CRUD/审核状态)
    push_logs         - 推送记录表(批量推送/送达/已读/退订)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 消息: 按 id 主键, user_id 索引(查询用户消息列表)
    - 模板: 按 id 主键, template_no 唯一
    - 推送记录: 按 task_id + user_id 索引
"""

import json
from datetime import datetime

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 消息渠道
# ============================================================

CHANNEL_INMAIL = "inmail"        # 站内信
CHANNEL_SMS = "sms"              # 短信
CHANNEL_EMAIL = "email"          # 邮件
CHANNEL_MINIAPP = "miniapp"      # 小程序订阅消息
CHANNEL_POPUP = "popup"          # 弹窗
CHANNEL_PUSH = "push"            # APP推送

# ============================================================
# 消息分类
# ============================================================

CATEGORY_SYSTEM = "system"       # 系统
CATEGORY_ORDER = "order"         # 订单
CATEGORY_LOGISTICS = "logistics"  # 物流
CATEGORY_ACTIVITY = "activity"   # 活动
CATEGORY_COUPON = "coupon"       # 优惠
CATEGORY_MEMBER = "member"       # 会员
CATEGORY_OLD_WINE = "old_wine"   # 老酒
CATEGORY_CONTENT = "content"     # 内容
CATEGORY_SECURITY = "security"  # 安全
CATEGORY_SERVICE = "service"    # 客服

# ============================================================
# 消息状态
# ============================================================

MSG_STATUS_UNREAD = "unread"     # 未读
MSG_STATUS_READ = "read"          # 已读
MSG_STATUS_DELETED = "deleted"    # 已删除

# 模板状态
TEMPLATE_DRAFT = "draft"          # 草稿
TEMPLATE_PENDING = "pending"      # 待审
TEMPLATE_APPROVED = "approved"    # 通过
TEMPLATE_DISABLED = "disabled"    # 停用

# 模板优先级
PRIORITY_P0 = "P0"   # 紧急
PRIORITY_P1 = "P1"   # 高
PRIORITY_P2 = "P2"   # 中
PRIORITY_P3 = "P3"   # 低

# 推送任务状态
TASK_PENDING = "pending"          # 待发送
TASK_SENDING = "sending"           # 发送中
TASK_COMPLETED = "completed"      # 已完成
TASK_CANCELLED = "cancelled"       # 已取消

# 推送记录状态
RECORD_PENDING = "pending"          # 待发
RECORD_SENT = "sent"                # 已发
RECORD_DELIVERED = "delivered"      # 送达
RECORD_READ = "read"                # 已读
RECORD_FAILED = "failed"             # 失败


class MessageRepository:
    """信息管理数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_message_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("message")
        return self._mem_next_id("_message_seq")

    async def next_template_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("message_template")
        return self._mem_next_id("_message_template_seq")

    async def next_push_log_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("message_push_log")
        return self._mem_next_id("_message_push_log_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("message", entity, "seq"))

    # ============================================================
    # 消息 CRUD
    # ============================================================

    async def add_message(self, message: dict) -> int:
        message_id = await self.next_message_id()
        message["id"] = message_id
        if "createdAt" not in message:
            message["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_message(message)
        else:
            self._mem_add_message(message)
        return message_id

    async def get_message(self, message_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get_message(message_id)
        return self._mem_get_message(message_id)

    async def update_message_status(self, message_id: int, status: str) -> None:
        if is_redis_mode():
            await self._redis_update_message_status(message_id, status)
        else:
            self._mem_update_message_status(message_id, status)

    async def list_messages(self, user_id: int, channel: str = None,
                             category: str = None, status: str = None,
                             limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_messages(user_id, channel, category, status, limit)
        return self._mem_list_messages(user_id, channel, category, status, limit)

    async def list_all_messages(self, channel: str = None, category: str = None,
                                 limit: int = 100) -> list[dict]:
        """查询所有消息(管理端, 不按用户筛选)"""
        if is_redis_mode():
            return await self._redis_list_all_messages(channel, category, limit)
        return self._mem_list_all_messages(channel, category, limit)

    async def count_unread(self, user_id: int) -> int:
        if is_redis_mode():
            return await self._redis_count_unread(user_id)
        return self._mem_count_unread(user_id)

    # ============================================================
    # 消息模板 CRUD
    # ============================================================

    async def get_template(self, template_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get_template(template_id)
        return self._mem_get_template(template_id)

    async def get_template_by_no(self, template_no: str) -> dict | None:
        if is_redis_mode():
            return await self._redis_get_template_by_no(template_no)
        return self._mem_get_template_by_no(template_no)

    async def save_template(self, template: dict) -> None:
        if is_redis_mode():
            await self._redis_save_template(template)
        else:
            self._mem_save_template(template)

    async def list_templates(self, status: str = None, category: str = None,
                             limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_templates(status, category, limit)
        return self._mem_list_templates(status, category, limit)

    async def delete_template(self, template_id: int) -> None:
        if is_redis_mode():
            await self._redis_delete_template(template_id)
        else:
            self._mem_delete_template(template_id)

    # ============================================================
    # 推送记录 CRUD
    # ============================================================

    async def add_push_log(self, log: dict) -> int:
        log_id = await self.next_push_log_id()
        log["id"] = log_id
        if "createdAt" not in log:
            log["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_push_log(log)
        else:
            self._mem_add_push_log(log)
        return log_id

    async def list_push_logs(self, task_id: int = None, user_id: int = None,
                              limit: int = 100) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_push_logs(task_id, user_id, limit)
        return self._mem_list_push_logs(task_id, user_id, limit)

    async def update_push_log_status(self, log_id: int, status: str) -> None:
        if is_redis_mode():
            await self._redis_update_push_log_status(log_id, status)
        else:
            self._mem_update_push_log_status(log_id, status)

    async def update_push_log_task_id(self, log_id: int, task_id: int) -> None:
        """更新推送记录关联的任务ID(批量群发场景)"""
        if is_redis_mode():
            await self._redis_update_push_log_task_id(log_id, task_id)
        else:
            self._mem_update_push_log_task_id(log_id, task_id)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        if "messages" not in self.store:
            self.store["messages"] = {}                          # id → message
            self.store["messages_by_user"] = {}                  # userId → [messageId, ...]
            self.store["message_templates"] = {}                 # id → template
            self.store["message_templates_by_no"] = {}           # templateNo → templateId
            self.store["message_push_logs"] = {}                 # id → pushLog
            self.store["message_push_logs_by_task"] = {}         # taskId → [pushLogId, ...]
            self.store["message_push_logs_by_user"] = {}         # userId → [pushLogId, ...]
            self.store["message_subscriptions"] = {}             # userId → 订阅偏好
            self.store["_message_seq"] = 0
            self.store["_message_template_seq"] = 0
            self.store["_message_push_log_seq"] = 0

    # ============================================================
    # 用户订阅偏好(P1-5 防骚扰体系, 设计文档 5.1/6.2/user_subscriptions 表)
    # ============================================================

    async def get_subscription(self, user_id: int) -> dict | None:
        """读取用户订阅偏好(无记录返回 None)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("message", "subscription", str(user_id)))
            return json.loads(raw) if raw else None
        self._ensure_store()
        sub = self.store["message_subscriptions"].get(user_id)
        return dict(sub) if sub else None

    async def save_subscription(self, sub: dict) -> dict:
        """保存用户订阅偏好(整体覆盖, 幂等)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("message", "subscription", str(sub["userId"])),
                             json.dumps(sub, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["message_subscriptions"][sub["userId"]] = dict(sub)
        return sub

    # --- 消息 ---

    def _mem_add_message(self, message: dict) -> None:
        self._ensure_store()
        message_id = message["id"]
        user_id = message.get("userId")
        self.store["messages"][message_id] = message
        if user_id:
            if user_id not in self.store["messages_by_user"]:
                self.store["messages_by_user"][user_id] = []
            self.store["messages_by_user"][user_id].append(message_id)

    def _mem_get_message(self, message_id: int) -> dict | None:
        self._ensure_store()
        return self.store["messages"].get(message_id)

    def _mem_update_message_status(self, message_id: int, status: str) -> None:
        self._ensure_store()
        msg = self.store["messages"].get(message_id)
        if msg:
            msg["status"] = status
            msg["updatedAt"] = datetime.utcnow().isoformat()
            if status == MSG_STATUS_READ:
                msg["readAt"] = datetime.utcnow().isoformat()

    def _mem_list_messages(self, user_id: int, channel: str = None,
                            category: str = None, status: str = None,
                            limit: int = 50) -> list[dict]:
        self._ensure_store()
        message_ids = self.store["messages_by_user"].get(user_id, [])
        messages = [self.store["messages"][mid] for mid in message_ids
                    if mid in self.store["messages"]]
        if channel:
            messages = [m for m in messages if m.get("channel") == channel]
        if category:
            messages = [m for m in messages if m.get("category") == category]
        if status:
            messages = [m for m in messages if m.get("status") == status]
        else:
            # 默认排除已删除
            messages = [m for m in messages if m.get("status") != MSG_STATUS_DELETED]
        # createdAt 相同时按 id 倒序兜底(避免时间戳精度不足导致顺序错乱)
        messages.sort(key=lambda m: (m.get("createdAt", ""), m.get("id", 0)), reverse=True)
        return messages[:limit]

    def _mem_list_all_messages(self, channel: str = None, category: str = None,
                                limit: int = 100) -> list[dict]:
        self._ensure_store()
        messages = list(self.store["messages"].values())
        if channel:
            messages = [m for m in messages if m.get("channel") == channel]
        if category:
            messages = [m for m in messages if m.get("category") == category]
        messages.sort(key=lambda m: m.get("createdAt", ""), reverse=True)
        return messages[:limit]

    def _mem_count_unread(self, user_id: int) -> int:
        self._ensure_store()
        message_ids = self.store["messages_by_user"].get(user_id, [])
        return sum(1 for mid in message_ids
                   if mid in self.store["messages"]
                   and self.store["messages"][mid].get("status") == MSG_STATUS_UNREAD)

    # --- 模板 ---

    def _mem_get_template(self, template_id: int) -> dict | None:
        self._ensure_store()
        return self.store["message_templates"].get(template_id)

    def _mem_get_template_by_no(self, template_no: str) -> dict | None:
        self._ensure_store()
        template_id = self.store["message_templates_by_no"].get(template_no)
        if template_id is None:
            return None
        return self.store["message_templates"].get(template_id)

    def _mem_save_template(self, template: dict) -> None:
        self._ensure_store()
        template_id = template["id"]
        template["updatedAt"] = datetime.utcnow().isoformat()
        self.store["message_templates"][template_id] = template
        if "templateNo" in template:
            self.store["message_templates_by_no"][template["templateNo"]] = template_id

    def _mem_list_templates(self, status: str = None, category: str = None,
                            limit: int = 100) -> list[dict]:
        self._ensure_store()
        templates = list(self.store["message_templates"].values())
        if status:
            templates = [t for t in templates if t.get("status") == status]
        if category:
            templates = [t for t in templates if t.get("category") == category]
        templates.sort(key=lambda t: t.get("createdAt", ""), reverse=True)
        return templates[:limit]

    def _mem_delete_template(self, template_id: int) -> None:
        self._ensure_store()
        template = self.store["message_templates"].pop(template_id, None)
        if template and "templateNo" in template:
            self.store["message_templates_by_no"].pop(template["templateNo"], None)

    # --- 推送记录 ---

    def _mem_add_push_log(self, log: dict) -> None:
        self._ensure_store()
        log_id = log["id"]
        task_id = log.get("taskId")
        user_id = log.get("userId")
        self.store["message_push_logs"][log_id] = log
        if task_id:
            if task_id not in self.store["message_push_logs_by_task"]:
                self.store["message_push_logs_by_task"][task_id] = []
            self.store["message_push_logs_by_task"][task_id].append(log_id)
        if user_id:
            if user_id not in self.store["message_push_logs_by_user"]:
                self.store["message_push_logs_by_user"][user_id] = []
            self.store["message_push_logs_by_user"][user_id].append(log_id)

    def _mem_list_push_logs(self, task_id: int = None, user_id: int = None,
                             limit: int = 100) -> list[dict]:
        self._ensure_store()
        if task_id:
            log_ids = self.store["message_push_logs_by_task"].get(task_id, [])
            logs = [self.store["message_push_logs"][lid] for lid in log_ids
                    if lid in self.store["message_push_logs"]]
        elif user_id:
            log_ids = self.store["message_push_logs_by_user"].get(user_id, [])
            logs = [self.store["message_push_logs"][lid] for lid in log_ids
                    if lid in self.store["message_push_logs"]]
        else:
            logs = list(self.store["message_push_logs"].values())
        logs.sort(key=lambda l: l.get("createdAt", ""), reverse=True)
        return logs[:limit]

    def _mem_update_push_log_status(self, log_id: int, status: str) -> None:
        self._ensure_store()
        log = self.store["message_push_logs"].get(log_id)
        if log:
            log["status"] = status
            log["updatedAt"] = datetime.utcnow().isoformat()

    def _mem_update_push_log_task_id(self, log_id: int, task_id: int) -> None:
        """内存模式: 更新推送记录的taskId并重建by_task索引"""
        self._ensure_store()
        log = self.store["message_push_logs"].get(log_id)
        if not log:
            return
        old_task_id = log.get("taskId")
        log["taskId"] = task_id
        log["updatedAt"] = datetime.utcnow().isoformat()
        # 从旧任务的索引中移除
        if old_task_id:
            old_list = self.store["message_push_logs_by_task"].get(old_task_id, [])
            if log_id in old_list:
                old_list.remove(log_id)
        # 添加到新任务的索引
        if task_id:
            if task_id not in self.store["message_push_logs_by_task"]:
                self.store["message_push_logs_by_task"][task_id] = []
            if log_id not in self.store["message_push_logs_by_task"][task_id]:
                self.store["message_push_logs_by_task"][task_id].append(log_id)

    # ============================================================
    # Redis 模式实现
    # ============================================================

    async def _redis_add_message(self, message: dict) -> None:
        client = await get_redis_client()
        message_id = message["id"]
        user_id = message.get("userId")
        await client.set(_k("message", "item", message_id),
                         json.dumps(message, ensure_ascii=False))
        if user_id:
            await client.lpush(_k("message", "by_user", user_id), message_id)

    async def _redis_get_message(self, message_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("message", "item", message_id))
        return json.loads(data) if data else None

    async def _redis_update_message_status(self, message_id: int, status: str) -> None:
        client = await get_redis_client()
        data = await client.get(_k("message", "item", message_id))
        if data:
            message = json.loads(data)
            message["status"] = status
            message["updatedAt"] = datetime.utcnow().isoformat()
            if status == MSG_STATUS_READ:
                message["readAt"] = datetime.utcnow().isoformat()
            await client.set(_k("message", "item", message_id),
                             json.dumps(message, ensure_ascii=False))

    async def _redis_list_messages(self, user_id: int, channel: str = None,
                                   category: str = None, status: str = None,
                                   limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        message_ids = await client.lrange(_k("message", "by_user", user_id), 0, -1)
        messages = []
        for mid in message_ids:
            data = await client.get(_k("message", "item", mid))
            if data:
                m = json.loads(data)
                if channel and m.get("channel") != channel:
                    continue
                if category and m.get("category") != category:
                    continue
                if status:
                    if m.get("status") != status:
                        continue
                elif m.get("status") == MSG_STATUS_DELETED:
                    continue
                messages.append(m)
        # createdAt 相同时按 id 倒序兜底(避免时间戳精度不足导致顺序错乱)
        messages.sort(key=lambda m: (m.get("createdAt", ""), m.get("id", 0)), reverse=True)
        return messages[:limit]

    async def _redis_list_all_messages(self, channel: str = None, category: str = None,
                                       limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("message", "item", "*"))
        messages = []
        for key in keys:
            data = await client.get(key)
            if data:
                m = json.loads(data)
                if channel and m.get("channel") != channel:
                    continue
                if category and m.get("category") != category:
                    continue
                messages.append(m)
        messages.sort(key=lambda m: m.get("createdAt", ""), reverse=True)
        return messages[:limit]

    async def _redis_count_unread(self, user_id: int) -> int:
        messages = await self._redis_list_messages(user_id, limit=10000)
        return sum(1 for m in messages if m.get("status") == MSG_STATUS_UNREAD)

    async def _redis_get_template(self, template_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("message", "template", template_id))
        return json.loads(data) if data else None

    async def _redis_get_template_by_no(self, template_no: str) -> dict | None:
        client = await get_redis_client()
        template_id = await client.get(_k("message", "template_by_no", template_no))
        if not template_id:
            return None
        data = await client.get(_k("message", "template", template_id))
        return json.loads(data) if data else None

    async def _redis_save_template(self, template: dict) -> None:
        client = await get_redis_client()
        template_id = template["id"]
        template["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("message", "template", template_id),
                         json.dumps(template, ensure_ascii=False))
        if "templateNo" in template:
            await client.set(_k("message", "template_by_no", template["templateNo"]), template_id)

    async def _redis_list_templates(self, status: str = None, category: str = None,
                                    limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("message", "template", "*"))
        templates = []
        for key in keys:
            if "template_by_no" in key:
                continue
            data = await client.get(key)
            if data:
                t = json.loads(data)
                if status and t.get("status") != status:
                    continue
                if category and t.get("category") != category:
                    continue
                templates.append(t)
        templates.sort(key=lambda t: t.get("createdAt", ""), reverse=True)
        return templates[:limit]

    async def _redis_delete_template(self, template_id: int) -> None:
        client = await get_redis_client()
        data = await client.get(_k("message", "template", template_id))
        if data:
            template = json.loads(data)
            await client.delete(_k("message", "template", template_id))
            if "templateNo" in template:
                await client.delete(_k("message", "template_by_no", template["templateNo"]))

    async def _redis_add_push_log(self, log: dict) -> None:
        client = await get_redis_client()
        log_id = log["id"]
        task_id = log.get("taskId")
        user_id = log.get("userId")
        await client.set(_k("message", "push_log", log_id),
                         json.dumps(log, ensure_ascii=False))
        if task_id:
            await client.lpush(_k("message", "push_logs_by_task", task_id), log_id)
        if user_id:
            await client.lpush(_k("message", "push_logs_by_user", user_id), log_id)

    async def _redis_list_push_logs(self, task_id: int = None, user_id: int = None,
                                    limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        if task_id:
            log_ids = await client.lrange(_k("message", "push_logs_by_task", task_id), 0, -1)
        elif user_id:
            log_ids = await client.lrange(_k("message", "push_logs_by_user", user_id), 0, -1)
        else:
            keys = await client.keys(_k("message", "push_log", "*"))
            log_ids = [k.split(":")[-1] for k in keys]
        logs = []
        for lid in log_ids:
            data = await client.get(_k("message", "push_log", lid))
            if data:
                logs.append(json.loads(data))
        logs.sort(key=lambda l: l.get("createdAt", ""), reverse=True)
        return logs[:limit]

    async def _redis_update_push_log_status(self, log_id: int, status: str) -> None:
        client = await get_redis_client()
        data = await client.get(_k("message", "push_log", log_id))
        if data:
            log = json.loads(data)
            log["status"] = status
            log["updatedAt"] = datetime.utcnow().isoformat()
            await client.set(_k("message", "push_log", log_id),
                             json.dumps(log, ensure_ascii=False))

    async def _redis_update_push_log_task_id(self, log_id: int, task_id: int) -> None:
        """Redis模式: 更新推送记录的taskId并重建by_task索引"""
        client = await get_redis_client()
        data = await client.get(_k("message", "push_log", log_id))
        if not data:
            return
        log = json.loads(data)
        old_task_id = log.get("taskId")
        log["taskId"] = task_id
        log["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("message", "push_log", log_id),
                         json.dumps(log, ensure_ascii=False))
        # 从旧任务的索引中移除
        if old_task_id:
            await client.lrem(_k("message", "push_logs_by_task", old_task_id), 0, log_id)
        # 添加到新任务的索引
        if task_id:
            await client.lpush(_k("message", "push_logs_by_task", task_id), log_id)
