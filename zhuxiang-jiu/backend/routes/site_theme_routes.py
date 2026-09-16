"""网站图标智能管理模块路由(16 端点)

鉴权(v8.1 升级为 JWT 强校验, 堵死伪造 X-Role 头绕过):
    - 管理端(9 接口): Authorization: Bearer <token> + token 角色 admin
      (复用 auth_routes.require_admin 依赖: 无 Token/无效 Token → 401,
       角色非 admin → 403; 操作人从 Token 载荷提取, 不可伪造)
    - 公开(2 接口): 激活主题(C 端运行时换肤) / 图标库只读

大模型二代·三态灰度(SITE_THEME_MODE, 全站范式):
    - 决策面(7 写): @/_decision 门控——off 拒绝(409)
      + shadow/assist 响应标记(themeMode)
      (require_admin 为 FastAPI Depends——401/403 天然
       先于装饰器门控 409)
    - C 端运行时换肤(2 公开): active/icons 永不关停
    - 观测面(3 管理端 GET): 永不关停
    - 控制面(4): mode/override/guard/resume

异常映射(遵循项目约定):
    - KeyError → 404(主题/日志不存在)
    - ValueError → 409(锁定编辑/AI 评估未通过/字段非法)
    - 权限校验 → 401(未登录/Token 无效) / 403(非管理员)
"""


from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from routes.auth_routes import require_admin
from services.site_theme_service import SiteThemeService


router = APIRouter()
_service = SiteThemeService()


def _admin_id(admin: dict) -> int:
    """从已鉴权管理员上下文提取操作人 ID(Token 验证, 不可伪造)"""
    try:
        return int(admin.get("memberId", 0))
    except (TypeError, ValueError):
        return 0


