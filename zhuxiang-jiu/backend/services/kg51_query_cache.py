"""51号·小竹可信知识图谱 查询缓存(kg51_query_cache)

计划(docs/51号_小竹可信知识图谱实施计划.md §五 阶段5):
    SOP 存储与服务层缓存——热点查询结果缓存,
    TTL 与数据更新事件联动(写失效)。

设计(轻量进程内缓存——与全站双模式存储一致,
零新中间件依赖):
    - TTL 60s + 容量上限 200(LRU 淘汰)
    - 写失效: 采集管道新建实体/三元组时 invalidate_all()
      (ingest 侧调用——同模块内耦合, 零跨模块侵入)
    - fail-soft: 缓存异常不阻断查询(直接穿透)

多副本注意: 进程内缓存不跨实例同步——
多副本迁移列外部待办(与 48-50号 token 同窗口)。
"""

import logging
import time
from collections import OrderedDict

logger = logging.getLogger("kg51_query_cache")

TTL_SECONDS = 60
MAX_ENTRIES = 200

# LRU 缓存: key → (value, expires_at)
_CACHE: OrderedDict = OrderedDict()


def cache_get(key: str):
    """取缓存(过期/异常 → None 穿透; fail-soft)"""
    try:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return value
    except Exception:  # noqa: BLE001
        return None


def cache_put(key: str, value) -> None:
    """写缓存(超容量 LRU 淘汰; fail-soft)"""
    try:
        _CACHE[key] = (value,
                       time.monotonic() + TTL_SECONDS)
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_ENTRIES:
            _CACHE.popitem(last=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kg51_cache_put_skip: %s", exc)


def invalidate_all() -> int:
    """全量失效(写事件联动——返回清除条数)"""
    count = len(_CACHE)
    _CACHE.clear()
    return count


def cache_stats() -> dict:
    """缓存观测(容量/命中窗口)"""
    return {
        "entries": len(_CACHE),
        "maxEntries": MAX_ENTRIES,
        "ttlSeconds": TTL_SECONDS,
        "strategy": "LRU+TTL(写失效)",
    }
