"""46号·AI 治理与合规中枢数据访问层(双模式: 内存 + Redis)

表清单(前缀 ai46, 计划 §三):
    ai46_registry   AI 资产注册中心(scorerId 自然键, P0)
    ai46_changes    变更审批总线(P0)

注册中心记录结构:
    {govId, scorerId, label, module, batch,
     status: active|frozen|retired,
     ownerNote, frozenAt, frozenBy,
     firstSeenAt, lastSyncedAt, createdAt}

变更审批记录结构:
    {changeId, govId, scorerId,
     kind: promote|patch|config|freeze|unfreeze,
     payload(JSON: {before, after}), reason, requestedBy,
     status: pending|approved|rejected,
     reviewedBy, reviewNote, error,
     requestedAt, reviewedAt}

设计对齐:
    - 双模式存储 + 显式序列化口径(38-45号惯例:
      bool→0/1, dict/list→JSON 字符串, None→"")
    - 变更审批留痕只追加语义(状态翻转仅 update 固定字段)
"""

import json

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

# 治理状态(46号 §三: active 可学习/frozen 审批冻结/retired 档案退役)
GOV_STATUS_VALUES = ("active", "frozen", "retired")

# 变更类型(P0 审批总线覆盖)
CHANGE_KIND_VALUES = ("promote", "patch", "config",
                      "freeze", "unfreeze")

# 审批状态
CHANGE_STATUS_VALUES = ("pending", "approved", "rejected")


class AiGovernance46Repository:
    """46号 AI 治理仓储(双模式, 45号仓储范式平移)"""

    TABLE_REGISTRY = "ai46_registry"
    TABLE_CHANGES = "ai46_changes"

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_REGISTRY, {})
        self.store.setdefault(self.TABLE_CHANGES, {})

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

    @staticmethod
    def _deserialize(data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in ("govId", "changeId"):
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k == "payload":
                try:
                    record[k] = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            else:
                record[k] = v
        return record

    async def next_gov_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("ai46", "registry", "seq"))
        self._ensure_store()
        seq = self.store.get("_ai46_gov_seq", 0) + 1
        self.store["_ai46_gov_seq"] = seq
        return seq

    async def next_change_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("ai46", "changes", "seq"))
        self._ensure_store()
        seq = self.store.get("_ai46_changes_seq", 0) + 1
        self.store["_ai46_changes_seq"] = seq
        return seq

    # --------------------------------------------------------
    # 注册中心(scorerId 自然键 upsert)
    # --------------------------------------------------------

    async def get_gov(self, scorer_id: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("ai46", self.TABLE_REGISTRY, scorer_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_REGISTRY].get(scorer_id)
        return dict(rec) if rec else None

    async def save_gov(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("ai46", self.TABLE_REGISTRY,
                   record["scorerId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_REGISTRY][
            record["scorerId"]] = dict(record)
        return record

    async def list_govs(self,
                        limit: int = 200) -> list[dict]:
        """全量治理台账(按 batch/scorerId 排序)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "ai46", self.TABLE_REGISTRY, "*"))
            keys = [k for k in keys if not k.endswith(":seq")]
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
                      self.store[self.TABLE_REGISTRY].values()]
        result.sort(key=lambda r: (
            -(int(r.get("batch") or 0)),
            str(r.get("scorerId") or "")))
        return result[:limit]

    # --------------------------------------------------------
    # 变更审批总线(只追加语义: 创建后仅状态字段可翻转)
    # --------------------------------------------------------

    async def save_change(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(
                _k("ai46", self.TABLE_CHANGES,
                   record["changeId"]),
                mapping=self._serialize(record))
            pipe.lpush(_k("ai46", "changes_all"),
                       record["changeId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_CHANGES][
            record["changeId"]] = dict(record)
        return record

    async def update_change_fields(
            self, change_id: int,
            changes: dict) -> dict | None:
        """部分字段更新(仅审批翻转: status/reviewedBy/
        reviewNote/error/reviewedAt)"""
        rec = await self.get_change(change_id)
        if rec is None:
            return None
        rec.update(changes)
        return await self.save_change(rec)

    async def get_change(self, change_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("ai46", self.TABLE_CHANGES, change_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_CHANGES].get(change_id)
        return dict(rec) if rec else None

    async def list_changes(
            self, status: str = None,
            scorer_id: str = None,
            limit: int = 200) -> list[dict]:
        """审批队列/历史(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("ai46", "changes_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for cid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "ai46", self.TABLE_CHANGES, int(cid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[self.TABLE_CHANGES].values()]
        if status:
            result = [c for c in result
                      if c.get("status") == status]
        if scorer_id:
            result = [c for c in result
                      if c.get("scorerId") == scorer_id]
        result.sort(key=lambda c: -(
            int(c.get("changeId") or 0)))
        return result[:limit]
