"""收款管理模块 Repository

双模式(内存/Redis)透明切换,3 个 P0 数据实体:
    - payment_orders(支付订单):  主信息, pay_no 主键, 按 user_id/order_id 索引
    - payment_refunds(退款记录): 主信息, refund_no 主键, 按 pay_no 索引, pending 集合
    - payment_payouts(平台付款): 主信息, payout_no 主键, 按 source_id 索引, pending 集合

锁键: payment:order:{orderId} / payment:callback:{channelTradeNo}
     payment:refund:{payNo} / payment:payout:{payoutNo}
     (并发安全由 services 层负责)

Redis Key 设计:
    payment:order:{payNo}             Hash(支付订单主信息, 支持 HINCRBYFLOAT 累计金额)
    payment:order:seq:{prefix}        String(INCR 序号, 同前缀共用)
    payment:order:index:user:{uid}    Set(用户支付单索引)
    payment:order:index:order:{oid}   Set(订单支付单索引, 幂等校验用)
    payment:callback:lock:{ctNo}      String(回调幂等锁, TTL 24h)

    payment:refund:{refundNo}         String(JSON)(退款记录)
    payment:refund:seq:{prefix}       String(INCR 序号)
    payment:refund:index:pay:{payNo}  Set(支付单关联的退款单)
    payment:refund:pending            Set(待审核退款单集合)
    payment:refund:callback:lock:{crNo} String(退款回调幂等锁, TTL 24h)

    payment:payout:{payoutNo}         String(JSON)(付款记录)
    payment:payout:seq:{prefix}       String(INCR 序号)
    payment:payout:pending            Set(待审核付款集合)
    payment:payout:index:source:{sid} Set(按来源单据索引)
"""

import json
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 序列号格式辅助(与 wallet_repository 风格一致)
# ============================================================

def _pay_no_prefix() -> str:
    """支付单号前缀: PAY + YYYYMMDD"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"PAY{now.strftime('%Y%m%d')}"


def _refund_no_prefix() -> str:
    """退款单号前缀: RF + YYYYMMDD"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"RF{now.strftime('%Y%m%d')}"


