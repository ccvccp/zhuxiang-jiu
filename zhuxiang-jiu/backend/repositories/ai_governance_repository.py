"""46号·AI 治理与合规中枢数据访问层(双模式: 内存 + Redis)

表清单(前缀 ai46, 计划 §三/§四/§五/§六):
    ai46_registry             AI 资产注册中心(scorerId 自然键, P0)
    ai46_changes              变更审批总线(P0)
    ai46_health_snapshots     健康巡检快照(P1, 只追加)
    ai46_alerts               治理告警队列(P1, 当日同键去重)
    ai46_fairness_samples     公平性采样(P2, 只追加)
    ai46_fairness_reports     公平性审计报告(P2, 只追加)
    ai46_replay_log           决策日志总线(P3, 只追加)

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

健康巡检快照结构(P1):
    {scanId, scannedAt, scorerCount, avgScore,
     byLevel(JSON), hits(JSON), alertsNew, alertsUpdated,
     entries(JSON: [单档案健康明细])}

治理告警记录结构(P1):
    {alertId, scorerId, label, signal: stagnation|depletion|
     drift_high, level: warn, message, day(YYYY-MM-DD),
     occurrences, firstSeenAt, lastSeenAt, firstScanId,
     status: open}

公平性采样记录结构(P2):
    {sampleId, scorerId, group, score, passed,
     source: report|trust45, reportedAt}
    ——最小采集红线: 无个人标识字段(43号脱敏口径)

公平性审计报告记录结构(P2):
    {reportId, scorerId, generatedAt, sampleCount,
     groupCount, flagged, meanDiffRatio, passRateGap,
     groups(JSON: 群体统计), conclusion}

决策回放日志结构(P3):
    {replayId, scorerId, subjectRef(脱敏引用——不含个人
     标识字段), factors(JSON: 因子快照), weightVersion,
     score, action, ts}
    ——最小采集红线: subjectRef 仅存脱敏引用(哈希/业务键)

设计对齐:
    - 双模式存储 + 显式序列化口径(38-45号惯例:
      bool→0/1, dict/list→JSON 字符串, None→"")
    - 变更审批留痕只追加语义(状态翻转仅 update 固定字段)
    - 告警索引创建与更新分离(45号教训: 列表 LPUSH 仅在
      new=True 创建时执行, 更新不重复入列)
    - 采样与报告只追加(审计流水不可变)
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

# 健康信号(P1 三检测器)
HEALTH_SIGNAL_VALUES = ("stagnation", "depletion", "drift_high")

# 公平性采样来源(P2: 自愿上报 / 45号事件适配器)
FAIRNESS_SAMPLE_SOURCES = ("report", "trust45")


class AiGovernance46Repository:
    """46号 AI 治理仓储(双模式, 45号仓储范式平移)"""

    TABLE_REGISTRY = "ai46_registry"
    TABLE_CHANGES = "ai46_changes"
    TABLE_SNAPSHOTS = "ai46_health_snapshots"
    TABLE_ALERTS = "ai46_alerts"
    TABLE_FAIRNESS_SAMPLES = "ai46_fairness_samples"
    TABLE_FAIRNESS_REPORTS = "ai46_fairness_reports"
    TABLE_REPLAY_LOG = "ai46_replay_log"

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_REGISTRY, {})
        self.store.setdefault(self.TABLE_CHANGES, {})
        self.store.setdefault(self.TABLE_SNAPSHOTS, {})
        self.store.setdefault(self.TABLE_ALERTS, {})
        self.store.setdefault(self.TABLE_FAIRNESS_SAMPLES, {})
        self.store.setdefault(self.TABLE_FAIRNESS_REPORTS, {})
        self.store.setdefault(self.TABLE_REPLAY_LOG, {})

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
            if k in ("govId", "changeId", "scanId", "alertId",
                     "occurrences", "alertsNew", "alertsUpdated",
                     "scorerCount", "firstScanId", "sampleId",
                     "reportId", "sampleCount", "groupCount",
                     "replayId"):
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in ("factors",):
                try:
                    record[k] = json.loads(v) if v else []
                except (TypeError, ValueError):
                    record[k] = []
            elif k in ("payload", "byLevel", "hits", "groups"):
                try:
                    record[k] = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            elif k in ("entries", "skipped"):
                try:
                    record[k] = json.loads(v) if v else []
                except (TypeError, ValueError):
                    record[k] = []
            elif k in ("avgScore", "score", "meanDiffRatio",
                       "passRateGap", "passRate"):
                try:
                    record[k] = float(v) if v != "" else 0.0
                except (TypeError, ValueError):
                    record[k] = 0.0
            elif k == "flagged":
                record[k] = str(v) == "1"
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

    async def save_change(self, record: dict,
                          new: bool = True) -> dict:
        """保存变更(new=True 创建并入列; new=False 仅更新
        字段不重复入列——Redis LPUSH 幂等防线, 45号教训)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(
                _k("ai46", self.TABLE_CHANGES,
                   record["changeId"]),
                mapping=self._serialize(record))
            if new:
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
        reviewNote/error/reviewedAt——不入列)"""
        rec = await self.get_change(change_id)
        if rec is None:
            return None
        rec.update(changes)
        return await self.save_change(rec, new=False)

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

    # --------------------------------------------------------
    # 健康巡检快照(P1, 只追加——快照不可变)
    # --------------------------------------------------------

    async def next_scan_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("ai46", "health", "snap", "seq"))
        self._ensure_store()
        seq = self.store.get("_ai46_scan_seq", 0) + 1
        self.store["_ai46_scan_seq"] = seq
        return seq

    async def save_snapshot(self, record: dict) -> dict:
        """保存巡检快照(创建即追加索引——快照不可变, 无更新路径)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(
                _k("ai46", self.TABLE_SNAPSHOTS,
                   record["scanId"]),
                mapping=self._serialize(record))
            pipe.lpush(_k("ai46", "health", "snap_all"),
                       record["scanId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_SNAPSHOTS][
            record["scanId"]] = dict(record)
        return record

    async def get_snapshot(self, scan_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("ai46", self.TABLE_SNAPSHOTS, scan_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_SNAPSHOTS].get(scan_id)
        return dict(rec) if rec else None

    async def list_snapshots(
            self, limit: int = 50) -> list[dict]:
        """快照列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("ai46", "health", "snap_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for sid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "ai46", self.TABLE_SNAPSHOTS, int(sid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[self.TABLE_SNAPSHOTS].values()]
        result.sort(key=lambda r: -(
            int(r.get("scanId") or 0)))
        return result[:limit]

    async def get_latest_snapshot(self) -> dict | None:
        snaps = await self.list_snapshots(limit=1)
        return snaps[0] if snaps else None

    # --------------------------------------------------------
    # 治理告警(P1, 当日同键去重: scorerId|signal|day)
    # --------------------------------------------------------

    async def next_alert_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("ai46", "alerts", "seq"))
        self._ensure_store()
        seq = self.store.get("_ai46_alert_seq", 0) + 1
        self.store["_ai46_alert_seq"] = seq
        return seq

    @staticmethod
    def _alert_day_key(scorer_id: str, signal: str,
                       day: str) -> str:
        return f"{scorer_id}|{signal}|{day}"

    async def save_alert(self, record: dict,
                         new: bool = True) -> dict:
        """保存告警(new=True 创建并入列+登记当日索引;
        new=False 仅更新字段不重复入列——45号索引教训)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            key = _k("ai46", self.TABLE_ALERTS,
                     record["alertId"])
            pipe.hset(key, mapping=self._serialize(record))
            if new:
                pipe.lpush(_k("ai46", "alerts_all"),
                           record["alertId"])
                pipe.hset(_k("ai46", "alerts", "day_index"),
                          self._alert_day_key(
                              record["scorerId"],
                              record["signal"],
                              record["day"]),
                          record["alertId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_ALERTS][
            record["alertId"]] = dict(record)
        if new:
            self.store.setdefault(
                "_ai46_alerts_all", []).insert(
                0, record["alertId"])
            self.store.setdefault(
                "_ai46_alert_day_index", {})[
                self._alert_day_key(
                    record["scorerId"],
                    record["signal"],
                    record["day"])] = record["alertId"]
        return record

    async def get_alert(self, alert_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("ai46", self.TABLE_ALERTS, alert_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_ALERTS].get(alert_id)
        return dict(rec) if rec else None

    async def find_alert_of_day(
            self, scorer_id: str, signal: str,
            day: str) -> dict | None:
        """当日同键告警查询(去重判定: 同档案同信号当日一条)"""
        if is_redis_mode():
            client = await get_redis_client()
            alert_id = await client.hget(
                _k("ai46", "alerts", "day_index"),
                self._alert_day_key(scorer_id, signal, day))
            if not alert_id:
                return None
            return await self.get_alert(int(alert_id))
        self._ensure_store()
        alert_id = self.store.get(
            "_ai46_alert_day_index", {}).get(
            self._alert_day_key(scorer_id, signal, day))
        if not alert_id:
            return None
        return await self.get_alert(alert_id)

    async def list_alerts(
            self, signal: str = None,
            scorer_id: str = None,
            limit: int = 200) -> list[dict]:
        """告警队列(最新在前; 信号/档案过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("ai46", "alerts_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for aid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "ai46", self.TABLE_ALERTS, int(aid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[self.TABLE_ALERTS].values()]
        if signal:
            result = [a for a in result
                      if a.get("signal") == signal]
        if scorer_id:
            result = [a for a in result
                      if a.get("scorerId") == scorer_id]
        result.sort(key=lambda a: -(
            int(a.get("alertId") or 0)))
        return result[:limit]

    # --------------------------------------------------------
    # 公平性采样(P2, 只追加——审计流水不可变)
    # --------------------------------------------------------

    async def next_sample_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("ai46", "fairness", "samples", "seq"))
        self._ensure_store()
        seq = self.store.get("_ai46_sample_seq", 0) + 1
        self.store["_ai46_sample_seq"] = seq
        return seq

    async def add_sample(self, record: dict) -> int:
        """追加一条公平性采样(返回 sampleId; LPUSH 新→旧)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.lpush(
                _k("ai46", "fairness", "samples",
                   record["scorerId"]),
                json.dumps(record, ensure_ascii=False))
            pipe.hset(
                _k("ai46", self.TABLE_FAIRNESS_SAMPLES,
                   record["sampleId"]),
                mapping=self._serialize(record))
            await pipe.execute()
            return record["sampleId"]
        self._ensure_store()
        self.store[self.TABLE_FAIRNESS_SAMPLES][
            record["sampleId"]] = dict(record)
        self.store.setdefault(
            "_ai46_fairness_index", {}).setdefault(
            record["scorerId"], []).insert(
            0, record["sampleId"])
        return record["sampleId"]

    async def list_samples(self, scorer_id: str,
                           limit: int = 0) -> list[dict]:
        """按档案列采样(新→旧; limit=0 不限)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.lrange(
                _k("ai46", "fairness", "samples", scorer_id),
                0, -1)
            records = [json.loads(x) for x in raw]
        else:
            self._ensure_store()
            records = [dict(self.store[
                self.TABLE_FAIRNESS_SAMPLES].get(sid))
                for sid in self.store.get(
                    "_ai46_fairness_index", {}).get(
                    scorer_id, [])]
        return records if not limit else records[:limit]

    async def count_samples(self, scorer_id: str) -> int:
        return len(await self.list_samples(scorer_id))

    # --------------------------------------------------------
    # 公平性审计报告(P2, 只追加)
    # --------------------------------------------------------

    async def next_report_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("ai46", "fairness", "reports", "seq"))
        self._ensure_store()
        seq = self.store.get("_ai46_report_seq", 0) + 1
        self.store["_ai46_report_seq"] = seq
        return seq

    async def save_report(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(
                _k("ai46", self.TABLE_FAIRNESS_REPORTS,
                   record["reportId"]),
                mapping=self._serialize(record))
            pipe.lpush(_k("ai46", "fairness", "reports_all"),
                       record["reportId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_FAIRNESS_REPORTS][
            record["reportId"]] = dict(record)
        return record

    async def get_report(self, report_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("ai46", self.TABLE_FAIRNESS_REPORTS,
                   report_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_FAIRNESS_REPORTS].get(
            report_id)
        return dict(rec) if rec else None

    async def list_reports(self, scorer_id: str = None,
                           limit: int = 50) -> list[dict]:
        """审计报告列表(最新在前; 档案过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("ai46", "fairness", "reports_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for rid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "ai46", self.TABLE_FAIRNESS_REPORTS,
                        int(rid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_FAIRNESS_REPORTS].values()]
        if scorer_id:
            result = [r for r in result
                      if r.get("scorerId") == scorer_id]
        result.sort(key=lambda r: -(
            int(r.get("reportId") or 0)))
        return result[:limit]

    async def get_latest_report(
            self, scorer_id: str = None) -> dict | None:
        reports = await self.list_reports(
            scorer_id=scorer_id, limit=1)
        return reports[0] if reports else None

    # --------------------------------------------------------
    # 调度统计(P6 调度轨——JSON 整体存取, ai_learning config 同款)
    # --------------------------------------------------------

    async def get_scheduler_stats(self) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("ai46", "scheduler", "stats"))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store.get("_ai46_scheduler_stats")

    async def save_scheduler_stats(self,
                                   record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("ai46", "scheduler", "stats"),
                json.dumps(record, ensure_ascii=False))
            return record
        self._ensure_store()
        self.store["_ai46_scheduler_stats"] = dict(record)
        return record

    # --------------------------------------------------------
    # 决策回放日志(P3, 只追加——决策流水不可变)
    # --------------------------------------------------------

    async def next_replay_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("ai46", "replay", "seq"))
        self._ensure_store()
        seq = self.store.get("_ai46_replay_seq", 0) + 1
        self.store["_ai46_replay_seq"] = seq
        return seq

    async def add_replay_log(self, record: dict) -> int:
        """追加一条决策日志(返回 replayId; LPUSH 新→旧)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.lpush(
                _k("ai46", "replay", "log",
                   record["scorerId"]),
                json.dumps(record, ensure_ascii=False))
            pipe.hset(
                _k("ai46", self.TABLE_REPLAY_LOG,
                   record["replayId"]),
                mapping=self._serialize(record))
            await pipe.execute()
            return record["replayId"]
        self._ensure_store()
        self.store[self.TABLE_REPLAY_LOG][
            record["replayId"]] = dict(record)
        self.store.setdefault(
            "_ai46_replay_index", {}).setdefault(
            record["scorerId"], []).insert(
            0, record["replayId"])
        return record["replayId"]

    async def get_replay_log(self,
                             replay_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("ai46", self.TABLE_REPLAY_LOG, replay_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_REPLAY_LOG].get(replay_id)
        return dict(rec) if rec else None

    async def list_replay_logs(
            self, scorer_id: str = None,
            limit: int = 50) -> list[dict]:
        """决策日志查询(新→旧; 档案过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = ([_k("ai46", "replay", "log", scorer_id)]
                    if scorer_id
                    else await client.keys(_k(
                        "ai46", "replay", "log", "*")))
            result = []
            for i in range(0, len(keys), 500):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 500]:
                    pipe.lrange(k, 0, -1)
                for raw_list in await pipe.execute():
                    for raw in raw_list:
                        result.append(json.loads(raw))
        else:
            self._ensure_store()
            index = self.store.get("_ai46_replay_index", {})
            if scorer_id:
                ids = index.get(scorer_id, [])
            else:
                ids = [rid for lst in index.values()
                       for rid in lst]
            result = [dict(self.store[
                self.TABLE_REPLAY_LOG].get(rid))
                for rid in ids]
        result.sort(key=lambda r: -(
            int(r.get("replayId") or 0)))
        return result[:limit]
