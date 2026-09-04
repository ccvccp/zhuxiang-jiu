"""45号·信值模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 trust45, 设计文档 §三):
    trust45_profiles   角色档案(个人/企业, P0)
    trust45_events     行为事件流水(P0 灌入; P1 后由雷达/存证通道接管)

档案记录结构:
    {trustId, role: person|org, name, idDigest(证件 SHA-256 摘要——
     明文不落盘), factors: {九因子快照}, l1Severity: {严重度计数},
     score, rawScore, grade, fused, fusedLevel, frozen,
     createdAt, updatedAt}

事件记录结构:
    {eventId, trustId, layer: L1|L2|L3, factor, delta, severity,
     source: radar|probe|deposit|manual, summary, ts,
     sources(47号P2: 存证数据源数组, 含互证引用 "trust:{id}")}

设计对齐:
    - 双模式存储 + None/bool 序列化口径(38-44号惯例)
    - 证件摘要唯一映射(idDigest → trustId, 防重复建档)
    - 事件为审计流水, 因子快照存档案(事件不重放——增量更新)
"""

import hashlib

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

# 角色类型(45号 §三: 双角色差异化)
ROLE_VALUES = ("person", "org")

# 三层(宪法结构: L1 法治 50% / L2 伦理 30% / L3 贡献 20%)
LAYER_VALUES = ("L1", "L2", "L3")

# L1 违规严重度(P2 修复引擎的 α 系数数据源)
SEVERITY_VALUES = ("general", "severe", "criminal")


def id_digest(id_number: str) -> str:
    """证件号 → SHA-256 摘要(建档查重主键, 明文永不落盘)"""
    return hashlib.sha256(
        (id_number or "").encode("utf-8")).hexdigest()


class TrustValue45Repository:
    """45号信值仓储(双模式, 44号仓储范式平移)"""

    TABLE_PROFILES = "trust45_profiles"
    TABLE_EVENTS = "trust45_events"

    _INT_FIELDS = ("trustId", "eventId")
    _BOOL_FIELDS = ("fused", "frozen")
    _FLOAT_FIELDS = ("score", "rawScore", "delta",
                     "scoreBefore", "scoreAfter")

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建(43/44号范式)
    # --------------------------------------------------------

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_PROFILES, {})
        self.store.setdefault(self.TABLE_EVENTS, {})

    def _serialize(self, record: dict) -> dict:
        """内存/Redis 统一口径: None→"" / bool→0|1 / 嵌套 dict/list 转 JSON 字符串"""
        import json
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

    def _deserialize(self, data: dict) -> dict:
        import json
        record = {}
        for k, v in data.items():
            if k in TrustValue45Repository._INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in TrustValue45Repository._BOOL_FIELDS:
                record[k] = v in (1, "1", True, "True", "true")
            elif k in TrustValue45Repository._FLOAT_FIELDS:
                try:
                    record[k] = float(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in ("factors", "l1Severity"):
                try:
                    record[k] = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            elif k == "sources":
                # 47号P2: 存证数据源数组(含互证引用)
                try:
                    record[k] = json.loads(v) if v else []
                except (TypeError, ValueError):
                    record[k] = []
            else:
                record[k] = v
        return record

    async def next_trust_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("trust45", "profiles", "seq"))
        self._ensure_store()
        seq = self.store.get("_trust45_profiles_seq", 0) + 1
        self.store["_trust45_profiles_seq"] = seq
        return seq

    async def next_event_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("trust45", "events", "seq"))
        self._ensure_store()
        seq = self.store.get("_trust45_events_seq", 0) + 1
        self.store["_trust45_events_seq"] = seq
        return seq

    # --------------------------------------------------------
    # 角色档案
    # --------------------------------------------------------

    async def get_profile(self, trust_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("trust45", self.TABLE_PROFILES, trust_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_PROFILES].get(trust_id)
        return dict(rec) if rec else None

    async def save_profile(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("trust45", self.TABLE_PROFILES,
                   record["trustId"]),
                mapping=self._serialize(record))
            # 证件摘要唯一映射(防重复建档)
            await client.set(
                _k("trust45", "idmap", record["idDigest"]),
                record["trustId"])
            return record
        self._ensure_store()
        self.store[self.TABLE_PROFILES][record["trustId"]] = \
            dict(record)
        self.store.setdefault("_trust45_idmap", {})[
            record["idDigest"]] = record["trustId"]
        return record

    async def find_by_digest(self, digest: str) -> dict | None:
        """按证件摘要查档(重复建档检测)"""
        if is_redis_mode():
            client = await get_redis_client()
            trust_id = await client.get(
                _k("trust45", "idmap", digest))
            if not trust_id:
                return None
            return await self.get_profile(int(trust_id))
        self._ensure_store()
        trust_id = self.store.get(
            "_trust45_idmap", {}).get(digest)
        if trust_id is None:
            return None
        return await self.get_profile(trust_id)

    async def list_profiles(self, limit: int = 5000) -> list[dict]:
        """全量档案(管理端列表)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "trust45", self.TABLE_PROFILES, "*"))
            keys = [k for k in keys if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        result.append(self._deserialize(data))
            result.sort(key=lambda r: -(r.get("trustId") or 0))
            return result[:limit]
        self._ensure_store()
        result = [dict(r) for r in
                  self.store[self.TABLE_PROFILES].values()]
        result.sort(key=lambda r: -(r.get("trustId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 行为事件(审计流水)
    # --------------------------------------------------------

    async def save_event(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("trust45", self.TABLE_EVENTS,
                   record["eventId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][record["eventId"]] = \
            dict(record)
        return record

    async def list_events_by_trust(
            self, trust_id: int) -> list[dict]:
        """角色的全部事件(按时间正序)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "trust45", self.TABLE_EVENTS, "*"))
            keys = [k for k in keys if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        ev = self._deserialize(data)
                        if ev.get("trustId") == trust_id:
                            result.append(ev)
            result.sort(key=lambda e: (e.get("ts") or ""))
            return result
        self._ensure_store()
        result = [dict(e) for e in
                  self.store[self.TABLE_EVENTS].values()
                  if e.get("trustId") == trust_id]
        result.sort(key=lambda e: (e.get("ts") or ""))
        return result

    async def list_deposit_events(
            self, days: int = 90) -> list[dict]:
        """近 N 日全部已入库存证事件(跨角色——47号P2
        互证对分析用; 口径 source=deposit, 按时间正序)"""
        from datetime import UTC, datetime, timedelta
        cutoff = (datetime.now(UTC)
                  - timedelta(days=days)).isoformat()
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "trust45", self.TABLE_EVENTS, "*"))
            keys = [k for k in keys if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        ev = self._deserialize(data)
                        if ev.get("source") == "deposit" \
                                and (ev.get("ts") or "") >= cutoff:
                            result.append(ev)
            result.sort(key=lambda e: (e.get("ts") or ""))
            return result
        self._ensure_store()
        result = [dict(e) for e in
                  self.store[self.TABLE_EVENTS].values()
                  if e.get("source") == "deposit"
                  and (e.get("ts") or "") >= cutoff]
        result.sort(key=lambda e: (e.get("ts") or ""))
        return result
