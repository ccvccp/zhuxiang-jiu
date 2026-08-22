"""广告管理模块路由(12 端点)

鉴权:
    - 用户端(2 接口): 曝光/点击/转化记录(X-Member-Id)
    - 管理端(10 接口): X-Role: admin 头(广告/广告位/审核/统计)

异常映射:
    - KeyError → 404(广告/广告位不存在)
    - ValueError → 409(状态冲突/审核不通过/合规违规)
    - 权限校验 → 403(无权操作)

端点分布:
    - 广告(5):  创建/列表/详情/更新/上下线
    - 广告位(2): 创建/列表(详情/更新/删除由 service 提供)
    - 投放(1):  投放记录查询
    - 效果(2):  曝光点击/广告统计
    - 审核(1):  广告审核
    - 统计(1):  管理端统计
"""

from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.ad_service import AdService


router = APIRouter()
_service = AdService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: Optional[str]) -> str:
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: Optional[str]):
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

class CreateAdRequest(PydBaseModel):
    advertiserName: str = Field(..., description="广告主名称")
    name: str = Field(..., description="广告名称")
    type: str = Field(..., description="广告类型: VIDEO/IMAGE/TEXT/CAROUSEL/POPUP/FLOAT/FEED/SPLASH/PRE_ROLL/BANNER")
    position: str = Field(..., description="广告位编码")
    title: str = Field(..., description="广告标题")
    description: str = Field("", description="广告描述")
    targetUrl: str = Field("", description="目标链接")
    imageUrl: str = Field("", description="图片URL")
    videoUrl: str = Field("", description="视频URL")
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    budget: float = Field(0, ge=0, description="总预算")
    dailyBudget: float = Field(0, ge=0, description="日预算")
    targetRules: dict = Field(default_factory=dict, description="定向规则")


