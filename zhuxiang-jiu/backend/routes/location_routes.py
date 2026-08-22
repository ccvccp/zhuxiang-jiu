"""位置地图管理模块路由(12 端点)

鉴权:
    - 用户端: X-Member-Id 头(地址管理/物流查询/距离计算)
    - 管理端: X-Role: admin 头(门店/代理商/配送范围管理)

端点分布:
    - 地址管理(5):  add / list / update / delete / set-default
    - 门店(2):       nearby / detail
    - 代理商(2):     list / detail
    - 物流(1):       track
    - 配送(1):       check
    - 存证(1):       evidence
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.location_service import LocationService
from repositories.location_repository import (
    # 门店类型
    STORE_TYPE_FLAGSHIP, STORE_TYPE_EXPERIENCE, STORE_TYPE_EXCLUSIVE,
    STORE_STATUS_OPEN, STORE_STATUS_CLOSED,
    # 代理商等级
    AGENT_LEVEL_DIAMOND, AGENT_LEVEL_GOLD, AGENT_LEVEL_SILVER,
    # 物流状态
    SHIPMENT_STATUS_IN_TRANSIT, SHIPMENT_STATUS_DELIVERING, SHIPMENT_STATUS_SIGNED,
    # 配送范围类型
    ZONE_TYPE_NATIONAL, ZONE_TYPE_CITY, ZONE_TYPE_SELF, ZONE_TYPE_REMOTE,
    # 存证类型
    EVIDENCE_TYPE_ADDRESS, EVIDENCE_TYPE_LOCATION, EVIDENCE_TYPE_LOGISTICS,
    EVIDENCE_TYPE_DELIVERY, EVIDENCE_TYPE_HEATMAP, EVIDENCE_TYPE_SITE,
    # 常量
    MAX_ADDRESSES_PER_USER,
)


router = APIRouter()
_service = LocationService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: Optional[str]) -> int:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-Member-Id 必须为数字")


def _require_admin(x_role: Optional[str]):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射"""
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

class AddAddressRequest(PydBaseModel):
    receiverName: str = Field(..., description="收件人")
    receiverPhone: str = Field(..., description="手机号")
    province: str = Field(..., description="省")
    city: str = Field(..., description="市")
    district: str = Field(..., description="区")
    detailAddress: str = Field(..., description="详细地址")
    longitude: Optional[float] = Field(None, description="经度")
    latitude: Optional[float] = Field(None, description="纬度")
    adcode: Optional[str] = Field(None, description="行政区划编码")
    label: Optional[str] = Field(None, description="标签(家/公司/学校)")
    isDefault: bool = Field(False, description="是否默认")


class UpdateAddressRequest(PydBaseModel):
    receiverName: Optional[str] = None
    receiverPhone: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    detailAddress: Optional[str] = None
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    adcode: Optional[str] = None
    label: Optional[str] = None


class AddStoreRequest(PydBaseModel):
    storeName: str = Field(..., description="门店名称")
    storeType: str = Field(..., description="门店类型")
    province: str = Field(..., description="省")
    city: str = Field(..., description="市")
    district: str = Field(..., description="区")
    address: str = Field(..., description="详细地址")
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")
    phone: Optional[str] = None
    openHours: Optional[str] = None
    services: Optional[str] = None
    status: str = Field(STORE_STATUS_OPEN, description="营业状态")


class AddAgentLocationRequest(PydBaseModel):
    agentId: int = Field(..., description="代理商ID")
    agentName: str = Field(..., description="代理商名称")
    agentLevel: str = Field(..., description="代理商等级")
    province: str = Field(..., description="省")
    city: str = Field(..., description="市")
    address: str = Field(..., description="详细地址")
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")
    contactName: Optional[str] = None
    contactPhone: Optional[str] = None


class CreateTrackRequest(PydBaseModel):
    shipmentId: str = Field(..., description="运单号")
    orderId: int = Field(..., description="订单ID")
    carrier: str = Field(..., description="物流商")
    trackingNo: str = Field(..., description="运单编号")
    originLng: float = Field(..., description="发货地经度")
    originLat: float = Field(..., description="发货地纬度")
    destLng: float = Field(..., description="收货地经度")
    destLat: float = Field(..., description="收货地纬度")
    currentLng: Optional[float] = None
    currentLat: Optional[float] = None
    currentAddress: Optional[str] = None
    eta: Optional[str] = None


class UpdateTrackRequest(PydBaseModel):
    currentLng: Optional[float] = None
    currentLat: Optional[float] = None
    currentAddress: Optional[str] = None
    status: Optional[str] = None
    eta: Optional[str] = None


class AddDeliveryZoneRequest(PydBaseModel):
    zoneName: str = Field(..., description="范围名称")
    zoneType: str = Field(..., description="范围类型")
    centerLng: Optional[float] = None
    centerLat: Optional[float] = None
    radius: float = Field(0, description="半径(km)")
    polygon: Optional[str] = None
    shippingFee: float = Field(0, description="运费")
    freeThreshold: float = Field(0, description="包邮门槛")
    deliveryTime: Optional[str] = None


