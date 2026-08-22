"""AI 智能维护模块路由(12 端点)

鉴权:
    - 管理端: X-Role: admin 头(全部接口需管理员权限)

端点分布:
    - 维护任务(3):   tasks-create / tasks-list / tasks-update
    - 健康检查(3):   health-create / health-list / health-detail
    - 故障自愈(2):   recovery-create / recovery-list
    - 性能优化(2):   optimizations-create / optimizations-list
    - 一键巡检(1):   inspect-all
    - 统计(1):       stats

路由顺序: 静态路径优先于动态路径注册。
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.maintenance_service import MaintenanceService
from repositories.maintenance_repository import (
    # 任务状态
    TASK_STATUS_PENDING, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS,
    TASK_STATUS_FAILED, TASK_STATUS_CANCELLED,
    # 任务类型
    TASK_TYPE_BACKUP, TASK_TYPE_CLEANUP, TASK_TYPE_OPTIMIZE,
    TASK_TYPE_INSPECT, TASK_TYPE_RESTART, TASK_TYPE_SCALE,
    # 触发类型
    TRIGGER_MANUAL, TRIGGER_SCHEDULED,
    # 健康状态
    HEALTH_HEALTHY, HEALTH_DEGRADED, HEALTH_UNHEALTHY, HEALTH_UNKNOWN,
    # 检查类型
    CHECK_TYPE_HTTP, CHECK_TYPE_TCP, CHECK_TYPE_RESOURCE, CHECK_TYPE_CUSTOM,
    # 自愈状态
    RECOVERY_STATUS_DETECTED, RECOVERY_STATUS_DIAGNOSING,
    RECOVERY_STATUS_RECOVERING, RECOVERY_STATUS_RECOVERED,
    RECOVERY_STATUS_FAILED, RECOVERY_STATUS_MANUAL_REQUIRED,
    # 自愈级别
    RECOVERY_LEVEL_AUTO, RECOVERY_LEVEL_ASSISTED, RECOVERY_LEVEL_MANUAL,
    # 优化状态
    OPTIMIZATION_STATUS_PROPOSED, OPTIMIZATION_STATUS_APPROVED,
    OPTIMIZATION_STATUS_EXECUTING, OPTIMIZATION_STATUS_COMPLETED,
    OPTIMIZATION_STATUS_REJECTED,
)


router = APIRouter()
_service = MaintenanceService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_admin(x_role: Optional[str]):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


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

class TaskCreateRequest(PydBaseModel):
    taskName: str = Field(..., description="任务名称")
    taskType: str = Field(..., description="任务类型(backup/cleanup/optimize/inspect/restart/scale)")
    target: str = Field(..., description="维护目标")
    triggerType: str = Field(TRIGGER_MANUAL, description="触发类型(manual/scheduled)")
    params: Dict[str, Any] = Field(default_factory=dict, description="任务参数")
    schedule: str = Field("", description="调度表达式")
    aiAutomationRate: float = Field(90.0, description="AI自动化率")


class TaskUpdateRequest(PydBaseModel):
    action: str = Field(..., description="动作(execute/cancel)")
    result: Dict[str, Any] = Field(default_factory=dict, description="执行结果")
    errorMessage: str = Field("", description="错误信息")


class HealthCheckRequest(PydBaseModel):
    checkName: str = Field(..., description="检查项名称")
    serviceName: str = Field(..., description="服务名称")
    checkType: str = Field(..., description="检查类型(http/tcp/resource/custom)")
    checkConfig: Dict[str, Any] = Field(default_factory=dict, description="检查配置")
    threshold: Dict[str, Any] = Field(default_factory=dict, description="阈值配置")
    healthStatus: Optional[str] = Field(None, description="健康状态(执行时指定)")
    responseTime: int = Field(0, description="响应时间(毫秒)")


class RecoveryRequest(PydBaseModel):
    faultType: str = Field(..., description="故障类型")
    faultSource: str = Field(..., description="故障来源")
    faultDescription: str = Field("", description="故障描述")
    recoveryLevel: str = Field(RECOVERY_LEVEL_AUTO, description="自愈级别(auto/assisted/manual)")
    aiAutomationRate: float = Field(90.0, description="AI自动化率")


class OptimizationRequest(PydBaseModel):
    optimizationType: str = Field(..., description="优化类型")
    target: str = Field(..., description="优化目标")
    proposal: str = Field(..., description="优化建议")
    expectedBenefit: Dict[str, Any] = Field(default_factory=dict, description="预期收益")
    executionPlan: Dict[str, Any] = Field(default_factory=dict, description="执行计划")
    aiAutomationRate: float = Field(85.0, description="AI自动化率")


# ============================================================
# P0 接口(12 个)
# ============================================================

# --- 维护任务 ---

@router.post("/api/maintenance/tasks", tags=["AI智能维护模块"])
async def create_task(
    data: TaskCreateRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建维护任务(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.create_task(
            task_name=data.taskName,
            task_type=data.taskType,
            target=data.target,
            trigger_type=data.triggerType,
            params=data.params,
            schedule=data.schedule,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/maintenance/tasks", tags=["AI智能维护模块"])
async def list_tasks(
    task_type: str = Query(None, description="按任务类型筛选"),
    task_status: str = Query(None, description="按任务状态筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询维护任务列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_tasks(task_type, task_status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.put("/api/maintenance/tasks/{task_id}", tags=["AI智能维护模块"])
async def update_task(
    task_id: int,
    data: TaskUpdateRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """执行/取消维护任务(管理员)

    action:
        - execute: 执行任务(pending → running → success/failed)
        - cancel:  取消任务(pending/running → cancelled)
    """
    _require_admin(x_role)
    try:
        if data.action == "execute":
            result = await _service.execute_task(
                task_id,
                result=data.result,
                error_message=data.errorMessage,
            )
        elif data.action == "cancel":
            result = await _service.cancel_task(task_id)
        else:
            raise ValueError(f"非法动作: {data.action}(须为 execute/cancel)")
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 健康检查 ---

@router.post("/api/maintenance/health", tags=["AI智能维护模块"])
async def create_health_check(
    data: HealthCheckRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建并执行健康检查(管理员)"""
    _require_admin(x_role)
    try:
        # 创建检查项
        result = await _service.create_health_check(
            check_name=data.checkName,
            service_name=data.serviceName,
            check_type=data.checkType,
            check_config=data.checkConfig,
            threshold=data.threshold,
        )
        # 立即执行检查(若指定 healthStatus)
        if data.healthStatus:
            result = await _service.run_health_check(
                result["id"],
                health_status=data.healthStatus,
                response_time=data.responseTime,
            )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/maintenance/health", tags=["AI智能维护模块"])
