"""71号·AI智能支付端口大模型 仓储
(pay71_repository, P0)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P0):
    3 表(前缀 pay71):
        pay71_ports       端口态观测记录
                          (三态+前兆评分)
        pay71_signals     前兆信号留痕
                          (预警观测——快环)
        pay71_events      全链事件埋点
                          (P1 自愈/P3 调配
                          共享)

69号仓储范式平移:
    - 通用读写基元(_save/_get/_list)
    - 五清单显式序列化(新增字段必须同步)
    - 71号永不写 69号表(叠加铁律)
"""

import contextlib
import json
import time

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)

# 信号留痕截断倍数(容量×2——观测上限
# 之外的余量)
_SIGNAL_WINDOW = 100


def _now_ts() -> float:
    """当前 Unix 秒(内存态 TTL 模拟)"""
    return time.time()


class Pay71Repository:
    """71号仓储(双模式——asyncio/Redis)"""

    TABLE_PORTS = "pay71_ports"
    TABLE_SIGNALS = "pay71_signals"
    TABLE_EVENTS = "pay71_events"

    _ALL_TABLES = (
        TABLE_PORTS, TABLE_SIGNALS,
        TABLE_EVENTS)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "portSeq", "signalSeq", "eventSeq",
        "sampleCount")
    _FLOAT_FIELDS = (
        "avgLatencyMs", "errorRate",
        "successRate", "precursorScore",
        "latencyDelta", "errorRateDelta",
        "successRateDelta", "callbackMs")
    _BOOL_FIELDS = ("alerted", "observed")
    _JSON_DICT_FIELDS = (
        "windows", "meta", "detail",
        "signals")
    _JSON_LIST_FIELDS = ("traits",)

    def __init__(self, store: dict = None):
        self.store = (store if store is not None
                      else get_in_memory_store())

    # ============================================================
    # 通用基元(69号范式)
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
    # 端口态观测(pay71_ports)
    # ============================================================

    async def get_port(self, port_id: str) -> dict | None:
        """按端口 ID 查端口态记录(内存态
        存取同形——69号 get_channel 范式)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("pay71", "port", port_id))
            if not data:
                return None
            return self._deserialize(data)
        self._ensure_store()
        rec = self.store[self.TABLE_PORTS]\
            .get(port_id)
        return dict(rec) if rec else None

    async def save_port(self, port_id: str,
                        record: dict) -> dict:
        """保存/覆盖端口态记录"""
        record["portId"] = port_id
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("pay71", "port", port_id)
            await client.hset(
                key, mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_PORTS][port_id] \
            = dict(record)
        return record

    async def list_ports(self) -> list[dict]:
        """列出全部端口态记录(未观测
        端口默认 healthy——观测面零影响)"""
        from services.pay71_registry import (
            _port_ids,
        )
        result = []
        for pid in _port_ids():
            rec = await self.get_port(pid)
            result.append(rec or {
                "portId": pid,
                "portState": "healthy",
                "alerted": False,
                "observed": False,
            })
        return result

    # ============================================================
    # 前兆信号留痕(pay71_signals)
    # ============================================================

    async def next_signal_seq(self) -> int:
        """前兆信号序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "signal", "seq"))
        self._ensure_store()
        self.store["_pay71_signal_seq"] = \
            self.store.get("_pay71_signal_seq", 0) + 1
        return self.store["_pay71_signal_seq"]

    async def save_signal(self, record: dict) -> dict:
        """保存前兆信号留痕"""
        seq = await self.next_signal_seq()
        record["signalSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k(
                "pay71", "signal", str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            await client.expire(
                key, 7 * 24 * 3600)
            return record
        self._ensure_store()
        self.store[self.TABLE_SIGNALS][str(seq)] \
            = dict(record)
        return record

    async def list_signals(
            self, limit: int = 50) -> list[dict]:
        """列出前兆信号留痕(近 limit 条)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "signal", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs[:limit]:
                raw = await client.get(
                    _k("pay71", "signal", str(seq)))
                if raw:
                    with contextlib.suppress(
                            ValueError, TypeError):
                        result.append(
                            self._deserialize(
                                json.loads(raw)))
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[self.TABLE_SIGNALS]),
            reverse=True)
        return [
            dict(self.store[
                self.TABLE_SIGNALS][str(s)])
            for s in seqs[:limit]
        ]

    # ============================================================
    # 全链事件埋点(pay71_events)
    # ============================================================

    async def next_event_seq(self) -> int:
        """事件序列"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("pay71", "event", "seq"))
        self._ensure_store()
        self.store["_pay71_event_seq"] = \
            self.store.get("_pay71_event_seq", 0) + 1
        return self.store["_pay71_event_seq"]

    async def save_event(self, event: dict) -> dict:
        """保存事件埋点(归因可审计)"""
        seq = await self.next_event_seq()
        event["eventSeq"] = seq
        if is_redis_mode():
            client = await get_redis_client()
            key = _k(
                "pay71", "event", str(seq))
            await client.set(
                key, json.dumps(
                    self._serialize(event),
                    ensure_ascii=False))
            return event
        self._ensure_store()
        self.store[self.TABLE_EVENTS][str(seq)] \
            = dict(event)
        return event

    async def list_events(
            self, limit: int = 50) -> list[dict]:
        """列出事件埋痕(近 limit 条)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("pay71", "event", "*"))
            seqs: list[int] = []
            for key in keys:
                tail = key.split(":")[-1]
                if tail.isdigit():
                    seqs.append(int(tail))
            seqs.sort(reverse=True)
            result = []
            for seq in seqs[:limit]:
                raw = await client.get(
                    _k("pay71", "event", str(seq)))
                if raw:
                    with contextlib.suppress(
                            ValueError, TypeError):
                        result.append(
                            self._deserialize(
                                json.loads(raw)))
            return result
        self._ensure_store()
        seqs = sorted(
            (int(s) for s in
             self.store[self.TABLE_EVENTS]),
            reverse=True)
        return [
            dict(self.store[
                self.TABLE_EVENTS][str(s)])
            for s in seqs[:limit]
        ]
