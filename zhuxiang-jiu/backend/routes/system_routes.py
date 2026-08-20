"""系统路由:健康检查 / 模式切换

端点:
    GET  /api/decision/health        健康检查(无需认证)
    GET  /api/decision/mode           查询运行模式(admin)
    POST /api/decision/mode/switch    切换 Mock/Live 模式(admin)
"""

from fastapi import APIRouter, Depends

from core.auth import require_role
from core.config import API_BASE, APP_MODE
from core.helpers import ok, uptime
from models import (
    BaseSuccessResponse, HealthDetails, ModeDetails,
    ModeSwitchRequest, ModeSwitchDetails,
)

router = APIRouter()


@router.get(f"{API_BASE}/health", tags=["系统"],
            response_model=BaseSuccessResponse,
            summary="健康检查",
            description="返回模块29运行状态。")
async def health():
    details = HealthDetails(uptime=uptime(), mockMode=APP_MODE["mode"] == "mock")
    return ok("health", details.model_dump(by_alias=True))


@router.get(f"{API_BASE}/mode", tags=["系统"],
            response_model=BaseSuccessResponse,
            summary="查询运行模式",
            description="查询当前运行模式(Mock/Live)。\n\n**角色**: admin")
async def get_mode(role: str = Depends(require_role("admin"))):
    details = ModeDetails(mode=APP_MODE["mode"], apiBase=APP_MODE["api_base"])
    return ok("mode", details.model_dump(by_alias=True))


@router.post(f"{API_BASE}/mode/switch", tags=["系统"],
             response_model=BaseSuccessResponse,
             summary="切换运行模式",
             description="切换Mock ↔ Live模式。并发安全: Mutex锁 `decision:mode`。\n\n**角色**: admin")
async def switch_mode(
    req: ModeSwitchRequest,
    role: str = Depends(require_role("admin")),
):
    APP_MODE["mode"] = req.mode
    APP_MODE["api_base"] = req.apiBase or API_BASE
    details = ModeSwitchDetails(mode=APP_MODE["mode"], apiBase=APP_MODE["api_base"])
    return ok("mode-switch", details.model_dump(by_alias=True),
              logs=[{"stage": "系统-模式切换", "message": f"切换至 {req.mode} 模式",
                     "data": {"apiBase": APP_MODE["api_base"]}}])


def register_system_routes(app):
    """注册系统端点(健康检查/模式)到 FastAPI app"""
    app.include_router(router)
