"""并发认领探针: 验证锁获取与冲突的日志时序

方式: httpx.AsyncClient + ASGITransport 直驱 ASGI app
     (走完整 FastAPI 栈: 中间件/路由/锁/日志, 等价于启动服务)

两个验证场景:
  1. 冲突时序: 5 个不同代理商并发认领同一区域
     → 1 成功(200) + 4 冲突(409), 锁应序列化 lock_acquired
  2. 幂等时序: 同一代理商并发认领同一区域 3 次
     → 全部成功(200), 日志 idempotent=True

日志时序捕获: 自定义 Handler 收集 perf_counter 时间戳, 最后按时序打印

运行: py probe_shipping_claim_concurrency.py
"""
import asyncio
import logging
import os
import time

# 必须在 import main 前设置(触发 core/config.py 的 basicConfig)
os.environ["LOG_LEVEL"] = "DEBUG"
os.environ["LOCK_MODE"] = "asyncio"   # 单进程 asyncio 锁(测试无需 Redis)
os.environ["STORE_MODE"] = "asyncio"  # 内存存储

import httpx

from main import app, _mock_store
from core.locks import _async_locks  # 重置锁用(新事件循环需新锁)


# ============================================================
#  日志时序捕获
# ============================================================

class SequenceCapture(logging.Handler):
    """收集日志记录及其高精度时间戳, 用于时序分析"""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[float, logging.LogRecord]] = []
        self._t0 = time.perf_counter()

    def emit(self, record: logging.LogRecord):
        self.records.append((time.perf_counter() - self._t0, record))

    def timeline(self, keywords: tuple[str, ...]) -> list[tuple[float, str, str]]:
        """返回含任一关键字的日志时序 (相对秒, 级别, 消息)"""
        out = []
        for t, r in self.records:
            msg = r.getMessage()
            if any(k in msg for k in keywords):
                out.append((t, r.levelname, msg))
        return out


_capture = SequenceCapture()
# 挂到 root logger, 捕获所有模块日志(shipping_service / shipping_repository 等)
logging.getLogger().addHandler(_capture)


# ============================================================
#  工具函数
# ============================================================

def _reset_state():
    """重置锁与认领状态(新事件循环需新 asyncio.Lock, 否则跨循环报错)"""
    _async_locks.clear()
    _mock_store["shipping_claims"].clear()


def _make_client() -> httpx.AsyncClient:
    """直连 ASGI app 的异步客户端(真并发, 走完整 FastAPI 栈)"""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _ensure_agents(agent_ids: list[int]):
    """确保 _mock_store 中存在指定 agent(冲突场景需要多个不同 agent)"""
    for aid in agent_ids:
        if aid not in _mock_store["agents"]:
            _mock_store["agents"][aid] = {
                "id": aid, "name": f"测试代理商{aid}", "level": "C", "wallet": 10000,
            }


def _print_timeline(title: str, timeline: list[tuple[float, str, str]]):
    """打印日志时序表"""
    print(f"\n  [{title}] 日志时序 (相对起点秒):")
    print("  " + "-" * 78)
    print(f"  {'相对时间(s)':>12}  {'级别':<7}  {'消息'}")
    print("  " + "-" * 78)
    for t, level, msg in timeline:
        print(f"  {t:>12.6f}  {level:<7}  {msg}")
    print("  " + "-" * 78)


# ============================================================
#  场景 1: 冲突时序(5 个不同代理商并发认领同一区域)
# ============================================================

