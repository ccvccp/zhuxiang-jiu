"""55号·二维码AI智能管理 仓储层(qr55_repository)

计划(docs/55号_二维码AI智能管理模块实施计划.md §四):
    4 表(前缀 qr55, 双模式存储)——
    qr55_codes         码实例(载荷/状态)
    qr55_events        全链事件(generate/scan/...)
    qr55_feedback       决策回流标注(P2)
    qr55_model_events   模型生命周期事件(P3)

设计对齐(51-54号范式平移):
    - 双模式存储+显式序列化口径
    - 列表索引只创建时入列(45号索引教训)
    - nonce 消费键(防重放——qr55_codes 状态域)
    - reset_all 全量清理(测试+实机幂等)
"""

import json

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


class Qr55Repository:
    """55号二维码管理仓储(双模式)"""

    TABLE_CODES = "qr55_codes"
    TABLE_EVENTS = "qr55_events"
    TABLE_FEEDBACK = "qr55_feedback"
    TABLE_MODEL_EVENTS = "qr55_model_events"

    _INT_FIELDS = ("codeId", "eventId", "memberId",
                   "feedbackId", "modelEventId",
                   "scanCount")
    _FLOAT_FIELDS = ("trustScore", "privacyCost", "reward")
    _JSON_DICT_FIELDS = ("params", "context", "metrics",
                         "factorCtx")
    _JSON_LIST_FIELDS = ("factors", "evidence")
    _BOOL_FIELDS = ("accessibility",)

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        for table in (self.TABLE_CODES,
                      self.TABLE_EVENTS,
                      self.TABLE_FEEDBACK,
                      self.TABLE_MODEL_EVENTS):
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
                    record[k] = float(v) if v != "" else 0.0
                except (TypeError, ValueError):
                    record[k] = 0.0
            elif k in cls._BOOL_FIELDS:
                record[k] = str(v).strip().lower() in (
                    "1", "true", "yes")
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

    async def _next_seq(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("qr55", kind, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_qr55_{kind}_seq", 0) + 1
        self.store[f"_qr55_{kind}_seq"] = seq
        return seq

    # --------------------------------------------------------
    # 码实例(codeId——含 nonce 消费防重放)
    # --------------------------------------------------------

    async def save_code(self, record: dict) -> dict:
        """码实例落库(创建入列)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("qr55", self.TABLE_CODES,
                        record["codeId"]),
                      mapping=self._serialize(record))
            pipe.lpush(_k("qr55", "codes_all"),
                      record["codeId"])
            pipe.hset(_k("qr55", "nonce",
                        record["nonce"]),
                      mapping={"codeId": record["codeId"],
                               "consumed": 0})
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_CODES][
            record["codeId"]] = dict(record)
        self.store.setdefault(
            "_qr55_codes_all", []).insert(
            0, record["codeId"])
        self.store.setdefault(
            "_qr55_nonce", {})[record["nonce"]] = {
            "codeId": record["codeId"],
            "consumed": 0}
        return record

    async def get_code(self, code_id: int) -> dict | None:
        """码实例读取"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "qr55", self.TABLE_CODES, code_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_CODES].get(code_id)
        return dict(rec) if rec else None

    async def get_by_nonce(self,
                           nonce: str) -> dict | None:
        """按 nonce 查码(防重放消费)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "qr55", "nonce", nonce))
            if not data:
                return None
            return await self.get_code(
                int(data.get("codeId") or 0))
        self._ensure_store()
        entry = self.store.get(
            "_qr55_nonce", {}).get(nonce)
        if not entry:
            return None
        return await self.get_code(
            int(entry.get("codeId") or 0))

    async def consume_nonce(self, nonce: str) -> bool:
        """nonce 一次性消费(True=首次, False=重放)"""
        if is_redis_mode():
            client = await get_redis_client()
            consumed = await client.hincrby(
                _k("qr55", "nonce", nonce),
                "consumed", 1)
            return consumed == 1
        self._ensure_store()
        entry = self.store.get(
            "_qr55_nonce", {}).get(nonce)
        if not entry:
            return True   # 未登记视为首次(容错)
        entry["consumed"] = int(
            entry.get("consumed", 0)) + 1
        return entry["consumed"] == 1

    async def update_code(self, record: dict) -> dict:
        """码实例更新(状态翻转——不入列)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("qr55", self.TABLE_CODES,
                   record["codeId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_CODES][
            record["codeId"]] = dict(record)
        return record

    async def list_codes(self, status: str = None,
                         member_id: int = None,
                         limit: int = 200) -> list[dict]:
        """码列表(最新在前; 状态/会员过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("qr55", "codes_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for cid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "qr55", self.TABLE_CODES,
                        int(cid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_CODES].values()]
        if status:
            result = [c for c in result
                      if (c.get("status") or "")
                      == status]
        if member_id is not None:
            result = [c for c in result
                      if int(c.get("memberId") or 0)
                      == int(member_id)]
        result.sort(key=lambda c: -int(
            c.get("codeId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 全链事件(eventId——generate/scan/redeem/expire/tamper)
    # --------------------------------------------------------

    async def add_event(self, record: dict) -> dict:
        """事件追加"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("qr55", self.TABLE_EVENTS,
                        record["eventId"]),
                      mapping=self._serialize(record))
            pipe.lpush(_k("qr55", "events_all"),
                       record["eventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][
            record["eventId"]] = dict(record)
        self.store.setdefault(
            "_qr55_events_all", []).insert(
            0, record["eventId"])
        return record

    async def next_event_id(self) -> int:
        return await self._next_seq("events")

    async def list_events(self,
                          code_id: int = None,
                          event_type: str = None,
                          limit: int = 200) -> list[dict]:
        """事件列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("qr55", "events_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for eid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "qr55", self.TABLE_EVENTS,
                        int(eid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_EVENTS].values()]
        if code_id is not None:
            result = [e for e in result
                      if int(e.get("codeId") or 0)
                      == int(code_id)]
        if event_type:
            result = [e for e in result
                      if (e.get("eventType") or "")
                      == event_type]
        result.sort(key=lambda e: -int(
            e.get("eventId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 决策回流标注(P2 域——结构预置)
    # --------------------------------------------------------

    async def next_feedback_id(self) -> int:
        return await self._next_seq("feedback")

    async def save_feedback(self, record: dict, *,
                            create: bool = True) -> dict:
        """回流标注落库(create=False 仅更新)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("qr55", self.TABLE_FEEDBACK,
                        record["feedbackId"]),
                      mapping=self._serialize(record))
            if create:
                pipe.lpush(_k("qr55", "feedback_all"),
                           record["feedbackId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_FEEDBACK][
            record["feedbackId"]] = dict(record)
        if create:
            self.store.setdefault(
                "_qr55_feedback_all", []).insert(
                0, record["feedbackId"])
        return record

    async def list_feedback(
            self, event_id: int = None,
            status: str = None,
            limit: int = 200) -> list[dict]:
        """回流标注列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("qr55", "feedback_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for fid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "qr55", self.TABLE_FEEDBACK,
                        int(fid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_FEEDBACK].values()]
        if event_id is not None:
            result = [f for f in result
                      if int(f.get("eventId") or 0)
                      == int(event_id)]
        if status:
            result = [f for f in result
                      if (f.get("status") or "")
                      == status]
        result.sort(key=lambda f: -int(
            f.get("feedbackId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 模型生命周期事件(P3 域——结构预置)
    # --------------------------------------------------------

    async def next_model_event_id(self) -> int:
        return await self._next_seq("model_events")

    async def save_model_event(self, record: dict) -> dict:
        """模型事件落库(学习/晋升/回滚/漂移)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("qr55", self.TABLE_MODEL_EVENTS,
                        record["modelEventId"]),
                      mapping=self._serialize(record))
            pipe.lpush(_k("qr55", "model_events_all"),
                       record["modelEventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_MODEL_EVENTS][
            record["modelEventId"]] = dict(record)
        self.store.setdefault(
            "_qr55_model_events_all", []).insert(
            0, record["modelEventId"])
        return record

    async def list_model_events(
            self, event_type: str = None,
            limit: int = 100) -> list[dict]:
        """模型事件列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("qr55", "model_events_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for eid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "qr55", self.TABLE_MODEL_EVENTS,
                        int(eid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_MODEL_EVENTS]
                      .values()]
        if event_type:
            result = [e for e in result
                      if (e.get("eventType") or "")
                      == event_type]
        result.sort(key=lambda e: -int(
            e.get("modelEventId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 测试辅助
    # --------------------------------------------------------

    async def reset_all(self) -> None:
        """全量清理(测试+实机幂等)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("qr55", "*"))
            pipe = client.pipeline(transaction=False)
            for k in keys:
                pipe.delete(k)
            await pipe.execute()
            return
        self._ensure_store()
        for table in (self.TABLE_CODES,
                      self.TABLE_EVENTS,
                      self.TABLE_FEEDBACK,
                      self.TABLE_MODEL_EVENTS):
            self.store[table] = {}
        for kind in ("events", "feedback",
                     "model_events"):
            self.store[f"_qr55_{kind}_seq"] = 0
        self.store["_qr55_codes_seq"] = 0
        self.store["_qr55_codes_all"] = []
        self.store["_qr55_events_all"] = []
        self.store["_qr55_feedback_all"] = []
        self.store["_qr55_model_events_all"] = []
        self.store["_qr55_nonce"] = {}
