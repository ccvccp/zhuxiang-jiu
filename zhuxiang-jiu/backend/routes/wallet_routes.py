"""钱包盈利模块路由(20 端点)

鉴权:
    - 用户端(15 接口): X-Member-Id 头标识当前会员(仅可操作本人钱包)
    - 管理端(5 接口):  X-Role: admin 头(提现审批/奖品发货/待审核列表)

异常映射(遵循项目约定):
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 余额不足/状态非法/参数非法等)
    - 权限校验 → 401(未登录) / 403(无权操作)

端点分布:
    - 钱包账户(2):  open / info
    - 充值提现(4):  deposit / withdraw / withdrawal-detail / pending-withdrawals
    - 提现审批(2):  approve / mark-paid
    - 消费退款(3):  pay / refund / transactions
    - 收益计算(3):  daily-interest / settle-monthly / interest-rules
    - 定期管理(4):  transfer-regular / deposits / settle-deposit / early-settle
    - 奖品管理(2):  rewards / claim-reward
    - 奖品履约(2):  ship-reward / sign-reward
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services import ai_feedback_hooks as ai_hooks
from services.wallet_service import (
    WalletService,
    CURRENT_ANNUAL_RATE,
    DEPOSIT_TIERS,
    REWARD_TIERS,
    LPR_RATE,
    LPR_CEILING,
    WITHDRAW_AUTO_APPROVE_THRESHOLD,
    WITHDRAW_MANUAL_THRESHOLD,
    REBATE_RATE,
    REBATE_MAX_PER_ORDER,
    MIN_DEPOSIT,
    OPEN_MIN_GROWTH,
)


router = APIRouter()
_service = WalletService()


# ============================================================
# 鉴权与异常映射辅助(对齐 member/finance 风格)
# ============================================================

def _require_member_id(x_member_id: str | None) -> int:
    """从 X-Member-Id 头提取会员ID, 缺失返回 401"""
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="X-Member-Id 格式不正确") from None


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
    """统一异常映射(对齐 finance_routes 风格)"""
    if isinstance(exc, KeyError):
        raise _map_key_error(exc) from exc
    if isinstance(exc, ValueError):
        raise _map_value_error(exc) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


# ============================================================
# 请求模型
# ============================================================

class DepositRequest(PydBaseModel):
    amount: float = Field(..., ge=0, description="充值金额(元)")
    payChannel: str = Field("alipay", description="支付渠道 alipay/wechat/bank")


class WithdrawRequest(PydBaseModel):
    amount: float = Field(..., gt=0, description="提现金额(元)")
    payChannel: str = Field("bank", description="提现渠道 bank/alipay/wechat")
    bankAccount: str = Field("", description="银行账号(银行提现必填)")


class ApproveWithdrawalRequest(PydBaseModel):
    decision: str = Field(..., description="审核决定: approved/rejected")
    auditor: str = Field(..., description="审核人")
    auditRemark: str = Field("", description="审核备注")


class PayRequest(PydBaseModel):
    amount: float = Field(..., gt=0, description="消费金额(元)")
    orderId: str = Field("", description="关联订单号")


class RefundRequest(PydBaseModel):
    amount: float = Field(..., gt=0, description="退款金额(元)")
    orderId: str = Field("", description="关联订单号")


class TransferRegularRequest(PydBaseModel):
    amount: float = Field(..., gt=0, description="预付款金额(元)")
    period: int = Field(..., description="存期(月): 3/6/12/24")


class ClaimRewardRequest(PydBaseModel):
    addressId: int = Field(0, ge=0, description="收货地址ID(0 表示稍后填写)")


class ShipRewardRequest(PydBaseModel):
    waybillNo: str = Field(..., description="运单号")


# ============================================================
# 1. 钱包账户(2 端点)
# ============================================================

@router.post("/api/wallet/open", tags=["钱包盈利"])
async def wallet_open(
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """开通钱包(前置: 会员等级 ≥ L2, 成长值 ≥ 500)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.open(member_id)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/wallet/info", tags=["钱包盈利"])
async def wallet_info(
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """钱包首页(余额 + 资产 + 累计收益 + 待领奖品数)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.get_info(member_id)
    except KeyError as e:
        raise _map_key_error(e) from e


# ============================================================
# 2. 充值提现(4 端点)
# ============================================================

@router.post("/api/wallet/deposit", tags=["钱包盈利"])
async def wallet_deposit(
    req: DepositRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """充值(资金进入活期钱包, 最低 ¥100)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.deposit(member_id, req.amount, req.payChannel)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/wallet/withdraw", tags=["钱包盈利"])
async def wallet_withdraw(
    req: WithdrawRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """提现申请(< ¥5000 自动通过; ¥5000-¥50000 一级审核; > ¥50000 二级审核)

    v7.8 AI 决策门: AI_ENFORCE_MODE=enforce 时高风险提现被拦截(409),
    中风险强制人工审核; observe/shadow 模式不改变业务行为。
    """
    member_id = _require_member_id(x_member_id)
    try:
        # v7.8 决策门(前置): 评分→阻断409/强制人工, 单号与创建复用
        from services.ai_enforcement_withdraw import enforce_withdrawal
        gate = await enforce_withdrawal(member_id, req.amount)
        return await _service.withdraw(
            member_id, req.amount, req.payChannel, req.bankAccount,
            withdraw_no=gate["withdrawNo"],
            force_review=gate["reviewRequired"],
        )
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/wallet/withdrawal/{withdraw_no}", tags=["钱包盈利"])
async def withdrawal_detail(
    withdraw_no: str,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """提现单详情(仅可查询本人提现单)"""
    member_id = _require_member_id(x_member_id)
    from repositories.wallet_repository import WalletRepository
    repo = WalletRepository()
    wd = await repo.get_withdrawal(withdraw_no)
    if not wd:
        raise HTTPException(status_code=404, detail=f"提现单 {withdraw_no} 不存在")
    if wd["userId"] != member_id:
        raise HTTPException(status_code=403, detail="无权查询他人提现单")
    return {"success": True, "withdrawal": wd, "logs": []}


@router.get("/api/wallet/withdrawals/pending", tags=["钱包盈利"])
async def pending_withdrawals(
    limit: int = Query(100, ge=1, le=500, description="返回条数上限"),
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """待审核提现列表(admin, 审批工作台)"""
    _require_admin(x_role)
    return await _service.list_pending_withdrawals(limit=limit)


# ============================================================
# 3. 提现审批(2 端点 · admin)
# ============================================================

@router.post("/api/wallet/withdrawal/{withdraw_no}/approve", tags=["钱包盈利"])
async def approve_withdrawal(
    withdraw_no: str,
    req: ApproveWithdrawalRequest,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """审核提现单(admin; approved 等待打款, rejected 释放冻结)"""
    _require_admin(x_role)
    try:
        result = await _service.approve_withdrawal(
            withdraw_no, req.decision, req.auditor, req.auditRemark,
        )
        # v7.6 自动反馈: 提现终态 → 自动配对反馈(通过期望 low 风险)
        await ai_hooks.on_withdraw_settled(
            withdraw_no, str(req.decision).lower() == "approved")
        return result
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/wallet/withdrawal/{withdraw_no}/paid", tags=["钱包盈利"])
async def mark_withdrawal_paid(
    withdraw_no: str,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """标记提现已打款(admin; 释放冻结, 累计提现累加)"""
    _require_admin(x_role)
    try:
        return await _service.mark_withdrawal_paid(withdraw_no)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
# 4. 消费退款(3 端点)
# ============================================================

@router.post("/api/wallet/pay", tags=["钱包盈利"])
async def wallet_pay(
    req: PayRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """钱包消费支付(扣减余额 + 1% 返利即时入账, 单笔返利上限 ¥100)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.pay(member_id, req.amount, req.orderId)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/wallet/refund", tags=["钱包盈利"])
async def wallet_refund(
    req: RefundRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """订单退款(资金退回钱包余额)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.refund(member_id, req.amount, req.orderId)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/wallet/transactions", tags=["钱包盈利"])
async def wallet_transactions(
    type: str | None = Query(
        default=None,
        description="交易类型筛选: deposit/withdraw/consume/refund/interest/rebate/transfer_regular",
    ),
    limit: int = Query(50, ge=1, le=500, description="返回条数上限"),
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """交易明细(可按类型筛选, 默认最新 50 条)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.list_transactions(member_id, tx_type=type, limit=limit)
    except KeyError as e:
        raise _map_key_error(e) from e


# ============================================================
# 5. 收益计算(3 端点)
# ============================================================

@router.get("/api/wallet/interest/daily", tags=["钱包盈利"])
async def daily_interest(
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """当日活期余额收益预估(年化 3%, 日计收益 = balance × 3% / 365)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.calc_daily_interest(member_id)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/wallet/interest/settle-monthly", tags=["钱包盈利"])
async def settle_monthly_interest(
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """月度余额收益入账(将 pending_interest 入账到 balance)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.settle_monthly_interest(member_id)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/wallet/interest/rules", tags=["钱包盈利"])
async def interest_rules():
    """收益规则说明(活期/定期档位 + LPR 合规)"""
    return {
        "success": True,
        "current": {
            "annualRate": CURRENT_ANNUAL_RATE,
            "dailyFormula": "balance × 3% / 365",
            "settleMethod": "按月入账",
        },
        "regular": [
            {
                "period": p,
                "minAmount": t["min"],
                "annualRate": t["rate"],
                "hasReward": t["hasReward"],
            }
            for p, t in DEPOSIT_TIERS.items()
        ],
        "rebate": {
            "rate": REBATE_RATE,
            "maxPerOrder": REBATE_MAX_PER_ORDER,
            "settleMethod": "即时入账",
        },
        "compliance": {
            "lprRate": LPR_RATE,
            "ceiling": LPR_CEILING,
            "rule": "综合收益率 ≤ LPR × 4(≈13.8%), 超限自动降档",
        },
        "walletRules": {
            "minDeposit": MIN_DEPOSIT,
            "openMinGrowth": OPEN_MIN_GROWTH,
            "withdrawAutoApproveThreshold": WITHDRAW_AUTO_APPROVE_THRESHOLD,
            "withdrawManualThreshold": WITHDRAW_MANUAL_THRESHOLD,
        },
        "rewardTiers": [
            {"minAmount": a, "period": p, "name": n, "value": v}
            for a, p, n, v in REWARD_TIERS
        ],
        "logs": [],
    }


# ============================================================
# 6. 定期管理(4 端点)
# ============================================================

@router.post("/api/wallet/transfer-regular", tags=["钱包盈利"])
async def transfer_to_regular(
    req: TransferRegularRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """活期转定期(扣减余额 + 创建定期记录 + LPR 合规校验)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.transfer_to_regular(member_id, req.amount, req.period)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.get("/api/wallet/deposits", tags=["钱包盈利"])
async def list_deposits(
    status: str | None = Query(
        default=None,
        description="状态筛选: active/matured/settled/early_settled",
    ),
    limit: int = Query(50, ge=1, le=500, description="返回条数上限"),
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """用户定期列表(可按状态筛选)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.list_deposits(member_id, status=status, limit=limit)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/wallet/deposit/{deposit_no}/settle", tags=["钱包盈利"])
async def settle_deposit(
    deposit_no: str,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """定期到期取出(本金 + 余额收益入账, 奖品转可领取)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.settle_deposit(member_id, deposit_no)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/wallet/deposit/{deposit_no}/early-settle", tags=["钱包盈利"])
async def early_settle_deposit(
    deposit_no: str,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """定期提前取出(收 1% 手续费, 损失余额收益 + 奖品)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.early_settle_deposit(member_id, deposit_no)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
# 7. 奖品管理(2 端点)
# ============================================================

@router.get("/api/wallet/rewards", tags=["钱包盈利"])
async def list_rewards(
    status: str | None = Query(
        default=None,
        description="状态筛选: claimable/claimed/shipped/signed/expired",
    ),
    limit: int = Query(50, ge=1, le=500, description="返回条数上限"),
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """用户奖品列表(可按状态筛选)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.list_rewards(member_id, status=status, limit=limit)
    except KeyError as e:
        raise _map_key_error(e) from e


@router.post("/api/wallet/reward/{reward_no}/claim", tags=["钱包盈利"])
async def claim_reward(
    reward_no: str,
    req: ClaimRewardRequest,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """领取奖品(状态: claimable → claimed, 等待发货)"""
    member_id = _require_member_id(x_member_id)
    try:
        return await _service.claim_reward(member_id, reward_no, req.addressId)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
# 8. 奖品履约(2 端点 · admin 发货 + 用户签收)
# ============================================================

@router.post("/api/wallet/reward/{reward_no}/ship", tags=["钱包盈利"])
async def ship_reward(
    reward_no: str,
    req: ShipRewardRequest,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
):
    """奖品发货(admin; 状态: claimed → shipped)"""
    _require_admin(x_role)
    try:
        return await _service.ship_reward(reward_no, req.waybillNo)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


@router.post("/api/wallet/reward/{reward_no}/sign", tags=["钱包盈利"])
async def sign_reward(
    reward_no: str,
    x_member_id: Annotated[str | None, Header(alias="X-Member-Id")] = None,
):
    """奖品签收(状态: shipped → signed)"""
    _require_member_id(x_member_id)
    try:
        return await _service.sign_reward(reward_no)
    except KeyError as e:
        raise _map_key_error(e) from e
    except ValueError as e:
        raise _map_value_error(e) from e


# ============================================================
# 路由注册
# ============================================================

def register_wallet_routes(app):
    """注册钱包盈利模块路由"""
    app.include_router(router)
