"""71号·AI智能支付端口大模型路由(P0-)

端点(P0 8):
    GET  /api/pay71/ports               端口池字典公示(admin, 观测面)
    GET  /api/pay71/ports/{id}          单端口详情(admin, 观测面)
    GET  /api/pay71/panorama            健康度全景(admin, 观测面——聚合 69号 P0 只读)
    POST /api/pay71/ports/{id}/state     端口态人工登记(admin, 人工——不受开关影响)
    POST /api/pay71/signals/report      前兆信号上报(admin, 快环观测——不受开关影响)
    GET  /api/pay71/signals             前兆信号留痕视图(admin, 观测面)
    GET  /api/pay71/events              全链事件视图(admin, 观测面)
    GET  /api/pay71/model/status        模型状态(admin, 观测面)

鉴权: 管理面 X-Role: admin(69号同款口径)。
统一口径(69号范式):
    - 观测面不受 PAY71_MODE 影响
    - 快环观测上报(signals report)
      不受开关影响
    - 人工面(port state)不受开关影响
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/pay71",
                   tags=["AI智能支付端口大模型(71号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


def _map(exc: Exception) -> HTTPException:
    """统一异常映射(69号口径)"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        return HTTPException(status_code=404,
                             detail=msg)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409,
                             detail=str(exc))
    return HTTPException(status_code=500,
                         detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class PortStateBody(BaseModel):
    portState: str = Field(description="端口态"
                           "(healthy/degraded/broken)")


class SignalReportBody(BaseModel):
    portId: str = Field(description="端口 ID")
    signal: str = Field(description="前兆信号"
                        "(latency_rising/errorcode_"
                        "climb/success_decline/"
                        "callback_delay)")
    latencyDelta: float = Field(
        default=0, description="延迟增量(ms)")
    errorRateDelta: float = Field(
        default=0, description="错误率增量")
    callbackMs: float = Field(
        default=0, description="回调耗时(ms)")


# ============================================================
# P0 端点
# ============================================================

@router.get("/ports")
async def ports(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """端口池字典公示(69号通道注册表只读
    消费+端口态/前兆信号/调配口径自描述
    ——观测面)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    return Pay71P0Service().port_dict()


@router.get("/ports/{port_id}")
async def port_detail(
        port_id: str,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """单端口详情(注册表只读+端口态
    扩展口径——观测面)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    try:
        return Pay71P0Service()\
            .port_detail(port_id)
    except Exception as e:
        raise _map(e) from e


@router.get("/panorama")
async def panorama(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """健康度全景视图(69号 P0 健康度
    观测+71号端口态聚合——只读消费
    铁律, 永不写 69号表)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    return await Pay71P0Service().panorama()


@router.post("/ports/{port_id}/state")
async def set_port_state(
        port_id: str,
        body: PortStateBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """端口态人工登记(P0 口径——人工
    专属; P1 引入保护方向自动熔断后
    本轨保留人工覆盖)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    try:
        return await Pay71P0Service()\
            .set_port_state(
                port_id, body.portState)
    except Exception as e:
        raise _map(e) from e


@router.post("/signals/report")
async def signal_report(
        body: SignalReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """前兆信号上报(快环观测——确定性
    权重叠加→预警留痕, 不受 PAY71_MODE
    影响; 预警仅观测, 阈值变更走慢环
    46号审批)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    try:
        return await Pay71P0Service()\
            .report_signal(
                body.portId, body.signal,
                body.latencyDelta,
                body.errorRateDelta,
                body.callbackMs)
    except Exception as e:
        raise _map(e) from e


@router.get("/signals")
async def signal_view(
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """前兆信号留痕视图(观测面)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    return await Pay71P0Service()\
        .signal_view(limit=limit)


@router.get("/events")
async def event_view(
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """全链事件视图(观测面)"""
    _require_admin(x_role)
    from repositories.pay71_repository import (
        Pay71Repository,
    )
    events = await Pay71Repository()\
        .list_events(limit=limit)
    return {"count": len(events),
            "events": events}


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(观测面——off 不受影响)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    return await Pay71P0Service().model_status()


def register_pay71_routes(app) -> None:
    """挂载 71号路由(main.py 惯例)"""
    app.include_router(router)
