"""收款管理模块路由(20 端点)

鉴权:
    - 用户端(9 接口): X-Member-Id 头标识会员(创建支付/查询/发起支付/关闭/退款申请/撤回/退款列表)
    - 管理端(8 接口): X-Role: admin 头(退款审批/待审核列表/付款审批/执行打款/打款回调/付款查询)
    - 渠道回调(3 接口): 支付/退款/打款回调(由渠道调用, 鉴权由签名/Token 保证, 此处简化)

异常映射(遵循项目约定):
    - KeyError   → 404(资源不存在)
    - ValueError → 409(业务冲突: 状态非法/参数非法/超额等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 支付订单(6):  create / detail / list / start / callback / close
    - 退款(5):        create / audit / callback / cancel / list + pending-list
    - 付款(8):        create / audit / execute / callback / retry / detail / list / pending-list
    - 付款失败(1):    fail(管理端标记打款失败)

注:
    1. 路由声明顺序 — 静态 GET 路径(list/pending-list 等)必须声明在
       参数化路径 /api/payment/{pay_no} 之前, 避免被参数路由吞掉。
    2. 跨模块联动(订单/钱包/财务)在路由层调用对应 service 完成,
       保持 payment_service 单一职责。
"""

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.payment_service import PaymentService


router = APIRouter()
_service = PaymentService()


# ============================================================
# 鉴权与异常映射辅助(对齐 wallet/finance 风格)
# ============================================================

def _require_member_id(x_member_id: Optional[str]) -> str:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return str(x_member_id)


def _require_admin(x_role: Optional[str]):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _map_key_error(exc: KeyError) -> HTTPException:
    """KeyError → 404"""
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    """ValueError → 409"""
    return HTTPException(status_code=409, detail=str(exc))


def _handle(exc):
    """统一异常映射"""
    if isinstance(exc, KeyError):
        raise _map_key_error(exc) from exc
    if isinstance(exc, ValueError):
        raise _map_value_error(exc) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


# ============================================================
# 请求模型
# ============================================================

class CreatePayRequest(PydBaseModel):
    orderId: str = Field(..., description="订单号")
    orderType: str = Field("retail", description="订单类型 retail/groupbuy/custom/wallet_deposit")
    totalAmount: float = Field(..., gt=0, description="订单总金额")
    payChannel: str = Field("alipay", description="支付渠道 wechat/alipay/unionpay/bank/aggregate")
    payMethod: str = Field("jsapi", description="支付方式 native/jsapi/h5/page/transfer")
    sceneType: str = Field("order_pay", description="场景 order_pay/wallet_deposit/agent_purchase")
    discountAmount: float = Field(0, ge=0, description="优惠抵扣")
    pointsAmount: float = Field(0, ge=0, description="积分抵扣")


class PayCallbackRequest(PydBaseModel):
    channelTradeNo: str = Field(..., description="渠道交易号")
    payNo: Optional[str] = Field(None, description="支付单号(可选, 缺失则按渠道交易号反查)")
    callbackContent: dict = Field(default_factory=dict, description="回调原始数据")


class ClosePayRequest(PydBaseModel):
    reason: str = Field("USER_CANCEL", description="关闭原因 USER_CANCEL/TIMEOUT")


class CreateRefundRequest(PydBaseModel):
    refundAmount: float = Field(0, ge=0, description="退款金额(partial 须>0; full 可传0自动计算)")
    refundReason: str = Field(..., description="退款原因")
    refundType: str = Field("partial", description="退款类型 full/partial")


class RefundCallbackRequest(PydBaseModel):
    channelRefundNo: str = Field(..., description="渠道退款号")
    refundNo: str = Field(..., description="退款单号(渠道回调须携带)")
    callbackContent: dict = Field(default_factory=dict, description="回调原始数据")


class AuditRefundRequest(PydBaseModel):
    decision: str = Field(..., description="审核决定 approved/rejected")
    auditor: str = Field("admin", description="审核人")
    auditRemark: str = Field("", description="审核备注")


