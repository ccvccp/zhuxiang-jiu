"""67号·AI智能叫帮模块数据访问层

数据表(Redis/内存双模式):
    help_orders       叫帮单主表
    help_trust_ledger 信值流水(公益100%/有偿10% 双轨)
    help_reviews      双向评价

Key 设计(对齐 backend._k):
    zhuxiang:help:order:{id}     Hash(dict JSON)
    zhuxiang:help:ledger:{id}    Hash(dict JSON)
    zhuxiang:help:review:{id}    Hash(dict JSON)
    zhuxiang:help:{entity}:seq   String(自增序列)

注意: keys() 通配须排除 :seq 序列键(36号 attract click:seq 教训)。
"""

import json

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

import asyncio


def _now_iso() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()


class HelpRepository:
    """AI智能叫帮数据访问层(67号)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # ID 生成
    # ============================================================

    async def next_id(self, entity: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("help", entity, "seq"))
        self._ensure_store()
        seq_key = f"_help_{entity}_seq"
        self.store[seq_key] = self.store.get(seq_key, 0) + 1
        return self.store[seq_key]

    # ============================================================
    # 叫帮单 CRUD
    # ============================================================

    async def get_order(self, order_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("help", "order", order_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["help_orders"].get(order_id)

    async def save_order(self, order: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("help", "order", order["orderId"]),
                             json.dumps(order, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["help_orders"][order["orderId"]] = order
        return order

    async def list_orders(self, status: str = None, mode: str = None,
                          category: str = None,
                          publisher_id: int = None,
                          helper_id: int = None,
                          limit: int = 200) -> list[dict]:
        """列表查询(多维筛选; 排序由 service 层按距离/紧急度处理)"""
        orders = []
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("help", "order", "*"))
            for key in keys:
                if str(key).endswith(":seq"):
                    continue
                data = await client.get(key)
                if data:
                    orders.append(json.loads(data))
        else:
            self._ensure_store()
            orders = list(self.store["help_orders"].values())
        if status:
            orders = [o for o in orders if o.get("status") == status]
        if mode:
            orders = [o for o in orders if o.get("mode") == mode]
        if category:
            orders = [o for o in orders if o.get("category") == category]
        if publisher_id is not None:
            orders = [o for o in orders if o.get("publisherId") == publisher_id]
        if helper_id is not None:
            orders = [o for o in orders if o.get("helperId") == helper_id]
        orders.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        return orders[:limit]

    # ============================================================
    # 信值流水 CRUD(双轨: 公益 100% / 有偿 10%)
    # ============================================================

    async def save_ledger(self, entry: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("help", "ledger", entry["ledgerId"]),
                             json.dumps(entry, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["help_trust_ledger"][entry["ledgerId"]] = entry
        return entry

    async def list_ledger(self, member_id: int, limit: int = 100) -> list[dict]:
        entries = []
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("help", "ledger", "*"))
            for key in keys:
                if str(key).endswith(":seq"):
                    continue
                data = await client.get(key)
                if data:
                    e = json.loads(data)
                    if e.get("memberId") == member_id:
                        entries.append(e)
        else:
            self._ensure_store()
            entries = [e for e in self.store["help_trust_ledger"].values()
                       if e.get("memberId") == member_id]
        entries.sort(key=lambda e: e.get("createdAt", ""), reverse=True)
        return entries[:limit]

    async def trust_summary(self, member_id: int) -> dict:
        """信值汇总: 公益累计 / 有偿累计 / 净值(可作接单门槛)"""
        entries = await self.list_ledger(member_id, limit=10000)
        public_total = sum(e.get("delta", 0) for e in entries
                           if e.get("track") == "public")
        paid_total = sum(e.get("delta", 0) for e in entries
                         if e.get("track") == "paid")
        return {
            "memberId": member_id,
            "publicTrust": round(public_total, 2),
            "paidTrust": round(paid_total, 2),
            "totalTrust": round(public_total + paid_total, 2),
            "recordCount": len(entries),
        }

    # ============================================================
    # 评价 CRUD
    # ============================================================

    async def save_review(self, review: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("help", "review", review["reviewId"]),
                             json.dumps(review, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["help_reviews"][review["reviewId"]] = review
        return review

    async def list_reviews(self, order_id: int = None,
                           reviewee_id: int = None,
                           limit: int = 100) -> list[dict]:
        reviews = []
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("help", "review", "*"))
            for key in keys:
                if str(key).endswith(":seq"):
                    continue
                data = await client.get(key)
                if data:
                    reviews.append(json.loads(data))
        else:
            self._ensure_store()
            reviews = list(self.store["help_reviews"].values())
        if order_id is not None:
            reviews = [r for r in reviews if r.get("orderId") == order_id]
        if reviewee_id is not None:
            reviews = [r for r in reviews if r.get("revieweeId") == reviewee_id]
        reviews.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return reviews[:limit]

    # ============================================================
    # 内存模式 store 初始化
    # ============================================================

    def _ensure_store(self) -> None:
        if "help_orders" not in self.store:
            self.store["help_orders"] = {}
        if "help_trust_ledger" not in self.store:
            self.store["help_trust_ledger"] = {}
        if "help_reviews" not in self.store:
            self.store["help_reviews"] = {}
        for entity in ("order", "ledger", "review"):
            seq_key = f"_help_{entity}_seq"
            if seq_key not in self.store:
                self.store[seq_key] = 0
