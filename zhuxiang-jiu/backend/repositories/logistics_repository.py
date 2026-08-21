"""物流接口管理 Repository(P0 三张表)

双模式(内存/Redis)透明切换,对齐 wallet/payment 模块风格:
    - 内存模式: 字典存储于 _mock_store["logistics_*"]
    - Redis 模式: Hash/String(JSON) 存储

表清单:
    - logistics_orders   : 物流订单(下单/状态流转/面单/签收)
    - logistics_tracks   : 物流轨迹(轨迹追踪)
    - logistics_settlement: 物流结算(月结对账)

并发安全:
    - 物流下单/状态流转: logistics:order:{waybillNo} 锁(对齐 wallet/payment)
    - 月结对账: logistics:settle:lock:{period}:{carrier} 锁(防并发对账)

异常约定(对齐项目既有模式):
    - KeyError: 资源不存在
    - ValueError: 业务冲突(状态非法/参数非法/重复创建等)
"""

import json
import logging
from typing import Optional

from core.helpers import ts
from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

logger = logging.getLogger(__name__)


# ============================================================
# 状态常量(对齐设计文档状态机)
# ============================================================

# 物流订单状态
ORDER_STATUS_PENDING = "pending"           # 待下单
ORDER_STATUS_BOOKED = "booked"             # 已下单
ORDER_STATUS_PICKED = "picked"             # 已揽收
ORDER_STATUS_TRANSPORTING = "transporting" # 运输中
ORDER_STATUS_DELIVERING = "delivering"      # 派送中
ORDER_STATUS_SIGNED = "signed"             # 已签收
ORDER_STATUS_FAILED = "failed"             # 投递失败
ORDER_STATUS_RETURNED = "returned"         # 已退回

ORDER_STATUS_NAMES = {
    ORDER_STATUS_PENDING: "待下单",
    ORDER_STATUS_BOOKED: "已下单",
    ORDER_STATUS_PICKED: "已揽收",
    ORDER_STATUS_TRANSPORTING: "运输中",
    ORDER_STATUS_DELIVERING: "派送中",
    ORDER_STATUS_SIGNED: "已签收",
    ORDER_STATUS_FAILED: "投递失败",
    ORDER_STATUS_RETURNED: "已退回",
}

# 状态机流转规则(用于校验)
ORDER_STATUS_FLOW = {
    ORDER_STATUS_PENDING: {ORDER_STATUS_BOOKED, ORDER_STATUS_FAILED},
    ORDER_STATUS_BOOKED: {ORDER_STATUS_PICKED, ORDER_STATUS_FAILED, ORDER_STATUS_RETURNED},
    ORDER_STATUS_PICKED: {ORDER_STATUS_TRANSPORTING, ORDER_STATUS_FAILED},
    ORDER_STATUS_TRANSPORTING: {ORDER_STATUS_DELIVERING, ORDER_STATUS_FAILED},
    ORDER_STATUS_DELIVERING: {ORDER_STATUS_SIGNED, ORDER_STATUS_FAILED},
    ORDER_STATUS_SIGNED: set(),  # 终态
    ORDER_STATUS_FAILED: {ORDER_STATUS_DELIVERING, ORDER_STATUS_RETURNED},  # 可重投或退回
    ORDER_STATUS_RETURNED: set(),  # 终态
}

# 结算状态
SETTLE_STATUS_PENDING = "pending"          # 待对账
SETTLE_STATUS_RECONCILING = "reconciling"  # 对账中
SETTLE_STATUS_CONFIRMED = "confirmed"      # 已确认
SETTLE_STATUS_PAID = "paid"                # 已付款
SETTLE_STATUS_DIFF = "diff"                # 有差异
SETTLE_STATUS_INVESTIGATING = "investigating"  # 调查中
SETTLE_STATUS_RESOLVED = "resolved"        # 已处理

SETTLE_STATUS_NAMES = {
    SETTLE_STATUS_PENDING: "待对账",
    SETTLE_STATUS_RECONCILING: "对账中",
    SETTLE_STATUS_CONFIRMED: "已确认",
    SETTLE_STATUS_PAID: "已付款",
    SETTLE_STATUS_DIFF: "有差异",
    SETTLE_STATUS_INVESTIGATING: "调查中",
    SETTLE_STATUS_RESOLVED: "已处理",
}

