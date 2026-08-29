"""收款管理业务:支付/退款/付款 + 幂等回调 + 状态机

并发安全(遵循项目约定 lock:{key} 格式):
    - 创建支付: payment:order:{orderId} 锁(防同订单并发创建支付单)
    - 支付回调: payment:callback:{channelTradeNo} 幂等锁(SETNX + TTL 24h)
    - 发起退款: payment:refund:{payNo} 锁(防并发退款超额)
    - 退款审核: payment:refund:audit:{refundNo} 锁(防重复审核)
    - 付款打款: payment:payout:{payoutNo} 锁(防重复打款)

状态机(对齐设计文档):
    支付: pending → paying → paid → refunding → refunded
                ↘ closed(超时/取消)        ↘ failed(可重试)
    退款: pending → auditing → approved → refunded
                ↘ cancelled   ↘ rejected
    付款: pending → auditing → approved → paying → paid
                ↘ cancelled   ↘ rejected   ↘ failed(可重试, 上限 3)

异常约定(遵循项目约定):
    - KeyError(message)  → 路由层映射为 404
    - ValueError(message) → 路由层映射为 409

注: 跨模块联动(订单/钱包/财务)由路由层或事件回调处理, 本服务保持单一职责
"""

import logging

from core.helpers import ts
from core.locks import get_lock
from core.age_gate import AGE_CONFIRM_REQUIRED_MSG
from repositories.payment_repository import (
    PaymentRepository,
    # 支付状态
    PAY_STATUS_PENDING, PAY_STATUS_PAYING, PAY_STATUS_PAID,
    PAY_STATUS_FAILED, PAY_STATUS_CLOSED,
    PAY_STATUS_REFUNDING, PAY_STATUS_REFUNDED,
    # 退款状态
    REFUND_STATUS_PENDING, REFUND_STATUS_AUDITING,
    REFUND_STATUS_APPROVED, REFUND_STATUS_REJECTED,
    REFUND_STATUS_REFUNDED, REFUND_STATUS_CANCELLED,
    # 付款状态
    PAYOUT_STATUS_PENDING, PAYOUT_STATUS_AUDITING,
    PAYOUT_STATUS_APPROVED, PAYOUT_STATUS_PAYING,
    PAYOUT_STATUS_PAID, PAYOUT_STATUS_FAILED,
    PAYOUT_STATUS_REJECTED, PAYOUT_STATUS_CANCELLED,
    # 对账状态(P1)
    RECON_STATUS_PENDING, RECON_STATUS_MATCHED, RECON_STATUS_DIFF,
    RECON_STATUS_INVESTIGATING, RECON_STATUS_RESOLVED,
    RECON_STATUS_NAMES,
    MATCH_TYPE_FULL, MATCH_TYPE_PARTIAL, MATCH_TYPE_MISMATCH,
    DIFF_TYPE_AMOUNT_MISMATCH, DIFF_TYPE_PLATFORM_ONLY, DIFF_TYPE_CHANNEL_ONLY,
    HANDLE_SUGGEST_REFUND, HANDLE_SUGGEST_SUPPLEMENT, CHANNEL_STATUS_ACTIVE, CHANNEL_STATUS_NAMES,
    CHANNEL_TYPE_THIRD_PARTY, CHANNEL_TYPE_BANK, CHANNEL_TYPE_AGGREGATE,
    FEE_TYPE_FIXED, FEE_TYPE_RATIO, FEE_TYPE_MIXED,
)

logger = logging.getLogger(__name__)


# ============================================================
# 状态中文名(对齐设计文档)
# ============================================================

PAY_STATUS_NAMES = {
    PAY_STATUS_PENDING: "待支付",
    PAY_STATUS_PAYING: "支付中",
    PAY_STATUS_PAID: "已支付",
    PAY_STATUS_FAILED: "支付失败",
    PAY_STATUS_CLOSED: "已关闭",
    PAY_STATUS_REFUNDING: "退款中",
    PAY_STATUS_REFUNDED: "已退款",
}

REFUND_STATUS_NAMES = {
    REFUND_STATUS_PENDING: "待审核",
    REFUND_STATUS_AUDITING: "审核中",
    REFUND_STATUS_APPROVED: "审核通过",
    REFUND_STATUS_REJECTED: "审核拒绝",
    REFUND_STATUS_REFUNDED: "已退款",
    REFUND_STATUS_CANCELLED: "已撤回",
}

PAYOUT_STATUS_NAMES = {
    PAYOUT_STATUS_PENDING: "待审核",
    PAYOUT_STATUS_AUDITING: "审核中",
    PAYOUT_STATUS_APPROVED: "审核通过",
    PAYOUT_STATUS_PAYING: "打款中",
    PAYOUT_STATUS_PAID: "已打款",
    PAYOUT_STATUS_FAILED: "打款失败",
    PAYOUT_STATUS_REJECTED: "审核拒绝",
    PAYOUT_STATUS_CANCELLED: "已撤回",
}


# ============================================================
# 业务常量
# ============================================================

# 支付超时(未支付自动关闭), 单位秒(默认 30 分钟)
PAY_EXPIRE_SECONDS = 30 * 60

# 支持的支付渠道
SUPPORTED_CHANNELS = {"wechat", "alipay", "unionpay", "bank", "aggregate"}

# 支持的支付方式(按渠道细分)
SUPPORTED_METHODS = {"native", "jsapi", "h5", "page", "transfer"}

# 场景类型(与已有模块联动; guest_order_pay 为游客扫码付免登录场景)
SUPPORTED_SCENES = {"order_pay", "wallet_deposit", "agent_purchase",
                    "guest_order_pay"}

# 游客扫码付规则(设计文档 2.7.3/2.7.8: P0 一期核心)
GUEST_SCENE = "guest_order_pay"
# 游客单笔金额上限(≤ ¥5,000)
GUEST_MAX_SINGLE_AMOUNT = 5000.0
# 游客支付单超时(15 分钟, 区别于登录单 30 分钟)
GUEST_EXPIRE_SECONDS = 15 * 60
# 游客仅扫码付/拉起(不支持 transfer/page)
GUEST_ALLOWED_METHODS = {"native", "jsapi", "h5"}
# 游客仅零售标品(团购需 SVIP / 定制需登录 / 钱包充值需登录)
GUEST_ALLOWED_ORDER_TYPE = "retail"

# 退款审核金额阈值(≥ 此值需人工审核, < 此值自动通过)
REFUND_AUTO_APPROVE_THRESHOLD = 1000.0

# 付款审核金额阈值(≥ 此值需人工审核, < 此值自动通过)
PAYOUT_AUTO_APPROVE_THRESHOLD = 5000.0

# 付款打款重试上限
PAYOUT_MAX_RETRY = 3

# 最小支付金额
MIN_PAY_AMOUNT = 0.01


def _mask_account(account: str) -> str:
    """银行账号脱敏(保留后 4 位)"""
    if not account or len(account) <= 4:
        return "****"
    return "*" * (len(account) - 4) + account[-4:]


def _mask_phone(phone: str) -> str:
    """手机号脱敏(保留前 3 后 4)"""
    if not phone or len(phone) <= 7:
        return "****"
    return phone[:3] + "****" + phone[-4:]


