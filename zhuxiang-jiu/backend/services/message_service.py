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
    MSG_STATUS_UNREAD, MSG_STATUS_READ, TEMPLATE_DRAFT, TEMPLATE_APPROVED, TEMPLATE_DISABLED,
    # 模板优先级
    PRIORITY_P0, PRIORITY_P1, PRIORITY_P2, PRIORITY_P3,
    # 推送任务状态
    RECORD_SENT,
)


# ============================================================
# P1-5 防骚扰体系常量(设计文档 5.2 订阅规则 / 6.1 频率限制 / 6.2 时段控制)
# ============================================================

# 不可退订分类(交易/资金/安全/资产必需信息, 强制投递)
MANDATORY_CATEGORIES = frozenset({
    CATEGORY_ORDER, CATEGORY_LOGISTICS, CATEGORY_SECURITY,
    CATEGORY_OLD_WINE, CATEGORY_SYSTEM, CATEGORY_SERVICE,
})

# 可退订的营销分类
MARKETING_CATEGORIES = frozenset({
    CATEGORY_ACTIVITY, CATEGORY_COUPON, CATEGORY_MEMBER, CATEGORY_CONTENT,
})

# 营销分类每日单类上限(设计文档 6.1.1: 活动1/优惠2/会员1/资讯2)
CATEGORY_DAILY_LIMITS = {
    CATEGORY_ACTIVITY: 1, CATEGORY_COUPON: 2,
    CATEGORY_MEMBER: 1, CATEGORY_CONTENT: 2,
}

# 营销合计上限(每日 3 / 每周 10)
MARKETING_DAILY_TOTAL = 3
MARKETING_WEEKLY_TOTAL = 10

ALL_CHANNELS = [CHANNEL_INMAIL, CHANNEL_SMS, CHANNEL_EMAIL,
                CHANNEL_MINIAPP, CHANNEL_POPUP, CHANNEL_PUSH]
ALL_CATEGORIES = [CATEGORY_SYSTEM, CATEGORY_ORDER, CATEGORY_LOGISTICS,
                  CATEGORY_ACTIVITY, CATEGORY_COUPON, CATEGORY_MEMBER,
                  CATEGORY_OLD_WINE, CATEGORY_CONTENT, CATEGORY_SECURITY,
                  CATEGORY_SERVICE]