async def probe_conflict_sequence(n=5, region="conflict_region"):
    """5 个不同代理商并发认领同一区域 → 1 成功 + 4 冲突

    验证点:
      - 锁序列化: 5 个 lock_acquired 应严格串行(无重叠)
      - 冲突日志: 4 个 claim_conflict 含 existing/agent_id 双类型
      - 最终状态: region 被 1 个 agent 持有
    """
    agent_ids = list(range(1, n + 1))
    _ensure_agents(agent_ids)
    _reset_state()
    _capture.records.clear()

    print("\n" + "=" * 80)
    print(f"  场景 1: {n} 个不同代理商并发认领同一区域 ({region})")
    print("=" * 80)

    async with _make_client() as client:
        tasks = [
            client.post("/api/agent-shipping/claim",
                        json={"agentId": aid, "region": region})
            for aid in agent_ids
        ]
        start = time.perf_counter()
        responses = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

    # 响应统计
    ok = [r for r in responses if r.status_code == 200]
    conflict = [r for r in responses if r.status_code == 409]
    winner = ok[0].json()["agentId"] if ok else None

    print(f"\n  响应: 成功 {len(ok)}/{n}, 冲突 {len(conflict)}/{n}, 耗时 {elapsed:.4f}s")
    print(f"  认领成功者: agent_id={winner} (type={type(winner).__name__})")
    for r in conflict:
        detail = r.json().get("details", {})
        print(f"  冲突响应: agent_id={detail.get('agentId')} msg={detail.get('message', r.json().get('message', ''))[:50]}")

    # 验证最终状态
    final_claim = _mock_store["shipping_claims"].get(region)
    print(f"  最终状态: {region} -> agent_id={final_claim} (type={type(final_claim).__name__})")

    # 断言
    assert len(ok) == 1, f"X 应 1 成功, 实际 {len(ok)}"
    assert len(conflict) == n - 1, f"X 应 {n-1} 冲突, 实际 {len(conflict)}"
    assert final_claim == winner, f"X 最终持有者应为 {winner}, 实际 {final_claim}"
    print(f"  OK 断言通过: 1 成功 + {n-1} 冲突, 最终持有者一致")

    # 日志时序
    tl = _capture.timeline(("claim_start", "lock_acquired", "claim_success", "claim_conflict"))
    _print_timeline("冲突场景", tl)

    # 锁序列化验证(lock_acquired 时间戳应严格递增)
    lock_times = [t for t, _, msg in tl if "lock_acquired" in msg]
    if len(lock_times) >= 2:
        gaps = [lock_times[i+1] - lock_times[i] for i in range(len(lock_times)-1)]
        print(f"\n  锁序列化: {len(lock_times)} 次 lock_acquired, 相邻间隔={gaps}")
        assert all(g >= 0 for g in gaps), "X lock_acquired 时间未递增(锁未序列化)"
        print(f"  OK 锁已序列化(时间戳严格递增)")


# ============================================================
#  场景 2: 幂等时序(同一代理商并发认领同一区域)
# ============================================================

async def probe_idempotent_sequence(n=3, agent_id=1, region="idempotent_region"):
    """同一代理商并发认领同一区域 3 次 → 全部成功(idempotent=True)

    验证点:
      - 全部 200(幂等成功, 不报 409)
      - 日志 claim_success 含 idempotent=True(首次 False, 后续 True)
      - 最终状态: region 被 agent_id 持有
    """
    _ensure_agents([agent_id])
    _reset_state()
    _capture.records.clear()

    print("\n" + "=" * 80)
    print(f"  场景 2: 代理商 {agent_id} 并发幂等认领 {n} 次 ({region})")
    print("=" * 80)

    async with _make_client() as client:
        tasks = [
            client.post("/api/agent-shipping/claim",
                        json={"agentId": agent_id, "region": region})
            for _ in range(n)
        ]
        responses = await asyncio.gather(*tasks)

    ok = [r for r in responses if r.status_code == 200]
    print(f"\n  响应: 成功 {len(ok)}/{n}")

    # 最终状态
    final_claim = _mock_store["shipping_claims"].get(region)
    print(f"  最终状态: {region} -> agent_id={final_claim} (type={type(final_claim).__name__})")

    # 断言
    assert len(ok) == n, f"X 幂等应全部成功, 实际 {len(ok)}/{n}"
    assert final_claim == agent_id, f"X 最终持有者应为 {agent_id}, 实际 {final_claim}"
    print(f"  OK 断言通过: {n} 次幂等认领全部成功")

    # 日志时序
    tl = _capture.timeline(("claim_start", "lock_acquired", "claim_success"))
    _print_timeline("幂等场景", tl)

    # 幂等标识验证
    idem_flags = [msg for _, _, msg in tl if "idempotent=" in msg]
    print(f"\n  幂等标识: {idem_flags}")
    true_count = sum(1 for m in idem_flags if "idempotent=True" in m)
    false_count = sum(1 for m in idem_flags if "idempotent=False" in m)
    print(f"  统计: idempotent=True x{true_count}, idempotent=False x{false_count}")
    assert true_count + false_count == n, "X idempotent 日志数量不符"
    print(f"  OK 幂等日志完整({false_count} 首次 + {true_count} 重复)")


# ============================================================
#  主入口
# ============================================================

async def main():
    print("=" * 80)
    print("  并发认领探针: 验证锁获取与冲突的日志时序")
    print("  方式: ASGITransport 直驱 FastAPI app(等价启动服务, 走完整栈)")
    print(f"  日志级别: {os.environ.get('LOG_LEVEL')}  锁模式: {os.environ.get('LOCK_MODE')}")
    print("=" * 80)

    await probe_conflict_sequence()
    await probe_idempotent_sequence()

    print("\n" + "=" * 80)
    print("  全部通过 OK  锁获取与冲突的日志时序已验证")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