def _payout_no_prefix() -> str:
    """付款单号前缀: PO + YYYYMMDD"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"PO{now.strftime('%Y%m%d')}"


# ============================================================
# 状态常量(对齐设计文档状态机)
# ============================================================

# 支付订单状态
PAY_STATUS_PENDING = "pending"      # 待支付(已创建,未发起渠道请求)
PAY_STATUS_PAYING = "paying"        # 支付中(已发起渠道请求,等待回调)
PAY_STATUS_PAID = "paid"            # 已支付
PAY_STATUS_FAILED = "failed"        # 支付失败(可重试)
PAY_STATUS_CLOSED = "closed"        # 已关闭(超时/主动取消,终态)
PAY_STATUS_REFUNDING = "refunding"  # 退款中
PAY_STATUS_REFUNDED = "refunded"    # 已退款(终态)

# 退款状态
REFUND_STATUS_PENDING = "pending"       # 待审核
REFUND_STATUS_AUDITING = "auditing"     # 审核中
REFUND_STATUS_APPROVED = "approved"     # 审核通过,待渠道退款
REFUND_STATUS_REJECTED = "rejected"     # 审核拒绝(终态)
REFUND_STATUS_REFUNDED = "refunded"     # 已退款(终态)
REFUND_STATUS_CANCELLED = "cancelled"   # 已撤回(终态)

# 付款状态(平台对外付款)
PAYOUT_STATUS_PENDING = "pending"       # 待审核
PAYOUT_STATUS_AUDITING = "auditing"     # 审核中
PAYOUT_STATUS_APPROVED = "approved"     # 审核通过,待打款
PAYOUT_STATUS_PAYING = "paying"         # 打款中
PAYOUT_STATUS_PAID = "paid"             # 已打款(终态)
PAYOUT_STATUS_FAILED = "failed"         # 打款失败(可重试)
PAYOUT_STATUS_REJECTED = "rejected"     # 审核拒绝(终态)
PAYOUT_STATUS_CANCELLED = "cancelled"  # 已撤回(终态)

# 非终态集合(用于查询"进行中"的单据)
# 设计原则: 同一订单只能有一个"非关闭"支付单, paid 也算活跃(已支付但可能退款)
PAY_ACTIVE_STATUSES = {
    PAY_STATUS_PENDING, PAY_STATUS_PAYING, PAY_STATUS_PAID,
    PAY_STATUS_FAILED, PAY_STATUS_REFUNDING,
}
# 退款"待审核"集合(管理员需处理的单据: pending + auditing)
# approved 是已通过, 不在待审核集合
REFUND_ACTIVE_STATUSES = {
    REFUND_STATUS_PENDING, REFUND_STATUS_AUDITING,
}
# 付款"待审核"集合(同上: pending + auditing)
PAYOUT_ACTIVE_STATUSES = {
    PAYOUT_STATUS_PENDING, PAYOUT_STATUS_AUDITING,
}


class PaymentRepository:
    """收款数据访问(双模式)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成(按前缀 INCR, 序号补 4 位)
    # ============================================================

    async def next_pay_no(self) -> str:
        """生成下一个支付单号: PAY + YYYYMMDD + 4 位序号"""
        if is_redis_mode():
            return await self._redis_next_seq_no("order:seq", _pay_no_prefix())
        return self._mem_next_seq_no("_payment_order_seq", _pay_no_prefix())

    async def next_refund_no(self) -> str:
        """生成下一个退款单号: RF + YYYYMMDD + 4 位序号"""
        if is_redis_mode():
            return await self._redis_next_seq_no("refund:seq", _refund_no_prefix())
        return self._mem_next_seq_no("_payment_refund_seq", _refund_no_prefix())

    async def next_payout_no(self) -> str:
        """生成下一个付款单号: PO + YYYYMMDD + 4 位序号"""
        if is_redis_mode():
            return await self._redis_next_seq_no("payout:seq", _payout_no_prefix())
        return self._mem_next_seq_no("_payment_payout_seq", _payout_no_prefix())

    def _mem_next_seq_no(self, counter_key: str, prefix: str) -> str:
        """内存模式序列号生成(全局计数器, 按 prefix 复用)"""
        self._ensure_store()
        full_key = f"{counter_key}:{prefix}"
        self.store[full_key] = self.store.get(full_key, 0) + 1
        return f"{prefix}{self.store[full_key]:04d}"

    async def _redis_next_seq_no(self, seq_key: str, prefix: str) -> str:
        """Redis 模式序列号生成(INCR 原子自增)"""
        client = await get_redis_client()
        # 同一 prefix 共用一个计数器, 避免不同日期 prefix 复用导致重号
        full_key = _k("payment", seq_key, prefix)
        n = await client.incr(full_key)
        return f"{prefix}{n:04d}"

    # ============================================================
    # 支付订单(payment_orders)
    # ============================================================

    async def save_order(self, order: dict) -> dict:
        """新增支付订单(含 user_id / order_id 索引)

        Raises:
            ValueError: payNo 已存在(重复保存)
        """
        if is_redis_mode():
            return await self._redis_save_order(order)
        return self._mem_save_order(order)

    async def get_order(self, pay_no: str) -> Optional[dict]:
        """按支付单号查询"""
        if is_redis_mode():
            return await self._redis_get_order(pay_no)
        return self._mem_get_order(pay_no)

    async def get_by_channel_trade_no(self, channel_trade_no: str) -> Optional[dict]:
        """按渠道交易号查询(回调幂等校验用)

        渠道回调时, 渠道只传 channel_trade_no, 需反向定位支付单
        """
        if is_redis_mode():
            return await self._redis_get_by_channel_trade_no(channel_trade_no)
        return self._mem_get_by_channel_trade_no(channel_trade_no)

    async def list_orders(self, user_id, status: str = None,
                         scene_type: str = None, limit: int = 50) -> list[dict]:
        """列出用户支付订单(可按 status/sceneType 筛选)"""
        if is_redis_mode():
            return await self._redis_list_orders(user_id, status, scene_type, limit)
        return self._mem_list_orders(user_id, status, scene_type, limit)

    async def list_by_order(self, order_id: str, order_type: str = None,
                            limit: int = 50) -> list[dict]:
        """按订单号查询关联的支付单(幂等校验: 同一订单只能有一个未关闭支付单)"""
        if is_redis_mode():
            return await self._redis_list_by_order(order_id, order_type, limit)
        return self._mem_list_by_order(order_id, order_type, limit)

    async def find_active_by_order(self, order_id: str,
                                    order_type: str = None) -> Optional[dict]:
        """查找订单的活跃支付单(非 closed/refunded 状态)

        用于创建支付时的幂等校验: 同一订单同一类型只能有一个未关闭支付单
        """
        if is_redis_mode():
            return await self._redis_find_active_by_order(order_id, order_type)
        return self._mem_find_active_by_order(order_id, order_type)

    async def update_order_fields(self, pay_no: str, fields: dict) -> dict:
        """部分字段更新(如 status: pending → paying → paid)

        - 若 fields 含 channelTradeNo, 自动维护渠道交易号索引

        Raises:
            KeyError: 支付订单不存在
        """
        if is_redis_mode():
            return await self._redis_update_order_fields(pay_no, fields)
        return self._mem_update_order_fields(pay_no, fields)

    async def add_refunded_amount(self, pay_no: str, amount: float) -> float:
        """累计已退款金额(部分退款场景), 返回新的累计退款金额

        Raises:
            KeyError: 支付订单不存在
        """
        if is_redis_mode():
            return await self._redis_add_refunded_amount(pay_no, amount)
        return self._mem_add_refunded_amount(pay_no, amount)

    async def acquire_callback_lock(self, channel_trade_no: str,
                                     ttl_seconds: int = 86400) -> bool:
        """回调幂等锁(防止回调重复处理)

        SETNX 实现, TTL 24h(足够覆盖渠道重试窗口)
        Returns:
            True 表示首次获取(可处理), False 表示已处理过(幂等返回)
        """
        if is_redis_mode():
            return await self._redis_acquire_callback_lock(
                "callback:lock", channel_trade_no, ttl_seconds)
        return self._mem_acquire_callback_lock(
            "_payment_callback_locks", channel_trade_no)

    async def acquire_refund_callback_lock(self, channel_refund_no: str,
                                           ttl_seconds: int = 86400) -> bool:
        """退款回调幂等锁(防止退款回调重复处理)"""
        if is_redis_mode():
            return await self._redis_acquire_callback_lock(
                "refund:callback:lock", channel_refund_no, ttl_seconds)
        return self._mem_acquire_callback_lock(
            "_payment_refund_callback_locks", channel_refund_no)

    # ============================================================
    # 退款记录(payment_refunds)
    # ============================================================

    async def save_refund(self, refund: dict) -> dict:
        """新增退款记录(含 pay_no 索引 + pending 集合)"""
        if is_redis_mode():
            return await self._redis_save_refund(refund)
        return self._mem_save_refund(refund)

    async def get_refund(self, refund_no: str) -> Optional[dict]:
        """按退款单号查询"""
        if is_redis_mode():
            return await self._redis_get_refund(refund_no)
        return self._mem_get_refund(refund_no)

    async def list_refunds(self, pay_no: str, status: str = None,
                            limit: int = 50) -> list[dict]:
        """列出支付单关联的退款记录(可按 status 筛选)"""
        if is_redis_mode():
            return await self._redis_list_refunds(pay_no, status, limit)
        return self._mem_list_refunds(pay_no, status, limit)

    async def list_pending_refunds(self, limit: int = 100) -> list[dict]:
        """列出待审核退款(管理端审批用)"""
        if is_redis_mode():
            return await self._redis_list_pending_refunds(limit)
        return self._mem_list_pending_refunds(limit)

    async def update_refund_fields(self, refund_no: str, fields: dict) -> dict:
        """部分字段更新(如 status: pending → auditing → approved → refunded)

        - 若 status 从 pending 变为非 pending, 自动从 pending 集合移除
        - 若 status 变为 pending, 自动加入 pending 集合

        Raises:
            KeyError: 退款记录不存在
        """
        if is_redis_mode():
            return await self._redis_update_refund_fields(refund_no, fields)
        return self._mem_update_refund_fields(refund_no, fields)

    async def sum_refunded_amount(self, pay_no: str) -> float:
        """累计某支付单已退款金额(部分退款校验用)

        用于创建退款时校验: 累计退款 + 本次退款 <= 原支付金额
        """
        if is_redis_mode():
            return await self._redis_sum_refunded_amount(pay_no)
        return self._mem_sum_refunded_amount(pay_no)

    # ============================================================
    # 平台付款(payment_payouts)
    # ============================================================

    async def save_payout(self, payout: dict) -> dict:
        """新增付款记录(含 source_id 索引 + pending 集合)"""
        if is_redis_mode():
            return await self._redis_save_payout(payout)
        return self._mem_save_payout(payout)

    async def get_payout(self, payout_no: str) -> Optional[dict]:
        """按付款单号查询"""
        if is_redis_mode():
            return await self._redis_get_payout(payout_no)
        return self._mem_get_payout(payout_no)

    async def list_payouts(self, payout_type: str = None, status: str = None,
                            limit: int = 50) -> list[dict]:
        """列出付款记录(可按 type/status 筛选, 管理端用)"""
        if is_redis_mode():
            return await self._redis_list_payouts(payout_type, status, limit)
        return self._mem_list_payouts(payout_type, status, limit)

    async def list_pending_payouts(self, limit: int = 100) -> list[dict]:
        """列出待审核付款(管理端审批用)"""
        if is_redis_mode():
            return await self._redis_list_pending_payouts(limit)
        return self._mem_list_pending_payouts(limit)

    async def find_by_source(self, source_id: str,
                              payout_type: str = None) -> Optional[dict]:
        """按来源单据查询付款记录(幂等校验: 同一来源只能创建一个付款单)

        例如: wallet_withdraw 提现单号 → payout.sourceId
        """
        if is_redis_mode():
            return await self._redis_find_by_source(source_id, payout_type)
        return self._mem_find_by_source(source_id, payout_type)

    async def update_payout_fields(self, payout_no: str, fields: dict) -> dict:
        """部分字段更新(如 status: pending → auditing → approved → paid)

        - 若 status 从 pending 变为非 pending, 自动从 pending 集合移除
        - 若 status 变为 pending, 自动加入 pending 集合
        - 若 fields 含 retryCount, 自动累加而非覆盖

        Raises:
            KeyError: 付款记录不存在
        """
        if is_redis_mode():
            return await self._redis_update_payout_fields(payout_no, fields)
        return self._mem_update_payout_fields(payout_no, fields)

    async def increment_payout_retry(self, payout_no: str) -> int:
        """付款重试次数 +1, 返回新次数

        Raises:
            KeyError: 付款记录不存在
        """
        if is_redis_mode():
            return await self._redis_increment_payout_retry(payout_no)
        return self._mem_increment_payout_retry(payout_no)

    # ============================================================
    # 内存后端
    # ============================================================

    def _ensure_store(self):
        """确保 store 包含收款相关键(懒初始化)"""
        if "payment_orders" not in self.store:
            self.store["payment_orders"] = {}
        if "payment_refunds" not in self.store:
            self.store["payment_refunds"] = {}
        if "payment_payouts" not in self.store:
            self.store["payment_payouts"] = {}
        # 渠道交易号反向索引(channelTradeNo → payNo, 回调幂等校验用)
        if "_payment_channel_trade_index" not in self.store:
            self.store["_payment_channel_trade_index"] = {}
        # 回调幂等锁集合
        if "_payment_callback_locks" not in self.store:
            self.store["_payment_callback_locks"] = set()
        if "_payment_refund_callback_locks" not in self.store:
            self.store["_payment_refund_callback_locks"] = set()

    # ---------- 支付订单(内存) ----------

    def _mem_save_order(self, order: dict) -> dict:
        self._ensure_store()
        pay_no = order["payNo"]
        if pay_no in self.store["payment_orders"]:
            raise ValueError(f"支付单号 {pay_no} 已存在")
        self.store["payment_orders"][pay_no] = order
        # 用户索引
        user_id = order.get("userId")
        if user_id is not None:
            self.store.setdefault("_payment_order_user_index", {}).setdefault(
                user_id, set()).add(pay_no)
        # 订单索引
        order_id = order.get("orderId")
        if order_id:
            self.store.setdefault("_payment_order_orderid_index", {}).setdefault(
                order_id, set()).add(pay_no)
        # 渠道交易号索引(回调幂等校验)
        ctn = order.get("channelTradeNo")
        if ctn:
            self.store["_payment_channel_trade_index"][ctn] = pay_no
        return order

    def _mem_get_order(self, pay_no: str) -> Optional[dict]:
        self._ensure_store()
        return self.store["payment_orders"].get(pay_no)

    def _mem_get_by_channel_trade_no(self, channel_trade_no: str) -> Optional[dict]:
        self._ensure_store()
        pay_no = self.store["_payment_channel_trade_index"].get(channel_trade_no)
        if not pay_no:
            return None
        return self.store["payment_orders"].get(pay_no)

    def _mem_list_orders(self, user_id, status: str = None,
                         scene_type: str = None, limit: int = 50) -> list[dict]:
        self._ensure_store()
        index_set = self.store.get("_payment_order_user_index", {}).get(user_id, set())
        result = []
        for pn in index_set:
            o = self.store["payment_orders"].get(pn)
            if not o:
                continue
            if status and o.get("status") != status:
                continue
            if scene_type and o.get("sceneType") != scene_type:
                continue
            result.append(o)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    def _mem_list_by_order(self, order_id: str, order_type: str = None,
                           limit: int = 50) -> list[dict]:
        self._ensure_store()
        index_set = self.store.get("_payment_order_orderid_index", {}).get(order_id, set())
        result = []
        for pn in index_set:
            o = self.store["payment_orders"].get(pn)
            if not o:
                continue
            if order_type and o.get("orderType") != order_type:
                continue
            result.append(o)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    def _mem_find_active_by_order(self, order_id: str,
                                   order_type: str = None) -> Optional[dict]:
        """查找订单的活跃支付单(非 closed/refunded 状态)"""
        self._ensure_store()
        index_set = self.store.get("_payment_order_orderid_index", {}).get(order_id, set())
        for pn in index_set:
            o = self.store["payment_orders"].get(pn)
            if not o:
                continue
            if order_type and o.get("orderType") != order_type:
                continue
            if o.get("status") in PAY_ACTIVE_STATUSES:
                return o
        return None

    def _mem_update_order_fields(self, pay_no: str, fields: dict) -> dict:
        self._ensure_store()
        order = self.store["payment_orders"].get(pay_no)
        if not order:
            raise KeyError(pay_no)
        order.update(fields)
        # 维护渠道交易号索引
        ctn = fields.get("channelTradeNo")
        if ctn:
            self.store["_payment_channel_trade_index"][ctn] = pay_no
        return order

    def _mem_add_refunded_amount(self, pay_no: str, amount: float) -> float:
        self._ensure_store()
        order = self.store["payment_orders"].get(pay_no)
        if not order:
            raise KeyError(pay_no)
        current = float(order.get("refundedAmount", 0))
        new_total = round(current + amount, 2)
        order["refundedAmount"] = new_total
        return new_total

    def _mem_acquire_callback_lock(self, lock_set_key: str,
                                   lock_id: str) -> bool:
        self._ensure_store()
        lock_set = self.store.setdefault(lock_set_key, set())
        if lock_id in lock_set:
            return False
        lock_set.add(lock_id)
        return True

    # ---------- 退款记录(内存) ----------

    def _mem_save_refund(self, refund: dict) -> dict:
        self._ensure_store()
        refund_no = refund["refundNo"]
        pay_no = refund.get("payNo")
        self.store["payment_refunds"][refund_no] = refund
        # 支付单索引
        if pay_no:
            self.store.setdefault("_payment_refund_payno_index", {}).setdefault(
                pay_no, set()).add(refund_no)
        # pending 集合
        if refund.get("status") == REFUND_STATUS_PENDING:
            self.store.setdefault("_payment_refund_pending", set()).add(refund_no)
        return refund

    def _mem_get_refund(self, refund_no: str) -> Optional[dict]:
        self._ensure_store()
        return self.store["payment_refunds"].get(refund_no)

    def _mem_list_refunds(self, pay_no: str, status: str = None,
                          limit: int = 50) -> list[dict]:
        self._ensure_store()
        index_set = self.store.get("_payment_refund_payno_index", {}).get(pay_no, set())
        result = []
        for rn in index_set:
            r = self.store["payment_refunds"].get(rn)
            if not r:
                continue
            if status and r.get("status") != status:
                continue
            result.append(r)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    def _mem_list_pending_refunds(self, limit: int = 100) -> list[dict]:
        self._ensure_store()
        pending_set = self.store.get("_payment_refund_pending", set())
        result = []
        for rn in pending_set:
            r = self.store["payment_refunds"].get(rn)
            if r and r.get("status") in REFUND_ACTIVE_STATUSES:
                result.append(r)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    def _mem_update_refund_fields(self, refund_no: str, fields: dict) -> dict:
        self._ensure_store()
        r = self.store["payment_refunds"].get(refund_no)
        if not r:
            raise KeyError(refund_no)
        old_status = r.get("status")
        r.update(fields)
        new_status = r.get("status")
        # 维护 pending 集合(使用统一的"待审核"状态集合)
        pending_set = self.store.setdefault("_payment_refund_pending", set())
        if old_status in REFUND_ACTIVE_STATUSES \
                and new_status not in REFUND_ACTIVE_STATUSES:
            pending_set.discard(refund_no)
        elif old_status not in REFUND_ACTIVE_STATUSES \
                and new_status in REFUND_ACTIVE_STATUSES:
            pending_set.add(refund_no)
        return r

    def _mem_sum_refunded_amount(self, pay_no: str) -> float:
        """累计某支付单已退款金额(仅统计 refunded 状态)"""
        self._ensure_store()
        index_set = self.store.get("_payment_refund_payno_index", {}).get(pay_no, set())
        total = 0.0
        for rn in index_set:
            r = self.store["payment_refunds"].get(rn)
            if r and r.get("status") == REFUND_STATUS_REFUNDED:
                total += float(r.get("refundAmount", 0))
        return round(total, 2)

    # ---------- 平台付款(内存) ----------

    def _mem_save_payout(self, payout: dict) -> dict:
        self._ensure_store()
        payout_no = payout["payoutNo"]
        self.store["payment_payouts"][payout_no] = payout
        # 来源单据索引(幂等校验)
        source_id = payout.get("sourceId")
        if source_id:
            self.store.setdefault("_payment_payout_source_index", {}).setdefault(
                source_id, set()).add(payout_no)
        # pending 集合
        if payout.get("status") in (PAYOUT_STATUS_PENDING, PAYOUT_STATUS_AUDITING):
            self.store.setdefault("_payment_payout_pending", set()).add(payout_no)
        return payout

    def _mem_get_payout(self, payout_no: str) -> Optional[dict]:
        self._ensure_store()
        return self.store["payment_payouts"].get(payout_no)

    def _mem_list_payouts(self, payout_type: str = None, status: str = None,
                          limit: int = 50) -> list[dict]:
        self._ensure_store()
        result = list(self.store["payment_payouts"].values())
        if payout_type:
            result = [p for p in result if p.get("payoutType") == payout_type]
        if status:
            result = [p for p in result if p.get("status") == status]
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    def _mem_list_pending_payouts(self, limit: int = 100) -> list[dict]:
        self._ensure_store()
        pending_set = self.store.get("_payment_payout_pending", set())
        result = []
        for pn in pending_set:
            p = self.store["payment_payouts"].get(pn)
            if p and p.get("status") in PAYOUT_ACTIVE_STATUSES:
                result.append(p)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    def _mem_find_by_source(self, source_id: str,
                             payout_type: str = None) -> Optional[dict]:
        self._ensure_store()
        index_set = self.store.get("_payment_payout_source_index", {}).get(source_id, set())
        for pn in index_set:
            p = self.store["payment_payouts"].get(pn)
            if not p:
                continue
            if payout_type and p.get("payoutType") != payout_type:
                continue
            return p
        return None

    def _mem_update_payout_fields(self, payout_no: str, fields: dict) -> dict:
        self._ensure_store()
        p = self.store["payment_payouts"].get(payout_no)
        if not p:
            raise KeyError(payout_no)
        old_status = p.get("status")
        # retryCount 特殊处理: 累加而非覆盖
        if "retryCount" in fields and "retryCount" in p:
            fields = {**fields, "retryCount": int(p["retryCount"]) + int(fields["retryCount"])}
        p.update(fields)
        new_status = p.get("status")
        # 维护 pending 集合(使用统一的"待审核"状态集合)
        pending_set = self.store.setdefault("_payment_payout_pending", set())
        if old_status in PAYOUT_ACTIVE_STATUSES \
                and new_status not in PAYOUT_ACTIVE_STATUSES:
            pending_set.discard(payout_no)
        elif old_status not in PAYOUT_ACTIVE_STATUSES \
                and new_status in PAYOUT_ACTIVE_STATUSES:
            pending_set.add(payout_no)
        return p

    def _mem_increment_payout_retry(self, payout_no: str) -> int:
        self._ensure_store()
        p = self.store["payment_payouts"].get(payout_no)
        if not p:
            raise KeyError(payout_no)
        p["retryCount"] = int(p.get("retryCount", 0)) + 1
        return p["retryCount"]

    # ============================================================
    # Redis 后端
    # ============================================================

    # ---------- 支付订单(Redis) ----------

    async def _redis_save_order(self, order: dict) -> dict:
        client = await get_redis_client()
        pay_no = order["payNo"]
        key = _k("payment", "order", pay_no)
        # 用 SETNX 保证 payNo 唯一
        acquired = await client.setnx(key, "")
        if not acquired:
            raise ValueError(f"支付单号 {pay_no} 已存在")
        await client.hset(key, mapping=self._serialize_hash(order))
        # 用户索引
        user_id = order.get("userId")
        if user_id is not None:
            await client.sadd(_k("payment", "order", "index", "user", user_id), pay_no)
        # 订单索引
        order_id = order.get("orderId")
        if order_id:
            await client.sadd(_k("payment", "order", "index", "order", order_id), pay_no)
        # 渠道交易号索引
        ctn = order.get("channelTradeNo")
        if ctn:
            await client.set(_k("payment", "ctn", ctn), pay_no)
        return order

    async def _redis_get_order(self, pay_no: str) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hgetall(_k("payment", "order", pay_no))
        if not data:
            return None
        return self._deserialize_order(data)

    async def _redis_get_by_channel_trade_no(self, channel_trade_no: str) -> Optional[dict]:
        client = await get_redis_client()
        pay_no = await client.get(_k("payment", "ctn", channel_trade_no))
        if not pay_no:
            return None
        return await self._redis_get_order(pay_no)

    async def _redis_list_orders(self, user_id, status: str = None,
                                  scene_type: str = None, limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        pay_nos = await client.smembers(_k("payment", "order", "index", "user", user_id))
        result = []
        for pn in pay_nos:
            data = await client.hgetall(_k("payment", "order", pn))
            if not data:
                continue
            o = self._deserialize_order(data)
            if status and o.get("status") != status:
                continue
            if scene_type and o.get("sceneType") != scene_type:
                continue
            result.append(o)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    async def _redis_list_by_order(self, order_id: str, order_type: str = None,
                                    limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        pay_nos = await client.smembers(_k("payment", "order", "index", "order", order_id))
        result = []
        for pn in pay_nos:
            data = await client.hgetall(_k("payment", "order", pn))
            if not data:
                continue
            o = self._deserialize_order(data)
            if order_type and o.get("orderType") != order_type:
                continue
            result.append(o)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    async def _redis_find_active_by_order(self, order_id: str,
                                           order_type: str = None) -> Optional[dict]:
        client = await get_redis_client()
        pay_nos = await client.smembers(_k("payment", "order", "index", "order", order_id))
        for pn in pay_nos:
            data = await client.hgetall(_k("payment", "order", pn))
            if not data:
                continue
            o = self._deserialize_order(data)
            if order_type and o.get("orderType") != order_type:
                continue
            if o.get("status") in PAY_ACTIVE_STATUSES:
                return o
        return None

    async def _redis_update_order_fields(self, pay_no: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("payment", "order", pay_no)
        if not await client.exists(key):
            raise KeyError(pay_no)
        await client.hset(key, mapping=self._serialize_hash(fields))
        # 维护渠道交易号索引
        ctn = fields.get("channelTradeNo")
        if ctn:
            await client.set(_k("payment", "ctn", ctn), pay_no)
        data = await client.hgetall(key)
        return self._deserialize_order(data)

    async def _redis_add_refunded_amount(self, pay_no: str, amount: float) -> float:
        client = await get_redis_client()
        key = _k("payment", "order", pay_no)
        if not await client.exists(key):
            raise KeyError(pay_no)
        new_total = await client.hincrbyfloat(key, "refundedAmount", amount)
        return round(new_total, 2)

    async def _redis_acquire_callback_lock(self, lock_prefix: str,
                                            lock_id: str, ttl: int = 86400) -> bool:
        client = await get_redis_client()
        key = _k("payment", lock_prefix, lock_id)
        # SET NX EX: 首次设置成功返回 True, 已存在返回 None
        acquired = await client.set(key, "1", nx=True, ex=ttl)
        return bool(acquired)

    # ---------- 退款记录(Redis) ----------

    async def _redis_save_refund(self, refund: dict) -> dict:
        client = await get_redis_client()
        refund_no = refund["refundNo"]
        pay_no = refund.get("payNo")
        await client.set(_k("payment", "refund", refund_no),
                         json.dumps(refund, ensure_ascii=False))
        # 支付单索引
        if pay_no:
            await client.sadd(_k("payment", "refund", "index", "pay", pay_no), refund_no)
        # pending 集合(待审核状态)
        if refund.get("status") in REFUND_ACTIVE_STATUSES:
            await client.sadd(_k("payment", "refund", "pending"), refund_no)
        return refund

    async def _redis_get_refund(self, refund_no: str) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("payment", "refund", refund_no))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_refunds(self, pay_no: str, status: str = None,
                                   limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        refund_nos = await client.smembers(_k("payment", "refund", "index", "pay", pay_no))
        result = []
        for rn in refund_nos:
            data = await client.get(_k("payment", "refund", rn))
            if not data:
                continue
            r = json.loads(data)
            if status and r.get("status") != status:
                continue
            result.append(r)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    async def _redis_list_pending_refunds(self, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        pending_nos = await client.smembers(_k("payment", "refund", "pending"))
        result = []
        for rn in pending_nos:
            data = await client.get(_k("payment", "refund", rn))
            if not data:
                continue
            r = json.loads(data)
            if r.get("status") in REFUND_ACTIVE_STATUSES:
                result.append(r)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    async def _redis_update_refund_fields(self, refund_no: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("payment", "refund", refund_no)
        data = await client.get(key)
        if not data:
            raise KeyError(refund_no)
        r = json.loads(data)
        old_status = r.get("status")
        r.update(fields)
        new_status = r.get("status")
        await client.set(key, json.dumps(r, ensure_ascii=False))
        # 维护 pending 集合(使用统一的"待审核"状态集合)
        pending_key = _k("payment", "refund", "pending")
        if old_status in REFUND_ACTIVE_STATUSES \
                and new_status not in REFUND_ACTIVE_STATUSES:
            await client.srem(pending_key, refund_no)
        elif old_status not in REFUND_ACTIVE_STATUSES \
                and new_status in REFUND_ACTIVE_STATUSES:
            await client.sadd(pending_key, refund_no)
        return r

    async def _redis_sum_refunded_amount(self, pay_no: str) -> float:
        client = await get_redis_client()
        refund_nos = await client.smembers(_k("payment", "refund", "index", "pay", pay_no))
        total = 0.0
        for rn in refund_nos:
            data = await client.get(_k("payment", "refund", rn))
            if not data:
                continue
            r = json.loads(data)
            if r.get("status") == REFUND_STATUS_REFUNDED:
                total += float(r.get("refundAmount", 0))
        return round(total, 2)

    # ---------- 平台付款(Redis) ----------

    async def _redis_save_payout(self, payout: dict) -> dict:
        client = await get_redis_client()
        payout_no = payout["payoutNo"]
        await client.set(_k("payment", "payout", payout_no),
                         json.dumps(payout, ensure_ascii=False))
        # 来源单据索引
        source_id = payout.get("sourceId")
        if source_id:
            await client.sadd(_k("payment", "payout", "index", "source", source_id), payout_no)
        # pending 集合(待审核状态)
        if payout.get("status") in PAYOUT_ACTIVE_STATUSES:
            await client.sadd(_k("payment", "payout", "pending"), payout_no)
        return payout

    async def _redis_get_payout(self, payout_no: str) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("payment", "payout", payout_no))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_payouts(self, payout_type: str = None, status: str = None,
                                   limit: int = 50) -> list[dict]:
        """Redis 模式无全局列表索引, 用 keys 扫描(数据量小可接受)
        生产环境建议加 payment:payout:index:all Set
        """
        client = await get_redis_client()
        # 扫描所有 payout 键
        keys = []
        async for key in client.scan_iter(match=_k("payment", "payout", "PO*")):
            keys.append(key)
        result = []
        for key in keys:
            data = await client.get(key)
            if not data:
                continue
            p = json.loads(data)
            if payout_type and p.get("payoutType") != payout_type:
                continue
            if status and p.get("status") != status:
                continue
            result.append(p)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    async def _redis_list_pending_payouts(self, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        pending_nos = await client.smembers(_k("payment", "payout", "pending"))
        result = []
        for pn in pending_nos:
            data = await client.get(_k("payment", "payout", pn))
            if not data:
                continue
            p = json.loads(data)
            if p.get("status") in PAYOUT_ACTIVE_STATUSES:
                result.append(p)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result[:limit]

    async def _redis_find_by_source(self, source_id: str,
                                    payout_type: str = None) -> Optional[dict]:
        client = await get_redis_client()
        payout_nos = await client.smembers(_k("payment", "payout", "index", "source", source_id))
        for pn in payout_nos:
            data = await client.get(_k("payment", "payout", pn))
            if not data:
                continue
            p = json.loads(data)
            if payout_type and p.get("payoutType") != payout_type:
                continue
            return p
        return None

    async def _redis_update_payout_fields(self, payout_no: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("payment", "payout", payout_no)
        data = await client.get(key)
        if not data:
            raise KeyError(payout_no)
        p = json.loads(data)
        old_status = p.get("status")
        # retryCount 特殊处理: 累加而非覆盖
        if "retryCount" in fields and "retryCount" in p:
            fields = {**fields, "retryCount": int(p["retryCount"]) + int(fields["retryCount"])}
        p.update(fields)
        new_status = p.get("status")
        await client.set(key, json.dumps(p, ensure_ascii=False))
        # 维护 pending 集合(使用统一的"待审核"状态集合)
        pending_key = _k("payment", "payout", "pending")
        if old_status in PAYOUT_ACTIVE_STATUSES \
                and new_status not in PAYOUT_ACTIVE_STATUSES:
            await client.srem(pending_key, payout_no)
        elif old_status not in PAYOUT_ACTIVE_STATUSES \
                and new_status in PAYOUT_ACTIVE_STATUSES:
            await client.sadd(pending_key, payout_no)
        return p

    async def _redis_increment_payout_retry(self, payout_no: str) -> int:
        client = await get_redis_client()
        key = _k("payment", "payout", payout_no)
        data = await client.get(key)
        if not data:
            raise KeyError(payout_no)
        p = json.loads(data)
        p["retryCount"] = int(p.get("retryCount", 0)) + 1
        await client.set(key, json.dumps(p, ensure_ascii=False))
        return p["retryCount"]

    # ============================================================
    # 序列化辅助(Redis Hash 要求 value 为 str/int/float)
    # ============================================================

    def _serialize_hash(self, data: dict) -> dict:
        """将 dict 序列化为 Redis Hash 兼容的 mapping

        - None 跳过
        - bool → 0/1
        - list/dict → JSON 字符串
        - int/float 原样保留
        - 其他 → str
        """
        result = {}
        for k, v in data.items():
            if v is None:
                continue
            if isinstance(v, bool):
                result[k] = 1 if v else 0
            elif isinstance(v, (list, dict)):
                result[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, (int, float)):
                result[k] = v
            else:
                result[k] = str(v)
        return result

    def _deserialize_order(self, data: dict) -> dict:
        """将 Redis hgetall 返回的支付订单 dict 反序列化

        金额字段还原为 float, 其他字段保持原样
        """
        def _to_number(v):
            if v is None:
                return None
            try:
                if "." in str(v):
                    return float(v)
                return int(v)
            except (TypeError, ValueError):
                return v

        result = dict(data)
        # 金额字段
        amount_fields = {
            "totalAmount", "discountAmount", "pointsAmount",
            "actualAmount", "refundedAmount",
        }
        for k in amount_fields:
            if k in result:
                result[k] = _to_number(result[k])
        return result
