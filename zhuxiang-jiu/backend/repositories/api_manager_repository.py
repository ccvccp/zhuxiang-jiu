"""44号·AI智能API管理模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 api44, 设计文档 §三/§四):
    api44_registry   API 资产台账(method|path 为自然键, P0)
    api44_keys       开发者凭证(Key 摘要为键, P1)

台账记录结构:
    {apiId, method, path, module, moduleSource, status, summary,
     missing, lastSeenAt, createdAt, updatedAt}

Key 记录结构(存储侧——apiKey 只存 SHA-256 摘要, 明文仅签发时
返回一次; appCode 明文存储=应用标识可回显):
    {keyId, memberId, name, keyPrefix, appCode, tier,
     status: pending|active|revoked|expired|rejected,
     createdAt, expireAt, lastUsedAt, requestCount}

设计对齐:
    - 双模式存储 + None/bool 序列化口径(38-43号惯例)
    - Key 主键: sha256(apiKey) 摘要——校验 O(1) 单键取
    - memberId 索引: memberkeys 集合(内存 list / Redis SET)
    - keyId 索引: 摘要反查(管理端按 id 操作)
"""

import hashlib
import secrets

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

# 生命周期状态(44号 §三: P5 状态机消费; P0 全量默认 development——
# 显式发布才进 Key 面, 保守口径)
API_STATUS_VALUES = ("development", "published", "deprecated", "offline")

# Key 生命周期(P1: 自动批默认 active; 审批开关 off 时 pending)
KEY_STATUS_VALUES = ("pending", "active", "revoked", "expired",
                    "rejected")


def key_digest(api_key: str) -> str:
    """apiKey → SHA-256 摘要(存储与校验的主键)"""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """生成 apiKey 明文(zk_ 前缀 + 32 hex)"""
    return "zk_" + secrets.token_hex(16)


def generate_app_code() -> str:
    """生成 appCode(ac_ 前缀 + 16 hex, 应用标识)"""
    return "ac_" + secrets.token_hex(8)


