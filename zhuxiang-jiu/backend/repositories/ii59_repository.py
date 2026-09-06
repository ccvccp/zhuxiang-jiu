"""59号·AI智能服务编排 仓储
(ii59_repository)

计划(docs/59号_AI智能服务编排模块实施计划.md §七):
    7 表(前缀 ii59):
        ii59_sessions       客服会话(状态机+归因链)
        ii59_tasks           任务编排实例(模板版本+步骤留痕)
        ii59_search_logs     检索/推荐日志(query+结果+采纳)
        ii59_feedback        满意度/点击/采纳反馈
        ii59_risk_decisions  风控决策(信号快照+处置+申诉态)
        ii59_thresholds      处置阈值配置域(tier 键——46号审批联动)
        ii59_events          全链事件(route/session/task/
                            search/risk/appeal/learn_signal/
                            scheduler_run)

58号 ii58_repository 范式平移:
    - 通用读写基元(_save/_get/_list——kind
      显式映射+_TABLE_BY_KIND)
    - 五清单显式序列化(_INT/_FLOAT/
      _JSON_DICT/_JSON_LIST/_BOOL——新增字段
      必须同步, 不得跨清单冲突——57号教训)
    - Redis 态 list 表名参数避开记录字段撞名
      (58号 P3 教训——table_kind 位置参数)
"""

import json

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)


