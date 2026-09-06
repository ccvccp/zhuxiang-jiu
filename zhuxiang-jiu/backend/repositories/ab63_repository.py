"""63号·AI智能后台管理 仓储
(ab63_repository)

计划(docs/63号_AI智能后台管理模块实施计划.md §五):
    7 表(前缀 ab63)+P4 培训表:
        ab63_grants       权限裁决记录(上下文快照+权限分+reason)
        ab63_workbench    工作台渲染(角色模板+意图关联+呈现配置)
        ab63_guards       编辑态护航事件(检测域+干预档+整改状态)
        ab63_submissions  发布提交(内容快照+Publish_Score+分流级)
        ab63_reviews      审核记录(L1抽检/L2辅助/L3双人——证据链)
        ab63_thresholds   分流/权限配置域(tier 键——46号审批联动)
        ab63_events       全链事件
        ab63_trainings    培训推送(P4——7 日转化窗口状态机)

58/59号仓储范式平移:
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


class Ab63Repository:
    """63号七表仓储(双模式——asyncio/Redis)"""

    TABLE_GRANTS = "ab63_grants"
    TABLE_WORKBENCH = "ab63_workbench"
    TABLE_GUARDS = "ab63_guards"
    TABLE_SUBMISSIONS = "ab63_submissions"
    TABLE_REVIEWS = "ab63_reviews"
    TABLE_THRESHOLDS = "ab63_thresholds"
    TABLE_EVENTS = "ab63_events"
    TABLE_TRAININGS = "ab63_trainings"

    _ALL_TABLES = (
        TABLE_GRANTS, TABLE_WORKBENCH,
        TABLE_GUARDS, TABLE_SUBMISSIONS,
        TABLE_REVIEWS, TABLE_THRESHOLDS,
        TABLE_EVENTS, TABLE_TRAININGS)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "grantId", "wbId", "guardId",
        "subId", "reviewId", "eventId",
        "memberId", "score", "threshold",
        "publishScore", "intentId",
        "pooledFeedbackId", "trainingId",
        "changeId")
    _FLOAT_FIELDS = (
        "complianceRate", "riskScore",
        "avgSatisfaction", "trustScore")
    _JSON_DICT_FIELDS = (
        "attribution", "context", "detail",
        "factors", "reason", "results",
        "signals", "thresholds", "config",
        "renderOptions", "content",
        "evidence", "extra", "grayscale",
        "feedback", "routing")
    _JSON_LIST_FIELDS = (
        "candidates", "steps",
        "reviewers", "auditTrail",
        "history", "findings", "tags")
    _BOOL_FIELDS = (
        "granted", "appealed",
        "humanVerified", "pooled",
        "approved", "escalated",
        "spotCheck")

    _TABLE_BY_KIND = {
        "grant": TABLE_GRANTS,
        "workbench": TABLE_WORKBENCH,
        "guard": TABLE_GUARDS,
        "submission": TABLE_SUBMISSIONS,
        "review": TABLE_REVIEWS,
        "threshold": TABLE_THRESHOLDS,
        "event": TABLE_EVENTS,
        "training": TABLE_TRAININGS,
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
            kind, f"ab63_{kind}s")

    async def _next_seq(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("ab63", kind, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_ab63_{kind}_seq", 0) + 1
        self.store[f"_ab63_{kind}_seq"] = seq
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
            pipe.hset(_k("ab63", table, key),
                      mapping=self._serialize(
                          record))
            if create:
                pipe.lpush(
                    _k("ab63", f"{kind}_all"),
                    key)
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[table][key] = dict(record)
        if create:
            self.store.setdefault(
                f"_ab63_{kind}_all", []).insert(
                0, key)
        return record

    async def _get(self, kind: str,
                   key) -> dict | None:
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("ab63", table, key))
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
                _k("ab63", f"{table_kind}_all"),
                0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for rid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "ab63", table, rid))
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
    # 权限裁决(grantId)
    # --------------------------------------------------------

    async def next_grant_id(self) -> int:
        return await self._next_seq("grant")

    async def save_grant(self, record: dict,
                         *, create: bool = True
                         ) -> dict:
        return await self._save(
            "grant", record, "grantId",
            create=create)

    async def get_grant(self, grant_id: int
                        ) -> dict | None:
        return await self._get(
            "grant", int(grant_id))

    async def list_grants(self,
                          member_id: int = None,
                          role: str = None,
                          limit: int = 200
                          ) -> list[dict]:
        return await self._list(
            "grant", limit,
            memberId=member_id, role=role)

    # --------------------------------------------------------
    # 工作台渲染(wbId)
    # --------------------------------------------------------

    async def next_wb_id(self) -> int:
        return await self._next_seq(
            "workbench")

    async def save_workbench(self,
                             record: dict,
                             *, create: bool = True
                             ) -> dict:
        return await self._save(
            "workbench", record, "wbId",
            create=create)

    async def get_workbench(self, wb_id: int
                            ) -> dict | None:
        return await self._get(
            "workbench", int(wb_id))

    async def list_workbench(self,
                             member_id: int = None,
                             limit: int = 200
                             ) -> list[dict]:
        return await self._list(
            "workbench", limit,
            memberId=member_id)

    # --------------------------------------------------------
    # 编辑态护航(guardId)
    # --------------------------------------------------------

    async def next_guard_id(self) -> int:
        return await self._next_seq("guard")

    async def save_guard(self, record: dict,
                         *, create: bool = True
                         ) -> dict:
        return await self._save(
            "guard", record, "guardId",
            create=create)

    async def get_guard(self, guard_id: int
                        ) -> dict | None:
        return await self._get(
            "guard", int(guard_id))

    async def list_guards(self,
                          member_id: int = None,
                          level: str = None,
                          limit: int = 200
                          ) -> list[dict]:
        return await self._list(
            "guard", limit,
            memberId=member_id,
            level=level)

    # --------------------------------------------------------
    # 发布提交(subId)
    # --------------------------------------------------------

    async def next_sub_id(self) -> int:
        return await self._next_seq(
            "submission")

    async def save_submission(self,
                               record: dict,
                               *, create: bool = True
                               ) -> dict:
        return await self._save(
            "submission", record, "subId",
            create=create)

    async def get_submission(self,
                             sub_id: int
                             ) -> dict | None:
        return await self._get(
            "submission", int(sub_id))

    async def list_submissions(self,
                               member_id: int = None,
                               status: str = None,
                               limit: int = 200
                               ) -> list[dict]:
        return await self._list(
            "submission", limit,
            memberId=member_id,
            status=status)

    # --------------------------------------------------------
    # 审核记录(reviewId)
    # --------------------------------------------------------

    async def next_review_id(self) -> int:
        return await self._next_seq("review")

    async def save_review(self, record: dict,
                           *, create: bool = True
                           ) -> dict:
        return await self._save(
            "review", record, "reviewId",
            create=create)

    async def get_review(self,
                         review_id: int
                         ) -> dict | None:
        return await self._get(
            "review", int(review_id))

    async def list_reviews(self,
                           sub_id: int = None,
                           reviewer: str = None,
                           limit: int = 200
                           ) -> list[dict]:
        return await self._list(
            "review", limit,
            subId=sub_id,
            reviewer=reviewer)

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
                _k("ab63", "threshold_all"),
                0, -1)
            result = []
            for k in keys[:limit]:
                data = await client.hgetall(
                    _k("ab63", table, k))
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
    # 培训推送(trainingId——7 日转化窗口)
    # --------------------------------------------------------

    async def next_training_id(self) -> int:
        return await self._next_seq("training")

    async def save_training(self,
                            record: dict,
                            *, create: bool = True
                            ) -> dict:
        return await self._save(
            "training", record, "trainingId",
            create=create)

    async def get_training(self,
                           training_id: int
                           ) -> dict | None:
        return await self._get(
            "training", int(training_id))

    async def list_trainings(self,
                             member_id: int = None,
                             status: str = None,
                             rule_id: str = None,
                             limit: int = 200
                             ) -> list[dict]:
        return await self._list(
            "training", limit,
            memberId=member_id,
            status=status, ruleId=rule_id)

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
                "ab63", self.TABLE_EVENTS,
                record["eventId"]),
                mapping=self._serialize(
                    record))
            pipe.lpush(
                _k("ab63", "event_all"),
                record["eventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][
            record["eventId"]] = dict(record)
        self.store.setdefault(
            "_ab63_event_all", []).insert(
            0, record["eventId"])
        return record

    async def list_events(self,
                          event_type: str = None,
                          limit: int = 100
                          ) -> list[dict]:
        return await self._list(
            "event", limit,
            eventType=event_type)
