"""
Redis Lock 单进程逻辑验证(用 mock FakeLock 模拟 redis-py Lock)

验证 _RedisLockWrapper 的接口适配逻辑:
  1. 基本获取/释放(async with → acquire/release)
  2. 互斥(并发时仅一个进入临界区)
  3. 释放后其他能获取
  4. 不同锁键不互斥
  5. 异常时锁自动释放

原理: _RedisLockWrapper 依赖 redis-py 的 Lock(acquire/owned/release)。
     redis-py Lock 本身是成熟库, 不需验证其正确性。
     需验证的是 _RedisLockWrapper 的"接口适配层":
     - async with 是否正确调用 acquire
     - __aexit__ 是否正确调用 release(且检查 owned)
     - 异常时是否自动释放

用 FakeLock(内部 asyncio.Lock + owned 标志)模拟 redis-py Lock,
     验证适配层逻辑。

运行: py probe_redis_lock_unit.py
"""
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.resolve()
PYLIBS = BACKEND_DIR.parent / "pylibs"
sys.path.insert(0, str(PYLIBS))
sys.path.insert(0, str(BACKEND_DIR))


# ============================================================
#  FakeLock: 模拟 redis-py 的 Lock 行为
# ============================================================

class FakeLock:
    """模拟 redis-py asyncio.Lock 的行为(单进程, 用于验证适配层)

    真实 redis-py Lock 用 Lua 脚本保证原子性, 这里用 asyncio.Lock 模拟。
    关键接口对齐: acquire() / owned() / release()
    """

    def __init__(self, name: str, timeout: float = 10.0, blocking_timeout: float = 30.0):
        self.name = name
        self.timeout = timeout
        self.blocking_timeout = blocking_timeout
        self._async_lock = asyncio.Lock()
        self._owner = False

    async def acquire(self):
        await self._async_lock.acquire()
        self._owner = True

    def owned(self):
        return self._owner

    async def release(self):
        self._owner = False
        self._async_lock.release()


class FakeRedisClient:
    """模拟 redis.asyncio.Redis 客户端, lock() 返回 FakeLock"""

    def __init__(self):
        self._locks: dict[str, FakeLock] = {}

    def lock(self, name: str, **kwargs):
        if name not in self._locks:
            self._locks[name] = FakeLock(name, **kwargs)
        return self._locks[name]


# ============================================================
#  Monkey-patch: 替换 main 的 Redis 客户端
# ============================================================

import main

_fake_client = FakeRedisClient()
main._redis_client = _fake_client
main.LOCK_MODE = "redis"  # 强制 redis 模式


# ============================================================
#  测试用例
# ============================================================

async def test_basic_acquire_release():
    """1. 基本: async with 能获取和释放锁"""
    lock = main._get_lock("test:basic")
    async with lock:
        # 持有锁期间, owned() 应为 True
        assert lock._lock.owned(), "锁持有期间 owned() 应为 True"
    # 退出后 owned() 应为 False
    assert not lock._lock.owned(), "退出后 owned() 应为 False"
    print("[1.基本] async with 获取/释放 OK")


async def test_mutual_exclusion():
    """2. 互斥: 并发时仅一个进入临界区"""
    in_critical = 0
    max_concurrent = 0

    async def worker():
        nonlocal in_critical, max_concurrent
        lock = main._get_lock("test:mutex")
        async with lock:
            in_critical += 1
            max_concurrent = max(max_concurrent, in_critical)
            await asyncio.sleep(0.05)  # 模拟临界区工作
            in_critical -= 1

    # 5 个协程并发
    await asyncio.gather(*[worker() for _ in range(5)])
    assert max_concurrent == 1, f"互斥失败: 最大并发 {max_concurrent} != 1"
    print(f"[2.互斥] 5 并发, 最大临界区并发 {max_concurrent} == 1 OK")


async def test_release_then_acquire():
    """3. 释放后其他能获取"""
    lock1 = main._get_lock("test:chain")
    async with lock1:
        pass  # 获取后立即释放

    # 锁已释放, 应能再次获取
    lock2 = main._get_lock("test:chain")
    async with lock2:
        assert lock2._lock.owned(), "第二次获取应成功"
    print("[3.释放后获取] 锁释放后可再次获取 OK")


async def test_different_keys_no_mutex():
    """4. 不同锁键不互斥"""
    lock_a = main._get_lock("test:keyA")
    lock_b = main._get_lock("test:keyB")

    # 同时持有两个不同键的锁
    async with lock_a, lock_b:
        assert lock_a._lock.owned(), "keyA 锁应持有"
        assert lock_b._lock.owned(), "keyB 锁应持有"
    print("[4.不同键] 不同锁键不互斥 OK")


