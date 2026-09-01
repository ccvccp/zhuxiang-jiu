"""AI智能客服聊天模块业务逻辑层

核心业务:
    - 创建会话(AI优先接待, 置信度0.9)
    - 发送消息(用户消息→AI自动回复+知识库检索)
    - 转人工触发引擎(P1-8, 设计文档 5.1 八条: 用户主动/投诉/退款/
      复杂问题/情绪愤怒/置信度<0.5/3次未解决/VIP直通)
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


import contextlib
import logging
import os

from core.locks import get_lock
from core.helpers import ts
from repositories.chat_repository import (
    ChatRepository,
    # 会话状态
    SESSION_STATUS_AI, SESSION_STATUS_HUMAN,
    SESSION_STATUS_ENDED, SESSION_TYPE_PRESALE, SENDER_USER, SENDER_AI, SENDER_SYSTEM,
    # 消息类型
    MESSAGE_TYPE_TEXT,
    # 知识库
    KNOW_STATUS_ENABLED,
)
# 会话级敏感词过滤复用风控评分模块词库(信息内容审核, 避免重复维护)
from services.ai_scoring_ext_service import MessageContentScorer
# 知识库训练模块(检索消费方: 新库优先, 旧FAQ兜底, 未命中回写缺口)
from services.knowledge_service import KnowledgeService

SENSITIVE_WORDS = MessageContentScorer.SENSITIVE_WORDS

logger = logging.getLogger("chat_service")


# ============================================================
# AI 业务规则常量
# ============================================================

# AI 初始置信度(接待时)
AI_INITIAL_CONFIDENCE = 0.90
# AI 默认回复置信度(命中知识库)
AI_REPLY_CONFIDENCE = 0.85
# AI 兜底回复置信度(未命中)
AI_FALLBACK_CONFIDENCE = 0.30
# 低置信转人工阈值(设计文档 5.1: 置信度<0.5 立即转人工)
LOW_CONFIDENCE_THRESHOLD = 0.5
# 连续未解决消息数(超过自动转人工)
MAX_UNRESOLVED_COUNT = 3
# 转人工关键词
TRANSFER_KEYWORDS = ("转人工", "人工客服", "人工", "找客服", "转接人工")
# 投诉关键词(设计文档 5.1: "投诉/差评/315" 优先转人工)
COMPLAINT_KEYWORDS = ("投诉", "差评", "315", "工商投诉", "12315")
# 退款/售后关键词(设计文档 5.1: 退款请求转售后)
REFUND_KEYWORDS = ("退款", "退货", "换货", "售后", "退钱", "仅退款")
# 复杂问题关键词(设计文档 5.1: 定制/团购/代理 转专属)
COMPLEX_KEYWORDS = ("定制", "团购", "代理", "加盟", "贴牌", "企业采购")
# VIP 会员等级阈值(L4/L5/SVIP 直接人工, 设计文档 5.1)
VIP_MEMBER_LEVEL = 4
# 情绪风险阈值(复用 role 满意度预测: riskScore>=60 干预)
from repositories.role_repository import SATISFACTION_RISK_THRESHOLD  # noqa: E402
# 情绪负向关键词(复用 role 词库: 情绪愤怒触发)
from repositories.role_repository import NEGATIVE_EMOTION_KEYWORDS  # noqa: E402
# 默认客服ID(简化: 轮询分配, 此处固定)
DEFAULT_CUSTOMER_SERVICE_ID = 1

# 兜底回复文案
FALLBACK_REPLY = '抱歉, 未找到与您问题匹配的答案。如需进一步帮助, 可回复"转人工"联系人工客服。'


class ChatService:
    """AI智能客服聊天业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: ChatRepository = ChatRepository()):
        self.repo = repo
        self.knowledge_svc = KnowledgeService()

    # ============================================================
    # 1. 创建会话
    # ============================================================

    async def create_session(self, user_id: int, session_type: str = SESSION_TYPE_PRESALE,
                              guest_phone: str = None,
                              age_confirmed: bool = False) -> dict:
        """创建会话(AI优先接待)

        规则:
            - 新会话状态=ai_chatting(AI优先接待)
            - AI置信度初始 0.9
            - 生成唯一 sessionId
            - 酒类合规(P0-1): 记录已满18周岁声明(咨询不构成销售, 声明式留痕)

        Returns:
            会话信息
        """
        session_id = await self.repo.generate_session_id()
        now = ts()
        session = {
            "sessionId": session_id,
            "userId": user_id,
            "guestPhone": guest_phone,
            "ageConfirmed": bool(age_confirmed),
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
            - 敏感词过滤: 消息内容命中敏感词库 → 拒绝发送(P0 内容合规)
            - 用户消息: 写入用户消息, AI检索知识库自动回复
              - 命中知识库: 回复答案, 置信度0.85
              - 未命中: 回复兜底文案, 置信度0.3, unresolved_count+1
              - 连续3次未命中 或 用户含"转人工": 自动转人工
            - AI/客服消息: 仅写入

        Returns:
            包含 userMessage 与 aiReply(若为用户消息) 的结果

        Raises:
            KeyError: 会话不存在
            ValueError: 会话已关闭 / 含敏感词
        """
        # 敏感词过滤(写入前拦截, 复用风控评分模块词库)
        if content:
            hit = next((w for w in SENSITIVE_WORDS if w in content), None)
            if hit:
                raise ValueError("消息包含敏感词, 发送被拒绝(请修改后重发)")

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

            # VIP 直通人工(P1-8: L4/L5/SVIP 首条消息免 AI 接待)
            await self._check_vip_trigger(session)
            if session["status"] != SESSION_STATUS_AI:
                result["transferred"] = True
                result["transferTrigger"] = {
                    "trigger": "vip", "reason": "VIP会员直通人工(L4/L5/SVIP)"}
                return result

            ai_reply = await self._generate_ai_reply(session, content)
            result["aiReply"] = ai_reply

            # 转人工触发判定(P1-8, 设计文档 5.1 八条触发集中评估)
            trigger = self._evaluate_transfer_triggers(session, content, ai_reply)
            if trigger:
                await self._do_transfer(session, reason=trigger["reason"])
                result["transferred"] = True
                result["transferTrigger"] = trigger

            return result

    # ============================================================
    # 转人工触发引擎(P1-8, 设计文档 5.1: 置信度/主动/情绪/投诉/退款/未解决/VIP/复杂)
    # ============================================================

    def _evaluate_transfer_triggers(self, session: dict, content: str,
                                     ai_reply: dict) -> dict | None:
        """集中评估转人工触发(须在锁内、AI 回复生成后调用)

        触发规则(设计文档 5.1, 命中即转, 优先级从高到低):
            1. 用户主动:  "转人工"等关键词(原有)
            2. 投诉关键词: "投诉/差评/315"等 → 优先转人工
            3. 退款请求:  "退款/退货/售后"等 → 转售后
            4. 复杂问题:  "定制/团购/代理"等 → 转专属
            5. 情绪愤怒:  负向情绪词命中 ≥3 → 情绪风险干预
            6. 置信度<0.5: AI 回复置信度过低 → 立即转人工
            7. 3次未解决: unresolvedCount>=3(原有)

        VIP 用户(L4/L5)触发: 建会话时由 create_session 落 vipMember 标记,
        首条用户消息即触发直通人工(见 _check_vip_trigger)。

        Returns:
            命中触发返回 {trigger, reason}; 未命中返回 None
        """
        # 1. 用户主动(原有, 最优先)
        if any(kw in content for kw in TRANSFER_KEYWORDS):
            return {"trigger": "user_request",
                    "reason": "用户主动转人工"}
        # 2. 投诉关键词
        if any(kw in content for kw in COMPLAINT_KEYWORDS):
            return {"trigger": "complaint",
                    "reason": "投诉关键词触发(优先转人工)"}
        # 3. 退款请求
        if any(kw in content for kw in REFUND_KEYWORDS):
            return {"trigger": "refund",
                    "reason": "退款/售后咨询(转售后)"}
        # 4. 复杂问题
        if any(kw in content for kw in COMPLEX_KEYWORDS):
            return {"trigger": "complex",
                    "reason": "复杂问题咨询(定制/团购/代理, 转专属)"}
        # 5. 情绪愤怒(负向词密集命中)
        emotion_hits = sum(1 for kw in NEGATIVE_EMOTION_KEYWORDS if kw in content)
        if emotion_hits >= 3:
            return {"trigger": "emotion",
                    "reason": f"情绪愤怒风险(负向词×{emotion_hits}, 优先转人工)"}
        # 6. 置信度 < 0.5(仅 RAG 动态低置信答案; 兜底回复走 3 次未解决计数)
        confidence = ai_reply.get("aiConfidence") if ai_reply else None
        is_fallback = bool(ai_reply.get("fallback")) if ai_reply else False
        if (confidence is not None and not is_fallback
                and confidence < LOW_CONFIDENCE_THRESHOLD):
            return {"trigger": "low_confidence",
                    "reason": f"AI置信度过低({confidence:.2f}<0.5, 立即转人工)"}
        # 7. 3 次未解决(原有)
        if session.get("unresolvedCount", 0) >= MAX_UNRESOLVED_COUNT:
            return {"trigger": "unresolved",
                    "reason": "AI连续3次未解决, 自动转人工"}
        return None

    async def _check_vip_trigger(self, session: dict) -> None:
        """VIP 用户直通人工(P1-8: L4/L5/SVIP 会员免 AI 接待)

        建会话后首条用户消息时调用一次: 命中即转人工并标记 vipMember。
        """
        if session.get("vipChecked") or session.get("vipMember"):
            return
        session["vipChecked"] = True
        user_id = session.get("userId")
        if not user_id:
            return
        try:
            from repositories.member_repository import MemberRepository
            member = await MemberRepository().get_by_id(user_id)
            level = member.get("level", 1) if member else 1
        except Exception:
            return
        if level and int(level) >= VIP_MEMBER_LEVEL:
            session["vipMember"] = True
            await self._do_transfer(session, reason="VIP会员直通人工(L4/L5/SVIP)")
            logger.info("chat_vip_direct_transfer session=%s user=%s level=%s",
                        session.get("sessionId"), user_id, level)

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

        # 2. 知识库检索(P3.2, D-18): RAG 问答优先, 旧FAQ表兜底——迁移过渡期双轨
        knowledge = await self._search_knowledge(user_content)
        if knowledge:
            session["unresolvedCount"] = 0
            await self.repo.save_session(session)
            content = knowledge.get("answer", "")
            # P3.2: 置信度动态化(RAG 相似度, 旧 FAQ 回退固定值)
            confidence = (knowledge.get("confidence")
                          or AI_REPLY_CONFIDENCE)
            reply = {
                "sessionId": session_id,
                "senderType": SENDER_AI,
                "senderId": 0,
                "messageType": MESSAGE_TYPE_TEXT,
                "content": content,
                "aiConfidence": round(float(confidence), 4),
                "transferred": False,
                "knowledgeId": knowledge.get("id"),
                # P3.2: RAG 引用溯源(旧 FAQ 兜底时为空列表)
                "citations": knowledge.get("citations") or [],
                "ragMode": knowledge.get("ragMode") or "legacy",
            }
        else:
            # 未命中: 兜底回复 + 未解决计数+1 + 记录知识缺口(飞轮)
            session["unresolvedCount"] = session.get("unresolvedCount", 0) + 1
            await self.repo.save_session(session)
            await self._record_knowledge_gap(user_content, session_id)
            reply = {
                "sessionId": session_id,
                "senderType": SENDER_AI,
                "senderId": 0,
                "messageType": MESSAGE_TYPE_TEXT,
                "content": FALLBACK_REPLY,
                "aiConfidence": AI_FALLBACK_CONFIDENCE,
                "transferred": False,
                # 兜底=完全未理解, 走 3 次未解决计数而非立即低置信转人工
                "fallback": True,
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
        """执行转人工(内部方法)

        对接 AI 智能管理模块服务调度中枢(设计文档 v1.1 §4.3):
            1. 自动创建 source=ai 工单(修复 chat-ticket 断链)
            2. 调度最优客服(信用×40%+技能×25%+负载×20%+满意度×15%)
            3. 无可用客服时回退默认客服ID(保持兼容)
        """
        assigned_id = DEFAULT_CUSTOMER_SERVICE_ID
        ticket_no = ""
        try:
            # 局部导入避免循环依赖(chat ← role ← ticket/credit/wallet)
            from services.role_service import RoleService
            dispatch = await RoleService().dispatch_customer_service(
                session, reason=reason)
            ticket_no = dispatch.get("ticketNo", "")
            if dispatch.get("assigneeId"):
                assigned_id = dispatch["assigneeId"]
        except Exception:
            # 调度中枢异常不影响转接主流程(回退默认客服)
            pass

        session["status"] = SESSION_STATUS_HUMAN
        session["customerServiceId"] = assigned_id
        session["ticketNo"] = ticket_no
        await self.repo.save_session(session)
        # 系统消息通知
        notice = f"已为您转接人工客服(工号{assigned_id}), 请稍候。"
        if ticket_no:
            notice = f"已为您转接人工客服(工号{assigned_id}), 工单号{ticket_no}。"
        await self.repo.add_message({
            "sessionId": session["sessionId"],
            "senderType": SENDER_SYSTEM,
            "senderId": 0,
            "messageType": MESSAGE_TYPE_TEXT,
            "content": notice,
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
                "customerServiceId": session.get("customerServiceId"),
                "ticketNo": session.get("ticketNo", ""),
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
    # 6.5 知识库检索消费方(新知识库模块优先, 旧FAQ兜底)
    # ============================================================

    async def _search_knowledge(self, user_content: str) -> dict | None:
        """统一知识检索(P3.2, D-18): RAG 问答优先(direct/synthesized
        带引用溯源), 旧 chat_knowledge(关键词匹配)兜底。

        RAG unsolved 或新库异常不阻断对话(best-effort 降级旧库)。

        P3.3 联动: KNOWLEDGE_CHAT_LLM=on 且 llm_client 可用时,
        synthesized 态走大模型合成(未配置 key/失败自动回退 rule 轨,
        行为与关闭开关一致); 默认 off 保持 rule 轨零成本。
        """
        try:
            provider = ("llm" if os.environ.get(
                "KNOWLEDGE_CHAT_LLM", "off").strip().lower() == "on"
                else "rule")
            rag = await self.knowledge_svc.rag_answer(
                user_content, provider=provider)
            if rag["mode"] != "unsolved" and rag["answer"]:
                return {"id": rag["citations"][0]["entryId"],
                        "answer": rag["answer"],
                        "citations": rag["citations"],
                        "confidence": rag["confidence"],
                        "ragMode": rag["mode"]}
        except Exception:
            pass
        # 旧 FAQ 兜底(迁移过渡期)
        legacy = await self.repo.search_knowledge(user_content, limit=1)
        return legacy[0] if legacy else None

    async def _record_knowledge_gap(self, user_content: str,
                                     session_id: str) -> None:
        """记录知识缺口(chat 未命中 → 新知识库缺口队列, 驱动补知识)

        best-effort: 失败不阻断对话主流程。
        """
        with contextlib.suppress(Exception):
            await self.knowledge_svc.record_gap(user_content, session_id)

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
