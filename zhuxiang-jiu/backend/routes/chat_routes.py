"""AI智能客服聊天模块路由(22 端点)

鉴权:
    - 用户端(9 接口): X-Member-Id 头标识当前会员(创建会话/发消息/查询/转人工/关闭/评价/已读)
    - 客服端(3 接口): X-Role: admin 头(排队/接入/回复)
    - 管理端(3 接口): X-Role: admin 头(管理端查询/会话统计/灰度管理)
    - 公开(2 接口): 知识库查询/列表(仅读)
    - 越权防护(v2 安全加固): 会话端点校验归属——会员仅可操作
      自己的会话(他人会话 403), admin 可操作任意会话(工作台)

异常映射:
    - KeyError → 404(会话/知识库不存在)
    - ValueError → 409(状态冲突)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 会话(7):  创建会话/发送消息/查询会话/查询消息(增量)/转人工/关闭会话/标记已读
    - 客服端(3): 排队列表/接入会话/客服回复
    - 知识库(4): 创建/列表/更新/删除
    - 管理端(2): 管理端查询会话/会话统计
    - 评价(1):  满意度评价
    - 灰度(4):  查询模式/运行时切档/护栏检测/人工恢复
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.chat_service import ChatService
from services.chat_mode_service import ChatModeService


router = APIRouter()
_service = ChatService()
_mode_service = ChatModeService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


async def _authorize_session(session_id: str, x_member_id: str | None,
                             x_role: str | None) -> None:
    """会话归属校验(v2 安全加固, 对齐 message 模块 TD-4 语义)

    - 无 X-Member-Id 且非 admin: 401
    - admin: 放行(客服工作台需操作任意会话)
    - 会员: 仅可操作自己的会话(userId 匹配), 他人会话 403
    - 会话不存在: 404
    """
    if x_role == "admin":
        return
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        session = await _service.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404,
                            detail=f"会话不存在(sessionId={session_id})") from None
    if session.get("userId") != int(x_member_id):
        raise HTTPException(status_code=403, detail="无权操作他人会话")


def _map_key_error(exc: KeyError) -> HTTPException:
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        raise _map_key_error(exc)
    if isinstance(exc, ValueError):
        raise _map_value_error(exc)
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class CreateSessionRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    sessionType: str = Field("presale", description="会话类型: presale/aftersale/old_wine/custom")
    guestPhone: str | None = Field(None, description="游客手机号(游客咨询时)")
    ageConfirmed: bool = Field(False, description="已满18周岁声明(酒类合规 P0-1)")


class SendMessageRequest(PydBaseModel):
    senderType: str = Field("user", description="发送方: user/ai/customer_service")
    senderId: int = Field(..., description="发送方ID")
    messageType: str = Field("text", description="消息类型: text/image/video/voice/file/card/button")
    content: str = Field(..., description="消息内容")
    mediaUrl: str | None = Field(None, description="多媒体URL")
    mediaThumb: str | None = Field(None, description="缩略图URL")
    mediaSize: int = Field(0, description="文件大小")
    duration: int = Field(0, description="语音/视频时长")


class TransferRequest(PydBaseModel):
    reason: str = Field("", description="转人工原因")


class SatisfactionRequest(PydBaseModel):
    satisfaction: int = Field(..., ge=1, le=5, description="满意度评分1-5")


class CsReplyRequest(PydBaseModel):
    customerServiceId: int = Field(..., description="客服ID")
    content: str = Field(..., description="回复内容")
    messageType: str = Field("text", description="消息类型: text/image/video/voice/file/card/button")


class KnowledgeRequest(PydBaseModel):
    category: str = Field(..., description="分类: product/faq/policy/order/activity/compliance")
    question: str = Field(..., description="问题")
    answer: str = Field(..., description="答案")
    keywords: str = Field("", description="关键词(空格分隔)")
    intent: str = Field("", description="意图标签")
    confidenceThreshold: float = Field(0.5, ge=0, le=1, description="置信度阈值")


class KnowledgeUpdateRequest(PydBaseModel):
    question: str | None = None
    answer: str | None = None
    keywords: str | None = None
    intent: str | None = None
    confidenceThreshold: float | None = Field(None, ge=0, le=1)
    status: str | None = None


class ModeOverrideRequest(PydBaseModel):
    mode: str = Field(..., description="目标灰度态: off/shadow/assist")


class ModeGuardRequest(PydBaseModel):
    complaintRate: float = Field(..., ge=0, le=1, description="当前投诉率(0-1)")
    unresolvedRate: float = Field(..., ge=0, le=1, description="当前未解决率(0-1)")
    baselineComplaintRate: float = Field(0.0, ge=0, le=1, description="基线投诉率")
    baselineUnresolvedRate: float = Field(0.0, ge=0, le=1, description="基线未解决率")


# ============================================================
# 会话接口(7)
# ============================================================

@router.get("/api/chat/my-sessions", tags=["AI智能客服聊天模块"])
async def my_sessions(
    x_member_id: str = Header(None, alias="X-Member-Id"),
    limit: int = Query(50, ge=1, le=200, description="返回数量"),
):
    """我的会话列表(用户端, 按创建时间倒序)"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.list_user_sessions(int(user_id), limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/sessions", tags=["AI智能客服聊天模块"])
