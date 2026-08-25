"""合作接口管理模块路由(10 端点)

鉴权:
    - 用户端(1 接口): 提交合作申请(X-Member-Id)
    - 管理端(9 接口): X-Role: admin 头(审核/签约/协议/合作方/统计)

异常映射:
    - KeyError → 404(申请/协议/合作方不存在)
    - ValueError → 409(状态冲突/审核不通过/资质违规)
    - 权限校验 → 403(无权操作)

端点分布:
    - 申请(4):  提交/列表/详情/审核
    - 签约(1):  签约(状态流转 approved→signed)
    - 协议(3):  创建/列表/终止
    - 合作方(2): 更新(分级/状态)/列表
    - 统计(1):  管理端统计
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.cooperation_service import CooperationService


router = APIRouter()
_service = CooperationService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: str | None):
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

class CreateApplicationRequest(PydBaseModel):
    partnerName: str = Field(..., description="合作方名称")
    partnerType: str = Field("enterprise", description="合作方类型: enterprise/personal/government/dealer")
    type: str = Field("new", description="申请类型: new/renewal/upgrade")
    businessScope: str = Field(..., description="业务范围")
    estimatedAmount: float = Field(..., ge=0, description="预估合作金额")
    contactName: str = Field("", description="联系人")
    contactPhone: str = Field("", description="联系电话")
    contactEmail: str = Field("", description="联系邮箱")
    qualificationFiles: list[str] = Field(default_factory=list, description="资质文件URL列表")
    deliveryDate: str | None = None


class SignRequest(PydBaseModel):
    contractTitle: str = Field("", description="协议标题")
    startDate: str | None = None
    endDate: str | None = None
    depositAmount: float = Field(0, ge=0, description="保证金金额")


class CreateContractRequest(PydBaseModel):
    partnerId: int = Field(..., description="合作方ID")
    title: str = Field(..., description="协议标题")
    content: str = Field("", description="协议内容")
    amount: float = Field(0, ge=0, description="合作金额")
    startDate: str | None = None
    endDate: str | None = None
    depositAmount: float = Field(0, ge=0, description="保证金金额")


class UpdatePartnerRequest(PydBaseModel):
    contactName: str | None = None
    contactPhone: str | None = None
    contactEmail: str | None = None
    level: str | None = None
    status: str | None = None


class TerminateContractRequest(PydBaseModel):
    reason: str = Field("", description="终止原因")


# ============================================================
# 合作申请接口(4)
# ============================================================

@router.post("/api/cooperation/applications", tags=["合作接口管理模块"])
async def create_application(
    data: CreateApplicationRequest,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """提交合作申请(用户端)"""
    _require_member_id(x_member_id)
    try:
        result = await _service.create_application(
            partner_name=data.partnerName, partner_type=data.partnerType,
            app_type=data.type, business_scope=data.businessScope,
            estimated_amount=data.estimatedAmount,
            contact_name=data.contactName, contact_phone=data.contactPhone,
            contact_email=data.contactEmail,
            qualification_files=data.qualificationFiles,
            delivery_date=data.deliveryDate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/cooperation/applications", tags=["合作接口管理模块"])
async def list_applications(
    status: str = Query(None, description="按状态筛选"),
    partner_id: int = Query(None, alias="partner_id", description="按合作方筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询申请列表(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.list_applications(status, partner_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/cooperation/applications/{application_id}", tags=["合作接口管理模块"])
async def get_application(
    application_id: int,
):
    """查询申请详情"""
    try:
        result = await _service.get_application(application_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/cooperation/applications/{application_id}/review", tags=["合作接口管理模块"])
async def review_application(
    application_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """AI审核申请(资质审核)(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.review_application(application_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 签约接口(1)
# ============================================================

@router.post("/api/cooperation/applications/{application_id}/sign", tags=["合作接口管理模块"])
async def sign_application(
    application_id: int,
    data: SignRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """签约(状态流转 approved→signed, 创建协议+激活合作方)(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.sign_application(
            application_id, contract_title=data.contractTitle,
            start_date=data.startDate, end_date=data.endDate,
            deposit_amount=data.depositAmount,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 合作协议接口(3)
# ============================================================

@router.post("/api/cooperation/contracts", tags=["合作接口管理模块"])
async def create_contract(
    data: CreateContractRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建合作协议(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.create_contract(
            partner_id=data.partnerId, title=data.title,
            content=data.content, amount=data.amount,
            start_date=data.startDate, end_date=data.endDate,
            deposit_amount=data.depositAmount,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/cooperation/contracts", tags=["合作接口管理模块"])
async def list_contracts(
    status: str = Query(None, description="按状态筛选"),
    partner_id: int = Query(None, alias="partner_id", description="按合作方筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询协议列表(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.list_contracts(status, partner_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.post("/api/cooperation/contracts/{contract_id}/terminate", tags=["合作接口管理模块"])
async def terminate_contract(
    contract_id: int,
    data: TerminateContractRequest = None,
    x_role: str = Header(None, alias="X-Role"),
):
    """终止协议(管理端)"""
    _require_admin(x_role)
    try:
        reason = data.reason if data else ""
        result = await _service.terminate_contract(contract_id, reason=reason)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 合作方接口(2)
# ============================================================

@router.put("/api/cooperation/partners/{partner_id}", tags=["合作接口管理模块"])
async def update_partner(
    partner_id: int,
    data: UpdatePartnerRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """更新合作方(分级/状态流转)(管理端)"""
    _require_admin(x_role)
    try:
        updates = {k: v for k, v in data.dict().items() if v is not None}
        result = await _service.update_partner(partner_id, updates)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/cooperation/partners", tags=["合作接口管理模块"])
async def list_partners(
    status: str = Query(None, description="按状态筛选"),
    level: str = Query(None, description="按分级筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询合作方列表(公开)"""
    try:
        result = await _service.list_partners(status, level, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 统计接口(1)
# ============================================================

@router.get("/api/cooperation/stats/overview", tags=["合作接口管理模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """合作模块总览统计(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_cooperation_routes(app):
    """注册合作接口管理模块路由"""
    app.include_router(router)
