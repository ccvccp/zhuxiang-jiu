"""并发安全:双模式锁工厂(对齐前端 Mutex FIFO 模式)

锁键: stock:{productId}(库存扣减/回补) / agent:{agentId}(代理商升级)

双模式设计(环境变量 LOCK_MODE 切换):
    asyncio:        单进程 asyncio.Lock(开发/测试/单 worker, 需显式设置)
    redis (默认):    跨进程 redis.asyncio.Lock(生产/多 worker, 开箱即用)

接口一致: get_lock(key) -> AsyncContextManager
端点代码零改动: async with get_lock(...) 不变
"""

import asyncio
import logging
import os
from typing import AsyncContextManager

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
_LOCK_TTL = 10.0            # 锁 TTL(秒), 超时自动释放防死锁
_LOCK_BLOCK_TIMEOUT = 30.0  # 等待获取锁的最长时间
_ASYNC_LOCKS_MAX_SIZE = 512  # asyncio 锁缓存上限, 防止无界增长导致内存泄漏

_async_locks: dict[str, asyncio.Lock] = {}
_redis_client = None


def _get_lock_mode() -> str:
    """动态读取 LOCK_MODE(运行时可变, 与 repositories.backend.is_redis_mode() 对齐)

    NOTE: 不再在模块级冻结, 避免测试 monkeypatch.setenv 后锁模式与存储模式不一致。
    """
    return os.environ.get("LOCK_MODE", "redis")


async def _get_redis_client():
    """懒加载 Redis 连接(单例)"""
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as redis
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


async def close_redis_client():
    """关闭 Redis 连接(应用 shutdown 时调用, 避免连接泄漏)"""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception as e:
            logger.warning("关闭 Redis 连接失败: %s", e)
        finally:
            _redis_client = None


class _RedisLockWrapper:
    """包装 redis.asyncio.Lock, 提供 async with 语义

    redis.asyncio.Lock 内置:
    - watchdog 自动续期(默认 TTL/3 续期一次, 长事务不误释放)
    - 阻塞获取(blocking=True, blocking_timeout 控制等待)
    - owned() 校验当前持有者, 防止误释放
    """

    def __init__(self, key: str):
        self.key = key
        self._lock = None

    async def __aenter__(self):
        client = await _get_redis_client()
        self._lock = client.lock(
            f"lock:{self.key}",
            timeout=_LOCK_TTL,
            blocking_timeout=_LOCK_BLOCK_TIMEOUT,
        )
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._lock is not None:
            try:
                if self._lock.owned():
                    await self._lock.release()
            except Exception as e:
                logger.warning("释放 Redis 锁失败(key=%s): %s", self.key, e)
        return False


def get_lock(key: str) -> AsyncContextManager:
    """获取锁: 双模式切换(动态读取 LOCK_MODE)

    LOCK_MODE=asyncio: 单进程 asyncio.Lock
        - 速度快, 无外部依赖
        - 仅单进程有效, 多 worker 下失效(已由方案 A 探针暴露)
        - 锁缓存有上限(_ASYNC_LOCKS_MAX_SIZE), 超限时清理无竞争的锁防止内存泄漏
    LOCK_MODE=redis (默认): 跨进程 redis.asyncio.Lock
        - 多 worker 下跨进程互斥
        - 需 Redis 服务, watchdog 自动续期
    """
    if _get_lock_mode() == "redis":
        return _RedisLockWrapper(key)
    # 单进程 asyncio.Lock
    if key not in _async_locks:
        # 锁缓存超限时, 清理未被持有的锁(LRU-like 策略)
        if len(_async_locks) >= _ASYNC_LOCKS_MAX_SIZE:
            _cleanup_async_locks()
        _async_locks[key] = asyncio.Lock()
    return _async_locks[key]


def _cleanup_async_locks():
    """清理未被持有的 asyncio 锁, 防止内存泄漏

    当锁缓存达到上限时, 遍历所有锁, 移除当前未被持有的锁。
    被持有的锁(locked=True)保留, 避免影响正在执行的临界区。
    """
    to_remove = [k for k, lock in _async_locks.items() if not lock.locked()]
    for k in to_remove:
        del _async_locks[k]
    if to_remove:
        logger.debug("清理 asyncio 锁缓存: 移除 %d 个未持有的锁", len(to_remove))
