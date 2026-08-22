"""AI智能客服聊天模块数据访问层(双模式: 内存 + Redis)

表清单:
    chat_sessions(会话表) + chat_messages(消息表) + chat_knowledge(知识库表)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 会话主键: session_id(字符串, CS+时间戳+序号)
    - 消息自增ID: 内存计数器 / Redis INCR
    - 会话状态: waiting/ai_chatting/human_chatting/transferring/ended/archived
"""

import json
from datetime import datetime
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 会话状态
# ============================================================

SESSION_STATUS_WAITING = "waiting"            # 排队等待
SESSION_STATUS_AI = "ai_chatting"             # AI对话中
SESSION_STATUS_HUMAN = "human_chatting"       # 人工对话中
SESSION_STATUS_TRANSFERRING = "transferring"  # 转接中
SESSION_STATUS_ENDED = "ended"                # 已结束
SESSION_STATUS_ARCHIVED = "archived"          # 已归档

# 会话类型
SESSION_TYPE_PRESALE = "presale"      # 售前
SESSION_TYPE_AFTERSALE = "aftersale"  # 售后
SESSION_TYPE_OLD_WINE = "old_wine"    # 老酒
SESSION_TYPE_CUSTOM = "custom"        # 定制

# 消息发送方
SENDER_USER = "user"                  # 用户
SENDER_AI = "ai"                       # AI
SENDER_CUSTOMER_SERVICE = "customer_service"  # 人工客服
SENDER_SYSTEM = "system"              # 系统

# 消息类型
MESSAGE_TYPE_TEXT = "text"
MESSAGE_TYPE_IMAGE = "image"
MESSAGE_TYPE_VIDEO = "video"
MESSAGE_TYPE_VOICE = "voice"
MESSAGE_TYPE_FILE = "file"
MESSAGE_TYPE_CARD = "card"
MESSAGE_TYPE_BUTTON = "button"

# 知识库分类
KNOW_CATEGORY_PRODUCT = "product"        # 产品
KNOW_CATEGORY_FAQ = "faq"                 # 常见问题
KNOW_CATEGORY_POLICY = "policy"            # 政策
KNOW_CATEGORY_ORDER = "order"             # 订单
KNOW_CATEGORY_ACTIVITY = "activity"       # 活动
KNOW_CATEGORY_COMPLIANCE = "compliance"    # 合规

KNOW_STATUS_ENABLED = "enabled"
KNOW_STATUS_DISABLED = "disabled"


# ============================================================
# 知识库匹配辅助(内存+Redis共用)
# ============================================================

def _knowledge_matches(knowledge: dict, query_lower: str) -> bool:
    """判定知识库条目是否命中用户查询

    命中条件(满足任一):
        - query 是 question 的子串, 或 question 是 query 的子串
        - 任一关键词 token 出现在 query 中
        - 任一 query token 出现在关键词中
    """
    if not query_lower:
        return False
    question = (knowledge.get("question") or "").lower()
    keywords = (knowledge.get("keywords") or "").lower()

    # 子串互含
    if query_lower in question or question in query_lower:
        return True
    # 关键词 token 命中 query
    for kkw in keywords.split():
        kkw = kkw.strip()
        if kkw and kkw in query_lower:
            return True
    # query token 命中关键词
    for qkw in query_lower.split():
        qkw = qkw.strip()
        if qkw and qkw in keywords:
            return True
    return False


