"""后台管理模块路由(13 端点)

鉴权(会话机制, 30分钟滑动过期):
    - 登录成功返回 sessionToken, 后续请求携带 X-Admin-Token 头
    - AUTH_MODE=strict: 仅接受有效会话 Token(生产推荐)
    - AUTH_MODE=compat(默认): 兼容旧 X-Role: admin 头(过渡期)
    - 操作人 ID 由 X-Admin-Id 头携带(可选, 缺省取会话 userId)

异常映射(遵循项目约定):
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 用户名重复/状态非法/密码错误等)
    - 权限校验 → 401(未登录/会话过期) / 403(无权操作)

端点分布:
    - 认证(2):     login / logout
    - 管理员(5):   create-user / list-users / get-user / update-user / reset-password
    - 角色(2):     create-role / list-roles
    - 权限(1):     assign-permissions
    - 日志(1):     logs
    - 配置(2):     create-config / list-configs
    - 仪表盘(1):   dashboard
"""


import os

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.admin_service import AdminService


router = APIRouter()
_service = AdminService()


# ============================================================
# 鉴权与异常映射辅助(对齐 points_routes 风格)
# ============================================================

async def _require_admin(
    x_role: str | None,
    x_admin_token: str | None = None,
    allow_password_change: bool = False,
    allow_two_factor: bool = False,
) -> dict | None:
    """校验管理员权限(会话 Token 优先, 兼容旧 X-Role 头)

    AUTH_MODE 约定(与项目 JWT 中间件一致):
        - strict: 仅 X-Admin-Token 有效会话可访问
        - compat(默认): 有效会话 或 旧 X-Role: admin 头均可访问

    首登强制改密: 会话带 mustChangePassword 标记(默认密码未修改)时,
    仅放行改密端点(allow_password_change=True), 其余接口 403。

    2FA 待验证(P0-5): 会话带 pendingTwoFactor 标记(密码已过、动态口令
    未验证)时, 仅放行 2FA 验证端点(allow_two_factor=True), 其余 403。

    Returns:
        有效会话 dict(含 userId); 旧头兼容模式返回 None

    Raises:
        HTTPException: 401(会话无效/过期) 或 403(无权限/须先改密/须先2FA)
    """
    # 会话 Token 优先
    if x_admin_token:
        session = await _service.verify_session(x_admin_token)
        if session is not None:
            if session.get("pendingTwoFactor") and not allow_two_factor:
                raise HTTPException(
                    status_code=403,
                    detail="双因素验证未完成(POST /api/admin/2fa/verify)")
            if session.get("mustChangePassword") and not allow_password_change:
                raise HTTPException(
                    status_code=403,
                    detail="首次登录请先修改默认密码(POST /api/admin/users/"
                           "{user_id}/reset-password)")
            return session
        raise HTTPException(status_code=401, detail="会话无效或已过期, 请重新登录")
    # 兼容旧头(AUTH_MODE=strict 时拒绝)
    auth_mode = os.environ.get("AUTH_MODE", "compat").lower()
    if auth_mode != "strict" and x_role == "admin":
        return None
    raise HTTPException(status_code=403, detail="需要管理员权限")


def _get_operator_id(x_admin_id: str | None) -> int:
    """从 X-Admin-Id 头提取操作人ID(缺省 0)"""
    if not x_admin_id:
        return 0
    try:
        return int(x_admin_id)
    except (ValueError, TypeError):
        return 0


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

class LoginRequest(PydBaseModel):
    username: str = Field(..., description="管理员用户名")
    password: str = Field(..., description="密码")
    ip: str = Field("", description="登录IP")
    device: str = Field("", description="设备信息")
    totpCode: str | None = Field(None, description="2FA 动态口令(已开启双因素的管理员必填, 6位数字)")


class TwoFactorCodeRequest(PydBaseModel):
    totpCode: str = Field(..., description="2FA 动态口令(6位数字)")


class CreateUserRequest(PydBaseModel):
    username: str = Field(..., description="管理员用户名")
    password: str = Field(..., description="密码(≥12位+大写+小写+数字+特殊字符)")
    realName: str = Field("", description="真实姓名")
    employeeNo: str = Field("", description="工号")
    department: str = Field("", description="部门")
    position: str = Field("", description="岗位")
    phone: str = Field("", description="电话")
    email: str = Field("", description="邮箱")
    roleIds: list[int] = Field(default_factory=list, description="角色ID列表")


class UpdateUserRequest(PydBaseModel):
    realName: str = Field(None, description="真实姓名")
    department: str = Field(None, description="部门")
    position: str = Field(None, description="岗位")
    phone: str = Field(None, description="电话")
    email: str = Field(None, description="邮箱")
    status: str = Field(None, description="状态(normal/disabled/locked)")
    expireDate: str = Field(None, description="有效期")


