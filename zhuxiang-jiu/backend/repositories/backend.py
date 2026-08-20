"""存储后端选择器:根据 STORE_MODE 透明切换内存/Redis

设计:
    - STORE_MODE 优先级: STORE_MODE > LOCK_MODE > asyncio
      (单一环境变量 LOCK_MODE=redis 即可同时切换锁+存储)
    - 内存模式(默认): 直接操作 _mock_store 字典
    - Redis 模式: 通过 redis.asyncio 客户端持久化

兼容契约:
    - `from main import _mock_store` 仍可用(测试直接改字典)
    - Redis 模式下 Repository 不再读写 _mock_store
    - 测试通过 conftest.py 强制 STORE_MODE=asyncio 保证零改动
"""

import os

# REDIS_URL 在导入时读取(连接配置, 运行时不变)
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

# NOTE: STORE_MODE 不再在模块级冻结, 改由 is_redis_mode() 动态读取
# 这样测试 fixture 可在运行时切换模式, 避免导入顺序污染

# Redis Key 前缀(避免与其他服务冲突)
KEY_PREFIX = "zhuxiang:"

# Redis 客户端单例(与 core/locks.py 共享配置, 独立连接池)
_redis_client = None


async def get_redis_client():
    """懒加载 Redis 连接(单例)

    与 core/locks.py 的 _get_redis_client 独立, 避免锁操作与数据操作互相阻塞。
    配置(REDIS_URL)共享, 确保连到同一 Redis 实例。
    """
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as redis
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def is_redis_mode() -> bool:
    """当前是否为 Redis 存储模式(动态读取环境变量)

    STORE_MODE 优先级: STORE_MODE > LOCK_MODE > asyncio
    每次调用实时读取, 避免 conftest.py 与 redis 测试的导入顺序污染。
    """
    mode = os.environ.get("STORE_MODE", os.environ.get("LOCK_MODE", "asyncio"))
    return mode == "redis"


def get_in_memory_store() -> dict:
    """获取内存后端单例(_mock_store)

    内存模式下 Repository 通过此函数访问数据,
    保持 `from main import _mock_store` 的测试契约。
    """
    from repositories.store import _mock_store
    return _mock_store


def _k(entity: str, *parts) -> str:
    """构造 Redis Key: zhuxiang:{entity}:{part1}:{part2}...

    例: _k("agent", 1) → "zhuxiang:agent:1"
        _k("inventory", "ZX42-2026L07") → "zhuxiang:inventory:ZX42-2026L07"
    """
    return KEY_PREFIX + entity + ":" + ":".join(str(p) for p in parts)
