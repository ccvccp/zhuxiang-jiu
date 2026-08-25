"""信息管理模块路由(10 端点)

鉴权:
    - 用户端(5接口): X-Member-Id 头标识当前会员
    - 管理端(5接口): X-Role: admin 头(模板CRUD/群发/统计等)

异常映射:
    - KeyError → 404(消息/模板不存在)
    - ValueError → 409(业务冲突)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布(10个):
    - 用户端(5): 发送消息/查询消息列表/查询消息详情/已读标记/批量已读
    - 管理端(5): 消息模板CRUD/推送记录/消息统计/管理端群发
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.message_service import MessageService


router = APIRouter()
_service = MessageService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: str | None):
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

class SendMessageRequest(PydBaseModel):
    userId: int = Field(..., description="目标用户ID")
    channel: str = Field(..., description="消息渠道: inmail/sms/email/miniapp/popup/push")
    title: str = Field(..., description="消息标题")
    content: str = Field(..., description="消息内容")
    category: str = Field("system", description="消息分类: system/order/logistics/activity/coupon/member/old_wine/content/security/service")
    templateId: int = Field(0, description="关联模板ID")
    jumpUrl: str = Field("", description="跳转URL")
    priority: str = Field("P2", description="优先级: P0/P1/P2/P3")


class MarkAllReadRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    channel: str = Field(None, description="按渠道筛选")
    category: str = Field(None, description="按分类筛选")


class CreateTemplateRequest(PydBaseModel):
    name: str = Field(..., description="模板名称")
    category: str = Field(..., description="消息分类")
    channel: str = Field(..., description="消息渠道: inmail/sms/email/miniapp/popup/push")
    title: str = Field(..., description="消息标题(模板)")
    content: str = Field(..., description="消息内容(模板)")
    variables: list = Field(default_factory=list, description="变量列表(JSON)")
    jumpUrl: str = Field("", description="跳转URL")
    icon: str = Field("", description="图标URL")
    priority: str = Field("P2", description="优先级")
    status: str = Field("draft", description="初始状态: draft/pending")


class UpdateTemplateRequest(PydBaseModel):
    name: str = Field(None, description="模板名称")
    category: str = Field(None, description="消息分类")
    channel: str = Field(None, description="消息渠道")
    title: str = Field(None, description="消息标题")
    content: str = Field(None, description="消息内容")
    variables: list = Field(None, description="变量列表")
    jumpUrl: str = Field(None, description="跳转URL")
    icon: str = Field(None, description="图标URL")
    priority: str = Field(None, description="优先级")
    status: str = Field(None, description="状态")


class BatchSendRequest(PydBaseModel):
    userIds: list[int] = Field(..., description="目标用户ID列表")
    channel: str = Field(..., description="消息渠道")
    title: str = Field(..., description="消息标题")
    content: str = Field(..., description="消息内容")
    category: str = Field("system", description="消息分类")
    templateId: int = Field(0, description="关联模板ID")
    taskId: int = Field(0, description="任务ID")


# ============================================================
# P0 接口(10 个) — 静态路径优先于动态路径
# ============================================================

# --- 用户端接口 ---

@router.post("/api/message/send", tags=["信息管理模块"])
async def send_message(
    data: SendMessageRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """发送消息(站内信/短信/邮件/小程序订阅消息)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.send_message(
            user_id=data.userId,
            channel=data.channel,
            title=data.title,
            content=data.content,
            category=data.category,
            template_id=data.templateId,
            jump_url=data.jumpUrl,
            priority=data.priority,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/message/list", tags=["信息管理模块"])
async def list_messages(
    user_id: int = Query(..., description="会员ID"),
    channel: str = Query(None, description="按渠道筛选: inmail/sms/email/miniapp/popup/push"),
    category: str = Query(None, description="按分类筛选: system/order/logistics/activity/coupon/member"),
    status: str = Query(None, description="按状态筛选: unread/read/deleted"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """查询用户消息列表(支持多条件筛选)"""
    try:
        result = await _service.list_messages(user_id, channel, category, status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/message/mark-all-read", tags=["信息管理模块"])
async def mark_all_read(
    data: MarkAllReadRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """批量标记已读(指定用户的所有未读消息)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.mark_all_read(
            user_id=data.userId,
            channel=data.channel,
            category=data.category,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/message/stats", tags=["信息管理模块"])
async def get_stats(
    user_id: int = Query(None, description="指定会员ID查询其统计"),
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """消息统计(按用户或全局)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_stats(user_id=user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/message/mark-read/{message_id}", tags=["信息管理模块"])
async def mark_read(
    message_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """标记单条消息为已读"""
    _require_member_id(x_member_id)
    try:
        result = await _service.mark_read(message_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/message/{message_id}", tags=["信息管理模块"])
async def get_message(
    message_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询消息详情"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_message(message_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 管理端接口 ---

@router.post("/api/message/admin/template/create", tags=["信息管理模块"])
async def create_template(
    data: CreateTemplateRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建消息模板(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.create_template(
            name=data.name,
            category=data.category,
            channel=data.channel,
            title=data.title,
            content=data.content,
            variables=data.variables,
            jump_url=data.jumpUrl,
            icon=data.icon,
            priority=data.priority,
            status=data.status,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/message/admin/template/list", tags=["信息管理模块"])
async def list_templates(
    status: str = Query(None, description="按状态筛选: draft/pending/approved/disabled"),
    category: str = Query(None, description="按分类筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询模板列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_templates(status=status, category=category, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/message/admin/template/{template_id}", tags=["信息管理模块"])
async def get_template(
    template_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """查询模板详情(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_template(template_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.put("/api/message/admin/template/{template_id}", tags=["信息管理模块"])
async def update_template(
    template_id: int,
    data: UpdateTemplateRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """更新模板(仅草稿/待审状态可更新)"""
    _require_admin(x_role)
    try:
        # 仅传入非空字段
        kwargs = {k: v for k, v in data.dict().items() if v is not None}
        result = await _service.update_template(template_id, **kwargs)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.delete("/api/message/admin/template/{template_id}", tags=["信息管理模块"])
async def delete_template(
    template_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """删除模板(仅草稿状态可删除)"""
    _require_admin(x_role)
    try:
        result = await _service.delete_template(template_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/message/admin/push-logs", tags=["信息管理模块"])
async def list_push_logs(
    task_id: int = Query(None, description="按任务ID筛选"),
    user_id: int = Query(None, description="按用户ID筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询推送记录(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_push_logs(task_id=task_id, user_id=user_id, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/message/admin/batch-send", tags=["信息管理模块"])
async def batch_send(
    data: BatchSendRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """批量群发消息(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.batch_send(
            user_ids=data.userIds,
            channel=data.channel,
            title=data.title,
            content=data.content,
            category=data.category,
            template_id=data.templateId,
            task_id=data.taskId,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_message_routes(app):
    """注册信息管理模块路由"""
    app.include_router(router)
