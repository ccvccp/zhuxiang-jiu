"""44号·AI智能API管理模块路由(P0 资产中心 + P1 开发者凭证)

端点(P0, 管理面 3):
    GET   /api/api-manager/admin/apis          台账列表(module/status
                                                 过滤 + 分布统计)
    PATCH /api/api-manager/admin/apis/{apiId}  人工修正归属/状态
    POST  /api/api-manager/admin/apis/sync     手动重扫(diff 返回)

端点(P1, 会员面 4——X-Member-Id 头, JWTAuthMiddleware 注入):
    POST /api/api-manager/keys                 申请(默认自动批,
                                               apiKey 明文仅此一次)
    GET  /api/api-manager/keys                 我的 Key 列表
    POST /api/api-manager/keys/{keyId}/revoke 自助吊销
    POST /api/api-manager/keys/{keyId}/renew   续期(90 天延展)

端点(P1, 管理面 4——X-Role: admin):
    GET  /api/api-manager/admin/apis/keys               全量+过滤
    POST /api/api-manager/admin/apis/keys/{keyId}/approve 审批通过
    POST /api/api-manager/admin/apis/keys/{keyId}/reject  驳回申请
    POST /api/api-manager/admin/apis/keys/{keyId}/revoke  管理员吊销

鉴权:
    - 管理端: X-Role: admin 头(43号同款口径)
    - 会员端: X-Member-Id 头(JWTAuthMiddleware 注入/兼容)
"""

from fastapi import APIRouter, Header, HTTPException, Query, Request

from services.api_registry_service import ApiRegistryService
from services.api_key_service import ApiKeyService

router = APIRouter(prefix="/api/api-manager",
                   tags=["API智能管理(44号)"])
_service = ApiRegistryService()
_key_service = ApiKeyService()


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _require_member_id(x_member_id: str | None) -> int:
    """从 X-Member-Id 头提取会员ID(40号会员面惯例)"""
    if not x_member_id:
        raise HTTPException(status_code=401,
                            detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401,
                            detail="X-Member-Id 头格式错误") from None


def _handle(exc: Exception):
    """统一异常映射(43号同款)"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# P0: API 资产中心(管理面)
# ============================================================

@router.get("/admin/apis")
async def admin_list_apis(
    module: str = Query(None, description="模块归属过滤"),
    status: str = Query(None,
                        description="生命周期过滤"
                                    "(development/published/"
                                    "deprecated/offline)"),
    missing: bool = Query(None, description="消失路由过滤"),
    limit: int = Query(2000, ge=1, le=10000),
    x_role: str = Header(default="", alias="X-Role"),
):
    """API 资产台账(分布统计 + 列表)"""
    _require_admin(x_role)
    try:
        return await _service.list_registry(
            module=module, status=status, missing=missing,
            limit=limit)
    except Exception as e:
        raise _handle(e) from e


@router.patch("/admin/apis/{api_id}")
async def admin_patch_api(
    api_id: int,
    body: dict,
    x_role: str = Header(default="", alias="X-Role"),
):
    """人工修正台账条目(module 修正后不被重扫覆盖)"""
    _require_admin(x_role)
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        return await _service.patch_entry(
            api_id,
            module=body.get("module"),
            status=body.get("status"))
    except Exception as e:
        raise _handle(e) from e


@router.post("/admin/apis/sync")
async def admin_sync_apis(
    request: Request,
    x_role: str = Header(default="", alias="X-Role"),
):
    """手动重扫台账(幂等, diff 返回: 新增/消失/module 修正)"""
    _require_admin(x_role)
    try:
        return await _service.sync_registry(request.app)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P1: 开发者凭证(会员面)
# ============================================================

@router.post("/keys")
async def apply_key(
    body: dict,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """申请 API Key(默认自动批秒级发放; apiKey 明文仅本次返回)"""
    member_id = _require_member_id(x_member_id)
    if not isinstance(body, dict):
        raise HTTPException(status_code=409, detail="请求体需为对象")
    try:
        return await _key_service.apply_key(
            member_id, str(body.get("name") or ""))
    except Exception as e:
        raise _handle(e) from e


@router.get("/keys")
async def list_my_keys(
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """我的 Key 列表(前缀展示/状态/用量摘要)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _key_service.list_my_keys(member_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/keys/{key_id}/revoke")
async def revoke_my_key(
    key_id: int,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """自助吊销(仅本人的 Key; 缓存即时失效)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _key_service.revoke_key(member_id, key_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/keys/{key_id}/renew")
async def renew_my_key(
    key_id: int,
    x_member_id: str = Header(default="", alias="X-Member-Id"),
):
    """续期(expireAt 自当前时刻延展 90 天)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _key_service.renew_key(member_id, key_id)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# P1: 开发者凭证(管理面)
# ============================================================

@router.get("/admin/apis/keys")
async def admin_list_keys(
    status: str = Query(None,
                        description="状态过滤(pending/active/"
                                    "revoked/expired/rejected)"),
    member_id: int = Query(None, description="会员ID过滤"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """全量 Key 列表(状态分布统计)"""
    _require_admin(x_role)
    try:
        return await _key_service.admin_list_keys(
            status=status, member_id=member_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/admin/apis/keys/{key_id}/approve")
async def admin_approve_key(
    key_id: int,
    x_role: str = Header(default="", alias="X-Role"),
):
    """审批通过(pending → active, 有效期自批准起算)"""
    _require_admin(x_role)
    try:
        return await _key_service.admin_approve(key_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/admin/apis/keys/{key_id}/reject")
async def admin_reject_key(
    key_id: int,
    x_role: str = Header(default="", alias="X-Role"),
):
    """驳回申请(pending → rejected)"""
    _require_admin(x_role)
    try:
        return await _key_service.admin_reject(key_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/admin/apis/keys/{key_id}/revoke")
async def admin_revoke_key(
    key_id: int,
    x_role: str = Header(default="", alias="X-Role"),
):
    """管理员吊销(任意状态 → revoked)"""
    _require_admin(x_role)
    try:
        return await _key_service.admin_revoke(key_id)
    except Exception as e:
        raise _handle(e) from e


def register_api_manager_routes(app) -> None:
    """注册44号路由(main.py startup 调用)"""
    app.include_router(router)
