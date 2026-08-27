"""权限AI智能管理模块路由(P0 核心闭环, 15 端点)

鉴权(复用 auth_routes 依赖, JWT 强校验):
    - get_current_member: 登录即可(权限树/我的权限/申请/责任书)
    - require_admin:      超级管理员(直授/吊销/角色模板/全局视图)
    - 审批人校验在 service 层(当前级候选审批人或超管)

异常映射(遵循项目约定):
    - KeyError → 404(权限点/授权/申请单不存在)
    - ValueError → 409(参数非法/SoD 冲突/状态非法/越级审批)
    - PermissionError → 403(无权限/未签责任书/非审批人)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from routes.auth_routes import get_current_member, require_admin
from services.perm_service import PermService


router = APIRouter()
_service = PermService()


def _member_id(member: dict) -> int:
    """从已鉴权会员上下文提取操作人 ID(Token 载荷, 不可伪造)"""
    try:
        return int(member.get("memberId", 0))
    except (TypeError, ValueError):
        return 0


def _handle(exc: Exception):
    """统一异常映射: KeyError → 404, ValueError → 409, PermissionError → 403"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class CreateRoleRequest(PydBaseModel):
    name: str = Field(..., min_length=1, max_length=30, description="角色名称")
    stage: str = Field(..., description="生产环节(purchase/production/storage/"
                                        "logistics/sales/aftersale/finance)")
    nodeCodes: list[str] = Field(..., min_length=1,
                                 description="权限码集合")


class AssignGrantRequest(PydBaseModel):
    memberId: int = Field(..., ge=1, description="被授权会员ID")
    nodeCode: str = Field(..., description="权限码(如 production.operate)")
    durationDays: int | None = Field(None, ge=1, le=90,
                                     description="授权期限(天, 默认按敏感级)")


class SubmitRequestRequest(PydBaseModel):
    nodeCode: str = Field(..., description="申请权限码")
    reason: str = Field(..., min_length=5, max_length=200,
                        description="申请理由(≥5字)")
    durationDays: int | None = Field(None, ge=1, le=90,
                                     description="申请期限(天, 默认按敏感级)")


class ApproveRequestRequest(PydBaseModel):
    action: str = Field(..., description="approve(同意)/reject(驳回)")
    opinion: str = Field("", max_length=200, description="审批意见")


class CheckPermissionRequest(PydBaseModel):
    nodeCode: str = Field(..., description="权限码(校验本人是否持有)")


# ============================================================
# 全员端(登录即可, 7 接口)
# ============================================================

@router.get("/api/perm/nodes", tags=["权限AI智能管理"])
async def list_nodes(member: dict = Depends(get_current_member)):
    """权限树(生产流程 7 环节 × 4 操作级, 按环节分组展示)"""
    nodes = await _service.list_nodes()
    grouped: dict[str, list[dict]] = {}
    for n in nodes:
        grouped.setdefault(n["stageName"], []).append(n)
    return {"total": len(nodes), "stages": grouped}


@router.get("/api/perm/roles", tags=["权限AI智能管理"])
async def list_roles(member: dict = Depends(get_current_member)):
    """角色模板列表"""
    return {"roles": await _service.list_roles()}


@router.get("/api/perm/my/grants", tags=["权限AI智能管理"])
async def my_grants(member: dict = Depends(get_current_member)):
    """我的权限(含到期倒计时/责任书状态/责任清单)"""
    return {"grants": await _service.list_my_grants(_member_id(member))}


@router.post("/api/perm/grants/{grant_id}/duty-sign",
             tags=["权限AI智能管理"])
async def sign_duty(grant_id: int,
                    member: dict = Depends(get_current_member)):
    """签署责任书(权责共存: 未签署则权限校验阻断)"""
    try:
        return await _service.sign_duty(_member_id(member), grant_id)
    except Exception as exc:
        _handle(exc)


@router.post("/api/perm/requests", tags=["权限AI智能管理"])
async def submit_request(data: SubmitRequestRequest,
                         member: dict = Depends(get_current_member)):
    """提交权限申请(AI 预检: SoD 冲突/重复申请/重复持有拦截)"""
    try:
        return await _service.submit_request(
            _member_id(member), data.nodeCode, data.reason,
            data.durationDays)
    except Exception as exc:
        _handle(exc)