def _handle(exc: Exception):
    """统一异常映射: KeyError → 404, ValueError → 409"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 大模型二代·三态灰度门控(SITE_THEME_MODE, 全站范式)
# ============================================================

async def _gate() -> dict:
    """决策面门槛(SITE_THEME_MODE=off → 409;
    shadow/assist 放行——大模型二代读取链:
    护栏暂停 > 运行时 override > env)"""
    from services.site_theme_mode_service import (
        SiteThemeModeService,
    )
    return await SiteThemeModeService() \
        .require_decision_mode()


def _decision(fn):
    """决策端点装饰器: 门控(off 409) +
    shadow/assist 标记(themeMode)

    鉴权优先: require_admin 为 FastAPI Depends——
    401/403 在进入本装饰器前完成(参数解析阶段),
    天然优先于门控 409。
    C 端运行时换肤(active/icons 公开端)不加——
    永不关停; 观测面(GET)不加——永不关停。
    """
    import functools
    from services.site_theme_mode_service import (
        MODE_VALUES,
    )

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            mode_state = await _gate()
        except ValueError as e:
            raise HTTPException(
                status_code=409,
                detail=str(e)) from e
        result = await fn(*args, **kwargs)
        if isinstance(result, dict) \
                and mode_state.get("mode") \
                in MODE_VALUES[1:]:
            result = {**result,
                      "themeMode":
                          mode_state["mode"]}
        return result
    return wrapper


# ============================================================
# 请求模型
# ============================================================

class ThemeColorsRequest(PydBaseModel):
    primary: str = Field(..., description="主色 #RRGGBB")
    primaryLight: str = Field(..., description="主色浅 #RRGGBB")
    navBar: str = Field(..., description="导航栏背景 #RRGGBB")
    tabSelected: str = Field(..., description="tabBar 选中色 #RRGGBB")
    tabColor: str = Field("#999999", description="tabBar 未选中色 #RRGGBB")
    tabBg: str = Field("#ffffff", description="tabBar 背景 #RRGGBB")
    textOnPrimary: str = Field("#ffffff", description="主色上文本色 #RRGGBB")


class CreateThemeRequest(PydBaseModel):
    name: str = Field(..., min_length=1, max_length=30,
                      description="主题名称")
    colors: ThemeColorsRequest
    icons: dict | None = Field(None, description="图标组(tabHome/tabProducts/"
                                                 "tabMine/quickGrid)")
    description: str = Field("", max_length=200, description="主题描述")


class UpdateThemeRequest(PydBaseModel):
    name: str | None = Field(None, min_length=1, max_length=30,
                             description="主题名称")
    colors: dict | None = Field(None, description="配色组(部分覆盖)")
    icons: dict | None = Field(None, description="图标组(部分覆盖)")
    description: str | None = Field(None, max_length=200,
                                    description="主题描述")


class CreateIconRequest(PydBaseModel):
    emoji: str | None = Field(None, max_length=8,
                              description="emoji 图标(与 image 二选一)")
    image: str | None = Field(None,
                              description="上传图片 data URL(data:image/*;base64, 与 emoji 二选一)")
    name: str = Field("", max_length=30, description="图标名称(可选)")


# ============================================================
# 管理端(9 接口)
# ============================================================

@router.post("/api/site-theme/themes", tags=["网站图标智能管理"])
@_decision
async def create_theme(
    data: CreateThemeRequest,
    admin: dict = Depends(require_admin),
):
    """创建主题方案(初始 draft, 落审计)"""
    try:
        return await _service.create_theme(
            _admin_id(admin), data.name,
            data.colors.model_dump(), data.icons, data.description)
    except Exception as exc:
        _handle(exc)


@router.get("/api/site-theme/themes", tags=["网站图标智能管理"])
async def list_themes(
    admin: dict = Depends(require_admin),
):
    """主题方案列表(管理端)"""
    return {"themes": await _service.list_themes()}


@router.put("/api/site-theme/themes/{theme_id}", tags=["网站图标智能管理"])
@_decision
async def update_theme(
    theme_id: int,
    data: UpdateThemeRequest,
    admin: dict = Depends(require_admin),
):
    """编辑主题(仅 draft 可编辑, active 锁定)"""
    try:
        return await _service.update_theme(
            _admin_id(admin), theme_id,
            name=data.name, colors=data.colors, icons=data.icons,
            description=data.description)
    except Exception as exc:
        _handle(exc)


@router.post("/api/site-theme/themes/{theme_id}/ai-check",
             tags=["网站图标智能管理"])
@_decision
async def ai_check(
    theme_id: int,
    admin: dict = Depends(require_admin),
):
    """AI 健康度评估(无障碍对比度/和谐度/品牌关联/图标完整)"""
    try:
        return await _service.ai_check(theme_id)
    except Exception as exc:
        _handle(exc)


@router.post("/api/site-theme/themes/{theme_id}/activate",
             tags=["网站图标智能管理"])
@_decision
async def activate_theme(
    theme_id: int,
    admin: dict = Depends(require_admin),
):
    """激活主题(AI<60 拒绝; C 端导航栏/tabBar 即时生效)"""
    try:
        return await _service.activate_theme(
            _admin_id(admin), theme_id)
    except Exception as exc:
        _handle(exc)


@router.post("/api/site-theme/themes/{theme_id}/archive",
             tags=["网站图标智能管理"])
@_decision
async def archive_theme(
    theme_id: int,
    admin: dict = Depends(require_admin),
):
    """归档主题"""
    try:
        return await _service.archive_theme(
            _admin_id(admin), theme_id)
    except Exception as exc:
        _handle(exc)


@router.get("/api/site-theme/admin/logs", tags=["网站图标智能管理"])
async def list_logs(
    theme_id: int | None = Query(None, gt=0, description="按主题过滤"),
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(require_admin),
):
    """变更审计日志列表"""
    return {"logs": await _service.list_logs(theme_id=theme_id,
                                             limit=limit)}


@router.post("/api/site-theme/admin/logs/{log_id}/rollback",
             tags=["网站图标智能管理"])
@_decision
async def rollback(
    log_id: int,
    admin: dict = Depends(require_admin),
):
    """一键回滚到指定变更点(恢复 before 快照)"""
    try:
        return await _service.rollback(_admin_id(admin), log_id)
    except Exception as exc:
        _handle(exc)


@router.get("/api/site-theme/admin/recommend", tags=["网站图标智能管理"])
async def recommend(
    month: int | None = Query(None, ge=1, le=12, description="月份(默认当前)"),
    admin: dict = Depends(require_admin),
):
    """AI 季节智能推荐(节日+季节+品牌基因多因子)"""
    return await _service.recommend(month)


# ============================================================
# 公开端(2 接口, C 端运行时拉取)
# ============================================================

@router.get("/api/site-theme/active", tags=["网站图标智能管理"])
async def get_active_theme():
    """当前激活主题(C 端导航栏/tabBar/图标运行时应用)"""
    return await _service.get_active_theme()


@router.get("/api/site-theme/icons", tags=["网站图标智能管理"])
async def list_icons(
    category: str | None = Query(None, description="按分类过滤: tab/grid/misc"),
):
    """图标资源库(公开只读)"""
    return {"icons": await _service.list_icons(category)}


@router.post("/api/site-theme/admin/icons", tags=["网站图标智能管理"])
@_decision
async def create_icon(
    data: CreateIconRequest,
    admin: dict = Depends(require_admin),
):
    """新增图标到资源库(emoji 或上传图片 data URL, 入库后可在编辑器选用)"""
    try:
        return await _service.create_icon(
            _admin_id(admin), emoji=data.emoji, image=data.image,
            name=data.name)
    except Exception as exc:
        _handle(exc)


# ============================================================
# 控制面(4, admin 门禁——大模型二代全站范式)
# ============================================================

@router.get("/api/site-theme/mode", tags=["网站图标智能管理"])
async def site_theme_mode_status(
    admin: dict = Depends(require_admin),
):
    """灰度总览(模式/读取链/护栏/红线公示——观测面永不关停)"""
    from services.site_theme_mode_service import (
        SiteThemeModeService,
    )
    return await SiteThemeModeService().status_view()


@router.post("/api/site-theme/mode/override",
             tags=["网站图标智能管理"])
async def site_theme_mode_override(
    data: dict = None,
    admin: dict = Depends(require_admin),
):
    """运行时切档(免容器重建; 空 mode=清除 override)"""
    body = data or {}
    from services.site_theme_mode_service import (
        SiteThemeModeService,
    )
    try:
        result = await SiteThemeModeService().set_override(
            str(body.get("mode") or ""),
            operator=f"admin:{_admin_id(admin)}")
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=409,
                             detail=str(e)) from e


@router.post("/api/site-theme/mode/guard",
             tags=["网站图标智能管理"])
async def site_theme_mode_guard(
    data: dict = None,
    admin: dict = Depends(require_admin),
):
    """护栏检查(三指标恶化 >3% 自动暂停)

    body: {aiLowScoreRate, rollbackRate,
           brokenIconRefRate, baseline?{同三键}}
    ——缺省时按图标主题仓储实时聚合(确定性, LLM 禁入)。
    """
    from services.site_theme_mode_service import (
        SiteThemeModeService,
    )
    if not isinstance(data, dict) or not data:
        # 缺省: 巡检聚合(确定性)
        from services.site_theme_scheduler import (
            run_guard_patrol,
        )
        r = await run_guard_patrol()
        return {"success": True,
                "data": {"metrics": r["metrics"],
                         "samples": r["samples"],
                         "breached": r["breached"],
                         "breaches": r["breaches"],
                         "pausedNow": r["pausedNow"]}}
    try:
        result = await SiteThemeModeService().guard_check(
            float(data.get("aiLowScoreRate") or 0),
            float(data.get("rollbackRate") or 0),
            float(data.get("brokenIconRefRate") or 0),
            baseline=data.get("baseline"))
        return {"success": True, "data": result}
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=409,
                             detail=str(e)) from e


@router.post("/api/site-theme/mode/resume",
             tags=["网站图标智能管理"])
async def site_theme_mode_resume(
    note: str = Query("", description="恢复备注(决策留痕)"),
    admin: dict = Depends(require_admin),
):
    """人工恢复(护栏暂停解除——决策留痕)"""
    from services.site_theme_mode_service import (
        SiteThemeModeService,
    )
    try:
        result = await SiteThemeModeService().resume(
            operator=f"admin:{_admin_id(admin)}",
            note=note)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=409,
                             detail=str(e)) from e


def register_site_theme_routes(app) -> None:
    """向 FastAPI 应用注册网站图标智能管理模块路由"""
    app.include_router(router)
