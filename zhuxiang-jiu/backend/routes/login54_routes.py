"""54号·小竹AI智能登录引擎大模型路由(P0)

端点(P0 4):
    GET  /api/login54/registry        模型注册表视图(admin, 观测面)
    GET  /api/login54/model/status    模型状态(admin, 观测面)
    POST /api/login54/score/preview   影子评分预览(admin, on)
    GET  /api/login54/model/history   模型事件历史(admin, 观测面)

鉴权: 管理面 X-Role: admin(43-53号同款口径)。
统一口径:
    - 观测面(registry/status/history)不受
      LOGIN54_MODE 影响
    - 模型面(preview/dual_score 内部调用)
      off=拒绝(409——53号编排走 auth_risk 原轨)
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


def register_login54_routes(app) -> None:
    """注册54号路由(main.py startup 调用)"""
    app.include_router(router)