class PaymentService:
    """收款业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, payment_repo: PaymentRepository = PaymentRepository()):
        self.repo = payment_repo

    # ============================================================
    # P0: 支付订单
    # ============================================================

    async def create_pay(self, user_id, order_id: str, order_type: str,
                          total_amount: float, pay_channel: str,
                          pay_method: str = "jsapi",
                          scene_type: str = "order_pay",
                          discount_amount: float = 0.0,
                          points_amount: float = 0.0,
                          guest_phone: str = None,
                          age_confirmed: bool = False) -> dict:
        """创建支付订单(幂等: 同一订单只能有一个活跃支付单)

        Args:
            user_id: 用户ID(游客场景传 "guest")
            order_id: 关联订单号
            order_type: 订单类型 retail/groupbuy/custom/wallet_deposit
            total_amount: 订单总金额
            pay_channel: 支付渠道 wechat/alipay/...
            pay_method: 支付方式 native/jsapi/h5/page/transfer
            scene_type: 场景 order_pay/wallet_deposit/agent_purchase/guest_order_pay
            discount_amount: 优惠抵扣
            points_amount: 积分抵扣
            guest_phone: 游客手机号(游客场景必填)
            age_confirmed: 已满18周岁声明(游客场景必填, 酒类合规)

        游客扫码付规则(设计文档 2.7.3/2.7.8, P0 一期):
            - 手机号必填(11 位) + 年龄声明必填(酒类合规)
            - 单笔 ≤ ¥5,000; 待付款 15 分钟超时
            - 仅零售标品 + 扫码付(native/jsapi/h5)
            - 不支持优惠/积分抵扣(仅全额)

        Raises:
            ValueError: 参数非法 / 已有活跃支付单 / 游客规则校验失败
        """
        is_guest = scene_type == GUEST_SCENE

        # 参数校验
        if total_amount < MIN_PAY_AMOUNT:
            raise ValueError(f"支付金额须 ≥ ¥{MIN_PAY_AMOUNT}")
        if pay_channel not in SUPPORTED_CHANNELS:
            raise ValueError(f"支付渠道非法: {pay_channel}")
        if pay_method not in SUPPORTED_METHODS:
            raise ValueError(f"支付方式非法: {pay_method}")
        if scene_type not in SUPPORTED_SCENES:
            raise ValueError(f"场景类型非法: {scene_type}")
        if discount_amount < 0 or points_amount < 0:
            raise ValueError("优惠/积分抵扣不能为负")

        # 游客扫码付规则校验(P0-3)
        if is_guest:
            if not guest_phone or len(str(guest_phone)) != 11 \
                    or not str(guest_phone).isdigit():
                raise ValueError("游客支付须提供 11 位手机号(订单关联+物流+售后)")
            if not age_confirmed:
                raise ValueError(AGE_CONFIRM_REQUIRED_MSG)
            if order_type != GUEST_ALLOWED_ORDER_TYPE:
                raise ValueError(
                    f"游客仅支持零售标品支付(团购/定制/钱包充值需登录), "
                    f"当前订单类型: {order_type}")
            if total_amount > GUEST_MAX_SINGLE_AMOUNT:
                raise ValueError(
                    f"游客单笔支付上限 ¥{GUEST_MAX_SINGLE_AMOUNT:,.0f}"
                    f"(当前 ¥{total_amount:,.2f}), 大额购买请注册后下单")
            if pay_method not in GUEST_ALLOWED_METHODS:
                raise ValueError(
                    f"游客仅支持扫码付/拉起支付({'/'.join(sorted(GUEST_ALLOWED_METHODS))}), "
                    f"当前: {pay_method}")
            if discount_amount > 0 or points_amount > 0:
                raise ValueError("游客支付不支持优惠/积分抵扣(仅全额支付)")
            # 游客场景无登录身份
            user_id = "guest"

        actual_amount = round(total_amount - discount_amount - points_amount, 2)
        if actual_amount < MIN_PAY_AMOUNT:
            raise ValueError(f"实付金额须 ≥ ¥{MIN_PAY_AMOUNT}")

        # 幂等校验: 同一订单同一类型只能有一个活跃支付单
        async with get_lock(f"payment:order:{order_id}"):
            existing = await self.repo.find_active_by_order(order_id, order_type)
            if existing:
                raise ValueError(
                    f"订单 {order_id} 已有活跃支付单 {existing['payNo']}"
                    f"(状态: {PAY_STATUS_NAMES.get(existing.get('status'))})"
                )
            # 创建支付单
            pay_no = await self.repo.next_pay_no()
            # 超时时间(ISO8601; 游客单 15 分钟, 登录单 30 分钟)
            from datetime import datetime, timezone, timedelta
            tz = timezone(timedelta(hours=8))
            expire_seconds = GUEST_EXPIRE_SECONDS if is_guest else PAY_EXPIRE_SECONDS
            expire_time = (datetime.now(tz) +
                           timedelta(seconds=expire_seconds)).isoformat()
            order_data = {
                "payNo": pay_no,
                "orderId": order_id,
                "orderType": order_type,
                "userId": str(user_id),
                "payChannel": pay_channel,
                "payMethod": pay_method,
                "totalAmount": total_amount,
                "discountAmount": discount_amount,
                "pointsAmount": points_amount,
                "actualAmount": actual_amount,
                "refundedAmount": 0.0,
                "channelTradeNo": "",
                "status": PAY_STATUS_PENDING,
                "expireTime": expire_time,
                "payTime": "",
                "callbackTime": "",
                "callbackContent": "",
                "sceneType": scene_type,
                "failReason": "",
                # 游客扫码付标识(设计文档 2.7.8: is_guest_order/guest_phone)
                "isGuest": is_guest,
                "guestPhone": str(guest_phone) if is_guest else "",
                "createdAt": ts(),
                "updatedAt": ts(),
            }
            await self.repo.save_order(order_data)
            logger.info("payment_created payNo=%s order=%s amount=%.2f channel=%s",
                        pay_no, order_id, actual_amount, pay_channel)
            return {
                "success": True,
                "payNo": pay_no,
                "orderId": order_id,
                "userId": str(user_id),
                "payChannel": pay_channel,
                "payMethod": pay_method,
                "sceneType": scene_type,
                "actualAmount": actual_amount,
                "status": PAY_STATUS_PENDING,
                "statusName": PAY_STATUS_NAMES[PAY_STATUS_PENDING],
                "expireTime": expire_time,
                "isGuest": is_guest,
                "guestPhone": order_data["guestPhone"],
                "createdAt": order_data["createdAt"],
            }

    async def get_pay(self, pay_no: str) -> dict:
        """查询支付订单详情

        Raises:
            KeyError: 支付单不存在
        """
        order = await self.repo.get_order(pay_no)
        if not order:
            raise KeyError(f"支付单 {pay_no} 不存在")
        return {
            "success": True,
            **order,
            "statusName": PAY_STATUS_NAMES.get(order.get("status"), "未知"),
        }

    async def list_pays(self, user_id, status: str = None,
                         scene_type: str = None, limit: int = 50) -> dict:
        """列出用户支付订单"""
        orders = await self.repo.list_orders(user_id, status, scene_type, limit)
        return {
            "success": True,
            "count": len(orders),
            "items": [
                {**o, "statusName": PAY_STATUS_NAMES.get(o.get("status"), "未知")}
                for o in orders
            ],
        }

    async def start_pay(self, pay_no: str) -> dict:
        """发起渠道支付(待支付/失败 → 支付中)

        实际场景: 调用渠道 SDK 生成预支付订单, 返回支付参数
        本实现: 仅更新状态为 paying

        状态机:
            - pending → paying(首次发起)
            - failed  → paying(失败重试)

        Raises:
            KeyError: 支付单不存在
            ValueError: 状态非法(非 pending/failed)
        """
        async with get_lock(f"payment:order:pay:{pay_no}"):
            order = await self.repo.get_order(pay_no)
            if not order:
                raise KeyError(f"支付单 {pay_no} 不存在")
            if order["status"] not in (PAY_STATUS_PENDING, PAY_STATUS_FAILED):
                raise ValueError(
                    f"支付单状态非法(当前: {PAY_STATUS_NAMES.get(order['status'])}), 须为待支付/支付失败"
                )
            await self.repo.update_order_fields(pay_no, {
                "status": PAY_STATUS_PAYING,
                "failReason": "",
                "updatedAt": ts(),
            })
            logger.info("payment_paying payNo=%s channel=%s", pay_no, order["payChannel"])
            return {
                "success": True,
                "payNo": pay_no,
                "status": PAY_STATUS_PAYING,
                "statusName": PAY_STATUS_NAMES[PAY_STATUS_PAYING],
                "payParams": {
                    "channel": order["payChannel"],
                    "method": order["payMethod"],
                    "actualAmount": order["actualAmount"],
                    "expireTime": order.get("expireTime", ""),
                },
            }

    async def pay_callback(self, channel_trade_no: str, callback_content: dict,
                            pay_no: str = None) -> dict:
        """支付回调处理(幂等: 重复回调返回成功, 不重复入账)

        Args:
            channel_trade_no: 渠道交易号
            callback_content: 回调原始数据
            pay_no: 可选, 若已知直接定位; 否则按 channel_trade_no 反查

        Returns:
            {"success": True, "payNo": ..., "idempotent": bool}
        """
        # 幂等锁(24h TTL)
        acquired = await self.repo.acquire_callback_lock(channel_trade_no)
        if not acquired:
            logger.info("payment_callback_idempotent channelTradeNo=%s(已处理过)",
                        channel_trade_no)
            return {
                "success": True,
                "payNo": pay_no or "",
                "idempotent": True,
                "msg": "回调已处理过, 幂等返回",
            }

        # 定位支付单
        if pay_no:
            order = await self.repo.get_order(pay_no)
        else:
            order = await self.repo.get_by_channel_trade_no(channel_trade_no)
        if not order:
            logger.warning("payment_callback_order_not_found channelTradeNo=%s",
                            channel_trade_no)
            return {
                "success": False,
                "payNo": "",
                "idempotent": False,
                "msg": f"渠道交易号 {channel_trade_no} 未找到对应支付单",
            }
        pay_no = order["payNo"]

        # 状态流转: paying → paid
        # 所有状态校验必须在锁内重新执行, 避免 TOCTOU 竞态
        # (防止两个并发回调同时通过锁外校验, 导致重复入账)
        async with get_lock(f"payment:order:pay:{pay_no}"):
            # 锁内重新读取支付单, 获取最新状态
            order = await self.repo.get_order(pay_no)
            if not order:
                return {
                    "success": False,
                    "payNo": pay_no,
                    "idempotent": False,
                    "msg": f"支付单 {pay_no} 不存在",
                }

            # 锁内状态校验(双重校验)
            if order["status"] == PAY_STATUS_PAID:
                return {"success": True, "payNo": pay_no, "idempotent": True,
                        "msg": "支付单已支付, 幂等返回"}
            if order["status"] != PAY_STATUS_PAYING:
                logger.warning("payment_callback_status_invalid payNo=%s status=%s",
                                pay_no, order["status"])
                return {
                    "success": False,
                    "payNo": pay_no,
                    "idempotent": False,
                    "msg": f"支付单状态非法(当前: {PAY_STATUS_NAMES.get(order['status'])})",
                }

            await self.repo.update_order_fields(pay_no, {
                "status": PAY_STATUS_PAID,
                "channelTradeNo": channel_trade_no,
                "payTime": ts(),
                "callbackTime": ts(),
                "callbackContent": _safe_json_dumps(callback_content),
                "updatedAt": ts(),
            })
            logger.info("payment_paid payNo=%s channelTradeNo=%s amount=%.2f",
                        pay_no, channel_trade_no, order["actualAmount"])
            return {
                "success": True,
                "payNo": pay_no,
                "idempotent": False,
                "orderId": order["orderId"],
                "userId": order["userId"],
                "amount": order["actualAmount"],
                "channel": order["payChannel"],
            }

    async def close_pay(self, pay_no: str, reason: str = "USER_CANCEL") -> dict:
        """关闭支付单(待支付/支付中 → 已关闭)

        场景: 用户主动取消 / 超时关闭

        Raises:
            KeyError: 支付单不存在
            ValueError: 状态非法(已支付/已退款不可关闭)
        """
        async with get_lock(f"payment:order:pay:{pay_no}"):
            order = await self.repo.get_order(pay_no)
            if not order:
                raise KeyError(f"支付单 {pay_no} 不存在")
            if order["status"] in (PAY_STATUS_PAID, PAY_STATUS_REFUNDED):
                raise ValueError(
                    f"支付单已{PAY_STATUS_NAMES.get(order['status'])}, 不可关闭"
                )
            if order["status"] == PAY_STATUS_CLOSED:
                return {"success": True, "payNo": pay_no, "idempotent": True}
            await self.repo.update_order_fields(pay_no, {
                "status": PAY_STATUS_CLOSED,
                "failReason": reason,
                "updatedAt": ts(),
            })
            logger.info("payment_closed payNo=%s reason=%s", pay_no, reason)
            return {
                "success": True,
                "payNo": pay_no,
                "status": PAY_STATUS_CLOSED,
                "statusName": PAY_STATUS_NAMES[PAY_STATUS_CLOSED],
                "reason": reason,
            }

    async def fail_pay(self, pay_no: str, reason: str = "CHANNEL_FAIL") -> dict:
        """标记支付失败(支付中 → 支付失败, 可重新发起)

        Raises:
            KeyError: 支付单不存在
            ValueError: 状态非法
        """
        async with get_lock(f"payment:order:pay:{pay_no}"):
            order = await self.repo.get_order(pay_no)
            if not order:
                raise KeyError(f"支付单 {pay_no} 不存在")
            if order["status"] != PAY_STATUS_PAYING:
                raise ValueError(
                    f"支付单状态非法(当前: {PAY_STATUS_NAMES.get(order['status'])}), 须为支付中"
                )
            await self.repo.update_order_fields(pay_no, {
                "status": PAY_STATUS_FAILED,
                "failReason": reason,
                "updatedAt": ts(),
            })
            logger.info("payment_failed payNo=%s reason=%s", pay_no, reason)
            return {
                "success": True,
                "payNo": pay_no,
                "status": PAY_STATUS_FAILED,
                "statusName": PAY_STATUS_NAMES[PAY_STATUS_FAILED],
                "reason": reason,
            }

    # ============================================================
    # P0: 退款
    # ============================================================

    async def create_refund(self, pay_no: str, refund_amount: float,
                              refund_reason: str,
                              refund_type: str = "partial") -> dict:
        """创建退款申请(幂等: 累计退款不超过原支付金额)

        Args:
            pay_no: 关联支付单号
            refund_amount: 退款金额(full 类型可传 0, 自动计算剩余可退)
            refund_reason: 退款原因
            refund_type: full(全额) / partial(部分)

        Raises:
            KeyError: 支付单不存在
            ValueError: 支付单未支付 / 退款金额非法 / 退款超额
        """
        if refund_type not in ("full", "partial"):
            raise ValueError(f"退款类型非法: {refund_type}")
        # partial 类型必须 > 0; full 类型允许 0(后续自动计算)
        if refund_type == "partial" and refund_amount <= 0:
            raise ValueError("部分退款金额须 > 0")

        # 持支付单锁, 防止并发退款超额
        async with get_lock(f"payment:refund:{pay_no}"):
            order = await self.repo.get_order(pay_no)
            if not order:
                raise KeyError(f"支付单 {pay_no} 不存在")
            if order["status"] not in (PAY_STATUS_PAID, PAY_STATUS_REFUNDING):
                raise ValueError(
                    f"支付单状态非法(当前: {PAY_STATUS_NAMES.get(order['status'])}), 须为已支付/退款中"
                )
            # 累计已占用退款额度(pending/auditing/approved/refunded 状态都占用额度,
            # 仅 rejected/cancelled 不占用)
            all_refunds = await self.repo.list_refunds(pay_no, limit=1000)
            occupied = sum(
                float(r.get("refundAmount", 0))
                for r in all_refunds
                if r.get("status") in (
                    REFUND_STATUS_PENDING, REFUND_STATUS_AUDITING,
                    REFUND_STATUS_APPROVED, REFUND_STATUS_REFUNDED,
                )
            )
            actual_amount = float(order["actualAmount"])
            # 全额退款: refund_amount = 实付 - 已占用
            if refund_type == "full":
                refund_amount = round(actual_amount - occupied, 2)
                if refund_amount <= 0:
                    raise ValueError("支付单已全额退款, 无可退金额")
            # 累计校验
            if occupied + refund_amount > actual_amount + 0.01:  # 允许 1 分误差
                raise ValueError(
                    f"退款超额: 已占用 ¥{occupied:.2f} + 本次 ¥{refund_amount:.2f}"
                    f" > 实付 ¥{actual_amount:.2f}"
                )

            refund_no = await self.repo.next_refund_no()
            refund_data = {
                "refundNo": refund_no,
                "payNo": pay_no,
                "orderId": order.get("orderId", ""),
                "userId": order.get("userId", ""),
                "refundType": refund_type,
                "refundAmount": refund_amount,
                "refundedAmount": 0.0,
                "refundReason": refund_reason,
                "refundChannel": order.get("payChannel", ""),
                "channelRefundNo": "",
                "status": REFUND_STATUS_PENDING,
                "auditor": "",
                "auditRemark": "",
                "auditTime": "",
                "refundTime": "",
                "callbackContent": "",
                "createdAt": ts(),
            }
            await self.repo.save_refund(refund_data)
            # 支付单状态流转: paid → refunding
            await self.repo.update_order_fields(pay_no, {
                "status": PAY_STATUS_REFUNDING,
                "updatedAt": ts(),
            })
            logger.info("refund_created refundNo=%s payNo=%s amount=%.2f type=%s",
                        refund_no, pay_no, refund_amount, refund_type)
            return {
                "success": True,
                "refundNo": refund_no,
                "payNo": pay_no,
                "refundAmount": refund_amount,
                "occupiedRefund": round(occupied, 2),
                "remainRefundable": round(actual_amount - occupied - refund_amount, 2),
                "status": REFUND_STATUS_PENDING,
                "statusName": REFUND_STATUS_NAMES[REFUND_STATUS_PENDING],
            }

    async def audit_refund(self, refund_no: str, decision: str,
                             auditor: str = "admin",
                             audit_remark: str = "") -> dict:
        """审核退款(待审核 → 审核通过/拒绝)

        Args:
            decision: approved/rejected

        Raises:
            KeyError: 退款单不存在
            ValueError: 状态非法 / decision 非法
        """
        if decision not in ("approved", "rejected"):
            raise ValueError(f"审核决定非法: {decision}(须 approved/rejected)")

        async with get_lock(f"payment:refund:audit:{refund_no}"):
            refund = await self.repo.get_refund(refund_no)
            if not refund:
                raise KeyError(f"退款单 {refund_no} 不存在")
            if refund["status"] not in (REFUND_STATUS_PENDING, REFUND_STATUS_AUDITING):
                raise ValueError(
                    f"退款单状态非法(当前: {REFUND_STATUS_NAMES.get(refund['status'])})"
                )

            if decision == "approved":
                new_status = REFUND_STATUS_APPROVED
                # 触发渠道退款(实际场景: 调用渠道退款 API)
                # 这里仅更新状态, 渠道退款通过 refund_callback 异步完成
            else:
                new_status = REFUND_STATUS_REJECTED
                # 退款被拒, 支付单状态回退: refunding → paid
                await self.repo.update_order_fields(refund["payNo"], {
                    "status": PAY_STATUS_PAID,
                    "updatedAt": ts(),
                })

            await self.repo.update_refund_fields(refund_no, {
                "status": new_status,
                "auditor": auditor,
                "auditRemark": audit_remark,
                "auditTime": ts(),
            })
            logger.info("refund_audited refundNo=%s decision=%s auditor=%s",
                        refund_no, decision, auditor)
            return {
                "success": True,
                "refundNo": refund_no,
                "status": new_status,
                "statusName": REFUND_STATUS_NAMES[new_status],
                "auditor": auditor,
            }

    async def refund_callback(self, channel_refund_no: str,
                                callback_content: dict,
                                refund_no: str = None) -> dict:
        """退款回调处理(幂等: 重复回调返回成功)

        Args:
            channel_refund_no: 渠道退款号
            callback_content: 回调原始数据
            refund_no: 可选, 若已知直接定位

        Returns:
            {"success": True, "refundNo": ..., "idempotent": bool}
        """
        # 幂等锁
        acquired = await self.repo.acquire_refund_callback_lock(channel_refund_no)
        if not acquired:
            logger.info("refund_callback_idempotent channelRefundNo=%s(已处理过)",
                        channel_refund_no)
            return {
                "success": True,
                "refundNo": refund_no or "",
                "idempotent": True,
                "msg": "退款回调已处理过, 幂等返回",
            }

        # 定位退款单(实际场景: 渠道回调携带 out_refund_no = refund_no)
        if not refund_no:
            return {
                "success": False,
                "refundNo": "",
                "idempotent": False,
                "msg": "未提供 refund_no, 无法定位退款单",
            }
        refund = await self.repo.get_refund(refund_no)
        if not refund:
            return {
                "success": False,
                "refundNo": refund_no,
                "idempotent": False,
                "msg": f"退款单 {refund_no} 不存在",
            }

        # 状态流转: approved → refunded
        # 所有状态校验 + 金额累计必须在锁内重新执行, 避免 TOCTOU 竞态
        # (防止两个并发回调同时通过锁外校验, 导致金额双倍累计)
        async with get_lock(f"payment:refund:audit:{refund_no}"):
            # 锁内重新读取退款单, 获取最新状态
            refund = await self.repo.get_refund(refund_no)
            if not refund:
                return {
                    "success": False,
                    "refundNo": refund_no,
                    "idempotent": False,
                    "msg": f"退款单 {refund_no} 不存在",
                }

            # 锁内状态校验(双重校验)
            if refund["status"] == REFUND_STATUS_REFUNDED:
                return {"success": True, "refundNo": refund_no, "idempotent": True}
            if refund["status"] != REFUND_STATUS_APPROVED:
                return {
                    "success": False,
                    "refundNo": refund_no,
                    "idempotent": False,
                    "msg": f"退款单状态非法(当前: {REFUND_STATUS_NAMES.get(refund['status'])})",
                }

            # 累计支付单已退款金额
            await self.repo.add_refunded_amount(refund["payNo"], float(refund["refundAmount"]))
            # 更新退款单
            await self.repo.update_refund_fields(refund_no, {
                "status": REFUND_STATUS_REFUNDED,
                "channelRefundNo": channel_refund_no,
                "refundTime": ts(),
                "callbackContent": _safe_json_dumps(callback_content),
            })
            # 检查支付单是否全额退款, 若是则状态 → refunded
            order = await self.repo.get_order(refund["payNo"])
            if order and float(order.get("refundedAmount", 0)) >= float(order["actualAmount"]):
                await self.repo.update_order_fields(refund["payNo"], {
                    "status": PAY_STATUS_REFUNDED,
                    "updatedAt": ts(),
                })
            logger.info("refund_completed refundNo=%s channelRefundNo=%s amount=%.2f",
                        refund_no, channel_refund_no, refund["refundAmount"])
            return {
                "success": True,
                "refundNo": refund_no,
                "idempotent": False,
                "payNo": refund["payNo"],
                "refundAmount": refund["refundAmount"],
            }

    async def cancel_refund(self, refund_no: str) -> dict:
        """用户撤回退款申请(待审核 → 已撤回)

        Raises:
            KeyError: 退款单不存在
            ValueError: 状态非法(已审核不可撤回)
        """
        async with get_lock(f"payment:refund:audit:{refund_no}"):
            refund = await self.repo.get_refund(refund_no)
            if not refund:
                raise KeyError(f"退款单 {refund_no} 不存在")
            if refund["status"] not in (REFUND_STATUS_PENDING, REFUND_STATUS_AUDITING):
                raise ValueError(
                    f"退款单已审核, 不可撤回(当前: {REFUND_STATUS_NAMES.get(refund['status'])})"
                )
            await self.repo.update_refund_fields(refund_no, {
                "status": REFUND_STATUS_CANCELLED,
            })
            # 支付单状态回退: refunding → paid
            await self.repo.update_order_fields(refund["payNo"], {
                "status": PAY_STATUS_PAID,
                "updatedAt": ts(),
            })
            logger.info("refund_cancelled refundNo=%s", refund_no)
            return {
                "success": True,
                "refundNo": refund_no,
                "status": REFUND_STATUS_CANCELLED,
                "statusName": REFUND_STATUS_NAMES[REFUND_STATUS_CANCELLED],
            }

    async def list_refunds(self, pay_no: str, status: str = None,
                            limit: int = 50) -> dict:
        """列出支付单的退款记录"""
        refunds = await self.repo.list_refunds(pay_no, status, limit)
        return {
            "success": True,
            "count": len(refunds),
            "items": [
                {**r, "statusName": REFUND_STATUS_NAMES.get(r.get("status"), "未知")}
                for r in refunds
            ],
        }

    async def list_pending_refunds(self, limit: int = 100) -> dict:
        """列出待审核退款(管理端审批用)"""
        refunds = await self.repo.list_pending_refunds(limit)
        return {
            "success": True,
            "count": len(refunds),
            "items": [
                {**r, "statusName": REFUND_STATUS_NAMES.get(r.get("status"), "未知")}
                for r in refunds
            ],
        }

    # ============================================================
    # P0: 平台付款
    # ============================================================

    async def create_payout(self, payout_type: str, source_id: str,
                              payee_name: str, payee_account: str,
                              payee_bank: str, amount: float,
                              pay_channel: str = "bank_transfer",
                              payee_phone: str = "",
                              tax_amount: float = 0.0) -> dict:
        """创建付款单(幂等: 同一来源单据只能创建一个付款单)

        Args:
            payout_type: supplier/logistics/recycle/commission/wallet_withdraw/salary
            source_id: 来源单据号(如钱包提现单号 WD...)
            payee_name: 收款人名称
            payee_account: 收款账号
            payee_bank: 收款银行
            amount: 付款金额
            pay_channel: 付款渠道 bank_transfer/alipay_transfer/wechat_transfer
            payee_phone: 收款人手机号
            tax_amount: 代扣税费

        Raises:
            ValueError: 参数非法 / 已存在付款单
        """
        if amount <= 0:
            raise ValueError("付款金额须 > 0")
        if tax_amount < 0 or tax_amount > amount:
            raise ValueError("代扣税费非法")
        if not payee_name or not payee_account:
            raise ValueError("收款人名称/账号不能为空")
        if pay_channel not in ("bank_transfer", "alipay_transfer", "wechat_transfer"):
            raise ValueError(f"付款渠道非法: {pay_channel}")

        # 幂等校验
        existing = await self.repo.find_by_source(source_id, payout_type)
        if existing:
            raise ValueError(
                f"来源单据 {source_id} 已存在付款单 {existing['payoutNo']}"
                f"(状态: {PAYOUT_STATUS_NAMES.get(existing.get('status'))})"
            )

        payout_no = await self.repo.next_payout_no()
        actual_amount = round(amount - tax_amount, 2)
        payout_data = {
            "payoutNo": payout_no,
            "payoutType": payout_type,
            "sourceId": source_id,
            "payeeName": payee_name,
            "payeeAccount": payee_account,
            "payeeBank": payee_bank,
            "payeePhone": payee_phone,
            "amount": amount,
            "taxAmount": tax_amount,
            "actualAmount": actual_amount,
            "payChannel": pay_channel,
            "channelPayoutNo": "",
            "status": PAYOUT_STATUS_PENDING,
            "auditor": "",
            "auditRemark": "",
            "auditTime": "",
            "payTime": "",
            "failReason": "",
            "voucherUrl": "",
            "retryCount": 0,
            "createdAt": ts(),
        }
        await self.repo.save_payout(payout_data)
        logger.info("payout_created payoutNo=%s type=%s source=%s amount=%.2f",
                    payout_no, payout_type, source_id, amount)

        # 小额自动通过审核(< ¥5000)
        if amount < PAYOUT_AUTO_APPROVE_THRESHOLD:
            await self.repo.update_payout_fields(payout_no, {
                "status": PAYOUT_STATUS_APPROVED,
                "auditor": "auto",
                "auditRemark": "小额自动通过",
                "auditTime": ts(),
            })
            logger.info("payout_auto_approved payoutNo=%s amount=%.2f",
                        payout_no, amount)

        return {
            "success": True,
            "payoutNo": payout_no,
            "sourceId": source_id,
            "amount": amount,
            "taxAmount": tax_amount,
            "actualAmount": actual_amount,
            "payeeAccountMasked": _mask_account(payee_account),
            "status": PAYOUT_STATUS_APPROVED if amount < PAYOUT_AUTO_APPROVE_THRESHOLD
                       else PAYOUT_STATUS_PENDING,
            "statusName": PAYOUT_STATUS_NAMES[
                PAYOUT_STATUS_APPROVED if amount < PAYOUT_AUTO_APPROVE_THRESHOLD
                else PAYOUT_STATUS_PENDING
            ],
        }

    async def audit_payout(self, payout_no: str, decision: str,
                              auditor: str = "admin",
                              audit_remark: str = "") -> dict:
        """审核付款(待审核 → 审核通过/拒绝)

        Raises:
            KeyError: 付款单不存在
            ValueError: 状态非法
        """
        if decision not in ("approved", "rejected"):
            raise ValueError(f"审核决定非法: {decision}")

        async with get_lock(f"payment:payout:{payout_no}"):
            payout = await self.repo.get_payout(payout_no)
            if not payout:
                raise KeyError(f"付款单 {payout_no} 不存在")
            if payout["status"] not in (PAYOUT_STATUS_PENDING, PAYOUT_STATUS_AUDITING):
                raise ValueError(
                    f"付款单状态非法(当前: {PAYOUT_STATUS_NAMES.get(payout['status'])})"
                )
            new_status = PAYOUT_STATUS_APPROVED if decision == "approved" \
                         else PAYOUT_STATUS_REJECTED
            await self.repo.update_payout_fields(payout_no, {
                "status": new_status,
                "auditor": auditor,
                "auditRemark": audit_remark,
                "auditTime": ts(),
            })
            logger.info("payout_audited payoutNo=%s decision=%s auditor=%s",
                        payout_no, decision, auditor)
            return {
                "success": True,
                "payoutNo": payout_no,
                "status": new_status,
                "statusName": PAYOUT_STATUS_NAMES[new_status],
                "auditor": auditor,
            }

    async def execute_payout(self, payout_no: str) -> dict:
        """执行打款(审核通过 → 打款中)

        实际场景: 调用银行/渠道 API 发起转账
        本实现: 仅更新状态为 paying, 等待渠道回调

        Raises:
            KeyError: 付款单不存在
            ValueError: 状态非法
        """
        async with get_lock(f"payment:payout:{payout_no}"):
            payout = await self.repo.get_payout(payout_no)
            if not payout:
                raise KeyError(f"付款单 {payout_no} 不存在")
            if payout["status"] != PAYOUT_STATUS_APPROVED:
                raise ValueError(
                    f"付款单状态非法(当前: {PAYOUT_STATUS_NAMES.get(payout['status'])}), 须为审核通过"
                )
            await self.repo.update_payout_fields(payout_no, {
                "status": PAYOUT_STATUS_PAYING,
            })
            logger.info("payout_paying payoutNo=%s amount=%.2f",
                        payout_no, payout["actualAmount"])
            return {
                "success": True,
                "payoutNo": payout_no,
                "status": PAYOUT_STATUS_PAYING,
                "statusName": PAYOUT_STATUS_NAMES[PAYOUT_STATUS_PAYING],
                "amount": payout["actualAmount"],
                "payeeAccountMasked": _mask_account(payout.get("payeeAccount", "")),
            }

    async def payout_callback(self, payout_no: str, channel_payout_no: str,
                                callback_content: dict,
                                success: bool = True,
                                fail_reason: str = "") -> dict:
        """打款回调处理(幂等: 已 paid 直接返回)

        Args:
            payout_no: 付款单号
            channel_payout_no: 渠道付款流水号
            success: 是否成功
            fail_reason: 失败原因(success=False 时)

        Raises:
            KeyError: 付款单不存在
            ValueError: 状态非法
        """
        async with get_lock(f"payment:payout:{payout_no}"):
            payout = await self.repo.get_payout(payout_no)
            if not payout:
                raise KeyError(f"付款单 {payout_no} 不存在")
            # 幂等: 已 paid 直接返回
            if payout["status"] == PAYOUT_STATUS_PAID:
                return {"success": True, "payoutNo": payout_no, "idempotent": True}
            if payout["status"] != PAYOUT_STATUS_PAYING:
                raise ValueError(
                    f"付款单状态非法(当前: {PAYOUT_STATUS_NAMES.get(payout['status'])}), 须为打款中"
                )

            if success:
                await self.repo.update_payout_fields(payout_no, {
                    "status": PAYOUT_STATUS_PAID,
                    "channelPayoutNo": channel_payout_no,
                    "payTime": ts(),
                })
                logger.info("payout_paid payoutNo=%s channelPayoutNo=%s amount=%.2f",
                            payout_no, channel_payout_no, payout["actualAmount"])
                return {
                    "success": True,
                    "payoutNo": payout_no,
                    "idempotent": False,
                    "status": PAYOUT_STATUS_PAID,
                    "statusName": PAYOUT_STATUS_NAMES[PAYOUT_STATUS_PAID],
                    "amount": payout["actualAmount"],
                }
            else:
                # 失败: retry_count +1, 超过上限自动 rejected
                retry_count = await self.repo.increment_payout_retry(payout_no)
                if retry_count >= PAYOUT_MAX_RETRY:
                    new_status = PAYOUT_STATUS_REJECTED
                    await self.repo.update_payout_fields(payout_no, {
                        "status": new_status,
                        "failReason": f"{fail_reason}(重试 {retry_count} 次上限, 自动拒绝)",
                    })
                    logger.warning("payout_rejected_after_retry payoutNo=%s retryCount=%d",
                                    payout_no, retry_count)
                else:
                    new_status = PAYOUT_STATUS_FAILED
                    await self.repo.update_payout_fields(payout_no, {
                        "status": new_status,
                        "failReason": fail_reason,
                    })
                    logger.warning("payout_failed payoutNo=%s retryCount=%d reason=%s",
                                    payout_no, retry_count, fail_reason)
                return {
                    "success": False,
                    "payoutNo": payout_no,
                    "status": new_status,
                    "statusName": PAYOUT_STATUS_NAMES[new_status],
                    "retryCount": retry_count,
                    "reason": fail_reason,
                }

    async def retry_payout(self, payout_no: str) -> dict:
        """重试打款(失败 → 打款中)

        Raises:
            KeyError: 付款单不存在
            ValueError: 状态非法 / 重试次数超限
        """
        async with get_lock(f"payment:payout:{payout_no}"):
            payout = await self.repo.get_payout(payout_no)
            if not payout:
                raise KeyError(f"付款单 {payout_no} 不存在")
            if payout["status"] != PAYOUT_STATUS_FAILED:
                raise ValueError(
                    f"付款单状态非法(当前: {PAYOUT_STATUS_NAMES.get(payout['status'])}), 须为打款失败"
                )
            retry_count = int(payout.get("retryCount", 0))
            if retry_count >= PAYOUT_MAX_RETRY:
                # 超限自动拒绝
                await self.repo.update_payout_fields(payout_no, {
                    "status": PAYOUT_STATUS_REJECTED,
                    "failReason": f"重试 {retry_count} 次上限, 自动拒绝",
                })
                raise ValueError(f"重试次数超限({PAYOUT_MAX_RETRY} 次), 已自动拒绝")
            await self.repo.update_payout_fields(payout_no, {
                "status": PAYOUT_STATUS_PAYING,
                "failReason": "",
            })
            logger.info("payout_retry payoutNo=%s retryCount=%d", payout_no, retry_count)
            return {
                "success": True,
                "payoutNo": payout_no,
                "status": PAYOUT_STATUS_PAYING,
                "statusName": PAYOUT_STATUS_NAMES[PAYOUT_STATUS_PAYING],
                "retryCount": retry_count,
            }

    async def get_payout(self, payout_no: str) -> dict:
        """查询付款详情

        Raises:
            KeyError: 付款单不存在
        """
        payout = await self.repo.get_payout(payout_no)
        if not payout:
            raise KeyError(f"付款单 {payout_no} 不存在")
        return {
            "success": True,
            **payout,
            "statusName": PAYOUT_STATUS_NAMES.get(payout.get("status"), "未知"),
            "payeeAccountMasked": _mask_account(payout.get("payeeAccount", "")),
            "payeePhoneMasked": _mask_phone(payout.get("payeePhone", "")),
        }

    async def list_payouts(self, payout_type: str = None, status: str = None,
                             limit: int = 50) -> dict:
        """列出付款记录(管理端)"""
        payouts = await self.repo.list_payouts(payout_type, status, limit)
        return {
            "success": True,
            "count": len(payouts),
            "items": [
                {
                    **p,
                    "statusName": PAYOUT_STATUS_NAMES.get(p.get("status"), "未知"),
                    "payeeAccountMasked": _mask_account(p.get("payeeAccount", "")),
                }
                for p in payouts
            ],
        }

    async def list_pending_payouts(self, limit: int = 100) -> dict:
        """列出待审核付款(管理端审批用)"""
        payouts = await self.repo.list_pending_payouts(limit)
        return {
            "success": True,
            "count": len(payouts),
            "items": [
                {
                    **p,
                    "statusName": PAYOUT_STATUS_NAMES.get(p.get("status"), "未知"),
                    "payeeAccountMasked": _mask_account(p.get("payeeAccount", "")),
                }
                for p in payouts
            ],
        }

    # ============================================================
    # 对账记录(P1)
    # ============================================================

    async def start_reconciliation(self, recon_date: str, channel: str,
                                     operator: str = "system") -> dict:
        """启动日终对账(获取锁 + 创建批次 + 比对流水 + 生成差异)

        流程:
            1. 获取对账锁(防并发对账同一日同一渠道)
            2. 创建对账批次(pending)
            3. 查询平台当日 paid 支付单
            4. 模拟拉取渠道流水(实际由渠道提供对账文件)
            5. 逐笔比对(按 channelTradeNo 匹配)
            6. 生成差异明细 + 更新状态(matched/diff)

        Raises:
            ValueError: 该日该渠道已对账或正在对账中
        """
        recon_no = f"RECON{recon_date.replace('-', '')}{channel.upper()}"

        # 1. 获取对账锁
        ok = await self.repo.acquire_recon_lock(recon_date, channel)
        if not ok:
            raise ValueError(f"{recon_date} 渠道 {channel} 已在对账中或已对账")

        # 2. 创建对账批次
        await self.repo.create_recon({
            "reconNo": recon_no,
            "reconDate": recon_date,
            "channel": channel,
            "status": RECON_STATUS_PENDING,
            "statusName": RECON_STATUS_NAMES[RECON_STATUS_PENDING],
            "platformCount": 0,
            "platformAmount": 0.0,
            "channelCount": 0,
            "channelAmount": 0.0,
            "matchType": MATCH_TYPE_FULL,
            "diffCount": 0,
            "diffAmount": 0.0,
            "diffDetails": [],
            "reconFile": f"/recon/{channel}/{recon_date.replace('-', '')}.csv",
            "startedAt": ts(),
            "operator": operator,
            "remark": "日终自动对账",
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        # 3. 查询平台当日已支付订单(按渠道筛选)
        platform_pays = await self.repo.list_orders(
            user_id=None, status=PAY_STATUS_PAID, limit=10000)
        # 过滤当日 + 指定渠道(实际应由 repo 按 date/channel 查询,此处简化)
        platform_items = {}
        platform_total = 0.0
        for p in platform_pays:
            if p.get("payChannel") != channel:
                continue
            ctn = p.get("channelTradeNo")
            if not ctn:
                continue
            platform_items[ctn] = p
            platform_total += float(p.get("actualAmount", 0))

        # 4. 模拟拉取渠道流水(实际应解析渠道对账文件)
        # 此处用平台数据模拟"完全对平"场景,差异场景由调用方注入
        channel_items = dict(platform_items)
        channel_total = platform_total

        # 5. 逐笔比对
        diff_details = []
        all_ctns = set(platform_items.keys()) | set(channel_items.keys())
        for ctn in all_ctns:
            p_pay = platform_items.get(ctn)
            c_pay = channel_items.get(ctn)
            if p_pay and not c_pay:
                # 平台有/渠道无
                diff_details.append({
                    "payNo": p_pay.get("payNo"),
                    "channelTradeNo": ctn,
                    "type": DIFF_TYPE_PLATFORM_ONLY,
                    "platformAmount": float(p_pay.get("actualAmount", 0)),
                    "channelAmount": 0.0,
                    "diffAmount": float(p_pay.get("actualAmount", 0)),
                    "handleSuggestion": HANDLE_SUGGEST_SUPPLEMENT,
                })
            elif c_pay and not p_pay:
                # 渠道有/平台无
                diff_details.append({
                    "payNo": "",
                    "channelTradeNo": ctn,
                    "type": DIFF_TYPE_CHANNEL_ONLY,
                    "platformAmount": 0.0,
                    "channelAmount": float(c_pay.get("actualAmount", 0)),
                    "diffAmount": -float(c_pay.get("actualAmount", 0)),
                    "handleSuggestion": HANDLE_SUGGEST_REFUND,
                })
            else:
                # 双方都有,比对金额
                p_amt = float(p_pay.get("actualAmount", 0))
                c_amt = float(c_pay.get("actualAmount", 0))
                if abs(p_amt - c_amt) > 0.01:
                    diff_details.append({
                        "payNo": p_pay.get("payNo"),
                        "channelTradeNo": ctn,
                        "type": DIFF_TYPE_AMOUNT_MISMATCH,
                        "platformAmount": p_amt,
                        "channelAmount": c_amt,
                        "diffAmount": round(p_amt - c_amt, 2),
                        "handleSuggestion": HANDLE_SUGGEST_REFUND if p_amt > c_amt else HANDLE_SUGGEST_SUPPLEMENT,
                    })

        # 6. 更新对账状态
        diff_amount = round(sum(d["diffAmount"] for d in diff_details), 2)
        if not diff_details:
            # 完全对平
            await self.repo.update_recon_status(recon_no, RECON_STATUS_MATCHED, extra={
                "platformCount": len(platform_items),
                "platformAmount": round(platform_total, 2),
                "channelCount": len(channel_items),
                "channelAmount": round(channel_total, 2),
                "matchType": MATCH_TYPE_FULL,
                "finishedAt": ts(),
                "updatedAt": ts(),
            })
        else:
            # 存在差异
            await self.repo.update_recon_status(recon_no, RECON_STATUS_DIFF, extra={
                "platformCount": len(platform_items),
                "platformAmount": round(platform_total, 2),
                "channelCount": len(channel_items),
                "channelAmount": round(channel_total, 2),
                "matchType": MATCH_TYPE_PARTIAL if len(diff_details) < len(all_ctns) else MATCH_TYPE_MISMATCH,
                "diffCount": len(diff_details),
                "diffAmount": diff_amount,
                "finishedAt": ts(),
                "updatedAt": ts(),
            })
            # 添加差异明细
            for d in diff_details:
                await self.repo.add_diff_detail(recon_no, d)

        result = await self.repo.get_recon(recon_no)
        logger.info(f"对账完成: {recon_no} 状态={result['status']} 差异={len(diff_details)}笔")
        return result

    async def get_reconciliation(self, recon_no: str) -> dict:
        """查询对账详情

        Raises:
            KeyError: 对账记录不存在
        """
        recon = await self.repo.get_recon(recon_no)
        if not recon:
            raise KeyError(f"对账记录 {recon_no} 不存在")
        return recon

    async def list_reconciliations(self, recon_date: str = None, channel: str = None,
                                     status: str = None, limit: int = 50) -> dict:
        """对账记录列表"""
        items = await self.repo.list_recons(recon_date, channel, status, limit)
        return {
            "success": True,
            "count": len(items),
            "items": items,
        }

    async def list_pending_diffs(self, limit: int = 100) -> dict:
        """待处理差异列表(管理端查询)"""
        items = await self.repo.list_pending_diffs(limit)
        return {
            "success": True,
            "count": len(items),
            "items": items,
        }

    async def investigate_diff(self, recon_no: str, operator: str,
                                  remark: str = "") -> dict:
        """介入调查(diff → investigating)

        Raises:
            KeyError: 对账记录不存在
            ValueError: 状态非法(非 diff 状态不可调查)
        """
        recon = await self.repo.get_recon(recon_no)
        if not recon:
            raise KeyError(f"对账记录 {recon_no} 不存在")
        if recon["status"] != RECON_STATUS_DIFF:
            raise ValueError(f"对账记录状态为 {recon['status']}, 不可介入调查")
        return await self.repo.update_recon_status(recon_no, RECON_STATUS_INVESTIGATING, extra={
            "operator": operator,
            "remark": remark or "介入调查",
            "updatedAt": ts(),
        })

    async def resolve_reconciliation(self, recon_no: str, operator: str,
                                       remark: str = "") -> dict:
        """处理完成(investigating → resolved)

        Raises:
            KeyError: 对账记录不存在
            ValueError: 状态非法(非 investigating 状态不可处理完成)
        """
        recon = await self.repo.get_recon(recon_no)
        if not recon:
            raise KeyError(f"对账记录 {recon_no} 不存在")
        if recon["status"] != RECON_STATUS_INVESTIGATING:
            raise ValueError(f"对账记录状态为 {recon['status']}, 不可处理完成")
        return await self.repo.update_recon_status(recon_no, RECON_STATUS_RESOLVED, extra={
            "operator": operator,
            "remark": remark or "已处理完成",
            "updatedAt": ts(),
        })

    # ============================================================
    # 渠道配置(P1)
    # ============================================================

    async def create_channel(self, channel_code: str, channel_name: str,
                                channel_type: str, supported_methods: list,
                                supported_scenes: list, merchant_id: str,
                                fee_rate: float, fee_type: str = FEE_TYPE_RATIO,
                                fixed_fee: float = 0.0, settle_cycle: str = "T+1",
                                min_amount: float = 0.01, max_amount: float = 50000.0,
                                daily_limit: float = 500000.0,
                                monthly_limit: float = 5000000.0,
                                retry_max: int = 8, timeout: int = 1800,
                                remark: str = "") -> dict:
        """创建渠道配置

        Raises:
            ValueError: 渠道编码已存在 / 参数非法
        """
        # 参数校验
        if channel_type not in {CHANNEL_TYPE_THIRD_PARTY, CHANNEL_TYPE_BANK, CHANNEL_TYPE_AGGREGATE}:
            raise ValueError(f"渠道类型非法: {channel_type}")
        if fee_type not in {FEE_TYPE_FIXED, FEE_TYPE_RATIO, FEE_TYPE_MIXED}:
            raise ValueError(f"费率类型非法: {fee_type}")
        if fee_rate < 0 or fee_rate > 1:
            raise ValueError(f"费率 {fee_rate} 超出范围(0-1)")
        if min_amount > max_amount:
            raise ValueError(f"单笔最小 {min_amount} 大于最大 {max_amount}")
        if daily_limit > monthly_limit:
            raise ValueError(f"单日限额 {daily_limit} 大于单月 {monthly_limit}")

        return await self.repo.create_channel({
            "channelCode": channel_code,
            "channelName": channel_name,
            "channelType": channel_type,
            "status": CHANNEL_STATUS_ACTIVE,
            "statusName": CHANNEL_STATUS_NAMES[CHANNEL_STATUS_ACTIVE],
            "supportedMethods": supported_methods,
            "supportedScenes": supported_scenes,
            "merchantId": merchant_id,
            "appId": "",
            "apiEndpoint": "",
            "notifyUrl": "",
            "signType": "RSA2",
            "signKey": "",
            "certPath": "",
            "feeRate": fee_rate,
            "feeType": fee_type,
            "fixedFee": fixed_fee,
            "settleCycle": settle_cycle,
            "minAmount": min_amount,
            "maxAmount": max_amount,
            "dailyLimit": daily_limit,
            "monthlyLimit": monthly_limit,
            "dailyAmount": 0.0,
            "dailyCount": 0,
            "monthlyAmount": 0.0,
            "retryMax": retry_max,
            "timeout": timeout,
            "remark": remark,
            "createdAt": ts(),
            "updatedAt": ts(),
        })

    async def get_channel(self, channel_code: str) -> dict:
        """查询渠道配置

        Raises:
            KeyError: 渠道不存在
        """
        ch = await self.repo.get_channel(channel_code)
        if not ch:
            raise KeyError(f"渠道 {channel_code} 不存在")
        return ch

    async def update_channel(self, channel_code: str, fields: dict) -> dict:
        """更新渠道配置(部分字段)

        Raises:
            KeyError: 渠道不存在
            ValueError: 参数非法
        """
        # 费率校验
        if "feeRate" in fields:
            rate = float(fields["feeRate"])
            if rate < 0 or rate > 1:
                raise ValueError(f"费率 {rate} 超出范围(0-1)")
        # 限额校验
        if "minAmount" in fields and "maxAmount" in fields and float(fields["minAmount"]) > float(fields["maxAmount"]):
            raise ValueError("单笔最小大于最大")
        fields["updatedAt"] = ts()
        return await self.repo.update_channel_fields(channel_code, fields)

    async def toggle_channel_status(self, channel_code: str, status: str,
                                      operator: str = "admin") -> dict:
        """启停渠道

        Raises:
            KeyError: 渠道不存在
            ValueError: 状态非法
        """
        ch = await self.repo.get_channel(channel_code)
        if not ch:
            raise KeyError(f"渠道 {channel_code} 不存在")
        if ch["status"] == status:
            raise ValueError(f"渠道已是 {CHANNEL_STATUS_NAMES[status]} 状态")
        result = await self.repo.update_channel_status(channel_code, status)
        logger.info(f"渠道 {channel_code} 状态变更为 {status}, 操作人={operator}")
        return result

    async def list_channels(self, status: str = None,
                               channel_type: str = None, limit: int = 50) -> dict:
        """渠道列表"""
        items = await self.repo.list_channels(status, channel_type, limit)
        return {
            "success": True,
            "count": len(items),
            "items": items,
        }

    async def list_active_channels(self) -> dict:
        """启用的渠道列表(支付下单时选择渠道)"""
        items = await self.repo.list_active_channels()
        return {
            "success": True,
            "count": len(items),
            "items": items,
        }

    async def check_channel_limit(self, channel_code: str, amount: float) -> dict:
        """限额校验(创建支付时调用)

        Returns:
            {"passed": bool, "reason": str}

        Raises:
            KeyError: 渠道不存在
            ValueError: 渠道未启用
        """
        ch = await self.repo.get_channel(channel_code)
        if not ch:
            raise KeyError(f"渠道 {channel_code} 不存在")
        if ch["status"] != CHANNEL_STATUS_ACTIVE:
            raise ValueError(f"渠道 {channel_code} 当前状态为 {ch['statusName']}, 不可用")
        return await self.repo.check_limit(channel_code, amount)

    async def record_channel_transaction(self, channel_code: str, amount: float) -> dict:
        """累计渠道交易额(支付成功后调用)

        Raises:
            KeyError: 渠道不存在
        """
        return await self.repo.add_transaction_amount(channel_code, amount)

    async def reset_daily_stats(self) -> dict:
        """重置所有渠道日累计(每日 00:00 定时任务)"""
        count = await self.repo.reset_daily_stats()
        logger.info(f"日累计统计已重置, 渠道数={count}")
        return {"success": True, "count": count}

    async def reset_monthly_stats(self) -> dict:
        """重置所有渠道月累计(每月 1 日定时任务)"""
        count = await self.repo.reset_monthly_stats()
        logger.info(f"月累计统计已重置, 渠道数={count}")
        return {"success": True, "count": count}


# ============================================================
# 辅助
# ============================================================

def _safe_json_dumps(obj) -> str:
    """安全 JSON 序列化(失败返回字符串)"""
    import json
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)
