"""信用管理模块路由(10 端点)

鉴权:
    - 用户端(7接口): X-Member-Id 头标识当前会员
    - 管理端(3接口): X-Role: admin 头(调整/升降级/黑名单/恢复)
    - 公开(部分查询接口)

异常映射:
    - KeyError → 404(账户不存在)
    - ValueError → 409(业务冲突)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布(10个):
    - 查询(3):    查询信用分 / 信用流水 / 额度查询
    - 操作(2):    调整信用分 / 信用升级
    - 管理(5):    降级 / 黑名单 / 恢复 / 统计 / 信用报告
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.credit_service import CreditService


router = APIRouter()
_service = CreditService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: Optional[str]) -> str:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: Optional[str]):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _map_key_error(exc: KeyError) -> HTTPException:
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        raise _map_key_error(exc)
    if isinstance(exc, ValueError):
        raise _map_value_error(exc)
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class AdjustScoreRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    delta: int = Field(..., description="信用分变化(正加分/负扣分)")
    reason: str = Field("", description="调整原因")
    operator: str = Field("system", description="操作者")
    roleType: str = Field("member", description="角色类型")


class UpgradeRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    targetLevel: str = Field(..., description="目标等级: L2/L3/L4/L5")
    reason: str = Field("", description="升级原因")
    operator: str = Field("admin", description="操作者")


class DowngradeRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    targetLevel: str = Field(..., description="目标等级: L1/L2/L3/L4")
    reason: str = Field("", description="降级原因")
    operator: str = Field("admin", description="操作者")


class BlacklistRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    reason: str = Field("", description="拉黑原因")
    operator: str = Field("admin", description="操作者")


class RestoreRequest(PydBaseModel):
    userId: int = Field(..., description="会员ID")
    restoreScore: int = Field(350, ge=0, le=1000, description="恢复后分数")
    reason: str = Field("", description="恢复原因")
    operator: str = Field("admin", description="操作者")


# ============================================================
# P0 接口(10 个) — 静态路径优先于动态路径
# ============================================================

# --- 查询接口(静态路径优先) ---

@router.get("/api/credit/list", tags=["信用管理模块"])
async def list_logs(
    user_id: int = Query(..., description="会员ID"),
    log_type: str = Query(None, description="按类型筛选: earn/deduct/adjust/upgrade/downgrade/blacklist/restore"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """查询信用流水(支持按类型筛选)"""
    try:
        result = await _service.list_logs(user_id, log_type, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/stats/{user_id}", tags=["信用管理模块"])
async def get_stats(
    user_id: int,
):
    """信用统计(按类型统计流水)"""
    try:
        result = await _service.get_stats(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/report/{user_id}", tags=["信用管理模块"])
async def get_credit_report(
    user_id: int,
):
    """信用报告(全维度画像)"""
    try:
        result = await _service.get_credit_report(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/quota/{user_id}", tags=["信用管理模块"])
async def get_paylater_quota(
    user_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询先享后付额度"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_paylater_quota(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/credit/score/{user_id}", tags=["信用管理模块"])
async def get_score(
    user_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询信用分(不存在则按会员创建)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.get_score(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 操作接口 ---

@router.post("/api/credit/adjust", tags=["信用管理模块"])
async def adjust_score(
    data: AdjustScoreRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """调整信用分(加分/扣分/人工调整)"""
    _require_admin(x_role)
    try:
        result = await _service.adjust_score(
            user_id=data.userId,
            delta=data.delta,
            reason=data.reason,
            operator=data.operator,
            role_type=data.roleType,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/upgrade", tags=["信用管理模块"])
async def upgrade_level(
    data: UpgradeRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """信用升级(强制设为目标等级对应分数下限)"""
    _require_admin(x_role)
    try:
        result = await _service.upgrade_level(
            user_id=data.userId,
            target_level=data.targetLevel,
            reason=data.reason,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/downgrade", tags=["信用管理模块"])
async def downgrade_level(
    data: DowngradeRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """信用降级(强制设为目标等级对应分数上限)"""
    _require_admin(x_role)
    try:
        result = await _service.downgrade_level(
            user_id=data.userId,
            target_level=data.targetLevel,
            reason=data.reason,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/blacklist", tags=["信用管理模块"])
async def add_to_blacklist(
    data: BlacklistRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """加入黑名单(状态: blacklist, 竹信分扣至0)"""
    _require_admin(x_role)
    try:
        result = await _service.add_to_blacklist(
            user_id=data.userId,
            reason=data.reason,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/credit/restore", tags=["信用管理模块"])
async def restore_credit(
    data: RestoreRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """恢复信用(解除黑名单/冻结, 重置分数)"""
    _require_admin(x_role)
    try:
        result = await _service.restore_credit(
            user_id=data.userId,
            restore_score=data.restoreScore,
            reason=data.reason,
            operator=data.operator,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_credit_routes(app):
    """注册信用管理模块路由"""
    app.include_router(router)
