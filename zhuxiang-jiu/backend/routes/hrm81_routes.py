"""81号·硬件资源智能模型(HRM)路由(观测面+决策面)

守卫: X-Role: admin(同 74号 ibms_routes 惯例)
分段: HRM81_MODE off(默认)→ 决策/建议书 409(方案铁律);
      观测面(status/registry/decisions)无模式门槛
      (看得见永远先于管得着)
只读铁律: proposal 返回 readOnly=True 显式标注——LLM 输出仅建议,
      任何变更走既有 approve 链(46号)
"""

from fastapi import APIRouter, Header, HTTPException

from services import hrm81_service as hrm

router = APIRouter()


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


# ============================================================
# 观测面(无模式门槛——off 也可看)
# ============================================================

@router.get("/api/hrm81/status", tags=["硬件资源智能模型(81号)"])
async def hrm_status(x_role: str = Header(None, alias="X-Role")):
    """模块状态(模式/水位/台账摘要/调度器/LLM 节流)——观测面"""
    _require_admin(x_role)
    from services.hrm81_scheduler import (
        scheduler_enabled, scheduler_interval_seconds, scheduler_running,
    )
    from services.llm_client import llm_throttle_remaining
    return {
        "success": True,
        "data": {
            "mode": hrm.hrm_mode(),
            "water": await hrm.assess_water_level(),
            "registry": hrm.registry_summary(),
            "scheduler": {
                "enabled": scheduler_enabled(),
                "running": scheduler_running(),
                "intervalSeconds": scheduler_interval_seconds(),
            },
            # P2 §2.4: EMA 观测面段(基线覆盖/当前小时桶/磁盘外推)
            "ema": await hrm.ema_status(),
            "llmThrottleRemainingSeconds":
                round(llm_throttle_remaining(), 1),
        },
    }


@router.get("/api/hrm81/registry", tags=["硬件资源智能模型(81号)"])
async def hrm_registry(x_role: str = Header(None, alias="X-Role")):
    """模块资源台账全量(观测面——统筹各模型的查询底座)"""
    _require_admin(x_role)
    return {"success": True,
            "data": hrm.MODULE_REGISTRY}


@router.get("/api/hrm81/decisions", tags=["硬件资源智能模型(81号)"])
async def hrm_decisions(
        limit: int = 20,
        x_role: str = Header(None, alias="X-Role")):
    """决策留痕史(观测面——留痕永远可查)"""
    _require_admin(x_role)
    rows = await hrm.decision_history(max(1, min(limit, 50)))
    return {"success": True, "data": rows, "count": len(rows)}


# ============================================================
# 决策面(HRM81_MODE 门槛)
# ============================================================

@router.post("/api/hrm81/run", tags=["硬件资源智能模型(81号)"])
async def hrm_run(x_role: str = Header(None, alias="X-Role")):
    """手动执行一轮资源统筹决策(决策面——mode 门槛; off 409 铁律)"""
    _require_admin(x_role)
    try:
        decision = await hrm.run_hrm_decision()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "data": decision}


@router.post("/api/hrm81/proposal", tags=["硬件资源智能模型(81号)"])
async def hrm_proposal(x_role: str = Header(None, alias="X-Role")):
    """容量规划建议书(决策面——LLM 只读, off 409; 执行走 approve 链)"""
    _require_admin(x_role)
    try:
        result = await hrm.capacity_proposal()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "data": result}


def register_hrm81_routes(app):
    """路由注册(routes/__init__ register 函数族惯例)"""
    app.include_router(router)
