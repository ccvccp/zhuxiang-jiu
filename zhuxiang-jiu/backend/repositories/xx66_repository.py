"""66号·AI智能工程师大模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 xx66):
    xx66_vitals_snapshots   生命体征快照(P0;
                            5 分钟粒度, 供预测特征消费)
    xx66_emotions           情绪匿名统计(P1——
                            烈度分档+模式选择+满意度关联;
                            红线: 情绪原文不落库)
    xx66_badges             虚拟勋章记录(P1——
                            服务终态满意度≥4 授予, 纯展示)
    xx66_predictions        故障预测预警(P2——
                            特征全量留痕可复现)
    xx66_engineer_log       操作留痕哈希指纹链(P2——
                            prev_hash 串联防篡改可审计)

快照记录结构(P0):
    {snapshotId, zoneScores(JSON: 四区红黄绿灯 0/1/2),
     totalScore(int 0-8), overall(healthy|degraded|critical),
     zoneDetails(JSON: 四区明细——各指标/计数),
     metricCount(int 聚合的指标数),
     createdAt}

情绪统计记录结构(P1, 匿名——无个人标识):
    {statId, band(calm|confused|frustrated|angry),
     modeChosen(soothe|teach|efficient),
     scenario(payment|trust|exchange|order|profile|general),
     satisfactionLinked(int 满意度 1-5, 终态回填),
     createdAt}

勋章记录结构(P1):
    {badgeId, memberId, kind, reason, grantedAt}

设计对齐:
    - 双模式存储 + 显式序列化口径(38-47号惯例:
      bool→0/1, dict/list→JSON 字符串, None→"")
    - 快照滚动截断(保留最近 500 条——5 分钟粒度
      ×500 ≈ 42 小时窗, 足够 7 日基线的日滚动重建)
    - 情绪原文即用即弃(红线)——仅匿名统计落库
"""

import json

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

# 快照滚动截断(防无限膨胀)
SNAPSHOT_MAX = 500

# 情绪统计滚动截断(近 1000 条匿名统计足够聚合分析)
EMOTION_MAX = 1000


