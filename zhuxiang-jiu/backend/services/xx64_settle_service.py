"""64号·信值兑换管理 支付结算引擎
(xx64_settle_service, P1)

计划(docs/64号_信值兑换商品服务AI智能管理模块
实施计划.md §4.1/§八 P1):
    ① 原子转移: 买扣卖增同事务落库
       (xx64_ledger 借贷对账——
        两笔同 entryId 关联, 失败
        整体回滚)
    ② 取消(解锁)——P0 已交付
    ③ 退款(反向转移——paid/settled
       → refunded)
    ④ 转移来源标记(R3——
       consumption_transfer 防洗钱)

铁律(计划 §二/§八):
    - R3 买扣卖增原子对(失败回滚)
    - R7 负值禁止(转移前余额复核)
    - 转移可追溯(ledger 借贷对+
      orderId 关联)

转移口径(P1):
    信值余额维护在 xx64 账本侧
    (ledger 累计口径)——与 45号
    档案对账(只读校验); 贷方先落,
    借方失败整体回滚。
"""

import logging
import os

from core.helpers import ts

from repositories.backend import (
    _k, get_redis_client, is_redis_mode,
)
from repositories.xx64_repository import (
    Xx64Repository,
)

logger = logging.getLogger("xx64_settle")

MODEL_VERSION = "v1-xx64-settle"

SCORER_ID = "value_exchange"

# 转移方向
DIRECTION_DEBIT = "debit"    # 买方扣减
DIRECTION_CREDIT = "credit"  # 卖方增加

# 转移类型
TRANSFER_PAY = "pay"              # 支付转移
TRANSFER_REFUND = "refund"        # 退款反向转移

# 支付互斥锁(asyncio 态——单进程
# 内存集合; Redis 态走 SET NX)
_PAY_LOCKS: set = set()

PAY_LOCK_TTL_SECONDS = 60


async def _claim_pay(order_id: int) -> bool:
    """支付占位(并发双花防护
    ——同订单同时仅一笔支付
    进行中)

    Redis 态: SET NX+TTL
    (跨进程互斥); asyncio 态:
    进程内集合。
    """
    oid = int(order_id)
    if is_redis_mode():
        client = await get_redis_client()
        ok = await client.set(
            _k("xx64", "paylock", oid),
            "1", nx=True,
            ex=PAY_LOCK_TTL_SECONDS)
        return bool(ok)
    if oid in _PAY_LOCKS:
        return False
    _PAY_LOCKS.add(oid)
    return True


async def _release_pay(order_id: int
                      ) -> None:
    """释放支付占位"""
    oid = int(order_id)
    if is_redis_mode():
        client = await get_redis_client()
        await client.delete(
            _k("xx64", "paylock", oid))
        return
    _PAY_LOCKS.discard(oid)


