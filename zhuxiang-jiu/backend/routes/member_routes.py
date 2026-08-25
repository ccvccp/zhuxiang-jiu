"""会员管理路由

端点(14 个):
    POST   /api/member/register            手机号注册
    POST   /api/member/login               密码登录
    POST   /api/member/login/bonus         每日登录奖励
    GET    /api/member/profile             获取个人信息
    PUT    /api/member/profile             修改个人信息
    PUT    /api/member/password            修改密码
    GET    /api/member/level               查询等级
    POST   /api/member/consume             消费(成长值+积分+自动升级)
    GET    /api/member/points              查询积分
    POST   /api/member/points/deduct       积分抵扣
    GET    /api/member/addresses           地址列表
    POST   /api/member/addresses           新增地址
    PUT    /api/member/addresses/{addr_id} 修改地址
    DELETE /api/member/addresses/{addr_id} 删除地址

鉴权:
    - 注册/登录: 无需登录态
    - 其他接口: 需 X-Member-Id 头标识当前会员(Mock 模式)

异常映射:
    KeyError  → 404(资源不存在)
    ValueError → 409(业务冲突: 手机号已注册/密码错误/积分不足等)
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

from services.member_service import MemberService

router = APIRouter()


# ============================================================
#  请求模型
# ============================================================

class _GenericRequest(PydBaseModel):
    class Config:
        extra = "allow"


class RegisterRequest(PydBaseModel):
    phone: str = Field(..., description="手机号(11 位)")
    password: str = Field(..., min_length=6, description="密码(至少 6 位)")
    nickname: str | None = Field(None, description="昵称(可选)")
    reg_source: str = Field("phone", description="注册来源")


class LoginRequest(PydBaseModel):
    phone: str = Field(..., description="手机号")
    password: str = Field(..., description="密码")


class ProfileUpdateRequest(PydBaseModel):
    nickname: str | None = None
    avatar: str | None = None
    gender: int | None = Field(None, ge=0, le=2)
    class Config:
        extra = "allow"


class PasswordChangeRequest(PydBaseModel):
    oldPassword: str = Field(..., alias="old_password")
    newPassword: str = Field(..., min_length=6, alias="new_password")
    class Config:
        extra = "allow"
        populate_by_name = True


class ConsumeRequest(PydBaseModel):
    amount: float = Field(..., gt=0, description="消费金额")


class PointsDeductRequest(PydBaseModel):
    points: int = Field(..., gt=0, description="抵扣积分数(须为 100 的倍数)")
    order_amount: float = Field(0, ge=0, description="订单金额(用于校验抵扣上限)")


class AddressRequest(PydBaseModel):
    name: str = Field(..., description="收货人")
    phone: str = Field(..., description="联系电话")
    province: str = Field(..., description="省")
    city: str = Field(..., description="市")
    district: str = Field(..., description="区")
    detail: str = Field(..., description="详细地址")
    is_default: int = Field(0, ge=0, le=1, description="是否默认 0/1")


class AddressUpdateRequest(PydBaseModel):
    name: str | None = None
    phone: str | None = None
    province: str | None = None
    city: str | None = None
    district: str | None = None
    detail: str | None = None
    is_default: int | None = Field(None, ge=0, le=1)
    class Config:
        extra = "allow"


# ============================================================
#  Service 单例
# ============================================================

_member_service = MemberService()


# ============================================================
#  异常映射辅助(与 business_routes 一致)
# ============================================================

def _map_key_error(exc: KeyError) -> HTTPException:
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _require_member_id(x_member_id: str | None) -> int:
    """从 X-Member-Id 头提取会员ID,缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="X-Member-Id 格式不正确")


# ============================================================
#  注册 / 登录(无需登录态)
# ============================================================

@router.post("/api/member/register", tags=["会员服务"])
async def member_register(req: RegisterRequest):
    """手机号注册(赠送 100 竹叶积分)"""
    try:
        return await _member_service.register(
            req.phone, req.password, req.nickname, req.reg_source
        )
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/member/login", tags=["会员服务"])
async def member_login(req: LoginRequest):
    """密码登录(返回 Mock token)"""
    try:
        return await _member_service.login(req.phone, req.password)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/member/login/bonus", tags=["会员服务"])
async def member_login_bonus(
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """每日登录奖励(+5 积分)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.daily_login_bonus(member_id)
    except KeyError as e:
        raise _map_key_error(e) from e


# ============================================================
#  资料(需登录态)
# ============================================================

@router.get("/api/member/profile", tags=["会员服务"])
async def get_profile(
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """获取个人信息(脱敏,不返回密码)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.get_profile(member_id)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.put("/api/member/profile", tags=["会员服务"])
async def update_profile(
    req: ProfileUpdateRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """修改个人信息(允许: nickname/avatar/gender)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.update_profile(member_id, req.model_dump(exclude_none=True))
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.put("/api/member/password", tags=["会员服务"])
async def change_password(
    req: PasswordChangeRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """修改密码"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.change_password(
            member_id, req.oldPassword, req.newPassword
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
#  等级(需登录态)
# ============================================================

@router.get("/api/member/level", tags=["会员服务"])
async def get_level(
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """查询等级信息(含成长值/下一级所需)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.get_level(member_id)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/member/consume", tags=["会员服务"])
async def member_consume(
    req: ConsumeRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """消费(成长值+1/元, 积分+1/元, 自动升级)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.consume(member_id, req.amount)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
#  积分(需登录态)
# ============================================================

@router.get("/api/member/points", tags=["会员服务"])
async def get_points(
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """查询积分(100 竹叶 = ¥1)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.get_points(member_id)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/member/points/deduct", tags=["会员服务"])
async def deduct_points(
    req: PointsDeductRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """积分抵扣(100 竹叶 = ¥1, 抵扣上限订单 30%)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.deduct_points(member_id, req.points, req.order_amount)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
#  收货地址(需登录态)
# ============================================================

@router.get("/api/member/addresses", tags=["会员服务"])
async def list_addresses(
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """地址列表"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.list_addresses(member_id)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/member/addresses", tags=["会员服务"])
async def add_address(
    req: AddressRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """新增地址"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.add_address(
            member_id, req.name, req.phone, req.province,
            req.city, req.district, req.detail, req.is_default
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.put("/api/member/addresses/{address_id}", tags=["会员服务"])
async def update_address(
    address_id: str,
    req: AddressUpdateRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """修改地址"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.update_address(
            member_id, address_id, req.model_dump(exclude_none=True)
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.delete("/api/member/addresses/{address_id}", tags=["会员服务"])
async def delete_address(
    address_id: str,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """删除地址"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _member_service.delete_address(member_id, address_id)
    except KeyError as e:
        raise _map_key_error(e) from e


# ============================================================
#  注册函数
# ============================================================

def register_member_routes(app):
    """注册会员管理端点到 FastAPI app"""
    app.include_router(router)
