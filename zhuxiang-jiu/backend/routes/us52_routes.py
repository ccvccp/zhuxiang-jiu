"""52号·小竹语音可用性评估引擎路由(P0 指标注册表+决策规则)

端点(P0 共 5):
    GET  /api/us52/registry           指标注册表视图(admin)
    GET  /api/us52/dimensions         五维结构(admin)
    POST /api/us52/metrics/compute    指标快照计算(admin, US52_MODE=on)
    GET  /api/us52/metrics/latest      最近快照(admin)
    GET  /api/us52/metrics/snapshots   快照历史(admin)
    POST /api/us52/release-gate       上线门禁(admin)

鉴权: 管理端 X-Role: admin(43-51号同款口径)。
统一口径:
    - 治理面(registry/dimensions/查询)不受
      US52_MODE 数据面开关影响
    - 计算面 off=拒绝(409——测试停铁律,
      51号采集停同款语义)
    - 模块纯增量(零既有路由改动)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services.us52_service import Us52MetricsService

router = APIRouter(prefix="/api/us52",
                   tags=["小竹语音可用性评估(52号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")
    return x_role


class ComputeIn(BaseModel):
    metrics: dict
    sacrificeFlags: list[str] | None = None


class GateIn(BaseModel):
    metrics: dict
    sacrificeFlags: list[str] | None = None


@router.get("/registry")
async def get_registry(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """指标注册表视图(五维 20 项+基线+
    一票否决域+proxy 声明)"""
    _require_admin(x_role)
    return Us52MetricsService.registry()


@router.get("/dimensions")
async def get_dimensions(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """五维结构(看板分区用)"""
    _require_admin(x_role)
    return Us52MetricsService.dimensions_view()


@router.post("/metrics/compute")
async def compute_metrics(
        body: ComputeIn,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """指标快照计算(P0 手工注入框架——
    P1-P4 计算管道逐期接入)"""
    _require_admin(x_role)
    try:
        return await Us52MetricsService(
        ).compute_snapshot(body.metrics)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/metrics/latest")
async def latest_snapshot(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """最近快照(无则空态)"""
    _require_admin(x_role)
    return await Us52MetricsService().latest_snapshot()


@router.get("/metrics/snapshots")
async def list_snapshots(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """快照历史(最新在前)"""
    _require_admin(x_role)
    return await Us52MetricsService().list_snapshots()


@router.post("/release-gate")
async def release_gate(
        body: GateIn,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """上线门禁(一票否决判定——安全韧性任一
    未达即拒; 负向改进红线)"""
    _require_admin(x_role)
    return Us52MetricsService.release_gate(
        body.metrics, body.sacrificeFlags)


def register_us52_routes(app) -> None:
    """注册52号路由(main.py startup 调用)"""
    app.include_router(router)