def current_mode() -> str:
    """模块开关(XX64_MODE——同底座)"""
    return os.environ.get(
        "XX64_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"XX64_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


class Xx64SettleService:
    """64号支付结算引擎(P1——
    原子转移+退款)"""

    def __init__(self):
        self.repo = Xx64Repository()

    # ============================================================
    # ① 支付(原子转移——买扣卖增)
    # ============================================================

    async def pay_order(self, order_id: int,
                        paid_by: str = "member"
                        ) -> dict:
        """订单支付(reserved→paid——
        买扣卖增原子转移)

        转移对(同一事务语义):
            借方: 买方 trustValue 扣减
            贷方: 卖方 trustValue 增加
        (两笔 ledger 记录同 entryId
         关联; 借方失败整体回滚)

        Raises:
            KeyError: 订单不存在
            ValueError: off 态/状态机
                拒绝/余额不足
        """
        require_active_mode()
        order = await self.repo.get_order(
            int(order_id))
        if not order:
            raise KeyError(
                f"订单 {order_id} 不存在")
        from services.xx64_registry import (
            ORDER_TRANSITIONS,
            TRANSFER_SOURCE,
        )
        if "paid" not in \
                ORDER_TRANSITIONS.get(
                    order.get("status"), ()):
            raise ValueError(
                f"订单状态 "
                f"{order.get('status')} "
                f"不可支付(须 reserved)")

        buyer_id = int(
            order.get("buyerId") or 0)
        seller_id = int(
            order.get("sellerId") or 0)
        trust_id = int(
            order.get("trustId") or 0)
        trust_value = float(
            order.get("trustValue") or 0)

        # P3 风控同步前置(ARB-HF+ARB-MA
        # ——assist 态 high 阻断当前笔
        # 可申诉秒级复核; shadow 仅观察)
        from services.xx64_risk_service import (
            Xx64RiskService,
        )
        gate = await Xx64RiskService() \
            .sync_gate_pay(order)
        if gate["blocked"]:
            raise ValueError(
                f"风控拦截(风险事件 "
                f"{gate['riskId']}"
                f"——多账号集中/高频套利"
                f"命中; 可经申诉通道"
                f"秒级复核)")

        # R7 余额复核(转移前)
        from services.xx64_service import (
            get_trust_balance,
        )
        bal = await get_trust_balance(
            trust_id)
        balance = float(bal["balance"])
        if balance < trust_value:
            raise ValueError(
                f"信值余额不足(余额 "
                f"{balance} < 需扣 "
                f"{trust_value}——"
                f"R7 负值禁止)")

        # 并发双花防护(支付占位——
        # 检查后转移前, 同订单同时
        # 仅一笔进行; Redis 态
        # SET NX 跨进程互斥)
        if not await _claim_pay(
                int(order_id)):
            raise ValueError(
                f"订单 {order_id} 支付"
                f"处理中(并发双花防护)")
        try:
            # 原子转移(借贷对——
            # 贷方先落, 借方失败则
            # 回滚删除贷方)
            entry_id = await \
                self.repo.next_entry_id()
            credit_record = {
                "entryId": entry_id,
                "orderId": int(order_id),
                "trustId": seller_id,
                "direction":
                    DIRECTION_CREDIT,
                "transferType":
                    TRANSFER_PAY,
                "amount": trust_value,
                "source":
                    TRANSFER_SOURCE,
                "balanceBefore": 0.0,
                "balanceAfter":
                    trust_value,
                "note": f"卖方收入(订单 "
                        f"{order_id} 信值"
                        f"支付 "
                        f"{trust_value})",
                "createdAt": ts(),
            }
            await self.repo.save_ledger(
                credit_record)
            try:
                debit_record = {
                    "entryId": entry_id,
                    "orderId":
                        int(order_id),
                    "trustId": trust_id,
                    "direction":
                        DIRECTION_DEBIT,
                    "transferType":
                        TRANSFER_PAY,
                    "amount":
                        -trust_value,
                    "source":
                        TRANSFER_SOURCE,
                    "balanceBefore":
                        balance,
                    "balanceAfter":
                        round(
                            balance
                            - trust_value,
                            2),
                    "note": f"买方支付(订单 "
                            f"{order_id} 信值"
                            f"扣减 "
                            f"{trust_value})",
                    "createdAt": ts(),
                }
                await self.repo \
                    .save_ledger(
                        debit_record)
            except Exception as exc:  # noqa: BLE001
                # 回滚: 标记贷方已回滚
                await self.repo \
                    .save_ledger({
                        **credit_record,
                        "rolledBack":
                            True,
                        "rollbackReason":
                            str(exc)[:100],
                    })
                raise ValueError(
                    f"转移失败已回滚: "
                    f"{str(exc)[:100]}") \
                    from exc

            # 订单状态迁移
            order.update({
                "status": "paid",
                "reserved": False,
                "paidBy": str(
                    paid_by
                    or "member"),
                "paidAt": ts(),
                "updatedAt": ts()})
            await self.repo.save_order(
                order, create=False)
            await self._track("settle", {
                "action": "pay",
                "orderId": int(order_id),
                "entryId": entry_id,
                "buyerTrustId":
                    trust_id,
                "sellerId": seller_id,
                "trustValue":
                    trust_value,
                "paidBy": paid_by,
            })
            return {
                "success": True,
                "orderId":
                    int(order_id),
                "status": "paid",
                "entryId": entry_id,
                "trustValue":
                    trust_value,
                "cashValue": order.get(
                    "cashValue"),
                "transfer": {
                    "buyerDebit":
                        -trust_value,
                    "sellerCredit":
                        trust_value,
                    "source":
                        TRANSFER_SOURCE,
                },
                "note": "支付完成——买扣卖增"
                        "原子转移(借贷对 "
                        f"entryId="
                        f"{entry_id})",
                "paidAt": order[
                    "paidAt"],
            }
        finally:
            await _release_pay(
                int(order_id))

    # ============================================================
    # ② 退款(反向转移)
    # ============================================================

    async def refund_order(self,
                           order_id: int,
                           refunded_by: str = "admin"
                           ) -> dict:
        """订单退款(paid/settled→
        refunded——反向转移: 买增卖扣)

        终审管理面(退款不受开关影响
        ——资金安全人工铁律)。

        Raises:
            KeyError: 订单不存在
            ValueError: 状态机拒绝
        """
        order = await self.repo.get_order(
            int(order_id))
        if not order:
            raise KeyError(
                f"订单 {order_id} 不存在")
        from services.xx64_registry import (
            ORDER_TRANSITIONS,
            TRANSFER_SOURCE,
        )
        if "refunded" not in \
                ORDER_TRANSITIONS.get(
                    order.get("status"), ()):
            raise ValueError(
                f"订单状态 "
                f"{order.get('status')} "
                f"不可退款(合法源态: "
                f"paid/settled)")

        buyer_id = int(
            order.get("buyerId") or 0)
        seller_id = int(
            order.get("sellerId") or 0)
        trust_id = int(
            order.get("trustId") or 0)
        trust_value = float(
            order.get("trustValue") or 0)

        # 反向转移(买增卖扣——
        # 同事务对)
        entry_id = await \
            self.repo.next_entry_id()
        # 借方: 卖方扣减
        debit = {
            "entryId": entry_id,
            "orderId": int(order_id),
            "trustId": seller_id,
            "direction":
                DIRECTION_DEBIT,
            "transferType":
                TRANSFER_REFUND,
            "amount": -trust_value,
            "source": TRANSFER_SOURCE,
            "balanceBefore": 0.0,
            "balanceAfter":
                -trust_value,
            "note": f"卖方退款扣减(订单 "
                    f"{order_id} 退 "
                    f"{trust_value})",
            "createdAt": ts(),
        }
        await self.repo.save_ledger(debit)
        # 贷方: 买方返还
        credit = {
            "entryId": entry_id,
            "orderId": int(order_id),
            "trustId": trust_id,
            "direction":
                DIRECTION_CREDIT,
            "transferType":
                TRANSFER_REFUND,
            "amount": trust_value,
            "source": TRANSFER_SOURCE,
            "balanceBefore": 0.0,
            "balanceAfter":
                trust_value,
            "note": f"买方退款返还(订单 "
                    f"{order_id} 返 "
                    f"{trust_value})",
            "createdAt": ts(),
        }
        await self.repo.save_ledger(
            credit)

        order.update({
            "status": "refunded",
            "refundedBy": str(
                refunded_by or "admin"),
            "refundedAt": ts(),
            "updatedAt": ts()})
        await self.repo.save_order(
            order, create=False)
        await self._track("settle", {
            "action": "refund",
            "orderId": int(order_id),
            "entryId": entry_id,
            "trustValue": trust_value,
            "refundedBy": refunded_by,
        })
        return {
            "success": True,
            "orderId": int(order_id),
            "status": "refunded",
            "entryId": entry_id,
            "trustValue": trust_value,
            "refund": {
                "buyerCredit":
                    trust_value,
                "sellerDebit":
                    -trust_value,
                "source":
                    TRANSFER_SOURCE,
            },
            "note": "退款完成——反向转移"
                    "(买增卖扣, 借贷对 "
                    f"entryId={entry_id})",
            "refundedAt":
                order["refundedAt"],
        }

    # ============================================================
    # 观测面(账本)
    # ============================================================

    async def ledger_view(self,
                          order_id: int = None,
                          trust_id: int = None,
                          limit: int = 100
                          ) -> dict:
        """转移账本观测面(借贷对
        ——转移可追溯)"""
        records = await self.repo.list_ledger(
            order_id=order_id,
            trust_id=trust_id,
            limit=int(limit or 100))
        total_debit = round(sum(
            -float(r.get("amount") or 0)
            for r in records
            if r.get("direction")
            == DIRECTION_DEBIT), 2)
        total_credit = round(sum(
            float(r.get("amount") or 0)
            for r in records
            if r.get("direction")
            == DIRECTION_CREDIT), 2)
        return {
            "success": True,
            "total": len(records),
            "entries": records,
            "totals": {
                "debit": -total_debit,
                "credit": total_credit,
                "balanced": abs(
                    total_debit
                    - total_credit)
                < 0.01,
            },
            "note": "转移账本——借贷对"
                    "(来源标记 "
                    "consumption_transfer)",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "orderId": int(
                    detail.get("orderId")
                    or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "xx64_track_failed %s: %s",
                event_type, exc)
