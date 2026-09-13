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
    TABLE_IMMUNITY = "nexus_immunity"
    TABLE_REDTEAM = "nexus_redteam"
    TABLE_EVOLOG = "nexus_evolution_log"

    _ALL_TABLES = (TABLE_PERSONAS,
                   TABLE_RULES,
                   TABLE_IMMUNITY,
                   TABLE_REDTEAM,
                   TABLE_EVOLOG)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "personaId", "ruleId",
        "runId", "evoLogId",
        "emojiDensity",
    )
    _FLOAT_FIELDS = (
        "confidence",
    )
    _BOOL_FIELDS = (
        "enabled", "harborApplied",
        "allDefended",
    )
    _JSON_DICT_FIELDS = (
        "testCases", "context",
        "detail",
    )
    _JSON_LIST_FIELDS = (
        "patterns", "safeHarbor",
        "vectors", "signals",
        "hits",
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
                list(self.store[table]
                     .values())[:limit]]

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
