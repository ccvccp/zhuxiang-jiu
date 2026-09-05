"""51号·小竹可信知识图谱仓储(kg51_repository)

表清单(前缀 kg51, 计划 §六——P0 仅审批总线, 余表随期落):
    kg51_schema_log    本体变更审批总线(P0, 只追加语义)
    (P1+: kg51_entities / kg51_triples / kg51_reviews /
          kg51_versions / kg51_inspections / kg51_feedback)

变更审批记录结构(46号 ai46_changes 范式平移):
    {changeId, kind: add_entity|add_relation|patch_attr|retire,
     target, payload(JSON: {before, after}), reason, requestedBy,
     status: pending|approved|rejected,
     reviewedBy, reviewNote,
     requestedAt, reviewedAt}

设计对齐:
    - 双模式存储 + 显式序列化口径(38-50号惯例:
      bool→0/1, dict/list→JSON 字符串, None→"")
    - 变更审批留痕只追加语义(状态翻转仅 update 固定字段;
      Redis LPUSH 仅 new=True 时入列——45号教训)
"""

import json

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

# 变更类型(P0 审批总线覆盖——本体四类演进)
SCHEMA_CHANGE_KINDS = ("add_entity", "add_relation",
                       "patch_attr", "retire")

# 审批状态(46号同款)
SCHEMA_CHANGE_STATUS = ("pending", "approved", "rejected")

# 三元组状态(P1: verified 计分可用/unverified 物理隔离/
# retired 退役留痕)
TRIPLE_STATUS_VALUES = ("verified", "unverified", "retired")

# 数据源三分级(计划 §五 阶段2)
KG51_SOURCE_VALUES = ("authority", "system", "user")

# 复核入队原因(P1)
REVIEW_REASON_VALUES = ("confidence", "conflict", "feedback")


