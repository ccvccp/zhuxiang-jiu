"""64号·信值兑换管理 仓储底座
(xx64_repository, 8 表)

计划(§六):
    xx64_orders         兑换订单(九态+快照)
    xx64_ledger         信值转移借贷对
                        (买扣卖增原子+来源标记)
    xx64_points_exchange 积分→信值兑换
                        (冻结观察期)
    xx64_quotas         限额快照
                        (单次/窗口基准)
    xx64_risk           风控事件(P3)
    xx64_anchors        购买力指数日快照(P4)
    xx64_appeals        申诉(P4)
    xx64_events         全链事件

双模式存储(asyncio/Redis)+序列化
五清单字段显式注册(60-62号范式)。
"""

import json

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)


class Xx64Repository:
    """64号八表仓储(双模式)"""

    TABLE_ORDERS = "xx64_orders"
    TABLE_LEDGER = "xx64_ledger"
    TABLE_POINTS = "xx64_points_exchange"
    TABLE_QUOTAS = "xx64_quotas"
    TABLE_RISK = "xx64_risk"
    TABLE_ANCHORS = "xx64_anchors"
    TABLE_APPEALS = "xx64_appeals"
    TABLE_EVENTS = "xx64_events"

    _ALL_TABLES = (
        TABLE_ORDERS, TABLE_LEDGER,
        TABLE_POINTS, TABLE_QUOTAS,
        TABLE_RISK, TABLE_ANCHORS,
        TABLE_APPEALS, TABLE_EVENTS)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "orderId", "entryId", "exchangeId",
        "quotaId", "riskId", "anchorId",
        "appealId", "eventId", "buyerId",
        "sellerId", "trustId", "points",
        "version", "changeId", "windowDays")
    _FLOAT_FIELDS = (
        "price", "trustValue", "cashValue",
        "balance", "balanceSnapshot",
        "maxSnapshot", "singleQuota",
        "cumulativeQuota", "windowUsed",
        "trustDelta", "pointsValue",
        "purchasingPower", "avgPrice",
        "exchangeRate", "trustScore",
        "pointsSpent", "trustGained")
    _JSON_DICT_FIELDS = (
        "snapshot", "detail", "context",
        "config", "result", "factors",
        "reason", "correction", "stats",
        "precheck", "explain", "extra")
    _JSON_LIST_FIELDS = (
        "orders", "findings", "signals",
        "history", "auditTrail")
    _BOOL_FIELDS = (
        "negative", "exclusive",
        "reserved", "frozen",
        "overturned", "pooled",
        "matched")

    _TABLE_BY_KIND = {
        "order": TABLE_ORDERS,
        "ledger": TABLE_LEDGER,
        "points": TABLE_POINTS,
        "quota": TABLE_QUOTAS,
        "risk": TABLE_RISK,
        "anchor": TABLE_ANCHORS,
        "appeal": TABLE_APPEALS,
        "event": TABLE_EVENTS,
    }

    # 表主键字段(排序依据)
    _KEY_FIELD_BY_KIND = {
        "order": "orderId",
        "ledger": "entryId",
        "points": "exchangeId",
        "quota": "quotaId",
        "risk": "riskId",
        "anchor": "anchorId",
        "appeal": "appealId",
        "event": "eventId",
    }

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        for table in self._ALL_TABLES:
            self.store.setdefault(table, {})

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(
                    v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @classmethod
    def _deserialize(cls, data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in cls._INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in cls._FLOAT_FIELDS:
                try:
                    record[k] = float(v) \
                        if v != "" else 0.0
                except (TypeError, ValueError):
                    record[k] = 0.0
            elif k in cls._BOOL_FIELDS:
                record[k] = str(v).strip().lower() \
                    in ("1", "true", "yes")
            elif k in cls._JSON_DICT_FIELDS:
                try:
                    record[k] = json.loads(v) \
                        if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            elif k in cls._JSON_LIST_FIELDS:
                try:
                    record[k] = json.loads(v) \
                        if v else []
                except (TypeError, ValueError):
                    record[k] = []
            else:
                record[k] = v
        return record

    @classmethod
    def _table_of(cls, kind: str) -> str:
        return cls._TABLE_BY_KIND.get(
            kind, f"xx64_{kind}s")

    async def _next_seq(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("xx64", kind, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_xx64_{kind}_seq", 0) + 1
        self.store[f"_xx64_{kind}_seq"] = seq
        return seq

    async def _save(self, kind: str,
                    record: dict,
                    key_field: str,
                    *, create: bool = True) -> dict:
        table = self._table_of(kind)
        key = record[key_field]
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(
                transaction=False)
            pipe.hset(_k("xx64", table, key),
                      mapping=self._serialize(
                          record))
            if create:
                pipe.lpush(
                    _k("xx64", f"{kind}_all"),
                    key)
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[table][key] = dict(record)
        if create:
            self.store.setdefault(
                f"_xx64_{kind}_all", []).insert(
                0, key)
        return record

    async def _get(self, kind: str,
                   key) -> dict | None:
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("xx64", table, key))
            return self._deserialize(data) \
                if data else None
        self._ensure_store()
        rec = self.store[table].get(key)
        return dict(rec) if rec else None

    async def _list(self, table_kind: str,
                    limit: int = 100,
                    **filters) -> list[dict]:
        table = self._table_of(table_kind)
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("xx64", f"{table_kind}_all"),
                0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for rid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "xx64", table, rid))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(
                                data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          table].values()]
        for field, value in filters.items():
            if value is not None:
                result = [r for r in result
                          if r.get(field)
                          == value]
        key_field = \
            self._KEY_FIELD_BY_KIND.get(
                table_kind,
                f"{table_kind}Id")
        result.sort(key=lambda r: -(
            int(r.get(key_field) or 0)))
        return result[:limit]

    # --------------------------------------------------------
    # 订单(orderId)
    # --------------------------------------------------------

    async def next_order_id(self) -> int:
        return await self._next_seq("order")

    async def save_order(self, record: dict,
                          *, create: bool = True
                          ) -> dict:
        return await self._save(
            "order", record, "orderId",
            create=create)

    async def get_order(self, order_id: int
                       ) -> dict | None:
        return await self._get(
            "order", int(order_id))

    async def list_orders(self,
                         buyer_id: int = None,
                         seller_id: int = None,
                         status: str = None,
                         limit: int = 200
                         ) -> list[dict]:
        return await self._list(
            "order", limit,
            buyerId=buyer_id,
            sellerId=seller_id,
            status=status)

    # --------------------------------------------------------
    # 转移账本(entryId+direction 复合主键
    # ——借贷对两笔同 entryId 不覆盖)
    # --------------------------------------------------------

    async def next_entry_id(self) -> int:
        return await self._next_seq("ledger")

    async def save_ledger(self, record: dict,
                          *, create: bool = True
                          ) -> dict:
        # 复合键: entryId:direction
        # (借贷对两笔同 entryId 共存)
        keyed = dict(record)
        key = (f"{record['entryId']}:"
               f"{record['direction']}")
        table = self._table_of("ledger")
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(
                transaction=False)
            pipe.hset(_k("xx64", table, key),
                      mapping=self._serialize(
                          keyed))
            if create:
                pipe.lpush(
                    _k("xx64",
                       "ledger_all"), key)
            await pipe.execute()
            return keyed
        self._ensure_store()
        self.store[table][key] = dict(keyed)
        if create:
            self.store.setdefault(
                "_xx64_ledger_all",
                []).insert(0, key)
        return keyed

    async def get_ledger(self, entry_id: int,
                         direction: str = None
                         ) -> dict | None:
        key = str(int(entry_id)) \
            + (f":{direction}"
               if direction else "")
        return await self._get(
            "ledger", key)

    async def list_ledger(self,
                          order_id: int = None,
                          trust_id: int = None,
                          limit: int = 200
                          ) -> list[dict]:
        return await self._list(
            "ledger", limit,
            orderId=order_id,
            trustId=trust_id)

    # --------------------------------------------------------
    # 积分兑换(exchangeId——P1)
    # --------------------------------------------------------

    async def next_exchange_id(self) -> int:
        return await self._next_seq("points")

    async def save_exchange(self, record: dict,
                            *, create: bool = True
                            ) -> dict:
        return await self._save(
            "points", record, "exchangeId",
            create=create)

    async def get_exchange(self,
                           exchange_id: int
                           ) -> dict | None:
        return await self._get(
            "points", int(exchange_id))

    async def list_exchanges(self,
                            user_id: int = None,
                            status: str = None,
                            limit: int = 200
                            ) -> list[dict]:
        return await self._list(
            "points", limit,
            buyerId=user_id,
            status=status)

    # --------------------------------------------------------
    # 限额快照(quotaId)
    # --------------------------------------------------------

    async def next_quota_id(self) -> int:
        return await self._next_seq("quota")

    async def save_quota(self, record: dict,
                         *, create: bool = True
                         ) -> dict:
        return await self._save(
            "quota", record, "quotaId",
            create=create)

    async def get_quota(self, quota_id: int
                       ) -> dict | None:
        return await self._get(
            "quota", int(quota_id))

    async def list_quotas(self,
                          buyer_id: int = None,
                          limit: int = 200
                          ) -> list[dict]:
        return await self._list(
            "quota", limit,
            buyerId=buyer_id)

    # --------------------------------------------------------
    # 风控事件(riskId——P3)
    # --------------------------------------------------------

    async def next_risk_id(self) -> int:
        return await self._next_seq("risk")

    async def save_risk(self, record: dict,
                        *, create: bool = True
                        ) -> dict:
        return await self._save(
            "risk", record, "riskId",
            create=create)

    async def list_risks(self,
                        trust_id: int = None,
                        limit: int = 200
                        ) -> list[dict]:
        return await self._list(
            "risk", limit,
            trustId=trust_id)

    # --------------------------------------------------------
    # 锚定快照(anchorId——P4)
    # --------------------------------------------------------

    async def next_anchor_id(self) -> int:
        return await self._next_seq("anchor")

    async def save_anchor(self, record: dict,
                          *, create: bool = True
                          ) -> dict:
        return await self._save(
            "anchor", record, "anchorId",
            create=create)

    async def list_anchors(self,
                          limit: int = 100
                          ) -> list[dict]:
        return await self._list(
            "anchor", limit)

    # --------------------------------------------------------
    # 阈值配置域(tier 键——P4)
    # --------------------------------------------------------

    async def save_threshold(self,
                             record: dict,
                             *, create: bool = True
                             ) -> dict:
        return await self._save(
            "threshold", record, "tier",
            create=create)

    async def get_threshold(self,
                            tier: str
                            ) -> dict | None:
        return await self._get(
            "threshold", tier)

    async def list_thresholds(self,
                             limit: int = 10
                             ) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.lrange(
                _k("xx64", "threshold_all"),
                0, -1)
            result = []
            for k in keys[:limit]:
                data = await client.hgetall(
                    _k("xx64",
                       "xx64_thresholds", k))
                if data:
                    result.append(
                        self._deserialize(
                            data))
            return result
        self._ensure_store()
        table = self._table_of("threshold")
        return [dict(r) for r in
                list(self.store[
                    table].values())[:limit]]

    # --------------------------------------------------------
    # 申诉(appealId——P4)
    # --------------------------------------------------------

    async def next_appeal_id(self) -> int:
        return await self._next_seq("appeal")

    async def save_appeal(self, record: dict,
                          *, create: bool = True
                          ) -> dict:
        return await self._save(
            "appeal", record, "appealId",
            create=create)

    async def get_appeal(self, appeal_id: int
                        ) -> dict | None:
        return await self._get(
            "appeal", int(appeal_id))

    async def list_appeals(self,
                           order_id: int = None,
                           status: str = None,
                           limit: int = 200
                           ) -> list[dict]:
        return await self._list(
            "appeal", limit,
            orderId=order_id,
            status=status)

    # --------------------------------------------------------
    # 全链事件(eventId)
    # --------------------------------------------------------

    async def next_event_id(self) -> int:
        return await self._next_seq("event")

    async def add_event(self, record: dict
                        ) -> dict:
        return await self._save(
            "event", record, "eventId")

    async def list_events(self,
                          limit: int = 100
                          ) -> list[dict]:
        return await self._list(
            "event", limit)
