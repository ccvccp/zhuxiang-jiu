"""锁键前缀约定回归测试(TD-2 闭环证据)

约定(见 docs/并发锁实施规范.md): Redis 分布式锁键必须为
`lock:{original_key}` 格式(如 lock:stock:2)。

实现方式: core/locks.py 的 _RedisLockWrapper 在 __aenter__ 内
统一添加 `lock:` 前缀, 各 service 只传资源键(如 agreement:{id})。

本测试用 FakeClient 捕获 client.lock(name) 调用, 验证:
    1. 传入任意资源键, 实际 Redis 锁名均为 lock:{资源键}
    2. agreement 模块的锁键(核对问题 TD-2 的怀疑对象)同样合规

运行: $env:LOCK_MODE="redis"; python test_lock_key_format.py
(只验证键名构造, 不需要真实 Redis 服务)
"""
import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BACKEND_DIR))

os.environ["LOCK_MODE"] = "redis"

import core.locks as locks_mod


class FakeRedisLock:
    """模拟 redis.asyncio.Lock(仅捕获键名)"""

    def __init__(self, name, **kwargs):
        self.name = name
        self._owner = False

    async def acquire(self):
        self._owner = True

    def owned(self):
        return self._owner

    async def release(self):
        self._owner = False


class FakeRedisClient:
    """模拟 redis.asyncio.Redis, 记录 lock() 收到的键名"""

    def __init__(self):
        self.lock_names = []

    def lock(self, name, **kwargs):
        self.lock_names.append(name)
        return FakeRedisLock(name, **kwargs)


async def _run_case(resource_key: str, fake: FakeRedisClient) -> str:
    async with locks_mod.get_lock(resource_key):
        pass
    return fake.lock_names[-1]


async def main():
    fake = FakeRedisClient()
    orig = locks_mod._get_redis_client
    locks_mod._get_redis_client = lambda: _async_return(fake)

    try:
        cases = [
            "agreement:1",                       # TD-2 怀疑对象
            "agreement:consent:100:1",
            "stock:2",                           # 规范示例键
            "ticket:transition:TK20260829001",
            "role:settle:TK001",
        ]
        for key in cases:
            actual = await _run_case(key, fake)
            expected = f"lock:{key}"
            assert actual == expected, (
                f"锁键前缀违规: 资源键 {key!r} → 实际 {actual!r}, 期望 {expected!r}")
            print(f"  ✓ {key!r} → {actual!r}")

        print("锁键前缀约定回归测试: 全部通过(lock: 前缀由 core/locks 统一添加)")
    finally:
        locks_mod._get_redis_client = orig


async def _async_return(value):
    return value


if __name__ == "__main__":
    asyncio.run(main())