class Xx66Repository:
    """66号仓储(双模式, 47号仓储范式平移)"""

    TABLE_SNAPSHOTS = "xx66_vitals_snapshots"
    TABLE_EMOTIONS = "xx66_emotions"
    TABLE_BADGES = "xx66_badges"
    TABLE_PREDICTIONS = "xx66_predictions"
    TABLE_LOGS = "xx66_engineer_log"

    _INT_FIELDS = ("snapshotId", "totalScore", "metricCount",
                   "statId", "satisfactionLinked",
                   "badgeId", "memberId",
                   "predictionId", "logId")

    def __init__(self):
        self.store = get_in_memory_store()

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_SNAPSHOTS, {})
        self.store.setdefault(self.TABLE_EMOTIONS, {})
        self.store.setdefault(self.TABLE_BADGES, {})
        self.store.setdefault(self.TABLE_PREDICTIONS, {})
        self.store.setdefault(self.TABLE_LOGS, {})

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
                     "metricCount", "statId",
                     "satisfactionLinked", "badgeId",
                     "memberId", "predictionId", "logId"):
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in ("zoneScores", "zoneDetails",
                       "features", "payload"):
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

    # --------------------------------------------------------
    # 情绪匿名统计(P1——红线: 情绪原文不落库, 仅分档+模式)
    # --------------------------------------------------------

    async def next_emotion_id(self) -> int:
        """情绪统计自增 ID"""
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.incr(
                _k("xx66", self.TABLE_EMOTIONS, "seq")))
        self._ensure_store()
        counter = self.store.setdefault(
            f"_{self.TABLE_EMOTIONS}_seq", 0)
        counter += 1
        self.store[f"_{self.TABLE_EMOTIONS}_seq"] = counter
        return counter

    async def save_emotion(self, record: dict) -> dict:
        """保存匿名情绪统计(滚动截断 EMOTION_MAX 条)"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("xx66", self.TABLE_EMOTIONS,
                     record["statId"])
            async with client.pipeline(
                    transaction=False) as pipe:
                pipe.hset(key,
                          mapping=self._serialize(record))
                pipe.zadd(_k("xx66", self.TABLE_EMOTIONS,
                             "index"),
                          {str(record["statId"]):
                           record["statId"]})
                pipe.zremrangebyrank(
                    _k("xx66", self.TABLE_EMOTIONS,
                       "index"), 0, -EMOTION_MAX - 1)
                await pipe.execute()
            return record
        self._ensure_store()
        table = self.store[self.TABLE_EMOTIONS]
        table[record["statId"]] = dict(record)
        if len(table) > EMOTION_MAX:
            for old_id in sorted(table)[:-EMOTION_MAX]:
                table.pop(old_id, None)
        return record

    async def list_emotions(
            self, limit: int = 100) -> list[dict]:
        """情绪统计时序(最新在前, 匿名)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.zrevrange(
                _k("xx66", self.TABLE_EMOTIONS, "index"),
                0, max(0, limit - 1))
            if not ids:
                return []
            async with client.pipeline(
                    transaction=False) as pipe:
                for sid in ids:
                    pipe.hgetall(_k(
                        "xx66", self.TABLE_EMOTIONS, sid))
                rows = await pipe.execute()
            return [self._deserialize(r) for r in rows
                    if r]
        self._ensure_store()
        rows = sorted(
            self.store[self.TABLE_EMOTIONS].values(),
            key=lambda r: r.get("statId") or 0,
            reverse=True)
        return [dict(r) for r in rows[:limit]]

    async def get_emotion(self, stat_id: int) -> dict | None:
        """单条情绪统计(终态满意度回填定位)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "xx66", self.TABLE_EMOTIONS, stat_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_EMOTIONS].get(stat_id)
        return dict(rec) if rec else None

    async def link_satisfaction(self, stat_id: int,
                                satisfaction: int) -> dict:
        """终态满意度回填(1-5; 幂等——已回填不覆盖)"""
        rec = await self.get_emotion(stat_id)
        if rec is None:
            raise KeyError(f"情绪统计 {stat_id} 不存在")
        if rec.get("satisfactionLinked"):
            return rec
        rec["satisfactionLinked"] = int(satisfaction)
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("xx66", self.TABLE_EMOTIONS,
                   stat_id),
                mapping=self._serialize(rec))
        else:
            self._ensure_store()
            self.store[self.TABLE_EMOTIONS][stat_id] = rec
        return rec

    # --------------------------------------------------------
    # 虚拟勋章(P1——服务终态满意度≥4 授予, 纯展示)
    # --------------------------------------------------------

    async def save_badge(self, record: dict) -> dict:
        """授予勋章(幂等——同 member+kind+reason 不重复)"""
        existing = await self.list_badges(
            member_id=record["memberId"], limit=200)
        for b in existing:
            if (b.get("kind") == record["kind"]
                    and b.get("reason") == record["reason"]):
                return b
        if is_redis_mode():
            client = await get_redis_client()
            badge_id = int(await client.incr(
                _k("xx66", self.TABLE_BADGES, "seq")))
            record["badgeId"] = badge_id
            await client.hset(
                _k("xx66", self.TABLE_BADGES, badge_id),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        table = self.store[self.TABLE_BADGES]
        badge_id = self.store.setdefault(
            f"_{self.TABLE_BADGES}_seq", 0) + 1
        self.store[f"_{self.TABLE_BADGES}_seq"] = badge_id
        record["badgeId"] = badge_id
        table[badge_id] = dict(record)
        return record

    async def list_badges(self, member_id: int = None,
                          limit: int = 50) -> list[dict]:
        """勋章列表(按 member 过滤; 授予时间倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "xx66", self.TABLE_BADGES, "*"))
            keys = [k for k in keys
                    if not k.endswith(":seq")]
            rows = []
            for i in range(0, len(keys), 200):
                pipe = client.pipeline(
                    transaction=False)
                for k in keys[i:i + 200]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        rows.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            rows = [dict(r) for r in
                    self.store[self.TABLE_BADGES].values()]
        if member_id is not None:
            rows = [r for r in rows
                    if r.get("memberId") == member_id]
        rows.sort(key=lambda r: r.get("badgeId") or 0,
                  reverse=True)
        return rows[:limit]

    # --------------------------------------------------------
    # 故障预测预警(P2——特征全量留痕可复现)
    # --------------------------------------------------------

    async def next_prediction_id(self) -> int:
        """预测自增 ID"""
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.incr(
                _k("xx66", self.TABLE_PREDICTIONS, "seq")))
        self._ensure_store()
        counter = self.store.setdefault(
            f"_{self.TABLE_PREDICTIONS}_seq", 0)
        counter += 1
        self.store[f"_{self.TABLE_PREDICTIONS}_seq"] = counter
        return counter

    async def save_prediction(self, record: dict) -> dict:
        """保存预警(同 metricKey open 态幂等由服务层把关)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("xx66", self.TABLE_PREDICTIONS,
                   record["predictionId"]),
                mapping=self._serialize(record))
            await client.zadd(
                _k("xx66", self.TABLE_PREDICTIONS, "index"),
                {str(record["predictionId"]):
                 record["predictionId"]})
            return record
        self._ensure_store()
        self.store[self.TABLE_PREDICTIONS][
            record["predictionId"]] = dict(record)
        return record

    async def list_predictions(
            self, limit: int = 50) -> list[dict]:
        """预警列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.zrevrange(
                _k("xx66", self.TABLE_PREDICTIONS,
                   "index"), 0, max(0, limit - 1))
            if not ids:
                return []
            async with client.pipeline(
                    transaction=False) as pipe:
                for pid in ids:
                    pipe.hgetall(_k(
                        "xx66", self.TABLE_PREDICTIONS,
                        pid))
                rows = await pipe.execute()
            return [self._deserialize(r) for r in rows
                    if r]
        self._ensure_store()
        rows = sorted(
            self.store[self.TABLE_PREDICTIONS].values(),
            key=lambda r: r.get("predictionId") or 0,
            reverse=True)
        return [dict(r) for r in rows[:limit]]

    # --------------------------------------------------------
    # engineer_log 哈希指纹链(P2——prev_hash 串联防篡改)
    # --------------------------------------------------------

    async def next_log_id(self) -> int:
        """留痕自增 ID"""
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.incr(
                _k("xx66", self.TABLE_LOGS, "seq")))
        self._ensure_store()
        counter = self.store.setdefault(
            f"_{self.TABLE_LOGS}_seq", 0)
        counter += 1
        self.store[f"_{self.TABLE_LOGS}_seq"] = counter
        return counter

    async def save_log(self, record: dict) -> dict:
        """追加操作留痕(指纹链——只追加不修改)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("xx66", self.TABLE_LOGS,
                   record["logId"]),
                mapping=self._serialize(record))
            await client.zadd(
                _k("xx66", self.TABLE_LOGS, "index"),
                {str(record["logId"]): record["logId"]})
            return record
        self._ensure_store()
        self.store[self.TABLE_LOGS][
            record["logId"]] = dict(record)
        return record

    async def list_logs(
            self, limit: int = 100) -> list[dict]:
        """留痕列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.zrevrange(
                _k("xx66", self.TABLE_LOGS, "index"),
                0, max(0, limit - 1))
            if not ids:
                return []
            async with client.pipeline(
                    transaction=False) as pipe:
                for lid in ids:
                    pipe.hgetall(_k(
                        "xx66", self.TABLE_LOGS, lid))
                rows = await pipe.execute()
            return [self._deserialize(r) for r in rows
                    if r]
        self._ensure_store()
        rows = sorted(
            self.store[self.TABLE_LOGS].values(),
            key=lambda r: r.get("logId") or 0,
            reverse=True)
        return [dict(r) for r in rows[:limit]]

    async def latest_log(self) -> dict | None:
        """最近一条留痕(指纹链 prev_hash 锚点)"""
        rows = await self.list_logs(limit=1)
        return rows[0] if rows else None
