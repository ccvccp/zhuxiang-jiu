"""AI 自学习层数据访问层(双模式: 内存 + Redis)

为 14 个 AI 评分器提供自学习数据支撑(参照成熟 AI 大模型的在线学习闭环):

    决策(评分结果) → 反馈(真实结果标注) → 学习(Hedge 在线更新) → 新权重版本
    → 冠军/挑战者评估 → 晋升生效 → 漂移监控

存储结构:
    ai_learning_feedback  - 反馈记录(因子快照 + 决策动作 + 真实结果)
    ai_learning_profiles  - 权重档案(champion 生产版 / challenger 影子版)
    ai_learning_history   - 版本历史(已退役的全部权重版本)
    ai_learning_configs   - 学习配置(学习率/最小反馈数/自动晋升/护栏)
    ai_drift_stats        - 漂移统计(因子分数 EMA 与漂移度)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis(与 TrafficRepository 一致)
    - 全 async(项目约定)
    - Redis Key: zhuxiang:ai_learning:{entity}:{scorerId}
"""

import json
from typing import Optional

from core.helpers import ts
from repositories.backend import (
    _k, get_in_memory_store, get_redis_client, is_redis_mode,
)


class AiLearningRepository:
    """AI 自学习层数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_feedback_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("ai_learning", "feedback", "seq"))
        self._ensure_store()
        seq = self.store.get("_ai_learning_feedback_seq", 0) + 1
        self.store["_ai_learning_feedback_seq"] = seq
        return seq

    # ============================================================
    # 反馈记录 CRUD(评分决策 → 真实结果闭环)
    # ============================================================

    async def add_feedback(self, record: dict) -> int:
        """新增反馈记录(返回反馈ID)"""
        record["feedbackId"] = await self.next_feedback_id()
        record["status"] = record.get("status", "pending")
        if is_redis_mode():
            client = await get_redis_client()
            await client.lpush(
                _k("ai_learning", "feedback", record["scorerId"]),
                json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["ai_learning_feedback"].setdefault(
                record["scorerId"], []).append(record)
        return record["feedbackId"]

    async def list_feedback(self, scorer_id: str, status: str = None,
                            limit: int = 200) -> list[dict]:
        """按评分器列出反馈记录(新→旧), 可按状态过滤"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.lrange(
                _k("ai_learning", "feedback", scorer_id), 0, -1)
            records = [json.loads(x) for x in raw]
            records.reverse()  # lpush 存储 → 反转为时间正序
        else:
            self._ensure_store()
            records = list(self.store["ai_learning_feedback"].get(scorer_id, []))
        if status:
            records = [r for r in records if r.get("status") == status]
        return records[-limit:] if limit else records

    async def mark_feedback_learned(self, scorer_id: str, feedback_ids: list[int]) -> int:
        """将指定反馈标记为已学习(返回实际更新数)"""
        id_set = set(feedback_ids)
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("ai_learning", "feedback", scorer_id)
            raw = await client.lrange(key, 0, -1)
            records = [json.loads(x) for x in raw]
            updated = 0
            for r in records:
                if r.get("feedbackId") in id_set and r.get("status") != "learned":
                    r["status"] = "learned"
                    r["learnedAt"] = ts()
                    updated += 1
            if updated:
                # 读改写整体重写(反馈量为管理级操作频次, 可接受)
                await client.delete(key)
                if records:
                    await client.rpush(key, *[json.dumps(r, ensure_ascii=False)
                                              for r in records])
            return updated
        self._ensure_store()
        records = self.store["ai_learning_feedback"].get(scorer_id, [])
        updated = 0
        for r in records:
            if r.get("feedbackId") in id_set and r.get("status") != "learned":
                r["status"] = "learned"
                r["learnedAt"] = ts()
                updated += 1
        return updated

    async def count_feedback(self, scorer_id: str, status: str = None) -> int:
        """统计反馈记录数(可按状态过滤)"""
        return len(await self.list_feedback(scorer_id, status=status, limit=0))

    # ============================================================
    # 权重档案(冠军/挑战者)与版本历史
    # ============================================================

    async def get_profile(self, scorer_id: str) -> Optional[dict]:
        """读取权重档案: {champion: 版本记录|None, challenger: 版本记录|None}"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("ai_learning", "profile", scorer_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["ai_learning_profiles"].get(scorer_id)

    async def save_profile(self, scorer_id: str, profile: dict) -> None:
        """整体保存权重档案(读-改-写在服务层加锁完成)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("ai_learning", "profile", scorer_id),
                             json.dumps(profile, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["ai_learning_profiles"][scorer_id] = profile

    async def add_history(self, scorer_id: str, version_record: dict) -> None:
        """追加退役版本到历史(lpush, 新→旧)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.lpush(
                _k("ai_learning", "history", scorer_id),
                json.dumps(version_record, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["ai_learning_history"].setdefault(
                scorer_id, []).insert(0, version_record)

    async def list_history(self, scorer_id: str, limit: int = 50) -> list[dict]:
        """列出版本历史(新→旧)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.lrange(
                _k("ai_learning", "history", scorer_id), 0, limit - 1)
            return [json.loads(x) for x in raw]
        self._ensure_store()
        history = self.store["ai_learning_history"].get(scorer_id, [])
        return history[:limit]

    # ============================================================
    # 学习配置
    # ============================================================

    async def get_config(self, scorer_id: str) -> Optional[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("ai_learning", "config", scorer_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["ai_learning_configs"].get(scorer_id)

    async def save_config(self, scorer_id: str, config: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("ai_learning", "config", scorer_id),
                             json.dumps(config, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["ai_learning_configs"][scorer_id] = config

    # ============================================================
    # 漂移统计
    # ============================================================

    async def get_drift(self, scorer_id: str) -> Optional[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("ai_learning", "drift", scorer_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["ai_drift_stats"].get(scorer_id)

    async def save_drift(self, scorer_id: str, stats: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("ai_learning", "drift", scorer_id),
                             json.dumps(stats, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["ai_drift_stats"][scorer_id] = stats

    # ============================================================
    # 决策快照暂存(v7.6 自动反馈闭环)
    # ============================================================

    SNAPSHOT_TTL_SECONDS = 7 * 24 * 3600  # 7 天, 过期未配对视为无效信号

    async def save_decision_snapshot(self, scorer_id: str, business_key: str,
                                     snapshot: dict) -> None:
        """暂存评分决策快照(等待业务终态事件配对)

        snapshot: {scorerId, businessKey, decision, score, factors,
                   weightVersion, createdAt}
        """
        snapshot = dict(snapshot)
        snapshot["scorerId"] = scorer_id
        snapshot["businessKey"] = business_key
        snapshot["createdAt"] = ts()
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("ai_learning", "snapshot", scorer_id, business_key),
                json.dumps(snapshot, ensure_ascii=False),
                ex=self.SNAPSHOT_TTL_SECONDS)
        else:
            self._ensure_store()
            self.store["ai_learning_snapshots"][(scorer_id, business_key)] = snapshot

    async def get_decision_snapshot(self, scorer_id: str,
                                     business_key: str) -> Optional[dict]:
        """读取决策快照(过期返回 None, 内存模式顺带惰性清理)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("ai_learning", "snapshot", scorer_id, business_key))
            return json.loads(data) if data else None
        self._ensure_store()
        snapshots = self.store["ai_learning_snapshots"]
        snapshot = snapshots.get((scorer_id, business_key))
        if snapshot is None:
            return None
        try:
            from datetime import datetime, timedelta, timezone
            created = datetime.fromisoformat(snapshot["createdAt"])
            if datetime.now(timezone.utc) - created > timedelta(
                    seconds=self.SNAPSHOT_TTL_SECONDS):
                snapshots.pop((scorer_id, business_key), None)
                return None
        except (KeyError, ValueError):
            return None
        return snapshot

    async def consume_decision_snapshot(self, scorer_id: str,
                                         business_key: str) -> Optional[dict]:
        """取出并删除决策快照(天然去重: 同一业务键只能配对一次)"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("ai_learning", "snapshot", scorer_id, business_key)
            data = await client.getdel(key)
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store["ai_learning_snapshots"].pop(
            (scorer_id, business_key), None)

    async def count_snapshots(self, scorer_id: str) -> int:
        """统计未配对快照数(监控用, 内存模式含过期项)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("ai_learning", "snapshot", scorer_id, "*"))
            return len(keys)
        self._ensure_store()
        return sum(1 for sid, _ in self.store["ai_learning_snapshots"]
                   if sid == scorer_id)

    # ============================================================
    # 调度统计(v7.6 定时学习)
    # ============================================================

    async def get_scheduler_stats(self) -> Optional[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("ai_learning", "scheduler", "stats"))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store.get("ai_learning_scheduler_stats")

    async def save_scheduler_stats(self, stats: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("ai_learning", "scheduler", "stats"),
                             json.dumps(stats, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["ai_learning_scheduler_stats"] = stats

    # ============================================================
    # 决策阻断审计与统计(v7.8)
    # ============================================================

    ENFORCEMENT_AUDIT_MAX = 1000   # 每评分器审计封顶(防内存/Redis 膨胀)
    BURST_WINDOW_SECONDS = 3600    # 熔断滑动窗口: 1 小时

    async def add_enforcement_audit(self, scorer_id: str, record: dict) -> None:
        """追加阻断决策审计记录(新→旧, 封顶截断)"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("ai_learning", "enforcement", "audit", scorer_id)
            pipe = client.pipeline()
            pipe.lpush(key, json.dumps(record, ensure_ascii=False))
            pipe.ltrim(key, 0, self.ENFORCEMENT_AUDIT_MAX - 1)
            await pipe.execute()
        else:
            self._ensure_store()
            audits = self.store["ai_learning_enforcement_audit"].setdefault(
                scorer_id, [])
            audits.insert(0, record)
            del audits[self.ENFORCEMENT_AUDIT_MAX:]

    async def list_enforcement_audit(self, scorer_id: str,
                                     limit: int = 50) -> list[dict]:
        """列出阻断决策审计(新→旧)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.lrange(
                _k("ai_learning", "enforcement", "audit", scorer_id),
                0, max(0, limit - 1) if limit else -1)
            return [json.loads(x) for x in raw]
        self._ensure_store()
        audits = self.store["ai_learning_enforcement_audit"].get(scorer_id, [])
        return audits[:limit] if limit else list(audits)

    async def incr_enforcement_stats(self, scorer_id: str,
                                     *fields: str) -> None:
        """累加阻断统计计数器(fields: total/blocked/reviews/degraded)"""
        if not fields:
            return
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline()
            key = _k("ai_learning", "enforcement", "stats", scorer_id)
            for field in fields:
                pipe.hincrby(key, field, 1)
            await pipe.execute()
        else:
            self._ensure_store()
            stats = self.store["ai_learning_enforcement_stats"].setdefault(
                scorer_id, {})
            for field in fields:
                stats[field] = int(stats.get(field, 0)) + 1

    async def get_enforcement_stats(self, scorer_id: str) -> dict:
        """读取阻断统计计数器(无记录返回全 0)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hgetall(
                _k("ai_learning", "enforcement", "stats", scorer_id))
            return {k: int(v) for k, v in raw.items()}
        self._ensure_store()
        return dict(self.store["ai_learning_enforcement_stats"].get(
            scorer_id, {}))

    def _burst_window_start(self) -> int:
        """当前熔断窗口起点(epoch 秒)"""
        import time
        return int(time.time()) // self.BURST_WINDOW_SECONDS

    async def incr_burst_window(self, scorer_id: str, field: str) -> None:
        """累加熔断滑动窗口计数(field: total/blocked)"""
        ws = self._burst_window_start()
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("ai_learning", "enforcement", "burst",
                     scorer_id, field, str(ws))
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.expire(key, self.BURST_WINDOW_SECONDS * 2)
            await pipe.execute()
        else:
            self._ensure_store()
            windows = self.store["ai_learning_enforcement_windows"]
            key = (scorer_id, field, ws)
            windows[key] = int(windows.get(key, 0)) + 1
            # 顺手清理过期窗口(仅同评分器, 避免无限增长)
            stale = [k for k in windows
                     if k[0] == scorer_id and k[2] < ws]
            for k in stale:
                windows.pop(k, None)

    async def get_burst_window(self, scorer_id: str,
                               field: str) -> int:
        """读取当前熔断窗口计数"""
        ws = self._burst_window_start()
        if is_redis_mode():
            client = await get_redis_client()
            value = await client.get(_k(
                "ai_learning", "enforcement", "burst",
                scorer_id, field, str(ws)))
            return int(value or 0)
        self._ensure_store()
        return int(self.store["ai_learning_enforcement_windows"].get(
            (scorer_id, field, ws), 0))

    # ============================================================
    # 内存模式辅助
    # ============================================================

    def _ensure_store(self) -> None:
        if "ai_learning_feedback" not in self.store:
            self.store["ai_learning_feedback"] = {}      # scorerId → [record]
            self.store["ai_learning_profiles"] = {}      # scorerId → {champion, challenger}
            self.store["ai_learning_history"] = {}       # scorerId → [version, ...]
            self.store["ai_learning_configs"] = {}       # scorerId → config
            self.store["ai_drift_stats"] = {}            # scorerId → stats
            self.store["ai_learning_snapshots"] = {}     # (scorerId, businessKey) → snapshot
            self.store["ai_learning_enforcement_audit"] = {}   # scorerId → [record]
            self.store["ai_learning_enforcement_stats"] = {}   # scorerId → {field: count}
            self.store["ai_learning_enforcement_windows"] = {} # (scorerId, field, ws) → count
            self.store["_ai_learning_feedback_seq"] = 0
