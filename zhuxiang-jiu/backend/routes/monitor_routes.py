"""AI 智能监控模块路由(12 端点)

鉴权:
    - 管理端: X-Role: admin 头(全部接口需管理员权限)

端点分布:
    - 指标采集(2): metrics-collect / metrics-list
    - 告警管理(3): alert-create / alert-list / alert-update
    - 故障事件(3): incident-create / incident-list / incident-update
    - 仪表盘(2):   dashboard-create / dashboard-list
    - 健康检查(1): health
    - 统计(1):     stats

注: 静态路径(/health, /stats)先于动态路径注册(项目约定)
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.monitor_service import MonitorService


router = APIRouter()
_service = MonitorService()


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

class MetricCollectRequest(PydBaseModel):
    metricName: str = Field(..., description="指标名称")
    metricType: str = Field(..., description="指标类型(system/business/performance/error)")
    metricValue: float = Field(..., description="指标数值")
    source: str = Field(..., description="采集来源")
    metricUnit: str = Field("", description="单位")
    tags: Dict[str, Any] = Field(default_factory=dict, description="标签")
    threshold: Dict[str, Any] = Field(default_factory=dict, description="阈值配置")
    aiAutomationRate: float = Field(95.0, description="AI自动化率")


class AlertCreateRequest(PydBaseModel):
    alertName: str = Field(..., description="告警名称")
    alertType: str = Field(..., description="告警类型")
    alertLevel: str = Field(..., description="告警级别(info/warning/critical/fatal)")
    source: str = Field(..., description="告警来源")
    metricId: Optional[int] = Field(None, description="关联指标ID")
    threshold: Dict[str, Any] = Field(default_factory=dict, description="触发阈值")
    currentValue: float = Field(0.0, description="当前值")
    description: str = Field("", description="告警描述")
    notification: Dict[str, Any] = Field(default_factory=dict, description="通知配置")
    aiAutomationRate: float = Field(95.0, description="AI自动化率")


class AlertUpdateRequest(PydBaseModel):
    """告警状态流转(action 决定目标态)"""
    action: str = Field(..., description="动作(acknowledge/resolve/suppress)")
    operator: str = Field("admin", description="操作人")


class IncidentCreateRequest(PydBaseModel):
    incidentName: str = Field(..., description="故障名称")
    incidentType: str = Field(..., description="故障类型")
    incidentLevel: str = Field(..., description="故障级别(P0/P1/P2/P3)")
    source: str = Field(..., description="故障来源")
    impact: Dict[str, Any] = Field(default_factory=dict, description="影响范围")
    alertIds: List[int] = Field(default_factory=list, description="关联告警ID列表")
    assignee: str = Field("", description="责任人")
    aiAutomationRate: float = Field(85.0, description="AI自动化率")


class IncidentUpdateRequest(PydBaseModel):
    """故障状态流转(action 决定目标态)"""
    action: str = Field(...,
                        description="动作(investigate/mitigate/resolve/postmortem)")
    operator: str = Field("admin", description="操作人")
    rootCause: str = Field("", description="根因(investigate 时可填)")
    mitigation: str = Field("", description="处置措施(mitigate 时可填)")
    postmortemDoc: str = Field("", description="复盘文档(postmortem 时可填)")


class DashboardCreateRequest(PydBaseModel):
    dashboardName: str = Field(..., description="仪表盘名称")
    dashboardType: str = Field(...,
                                description="仪表盘类型(system/business/incident/custom)")
    owner: str = Field("admin", description="所属用户")
    widgets: List[Dict[str, Any]] = Field(default_factory=list, description="组件配置")
    layout: Dict[str, Any] = Field(default_factory=dict, description="布局配置")
    filters: Dict[str, Any] = Field(default_factory=dict, description="全局过滤器")
    refreshInterval: int = Field(30, description="刷新间隔(秒)")
    isShared: bool = Field(False, description="是否共享")
    aiAutomationRate: float = Field(85.0, description="AI自动化率")


# ============================================================
# P0 接口(12 个)
# 注: 静态路径(/health, /stats)先于动态路径注册
# ============================================================

# --- 健康检查 ---

@router.get("/api/monitor/health", tags=["AI智能监控模块"])
async def health_check(
    x_role: str = Header(None, alias="X-Role"),
):
    """健康检查(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.health_check()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 统计 ---

@router.get("/api/monitor/stats", tags=["AI智能监控模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """监控统计(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 指标采集 ---

@router.post("/api/monitor/metrics", tags=["AI智能监控模块"])
async def collect_metric(
    data: MetricCollectRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """采集监控指标(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.collect_metric(
            metric_name=data.metricName,
            metric_type=data.metricType,
            metric_value=data.metricValue,
            source=data.source,
            metric_unit=data.metricUnit,
            tags=data.tags,
            threshold=data.threshold,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/monitor/metrics", tags=["AI智能监控模块"])
async def list_metrics(
    metric_name: str = Query(None, description="按指标名称筛选"),
    metric_type: str = Query(None, description="按指标类型筛选"),
    source: str = Query(None, description="按来源筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询指标列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_metrics(metric_name, metric_type, source, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 告警管理 ---

@router.post("/api/monitor/alerts", tags=["AI智能监控模块"])
async def create_alert(
    data: AlertCreateRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建告警(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.raise_alert(
            alert_name=data.alertName,
            alert_type=data.alertType,
            alert_level=data.alertLevel,
            source=data.source,
            metric_id=data.metricId,
            threshold=data.threshold,
            current_value=data.currentValue,
            description=data.description,
            notification=data.notification or None,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/monitor/alerts", tags=["AI智能监控模块"])
async def list_alerts(
    alert_type: str = Query(None, description="按告警类型筛选"),
    alert_level: str = Query(None, description="按告警级别筛选"),
    alert_status: str = Query(None, description="按告警状态筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询告警列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_alerts(alert_type, alert_level, alert_status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.put("/api/monitor/alerts/{alert_id}", tags=["AI智能监控模块"])
async def update_alert(
    alert_id: int,
    data: AlertUpdateRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """告警状态流转(管理员)

    action 取值:
        - acknowledge: pending → acknowledged
        - resolve:     acknowledged → resolved
        - suppress:    任意态 → suppressed
    """
    _require_admin(x_role)
    try:
        if data.action == "acknowledge":
            result = await _service.acknowledge_alert(alert_id, data.operator)
        elif data.action == "resolve":
            result = await _service.resolve_alert(alert_id, data.operator)
        elif data.action == "suppress":
            result = await _service.suppress_alert(alert_id, data.operator)
        else:
            raise ValueError(f"非法动作: {data.action}(须为 acknowledge/resolve/suppress)")
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 故障事件 ---

@router.post("/api/monitor/incidents", tags=["AI智能监控模块"])
async def create_incident(
    data: IncidentCreateRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建故障事件(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.raise_incident(
            incident_name=data.incidentName,
            incident_type=data.incidentType,
            incident_level=data.incidentLevel,
            source=data.source,
            impact=data.impact,
            alert_ids=data.alertIds,
            assignee=data.assignee,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/monitor/incidents", tags=["AI智能监控模块"])
async def list_incidents(
    incident_type: str = Query(None, description="按故障类型筛选"),
    incident_level: str = Query(None, description="按故障级别筛选"),
    incident_status: str = Query(None, description="按故障状态筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询故障列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_incidents(incident_type, incident_level,
                                                  incident_status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.put("/api/monitor/incidents/{incident_id}", tags=["AI智能监控模块"])
async def update_incident(
    incident_id: int,
    data: IncidentUpdateRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """故障状态流转(管理员)

    action 取值:
        - investigate: detected → investigating
        - mitigate:    investigating → mitigating
        - resolve:     mitigating → resolved
        - postmortem:  resolved → postmortem
    """
    _require_admin(x_role)
    try:
        if data.action == "investigate":
            result = await _service.investigate_incident(incident_id, data.operator,
                                                          data.rootCause)
        elif data.action == "mitigate":
            result = await _service.mitigate_incident(incident_id, data.operator,
                                                        data.mitigation)
        elif data.action == "resolve":
            result = await _service.resolve_incident(incident_id, data.operator)
        elif data.action == "postmortem":
            result = await _service.postmortem_incident(incident_id, data.operator,
                                                          data.postmortemDoc)
        else:
            raise ValueError(
                f"非法动作: {data.action}(须为 investigate/mitigate/resolve/postmortem)"
            )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 仪表盘 ---

@router.post("/api/monitor/dashboards", tags=["AI智能监控模块"])
async def create_dashboard(
    data: DashboardCreateRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建仪表盘(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.create_dashboard(
            dashboard_name=data.dashboardName,
            dashboard_type=data.dashboardType,
            owner=data.owner,
            widgets=data.widgets,
            layout=data.layout,
            filters=data.filters,
            refresh_interval=data.refreshInterval,
            is_shared=data.isShared,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/monitor/dashboards", tags=["AI智能监控模块"])
async def list_dashboards(
    dashboard_type: str = Query(None, description="按仪表盘类型筛选"),
    owner: str = Query(None, description="按所属用户筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询仪表盘列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_dashboards(dashboard_type, owner, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


def register_monitor_routes(app):
    """注册 AI 智能监控模块路由"""
    app.include_router(router)
