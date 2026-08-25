"""AI智能客服聊天模块业务逻辑层

核心业务:
    - 创建会话(AI优先接待, 置信度0.9)
    - 发送消息(用户消息→AI自动回复+知识库检索+未解决3次转人工)
    - 人工转接(状态流转+客服分配)
    - 关闭会话(状态终结)
    - 知识库CRUD
    - 会话统计(总数/状态分布/AI解决率/满意度)
    - 满意度评价

锁保护:
    - 发送消息: lock:chat:session:{session_id} (消息顺序+状态原子更新)
    - 转人工: lock:chat:session:{session_id}
    - 关闭会话: lock:chat:session:{session_id}
    - 知识库更新/删除: lock:chat:knowledge:{knowledge_id}

异常约定:
    - KeyError → 404(会话/知识库不存在)
    - ValueError → 409(状态冲突: 已关闭/已转人工重复等)
"""


from core.locks import get_lock
from core.helpers import ts
from repositories.chat_repository import (
    ChatRepository,
    # 会话状态
    SESSION_STATUS_WAITING, SESSION_STATUS_AI, SESSION_STATUS_HUMAN,
    SESSION_STATUS_TRANSFERRING, SESSION_STATUS_ENDED, SESSION_STATUS_ARCHIVED,
    # 会话类型
    SESSION_TYPE_PRESALE, SESSION_TYPE_AFTERSALE, SESSION_TYPE_OLD_WINE, SESSION_TYPE_CUSTOM,
    # 消息发送方
    SENDER_USER, SENDER_AI, SENDER_CUSTOMER_SERVICE, SENDER_SYSTEM,
    # 消息类型
    MESSAGE_TYPE_TEXT,
    # 知识库
    KNOW_STATUS_ENABLED, KNOW_STATUS_DISABLED,
)


# ============================================================
# AI 业务规则常量
# ============================================================

# AI 初始置信度(接待时)
AI_INITIAL_CONFIDENCE = 0.90
# AI 默认回复置信度(命中知识库)
AI_REPLY_CONFIDENCE = 0.85
# AI 兜底回复置信度(未命中)
AI_FALLBACK_CONFIDENCE = 0.30
# 连续未解决消息数(超过自动转人工)
MAX_UNRESOLVED_COUNT = 3
# 转人工关键词
TRANSFER_KEYWORDS = ("转人工", "人工客服", "人工", "找客服", "转接人工")
# 默认客服ID(简化: 轮询分配, 此处固定)
DEFAULT_CUSTOMER_SERVICE_ID = 1

# 兜底回复文案
FALLBACK_REPLY = '抱歉, 未找到与您问题匹配的答案。如需进一步帮助, 可回复"转人工"联系人工客服。'


