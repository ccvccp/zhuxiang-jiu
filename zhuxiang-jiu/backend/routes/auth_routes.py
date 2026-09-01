"""用户认证模块路由(8 端点)

鉴权方式:
    - Authorization: Bearer <accessToken> 头(新体系, JWT)
    - 兼容期: 现有 X-Member-Id 头接口不受影响, 可渐进迁移

端点分布:
    - 公开(4):  register / login / refresh / logout
    - 会员(2):  me / change-password
    - 管理(2):  set-role / blacklist-stats

依赖注入:
    - get_current_member: Bearer Token → 当前会员(401 拦截)
    - require_admin:      Bearer Token + role=admin(401/403 拦截)
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from core.auth import AuthError, TokenExpiredError
from services import ai_feedback_hooks as ai_hooks
from services.auth_service import AuthService, ROLE_ADMIN

logger = logging.getLogger(__name__)

router = APIRouter()
_service = AuthService()


# ============================================================
# 请求模型
# ============================================================

class RegisterRequest(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11, description="手机号(11位)")
    password: str = Field(..., min_length=6, max_length=64, description="密码(至少6位)")
    nickname: str | None = Field(None, max_length=50, description="昵称(可选)")
    birthdate: str | None = Field(
        None, description="出生日期 ISO8601(可选, 提供时硬校验≥18周岁, 酒类合规)")
    ageConfirmed: bool = Field(
        False, description="成年声明(酒类合规, 落库供下单年龄门复用)")


class LoginRequest(BaseModel):
    phone: str = Field(..., description="手机号")
    password: str = Field(..., description="密码")


class RefreshRequest(BaseModel):
    refreshToken: str = Field(..., description="刷新令牌")


class LogoutRequest(BaseModel):
    refreshToken: str | None = Field(None, description="刷新令牌(可选,一并吊销)")


class ChangePasswordRequest(BaseModel):
    oldPassword: str = Field(..., description="旧密码")
    newPassword: str = Field(..., min_length=6, max_length=64, description="新密码(至少6位)")


class SetRoleRequest(BaseModel):
    memberId: int = Field(..., ge=1, description="目标会员ID")
    role: str = Field(..., description="角色(member/admin)")


class SmsSendRequest(BaseModel):
    """发送短信验证码(P1-1)"""
    phone: str = Field(..., min_length=11, max_length=11, description="手机号(11位)")


class SmsVerifyRequest(BaseModel):
    """校验短信验证码(P1-1, 校验通过即消费)"""
    phone: str = Field(..., min_length=11, max_length=11, description="手机号(11位)")
    code: str = Field(..., min_length=6, max_length=6, description="验证码(6位数字)")


class SmsLoginRequest(BaseModel):
    """验证码登录(P1-1, 返回 JWT 双令牌)"""
    phone: str = Field(..., min_length=11, max_length=11, description="手机号(11位)")
    code: str = Field(..., min_length=6, max_length=6, description="验证码(6位数字)")


# ============================================================
# 异常映射与鉴权依赖
# ============================================================

def _handle_auth_error(exc: Exception):
    """认证异常统一映射"""
    if isinstance(exc, (AuthError, TokenExpiredError)):
        raise HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, KeyError):
        msg = str(exc)
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    logger.exception("auth_internal_error: %s", exc)
    raise HTTPException(status_code=500, detail="服务器内部错误")


def _extract_bearer_token(authorization: str | None) -> str:
    """从 Authorization 头提取 Bearer Token"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录: 请提供 Authorization: Bearer <token>")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="Authorization 头格式错误(须为 Bearer <token>)")
    return parts[1].strip()


async def get_current_member(
    authorization: str | None = Header(None, alias="Authorization"),
) -> dict:
    """FastAPI 依赖: 校验 access token 并返回当前会员

    用法: member = Depends(get_current_member)
    """
    token = _extract_bearer_token(authorization)
    try:
        return await _service.get_current_member(token)
    except Exception as exc:
        _handle_auth_error(exc)


async def require_admin(
    current_member: dict = Depends(get_current_member),
) -> dict:
    """FastAPI 依赖: 要求管理员角色

    用法: admin = Depends(require_admin)
    """
    if current_member.get("role") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_member


def _get_token_from_current(member: dict, authorization: str | None) -> str:
    """从已鉴权上下文取回原始 token(改密/登出需 jti)"""
    return _extract_bearer_token(authorization)


# ============================================================
# 公开端点
# ============================================================

