"""酒店酒吧会所合作商模块路由(12 端点)

鉴权:
    - 用户端(4 接口): X-Member-Id 头标识合作商身份(申请/场地CRUD/铺货记录)
    - 管理端(7 接口): X-Role: admin 头(审核/状态流转/分级/佣金结算/统计)
    - 公开(1 接口): 合作商详情/列表查询(仅读)

异常映射(遵循项目约定):
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 类型/状态/状态流转非法等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布(12):
    - 合作商(4):  apply(申请) / audit(审核) / get(详情) / list(列表)
    - 场地(3):    create-venue / list-venues / update-venue
    - 铺货(1):    add-stocking
    - 流转(1):    transition
    - 分级(1):    grade
    - 结算(1):    settle
    - 统计(1):    stats

注: list_stockings / list_admin_partners 作为 Service 方法提供, 可直接调用
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.venue_service import VenueService


router = APIRouter()
_service = VenueService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    """从 X-Member-Id 头提取合作商ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _map_key_error(exc: KeyError) -> HTTPException:
    """KeyError → 404"""
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    """ValueError → 409"""
    return HTTPException(status_code=409, detail=str(exc))


def _handle(exc: Exception):
    """统一异常映射"""
    if isinstance(exc, KeyError):
        raise _map_key_error(exc)
    if isinstance(exc, ValueError):
        raise _map_value_error(exc)
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class ApplyPartnerRequest(PydBaseModel):
    partnerType: str = Field(..., description="合作商类型: hotel/bar/club")
    partnerName: str = Field(..., description="合作商名称")
    creditCode: str = Field(..., description="统一社会信用代码")
    legalPerson: str = Field("", description="法人")
    contactPhone: str = Field("", description="联系电话")
    contactAddress: str = Field("", description="联系地址")
    longitude: float = Field(0.0, description="经度")
    latitude: float = Field(0.0, description="纬度")
    starLevel: int = Field(0, ge=0, le=5, description="星级")
    agentId: int = Field(None, description="关联代理商ID")


class AuditPartnerRequest(PydBaseModel):
    action: str = Field(..., description="审核动作: approve/reject")
    auditorId: int = Field(0, description="审核人ID")
    contractStart: str = Field("", description="合同开始日期")
    contractEnd: str = Field("", description="合同结束日期")
    partnerLevel: str = Field(None, description="初始等级(D/C/B/A/S)")
    rejectReason: str = Field("", description="驳回原因")


class TransitionRequest(PydBaseModel):
    targetStatus: str = Field(..., description="目标状态")
    operatorId: int = Field(0, description="操作人ID")
    remark: str = Field("", description="备注")


class GradePartnerRequest(PydBaseModel):
    newLevel: str = Field(..., description="新等级: D/C/B/A/S")
    reason: str = Field("", description="分级原因")
    operatorId: int = Field(0, description="操作人ID")


class CreateVenueRequest(PydBaseModel):
    partnerId: int = Field(..., description="合作商ID")
    venueName: str = Field(..., description="场地名称")
    venueType: str = Field(..., description="场地类型(如宴会厅/包间/吧台)")
    address: str = Field("", description="地址")
    capacity: int = Field(0, ge=0, description="容量")
    managerName: str = Field("", description="负责人姓名")
    managerPhone: str = Field("", description="负责人电话")
    businessHours: str = Field("", description="营业时间")


class UpdateVenueRequest(PydBaseModel):
    venueName: str = Field(None, description="场地名称")
    address: str = Field(None, description="地址")
    capacity: int = Field(None, ge=0, description="容量")
    managerName: str = Field(None, description="负责人姓名")
    managerPhone: str = Field(None, description="负责人电话")
    businessHours: str = Field(None, description="营业时间")
    status: str = Field(None, description="状态")


class AddStockingRequest(PydBaseModel):
    partnerId: int = Field(..., description="合作商ID")
    venueId: int = Field(..., description="场地ID")
    productId: str = Field(..., description="产品ID")
    productName: str = Field(..., description="产品名称")
    quantity: int = Field(..., gt=0, description="铺货数量")
    svipPrice: float = Field(..., gt=0, description="SVIP进货价")
    retailPrice: float = Field(..., gt=0, description="统一零售价")
    supplyMode: str = Field("direct", description="供货模式: agent/direct/neighbor")
    agentId: int = Field(None, description="供货代理商ID")
    stockingsDate: str = Field("", description="铺货日期")


# ============================================================
# P0 接口(12 个)
# ============================================================

# --- 合作商(4) ---

