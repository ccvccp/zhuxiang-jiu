"""免费代驾域数据访问层(双模式: 内存 + Redis)

归属: 酒店酒吧会所合作商模块·第三章代驾服务管理(P2 落地)
    合作商场所消费≥1瓶竹奕酒 → 30公里内本站代付(单次上限¥200)

表清单:
    venue_dd_orders(代驾订单) + venue_dd_verifications(消费凭证)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis String-JSON
    - 订单: 按 ddOrderId 主键, status 状态机
    - 消费凭证: 按 verificationId 主键, 24h 有效, 下单核销
"""

import json
from datetime import datetime

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


# ============================================================
# 订单状态机(创建 → 司机赶来 → 行程中 → 已完成 → 已代付)
# ============================================================

DD_STATUS_CREATED = "created"      # 已下单(待司机接单)
DD_STATUS_ARRIVING = "arriving"    # 司机赶来
DD_STATUS_IN_TRIP = "in_trip"      # 行程中
DD_STATUS_COMPLETED = "completed"  # 已完成(待代付)
DD_STATUS_PAID = "paid"            # 已代付(终态)
DD_STATUS_CANCELLED = "cancelled"  # 已取消

DD_STATUSES = {
    DD_STATUS_CREATED, DD_STATUS_ARRIVING, DD_STATUS_IN_TRIP,
    DD_STATUS_COMPLETED, DD_STATUS_PAID, DD_STATUS_CANCELLED,
}

# 合法状态流转(advance_order 用)
DD_TRANSITIONS = {
    DD_STATUS_CREATED: {DD_STATUS_ARRIVING, DD_STATUS_CANCELLED},
    DD_STATUS_ARRIVING: {DD_STATUS_IN_TRIP, DD_STATUS_CANCELLED},
    DD_STATUS_IN_TRIP: {DD_STATUS_COMPLETED},
    DD_STATUS_COMPLETED: {DD_STATUS_PAID},
    DD_STATUS_PAID: set(),
    DD_STATUS_CANCELLED: set(),
}

# 消费凭证状态
VERIF_STATUS_VALID = "valid"    # 有效(可下单)
VERIF_STATUS_USED = "used"      # 已核销(已下单)
VERIF_STATUS_EXPIRED = "expired"  # 已过期(查询时惰性推进)
VERIF_STATUS_RETURNED = "returned"  # 取消订单退回(等效 valid)