@router.get("/api/perm/requests", tags=["权限AI智能管理"])
async def list_requests(member: dict = Depends(get_current_member)):
    """我的申请 + 待我审批(按身份聚合)"""
    try:
        return await _service.list_requests(_member_id(member))
    except Exception as exc:
        _handle(exc)


@router.post("/api/perm/requests/{request_id}/approve",
             tags=["权限AI智能管理"])
async def approve_request(request_id: int, data: ApproveRequestRequest,
                          member: dict = Depends(get_current_member)):
    """逐级审批(同意/驳回): 仅当前级候选审批人或超管可操作"""
    try:
        return await _service.approve_request(
            _member_id(member), request_id, data.action, data.opinion)
    except Exception as exc:
        _handle(exc)


@router.post("/api/perm/requests/{request_id}/cancel",
             tags=["权限AI智能管理"])
async def cancel_request(request_id: int,
                         member: dict = Depends(get_current_member)):
    """撤回申请(仅申请人本人, 仅 pending)"""
    try:
        return await _service.cancel_request(_member_id(member), request_id)
    except Exception as exc:
        _handle(exc)


@router.post("/api/perm/check", tags=["权限AI智能管理"])
async def check_permission(data: CheckPermissionRequest,
                           member: dict = Depends(get_current_member)):
    """权限校验(演示/联调用: 校验本人是否持有某权限)"""
    try:
        return await _service.check_permission(
            _member_id(member), data.nodeCode)
    except Exception as exc:
        _handle(exc)


# ============================================================
# 超管端(JWT + role=admin, 5 接口)
# ============================================================

@router.post("/api/perm/roles", tags=["权限AI智能管理"])
async def create_role(data: CreateRoleRequest,
                      admin: dict = Depends(require_admin)):
    """创建角色模板(仅超管)"""
    try:
        return await _service.create_role(
            _member_id(admin), data.name, data.stage, data.nodeCodes)
    except Exception as exc:
        _handle(exc)


@router.post("/api/perm/grants", tags=["权限AI智能管理"])
async def assign_grant(data: AssignGrantRequest,
                       admin: dict = Depends(require_admin)):
    """超管直授主要权限(SoD 硬拦截, 免申请流, 仍需签责任书+限时)"""
    try:
        return await _service.assign_grant(
            _member_id(admin), data.memberId, data.nodeCode,
            data.durationDays)
    except Exception as exc:
        _handle(exc)


@router.delete("/api/perm/grants/{grant_id}", tags=["权限AI智能管理"])
async def revoke_grant(grant_id: int,
                       admin: dict = Depends(require_admin)):
    """吊销授权(仅超管)"""
    try:
        return await _service.revoke_grant(_member_id(admin), grant_id)
    except Exception as exc:
        _handle(exc)


@router.get("/api/perm/admin/grants", tags=["权限AI智能管理"])
async def admin_list_grants(
    status: str | None = Query(None, description="按状态过滤"),
    admin: dict = Depends(require_admin),
):
    """全部授权视图(仅超管, 附会员昵称)"""
    try:
        return {"grants": await _service.admin_list_grants(
            _member_id(admin), status=status)}
    except Exception as exc:
        _handle(exc)


@router.get("/api/perm/admin/logs", tags=["权限AI智能管理"])
async def admin_list_logs(
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(require_admin),
):
    """AI 监控审计日志(仅超管)"""
    try:
        return {"logs": await _service.admin_list_logs(
            _member_id(admin), limit=limit)}
    except Exception as exc:
        _handle(exc)


@router.post("/api/perm/admin/expire-sweep", tags=["权限AI智能管理"])
async def expire_sweep(admin: dict = Depends(require_admin)):
    """手动触发到期回收(访问时亦有惰性过期)"""
    try:
        return await _service.expire_sweep(_member_id(admin))
    except Exception as exc:
        _handle(exc)


def register_perm_routes(app) -> None:
    """向 FastAPI 应用注册权限AI智能管理模块路由"""
    app.include_router(router)