class CheckDeliveryRequest(PydBaseModel):
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")


class AddEvidenceRequest(PydBaseModel):
    evidenceType: str = Field(..., description="存证类型")
    evidenceData: str = Field("", description="存证数据")


# ============================================================
# P0 接口(12 个)
# ============================================================

# --- 地址管理 ---

@router.post("/api/location/address/add", tags=["位置地图管理模块"])
async def add_address(
    data: AddAddressRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """新增收货地址"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.add_address(
            user_id=user_id,
            receiver_name=data.receiverName,
            receiver_phone=data.receiverPhone,
            province=data.province,
            city=data.city,
            district=data.district,
            detail_address=data.detailAddress,
            longitude=data.longitude,
            latitude=data.latitude,
            adcode=data.adcode,
            label=data.label,
            is_default=data.isDefault,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/location/address/list", tags=["位置地图管理模块"])
async def list_addresses(
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """查询用户地址列表"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.list_addresses(user_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.put("/api/location/address/{address_id}", tags=["位置地图管理模块"])
async def update_address(
    address_id: int,
    data: UpdateAddressRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """编辑地址"""
    _require_member_id(x_member_id)
    try:
        result = await _service.update_address(
            address_id=address_id,
            receiver_name=data.receiverName,
            receiver_phone=data.receiverPhone,
            province=data.province,
            city=data.city,
            district=data.district,
            detail_address=data.detailAddress,
            longitude=data.longitude,
            latitude=data.latitude,
            adcode=data.adcode,
            label=data.label,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.delete("/api/location/address/{address_id}", tags=["位置地图管理模块"])
async def delete_address(
    address_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """删除地址"""
    _require_member_id(x_member_id)
    try:
        result = await _service.delete_address(address_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/location/address/{address_id}/default", tags=["位置地图管理模块"])
async def set_default_address(
    address_id: int,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """设为默认地址"""
    user_id = _require_member_id(x_member_id)
    try:
        result = await _service.set_default_address(address_id, user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 门店 ---

@router.get("/api/location/stores/nearby", tags=["位置地图管理模块"])
async def list_nearby_stores(
    longitude: float = Query(..., description="经度"),
    latitude: float = Query(..., description="纬度"),
    radius_km: float = Query(5.0, ge=0.1, le=100, description="半径(km)"),
    limit: int = Query(50, ge=1, le=200, description="查询条数"),
):
    """附近门店(按距离排序)"""
    try:
        result = await _service.list_nearby_stores(longitude, latitude, radius_km, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/location/stores/{store_id}", tags=["位置地图管理模块"])
async def get_store(store_id: int):
    """门店详情"""
    try:
        result = await _service.get_store(store_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 代理商 ---

@router.get("/api/location/agents", tags=["位置地图管理模块"])
async def list_agent_locations(
    province: str = Query(None, description="按省筛选"),
    city: str = Query(None, description="按市筛选"),
    agent_level: str = Query(None, description="按等级筛选"),
    longitude: float = Query(None, description="经度(附近查询)"),
    latitude: float = Query(None, description="纬度(附近查询)"),
    radius_km: float = Query(50.0, ge=0.1, le=500, description="半径(km)"),
    limit: int = Query(50, ge=1, le=200, description="查询条数"),
):
    """代理商查询"""
    try:
        if longitude is not None and latitude is not None:
            result = await _service.list_nearby_agents(longitude, latitude, radius_km, limit)
        else:
            result = await _service.list_agent_locations(province, city, agent_level, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/location/agents/{agent_id}", tags=["位置地图管理模块"])
async def get_agent_location(agent_id: int):
    """代理商详情"""
    try:
        result = await _service.get_agent_location(agent_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 物流轨迹 ---

@router.get("/api/location/logistics/{shipment_id}", tags=["位置地图管理模块"])
async def get_shipment_track(shipment_id: str):
    """物流轨迹查询"""
    try:
        result = await _service.get_track_by_shipment(shipment_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 配送范围 ---

@router.post("/api/location/delivery/check", tags=["位置地图管理模块"])
async def check_delivery_point(
    data: CheckDeliveryRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """配送范围检测"""
    _require_member_id(x_member_id)
    try:
        result = await _service.check_delivery_point(data.longitude, data.latitude)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 区块链存证 ---

@router.post("/api/location/blockchain/evidence", tags=["位置地图管理模块"])
async def add_evidence(
    data: AddEvidenceRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """区块链存证(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.add_evidence(data.evidenceType, data.evidenceData)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/location/blockchain/verify", tags=["位置地图管理模块"])
async def verify_evidence(
    hash: str = Query(..., description="存证哈希"),
):
    """按哈希验证存证"""
    try:
        result = await _service.verify_evidence_by_hash(hash)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_location_routes(app):
    """注册位置地图管理模块路由"""
    app.include_router(router)
