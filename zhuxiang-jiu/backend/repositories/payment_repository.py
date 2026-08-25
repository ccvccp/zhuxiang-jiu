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


# 对账状态(P1)
RECON_STATUS_PENDING = "pending"            # 待对账
RECON_STATUS_MATCHED = "matched"            # 已对平
RECON_STATUS_DIFF = "diff"                  # 存在差异
RECON_STATUS_INVESTIGATING = "investigating" # 调查中
RECON_STATUS_RESOLVED = "resolved"          # 已处理(终态)

RECON_STATUS_NAMES = {
    RECON_STATUS_PENDING: "待对账",
    RECON_STATUS_MATCHED: "已对平",
    RECON_STATUS_DIFF: "存在差异",
    RECON_STATUS_INVESTIGATING: "调查中",
    RECON_STATUS_RESOLVED: "已处理",
}

# 对账类型
MATCH_TYPE_FULL = "full"        # 完全对平
MATCH_TYPE_PARTIAL = "partial"  # 部分对平(有差异但已处理)
MATCH_TYPE_MISMATCH = "mismatch" # 完全不匹配

# 差异类型
DIFF_TYPE_AMOUNT_MISMATCH = "amount_mismatch"  # 金额不一致
DIFF_TYPE_PLATFORM_ONLY = "platform_only"      # 平台有/渠道无
DIFF_TYPE_CHANNEL_ONLY = "channel_only"        # 渠道有/平台无

# 差异处理建议
HANDLE_SUGGEST_REFUND = "refund"        # 退款(平台多收)
HANDLE_SUGGEST_SUPPLEMENT = "supplement" # 补单(平台少收)
HANDLE_SUGGEST_IGNORE = "ignore"        # 忽略(误差范围内)


# 渠道状态(P1)
CHANNEL_STATUS_ACTIVE = "active"             # 启用
CHANNEL_STATUS_MAINTENANCE = "maintenance"   # 维护中
CHANNEL_STATUS_DISABLED = "disabled"          # 停用

CHANNEL_STATUS_NAMES = {
    CHANNEL_STATUS_ACTIVE: "启用",
    CHANNEL_STATUS_MAINTENANCE: "维护中",
    CHANNEL_STATUS_DISABLED: "停用",
}

# 渠道类型
CHANNEL_TYPE_THIRD_PARTY = "third_party"  # 第三方支付(微信/支付宝)
CHANNEL_TYPE_BANK = "bank"                # 银行直连
CHANNEL_TYPE_AGGREGATE = "aggregate"       # 聚合支付