class CreatePayoutRequest(PydBaseModel):
    payoutType: str = Field(..., description="付款类型 supplier/logistics/recycle/commission/wallet_withdraw/salary")
    sourceId: str = Field(..., description="来源单据号")
    payeeName: str = Field(..., description="收款人名称")
    payeeAccount: str = Field(..., description="收款账号")
    payeeBank: str = Field("", description="收款银行")
    amount: float = Field(..., gt=0, description="付款金额")
    payChannel: str = Field("bank_transfer", description="付款渠道 bank_transfer/alipay_transfer/wechat_transfer")
    payeePhone: str = Field("", description="收款人手机号")
    taxAmount: float = Field(0, ge=0, description="代扣税费")


class AuditPayoutRequest(PydBaseModel):
    decision: str = Field(..., description="审核决定 approved/rejected")
    auditor: str = Field("admin", description="审核人")
    auditRemark: str = Field("", description="审核备注")


class PayoutCallbackRequest(PydBaseModel):
    channelPayoutNo: str = Field(..., description="渠道付款流水号")
    success: bool = Field(True, description="是否成功")
    failReason: str = Field("", description="失败原因(success=false 时填)")
    callbackContent: dict = Field(default_factory=dict, description="回调原始数据")


# ============================================================
# 1. 支付订单(6 端点)
# ============================================================