class ApiManager44Repository:
    """44号 API 管理仓储(双模式, 43号仓储范式平移)"""

    TABLE_REGISTRY = "api44_registry"
    TABLE_KEYS = "api44_keys"

    _INT_FIELDS = ("apiId", "keyId", "memberId", "requestCount")
    _BOOL_FIELDS = ("missing",)

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建(43号范式)
    # --------------------------------------------------------

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_REGISTRY, {})
        self.store.setdefault(self.TABLE_KEYS, {})

    def _serialize(self, record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            else:
                out[k] = v
        return out

    def _deserialize(self, data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in ApiManager44Repository._INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in ApiManager44Repository._BOOL_FIELDS:
                record[k] = v in (1, "1", True, "True", "true")
            else:
                record[k] = v
        return record

    @staticmethod
    def _entry_key(method: str, path: str) -> str:
        """自然键: method|path"""
        return f"{method}|{path}"

    async def next_api_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("api44", "registry", "seq"))
        self._ensure_store()
        seq_key = "_api44_registry_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def next_key_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("api44", "keys", "seq"))
        self._ensure_store()
        seq_key = "_api44_keys_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    # --------------------------------------------------------
    # 台账 CRUD(自然键 upsert)
    # --------------------------------------------------------

    async def get_entry(self, method: str, path: str) -> dict | None:
        key = self._entry_key(method, path)
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("api44", self.TABLE_REGISTRY, key))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_REGISTRY].get(key)
        return dict(rec) if rec else None

    async def save_entry(self, method: str, path: str,
                         record: dict) -> dict:
        key = self._entry_key(method, path)
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("api44", self.TABLE_REGISTRY, key),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_REGISTRY][key] = dict(record)
        return record

    async def list_entries(self, limit: int = 5000) -> list[dict]:
        """全部台账条目(Redis: keys + pipeline hgetall)"""
        if is_redis_mode():
            import asyncio
            client = await get_redis_client()
            keys = await client.keys(_k(
                "api44", self.TABLE_REGISTRY, "*"))
            keys = [k for k in keys if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        result.append(self._deserialize(data))
                await asyncio.sleep(0)   # 让出事件循环
            return result[:limit]
        self._ensure_store()
        result = [dict(r) for r in
                  self.store[self.TABLE_REGISTRY].values()]
        return result[:limit]

    async def find_by_id(self, api_id: int) -> dict | None:
        """按 apiId 查找(线性扫, 台账规模 ~1k 可接受)"""
        for r in await self.list_entries(limit=10000):
            if r.get("apiId") == api_id:
                return r
        return None

    async def update_entry_fields(
            self, method: str, path: str,
            changes: dict) -> dict | None:
        """部分字段更新(保留其余字段)"""
        rec = await self.get_entry(method, path)
        if rec is None:
            return None
        rec.update(changes)
        rec["updatedAt"] = ts()
        return await self.save_entry(method, path, rec)

    # --------------------------------------------------------
    # 开发者凭证(P1)
    # --------------------------------------------------------

    async def save_key(self, digest: str, record: dict) -> dict:
        """保存 Key 记录(主键=摘要) + memberId 索引维护"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("api44", self.TABLE_KEYS, digest),
                      mapping=self._serialize(record))
            pipe.set(_k("api44", "keyid", record["keyId"]), digest)
            pipe.sadd(_k("api44", "memberkeys",
                         record["memberId"]), digest)
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_KEYS][digest] = dict(record)
        # 索引: keyId → digest / memberId → digests
        self.store.setdefault("_api44_keyid", {})[
            record["keyId"]] = digest
        self.store.setdefault(
            "_api44_memberkeys", {}).setdefault(
            record["memberId"], set()).add(digest)
        return record

    async def get_key(self, digest: str) -> dict | None:
        """按摘要取 Key 记录(校验热路径, 单键 O(1))"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("api44", self.TABLE_KEYS, digest))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_KEYS].get(digest)
        return dict(rec) if rec else None

    async def update_key_fields(self, digest: str,
                                changes: dict) -> dict | None:
        """部分字段更新(吊销/续期/审批/用量)"""
        rec = await self.get_key(digest)
        if rec is None:
            return None
        rec.update(changes)
        return await self.save_key(digest, rec)

    async def digest_by_key_id(self, key_id: int) -> str | None:
        """keyId → 摘要(管理端按 id 操作)"""
        if is_redis_mode():
            client = await get_redis_client()
            d = await client.get(_k("api44", "keyid", key_id))
            return d or None
        self._ensure_store()
        return self.store.get("_api44_keyid", {}).get(key_id)

    async def list_keys_by_member(self, member_id: int) -> list[dict]:
        """会员的 Key 列表(索引驱动, 非全表扫)"""
        if is_redis_mode():
            client = await get_redis_client()
            digests = await client.smembers(
                _k("api44", "memberkeys", member_id))
            if not digests:
                return []
            pipe = client.pipeline(transaction=False)
            for d in digests:
                pipe.hgetall(_k("api44", self.TABLE_KEYS, d))
            result = []
            for data in await pipe.execute():
                if data:
                    result.append(self._deserialize(data))
            result.sort(key=lambda r: -(r.get("keyId") or 0))
            return result
        self._ensure_store()
        digests = self.store.get(
            "_api44_memberkeys", {}).get(member_id, set())
        result = [dict(self.store[self.TABLE_KEYS][d])
                  for d in digests
                  if d in self.store[self.TABLE_KEYS]]
        result.sort(key=lambda r: -(r.get("keyId") or 0))
        return result

    async def list_all_keys(self, limit: int = 5000) -> list[dict]:
        """全量 Key 列表(管理端; Redis: keys + pipeline)"""
        if is_redis_mode():
            import asyncio
            client = await get_redis_client()
            keys = await client.keys(_k(
                "api44", self.TABLE_KEYS, "*"))
            keys = [k for k in keys if not k.endswith(":seq")]
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        result.append(self._deserialize(data))
                await asyncio.sleep(0)
            result.sort(key=lambda r: -(r.get("keyId") or 0))
            return result[:limit]
        self._ensure_store()
        result = [dict(r) for r in
                  self.store[self.TABLE_KEYS].values()]
        result.sort(key=lambda r: -(r.get("keyId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # published 索引(P1: 中间件 Key 面判定的 O(1) 数据源)
    # --------------------------------------------------------

    async def set_published(self, method: str, path: str,
                            published: bool) -> None:
        """维护 published 索引(SADD/SREM; 状态转换时调用)"""
        member = f"{method}|{path}"
        if is_redis_mode():
            client = await get_redis_client()
            if published:
                await client.sadd(
                    _k("api44", "published"), member)
            else:
                await client.srem(
                    _k("api44", "published"), member)
            return
        self._ensure_store()
        bucket = self.store.setdefault("_api44_published", set())
        if published:
            bucket.add(member)
        else:
            bucket.discard(member)

    async def get_published(self) -> set:
        """Key 面索引全集(中间件 60s 缓存刷新用)"""
        if is_redis_mode():
            client = await get_redis_client()
            members = await client.smembers(
                _k("api44", "published"))
            return set(members or ())
        self._ensure_store()
        return set(self.store.get("_api44_published", ()))

    async def get_published_with_status(self) -> dict:
        """Key 面索引全集 + 各条目状态({member: status})

        P5: Key 面 = published + deprecated + offline
        (development 不入面); 状态供中间件弃用预警头/410 判定。
        Redis: SMEMBERS + pipeline HGET status 单字段批量取。
        """
        members = await self.get_published()
        if not members:
            return {}
        result = {}
        if is_redis_mode():
            client = await get_redis_client()
            member_list = [str(m) for m in members]
            pipe = client.pipeline(transaction=False)
            for member in member_list:
                pipe.hget(_k("api44", self.TABLE_REGISTRY,
                             member), "status")
            for member, status in zip(
                    member_list, await pipe.execute()):
                result[member] = str(status or "published")
            return result
        self._ensure_store()
        for member in members:
            rec = self.store[self.TABLE_REGISTRY].get(str(member))
            result[str(member)] = \
                (rec or {}).get("status") or "published"
        return result
