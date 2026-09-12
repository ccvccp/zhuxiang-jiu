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


# ============================================================
# P1 溯源码升维(签名化瓶码+分层呈现)
# ============================================================

class BottleGenerateBody(BaseModel):
    memberId: int = Field(default=0,
                          description="操作/归属会员 ID")
    blc: str = Field(description="BLC 生命码(22号流通域)")
    batchNo: str = Field(default="",
                         description="批次号(可选——传入则交叉校验)")


class ViewReportBody(BaseModel):
    persona: str = Field(description="画像(quality/story/value)")
    dwell: dict = Field(default_factory=dict,
                        description="段位停留 {sectionId: ms}")
    clicked: list[str] = Field(default_factory=list,
                               description="点击段位列表")
    bottle: str = Field(default="",
                        description="瓶码(截断留痕——可选)")


@router.post("/trace/bottle/generate")
async def trace_bottle_generate(
        body: BottleGenerateBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """签名化瓶码生成(55号签名链绑定
    22号 BLC——决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_trace_service import (
        Qr70TraceService,
    )
    try:
        return await Qr70TraceService()\
            .bottle_generate(
                body.memberId, body.blc,
                body.batchNo)
    except Exception as e:
        raise _map(e) from e


@router.get("/trace/personas")
async def trace_personas():
    """三画像字典公示(公开——trace-view
    惯例, 免登录)"""
    from services.qr70_trace_service import (
        Qr70TraceService,
    )
    return Qr70TraceService().persona_dict()


@router.get("/trace/view")
async def trace_view(
        code: str,
        persona: str = "quality",
        deviceCap: str = "none"):
    """扫瓶码分层呈现(公开免登录——
    签名码验签/存量 BLC 直读兼容;
    决策面 off 409; 数据 100% 来自
    22/33号查询层)"""
    from services.qr70_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="QR70_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.qr70_trace_service import (
        Qr70TraceService,
    )
    try:
        return await Qr70TraceService().trace_view(
            code, persona, deviceCap)
    except Exception as e:
        raise _map(e) from e


@router.post("/trace/view/report")
async def trace_view_report(body: ViewReportBody):
    """停留/点击观测上报(公开快环
    ——不受开关影响, 无 PII; P7 慢环
    信息优先级建议书消费底座)"""
    from services.qr70_trace_service import (
        Qr70TraceService,
    )
    try:
        return await Qr70TraceService().view_report(
            body.persona, body.dwell,
            body.clicked, body.bottle)
    except Exception as e:
        raise _map(e) from e


@router.get("/trace/view/stats")
async def trace_view_stats(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """停留/点击聚合观测(按画像×信息段
    ——观测面)"""
    _require_admin(x_role)
    from services.qr70_trace_service import (
        Qr70TraceService,
    )
    return await Qr70TraceService().view_stats()


# ============================================================
# P2 认证码统一(三链承接+漂移观测+失败基线)
# ============================================================

class AuthBeginBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    channel: str = Field(description="认证通道(entry_qr/confirm/biometric)")
    fingerprint: str = Field(default="",
                              description="设备指纹(脱敏留痕)")
    riskHint: int = Field(default=0, ge=0, le=100,
                          description="风控分提示(0-100)")


class DriftCheckBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    storedFingerprint: str = Field(description="已存指纹(观测比对)")
    presentedFingerprint: str = Field(description="当前呈现指纹")
    riskScore: int = Field(default=0, ge=0, le=100,
                           description="风控分(39号 guard 口径)")
    trustedDevice: bool = Field(default=False,
                                description="可信设备(39号 30 天)")


class FailureReportBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    channel: str = Field(description="认证通道")
    failureMode: str = Field(description="失败模式(六域)")


@router.get("/auth/dict")
async def auth_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """认证码统一字典(通道×漂移×失败
    模式域公示——观测面)"""
    _require_admin(x_role)
    from services.qr70_auth_service import (
        Qr70AuthService,
    )
    return Qr70AuthService().auth_dict()


@router.post("/auth/begin")
async def auth_begin(
        body: AuthBeginBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """统一认证会话发起(三链 70号 承接
    面——决策面 off 409; 39/48/69号
    零改动)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_auth_service import (
        Qr70AuthService,
    )
    try:
        return await Qr70AuthService().auth_begin(
            body.memberId, body.channel,
            body.fingerprint, body.riskHint)
    except Exception as e:
        raise _map(e) from e


@router.post("/auth/drift/check")
async def auth_drift_check(
        body: DriftCheckBody):
    """设备指纹漂移校准观测(快环
    ——不受开关影响, 观测面不阻断
    业务; 指纹脱敏留痕)"""
    from services.qr70_auth_service import (
        Qr70AuthService,
    )
    try:
        return await Qr70AuthService().drift_check(
            body.memberId, body.storedFingerprint,
            body.presentedFingerprint,
            body.riskScore, body.trustedDevice)
    except Exception as e:
        raise _map(e) from e


@router.post("/auth/failure/report")
async def auth_failure_report(
        body: FailureReportBody):
    """认证失败模式上报(快环纯统计
    ——不受开关影响; 阈值调整走 P7
    慢环 46号审批)"""
    from services.qr70_auth_service import (
        Qr70AuthService,
    )
    try:
        return await Qr70AuthService()\
            .failure_report(
                body.memberId, body.channel,
                body.failureMode)
    except Exception as e:
        raise _map(e) from e


@router.get("/auth/failure/stats")
async def auth_failure_stats(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """失败模式统计基线(按通道×模式
    ——观测面)"""
    _require_admin(x_role)
    from services.qr70_auth_service import (
        Qr70AuthService,
    )
    return await Qr70AuthService().failure_stats()


# ============================================================
# P3 收货码(三要素校验+证据链+异常升档)
# ============================================================

class ReceivingIssueBody(BaseModel):
    orderId: str = Field(description="订单号(SHIPPED 态)")
    fenceKm: int = Field(default=20, gt=0,
                         description="围栏半径 km(默认 20——67/68号 LBS 范式)")


class ReceivingScanBody(BaseModel):
    code: str = Field(description="收货码(ZXBJ-QR70 签名链)")
    memberId: int = Field(description="扫码会员 ID(归属校验)")
    city: str = Field(default="",
                     description="扫码城市(围栏要素)")
    lng: float | None = Field(default=None,
                              description="经度(坐标级可选)")
    lat: float | None = Field(default=None,
                              description="纬度(坐标级可选)")
    deviceId: str = Field(default="",
                           description="设备标识(摘要留痕)")


class ReceivingConfirmBody(BaseModel):
    code: str = Field(description="收货码")
    operatorId: int = Field(default=0,
                            description="操作者 ID(审计)")
    escalatedAck: bool = Field(default=False,
                               description="异常扫码二次确认(升档铁律)")


@router.get("/receiving/dict")
async def receiving_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """收货码字典(三要素+判定域公示
    ——观测面)"""
    _require_admin(x_role)
    from services.qr70_receiving_service import (
        Qr70ReceivingService,
    )
    return Qr70ReceivingService().dict_view()


@router.post("/receiving/issue")
async def receiving_issue(
        body: ReceivingIssueBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """签收码签发(绑定订单归属人——
    决策面 off 409; 仅 SHIPPED 可签)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_receiving_service import (
        Qr70ReceivingService,
    )
    try:
        return await Qr70ReceivingService().issue(
            body.orderId, body.fenceKm)
    except Exception as e:
        raise _map(e) from e


@router.post("/receiving/scan")
async def receiving_scan(body: ReceivingScanBody):
    """三要素校验(订单+围栏+时间窗
    ——公开收件人动作; 只校验留痕,
    不改订单状态——签收永远显式)"""
    from services.qr70_receiving_service import (
        Qr70ReceivingService,
    )
    try:
        return await Qr70ReceivingService().scan(
            body.code, body.memberId,
            body.city, body.lng, body.lat,
            body.deviceId)
    except Exception as e:
        raise _map(e) from e


@router.post("/receiving/confirm")
async def receiving_confirm(
        body: ReceivingConfirmBody):
    """显式签收(核销 once 码+订单域
    confirm 调用——公开用户主动;
    异常扫码须 escalatedAck)"""
    from services.qr70_receiving_service import (
        Qr70ReceivingService,
    )
    try:
        return await Qr70ReceivingService()\
            .confirm(body.code, body.operatorId,
                     body.escalatedAck)
    except Exception as e:
        raise _map(e) from e


@router.get("/receiving/evidence/{order_id}")
async def receiving_evidence(
        order_id: str,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """订单签收证据链(扫码判定+签收
    回执——观测面)"""
    _require_admin(x_role)
    from services.qr70_receiving_service import (
        Qr70ReceivingService,
    )
    return await Qr70ReceivingService()\
        .evidence_view(order_id)


# ============================================================
# P4 发货码(版式生成+交接绑定+错发观测)
# ============================================================

class ShippingIssueBody(BaseModel):
    waveNo: str = Field(description="出库波次号(warehouse 既有波次域)")
    orderId: str = Field(description="关联订单号")
    skuNames: list[str] = Field(description="SKU 名称列表(易混检测)")
    destinationProvince: str = Field(default="",
                                     description="目的省份(远途标记)")
    carrier: str = Field(default="",
                         description="承运商(SF/EMS/POST...)")
    operatorId: int = Field(default=0,
                            description="操作者 ID(仓管)")


class ShippingScanBody(BaseModel):
    code: str = Field(description="交接码(ZXBJ-QR70 签名链)")
    operatorId: int = Field(description="操作者 ID")
    waybillNo: str = Field(default="",
                           description="运单号(物流节点绑定)")


class ShippingConfirmBody(BaseModel):
    code: str = Field(description="交接码")
    operatorId: int = Field(default=0,
                            description="确认操作者 ID")


class ConfusionReportBody(BaseModel):
    skuA: str = Field(description="混淆 SKU A")
    skuB: str = Field(description="混淆 SKU B")
    scene: str = Field(description="场景(wave_pick/pack_scan/handover)")
    operatorId: int = Field(default=0,
                            description="操作者 ID")


@router.get("/shipping/dict")
async def shipping_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """发货码字典(版式规则+色带域公示
    ——观测面)"""
    _require_admin(x_role)
    from services.qr70_shipping_service import (
        Qr70ShippingService,
    )
    return Qr70ShippingService().dict_view()


@router.post("/shipping/issue")
async def shipping_issue(
        body: ShippingIssueBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """交接码签发(版式规则表确定性+
    易混色带——决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_shipping_service import (
        Qr70ShippingService,
    )
    try:
        return await Qr70ShippingService().issue(
            body.waveNo, body.orderId,
            body.skuNames,
            body.destinationProvince,
            body.carrier, body.operatorId)
    except Exception as e:
        raise _map(e) from e


@router.post("/shipping/scan")
async def shipping_scan(body: ShippingScanBody):
    """交接扫码(绑定物流节点——运单号
    留痕; 出库核销走 warehouse 既有
    波次——公开仓管动作)"""
    from services.qr70_shipping_service import (
        Qr70ShippingService,
    )
    try:
        return await Qr70ShippingService().scan(
            body.code, body.operatorId,
            body.waybillNo)
    except Exception as e:
        raise _map(e) from e


@router.post("/shipping/confirm")
async def shipping_confirm(
        body: ShippingConfirmBody):
    """交接确认(once 核销+操作者显式
    ——公开; 不执行库存动作)"""
    from services.qr70_shipping_service import (
        Qr70ShippingService,
    )
    try:
        return await Qr70ShippingService()\
            .confirm(body.code, body.operatorId)
    except Exception as e:
        raise _map(e) from e


@router.post("/shipping/confusion/report")
async def shipping_confusion_report(
        body: ConfusionReportBody):
    """错发案例上报(快环纯统计
    ——不受开关影响; P7 慢环区分
    标识建议书消费底座)"""
    from services.qr70_shipping_service import (
        Qr70ShippingService,
    )
    try:
        return await Qr70ShippingService()\
            .confusion_report(
                body.skuA, body.skuB,
                body.scene, body.operatorId)
    except Exception as e:
        raise _map(e) from e


@router.get("/shipping/confusion/stats")
async def shipping_confusion_stats(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """错发统计基线(按混淆对×场景
    ——观测面)"""
    _require_admin(x_role)
    from services.qr70_shipping_service import (
        Qr70ShippingService,
    )
    return await Qr70ShippingService()\
        .confusion_stats()


# ============================================================
# P5 管理码(角色情境办事台)
# ============================================================

class ManageIssueBody(BaseModel):
    memberId: int = Field(description="会员 ID(归属)")
    station: str = Field(default="",
                         description="工位/站点(面板情境)")
    batchNo: str = Field(default="",
                         description="批次上下文")


class ManageOpenBody(BaseModel):
    code: str = Field(description="办事台码(ZXBJ-QR70 签名链)")
    memberId: int = Field(description="扫码会员 ID(实时权限过滤)")


class ManageOpBody(BaseModel):
    code: str = Field(description="办事台码")
    memberId: int = Field(description="操作会员 ID")
    nodeCode: str = Field(description="权限点(如 storage.operate)")
    ack: bool = Field(default=False,
                     description="显式确认(approve/manage 级必需)")


@router.get("/manage/dict")
async def manage_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """管理码字典(面板域+确认级公示
    ——观测面)"""
    _require_admin(x_role)
    from services.qr70_manage_service import (
        Qr70ManageService,
    )
    return Qr70ManageService().dict_view()


@router.post("/manage/issue")
async def manage_issue(
        body: ManageIssueBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """办事台码签发(session 策略
    ——决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_manage_service import (
        Qr70ManageService,
    )
    try:
        return await Qr70ManageService().issue(
            body.memberId, body.station,
            body.batchNo)
    except Exception as e:
        raise _map(e) from e


@router.post("/manage/open")
async def manage_open(body: ManageOpenBody):
    """扫码开办事台(session 码——
    公开; 面板=33号实时权限×频次排序,
    码只是入口)"""
    from services.qr70_manage_service import (
        Qr70ManageService,
    )
    try:
        return await Qr70ManageService().open(
            body.code, body.memberId)
    except Exception as e:
        raise _map(e) from e


@router.post("/manage/op")
async def manage_op(body: ManageOpBody):
    """办事操作(公开——权限校验服务端
    强制每次必查; approve/manage 级
    须 ack 显式确认)"""
    from services.qr70_manage_service import (
        Qr70ManageService,
    )
    try:
        return await Qr70ManageService()\
            .op_execute(body.code,
                        body.memberId,
                        body.nodeCode, body.ack)
    except Exception as e:
        if isinstance(e, PermissionError):
            raise HTTPException(
                status_code=403,
                detail=str(e)) from e
        raise _map(e) from e


@router.get("/manage/rank")
async def manage_rank(
        x_role: str | None = Header(default=None,
                                    alias="X-Role"),
        memberId: int = 0):
    """操作频次统计(按会员×权限点
    ——观测面; 布局建议书在 P7 慢环)"""
    _require_admin(x_role)
    from services.qr70_manage_service import (
        Qr70ManageService,
    )
    return await Qr70ManageService().rank_view(
        member_id=memberId)


# ============================================================
# P6 收款码(场景化版式+收款核销+对账摘要)
# ============================================================

class CollectIssueBody(BaseModel):
    merchantId: int = Field(gt=0,
                            description="商户 ID(收款码永远商户主动)")
    amount: float = Field(gt=0,
                          description="收款金额(元)")
    scene: str = Field(default="storefront",
                       description="场景(storefront/street)")
    sceneNote: str = Field(default="",
                           description="场景备注(桌号/商品名)")
    operatorId: int = Field(default=0,
                            description="操作者 ID(审计)")


class CollectRedeemBody(BaseModel):
    code: str = Field(description="收款码(ZXBJ-QR70 签名链)")
    operatorId: int = Field(default=0,
                           description="核销操作者 ID")


@router.get("/collect/dict")
async def collect_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """收款码字典(版式规则+大额阈值
    公示——观测面)"""
    _require_admin(x_role)
    from services.qr70_collect_service import (
        Qr70CollectService,
    )
    return Qr70CollectService().dict_view()


@router.post("/collect/issue")
async def collect_issue(
        body: CollectIssueBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """商户收款码生成(场景化版式+
    大额挑战标记——决策面 off 409;
    商户主动铁律)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_collect_service import (
        Qr70CollectService,
    )
    try:
        return await Qr70CollectService().issue(
            body.merchantId, body.amount,
            body.scene, body.sceneNote,
            body.operatorId)
    except Exception as e:
        raise _map(e) from e


@router.post("/collect/redeem")
async def collect_redeem(
        body: CollectRedeemBody):
    """收款确认核销(公开——once 核销;
    资金路由由 69号 P1 唯一负责,
    70号零重复)"""
    from services.qr70_collect_service import (
        Qr70CollectService,
    )
    try:
        return await Qr70CollectService().redeem(
            body.code, body.operatorId)
    except Exception as e:
        raise _map(e) from e


@router.get("/collect/merchant/{merchant_id}/summary")
async def collect_merchant_summary(
        merchant_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """商户对账摘要(核销数/金额合计/
    挑战数——观测面只读, 68号结算
    零改动)"""
    _require_admin(x_role)
    from services.qr70_collect_service import (
        Qr70CollectService,
    )
    try:
        return await Qr70CollectService()\
            .merchant_summary(merchant_id)
    except Exception as e:
        raise _map(e) from e


@router.get("/collect/pattern/stats")
async def collect_pattern_stats(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """异常交易模式统计(按版式×挑战
    ——观测面; P7 慢环风控强度建议书
    消费底座)"""
    _require_admin(x_role)
    from services.qr70_collect_service import (
        Qr70CollectService,
    )
    return await Qr70CollectService()\
        .pattern_stats()


# ============================================================
# P7 愉悦度引擎(四层自适应学习)
# ============================================================

class RenderParamsBody(BaseModel):
    memberId: int = Field(default=0,
                          description="会员 ID(归因)")
    kind: str = Field(default="",
                      description="码类(六类域)")
    elderly: bool = Field(default=False,
                          description="老年群体(字号 1.5×)")
    lowLight: bool = Field(default=False,
                           description="弱光环境(对比度增强)")
    consecutiveFailures: int = Field(default=0, ge=0,
                                     description="连续扫码失败数(≥3 切备用模式)")


class HypothesisBody(BaseModel):
    paramId: str = Field(description="可进化参数(render.* 白名单)")
    fromValue: str = Field(description="当前值")
    toValue: str = Field(description="建议值")
    reason: str = Field(description="痛点依据(观测数据引用)")
    riskLevel: str = Field(default="low",
                           description="风险级(low/high)")


class ParamVersionBody(BaseModel):
    paramId: str = Field(description="参数 ID(白名单)")
    value: str = Field(description="参数值")
    sourceHypothesisId: int = Field(default=0,
                                     description="来源假设 ID")


class KillBody(BaseModel):
    activate: bool = Field(description="激活/解除制动")


@router.get("/joy/engine/dict")
async def joy_engine_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """引擎字典(四层结构+表现层白名单
    ——观测面)"""
    _require_admin(x_role)
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    return Qr70JoyService().engine_dict()


@router.post("/joy/render/params")
async def joy_render_params(
        body: RenderParamsBody):
    """端侧表现层参数建议(公开快环
    ——白名单六参数纯建议下发;
    业务参数服务端唯一权威)"""
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    try:
        return await Qr70JoyService()\
            .render_params(
                body.memberId, body.kind,
                body.elderly, body.lowLight,
                body.consecutiveFailures)
    except Exception as e:
        raise _map(e) from e


@router.post("/joy/hypothesis/propose")
async def joy_hypothesis_propose(
        body: HypothesisBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """进化假设建议书发起(慢环——
    决策面 off 409; 愉悦度=奖励
    信号非决策主体)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    try:
        return await Qr70JoyService()\
            .propose_hypothesis(
                body.paramId,
                body.fromValue, body.toValue,
                body.reason, body.riskLevel)
    except Exception as e:
        raise _map(e) from e


@router.post("/joy/hypothesis/{hyp_id}/submit")
async def joy_hypothesis_submit(
        hyp_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """假设提交 46号审批总线(纯调用
    submit_change——46号零改动)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    try:
        return await Qr70JoyService()\
            .submit_to_governance(hyp_id)
    except Exception as e:
        raise _map(e) from e


@router.post("/joy/hypothesis/{hyp_id}/reject")
async def joy_hypothesis_reject(
        hyp_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """人工驳回留痕(46号审批拒绝后
    ——人工动作不受开关影响)"""
    _require_admin(x_role)
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    try:
        return await Qr70JoyService()\
            .mark_rejected(hyp_id)
    except Exception as e:
        raise _map(e) from e


@router.get("/joy/hypotheses")
async def joy_hypotheses(
        x_role: str | None = Header(default=None,
                                    alias="X-Role"),
        status: str = "", limit: int = 50):
    """假设建议书视图(观测面)"""
    _require_admin(x_role)
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    from repositories.qr70_repository \
        import Qr70Repository
    hyps = await Qr70Repository()\
        .list_hypotheses(
            status=status or None,
            limit=limit)
    return {
        "modelVersion":
            Qr70JoyService()
            .engine_dict()["modelVersion"],
        "count": len(hyps),
        "hypotheses": hyps,
    }


@router.post("/joy/params/version")
async def joy_params_create(
        body: ParamVersionBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """创建参数版本草案(46号审批通过
    后——决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    try:
        return await Qr70JoyService()\
            .create_param_version(
                body.paramId, body.value,
                body.sourceHypothesisId)
    except Exception as e:
        raise _map(e) from e


@router.post("/joy/params/{version}/publish")
async def joy_params_publish(
        version: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role"),
        shadowFirst: bool = False):
    """版本发布(draft→shadow/active
    ——决策面 off 409; active 互斥)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    try:
        return await Qr70JoyService()\
            .publish_version(
                version, shadowFirst)
    except Exception as e:
        raise _map(e) from e


@router.post("/joy/params/{version}/rollback")
async def joy_params_rollback(
        version: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """版本回滚(active→retired
    ——决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_mode()
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    try:
        return await Qr70JoyService()\
            .rollback_version(version)
    except Exception as e:
        raise _map(e) from e


@router.get("/joy/params")
async def joy_params_view(
        x_role: str | None = Header(default=None,
                                    alias="X-Role"),
        paramId: str = "",
        status: str = ""):
    """参数版本基线视图(观测面)"""
    _require_admin(x_role)
    from repositories.qr70_repository \
        import Qr70Repository
    versions = await Qr70Repository()\
        .list_param_versions(
            param_id=paramId or None,
            status=status or None)
    return {
        "count": len(versions),
        "versions": versions,
    }


@router.post("/joy/drift/detect")
async def joy_drift_detect():
    """分布漂移检测(元认知快环
    ——不受开关影响)"""
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    return await Qr70JoyService()\
        .drift_detect()


@router.post("/joy/kill")
async def joy_kill(
        body: KillBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """紧急制动(QR70_KILL——人工
    动作不受开关影响; 激活即全参数
    退役只读)"""
    _require_admin(x_role)
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    try:
        return await Qr70JoyService()\
            .kill_switch(body.activate)
    except Exception as e:
        raise _map(e) from e


@router.get("/joy/health")
async def joy_health(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """进化健康报告(元认知观测面
    ——各码类愉悦度趋势+治理状态)"""
    _require_admin(x_role)
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    return await Qr70JoyService()\
        .health_report()


@router.get("/joy/knowledge")
async def joy_knowledge(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """原子知识库视图(知识迁移层
    ——四原子×六类码复用映射)"""
    _require_admin(x_role)
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    return Qr70JoyService().knowledge_view()


def register_qr70_routes(app) -> None:
    """注册70号路由(main.py startup 调用)"""
    app.include_router(router)
