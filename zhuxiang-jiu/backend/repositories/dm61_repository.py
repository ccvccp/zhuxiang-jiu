"""61号·AI智能系统升级决策 仓储
(dm61_repository)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§五):
    7 表(前缀 dm61):
        dm61_requests     决策请求(56号提案/44号信号/人工
                          发起→语义标签+环境感知快照)
        dm61_assessments  风险评估(P1——riskScore+因子
                          快照+影响面+先验引用)
        dm61_simulations  影子沙箱推演(P2——静态校验
                          +指标漂移预估)
        dm61_decisions    决策记录(P1——L1/L2/L3+方案集
                          +人类裁决+归因链+dissent)
        dm61_feedback     RLHF 反馈(P3/P4——采纳/修改
                          /拒绝+修正内容)
        dm61_thresholds   决策阈值配置域(P2——tier 键
                          46号审批联动)
        dm61_events       全链事件(request/assess/
                          simulate/recommend/decide/
                          dissent/appeal/learn_signal/
                          scheduler_run)

58/63号仓储范式平移:
    - 通用读写基元(_save/_get/_list——
      _TABLE_BY_KIND 显式映射)
    - 五清单显式序列化(新增字段必须同步)
    - table_kind 位置参数避开记录字段
      撞名(58号 P3 教训)
"""

import json

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)