async def list_health_checks(
    service_name: str = Query(None, description="按服务名称筛选"),
    health_status: str = Query(None, description="按健康状态筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询健康检查列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_health_checks(service_name, health_status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/maintenance/health/{check_id}", tags=["AI智能维护模块"])
async def get_health_check(
    check_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """查询健康检查详情(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_health_check(check_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 故障自愈 ---

@router.post("/api/maintenance/recovery", tags=["AI智能维护模块"])
async def detect_fault(
    data: RecoveryRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """检测故障并创建自愈记录(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.detect_fault(
            fault_type=data.faultType,
            fault_source=data.faultSource,
            fault_description=data.faultDescription,
            recovery_level=data.recoveryLevel,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/maintenance/recovery", tags=["AI智能维护模块"])
async def list_recoveries(
    fault_type: str = Query(None, description="按故障类型筛选"),
    recovery_status: str = Query(None, description="按自愈状态筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询自愈记录列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_recoveries(fault_type, recovery_status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 性能优化 ---

@router.post("/api/maintenance/optimizations", tags=["AI智能维护模块"])
async def propose_optimization(
    data: OptimizationRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """提交性能优化建议(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.propose_optimization(
            optimization_type=data.optimizationType,
            target=data.target,
            proposal=data.proposal,
            expected_benefit=data.expectedBenefit,
            execution_plan=data.executionPlan,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/maintenance/optimizations", tags=["AI智能维护模块"])
async def list_optimizations(
    optimization_type: str = Query(None, description="按优化类型筛选"),
    optimization_status: str = Query(None, description="按优化状态筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询优化建议列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_optimizations(
            optimization_type, optimization_status, limit
        )
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 一键巡检 ---

@router.post("/api/maintenance/inspect", tags=["AI智能维护模块"])
async def inspect_all(
    x_role: str = Header(None, alias="X-Role"),
):
    """一键巡检全服务(管理员)

    自动对所有核心服务执行健康检查,
    发现不健康服务自动创建故障自愈记录。
    """
    _require_admin(x_role)
    try:
        result = await _service.inspect_all()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 统计 ---

@router.get("/api/maintenance/stats", tags=["AI智能维护模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """维护模块统计(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_maintenance_routes(app):
    """注册 AI 智能维护模块路由"""
    app.include_router(router)