class UpdateAdRequest(PydBaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    targetUrl: Optional[str] = None
    imageUrl: Optional[str] = None
    videoUrl: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    budget: Optional[float] = Field(None, ge=0)
    dailyBudget: Optional[float] = Field(None, ge=0)
    targetRules: Optional[dict] = None


class CreateSlotRequest(PydBaseModel):
    slotCode: str = Field(..., description="广告位编码(如 AD_HOME_BANNER)")
    name: str = Field(..., description="广告位名称")
    position: str = Field(..., description="位置描述")
    size: str = Field(..., description="尺寸(如 1920×600)")
    supportedTypes: List[str] = Field(default_factory=list, description="支持的广告类型")
    dailyEstimateImpressions: int = Field(0, ge=0, description="日预估曝光")


class UpdateSlotRequest(PydBaseModel):
    name: Optional[str] = None
    position: Optional[str] = None
    size: Optional[str] = None
    supportedTypes: Optional[List[str]] = None
    dailyEstimateImpressions: Optional[int] = Field(None, ge=0)
    status: Optional[str] = None


class RecordRequest(PydBaseModel):
    count: int = Field(1, ge=1, description="数量")
    revenue: float = Field(0, ge=0, description="转化产出(仅转化记录)")


class OfflineRequest(PydBaseModel):
    reason: str = Field("", description="下线原因")


# ============================================================
# 广告接口(5)
# ============================================================

@router.post("/api/ads", tags=["广告管理模块"])
async def create_ad(
    data: CreateAdRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建广告(草稿状态, 待审核)"""
    _require_admin(x_role)
    try:
        result = await _service.create_ad(
            advertiser_name=data.advertiserName,
            name=data.name, ad_type=data.type, position=data.position,
            title=data.title, description=data.description,
            target_url=data.targetUrl, image_url=data.imageUrl,
            video_url=data.videoUrl, start_time=data.startTime,
            end_time=data.endTime, budget=data.budget,
            daily_budget=data.dailyBudget, target_rules=data.targetRules,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/ads", tags=["广告管理模块"])
async def list_ads(
    status: str = Query(None, description="按状态筛选"),
    ad_type: str = Query(None, alias="ad_type", description="按类型筛选"),
    position: str = Query(None, description="按广告位筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询广告列表(公开)"""
    try:
        result = await _service.list_ads(status, ad_type, position, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/ads/{ad_id}", tags=["广告管理模块"])
async def get_ad(
    ad_id: int,
):
    """查询广告详情"""
    try:
        result = await _service.get_ad(ad_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.put("/api/ads/{ad_id}", tags=["广告管理模块"])
async def update_ad(
    ad_id: int,
    data: UpdateAdRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """更新广告(仅草稿/已驳回状态可改)"""
    _require_admin(x_role)
    try:
        updates = {k: v for k, v in data.dict().items() if v is not None}
        result = await _service.update_ad(ad_id, updates)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/ads/{ad_id}/online", tags=["广告管理模块"])
async def online_ad(
    ad_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """广告上线(创建投放记录)"""
    _require_admin(x_role)
    try:
        result = await _service.online_ad(ad_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/ads/{ad_id}/offline", tags=["广告管理模块"])
async def offline_ad(
    ad_id: int,
    data: OfflineRequest = None,
    x_role: str = Header(None, alias="X-Role"),
):
    """广告下线"""
    _require_admin(x_role)
    try:
        reason = data.reason if data else ""
        result = await _service.offline_ad(ad_id, reason=reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 审核接口(1)
# ============================================================

@router.post("/api/ads/{ad_id}/review", tags=["广告管理模块"])
async def review_ad(
    ad_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """AI审核广告(合规检测: 极限词/健康警示/广告标识)"""
    _require_admin(x_role)
    try:
        result = await _service.review_ad(ad_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 效果统计接口(2)
# ============================================================

@router.post("/api/ads/{ad_id}/impression", tags=["广告管理模块"])
async def record_impression(
    ad_id: int,
    data: RecordRequest = None,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """记录曝光"""
    _require_member_id(x_member_id)
    try:
        count = data.count if data else 1
        result = await _service.record_impression(ad_id, count)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/ads/{ad_id}/click", tags=["广告管理模块"])
async def record_click(
    ad_id: int,
    data: RecordRequest = None,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """记录点击"""
    _require_member_id(x_member_id)
    try:
        count = data.count if data else 1
        result = await _service.record_click(ad_id, count)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/ads/{ad_id}/stats", tags=["广告管理模块"])
async def get_ad_stats(
    ad_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """查询广告效果统计(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_ad_stats(ad_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 广告位接口(2)
# ============================================================

@router.post("/api/ads/slots", tags=["广告管理模块"])
async def create_slot(
    data: CreateSlotRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建广告位(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.create_slot(
            slot_code=data.slotCode, name=data.name, position=data.position,
            size=data.size, supported_types=data.supportedTypes,
            daily_estimate_impressions=data.dailyEstimateImpressions,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/ads/slots", tags=["广告管理模块"])
async def list_slots(
    status: str = Query(None, description="按状态筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询广告位列表(公开)"""
    try:
        result = await _service.list_slots(status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.put("/api/ads/slots/{slot_code}", tags=["广告管理模块"])
async def update_slot(
    slot_code: str,
    data: UpdateSlotRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """更新广告位(管理员)"""
    _require_admin(x_role)
    try:
        updates = {k: v for k, v in data.dict().items() if v is not None}
        result = await _service.update_slot(slot_code, updates)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.delete("/api/ads/slots/{slot_code}", tags=["广告管理模块"])
async def delete_slot(
    slot_code: str,
    x_role: str = Header(None, alias="X-Role"),
):
    """删除广告位(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.delete_slot(slot_code)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 投放记录与总览统计(2)
# ============================================================

@router.get("/api/ads/placements/list", tags=["广告管理模块"])
async def list_placements(
    ad_id: int = Query(None, description="按广告ID筛选"),
    slot_code: str = Query(None, alias="slot_code", description="按广告位筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询投放记录(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_placements(ad_id, slot_code, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/ads/stats/overview", tags=["广告管理模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """广告模块总览统计(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_ad_routes(app):
    """注册广告管理模块路由"""
    app.include_router(router)
