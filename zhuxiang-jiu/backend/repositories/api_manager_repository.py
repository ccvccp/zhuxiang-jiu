"""44号·AI智能API管理模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 api44, 设计文档 §三):
    api44_registry   API 资产台账(method|path 为自然键)

记录结构:
    {apiId, method, path, module, moduleSource, status, summary,
     missing, lastSeenAt, createdAt, updatedAt}

设计对齐:
    - 双模式存储 + None/bool 序列化口径(38-43号惯例)
    - 自然键 method|path: 同步幂等的基础(重扫 upsert 不产生新记录)
    - seq 键 api44:registry:seq 在 Redis 模式下与表键同前缀,
      list 时 endswith(":seq") 过滤(43号惯例)
"""

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

# 生命周期状态(44号 §三: P5 状态机消费; P0 全量默认 development——
# 显式发布才进 Key 面, 保守口径)
API_STATUS_VALUES = ("development", "published", "deprecated", "offline")


class ApiManager44Repository:
    """44号 API 管理仓储(双模式, 43号仓储范式平移)"""

    TABLE_REGISTRY = "api44_registry"

    _INT_FIELDS = ("apiId",)
    _BOOL_FIELDS = ("missing",)

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建(43号范式)
    # --------------------------------------------------------

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_REGISTRY, {})

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