class MessageService:
    """信息管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: MessageRepository = MessageRepository()):
        self.repo = repo

    # ============================================================
    # 1b. 订阅偏好管理(P1-5 防骚扰体系, 设计文档 5.1/5.3)
    # ============================================================

    def _default_subscription(self, user_id: int) -> dict:
        """默认订阅偏好: 全渠道全分类订阅 + 默认静默时段与频率阈值"""
        return {
            "userId": user_id,
            "channels": list(ALL_CHANNELS),
            "categories": list(ALL_CATEGORIES),
            "silentStart": "22:00",
            "silentEnd": "08:00",
            "silentEnabled": True,
            "dailyLimit": MARKETING_DAILY_TOTAL,
            "weeklyLimit": MARKETING_WEEKLY_TOTAL,
            "updatedAt": ts(),
        }

    async def get_subscription(self, user_id: int) -> dict:
        """读取订阅偏好(无记录时返回默认值并落库)"""
        sub = await self.repo.get_subscription(user_id)
        if sub is None:
            sub = self._default_subscription(user_id)
            await self.repo.save_subscription(sub)
        return sub

    async def update_subscription(self, user_id: int, channels: list = None,
                                  categories: list = None,
                                  silent_start: str = None, silent_end: str = None,
                                  silent_enabled: bool = None,
                                  daily_limit: int = None,
                                  weekly_limit: int = None) -> dict:
        """更新订阅偏好(不可退订分类强制保留; 上限夹在 1~默认值之间)

        Raises:
            ValueError: 渠道/分类/时段格式非法
        """
        sub = await self.get_subscription(user_id)
        if channels is not None:
            invalid = set(channels) - set(ALL_CHANNELS)
            if invalid:
                raise ValueError(f"无效渠道: {sorted(invalid)}")
            sub["channels"] = list(channels) or []
        if categories is not None:
            invalid = set(categories) - set(ALL_CATEGORIES)
            if invalid:
                raise ValueError(f"无效分类: {sorted(invalid)}")
            # 不可退订分类强制保留(设计文档 5.2)
            effective = set(categories) | MANDATORY_CATEGORIES
            sub["categories"] = sorted(effective)
        if silent_start is not None:
            self._validate_hhmm(silent_start)
            sub["silentStart"] = silent_start
        if silent_end is not None:
            self._validate_hhmm(silent_end)
            sub["silentEnd"] = silent_end
        if silent_enabled is not None:
            sub["silentEnabled"] = bool(silent_enabled)
        if daily_limit is not None:
            sub["dailyLimit"] = max(1, min(int(daily_limit), MARKETING_DAILY_TOTAL))
        if weekly_limit is not None:
            sub["weeklyLimit"] = max(1, min(int(weekly_limit), MARKETING_WEEKLY_TOTAL))
        sub["updatedAt"] = ts()
        return await self.repo.save_subscription(sub)

    async def unsubscribe_all(self, user_id: int) -> dict:
        """一键退订全部营销信息(仅保留不可退订的必需通知, 设计文档 5.3)"""
        sub = await self.get_subscription(user_id)
        sub["categories"] = sorted(MANDATORY_CATEGORIES)
        sub["updatedAt"] = ts()
        return await self.repo.save_subscription(sub)

    @staticmethod
    def _validate_hhmm(value: str) -> None:
        if not isinstance(value, str) or len(value) != 5 \
                or value[2] != ":" or not value[:2].isdigit() or not value[3:].isdigit():
            raise ValueError(f"时段格式须为 HH:MM: {value}")
        if not (0 <= int(value[:2]) <= 23 and 0 <= int(value[3:]) <= 59):
            raise ValueError(f"时段超出范围: {value}")

    # ============================================================
    # 1c. 发送前防骚扰检查(四重调控, 设计文档 6.1.2)
    # ============================================================

    def _in_silent_window(self, sub: dict, now: datetime) -> bool:
        """当前是否处于静默时段(支持跨零点区间, 如 22:00-08:00)"""
        if not sub.get("silentEnabled", True):
            return False
        start = int(sub.get("silentStart", "22:00")[:2]) * 60 + int(sub.get("silentStart", "22:00")[3:])
        end = int(sub.get("silentEnd", "08:00")[:2]) * 60 + int(sub.get("silentEnd", "08:00")[3:])
        cur = now.hour * 60 + now.minute
        if start <= end:
            return start <= cur < end
        return cur >= start or cur < end   # 跨零点

    async def _check_send_allowed(self, user_id: int, channel: str,
                                   category: str, priority: str,
                                   now: datetime = None) -> dict:
        """发送前四重调控: 订阅 → 静默时段 → 频率

        Returns:
            {"allowed": bool, "reason": str}  拦截原因供调用方透出

        规则(设计文档 5.2/6.1/6.2):
            - 不可退订分类(交易/资金/安全/资产): 强制投递, 不受订阅/静默/频率限制
            - P0 紧急通知: 静默时段白名单放行(仍受订阅与频率约束)
            - 营销分类: 订阅检查 + 静默时段 + 单类每日上限 + 营销合计日/周上限
        """
        now = now or datetime.now()
        is_mandatory = category in MANDATORY_CATEGORIES
        sub = await self.get_subscription(user_id)

        # 1. 订阅检查(渠道 + 分类; 必需类强制放行)
        if not is_mandatory:
            if channel not in sub.get("channels", []):
                return {"allowed": False,
                        "reason": f"用户已退订渠道 {channel}"}
            if category not in sub.get("categories", []):
                return {"allowed": False,
                        "reason": f"用户已退订 {category} 类消息"}

        # 2. 静默时段(营销类; P0 紧急白名单放行, 设计文档 6.2.2)
        if not is_mandatory and priority != PRIORITY_P0 \
                and self._in_silent_window(sub, now):
            return {"allowed": False,
                    "reason": f"静默时段({sub['silentStart']}-{sub['silentEnd']}), "
                              f"营销消息延迟至活跃时段发送"}

        # 3. 频率(仅营销类; 按用户当日/当周已发送数)
        if not is_mandatory:
            messages = await self.repo.list_messages(
                user_id, limit=10000)
            today_str = now.strftime("%Y-%m-%d")
            week_key = now.isocalendar()[:2]
            day_total = week_total = 0
            day_category = 0
            for m in messages:
                if m.get("category") not in MARKETING_CATEGORIES:
                    continue
                sent = str(m.get("sentAt", ""))[:10]
                if sent != today_str:
                    continue
                day_total += 1
                if m.get("category") == category:
                    day_category += 1
                sent_dt = m.get("sentAt")
                if sent_dt:
                    try:
                        iso = datetime.fromisoformat(sent_dt)
                        if iso.isocalendar()[:2] == week_key:
                            week_total += 1
                    except (ValueError, TypeError):
                        pass
            cat_limit = CATEGORY_DAILY_LIMITS.get(category)
            if cat_limit is not None and day_category >= cat_limit:
                return {"allowed": False,
                        "reason": f"{category} 类今日已达上限({cat_limit} 条)"}
            if day_total >= sub.get("dailyLimit", MARKETING_DAILY_TOTAL):
                return {"allowed": False,
                        "reason": f"营销消息今日合计已达上限"
                                  f"({sub.get('dailyLimit')} 条)"}
            if week_total >= sub.get("weeklyLimit", MARKETING_WEEKLY_TOTAL):
                return {"allowed": False,
                        "reason": f"营销消息本周合计已达上限"
                                  f"({sub.get('weeklyLimit')} 条)"}
        return {"allowed": True, "reason": ""}

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

        # P1-5 防骚扰: 发送前四重调控(订阅/静默/频率; 必需类强制放行)
        gate = await self._check_send_allowed(user_id, channel, category, priority)
        if not gate["allowed"]:
            raise ValueError(f"发送被拦截: {gate['reason']}")

        # 本地时间: 与静默时段/频率检查同口径(用户视角), sentAt 前缀即发送日
        now = datetime.now().isoformat()
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
