"""58号·AI智能优化意图识别 仓储层
(ii58_repository)

计划(docs/58号_AI智能优化意图识别算法模块实施计划.md §七):
    7 表(前缀 ii58):
        intents      意图注册(动态扩展域)
        corpus       语料库(四类样本+版本化+来源标签)
        evaluations  识别记录(置信度+三态+归因链)
        feedback     双通道反馈(显式+隐式转化)
        labels       主动学习标注队列(pending/approved/rejected)
        thresholds   动态阈值配置域(基线+tier delta)
        events       全链事件

双模式存储(内存/Redis)——显式序列化口径
(56/57号教训: 新增落库字段必须同步五清单
且不得跨清单冲突; 通用表名用 _TABLE_BY_KIND
显式映射防不规则复数断裂)。
"""

import json

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


class Ii58Repository:
    """58号AI智能优化意图识别仓储(双模式)"""

    TABLE_INTENTS = "ii58_intents"
    TABLE_CORPUS = "ii58_corpus"
    TABLE_EVALUATIONS = "ii58_evaluations"
    TABLE_FEEDBACK = "ii58_feedback"
    TABLE_LABELS = "ii58_labels"
    TABLE_THRESHOLDS = "ii58_thresholds"
    TABLE_EVENTS = "ii58_events"

    _ALL_TABLES = (
        TABLE_INTENTS, TABLE_CORPUS,
        TABLE_EVALUATIONS, TABLE_FEEDBACK,
        TABLE_LABELS, TABLE_THRESHOLDS,
        TABLE_EVENTS)

    # ============================================================
    # 序列化字段清单(五清单——新增字段必须同步,
    # 不得跨清单冲突)
    # ============================================================

    _INT_FIELDS = (
        "corpusId", "evalId", "feedbackId",
        "labelId", "eventId", "corpusVersion",
        "corpusHits", "pooledFeedbackId",
        "memberId", "evalCount")
    _FLOAT_FIELDS = (
        "confidence", "weight", "trustScore",
        "poolReward", "avgConfidence",
        "baseUpper", "baseLower",
        "deltaUpper", "deltaLower")
    _JSON_DICT_FIELDS = (
        "attribution", "context", "detail",
        "corpusMatched", "slots", "config",
        "factors", "thresholds", "extra")
    _JSON_LIST_FIELDS = (
        "slotSchema", "confusableWith",
        "matchedCorpusIds", "candidates",
        "results", "samples", "auditTrail")
    _BOOL_FIELDS = (
        "humanVerified", "humanSuggested",
        "boundaryIntercepted", "pooled",
        "approved")

    # kind → 表名显式映射(不规则复数防断裂)
    _TABLE_BY_KIND = {
        "intent": TABLE_INTENTS,
        "corpus": TABLE_CORPUS,
        "evaluation": TABLE_EVALUATIONS,
        "feedback": TABLE_FEEDBACK,
        "label": TABLE_LABELS,
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

    @classmethod
    def _table_of(cls, kind: str) -> str:
        return cls._TABLE_BY_KIND.get(
            kind, f"ii58_{kind}s")

    async def _next_seq(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("ii58", kind, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_ii58_{kind}_seq", 0) + 1
        self.store[f"_ii58_{kind}_seq"] = seq
        return seq

    # --------------------------------------------------------
    # 通用读写基元(kind: 表短名;
    # record 必含 {kind}Id 主键——threshold 用 tier 键)
    # --------------------------------------------------------

    async def _save(self, kind: str, record: dict,
                    key_field: str,
                    *, create: bool = True) -> dict:
        table = self._table_of(kind)
        key = record[key_field]
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("ii58", table, key),
                      mapping=self._serialize(record))
            if create:
                pipe.lpush(
                    _k("ii58", f"{kind}_all"), key)
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[table][key] = dict(record)
        if create:
            self.store.setdefault(
                f"_ii58_{kind}_all", []).insert(
                0, key)
        return record

    async def _get(self, kind: str,
                   key) -> dict | None:
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "ii58", table, key))
            return self._deserialize(data) if data \
                else None
        self._ensure_store()
        rec = self.store[table].get(key)
        return dict(rec) if rec else None

    async def _list(self, kind: str,
                    limit: int = 100,
                    **filters) -> list[dict]:
        """列表(最新在前; 可选字段过滤)"""
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("ii58", f"{kind}_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for rid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "ii58", table, rid))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[table].values()]
        for field, value in filters.items():
            if value is not None:
                result = [r for r in result
                          if r.get(field) == value]
        # 主键倒序(int 字段名取 {kind}Id 或 tier)
        result.sort(key=lambda r: -(
            int(r.get(f"{kind}Id") or 0)
            if isinstance(
                r.get(f"{kind}Id"), int)
            else 0))
        return result[:limit]

    # --------------------------------------------------------
    # 意图(动态扩展域——内置白名单见 registry)
    # --------------------------------------------------------

    async def save_intent(self, record: dict,
                          *, create: bool = True) -> dict:
        return await self._save(
            "intent", record, "intentKey",
            create=create)

    async def get_intent(self, intent_key: str
                         ) -> dict | None:
        return await self._get(
            "intent", intent_key)

    async def list_intents(self,
                           limit: int = 100
                           ) -> list[dict]:
        table = self._table_of("intent")
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.lrange(
                _k("ii58", "intent_all"), 0, -1)
            result = []
            for i in range(0, len(keys), 500):
                pipe = client.pipeline(
                    transaction=False)
                for k in keys[i:i + 500]:
                    pipe.hgetall(_k(
                        "ii58", table, k))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
            return result[:limit]
        self._ensure_store()
        return [dict(r) for r in
                self.store[table].values()][:limit]

    async def next_intent_id(self) -> int:
        return await self._next_seq("intent")

    # --------------------------------------------------------
    # 语料库(corpusId)
    # --------------------------------------------------------

    async def save_corpus(self, record: dict,
                          *, create: bool = True) -> dict:
        return await self._save(
            "corpus", record, "corpusId",
            create=create)

    async def get_corpus(self, corpus_id: int
                         ) -> dict | None:
        return await self._get("corpus", int(corpus_id))

    async def list_corpus(self,
                          intent_id: str = None,
                          sample_type: str = None,
                          status: str = None,
                          limit: int = 200
                          ) -> list[dict]:
        return await self._list(
            "corpus", limit, intentId=intent_id,
            sampleType=sample_type, status=status)

    async def next_corpus_id(self) -> int:
        return await self._next_seq("corpus")

    # --------------------------------------------------------
    # 识别记录(evalId)
    # --------------------------------------------------------

    async def save_evaluation(self, record: dict,
                              *, create: bool = True
                              ) -> dict:
        return await self._save(
            "evaluation", record, "evalId",
            create=create)

    async def get_evaluation(self, eval_id: int
                             ) -> dict | None:
        return await self._get(
            "evaluation", int(eval_id))

    async def list_evaluations(self,
                               intent_id: str = None,
                               state: str = None,
                               member_id: int = None,
                               limit: int = 200
                               ) -> list[dict]:
        return await self._list(
            "evaluation", limit, intentId=intent_id,
            state=state, memberId=member_id)

    async def next_eval_id(self) -> int:
        return await self._next_seq("evaluation")

    # --------------------------------------------------------
    # 反馈(feedbackId)
    # --------------------------------------------------------

    async def save_feedback(self, record: dict,
                            *, create: bool = True
                            ) -> dict:
        return await self._save(
            "feedback", record, "feedbackId",
            create=create)

    async def get_feedback(self, feedback_id: int
                           ) -> dict | None:
        return await self._get(
            "feedback", int(feedback_id))

    async def list_feedback(self,
                            eval_id: int = None,
                            member_id: int = None,
                            kind: str = None,
                            limit: int = 200
                            ) -> list[dict]:
        return await self._list(
            "feedback", limit, evalId=eval_id,
            memberId=member_id, kind=kind)

    async def next_feedback_id(self) -> int:
        return await self._next_seq("feedback")

    # --------------------------------------------------------
    # 标注队列(labelId)
    # --------------------------------------------------------

    async def save_label(self, record: dict,
                         *, create: bool = True) -> dict:
        return await self._save(
            "label", record, "labelId",
            create=create)

    async def get_label(self, label_id: int
                        ) -> dict | None:
        return await self._get("label", int(label_id))

    async def list_labels(self,
                          status: str = None,
                          limit: int = 200
                          ) -> list[dict]:
        return await self._list(
            "label", limit, status=status)

    async def next_label_id(self) -> int:
        return await self._next_seq("label")

    # --------------------------------------------------------
    # 阈值配置域(tier 键)
    # --------------------------------------------------------

    async def save_threshold(self, record: dict,
                             *, create: bool = True
                             ) -> dict:
        return await self._save(
            "threshold", record, "tier",
            create=create)

    async def get_threshold(self, tier: str
                            ) -> dict | None:
        return await self._get("threshold", tier)

    async def list_thresholds(self,
                              limit: int = 10
                              ) -> list[dict]:
        table = self._table_of("threshold")
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.lrange(
                _k("ii58", "threshold_all"), 0, -1)
            result = []
            for k in keys[:limit]:
                data = await client.hgetall(_k(
                    "ii58", table, k))
                if data:
                    result.append(
                        self._deserialize(data))
            return result
        self._ensure_store()
        return [dict(r) for r in
                self.store[table].values()][:limit]

    # --------------------------------------------------------
    # 全链事件(eventId)
    # --------------------------------------------------------

    async def add_event(self, record: dict) -> dict:
        """事件追加"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("ii58", self.TABLE_EVENTS,
                        record["eventId"]),
                      mapping=self._serialize(record))
            pipe.lpush(_k("ii58", "event_all"),
                       record["eventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][
            record["eventId"]] = dict(record)
        self.store.setdefault(
            "_ii58_event_all", []).insert(
            0, record["eventId"])
        return record

    async def list_events(self,
                          eval_id: int = None,
                          event_type: str = None,
                          limit: int = 200
                          ) -> list[dict]:
        """事件列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("ii58", "event_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for eid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "ii58", self.TABLE_EVENTS,
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
        if eval_id is not None:
            result = [e for e in result
                      if int(e.get("evalId") or 0)
                      == int(eval_id)]
        if event_type:
            result = [e for e in result
                      if (e.get("eventType") or "")
                      == event_type]
        result.sort(key=lambda e: -int(
            e.get("eventId") or 0))
        return result[:limit]

    async def next_event_id(self) -> int:
        return await self._next_seq("event")

    # --------------------------------------------------------
    # 测试辅助
    # --------------------------------------------------------

    async def reset_all(self) -> None:
        """全量清理(测试+实机幂等)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("ii58", "*"))
            pipe = client.pipeline(transaction=False)
            for k in keys:
                pipe.delete(k)
            await pipe.execute()
            return
        self._ensure_store()
        for table in self._ALL_TABLES:
            self.store[table] = {}
        for kind in ("intent", "corpus",
                     "evaluation", "feedback",
                     "label", "threshold", "event"):
            self.store[f"_ii58_{kind}_seq"] = 0
            self.store[f"_ii58_{kind}_all"] = []