async def test_exception_releases_lock():
    """5. 异常时锁自动释放"""
    lock = main._get_lock("test:exc")
    try:
        async with lock:
            assert lock._lock.owned(), "持有期间 owned() 应为 True"
            raise ValueError("模拟异常")
    except ValueError:
        pass

    # 异常后锁应已释放
    assert not lock._lock.owned(), "异常后锁应自动释放"
    # 能再次获取
    async with main._get_lock("test:exc"):
        pass
    print("[5.异常释放] 异常时锁自动释放 OK")


async def test_concurrent_inventory_simulation():
    """6. 模拟库存场景: 100 并发扣减, 无超卖(用共享变量模拟 Redis 存储)"""
    inventory = {"stock": 50}

    async def deduct_one():
        lock = main._get_lock("stock:ZX42")
        async with lock:
            if inventory["stock"] > 0:
                inventory["stock"] -= 1
                return True
            return False

    results = await asyncio.gather(*[deduct_one() for _ in range(100)])
    success = sum(1 for r in results if r)
    final = inventory["stock"]
    assert success == 50, f"超卖: 成功 {success} > 50"
    assert final == 0, f"库存不对: {final} != 0"
    print(f"[6.库存模拟] 100 并发扣减, 成功 {success}, 最终库存 {final} OK 无超卖")


async def test_concurrent_wallet_simulation():
    """7. 模拟钱包场景: 100 并发充值, 无 lost-update"""
    wallet = {"balance": 0}

    async def add_100():
        lock = main._get_lock("agent:1")
        async with lock:
            wallet["balance"] += 100

    await asyncio.gather(*[add_100() for _ in range(100)])
    final = wallet["balance"]
    assert final == 10000, f"lost-update: wallet={final} != 10000"
    print(f"[7.钱包模拟] 100 并发充 100, 最终 wallet={final} OK 无丢失更新")


async def test_lock_key_format():
    """8. 验证锁键格式: lock:{原始key}"""
    lock = main._get_lock("stock:ZX42-2026")
    assert lock.key == "stock:ZX42-2026", f"key 不匹配: {lock.key}"
    # _lock 在 __aenter__ 时才创建, 需进入 async with 后检查
    async with lock:
        assert lock._lock.name == "lock:stock:ZX42-2026", f"Redis key 不匹配: {lock._lock.name}"
    print(f"[8.锁键格式] key={lock.key} redis_key={lock._lock.name} OK")


async def test_redis_mode_vs_asyncio_mode():
    """9. 验证双模式: LOCK_MODE=redis 返回 _RedisLockWrapper"""
    # 当前已强制 redis 模式
    lock = main._get_lock("test:mode")
    assert isinstance(lock, main._RedisLockWrapper), f"redis 模式应返回 _RedisLockWrapper, 实际 {type(lock)}"
    print(f"[9.双模式] LOCK_MODE=redis 返回 {type(lock).__name__} OK")

    # 切回 asyncio 模式
    main.LOCK_MODE = "asyncio"
    lock2 = main._get_lock("test:mode2")
    assert isinstance(lock2, asyncio.Lock), f"asyncio 模式应返回 asyncio.Lock, 实际 {type(lock2)}"
    print(f"[9.双模式] LOCK_MODE=asyncio 返回 {type(lock2).__name__} OK")
    # 恢复 redis 模式
    main.LOCK_MODE = "redis"


# ============================================================
#  主入口
# ============================================================

async def run_tests():
    print("=" * 72)
    print("  Redis Lock 单进程逻辑验证(用 FakeLock 模拟 redis-py Lock)")
    print("  验证 _RedisLockWrapper 的 acquire/release/互斥/异常释放逻辑")
    print("=" * 72)
    print()

    await test_basic_acquire_release()
    await test_mutual_exclusion()
    await test_release_then_acquire()
    await test_different_keys_no_mutex()
    await test_exception_releases_lock()
    await test_concurrent_inventory_simulation()
    await test_concurrent_wallet_simulation()
    await test_lock_key_format()
    await test_redis_mode_vs_asyncio_mode()

    print()
    print("=" * 72)
    print("  全部通过 OK  _RedisLockWrapper 逻辑正确")
    print("  注: FakeLock 模拟验证适配层逻辑, 跨进程需真 Redis + multiworker 探针")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(run_tests())
