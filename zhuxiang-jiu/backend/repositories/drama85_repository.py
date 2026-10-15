"""85号·短剧智能研判引擎数据访问层(2026-10-12)

外部方案《全网短剧智能数据搜集预判决策大模型》七层中, 剧本生成层
由 84号在位承担, 渲染发布由 73号承担——85号补齐数据侧四层:
采集池(清洗结构化+标签)、知识库四库(题材/爽点模式/人物场景/合规)、
改编方案(A/B 版本)、进化日志与策略版本。

存储(内存/Redis 双模式, drama85:* 键):
    drama85:drama:{dramaId}        采集的短剧记录(含结构+标签+指数)
    drama85:dramas:all             全量索引(set)
    drama85:adapt:{adaptId}        改编方案(策略+A/B+关联84剧本)
    drama85:adapts:all             改编索引(set)
    drama85:kb:{kbType}:{itemId}   知识库条目(四种 kbType)
    drama85:kb:{kbType}:all        各库索引(set)
    drama85:evolution:log          进化日志(list, 头插)
    drama85:strategy               当前策略权重(dict, 版本化字段内嵌)
"""
import json
from datetime import datetime

from core.helpers import ts
from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

KB_TYPES = ("theme", "hook_pattern", "persona_scene", "compliance")

EVOLUTION_LOG_MAX = 200