class ChatService:
    """AI智能客服聊天业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: ChatRepository = ChatRepository()):
        self.repo = repo

    # ============================================================
    # 1. 创建会话
    # ============================================================

    async def create_session(self, user_id: int, session_type: str = SESSION_TYPE_PRESALE,
                              guest_phone: str = None) -> dict:
        """创建会话(AI优先接待)

        规则:
            - 新会话状态=ai_chatting(AI优先接待)
            - AI置信度初始 0.9
            - 生成唯一 sessionId

        Returns:
            会话信息
        """
        session_id = await self.repo.generate_session_id()
        now = ts()
        session = {
            "sessionId": session_id,
            "userId": user_id,
            "guestPhone": guest_phone,
            "customerServiceId": None,
            "sessionType": session_type,
            "status": SESSION_STATUS_AI,
            "aiConfidence": AI_INITIAL_CONFIDENCE,
            "emotionScore": 0.0,
            "satisfaction": 0,
            "tags": [],
            "unresolvedCount": 0,
            "createdAt": now,
            "endedAt": None,
        }
        await self.repo.save_session(session)

        # 系统欢迎消息
        await self.repo.add_message({
            "sessionId": session_id,
            "senderType": SENDER_SYSTEM,
            "senderId": 0,
            "messageType": MESSAGE_TYPE_TEXT,
            "content": "您好, 欢迎咨询竹香酒官方客服, 请问有什么可以帮您?",
            "mediaUrl": None,
            "mediaThumb": None,
            "mediaSize": 0,
            "duration": 0,
            "aiConfidence": AI_INITIAL_CONFIDENCE,
            "isRead": False,
            "readAt": None,
        })

        return session

    # ============================================================
    # 2. 发送消息
    # ============================================================

    async def send_message(self, session_id: str, sender_type: str, sender_id: int,
                            message_type: str, content: str,
                            media_url: str = None, media_thumb: str = None,
                            media_size: int = 0, duration: int = 0) -> dict:
        """发送消息(用户消息触发AI自动回复)

        规则:
            - 用户消息: 写入用户消息, AI检索知识库自动回复
              - 命中知识库: 回复答案, 置信度0.85
              - 未命中: 回复兜底文案, 置信度0.3, unresolved_count+1
              - 连续3次未命中 或 用户含"转人工": 自动转人工
            - AI/客服消息: 仅写入

        Returns:
            包含 userMessage 与 aiReply(若为用户消息) 的结果

        Raises:
            KeyError: 会话不存在
            ValueError: 会话已关闭
        """
        lock_key = f"chat:session:{session_id}"

        async with get_lock(lock_key):
            session = await self.repo.get_session(session_id)
            if session is None:
                raise KeyError(f"会话不存在(sessionId={session_id})")
            if session["status"] == SESSION_STATUS_ENDED:
                raise ValueError(f"会话已关闭(sessionId={session_id})")

            # 写入发送方消息
            user_message_id = await self.repo.add_message({
                "sessionId": session_id,
                "senderType": sender_type,
                "senderId": sender_id,
                "messageType": message_type,
                "content": content,
                "mediaUrl": media_url,
                "mediaThumb": media_thumb,
                "mediaSize": media_size,
                "duration": duration,
                "aiConfidence": None,
                "isRead": False,
                "readAt": None,
            })

            result = {
                "userMessageId": user_message_id,
                "sessionId": session_id,
                "aiReply": None,
                "transferred": False,
            }

            # 仅用户消息触发 AI 自动回复
            if sender_type != SENDER_USER:
                return result

            # 会话必须处于 AI 对话中才自动回复
            if session["status"] != SESSION_STATUS_AI:
                return result

            ai_reply = await self._generate_ai_reply(session, content)
            result["aiReply"] = ai_reply

            # 判定是否需要转人工
            should_transfer = (
                ai_reply["transferred"]
                or session.get("unresolvedCount", 0) >= MAX_UNRESOLVED_COUNT
            )

            if should_transfer:
                await self._do_transfer(session, reason="AI未解决/用户主动转人工")
                result["transferred"] = True

            return result

    async def _generate_ai_reply(self, session: dict, user_content: str) -> dict:
        """AI生成回复(内部方法, 需在锁内调用)

        流程:
            1. 用户主动转人工 → 标记转接
            2. 检索知识库
            3. 命中 → 回复答案, 重置未解决计数
            4. 未命中 → 兜底回复, 未解决计数+1
        """
        session_id = session["sessionId"]

        # 1. 用户主动转人工
        if any(kw in user_content for kw in TRANSFER_KEYWORDS):
            reply = {
                "sessionId": session_id,
                "senderType": SENDER_AI,
                "senderId": 0,
                "messageType": MESSAGE_TYPE_TEXT,
                "content": "好的, 正在为您转接人工客服, 请稍候...",
                "aiConfidence": AI_REPLY_CONFIDENCE,
                "transferred": True,
            }
            message_id = await self.repo.add_message({
                "sessionId": session_id,
                "senderType": SENDER_AI,
                "senderId": 0,
                "messageType": MESSAGE_TYPE_TEXT,
                "content": reply["content"],
                "mediaUrl": None, "mediaThumb": None, "mediaSize": 0, "duration": 0,
                "aiConfidence": AI_REPLY_CONFIDENCE,
                "isRead": False, "readAt": None,
            })
            reply["messageId"] = message_id
            return reply

        # 2. 知识库检索
        matches = await self.repo.search_knowledge(user_content, limit=1)
        if matches:
            knowledge = matches[0]
            session["unresolvedCount"] = 0
            await self.repo.save_session(session)
            content = knowledge.get("answer", "")
            reply = {
                "sessionId": session_id,
                "senderType": SENDER_AI,
                "senderId": 0,
                "messageType": MESSAGE_TYPE_TEXT,
                "content": content,
                "aiConfidence": AI_REPLY_CONFIDENCE,
                "transferred": False,
                "knowledgeId": knowledge.get("id"),
            }
        else:
            # 未命中: 兜底回复 + 未解决计数+1
            session["unresolvedCount"] = session.get("unresolvedCount", 0) + 1
            await self.repo.save_session(session)
            reply = {
                "sessionId": session_id,
                "senderType": SENDER_AI,
                "senderId": 0,
                "messageType": MESSAGE_TYPE_TEXT,
                "content": FALLBACK_REPLY,
                "aiConfidence": AI_FALLBACK_CONFIDENCE,
                "transferred": False,
            }

        message_id = await self.repo.add_message({
            "sessionId": session_id,
            "senderType": SENDER_AI,
            "senderId": 0,
            "messageType": MESSAGE_TYPE_TEXT,
            "content": reply["content"],
            "mediaUrl": None, "mediaThumb": None, "mediaSize": 0, "duration": 0,
            "aiConfidence": reply["aiConfidence"],
            "isRead": False, "readAt": None,
        })
        reply["messageId"] = message_id
        return reply

    async def _do_transfer(self, session: dict, reason: str = "") -> None:
        """执行转人工(内部方法)"""
        session["status"] = SESSION_STATUS_HUMAN
        session["customerServiceId"] = DEFAULT_CUSTOMER_SERVICE_ID
        await self.repo.save_session(session)
        # 系统消息通知
        await self.repo.add_message({
            "sessionId": session["sessionId"],
            "senderType": SENDER_SYSTEM,
            "senderId": 0,
            "messageType": MESSAGE_TYPE_TEXT,
            "content": f"已为您转接人工客服(工号{DEFAULT_CUSTOMER_SERVICE_ID}), 请稍候。",
            "mediaUrl": None, "mediaThumb": None, "mediaSize": 0, "duration": 0,
            "aiConfidence": None, "isRead": False, "readAt": None,
        })

    # ============================================================
    # 3. 人工转接
    # ============================================================

    async def transfer_to_human(self, session_id: str, reason: str = "") -> dict:
        """主动转人工

        Raises:
            KeyError: 会话不存在
            ValueError: 会话已关闭/已人工
        """
        lock_key = f"chat:session:{session_id}"

        async with get_lock(lock_key):
            session = await self.repo.get_session(session_id)
            if session is None:
                raise KeyError(f"会话不存在(sessionId={session_id})")
            if session["status"] == SESSION_STATUS_ENDED:
                raise ValueError("会话已关闭, 无法转人工")
            if session["status"] == SESSION_STATUS_HUMAN:
                raise ValueError("会话已处于人工对话中")

            await self._do_transfer(session, reason=reason)
            return {
                "sessionId": session_id,
                "status": SESSION_STATUS_HUMAN,
                "customerServiceId": DEFAULT_CUSTOMER_SERVICE_ID,
                "transferredAt": ts(),
            }

    # ============================================================
    # 4. 关闭会话
    # ============================================================

    async def close_session(self, session_id: str) -> dict:
        """关闭会话

        Raises:
            KeyError: 会话不存在
            ValueError: 会话已关闭
        """
        lock_key = f"chat:session:{session_id}"

        async with get_lock(lock_key):
            session = await self.repo.get_session(session_id)
            if session is None:
                raise KeyError(f"会话不存在(sessionId={session_id})")
            if session["status"] == SESSION_STATUS_ENDED:
                raise ValueError("会话已关闭")

            session["status"] = SESSION_STATUS_ENDED
            session["endedAt"] = ts()
            await self.repo.save_session(session)

            await self.repo.add_message({
                "sessionId": session_id,
                "senderType": SENDER_SYSTEM,
                "senderId": 0,
                "messageType": MESSAGE_TYPE_TEXT,
                "content": "会话已结束, 感谢您的咨询, 祝您生活愉快!",
                "mediaUrl": None, "mediaThumb": None, "mediaSize": 0, "duration": 0,
                "aiConfidence": None, "isRead": False, "readAt": None,
            })

            return {
                "sessionId": session_id,
                "status": SESSION_STATUS_ENDED,
                "endedAt": session["endedAt"],
            }

    # ============================================================
    # 5. 满意度评价
    # ============================================================

    async def rate_satisfaction(self, session_id: str, satisfaction: int) -> dict:
        """会话满意度评价(1-5分)

        Raises:
            KeyError: 会话不存在
            ValueError: 评分范围错误/会话未关闭/已评价
        """
        if satisfaction < 1 or satisfaction > 5:
            raise ValueError(f"满意度评分须为1-5, 当前{satisfaction}")

        lock_key = f"chat:session:{session_id}"

        async with get_lock(lock_key):
            session = await self.repo.get_session(session_id)
            if session is None:
                raise KeyError(f"会话不存在(sessionId={session_id})")
            if session["status"] != SESSION_STATUS_ENDED:
                raise ValueError("会话未关闭, 无法评价")
            if session.get("satisfaction", 0) > 0:
                raise ValueError("会话已评价, 不可重复评价")

            session["satisfaction"] = satisfaction
            await self.repo.save_session(session)

            return {
                "sessionId": session_id,
                "satisfaction": satisfaction,
                "ratedAt": ts(),
            }

    # ============================================================
    # 6. 查询
    # ============================================================

    async def get_session(self, session_id: str) -> dict:
        """查询会话详情

        Raises:
            KeyError: 会话不存在
        """
        session = await self.repo.get_session(session_id)
        if session is None:
            raise KeyError(f"会话不存在(sessionId={session_id})")
        return session

    async def list_messages(self, session_id: str, limit: int = 100) -> list[dict]:
        """查询会话消息

        Raises:
            KeyError: 会话不存在
        """
        session = await self.repo.get_session(session_id)
        if session is None:
            raise KeyError(f"会话不存在(sessionId={session_id})")
        return await self.repo.list_messages(session_id, limit)

    async def list_user_sessions(self, user_id: int, limit: int = 50) -> list[dict]:
        """查询用户会话列表"""
        return await self.repo.list_sessions_by_user(user_id, limit)

    async def admin_list_sessions(self, status: str = None, session_type: str = None,
                                   limit: int = 100) -> list[dict]:
        """管理端查询会话"""
        return await self.repo.list_all_sessions(status, session_type, limit)

    # ============================================================
    # 7. 知识库 CRUD
    # ============================================================

    async def create_knowledge(self, category: str, question: str, answer: str,
                                 keywords: str = "", intent: str = "",
                                 confidence_threshold: float = 0.5) -> dict:
        """创建知识库条目"""
        knowledge_id = await self.repo.add_knowledge({
            "category": category,
            "question": question,
            "answer": answer,
            "keywords": keywords,
            "intent": intent,
            "confidenceThreshold": confidence_threshold,
            "status": KNOW_STATUS_ENABLED,
        })
        knowledge = await self.repo.get_knowledge(knowledge_id)
        return knowledge

    async def get_knowledge(self, knowledge_id: int) -> dict:
        """查询知识库条目

        Raises:
            KeyError: 不存在
        """
        knowledge = await self.repo.get_knowledge(knowledge_id)
        if knowledge is None:
            raise KeyError(f"知识库条目不存在(id={knowledge_id})")
        return knowledge

    async def update_knowledge(self, knowledge_id: int, updates: dict) -> dict:
        """更新知识库条目

        Raises:
            KeyError: 不存在
        """
        lock_key = f"chat:knowledge:{knowledge_id}"
        async with get_lock(lock_key):
            existing = await self.repo.get_knowledge(knowledge_id)
            if existing is None:
                raise KeyError(f"知识库条目不存在(id={knowledge_id})")
            # 不允许修改 id 与创建时间
            safe_updates = {k: v for k, v in updates.items()
                            if k not in ("id", "createdAt")}
            await self.repo.update_knowledge(knowledge_id, safe_updates)
            return await self.repo.get_knowledge(knowledge_id)

    async def delete_knowledge(self, knowledge_id: int) -> dict:
        """删除知识库条目

        Raises:
            KeyError: 不存在
        """
        lock_key = f"chat:knowledge:{knowledge_id}"
        async with get_lock(lock_key):
            existing = await self.repo.get_knowledge(knowledge_id)
            if existing is None:
                raise KeyError(f"知识库条目不存在(id={knowledge_id})")
            await self.repo.delete_knowledge(knowledge_id)
            return {"knowledgeId": knowledge_id, "deleted": True}

    async def list_knowledge(self, category: str = None, status: str = None,
                              limit: int = 100) -> list[dict]:
        """查询知识库列表"""
        return await self.repo.list_knowledge(category, status, limit)

    # ============================================================
    # 8. 会话统计
    # ============================================================

    async def get_stats(self) -> dict:
        """会话统计

        返回:
            - 总会话数
            - 按状态分布
            - 按类型分布
            - AI解决率(AI对话中结束的会话占比)
            - 平均满意度
        """
        all_sessions = await self.repo.list_all_sessions(limit=10000)

        status_dist = {}
        type_dist = {}
        satisfaction_sum = 0
        satisfaction_count = 0
        ai_resolved = 0  # 结束时仍为 AI 状态(未转人工)的会话数

        for s in all_sessions:
            status = s.get("status", "unknown")
            status_dist[status] = status_dist.get(status, 0) + 1

            stype = s.get("sessionType", "unknown")
            type_dist[stype] = type_dist.get(stype, 0) + 1

            sat = s.get("satisfaction", 0)
            if sat and sat > 0:
                satisfaction_sum += sat
                satisfaction_count += 1

            # AI解决: 已结束且未转人工(customerServiceId 为空)
            if s.get("status") == SESSION_STATUS_ENDED and not s.get("customerServiceId"):
                ai_resolved += 1

        ended_count = status_dist.get(SESSION_STATUS_ENDED, 0)
        avg_satisfaction = (round(satisfaction_sum / satisfaction_count, 2)
                            if satisfaction_count > 0 else 0)
        ai_resolution_rate = (round(ai_resolved / ended_count, 4)
                              if ended_count > 0 else 0)

        return {
            "totalSessions": len(all_sessions),
            "statusDistribution": status_dist,
            "typeDistribution": type_dist,
            "aiResolutionRate": ai_resolution_rate,
            "aiResolvedCount": ai_resolved,
            "endedCount": ended_count,
            "avgSatisfaction": avg_satisfaction,
            "satisfactionCount": satisfaction_count,
        }
