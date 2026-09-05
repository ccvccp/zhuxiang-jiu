"""54号·小竹AI智能登录引擎大模型 仓储层(login54_repository)

计划(docs/54号_小竹AI智能登录引擎大模型实施计划.md §二):
    2 表(前缀 login54, 双模式存储)——
    login54_feedback     决策回流标注(eventId 关联+reward)
    login54_model_events 模型生命周期事件(学习/晋升/
                         回滚/漂移——版本溯源)

复用 44号: ai_learning_profiles(champion/challenger/
历史版本)+ai_learning_feedback(跨模块反馈池)。

设计对齐(51-53号范式平移):
    - 双模式存储+显式序列化口径
    - 列表索引只创建时入列(45号索引教训)
    - reset_all 全量清理(测试+实机幂等)
"""

import json

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


class Login54Repository:
    """54号登录大模型仓储(双模式)"""

    TABLE_FEEDBACK = "login54_feedback"
    TABLE_MODEL_EVENTS = "login54_model_events"

    _INT_FIELDS = ("feedbackId", "eventId", "modelEventId",
                   "memberId", "sampleCount",
                   "poolFeedbackId")
    _FLOAT_FIELDS = ("reward", "trustScore", "baseline")
    _JSON_DICT_FIELDS = ("factors", "context", "metrics")
    _JSON_LIST_FIELDS = ("evidence",)

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        for table in (self.TABLE_FEEDBACK,
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
    # 决策回流标注(feedbackId 只追加)
    # --------------------------------------------------------

    async def next_feedback_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("login54", "feedback", "seq"))
        self._ensure_store()
        seq = self.store.get(
            "_login54_feedback_seq", 0) + 1
        self.store["_login54_feedback_seq"] = seq
        return seq

    async def save_feedback(self, record: dict, *,
                            create: bool = True) -> dict:
        """回流标注落库(create=True 创建入列——只追加;
        create=False 仅更新 pending 转正场景——
        索引不重复入列, 45号索引教训)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("login54", self.TABLE_FEEDBACK,
                        record["feedbackId"]),
                      mapping=self._serialize(record))
            if create:
                pipe.lpush(_k("login54", "feedback_all"),
                           record["feedbackId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_FEEDBACK][
            record["feedbackId"]] = dict(record)
        if create:
            self.store.setdefault(
                "_login54_feedback_all", []).insert(
                0, record["feedbackId"])
        return record

    async def get_feedback_by_event(
            self, event_id: int) -> dict | None:
        """按源事件查回流标注(eventId 1:1——幂等去重键)"""
        records = await self.list_feedback(
            event_id=int(event_id), limit=1)
        return records[0] if records else None

    async def list_feedback(
            self, event_id: int = None,
            status: str = None,
            limit: int = 200) -> list[dict]:
        """回流标注列表(最新在前; 事件/状态过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("login54", "feedback_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for fid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "login54", self.TABLE_FEEDBACK,
                        int(fid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[self.TABLE_FEEDBACK]
                      .values()]
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
    # 模型生命周期事件(modelEventId 只追加)
    # --------------------------------------------------------

    async def next_model_event_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("login54", "model_events", "seq"))
        self._ensure_store()
        seq = self.store.get(
            "_login54_model_events_seq", 0) + 1
        self.store["_login54_model_events_seq"] = seq
        return seq

    async def save_model_event(self, record: dict) -> dict:
        """模型事件落库(学习轮次/晋升/回滚/漂移)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("login54", self.TABLE_MODEL_EVENTS,
                        record["modelEventId"]),
                      mapping=self._serialize(record))
            pipe.lpush(_k("login54", "model_events_all"),
                       record["modelEventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_MODEL_EVENTS][
            record["modelEventId"]] = dict(record)
        self.store.setdefault(
            "_login54_model_events_all", []).insert(
            0, record["modelEventId"])
        return record

    async def list_model_events(
            self, event_type: str = None,
            limit: int = 100) -> list[dict]:
        """模型事件列表(最新在前; 类型过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("login54", "model_events_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for eid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "login54", self.TABLE_MODEL_EVENTS,
                        int(eid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_MODEL_EVENTS].values()]
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
            keys = await client.keys(_k("login54", "*"))
            pipe = client.pipeline(transaction=False)
            for k in keys:
                pipe.delete(k)
            await pipe.execute()
            return
        self._ensure_store()
        for table in (self.TABLE_FEEDBACK,
                      self.TABLE_MODEL_EVENTS):
            self.store[table] = {}
        self.store["_login54_feedback_seq"] = 0
        self.store["_login54_feedback_all"] = []
        self.store["_login54_model_events_seq"] = 0
        self.store["_login54_model_events_all"] = []