class Drama85Repository:
    """短剧研判引擎存储(双模式)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None \
            else get_in_memory_store()
        self._ensure_store()

    def _ensure_store(self) -> None:
        s = self.store
        s.setdefault("drama85_dramas", {})
        s.setdefault("drama85_adapts", {})
        s.setdefault("drama85_kb", {t: {} for t in KB_TYPES})
        s.setdefault("drama85_evolution_log", [])
        s.setdefault("drama85_strategy", None)

    # ---------- 采集池 ----------

    async def save_drama(self, drama: dict) -> dict:
        did = drama["dramaId"]
        drama.setdefault("createdAt", ts())
        drama["updatedAt"] = ts()
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("drama85", "drama", did),
                             json.dumps(drama, ensure_ascii=False))
            await client.sadd(_k("drama85", "dramas", "all"), did)
        else:
            self._ensure_store()
            self.store["drama85_dramas"][did] = dict(drama)
        return dict(drama)

    async def get_drama(self, drama_id: str):
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("drama85", "drama", drama_id))
            return json.loads(raw) if raw else None
        self._ensure_store()
        d = self.store["drama85_dramas"].get(drama_id)
        return dict(d) if d else None

    async def list_dramas(self, theme: str = None,
                          min_quality: float = 0.0,
                          limit: int = 100) -> list:
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.smembers(
                _k("drama85", "dramas", "all"))
            items = []
            for i in ids:
                raw = await client.get(
                    _k("drama85", "drama", i))
                if raw:
                    items.append(json.loads(raw))
        else:
            self._ensure_store()
            items = list(self.store["drama85_dramas"].values())
        if theme:
            items = [d for d in items
                     if theme in (d.get("themes") or [])]
        if min_quality > 0:
            items = [d for d in items
                     if (d.get("qualityScore") or 0) >= min_quality]
        items.sort(key=lambda d: d.get("hotIndex", 0) or 0,
                   reverse=True)
        return items[:limit]

    # ---------- 知识库四库 ----------

    async def save_kb_item(self, kb_type: str, item: dict) -> dict:
        if kb_type not in KB_TYPES:
            raise ValueError(f"非法知识库类型: {kb_type}")
        item_id = item["itemId"]
        item["kbType"] = kb_type
        item.setdefault("createdAt", ts())
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("drama85", "kb", kb_type, item_id),
                json.dumps(item, ensure_ascii=False))
            await client.sadd(
                _k("drama85", "kb", kb_type, "all"), item_id)
        else:
            self._ensure_store()
            self.store["drama85_kb"][kb_type][item_id] = dict(item)
        return dict(item)

    async def get_kb_item(self, kb_type: str, item_id: str):
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("drama85", "kb", kb_type, item_id))
            return json.loads(raw) if raw else None
        self._ensure_store()
        item = self.store["drama85_kb"][kb_type].get(item_id)
        return dict(item) if item else None

    async def list_kb(self, kb_type: str,
                      limit: int = 200) -> list:
        if kb_type not in KB_TYPES:
            raise ValueError(f"非法知识库类型: {kb_type}")
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.smembers(
                _k("drama85", "kb", kb_type, "all"))
            items = []
            for i in ids:
                raw = await client.get(
                    _k("drama85", "kb", kb_type, i))
                if raw:
                    items.append(json.loads(raw))
        else:
            self._ensure_store()
            items = list(
                self.store["drama85_kb"][kb_type].values())
        items.sort(key=lambda x: x.get("createdAt", ""))
        return items[:limit]

    # ---------- 改编方案 ----------

    async def save_adapt(self, adapt: dict) -> dict:
        aid = adapt["adaptId"]
        adapt.setdefault("createdAt", ts())
        adapt["updatedAt"] = ts()
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("drama85", "adapt", aid),
                             json.dumps(adapt, ensure_ascii=False))
            await client.sadd(_k("drama85", "adapts", "all"), aid)
        else:
            self._ensure_store()
            self.store["drama85_adapts"][aid] = dict(adapt)
        return dict(adapt)

    async def get_adapt(self, adapt_id: str):
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("drama85", "adapt", adapt_id))
            return json.loads(raw) if raw else None
        self._ensure_store()
        a = self.store["drama85_adapts"].get(adapt_id)
        return dict(a) if a else None

    async def update_adapt(self, adapt_id: str,
                           fields: dict):
        adapt = await self.get_adapt(adapt_id)
        if adapt is None:
            return None
        adapt.update(fields)
        return await self.save_adapt(adapt)

    async def list_adapts(self, limit: int = 50) -> list:
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.smembers(
                _k("drama85", "adapts", "all"))
            items = []
            for i in ids:
                raw = await client.get(
                    _k("drama85", "adapt", i))
                if raw:
                    items.append(json.loads(raw))
        else:
            self._ensure_store()
            items = list(self.store["drama85_adapts"].values())
        items.sort(key=lambda a: a.get("createdAt", ""),
                   reverse=True)
        return items[:limit]

    # ---------- 进化日志 + 策略版本 ----------

    async def append_evolution(self, entry: dict) -> dict:
        entry["loggedAt"] = ts()
        if is_redis_mode():
            client = await get_redis_client()
            await client.lpush(
                _k("drama85", "evolution", "log"),
                json.dumps(entry, ensure_ascii=False))
            await client.ltrim(
                _k("drama85", "evolution", "log"),
                0, EVOLUTION_LOG_MAX - 1)
        else:
            self._ensure_store()
            self.store["drama85_evolution_log"].insert(0, entry)
            del self.store["drama85_evolution_log"][
                EVOLUTION_LOG_MAX:]
        return entry

    async def list_evolution(self, limit: int = 50) -> list:
        if is_redis_mode():
            client = await get_redis_client()
            rows = await client.lrange(
                _k("drama85", "evolution", "log"), 0, limit - 1)
            return [json.loads(r) for r in rows]
        self._ensure_store()
        return list(self.store["drama85_evolution_log"][:limit])

    async def get_strategy(self) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("drama85", "strategy"))
            return json.loads(raw) if raw else None
        self._ensure_store()
        s = self.store["drama85_strategy"]
        return dict(s) if s else None

    async def save_strategy(self, strategy: dict) -> dict:
        strategy["updatedAt"] = ts()
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("drama85", "strategy"),
                json.dumps(strategy, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["drama85_strategy"] = dict(strategy)
        return dict(strategy)