class Kg51Repository:
    """51号知识图谱仓储(双模式, 46号仓储范式平移)"""

    TABLE_SCHEMA_LOG = "kg51_schema_log"
    TABLE_ENTITIES = "kg51_entities"
    TABLE_TRIPLES = "kg51_triples"
    TABLE_REVIEWS = "kg51_reviews"
    TABLE_FP_INDEX = "kg51_fp_index"

    _INT_FIELDS = ("changeId", "reviewId")
    _FLOAT_FIELDS = ("confidence",)

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_SCHEMA_LOG, {})
        self.store.setdefault(self.TABLE_ENTITIES, {})
        self.store.setdefault(self.TABLE_TRIPLES, {})
        self.store.setdefault(self.TABLE_REVIEWS, {})
        self.store.setdefault(self.TABLE_FP_INDEX, {})

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
            elif k in ("payload", "evidence", "attrs"):
                try:
                    record[k] = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            else:
                record[k] = v
        return record

    async def next_change_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("kg51", "schema_log", "seq"))
        self._ensure_store()
        seq = self.store.get("_kg51_changes_seq", 0) + 1
        self.store["_kg51_changes_seq"] = seq
        return seq

    # --------------------------------------------------------
    # 变更审批总线(只追加语义: 创建后仅状态字段可翻转)
    # --------------------------------------------------------

    async def save_change(self, record: dict,
                          new: bool = True) -> dict:
        """保存变更(new=True 创建并入列; new=False 仅更新
        字段不重复入列——Redis LPUSH 幂等防线, 45号教训)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(
                _k("kg51", self.TABLE_SCHEMA_LOG,
                   record["changeId"]),
                mapping=self._serialize(record))
            if new:
                pipe.lpush(_k("kg51", "schema_log_all"),
                           record["changeId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_SCHEMA_LOG][
            record["changeId"]] = dict(record)
        return record

    async def update_change_fields(
            self, change_id: int,
            changes: dict) -> dict | None:
        """部分字段更新(仅审批翻转: status/reviewedBy/
        reviewNote/reviewedAt——不入列)"""
        rec = await self.get_change(change_id)
        if rec is None:
            return None
        rec.update(changes)
        return await self.save_change(rec, new=False)

    async def get_change(self, change_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("kg51", self.TABLE_SCHEMA_LOG, change_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_SCHEMA_LOG].get(change_id)
        return dict(rec) if rec else None

    async def list_changes(
            self, status: str = None,
            target: str = None,
            limit: int = 200) -> list[dict]:
        """审批队列/历史(最新在前; 状态/目标过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("kg51", "schema_log_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for cid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "kg51", self.TABLE_SCHEMA_LOG, cid))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[self.TABLE_SCHEMA_LOG].values()]
        if status:
            result = [r for r in result
                      if r.get("status") == status]
        if target:
            result = [r for r in result
                      if r.get("target") == target]
        result.sort(key=lambda r: -int(
            r.get("changeId") or 0))
        return result[:limit]

    async def reset_seq(self) -> None:
        """测试辅助: 清空审批总线(跨模块种子数据清理教训)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("kg51", self.TABLE_SCHEMA_LOG, "*"))
            pipe = client.pipeline(transaction=False)
            for k in keys:
                pipe.delete(k)
            pipe.delete(_k("kg51", "schema_log_all"))
            pipe.delete(_k("kg51", "schema_log", "seq"))
            await pipe.execute()
            return
        self._ensure_store()
        self.store[self.TABLE_SCHEMA_LOG] = {}
        self.store["_kg51_changes_seq"] = 0

    # --------------------------------------------------------
    # 实体表(P1: entityId 自然键 upsert——首次写入为准)
    # --------------------------------------------------------

    async def get_entity(self, entity_id: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("kg51", self.TABLE_ENTITIES, entity_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_ENTITIES].get(entity_id)
        return dict(rec) if rec else None

    async def save_entity(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("kg51", self.TABLE_ENTITIES,
                   record["entityId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_ENTITIES][
            record["entityId"]] = dict(record)
        return record

    async def list_entities(
            self, entity_type: str = None,
            status: str = None,
            limit: int = 2000) -> list[dict]:
        """实体列表(按 entityId 正序; 类型/状态过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "kg51", self.TABLE_ENTITIES, "*"))
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[self.TABLE_ENTITIES].values()]
        if entity_type:
            result = [r for r in result
                      if r.get("entityType") == entity_type]
        if status:
            result = [r for r in result
                      if r.get("status") == status]
        result.sort(key=lambda r: str(r.get("entityId")))
        return result[:limit]

    # --------------------------------------------------------
    # 三元组表(P1: tripleId uuid 键——指纹独立于主键,
    # 教训继承; 只追加语义, 状态翻转走 update)
    # --------------------------------------------------------

    async def save_triple(self, record: dict,
                          new: bool = True) -> dict:
        """保存三元组(new=True 创建并入列; new=False 仅
        更新字段不重复入列——Redis LPUSH 幂等防线)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(
                _k("kg51", self.TABLE_TRIPLES,
                   record["tripleId"]),
                mapping=self._serialize(record))
            if new:
                pipe.lpush(_k("kg51", "triples_all"),
                           record["tripleId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_TRIPLES][
            record["tripleId"]] = dict(record)
        return record

    async def update_triple_fields(
            self, triple_id: str,
            changes: dict) -> dict | None:
        """部分字段更新(仅 status/evidence/confidence/
        reviewedAt——不入列)"""
        rec = await self.get_triple(triple_id)
        if rec is None:
            return None
        rec.update(changes)
        return await self.save_triple(rec, new=False)

    async def get_triple(self, triple_id: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("kg51", self.TABLE_TRIPLES, triple_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_TRIPLES].get(triple_id)
        return dict(rec) if rec else None

    async def find_triple_by_fp(
            self, fingerprint: str) -> dict | None:
        """指纹查重(sha256(subject|predicate|object)——
        O(1) 索引, 47号 ev_sha 指纹桶范式)"""
        if is_redis_mode():
            client = await get_redis_client()
            triple_id = await client.get(
                _k("kg51", "fp", fingerprint))
            if not triple_id:
                return None
            return await self.get_triple(
                triple_id.decode()
                if isinstance(triple_id, bytes)
                else triple_id)
        self._ensure_store()
        triple_id = self.store[
            self.TABLE_FP_INDEX].get(fingerprint)
        if not triple_id:
            return None
        return await self.get_triple(triple_id)

    async def index_fp(self, fingerprint: str,
                       triple_id: str) -> None:
        """指纹索引写入(fp → tripleId)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("kg51", "fp", fingerprint), triple_id)
            return
        self._ensure_store()
        self.store[self.TABLE_FP_INDEX][
            fingerprint] = triple_id

    async def list_triples(
            self, status: str = None,
            predicate: str = None,
            source_type: str = None,
            subject: str = None,
            limit: int = 2000) -> list[dict]:
        """三元组列表(按创建时间倒序; 多维过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("kg51", "triples_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for tid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "kg51", self.TABLE_TRIPLES, tid))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[self.TABLE_TRIPLES].values()]
        if status:
            result = [r for r in result
                      if r.get("status") == status]
        if predicate:
            result = [r for r in result
                      if r.get("predicate") == predicate]
        if source_type:
            result = [r for r in result
                      if r.get("sourceType")
                      == source_type]
        if subject:
            result = [r for r in result
                      if r.get("subject") == subject]
        return result[:limit]

    async def count_by_status(self) -> dict:
        """按状态计数(unverified 隔离观测口径)"""
        triples = await self.list_triples(limit=100000)
        by: dict = {}
        for t in triples:
            s = t.get("status") or "unverified"
            by[s] = by.get(s, 0) + 1
        return by

    # --------------------------------------------------------
    # 复核队列(P1: 只追加语义, 裁决走状态翻转)
    # --------------------------------------------------------

    async def next_review_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("kg51", "reviews", "seq"))
        self._ensure_store()
        seq = self.store.get("_kg51_reviews_seq", 0) + 1
        self.store["_kg51_reviews_seq"] = seq
        return seq

    async def save_review(self, record: dict,
                          new: bool = True) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(
                _k("kg51", self.TABLE_REVIEWS,
                   record["reviewId"]),
                mapping=self._serialize(record))
            if new:
                pipe.lpush(_k("kg51", "reviews_all"),
                           record["reviewId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_REVIEWS][
            record["reviewId"]] = dict(record)
        return record

    async def update_review_fields(
            self, review_id: int,
            changes: dict) -> dict | None:
        rec = await self.get_review(review_id)
        if rec is None:
            return None
        rec.update(changes)
        return await self.save_review(rec, new=False)

    async def get_review(self, review_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("kg51", self.TABLE_REVIEWS, review_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_REVIEWS].get(review_id)
        return dict(rec) if rec else None

    async def list_reviews(
            self, status: str = None,
            reason: str = None,
            limit: int = 500) -> list[dict]:
        """复核队列/历史(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("kg51", "reviews_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for rid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "kg51", self.TABLE_REVIEWS, rid))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[self.TABLE_REVIEWS].values()]
        if status:
            result = [r for r in result
                      if r.get("status") == status]
        if reason:
            result = [r for r in result
                      if r.get("queueReason") == reason]
        result.sort(key=lambda r: -int(
            r.get("reviewId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 全量清理(测试辅助 + 实机验收前置)
    # --------------------------------------------------------

    async def reset_all(self) -> None:
        """清空全部 kg51 数据面(审批总线/实体/三元组/
        复核/指纹索引——治理留痕一并清理, 实机幂等验证用)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("kg51", "*"))
            pipe = client.pipeline(transaction=False)
            for k in keys:
                pipe.delete(k)
            await pipe.execute()
            return
        self._ensure_store()
        for table in (self.TABLE_SCHEMA_LOG,
                      self.TABLE_ENTITIES,
                      self.TABLE_TRIPLES,
                      self.TABLE_REVIEWS,
                      self.TABLE_FP_INDEX):
            self.store[table] = {}
        self.store["_kg51_changes_seq"] = 0
        self.store["_kg51_reviews_seq"] = 0
