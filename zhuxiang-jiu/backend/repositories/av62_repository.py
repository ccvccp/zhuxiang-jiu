"""62号·AI智能无形资产估值 仓储
(av62_repository)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§五):
    7 表(前缀 av62):
        av62_assets       资产登记(主体×角色×要素域
                          +证据快照)
        av62_assessments  评估记录(P1——贡献度+
                          因子快照+置信度+归因链)
        av62_liquidity    流动性档案(P2——评级+
                          衰减状态+激活史)
        av62_appeals      申诉(P3——证据+重估结果+
                          翻转留痕)
        av62_fairness     公平性审计报告(P3)
        av62_thresholds   权重/折算/衰减配置域
                          (tier 键——46号审批联动)
        av62_events       全链事件(register/assess/
                          activate/appeal/
                          learn_signal/scheduler_run)

58/61号仓储范式平移:
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


class Av62Repository:
    """62号七表仓储(双模式——asyncio/Redis)"""

    TABLE_ASSETS = "av62_assets"
    TABLE_ASSESSMENTS = "av62_assessments"
    TABLE_LIQUIDITY = "av62_liquidity"
    TABLE_APPEALS = "av62_appeals"
    TABLE_FAIRNESS = "av62_fairness"
    TABLE_THRESHOLDS = "av62_thresholds"
    TABLE_EVENTS = "av62_events"

    _ALL_TABLES = (
        TABLE_ASSETS, TABLE_ASSESSMENTS,
        TABLE_LIQUIDITY, TABLE_APPEALS,
        TABLE_FAIRNESS, TABLE_THRESHOLDS,
        TABLE_EVENTS)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "assetId", "assessId", "appealId",
        "reportId", "eventId", "memberId",
        "subjectId", "pooledFeedbackId",
        "assessmentId", "appealCount")
    _FLOAT_FIELDS = (
        "contribution", "riskDeduction",
        "netContribution", "confidence",
        "baseValue", "scenarioFactor",
        "decayFactor", "weight",
        "avgBefore", "avgAfter",
        "poolReward")
    _JSON_DICT_FIELDS = (
        "evidence", "factors", "reason",
        "attribution", "detail", "context",
        "config", "thresholds", "result",
        "liquidity", "appeal", "correction",
        "extra", "stats", "snapshot",
        "impact")
    _JSON_LIST_FIELDS = (
        "auditTrail", "domains", "findings",
        "signals", "history", "tags",
        "evidenceFields", "rejectedFields")
    _BOOL_FIELDS = (
        "negative", "grounded",
        "overturned", "pooled",
        "escalated")

    _TABLE_BY_KIND = {
        "asset": TABLE_ASSETS,
        "assessment": TABLE_ASSESSMENTS,
        "liquidity": TABLE_LIQUIDITY,
        "appeal": TABLE_APPEALS,
        "fairness": TABLE_FAIRNESS,
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
            kind, f"av62_{kind}s")

    async def _next_seq(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("av62", kind, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_av62_{kind}_seq", 0) + 1
        self.store[f"_av62_{kind}_seq"] = seq
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
            pipe.hset(_k("av62", table, key),
                      mapping=self._serialize(
                          record))
            if create:
                pipe.lpush(
                    _k("av62", f"{kind}_all"),
                    key)
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[table][key] = dict(record)
        if create:
            self.store.setdefault(
                f"_av62_{kind}_all", []).insert(
                0, key)
        return record

    async def _get(self, kind: str,
                   key) -> dict | None:
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("av62", table, key))
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
                _k("av62", f"{table_kind}_all"),
                0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for rid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "av62", table, rid))
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
    # 资产登记(assetId——P0 底座)
    # --------------------------------------------------------

    async def next_asset_id(self) -> int:
        return await self._next_seq("asset")

    async def save_asset(self, record: dict,
                         *, create: bool = True
                         ) -> dict:
        return await self._save(
            "asset", record, "assetId",
            create=create)

    async def get_asset(self, asset_id: int
                       ) -> dict | None:
        return await self._get(
            "asset", int(asset_id))

    async def list_assets(self,
                          subject_id: int = None,
                          role: str = None,
                          domain: str = None,
                          status: str = None,
                          limit: int = 200
                          ) -> list[dict]:
        return await self._list(
            "asset", limit,
            subjectId=subject_id, role=role,
            domain=domain, status=status)

    # --------------------------------------------------------
    # 评估记录(assessId——P1)
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
                               asset_id: int = None,
                               limit: int = 200
                               ) -> list[dict]:
        return await self._list(
            "assessment", limit,
            assetId=asset_id)

    # --------------------------------------------------------
    # 流动性档案(assetId 键——P2)
    # --------------------------------------------------------

    async def save_liquidity(self,
                             record: dict,
                             *, create: bool = True
                             ) -> dict:
        return await self._save(
            "liquidity", record, "assetId",
            create=create)

    async def get_liquidity(self,
                            asset_id: int
                            ) -> dict | None:
        return await self._get(
            "liquidity", int(asset_id))

    # --------------------------------------------------------
    # 申诉(appealId——P3)
    # --------------------------------------------------------

    async def next_appeal_id(self) -> int:
        return await self._next_seq("appeal")

    async def save_appeal(self,
                          record: dict,
                          *, create: bool = True
                          ) -> dict:
        return await self._save(
            "appeal", record, "appealId",
            create=create)

    async def get_appeal(self,
                         appeal_id: int
                         ) -> dict | None:
        return await self._get(
            "appeal", int(appeal_id))

    async def list_appeals(self,
                          asset_id: int = None,
                          status: str = None,
                          limit: int = 200
                          ) -> list[dict]:
        return await self._list(
            "appeal", limit,
            assetId=asset_id,
            status=status)

    # --------------------------------------------------------
    # 公平性审计报告(reportId——P3)
    # --------------------------------------------------------

    async def next_report_id(self) -> int:
        return await self._next_seq("fairness")

    async def save_fairness(self,
                            record: dict,
                            *, create: bool = True
                            ) -> dict:
        return await self._save(
            "fairness", record, "reportId",
            create=create)

    async def get_fairness(self,
                           report_id: int
                           ) -> dict | None:
        return await self._get(
            "fairness", int(report_id))

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
                _k("av62", "threshold_all"),
                0, -1)
            result = []
            for k in keys[:limit]:
                data = await client.hgetall(
                    _k("av62", table, k))
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
                "av62", self.TABLE_EVENTS,
                record["eventId"]),
                mapping=self._serialize(
                    record))
            pipe.lpush(
                _k("av62", "event_all"),
                record["eventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][
            record["eventId"]] = dict(record)
        self.store.setdefault(
            "_av62_event_all", []).insert(
            0, record["eventId"])
        return record

    async def list_events(self,
                          event_type: str = None,
                          limit: int = 100
                          ) -> list[dict]:
        return await self._list(
            "event", limit,
            eventType=event_type)
