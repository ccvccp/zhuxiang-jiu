"""顺手赚钱模块路由(11 端点)

鉴权:
    - 用户端(8 接口): X-Member-Id 头(张贴打卡/每日打卡/存续奖/撤销/列表/统计)
    - 管理端(3 接口): X-Role: admin 头(参数配置/点位管理)
    - 公开(1 接口): 规则说明

异常映射(遵循项目约定):
    - KeyError → 404(点位/会员不存在)
    - ValueError → 409(重复打卡/未满时长/超限/参数非法等)
    - 权限校验 → 401(未登录) / 403(无权操作)
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.pocket_service import PocketService


router = APIRouter()
_service = PocketService()


# ============================================================
# 鉴权与异常映射辅助(对齐 promotion 风格)
# ============================================================

def _require_member_id(x_member_id: str | None) -> int:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="X-Member-Id 头须为会员ID数字") from None


def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射: KeyError → 404, ValueError → 409"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class ReportSiteRequest(PydBaseModel):
    scene: str = Field(..., description="张贴场景: hotel/supermarket/taxi_rear/"
                                        "restaurant/community")
    address: str = Field(..., min_length=1, max_length=120,
                         description="张贴地址(≥5字符)")
    photoUrl: str = Field(..., min_length=1, max_length=500,
                          description="打卡照片URL")


class CheckinRequest(PydBaseModel):
    photoUrl: str = Field(..., min_length=1, max_length=500,
                          description="打卡照片URL")


class InvalidateSiteRequest(PydBaseModel):
    reason: str = Field("", max_length=200, description="作废原因")


class UpdateSettingsRequest(PydBaseModel):
    enabled: bool | None = Field(None, description="模块总开关")
    checkinReward: float | None = Field(None, gt=0, le=1000,
                                        description="每次有效打卡奖励(元)")
    monthRewardPoster: float | None = Field(None, gt=0, le=1000,
                                            description="海报满月存续奖(元)")
    monthRewardSticker: float | None = Field(None, gt=0, le=1000,
                                             description="车贴满月存续奖(元)")
    maxActiveSites: int | None = Field(None, ge=1, le=365,
                                       description="每人同时在贴点位上限")
    aiScoreThreshold: int | None = Field(None, ge=1, le=100,
                                         description="有效打卡 AI 评分阈值")
    durationDays: int | None = Field(None, ge=1, le=365,
                                     description="存续奖天数门槛")
    minAddressLen: int | None = Field(None, ge=1, le=50,
                                      description="地址最小长度")


# ============================================================
# 用户端(8 接口)
# ============================================================

@router.post("/api/pocket/site/report", tags=["顺手赚钱模块"])
async def report_site(
    data: ReportSiteRequest,
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """张贴打卡: 登记新张贴点位(酒店/超市/车后窗等)并完成首次打卡发奖"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.report_site(member_id, data.scene,
                                          data.address, data.photoUrl)
    except Exception as exc:
        _handle(exc)


@router.post("/api/pocket/site/{site_id}/checkin", tags=["顺手赚钱模块"])
async def checkin_site(
    site_id: int,
    data: CheckinRequest,
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """张贴点每日打卡(AI 评估, 每点位每日限 1 次)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.checkin_site(member_id, site_id, data.photoUrl)
    except Exception as exc:
        _handle(exc)


@router.post("/api/pocket/site/{site_id}/month-reward", tags=["顺手赚钱模块"])
async def claim_month_reward(
    site_id: int,
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """领取满月存续奖(海报¥20/车贴¥30, 每点位限 1 次)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.claim_month_reward(member_id, site_id)
    except Exception as exc:
        _handle(exc)


@router.post("/api/pocket/site/{site_id}/remove", tags=["顺手赚钱模块"])
async def remove_site(
    site_id: int,
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """撤销张贴(未领存续奖视为放弃)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.remove_site(member_id, site_id)
    except Exception as exc:
        _handle(exc)


@router.get("/api/pocket/my/sites", tags=["顺手赚钱模块"])
async def my_sites(
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """我的张贴点位列表(附在贴天数/满月进度)"""
    member_id = _require_member_id(x_member_id)
    return {"sites": await _service.my_sites(member_id)}


@router.get("/api/pocket/my/stats", tags=["顺手赚钱模块"])
async def my_stats(
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """我的顺手赚钱统计"""
    member_id = _require_member_id(x_member_id)
    return await _service.my_stats(member_id)


@router.get("/api/pocket/my/checkins", tags=["顺手赚钱模块"])
async def my_checkins(
    limit: int = Query(50, ge=1, le=200, description="返回条数"),
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """我的打卡记录(倒序)"""
    member_id = _require_member_id(x_member_id)
    return {"checkins": await _service.my_checkins(member_id, limit)}


@router.get("/api/pocket/rules", tags=["顺手赚钱模块"])
async def get_rules():
    """规则说明(公开: 奖励规则/场景/防刷)"""
    return await _service.get_rules()


# ============================================================
# 管理端(3 接口)
# ============================================================

@router.get("/api/pocket/admin/settings", tags=["顺手赚钱模块"])
async def admin_get_settings(
    x_role: str | None = Header(None, alias="X-Role"),
):
    """查询模块参数"""
    _require_admin(x_role)
    return await _service.admin_get_settings()


@router.put("/api/pocket/admin/settings", tags=["顺手赚钱模块"])
async def admin_update_settings(
    data: UpdateSettingsRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """修改模块参数(即时生效)"""
    _require_admin(x_role)
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    try:
        return await _service.admin_update_settings(fields)
    except Exception as exc:
        _handle(exc)


@router.get("/api/pocket/admin/sites", tags=["顺手赚钱模块"])
async def admin_list_sites(
    member_id: int | None = Query(None, gt=0, description="按会员过滤"),
    scene: str | None = Query(None, description="按场景过滤"),
    status: str | None = Query(None, description="按状态过滤"),
    limit: int = Query(200, ge=1, le=500),
    x_role: str | None = Header(None, alias="X-Role"),
):
    """点位列表(管理端全量)"""
    _require_admin(x_role)
    return {"sites": await _service.admin_list_sites(
        member_id=member_id, scene=scene, status=status, limit=limit)}


@router.post("/api/pocket/admin/sites/{site_id}/invalidate",
             tags=["顺手赚钱模块"])
async def admin_invalidate_site(
    site_id: int,
    data: InvalidateSiteRequest | None = None,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """作废点位(违规处理)"""
    _require_admin(x_role)
    try:
        reason = data.reason if data else ""
        return await _service.admin_invalidate_site(site_id, reason)
    except Exception as exc:
        _handle(exc)


def register_pocket_routes(app) -> None:
    """向 FastAPI 应用注册顺手赚钱模块路由"""
    app.include_router(router)
