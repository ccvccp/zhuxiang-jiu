"""54号·小竹AI智能登录引擎大模型路由(P0-P5)

端点(P0 4 + P1 2 + P2 2 + P3 2 + P4 2 + P5 1 = 13):
    GET  /api/login54/registry        模型注册表视图(admin, 观测面)
    GET  /api/login54/model/status    模型状态(admin, 观测面)
    POST /api/login54/score/preview   影子评分预览(admin, on)
    GET  /api/login54/model/history   模型事件历史(admin, 观测面)
    POST /api/login54/feedback/collect 决策回流标注触发(admin, P1)
    GET  /api/login54/feedback/stats  回流统计(admin, 观测面, P1)
    POST /api/login54/model/learn     在线学习轮次触发(admin, P2)
    POST /api/login54/model/shadow-compare 影子对比(challenger vs champion, admin, P2)
    POST /api/login54/model/promote   挑战者手动晋升(admin, P3)
    POST /api/login54/model/rollback  版本回滚(admin, P3)
    GET  /api/login54/governance/health 模型健康(46号三检测器, admin, P4)
    POST /api/login54/attribution     LLM 归因报告(admin, P4)
    GET  /api/login54/dashboard       引擎大模型看板(admin, P5)

鉴权: 管理面 X-Role: admin(43-53号同款口径)。
统一口径:
    - 观测面(registry/status/history/stats/health/
      dashboard)不受 LOGIN54_MODE 影响
    - 模型面(preview/dual_score 内部调用)
      off=拒绝(409——53号编排走 auth_risk 原轨)
    - 回流面(collect): 53号 events 幂等扫描——
      主动触发即回流(同步语义), 调度器 T+1 补标
    - 学习面(learn): 44号 Hedge 引擎复用——
      min_feedback 门槛不足/冻结中 → 409
    - 升级面(promote/rollback): 人工兜底通道;
      滑动窗口回归检测由调度器自动执行
      (回退→自动回滚+冻结+告警)
    - 治理面(health/attribution): 漂移 EMA+46号
      三检测器+LLM 归因(mock/real 三态)
    - 看板面(dashboard): 四区聚合+红队防御区
      (fail-soft 单区异常不阻断)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services.login54_service import Login54Service

router = APIRouter(prefix="/api/login54",
                   tags=["小竹登录引擎大模型(54号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")
    return x_role


class PreviewIn(BaseModel):
    ctx: dict


class CollectIn(BaseModel):
    """回流标注触发入参(可选 memberId 定向扫描)"""
    memberId: int | None = None
    limit: int = 500


@router.get("/registry")
async def get_registry(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """模型注册表自描述(八因子+双模型+学习闭环
    ——观测面不受开关影响)"""
    _require_admin(x_role)
    return Login54Service.registry()


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """模型状态(champion/challenger/漂移——
    44号 get_weights_view 复用; 观测面)"""
    _require_admin(x_role)
    return await Login54Service().model_status()


@router.post("/score/preview")
async def score_preview(
        body: PreviewIn,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """影子评分预览(输入上下文试算——不落库不生效)"""
    _require_admin(x_role)
    try:
        return await Login54Service().score_preview(
            body.ctx)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/model/history")
async def model_history(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """模型事件历史(学习/晋升/回滚/漂移——
    观测面, 最新在前)"""
    _require_admin(x_role)
    return await Login54Service().model_history()


@router.post("/feedback/collect")
async def feedback_collect(
        body: CollectIn | None = None,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """决策回流标注触发(53号 events 幂等扫描→七类
    信号源真值标注→44号池双写; 主动触发即同步回流,
    pending 延迟态 T+1 重扫转正)"""
    _require_admin(x_role)
    from services.login54_feedback_service import (
        Login54FeedbackService,
    )
    payload = body or CollectIn()
    try:
        return await Login54FeedbackService(
        ).collect_feedback(
            member_id=payload.memberId,
            limit=payload.limit)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/feedback/stats")
async def feedback_stats(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """回流统计(七类信号分布/样本量/池双写/延迟态
    ——观测面)"""
    _require_admin(x_role)
    from services.login54_feedback_service import (
        Login54FeedbackService,
    )
    return await Login54FeedbackService().feedback_stats()


@router.post("/model/learn")
async def model_learn(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """在线学习轮次触发(44号 Hedge 引擎复用——
    min_feedback=10 门槛+护栏 [0.5,2.0] 倍+
    冻结守卫内建; login54_model_events 留痕)"""
    _require_admin(x_role)
    from services.login54_learn_service import (
        Login54LearnService,
    )
    try:
        return await Login54LearnService().run_learning()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/model/shadow-compare")
async def model_shadow_compare(
        body: PreviewIn,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """影子评分对比(challenger vs champion 双轨试算
    ——模拟决策对比; 无挑战者时仅返回 champion 轨)"""
    _require_admin(x_role)
    from services.login54_learn_service import (
        Login54LearnService,
    )
    try:
        return await Login54LearnService(
        ).shadow_compare(body.ctx)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/model/promote")
async def model_promote(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """挑战者手动晋升(auto_apply 外人工通道——
    44号 promote_challenger 复用+事件留痕)"""
    _require_admin(x_role)
    from services.login54_learn_service import (
        Login54LearnService,
    )
    try:
        return await Login54LearnService().promote()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


class RollbackIn(BaseModel):
    """版本回滚入参(缺省→最新退役版本)"""
    versionId: str | None = None
    reason: str = ""


@router.post("/model/rollback")
async def model_rollback(
        body: RollbackIn | None = None,
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """版本回滚(指定历史版本缺省→最新退役——
    旧冠军入历史+rollback 事件留痕可溯)"""
    _require_admin(x_role)
    from services.login54_learn_service import (
        Login54LearnService,
    )
    payload = body or RollbackIn()
    try:
        return await Login54LearnService().rollback(
            version_id=payload.versionId,
            reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/governance/health")
async def governance_health(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """模型健康(46号三检测器: 停滞/枯竭/漂移——
    单档案评估+冻结状态+变更审批通道; 观测面)"""
    _require_admin(x_role)
    from services.login54_health_service import (
        Login54HealthService,
    )
    return await Login54HealthService(
    ).governance_health()


@router.post("/attribution")
async def attribution(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """LLM 归因报告(最近权重变更自然语言解释——
    mock 确定性模板/real 润色三态, 数字来自数据层)"""
    _require_admin(x_role)
    from services.login54_health_service import (
        Login54HealthService,
    )
    try:
        return await Login54HealthService().attribution()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/dashboard")
async def dashboard(
        x_role: str | None = Header(default=None,
                                     alias="X-Role")):
    """引擎大模型看板(版本/因子/回流/漂移四区+
    红队防御区——护栏状态+标注源集中度; fail-soft)"""
    _require_admin(x_role)
    from services.login54_dashboard_service import (
        Login54DashboardService,
    )
    return await Login54DashboardService().build()


def register_login54_routes(app) -> None:
    """注册54号路由(main.py startup 调用)"""
    app.include_router(router)
