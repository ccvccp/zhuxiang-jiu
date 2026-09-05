"""52号·小竹语音可用性评估引擎仓储(us52_repository)

表清单(前缀 us52, 计划 §四):
    us52_sessions          测试会话(P0 建, P1+ 用)
    us52_task_results      任务执行结果(P1 用)
    us52_metric_snapshots  指标快照(P0 建)
    us52_reports           评估报告(P4 用)
    us52_alerts            阈值告警(P5 用)

P0 范围: 快照表读写+会话表框架——
指标计算管道(P1-P4)逐期接入。

设计对齐:
    - 双模式存储 + 显式序列化口径(38-51号惯例:
      bool→0/1, dict/list→JSON 字符串, None→"")
    - 快照只追加(回溯可比)
"""

import json

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


class Us52Repository:
    """52号可用性评估仓储(双模式, 51号范式平移)"""

    TABLE_SESSIONS = "us52_sessions"
    TABLE_RESULTS = "us52_task_results"
    TABLE_SNAPSHOTS = "us52_metric_snapshots"
    TABLE_REPORTS = "us52_reports"
    TABLE_ALERTS = "us52_alerts"

    _INT_FIELDS = ("snapId", "testId", "sessionId",
                   "memberId", "resultId", "reportId",
                   "alertId", "taskCount", "sampleCount")
    _FLOAT_FIELDS = ("value", "baseline")
    _JSON_DICT_FIELDS = ("metrics", "complianceImpact")
    _JSON_LIST_FIELDS = ("vetoFailed", "failedByDimension")

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        for table in (self.TABLE_SESSIONS,
                      self.TABLE_RESULTS,
                      self.TABLE_SNAPSHOTS,
                      self.TABLE_REPORTS,
                      self.TABLE_ALERTS):
            self.store.setdefault(table, {})

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
                    record[k] = float(v) if v != "" \
                        else 0.0
                except (TypeError, ValueError):
                    record[k] = 0.0
            elif k in cls._JSON_DICT_FIELDS:
                try:
                    record[k] = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            elif k in cls._JSON_LIST_FIELDS:
                try:
                    record[k] = json.loads(v) if v else []
                except (TypeError, ValueError):
                    record[k] = []
            else:
                record[k] = v
        return record

    async def next_snap_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("us52", "snapshots", "seq"))
        self._ensure_store()
        seq = self.store.get("_us52_snap_seq", 0) + 1
        self.store["_us52_snap_seq"] = seq
        return seq

    # --------------------------------------------------------
    # 指标快照(只追加)
    # --------------------------------------------------------

    async def save_snapshot(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(
                _k("us52", self.TABLE_SNAPSHOTS,
                   record["snapId"]),
                mapping=self._serialize(record))
            pipe.lpush(_k("us52", "snapshots_all"),
                       record["snapId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_SNAPSHOTS][
            record["snapId"]] = dict(record)
        return record

    async def get_snapshot(self,
                           snap_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("us52", self.TABLE_SNAPSHOTS, snap_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_SNAPSHOTS].get(snap_id)
        return dict(rec) if rec else None

    async def list_snapshots(
            self, limit: int = 50) -> list[dict]:
        """快照列表(最新在前——回溯可比)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("us52", "snapshots_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for sid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "us52", self.TABLE_SNAPSHOTS, sid))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[self.TABLE_SNAPSHOTS]
                      .values()]
        result.sort(key=lambda r: -int(
            r.get("snapId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 评估报告(P4 只追加)
    # --------------------------------------------------------

    async def list_reports(
            self, limit: int = 50) -> list[dict]:
        """评估报告列表(最新在前——P4 留痕回溯;
        reportId 取自 sessions seq, 无独立 seq 键)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "us52", self.TABLE_REPORTS, "*"))
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
                      self.store.get(self.TABLE_REPORTS,
                                     {}).values()]
        result.sort(key=lambda r: -int(
            r.get("reportId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 测试会话(框架——P1 任务管道接入)
    # --------------------------------------------------------

    async def next_test_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("us52", "sessions", "seq"))
        self._ensure_store()
        seq = self.store.get("_us52_test_seq", 0) + 1
        self.store["_us52_test_seq"] = seq
        return seq

    async def save_session(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("us52", self.TABLE_SESSIONS,
                   record["testId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_SESSIONS][
            record["testId"]] = dict(record)
        return record

    async def get_session(self,
                          test_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("us52", self.TABLE_SESSIONS, test_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_SESSIONS].get(test_id)
        return dict(rec) if rec else None

    async def list_sessions(
            self, limit: int = 100) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "us52", self.TABLE_SESSIONS, "*"))
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
                      self.store[self.TABLE_SESSIONS]
                      .values()]
        result.sort(key=lambda r: -int(
            r.get("testId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 测试辅助
    # --------------------------------------------------------

    async def reset_all(self) -> None:
        """全量清理(测试辅助+实机幂等)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("us52", "*"))
            pipe = client.pipeline(transaction=False)
            for k in keys:
                pipe.delete(k)
            await pipe.execute()
            return
        self._ensure_store()
        for table in (self.TABLE_SESSIONS,
                      self.TABLE_RESULTS,
                      self.TABLE_SNAPSHOTS,
                      self.TABLE_REPORTS,
                      self.TABLE_ALERTS):
            self.store[table] = {}
        self.store["_us52_snap_seq"] = 0
        self.store["_us52_test_seq"] = 0
