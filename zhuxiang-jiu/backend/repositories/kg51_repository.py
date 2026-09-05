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


class Kg51Repository:
    """51号知识图谱仓储(双模式, 46号仓储范式平移)"""

    TABLE_SCHEMA_LOG = "kg51_schema_log"

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_SCHEMA_LOG, {})

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
            if k == "changeId":
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
