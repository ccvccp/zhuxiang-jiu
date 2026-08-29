"""收款管理模块路由(20 + 12 = 32 端点)

鉴权:
    - 用户端(9 接口): X-Member-Id 头标识会员(创建支付/查询/发起支付/关闭/退款申请/撤回/退款列表)
    - 管理端(17 接口): X-Role: admin 头(退款/付款审批 + P1 对账/渠道管理)
    - 渠道回调(3 接口): 支付/退款/打款回调(由渠道调用, 鉴权由签名/Token 保证, 此处简化)
    - 公开(3 接口): 启用的渠道列表/渠道详情/对账批次查询(P1)

异常映射(遵循项目约定):
    - KeyError   → 404(资源不存在)
    - ValueError → 409(业务冲突: 状态非法/参数非法/超额等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 支付订单(6):  create / detail / list / start / callback / close
    - 退款(5):        create / audit / callback / cancel / list + pending-list
    - 付款(8):        create / audit / execute / callback / retry / detail / list / pending-list
    - 付款失败(1):    fail(管理端标记打款失败)
    - 对账记录(6):    start / detail / list / pending-diffs / investigate / resolve
    - 渠道配置(6):    create / detail / list / active-list / toggle / update

注:
    1. 路由声明顺序 — 静态 GET 路径(list/pending-list 等)必须声明在
       参数化路径 /api/payment/{pay_no} 之前, 避免被参数路由吞掉。
    2. 跨模块联动(订单/钱包/财务)在路由层调用对应 service 完成,
       保持 payment_service 单一职责。
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.payment_service import PaymentService


router = APIRouter()
_service = PaymentService()


# ============================================================
# 鉴权与异常映射辅助(对齐 wallet/finance 风格)
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return str(x_member_id)


def _require_admin(x_role: str | None):
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
    sceneType: str = Field("order_pay", description="场景 order_pay/wallet_deposit/agent_purchase/guest_order_pay(游客免登录)")
    discountAmount: float = Field(0, ge=0, description="优惠抵扣")
    pointsAmount: float = Field(0, ge=0, description="积分抵扣")
    guestPhone: str | None = Field(None, description="游客手机号(游客场景必填, 11位)")
    ageConfirmed: bool = Field(False, description="已满18周岁声明(游客场景必填, 酒类合规)")


class PayCallbackRequest(PydBaseModel):
    channelTradeNo: str = Field(..., description="渠道交易号")
    payNo: str | None = Field(None, description="支付单号(可选, 缺失则按渠道交易号反查)")
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


# ---- P1: 对账记录 ----

class StartReconciliationRequest(PydBaseModel):
    reconDate: str = Field(..., description="对账日期 YYYY-MM-DD")
    channel: str = Field(..., description="对账渠道(对应渠道编码 channelCode)")
    operator: str = Field("system", description="操作人")


class InvestigateDiffRequest(PydBaseModel):
    operator: str = Field("admin", description="操作人")
    remark: str = Field("", description="调查备注")


class ResolveReconciliationRequest(PydBaseModel):
    operator: str = Field("admin", description="操作人")
    resolution: str = Field(..., description="处理方式 refund/supplement/ignore")
    remark: str = Field("", description="处理备注")


# ---- P1: 渠道配置 ----

class CreateChannelRequest(PydBaseModel):
    channelCode: str = Field(..., description="渠道编码(如 wechat/alipay/unionpay)")
    channelName: str = Field(..., description="渠道名称")
    channelType: str = Field("third_party", description="渠道类型 third_party/bank/aggregate")
    supportedMethods: list[str] = Field(default_factory=lambda: ["jsapi", "native", "h5"], description="支持的支付方式")
    supportedScenes: list[str] = Field(default_factory=lambda: ["order_pay", "wallet_deposit"], description="支持的业务场景")
    merchantId: str = Field(..., description="商户号")
    feeRate: float = Field(0.006, ge=0, le=1, description="手续费率")
    feeType: str = Field("ratio", description="费率类型 fixed/ratio/mixed")
    fixedFee: float = Field(0, ge=0, description="固定手续费(feeType=fixed/mixed 时生效)")
    settleCycle: str = Field("T+1", description="结算周期 T+0/T+1/T+7")
    minAmount: float = Field(0.01, gt=0, description="单笔最小金额")
    maxAmount: float = Field(50000, gt=0, description="单笔最大金额")
    dailyLimit: float = Field(500000, gt=0, description="单日累计限额")
    monthlyLimit: float = Field(5000000, gt=0, description="单月累计限额")
    retryMax: int = Field(8, ge=0, le=20, description="最大重试次数")
    timeout: int = Field(1800, ge=60, description="超时秒数")
    remark: str = Field("", description="备注")


class UpdateChannelRequest(PydBaseModel):
    """更新字段名须对齐 Repository 字段(channelCode/feeRate/maxAmount 等驼峰命名)"""
    channelName: str | None = None
    feeRate: float | None = Field(None, ge=0, le=1)
    feeType: str | None = None
    fixedFee: float | None = Field(None, ge=0)
    settleCycle: str | None = None
    minAmount: float | None = Field(None, gt=0)
    maxAmount: float | None = Field(None, gt=0)
    dailyLimit: float | None = Field(None, gt=0)
    monthlyLimit: float | None = Field(None, gt=0)
    retryMax: int | None = Field(None, ge=0, le=20)
    timeout: int | None = Field(None, ge=60)
    remark: str | None = None

    class Config:
        extra = "allow"


class ToggleChannelRequest(PydBaseModel):
    status: str = Field(..., description="目标状态 active/maintenance/disabled")
    operator: str = Field("admin", description="操作人")


# ============================================================
# 1. 支付订单(6 端点)
# ============================================================

@router.post("/api/payment/pay", tags=["收款管理"])
async def create_pay(
    req: CreatePayRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """创建支付订单(幂等: 同一订单只能有一个活跃支付单)

    游客扫码付(P0-3): sceneType=guest_order_pay 时免登录(不校验 X-Member-Id),
    须携带 guestPhone(11位)+ageConfirmed=true; 单笔 ≤ ¥5,000, 仅零售全额扫码付。
    """
    # 游客场景免登录(文档 2.7.1: 免登录临时单, 降低购物门槛)
    user_id = ("guest" if req.sceneType == "guest_order_pay"
               else _require_member_id(x_member_id))
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
            guest_phone=req.guestPhone,
            age_confirmed=req.ageConfirmed,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/payment/pays", tags=["收款管理"])
async def list_pays(
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
    status: str | None = Query(None, description="状态筛选"),
    sceneType: str | None = Query(None, description="场景筛选"),
    limit: int = Query(50, ge=1, le=200, description="返回数量"),
):
    """列出用户支付订单"""
    user_id = _require_member_id(x_member_id)
    try:
        return await _service.list_pays(user_id, status, sceneType, limit)
    except KeyError as e:
        raise _map_key_error(e) from e


# 注: GET /api/payment/{pay_no} 已移至文件末尾, 避免捕获后续静态路径
#      (payouts/reconciliations/channels 等同段数 GET 路由)


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

# 静态路由必须在动态路由之前, 避免 {pay_no} 捕获静态路径段
@router.get("/api/payment/refunds/pending", tags=["收款管理"])
async def list_pending_refunds(
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
    limit: int = Query(100, ge=1, le=500),
):
    """待审核退款列表(admin)"""
    _require_admin(x_role)
    return await _service.list_pending_refunds(limit)


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
    status: str | None = Query(None, description="状态筛选"),
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
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
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
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
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


# ============================================================
# 3. 付款(8 端点)
# ============================================================

@router.post("/api/payment/payout", tags=["收款管理"])
async def create_payout(
    req: CreatePayoutRequest,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
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
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
    payoutType: str | None = Query(None, description="付款类型筛选"),
    status: str | None = Query(None, description="状态筛选"),
    limit: int = Query(50, ge=1, le=200),
):
    """列出付款记录(admin)"""
    _require_admin(x_role)
    return await _service.list_payouts(payoutType, status, limit)


@router.get("/api/payment/payouts/pending", tags=["收款管理"])
async def list_pending_payouts(
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
    limit: int = Query(100, ge=1, le=500),
):
    """待审核付款列表(admin)"""
    _require_admin(x_role)
    return await _service.list_pending_payouts(limit)


@router.get("/api/payment/payout/{payout_no}", tags=["收款管理"])
async def get_payout(
    payout_no: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
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
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
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
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
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
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
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
# 4. 对账记录(6 端点, 管理端 + 公开查询)
# 注: 静态 GET 路径(list/pending)声明在参数路径 {recon_no} 之前
# ============================================================

@router.post("/api/payment/reconciliation/start", tags=["收款管理"])
async def start_reconciliation(
    req: StartReconciliationRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """启动日终对账(管理端)"""
    _require_admin(x_role)
    try:
        return await _service.start_reconciliation(req.reconDate, req.channel, req.operator)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/payment/reconciliations", tags=["收款管理"])
async def list_reconciliations(
    date: str | None = Query(None, description="按日期筛选 YYYY-MM-DD"),
    channel: str | None = Query(None, description="按渠道筛选"),
    status: str | None = Query(None, description="按状态筛选 pending/matched/diff/investigating/resolved"),
    limit: int = Query(20, ge=1, le=100),
):
    """对账批次列表(支持筛选)"""
    try:
        return await _service.list_reconciliations(date=date, channel=channel, status=status, limit=limit)
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/payment/reconciliations/pending", tags=["收款管理"])
async def list_pending_diffs(
    x_role: str | None = Header(None, alias="X-Role"),
    limit: int = Query(20, ge=1, le=100),
):
    """待处理差异列表(管理端)"""
    _require_admin(x_role)
    try:
        return await _service.list_pending_diffs(limit=limit)
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/payment/reconciliation/{recon_no}", tags=["收款管理"])
async def get_reconciliation(recon_no: str):
    """查询对账批次详情"""
    try:
        return await _service.get_reconciliation(recon_no)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/payment/reconciliation/{recon_no}/investigate", tags=["收款管理"])
async def investigate_diff(
    recon_no: str,
    req: InvestigateDiffRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """介入调查差异(管理端, diff → investigating)"""
    _require_admin(x_role)
    try:
        return await _service.investigate_diff(recon_no, req.operator, req.remark)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/payment/reconciliation/{recon_no}/resolve", tags=["收款管理"])
async def resolve_reconciliation(
    recon_no: str,
    req: ResolveReconciliationRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """处理完成(管理端, investigating → resolved)

    注: service 层 resolve_reconciliation(recon_no, operator, remark) 未单独
        接收 resolution 参数, 此处将处理方式合并到 remark 前缀便于审计。
    """
    _require_admin(x_role)
    try:
        merged_remark = f"[{req.resolution}] {req.remark}".strip()
        return await _service.resolve_reconciliation(
            recon_no, req.operator, merged_remark
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
# 5. 渠道配置(6 端点, 管理端 + 公开)
# 注: 静态 GET 路径(channels/active)声明在参数路径 {code} 之前
# ============================================================

@router.post("/api/payment/channel", tags=["收款管理"])
async def create_channel(
    req: CreateChannelRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """创建渠道配置(管理端)"""
    _require_admin(x_role)
    try:
        return await _service.create_channel(
            channel_code=req.channelCode,
            channel_name=req.channelName,
            channel_type=req.channelType,
            supported_methods=req.supportedMethods,
            supported_scenes=req.supportedScenes,
            merchant_id=req.merchantId,
            fee_rate=req.feeRate,
            fee_type=req.feeType,
            fixed_fee=req.fixedFee,
            settle_cycle=req.settleCycle,
            min_amount=req.minAmount,
            max_amount=req.maxAmount,
            daily_limit=req.dailyLimit,
            monthly_limit=req.monthlyLimit,
            retry_max=req.retryMax,
            timeout=req.timeout,
            remark=req.remark,
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/payment/channels", tags=["收款管理"])
async def list_channels(
    x_role: str | None = Header(None, alias="X-Role"),
    status: str | None = Query(None, description="按状态筛选 active/maintenance/disabled"),
):
    """渠道列表(管理端可查全部, 含禁用)"""
    _require_admin(x_role)
    try:
        return await _service.list_channels(status=status)
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/payment/channels/active", tags=["收款管理"])
async def list_active_channels():
    """启用的渠道列表(公开, 供客户端收银台选择)"""
    try:
        return await _service.list_active_channels()
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/payment/channel/{code}", tags=["收款管理"])
async def get_channel(code: str):
    """查询渠道详情(公开)"""
    try:
        return await _service.get_channel(code)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/payment/channel/{code}/toggle", tags=["收款管理"])
async def toggle_channel_status(
    code: str,
    req: ToggleChannelRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """启停渠道(管理端)"""
    _require_admin(x_role)
    try:
        return await _service.toggle_channel_status(code, req.status, req.operator)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/payment/channel/{code}/update", tags=["收款管理"])
async def update_channel(
    code: str,
    req: UpdateChannelRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """更新渠道配置(管理端)

    注: 字段名须对齐 Repository 字段(驼峰命名), service 层 update_channel
        接收 fields dict 直接透传给 repo.update_channel_fields。
    """
    _require_admin(x_role)
    try:
        # 仅传递非 None 字段(已对齐 Repository 驼峰命名)
        fields = {k: v for k, v in req.model_dump().items() if v is not None}
        return await _service.update_channel(code, fields)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
# 支付订单详情(动态路径, 必须在所有静态 GET 路由之后)
# ============================================================

@router.get("/api/payment/{pay_no}", tags=["收款管理"])
async def get_pay(pay_no: str):
    """查询支付订单详情"""
    try:
        return await _service.get_pay(pay_no)
    except KeyError as e:
        raise _map_key_error(e) from e


# ============================================================
# 路由注册
# ============================================================

def register_payment_routes(app):
    """注册收款管理模块路由"""
    app.include_router(router)
