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
    # 内存模式辅助
    # ============================================================

    def _ensure_store(self) -> None:
        if "ai_learning_feedback" not in self.store:
            self.store["ai_learning_feedback"] = {}      # scorerId → [record]
            self.store["ai_learning_profiles"] = {}      # scorerId → {champion, challenger}
            self.store["ai_learning_history"] = {}       # scorerId → [version, ...]
            self.store["ai_learning_configs"] = {}       # scorerId → config
            self.store["ai_drift_stats"] = {}            # scorerId → stats
            self.store["_ai_learning_feedback_seq"] = 0
