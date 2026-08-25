"""财务管理路由(22 端点)

鉴权:
    - 管理端(21 接口): X-Role: admin 头
    - 用户端(1 接口):  发票开具使用 X-Member-Id(普通会员可开具本人订单发票)

异常映射(遵循项目约定):
    - KeyError → 404(资源不存在)
    - ValueError → 409(状态/业务冲突)
    - 权限校验 → 401/403

端点分布:
    - 财务凭证: 6 个(list/detail/order-auto/refund-auto/audit/closing)
    - 发票管理: 4 个(list/detail/issue/red)
    - 税务管理: 4 个(list/detail/calc/declare)
    - 资金对账: 3 个(list/daily/resolve)
    - 付款管理: 3 个(list/apply/approve)
    - 财务报表: 2 个(profit/management)
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query

from services.finance_service import FinanceService


router = APIRouter()
_service = FinanceService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_admin(x_role: str):
    """校验管理员权限"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _require_member(x_member_id: str) -> int:
    """校验登录态, 返回 member_id"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 缺少 X-Member-Id 头")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="X-Member-Id 须为数字") from None


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
    """统一异常映射(对齐 order_routes 风格)"""
    if isinstance(exc, KeyError):
        raise _map_key_error(exc) from exc
    if isinstance(exc, ValueError):
        raise _map_value_error(exc) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


# ============================================================
# 1. 财务凭证(6 个)
# ============================================================

@router.get("/api/finance/voucher/list", tags=["财务管理"])
async def list_vouchers(
    period: str | None = Query(default=None, description="账期 YYYYMM 或 YYYY-MM"),
    type: str | None = Query(default=None, description="凭证类型 income/refund"),
    source: str | None = Query(default=None, description="来源 order/refund"),
    status: str | None = Query(default=None, description="状态 draft/audited/posted"),
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """凭证列表(可按 period/type/source/status 筛选)"""
    _require_admin(x_role)
    try:
        return await _service.list_vouchers(
            period=period, voucher_type=type, source=source, status=status,
        )
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/finance/voucher/{voucher_no}", tags=["财务管理"])
async def get_voucher(
    voucher_no: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """凭证详情(含分录)"""
    _require_admin(x_role)
    try:
        return await _service.get_voucher(voucher_no)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/voucher/auto/order/{order_id}", tags=["财务管理"])
async def auto_voucher_from_order(
    order_id: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """按订单自动生成收入凭证

    会计分录(借:银行存款,贷:主营业务收入+应交税费)
    """
    _require_admin(x_role)
    try:
        return await _service.auto_voucher_from_order(order_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/voucher/auto/refund/{order_id}", tags=["财务管理"])
async def auto_voucher_from_refund(
    order_id: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """按退款自动生成红字凭证(借贷反转)"""
    _require_admin(x_role)
    try:
        return await _service.auto_voucher_from_refund(order_id)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/voucher/audit/{voucher_no}", tags=["财务管理"])
async def audit_voucher(
    voucher_no: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """凭证审核(草稿→已审核→已过账)"""
    _require_admin(x_role)
    try:
        return await _service.audit_voucher(voucher_no)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/voucher/closing", tags=["财务管理"])
async def month_end_closing(
    body: dict,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """月末结账(period 参数)"""
    _require_admin(x_role)
    period = (body or {}).get("period")
    if not period:
        raise HTTPException(status_code=400, detail="period 参数不能为空")
    try:
        return await _service.month_end_closing(period)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 2. 发票管理(4 个)
# ============================================================

@router.get("/api/finance/invoice/list", tags=["财务管理"])
async def list_invoices(
    status: str | None = Query(default=None, description="状态 issued/red/void"),
    type: str | None = Query(default=None, description="发票类型 normal/red"),
    period: str | None = Query(default=None, description="账期"),
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """发票列表"""
    _require_admin(x_role)
    try:
        return await _service.list_invoices(
            status=status, invoice_type=type, period=period,
        )
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/finance/invoice/{invoice_no}", tags=["财务管理"])
async def get_invoice(
    invoice_no: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """发票详情"""
    _require_admin(x_role)
    try:
        return await _service.get_invoice(invoice_no)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/invoice/issue", tags=["财务管理"])
async def issue_invoice(
    body: dict,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """开具发票(X-Member-Id, orderId+抬头信息)

    Body:
        {
            "orderId": "RT...",
            "titleType": "personal"/"company",
            "title": "抬头",
            "taxNo": "税号(企业抬头)",
            "amount": 1000.00  # 可选, 默认取订单实付
        }
    """
    member_id = _require_member(x_member_id)
    order_id = (body or {}).get("orderId")
    if not order_id:
        raise HTTPException(status_code=400, detail="orderId 不能为空")
    try:
        return await _service.issue_invoice(
            member_id=member_id,
            order_id=order_id,
            title_type=body.get("titleType", "personal"),
            title=body.get("title", ""),
            tax_no=body.get("taxNo", ""),
            amount=body.get("amount"),
        )
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/invoice/red/{invoice_no}", tags=["财务管理"])
async def red_invoice(
    invoice_no: str,
    body: dict = None,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """红字冲红(admin)"""
    _require_admin(x_role)
    reason = (body or {}).get("reason", "业务冲红")
    try:
        return await _service.red_invoice(invoice_no, reason)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 3. 税务管理(4 个)
# ============================================================

@router.get("/api/finance/tax/list", tags=["财务管理"])
async def list_tax_declarations(
    taxType: str | None = Query(default=None, description="税种 vat/consumption/surtax/income"),
    period: str | None = Query(default=None, description="账期"),
    status: str | None = Query(default=None, description="状态 pending/declared/paid"),
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """税务申报记录列表"""
    _require_admin(x_role)
    try:
        return await _service.list_tax_declarations(
            tax_type=taxType, period=period, status=status,
        )
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/finance/tax/{declaration_no}", tags=["财务管理"])
async def get_tax_declaration(
    declaration_no: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """税务申报详情"""
    _require_admin(x_role)
    try:
        return await _service.get_tax_declaration(declaration_no)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/tax/calc/{period}", tags=["财务管理"])
async def calc_tax(
    period: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """税额计算(按账期计算各税种)"""
    _require_admin(x_role)
    try:
        return await _service.calc_tax(period)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/tax/declare/{declaration_no}", tags=["财务管理"])
async def declare_tax(
    declaration_no: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """提交申报(待申报→已申报→已缴款)"""
    _require_admin(x_role)
    try:
        return await _service.declare_tax(declaration_no)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 4. 资金对账(3 个)
# ============================================================

@router.get("/api/finance/recon/list", tags=["财务管理"])
async def list_reconciliations(
    date: str | None = Query(default=None, description="日期 YYYY-MM-DD"),
    type: str | None = Query(default=None, description="对账类型 daily"),
    status: str | None = Query(default=None, description="状态 matched/diff/resolved"),
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """对账记录列表"""
    _require_admin(x_role)
    try:
        return await _service.list_reconciliations(
            date=date, recon_type=type, status=status,
        )
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/recon/daily/{date}", tags=["财务管理"])
async def daily_reconciliation(
    date: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """执行日终对账(三方对账: 订单+支付+银行)"""
    _require_admin(x_role)
    try:
        return await _service.daily_reconciliation(date)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/recon/{recon_id}/resolve", tags=["财务管理"])
async def resolve_reconciliation(
    recon_id: str,
    body: dict,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """差异处理(reason+handler)"""
    _require_admin(x_role)
    reason = (body or {}).get("reason", "")
    handler = (body or {}).get("handler", "admin")
    try:
        return await _service.resolve_reconciliation(recon_id, reason, handler)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 5. 付款管理(3 个)
# ============================================================

@router.get("/api/finance/payment/list", tags=["财务管理"])
async def list_payments(
    type: str | None = Query(default=None, description="付款类型"),
    status: str | None = Query(default=None, description="状态 pending/approving/approved/paid/rejected"),
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """付款记录列表"""
    _require_admin(x_role)
    try:
        return await _service.list_payments(payment_type=type, status=status)
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/payment/apply", tags=["财务管理"])
async def apply_payment(
    body: dict,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """发起付款(type/payee/amount/description)"""
    _require_admin(x_role)
    try:
        return await _service.apply_payment(
            payment_type=body.get("type", ""),
            payee=body.get("payee", ""),
            amount=float(body.get("amount", 0)),
            description=body.get("description", ""),
        )
    except Exception as e:
        raise _handle(e) from e


@router.post("/api/finance/payment/{payment_no}/approve", tags=["财务管理"])
async def approve_payment(
    payment_no: str,
    body: dict,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """审批付款(level: 1/2/3, decision: approve/reject)"""
    _require_admin(x_role)
    level = int(body.get("level", 0))
    approver = body.get("approver", "admin")
    decision = body.get("decision", "approve")
    reason = body.get("reason", "")
    try:
        return await _service.approve_payment(
            payment_no=payment_no, level=level,
            approver=approver, decision=decision, reason=reason,
        )
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 6. 财务报表(2 个)
# ============================================================

@router.get("/api/finance/report/profit/{period}", tags=["财务管理"])
async def profit_statement(
    period: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """利润表(收入-成本-税金-费用=利润)"""
    _require_admin(x_role)
    try:
        return await _service.profit_statement(period)
    except Exception as e:
        raise _handle(e) from e


@router.get("/api/finance/report/management/{date}", tags=["财务管理"])
async def management_report(
    date: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """管理报表(日度)"""
    _require_admin(x_role)
    try:
        return await _service.management_report(date)
    except Exception as e:
        raise _handle(e) from e


# ============================================================
# 注册函数
# ============================================================

def register_finance_routes(app):
    """注册财务管理端点到 FastAPI app"""
    app.include_router(router)
