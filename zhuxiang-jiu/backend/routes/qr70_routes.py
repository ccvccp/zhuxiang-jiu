"""70号·AI智能二维码大模型路由(P0)

端点(P0 9 个):
    GET  /api/qr70/dict              六类码注册表公示(admin, 观测面)
    GET  /api/qr70/dict/{kind}       按码类列码型(admin, 观测面)
    GET  /api/qr70/dict/code/{id}    单码型详情(admin, 观测面)
    GET  /api/qr70/model/status      模型状态(admin, 观测面)
    POST /api/qr70/codes/generate    统一生成管道(admin, 决策面 off 409)
    POST /api/qr70/codes/redeem      统一核销管道(admin, 决策面 off 409)
    POST /api/qr70/codes/{nonce}/void 码作废(admin, 人工——不受开关影响)
    GET  /api/qr70/codes             码实例留痕视图(admin, 观测面)
    GET  /api/qr70/codes/{nonce}     单码实例详情(admin, 观测面)
    GET  /api/qr70/events            全链事件视图(admin, 观测面)
    POST /api/qr70/joy/report        愉悦度样本上报(admin, 快环——不受开关影响)
    GET  /api/qr70/joy/stats         愉悦度统计基线(admin, 观测面)

鉴权: 管理面 X-Role: admin(69号同款口径)。
统一口径(69号范式):
    - 观测面不受 QR70_MODE 影响
    - 快环观测上报(joy report)不受
      开关影响
    - 决策面(generate/redeem):
      off=拒绝(409)
    - 作废=安全方向动作(人工
      ——不受开关影响)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/qr70",
                   tags=["AI智能二维码大模型(70号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


def _map(exc: Exception) -> HTTPException:
    """统一异常映射(69号口径)"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        return HTTPException(status_code=404,
                             detail=msg)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409,
                             detail=str(exc))
    return HTTPException(status_code=500,
                         detail=str(exc))


def _require_decision_mode() -> None:
    """决策面前置(off=409)"""
    from services.qr70_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="QR70_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")


# ============================================================
# 请求模型
# ============================================================

class GenerateBody(BaseModel):
    memberId: int = Field(default=0,
                          description="会员 ID")
    codeId: str = Field(description="码型 ID(六类码注册表)")
    params: dict = Field(default_factory=dict,
                         description="码参数(码型白名单内)")
    scene: str = Field(default="",
                       description="业务场景(SCENES 域)")


class RedeemBody(BaseModel):
    code: str = Field(min_length=1,
                      description="完整码串(ZXBJ-QR55 格式)")
    operatorId: int = Field(default=0,
                           description="操作者 ID(审计)")


class JoyReportBody(BaseModel):
    codeId: str = Field(description="码型 ID")
    durationMs: int = Field(default=0, ge=0,
                            description="扫码耗时(ms)")
    completed: bool = Field(default=True,
                           description="任务是否完成")
    misTouch: bool = Field(default=False,
                           description="是否误触")
    memberId: int = Field(default=0,
                          description="会员 ID(归因)")


# ============================================================
# P0 端点(观测面)
# ============================================================

@router.get("/dict")
async def dict_view(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """六类码注册表公示(码型×场景×权限×
    生命周期——观测面)"""
    _require_admin(x_role)
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    return Qr70HubService().code_dict()


@router.get("/dict/{kind}")
async def dict_kind(
        kind: str,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """按码类列码型(观测面)"""
    _require_admin(x_role)
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    try:
        return Qr70HubService().kind_codes(kind)
    except Exception as e:
        raise _map(e) from e


@router.get("/dict/code/{code_id}")
async def dict_code(
        code_id: str,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """单码型详情(注册表+serviceId——
    观测面)"""
    _require_admin(x_role)
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    try:
        return Qr70HubService().code_detail(code_id)
    except Exception as e:
        raise _map(e) from e


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(观测面——off 不受影响)"""
    _require_admin(x_role)
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    return await Qr70HubService().model_status()


# ============================================================
# P0 端点(决策面——off 409)
# ============================================================

@router.post("/codes/generate")
async def codes_generate(
        body: GenerateBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """六类码统一生成(55号 qr55_crypto
    签名链——决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    try:
        return await Qr70HubService().generate(
            body.memberId, body.codeId,
            body.params, body.scene)
    except Exception as e:
        raise _map(e) from e


@router.post("/codes/redeem")
async def codes_redeem(
        body: RedeemBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """六类码统一核销(verify 四态+消费
    策略状态机——决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    try:
        return await Qr70HubService().redeem(
            body.code, body.operatorId)
    except Exception as e:
        raise _map(e) from e


@router.post("/codes/{nonce}/void")
async def codes_void(
        nonce: str,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """码作废(安全方向动作——人工,
    不受开关影响)"""
    _require_admin(x_role)
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    try:
        return await Qr70HubService().void_code(nonce)
    except Exception as e:
        raise _map(e) from e


# ============================================================
# P0 端点(留痕观测)
# ============================================================

@router.get("/codes")
async def codes_view(
        x_role: str | None = Header(default=None,
                                    alias="X-Role"),
        limit: int = 50, kind: str = ""):
    """码实例留痕视图(生命周期可审计
    ——观测面)"""
    _require_admin(x_role)
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    return await Qr70HubService().codes_view(
        limit=limit, kind=kind)


@router.get("/codes/{nonce}")
async def code_detail(
        nonce: str,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """单码实例详情(生命周期——观测面)"""
    _require_admin(x_role)
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    try:
        return await Qr70HubService()\
            .code_detail_by_nonce(nonce)
    except Exception as e:
        raise _map(e) from e


@router.get("/events")
async def events_view(
        x_role: str | None = Header(default=None,
                                    alias="X-Role"),
        limit: int = 50):
    """全链事件视图(生成/核销/重放拒绝/
    作废——四可审计)"""
    _require_admin(x_role)
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    return await Qr70HubService().events_view(
        limit=limit)


# ============================================================
# P0 端点(愉悦度观测底座——快环)
# ============================================================

@router.post("/joy/report")
async def joy_report(
        body: JoyReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """愉悦度样本上报(扫码耗时/完成/误触
    ——快环纯统计, 不受开关影响;
    愉悦度=观测指标, 永不直接触发
    策略变更)"""
    _require_admin(x_role)
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    try:
        return await Qr70HubService().report_joy(
            body.codeId, body.durationMs,
            body.completed, body.misTouch,
            body.memberId)
    except Exception as e:
        raise _map(e) from e


@router.get("/joy/stats")
async def joy_stats(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """愉悦度统计基线(按码型聚合
    ——观测面)"""
    _require_admin(x_role)
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    return await Qr70HubService().joy_stats()


def register_qr70_routes(app) -> None:
    """注册70号路由(main.py startup 调用)"""
    app.include_router(router)