# 结算状态机(含差异分支)
SETTLE_STATUS_FLOW = {
    SETTLE_STATUS_PENDING: {SETTLE_STATUS_RECONCILING},
    SETTLE_STATUS_RECONCILING: {SETTLE_STATUS_CONFIRMED, SETTLE_STATUS_DIFF},
    SETTLE_STATUS_DIFF: {SETTLE_STATUS_INVESTIGATING},
    SETTLE_STATUS_INVESTIGATING: {SETTLE_STATUS_RESOLVED, SETTLE_STATUS_DIFF},
    SETTLE_STATUS_RESOLVED: {SETTLE_STATUS_CONFIRMED},
    SETTLE_STATUS_CONFIRMED: {SETTLE_STATUS_PAID},
    SETTLE_STATUS_PAID: set(),  # 终态
}

# 待对账集合状态(用于 list_pending)
SETTLE_PENDING_STATUSES = {SETTLE_STATUS_PENDING, SETTLE_STATUS_DIFF, SETTLE_STATUS_INVESTIGATING}

# 物流商编码
CARRIER_SF = "SF"      # 顺丰
CARRIER_JD = "JD"      # 京东
CARRIER_LLL = "LLL"     # 货拉拉
CARRIER_DB = "DB"      # 德邦
CARRIER_YT = "YT"      # 圆通

CARRIER_NAMES = {
    CARRIER_SF: "顺丰速运",
    CARRIER_JD: "京东物流",
    CARRIER_LLL: "货拉拉",
    CARRIER_DB: "德邦快递",
    CARRIER_YT: "圆通速递",
}


# ============================================================
# 序列化辅助(对齐 payment_repository 风格)
# ============================================================

