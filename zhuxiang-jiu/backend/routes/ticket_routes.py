"""客服工单模块路由(9 端点)

鉴权:
    - 用户端(2): 创建工单/确认+满意度(X-Member-Id 头)
    - 客服/管理端(7): 分配/回复/解决/关闭/列表/详情/统计(X-Role 头,
      放行 admin 与 cs_staff, 对应权限码 ticket:view/ticket:reply)

异常映射(遵循项目约定):
    - KeyError → 404(工单不存在)
    - ValueError → 409(状态流转非法/类型非法/越权确认等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 工单(4):   create / detail / list / stats
    - 流转(4):   assign / reply / resolve / close
    - 确认(1):   confirm(用户, 含满意度)
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.ticket_service import TicketService
from repositories.ticket_repository import (
    TICKET_TYPE_PRESALE, TICKET_TYPE_COMPLAINT,
    PRIORITY_MEDIUM, SOURCE_USER,
    TICKET_STATUS_PENDING,
)


router = APIRouter()
_service = TicketService()


# ============================================================
# 鉴权与异常映射辅助(对齐 chat_routes 风格)
# ============================================================

def _require_member_id(x_member_id: str | None) -> int:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="X-Member-Id 格式不正确") from None


def _require_staff(x_role: str | None):
    """校验客服/管理员权限(权限码 ticket:view/ticket:reply 落地), 失败返回 403"""
    if x_role not in ("admin", "cs_staff"):
        raise HTTPException(status_code=403, detail="需要客服或管理员权限")


def _handle(exc: Exception):
    """统一异常映射"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class CreateTicketRequest(PydBaseModel):
    type: str = Field(..., description="工单类型: presale/aftersale/complaint/suggestion/oldwine")
    priority: str = Field(PRIORITY_MEDIUM, description="优先级: urgent/high/medium/low")
    description: str = Field(..., min_length=1, max_length=2000, description="问题描述")
    source: str = Field(SOURCE_USER, description="来源: ai/user/rule/phone")
    orderId: str = Field("", description="关联订单号(可选)")
    userPhone: str = Field("", description="手机号(可选)")
    userLevel: int = Field(1, ge=1, le=5, description="会员等级(1-5)")
    memberVip: bool = Field(False, description="是否VIP(VIP自动升紧急)")


class AssignTicketRequest(PydBaseModel):
    handlerId: int = Field(..., description="客服ID")
    handlerName: str = Field("", description="客服姓名")


class ReplyTicketRequest(PydBaseModel):
    replierId: str | int = Field(..., description="回复人ID(客服/用户)")
    content: str = Field(..., min_length=1, max_length=2000, description="回复内容")
    replierRole: str = Field("staff", description="回复角色: staff/user")


class ResolveTicketRequest(PydBaseModel):
    handlerId: str | int = Field(..., description="处理客服ID")
    resolution: str = Field(..., min_length=1, max_length=2000, description="解决方案")


class ConfirmTicketRequest(PydBaseModel):
    satisfaction: int = Field(..., ge=1, le=5, description="满意度 1-5 星")


class CloseTicketRequest(PydBaseModel):
    reason: str = Field("", description="关闭原因(强制关闭时必填)")


# ============================================================
# 用户端接口(2)
# ============================================================

@router.post("/api/ticket/create", tags=["客服工单模块"])
async def create_ticket(
    data: CreateTicketRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """创建工单(投诉/VIP 自动升紧急优先级)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.create_ticket(
            user_id=member_id,
            ticket_type=data.type,
            priority=data.priority,
            description=data.description,
            source=data.source,
            order_id=data.orderId,
            user_phone=data.userPhone,
            user_level=data.userLevel,
            member_vip=data.memberVip,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/ticket/{ticket_no}/confirm", tags=["客服工单模块"])
async def confirm_ticket(
    ticket_no: str,
    data: ConfirmTicketRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """用户确认解决+满意度评价(待用户确认 → 已解决)

    确认后自动触发服务分润结算(AI智能管理模块 D-8:
    满意度≥4星且关联订单才产生分润; 结算失败不影响确认结果)。
    """
    member_id = _require_member_id(x_member_id)
    try:
        result = await _service.confirm_ticket(
            ticket_no=ticket_no,
            user_id=member_id,
            satisfaction=data.satisfaction,
        )
        # 服务分润结算钩子(满意度确认后即时结算, 幂等)
        try:
            from services.role_service import RoleService
            settlement = await RoleService().settle_service_profit(ticket_no)
            result["serviceProfit"] = {
                "ledgerNo": settlement.get("ledgerNo"),
                "amount": settlement.get("amount"),
                "status": settlement.get("status"),
            }
        except Exception:
            # 工单未关联订单/无客服等场景不结算, 不影响确认
            pass
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 客服/管理端接口(7)
# ============================================================

@router.get("/api/ticket/list", tags=["客服工单模块"])
async def list_tickets(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="状态筛选 pending/processing/wait_confirm/resolved/closed"),
    type: str = Query(None, description="类型筛选 presale/aftersale/complaint/suggestion/oldwine"),
    priority: str = Query(None, description="优先级筛选 urgent/high/medium/low"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """工单列表(含动态 SLA 截止/超时/升级标记)"""
    _require_staff(x_role)
    try:
        result = await _service.list_tickets(
            status=status, ticket_type=type,
            priority=priority, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/ticket/stats", tags=["客服工单模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """工单统计(状态/类型/优先级分布+超时+升级+满意度)"""
    _require_staff(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/ticket/{ticket_no}", tags=["客服工单模块"])
async def get_ticket(
    ticket_no: str,
    x_role: str = Header(None, alias="X-Role"),
):
    """工单详情(含处理记录+SLA/超时/升级标记)"""
    _require_staff(x_role)
    try:
        result = await _service.get_ticket(ticket_no)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/ticket/{ticket_no}/assign", tags=["客服工单模块"])
async def assign_ticket(
    ticket_no: str,
    data: AssignTicketRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """分配客服(待分配 → 处理中)"""
    _require_staff(x_role)
    try:
        result = await _service.assign_ticket(
            ticket_no=ticket_no,
            handler_id=data.handlerId,
            handler_name=data.handlerName,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/ticket/{ticket_no}/reply", tags=["客服工单模块"])
async def reply_ticket(
    ticket_no: str,
    data: ReplyTicketRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """工单回复/处理记录(首次客服回复记录响应时间)"""
    _require_staff(x_role)
    try:
        result = await _service.reply_ticket(
            ticket_no=ticket_no,
            replier_id=data.replierId,
            replier_role=data.replierRole,
            content=data.content,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/ticket/{ticket_no}/resolve", tags=["客服工单模块"])
async def resolve_ticket(
    ticket_no: str,
    data: ResolveTicketRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """标记已处理(→ 待用户确认, 用户确认后才算已解决)"""
    _require_staff(x_role)
    try:
        result = await _service.resolve_ticket(
            ticket_no=ticket_no,
            handler_id=data.handlerId,
            resolution=data.resolution,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/ticket/{ticket_no}/close", tags=["客服工单模块"])
async def close_ticket(
    ticket_no: str,
    data: CloseTicketRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """关闭工单(已解决 → 已关闭; 未解决须满48小时方可强制关闭)"""
    _require_staff(x_role)
    try:
        result = await _service.close_ticket(
            ticket_no=ticket_no,
            reason=data.reason,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_ticket_routes(app):
    """注册客服工单模块路由"""
    app.include_router(router)