# 费率类型
FEE_TYPE_FIXED = "fixed"     # 固定手续费
FEE_TYPE_RATIO = "ratio"     # 比例手续费
FEE_TYPE_MIXED = "mixed"     # 混合(比例 + 固定)


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

    async def get_order(self, pay_no: str) -> dict | None:
        """按支付单号查询"""
        if is_redis_mode():
            return await self._redis_get_order(pay_no)
        return self._mem_get_order(pay_no)

    async def get_by_channel_trade_no(self, channel_trade_no: str) -> dict | None:
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
                                    order_type: str = None) -> dict | None:
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

    async def get_refund(self, refund_no: str) -> dict | None:
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

    async def get_payout(self, payout_no: str) -> dict | None:
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
                              payout_type: str = None) -> dict | None:
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

    def _mem_get_order(self, pay_no: str) -> dict | None:
        self._ensure_store()
        return self.store["payment_orders"].get(pay_no)

    def _mem_get_by_channel_trade_no(self, channel_trade_no: str) -> dict | None:
        self._ensure_store()
        pay_no = self.store["_payment_channel_trade_index"].get(channel_trade_no)
        if not pay_no:
            return None
        return self.store["payment_orders"].get(pay_no)

    def _mem_list_orders(self, user_id, status: str = None,
                         scene_type: str = None, limit: int = 50) -> list[dict]:
        self._ensure_store()
        table = self.store.get("payment_orders", {})
        # user_id=None 时返回所有用户的订单(对账场景)
        if user_id is not None:
            index_set = self.store.get("_payment_order_user_index", {}).get(user_id, set())
            candidates = [table.get(pn) for pn in index_set if pn in table]
        else:
            candidates = list(table.values())
        result = []
        for o in candidates:
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
                                   order_type: str = None) -> dict | None:
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

    def _mem_get_refund(self, refund_no: str) -> dict | None:
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

    def _mem_get_payout(self, payout_no: str) -> dict | None:
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
                             payout_type: str = None) -> dict | None:
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

    async def _redis_get_order(self, pay_no: str) -> dict | None:
        client = await get_redis_client()
        data = await client.hgetall(_k("payment", "order", pay_no))
        if not data:
            return None
        return self._deserialize_order(data)

    async def _redis_get_by_channel_trade_no(self, channel_trade_no: str) -> dict | None:
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
                                           order_type: str = None) -> dict | None:
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

    async def _redis_get_refund(self, refund_no: str) -> dict | None:
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

    async def _redis_get_payout(self, payout_no: str) -> dict | None:
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
                                    payout_type: str = None) -> dict | None:
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
    # 对账记录(payment_reconciliation, P1)
    # ============================================================

    async def create_recon(self, recon: dict) -> dict:
        """创建对账批次(含日期/渠道索引 + diff_pending 集合)

        Raises:
            ValueError: reconNo 已存在
        """
        if is_redis_mode():
            return await self._redis_create_recon(recon)
        return self._mem_create_recon(recon)

    async def get_recon(self, recon_no: str) -> dict | None:
        """按对账批次号查询"""
        if is_redis_mode():
            return await self._redis_get_recon(recon_no)
        return self._mem_get_recon(recon_no)

    async def update_recon_fields(self, recon_no: str, fields: dict) -> dict:
        """部分字段更新

        Raises:
            KeyError: 对账记录不存在
        """
        if is_redis_mode():
            return await self._redis_update_recon_fields(recon_no, fields)
        return self._mem_update_recon_fields(recon_no, fields)

    async def update_recon_status(self, recon_no: str, status: str,
                                    extra: dict = None) -> dict:
        """更新对账状态(状态机: pending → matched/diff → investigating → resolved)

        - diff 状态自动加入 diff_pending 集合
        - resolved 状态自动从 diff_pending 移除

        Raises:
            KeyError: 对账记录不存在
        """
        fields = {"status": status,
                   "statusName": RECON_STATUS_NAMES.get(status, status)}
        if extra:
            fields.update(extra)
        return await self.update_recon_fields(recon_no, fields)

    async def list_recons(self, recon_date: str = None, channel: str = None,
                            status: str = None, limit: int = 50) -> list[dict]:
        """列出对账记录(可按 date/channel/status 筛选)"""
        if is_redis_mode():
            return await self._redis_list_recons(recon_date, channel, status, limit)
        return self._mem_list_recons(recon_date, channel, status, limit)

    async def list_pending_diffs(self, limit: int = 100) -> list[dict]:
        """列出待处理差异(管理端查询, status ∈ {diff, investigating})"""
        if is_redis_mode():
            return await self._redis_list_pending_diffs(limit)
        return self._mem_list_pending_diffs(limit)

    async def add_diff_detail(self, recon_no: str, diff_detail: dict) -> dict:
        """添加差异明细到 diffDetails 数组(并累加 diffCount)

        Raises:
            KeyError: 对账记录不存在
        """
        if is_redis_mode():
            return await self._redis_add_diff_detail(recon_no, diff_detail)
        return self._mem_add_diff_detail(recon_no, diff_detail)

    async def acquire_recon_lock(self, recon_date: str, channel: str,
                                    ttl_seconds: int = 3600) -> bool:
        """获取对账锁(防止并发对账同一日同一渠道, TTL 1h)

        Returns:
            True 表示获取成功(可对账), False 表示正在对账中
        """
        if is_redis_mode():
            return await self._redis_acquire_recon_lock(recon_date, channel, ttl_seconds)
        return self._mem_acquire_recon_lock(recon_date, channel)

    # ---------- 对账记录: 内存模式 ----------

    def _mem_create_recon(self, recon: dict) -> dict:
        self._ensure_store()
        if "payment_reconciliation" not in self.store:
            self.store["payment_reconciliation"] = {}
        table = self.store["payment_reconciliation"]
        recon_no = recon["reconNo"]
        if recon_no in table:
            raise ValueError(f"对账批次 {recon_no} 已存在")
        # 确保 diffDetails 为 list
        recon.setdefault("diffDetails", [])
        recon.setdefault("diffCount", 0)
        recon.setdefault("diffAmount", 0.0)
        table[recon_no] = recon
        # 维护索引
        date_idx = f"_payment_recon_index:{recon['reconDate']}"
        self.store.setdefault(date_idx, set()).add(recon_no)
        chan_idx = f"_payment_recon_index:{recon['channel']}"
        self.store.setdefault(chan_idx, set()).add(recon_no)
        # 维护 diff_pending 集合(创建时若状态为 diff/investigating)
        status = recon.get("status")
        if status in (RECON_STATUS_DIFF, RECON_STATUS_INVESTIGATING):
            self.store.setdefault("_payment_recon_diff_pending", set()).add(recon_no)
        return recon

    def _mem_get_recon(self, recon_no: str) -> dict | None:
        self._ensure_store()
        table = self.store.get("payment_reconciliation", {})
        return table.get(recon_no)

    def _mem_update_recon_fields(self, recon_no: str, fields: dict) -> dict:
        self._ensure_store()
        table = self.store.get("payment_reconciliation", {})
        if recon_no not in table:
            raise KeyError(recon_no)
        table[recon_no].update(fields)
        # 维护 diff_pending 集合
        new_status = fields.get("status")
        if new_status:
            pending_set = self.store.setdefault("_payment_recon_diff_pending", set())
            if new_status in (RECON_STATUS_DIFF, RECON_STATUS_INVESTIGATING):
                pending_set.add(recon_no)
            elif new_status in (RECON_STATUS_MATCHED, RECON_STATUS_RESOLVED):
                pending_set.discard(recon_no)
        return table[recon_no]

    def _mem_list_recons(self, recon_date, channel, status, limit):
        self._ensure_store()
        table = self.store.get("payment_reconciliation", {})
        # 优先用索引加速
        if recon_date:
            idx = self.store.get(f"_payment_recon_index:{recon_date}", set())
            items = [table[k] for k in idx if k in table]
        elif channel:
            idx = self.store.get(f"_payment_recon_index:{channel}", set())
            items = [table[k] for k in idx if k in table]
        else:
            items = list(table.values())
        if status:
            items = [r for r in items if r.get("status") == status]
        return items[:limit]

    def _mem_list_pending_diffs(self, limit):
        self._ensure_store()
        pending = self.store.get("_payment_recon_diff_pending", set())
        table = self.store.get("payment_reconciliation", {})
        return [table[k] for k in pending if k in table][:limit]

    def _mem_add_diff_detail(self, recon_no, diff_detail):
        self._ensure_store()
        table = self.store.get("payment_reconciliation", {})
        if recon_no not in table:
            raise KeyError(recon_no)
        recon = table[recon_no]
        recon.setdefault("diffDetails", []).append(diff_detail)
        recon["diffCount"] = len(recon["diffDetails"])
        # 累加差异金额(正数平台多/负数渠道多)
        recon["diffAmount"] = round(
            float(recon.get("diffAmount", 0)) + float(diff_detail.get("diffAmount", 0)), 2)
        return recon

    def _mem_acquire_recon_lock(self, recon_date, channel):
        self._ensure_store()
        locks = self.store.setdefault("_payment_recon_locks", {})
        key = f"{recon_date}:{channel}"
        if key in locks:
            return False
        locks[key] = True
        return True

    # ---------- 对账记录: Redis 模式 ----------

    async def _redis_create_recon(self, recon: dict) -> dict:
        client = await get_redis_client()
        recon_no = recon["reconNo"]
        key = _k("payment", "recon", recon_no)
        if await client.exists(key):
            raise ValueError(f"对账批次 {recon_no} 已存在")
        recon.setdefault("diffDetails", [])
        recon.setdefault("diffCount", 0)
        recon.setdefault("diffAmount", 0.0)
        await client.set(key, json.dumps(recon, ensure_ascii=False))
        # 索引
        await client.sadd(_k("payment", "recon", "index:date", recon["reconDate"]), recon_no)
        await client.sadd(_k("payment", "recon", "index:channel", recon["channel"]), recon_no)
        # 维护 diff_pending 集合(创建时若状态为 diff/investigating)
        status = recon.get("status")
        if status in (RECON_STATUS_DIFF, RECON_STATUS_INVESTIGATING):
            await client.sadd(_k("payment", "recon", "diff:pending"), recon_no)
        return recon

    async def _redis_get_recon(self, recon_no):
        client = await get_redis_client()
        data = await client.get(_k("payment", "recon", recon_no))
        return json.loads(data) if data else None

    async def _redis_update_recon_fields(self, recon_no, fields):
        client = await get_redis_client()
        key = _k("payment", "recon", recon_no)
        data = await client.get(key)
        if not data:
            raise KeyError(recon_no)
        recon = json.loads(data)
        recon.update(fields)
        await client.set(key, json.dumps(recon, ensure_ascii=False))
        new_status = fields.get("status")
        if new_status:
            pending_key = _k("payment", "recon", "diff:pending")
            if new_status in (RECON_STATUS_DIFF, RECON_STATUS_INVESTIGATING):
                await client.sadd(pending_key, recon_no)
            elif new_status in (RECON_STATUS_MATCHED, RECON_STATUS_RESOLVED):
                await client.srem(pending_key, recon_no)
        return recon

    async def _redis_list_recons(self, recon_date, channel, status, limit):
        client = await get_redis_client()
        if recon_date:
            idx_key = _k("payment", "recon", "index:date", recon_date)
            keys = await client.smembers(idx_key)
            pipe = client.pipeline()
            for k in keys:
                await pipe.get(_k("payment", "recon", k))
            datas = await pipe.execute()
            items = [json.loads(d) for d in datas if d]
        elif channel:
            idx_key = _k("payment", "recon", "index:channel", channel)
            keys = await client.smembers(idx_key)
            pipe = client.pipeline()
            for k in keys:
                await pipe.get(_k("payment", "recon", k))
            datas = await pipe.execute()
            items = [json.loads(d) for d in datas if d]
        else:
            items = []
            async for key in client.scan_iter(match=_k("payment", "recon", "*")):
                # 排除索引和锁键
                if "index:" in key or "diff:pending" in key or ":lock:" in key:
                    continue
                data = await client.get(key)
                if data:
                    items.append(json.loads(data))
        if status:
            items = [r for r in items if r.get("status") == status]
        return items[:limit]

    async def _redis_list_pending_diffs(self, limit):
        client = await get_redis_client()
        pending_key = _k("payment", "recon", "diff:pending")
        keys = await client.smembers(pending_key)
        pipe = client.pipeline()
        for k in keys:
            await pipe.get(_k("payment", "recon", k))
        datas = await pipe.execute()
        return [json.loads(d) for d in datas if d][:limit]

    async def _redis_add_diff_detail(self, recon_no, diff_detail):
        client = await get_redis_client()
        key = _k("payment", "recon", recon_no)
        data = await client.get(key)
        if not data:
            raise KeyError(recon_no)
        recon = json.loads(data)
        recon.setdefault("diffDetails", []).append(diff_detail)
        recon["diffCount"] = len(recon["diffDetails"])
        recon["diffAmount"] = round(
            float(recon.get("diffAmount", 0)) + float(diff_detail.get("diffAmount", 0)), 2)
        await client.set(key, json.dumps(recon, ensure_ascii=False))
        return recon

    async def _redis_acquire_recon_lock(self, recon_date, channel, ttl):
        client = await get_redis_client()
        key = _k("payment", "recon", "lock", recon_date, channel)
        # SETNX + TTL
        ok = await client.set(key, 1, nx=True, ex=ttl)
        return bool(ok)

    # ============================================================
    # 渠道配置(payment_channels, P1)
    # ============================================================

    async def create_channel(self, channel: dict) -> dict:
        """创建渠道配置

        Raises:
            ValueError: channelCode 已存在
        """
        if is_redis_mode():
            return await self._redis_create_channel(channel)
        return self._mem_create_channel(channel)

    async def get_channel(self, channel_code: str) -> dict | None:
        """按渠道编码查询"""
        if is_redis_mode():
            return await self._redis_get_channel(channel_code)
        return self._mem_get_channel(channel_code)

    async def update_channel_fields(self, channel_code: str, fields: dict) -> dict:
        """部分字段更新

        Raises:
            KeyError: 渠道不存在
        """
        if is_redis_mode():
            return await self._redis_update_channel_fields(channel_code, fields)
        return self._mem_update_channel_fields(channel_code, fields)

    async def update_channel_status(self, channel_code: str, status: str) -> dict:
        """更新渠道状态(启停)

        Raises:
            KeyError: 渠道不存在
            ValueError: 状态非法
        """
        if status not in CHANNEL_STATUS_NAMES:
            raise ValueError(f"渠道状态非法: {status}")
        fields = {"status": status,
                   "statusName": CHANNEL_STATUS_NAMES[status]}
        return await self.update_channel_fields(channel_code, fields)

    async def list_channels(self, status: str = None,
                              channel_type: str = None, limit: int = 50) -> list[dict]:
        """列出渠道配置(可按 status/type 筛选)"""
        if is_redis_mode():
            return await self._redis_list_channels(status, channel_type, limit)
        return self._mem_list_channels(status, channel_type, limit)

    async def list_active_channels(self) -> list[dict]:
        """列出启用的渠道(高频查询, 用于支付下单时选择渠道)"""
        return await self.list_channels(status=CHANNEL_STATUS_ACTIVE, limit=100)

    async def check_limit(self, channel_code: str, amount: float) -> dict:
        """限额校验(单笔 + 单日累计 + 单月累计)

        Returns:
            {"passed": bool, "reason": str, "dailyAmount": float, "monthlyAmount": float}

        Raises:
            KeyError: 渠道不存在
        """
        if is_redis_mode():
            return await self._redis_check_limit(channel_code, amount)
        return self._mem_check_limit(channel_code, amount)

    async def add_transaction_amount(self, channel_code: str, amount: float) -> dict:
        """累计交易额(支付成功后调用, HINCRBYFLOAT 原子操作)

        Returns:
            {"dailyAmount": float, "dailyCount": int, "monthlyAmount": float}

        Raises:
            KeyError: 渠道不存在
        """
        if is_redis_mode():
            return await self._redis_add_transaction_amount(channel_code, amount)
        return self._mem_add_transaction_amount(channel_code, amount)

    async def reset_daily_stats(self) -> int:
        """重置所有渠道的日累计统计(每日 00:00 定时任务)

        Returns:
            重置的渠道数量
        """
        if is_redis_mode():
            return await self._redis_reset_daily_stats()
        return self._mem_reset_daily_stats()

    async def reset_monthly_stats(self) -> int:
        """重置所有渠道的月累计统计(每月 1 日定时任务)"""
        if is_redis_mode():
            return await self._redis_reset_monthly_stats()
        return self._mem_reset_monthly_stats()

    # ---------- 渠道配置: 内存模式 ----------

    def _mem_create_channel(self, channel: dict) -> dict:
        self._ensure_store()
        if "payment_channels" not in self.store:
            self.store["payment_channels"] = {}
        table = self.store["payment_channels"]
        code = channel["channelCode"]
        if code in table:
            raise ValueError(f"渠道 {code} 已存在")
        # 默认值
        channel.setdefault("dailyAmount", 0.0)
        channel.setdefault("dailyCount", 0)
        channel.setdefault("monthlyAmount", 0.0)
        channel.setdefault("status", CHANNEL_STATUS_ACTIVE)
        channel.setdefault("statusName", CHANNEL_STATUS_NAMES[CHANNEL_STATUS_ACTIVE])
        table[code] = channel
        # 状态索引
        status_idx = f"_payment_channel_index:{channel['status']}"
        self.store.setdefault(status_idx, set()).add(code)
        return channel

    def _mem_get_channel(self, channel_code):
        self._ensure_store()
        table = self.store.get("payment_channels", {})
        ch = table.get(channel_code)
        if ch:
            return self._deserialize_channel(ch)
        return None

    def _mem_update_channel_fields(self, channel_code, fields):
        self._ensure_store()
        table = self.store.get("payment_channels", {})
        if channel_code not in table:
            raise KeyError(channel_code)
        old_status = table[channel_code].get("status")
        table[channel_code].update(fields)
        new_status = fields.get("status")
        if new_status and new_status != old_status:
            old_idx = self.store.get(f"_payment_channel_index:{old_status}", set())
            old_idx.discard(channel_code)
            new_idx = self.store.setdefault(f"_payment_channel_index:{new_status}", set())
            new_idx.add(channel_code)
        return self._deserialize_channel(table[channel_code])

    def _mem_list_channels(self, status, channel_type, limit):
        self._ensure_store()
        table = self.store.get("payment_channels", {})
        items = list(table.values())
        if status:
            items = [c for c in items if c.get("status") == status]
        if channel_type:
            items = [c for c in items if c.get("channelType") == channel_type]
        return [self._deserialize_channel(c) for c in items[:limit]]

    def _mem_check_limit(self, channel_code, amount):
        self._ensure_store()
        table = self.store.get("payment_channels", {})
        if channel_code not in table:
            raise KeyError(channel_code)
        ch = table[channel_code]
        # 单笔限额
        if amount < float(ch.get("minAmount", 0)):
            return {"passed": False, "reason": f"金额 {amount} 低于单笔最小 {ch['minAmount']}"}
        if amount > float(ch.get("maxAmount", float('inf'))):
            return {"passed": False, "reason": f"金额 {amount} 超过单笔最大 {ch['maxAmount']}"}
        # 单日累计
        daily = float(ch.get("dailyAmount", 0))
        if daily + amount > float(ch.get("dailyLimit", float('inf'))):
            return {"passed": False,
                     "reason": f"单日累计 {daily+amount} 超过限额 {ch['dailyLimit']}"}
        # 单月累计
        monthly = float(ch.get("monthlyAmount", 0))
        if monthly + amount > float(ch.get("monthlyLimit", float('inf'))):
            return {"passed": False,
                     "reason": f"单月累计 {monthly+amount} 超过限额 {ch['monthlyLimit']}"}
        return {"passed": True, "reason": "",
                 "dailyAmount": daily, "monthlyAmount": monthly}

    def _mem_add_transaction_amount(self, channel_code, amount):
        self._ensure_store()
        table = self.store.get("payment_channels", {})
        if channel_code not in table:
            raise KeyError(channel_code)
        ch = table[channel_code]
        ch["dailyAmount"] = round(float(ch.get("dailyAmount", 0)) + amount, 2)
        ch["dailyCount"] = int(ch.get("dailyCount", 0)) + 1
        ch["monthlyAmount"] = round(float(ch.get("monthlyAmount", 0)) + amount, 2)
        return {"dailyAmount": ch["dailyAmount"],
                 "dailyCount": ch["dailyCount"],
                 "monthlyAmount": ch["monthlyAmount"]}

    def _mem_reset_daily_stats(self):
        self._ensure_store()
        table = self.store.get("payment_channels", {})
        count = 0
        for ch in table.values():
            ch["dailyAmount"] = 0.0
            ch["dailyCount"] = 0
            count += 1
        return count

    def _mem_reset_monthly_stats(self):
        self._ensure_store()
        table = self.store.get("payment_channels", {})
        count = 0
        for ch in table.values():
            ch["monthlyAmount"] = 0.0
            ch["dailyAmount"] = 0.0
            ch["dailyCount"] = 0
            count += 1
        return count

    # ---------- 渠道配置: Redis 模式 ----------

    async def _redis_create_channel(self, channel: dict) -> dict:
        client = await get_redis_client()
        code = channel["channelCode"]
        key = _k("payment", "channel", code)
        if await client.exists(key):
            raise ValueError(f"渠道 {code} 已存在")
        channel.setdefault("dailyAmount", 0.0)
        channel.setdefault("dailyCount", 0)
        channel.setdefault("monthlyAmount", 0.0)
        channel.setdefault("status", CHANNEL_STATUS_ACTIVE)
        channel.setdefault("statusName", CHANNEL_STATUS_NAMES[CHANNEL_STATUS_ACTIVE])
        await client.hset(key, mapping=self._serialize_hash(channel))
        await client.sadd(_k("payment", "channel", "index:status", channel["status"]), code)
        return channel

    async def _redis_get_channel(self, channel_code):
        client = await get_redis_client()
        data = await client.hgetall(_k("payment", "channel", channel_code))
        if not data:
            return None
        return self._deserialize_channel(data)

    async def _redis_update_channel_fields(self, channel_code, fields):
        client = await get_redis_client()
        key = _k("payment", "channel", channel_code)
        if not await client.exists(key):
            raise KeyError(channel_code)
        old_status = await client.hget(key, "status")
        # 金额字段单独 HINCRBYFLOAT(避免读-改-写竞态)
        amount_fields = {"dailyAmount", "monthlyAmount"}
        normal_fields = {k: v for k, v in fields.items() if k not in amount_fields}
        if normal_fields:
            await client.hset(key, mapping=self._serialize_hash(normal_fields))
        for af in amount_fields:
            if af in fields:
                await client.hincrbyfloat(key, af, float(fields[af]))
        new_status = fields.get("status")
        if new_status and new_status != old_status:
            await client.srem(_k("payment", "channel", "index:status", old_status), channel_code)
            await client.sadd(_k("payment", "channel", "index:status", new_status), channel_code)
        data = await client.hgetall(key)
        return self._deserialize_channel(data)

    async def _redis_list_channels(self, status, channel_type, limit):
        client = await get_redis_client()
        items = []
        if status:
            idx_key = _k("payment", "channel", "index:status", status)
            codes = await client.smembers(idx_key)
            pipe = client.pipeline()
            for c in codes:
                await pipe.hgetall(_k("payment", "channel", c))
            datas = await pipe.execute()
            items = [self._deserialize_channel(d) for d in datas if d]
        else:
            async for key in client.scan_iter(match=_k("payment", "channel:*")):
                if "index:" in key:
                    continue
                data = await client.hgetall(key)
                if data:
                    items.append(self._deserialize_channel(data))
        if channel_type:
            items = [c for c in items if c.get("channelType") == channel_type]
        return items[:limit]

    async def _redis_check_limit(self, channel_code, amount):
        client = await get_redis_client()
        key = _k("payment", "channel", channel_code)
        if not await client.exists(key):
            raise KeyError(channel_code)
        min_a = float(await client.hget(key, "minAmount") or 0)
        max_a = float(await client.hget(key, "maxAmount") or float('inf'))
        daily_limit = float(await client.hget(key, "dailyLimit") or float('inf'))
        monthly_limit = float(await client.hget(key, "monthlyLimit") or float('inf'))
        daily = float(await client.hget(key, "dailyAmount") or 0)
        monthly = float(await client.hget(key, "monthlyAmount") or 0)
        if amount < min_a:
            return {"passed": False, "reason": f"金额 {amount} 低于单笔最小 {min_a}"}
        if amount > max_a:
            return {"passed": False, "reason": f"金额 {amount} 超过单笔最大 {max_a}"}
        if daily + amount > daily_limit:
            return {"passed": False, "reason": f"单日累计 {daily+amount} 超过限额 {daily_limit}"}
        if monthly + amount > monthly_limit:
            return {"passed": False, "reason": f"单月累计 {monthly+amount} 超过限额 {monthly_limit}"}
        return {"passed": True, "reason": "", "dailyAmount": daily, "monthlyAmount": monthly}

    async def _redis_add_transaction_amount(self, channel_code, amount):
        client = await get_redis_client()
        key = _k("payment", "channel", channel_code)
        if not await client.exists(key):
            raise KeyError(channel_code)
        pipe = client.pipeline()
        await pipe.hincrbyfloat(key, "dailyAmount", amount)
        await pipe.hincrby(key, "dailyCount", 1)
        await pipe.hincrbyfloat(key, "monthlyAmount", amount)
        results = await pipe.execute()
        return {"dailyAmount": float(results[0]),
                 "dailyCount": int(results[1]),
                 "monthlyAmount": float(results[2])}

    async def _redis_reset_daily_stats(self):
        client = await get_redis_client()
        count = 0
        async for key in client.scan_iter(match=_k("payment", "channel:*")):
            if "index:" in key:
                continue
            await client.hset(key, "dailyAmount", 0.0)
            await client.hset(key, "dailyCount", 0)
            count += 1
        return count

    async def _redis_reset_monthly_stats(self):
        client = await get_redis_client()
        count = 0
        async for key in client.scan_iter(match=_k("payment", "channel:*")):
            if "index:" in key:
                continue
            await client.hset(key, "monthlyAmount", 0.0)
            await client.hset(key, "dailyAmount", 0.0)
            await client.hset(key, "dailyCount", 0)
            count += 1
        return count

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

    def _deserialize_channel(self, data: dict) -> dict:
        """将 Redis hgetall 返回的渠道配置 dict 反序列化

        - 金额字段还原为 float
        - 计数字段还原为 int
        - JSON 字段(list/dict) 还原为原生类型
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
            "feeRate", "fixedFee", "minAmount", "maxAmount",
            "dailyLimit", "monthlyLimit",
            "dailyAmount", "monthlyAmount",
        }
        for k in amount_fields:
            if k in result:
                result[k] = _to_number(result[k])
        # 计数字段
        count_fields = {"dailyCount", "retryMax", "timeout"}
        for k in count_fields:
            if k in result:
                result[k] = _to_number(result[k])
        # JSON 字段(supportedMethods, supportedScenes 等)
        json_fields = {"supportedMethods", "supportedScenes"}
        for k in json_fields:
            if k in result and isinstance(result[k], str):
                try:
                    result[k] = json.loads(result[k])
                except (TypeError, ValueError):
                    pass
        return result
