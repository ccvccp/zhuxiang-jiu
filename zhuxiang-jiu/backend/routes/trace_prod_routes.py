"""产品溯源管理模块路由(14 端点)

鉴权(复用 auth_routes 依赖, 与 33 号权限模块联动):
    - 生产端(7): JWT 登录, 打卡/出库等在 service 层校验环节权限
    - 公开端(2): 批次溯源时间线 / AI 健康度(C 端消费)
    - 管理端(5): 超管(异常事件/审计/工段编辑/阻断解锁/统计)

异常映射:
    - KeyError → 404(工段/批次不存在)
    - ValueError → 409(参数/状态非法/质检结论缺失)
    - PermissionError → 403(无权限/未签责任书/质检阻断)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from routes.auth_routes import get_current_member, require_admin
from services.trace_prod_service import TraceProdService


router = APIRouter()
_service = TraceProdService()


def _member_id(member: dict) -> int:
    try:
        return int(member.get("memberId", 0))
    except (TypeError, ValueError):
        return 0


def _handle(exc: Exception):
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

class CreateBatchRequest(PydBaseModel):
    batchNo: str = Field(..., min_length=3, max_length=40,
                         description="批次号(如 ZX52-2026L08)")
    productId: int = Field(1, ge=1, description="产品ID")
    plannedQty: int = Field(..., ge=1, le=1000000,
                            description="计划产量(瓶)")


class PunchRequest(PydBaseModel):
    stageCode: str = Field(..., description="工段码(扫码所得, 如 STG-BREW)")
    batchNo: str = Field(..., description="批次号")
    qcConclusion: str = Field("", max_length=200,
                              description="质检结论(质检关卡必填)")
    params: dict | None = Field(None,
                                description="工艺参数快照(温度/坛号/数量等)")


class BindCodesRequest(PydBaseModel):
    lifeCodes: list[str] = Field(..., min_length=1,
                                 description="瓶码列表(出库前绑定)")


class UnblockRequest(PydBaseModel):
    reason: str = Field("", max_length=200, description="解除原因")


class UpdateStageRequest(PydBaseModel):
    maxDwellHours: int | None = Field(None, ge=0, le=24 * 365)
    isQcGate: bool | None = None
    name: str | None = Field(None, min_length=1, max_length=30)
    desc: str | None = Field(None, max_length=200)


# ============================================================
# 生产端(7 接口, JWT)
# ============================================================

@router.get("/api/trace-prod/stages", tags=["产品溯源管理"])
async def list_stages(member: dict = Depends(get_current_member)):
    """工段定义列表(7 工段, 附责任人候选=环节权限持有者)"""
    return {"stages": await _service.list_stages()}


@router.post("/api/trace-prod/batches", tags=["产品溯源管理"])
async def create_batch(data: CreateBatchRequest,
                       member: dict = Depends(get_current_member)):
    """创建生产批次(生产环节权限)"""
    try:
        return await _service.create_batch(
            _member_id(member), data.batchNo, data.productId,
            data.plannedQty)
    except Exception as exc:
        _handle(exc)


@router.get("/api/trace-prod/batches", tags=["产品溯源管理"])
async def list_batches(
    status: str | None = Query(None,
                               description="producing/released/blocked"),
    member: dict = Depends(get_current_member),
):
    """批次列表"""
    return {"batches": await _service.list_batches(status=status)}


@router.post("/api/trace-prod/punch", tags=["产品溯源管理"])
async def punch(data: PunchRequest,
                member: dict = Depends(get_current_member)):
    """工段扫码打卡(权限即责任: 环节权限校验+AI 流转异常检测+链式哈希)"""
    try:
        return await _service.punch(
            _member_id(member), data.stageCode, data.batchNo,
            data.qcConclusion, data.params)
    except Exception as exc:
        _handle(exc)


@router.get("/api/trace-prod/batches/{batch_no}/chain",
            tags=["产品溯源管理"])
async def batch_chain(batch_no: str,
                      member: dict = Depends(get_current_member)):
    """批次完整生产溯源链(含链完整性校验)"""
    try:
        return await _service.batch_chain(batch_no)
    except Exception as exc:
        _handle(exc)


@router.post("/api/trace-prod/batches/{batch_no}/bind-codes",
             tags=["产品溯源管理"])
async def bind_codes(batch_no: str, data: BindCodesRequest,
                     member: dict = Depends(get_current_member)):
    """出库前绑定瓶码(仓储环节权限)"""
    try:
        return await _service.bind_life_codes(
            _member_id(member), batch_no, data.lifeCodes)
    except Exception as exc:
        _handle(exc)


@router.post("/api/trace-prod/batches/{batch_no}/release",
             tags=["产品溯源管理"])
async def release_batch(batch_no: str,
                        member: dict = Depends(get_current_member)):
    """出库放行(物流环节权限; 须 7 工段全完成+瓶码已绑定)"""
    try:
        return await _service.release_batch(_member_id(member),
                                            batch_no)
    except Exception as exc:
        _handle(exc)


# ============================================================
# 公开端(2 接口, C 端消费)
# ============================================================

@router.get("/api/trace-prod/public/{batch_no}", tags=["产品溯源管理"])
async def public_trace(batch_no: str):
    """公开生产溯源时间线(责任人脱敏, 消费者扫码用)"""
    try:
        return await _service.public_trace(batch_no)
    except Exception as exc:
        _handle(exc)


# ============================================================
# 管理端(5 接口, 超管)
# ============================================================

@router.get("/api/trace-prod/admin/anomalies",
            tags=["产品溯源管理"])
async def admin_anomalies(
    limit: int = Query(100, ge=1, le=500),
    admin: dict = Depends(require_admin),
):
    """AI 异常打卡事件列表"""
    try:
        return {"anomalies": await _service.admin_anomalies(
            _member_id(admin), limit=limit)}
    except Exception as exc:
        _handle(exc)


@router.post("/api/trace-prod/admin/batches/{batch_no}/unblock",
             tags=["产品溯源管理"])
async def admin_unblock(batch_no: str, data: UnblockRequest,
                        admin: dict = Depends(require_admin)):
    """质检阻断解除"""
    try:
        return await _service.admin_unblock(
            _member_id(admin), batch_no, data.reason)
    except Exception as exc:
        _handle(exc)


@router.post("/api/trace-prod/admin/stages/{stage_id}",
             tags=["产品溯源管理"])
async def admin_update_stage(stage_id: int, data: UpdateStageRequest,
                             admin: dict = Depends(require_admin)):
    """编辑工段(阈值/质检关卡)"""
    try:
        return await _service.admin_update_stage(
            _member_id(admin), stage_id, data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


@router.get("/api/trace-prod/admin/stats", tags=["产品溯源管理"])
async def admin_stats(admin: dict = Depends(require_admin)):
    """溯源统计(批次/打卡/异常/健康度)"""
    try:
        return await _service.admin_stats(_member_id(admin))
    except Exception as exc:
        _handle(exc)


@router.get("/api/trace-prod/admin/punch-logs",
            tags=["产品溯源管理"])
async def admin_punch_logs(
    batch_no: str | None = Query(None, description="按批次过滤"),
    limit: int = Query(100, ge=1, le=500),
    admin: dict = Depends(require_admin),
):
    """全部打卡审计日志"""
    if not await _service.perm._is_super_admin(_member_id(admin)):
        raise HTTPException(status_code=403, detail="仅超级管理员可查看")
    return {"logs": await _service.repo.list_logs(batch_no=batch_no,
                                                  limit=limit)}


def register_trace_prod_routes(app) -> None:
    """向 FastAPI 应用注册产品溯源管理模块路由"""
    app.include_router(router)
