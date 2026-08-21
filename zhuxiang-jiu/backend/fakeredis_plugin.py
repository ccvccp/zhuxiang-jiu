"""pytest 插件: 用 fakeredis 替代真实 Redis, 使 Redis 集成测试可在无 Redis 环境下运行

使用方式:
    python -m pytest test_redis_integration.py -p fakeredis_plugin -v

设计:
    - 在 pytest_configure 阶段 patch redis.from_url 和 redis.asyncio.from_url
    - 所有测试共享同一个 fakeredis 实例(通过 from_url 返回单例)
    - 不修改任何测试文件或业务代码
"""

import fakeredis
import fakeredis.aioredis

# fakeredis 单例(所有 from_url 调用返回同一实例, 模拟真实 Redis)
# 启用 Lua 脚本支持(lupa 已安装), 否则 Redis 分布式锁的 EVALSHA 会失败
_fake_sync = fakeredis.FakeStrictRedis(decode_responses=True, lua_max_executor_time=5)
_fake_async = fakeredis.aioredis.FakeRedis(decode_responses=True, lua_max_executor_time=5)


def pytest_configure(config):
    """在 pytest 启动时 patch redis.from_url(早于测试模块导入)"""
    import redis
    import redis.asyncio as aioredis

    # patch 同步 redis.from_url → 返回 fakeredis 单例
    redis.from_url = lambda url, **kwargs: _fake_sync

    # patch 异步 redis.asyncio.from_url → 返回 fakeredis 单例
    aioredis.from_url = lambda url, **kwargs: _fake_async
