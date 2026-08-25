"""钱包盈利业务:开通/充值/提现/消费/收益/转定期/奖品

并发安全(遵循项目约定 lock:{key} 格式):
    - 余额 RMW: 使用 wallet:{userId} 锁(对齐 agent:{agentId})
    - 提现审批: 嵌套 wallet:withdraw:{withdrawNo} 锁(防重复审批)
    - 定期结清: 嵌套 wallet:deposit:{depositNo} 锁(防重复结清)
    - 奖品领取: 嵌套 wallet:reward:{rewardNo} 锁(防重复领取)

收益规则(营销补贴, 非借贷利息):
    - 活期: 年化 3%, 日计补贴 = balance × 3% / 365, 按月入账
    - 定期: 按档位(3/6/12/24 月), 到期一次性发放 + 奖品
    - 消费返利: 钱包支付额外 1% 返现, 单笔上限 ¥100, 即时入账
    - LPR 合规: 综合收益率 ≤ LPR × 4(≈13.8%), 超限自动降档

异常约定(遵循项目约定):
    - KeyError(message)  → 路由层映射为 404
    - ValueError(message) → 路由层映射为 409
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from core.helpers import ts
from core.locks import get_lock
from repositories.member_repository import MemberRepository
from repositories.wallet_repository import (
    WalletRepository,
    STATUS_ACTIVE,
    STATUS_FROZEN,
    STATUS_CLOSED,
    STATUS_PENDING,
)

logger = logging.getLogger(__name__)


# ============================================================
# 时区(项目统一为 Asia/Shanghai)
# ============================================================

_TZ_SHANGHAI = timezone(timedelta(hours=8))


def _now_shanghai() -> datetime:
    """上海时区当前时间"""
    return datetime.now(_TZ_SHANGHAI)


def _today_str() -> str:
    """上海时区当前日期(YYYY-MM-DD)"""
    return _now_shanghai().strftime("%Y-%m-%d")


def _add_months(date_str: str, months: int) -> str:
    """日期字符串加 N 个月(用于定期到期日计算)

    Args:
        date_str: YYYY-MM-DD 格式
        months: 月数(3/6/12/24)
    """
    d = datetime.strptime(date_str, "%Y-%m-%d")
    # 简单加月(处理年末跨年), 若目标日不存在则取当月最后一天
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    # 处理月末(如 2 月 30 日不存在)
    import calendar
    day = min(d.day, calendar.monthrange(year, month)[1])
    return datetime(year, month, day).strftime("%Y-%m-%d")


# ============================================================
# 钱包状态常量(与 repository 对齐)
# ============================================================

STATUS_NAMES = {
    STATUS_PENDING: "待开通",
    STATUS_ACTIVE: "正常",
    STATUS_FROZEN: "冻结",
    STATUS_CLOSED: "注销",
}

# 开通前置: 会员等级 ≥ L2(成长值 ≥ 500), 累计消费 ≥ ¥500
OPEN_MIN_LEVEL = 2
OPEN_MIN_GROWTH = 500

# 充值
MIN_DEPOSIT = 100.0  # 最低充值 ¥100

# 提现
WITHDRAW_AUTO_APPROVE_THRESHOLD = 5000.0       # < ¥5000 自动通过
WITHDRAW_MANUAL_THRESHOLD = 50000.0             # > ¥50000 二级审核
WITHDRAW_REGULAR_EARLY_FEE_RATE = 0.01          # 定期提前取出手续费 1%

# 消费返利
REBATE_RATE = 0.01            # 钱包支付返利 1%
REBATE_MAX_PER_ORDER = 100.0  # 单笔返利上限 ¥100

# 活期收益
CURRENT_ANNUAL_RATE = 0.03    # 活期年化 3%
DAYS_PER_YEAR = 365

# 定期档位: {存期月: (最低存入, 年化补贴率, 奖品资格)}
DEPOSIT_TIERS = {
    3:  {"min": 1000.0,  "rate": 0.030, "hasReward": False},
    6:  {"min": 2000.0,  "rate": 0.050, "hasReward": True},
    12: {"min": 5000.0,  "rate": 0.030, "hasReward": True},
    24: {"min": 10000.0, "rate": 0.035, "hasReward": True},
}

# 奖品阶梯: (最低金额, 存期月, 奖品名称, 奖品价值)
# 按金额升序匹配第一个满足条件的档位
REWARD_TIERS = [
    (2000,  6,  "翠竹小酌 250ml × 1",                   88.0),
    (3000,  6,  "翠竹小酌 250ml × 1 + 50 优惠券",         138.0),
    (5000,  12, "竹香经典 45° 500ml × 2 瓶",            536.0),
    (8000,  12, "竹香典藏 750ml × 1 + 竹香经典 × 1",     1266.0),
    (10000, 12, "竹香典藏 750ml × 2",                   1996.0),
    (20000, 12, "竹香尊享礼盒 × 1 + 泰山游资格",          1888.0),
    (50000, 12, "竹香尊享礼盒 × 3 + 泰山游 × 2",         5664.0),
    (10000, 24, "竹香典藏 750ml × 2",                   1996.0),
    (50000, 24, "大师定制酒 × 1 + 泰山游 × 2",          6000.0),
]

# 奖品领取有效期(到期后 30 天内领取, 逾期作废)
REWARD_CLAIM_DAYS = 30

# LPR 合规(民间借贷利率上限 = LPR × 4)
LPR_RATE = 0.0345                 # 当前 1 年期 LPR 3.45%
LPR_CEILING = LPR_RATE * 4        # 法定上限 ≈ 13.8%


def _calc_daily_interest(balance: float) -> float:
    """活期日计补贴: balance × 年化 3% / 365

    Returns:
        日补贴金额(向下取整到分)
    """
    daily = balance * CURRENT_ANNUAL_RATE / DAYS_PER_YEAR
    return round(daily, 2)


def _calc_regular_interest(amount: float, period: int) -> tuple:
    """定期营销补贴计算

    Args:
        amount: 预付款金额
        period: 存期(月) 3/6/12/24

    Returns:
        (interest, annual_rate)
    """
    tier = DEPOSIT_TIERS.get(period)
    if not tier:
        raise ValueError(f"存期非法: {period} 月(须为 3/6/12/24)")
    rate = tier["rate"]
    # 利息 = 本金 × 年化 × (月数/12)
    interest = round(amount * rate * period / 12, 2)
    return interest, rate


def _match_reward(amount: float, period: int) -> Optional[tuple]:
    """匹配奖品档位(金额 ≥ 档位金额 + 存期匹配)

    Returns:
        (reward_name, reward_value) 或 None
    """
    for min_amount, req_period, name, value in REWARD_TIERS:
        if amount >= min_amount and period == req_period:
            return name, value
    return None


def _check_lpr_compliance(amount: float, interest: float,
                           reward_value: float = 0.0) -> dict:
    """LPR 合规校验: 综合收益率 ≤ LPR × 4(≈13.8%)

    Returns:
        {
            "compliant": bool,
            "actualRate": float,  # 综合收益率
            "ceiling": float,    # 法定上限
            "action": str,       # "pass" / "degraded"(已降档)
        }
    """
    total_income = interest + reward_value
    if amount <= 0:
        return {"compliant": True, "actualRate": 0.0,
                "ceiling": LPR_CEILING, "action": "pass"}
    actual_rate = total_income / amount
    compliant = actual_rate <= LPR_CEILING
    return {
        "compliant": compliant,
        "actualRate": round(actual_rate, 4),
        "ceiling": LPR_CEILING,
        "action": "pass" if compliant else "degraded",
    }


def _mask_bank_account(account: str) -> str:
    """银行账号脱敏(保留后 4 位)"""
    if not account or len(account) <= 4:
        return "****"
    return "*" * (len(account) - 4) + account[-4:]


class WalletService:
    """钱包业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, wallet_repo: WalletRepository = WalletRepository(),
                 member_repo: MemberRepository = MemberRepository()):
        self.wallet_repo = wallet_repo
        self.member_repo = member_repo

    # ============================================================
    # P0: 钱包开通 + 查询
    # ============================================================

    async def open(self, user_id) -> dict:
        """开通钱包

        前置条件:
            - 会员等级 ≥ L2(成长值 ≥ 500)
            - 会员状态正常
            - 未已开通钱包

        Raises:
            KeyError: 会员不存在
            ValueError: 条件不满足 / 已开通
        """
        # 会员校验(读操作, 无需持锁)
        member = await self.member_repo.get_by_id(user_id)
        if not member:
            raise KeyError(f"会员 {user_id} 不存在")
        if member.get("status", 1) == 0:
            raise ValueError("账号已被禁用, 无法开通钱包")

        growth = member.get("growth_value", 0)
        if growth < OPEN_MIN_GROWTH:
            raise ValueError(
                f"会员等级不足: 需成长值 ≥ {OPEN_MIN_GROWTH}(当前 {growth})"
            )

        # 开通(内含 user_id 唯一约束校验)
        now = ts()
        account_data = {
            "status": STATUS_ACTIVE,
            "balance": 0.0,
            "frozenAmount": 0.0,
            "totalDeposit": 0.0,
            "totalWithdraw": 0.0,
            "totalInterest": 0.0,
            "totalReward": 0.0,
            "totalRebate": 0.0,
            "pendingInterest": 0.0,
            "openedAt": now,
            "closedAt": "",
            "createdAt": now,
            "updatedAt": now,
        }
        async with get_lock(f"wallet:{user_id}"):
            account = await self.wallet_repo.open_account(user_id, account_data)
            logger.info("wallet_opened user_id=%r", user_id)
            return {
                "success": True,
                "userId": user_id,
                "status": account["status"],
                "statusName": STATUS_NAMES[account["status"]],
                "balance": account["balance"],
                "openedAt": account["openedAt"],
                "logs": [
                    {"step": "开通校验", "level": "INFO",
                     "msg": f"会员成长值 {growth} ≥ {OPEN_MIN_GROWTH} 通过"},
                    {"step": "钱包开通", "level": "INFO", "msg": "钱包已激活"},
                ],
            }

    async def get_info(self, user_id) -> dict:
        """钱包首页信息(余额 + 资产 + 累计收益)

        Raises:
            KeyError: 钱包未开通
        """
        account = await self.wallet_repo.get_account(user_id)
        if not account:
            raise KeyError(f"用户 {user_id} 未开通钱包")

        # 查询定期总金额 + 待领奖品
        deposits = await self.wallet_repo.list_deposits(user_id, status="active")
        regular_total = sum(d["amount"] for d in deposits)
        rewards = await self.wallet_repo.list_claimable_rewards(user_id)

        balance = float(account.get("balance", 0))
        pending = float(account.get("pendingInterest", 0))
        total_assets = balance + regular_total + pending

        return {
            "success": True,
            "userId": user_id,
            "status": account.get("status"),
            "statusName": STATUS_NAMES.get(account.get("status"), "未知"),
            "totalAssets": round(total_assets, 2),
            "currentBalance": balance,
            "regularTotal": round(regular_total, 2),
            "pendingInterest": pending,
            "totalDeposit": float(account.get("totalDeposit", 0)),
            "totalWithdraw": float(account.get("totalWithdraw", 0)),
            "totalInterest": float(account.get("totalInterest", 0)),
            "totalReward": float(account.get("totalReward", 0)),
            "totalRebate": float(account.get("totalRebate", 0)),
            "claimableRewardCount": len(rewards),
            "logs": [],
        }

    # ============================================================
    # P0: 充值
    # ============================================================

    async def deposit(self, user_id, amount: float,
                       pay_channel: str = "alipay") -> dict:
        """充值: 资金进入活期钱包

        Args:
            user_id: 用户ID
            amount: 充值金额(≥ ¥100)
            pay_channel: 支付渠道 alipay/wechat/bank

        Raises:
            KeyError: 钱包未开通
            ValueError: 金额非法 / 钱包已冻结
        """
        if amount < MIN_DEPOSIT:
            raise ValueError(f"充值金额须 ≥ ¥{MIN_DEPOSIT}")
        if pay_channel not in ("alipay", "wechat", "bank"):
            raise ValueError(f"支付渠道非法: {pay_channel}")

        async with get_lock(f"wallet:{user_id}"):
            account = await self.wallet_repo.get_account(user_id)
            if not account:
                raise KeyError(f"用户 {user_id} 未开通钱包")
            if account.get("status") != STATUS_ACTIVE:
                raise ValueError(
                    f"钱包状态异常(当前: {STATUS_NAMES.get(account['status'])}), 无法充值"
                )

            # 1. 余额累加
            new_balance = await self.wallet_repo.add_balance(user_id, amount)
            # 2. 累计充值累加
            await self.wallet_repo.update_account_fields(user_id, {
                "totalDeposit": float(account.get("totalDeposit", 0)) + amount,
                "updatedAt": ts(),
            })
            # 3. 记录交易流水
            tx_no = await self.wallet_repo.next_tx_no()
            await self.wallet_repo.save_transaction({
                "txNo": tx_no,
                "userId": user_id,
                "type": "deposit",
                "direction": "IN",
                "amount": amount,
                "balanceAfter": new_balance,
                "payChannel": pay_channel,
                "orderId": "",
                "depositNo": "",
                "withdrawNo": "",
                "status": "success",
                "description": f"{pay_channel} 充值 ¥{amount:.2f}",
                "createdAt": ts(),
            })

            logger.info("wallet_deposit user_id=%r amount=%.2f tx=%s",
                        user_id, amount, tx_no)
            return {
                "success": True,
                "userId": user_id,
                "txNo": tx_no,
                "amount": amount,
                "payChannel": pay_channel,
                "balanceAfter": new_balance,
                "logs": [{"step": "充值", "level": "INFO",
                          "msg": f"充值 ¥{amount:.2f}, 余额 ¥{new_balance:.2f}"}],
            }

    # ============================================================
    # P0: 奖励余额(推广奖励, 只可购物不可提现)
    # ============================================================

    async def deposit_reward(self, user_id, amount: float,
                             description: str = "推广奖励") -> dict:
        """奖励余额入账: 计入 rewardBalance, 不可提现、不可转赠

        用途: 推广码矩阵奖励等营销激励, 仅可在本站购买产品时核销。

        Raises:
            KeyError: 钱包未开通
            ValueError: 金额非法
        """
        if amount <= 0:
            raise ValueError("奖励金额必须大于 0")

        async with get_lock(f"wallet:{user_id}"):
            account = await self.wallet_repo.get_account(user_id)
            if not account:
                raise KeyError(f"用户 {user_id} 未开通钱包")
            if account.get("status") != STATUS_ACTIVE:
                raise ValueError("钱包状态异常, 无法发放奖励")

            new_reward = await self.wallet_repo.add_reward_balance(user_id, amount)
            tx_no = await self.wallet_repo.next_tx_no()
            await self.wallet_repo.save_transaction({
                "txNo": tx_no,
                "userId": user_id,
                "type": "reward",
                "direction": "IN",
                "amount": amount,
                "balanceAfter": new_reward,
                "payChannel": "",
                "orderId": "",
                "depositNo": "",
                "withdrawNo": "",
                "status": "success",
                "description": f"{description} ¥{amount:.2f}(仅限购物,不可提现)",
                "createdAt": ts(),
            })
            logger.info("wallet_reward_deposit user_id=%r amount=%.2f tx=%s",
                        user_id, amount, tx_no)
            return {
                "success": True,
                "userId": user_id,
                "txNo": tx_no,
                "amount": amount,
                "rewardBalanceAfter": new_reward,
                "note": "奖励余额仅可购买本站产品,不可提现",
            }

    async def pay_with_reward(self, user_id, amount: float,
                              order_id: str = "",
                              description: str = "奖励余额购物") -> dict:
        """奖励余额购买本站产品: 核销 rewardBalance

        与活期余额无关; 提现接口(withdraw)仅操作 balance, 奖励余额天然不可提现。

        Raises:
            KeyError: 钱包未开通
            ValueError: 金额非法 / 奖励余额不足 / 钱包冻结
        """
        if amount <= 0:
            raise ValueError("支付金额必须大于 0")

        async with get_lock(f"wallet:{user_id}"):
            account = await self.wallet_repo.get_account(user_id)
            if not account:
                raise KeyError(f"用户 {user_id} 未开通钱包")
            if account.get("status") != STATUS_ACTIVE:
                raise ValueError("钱包状态异常, 无法支付")

            reward = float(account.get("rewardBalance", 0))
            if reward < amount:
                raise ValueError(
                    f"奖励余额不足: 当前 ¥{reward:.2f}, 需 ¥{amount:.2f}")

            new_reward = await self.wallet_repo.add_reward_balance(
                user_id, -amount)
            tx_no = await self.wallet_repo.next_tx_no()
            await self.wallet_repo.save_transaction({
                "txNo": tx_no,
                "userId": user_id,
                "type": "reward_pay",
                "direction": "OUT",
                "amount": amount,
                "balanceAfter": new_reward,
                "payChannel": "reward",
                "orderId": order_id,
                "depositNo": "",
                "withdrawNo": "",
                "status": "success",
                "description": f"{description} ¥{amount:.2f}",
                "createdAt": ts(),
            })
            logger.info("wallet_reward_pay user_id=%r amount=%.2f tx=%s order=%s",
                        user_id, amount, tx_no, order_id)
            return {
                "success": True,
                "userId": user_id,
                "txNo": tx_no,
                "orderId": order_id,
                "amount": amount,
                "rewardBalanceAfter": new_reward,
            }

    async def get_reward_balance(self, user_id) -> dict:
        """查询奖励余额与活期余额对比

        Raises:
            KeyError: 钱包未开通
        """
        account = await self.wallet_repo.get_account(user_id)
        if not account:
            raise KeyError(f"用户 {user_id} 未开通钱包")
        return {
            "userId": user_id,
            "balance": float(account.get("balance", 0)),
            "rewardBalance": float(account.get("rewardBalance", 0)),
            "note": "rewardBalance 仅可购买本站产品,不可提现",
        }

    # ============================================================
    # P0: 提现
    # ============================================================

    async def withdraw(self, user_id, amount: float,
                        pay_channel: str = "bank",
                        bank_account: str = "",
                        withdraw_no: str = "",
                        force_review: bool = False) -> dict:
        """提现申请: 活期余额冻结, 待审核

        规则:
            - amount < ¥5000: 自动通过(待打款)
            - ¥5000 ≤ amount ≤ ¥50000: 人工一级审核
            - amount > ¥50000: 人工二级审核
            - force_review=True(v7.8 AI 决策门): 跳过自动通过, 强制人工审核

        Args:
            withdraw_no: v7.8 决策门预生成的提现单号(缺省内部生成)
            force_review: AI reviewRequired 决策 → 单子生而 pending

        Raises:
            KeyError: 钱包未开通
            ValueError: 金额非法 / 余额不足 / 钱包冻结
        """
        if amount <= 0:
            raise ValueError("提现金额必须大于 0")
        if not bank_account and pay_channel == "bank":
            raise ValueError("银行提现须提供银行账号")

        async with get_lock(f"wallet:{user_id}"):
            account = await self.wallet_repo.get_account(user_id)
            if not account:
                raise KeyError(f"用户 {user_id} 未开通钱包")
            if account.get("status") != STATUS_ACTIVE:
                raise ValueError("钱包已冻结/注销, 无法提现")

            balance = float(account.get("balance", 0))
            if balance < amount:
                raise ValueError(f"余额不足: 当前 ¥{balance:.2f}, 需 ¥{amount:.2f}")

            # 1. 冻结金额(从 balance 扣减, 加到 frozenAmount)
            await self.wallet_repo.add_balance(user_id, -amount)
            await self.wallet_repo.add_frozen(user_id, amount)

            # 2. 生成提现单(决策门预生成则复用, 保证快照配对键一致)
            if not withdraw_no:
                withdraw_no = await self.wallet_repo.next_withdraw_no()
            # 自动通过判定(AI 强制人工时跳过)
            if amount < WITHDRAW_AUTO_APPROVE_THRESHOLD and not force_review:
                status = "approved"
                auditor = "AI风控"
            else:
                status = "pending"
                auditor = ""

            await self.wallet_repo.save_withdrawal({
                "withdrawNo": withdraw_no,
                "userId": user_id,
                "amount": amount,
                "fee": 0.0,
                "actualAmount": amount,
                "source": "current",
                "depositNo": "",
                "lossInterest": 0.0,
                "lossReward": False,
                "payChannel": pay_channel,
                "bankAccount": _mask_bank_account(bank_account),
                "status": status,
                "auditor": auditor,
                "auditRemark": ("自动通过" if status == "approved"
                                else "AI风控: 强制人工审核" if force_review
                                else ""),
                "payTime": "",
                "createdAt": ts(),
                "updatedAt": ts(),
            })

            # 3. 记录交易流水(出账, status=processing)
            tx_no = await self.wallet_repo.next_tx_no()
            await self.wallet_repo.save_transaction({
                "txNo": tx_no,
                "userId": user_id,
                "type": "withdraw",
                "direction": "OUT",
                "amount": amount,
                "balanceAfter": balance - amount,
                "payChannel": pay_channel,
                "orderId": "",
                "depositNo": "",
                "withdrawNo": withdraw_no,
                "status": "processing",
                "description": f"提现申请 ¥{amount:.2f}({status})",
                "createdAt": ts(),
            })

            logger.info("wallet_withdraw user_id=%r amount=%.2f wd=%s status=%s",
                        user_id, amount, withdraw_no, status)
            return {
                "success": True,
                "userId": user_id,
                "withdrawNo": withdraw_no,
                "txNo": tx_no,
                "amount": amount,
                "actualAmount": amount,
                "status": status,
                "autoApproved": status == "approved",
                "balanceAfter": balance - amount,
                "logs": [
                    {"step": "冻结", "level": "INFO",
                     "msg": f"冻结 ¥{amount:.2f}(待打款)"},
                    {"step": "提现申请", "level": "INFO",
                     "msg": f"提现单 {withdraw_no}, 状态: {status}"},
                ],
            }

    async def approve_withdrawal(self, withdraw_no, decision: str,
                                  auditor: str, audit_remark: str = "") -> dict:
        """审核提现单(decision: approved/rejected)

        approved: 标记已批准(等待打款), 仍需 mark_withdrawal_paid 完成打款
        rejected: 释放冻结金额回到余额

        Raises:
            KeyError: 提现单不存在
            ValueError: decision 非法 / 已处理
        """
        if decision not in ("approved", "rejected"):
            raise ValueError("decision 须为 approved/rejected")

        # 防并发重复审核
        async with get_lock(f"wallet:withdraw:{withdraw_no}"):
            wd = await self.wallet_repo.get_withdrawal(withdraw_no)
            if not wd:
                raise KeyError(f"提现单 {withdraw_no} 不存在")
            if wd["status"] != "pending":
                raise ValueError(f"提现单已处理(当前状态: {wd['status']})")

            user_id = wd["userId"]
            amount = float(wd["amount"])

            # 嵌套持钱包锁(保护余额 RMW)
            async with get_lock(f"wallet:{user_id}"):
                if decision == "rejected":
                    # 释放冻结: frozenAmount - amount, balance + amount
                    await self.wallet_repo.reduce_frozen(user_id, amount)
                    await self.wallet_repo.add_balance(user_id, amount)
                    new_status = "rejected"
                    log_msg = f"提现被拒, 释放冻结 ¥{amount:.2f}"
                else:
                    # 批准: 冻结保持, 等待打款
                    new_status = "approved"
                    log_msg = "提现已批准, 等待打款"

                await self.wallet_repo.update_withdrawal_fields(withdraw_no, {
                    "status": new_status,
                    "auditor": auditor,
                    "auditRemark": audit_remark,
                    "updatedAt": ts(),
                })

            logger.info("wallet_withdraw_audit wd=%s decision=%s auditor=%s",
                        withdraw_no, decision, auditor)
            return {
                "success": True,
                "withdrawNo": withdraw_no,
                "decision": decision,
                "status": new_status,
                "auditor": auditor,
                "logs": [{"step": "提现审核", "level": "INFO", "msg": log_msg}],
            }

    async def mark_withdrawal_paid(self, withdraw_no) -> dict:
        """标记提现已打款(完成提现)

        释放冻结金额, 累计提现累加, 交易流水状态更新为 success

        Raises:
            KeyError: 提现单不存在
            ValueError: 状态非法(须为 approved)
        """
        async with get_lock(f"wallet:withdraw:{withdraw_no}"):
            wd = await self.wallet_repo.get_withdrawal(withdraw_no)
            if not wd:
                raise KeyError(f"提现单 {withdraw_no} 不存在")
            if wd["status"] != "approved":
                raise ValueError(f"提现单状态非 approved(当前: {wd['status']})")

            user_id = wd["userId"]
            amount = float(wd["amount"])
            actual = float(wd["actualAmount"])

            async with get_lock(f"wallet:{user_id}"):
                # 释放冻结(资金已出账)
                await self.wallet_repo.reduce_frozen(user_id, amount)
                # 累计提现累加
                account = await self.wallet_repo.get_account(user_id)
                await self.wallet_repo.update_account_fields(user_id, {
                    "totalWithdraw": float(account.get("totalWithdraw", 0)) + amount,
                    "updatedAt": ts(),
                })
                # 提现单状态更新
                await self.wallet_repo.update_withdrawal_fields(withdraw_no, {
                    "status": "paid",
                    "payTime": ts(),
                    "updatedAt": ts(),
                })

            logger.info("wallet_withdraw_paid wd=%s amount=%.2f", withdraw_no, amount)
            return {
                "success": True,
                "withdrawNo": withdraw_no,
                "amount": amount,
                "actualAmount": actual,
                "status": "paid",
                "payTime": ts(),
                "logs": [{"step": "提现打款", "level": "INFO",
                          "msg": f"已打款 ¥{actual:.2f}"}],
            }

    # ============================================================
    # P0: 消费支付 + 返利
    # ============================================================

    async def pay(self, user_id, amount: float,
                  order_id: str = "") -> dict:
        """钱包消费支付: 余额扣减 + 1% 返利即时入账

        Args:
            user_id: 用户ID
            amount: 消费金额
            order_id: 关联订单号

        Raises:
            KeyError: 钱包未开通
            ValueError: 金额非法 / 余额不足 / 钱包冻结
        """
        if amount <= 0:
            raise ValueError("消费金额必须大于 0")

        async with get_lock(f"wallet:{user_id}"):
            account = await self.wallet_repo.get_account(user_id)
            if not account:
                raise KeyError(f"用户 {user_id} 未开通钱包")
            if account.get("status") != STATUS_ACTIVE:
                raise ValueError("钱包状态异常, 无法支付")

            balance = float(account.get("balance", 0))
            if balance < amount:
                raise ValueError(f"余额不足: 当前 ¥{balance:.2f}, 需 ¥{amount:.2f}")

            # 1. 扣减余额
            new_balance = await self.wallet_repo.add_balance(user_id, -amount)

            # 2. 计算返利(1%, 上限 ¥100)
            rebate = min(amount * REBATE_RATE, REBATE_MAX_PER_ORDER)
            rebate = round(rebate, 2)
            if rebate > 0:
                new_balance = await self.wallet_repo.add_balance(user_id, rebate)
                # 累计返利累加
                await self.wallet_repo.update_account_fields(user_id, {
                    "totalRebate": float(account.get("totalRebate", 0)) + rebate,
                    "updatedAt": ts(),
                })

            # 3. 消费交易流水
            tx_no = await self.wallet_repo.next_tx_no()
            await self.wallet_repo.save_transaction({
                "txNo": tx_no,
                "userId": user_id,
                "type": "consume",
                "direction": "OUT",
                "amount": amount,
                "balanceAfter": new_balance,
                "payChannel": "wallet",
                "orderId": order_id,
                "depositNo": "",
                "withdrawNo": "",
                "status": "success",
                "description": f"消费支付 ¥{amount:.2f}" + (
                    f", 订单 {order_id}" if order_id else ""),
                "createdAt": ts(),
            })

            # 4. 返利交易流水(入账)
            logs = [{"step": "消费支付", "level": "INFO",
                     "msg": f"支付 ¥{amount:.2f}, 余额 ¥{new_balance:.2f}"}]
            if rebate > 0:
                rebate_tx_no = await self.wallet_repo.next_tx_no()
                await self.wallet_repo.save_transaction({
                    "txNo": rebate_tx_no,
                    "userId": user_id,
                    "type": "rebate",
                    "direction": "IN",
                    "amount": rebate,
                    "balanceAfter": new_balance,
                    "payChannel": "",
                    "orderId": order_id,
                    "depositNo": "",
                    "withdrawNo": "",
                    "status": "success",
                    "description": f"消费返利 {REBATE_RATE*100:.0f}% ¥{rebate:.2f}",
                    "createdAt": ts(),
                })
                logs.append({"step": "消费返利", "level": "INFO",
                             "msg": f"返利 ¥{rebate:.2f} 已入账"})

            logger.info("wallet_pay user_id=%r amount=%.2f rebate=%.2f tx=%s",
                        user_id, amount, rebate, tx_no)
            return {
                "success": True,
                "userId": user_id,
                "txNo": tx_no,
                "amount": amount,
                "rebate": rebate,
                "balanceAfter": new_balance,
                "orderId": order_id,
                "logs": logs,
            }

    async def refund(self, user_id, amount: float,
                      order_id: str = "") -> dict:
        """订单退款: 资金退回钱包余额

        Raises:
            KeyError: 钱包未开通
            ValueError: 金额非法
        """
        if amount <= 0:
            raise ValueError("退款金额必须大于 0")

        async with get_lock(f"wallet:{user_id}"):
            account = await self.wallet_repo.get_account(user_id)
            if not account:
                raise KeyError(f"用户 {user_id} 未开通钱包")

            new_balance = await self.wallet_repo.add_balance(user_id, amount)
            tx_no = await self.wallet_repo.next_tx_no()
            await self.wallet_repo.save_transaction({
                "txNo": tx_no,
                "userId": user_id,
                "type": "refund",
                "direction": "IN",
                "amount": amount,
                "balanceAfter": new_balance,
                "payChannel": "",
                "orderId": order_id,
                "depositNo": "",
                "withdrawNo": "",
                "status": "success",
                "description": f"订单退款 ¥{amount:.2f}" + (
                    f", 订单 {order_id}" if order_id else ""),
                "createdAt": ts(),
            })

            logger.info("wallet_refund user_id=%r amount=%.2f tx=%s",
                        user_id, amount, tx_no)
            return {
                "success": True,
                "userId": user_id,
                "txNo": tx_no,
                "amount": amount,
                "balanceAfter": new_balance,
                "orderId": order_id,
                "logs": [{"step": "退款入账", "level": "INFO",
                          "msg": f"退款 ¥{amount:.2f}, 余额 ¥{new_balance:.2f}"}],
            }

    # ============================================================
    # P0: 交易明细
    # ============================================================

    async def list_transactions(self, user_id, tx_type: str = None,
                                 limit: int = 50) -> dict:
        """查询交易明细(可按类型筛选)

        Raises:
            KeyError: 钱包未开通
        """
        account = await self.wallet_repo.get_account(user_id)
        if not account:
            raise KeyError(f"用户 {user_id} 未开通钱包")

        txs = await self.wallet_repo.list_transactions(
            user_id, tx_type=tx_type, limit=limit)
        return {
            "success": True,
            "userId": user_id,
            "count": len(txs),
            "transactions": txs,
            "logs": [],
        }

    # ============================================================
    # P0: 收益计算(日计补贴 + 月结付)
    # ============================================================

    async def calc_daily_interest(self, user_id) -> dict:
        """计算当日活期营销补贴(不入账, 仅预估)

        公式: 日补贴 = balance × 3% / 365

        Raises:
            KeyError: 钱包未开通
        """
        account = await self.wallet_repo.get_account(user_id)
        if not account:
            raise KeyError(f"用户 {user_id} 未开通钱包")

        balance = float(account.get("balance", 0))
        daily = _calc_daily_interest(balance)
        monthly = round(daily * 30, 2)  # 预估月收益(30 天)
        yearly = round(balance * CURRENT_ANNUAL_RATE, 2)

        return {
            "success": True,
            "userId": user_id,
            "balance": balance,
            "annualRate": CURRENT_ANNUAL_RATE,
            "dailyInterest": daily,
            "monthlyEstimate": monthly,
            "yearlyEstimate": yearly,
            "logs": [{"step": "收益预估", "level": "INFO",
                      "msg": f"日补贴 ¥{daily:.2f}(年化 3%)"}],
        }

    async def settle_monthly_interest(self, user_id) -> dict:
        """月末结付: 将 pending_interest 入账到 balance

        生产环境应由定时任务每日累计 pending_interest, 月末统一入账。
        此处简化为: 读取 pending_interest → 入账 → 清零。

        Raises:
            KeyError: 钱包未开通
            ValueError: 无待入账收益
        """
        async with get_lock(f"wallet:{user_id}"):
            account = await self.wallet_repo.get_account(user_id)
            if not account:
                raise KeyError(f"用户 {user_id} 未开通钱包")

            pending = float(account.get("pendingInterest", 0))
            if pending <= 0:
                raise ValueError("无待入账营销补贴")

            # 1. 入账到余额
            new_balance = await self.wallet_repo.add_balance(user_id, pending)
            # 2. 累计收益 + 清零 pending
            await self.wallet_repo.update_account_fields(user_id, {
                "totalInterest": float(account.get("totalInterest", 0)) + pending,
                "pendingInterest": 0.0,
                "updatedAt": ts(),
            })
            # 3. 交易流水
            tx_no = await self.wallet_repo.next_tx_no()
            await self.wallet_repo.save_transaction({
                "txNo": tx_no,
                "userId": user_id,
                "type": "interest",
                "direction": "IN",
                "amount": pending,
                "balanceAfter": new_balance,
                "payChannel": "",
                "orderId": "",
                "depositNo": "",
                "withdrawNo": "",
                "status": "success",
                "description": f"月度营销补贴入账 ¥{pending:.2f}",
                "createdAt": ts(),
            })

            logger.info("wallet_interest_settled user_id=%r amount=%.2f",
                        user_id, pending)
            return {
                "success": True,
                "userId": user_id,
                "txNo": tx_no,
                "amount": pending,
                "balanceAfter": new_balance,
                "logs": [{"step": "月度结付", "level": "INFO",
                          "msg": f"营销补贴 ¥{pending:.2f} 已入账"}],
            }

    # ============================================================
    # P0: 风控接口(查询待审核/冻结金额)
    # ============================================================

    async def list_pending_withdrawals(self, limit: int = 100) -> dict:
        """列出待审核提现(管理端审批用)"""
        wds = await self.wallet_repo.list_pending_withdrawals(limit=limit)
        return {
            "success": True,
            "count": len(wds),
            "withdrawals": wds,
            "logs": [],
        }

    # ============================================================
    # P1: 活期转定期
    # ============================================================

    async def transfer_to_regular(self, user_id, amount: float,
                                    period: int) -> dict:
        """活期转定期: 余额扣减 + 创建定期记录 + LPR 合规校验

        Args:
            user_id: 用户ID
            amount: 预付款金额
            period: 存期(月) 3/6/12/24

        Raises:
            KeyError: 钱包未开通
            ValueError: 金额/存期非法 / 余额不足 / LPR 超限
        """
        tier = DEPOSIT_TIERS.get(period)
        if not tier:
            raise ValueError(f"存期非法: {period} 月(须为 3/6/12/24)")
        if amount < tier["min"]:
            raise ValueError(f"存入金额不足: {period} 月须 ≥ ¥{tier['min']}")
        if amount <= 0:
            raise ValueError("存入金额必须大于 0")

        async with get_lock(f"wallet:{user_id}"):
            account = await self.wallet_repo.get_account(user_id)
            if not account:
                raise KeyError(f"用户 {user_id} 未开通钱包")
            if account.get("status") != STATUS_ACTIVE:
                raise ValueError("钱包状态异常, 无法转定期")

            balance = float(account.get("balance", 0))
            if balance < amount:
                raise ValueError(f"余额不足: 当前 ¥{balance:.2f}, 需 ¥{amount:.2f}")

            # 1. 计算预计营销补贴
            interest, annual_rate = _calc_regular_interest(amount, period)
            # 2. 匹配奖品
            reward_match = _match_reward(amount, period)
            reward_name = reward_match[0] if reward_match else ""
            reward_value = reward_match[1] if reward_match else 0.0
            # 3. LPR 合规校验
            compliance = _check_lpr_compliance(amount, interest, reward_value)
            if not compliance["compliant"]:
                # 降档处理: 仅保留营销补贴, 取消奖品
                reward_name = ""
                reward_value = 0.0
                compliance["action"] = "degraded"

            # 4. 余额扣减
            new_balance = await self.wallet_repo.add_balance(user_id, -amount)

            # 5. 创建定期记录
            deposit_no = await self.wallet_repo.next_deposit_no()
            start_date = _today_str()
            end_date = _add_months(start_date, period)
            now = ts()
            await self.wallet_repo.save_deposit({
                "depositNo": deposit_no,
                "userId": user_id,
                "amount": amount,
                "period": period,
                "annualRate": annual_rate,
                "expectedInterest": interest,
                "rewardType": reward_name,
                "rewardValue": reward_value,
                "startDate": start_date,
                "endDate": end_date,
                "status": "active",
                "autoRenew": False,
                "settledAt": "",
                "settledInterest": 0.0,
                "createdAt": now,
                "updatedAt": now,
            })

            # 6. 交易流水
            tx_no = await self.wallet_repo.next_tx_no()
            await self.wallet_repo.save_transaction({
                "txNo": tx_no,
                "userId": user_id,
                "type": "transfer_regular",
                "direction": "OUT",
                "amount": amount,
                "balanceAfter": new_balance,
                "payChannel": "",
                "orderId": "",
                "depositNo": deposit_no,
                "withdrawNo": "",
                "status": "success",
                "description": f"转定期 ¥{amount:.2f}({period} 个月, 年化 {annual_rate*100:.1f}%)",
                "createdAt": now,
            })

            logs = [
                {"step": "转定期", "level": "INFO",
                 "msg": f"¥{amount:.2f} → {period} 个月定期"},
                {"step": "预计补贴", "level": "INFO",
                 "msg": f"营销补贴 ¥{interest:.2f}(年化 {annual_rate*100:.1f}%)"},
            ]
            if reward_name:
                logs.append({"step": "奖品", "level": "INFO",
                             "msg": f"到期可领: {reward_name}(¥{reward_value:.2f})"})
            logs.append({"step": "LPR 合规", "level": "INFO",
                         "msg": f"综合收益率 {compliance['actualRate']*100:.2f}% ≤ {compliance['ceiling']*100:.1f}%({'通过' if compliance['compliant'] else '已降档'})"})

            logger.info("wallet_transfer_regular user_id=%r amount=%.2f period=%d dp=%s",
                        user_id, amount, period, deposit_no)
            return {
                "success": True,
                "userId": user_id,
                "depositNo": deposit_no,
                "txNo": tx_no,
                "amount": amount,
                "period": period,
                "annualRate": annual_rate,
                "expectedInterest": interest,
                "rewardName": reward_name,
                "rewardValue": reward_value,
                "startDate": start_date,
                "endDate": end_date,
                "balanceAfter": new_balance,
                "compliance": compliance,
                "logs": logs,
            }

    # ============================================================
    # P1: 定期到期取出 + 提前取出
    # ============================================================

    async def settle_deposit(self, user_id, deposit_no) -> dict:
        """定期到期取出: 本金 + 营销补贴入账, 奖品转为可领取

        前置条件: status=active 或 matured

        Raises:
            KeyError: 定期记录不存在
            ValueError: 状态非法 / 未到期
        """
        async with get_lock(f"wallet:deposit:{deposit_no}"):
            deposit = await self.wallet_repo.get_deposit(deposit_no)
            if not deposit:
                raise KeyError(f"定期 {deposit_no} 不存在")
            if deposit["status"] not in ("active", "matured"):
                raise ValueError(f"定期状态非 active/matured(当前: {deposit['status']})")
            if deposit["userId"] != user_id:
                raise ValueError("无权操作他人定期")

            # 到期校验
            today = _today_str()
            if deposit["status"] == "active" and today < deposit["endDate"]:
                raise ValueError(f"定期未到期(到期日: {deposit['endDate']})")

            amount = float(deposit["amount"])
            interest = float(deposit["expectedInterest"])

            async with get_lock(f"wallet:{user_id}"):
                # 1. 本金 + 营销补贴入账
                new_balance = await self.wallet_repo.add_balance(user_id, amount + interest)
                # 2. 累计收益累加
                account = await self.wallet_repo.get_account(user_id)
                await self.wallet_repo.update_account_fields(user_id, {
                    "totalInterest": float(account.get("totalInterest", 0)) + interest,
                    "updatedAt": ts(),
                })

                # 3. 定期状态更新
                await self.wallet_repo.update_deposit_fields(deposit_no, {
                    "status": "settled",
                    "settledAt": ts(),
                    "settledInterest": interest,
                    "updatedAt": ts(),
                })

                # 4. 奖品转可领取
                reward_name = deposit.get("rewardType", "")
                reward_value = float(deposit.get("rewardValue", 0))
                reward_no = ""
                if reward_name and reward_value > 0:
                    reward_no = await self.wallet_repo.next_reward_no()
                    claim_deadline = _now_shanghai() + timedelta(days=REWARD_CLAIM_DAYS)
                    await self.wallet_repo.save_reward({
                        "rewardNo": reward_no,
                        "userId": user_id,
                        "depositNo": deposit_no,
                        "rewardName": reward_name,
                        "rewardItems": [],
                        "rewardValue": reward_value,
                        "addressId": 0,
                        "waybillNo": "",
                        "status": "claimable",
                        "claimDeadline": claim_deadline.isoformat(),
                        "claimedAt": "",
                        "shippedAt": "",
                        "signedAt": "",
                        "createdAt": ts(),
                        "updatedAt": ts(),
                    })
                    await self.wallet_repo.update_account_fields(user_id, {
                        "totalReward": float(account.get("totalReward", 0)) + reward_value,
                    })

                # 5. 交易流水(本金入账)
                tx_no = await self.wallet_repo.next_tx_no()
                await self.wallet_repo.save_transaction({
                    "txNo": tx_no,
                    "userId": user_id,
                    "type": "interest",
                    "direction": "IN",
                    "amount": amount + interest,
                    "balanceAfter": new_balance,
                    "payChannel": "",
                    "orderId": "",
                    "depositNo": deposit_no,
                    "withdrawNo": "",
                    "status": "success",
                    "description": f"定期到期取出 ¥{amount:.2f} + 补贴 ¥{interest:.2f}",
                    "createdAt": ts(),
                })

            logs = [
                {"step": "定期到期", "level": "INFO",
                 "msg": f"本金 ¥{amount:.2f} + 补贴 ¥{interest:.2f} 已入账"},
            ]
            if reward_no:
                logs.append({"step": "奖品可领", "level": "INFO",
                             "msg": f"奖品 {reward_name} 已可领取(编号 {reward_no})"})

            logger.info("wallet_deposit_settled user_id=%r dp=%s amount=%.2f interest=%.2f",
                        user_id, deposit_no, amount, interest)
            return {
                "success": True,
                "userId": user_id,
                "depositNo": deposit_no,
                "txNo": tx_no,
                "amount": amount,
                "interest": interest,
                "rewardNo": reward_no,
                "rewardName": reward_name,
                "rewardValue": reward_value,
                "balanceAfter": new_balance,
                "logs": logs,
            }

    async def early_settle_deposit(self, user_id, deposit_no) -> dict:
        """定期提前取出: 损失营销补贴 + 奖品, 收 1% 手续费

        规则:
            - 本金全额返还
            - 营销补贴清零(损失)
            - 奖品作废(损失)
            - 收 1% 手续费(从本金扣除)

        Raises:
            KeyError: 定期记录不存在
            ValueError: 状态非法
        """
    # 防重复结清
        async with get_lock(f"wallet:deposit:{deposit_no}"):
            deposit = await self.wallet_repo.get_deposit(deposit_no)
            if not deposit:
                raise KeyError(f"定期 {deposit_no} 不存在")
            if deposit["status"] != "active":
                raise ValueError(f"定期状态非 active(当前: {deposit['status']})")
            if deposit["userId"] != user_id:
                raise ValueError("无权操作他人定期")

            amount = float(deposit["amount"])
            expected_interest = float(deposit.get("expectedInterest", 0))
            reward_value = float(deposit.get("rewardValue", 0))

            # 手续费 1%
            fee = round(amount * WITHDRAW_REGULAR_EARLY_FEE_RATE, 2)
            actual = round(amount - fee, 2)

            async with get_lock(f"wallet:{user_id}"):
                # 1. 实际到账金额入账
                new_balance = await self.wallet_repo.add_balance(user_id, actual)

                # 2. 定期状态更新(标记损失)
                await self.wallet_repo.update_deposit_fields(deposit_no, {
                    "status": "early_settled",
                    "settledAt": ts(),
                    "settledInterest": 0.0,  # 提前取出无补贴
                    "updatedAt": ts(),
                })

                # 3. 创建提现单(记录手续费)
                withdraw_no = await self.wallet_repo.next_withdraw_no()
                await self.wallet_repo.save_withdrawal({
                    "withdrawNo": withdraw_no,
                    "userId": user_id,
                    "amount": amount,
                    "fee": fee,
                    "actualAmount": actual,
                    "source": "regular_early",
                    "depositNo": deposit_no,
                    "lossInterest": expected_interest,
                    "lossReward": reward_value > 0,
                    "payChannel": "wallet",
                    "bankAccount": "",
                    "status": "paid",  # 直接入账到钱包, 无需审核
                    "auditor": "系统",
                    "auditRemark": "定期提前取出, 自动完成",
                    "payTime": ts(),
                    "createdAt": ts(),
                    "updatedAt": ts(),
                })

                # 4. 交易流水(入账)
                tx_no = await self.wallet_repo.next_tx_no()
                await self.wallet_repo.save_transaction({
                    "txNo": tx_no,
                    "userId": user_id,
                    "type": "interest",
                    "direction": "IN",
                    "amount": actual,
                    "balanceAfter": new_balance,
                    "payChannel": "",
                    "orderId": "",
                    "depositNo": deposit_no,
                    "withdrawNo": withdraw_no,
                    "status": "success",
                    "description": f"定期提前取出 ¥{actual:.2f}(扣手续费 ¥{fee:.2f})",
                    "createdAt": ts(),
                })

            logs = [
                {"step": "提前取出", "level": "WARN",
                 "msg": f"本金 ¥{amount:.2f}, 手续费 ¥{fee:.2f}, 到账 ¥{actual:.2f}"},
                {"step": "损失营销补贴", "level": "WARN",
                 "msg": f"损失补贴 ¥{expected_interest:.2f}"},
            ]
            if reward_value > 0:
                logs.append({"step": "损失奖品", "level": "WARN",
                             "msg": f"损失奖品(价值 ¥{reward_value:.2f})"})

            logger.info("wallet_deposit_early_settled user_id=%r dp=%s actual=%.2f fee=%.2f",
                        user_id, deposit_no, actual, fee)
            return {
                "success": True,
                "userId": user_id,
                "depositNo": deposit_no,
                "withdrawNo": withdraw_no,
                "txNo": tx_no,
                "amount": amount,
                "fee": fee,
                "actualAmount": actual,
                "lossInterest": expected_interest,
                "lossReward": reward_value > 0,
                "balanceAfter": new_balance,
                "logs": logs,
            }

    # ============================================================
    # P1: 奖品领取
    # ============================================================

    async def list_rewards(self, user_id, status: str = None,
                            limit: int = 50) -> dict:
        """查询奖品列表(可按 status 筛选)

        Raises:
            KeyError: 钱包未开通
        """
        account = await self.wallet_repo.get_account(user_id)
        if not account:
            raise KeyError(f"用户 {user_id} 未开通钱包")

        rewards = await self.wallet_repo.list_rewards(
            user_id, status=status, limit=limit)
        return {
            "success": True,
            "userId": user_id,
            "count": len(rewards),
            "rewards": rewards,
            "logs": [],
        }

    async def claim_reward(self, user_id, reward_no,
                            address_id: int = 0) -> dict:
        """领取奖品: 状态 claimable → claimed → 待发货

        Args:
            user_id: 用户ID
            reward_no: 奖品编号
            address_id: 收货地址ID(关联 member_addresses, 0 表示稍后填写)

        Raises:
            KeyError: 奖品不存在
            ValueError: 状态非法 / 已过期 / 无权操作
        """
        async with get_lock(f"wallet:reward:{reward_no}"):
            reward = await self.wallet_repo.get_reward(reward_no)
            if not reward:
                raise KeyError(f"奖品 {reward_no} 不存在")
            if reward["userId"] != user_id:
                raise ValueError("无权操作他人奖品")
            if reward["status"] != "claimable":
                raise ValueError(f"奖品状态非 claimable(当前: {reward['status']})")

            # 过期校验
            deadline = reward.get("claimDeadline", "")
            if deadline and _now_shanghai().isoformat() > deadline:
                await self.wallet_repo.update_reward_fields(reward_no, {
                    "status": "expired",
                    "updatedAt": ts(),
                })
                raise ValueError(f"奖品已过期(截止 {deadline})")

            # 领取: 状态 → claimed, 等待发货
            await self.wallet_repo.update_reward_fields(reward_no, {
                "status": "claimed",
                "addressId": address_id,
                "claimedAt": ts(),
                "updatedAt": ts(),
            })

            logger.info("wallet_reward_claimed user_id=%r rw=%s",
                        user_id, reward_no)
            return {
                "success": True,
                "userId": user_id,
                "rewardNo": reward_no,
                "rewardName": reward.get("rewardName", ""),
                "rewardValue": float(reward.get("rewardValue", 0)),
                "addressId": address_id,
                "status": "claimed",
                "logs": [
                    {"step": "奖品领取", "level": "INFO",
                     "msg": f"{reward.get('rewardName', '')} 已领取, 等待发货"},
                ],
            }

    async def ship_reward(self, reward_no, waybill_no: str) -> dict:
        """奖品发货: 状态 claimed → shipped(管理端调用)

        Raises:
            KeyError: 奖品不存在
            ValueError: 状态非法
        """
        async with get_lock(f"wallet:reward:{reward_no}"):
            reward = await self.wallet_repo.get_reward(reward_no)
            if not reward:
                raise KeyError(f"奖品 {reward_no} 不存在")
            if reward["status"] != "claimed":
                raise ValueError(f"奖品状态非 claimed(当前: {reward['status']})")

            await self.wallet_repo.update_reward_fields(reward_no, {
                "status": "shipped",
                "waybillNo": waybill_no,
                "shippedAt": ts(),
                "updatedAt": ts(),
            })

            logger.info("wallet_reward_shipped rw=%s waybill=%s",
                        reward_no, waybill_no)
            return {
                "success": True,
                "rewardNo": reward_no,
                "waybillNo": waybill_no,
                "status": "shipped",
                "logs": [{"step": "奖品发货", "level": "INFO",
                          "msg": f"运单号 {waybill_no}"}],
            }

    async def sign_reward(self, reward_no) -> dict:
        """奖品签收: 状态 shipped → signed(用户确认)

        Raises:
            KeyError: 奖品不存在
            ValueError: 状态非法
        """
        async with get_lock(f"wallet:reward:{reward_no}"):
            reward = await self.wallet_repo.get_reward(reward_no)
            if not reward:
                raise KeyError(f"奖品 {reward_no} 不存在")
            if reward["status"] != "shipped":
                raise ValueError(f"奖品状态非 shipped(当前: {reward['status']})")

            await self.wallet_repo.update_reward_fields(reward_no, {
                "status": "signed",
                "signedAt": ts(),
                "updatedAt": ts(),
            })

            logger.info("wallet_reward_signed rw=%s", reward_no)
            return {
                "success": True,
                "rewardNo": reward_no,
                "status": "signed",
                "logs": [{"step": "奖品签收", "level": "INFO",
                          "msg": "签收完成"}],
            }

    # ============================================================
    # P1: 定期列表查询
    # ============================================================

    async def list_deposits(self, user_id, status: str = None,
                             limit: int = 50) -> dict:
        """查询用户定期列表

        Raises:
            KeyError: 钱包未开通
        """
        account = await self.wallet_repo.get_account(user_id)
        if not account:
            raise KeyError(f"用户 {user_id} 未开通钱包")

        deposits = await self.wallet_repo.list_deposits(
            user_id, status=status, limit=limit)
        # 附加进度信息
        today = _today_str()
        for d in deposits:
            if d.get("status") == "active" and d.get("endDate", "") <= today:
                # 已到期但未标记, 附加 matured 标记(不修改存储, 仅返回)
                d["matured"] = True
        return {
            "success": True,
            "userId": user_id,
            "count": len(deposits),
            "deposits": deposits,
            "logs": [],
        }