async def create_session(
    data: CreateSessionRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """创建会话(AI优先接待)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.create_session(
            user_id=data.userId,
            session_type=data.sessionType,
            guest_phone=data.guestPhone,
            age_confirmed=data.ageConfirmed,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/sessions/{session_id}/messages", tags=["AI智能客服聊天模块"])
async def send_message(
    session_id: str,
    data: SendMessageRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """发送消息(用户消息触发AI自动回复; 会员仅自己会话, admin 任意)"""
    await _authorize_session(session_id, x_member_id, x_role)
    try:
        result = await _service.send_message(
            session_id=session_id,
            sender_type=data.senderType,
            sender_id=data.senderId,
            message_type=data.messageType,
            content=data.content,
            media_url=data.mediaUrl,
            media_thumb=data.mediaThumb,
            media_size=data.mediaSize,
            duration=data.duration,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/chat/sessions/{session_id}", tags=["AI智能客服聊天模块"])
async def get_session(
    session_id: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询会话详情(会员仅自己会话, admin 任意)"""
    await _authorize_session(session_id, x_member_id, x_role)
    try:
        result = await _service.get_session(session_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/chat/sessions/{session_id}/messages", tags=["AI智能客服聊天模块"])
async def list_messages(
    session_id: str,
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    since_message_id: int = Query(None, ge=0, alias="since_message_id",
                                  description="增量游标: 仅返回 id 大于该值的消息(P1 实时轮询)"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询会话消息(按时间正序; since_message_id 增量模式, 缺省兼容全量)"""
    await _authorize_session(session_id, x_member_id, x_role)
    try:
        result = await _service.list_messages(session_id, limit, since_message_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/sessions/{session_id}/transfer", tags=["AI智能客服聊天模块"])
async def transfer_to_human(
    session_id: str,
    data: TransferRequest = None,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """转人工客服(会员仅自己会话, admin 任意)"""
    await _authorize_session(session_id, x_member_id, x_role)
    try:
        reason = data.reason if data else ""
        result = await _service.transfer_to_human(session_id, reason=reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/sessions/{session_id}/close", tags=["AI智能客服聊天模块"])
async def close_session(
    session_id: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """关闭会话(会员仅自己会话, admin 任意)"""
    await _authorize_session(session_id, x_member_id, x_role)
    try:
        result = await _service.close_session(session_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/sessions/{session_id}/read", tags=["AI智能客服聊天模块"])
async def mark_read(
    session_id: str,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """标记会话已读(会员视角: 标记 AI/客服消息为已读, P1 实时配套)"""
    await _authorize_session(session_id, x_member_id, x_role)
    try:
        result = await _service.mark_session_read(session_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 客服端接口(3) —— 人工客服工作台
# ============================================================

@router.get("/api/chat/cs/queue", tags=["AI智能客服聊天模块"])
async def cs_queue(
    status: str = Query("human_chatting", description="按状态筛选: waiting/human_chatting/transferring"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """客服排队列表(客服端, 按状态筛选)"""
    _require_admin(x_role)
    try:
        result = await _service.cs_queue(status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/cs/sessions/{session_id}/accept", tags=["AI智能客服聊天模块"])
async def cs_accept(
    session_id: str,
    x_role: str = Header(None, alias="X-Role"),
):
    """客服接入会话(客服端, 未分配的 human_chatting 会话)"""
    _require_admin(x_role)
    try:
        result = await _service.cs_accept(session_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/cs/sessions/{session_id}/reply", tags=["AI智能客服聊天模块"])
async def cs_reply(
    session_id: str,
    data: CsReplyRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """客服回复消息(客服端, 走敏感词过滤, 不触发 AI)"""
    _require_admin(x_role)
    try:
        result = await _service.cs_reply(
            session_id=session_id,
            customer_service_id=data.customerServiceId,
            content=data.content,
            message_type=data.messageType,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 满意度评价(1)
# ============================================================

@router.post("/api/chat/sessions/{session_id}/satisfaction", tags=["AI智能客服聊天模块"])
async def rate_satisfaction(
    session_id: str,
    data: SatisfactionRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
    x_role: str = Header(None, alias="X-Role"),
):
    """会话满意度评价(1-5分; 会员仅自己会话)"""
    await _authorize_session(session_id, x_member_id, x_role)
    try:
        result = await _service.rate_satisfaction(session_id, data.satisfaction)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 知识库接口(4)
# ============================================================

@router.post("/api/chat/knowledge", tags=["AI智能客服聊天模块"])
async def create_knowledge(
    data: KnowledgeRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建知识库条目(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.create_knowledge(
            category=data.category,
            question=data.question,
            answer=data.answer,
            keywords=data.keywords,
            intent=data.intent,
            confidence_threshold=data.confidenceThreshold,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/chat/knowledge", tags=["AI智能客服聊天模块"])
async def list_knowledge(
    category: str = Query(None, description="按分类筛选"),
    status: str = Query(None, description="按状态筛选: enabled/disabled"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询知识库列表(公开)"""
    try:
        result = await _service.list_knowledge(category, status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.put("/api/chat/knowledge/{knowledge_id}", tags=["AI智能客服聊天模块"])
async def update_knowledge(
    knowledge_id: int,
    data: KnowledgeUpdateRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """更新知识库条目(管理员)"""
    _require_admin(x_role)
    try:
        updates = {k: v for k, v in data.dict().items() if v is not None}
        result = await _service.update_knowledge(knowledge_id, updates)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.delete("/api/chat/knowledge/{knowledge_id}", tags=["AI智能客服聊天模块"])
async def delete_knowledge(
    knowledge_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """删除知识库条目(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.delete_knowledge(knowledge_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 管理端与统计接口(2)
# ============================================================

@router.get("/api/chat/admin/sessions", tags=["AI智能客服聊天模块"])
async def admin_list_sessions(
    status: str = Query(None, description="按状态筛选"),
    session_type: str = Query(None, alias="session_type", description="按类型筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """管理端查询会话(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.admin_list_sessions(status, session_type, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/chat/stats", tags=["AI智能客服聊天模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """会话统计(管理员, 观测面——不受灰度影响)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 灰度接口(4) —— AI 智能层三态管理(观测面 + 决策面)
# ============================================================

@router.get("/api/chat/mode", tags=["AI智能客服聊天模块"])
async def get_mode(
    x_role: str = Header(None, alias="X-Role"),
):
    """查询 AI 智能层灰度态(管理员, 观测面)"""
    _require_admin(x_role)
    try:
        result = await _mode_service.current_mode()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/mode/override", tags=["AI智能客服聊天模块"])
async def mode_override(
    data: ModeOverrideRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """运行时切档(管理员, 免容器重建留痕; 空串清除)"""
    _require_admin(x_role)
    try:
        result = await _mode_service.set_override(data.mode)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/mode/guard", tags=["AI智能客服聊天模块"])
async def mode_guard(
    data: ModeGuardRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """护栏检测(管理员): 投诉率/未解决率相对基线恶化 >3% 自动降档 off"""
    _require_admin(x_role)
    try:
        result = await _mode_service.guard_check(
            complaint_rate=data.complaintRate,
            unresolved_rate=data.unresolvedRate,
            baseline_complaint_rate=data.baselineComplaintRate,
            baseline_unresolved_rate=data.baselineUnresolvedRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/mode/resume", tags=["AI智能客服聊天模块"])
async def mode_resume(
    x_role: str = Header(None, alias="X-Role"),
):
    """人工恢复(管理员, 护栏暂停后恢复须人工留痕)"""
    _require_admin(x_role)
    try:
        result = await _mode_service.resume()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_chat_routes(app):
    """注册AI智能客服聊天模块路由"""
    app.include_router(router)
