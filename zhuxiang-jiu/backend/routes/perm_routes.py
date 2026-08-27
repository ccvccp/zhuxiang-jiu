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
from services.perm_ai_service import PermAiService


router = APIRouter()
_service = PermService()
_ai_service = PermAiService()


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


class RecordUseRequest(PydBaseModel):
    nodeCode: str = Field(..., description="使用的权限码")
    bulkCount: int = Field(0, ge=0, le=100000,
                           description="批量操作条数(导出/查询, >100 触发风控)")


class RiskReviewRequest(PydBaseModel):
    action: str = Field(..., description="unfreeze(解冻)/revoke(维持吊销)")
    opinion: str = Field("", max_length=200, description="复核意见")


class RunAssessmentRequest(PydBaseModel):
    period: str | None = Field(None, description="考核周期(YYYY-MM, 默认当月)")
    force: bool = Field(False, description="重跑本期(覆盖幂等)")


class SetDelegateRequest(PydBaseModel):
    delegateToId: int = Field(..., ge=1, description="代理人会员ID")


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


@router.post("/api/perm/use", tags=["权限AI智能管理"])
async def record_use(data: RecordUseRequest,
                     member: dict = Depends(get_current_member)):
    """记录权限使用(P1 AI 监控: 异常时段/频率/批量导出 → 风险评分 4 级处置)"""
    try:
        return await _ai_service.record_use(
            _member_id(member), data.nodeCode, data.bulkCount)
    except Exception as exc:
        _handle(exc)


@router.get("/api/perm/my/scores", tags=["权限AI智能管理"])
async def my_scores(member: dict = Depends(get_current_member)):
    """我的权责信用分+考核报告(近 12 期)"""
    try:
        return {"scores": await _ai_service.my_scores(_member_id(member))}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 全员端 P2(代理审批委托, 3 接口)
# ============================================================

@router.get("/api/perm/my/delegates", tags=["权限AI智能管理"])
async def my_delegates(member: dict = Depends(get_current_member)):
    """我的代理委托(我设的代理人 + 受托我代批的)"""
    try:
        return await _service.my_delegates(_member_id(member))
    except Exception as exc:
        _handle(exc)


@router.post("/api/perm/my/delegates", tags=["权限AI智能管理"])
async def set_delegate(data: SetDelegateRequest,
                       member: dict = Depends(get_current_member)):
    """设置代理审批人(覆盖式, 每人同时仅 1 个生效代理人)"""
    try:
        return await _service.set_delegate(
            _member_id(member), data.delegateToId)
    except Exception as exc:
        _handle(exc)


@router.delete("/api/perm/my/delegates", tags=["权限AI智能管理"])
async def cancel_delegate(member: dict = Depends(get_current_member)):
    """取消我的代理委托"""
    try:
        return await _service.cancel_delegate(_member_id(member))
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


# ============================================================
# 超管端 P1(AI 监控 + 信用分考核, 4 接口)
# ============================================================

@router.get("/api/perm/admin/risk-summary", tags=["权限AI智能管理"])
async def risk_summary(admin: dict = Depends(require_admin)):
    """AI 风险概览(各级事件统计 + 待复核列表)"""
    try:
        return await _ai_service.risk_summary(_member_id(admin))
    except Exception as exc:
        _handle(exc)


@router.post("/api/perm/admin/risk-review/{log_id}",
             tags=["权限AI智能管理"])
async def risk_review(log_id: int, data: RiskReviewRequest,
                      admin: dict = Depends(require_admin)):
    """高危风险事件复核(解冻/维持吊销)"""
    try:
        return await _ai_service.review_risk(
            _member_id(admin), log_id, data.action, data.opinion)
    except Exception as exc:
        _handle(exc)


@router.post("/api/perm/admin/scores/run", tags=["权限AI智能管理"])
async def run_assessment(data: RunAssessmentRequest,
                         admin: dict = Depends(require_admin)):
    """触发月度权责信用分考核(自动执行奖惩: 奖金入钱包/积分/降权/冻结)"""
    try:
        return await _ai_service.run_assessment(
            _member_id(admin), period=data.period, force=data.force)
    except Exception as exc:
        _handle(exc)


@router.get("/api/perm/admin/scores", tags=["权限AI智能管理"])
async def admin_list_scores(
    period: str | None = Query(None, description="按周期过滤(YYYY-MM)"),
    admin: dict = Depends(require_admin),
):
    """全部考核记录(仅超管, 附会员昵称)"""
    try:
        return {"scores": await _ai_service.admin_list_scores(
            _member_id(admin), period=period)}
    except Exception as exc:
        _handle(exc)


# ============================================================
# 超管端 P2(超时升级扫描 + AI 角色推荐, 2 接口)
# ============================================================

@router.post("/api/perm/admin/escalation-sweep",
             tags=["权限AI智能管理"])
async def escalation_sweep(admin: dict = Depends(require_admin)):
    """批量超时升级扫描(48h 未批的申请单追加上一级候选审批人)"""
    try:
        return await _service.escalation_sweep(_member_id(admin))
    except Exception as exc:
        _handle(exc)


@router.get("/api/perm/admin/recommend", tags=["权限AI智能管理"])
async def recommend_role(
    position: str = Query(..., min_length=1, max_length=50,
                          description="岗位名称(如 酿造车间主管)"),
    admin: dict = Depends(require_admin),
):
    """AI 角色配置推荐(岗位关键词→环节匹配 + 职级定级→权限码组合)"""
    try:
        return await _ai_service.recommend_role(position)
    except Exception as exc:
        _handle(exc)


def register_perm_routes(app) -> None:
    """向 FastAPI 应用注册权限AI智能管理模块路由"""
    app.include_router(router)
