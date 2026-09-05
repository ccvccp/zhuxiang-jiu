"""54号·小竹AI智能登录引擎大模型路由(P0+P1)

端点(P0 4 + P1 2 = 6):
    GET  /api/login54/registry        模型注册表视图(admin, 观测面)
    GET  /api/login54/model/status    模型状态(admin, 观测面)
    POST /api/login54/score/preview   影子评分预览(admin, on)
    GET  /api/login54/model/history   模型事件历史(admin, 观测面)
    POST /api/login54/feedback/collect 决策回流标注触发(admin, P1)
    GET  /api/login54/feedback/stats  回流统计(admin, 观测面, P1)

鉴权: 管理面 X-Role: admin(43-53号同款口径)。
统一口径:
    - 观测面(registry/status/history/stats)不受
      LOGIN54_MODE 影响
    - 模型面(preview/dual_score 内部调用)
      off=拒绝(409——53号编排走 auth_risk 原轨)
    - 回流面(collect): 53号 events 幂等扫描——
      主动触发即回流(同步语义), 调度器 T+1 补标
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


def register_login54_routes(app) -> None:
    """注册54号路由(main.py startup 调用)"""
    app.include_router(router)