@router.post("/api/auth/register", tags=["用户认证模块"])
async def register(data: RegisterRequest):
    """手机号注册(返回 JWT 双令牌)"""
    try:
        result = await _service.register(
            phone=data.phone,
            password=data.password,
            nickname=data.nickname,
            birthdate=data.birthdate,
            age_confirmed=data.ageConfirmed,
        )
        return result
    except Exception as exc:
        _handle_auth_error(exc)


@router.post("/api/auth/login", tags=["用户认证模块"])
async def login(data: LoginRequest):
    """密码登录(返回 JWT 双令牌)"""
    try:
        result = await _service.login(phone=data.phone, password=data.password)
        # v7.6 自动反馈: 登录成功 → 认证风控观察评分+配对(凭证有效期望 allow)
        await ai_hooks.on_login_success(data.phone)
        return result
    except Exception as exc:
        _handle_auth_error(exc)


@router.post("/api/auth/login/sms", tags=["用户认证模块"])
async def login_by_sms(data: SmsLoginRequest):
    """验证码登录(P1-1, 返回 JWT 双令牌; 校验通过即消费验证码)"""
    try:
        result = await _service.login_by_sms(phone=data.phone, code=data.code)
        await ai_hooks.on_login_success(data.phone)
        return result
    except Exception as exc:
        _handle_auth_error(exc)


# ============================================================
# 短信验证码(P1-1, 设计文档 2.2: 6 位数字/5 分钟/60 秒频控/日 10 次)
# ============================================================

@router.post("/api/sms/send", tags=["用户认证模块"])
async def sms_send(data: SmsSendRequest):
    """发送短信验证码(60 秒频控 + 日 10 次上限; 服务商未接入, 验证码写日志)"""
    try:
        return await _service.send_sms_code(data.phone)
    except Exception as exc:
        _handle_auth_error(exc)


@router.post("/api/sms/verify", tags=["用户认证模块"])
async def sms_verify(data: SmsVerifyRequest):
    """校验短信验证码(校验通过即消费, 一次性)"""
    try:
        return await _service.verify_sms_code(data.phone, data.code)
    except Exception as exc:
        _handle_auth_error(exc)


@router.post("/api/auth/refresh", tags=["用户认证模块"])
async def refresh(data: RefreshRequest):
    """刷新令牌(轮换机制: 旧 refresh 立即吊销)"""
    try:
        result = await _service.refresh(refresh_token=data.refreshToken)
        return result
    except Exception as exc:
        _handle_auth_error(exc)


@router.post("/api/auth/logout", tags=["用户认证模块"])
async def logout(
    data: LogoutRequest,
    authorization: str | None = Header(None, alias="Authorization"),
):
    """登出(吊销 access + refresh)"""
    token = _extract_bearer_token(authorization)
    try:
        result = await _service.logout(token, data.refreshToken)
        return result
    except Exception as exc:
        _handle_auth_error(exc)


# ============================================================
# 受保护端点(会员)
# ============================================================

@router.get("/api/auth/me", tags=["用户认证模块"])
async def me(current_member: dict = Depends(get_current_member)):
    """当前登录会员信息(需要 Bearer Token)"""
    return {"success": True, "member": current_member}


@router.post("/api/auth/change-password", tags=["用户认证模块"])
async def change_password(
    data: ChangePasswordRequest,
    current_member: dict = Depends(get_current_member),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """修改密码(级联吊销全部设备令牌, 返回新令牌对)"""
    token = _get_token_from_current(current_member, authorization)
    try:
        result = await _service.change_password(
            access_token=token,
            old_password=data.oldPassword,
            new_password=data.newPassword,
        )
        return result
    except Exception as exc:
        _handle_auth_error(exc)


# ============================================================
# 管理端点(管理员)
# ============================================================

@router.post("/api/auth/role", tags=["用户认证模块"])
async def set_role(
    data: SetRoleRequest,
    admin: dict = Depends(require_admin),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """设置会员角色(管理员专用)"""
    token = _get_token_from_current(admin, authorization)
    try:
        result = await _service.set_role(
            operator_token=token,
            member_id=data.memberId,
            new_role=data.role,
        )
        return result
    except Exception as exc:
        _handle_auth_error(exc)


@router.get("/api/auth/blacklist/stats", tags=["用户认证模块"])
async def blacklist_stats(admin: dict = Depends(require_admin)):
    """Token 黑名单统计(管理员专用, 监控用)"""
    size = await _service.auth_repo.blacklist_size()
    return {"success": True, "blacklistSize": size}


# ============================================================
# 路由注册
# ============================================================

def register_auth_routes(app):
    app.include_router(router)
