"""74号·IBMS 智能后台管理路由(巡检总线+LLM 诊断入口)

守卫: X-Role: admin(同 36号 promo_routes 惯例)
分段: IBMS_PATROL_MODE off(默认)→ 巡检/诊断 409(方案铁律);
      观测面(status/history)无门槛(看得见永远先于管得着)
只读铁律: diagnose 返回 readOnly=True 显式标注——LLM 输出仅建议,
      任何变更走既有 approve 链(46号/36号)
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services import ibms_patrol_service as patrol

router = APIRouter()


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


class DiagnoseRequest(BaseModel):
    target: str = "all"      # 检查项名/all/模块名


def _handle(exc: Exception):
    """异常映射(KeyError 404 / ValueError 409——全站惯例)"""
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, KeyError):
        msg = str(exc) or "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    raise HTTPException(status_code=500, detail=str(exc)[:200])


# ============================================================
# 观测面(无模式门槛——off 也可看)
# ============================================================

@router.get("/api/ibms/status", tags=["智能后台管理模型(74号)"])
async def ibms_status(x_role: str = Header(None, alias="X-Role")):
    """模块状态(模式/清单/调度器)——观测面永不关停"""
    _require_admin(x_role)
    from services.ibms_patrol_scheduler import (
        scheduler_enabled, scheduler_interval_seconds, scheduler_running,
    )
    return {
        "success": True,
        "data": {
            "mode": patrol.patrol_mode(),
            "items": [{"rule": n, "name": label}
                      for n, label, _ in patrol.PATROL_ITEMS],
            "scheduler": {
                "enabled": scheduler_enabled(),
                "running": scheduler_running(),
                "intervalSeconds": scheduler_interval_seconds(),
            },
        },
    }


@router.get("/api/ibms/patrol/history",
            tags=["智能后台管理模型(74号)"])
async def ibms_patrol_history(
        limit: int = 20,
        x_role: str = Header(None, alias="X-Role")):
    """巡检留痕(观测面——无门槛; 留痕永远可查)"""
    _require_admin(x_role)
    rows = await patrol.patrol_history(max(1, min(limit, 50)))
    return {"success": True, "data": rows,
            "count": len(rows)}


# ============================================================
# 决策面(IBMS_PATROL_MODE 门槛)
# ============================================================

@router.post("/api/ibms/patrol/run", tags=["智能后台管理模型(74号)"])
async def ibms_patrol_run(
        x_role: str = Header(None, alias="X-Role")):
    """手动巡检一轮(决策面——mode 门槛; off 409 铁律)"""
    _require_admin(x_role)
    try:
        report = await patrol.run_patrol()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "data": report}


@router.post("/api/ibms/diagnose", tags=["智能后台管理模型(74号)"])
async def ibms_diagnose(
        body: DiagnoseRequest,
        x_role: str = Header(None, alias="X-Role")):
    """LLM 只读诊断(决策面——mode 门槛; off 409; 只读铁律)"""
    _require_admin(x_role)
    try:
        result = await patrol.diagnose(
            (body.target or "all").strip() or "all")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "data": result}


def register_ibms_routes(app):
    """路由注册(routes/__init__ register 函数族惯例)"""
    app.include_router(router)
