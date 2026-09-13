"""74号·NexusFlow(智枢·流)仓储
(nexus74_repository, P1)

规划(docs/74号_NexusFlow智枢流_AI智能全域
发布大模型_创新规划方案.md §五):
    P1 起用 2 表(前缀 nexus74):
        nexus_personas  平台人格档案
                        (六平台+状态机)
        nexus_rules     合规规则库
                        (对象化 Schema)
    P5 预留 3 表(免疫/红队/进化日志)——
    表名先行封闭, 防漂移

72/73号仓储范式平移:
    - 通用读写基元(_save/_get/_list)
    - 五清单显式序列化(新增字段必须同步)
    - 74号永不写 member/72号表
      (叠加铁律——只读消费)
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


class Nexus74Repository:
    """74号仓储(双模式——asyncio/Redis)"""

    TABLE_PERSONAS = "nexus_personas"
    TABLE_RULES = "nexus_rules"
    TABLE_SOURCES = "nexus_sources"
    TABLE_ADAPTATIONS = "nexus_adaptations"
    TABLE_PUBLICATIONS = "nexus_publications"
    TABLE_SILENCE = "nexus_silence"
    TABLE_METRICS = "nexus_metrics"
    TABLE_AUDITS = "nexus_audits"
    TABLE_LEARNINGS = "nexus_learnings"
    TABLE_RETROSPECTS = "nexus_retrospects"
    TABLE_IMMUNITY = "nexus_immunity"
    TABLE_REDTEAM = "nexus_redteam"
    TABLE_EVOLOG = "nexus_evolution_log"

    _ALL_TABLES = (TABLE_PERSONAS,
                   TABLE_RULES,
                   TABLE_SOURCES,
                   TABLE_ADAPTATIONS,
                   TABLE_PUBLICATIONS,
                   TABLE_SILENCE,
                   TABLE_METRICS,
                   TABLE_AUDITS,
                   TABLE_LEARNINGS,
                   TABLE_RETROSPECTS,
                   TABLE_IMMUNITY,
                   TABLE_REDTEAM,
                   TABLE_EVOLOG)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "personaId", "ruleId",
        "sourceId", "adaptationId",
        "publicationId", "retryCount",
        "backoffSeconds",
        "auditId", "learningId",
        "readCount", "likeCount",
        "commentCount", "shareCount",
        "proposedChangeId",
        "retroId",
        "runId", "evoLogId",
        "emojiDensity",
    )
    _FLOAT_FIELDS = (
        "confidence", "matrixScore",
        "engagementRate", "avgEngagement",
    )
    _BOOL_FIELDS = (
        "enabled", "harborApplied",
        "allDefended", "hasImage",
        "hasVideo", "warningInjected",
        "delivered", "needsReview",
        "shadow", "hasWarning",
        "autoPublished", "silenceEnabled",
    )
    _JSON_DICT_FIELDS = (
        "testCases", "context",
        "detail", "talkingPoints",
        "compliance", "error",
        "receipt", "package",
    )
    _JSON_LIST_FIELDS = (
        "patterns", "safeHarbor",
        "vectors", "signals",
        "hits", "keywords", "tags",
        "hours", "evidence",
        "advices",
    )

    def __init__(self, store: dict = None):
        self.store = (store if store is not None
                      else (get_in_memory_store()
                            if not is_redis_mode()
                            else None))

    # ============================================================
    # 序列化(五清单范式)
    # ============================================================

    def _ensure_store(self):
        for table in self._ALL_TABLES:
            self.store.setdefault(table, {})

    def _serialize(self, record: dict) -> dict:
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
                out[k] = (json.dumps(
                              v, ensure_ascii=False)
                          if isinstance(v, dict)
                          else v)
            elif k in self._JSON_LIST_FIELDS:
                out[k] = (json.dumps(
                              v, ensure_ascii=False)
                          if isinstance(
                              v, (list, tuple))
                          else v)
            else:
                out[k] = str(v)
        return out

    def _deserialize(self, data: dict) -> dict:
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
    # 通用基元(72/73号范式)
    # ============================================================

    async def _save(self, table: str, record_id,
                    record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("nexus74",
                   table.replace("nexus_", ""),
                   record_id),
                json.dumps(self._serialize(record),
                           ensure_ascii=False))
        else:
            self._ensure_store()
            self.store[table][record_id] = \
                dict(record)
        return record

    async def _get(self, table: str,
                    record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("nexus74",
                   table.replace("nexus_", ""),
                   record_id))
            return (self._deserialize(
                        json.loads(data))
                    if data else None)
        self._ensure_store()
        rec = self.store[table].get(record_id)
        return dict(rec) if rec else None

    async def _list(self, table: str,
                    limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("nexus74",
                   table.replace("nexus_", ""),
                   "*"))
            # 仅数字尾键为记录(71号 isdigit
            # 范式)——排除 seq 发号器
            # (redteam 表名单复数同形:
            #  nexus74:redteam:seq 命中
            #  redteam:* 通配符→dict(int)
            #  TypeError; immunity/silence
            #  的 default 键走 _get 不受影响)
            id_keys = sorted(
                (str(k) for k in keys
                 if str(k).rsplit(":", 1)[-1]
                 .isdigit()),
                key=lambda k: int(
                    k.rsplit(":", 1)[-1]))
            records = []
            for key in id_keys:
                data = await client.get(key)
                if data:
                    parsed = json.loads(data)
                    if isinstance(parsed, dict):
                        records.append(
                            self._deserialize(
                                parsed))
                if len(records) >= limit:
                    break
            return records[:limit]
        self._ensure_store()
        return [dict(r) for r in
                list(self.store[table].values())
                [:limit]]

    async def _delete(self, table: str,
                      record_id) -> bool:
        """硬删除(规则库管理)"""
        if is_redis_mode():
            client = await get_redis_client()
            return bool(await client.delete(
                _k("nexus74",
                   table.replace("nexus_", ""),
                   record_id)))
        self._ensure_store()
        return bool(
            self.store[table].pop(
                record_id, None))

    async def next_id(self, entity: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("nexus74", entity, "seq"))
        self._ensure_store()
        seq_key = f"_nexus74_{entity}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    # ============================================================
    # P1 人格档案(记忆与人设层)
    # ============================================================

    async def save_persona(self,
                           record: dict) -> dict:
        return await self._save(
            self.TABLE_PERSONAS,
            record["personaId"], record)

    async def get_persona(
            self, persona_id) -> dict | None:
        return await self._get(
            self.TABLE_PERSONAS, persona_id)

    async def list_personas(
            self, limit: int = 50
    ) -> list[dict]:
        return await self._list(
            self.TABLE_PERSONAS, limit=limit)

    # ============================================================
    # P1 合规规则库
    # ============================================================

    async def save_rule(self,
                        record: dict) -> dict:
        return await self._save(
            self.TABLE_RULES,
            record["ruleId"], record)

    async def get_rule(
            self, rule_id) -> dict | None:
        return await self._get(
            self.TABLE_RULES, rule_id)

    async def list_rules(
            self, limit: int = 200
    ) -> list[dict]:
        return await self._list(
            self.TABLE_RULES, limit=limit)

    async def delete_rule(self,
                          rule_id) -> bool:
        return await self._delete(
            self.TABLE_RULES, rule_id)

    # ============================================================
    # P2 源内容登记
    # ============================================================

    async def save_source(self,
                          record: dict) -> dict:
        return await self._save(
            self.TABLE_SOURCES,
            record["sourceId"], record)

    async def get_source(
            self, source_id) -> dict | None:
        return await self._get(
            self.TABLE_SOURCES, source_id)

    async def list_sources(
            self, limit: int = 100
    ) -> list[dict]:
        return await self._list(
            self.TABLE_SOURCES, limit=limit)

    # ============================================================
    # P2 适配版本
    # ============================================================

    async def save_adaptation(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_ADAPTATIONS,
            record["adaptationId"], record)

    async def get_adaptation(
            self, adaptation_id) -> dict | None:
        return await self._get(
            self.TABLE_ADAPTATIONS,
            adaptation_id)

    async def list_adaptations(
            self, source_id=None,
            limit: int = 200
    ) -> list[dict]:
        records = await self._list(
            self.TABLE_ADAPTATIONS,
            limit=limit)
        if source_id is not None:
            records = [r for r in records
                       if r.get("sourceId")
                       == source_id]
        return records

    # ============================================================
    # P3 发布记录+静默窗
    # ============================================================

    async def save_publication(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_PUBLICATIONS,
            record["publicationId"],
            record)

    async def get_publication(
            self, publication_id) -> dict | None:
        return await self._get(
            self.TABLE_PUBLICATIONS,
            publication_id)

    async def list_publications(
            self, platform: str = None,
            status: str = None,
            limit: int = 200
    ) -> list[dict]:
        records = await self._list(
            self.TABLE_PUBLICATIONS,
            limit=limit)
        if platform:
            records = [r for r in records
                       if r.get("platform")
                       == platform]
        if status:
            records = [r for r in records
                       if r.get("status")
                       == status]
        return records

    async def save_silence(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_SILENCE,
            record.get("id", "default"),
            record)

    async def get_silence(
            self) -> dict | None:
        return await self._get(
            self.TABLE_SILENCE, "default")

    # ============================================================
    # P4 数据回流(指标/审核/学习)
    # ============================================================

    async def save_metrics(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_METRICS,
            record["publicationId"],
            record)

    async def get_metrics(
            self, publication_id) -> dict | None:
        return await self._get(
            self.TABLE_METRICS,
            publication_id)

    async def list_metrics(
            self, limit: int = 500
    ) -> list[dict]:
        return await self._list(
            self.TABLE_METRICS, limit=limit)

    async def save_audit(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_AUDITS,
            record["auditId"], record)

    async def get_audit(
            self, audit_id) -> dict | None:
        return await self._get(
            self.TABLE_AUDITS, audit_id)

    async def list_audits(
            self, platform: str = None,
            limit: int = 500
    ) -> list[dict]:
        records = await self._list(
            self.TABLE_AUDITS, limit=limit)
        if platform:
            records = [r for r in records
                       if r.get("platform")
                       == platform]
        return records

    async def save_learning(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_LEARNINGS,
            record["learningId"], record)

    async def list_learnings(
            self, kind: str = None,
            limit: int = 200
    ) -> list[dict]:
        records = await self._list(
            self.TABLE_LEARNINGS,
            limit=limit)
        if kind:
            records = [r for r in records
                       if r.get("kind")
                       == kind]
        return records

    # ============================================================
    # P6 发布后复盘
    # ============================================================

    async def save_retro(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_RETROSPECTS,
            record["retroId"], record)

    async def get_retro(
            self, retro_id) -> dict | None:
        return await self._get(
            self.TABLE_RETROSPECTS,
            retro_id)

    async def list_retrospects(
            self, scope: str = None,
            limit: int = 200
    ) -> list[dict]:
        records = await self._list(
            self.TABLE_RETROSPECTS,
            limit=limit)
        if scope:
            records = [r for r in records
                       if r.get("scope")
                       == scope]
        return records

    # ============================================================
    # P5 元认知(免疫/红队/进化日志)
    # ============================================================

    async def save_immunity(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_IMMUNITY,
            "default", record)

    async def get_immunity(
            self) -> dict | None:
        return await self._get(
            self.TABLE_IMMUNITY,
            "default")

    async def save_redteam(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_REDTEAM,
            record["runId"], record)

    async def list_redteams(
            self, limit: int = 50
    ) -> list[dict]:
        return await self._list(
            self.TABLE_REDTEAM,
            limit=limit)

    async def save_evolog(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_EVOLOG,
            record["evoLogId"], record)

    async def list_evologs(
            self, kind: str = None,
            limit: int = 200
    ) -> list[dict]:
        records = await self._list(
            self.TABLE_EVOLOG,
            limit=limit)
        if kind:
            records = [r for r in records
                       if r.get("kind")
                       == kind]
        return records
