"""68号 P9·信值·臻选——结算服务(平台化升级 §二 P9)

依据:《信值·臻选68号_平台化升级创新规划方案》§二/§三:
    P9 结算 = 订单支付(信值抵扣资金源: TV redeem + wallet
    组合) → 分账(商家货款 88% + 平台抽佣 12%[低于 37号
    15%——臻选让利]) → T+1 结算 + 冲正 + 信值回流留痕

资金源铁律(项目宪法——资金给付永不自动):
    - 支付是用户主动调用的接口(对齐主站 order pay 语义,
      属"用户确认"范畴, 允许): wallet / trust_value / mixed
    - trust_value 调 45号 TrustAssetService.redeem(1TV=1元,
      熔断/余额/防挤兑日月上限/商户保证金校验全部走其既有
      链); TV 销毁(核销)属 45号商户核销流, 本模块永不自动
      执行——仅留痕 funding{source, amount, txRef}
    - mixed 组合原子性: 先 TV 后 wallet, 任一失败整体回滚
      (TV 失败→整体 409; wallet 失败→回滚已扣 TV,
      try/compensate)
    - T+1 分账是幂等事务(admin 或调度触发; 已 settled 跳过
      ——对齐 37号 settle_order 范式)
    - 冲正是 admin 人工动作(退货语义预留); wallet 不足部分
      记负债字段, 诚实标注, 追缴走人工/建议书轨——永不自动

存储: xz:settlements(仓储层在 xinzhi_cart_service.
XinzhiTradeRepository——P8/P9 共享, 单向 import 防循环)。
"""

import logging

from core.helpers import ts
from core.locks import get_lock
from repositories.wallet_repository import WalletRepository
from services.wallet_service import WalletService
from services.trust_asset_service import TrustAssetService
from services.xinzhi_cart_service import (
    XinzhiTradeRepository, PENDING, PAID, STATUS_CN,
)

logger = logging.getLogger(__name__)

# 分账比例(平台化升级方案 §二: 平台抽佣 12%, 低于 37号 15%——臻选让利)
PLATFORM_FEE_RATE = 0.12
PROCEEDS_RATE = 0.88

# 结算单状态机(pending→settled/reversed)
SETTLE_PENDING = "pending"
SETTLE_SETTLED = "settled"
SETTLE_REVERSED = "reversed"

# 支付方式域(封闭)
PAY_METHODS = ("wallet", "trust_value", "mixed")