class ChatRepository:
    """AI智能客服聊天数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_message_id(self) -> int:
        """生成消息ID"""
        if is_redis_mode():
            return await self._redis_next_id("message")
        return self._mem_next_id("_chat_message_seq")

    async def next_knowledge_id(self) -> int:
        """生成知识库ID"""
        if is_redis_mode():
            return await self._redis_next_id("knowledge")
        return self._mem_next_id("_chat_knowledge_seq")

    async def next_session_seq(self) -> int:
        """生成会话序号(用于 session_id 拼接)"""
        if is_redis_mode():
            return await self._redis_next_id("session")
        return self._mem_next_id("_chat_session_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("chat", entity, "seq"))

    async def generate_session_id(self) -> str:
        """生成会话ID: CS+时间戳+序号"""
        seq = await self.next_session_seq()
        ts = int(datetime.utcnow().timestamp() * 1000)
        return f"CS{ts}{seq:04d}"

    # ============================================================
    # 会话 CRUD
    # ============================================================

    async def get_session(self, session_id: str) -> Optional[dict]:
        """查询会话"""
        if is_redis_mode():
            return await self._redis_get_session(session_id)
        return self._mem_get_session(session_id)

    async def save_session(self, session: dict) -> None:
        """保存会话(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_session(session)
        else:
            self._mem_save_session(session)

    async def list_sessions_by_user(self, user_id: int, limit: int = 50) -> list[dict]:
        """查询用户会话(按创建时间倒序)"""
        if is_redis_mode():
            return await self._redis_list_sessions_by_user(user_id, limit)
        return self._mem_list_sessions_by_user(user_id, limit)

    async def list_all_sessions(self, status: str = None, session_type: str = None,
                                 limit: int = 100) -> list[dict]:
        """查询全部会话(管理端, 支持筛选)"""
        if is_redis_mode():
            return await self._redis_list_all_sessions(status, session_type, limit)
        return self._mem_list_all_sessions(status, session_type, limit)

    # ============================================================
    # 消息 CRUD
    # ============================================================

    async def add_message(self, message: dict) -> int:
        """新增消息(返回消息ID)"""
        message_id = await self.next_message_id()
        message["id"] = message_id
        if "createdAt" not in message:
            message["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_message(message)
        else:
            self._mem_add_message(message)
        return message_id

    async def get_message(self, message_id: int) -> Optional[dict]:
        """按ID查询消息"""
        if is_redis_mode():
            return await self._redis_get_message(message_id)
        return self._mem_get_message(message_id)

    async def list_messages(self, session_id: str, limit: int = 100) -> list[dict]:
        """查询会话消息(按时间正序)"""
        if is_redis_mode():
            return await self._redis_list_messages(session_id, limit)
        return self._mem_list_messages(session_id, limit)

    async def mark_read(self, session_id: str, sender_type: str) -> int:
        """将会话中某方消息标记已读, 返回更新条数"""
        if is_redis_mode():
            return await self._redis_mark_read(session_id, sender_type)
        return self._mem_mark_read(session_id, sender_type)

    # ============================================================
    # 知识库 CRUD
    # ============================================================

    async def add_knowledge(self, knowledge: dict) -> int:
        """新增知识库条目(返回ID)"""
        knowledge_id = await self.next_knowledge_id()
        knowledge["id"] = knowledge_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in knowledge:
            knowledge["createdAt"] = now
        knowledge["updatedAt"] = now
        if "status" not in knowledge:
            knowledge["status"] = KNOW_STATUS_ENABLED
        if is_redis_mode():
            await self._redis_add_knowledge(knowledge)
        else:
            self._mem_add_knowledge(knowledge)
        return knowledge_id

    async def get_knowledge(self, knowledge_id: int) -> Optional[dict]:
        """按ID查询知识库条目"""
        if is_redis_mode():
            return await self._redis_get_knowledge(knowledge_id)
        return self._mem_get_knowledge(knowledge_id)

    async def update_knowledge(self, knowledge_id: int, updates: dict) -> None:
        """更新知识库条目"""
        if is_redis_mode():
            await self._redis_update_knowledge(knowledge_id, updates)
        else:
            self._mem_update_knowledge(knowledge_id, updates)

    async def delete_knowledge(self, knowledge_id: int) -> None:
        """删除知识库条目"""
        if is_redis_mode():
            await self._redis_delete_knowledge(knowledge_id)
        else:
            self._mem_delete_knowledge(knowledge_id)

    async def list_knowledge(self, category: str = None, status: str = None,
                              limit: int = 100) -> list[dict]:
        """查询知识库(支持按分类/状态筛选)"""
        if is_redis_mode():
            return await self._redis_list_knowledge(category, status, limit)
        return self._mem_list_knowledge(category, status, limit)

    async def search_knowledge(self, query: str, category: str = None,
                                limit: int = 5) -> list[dict]:
        """知识库关键词检索(用于 AI 回复)

        匹配规则: question 或 keywords 包含 query 任一分词即命中
        """
        if is_redis_mode():
            return await self._redis_search_knowledge(query, category, limit)
        return self._mem_search_knowledge(query, category, limit)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含聊天模块的键(懒初始化)"""
        if "chat_sessions" not in self.store:
            self.store["chat_sessions"] = {}                    # sessionId → session
            self.store["chat_sessions_by_user"] = {}             # userId → [sessionId, ...]
            self.store["chat_messages"] = {}                    # messageId → message
            self.store["chat_messages_by_session"] = {}         # sessionId → [messageId, ...]
            self.store["chat_knowledge"] = {}                   # knowledgeId → knowledge
            self.store["_chat_message_seq"] = 0
            self.store["_chat_knowledge_seq"] = 0
            self.store["_chat_session_seq"] = 0

    # --- 会话 ---

    def _mem_get_session(self, session_id: str) -> Optional[dict]:
        self._ensure_store()
        return self.store["chat_sessions"].get(session_id)

    def _mem_save_session(self, session: dict) -> None:
        self._ensure_store()
        session_id = session["sessionId"]
        is_new = session_id not in self.store["chat_sessions"]
        self.store["chat_sessions"][session_id] = session
        if is_new:
            user_id = session.get("userId")
            if user_id is not None:
                if user_id not in self.store["chat_sessions_by_user"]:
                    self.store["chat_sessions_by_user"][user_id] = []
                self.store["chat_sessions_by_user"][user_id].append(session_id)

    def _mem_list_sessions_by_user(self, user_id: int, limit: int = 50) -> list[dict]:
        self._ensure_store()
        ids = self.store["chat_sessions_by_user"].get(user_id, [])
        sessions = [self.store["chat_sessions"][sid] for sid in ids
                    if sid in self.store["chat_sessions"]]
        sessions.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return sessions[:limit]

    def _mem_list_all_sessions(self, status: str = None, session_type: str = None,
                                limit: int = 100) -> list[dict]:
        self._ensure_store()
        sessions = list(self.store["chat_sessions"].values())
        if status:
            sessions = [s for s in sessions if s.get("status") == status]
        if session_type:
            sessions = [s for s in sessions if s.get("sessionType") == session_type]
        sessions.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return sessions[:limit]

    # --- 消息 ---

    def _mem_add_message(self, message: dict) -> None:
        self._ensure_store()
        message_id = message["id"]
        session_id = message["sessionId"]
        self.store["chat_messages"][message_id] = message
        if session_id not in self.store["chat_messages_by_session"]:
            self.store["chat_messages_by_session"][session_id] = []
        self.store["chat_messages_by_session"][session_id].append(message_id)

    def _mem_get_message(self, message_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["chat_messages"].get(message_id)

    def _mem_list_messages(self, session_id: str, limit: int = 100) -> list[dict]:
        self._ensure_store()
        ids = self.store["chat_messages_by_session"].get(session_id, [])
        msgs = [self.store["chat_messages"][mid] for mid in ids
                if mid in self.store["chat_messages"]]
        msgs.sort(key=lambda m: m.get("createdAt", ""))
        return msgs[:limit]

    def _mem_mark_read(self, session_id: str, sender_type: str) -> int:
        self._ensure_store()
        ids = self.store["chat_messages_by_session"].get(session_id, [])
        count = 0
        now = datetime.utcnow().isoformat()
        for mid in ids:
            msg = self.store["chat_messages"].get(mid)
            if msg and msg.get("senderType") == sender_type and not msg.get("isRead"):
                msg["isRead"] = True
                msg["readAt"] = now
                count += 1
        return count

    # --- 知识库 ---

    def _mem_add_knowledge(self, knowledge: dict) -> None:
        self._ensure_store()
        self.store["chat_knowledge"][knowledge["id"]] = knowledge

    def _mem_get_knowledge(self, knowledge_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["chat_knowledge"].get(knowledge_id)

    def _mem_update_knowledge(self, knowledge_id: int, updates: dict) -> None:
        self._ensure_store()
        knowledge = self.store["chat_knowledge"].get(knowledge_id)
        if knowledge:
            knowledge.update(updates)
            knowledge["updatedAt"] = datetime.utcnow().isoformat()

    def _mem_delete_knowledge(self, knowledge_id: int) -> None:
        self._ensure_store()
        self.store["chat_knowledge"].pop(knowledge_id, None)

    def _mem_list_knowledge(self, category: str = None, status: str = None,
                             limit: int = 100) -> list[dict]:
        self._ensure_store()
        items = list(self.store["chat_knowledge"].values())
        if category:
            items = [k for k in items if k.get("category") == category]
        if status:
            items = [k for k in items if k.get("status") == status]
        items.sort(key=lambda k: k.get("createdAt", ""), reverse=True)
        return items[:limit]

    def _mem_search_knowledge(self, query: str, category: str = None,
                               limit: int = 5) -> list[dict]:
        self._ensure_store()
        if not query:
            return []
        query_lower = query.lower()
        results = []
        for k in self.store["chat_knowledge"].values():
            if k.get("status") != KNOW_STATUS_ENABLED:
                continue
            if category and k.get("category") != category:
                continue
            if _knowledge_matches(k, query_lower):
                results.append(k)
        # 简单评分: question 含 query 优先(子串命中)
        results.sort(key=lambda k: (
            0 if query_lower in (k.get("question") or "").lower() else 1
        ))
        return results[:limit]

    # ============================================================
    # Redis 模式实现
    # ============================================================

    # --- 会话 ---

    async def _redis_get_session(self, session_id: str) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("chat", "session", session_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_session(self, session: dict) -> None:
        client = await get_redis_client()
        session_id = session["sessionId"]
        is_new = await client.exists(_k("chat", "session", session_id)) == 0
        await client.set(_k("chat", "session", session_id),
                         json.dumps(session, ensure_ascii=False))
        if is_new:
            user_id = session.get("userId")
            if user_id is not None:
                await client.lpush(_k("chat", "sessions_by_user", user_id), session_id)

    async def _redis_list_sessions_by_user(self, user_id: int, limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        ids = await client.lrange(_k("chat", "sessions_by_user", user_id), 0, limit - 1)
        result = []
        for sid in ids:
            data = await client.get(_k("chat", "session", sid))
            if data:
                result.append(json.loads(data))
        return result

    async def _redis_list_all_sessions(self, status: str = None, session_type: str = None,
                                        limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("chat", "session", "*"))
        sessions = []
        for key in keys:
            data = await client.get(key)
            if data:
                s = json.loads(data)
                if status and s.get("status") != status:
                    continue
                if session_type and s.get("sessionType") != session_type:
                    continue
                sessions.append(s)
        sessions.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return sessions[:limit]

    # --- 消息 ---

    async def _redis_add_message(self, message: dict) -> None:
        client = await get_redis_client()
        message_id = message["id"]
        session_id = message["sessionId"]
        await client.set(_k("chat", "message", message_id),
                         json.dumps(message, ensure_ascii=False))
        await client.lpush(_k("chat", "messages_by_session", session_id), message_id)

    async def _redis_get_message(self, message_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("chat", "message", message_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_messages(self, session_id: str, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        # lpush 写入为倒序, 取 limit 后反转
        ids = await client.lrange(_k("chat", "messages_by_session", session_id), 0, limit - 1)
        msgs = []
        for mid in ids:
            data = await client.get(_k("chat", "message", mid))
            if data:
                msgs.append(json.loads(data))
        msgs.sort(key=lambda m: m.get("createdAt", ""))
        return msgs

    async def _redis_mark_read(self, session_id: str, sender_type: str) -> int:
        client = await get_redis_client()
        ids = await client.lrange(_k("chat", "messages_by_session", session_id), 0, -1)
        count = 0
        now = datetime.utcnow().isoformat()
        for mid in ids:
            data = await client.get(_k("chat", "message", mid))
            if data:
                msg = json.loads(data)
                if msg.get("senderType") == sender_type and not msg.get("isRead"):
                    msg["isRead"] = True
                    msg["readAt"] = now
                    await client.set(_k("chat", "message", mid),
                                     json.dumps(msg, ensure_ascii=False))
                    count += 1
        return count

    # --- 知识库 ---

    async def _redis_add_knowledge(self, knowledge: dict) -> None:
        client = await get_redis_client()
        knowledge_id = knowledge["id"]
        await client.set(_k("chat", "knowledge", knowledge_id),
                         json.dumps(knowledge, ensure_ascii=False))
        await client.lpush(_k("chat", "knowledge_list"), knowledge_id)

    async def _redis_get_knowledge(self, knowledge_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("chat", "knowledge", knowledge_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_update_knowledge(self, knowledge_id: int, updates: dict) -> None:
        client = await get_redis_client()
        data = await client.get(_k("chat", "knowledge", knowledge_id))
        if data:
            knowledge = json.loads(data)
            knowledge.update(updates)
            knowledge["updatedAt"] = datetime.utcnow().isoformat()
            await client.set(_k("chat", "knowledge", knowledge_id),
                             json.dumps(knowledge, ensure_ascii=False))

    async def _redis_delete_knowledge(self, knowledge_id: int) -> None:
        client = await get_redis_client()
        await client.delete(_k("chat", "knowledge", knowledge_id))

    async def _redis_list_knowledge(self, category: str = None, status: str = None,
                                     limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        ids = await client.lrange(_k("chat", "knowledge_list"), 0, limit - 1)
        result = []
        for kid in ids:
            data = await client.get(_k("chat", "knowledge", kid))
            if data:
                k = json.loads(data)
                if category and k.get("category") != category:
                    continue
                if status and k.get("status") != status:
                    continue
                result.append(k)
        return result

    async def _redis_search_knowledge(self, query: str, category: str = None,
                                       limit: int = 5) -> list[dict]:
        all_knowledge = await self._redis_list_knowledge(category=category,
                                                          status=KNOW_STATUS_ENABLED,
                                                          limit=1000)
        if not query:
            return []
        query_lower = query.lower()
        results = [k for k in all_knowledge if _knowledge_matches(k, query_lower)]
        results.sort(key=lambda k: (
            0 if query_lower in (k.get("question") or "").lower() else 1
        ))
        return results[:limit]
