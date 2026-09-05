"""52号·小竹语音可用性评估引擎路由(P0-P3)

端点(P0 6 + P1 3 + P2 1 + P3 1 = 11):
    GET  /api/us52/registry           指标注册表视图(admin)
    GET  /api/us52/dimensions         五维结构(admin)
    POST /api/us52/metrics/compute    指标快照计算(admin, US52_MODE=on)
    GET  /api/us52/metrics/latest      最近快照(admin)
    GET  /api/us52/metrics/snapshots   快照历史(admin)
    POST /api/us52/release-gate       上线门禁(admin)
    POST /api/us52/tests/run          执行测试任务集(P1, admin)
    GET  /api/us52/tests              测试会话历史(P1, admin)
    POST /api/us52/metrics/functional 功能可信度五指标计算(P1, admin)
    POST /api/us52/metrics/resilience 安全韧性五指标计算(P2, admin)
    POST /api/us52/metrics/inclusion   包容性公平两指标计算(P3, admin)

鉴权: 管理端 X-Role: admin(43-51号同款口径)。
统一口径:
    - 治理面(registry/dimensions/查询)不受
      US52_MODE 数据面开关影响
    - 计算面/测试面 off=拒绝(409——测试停铁律,
      51号采集停同款语义)
    - 测试走真管道(48号会话+49号网关)——
      独立号段 5300-5399 隔离
    - 模块纯增量(零既有路由改动)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services.us52_service import Us52MetricsService
from services.us52_task_engine import (
    TASK_LIBRARY, Us52TaskEngine,
)

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


class TestsRunIn(BaseModel):
    taskIds: list[str] | None = None
    memberId: int | None = None


class FunctionalIn(BaseModel):
    testId: int | None = None


@router.post("/tests/run")
async def run_tests(
        body: TestsRunIn | None = None,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """执行测试任务集(P1——四类任务跑真管道;
    US52_MODE=on 时可用)"""
    _require_admin(x_role)
    task_ids = (body.taskIds if body
                and body.taskIds else None)
    member_id = (body.memberId if body
                 and body.memberId else None)
    try:
        return await Us52TaskEngine().run_tests(
            task_ids=task_ids,
            member_id=member_id)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/tests")
async def list_tests(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """测试会话历史(最新在前)"""
    _require_admin(x_role)
    sessions = await Us52TaskEngine().repo \
        .list_sessions(limit=100)
    return {"success": True,
            "total": len(sessions),
            "tests": sessions,
            "taskLibrary": {
                k: {"kind": v["kind"],
                    "description": v["description"]}
                for k, v in TASK_LIBRARY.items()}}


@router.post("/metrics/functional")
async def compute_functional(
        body: FunctionalIn | None = None,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """功能可信度五指标计算(P1——49号审计
    口径直采+任务结果命中率)"""
    _require_admin(x_role)
    test_id = (body.testId if body
               and body.testId else None)
    try:
        return await Us52MetricsService(
        ).compute_functional_metrics(
            test_id=test_id)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/metrics/resilience")
async def compute_resilience(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """安全韧性五指标计算(P2——一票否决域:
    注入抵御复用 49/51号红队真跑 +
    降级合规/预算引导/跨会话隔离审计直采)"""
    _require_admin(x_role)
    try:
        return await Us52MetricsService(
        ).compute_resilience_metrics()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/metrics/inclusion")
async def compute_inclusion(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """包容性公平两指标计算(P3——五群体意图
    命中率组间差+低信值服务平等)"""
    _require_admin(x_role)
    try:
        return await Us52MetricsService(
        ).compute_inclusion_metrics()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


def register_us52_routes(app) -> None:
    """注册52号路由(main.py startup 调用)"""
    app.include_router(router)