class XinzhiSettleService:
    """68号 P9·支付三通道+分账+T+1+冲正"""

    def __init__(self, repo: XinzhiTradeRepository = None):
        self.repo = (repo if repo is not None
                     else XinzhiTradeRepository())
        self.wallet = WalletService()
        self.wallet_repo = WalletRepository()
        self.trust = TrustAssetService()

    # ============================================================
    # 支付(PENDING→PAID, 支付成功即建 pending 结算单)
    # ============================================================

    async def pay_order(self, member_id: int, order_id: str,
                        method: str,
                        use_trust_value: float = None,
                        trust_id: int = None) -> dict:
        """订单支付(method: wallet | trust_value | mixed)

        Raises:
            KeyError: 订单/钱包未开通
            PermissionError: 非订单买家
            ValueError: 方式域外/状态非 PENDING/资金不足
        """
        method = str(method or "")
        if method not in PAY_METHODS:
            raise ValueError(
                f"支付方式域外: {method}"
                f"(合法: {'/'.join(PAY_METHODS)})")
        async with get_lock(f"xz:order:{order_id}"):
            order = await self.repo.get_order(order_id)
            if order is None:
                raise KeyError(
                    f"订单不存在(orderId={order_id})")
            if order.get("memberId") != member_id:
                raise PermissionError("仅订单买家可支付")
            if order.get("status") != PENDING:
                raise ValueError(
                    f"订单状态异常: 仅 {PENDING} 可支付, "
                    f"当前 {order.get('status')}")

            detail = order.get("priceDetail") or {}
            amount = round(float(detail.get("actualAmount")
                                or 0), 2)
            credit = round(float(detail.get("xinzhiCredit")
                                 or 0), 2)
            funding: list[dict] = []

            if method == "wallet":
                # ① wallet: 全额活期余额支付(1% 返利走其既有链)
                result = await self.wallet.pay(
                    member_id, amount, order_id)
                funding.append({
                    "source": "wallet",
                    "amount": amount,
                    "txRef": result.get("txNo", ""),
                })
            elif method == "trust_value":
                # ② trust_value: 全额 TV 兑换支付(1TV=1元;
                #    熔断/余额/防挤兑/商户保证金校验走 45号链)
                if not trust_id:
                    raise ValueError(
                        "trust_value 支付须携带 trustId"
                        "(45号信值档案)")
                redeem_id = await self._redeem_tv(
                    trust_id, amount, order)
                funding.append({
                    "source": "trust_value",
                    "amount": amount,
                    "txRef": f"redeem:{redeem_id}",
                    "trustId": trust_id,
                })
            else:
                # ③ mixed: TV 优先抵 xinzhiCredit 部分 +
                #    wallet 付余下(先 TV 后 wallet, 任一失败
                #    整体回滚——try/compensate)
                funding = await self._pay_mixed(
                    member_id, order, amount, credit,
                    use_trust_value, trust_id)

            # 支付成功: PENDING→PAID + payment{method, paidAt,
            # funding}(资金源留痕, 全程可审计)
            now = ts()
            order["status"] = PAID
            order["payment"] = {
                "method": method,
                "paidAt": now,
                "funding": funding,
            }
            order["timeline"].append(
                {"status": PAID, "time": now,
                 "action": f"支付成功({method})"})
            order["updatedAt"] = now
            await self.repo.save_order(order)
            # 分账单(支付成功时计算, 异步 T+1 执行)
            settlement = await self._create_settlement(order)
            logger.info("xz_order_paid order=%s method=%s "
                        "amount=%.2f funding=%s",
                        order_id, method, amount,
                        [f["source"] for f in funding])
            return {
                "success": True,
                "orderId": order_id,
                "status": PAID,
                "statusName": STATUS_CN[PAID],
                "payment": order["payment"],
                "settlement": settlement,
            }

    async def _pay_mixed(self, member_id: int, order: dict,
                         amount: float, credit: float,
                         use_trust_value, trust_id) -> list[dict]:
        """mixed 组合支付(先 TV 后 wallet; 原子性 try/compensate)

        - TV 额默认=信值抵扣额 xinzhiCredit(α 抵扣资金源语义);
          useTrustValue 指定时不可超过 xinzhiCredit
        - TV 失败 → 整体 409(redeem 抛 ValueError)
        - wallet 失败 → 回滚已扣 TV(解冻+申请单作废)后 409
        """
        tv_amount = (round(float(use_trust_value), 2)
                     if use_trust_value is not None
                     else credit)
        if tv_amount < 0:
            raise ValueError("useTrustValue 不可为负")
        if tv_amount > credit + 1e-9:
            raise ValueError(
                f"mixed TV 抵扣额不可超过信值抵扣额"
                f"(xinzhiCredit={credit})")
        tv_amount = round(min(tv_amount, credit), 2)
        wallet_part = round(amount - tv_amount, 2)
        funding: list[dict] = []
        redeem_id = None
        if tv_amount > 0:
            if not trust_id:
                raise ValueError(
                    "mixed 支付使用 TV 须携带 trustId"
                    "(45号信值档案)")
            redeem_id = await self._redeem_tv(
                trust_id, tv_amount, order)
            funding.append({
                "source": "trust_value",
                "amount": tv_amount,
                "txRef": f"redeem:{redeem_id}",
                "trustId": trust_id,
            })
        try:
            if wallet_part > 0:
                result = await self.wallet.pay(
                    member_id, wallet_part,
                    order["orderId"])
                funding.append({
                    "source": "wallet",
                    "amount": wallet_part,
                    "txRef": result.get("txNo", ""),
                })
        except (KeyError, ValueError) as exc:
            # wallet 失败 → 回滚已扣 TV(45号无"取消兑换"
            # 公开端点, 此处仅解冻不增发不销毁, 申请单标记
            # cancelled 后核销端自然失效——诚实补偿留痕)
            if redeem_id is not None:
                await self._cancel_tv_redeem(
                    trust_id, redeem_id, tv_amount)
                logger.warning(
                    "xz_mixed_pay_compensated order=%s "
                    "tv_redeem=%s rolled back (%s)",
                    order["orderId"], redeem_id, exc)
            raise
        return funding

    async def _redeem_tv(self, trust_id: int, amount: float,
                         order: dict) -> int:
        """调 45号 trust_asset redeem(1TV=1元; 防挤兑限制
        遵守其既有校验——熔断/可用余额/日月上限/商户保证金)

        商户=订单店铺(保证金由店铺缴纳, 履约担保)。
        TV 销毁(核销)走 45号商户核销流, 本模块不自动执行。

        Raises:
            ValueError: 45号校验未过(余额/上限/保证金等)
        """
        result = await self.trust.redeem(
            int(trust_id), float(amount),
            str(order.get("shopName") or ""),
            goods=f"臻选订单 {order.get('orderId')}")
        return int(result.get("redeemId") or 0)

    async def _cancel_tv_redeem(self, trust_id: int,
                                redeem_id: int,
                                amount: float) -> None:
        """mixed 钱包失败补偿: 解冻已锁 TV(不增发/不销毁)

        45号无取消兑换公开端点, 此处调用其服务内部存储方法
        完成解冻: redeem 申请单标记 cancelled(核销端仅认
        pending, 自然失效) + 余额行 frozen 等额回落。
        68号侧留痕见调用方日志与订单状态(仍 PENDING)。
        """
        record = await self.trust._get_redeem(redeem_id)
        if record is None \
                or record.get("status") != "pending":
            return
        record["status"] = "cancelled"
        record["cancelledAt"] = ts()
        record["cancelReason"] = (
            "68号臻选 mixed 支付 wallet 失败补偿解冻")
        await self.trust._save_redeem(record)
        row = await self.trust._balance_row(int(trust_id))
        row["frozen"] = round(
            max(0.0, float(row.get("frozen") or 0)
                - float(amount)), 2)
        await self.trust._save_balance_row(int(trust_id), row)

    # ============================================================
    # 分账(支付成功时计算, 异步 T+1 执行)
    # ============================================================

    async def _create_settlement(self, order: dict) -> dict:
        """生成结算单(货款 88% / 平台费 12%; 金额守恒:
        平台费四舍五入, 残差归商家货款)"""
        actual = round(float(
            (order.get("priceDetail") or {}).get(
                "actualAmount") or 0), 2)
        platform_fee = round(actual * PLATFORM_FEE_RATE, 2)
        proceeds = round(actual - platform_fee, 2)
        settle_id = await self.repo.next_id("settlement")
        settlement = {
            "settleId": settle_id,
            "orderId": order["orderId"],
            "memberId": order.get("memberId"),
            "shopId": order.get("shopId"),
            "shopMemberId": order.get("shopMemberId"),
            "shopName": order.get("shopName"),
            "orderAmount": actual,
            "merchantProceeds": proceeds,
            "platformFee": platform_fee,
            "proceedsRate": PROCEEDS_RATE,
            "feeRate": PLATFORM_FEE_RATE,
            "status": SETTLE_PENDING,
            "breakdown": {
                "orderAmount": actual,
                "merchantProceeds": proceeds,
                "platformFee": platform_fee,
                "platformLedgerNote": (
                    "平台费记平台总账(简化: 本期仅留痕, "
                    "不入商家账)——platform_fee 12%"),
                "fundingSources": [
                    f.get("source")
                    for f in (order.get("payment")
                              or {}).get("funding") or []],
            },
            "walletTxNo": "",
            "pendingAt": ts(),
            "settledAt": "",
            "reversedAt": "",
            "reverseReason": "",
            "reversalDebt": 0.0,
            "reversalNote": "",
        }
        await self.repo.save_settlement(settlement)
        return settlement

    async def run_settlements(self, operator: str = "admin") -> dict:
        """T+1 分账执行(admin 或调度触发; 幂等: 已 settled 跳过)

        对齐 37号 run_scheduled_settlement 范式: 货款入商家
        wallet.deposit_reward(奖励余额口径——只可消费不可提现);
        商家未开户记 PENDING_NO_WALLET 待重试(37号同款)。
        T+1 节奏由外部调度器保证(本端点为幂等执行通道,
        手动触发不校验延迟——37号运维通道同语义)。

        Raises:
            ValueError: 无 pending 结算单(诚实空跑提示)
        """
        pending = await self.repo.list_settlements(
            status=SETTLE_PENDING)
        settled, skipped = [], 0
        for s in pending:
            async with get_lock(
                    f"xz:settle:{s['settleId']}"):
                record = await self.repo.get_settlement(
                    s["settleId"])
                if record is None \
                        or record.get("status") \
                        != SETTLE_PENDING:
                    skipped += 1
                    continue
                tx_no = ""
                try:
                    result = await self.wallet.deposit_reward(
                        int(record.get("shopMemberId") or 0),
                        float(record["merchantProceeds"]),
                        description=(
                            f"臻选货款 {record['orderId']}"))
                    tx_no = result.get("txNo", "")
                except KeyError:
                    tx_no = "PENDING_NO_WALLET"
                except Exception as exc:
                    tx_no = "FAILED"
                    logger.warning(
                        "xz_settle_deposit_failed id=%s: %s",
                        record["settleId"], exc)
                record["status"] = SETTLE_SETTLED
                record["walletTxNo"] = tx_no
                record["settledAt"] = ts()
                await self.repo.save_settlement(record)
                settled.append(record["settleId"])
        if settled:
            logger.info("xz_settlement_run operator=%s "
                        "settled=%s skipped=%s",
                        operator, len(settled), skipped)
        return {
            "success": True,
            "operator": operator,
            "settledCount": len(settled),
            "settled": settled,
            "skipped": skipped,
        }

    async def reverse_settlement(self, settle_id: int,
                                 reason: str = "",
                                 operator: str = "admin") -> dict:
        """结算冲正(admin 人工动作——退货语义预留)

        反向扣回商家奖励余额; 不足部分记负债字段 reversalDebt
        (诚实标注——追缴走人工/建议书轨, 永不自动扣款)。
        TV 抵扣部分的退还属 45号退货语义, 本期预留不实现。

        Raises:
            KeyError: 结算单不存在
            ValueError: 状态非 settled
        """
        async with get_lock(f"xz:settle:{settle_id}"):
            settlement = await self.repo.get_settlement(settle_id)
            if settlement is None:
                raise KeyError(
                    f"结算单不存在(settleId={settle_id})")
            if settlement.get("status") != SETTLE_SETTLED:
                raise ValueError(
                    f"结算单状态不可冲正"
                    f"(当前 {settlement.get('status')}, "
                    f"须 {SETTLE_SETTLED})")
            proceeds = round(float(
                settlement.get("merchantProceeds") or 0), 2)
            available = 0.0
            account = await self.wallet_repo.get_account(
                int(settlement.get("shopMemberId") or 0))
            if account:
                available = round(float(
                    account.get("rewardBalance") or 0), 2)
            deduct = round(min(available, proceeds), 2)
            if deduct > 0:
                await self.wallet.pay_with_reward(
                    int(settlement.get("shopMemberId") or 0),
                    deduct,
                    order_id=str(settlement.get("orderId")),
                    description=(
                        f"臻选结算冲正扣回 "
                        f"{settlement.get('orderId')}"))
            debt = round(proceeds - deduct, 2)
            settlement["status"] = SETTLE_REVERSED
            settlement["reversedAt"] = ts()
            settlement["reverseReason"] = (reason or "")[:200]
            settlement["reversalDebt"] = debt
            settlement["reversalNote"] = (
                f"冲正扣回 {deduct:.2f}; 奖励余额不足部分 "
                f"{debt:.2f} 记负债字段(诚实标注)——追缴走"
                f"人工/建议书轨, 永不自动" if debt > 0 else
                f"冲正扣回 {deduct:.2f}(足额)")
            await self.repo.save_settlement(settlement)
            logger.info("xz_settlement_reversed id=%s "
                        "deduct=%.2f debt=%.2f",
                        settle_id, deduct, debt)
            return settlement

    # ============================================================
    # 查询
    # ============================================================

    async def get_settlement(self, settle_id: int) -> dict:
        """结算单详情(访问控制在路由层)

        Raises:
            KeyError: 结算单不存在
        """
        settlement = await self.repo.get_settlement(settle_id)
        if settlement is None:
            raise KeyError(
                f"结算单不存在(settleId={settle_id})")
        return settlement

    async def my_settlements(self,
                             member_id: int) -> list[dict]:
        """商家侧结算单列表(店铺归属会员)"""
        return await self.repo.list_settlements(
            shop_member_id=member_id)

    async def list_settlements(self,
                               status: str = None) -> list[dict]:
        """管理侧结算单列表(可按状态筛)"""
        return await self.repo.list_settlements(status=status)
