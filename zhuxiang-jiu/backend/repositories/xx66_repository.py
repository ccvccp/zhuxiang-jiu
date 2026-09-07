"""66号·AI智能工程师大模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 xx66):
    xx66_vitals_snapshots   生命体征快照(P0;
                            5 分钟粒度, 供预测特征消费)

快照记录结构(P0):
    {snapshotId, zoneScores(JSON: 四区红黄绿灯 0/1/2),
     totalScore(int 0-8), overall(healthy|degraded|critical),
     zoneDetails(JSON: 四区明细——各指标/计数),
     metricCount(int 聚合的指标数),
     createdAt}

设计对齐:
    - 双模式存储 + 显式序列化口径(38-47号惯例:
      bool→0/1, dict/list→JSON 字符串, None→"")
    - 快照滚动截断(保留最近 500 条——5 分钟粒度
      ×500 ≈ 42 小时窗, 足够 7 日基线的日滚动重建)
"""

import json

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

# 快照滚动截断(防无限膨胀)
SNAPSHOT_MAX = 500


class Xx66Repository:
    """66号仓储(双模式, 47号仓储范式平移)"""

    TABLE_SNAPSHOTS = "xx66_vitals_snapshots"

    _INT_FIELDS = ("snapshotId", "totalScore", "metricCount")

    def __init__(self):
        self.store = get_in_memory_store()

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_SNAPSHOTS, {})

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
            if k in ("snapshotId", "totalScore",
                     "metricCount"):
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in ("zoneScores", "zoneDetails"):
                try:
                    record[k] = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            else:
                record[k] = v
        return record

    async def next_snapshot_id(self) -> int:
        """快照自增 ID(内存: 表级计数器; Redis: seq 键)"""
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.incr(
                _k("xx66", self.TABLE_SNAPSHOTS, "seq")))
        self._ensure_store()
        counter = self.store.setdefault(
            f"_{self.TABLE_SNAPSHOTS}_seq", 0)
        counter += 1
        self.store[f"_{self.TABLE_SNAPSHOTS}_seq"] = counter
        return counter

    async def save_snapshot(self, record: dict) -> dict:
        """保存快照(滚动截断 SNAPSHOT_MAX 条)"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("xx66", self.TABLE_SNAPSHOTS,
                     record["snapshotId"])
            async with client.pipeline(
                    transaction=False) as pipe:
                pipe.hset(key,
                          mapping=self._serialize(record))
                pipe.zadd(_k("xx66",
                             self.TABLE_SNAPSHOTS, "index"),
                          {str(record["snapshotId"]):
                           record["snapshotId"]})
                pipe.zremrangebyrank(
                    _k("xx66", self.TABLE_SNAPSHOTS,
                       "index"), 0, -SNAPSHOT_MAX - 1)
                await pipe.execute()
            return record
        self._ensure_store()
        table = self.store[self.TABLE_SNAPSHOTS]
        table[record["snapshotId"]] = dict(record)
        # 滚动截断(按 ID 序——ID 单调递增即时间序)
        if len(table) > SNAPSHOT_MAX:
            for old_id in sorted(table)[:-SNAPSHOT_MAX]:
                table.pop(old_id, None)
        return record

    async def list_snapshots(
            self, limit: int = 50) -> list[dict]:
        """快照时序(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.zrevrange(
                _k("xx66", self.TABLE_SNAPSHOTS, "index"),
                0, max(0, limit - 1))
            if not ids:
                return []
            async with client.pipeline(
                    transaction=False) as pipe:
                for sid in ids:
                    pipe.hgetall(_k(
                        "xx66", self.TABLE_SNAPSHOTS,
                        sid))
                rows = await pipe.execute()
            return [self._deserialize(r) for r in rows
                    if r]
        self._ensure_store()
        rows = sorted(
            self.store[self.TABLE_SNAPSHOTS].values(),
            key=lambda r: r.get("snapshotId") or 0,
            reverse=True)
        return [dict(r) for r in rows[:limit]]

    async def latest_snapshot(self) -> dict | None:
        """最近一次快照(无则 None)"""
        rows = await self.list_snapshots(limit=1)
        return rows[0] if rows else None
