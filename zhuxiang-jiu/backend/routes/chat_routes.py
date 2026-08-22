"""AI智能客服聊天模块路由(12 端点)

鉴权:
    - 用户端(8 接口): X-Member-Id 头标识当前会员(创建会话/发消息/查询/转人工/关闭/评价)
    - 管理端(2 接口): X-Role: admin 头(管理端查询/会话统计)
    - 公开(2 接口): 知识库查询/列表(仅读)

异常映射:
    - KeyError → 404(会话/知识库不存在)
    - ValueError → 409(状态冲突)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 会话(6):  创建会话/发送消息/查询会话/查询消息/转人工/关闭会话
    - 知识库(4): 创建/列表/更新/删除
    - 管理端(1): 管理端查询会话
    - 统计(1):  会话统计
    - 评价(1):  满意度评价
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.chat_service import ChatService


router = APIRouter()
_service = ChatService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: Optional[str]) -> str:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: Optional[str]):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


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
    guestPhone: Optional[str] = Field(None, description="游客手机号(游客咨询时)")


class SendMessageRequest(PydBaseModel):
    senderType: str = Field("user", description="发送方: user/ai/customer_service")
    senderId: int = Field(..., description="发送方ID")
    messageType: str = Field("text", description="消息类型: text/image/video/voice/file/card/button")
    content: str = Field(..., description="消息内容")
    mediaUrl: Optional[str] = Field(None, description="多媒体URL")
    mediaThumb: Optional[str] = Field(None, description="缩略图URL")
    mediaSize: int = Field(0, description="文件大小")
    duration: int = Field(0, description="语音/视频时长")


class TransferRequest(PydBaseModel):
    reason: str = Field("", description="转人工原因")


class SatisfactionRequest(PydBaseModel):
    satisfaction: int = Field(..., ge=1, le=5, description="满意度评分1-5")


class KnowledgeRequest(PydBaseModel):
    category: str = Field(..., description="分类: product/faq/policy/order/activity/compliance")
    question: str = Field(..., description="问题")
    answer: str = Field(..., description="答案")
    keywords: str = Field("", description="关键词(空格分隔)")
    intent: str = Field("", description="意图标签")
    confidenceThreshold: float = Field(0.5, ge=0, le=1, description="置信度阈值")


class KnowledgeUpdateRequest(PydBaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    keywords: Optional[str] = None
    intent: Optional[str] = None
    confidenceThreshold: Optional[float] = Field(None, ge=0, le=1)
    status: Optional[str] = None


# ============================================================
# 会话接口(6)
# ============================================================

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
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/sessions/{session_id}/messages", tags=["AI智能客服聊天模块"])
async def send_message(
    session_id: str,
    data: SendMessageRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """发送消息(用户消息触发AI自动回复)"""
    _require_member_id(x_member_id)
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
):
    """查询会话详情"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_session(session_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/chat/sessions/{session_id}/messages", tags=["AI智能客服聊天模块"])
async def list_messages(
    session_id: str,
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询会话消息(按时间正序)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.list_messages(session_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/chat/sessions/{session_id}/transfer", tags=["AI智能客服聊天模块"])
async def transfer_to_human(
    session_id: str,
    data: TransferRequest = None,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """转人工客服"""
    _require_member_id(x_member_id)
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
):
    """关闭会话"""
    _require_member_id(x_member_id)
    try:
        result = await _service.close_session(session_id)
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
):
    """会话满意度评价(1-5分)"""
    _require_member_id(x_member_id)
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
    """会话统计(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_chat_routes(app):
    """注册AI智能客服聊天模块路由"""
    app.include_router(router)