@router.post("/api/venue/partners", tags=["酒店酒吧会所合作商模块"])
async def apply_partner(
    data: ApplyPartnerRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """合作商申请入驻(默认状态 pending, 默认等级 D)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.apply_partner(
            partner_type=data.partnerType, partner_name=data.partnerName,
            credit_code=data.creditCode, legal_person=data.legalPerson,
            contact_phone=data.contactPhone,
            contact_address=data.contactAddress,
            longitude=data.longitude, latitude=data.latitude,
            star_level=data.starLevel, agent_id=data.agentId,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/venue/partners/{partner_id}/audit", tags=["酒店酒吧会所合作商模块"])
async def audit_partner(
    partner_id: int,
    data: AuditPartnerRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """合作商审核(approve→signed / reject→rejected)"""
    _require_admin(x_role)
    try:
        result = await _service.audit_partner(
            partner_id=partner_id, action=data.action,
            auditor_id=data.auditorId,
            contract_start=data.contractStart,
            contract_end=data.contractEnd,
            partner_level=data.partnerLevel,
            reject_reason=data.rejectReason,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/venue/partners/{partner_id}", tags=["酒店酒吧会所合作商模块"])
async def get_partner(
    partner_id: int,
):
    """查询合作商详情"""
    try:
        result = await _service.get_partner(partner_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/venue/partners", tags=["酒店酒吧会所合作商模块"])
async def list_partners(
    partner_type: str = Query(None, description="按类型筛选: hotel/bar/club"),
    status: str = Query(None, description="按状态筛选"),
    level: str = Query(None, description="按等级筛选: D/C/B/A/S"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """合作商列表(支持类型/状态/等级筛选)"""
    try:
        result = await _service.list_partners(
            partner_type=partner_type, status=status, level=level, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 场地(3) ---

@router.post("/api/venue/venues", tags=["酒店酒吧会所合作商模块"])
async def create_venue(
    data: CreateVenueRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """创建场地(合作商需存在)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.create_venue(
            partner_id=data.partnerId, venue_name=data.venueName,
            venue_type=data.venueType, address=data.address,
            capacity=data.capacity, manager_name=data.managerName,
            manager_phone=data.managerPhone,
            business_hours=data.businessHours,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/venue/venues", tags=["酒店酒吧会所合作商模块"])
async def list_venues(
    partner_id: int = Query(None, description="按合作商ID筛选"),
    venue_type: str = Query(None, description="按场地类型筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """场地列表(支持按合作商/类型筛选)"""
    try:
        result = await _service.list_venues(
            partner_id=partner_id, venue_type=venue_type, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.put("/api/venue/venues/{venue_id}", tags=["酒店酒吧会所合作商模块"])
async def update_venue(
    venue_id: int,
    data: UpdateVenueRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """更新场地(名称/地址/容量/负责人/状态)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.update_venue(
            venue_id=venue_id, venue_name=data.venueName,
            address=data.address, capacity=data.capacity,
            manager_name=data.managerName,
            manager_phone=data.managerPhone,
            business_hours=data.businessHours, status=data.status,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 铺货(1) ---

@router.post("/api/venue/stockings", tags=["酒店酒吧会所合作商模块"])
async def add_stocking(
    data: AddStockingRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """新增铺货记录(合作商需为 active 状态, 场地需属于合作商)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.add_stocking(
            partner_id=data.partnerId, venue_id=data.venueId,
            product_id=data.productId, product_name=data.productName,
            quantity=data.quantity, svip_price=data.svipPrice,
            retail_price=data.retailPrice, supply_mode=data.supplyMode,
            agent_id=data.agentId, stockings_date=data.stockingsDate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 流转(1) ---

@router.post("/api/venue/partners/{partner_id}/transition", tags=["酒店酒吧会所合作商模块"])
async def transition(
    partner_id: int,
    data: TransitionRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """合作商状态流转(状态机校验: 申请→审核→签约→合作→终止)"""
    _require_admin(x_role)
    try:
        result = await _service.transition(
            partner_id=partner_id, target_status=data.targetStatus,
            operator_id=data.operatorId, remark=data.remark,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 分级(1) ---

@router.post("/api/venue/partners/{partner_id}/grade", tags=["酒店酒吧会所合作商模块"])
async def grade_partner(
    partner_id: int,
    data: GradePartnerRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """合作商分级(手动升降级, 同步品鉴酒比例)"""
    _require_admin(x_role)
    try:
        result = await _service.grade_partner(
            partner_id=partner_id, new_level=data.newLevel,
            reason=data.reason, operator_id=data.operatorId,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 结算(1) ---

@router.post("/api/venue/partners/{partner_id}/settle", tags=["酒店酒吧会所合作商模块"])
async def settle_commission(
    partner_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """佣金结算(基于等级的差价利润分润, active 铺货结算后置为 offline)"""
    _require_admin(x_role)
    try:
        result = await _service.settle_commission(partner_id=partner_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 统计(1) ---

@router.get("/api/venue/stats", tags=["酒店酒吧会所合作商模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """合作统计(按类型/状态/等级聚合)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_venue_routes(app):
    """注册酒店酒吧会所合作商模块路由"""
    app.include_router(router)
