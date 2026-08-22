"""后台管理模块路由(12 端点)

鉴权:
    - 管理端: X-Role: admin 头标识管理员身份(全部接口需校验)
    - 操作人 ID 由 X-Admin-Id 头携带(可选, 缺省 0)

异常映射(遵循项目约定):
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 用户名重复/状态非法/密码错误等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 认证(1):     login
    - 管理员(5):   create-user / list-users / get-user / update-user / reset-password
    - 角色(2):     create-role / list-roles
    - 权限(1):     assign-permissions
    - 日志(1):     logs
    - 配置(2):     create-config / list-configs
    - 仪表盘(1):   dashboard
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.admin_service import AdminService


router = APIRouter()
_service = AdminService()


# ============================================================
# 鉴权与异常映射辅助(对齐 points_routes 风格)
# ============================================================

def _require_admin(x_role: Optional[str]):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _get_operator_id(x_admin_id: Optional[str]) -> int:
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


class CreateUserRequest(PydBaseModel):
    username: str = Field(..., description="管理员用户名")
    password: str = Field(..., description="密码(≥6位)")
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
    newPassword: str = Field(..., description="新密码(≥6位)")


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
    """管理员登录(密码校验 + 失败锁定 + 返回权限)"""
    try:
        result = await _service.login(
            username=data.username, password=data.password,
            ip=data.ip, device=data.device,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 管理员(5) ---

@router.post("/api/admin/users", tags=["后台管理模块"])
async def create_user(
    data: CreateUserRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """创建管理员(用户名唯一 + 角色校验)"""
    _require_admin(x_role)
    try:
        result = await _service.create_user(
            username=data.username, password=data.password,
            real_name=data.realName, employee_no=data.employeeNo,
            department=data.department, position=data.position,
            phone=data.phone, email=data.email, role_ids=data.roleIds,
            operator_id=_get_operator_id(x_admin_id),
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/admin/users", tags=["后台管理模块"])
async def list_users(
    x_role: str = Header(None, alias="X-Role"),
    status: str = Query(None, description="按状态筛选(normal/disabled/locked)"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """管理端列表(管理员列表, 支持状态筛选)"""
    _require_admin(x_role)
    try:
        result = await _service.list_users(status=status, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/admin/users/{user_id}", tags=["后台管理模块"])
async def get_user(
    user_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """管理员详情(含角色列表)"""
    _require_admin(x_role)
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
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """更新管理员(不含密码, 密码走 reset-password)"""
    _require_admin(x_role)
    try:
        result = await _service.update_user(
            user_id=user_id,
            real_name=data.realName, department=data.department,
            position=data.position, phone=data.phone, email=data.email,
            status=data.status, expire_date=data.expireDate,
            operator_id=_get_operator_id(x_admin_id),
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/admin/users/{user_id}/reset-password", tags=["后台管理模块"])
async def reset_password(
    user_id: int,
    data: ResetPasswordRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """密码重置(超管重置任意管理员密码)"""
    _require_admin(x_role)
    try:
        result = await _service.reset_password(
            user_id=user_id, new_password=data.newPassword,
            operator_id=_get_operator_id(x_admin_id),
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 角色(2) ---

@router.post("/api/admin/roles", tags=["后台管理模块"])
async def create_role(
    data: CreateRoleRequest,
    x_role: str = Header(None, alias="X-Role"),
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """创建角色(角色编码唯一)"""
    _require_admin(x_role)
    try:
        result = await _service.create_role(
            role_code=data.roleCode, role_name=data.roleName,
            description=data.description, data_scope=data.dataScope,
            permissions=data.permissions,
            operator_id=_get_operator_id(x_admin_id),
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/admin/roles", tags=["后台管理模块"])
async def list_roles(
    x_role: str = Header(None, alias="X-Role"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """角色列表"""
    _require_admin(x_role)
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
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """权限分配(覆盖式更新用户角色 → 返回合并后的权限编码)"""
    _require_admin(x_role)
    try:
        result = await _service.assign_permissions(
            user_id=user_id, role_ids=data.roleIds,
            operator_id=_get_operator_id(x_admin_id),
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 日志(1) ---

@router.get("/api/admin/logs", tags=["后台管理模块"])
async def list_logs(
    x_role: str = Header(None, alias="X-Role"),
    user_id: int = Query(None, description="按管理员ID筛选"),
    module: str = Query(None, description="按模块筛选(auth/admin_user/admin_role/...)"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
):
    """操作日志查询(按时间倒序, 支持用户/模块筛选)"""
    _require_admin(x_role)
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
    x_admin_id: str = Header(None, alias="X-Admin-Id"),
):
    """创建/更新系统配置(已存在则覆盖)"""
    _require_admin(x_role)
    try:
        result = await _service.create_config(
            config_key=data.configKey, config_value=data.configValue,
            config_type=data.configType, module=data.module,
            description=data.description,
            operator_id=_get_operator_id(x_admin_id),
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/admin/configs", tags=["后台管理模块"])
async def list_configs(
    x_role: str = Header(None, alias="X-Role"),
    module: str = Query(None, description="按模块筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """系统配置列表(支持模块筛选)"""
    _require_admin(x_role)
    try:
        result = await _service.list_configs(module=module, limit=limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 仪表盘(1) ---

@router.get("/api/admin/dashboard", tags=["后台管理模块"])
async def get_dashboard(
    x_role: str = Header(None, alias="X-Role"),
):
    """仪表盘统计(会员/订单/收入/产品/管理员/角色/最近日志)"""
    _require_admin(x_role)
    try:
        result = await _service.get_dashboard()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_admin_routes(app):
    """注册后台管理模块路由"""
    app.include_router(router)
