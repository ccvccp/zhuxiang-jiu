"""活动管理模块路由(12 端点)

鉴权:
    - 用户端(6接口): X-Member-Id 头标识当前会员
    - 管理端(6接口): X-Role: admin 头(创建/状态流转/审核/管理端列表等)

异常映射:
    - KeyError → 404(活动/报名不存在)
    - ValueError → 409(业务冲突)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布(12个):
    - 用户端(6): 查询列表/查询详情/报名/取消报名/擂台赛排名/活动统计
    - 管理端(6): 创建活动/活动状态流转/活动审核/管理端列表/提交擂台赛分数/查询报名列表
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.activity_service import ActivityService


router = APIRouter()
_service = ActivityService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: Optional[str]) -> str:
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: Optional[str]):
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

class CreateActivityRequest(PydBaseModel):
    name: str = Field(..., description="活动名称")
    type: str = Field(..., description="活动类型: promotion/lottery/competition/arena/interactive/groupbuy/seckill/presale")
    subType: str = Field("", description="子类型(如擂台赛L01-L08)")
    description: str = Field("", description="活动描述")
    startTime: str = Field("", description="开始时间")
    endTime: str = Field("", description="结束时间")
    budget: float = Field(0.0, ge=0, description="活动预算")
    rules: dict = Field(default_factory=dict, description="活动规则(JSON)")
    applicableScope: dict = Field(default_factory=dict, description="适用范围(JSON)")
    createdBy: int = Field(0, description="创建人ID")


class RegisterRequest(PydBaseModel):
    activityId: int = Field(..., description="活动ID")
    userId: int = Field(..., description="会员ID")
    participateData: dict = Field(default_factory=dict, description="参与数据(JSON)")


class CancelRegistrationRequest(PydBaseModel):
    activityId: int = Field(..., description="活动ID")
    userId: int = Field(..., description="会员ID")


class TransitionStatusRequest(PydBaseModel):
    targetStatus: str = Field(..., description="目标状态: registering/ongoing/ended/cancelled")
    operator: int = Field(0, description="操作人ID")


class AuditActivityRequest(PydBaseModel):
    approve: bool = Field(..., description="是否通过")
    auditor: int = Field(0, description="审核人ID")
    reason: str = Field("", description="审核理由")


class SubmitArenaScoreRequest(PydBaseModel):
    activityId: int = Field(..., description="活动ID")
    userId: int = Field(..., description="会员ID")
    score: float = Field(..., ge=0, description="擂台赛分数")
    realName: str = Field("", description="实名认证姓名")


# ============================================================
# P0 接口(12 个) — 静态路径优先于动态路径
# ============================================================

# --- 用户端接口 ---

@router.get("/api/activity/list", tags=["活动管理模块"])
async def list_activities(
    status: str = Query(None, description="按状态筛选: draft/registering/ongoing/ended/cancelled"),
    type: str = Query(None, description="按类型筛选: promotion/lottery/competition/arena/interactive/groupbuy/seckill/presale"),
    limit: int = Query(50, ge=1, le=200, description="查询条数"),
):
    """查询活动列表(默认仅查非草稿状态)"""
    try:
        result = await _service.list_activities(status=status, type_=type, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/stats/{activity_id}", tags=["活动管理模块"])
async def get_stats(
    activity_id: int,
):
    """活动统计(报名数/预算使用等)"""
    try:
        result = await _service.get_stats(activity_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/leaderboard/{activity_id}", tags=["活动管理模块"])
async def get_leaderboard(
    activity_id: int,
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询擂台赛排名(按 rank 升序)"""
    try:
        result = await _service.get_leaderboard(activity_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/registrations/{activity_id}", tags=["活动管理模块"])
async def list_registrations(
    activity_id: int,
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询活动报名列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.repo.list_registrations(activity_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/{activity_id}", tags=["活动管理模块"])
async def get_activity(
    activity_id: int,
):
    """查询活动详情"""
    try:
        result = await _service.get_activity(activity_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/register", tags=["活动管理模块"])
async def register(
    data: RegisterRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """活动报名(幂等防重, 同一用户对同一活动仅可报名一次)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.register(
            activity_id=data.activityId,
            user_id=data.userId,
            participate_data=data.participateData,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/cancel", tags=["活动管理模块"])
async def cancel_registration(
    data: CancelRegistrationRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """取消报名(报名中/进行中状态可取消)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.cancel_registration(data.activityId, data.userId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/arena/score", tags=["活动管理模块"])
async def submit_arena_score(
    data: SubmitArenaScoreRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """提交擂台赛分数(自动排名)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.submit_arena_score(
            activity_id=data.activityId,
            user_id=data.userId,
            score=data.score,
            real_name=data.realName,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 管理端接口 ---

@router.post("/api/activity/admin/create", tags=["活动管理模块"])
async def create_activity(
    data: CreateActivityRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建活动(初始状态: 草稿)"""
    _require_admin(x_role)
    try:
        result = await _service.create_activity(
            name=data.name,
            type_=data.type,
            sub_type=data.subType,
            description=data.description,
            start_time=data.startTime,
            end_time=data.endTime,
            budget=data.budget,
            rules=data.rules,
            applicable_scope=data.applicableScope,
            created_by=data.createdBy,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/activity/admin/list", tags=["活动管理模块"])
async def list_admin_activities(
    status: str = Query(None, description="按状态筛选"),
    limit: int = Query(50, ge=1, le=200, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """管理端查询活动列表(含草稿)"""
    _require_admin(x_role)
    try:
        result = await _service.list_admin_activities(status=status, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/admin/transition/{activity_id}", tags=["活动管理模块"])
async def transition_status(
    activity_id: int,
    data: TransitionStatusRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """活动状态流转(草稿→报名中→进行中→已结束)"""
    _require_admin(x_role)
    try:
        result = await _service.transition_status(
            activity_id=activity_id,
            target_status=data.targetStatus,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/activity/admin/audit/{activity_id}", tags=["活动管理模块"])
async def audit_activity(
    activity_id: int,
    data: AuditActivityRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """活动审核(草稿→报名中 or 拒绝)"""
    _require_admin(x_role)
    try:
        result = await _service.audit_activity(
            activity_id=activity_id,
            approve=data.approve,
            auditor=data.auditor,
            reason=data.reason,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_activity_routes(app):
    """注册活动管理模块路由"""
    app.include_router(router)
