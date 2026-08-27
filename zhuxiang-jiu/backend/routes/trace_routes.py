"""双码追溯管理模块路由(13 端点)

鉴权:
    - 用户端: X-Member-Id 头(扫码激活/转让/查询)
    - 管理端: X-Role: admin 头(生成箱码/生命码/绑定/统计)

端点分布:
    - 箱码(3):     generate-box / bind-box / query-box
    - 生命码(3):    generate-life / bind-life / query-life
    - 扫码(3):     activate / scan-trace / trace-chain
    - 防窜(1):     anti-channel
    - 记录(1):     scan-logs
    - 管理(1):     transition-status(预留)
    - 统计(1):     stats
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.trace_service import TraceService
from repositories.trace_repository import (
    SCAN_TYPE_QUERY,
)


router = APIRouter()
_service = TraceService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: str | None):
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

class GenerateBoxRequest(PydBaseModel):
    productId: str = Field(..., description="产品ID")
    batchNo: str = Field(..., description="批次号")
    count: int = Field(..., ge=1, le=1000, description="生成数量(1-1000)")
    agentId: int | None = Field(None, description="代理商ID")
    agentRegion: str | None = Field(None, description="代理区域")


class BindBoxRequest(PydBaseModel):
    boxId: int = Field(..., description="箱码ID")
    lifeCodeIds: list[int] = Field(..., min_items=1, description="生命码ID数组")
    agentId: int | None = Field(None, description="代理商ID")


class GenerateLifeRequest(PydBaseModel):
    productId: str = Field(..., description="产品ID")
    batchNo: str = Field(..., description="批次号")
    count: int = Field(..., ge=1, le=1000, description="生成数量(1-1000)")
    productName: str = Field("", description="产品名称")
    productAbv: int = Field(42, description="度数")
    productVolume: str = Field("500ml", description="容量")


class BindLifeRequest(PydBaseModel):
    lifeId: int = Field(..., description="生命码ID")
    boxId: int = Field(..., description="箱码ID")


class ActivateRequest(PydBaseModel):
    lifeCode: str = Field(..., description="生命码")
    userId: int = Field(..., description="激活用户ID")
    userPhone: str | None = Field(None, description="手机号")
    userName: str | None = Field(None, description="持有人姓名")
    longitude: float | None = Field(None, description="经度")
    latitude: float | None = Field(None, description="纬度")
    province: str | None = Field(None, description="省")
    city: str | None = Field(None, description="市")
    district: str | None = Field(None, description="区")
    purchaseChannel: str = Field("online", description="购买渠道")
    purchasePrice: float = Field(0, description="购买价格")


class ScanTraceRequest(PydBaseModel):
    code: str = Field(..., description="箱码/生命码")
    userId: int | None = Field(None, description="扫码人ID")
    longitude: float | None = Field(None, description="经度")
    latitude: float | None = Field(None, description="纬度")
    province: str | None = Field(None, description="省")
    city: str | None = Field(None, description="市")
    scanType: str = Field(SCAN_TYPE_QUERY, description="扫码类型")


class AntiChannelRequest(PydBaseModel):
    lifeCode: str = Field(..., description="生命码")
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")
    province: str | None = Field(None, description="省")
    city: str | None = Field(None, description="市")


class TransferRequest(PydBaseModel):
    lifeCode: str = Field(..., description="生命码")
    fromUserId: int = Field(..., description="转让人ID")
    toUserId: int = Field(..., description="受让人ID")
    toName: str | None = Field(None, description="受让人姓名")
    transferType: str = Field("gift", description="转让类型(gift/trade/inherit)")
    longitude: float | None = Field(None, description="经度")
    latitude: float | None = Field(None, description="纬度")
    province: str | None = Field(None, description="省")
    city: str | None = Field(None, description="市")


# ============================================================
# P0 接口(12 个)
# ============================================================

# --- 箱码 ---

@router.post("/api/trace/box/generate", tags=["双码追溯模块"])
async def generate_box_codes(
    data: GenerateBoxRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """生成箱码(TBC+BBC双码, 管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.generate_box_codes(
            product_id=data.productId,
            batch_no=data.batchNo,
            count=data.count,
            agent_id=data.agentId,
            agent_region=data.agentRegion,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/trace/box/bind", tags=["双码追溯模块"])
async def bind_box_code(
    data: BindBoxRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """绑定箱码(关联生命码, 管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.bind_box_code(
            box_id=data.boxId,
            life_code_ids=data.lifeCodeIds,
            agent_id=data.agentId,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/trace/box/{box_id}", tags=["双码追溯模块"])
async def get_box_code(box_id: int):
    """查询箱码"""
    try:
        result = await _service.get_box_code(box_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 生命码 ---

@router.post("/api/trace/life/generate", tags=["双码追溯模块"])
async def generate_life_codes(
    data: GenerateLifeRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """生成生命码(BLC格式, 管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.generate_life_codes(
            product_id=data.productId,
            batch_no=data.batchNo,
            count=data.count,
            product_name=data.productName,
            product_abv=data.productAbv,
            product_volume=data.productVolume,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/trace/life/bind", tags=["双码追溯模块"])
async def bind_life_to_box(
    data: BindLifeRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """绑定生命码到箱码(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.bind_life_to_box(data.lifeId, data.boxId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/trace/life/{life_id}", tags=["双码追溯模块"])
async def get_life_code(life_id: int):
    """查询生命码"""
    try:
        result = await _service.get_life_code(life_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 扫码追溯 ---

@router.post("/api/trace/activate", tags=["双码追溯模块"])
async def activate_life_code(
    data: ActivateRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """扫码激活生命码(P4: 消费者首扫激活, 首启日期为老酒回收唯一基准)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.activate_life_code(
            life_code=data.lifeCode,
            user_id=data.userId,
            user_phone=data.userPhone,
            user_name=data.userName,
            longitude=data.longitude,
            latitude=data.latitude,
            province=data.province,
            city=data.city,
            district=data.district,
            purchase_channel=data.purchaseChannel,
            purchase_price=data.purchasePrice,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/trace/scan", tags=["双码追溯模块"])
async def scan_trace(
    data: ScanTraceRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """扫码追溯(箱码/生命码)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.scan_trace(
            code=data.code,
            user_id=data.userId,
            longitude=data.longitude,
            latitude=data.latitude,
            province=data.province,
            city=data.city,
            scan_type=data.scanType,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/trace/chain/{code}", tags=["双码追溯模块"])
async def get_trace_chain(code: str):
    """查询追溯链"""
    try:
        result = await _service.get_trace_chain(code)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 防窜货 ---

@router.post("/api/trace/anti-channel", tags=["双码追溯模块"])
async def detect_anti_channel(
    data: AntiChannelRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """防窜货检测"""
    _require_member_id(x_member_id)
    try:
        result = await _service.detect_anti_channel(
            life_code=data.lifeCode,
            longitude=data.longitude,
            latitude=data.latitude,
            province=data.province,
            city=data.city,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 转让 ---

@router.post("/api/trace/transfer", tags=["双码追溯模块"])
async def transfer_life_code(
    data: TransferRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """生命码转让(持有人变更)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.transfer_life_code(
            life_code=data.lifeCode,
            from_user_id=data.fromUserId,
            to_user_id=data.toUserId,
            to_name=data.toName,
            transfer_type=data.transferType,
            longitude=data.longitude,
            latitude=data.latitude,
            province=data.province,
            city=data.city,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 扫码记录 ---

@router.get("/api/trace/scan-logs", tags=["双码追溯模块"])
async def list_scan_logs(
    code: str = Query(None, description="按码筛选"),
    user_id: int = Query(None, description="按用户筛选"),
    scan_type: str = Query(None, description="按类型筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """查询扫码记录列表"""
    try:
        result = await _service.list_scan_logs(code, user_id, scan_type, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 统计 ---

@router.get("/api/trace/stats", tags=["双码追溯模块"])
async def get_stats(
    batch_no: str = Query(None, description="按批次筛选"),
    x_role: str = Header(None, alias="X-Role"),
):
    """追溯统计(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats(batch_no)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_trace_routes(app):
    """注册双码追溯模块路由"""
    app.include_router(router)