class ResetPasswordRequest(PydBaseModel):
    newPassword: str = Field(..., description="新密码(≥12位+大写+小写+数字+特殊字符)")


class CreateRoleRequest(PydBaseModel):
    roleCode: str = Field(..., description="角色编码")
    roleName: str = Field(..., description="角色名称")
    description: str = Field("", description="描述")
    dataScope: str = Field("all", description="数据范围(all/dept/personal)")
    permissions: list[str] = Field(default_factory=list, description="权限编码列表")


class AssignPermissionsRequest(PydBaseModel):
    roleIds: list[int] = Field(..., description="角色ID列表(覆盖式)")


class CreateConfigRequest(PydBaseModel):
    configKey: str = Field(..., description="配置键")
    configValue: str = Field(..., description="配置值")
    configType: str = Field("string", description="类型(string/int/bool/json)")
    module: str = Field("system", description="所属模块")
    description: str = Field("", description="描述")


# ============================================================
# P0 接口(12 个)
# ============================================================

# --- 认证(1) ---

@router.post("/api/admin/login", tags=["后台管理模块"])
async def login(data: LoginRequest):
    """管理员登录(密码校验 + 失败锁定 + 返回权限与会话Token)"""
    try:
        result = await _service.login(
            username=data.username, password=data.password,
            ip=data.ip, device=data.device, totp_code=data.totpCode,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 1b. 2FA 双因素(TOTP, P0-5: 高危角色强制)
# ============================================================

@router.post("/api/admin/2fa/setup", tags=["后台管理模块"])
async def setup_two_factor(
    x_role: str | None = Header(default=None, alias="X-Role"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_admin_id: str | None = Header(default=None, alias="X-Admin-Id"),
):
    """生成 2FA 密钥(返回 secret + otpauth URI, 未启用状态)

    流程: setup(扫码绑定) → enable(提交验证码开启) → 登录须携带 totpCode
    """
    session = await _require_admin(x_role, x_admin_token)
    user_id = session["userId"] if session else _get_operator_id(x_admin_id)
    try:
        result = await _service.setup_2fa(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/admin/2fa/enable", tags=["后台管理模块"])
async def enable_two_factor(
    data: TwoFactorCodeRequest,
    x_role: str | None = Header(default=None, alias="X-Role"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_admin_id: str | None = Header(default=None, alias="X-Admin-Id"),
):
    """开启 2FA(验证 setup 绑定的动态口令, 开启后登录强制双因素)"""
    session = await _require_admin(x_role, x_admin_token)
    user_id = session["userId"] if session else _get_operator_id(x_admin_id)
    try:
        result = await _service.enable_2fa(user_id, data.totpCode)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/admin/2fa/verify", tags=["后台管理模块"])
async def verify_two_factor(
    data: TwoFactorCodeRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """登录二次验证(密码通过后提交动态口令, 清除 pendingTwoFactor 完成登录)"""
    try:
        result = await _service.verify_2fa_login(x_admin_token, data.totpCode)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/admin/logout", tags=["后台管理模块"])
async def logout(
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
):
    """管理员登出(销毁会话)"""
    if not x_admin_token:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Admin-Token 头")
    try:
        result = await _service.logout(x_admin_token)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 管理员(5) ---

@router.post("/api/admin/users", tags=["后台管理模块"])
async def create_user(
    data: CreateUserRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """创建管理员(用户名唯一 + 角色校验 + 密码复杂度)"""
    session = await _require_admin(x_role, x_admin_token)
    operator_fallback = session.get("userId", 0) if session else 0
    operator_id = _get_operator_id(x_admin_id) or operator_fallback
    try:
        result = await _service.create_user(
            username=data.username, password=data.password,
            real_name=data.realName, employee_no=data.employeeNo,
            department=data.department, position=data.position,
            phone=data.phone, email=data.email, role_ids=data.roleIds,
            operator_id=operator_id,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/admin/users", tags=["后台管理模块"])
async def list_users(
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
    status: str = Query(None, description="按状态筛选(normal/disabled/locked)"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """管理端列表(管理员列表, 支持状态筛选)"""
    await _require_admin(x_role, x_admin_token)
    try:
        result = await _service.list_users(status=status, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/admin/users/{user_id}", tags=["后台管理模块"])
async def get_user(
    user_id: int,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
):
    """管理员详情(含角色列表)"""
    await _require_admin(x_role, x_admin_token)
    try:
        result = await _service.get_user(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.put("/api/admin/users/{user_id}", tags=["后台管理模块"])
async def update_user(
    user_id: int,
    data: UpdateUserRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """更新管理员(不含密码, 密码走 reset-password)"""
    session = await _require_admin(x_role, x_admin_token)
    operator_fallback = session.get("userId", 0) if session else 0
    operator_id = _get_operator_id(x_admin_id) or operator_fallback
    try:
        result = await _service.update_user(
            user_id=user_id,
            real_name=data.realName, department=data.department,
            position=data.position, phone=data.phone, email=data.email,
            status=data.status, expire_date=data.expireDate,
            operator_id=operator_id,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/admin/users/{user_id}/reset-password", tags=["后台管理模块"])
async def reset_password(
    user_id: int,
    data: ResetPasswordRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """密码重置(超管重置任意管理员密码, 新密码须满足复杂度)

    首登强制改密: 会话带 mustChangePassword 标记时仅可重置本人密码。
    """
    session = await _require_admin(x_role, x_admin_token,
                                    allow_password_change=True)
    if session and session.get("mustChangePassword") and user_id != session.get("userId"):
        raise HTTPException(
            status_code=403, detail="首次登录须先修改本人密码")
    operator_fallback = session.get("userId", 0) if session else 0
    operator_id = _get_operator_id(x_admin_id) or operator_fallback
    try:
        result = await _service.reset_password(
            user_id=user_id, new_password=data.newPassword,
            operator_id=operator_id,
        )
        # 改密成功后解除会话"须改密"限制
        if session and session.get("mustChangePassword"):
            await _service.clear_must_change_password(x_admin_token)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 角色(2) ---

@router.post("/api/admin/roles", tags=["后台管理模块"])
async def create_role(
    data: CreateRoleRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """创建角色(角色编码唯一)"""
    session = await _require_admin(x_role, x_admin_token)
    operator_fallback = session.get("userId", 0) if session else 0
    operator_id = _get_operator_id(x_admin_id) or operator_fallback
    try:
        result = await _service.create_role(
            role_code=data.roleCode, role_name=data.roleName,
            description=data.description, data_scope=data.dataScope,
            permissions=data.permissions,
            operator_id=operator_id,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/admin/roles", tags=["后台管理模块"])
async def list_roles(
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """角色列表"""
    await _require_admin(x_role, x_admin_token)
    try:
        result = await _service.list_roles(limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 权限(1) ---

@router.post("/api/admin/users/{user_id}/permissions", tags=["后台管理模块"])
async def assign_permissions(
    user_id: int,
    data: AssignPermissionsRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """权限分配(覆盖式更新用户角色 → 返回合并后的权限编码)"""
    session = await _require_admin(x_role, x_admin_token)
    operator_fallback = session.get("userId", 0) if session else 0
    operator_id = _get_operator_id(x_admin_id) or operator_fallback
    try:
        result = await _service.assign_permissions(
            user_id=user_id, role_ids=data.roleIds,
            operator_id=operator_id,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 日志(1) ---

@router.get("/api/admin/logs", tags=["后台管理模块"])
async def list_logs(
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
    user_id: int = Query(None, description="按管理员ID筛选"),
    module: str = Query(None, description="按模块筛选(auth/admin_user/admin_role/...)"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """操作日志查询(按时间倒序, 支持用户/模块筛选)"""
    await _require_admin(x_role, x_admin_token)
    try:
        result = await _service.list_logs(user_id=user_id, module=module,
                                             limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 配置(2) ---

@router.post("/api/admin/configs", tags=["后台管理模块"])
async def create_config(
    data: CreateConfigRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """创建/更新系统配置(已存在则覆盖)"""
    session = await _require_admin(x_role, x_admin_token)
    operator_fallback = session.get("userId", 0) if session else 0
    operator_id = _get_operator_id(x_admin_id) or operator_fallback
    try:
        result = await _service.create_config(
            config_key=data.configKey, config_value=data.configValue,
            config_type=data.configType, module=data.module,
            description=data.description,
            operator_id=operator_id,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/admin/configs", tags=["后台管理模块"])
async def list_configs(
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
    module: str = Query(None, description="按模块筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """系统配置列表(支持模块筛选)"""
    await _require_admin(x_role, x_admin_token)
    try:
        result = await _service.list_configs(module=module, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 仪表盘(1) ---

@router.get("/api/admin/dashboard", tags=["后台管理模块"])
async def get_dashboard(
    x_role: str = Header(None, alias="X-Role"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
):
    """仪表盘统计(会员/订单/收入/产品/管理员/角色/最近日志)"""
    await _require_admin(x_role, x_admin_token)
    try:
        result = await _service.get_dashboard()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_admin_routes(app):
    """注册后台管理模块路由"""
    app.include_router(router)
