"""72号·AI智能自动引流大模型 仓储
(attract72_repository, P1)

规划(docs/72号_AI智能自动引流大模型_创新规划方案.md
§七 P1):
    3 表(前缀 attract72):
        attract72_personas           渠道人格画像
                                     (member/influencer
                                     双主体, 动态标签)
        attract72_intent_snapshots   意图快照
                                     (点击增强——
                                     clickId 唯一
                                     upsert)
        attract72_external_signals   外部信号总线
                                     (节日/雷达事件
                                     /平台规则,
                                     ref 幂等去重)

71号仓储范式平移:
    - 通用读写基元(_save/_get/_list)
    - 五清单显式序列化(新增字段必须同步)
    - 72号永不写 40号/traffic/promotion/
      attract v1.0 表(叠加铁律——
      雷达事件/归因数据只读消费)
"""

import contextlib
import json

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)


def _now_ts() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()


class Attract72Repository:
    """72号仓储(双模式——asyncio/Redis)"""

    TABLE_PERSONAS = "attract72_personas"
    TABLE_INTENTS = "attract72_intent_snapshots"
    TABLE_SIGNALS = "attract72_external_signals"
    TABLE_INSIGHTS = "attract72_causal_insights"
    TABLE_LAWS = "attract72_growth_laws"
    TABLE_FORECASTS = "attract72_budget_forecasts"

    _ALL_TABLES = (TABLE_PERSONAS, TABLE_INTENTS,
                   TABLE_SIGNALS, TABLE_INSIGHTS,
                   TABLE_LAWS, TABLE_FORECASTS)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "personaId", "subjectId",
        "snapshotId", "clickId", "signalId",
        "insightId", "lawId",
        "forecastId", "rateChangeId",
        "changeId", "kbEntryId",
        "clickCount", "registeredCount",
        "orderCount", "followerCount",
        "platformCount", "textLen",
        "sampleSize", "baseSampleSize",
        "observedCount", "validatedCount",
        "decayCount", "version",
    )
    _FLOAT_FIELDS = (
        "engagementRate", "conversionRate",
        "orderRate", "avgOrderAmount", "gmv",
        "trustScore", "confidence",
        "emotionBaseline", "dwellSeconds",
        "weight",
        "counterfactualScore", "rateA",
        "rateB", "orderRateA", "orderRateB",
        "poolTotal", "windowBudget",
        "explorationRatio",
        "explorationAmount", "expectedRoi",
        "actualDeviation", "monthlyReserve",
    )
    _BOOL_FIELDS = ("verified", "consumed",
                    "syncedToKB")
    _JSON_DICT_FIELDS = ("stats", "payload",
                         "conditions",
                         "demandSignals",
                         "timeScales")
    _JSON_LIST_FIELDS = (
        "platforms", "intentTags", "sceneTags",
        "hesitationSignals", "impactChannels",
        "history", "evidence",
        "allocations",
    )

    def __init__(self, store: dict = None):
        self.store = (store if store is not None
                     else get_in_memory_store())

    # ============================================================
    # 序列化(71号范式)
    # ============================================================

    def _ensure_store(self):
        for table in self._ALL_TABLES:
            self.store.setdefault(table, {})

    def _serialize(self, record: dict) -> dict:
        """五清单序列化(Redis 兼容)"""
        out = {}
        for k, v in record.items():
            if v is None:
                continue
            if k in self._INT_FIELDS:
                out[k] = int(v)
            elif k in self._FLOAT_FIELDS:
                out[k] = float(v)
            elif k in self._BOOL_FIELDS:
                out[k] = 1 if v else 0
            elif k in self._JSON_DICT_FIELDS:
                out[k] = (json.dumps(v, ensure_ascii=False)
                          if isinstance(v, dict) else v)
            elif k in self._JSON_LIST_FIELDS:
                out[k] = (json.dumps(v, ensure_ascii=False)
                          if isinstance(v, (list, tuple))
                          else v)
            else:
                out[k] = str(v)
        return out

    def _deserialize(self, data: dict) -> dict:
        """五清单反序列化(读回类型还原)"""
        out = dict(data)
        for k in self._INT_FIELDS:
            if k in out:
                with contextlib.suppress(
                        TypeError, ValueError):
                    out[k] = int(float(out[k]))
        for k in self._FLOAT_FIELDS:
            if k in out:
                with contextlib.suppress(
                        TypeError, ValueError):
                    out[k] = float(out[k])
        for k in self._BOOL_FIELDS:
            if k in out:
                out[k] = str(out[k]) == "1"
        for k in (self._JSON_DICT_FIELDS
                  + self._JSON_LIST_FIELDS):
            if k in out and isinstance(out[k], str):
                with contextlib.suppress(
                        ValueError, TypeError):
                    out[k] = json.loads(out[k])
        return out

    # ============================================================
    # 通用基元
    # ============================================================

    async def _save(self, table: str, record_id,
                    record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("attract72",
                   table.rsplit("_", 1)[-1],
                   record_id),
                json.dumps(self._serialize(record),
                           ensure_ascii=False))
        else:
            self._ensure_store()
            self.store[table][record_id] \
                = dict(record)
        return record

    async def _get(self, table: str,
                   record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("attract72",
                   table.rsplit("_", 1)[-1],
                   record_id))
            return (self._deserialize(json.loads(data))
                    if data else None)
        self._ensure_store()
        rec = self.store[table].get(record_id)
        return dict(rec) if rec else None

    async def _list(self, table: str,
                    limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("attract72",
                   table.rsplit("_", 1)[-1],
                   "*"))
            records = []
            for key in keys:
                data = await client.get(key)
                if data:
                    records.append(
                        self._deserialize(
                            json.loads(data)))
            return records[:limit]
        self._ensure_store()
        return [dict(r) for r in
                list(self.store[table].values())
                [:limit]]

    async def next_id(self, entity: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("attract72", entity, "seq"))
        self._ensure_store()
        seq_key = f"_attract72_{entity}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    # ============================================================
    # 渠道人格画像(attract72_personas)
    # ============================================================

    async def save_persona(self, record: dict) -> dict:
        return await self._save(
            self.TABLE_PERSONAS,
            record["personaId"], record)

    async def get_persona(self,
                          persona_id: int) -> dict | None:
        return await self._get(
            self.TABLE_PERSONAS, persona_id)

    async def find_persona_by_subject(
            self, subject_type: str,
            subject_id: int) -> dict | None:
        for p in await self._list(
                self.TABLE_PERSONAS, 2000):
            if p.get("subjectType") == subject_type \
                    and p.get("subjectId") == subject_id:
                return p
        return None

    async def list_personas(
            self, subject_type: str = None,
            persona_type: str = None,
            limit: int = 100) -> list[dict]:
        personas = await self._list(
            self.TABLE_PERSONAS, 2000)
        if subject_type:
            personas = [p for p in personas
                        if p.get("subjectType")
                        == subject_type]
        if persona_type:
            personas = [p for p in personas
                        if p.get("personaType")
                        == persona_type]
        return sorted(personas,
                      key=lambda p: (
                          -p.get("confidence", 0.0),
                          p.get("personaId", 0))
                      )[:limit]

    # ============================================================
    # 意图快照(attract72_intent_snapshots,
    # clickId 唯一 upsert)
    # ============================================================

    async def save_snapshot(self,
                            record: dict) -> dict:
        return await self._save(
            self.TABLE_INTENTS,
            record["clickId"], record)

    async def get_snapshot_by_click(
            self, click_id: int) -> dict | None:
        return await self._get(
            self.TABLE_INTENTS, click_id)

    async def list_snapshots(
            self, limit: int = 200) -> list[dict]:
        snapshots = await self._list(
            self.TABLE_INTENTS, limit)
        return sorted(snapshots,
                      key=lambda s: s.get("at", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 外部信号(attract72_external_signals,
    # ref 幂等去重)
    # ============================================================

    async def save_signal(self,
                          record: dict) -> dict:
        return await self._save(
            self.TABLE_SIGNALS,
            record["signalId"], record)

    async def get_signal(self,
                         signal_id: int) -> dict | None:
        return await self._get(
            self.TABLE_SIGNALS, signal_id)

    async def find_signal_by_ref(
            self, ref: str) -> dict | None:
        for s in await self._list(
                self.TABLE_SIGNALS, 2000):
            if s.get("ref") == ref:
                return s
        return None

    async def list_signals(
            self, signal_type: str = None,
            limit: int = 100) -> list[dict]:
        signals = await self._list(
            self.TABLE_SIGNALS, 2000)
        if signal_type:
            signals = [s for s in signals
                       if s.get("type")
                       == signal_type]
        return sorted(signals,
                      key=lambda s: (
                          -s.get("weight", 0.0),
                          s.get("signalId", 0))
                      )[:limit]

    # ============================================================
    # 因果洞察(attract72_causal_insights,
    # dimension+factor 唯一 upsert)
    # ============================================================

    async def save_insight(self,
                           record: dict) -> dict:
        return await self._save(
            self.TABLE_INSIGHTS,
            record["insightId"], record)

    async def get_insight(self,
                          insight_id: int) -> dict | None:
        return await self._get(
            self.TABLE_INSIGHTS, insight_id)

    async def find_insight_by_factor(
            self, dimension: str,
            factor: str) -> dict | None:
        for i in await self._list(
                self.TABLE_INSIGHTS, 2000):
            if i.get("dimension") == dimension \
                    and i.get("factor") == factor:
                return i
        return None

    async def list_insights(
            self, dimension: str = None,
            effect_type: str = None,
            status: str = None,
            limit: int = 100) -> list[dict]:
        insights = await self._list(
            self.TABLE_INSIGHTS, 2000)
        if dimension:
            insights = [i for i in insights
                        if i.get("dimension")
                        == dimension]
        if effect_type:
            insights = [i for i in insights
                        if i.get("effectType")
                        == effect_type]
        if status:
            insights = [i for i in insights
                        if i.get("status") == status]
        return sorted(insights,
                      key=lambda i: (
                          -abs(i.get(
                              "counterfactualScore",
                              0.0)),
                          i.get("insightId", 0))
                      )[:limit]

    # ============================================================
    # 增长定律(attract72_growth_laws)
    # ============================================================

    async def save_law(self,
                       record: dict) -> dict:
        return await self._save(
            self.TABLE_LAWS,
            record["lawId"], record)

    async def get_law(self,
                      law_id: int) -> dict | None:
        return await self._get(
            self.TABLE_LAWS, law_id)

    async def list_laws(
            self, kind: str = None,
            status: str = None,
            limit: int = 100) -> list[dict]:
        laws = await self._list(
            self.TABLE_LAWS, 2000)
        if kind:
            laws = [l for l in laws
                    if l.get("kind") == kind]
        if status:
            laws = [l for l in laws
                    if l.get("status") == status]
        return sorted(laws,
                      key=lambda l: (
                          -l.get("validatedCount", 0),
                          l.get("lawId", 0))
                      )[:limit]

    async def find_active_law_by_factor(
            self, dimension: str,
            factor: str) -> dict | None:
        for l in await self._list(
                self.TABLE_LAWS, 2000):
            if l.get("dimension") == dimension \
                    and l.get("factor") == factor \
                    and l.get("status") == "active":
                return l
        return None

    # ============================================================
    # 预算预分配(attract72_budget_forecasts,
    # active 唯一由服务层保证)
    # ============================================================

    async def save_forecast(self,
                            record: dict) -> dict:
        return await self._save(
            self.TABLE_FORECASTS,
            record["forecastId"], record)

    async def get_forecast(self,
                           forecast_id: int) -> dict | None:
        return await self._get(
            self.TABLE_FORECASTS, forecast_id)

    async def list_forecasts(
            self, status: str = None,
            limit: int = 50) -> list[dict]:
        forecasts = await self._list(
            self.TABLE_FORECASTS,
            max(limit * 10, 2000))
        if status:
            forecasts = [f for f in forecasts
                         if f.get("status")
                         == status]
        return sorted(forecasts,
                      key=lambda f: -f.get(
                          "forecastId", 0)
                      )[:limit]

    async def find_active_forecast(self) -> dict | None:
        for f in await self._list(
                self.TABLE_FORECASTS, 2000):
            if f.get("status") == "active":
                return f
        return None
