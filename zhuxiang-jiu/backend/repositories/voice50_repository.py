"""50号·小竹语音信值积分引擎数据访问层(voice50 前缀)

表清单(计划 §五):
    voice50_events   语音积分事件(evId 键, 只追加——
                     每笔绑 explainability_ref)
    voice50_ledger  会员激励池台账(memberId 自然键——
                     余额/基线/冻结/衰减史)
    voice50_settlement T+1 结算批次(batchId 键——
                     L2/L3 聚合 deposit 申报留痕)
    voice50_rules_log 规则热更新留痕(logId 键, 只追加)

设计对齐(43-49号仓储范式):
    - 双模式存储(内存 + Redis) + 显式序列化
      (bool→0/1, dict/list→JSON, None→"")
    - 事件只追加; 台账 memberId 自然键(49号 privacy 范式)
    - _k("voice50", ...) 独立前缀——与 voice48 键空间隔离
"""

import json

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


class Voice50Repository:
    """语音信值积分仓储(双模式)"""

    TABLE_EVENTS = "voice50_events"
    TABLE_LEDGER = "voice50_ledger"
    TABLE_SETTLEMENT = "voice50_settlement"
    TABLE_RULES_LOG = "voice50_rules_log"

    _INT_FIELDS = ("evId", "memberId", "sessionId", "turnSeq",
                  "logId", "batchId", "eventCount",
                  "depositId")
    _FLOAT_FIELDS = ("baseScore", "finalScore",
                     "poolBalance", "earnedTotal",
                     "offsetUsed", "baseline",
                     "l1PenaltyTotal", "capBaseline",
                     "cappedScore", "overflowScore",
                     "credits", "depositDelta")

    def __init__(self):
        self.store = get_in_memory_store()

    def _ensure_store(self):
        for t in (self.TABLE_EVENTS, self.TABLE_LEDGER,
                  self.TABLE_SETTLEMENT,
                  self.TABLE_RULES_LOG):
            self.store.setdefault(t, {})

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @staticmethod
    def _deserialize(data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in Voice50Repository._INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in Voice50Repository._FLOAT_FIELDS:
                try:
                    record[k] = (float(v)
                                 if v != "" else 0.0)
                except (TypeError, ValueError):
                    record[k] = 0.0
            elif k in ("multipliers", "decayHistory"):
                try:
                    parsed = json.loads(v) if v else {}
                    record[k] = (parsed
                                 if isinstance(parsed,
                                               (dict, list))
                                 else {})
                except (TypeError, ValueError):
                    record[k] = {}
            elif k == "frozen":
                record[k] = v in (1, "1", True, "True", "true")
            else:
                record[k] = v
        return record

    # --------------------------------------------------------
    # 事件(只追加——evId 键)
    # --------------------------------------------------------

    async def next_event_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("voice50", self.TABLE_EVENTS, "seq"))
        self._ensure_store()
        seq = self.store.get(
            "_voice50_events_seq", 0) + 1
        self.store["_voice50_events_seq"] = seq
        return seq

    async def save_event(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("voice50", self.TABLE_EVENTS,
                   record["evId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][
            record["evId"]] = dict(record)
        return record

    async def list_events(self,
                          member_id: int = None,
                          day_key: str = None,
                          status: str = None,
                          limit: int = 5000) -> list[dict]:
        """事件列表(可按会员/日/状态过滤; evId 正序)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "voice50", self.TABLE_EVENTS, "*"))
            keys = [k for k in keys
                    if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if not data:
                        continue
                    rec = self._deserialize(data)
                    if member_id is not None \
                            and rec.get("memberId") \
                            != member_id:
                        continue
                    if day_key is not None \
                            and rec.get("dayKey") != day_key:
                        continue
                    if status is not None \
                            and rec.get("status") != status:
                        continue
                    result.append(rec)
            result.sort(key=lambda r: r.get("evId") or 0)
            return result[:limit]
        self._ensure_store()
        result = [dict(r) for r in
                  self.store[self.TABLE_EVENTS].values()
                  if (member_id is None
                      or r.get("memberId") == member_id)
                  and (day_key is None
                       or r.get("dayKey") == day_key)
                  and (status is None
                       or r.get("status") == status)]
        result.sort(key=lambda r: r.get("evId") or 0)
        return result[:limit]

    # --------------------------------------------------------
    # 台账(激励池——memberId 自然键)
    # --------------------------------------------------------

    async def get_ledger(self,
                         member_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "voice50", self.TABLE_LEDGER, member_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_LEDGER].get(member_id)
        return dict(rec) if rec else None

    async def save_ledger(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("voice50", self.TABLE_LEDGER,
                   record["memberId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_LEDGER][
            record["memberId"]] = dict(record)
        return record

    # --------------------------------------------------------
    # T+1 结算批次(batchId 键——只追加; 幂等由事件状态迁移保证)
    # --------------------------------------------------------

    async def next_batch_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("voice50", self.TABLE_SETTLEMENT, "seq"))
        self._ensure_store()
        seq = self.store.get(
            "_voice50_settlement_seq", 0) + 1
        self.store["_voice50_settlement_seq"] = seq
        return seq

    async def save_settlement(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("voice50", self.TABLE_SETTLEMENT,
                   record["batchId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_SETTLEMENT][
            record["batchId"]] = dict(record)
        return record

    async def list_settlements(self,
                               day_key: str = None,
                               member_id: int = None,
                               limit: int = 200) -> list[dict]:
        """结算批次列表(可按日/会员过滤; batchId 正序)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "voice50", self.TABLE_SETTLEMENT, "*"))
            keys = [k for k in keys
                    if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if not data:
                        continue
                    rec = self._deserialize(data)
                    if day_key is not None \
                            and rec.get("dayKey") != day_key:
                        continue
                    if member_id is not None \
                            and rec.get("memberId") != member_id:
                        continue
                    result.append(rec)
            result.sort(
                key=lambda r: r.get("batchId") or 0)
            return result[-limit:]
        self._ensure_store()
        result = [dict(r) for r in
                  self.store[
                      self.TABLE_SETTLEMENT].values()
                  if (day_key is None
                      or r.get("dayKey") == day_key)
                  and (member_id is None
                       or r.get("memberId") == member_id)]
        result.sort(key=lambda r: r.get("batchId") or 0)
        return result[-limit:]

    # --------------------------------------------------------
    # 规则热更新留痕(只追加——logId 键)
    # --------------------------------------------------------

    async def next_log_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("voice50", self.TABLE_RULES_LOG, "seq"))
        self._ensure_store()
        seq = self.store.get(
            "_voice50_rules_log_seq", 0) + 1
        self.store["_voice50_rules_log_seq"] = seq
        return seq

    async def save_rules_log(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("voice50", self.TABLE_RULES_LOG,
                   record["logId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_RULES_LOG][
            record["logId"]] = dict(record)
        return record

    async def list_rules_log(self,
                             limit: int = 100) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "voice50", self.TABLE_RULES_LOG, "*"))
            keys = [k for k in keys
                    if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
            result.sort(
                key=lambda r: r.get("logId") or 0)
            return result[-limit:]
        self._ensure_store()
        result = [dict(r) for r in
                  self.store[
                      self.TABLE_RULES_LOG].values()]
        result.sort(key=lambda r: r.get("logId") or 0)
        return result[-limit:]
