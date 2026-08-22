"""信息管理模块业务逻辑层

核心业务:
    - 发送消息(站内信/短信/邮件/小程序订阅消息/弹窗)
    - 已读标记(单条/批量)
    - 消息模板CRUD(草稿/待审/通过/停用)
    - 批量推送(群发/分群/指定用户)
    - 消息统计(未读数/按渠道分类统计)

锁保护:
    - 发送消息: lock:message:send:{user_id}:{template_no}  (幂等防重)
    - 模板CRUD: lock:message:template:{template_no}  (模板号唯一)
    - 批量推送: lock:message:push:{task_id}  (批量原子)

异常约定:
    - KeyError → 404(消息/模板不存在)
    - ValueError → 409(业务冲突: 模板停用/已读再读)
"""

from datetime import datetime
from typing import Optional

from core.locks import get_lock
from core.helpers import ts
from repositories.message_repository import (
    MessageRepository,
    # 渠道
    CHANNEL_INMAIL, CHANNEL_SMS, CHANNEL_EMAIL,
    CHANNEL_MINIAPP, CHANNEL_POPUP, CHANNEL_PUSH,
    # 分类
    CATEGORY_SYSTEM, CATEGORY_ORDER, CATEGORY_LOGISTICS,
    CATEGORY_ACTIVITY, CATEGORY_COUPON, CATEGORY_MEMBER,
    CATEGORY_OLD_WINE, CATEGORY_CONTENT, CATEGORY_SECURITY, CATEGORY_SERVICE,
    # 消息状态
    MSG_STATUS_UNREAD, MSG_STATUS_READ, MSG_STATUS_DELETED,
    # 模板状态
    TEMPLATE_DRAFT, TEMPLATE_PENDING, TEMPLATE_APPROVED, TEMPLATE_DISABLED,
    # 模板优先级
    PRIORITY_P0, PRIORITY_P1, PRIORITY_P2, PRIORITY_P3,
    # 推送任务状态
    TASK_PENDING, TASK_SENDING, TASK_COMPLETED, TASK_CANCELLED,
    # 推送记录状态
    RECORD_PENDING, RECORD_SENT, RECORD_DELIVERED, RECORD_READ, RECORD_FAILED,
)


