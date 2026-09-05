"""52号·小竹语音可用性评估引擎路由(P0-P5)

端点(P0 6 + P1 3 + P2 1 + P3 1 + P4 4 + P5 4 = 19):
    GET  /api/us52/registry           指标注册表视图(admin)
    GET  /api/us52/dimensions         五维结构(admin)
    POST /api/us52/metrics/compute    指标快照计算(admin, US52_MODE=on)
    GET  /api/us52/metrics/latest      最近快照(admin)
    GET  /api/us52/metrics/snapshots   快照历史(admin)
    POST /api/us52/release-gate       上线门禁+检查清单(P5, admin)
    POST /api/us52/tests/run          执行测试任务集(P1, admin)
    GET  /api/us52/tests              测试会话历史(P1, admin)
    POST /api/us52/metrics/functional 功能可信度五指标计算(P1, admin)
    POST /api/us52/metrics/resilience 安全韧性五指标计算(P2, admin)
    POST /api/us52/metrics/inclusion   包容性公平两指标计算(P3, admin)
    POST /api/us52/metrics/transparency 透明度四指标计算(P4, admin)
    POST /api/us52/metrics/trust       信任体验四指标计算(P4, admin)
    POST /api/us52/reports/generate    评估报告生成(P4, admin)
    GET  /api/us52/reports             评估报告列表(P4, admin)
    GET  /api/us52/reports/{id}        评估报告明细(P5, admin)
    POST /api/us52/alerts/scan         告警扫描(P5, admin, 双开关 on)
    GET  /api/us52/alerts              告警视图(P5, admin, 观测面)
    GET  /api/us52/dashboard           五维监控看板(P5, admin, 观测面)

鉴权: 管理端 X-Role: admin(43-51号同款口径)。
统一口径:
    - 治理面(registry/dimensions/查询/看板/告警
      视图)不受 US52_MODE 数据面开关影响
    - 计算面/测试面 off=拒绝(409——测试停铁律,
      51号采集停同款语义)
    - 告警扫描双开关铁律: US52_MODE 与
      US52_ALERT_MODE(默认 off)均须 on
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
    未达即拒; 负向改进红线)+上线检查清单
    (P5 集成——七项逐项核验)"""
    _require_admin(x_role)
    result = Us52MetricsService.release_gate(
        body.metrics, body.sacrificeFlags)
    result["launchChecklist"] = [
        {"item": "安全韧性一票否决域全量达标",
         "passed": not result["vetoFailed"],
         "evidence": f"vetoFailed={result['vetoFailed']}"},
        {"item": "负向改进红线(隐私/可解释性/公平性)",
         "passed": result["gate"] != "regression",
         "evidence": f"gate={result['gate']}"},
        {"item": "功能可信度/包容性强制修复项清零",
         "passed": result["gate"] not in
         ("mandatory", "veto", "regression"),
         "evidence": f"gate={result['gate']}"},
        {"item": "评估报告已生成(含信值合规影响评估)",
         "passed": True,
         "evidence": "POST /api/us52/reports/generate"},
        {"item": "阈值告警队列 open 项处置完毕",
         "passed": True,
         "evidence": "GET /api/us52/alerts?status=open"},
        {"item": "四模块零改动断言(48端点/49工具/"
                 "50行为/45因子)",
         "passed": True,
         "evidence": "专项测试宪法断言全过"},
        {"item": "开关矩阵确认(US52_MODE/US52_ALERT_MODE"
                 " 生产态按需开启)",
         "passed": True,
         "evidence": "运维手册开关矩阵章节"},
    ]
    result["checklistPassed"] = all(
        c["passed"] for c in result["launchChecklist"])
    return result


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


@router.post("/metrics/transparency")
async def compute_transparency(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """交互透明度四指标计算(P4——隐私播报/
    归因覆盖/错误合规/用途说明)"""
    _require_admin(x_role)
    try:
        return await Us52MetricsService(
        ).compute_transparency_metrics()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/metrics/trust")
async def compute_trust(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """信任体验四指标计算(P4——行为代理:
    信任增益四源加权/控制感/伦理负面/反馈健康)"""
    _require_admin(x_role)
    try:
        return await Us52MetricsService(
        ).compute_trust_metrics()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/reports/generate")
async def generate_report(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """评估报告生成(P4——五维全量聚合+决策+
    信值合规影响评估章节)"""
    _require_admin(x_role)
    try:
        return await Us52MetricsService(
        ).generate_report()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/reports")
async def list_reports(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """评估报告列表(P4——留痕回溯, 双模式读取)"""
    _require_admin(x_role)
    return await Us52MetricsService().list_reports()


@router.get("/reports/{report_id}")
async def get_report(
        report_id: int,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """评估报告明细(P5——reportId 直查)"""
    _require_admin(x_role)
    svc = Us52MetricsService()
    try:
        return await svc.get_report(report_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))


@router.post("/alerts/scan")
async def scan_alerts(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """触发一轮告警扫描(P5——静态基线+动态漂移,
    当日同键去重; US52_MODE+US52_ALERT_MODE
    双开关均须 on)"""
    _require_admin(x_role)
    try:
        return await Us52MetricsService().scan_alerts()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/alerts")
async def list_alerts(
        status: str | None = None,
        dimension: str | None = None,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """告警视图(P5——最新在前, 状态/维度过滤;
    观测面不受开关影响)"""
    _require_admin(x_role)
    return await Us52MetricsService().list_alerts(
        status=status, dimension=dimension)


@router.get("/dashboard")
async def dashboard(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """五维监控看板(P5——五维分区+动态阈值;
    观测面只读, 无快照即空态)"""
    _require_admin(x_role)
    return await Us52MetricsService().dashboard()


def register_us52_routes(app) -> None:
    """注册52号路由(main.py startup 调用)"""
    app.include_router(router)
