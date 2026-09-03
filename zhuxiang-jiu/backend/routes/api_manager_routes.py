"""44号·AI智能API管理模块路由(P0: API 资产中心)

端点(3, 管理面):
    GET   /api/api-manager/admin/apis          台账列表(module/status
                                                 过滤 + 分布统计)
    PATCH /api/api-manager/admin/apis/{apiId}  人工修正归属/状态
    POST  /api/api-manager/admin/apis/sync      手动重扫(diff 返回)

鉴权:
    - 管理端: X-Role: admin 头(43号同款口径)
"""

from fastapi import APIRouter, Header, HTTPException, Query, Request

from services.api_registry_service import ApiRegistryService

router = APIRouter(prefix="/api/api-manager",
                   tags=["API智能管理(44号)"])
_service = ApiRegistryService()


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


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


def register_api_manager_routes(app) -> None:
    """注册44号路由(main.py startup 调用)"""
    app.include_router(router)
