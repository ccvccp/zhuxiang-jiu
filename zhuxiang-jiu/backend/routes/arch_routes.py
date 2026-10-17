"""竹智·Arch-Agent MVP · 架构态势路由

端点(3):
    - GET /api/arch/observe   公开观测面(态势+隐喻态, 游客可查——对齐
      zyh/synapse/zjian 公开 GET 范式; 无操作面)
    - GET /api/arch/status    admin 详情(全模块 mode/paused/guard)
    - GET /api/arch/history   admin 滚动历史(趋势)
"""

import logging

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from services.arch_agent_service import ArchAgentService

logger = logging.getLogger(__name__)
router = APIRouter()
_service = ArchAgentService()


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


@router.get("/api/arch/observe", tags=["竹智架构大脑模块"])
async def arch_observe():
    """公开观测面: 竹林生态态势(仅汇总与隐喻态, 模块名不含内部路径)"""
    snap = await _service.snapshot()
    return JSONResponse({
        "success": True,
        "data": {
            "collectedAt": snap["collectedAt"],
            "total": snap["total"],
            "healthy": snap["healthy"],
            "bamboo": snap["bamboo"],
            "modules": [
                {"module": m["module"], "label": m["label"],
                 "mode": m["mode"], "degraded": m["degraded"]}
                for m in snap["modules"]
            ],
        },
    })


@router.get("/api/arch/status", tags=["竹智架构大脑模块"])
async def arch_status(
    x_role: str | None = Header(None, alias="X-Role"),
):
    """架构态势详情(admin): 全模块 mode/source/paused/degraded"""
    _require_admin(x_role)
    snap = await _service.snapshot()
    return {"success": True, "data": snap}


@router.get("/api/arch/history", tags=["竹智架构大脑模块"])
async def arch_history(
    x_role: str | None = Header(None, alias="X-Role"),
    limit: int = 20,
):
    """快照滚动历史(admin, 旧→新, 趋势观察)"""
    _require_admin(x_role)
    hist = await _service.history(limit=limit)
    return {"success": True, "data": hist,
            "count": len(hist)}


def register_arch_routes(app):
    """注册竹智架构大脑路由到 FastAPI app"""
    app.include_router(router)