def _serialize_hash(data: dict) -> dict:
    """序列化为 Redis Hash 兼容格式(嵌套结构转 JSON)"""
    result = {}
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            result[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            result[k] = "1" if v else "0"
        elif v is None:
            result[k] = ""
        else:
            result[k] = str(v)
    return result


def _deserialize_order(data: dict) -> dict:
    """从 Redis Hash 反序列化物流订单"""
    if not data:
        return None
    result = {}
    for k, v in data.items():
        if k in ("weight", "volume", "insuredValue", "baseFee", "insuredFee",
                 "packageFee", "extraFee", "discount", "totalFee"):
            try:
                result[k] = float(v) if v != "" else 0.0
            except (ValueError, TypeError):
                result[k] = 0.0
        elif k == "pieceCount":
            try:
                result[k] = int(v) if v != "" else 0
            except (ValueError, TypeError):
                result[k] = 0
        else:
            result[k] = v
    return result


def _deserialize_settlement(data: dict) -> dict:
    """从 Redis Hash 反序列化结算单"""
    if not data:
        return None
    result = {}
    for k, v in data.items():
        if k in ("totalWeight", "baseFeeTotal", "insuredTotal", "packageTotal",
                 "extraTotal", "subtotal", "discountAmount", "payableAmount"):
            try:
                result[k] = float(v) if v != "" else 0.0
            except (ValueError, TypeError):
                result[k] = 0.0
        elif k in ("totalOrders", "diffCount"):
            try:
                result[k] = int(v) if v != "" else 0
            except (ValueError, TypeError):
                result[k] = 0
        elif k == "diffDetails":
            try:
                result[k] = json.loads(v) if v else []
            except (json.JSONDecodeError, TypeError):
                result[k] = []
        else:
            result[k] = v
    return result


# ============================================================
# LogisticsRepository: 物流接口管理数据访问层
# ============================================================

class LogisticsRepository:
    """物流接口管理数据访问层(P0 三张表)

    表:
        - logistics_orders    : 物流订单
        - logistics_tracks    : 物流轨迹
        - logistics_settlement: 物流结算
    """

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()
        # 初始化内存存储键
        self.store.setdefault("logistics_orders", {})
        self.store.setdefault("logistics_tracks", {})
        self.store.setdefault("logistics_settlement", {})
        self.store.setdefault("logistics_settle_pending", set())  # 待对账集合
        self.store.setdefault("logistics_order_index_orderid", {})  # orderId → waybillNo
        self.store.setdefault("logistics_track_index_waybill", {})  # waybillNo → [trackId]
        self._track_seq = 0  # 轨迹ID自增序列(内存模式)

    # ============================================================
    # 1. 物流订单(logistics_orders)
    # ============================================================

    async def save_order(self, order: dict) -> dict:
        """创建/更新物流订单

        Args:
            order: 含 waybillNo/orderId/carrier/status 等字段

        Returns:
            保存后的订单数据
        """
        waybill_no = order["waybillNo"]
        now = ts()
        order.setdefault("createdAt", now)
        order["updatedAt"] = now

        if is_redis_mode():
            client = await get_redis_client()
            key = _k("logistics:order", waybill_no)
            # 序列化并写入 Hash
            await client.hset(key, mapping=_serialize_hash(order))
            # 维护 orderId 索引
            if order.get("orderId"):
                await client.hset(_k("logistics:order:index:orderId"), order["orderId"], waybill_no)
            # 维护 status 索引
            status = order.get("status", ORDER_STATUS_PENDING)
            await client.sadd(_k("logistics:order:index:status", status), waybill_no)
            # 若旧状态存在则从旧状态集合移除(更新场景)
            old = await client.hget(key, "status")
            if old and old != status:
                await client.srem(_k("logistics:order:index:status", old), waybill_no)
        else:
            self.store["logistics_orders"][waybill_no] = dict(order)
            # 维护 orderId 索引
            if order.get("orderId"):
                self.store["logistics_order_index_orderid"][order["orderId"]] = waybill_no

        logger.info(f"保存物流订单 waybillNo={waybill_no} status={order.get('status')}")
        return dict(order)

    async def get_order(self, waybill_no: str) -> Optional[dict]:
        """查询物流订单"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("logistics:order", waybill_no))
            return _deserialize_order(data)
        order = self.store["logistics_orders"].get(waybill_no)
        return dict(order) if order else None

    async def find_by_order(self, order_id: str) -> Optional[dict]:
        """按订单号查询物流单(返回最新一条)

        用于防重复下单: 同一 orderId 只能有一个未关闭运单
        """
        if is_redis_mode():
            client = await get_redis_client()
            waybill_no = await client.hget(_k("logistics:order:index:orderId"), order_id)
            if not waybill_no:
                return None
            return await self.get_order(waybill_no)
        waybill_no = self.store.get("logistics_order_index_orderid", {}).get(order_id)
        if not waybill_no:
            return None
        return await self.get_order(waybill_no)

    async def list_orders(self, carrier: str = None, status: str = None,
                           order_type: str = None, limit: int = 50) -> list:
        """物流订单列表(支持筛选)"""
        if is_redis_mode():
            client = await get_redis_client()
            # 按状态集合查询(若指定), 否则扫描所有
            if status:
                waybill_nos = await client.smembers(_k("logistics:order:index:status", status))
                waybill_nos = list(waybill_nos)
            else:
                # 扫描所有物流订单 key
                waybill_nos = []
                async for key in client.scan_iter(match=_k("logistics:order:*"), count=100):
                    # 提取 waybillNo(跳过 index: 开头的)
                    parts = key.split(":")
                    if len(parts) >= 3 and parts[2] != "index":
                        waybill_nos.append(parts[2])
            items = []
            for wn in waybill_nos[:limit]:
                order = await self.get_order(wn)
                if not order:
                    continue
                if carrier and order.get("carrier") != carrier:
                    continue
                if order_type and order.get("orderType") != order_type:
                    continue
                items.append(order)
            return items
        # 内存模式
        items = list(self.store["logistics_orders"].values())
        if carrier:
            items = [o for o in items if o.get("carrier") == carrier]
        if status:
            items = [o for o in items if o.get("status") == status]
        if order_type:
            items = [o for o in items if o.get("orderType") == order_type]
        return items[:limit]

    async def update_order_fields(self, waybill_no: str, fields: dict) -> dict:
        """更新物流订单字段(部分更新)

        自动维护 status 索引(若 status 变更)
        """
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("logistics:order", waybill_no)
            old_status = await client.hget(key, "status")
            fields["updatedAt"] = ts()
            await client.hset(key, mapping=_serialize_hash(fields))
            # 维护 status 索引
            new_status = fields.get("status")
            if new_status and old_status != new_status:
                if old_status:
                    await client.srem(_k("logistics:order:index:status", old_status), waybill_no)
                await client.sadd(_k("logistics:order:index:status", new_status), waybill_no)
            data = await client.hgetall(key)
            result = _deserialize_order(data)
            if not result:
                raise KeyError(f"物流订单 {waybill_no} 不存在")
            return result
        # 内存模式
        order = self.store["logistics_orders"].get(waybill_no)
        if not order:
            raise KeyError(f"物流订单 {waybill_no} 不存在")
        order.update(fields)
        order["updatedAt"] = ts()
        return dict(order)

    # ============================================================
    # 2. 物流轨迹(logistics_tracks)
    # ============================================================

    async def add_track(self, track: dict) -> dict:
        """添加物流轨迹记录

        Args:
            track: 含 waybillNo/carrier/trackStatus/unifiedStatus/description 等

        Returns:
            保存后的轨迹记录(含 trackId)
        """
        self._track_seq += 1
        track_id = f"TRACK{self._track_seq:08d}"
        track["trackId"] = track_id
        track.setdefault("createdAt", ts())

        waybill_no = track["waybillNo"]

        if is_redis_mode():
            client = await get_redis_client()
            # 存储轨迹记录(使用 List 按时间倒序保留最新 50 条)
            list_key = _k("logistics:track", waybill_no)
            await client.lpush(list_key, json.dumps(track, ensure_ascii=False))
            await client.ltrim(list_key, 0, 49)  # 保留最新 50 条
        else:
            self.store["logistics_tracks"][track_id] = dict(track)
            # 维护 waybillNo → trackIds 索引(按时间倒序, 最新在前)
            self.store["logistics_track_index_waybill"].setdefault(waybill_no, []).insert(0, track_id)

        logger.info(f"添加物流轨迹 waybillNo={waybill_no} status={track.get('unifiedStatus')}")
        return dict(track)

    async def list_tracks(self, waybill_no: str, limit: int = 50) -> list:
        """查询运单轨迹列表(按时间倒序, 最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw_list = await client.lrange(_k("logistics:track", waybill_no), 0, limit - 1)
            items = []
            for raw in raw_list:
                try:
                    items.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    continue
            return items
        # 内存模式
        track_ids = self.store["logistics_track_index_waybill"].get(waybill_no, [])[:limit]
        return [dict(self.store["logistics_tracks"][tid]) for tid in track_ids if tid in self.store["logistics_tracks"]]

    # ============================================================
    # 3. 物流结算(logistics_settlement)
    # ============================================================

    async def create_settlement(self, settle: dict) -> dict:
        """创建结算单

        Args:
            settle: 含 settleNo/carrier/period/totalOrders 等

        Raises:
            ValueError: 结算单号已存在
        """
        settle_no = settle["settleNo"]

        if is_redis_mode():
            client = await get_redis_client()
            key = _k("logistics:settle", settle_no)
            if await client.exists(key):
                raise ValueError(f"结算单 {settle_no} 已存在")
            now = ts()
            settle.setdefault("createdAt", now)
            settle["updatedAt"] = now
            settle.setdefault("status", SETTLE_STATUS_PENDING)
            settle.setdefault("diffDetails", [])
            settle.setdefault("diffCount", 0)
            await client.set(key, json.dumps(settle, ensure_ascii=False))
            # 加入待对账集合(若状态为 pending)
            if settle["status"] in SETTLE_PENDING_STATUSES:
                await client.sadd(_k("logistics:settle:pending"), settle_no)
        else:
            if settle_no in self.store["logistics_settlement"]:
                raise ValueError(f"结算单 {settle_no} 已存在")
            now = ts()
            settle.setdefault("createdAt", now)
            settle["updatedAt"] = now
            settle.setdefault("status", SETTLE_STATUS_PENDING)
            settle.setdefault("diffDetails", [])
            settle.setdefault("diffCount", 0)
            self.store["logistics_settlement"][settle_no] = dict(settle)
            if settle["status"] in SETTLE_PENDING_STATUSES:
                self.store["logistics_settle_pending"].add(settle_no)

        logger.info(f"创建结算单 settleNo={settle_no} carrier={settle.get('carrier')} period={settle.get('period')}")
        return dict(settle)

    async def get_settlement(self, settle_no: str) -> Optional[dict]:
        """查询结算单"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("logistics:settle", settle_no))
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
        settle = self.store["logistics_settlement"].get(settle_no)
        return dict(settle) if settle else None

    async def update_settlement_fields(self, settle_no: str, fields: dict) -> dict:
        """更新结算单字段(部分更新)

        自动维护 pending 集合(若状态变更)
        """
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("logistics:settle", settle_no)
            raw = await client.get(key)
            if not raw:
                raise KeyError(f"结算单 {settle_no} 不存在")
            settle = json.loads(raw)
            old_status = settle.get("status")
            settle.update(fields)
            settle["updatedAt"] = ts()
            await client.set(key, json.dumps(settle, ensure_ascii=False))
            # 维护 pending 集合
            new_status = settle.get("status")
            if new_status != old_status:
                if old_status in SETTLE_PENDING_STATUSES:
                    await client.srem(_k("logistics:settle:pending"), settle_no)
                if new_status in SETTLE_PENDING_STATUSES:
                    await client.sadd(_k("logistics:settle:pending"), settle_no)
            return settle
        # 内存模式
        settle = self.store["logistics_settlement"].get(settle_no)
        if not settle:
            raise KeyError(f"结算单 {settle_no} 不存在")
        old_status = settle.get("status")
        settle.update(fields)
        settle["updatedAt"] = ts()
        new_status = settle.get("status")
        if new_status != old_status:
            if old_status in SETTLE_PENDING_STATUSES:
                self.store["logistics_settle_pending"].discard(settle_no)
            if new_status in SETTLE_PENDING_STATUSES:
                self.store["logistics_settle_pending"].add(settle_no)
        return dict(settle)

    async def list_settlements(self, carrier: str = None, period: str = None,
                                status: str = None, limit: int = 50) -> list:
        """结算单列表(支持筛选)"""
        if is_redis_mode():
            client = await get_redis_client()
            items = []
            async for key in client.scan_iter(match=_k("logistics:settle:*"), count=100):
                raw = await client.get(key)
                if not raw:
                    continue
                try:
                    settle = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if carrier and settle.get("carrier") != carrier:
                    continue
                if period and settle.get("period") != period:
                    continue
                if status and settle.get("status") != status:
                    continue
                items.append(settle)
            return items[:limit]
        # 内存模式
        items = list(self.store["logistics_settlement"].values())
        if carrier:
            items = [s for s in items if s.get("carrier") == carrier]
        if period:
            items = [s for s in items if s.get("period") == period]
        if status:
            items = [s for s in items if s.get("status") == status]
        return items[:limit]

    async def list_pending_settlements(self, limit: int = 50) -> list:
        """待对账结算单列表(pending/diff/investigating)"""
        if is_redis_mode():
            client = await get_redis_client()
            settle_nos = await client.smembers(_k("logistics:settle:pending"))
            items = []
            for sn in list(settle_nos)[:limit]:
                settle = await self.get_settlement(sn)
                if settle:
                    items.append(settle)
            return items
        # 内存模式
        items = []
        for sn in list(self.store["logistics_settle_pending"])[:limit]:
            settle = self.store["logistics_settlement"].get(sn)
            if settle:
                items.append(dict(settle))
        return items

    async def acquire_settle_lock(self, period: str, carrier: str, ttl: int = 3600) -> bool:
        """获取对账锁(防止并发对账同一账期同一物流商)

        Returns:
            True: 获取成功 / False: 已被锁定
        """
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("logistics:settle:lock", period, carrier)
            return await client.set(key, "1", ex=ttl, nx=True)
        # 内存模式: 使用简单标记(测试场景不需要真实锁)
        lock_key = f"settle_lock_{period}_{carrier}"
        if lock_key in self.store:
            return False
        self.store[lock_key] = "1"
        return True

    async def release_settle_lock(self, period: str, carrier: str) -> None:
        """释放对账锁"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.delete(_k("logistics:settle:lock", period, carrier))
        else:
            lock_key = f"settle_lock_{period}_{carrier}"
            self.store.pop(lock_key, None)