@router.post("/api/payment/pay", tags=["收款管理"])
async def create_pay(
    req: CreatePayRequest,
    x_member_id: Annotated[Optional[str], Header(alias="X-Member-Id")] = None,
):
    """创建支付订单(幂等: 同一订单只能有一个活跃支付单)"""
    user_id = _require_member_id(x_member_id)
    try:
        return await _service.create_pay(
            user_id=user_id,
            order_id=req.orderId,
            order_type=req.orderType,
            total_amount=req.totalAmount,
            pay_channel=req.payChannel,
            pay_method=req.payMethod,
            scene_type=req.sceneType,
            discount_amount=req.discountAmount,
            points_amount=req.pointsAmount,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/payment/pays", tags=["收款管理"])
async def list_pays(
    x_member_id: Annotated[Optional[str], Header(alias="X-Member-Id")] = None,
    status: Optional[str] = Query(None, description="状态筛选"),
    sceneType: Optional[str] = Query(None, description="场景筛选"),
    limit: int = Query(50, ge=1, le=200, description="返回数量"),
):
    """列出用户支付订单"""
    user_id = _require_member_id(x_member_id)
    try:
        return await _service.list_pays(user_id, status, sceneType, limit)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.get("/api/payment/{pay_no}", tags=["收款管理"])
async def get_pay(pay_no: str):
    """查询支付订单详情"""
    try:
        return await _service.get_pay(pay_no)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/payment/{pay_no}/start", tags=["收款管理"])
async def start_pay(pay_no: str):
    """发起渠道支付(待支付 → 支付中)"""
    try:
        return await _service.start_pay(pay_no)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/payment/{pay_no}/close", tags=["收款管理"])
async def close_pay(
    pay_no: str,
    req: ClosePayRequest,
):
    """关闭支付单(待支付/支付中 → 已关闭)"""
    try:
        return await _service.close_pay(pay_no, req.reason)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/payment/callback/pay", tags=["收款管理"])
async def pay_callback(req: PayCallbackRequest):
    """支付回调(渠道推送, 幂等: 重复回调返回成功)

    实际场景: 由支付渠道(微信/支付宝)调用, 通过签名/Token 鉴权
    """
    try:
        return await _service.pay_callback(
            req.channelTradeNo, req.callbackContent, req.payNo,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
# 2. 退款(5 端点 + 1 pending 列表 = 6)
# ============================================================

@router.post("/api/payment/{pay_no}/refund", tags=["收款管理"])
async def create_refund(
    pay_no: str,
    req: CreateRefundRequest,
):
    """创建退款申请(幂等: 累计退款不超过原支付金额)"""
    try:
        return await _service.create_refund(
            pay_no, req.refundAmount, req.refundReason, req.refundType,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/payment/{pay_no}/refunds", tags=["收款管理"])
async def list_refunds(
    pay_no: str,
    status: Optional[str] = Query(None, description="状态筛选"),
    limit: int = Query(50, ge=1, le=200),
):
    """列出支付单关联的退款记录"""
    try:
        return await _service.list_refunds(pay_no, status, limit)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/payment/refund/{refund_no}/audit", tags=["收款管理"])
async def audit_refund(
    refund_no: str,
    req: AuditRefundRequest,
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
):
    """审核退款(admin; 待审核 → 审核通过/拒绝)"""
    _require_admin(x_role)
    try:
        return await _service.audit_refund(refund_no, req.decision,
                                             req.auditor, req.auditRemark)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/payment/refund/{refund_no}/cancel", tags=["收款管理"])
async def cancel_refund(
    refund_no: str,
    x_member_id: Annotated[Optional[str], Header(alias="X-Member-Id")] = None,
):
    """撤回退款申请(用户; 待审核 → 已撤回)"""
    _require_member_id(x_member_id)
    try:
        return await _service.cancel_refund(refund_no)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/payment/callback/refund", tags=["收款管理"])
async def refund_callback(req: RefundCallbackRequest):
    """退款回调(渠道推送, 幂等)"""
    try:
        return await _service.refund_callback(
            req.channelRefundNo, req.callbackContent, req.refundNo,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/payment/refunds/pending", tags=["收款管理"])
async def list_pending_refunds(
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
    limit: int = Query(100, ge=1, le=500),
):
    """待审核退款列表(admin)"""
    _require_admin(x_role)
    return await _service.list_pending_refunds(limit)


# ============================================================
# 3. 付款(8 端点)
# ============================================================

@router.post("/api/payment/payout", tags=["收款管理"])
async def create_payout(
    req: CreatePayoutRequest,
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
):
    """创建付款单(admin; 幂等: 同来源只能创建一个付款单)"""
    _require_admin(x_role)
    try:
        return await _service.create_payout(
            payout_type=req.payoutType,
            source_id=req.sourceId,
            payee_name=req.payeeName,
            payee_account=req.payeeAccount,
            payee_bank=req.payeeBank,
            amount=req.amount,
            pay_channel=req.payChannel,
            payee_phone=req.payeePhone,
            tax_amount=req.taxAmount,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/payment/payouts", tags=["收款管理"])
async def list_payouts(
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
    payoutType: Optional[str] = Query(None, description="付款类型筛选"),
    status: Optional[str] = Query(None, description="状态筛选"),
    limit: int = Query(50, ge=1, le=200),
):
    """列出付款记录(admin)"""
    _require_admin(x_role)
    return await _service.list_payouts(payoutType, status, limit)


@router.get("/api/payment/payouts/pending", tags=["收款管理"])
async def list_pending_payouts(
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
    limit: int = Query(100, ge=1, le=500),
):
    """待审核付款列表(admin)"""
    _require_admin(x_role)
    return await _service.list_pending_payouts(limit)


@router.get("/api/payment/payout/{payout_no}", tags=["收款管理"])
async def get_payout(
    payout_no: str,
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
):
    """查询付款详情(admin)"""
    _require_admin(x_role)
    try:
        return await _service.get_payout(payout_no)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/payment/payout/{payout_no}/audit", tags=["收款管理"])
async def audit_payout(
    payout_no: str,
    req: AuditPayoutRequest,
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
):
    """审核付款(admin; 待审核 → 审核通过/拒绝)"""
    _require_admin(x_role)
    try:
        return await _service.audit_payout(payout_no, req.decision,
                                             req.auditor, req.auditRemark)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/payment/payout/{payout_no}/execute", tags=["收款管理"])
async def execute_payout(
    payout_no: str,
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
):
    """执行打款(admin; 审核通过 → 打款中)"""
    _require_admin(x_role)
    try:
        return await _service.execute_payout(payout_no)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/payment/payout/{payout_no}/retry", tags=["收款管理"])
async def retry_payout(
    payout_no: str,
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
):
    """重试打款(admin; 失败 → 打款中)"""
    _require_admin(x_role)
    try:
        return await _service.retry_payout(payout_no)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/payment/callback/payout", tags=["收款管理"])
async def payout_callback(
    payout_no: str,
    req: PayoutCallbackRequest,
):
    """打款回调(渠道推送, 幂等)

    实际场景: 由银行/渠道 API 调用, 通过签名/Token 鉴权
    """
    try:
        return await _service.payout_callback(
            payout_no, req.channelPayoutNo, req.callbackContent,
            req.success, req.failReason,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
# 路由注册
# ============================================================

def register_payment_routes(app):
    """注册收款管理模块路由"""
    app.include_router(router)