class Ii59Repository:
    """59号七表仓储(双模式——asyncio/Redis)"""

    TABLE_SESSIONS = "ii59_sessions"
    TABLE_TASKS = "ii59_tasks"
    TABLE_SEARCH_LOGS = "ii59_search_logs"
    TABLE_FEEDBACK = "ii59_feedback"
    TABLE_RISK_DECISIONS = "ii59_risk_decisions"
    TABLE_THRESHOLDS = "ii59_thresholds"
    TABLE_EVENTS = "ii59_events"

    _ALL_TABLES = (
        TABLE_SESSIONS, TABLE_TASKS,
        TABLE_SEARCH_LOGS, TABLE_FEEDBACK,
        TABLE_RISK_DECISIONS,
        TABLE_THRESHOLDS, TABLE_EVENTS)

    # ============================================================
    # 序列化字段清单(五清单——新增字段必须同步)
    # ============================================================

    _INT_FIELDS = (
        "sessionId", "taskId", "logId",
        "feedbackId", "decisionId", "eventId",
        "memberId", "turnCount", "queuePosition",
        "pooledFeedbackId", "riskScore",
        "appealId", "adopted")
    _FLOAT_FIELDS = (
        "satisfaction", "relevance",
        "diversity", "latencyMs",
        "baseUpper", "baseLower",
        "avgSatisfaction", "trustScore")
    _JSON_DICT_FIELDS = (
        "attribution", "context", "detail",
        "taskStack", "results", "signals",
        "factors", "thresholds", "sla",
        "query", "extra", "config")
    _JSON_LIST_FIELDS = (
        "resultIds", "steps", "candidates",
        "matchedIds", "auditTrail", "history")
    _BOOL_FIELDS = (
        "escalated", "sensitive",
        "humanVerified", "pooled",
        "approved", "appealed")

    # kind → 表名显式映射
    _TABLE_BY_KIND = {
        "session": TABLE_SESSIONS,
        "task": TABLE_TASKS,
        "search_log": TABLE_SEARCH_LOGS,
        "feedback": TABLE_FEEDBACK,
        "risk_decision":
            TABLE_RISK_DECISIONS,
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
            kind, f"ii59_{kind}s")

    async def _next_seq(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("ii59", kind, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_ii59_{kind}_seq", 0) + 1
        self.store[f"_ii59_{kind}_seq"] = seq
        return seq

    # --------------------------------------------------------
    # 通用读写基元(位置参数 table_kind——避开
    # 记录字段撞名 58号 P3 教训)
    # --------------------------------------------------------

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
            pipe.hset(_k("ii59", table, key),
                      mapping=self._serialize(
                          record))
            if create:
                pipe.lpush(
                    _k("ii59", f"{kind}_all"), key)
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[table][key] = dict(record)
        if create:
            self.store.setdefault(
                f"_ii59_{kind}_all", []).insert(
                0, key)
        return record

    async def _get(self, kind: str,
                   key) -> dict | None:
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("ii59", table, key))
            return self._deserialize(data) \
                if data else None
        self._ensure_store()
        rec = self.store[table].get(key)
        return dict(rec) if rec else None

    async def _list(self, table_kind: str,
                    limit: int = 100,
                    **filters) -> list[dict]:
        """列表(最新在前; 可选字段过滤)"""
        table = self._table_of(table_kind)
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("ii59", f"{table_kind}_all"),
                0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for rid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "ii59", table, rid))
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
        # 主键倒序
        result.sort(key=lambda r: -(
            int(r.get(f"{table_kind}Id")
                or 0)))
        return result[:limit]

    # --------------------------------------------------------
    # 会话(sessionId)
    # --------------------------------------------------------

    async def next_session_id(self) -> int:
        return await self._next_seq("session")

    async def save_session(self, record: dict,
                           *, create: bool = True
                           ) -> dict:
        return await self._save(
            "session", record, "sessionId",
            create=create)

    async def get_session(self,
                          session_id: int
                          ) -> dict | None:
        return await self._get(
            "session", int(session_id))

    async def list_sessions(self,
                            member_id: int = None,
                            state: str = None,
                            limit: int = 200
                            ) -> list[dict]:
        return await self._list(
            "session", limit,
            memberId=member_id, state=state)

    # --------------------------------------------------------
    # 任务编排实例(taskId)
    # --------------------------------------------------------

    async def next_task_id(self) -> int:
        return await self._next_seq("task")

    async def save_task(self, record: dict,
                        *, create: bool = True
                        ) -> dict:
        return await self._save(
            "task", record, "taskId",
            create=create)

    async def get_task(self, task_id: int
                       ) -> dict | None:
        return await self._get(
            "task", int(task_id))

    async def list_tasks(self,
                         session_id: int = None,
                         limit: int = 200
                         ) -> list[dict]:
        return await self._list(
            "task", limit,
            sessionId=session_id)

    # --------------------------------------------------------
    # 检索/推荐日志(logId)
    # --------------------------------------------------------

    async def next_log_id(self) -> int:
        return await self._next_seq(
            "search_log")

    async def save_search_log(self,
                              record: dict,
                              *, create: bool = True
                              ) -> dict:
        return await self._save(
            "search_log", record, "logId",
            create=create)

    async def get_search_log(self,
                             log_id: int
                             ) -> dict | None:
        return await self._get(
            "search_log", int(log_id))

    async def list_search_logs(self,
                               member_id: int = None,
                               limit: int = 200
                               ) -> list[dict]:
        return await self._list(
            "search_log", limit,
            memberId=member_id)

    # --------------------------------------------------------
    # 反馈(feedbackId)
    # --------------------------------------------------------

    async def next_feedback_id(self) -> int:
        return await self._next_seq("feedback")

    async def save_feedback(self,
                            record: dict,
                            *, create: bool = True
                            ) -> dict:
        return await self._save(
            "feedback", record, "feedbackId",
            create=create)

    async def get_feedback(self,
                           feedback_id: int
                           ) -> dict | None:
        return await self._get(
            "feedback", int(feedback_id))

    async def list_feedback(self,
                            session_id: int = None,
                            member_id: int = None,
                            kind: str = None,
                            limit: int = 200
                            ) -> list[dict]:
        return await self._list(
            "feedback", limit,
            sessionId=session_id,
            memberId=member_id, kind=kind)

    # --------------------------------------------------------
    # 风控决策(decisionId)
    # --------------------------------------------------------

    async def next_decision_id(self) -> int:
        return await self._next_seq(
            "risk_decision")

    async def save_risk_decision(self,
                                record: dict,
                                *, create: bool = True
                                ) -> dict:
        return await self._save(
            "risk_decision", record,
            "decisionId", create=create)

    async def get_risk_decision(self,
                                decision_id: int
                                ) -> dict | None:
        return await self._get(
            "risk_decision", int(decision_id))

    async def list_risk_decisions(self,
                                  member_id: int = None,
                                  action: str = None,
                                  limit: int = 200
                                  ) -> list[dict]:
        return await self._list(
            "risk_decision", limit,
            memberId=member_id,
            action=action)

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
                _k("ii59", "threshold_all"),
                0, -1)
            result = []
            for k in keys[:limit]:
                data = await client.hgetall(
                    _k("ii59", table, k))
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
                "ii59", self.TABLE_EVENTS,
                record["eventId"]),
                mapping=self._serialize(
                    record))
            pipe.lpush(_k("ii59", "event_all"),
                       record["eventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][
            record["eventId"]] = dict(record)
        self.store.setdefault(
            "_ii59_event_all", []).insert(
            0, record["eventId"])
        return record

    async def list_events(self,
                         event_type: str = None,
                         limit: int = 100
                         ) -> list[dict]:
        return await self._list(
            "event", limit,
            eventType=event_type)
