"""60号·AI智能支付管理 仓储
(pay60_repository)

计划(docs/60号_AI智能支付管理模块实施计划.md §五):
    8 表(前缀 pay60):
        pay60_orders         支付订单(状态机+归因链+定价快照)
        pay60_checkouts      收银台渲染(场景组合+renderOptions)
        pay60_verifications  验证事件(riskTier+验证方式+结果)
        pay60_flows          渠道流水(mock/real 回执+指纹)
        pay60_recon          对账批次(差异分类+归因+冲正态)
        pay60_splits         分账指令(合约版本+拆分明细+结算态)
        pay60_thresholds     风控/定价阈值配置域(46号审批联动)
        pay60_events         全链事件

58/59/63号仓储范式平移:
    - 通用读写基元(_save/_get/_list——
      _TABLE_BY_KIND 显式映射)
    - 五清单显式序列化(新增字段必须同步)
    - table_kind 位置参数避开记录字段撞名
"""

import json

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)


class Pay60Repository:
    """60号八表仓储(双模式——asyncio/Redis)"""

    TABLE_ORDERS = "pay60_orders"
    TABLE_CHECKOUTS = "pay60_checkouts"
    TABLE_VERIFICATIONS = "pay60_verifications"
    TABLE_FLOWS = "pay60_flows"
    TABLE_RECON = "pay60_recon"
    TABLE_SPLITS = "pay60_splits"
    TABLE_THRESHOLDS = "pay60_thresholds"
    TABLE_EVENTS = "pay60_events"

    _ALL_TABLES = (
        TABLE_ORDERS, TABLE_CHECKOUTS,
        TABLE_VERIFICATIONS, TABLE_FLOWS,
        TABLE_RECON, TABLE_SPLITS,
        TABLE_THRESHOLDS, TABLE_EVENTS)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "payId", "checkoutId", "verifyId",
        "flowId", "reconId", "splitId",
        "eventId", "memberId",
        "intentId", "sessionId",
        "pooledFeedbackId", "changeId",
        "attemptCount")
    _FLOAT_FIELDS = (
        "basePrice", "finalPrice",
        "amount", "trustScore",
        "riskScore", "promoFactor",
        "actualAmount", "expectedAmount",
        "refundAmount")
    _JSON_DICT_FIELDS = (
        "attribution", "context", "detail",
        "factors", "reason", "results",
        "signals", "thresholds", "config",
        "renderOptions", "content",
        "evidence", "extra", "pricing",
        "channelReceipt", "recovery",
        "attributionChain",
        "settlement", "reversal")
    _JSON_LIST_FIELDS = (
        "candidates", "steps", "splits",
        "auditTrail", "history",
        "findings", "methods",
        "differences", "invoices")
    _BOOL_FIELDS = (
        "granted", "conserved",
        "humanVerified", "pooled",
        "approved", "escalated",
        "spotCheck", "settled",
        "reversed")

    _TABLE_BY_KIND = {
        "order": TABLE_ORDERS,
        "checkout": TABLE_CHECKOUTS,
        "verification": TABLE_VERIFICATIONS,
        "flow": TABLE_FLOWS,
        "recon": TABLE_RECON,
        "split": TABLE_SPLITS,
        "threshold": TABLE_THRESHOLDS,
        "event": TABLE_EVENTS,
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
            kind, f"pay60_{kind}s")

    async def _next_seq(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay60", kind, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_pay60_{kind}_seq", 0) + 1
        self.store[f"_pay60_{kind}_seq"] = seq
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
            pipe.hset(_k("pay60", table, key),
                      mapping=self._serialize(
                          record))
            if create:
                pipe.lpush(
                    _k("pay60", f"{kind}_all"),
                    key)
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[table][key] = dict(record)
        if create:
            self.store.setdefault(
                f"_pay60_{kind}_all", []).insert(
                0, key)
        return record

    async def _get(self, kind: str,
                   key) -> dict | None:
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay60", table, key))
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
                _k("pay60", f"{table_kind}_all"),
                0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for rid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "pay60", table, rid))
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
        result.sort(key=lambda r: -(
            int(r.get(f"{table_kind}Id")
                or 0)))
        return result[:limit]

    # --------------------------------------------------------
    # 支付订单(payId)
    # --------------------------------------------------------

    async def next_pay_id(self) -> int:
        return await self._next_seq("order")

    async def save_order(self, record: dict,
                         *, create: bool = True
                         ) -> dict:
        return await self._save(
            "order", record, "payId",
            create=create)

    async def get_order(self, pay_id: int
                        ) -> dict | None:
        return await self._get(
            "order", int(pay_id))

    async def list_orders(self,
                          member_id: int = None,
                          status: str = None,
                          limit: int = 200
                          ) -> list[dict]:
        return await self._list(
            "order", limit,
            memberId=member_id,
            status=status)

    # --------------------------------------------------------
    # 收银台渲染(checkoutId)
    # --------------------------------------------------------

    async def next_checkout_id(self) -> int:
        return await self._next_seq("checkout")

    async def save_checkout(self,
                            record: dict,
                            *, create: bool = True
                            ) -> dict:
        return await self._save(
            "checkout", record, "checkoutId",
            create=create)

    async def get_checkout(self,
                           checkout_id: int
                           ) -> dict | None:
        return await self._get(
            "checkout", int(checkout_id))

    async def list_checkouts(self,
                             member_id: int = None,
                             limit: int = 200
                             ) -> list[dict]:
        return await self._list(
            "checkout", limit,
            memberId=member_id)

    # --------------------------------------------------------
    # 验证事件(verifyId)
    # --------------------------------------------------------

    async def next_verify_id(self) -> int:
        return await self._next_seq(
            "verification")

    async def save_verification(self,
                                record: dict,
                                *, create: bool = True
                                ) -> dict:
        return await self._save(
            "verification", record,
            "verifyId", create=create)

    async def get_verification(self,
                              verify_id: int
                              ) -> dict | None:
        return await self._get(
            "verification", int(verify_id))

    async def list_verifications(self,
                                 pay_id: int = None,
                                 limit: int = 200
                                 ) -> list[dict]:
        return await self._list(
            "verification", limit,
            payId=pay_id)

    # --------------------------------------------------------
    # 渠道流水(flowId)
    # --------------------------------------------------------

    async def next_flow_id(self) -> int:
        return await self._next_seq("flow")

    async def save_flow(self, record: dict,
                        *, create: bool = True
                        ) -> dict:
        return await self._save(
            "flow", record, "flowId",
            create=create)

    async def get_flow(self, flow_id: int
                       ) -> dict | None:
        return await self._get(
            "flow", int(flow_id))

    async def list_flows(self,
                         pay_id: int = None,
                         limit: int = 200
                         ) -> list[dict]:
        return await self._list(
            "flow", limit, payId=pay_id)

    # --------------------------------------------------------
    # 对账批次(reconId)
    # --------------------------------------------------------

    async def next_recon_id(self) -> int:
        return await self._next_seq("recon")

    async def save_recon(self, record: dict,
                         *, create: bool = True
                         ) -> dict:
        return await self._save(
            "recon", record, "reconId",
            create=create)

    async def get_recon(self, recon_id: int
                        ) -> dict | None:
        return await self._get(
            "recon", int(recon_id))

    async def list_recon(self,
                         status: str = None,
                         limit: int = 200
                         ) -> list[dict]:
        return await self._list(
            "recon", limit, status=status)

    # --------------------------------------------------------
    # 分账指令(splitId)
    # --------------------------------------------------------

    async def next_split_id(self) -> int:
        return await self._next_seq("split")

    async def save_split(self, record: dict,
                         *, create: bool = True
                         ) -> dict:
        return await self._save(
            "split", record, "splitId",
            create=create)

    async def get_split(self, split_id: int
                        ) -> dict | None:
        return await self._get(
            "split", int(split_id))

    async def list_splits(self,
                          pay_id: int = None,
                          status: str = None,
                          limit: int = 200
                          ) -> list[dict]:
        return await self._list(
            "split", limit,
            payId=pay_id, status=status)

    # --------------------------------------------------------
    # 阈值配置域(tier 键)
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
        table = self._table_of("threshold")
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.lrange(
                _k("pay60", "threshold_all"),
                0, -1)
            result = []
            for k in keys[:limit]:
                data = await client.hgetall(
                    _k("pay60", table, k))
                if data:
                    result.append(
                        self._deserialize(
                            data))
            return result
        self._ensure_store()
        return [dict(r) for r in
                self.store[
                    table].values()][:limit]

    # --------------------------------------------------------
    # 全链事件(eventId)
    # --------------------------------------------------------

    async def next_event_id(self) -> int:
        return await self._next_seq("event")

    async def add_event(self,
                        record: dict) -> dict:
        """事件追加"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(
                transaction=False)
            pipe.hset(_k(
                "pay60", self.TABLE_EVENTS,
                record["eventId"]),
                mapping=self._serialize(
                    record))
            pipe.lpush(
                _k("pay60", "event_all"),
                record["eventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][
            record["eventId"]] = dict(record)
        self.store.setdefault(
            "_pay60_event_all", []).insert(
            0, record["eventId"])
        return record

    async def list_events(self,
                          event_type: str = None,
                          limit: int = 100
                          ) -> list[dict]:
        return await self._list(
            "event", limit,
            eventType=event_type)
