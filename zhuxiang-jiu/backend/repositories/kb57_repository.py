"""57号·AI智能知识库 仓储层
(kb57_repository)

计划(docs/57号_AI智能知识库模块实施计划.md §九):
    9 表(前缀 kb57):
        sources    采集源注册表(admin 动态注册域)
        gaps       知识缺口(open→collecting→resolved/ignored)
        resources  原始资源(沙箱隔离态 quarantined/compliant/rejected)
        compliance 合规鉴别记录(三关明细+合规指纹)
        seeds      知识种子(版本化+八态状态机+多模态 content)
        pushes     植入提醒记录(角色×种子×场景×预算)
        paths      学习路径微课程(种子序列+进度)
        feedback   使用反馈(六类回流信号源)
        events     全链事件

双模式存储(内存/Redis)——显式序列化口径
(56号三复发教训: 新增落库字段必须同步五清单)。
"""

import json

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


class Kb57Repository:
    """57号AI智能知识库仓储(双模式)"""

    # ============================================================
    # 序列化字段清单(五清单——新增字段必须同步)
    # ============================================================

    _INT_FIELDS = (
        "sourceId", "gapId", "resourceId",
        "complianceId", "seedId", "pushId",
        "pathId", "feedbackId", "eventId",
        "seedVersion", "askCount",
        "resourceVersion", "viewCount",
        "positiveCount", "negativeCount",
        "pooledFeedbackId", "memberId",
        "llmCalls")
    _FLOAT_FIELDS = (
        "necessityScore", "trustScore", "credibility",
        "privacyCost", "estimatedValue", "actualValue",
        "poolReward", "sourceCredibility",
        "budgetCap", "budgetSpent")
    _JSON_DICT_FIELDS = (
        "signalSnapshot", "scoring", "detail",
        "content", "abTest", "gate", "copyright",
        "privacy", "contentSafety",
        "context", "progress", "factors", "extra")
    _JSON_LIST_FIELDS = (
        "hits", "suggestedSources", "valueTags",
        "seedIds", "complianceReports",
        "maskedFields", "auditTrail",
        "affectedMembers", "results")
    _BOOL_FIELDS = (
        "humanVerified", "active", "completed",
        "reviewRequired", "deferred", "escalated",
        "budgetHalted", "pooled",
        "gapRecurrence")

    TABLE_SOURCES = "kb57_sources"
    TABLE_GAPS = "kb57_gaps"
    TABLE_RESOURCES = "kb57_resources"
    TABLE_COMPLIANCE = "kb57_compliance"
    TABLE_SEEDS = "kb57_seeds"
    TABLE_PUSHES = "kb57_pushes"
    TABLE_PATHS = "kb57_paths"
    TABLE_FEEDBACK = "kb57_feedback"
    TABLE_EVENTS = "kb57_events"

    _ALL_TABLES = (
        TABLE_SOURCES, TABLE_GAPS, TABLE_RESOURCES,
        TABLE_COMPLIANCE, TABLE_SEEDS, TABLE_PUSHES,
        TABLE_PATHS, TABLE_FEEDBACK, TABLE_EVENTS)

    # 知识缺口状态机(计划 §九)
    GAP_STATUSES = (
        "open", "collecting", "resolved", "ignored")

    # 种子八态状态机(计划 §五)
    SEED_STATUSES = (
        "sandbox", "review", "published", "boosted",
        "downgraded", "retired", "rejected", "recalled")

    # 原始资源隔离态状态机(计划 §四——沙箱铁律)
    RESOURCE_STATUSES = (
        "quarantined", "compliant", "rejected")

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

    async def _next_seq(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("kb57", kind, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_kb57_{kind}_seq", 0) + 1
        self.store[f"_kb57_{kind}_seq"] = seq
        return seq

    # --------------------------------------------------------
    # 通用读写基元(kind: 表短名; record 必含 {kind}Id)
    # --------------------------------------------------------

    # kind → 表名显式映射(compliance/feedback 等
    # 不规则复数词——通用 f"{kind}s" 推导会断裂)
    _TABLE_BY_KIND = {
        "source": TABLE_SOURCES,
        "gap": TABLE_GAPS,
        "resource": TABLE_RESOURCES,
        "compliance": TABLE_COMPLIANCE,
        "seed": TABLE_SEEDS,
        "push": TABLE_PUSHES,
        "path": TABLE_PATHS,
        "feedback": TABLE_FEEDBACK,
    }

    @classmethod
    def _table_of(cls, kind: str) -> str:
        return cls._TABLE_BY_KIND.get(
            kind, f"kb57_{kind}s")

    async def _save(self, kind: str, record: dict,
                    *, create: bool = True) -> dict:
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("kb57", table,
                        record[f"{kind}Id"]),
                     mapping=self._serialize(record))
            if create:
                pipe.lpush(
                    _k("kb57", f"{kind}_all"),
                    record[f"{kind}Id"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[table][
            record[f"{kind}Id"]] = dict(record)
        if create:
            self.store.setdefault(
                f"_kb57_{kind}_all", []).insert(
                0, record[f"{kind}Id"])
        return record

    async def _get(self, kind: str,
                   record_id: int) -> dict | None:
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "kb57", table, record_id))
            return self._deserialize(data) if data \
                else None
        self._ensure_store()
        rec = self.store[table].get(record_id)
        return dict(rec) if rec else None

    async def _list(self, kind: str,
                    limit: int = 100,
                    **filters) -> list[dict]:
        """列表(最新在前; 可选字段过滤)"""
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("kb57", f"{kind}_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for rid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "kb57", table, int(rid)))
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
        result.sort(key=lambda r: -int(
            r.get(f"{kind}Id") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 采集源(admin 动态注册域——内置白名单见 registry)
    # --------------------------------------------------------

    async def save_source(self, record: dict,
                          *, create: bool = True) -> dict:
        return await self._save("source", record,
                                create=create)

    async def get_source(self,
                         source_id: int) -> dict | None:
        return await self._get("source", source_id)

    async def list_sources(self,
                           limit: int = 100
                           ) -> list[dict]:
        return await self._list("source", limit)

    async def next_source_id(self) -> int:
        return await self._next_seq("source")

    # --------------------------------------------------------
    # 知识缺口(gapId)
    # --------------------------------------------------------

    async def save_gap(self, record: dict,
                       *, create: bool = True) -> dict:
        return await self._save("gap", record,
                               create=create)

    async def get_gap(self,
                      gap_id: int) -> dict | None:
        return await self._get("gap", gap_id)

    async def list_gaps(self,
                        status: str = None,
                        limit: int = 100
                        ) -> list[dict]:
        return await self._list("gap", limit,
                                status=status)

    async def next_gap_id(self) -> int:
        return await self._next_seq("gap")

    # --------------------------------------------------------
    # 原始资源(resourceId——沙箱隔离态)
    # --------------------------------------------------------

    async def save_resource(self, record: dict,
                            *, create: bool = True) -> dict:
        return await self._save("resource", record,
                                create=create)

    async def get_resource(self, resource_id: int
                           ) -> dict | None:
        return await self._get("resource", resource_id)

    async def list_resources(self,
                             status: str = None,
                             limit: int = 100
                             ) -> list[dict]:
        return await self._list("resource", limit,
                                status=status)

    async def next_resource_id(self) -> int:
        return await self._next_seq("resource")

    # --------------------------------------------------------
    # 合规鉴别记录(complianceId)
    # --------------------------------------------------------

    async def save_compliance(self, record: dict,
                              *, create: bool = True
                              ) -> dict:
        return await self._save("compliance", record,
                               create=create)

    async def get_compliance(self, compliance_id: int
                             ) -> dict | None:
        return await self._get("compliance",
                               compliance_id)

    async def list_compliance(self,
                              resource_id: int = None,
                              limit: int = 100
                              ) -> list[dict]:
        return await self._list(
            "compliance", limit,
            resourceId=resource_id)

    async def next_compliance_id(self) -> int:
        return await self._next_seq("compliance")

    # --------------------------------------------------------
    # 知识种子(seedId——版本化八态)
    # --------------------------------------------------------

    async def save_seed(self, record: dict,
                        *, create: bool = True) -> dict:
        return await self._save("seed", record,
                               create=create)

    async def get_seed(self,
                       seed_id: int) -> dict | None:
        return await self._get("seed", seed_id)

    async def list_seeds(self,
                         status: str = None,
                         seed_type: str = None,
                         limit: int = 100
                         ) -> list[dict]:
        return await self._list(
            "seed", limit, status=status,
            type=seed_type)

    async def next_seed_id(self) -> int:
        return await self._next_seq("seed")

    # --------------------------------------------------------
    # 植入提醒(pushId)
    # --------------------------------------------------------

    async def save_push(self, record: dict,
                        *, create: bool = True) -> dict:
        return await self._save("push", record,
                               create=create)

    async def get_push(self,
                       push_id: int) -> dict | None:
        return await self._get("push", push_id)

    async def list_pushes(self,
                          member_id: int = None,
                          limit: int = 100
                          ) -> list[dict]:
        return await self._list("push", limit,
                                memberId=member_id)

    async def next_push_id(self) -> int:
        return await self._next_seq("push")

    # --------------------------------------------------------
    # 学习路径(pathId)
    # --------------------------------------------------------

    async def save_path(self, record: dict,
                        *, create: bool = True) -> dict:
        return await self._save("path", record,
                               create=create)

    async def get_path(self,
                       path_id: int) -> dict | None:
        return await self._get("path", path_id)

    async def list_paths(self,
                         member_id: int = None,
                         limit: int = 100
                         ) -> list[dict]:
        return await self._list("path", limit,
                                memberId=member_id)

    async def next_path_id(self) -> int:
        return await self._next_seq("path")

    # --------------------------------------------------------
    # 使用反馈(feedbackId)
    # --------------------------------------------------------

    async def save_feedback(self, record: dict,
                            *, create: bool = True
                            ) -> dict:
        return await self._save("feedback", record,
                               create=create)

    async def get_feedback(self, feedback_id: int
                           ) -> dict | None:
        return await self._get("feedback",
                               feedback_id)

    async def list_feedback(self,
                            seed_id: int = None,
                            member_id: int = None,
                            limit: int = 100
                            ) -> list[dict]:
        return await self._list(
            "feedback", limit, seedId=seed_id,
            memberId=member_id)

    async def next_feedback_id(self) -> int:
        return await self._next_seq("feedback")

    # --------------------------------------------------------
    # 全链事件(eventId)
    # --------------------------------------------------------

    async def add_event(self, record: dict) -> dict:
        """事件追加"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("kb57", self.TABLE_EVENTS,
                        record["eventId"]),
                      mapping=self._serialize(record))
            pipe.lpush(_k("kb57", "events_all"),
                       record["eventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][
            record["eventId"]] = dict(record)
        self.store.setdefault(
            "_kb57_events_all", []).insert(
            0, record["eventId"])
        return record

    async def list_events(self,
                          gap_id: int = None,
                          event_type: str = None,
                          limit: int = 200
                          ) -> list[dict]:
        """事件列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("kb57", "events_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for eid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "kb57", self.TABLE_EVENTS,
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
        if gap_id is not None:
            result = [e for e in result
                      if int(e.get("gapId") or 0)
                      == int(gap_id)]
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
            keys = await client.keys(_k("kb57", "*"))
            pipe = client.pipeline(transaction=False)
            for k in keys:
                pipe.delete(k)
            await pipe.execute()
            return
        self._ensure_store()
        for table in self._ALL_TABLES:
            self.store[table] = {}
        for kind in ("source", "gap", "resource",
                     "compliance", "seed", "push",
                     "path", "feedback", "event"):
            self.store[f"_kb57_{kind}_seq"] = 0
            self.store[f"_kb57_{kind}_all"] = []