class Dm61Repository:
    """61号七表仓储(双模式——asyncio/Redis)"""

    TABLE_REQUESTS = "dm61_requests"
    TABLE_ASSESSMENTS = "dm61_assessments"
    TABLE_SIMULATIONS = "dm61_simulations"
    TABLE_DECISIONS = "dm61_decisions"
    TABLE_FEEDBACK = "dm61_feedback"
    TABLE_THRESHOLDS = "dm61_thresholds"
    TABLE_EVENTS = "dm61_events"

    _ALL_TABLES = (
        TABLE_REQUESTS, TABLE_ASSESSMENTS,
        TABLE_SIMULATIONS, TABLE_DECISIONS,
        TABLE_FEEDBACK, TABLE_THRESHOLDS,
        TABLE_EVENTS)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "requestId", "assessId", "simId",
        "decisionId", "feedbackId",
        "eventId", "proposalId", "changeId",
        "signalId", "memberId",
        "pooledFeedbackId", "caseId")
    _FLOAT_FIELDS = (
        "riskScore", "impactPct", "trustScore",
        "confidence", "errorBudget",
        "historyFailRate", "latencyP95",
        "poolReward")
    _JSON_DICT_FIELDS = (
        "semantic", "environment", "impact",
        "factors", "reason", "attribution",
        "detail", "context", "options",
        "prior", "evidence", "config",
        "thresholds", "simResult", "result",
        "dissent", "correction", "extra",
        "staticGate", "replay",
        "grayscale", "rollback")
    _JSON_LIST_FIELDS = (
        "affectedRoles", "affectedFeatures",
        "trustElements", "auditTrail",
        "signals", "findings", "tags",
        "history")
    _BOOL_FIELDS = (
        "escalated", "dissentFlag",
        "humanOverride", "pooled",
        "windowCaution", "fromProposal")

    _TABLE_BY_KIND = {
        "request": TABLE_REQUESTS,
        "assessment": TABLE_ASSESSMENTS,
        "simulation": TABLE_SIMULATIONS,
        "decision": TABLE_DECISIONS,
        "feedback": TABLE_FEEDBACK,
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
            kind, f"dm61_{kind}s")

    async def _next_seq(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("dm61", kind, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_dm61_{kind}_seq", 0) + 1
        self.store[f"_dm61_{kind}_seq"] = seq
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
            pipe.hset(_k("dm61", table, key),
                      mapping=self._serialize(
                          record))
            if create:
                pipe.lpush(
                    _k("dm61", f"{kind}_all"),
                    key)
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[table][key] = dict(record)
        if create:
            self.store.setdefault(
                f"_dm61_{kind}_all", []).insert(
                0, key)
        return record

    async def _get(self, kind: str,
                   key) -> dict | None:
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("dm61", table, key))
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
                _k("dm61", f"{table_kind}_all"),
                0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for rid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "dm61", table, rid))
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
    # 决策请求(requestId——P0 底座)
    # --------------------------------------------------------

    async def next_request_id(self) -> int:
        return await self._next_seq("request")

    async def save_request(self, record: dict,
                           *, create: bool = True
                           ) -> dict:
        return await self._save(
            "request", record, "requestId",
            create=create)

    async def get_request(self,
                           request_id: int
                           ) -> dict | None:
        return await self._get(
            "request", int(request_id))

    async def list_requests(self,
                            source: str = None,
                            tag: str = None,
                            status: str = None,
                            limit: int = 200
                            ) -> list[dict]:
        return await self._list(
            "request", limit,
            source=source, tag=tag,
            status=status)

    # --------------------------------------------------------
    # 风险评估(assessId——P1)
    # --------------------------------------------------------

    async def next_assess_id(self) -> int:
        return await self._next_seq("assessment")

    async def save_assessment(self,
                              record: dict,
                              *, create: bool = True
                              ) -> dict:
        return await self._save(
            "assessment", record, "assessId",
            create=create)

    async def get_assessment(self,
                            assess_id: int
                            ) -> dict | None:
        return await self._get(
            "assessment", int(assess_id))

    async def list_assessments(self,
                              request_id: int = None,
                              limit: int = 200
                              ) -> list[dict]:
        return await self._list(
            "assessment", limit,
            requestId=request_id)

    # --------------------------------------------------------
    # 影子沙箱(simId——P2)
    # --------------------------------------------------------

    async def next_sim_id(self) -> int:
        return await self._next_seq("simulation")

    async def save_simulation(self,
                              record: dict,
                              *, create: bool = True
                              ) -> dict:
        return await self._save(
            "simulation", record, "simId",
            create=create)

    async def get_simulation(self,
                             sim_id: int
                             ) -> dict | None:
        return await self._get(
            "simulation", int(sim_id))

    async def list_simulations(self,
                               request_id: int = None,
                               limit: int = 200
                               ) -> list[dict]:
        return await self._list(
            "simulation", limit,
            requestId=request_id)

    # --------------------------------------------------------
    # 决策记录(decisionId——P1)
    # --------------------------------------------------------

    async def next_decision_id(self) -> int:
        return await self._next_seq("decision")

    async def save_decision(self,
                            record: dict,
                            *, create: bool = True
                            ) -> dict:
        return await self._save(
            "decision", record, "decisionId",
            create=create)

    async def get_decision(self,
                           decision_id: int
                           ) -> dict | None:
        return await self._get(
            "decision", int(decision_id))

    async def list_decisions(self,
                             request_id: int = None,
                             level: str = None,
                             limit: int = 200
                             ) -> list[dict]:
        return await self._list(
            "decision", limit,
            requestId=request_id,
            level=level)

    # --------------------------------------------------------
    # RLHF 反馈(feedbackId——P3/P4)
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
                            decision_id: int = None,
                            state: str = None,
                            limit: int = 200
                            ) -> list[dict]:
        return await self._list(
            "feedback", limit,
            decisionId=decision_id,
            state=state)

    # --------------------------------------------------------
    # 阈值配置域(tier 键——P2)
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
                _k("dm61", "threshold_all"),
                0, -1)
            result = []
            for k in keys[:limit]:
                data = await client.hgetall(
                    _k("dm61", table, k))
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
                "dm61", self.TABLE_EVENTS,
                record["eventId"]),
                mapping=self._serialize(
                    record))
            pipe.lpush(
                _k("dm61", "event_all"),
                record["eventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][
            record["eventId"]] = dict(record)
        self.store.setdefault(
            "_dm61_event_all", []).insert(
            0, record["eventId"])
        return record

    async def list_events(self,
                          event_type: str = None,
                          limit: int = 100
                          ) -> list[dict]:
        return await self._list(
            "event", limit,
            eventType=event_type)
