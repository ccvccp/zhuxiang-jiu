"""69号·AI智能支付大模型 仓储
(pay69_repository, P0)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§七 P0):
    3 表(前缀 pay69):
        pay69_channels    通道健康度观测
                          (滚动窗口统计基线)
        pay69_intents     意图标签留痕
                          (60号归因链消费)
        pay69_events      全链事件埋点
                          (P1 路由/P2 熵共享)

60号仓储范式平移:
    - 通用读写基元(_save/_get/_list)
    - 五清单显式序列化(新增字段必须同步)
    - 69号永不写 60号表(叠加铁律)
"""

import contextlib
import json

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)


class Pay69Repository:
    """69号三表仓储(双模式——asyncio/Redis)"""

    TABLE_CHANNELS = "pay69_channels"
    TABLE_INTENTS = "pay69_intents"
    TABLE_EVENTS = "pay69_events"

    _ALL_TABLES = (
        TABLE_CHANNELS, TABLE_INTENTS,
        TABLE_EVENTS)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "channelSeq", "intentSeq", "eventSeq",
        "memberId", "intentId", "sessionId",
        "attemptCount", "successCount",
        "failureCount", "timeoutCount")
    _FLOAT_FIELDS = (
        "successRate", "avgLatencyMs",
        "feeRate", "singleLimit",
        "dailyLimit", "amount")
    _BOOL_FIELDS = ("frozen",)
    _JSON_DICT_FIELDS = (
        "windows", "traits", "meta",
        "attribution", "context", "detail")
    _JSON_LIST_FIELDS = ("tags", "candidates")

    def __init__(self, store: dict = None):
        self.store = (store if store is not None
                      else get_in_memory_store())

    # ============================================================
    # 通用基元(60号范式)
    # ============================================================

    def _ensure_store(self):
        for table in self._ALL_TABLES:
            self.store.setdefault(table, {})

    def _serialize(self, record: dict) -> dict:
        """五清单序列化(Redis Hash 兼容)"""
        out = {}
        for k, v in record.items():
            if v is None:
                continue
            if k in self._INT_FIELDS:
                out[k] = int(v)
            elif k in self._FLOAT_FIELDS:
                out[k] = float(v)
            elif k in self._BOOL_FIELDS:
                out[k] = 1 if v else 0
            elif k in self._JSON_DICT_FIELDS:
                out[k] = (json.dumps(v, ensure_ascii=False)
                          if isinstance(v, dict) else v)
            elif k in self._JSON_LIST_FIELDS:
                out[k] = (json.dumps(v, ensure_ascii=False)
                          if isinstance(v, (list, tuple))
                          else v)
            else:
                out[k] = str(v)
        return out

    def _deserialize(self, data: dict) -> dict:
        """五清单反序列化(读回类型还原)"""
        out = dict(data)
        for k in self._INT_FIELDS:
            if k in out:
                with contextlib.suppress(
                        TypeError, ValueError):
                    out[k] = int(float(out[k]))
        for k in self._FLOAT_FIELDS:
            if k in out:
                with contextlib.suppress(
                        TypeError, ValueError):
                    out[k] = float(out[k])
        for k in self._BOOL_FIELDS:
            if k in out:
                out[k] = str(out[k]) == "1"
        for k in (self._JSON_DICT_FIELDS
                  + self._JSON_LIST_FIELDS):
            if k in out and isinstance(out[k], str):
                with contextlib.suppress(
                        ValueError, TypeError):
                    out[k] = json.loads(out[k])
        return out

    # ============================================================
    # 通道健康度观测(pay69_channels)
    # ============================================================

    async def get_channel(self, channel_id: str) -> dict | None:
        """按通道 ID 查健康度记录(内存态
        存取同形——60号 _get 范式, 不走
        deserialize 防 bool 陷阱)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay69", "channel", channel_id))
            if not data:
                return None
            return self._deserialize(data)
        self._ensure_store()
        rec = self.store[self.TABLE_CHANNELS]\
            .get(channel_id)
        return dict(rec) if rec else None

    async def save_channel(self, channel_id: str,
                          record: dict) -> dict:
        """保存/覆盖通道健康度记录"""
        record["channelId"] = channel_id
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay69", "channel", channel_id)
            await client.hset(
                key, mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_CHANNELS][channel_id] \
            = dict(record)
        return record

    async def list_channels(self) -> list[dict]:
        """列出全部通道健康度记录"""
        from services.pay69_registry import (
            CHANNEL_IDS,
        )
        result = []
        for cid in CHANNEL_IDS:
            rec = await self.get_channel(cid)
            result.append(rec or {
                "channelId": cid,
                "state": "healthy",  # 未观测=默认健康
                "frozen": False,
            })
        return result

    # ============================================================
    # 意图标签留痕(pay69_intents)
    # ============================================================

    async def next_intent_seq(self) -> int:
        """意图标签序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay69", "intent", "seq"))
        self._ensure_store()
        self.store["_pay69_intent_seq"] = \
            self.store.get("_pay69_intent_seq", 0) + 1
        return self.store["_pay69_intent_seq"]

    async def save_intent(self, record: dict) -> dict:
        """保存意图标签留痕"""
        seq = await self.next_intent_seq()
        record["intentSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("pay69", "intent", seq),
                json.dumps(record, ensure_ascii=False))
            await client.sadd(
                _k("pay69", "intent", "index"),
                str(seq))
            return record
        self._ensure_store()
        self.store[self.TABLE_INTENTS][seq] \
            = dict(record)
        return record

    async def get_intent(self, seq: int) -> dict | None:
        """按序列查意图留痕"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("pay69", "intent", seq))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store[self.TABLE_INTENTS].get(seq)

    async def list_intents(
            self, limit: int = 50) -> list[dict]:
        """意图留痕列表(倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            seqs = await client.smembers(
                _k("pay69", "intent", "index"))
            result = []
            for s in sorted(
                    (int(x) for x in seqs),
                    reverse=True)[:limit]:
                data = await client.get(
                    _k("pay69", "intent", s))
                if data:
                    result.append(json.loads(data))
            return result
        self._ensure_store()
        recs = sorted(
            self.store[self.TABLE_INTENTS].values(),
            key=lambda r: r.get("intentSeq", 0),
            reverse=True)[:limit]
        return [dict(r) for r in recs]

    # ============================================================
    # 全链事件埋点(pay69_events)
    # ============================================================

    async def next_event_seq(self) -> int:
        """事件序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay69", "event", "seq"))
        self._ensure_store()
        self.store["_pay69_event_seq"] = \
            self.store.get("_pay69_event_seq", 0) + 1
        return self.store["_pay69_event_seq"]

    async def save_event(self, record: dict) -> dict:
        """保存事件埋点"""
        seq = await self.next_event_seq()
        record["eventSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("pay69", "event", seq),
                json.dumps(record, ensure_ascii=False))
            await client.sadd(
                _k("pay69", "event", "index"),
                str(seq))
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][seq] = dict(record)
        return record

    async def list_events(
            self, limit: int = 50) -> list[dict]:
        """事件列表(倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            seqs = await client.smembers(
                _k("pay69", "event", "index"))
            result = []
            for s in sorted(
                    (int(x) for x in seqs),
                    reverse=True)[:limit]:
                data = await client.get(
                    _k("pay69", "event", s))
                if data:
                    result.append(json.loads(data))
            return result
        self._ensure_store()
        recs = sorted(
            self.store[self.TABLE_EVENTS].values(),
            key=lambda r: r.get("eventSeq", 0),
            reverse=True)[:limit]
        return [dict(r) for r in recs]
