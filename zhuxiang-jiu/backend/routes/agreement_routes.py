"""网站条款及角色协议管理模块路由(10 端点)

鉴权:
    - 用户端(2 接口): 同意条款/检查同意(X-Member-Id)
    - 管理端(8 接口): X-Role: admin 头(条款CRUD/发布/角色协议/统计)

异常映射:
    - KeyError → 404(条款/协议不存在)
    - ValueError → 409(状态冲突/未发布/重复)
    - 权限校验 → 403(无权操作)

端点分布:
    - 条款(3):  创建/列表/详情
    - 发布(2):  发布生效/新版本
    - 同意(2):  用户同意/查询同意记录
    - 角色协议(2): 创建/列表
    - 历史(1):  条款历史版本
    - 统计(1):  管理端统计
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.agreement_service import AgreementService


router = APIRouter()
_service = AgreementService()


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

class CreateAgreementRequest(PydBaseModel):
    agreementNo: str = Field(..., description="条款编号(T01/T02...)")
    name: str = Field(..., description="条款名称")
    type: str = Field("term", description="类型: term/rule/contract")
    applicableRole: str = Field("user", description="适用角色: user/member/agent/merchant")
    content: str = Field("", description="条款内容")
    legalBasis: str = Field("", description="法律依据")
    changeLog: str = Field("", description="变更说明")


class UpdateAgreementRequest(PydBaseModel):
    name: str | None = None
    content: str | None = None
    legalBasis: str | None = None
    changeLog: str | None = None
    applicableRole: str | None = None


class PublishRequest(PydBaseModel):
    effectiveDate: str | None = None


class NewVersionRequest(PydBaseModel):
    content: str = Field(..., description="新版本内容")
    changeLog: str = Field("", description="变更说明")


class ConsentRequest(PydBaseModel):
    signMethod: str = Field("checkbox", description="签署方式: checkbox/popup/e-sign")
    ip: str = Field("", description="IP地址")
    device: str = Field("", description="设备信息")


class CreateProtocolRequest(PydBaseModel):
    role: str = Field(..., description="角色: user/member/agent/merchant")
    agreementId: int = Field(..., description="条款ID")
    required: bool = Field(True, description="是否必须同意")


class UpdateProtocolRequest(PydBaseModel):
    required: bool | None = None
    status: str | None = None


# ============================================================
# 条款接口(3)
# ============================================================

@router.post("/api/agreements", tags=["条款协议模块"])
async def create_agreement(
    data: CreateAgreementRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建条款(草稿状态)(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.create_agreement(
            agreement_no=data.agreementNo, name=data.name,
            atype=data.type, applicable_role=data.applicableRole,
            content=data.content, legal_basis=data.legalBasis,
            change_log=data.changeLog,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/agreements", tags=["条款协议模块"])
async def list_agreements(
    status: str = Query(None, description="按状态筛选"),
    type: str = Query(None, description="按类型筛选"),
    role: str = Query(None, description="按角色筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询条款列表(公开)"""
    try:
        result = await _service.list_agreements(status, type, role, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/agreements/{agreement_id}", tags=["条款协议模块"])
async def get_agreement(
    agreement_id: int,
):
    """查询条款详情"""
    try:
        result = await _service.get_agreement(agreement_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 发布与新版本接口(2)
# ============================================================

@router.post("/api/agreements/{agreement_id}/publish", tags=["条款协议模块"])
async def publish_agreement(
    agreement_id: int,
    data: PublishRequest = None,
    x_role: str = Header(None, alias="X-Role"),
):
    """条款生效(发布)(管理端)"""
    _require_admin(x_role)
    try:
        effective_date = data.effectiveDate if data else None
        result = await _service.publish_agreement(agreement_id, effective_date)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/agreements/{agreement_id}/versions", tags=["条款协议模块"])
async def new_version(
    agreement_id: int,
    data: NewVersionRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建新版本(版本递增)(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.new_version(agreement_id, data.content,
                                              data.changeLog)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 用户同意接口(2)
# ============================================================

@router.post("/api/agreements/{agreement_id}/consent", tags=["条款协议模块"])
async def consent(
    agreement_id: int,
    data: ConsentRequest = None,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """用户同意(签署)条款(用户端)"""
    user_id = int(_require_member_id(x_member_id))
    try:
        sign_method = data.signMethod if data else "checkbox"
        ip = data.ip if data else ""
        device = data.device if data else ""
        result = await _service.consent(user_id, agreement_id,
                                         sign_method=sign_method,
                                         ip=ip, device=device)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/agreements/consents", tags=["条款协议模块"])
async def list_consents(
    user_id: int = Query(None, alias="user_id", description="按用户筛选"),
    agreement_id: int = Query(None, alias="agreement_id", description="按条款筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询同意记录(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.list_consents(user_id, agreement_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 角色协议接口(2)
# ============================================================

@router.post("/api/agreements/role-protocols", tags=["条款协议模块"])
async def create_protocol(
    data: CreateProtocolRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建角色协议(关联条款与角色)(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.create_protocol(
            role=data.role, agreement_id=data.agreementId,
            required=data.required,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/agreements/role-protocols", tags=["条款协议模块"])
async def list_protocols(
    role: str = Query(None, description="按角色筛选"),
    status: str = Query(None, description="按状态筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询角色协议列表(公开)"""
    try:
        result = await _service.list_protocols(role, status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 历史版本接口(1)
# ============================================================

@router.get("/api/agreements/{agreement_id}/history", tags=["条款协议模块"])
async def get_history(
    agreement_id: int,
):
    """查询条款历史版本"""
    try:
        result = await _service.get_version_history(agreement_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 统计接口(1)
# ============================================================

@router.get("/api/agreements/stats/overview", tags=["条款协议模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """条款模块总览统计(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_agreement_routes(app):
    """注册条款协议管理模块路由"""
    app.include_router(router)
