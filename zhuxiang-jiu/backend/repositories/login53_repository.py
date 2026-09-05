"""53号·小竹智能登录引擎 仓储层(login53_repository)

计划(docs/53号_小竹智能登录引擎实施计划.md §二):
    4 表(前缀 login53, 双模式存储)——
    login53_profiles    角色入口档案(memberId 自然键)
    login53_events      登录事件流水(eventId)
    login53_retention   驻留激励台账(memberId+dayKey)
    login53_snapshots   效果指标快照(snapId)

设计对齐(51/52号范式平移):
    - 双模式存储+显式序列化口径
      (bool→0/1, dict/list→JSON, None→"")
    - 列表索引只创建时入列(45号索引教训)
    - reset_all 全量清理(测试+实机幂等)
"""

import json

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


class Login53Repository:
    """53号智能登录引擎仓储(双模式)"""

    TABLE_PROFILES = "login53_profiles"
    TABLE_EVENTS = "login53_events"
    TABLE_RETENTION = "login53_retention"
    TABLE_SNAPSHOTS = "login53_snapshots"

    _INT_FIELDS = ("eventId", "memberId", "snapId",
                   "streakDays", "seenCount",
                   "successCount", "failCount")
    _FLOAT_FIELDS = ("riskScore", "privacyCost",
                     "durationMs", "value", "baseline")
    _JSON_DICT_FIELDS = ("metrics", "context", "profile",
                         "failCounts")
    _JSON_LIST_FIELDS = ("intentTags", "factors",
                         "hooks")

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        for table in (self.TABLE_PROFILES,
                      self.TABLE_EVENTS,
                      self.TABLE_RETENTION,
                      self.TABLE_SNAPSHOTS):
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
                out[k] = json.dumps(v, ensure_ascii=False)
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
                    record[k] = float(v) if v != "" \
                        else 0.0
                except (TypeError, ValueError):
                    record[k] = 0.0
            elif k in cls._JSON_DICT_FIELDS:
                try:
                    record[k] = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            elif k in cls._JSON_LIST_FIELDS:
                try:
                    record[k] = json.loads(v) if v else []
                except (TypeError, ValueError):
                    record[k] = []
            else:
                record[k] = v
        return record

    # --------------------------------------------------------
    # 角色入口档案(memberId 自然键)
    # --------------------------------------------------------

    async def get_profile(self,
                          member_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "login53", self.TABLE_PROFILES, member_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_PROFILES].get(member_id)
        return dict(rec) if rec else None

    async def save_profile(self,
                           record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("login53", self.TABLE_PROFILES,
                   record["memberId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_PROFILES][
            record["memberId"]] = dict(record)
        return record

    # --------------------------------------------------------
    # 登录事件流水(eventId 只追加)
    # --------------------------------------------------------

    async def next_event_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("login53", "events", "seq"))
        self._ensure_store()
        seq = self.store.get(
            "_login53_events_seq", 0) + 1
        self.store["_login53_events_seq"] = seq
        return seq

    async def save_event(self, record: dict) -> dict:
        """事件落库(创建入列——只追加)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("login53", self.TABLE_EVENTS,
                         record["eventId"]),
                      mapping=self._serialize(record))
            pipe.lpush(_k("login53", "events_all"),
                       record["eventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][
            record["eventId"]] = dict(record)
        self.store.setdefault(
            "_login53_events_all", []).insert(
            0, record["eventId"])
        return record

    async def list_events(self,
                          member_id: int = None,
                          limit: int = 200) -> list[dict]:
        """事件列表(最新在前; 会员过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("login53", "events_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for eid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "login53", self.TABLE_EVENTS,
                        int(eid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[self.TABLE_EVENTS]
                      .values()]
        if member_id is not None:
            result = [e for e in result
                     if int(e.get("memberId") or 0)
                     == int(member_id)]
        result.sort(key=lambda e: -int(
            e.get("eventId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 驻留激励台账(memberId+dayKey 复合自然键)
    # --------------------------------------------------------

    @staticmethod
    def _retention_key(member_id: int,
                       day_key: str) -> str:
        return f"{member_id}|{day_key}"

    async def get_retention(self, member_id: int,
                            day_key: str) -> dict | None:
        key = self._retention_key(member_id, day_key)
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "login53", self.TABLE_RETENTION, key))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_RETENTION].get(key)
        return dict(rec) if rec else None

    async def save_retention(self, record: dict) -> dict:
        key = self._retention_key(
            record["memberId"], record["dayKey"])
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("login53", self.TABLE_RETENTION, key),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_RETENTION][key] = \
            dict(record)
        return record

    async def list_retention(self,
                             member_id: int = None,
                             limit: int = 100) -> list[dict]:
        """驻留台账列表(时间倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "login53", self.TABLE_RETENTION, "*"))
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_RETENTION].values()]
        if member_id is not None:
            result = [r for r in result
                     if int(r.get("memberId") or 0)
                     == int(member_id)]
        result.sort(key=lambda r: str(
            r.get("dayKey") or ""), reverse=True)
        return result[:limit]

    # --------------------------------------------------------
    # 指标快照(snapId 只追加)
    # --------------------------------------------------------

    async def next_snap_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("login53", "snapshots", "seq"))
        self._ensure_store()
        seq = self.store.get(
            "_login53_snap_seq", 0) + 1
        self.store["_login53_snap_seq"] = seq
        return seq

    async def save_snapshot(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("login53", self.TABLE_SNAPSHOTS,
                         record["snapId"]),
                      mapping=self._serialize(record))
            pipe.lpush(_k("login53", "snapshots_all"),
                       record["snapId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_SNAPSHOTS][
            record["snapId"]] = dict(record)
        self.store.setdefault(
            "_login53_snapshots_all", []).insert(
            0, record["snapId"])
        return record

    async def list_snapshots(
            self, limit: int = 50) -> list[dict]:
        """快照列表(最新在前——回溯可比)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("login53", "snapshots_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for sid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "login53", self.TABLE_SNAPSHOTS, sid))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_SNAPSHOTS].values()]
        result.sort(key=lambda r: -int(
            r.get("snapId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 测试辅助
    # --------------------------------------------------------

    async def reset_all(self) -> None:
        """全量清理(测试+实机幂等)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("login53", "*"))
            pipe = client.pipeline(transaction=False)
            for k in keys:
                pipe.delete(k)
            await pipe.execute()
            return
        self._ensure_store()
        for table in (self.TABLE_PROFILES,
                      self.TABLE_EVENTS,
                      self.TABLE_RETENTION,
                      self.TABLE_SNAPSHOTS):
            self.store[table] = {}
        self.store["_login53_events_seq"] = 0
        self.store["_login53_events_all"] = []
        self.store["_login53_snap_seq"] = 0
        self.store["_login53_snapshots_all"] = []