class DesignatedDrivingRepository:
    """免费代驾数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # ID 生成
    # ============================================================

    async def next_order_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("venue", "dd:order", "seq"))
        self._ensure_store()
        seq = self.store.get("_venue_dd_order_seq", 0) + 1
        self.store["_venue_dd_order_seq"] = seq
        return seq

    async def next_verification_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("venue", "dd:verif", "seq"))
        self._ensure_store()
        seq = self.store.get("_venue_dd_verification_seq", 0) + 1
        self.store["_venue_dd_verification_seq"] = seq
        return seq

    # ============================================================
    # 消费凭证 CRUD
    # ============================================================

    async def get_verification(self, verification_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("venue", "dd:verif", verification_id))
            return json.loads(data) if data else None
        self._ensure_store()
        v = self.store["venue_dd_verifications"].get(verification_id)
        return dict(v) if v else None

    async def save_verification(self, verif: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("venue", "dd:verif", verif["id"]),
                json.dumps(verif, ensure_ascii=False))
            await client.sadd(_k("venue", "dd:verif", "ids"), verif["id"])
            return
        self._ensure_store()
        self.store["venue_dd_verifications"][verif["id"]] = dict(verif)

    async def list_verifications(self, user_id: int = None,
                                 partner_id: int = None,
                                 limit: int = 100) -> list[dict]:
        """消费凭证列表(按创建时间倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.smembers(_k("venue", "dd:verif", "ids"))
            items = []
            for vid in ids:
                data = await client.get(_k("venue", "dd:verif", vid))
                if data:
                    items.append(json.loads(data))
        else:
            self._ensure_store()
            items = list(self.store["venue_dd_verifications"].values())
        if user_id is not None:
            items = [v for v in items if v.get("userId") == user_id]
        if partner_id is not None:
            items = [v for v in items
                     if v.get("partnerId") == partner_id]
        items.sort(key=lambda v: v.get("createdAt", ""), reverse=True)
        return items[:limit]

    # ============================================================
    # 代驾订单 CRUD
    # ============================================================

    async def get_order(self, order_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("venue", "dd:order", order_id))
            return json.loads(data) if data else None
        self._ensure_store()
        o = self.store["venue_dd_orders"].get(order_id)
        return dict(o) if o else None

    async def save_order(self, order: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("venue", "dd:order", order["id"]),
                json.dumps(order, ensure_ascii=False))
            await client.sadd(_k("venue", "dd:order", "ids"), order["id"])
            # 用户索引去重(新建+更新共用 save, 先删后插防重复)
            ulist = _k("venue", "dd:orders_by_user", order.get("userId", 0))
            await client.lrem(ulist, 0, order["id"])
            await client.lpush(ulist, order["id"])
            return
        self._ensure_store()
        self.store["venue_dd_orders"][order["id"]] = dict(order)
        idx = self.store["venue_dd_orders_by_user"].setdefault(
            order.get("userId", 0), [])
        if order["id"] not in idx:
            idx.insert(0, order["id"])

    async def list_orders(self, user_id: int = None, status: str = None,
                          limit: int = 50) -> list[dict]:
        """代驾订单列表(按创建时间倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            if user_id is not None:
                oids = await client.lrange(
                    _k("venue", "dd:orders_by_user", user_id), 0, -1)
                items = []
                for oid in oids:
                    data = await client.get(_k("venue", "dd:order", oid))
                    if data:
                        items.append(json.loads(data))
            else:
                ids = await client.smembers(_k("venue", "dd:order", "ids"))
                items = []
                for oid in ids:
                    data = await client.get(_k("venue", "dd:order", oid))
                    if data:
                        items.append(json.loads(data))
        else:
            self._ensure_store()
            if user_id is not None:
                oids = self.store["venue_dd_orders_by_user"].get(user_id, [])
                items = [self.store["venue_dd_orders"][oid]
                         for oid in oids
                         if oid in self.store["venue_dd_orders"]]
            else:
                items = list(self.store["venue_dd_orders"].values())
        if status:
            items = [o for o in items if o.get("status") == status]
        items.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        return items[:limit]

    async def stats(self) -> dict:
        """代驾统计(订单量/代付总额/用户自付/平均评分)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.smembers(_k("venue", "dd:order", "ids"))
            orders = []
            for oid in ids:
                data = await client.get(_k("venue", "dd:order", oid))
                if data:
                    orders.append(json.loads(data))
        else:
            self._ensure_store()
            orders = list(self.store["venue_dd_orders"].values())
        paid = [o for o in orders if o.get("status") == DD_STATUS_PAID]
        ratings = [o["rating"] for o in orders if o.get("rating")]
        return {
            "totalOrders": len(orders),
            "paidOrders": len(paid),
            "totalPlatformPaid": round(sum(
                o.get("platformPaid", 0) for o in paid), 2),
            "totalUserPaid": round(sum(
                o.get("userPaid", 0) for o in paid), 2),
            "avgRating": round(sum(ratings) / len(ratings), 2)
            if ratings else None,
            "generatedAt": datetime.utcnow().isoformat(),
        }

    # ============================================================
    # 内存模式初始化
    # ============================================================

    def _ensure_store(self) -> None:
        if "venue_dd_orders" not in self.store:
            self.store["venue_dd_orders"] = {}            # orderId → order
            self.store["venue_dd_orders_by_user"] = {}     # userId → [orderId]
            self.store["venue_dd_verifications"] = {}      # verifId → verif
            self.store["_venue_dd_order_seq"] = 0
            self.store["_venue_dd_verification_seq"] = 0
