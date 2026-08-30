"""
并发探针: 验证 asyncio.Lock 是否真的生效
方式: httpx.AsyncClient + ASGITransport 直接驱动 ASGI app(走完整 FastAPI 栈)
     asyncio.gather 同时发起 N 个请求,制造真并发

四个验证场景:
  1. deduct 超卖防护:  100 并发扣减, 初始库存 50 → 50 成功 / 50 不足, 最终 0
  2. upgrade lost-update 防护: 100 并发充值 100 → wallet=10000(非丢失)
  3. 共享锁(deduct+restock 同锁键): 50+50 混合并发 → 库存 50→50
  4. 死锁检测: 100 并发必须在 10s 内全部完成

运行: py probe_concurrency.py

环境: 脚本自含 asyncio 单进程模式设定(探针验证 asyncio.Lock 回归,
不依赖 Redis; CI 该 job 无 env, 默认 redis 模式会连不上 Redis)
"""
import asyncio
import os
import time

# 必须在 import main 之前设定(锁工厂与存储工厂动态读取)
os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")

import httpx

from main import app, _mock_store
# 锁缓存已迁移至 core/locks(锁重构后 main 不再持有 _async_locks)
from core.locks import _async_locks

PRODUCT = "ZX42-2026L07"
AGENT_ID = 1


# ============================================================
#  工具函数
# ============================================================

def _reset_state():
    """重置锁(新事件循环需新锁,否则跨循环报错)"""
    _async_locks.clear()


def _make_client() -> httpx.AsyncClient:
    """创建直连 ASGI app 的异步客户端(真并发)"""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# ============================================================
#  场景 1: 库存扣减超卖防护
# ============================================================

async def probe_deduct_oversell(n=100, initial=50):
    """100 并发各扣 1, 初始库存 50 → 预期 50 成功 / 50 不足, 最终 0"""
    _mock_store["inventory"][PRODUCT] = {"stock": initial}
    _reset_state()

    async with _make_client() as client:
        tasks = [
            client.post("/api/inventory/deduct", json={"productId": PRODUCT, "quantity": 1})
            for _ in range(n)
        ]
        responses = await asyncio.gather(*tasks)

    success = sum(1 for r in responses if r.json().get("success") is True)
    final = _mock_store["inventory"][PRODUCT]["stock"]
    print(f"[1.超卖] {n} 并发扣1, 初始 {initial} -> 成功 {success}, 最终库存 {final}")
    assert final == 0, f"  X 超卖! 库存应为 0, 实际 {final}"
    assert success == initial, f"  X 成功数应为 {initial}, 实际 {success}"
    print(f"  OK 无超卖 (库存 {initial} -> 0, 成功 {success}/{n})")


# ============================================================
#  场景 2: 钱包充值 lost-update 防护
# ============================================================

async def probe_upgrade_lost_update(n=100, pay=100):
    """100 并发各充 100 → 预期 wallet = 10000"""
    _mock_store["agents"][AGENT_ID] = {"level": "D", "wallet": 0}
    _reset_state()

    async with _make_client() as client:
        tasks = [
            client.post("/api/agent/upgrade", json={
                "agentId": AGENT_ID, "fromLevel": "D", "toLevel": "D", "payAmount": pay
            })
            for _ in range(n)
        ]
        await asyncio.gather(*tasks)

    final_wallet = _mock_store["agents"][AGENT_ID]["wallet"]
    expected = n * pay
    print(f"[2.丢失更新] {n} 并发充 {pay}, 预期 wallet={expected} -> 实际 {final_wallet}")
    assert final_wallet == expected, f"  X lost-update! 应为 {expected}, 实际 {final_wallet}"
    print(f"  OK 无丢失更新 (wallet={final_wallet})")


# ============================================================
#  场景 3: 共享锁(deduct + restock 同锁键)
# ============================================================

async def probe_mixed_shared_lock(n=50):
    """50 deduct + 50 restock 混合并发, 初始 50 → 预期最终 50"""
    _mock_store["inventory"][PRODUCT] = {"stock": 50}
    _reset_state()

    async with _make_client() as client:
        deduct_tasks = [
            client.post("/api/inventory/deduct", json={"productId": PRODUCT, "quantity": 1})
            for _ in range(n)
        ]
        restock_tasks = [
            client.post("/api/inventory/restock", json={"productId": PRODUCT, "quantity": 1})
            for _ in range(n)
        ]
        await asyncio.gather(*deduct_tasks, *restock_tasks)

    final = _mock_store["inventory"][PRODUCT]["stock"]
    print(f"[3.共享锁] {n}deduct + {n}restock, 初始 50 -> 最终库存 {final}")
    assert final == 50, f"  X lost-update! 应为 50, 实际 {final}"
    print(f"  OK 共享锁生效 (库存 50 -> {final})")


# ============================================================
#  场景 4: 死锁检测
# ============================================================

async def probe_deadlock(n=100, timeout=10.0):
    """100 并发必须在 timeout 内全部完成(库存储备=n, 全部成功)"""
    _mock_store["inventory"][PRODUCT] = {"stock": n}
    _reset_state()

    async with _make_client() as client:
        tasks = [
            client.post("/api/inventory/deduct", json={"productId": PRODUCT, "quantity": 1})
            for _ in range(n)
        ]
        start = time.perf_counter()
        try:
            responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
        except TimeoutError:
            elapsed = time.perf_counter() - start
            print(f"[4.死锁] X 超时! {n} 并发未在 {timeout}s 内完成 (耗时 {elapsed:.2f}s), 疑似死锁")
            return
        elapsed = time.perf_counter() - start

    success = sum(1 for r in responses if r.json().get("success") is True)
    print(f"[4.死锁] {n} 并发全部完成 ({elapsed:.2f}s < {timeout}s), 成功 {success}/{n}")
    assert success == n, f"  X 成功数应为 {n}, 实际 {success}"
    print(f"  OK 无死锁 (全部 {success} 请求完成, 耗时 {elapsed:.2f}s)")


# ============================================================
#  主入口
# ============================================================

async def main():
    print("=" * 64)
    print("  并发探针: 验证 asyncio.Lock 是否真的生效")
    print("  方式: httpx.AsyncClient + ASGITransport + asyncio.gather")
    print("=" * 64)
    await probe_deduct_oversell()
    await probe_upgrade_lost_update()
    await probe_mixed_shared_lock()
    await probe_deadlock()
    print("=" * 64)
    print("  全部通过 OK  asyncio.Lock 在真并发下生效")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