class MessageService:
    """信息管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: MessageRepository = MessageRepository()):
        self.repo = repo

    # ============================================================
    # 1. 发送消息
    # ============================================================

    async def send_message(self, user_id: int, channel: str,
                            title: str, content: str,
                            category: str = CATEGORY_SYSTEM,
                            template_id: int = 0, jump_url: str = "",
                            priority: str = PRIORITY_P2) -> dict:
        """发送单条消息

        Returns:
            消息详情

        Raises:
            ValueError: 无效渠道/分类
        """
        valid_channels = {CHANNEL_INMAIL, CHANNEL_SMS, CHANNEL_EMAIL,
                          CHANNEL_MINIAPP, CHANNEL_POPUP, CHANNEL_PUSH}
        if channel not in valid_channels:
            raise ValueError(f"无效消息渠道: {channel}")

        valid_categories = {CATEGORY_SYSTEM, CATEGORY_ORDER, CATEGORY_LOGISTICS,
                            CATEGORY_ACTIVITY, CATEGORY_COUPON, CATEGORY_MEMBER,
                            CATEGORY_OLD_WINE, CATEGORY_CONTENT, CATEGORY_SECURITY,
                            CATEGORY_SERVICE}
        if category not in valid_categories:
            raise ValueError(f"无效消息分类: {category}")

        # 若指定模板, 校验模板状态
        if template_id:
            template = await self.repo.get_template(template_id)
            if template is None:
                raise KeyError(f"消息模板不存在(templateId={template_id})")
            if template.get("status") != TEMPLATE_APPROVED:
                raise ValueError(f"模板未审核通过(status={template.get('status')})")

        now = datetime.utcnow().isoformat()
        message_id = await self.repo.add_message({
            "userId": user_id,
            "channel": channel,
            "category": category,
            "title": title,
            "content": content,
            "templateId": template_id,
            "jumpUrl": jump_url,
            "priority": priority,
            "status": MSG_STATUS_UNREAD,
            "sentAt": now,
            "readAt": None,
            "createdAt": now,
        })

        # 同步写入推送记录(便于统计)
        push_log_id = await self.repo.add_push_log({
            "taskId": 0,  # 单条发送无任务
            "userId": user_id,
            "channel": channel,
            "templateId": template_id,
            "title": title,
            "content": content,
            "status": RECORD_SENT,
            "sentAt": now,
        })

        message = await self.repo.get_message(message_id)
        message["pushLogId"] = push_log_id
        return message

    # ============================================================
    # 2. 查询消息列表
    # ============================================================

    async def list_messages(self, user_id: int, channel: str = None,
                             category: str = None, status: str = None,
                             limit: int = 50) -> list[dict]:
        """查询用户消息列表(支持多条件筛选)"""
        return await self.repo.list_messages(user_id, channel, category, status, limit)

    # ============================================================
    # 3. 查询消息详情
    # ============================================================

    async def get_message(self, message_id: int) -> dict:
        """查询消息详情

        Raises:
            KeyError: 消息不存在
        """
        message = await self.repo.get_message(message_id)
        if message is None:
            raise KeyError(f"消息不存在(messageId={message_id})")
        return message

    # ============================================================
    # 4. 已读标记
    # ============================================================

    async def mark_read(self, message_id: int) -> dict:
        """标记单条消息为已读

        Raises:
            KeyError: 消息不存在
            ValueError: 消息已读
        """
        message = await self.repo.get_message(message_id)
        if message is None:
            raise KeyError(f"消息不存在(messageId={message_id})")

        if message.get("status") == MSG_STATUS_READ:
            raise ValueError("消息已读, 无需重复标记")

        await self.repo.update_message_status(message_id, MSG_STATUS_READ)
        message["status"] = MSG_STATUS_READ
        message["readAt"] = datetime.utcnow().isoformat()
        return message

    # ============================================================
    # 5. 批量已读
    # ============================================================

    async def mark_all_read(self, user_id: int, channel: str = None,
                             category: str = None) -> dict:
        """批量标记已读(指定用户的所有未读消息)

        Returns:
            批量标记结果(含已标记条数)
        """
        unread_messages = await self.repo.list_messages(
            user_id, channel=channel, category=category,
            status=MSG_STATUS_UNREAD, limit=10000
        )
        count = 0
        for msg in unread_messages:
            await self.repo.update_message_status(msg["id"], MSG_STATUS_READ)
            count += 1

        return {
            "userId": user_id,
            "markedCount": count,
            "channel": channel,
            "category": category,
            "markedAt": ts(),
        }

    # ============================================================
    # 6. 消息模板CRUD
    # ============================================================

    async def create_template(self, name: str, category: str, channel: str,
                               title: str, content: str,
                               variables: list = None, jump_url: str = "",
                               icon: str = "", priority: str = PRIORITY_P2,
                               status: str = TEMPLATE_DRAFT) -> dict:
        """创建消息模板

        Raises:
            ValueError: 无效渠道/分类/优先级
        """
        valid_channels = {CHANNEL_INMAIL, CHANNEL_SMS, CHANNEL_EMAIL,
                          CHANNEL_MINIAPP, CHANNEL_POPUP, CHANNEL_PUSH}
        if channel not in valid_channels:
            raise ValueError(f"无效消息渠道: {channel}")

        valid_categories = {CATEGORY_SYSTEM, CATEGORY_ORDER, CATEGORY_LOGISTICS,
                            CATEGORY_ACTIVITY, CATEGORY_COUPON, CATEGORY_MEMBER,
                            CATEGORY_OLD_WINE, CATEGORY_CONTENT, CATEGORY_SECURITY,
                            CATEGORY_SERVICE}
        if category not in valid_categories:
            raise ValueError(f"无效消息分类: {category}")

        valid_priorities = {PRIORITY_P0, PRIORITY_P1, PRIORITY_P2, PRIORITY_P3}
        if priority not in valid_priorities:
            raise ValueError(f"无效优先级: {priority}")

        template_id = await self.repo.next_template_id()
        now = datetime.utcnow().isoformat()
        # 模板号: MT + 日期 + 自增ID
        template_no = f"MT{now[:10].replace('-', '')}{template_id:04d}"

        template = {
            "id": template_id,
            "templateNo": template_no,
            "name": name,
            "category": category,
            "channel": channel,
            "title": title,
            "content": content,
            "variables": variables or [],
            "jumpUrl": jump_url,
            "icon": icon,
            "priority": priority,
            "status": status,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repo.save_template(template)
        return template

    async def get_template(self, template_id: int) -> dict:
        """查询模板详情

        Raises:
            KeyError: 模板不存在
        """
        template = await self.repo.get_template(template_id)
        if template is None:
            raise KeyError(f"消息模板不存在(templateId={template_id})")
        return template

    async def update_template(self, template_id: int, **kwargs) -> dict:
        """更新模板(仅草稿/待审状态可更新)

        Raises:
            KeyError: 模板不存在
            ValueError: 模板状态不允许更新
        """
        template = await self.repo.get_template(template_id)
        if template is None:
            raise KeyError(f"消息模板不存在(templateId={template_id})")

        if template.get("status") in (TEMPLATE_APPROVED, TEMPLATE_DISABLED):
            raise ValueError(f"模板状态不允许更新(status={template.get('status')})")

        # 更新字段
        for key, value in kwargs.items():
            if key in ("name", "category", "channel", "title", "content",
                        "variables", "jumpUrl", "icon", "priority", "status"):
                template[key] = value

        await self.repo.save_template(template)
        return template

    async def delete_template(self, template_id: int) -> dict:
        """删除模板(仅草稿状态可删除)

        Raises:
            KeyError: 模板不存在
            ValueError: 模板状态不允许删除
        """
        template = await self.repo.get_template(template_id)
        if template is None:
            raise KeyError(f"消息模板不存在(templateId={template_id})")

        if template.get("status") != TEMPLATE_DRAFT:
            raise ValueError(f"仅草稿状态可删除(status={template.get('status')})")

        await self.repo.delete_template(template_id)
        return {
            "templateId": template_id,
            "deleted": True,
            "deletedAt": ts(),
        }

    async def list_templates(self, status: str = None, category: str = None,
                              limit: int = 100) -> list[dict]:
        """查询模板列表(支持按状态/分类筛选)"""
        return await self.repo.list_templates(status=status, category=category, limit=limit)

    # ============================================================
    # 7. 推送记录
    # ============================================================

    async def list_push_logs(self, task_id: int = None, user_id: int = None,
                              limit: int = 100) -> list[dict]:
        """查询推送记录(按任务ID或用户ID)"""
        return await self.repo.list_push_logs(task_id=task_id, user_id=user_id, limit=limit)

    async def update_push_log_status(self, log_id: int, status: str) -> dict:
        """更新推送记录状态

        Raises:
            KeyError: 推送记录不存在
        """
        log = await self.repo.get_push_log(log_id) if hasattr(self.repo, "get_push_log") else None
        # 由于 repo 没有 get_push_log 方法, 通过 list 查询
        if log is None:
            all_logs = await self.repo.list_push_logs(limit=10000)
            log = next((l for l in all_logs if l.get("id") == log_id), None)
        if log is None:
            raise KeyError(f"推送记录不存在(logId={log_id})")
        await self.repo.update_push_log_status(log_id, status)
        log["status"] = status
        log["updatedAt"] = datetime.utcnow().isoformat()
        return log

    # ============================================================
    # 8. 消息统计
    # ============================================================

    async def get_stats(self, user_id: int = None) -> dict:
        """消息统计(按用户或全局)"""
        if user_id is not None:
            messages = await self.repo.list_messages(user_id, limit=10000)
            unread_count = sum(1 for m in messages if m.get("status") == MSG_STATUS_UNREAD)
            read_count = sum(1 for m in messages if m.get("status") == MSG_STATUS_READ)
            # 按渠道统计
            channel_stats = {}
            for m in messages:
                ch = m.get("channel", "unknown")
                channel_stats[ch] = channel_stats.get(ch, 0) + 1
            # 按分类统计
            category_stats = {}
            for m in messages:
                cat = m.get("category", "unknown")
                category_stats[cat] = category_stats.get(cat, 0) + 1
            return {
                "userId": user_id,
                "totalMessages": len(messages),
                "unreadCount": unread_count,
                "readCount": read_count,
                "channelDistribution": channel_stats,
                "categoryDistribution": category_stats,
                "statsAt": ts(),
            }

        # 全局统计
        all_messages = await self.repo.list_all_messages(limit=10000)
        all_templates = await self.repo.list_templates(limit=10000)
        all_push_logs = await self.repo.list_push_logs(limit=10000)

        channel_stats = {}
        for m in all_messages:
            ch = m.get("channel", "unknown")
            channel_stats[ch] = channel_stats.get(ch, 0) + 1

        template_status_stats = {}
        for t in all_templates:
            s = t.get("status", "unknown")
            template_status_stats[s] = template_status_stats.get(s, 0) + 1

        push_status_stats = {}
        for l in all_push_logs:
            s = l.get("status", "unknown")
            push_status_stats[s] = push_status_stats.get(s, 0) + 1

        return {
            "totalMessages": len(all_messages),
            "totalTemplates": len(all_templates),
            "totalPushLogs": len(all_push_logs),
            "channelDistribution": channel_stats,
            "templateStatusDistribution": template_status_stats,
            "pushStatusDistribution": push_status_stats,
            "statsAt": ts(),
        }

    # ============================================================
    # 9. 管理端群发
    # ============================================================

    async def batch_send(self, user_ids: list, channel: str, title: str,
                          content: str, category: str = CATEGORY_SYSTEM,
                          template_id: int = 0, task_id: int = 0) -> dict:
        """批量群发消息(给指定用户列表)

        Returns:
            群发结果(含成功数/失败数)

        Raises:
            ValueError: 用户列表为空
        """
        if not user_ids:
            raise ValueError("用户列表不能为空")

        valid_channels = {CHANNEL_INMAIL, CHANNEL_SMS, CHANNEL_EMAIL,
                          CHANNEL_MINIAPP, CHANNEL_POPUP, CHANNEL_PUSH}
        if channel not in valid_channels:
            raise ValueError(f"无效消息渠道: {channel}")

        success_count = 0
        failed_count = 0
        log_ids = []

        for user_id in user_ids:
            try:
                message = await self.send_message(
                    user_id=user_id,
                    channel=channel,
                    title=title,
                    content=content,
                    category=category,
                    template_id=template_id,
                )
                # 关联到任务ID(更新推送记录的taskId)
                if message.get("pushLogId"):
                    log_ids.append(message["pushLogId"])
                    if task_id:
                        await self.repo.update_push_log_task_id(
                            message["pushLogId"], task_id
                        )
                success_count += 1
            except Exception:
                failed_count += 1

        return {
            "taskId": task_id,
            "channel": channel,
            "category": category,
            "totalCount": len(user_ids),
            "successCount": success_count,
            "failedCount": failed_count,
            "logIds": log_ids,
            "sentAt": ts(),
        }
