"""多进程并发认领探针: 验证 Redis 分布式锁的跨进程互斥与状态一致性

方式: subprocess 启动 2 个独立 uvicorn 进程(端口 8765/8766)模拟多 worker
     httpx 轮询访问两个端口, 制造跨进程并发
     asyncio.gather 同时发起 N 个请求

与 probe_concurrency_multiworker.py 的关键差异:
  - 使用 STORE_MODE=redis(Redis 共享存储), 状态跨进程一致
  - 因此可验证"最终状态一致性", 而非仅"无死锁"
  - 验证 shipping:claim:{region} Redis 分布式锁的跨进程互斥

两个验证场景:
  1. 跨进程冲突: 5 个不同代理商并发认领同一区域(轮询到 2 个进程)
     → 1 成功(200) + 4 冲突(409), Redis 中 region 被 1 个 agent 持有
  2. 跨进程幂等: 同一代理商并发认领同一区域 3 次
     → 全部成功(200), Redis 中 region 被该 agent 持有

退出码(CI 捕获失败):
  全部场景通过 + 状态一致 → exit 0
  任一场景失败/状态不一致 → exit 1

运行:
  # Redis 模式(需 Redis 服务)
  $env:LOCK_MODE = "redis"
  $env:STORE_MODE = "redis"
  $env:REDIS_URL = "redis://127.0.0.1:6379/0"
  py probe_shipping_claim_concurrency_multiworker.py
"""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

# ============================================================
#  配置
# ============================================================

BACKEND_DIR = Path(__file__).parent.resolve()
PYLIBS = BACKEND_DIR.parent / "pylibs"
DEPS_DIR = BACKEND_DIR / ".deps"
PORTS = [8765, 8766]  # 2 个独立进程模拟多 worker
BASE_URLS = [f"http://127.0.0.1:{p}" for p in PORTS]
CLAIM_ENDPOINT = "/api/agent-shipping/claim"
HEALTH_ENDPOINT = "/api/decision/health"
REQUEST_TIMEOUT = 15.0
LOCK_MODE = os.environ.get("LOCK_MODE", "redis")
STORE_MODE = os.environ.get("STORE_MODE", "redis")
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
KEY_PREFIX = "zhuxiang:"  # 必须与 repositories/backend.py 一致

# 测试用 agent(冲突场景需要 5 个, seed_redis.py 只 seed 了 1/2)
TEST_AGENTS = {
    1: {"id": 1, "name": "泰安市级代理商", "level": "C", "wallet": 50000},
    2: {"id": 2, "name": "济南核心代理商", "level": "B", "wallet": 120000},
    3: {"id": 3, "name": "青岛区域代理商", "level": "B", "wallet": 80000},
    4: {"id": 4, "name": "烟台市级代理商", "level": "C", "wallet": 60000},
    5: {"id": 5, "name": "潍坊核心代理商", "level": "A", "wallet": 150000},
}


def _k(entity: str, *parts) -> str:
    """构造 Redis Key(复制自 repositories/backend.py, 避免循环导入)"""
    return KEY_PREFIX + entity + ":" + ":".join(str(p) for p in parts)


# ============================================================
#  Redis 预置数据(确保 5 个 agent 存在 + 清空认领记录)
# ============================================================

async def seed_test_data():
    """在 Redis 中预置测试 agent 并清空 shipping_claims(幂等)"""
    import redis.asyncio as redis

    client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()

        # 清空 shipping_claims(确保场景隔离)
        await client.delete(_k("shipping_claims"))

        # 预置 5 个 agent(若已存在则覆盖, 保证数据正确)
        for agent_id, data in TEST_AGENTS.items():
            await client.hset(_k("agent", agent_id), mapping={
                "id": data["id"],
                "name": data["name"],
                "level": data["level"],
                "wallet": data["wallet"],
            })

        # 验证
        claims_after = await client.hlen(_k("shipping_claims"))
        agents_count = len(await client.keys(_k("agent", "*")))
        print(f"[SEED] Redis 预置完成: agents={agents_count}, shipping_claims 清空(剩余 {claims_after} 条)")
        return True
    except Exception as e:
        print(f"[SEED ERROR] {e}")
        return False
    finally:
        await client.aclose()


async def verify_redis_state(region: str, expected_agent_id) -> bool:
    """验证 Redis 中 region 的认领状态(跨进程一致性)"""
    import redis.asyncio as redis

    client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        value = await client.hget(_k("shipping_claims"), region)
        # Redis 存储为 str, 归一化比较
        match = (value is not None and str(value) == str(expected_agent_id))
        print(f"[VERIFY] Redis shipping_claims[{region}] = {value!r} (type={type(value).__name__}), "
              f"期望 {expected_agent_id!r} → {'OK' if match else 'X 不一致'}")
        return match
    except Exception as e:
        print(f"[VERIFY ERROR] {e}")
        return False
    finally:
        await client.aclose()


# ============================================================
#  多进程 uvicorn 管理(2 个独立进程)
# ============================================================

class MultiProcessUvicorn:
    """启动 N 个独立 uvicorn 进程在不同端口, 模拟多 worker"""

    def __init__(self, ports: list):
        self.ports = ports
        self.procs = []

    async def __aenter__(self):
        # 跨平台 PYTHONPATH 分隔符
        path_sep = ";" if sys.platform == "win32" else ":"
        # 可选依赖目录(本地运行时使用 .deps 或 pylibs, CI 中全局安装无需)
        extra_paths = []
        for p in (DEPS_DIR, PYLIBS):
            if p.exists():
                extra_paths.append(str(p))
        pythonpath = path_sep.join(extra_paths + [str(BACKEND_DIR)])

        env = {
            "PYTHONPATH": pythonpath,
            "LOCK_MODE": LOCK_MODE,
            "STORE_MODE": STORE_MODE,
            "REDIS_URL": REDIS_URL,
            "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO"),
        }
        cmd_base = [
            sys.executable, "-m", "uvicorn",
            "main:app", "--host", "127.0.0.1", "--no-access-log",
        ]
        for port in self.ports:
            cmd = cmd_base + ["--port", str(port)]
            # DEVNULL 避免管道缓冲满导致子进程阻塞(死锁风险)
            proc = subprocess.Popen(
                cmd, cwd=str(BACKEND_DIR), env={**os.environ, **env},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.procs.append(proc)
        await self._wait_ready()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        self.procs.clear()
        return False

    async def _wait_ready(self, timeout=30.0):
        """等待所有端口就绪"""
        start = time.perf_counter()
        async with httpx.AsyncClient() as client:
            for port in self.ports:
                url = f"http://127.0.0.1:{port}"
                while time.perf_counter() - start < timeout:
                    try:
                        r = await client.get(f"{url}{HEALTH_ENDPOINT}", timeout=1.0)
                        if r.status_code == 200:
                            break
                    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
                        pass
                    await asyncio.sleep(0.3)
                else:
                    raise RuntimeError(f"端口 {port} 未在 {timeout}s 内就绪")
        await asyncio.sleep(0.5)  # 让所有 worker 完全就绪


# ============================================================
#  轮询客户端(模拟负载均衡到多 worker)
# ============================================================

class RoundRobinClient:
    """轮询访问多个 base_url, 模拟 uvicorn 的负载均衡"""

    def __init__(self, base_urls: list):
        self.base_urls = base_urls
        self._idx = 0

    def _next_url(self):
        url = self.base_urls[self._idx % len(self.base_urls)]
        self._idx += 1
        return url

    async def post(self, path: str, json: dict):
        url = self._next_url() + path
        async with httpx.AsyncClient() as client:
            return await client.post(url, json=json, timeout=REQUEST_TIMEOUT)


# ============================================================
#  场景 1: 跨进程冲突(5 个不同代理商并发认领同一区域)
# ============================================================

async def probe_cross_process_conflict(n=5, region="mp_conflict_region") -> bool:
    """多进程下 5 个不同代理商并发认领同一区域

    验证点(Redis 分布式锁跨进程互斥):
      - 1 成功(200) + 4 冲突(409)
      - Redis 中 region 被 1 个 agent 持有(状态一致性)
      - 无死锁(在 timeout 内完成)
    """
    rr = RoundRobinClient(BASE_URLS)
    agent_ids = list(range(1, n + 1))

    print(f"\n[场景1] {n} 个代理商并发认领 {region}(轮询 {len(PORTS)} 进程)")
    tasks = [
        rr.post(CLAIM_ENDPOINT, {"agentId": aid, "region": region})
        for aid in agent_ids
    ]

    start = time.perf_counter()
    try:
        responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15.0)
    except TimeoutError:
        elapsed = time.perf_counter() - start
        print(f"  X 超时! {n} 并发认领未完成 ({elapsed:.2f}s) → 死锁?")
        return False
    elapsed = time.perf_counter() - start

    ok = [r for r in responses if r.status_code == 200]
    conflict = [r for r in responses if r.status_code == 409]

    print(f"  响应: 成功 {len(ok)}/{n}, 冲突 {len(conflict)}/{n}, 耗时 {elapsed:.2f}s")
    if ok:
        winner = ok[0].json().get("agentId")
        print(f"  认领成功者: agent_id={winner}")

    # 断言 1: 1 成功 + 4 冲突
    if len(ok) != 1 or len(conflict) != n - 1:
        print(f"  X 断言失败: 应 1 成功 + {n-1} 冲突, 实际 {len(ok)} 成功 + {len(conflict)} 冲突")
        return False
    print(f"  OK 1 成功 + {n-1} 冲突(Redis 分布式锁跨进程互斥生效)")

    # 断言 2: Redis 状态一致性
    winner_id = ok[0].json().get("agentId")
    if not await verify_redis_state(region, winner_id):
        print("  X Redis 状态不一致: 跨进程数据分裂")
        return False
    print(f"  OK Redis 状态一致: {region} -> agent_id={winner_id}")

    return True


# ============================================================
#  场景 2: 跨进程幂等(同一代理商并发认领同一区域)
# ============================================================

async def probe_cross_process_idempotent(n=3, agent_id=1, region="mp_idempotent_region") -> bool:
    """多进程下同一代理商并发认领同一区域 3 次

    验证点(幂等 + 跨进程一致性):
      - 全部成功(200, 幂等, 不报 409)
      - Redis 中 region 被该 agent 持有
      - 无死锁
    """
    rr = RoundRobinClient(BASE_URLS)

    print(f"\n[场景2] 代理商 {agent_id} 并发幂等认领 {region} {n} 次(轮询 {len(PORTS)} 进程)")
    tasks = [
        rr.post(CLAIM_ENDPOINT, {"agentId": agent_id, "region": region})
        for _ in range(n)
    ]

    start = time.perf_counter()
    try:
        responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15.0)
    except TimeoutError:
        elapsed = time.perf_counter() - start
        print(f"  X 超时! {n} 并发幂等认领未完成 ({elapsed:.2f}s) → 死锁?")
        return False
    elapsed = time.perf_counter() - start

    ok = [r for r in responses if r.status_code == 200]
    conflict = [r for r in responses if r.status_code == 409]

    print(f"  响应: 成功 {len(ok)}/{n}, 冲突 {len(conflict)}/{n}, 耗时 {elapsed:.2f}s")

    # 断言 1: 全部成功(幂等)
    if len(ok) != n:
        print(f"  X 断言失败: 幂等应全部成功, 实际 {len(ok)}/{n}")
        return False
    print(f"  OK {n} 次幂等认领全部成功(跨进程幂等生效)")

    # 断言 2: Redis 状态一致性
    if not await verify_redis_state(region, agent_id):
        print("  X Redis 状态不一致: 跨进程数据分裂")
        return False
    print(f"  OK Redis 状态一致: {region} -> agent_id={agent_id}")

    return True


# ============================================================
#  主入口(汇总失败数, exit code 让 CI 捕获)
# ============================================================

async def main():
    print("=" * 72)
    print("  多进程并发认领探针: 验证 Redis 分布式锁的跨进程互斥")
    print(f"  方式: 启动 {len(PORTS)} 个独立 uvicorn 进程(端口 {PORTS})")
    print("        httpx 轮询访问 + asyncio.gather 并发")
    print(f"  锁模式: LOCK_MODE={LOCK_MODE}  存储模式: STORE_MODE={STORE_MODE}")
    print(f"  Redis: {REDIS_URL}")
    print("=" * 72)

    # 前置检查: 必须为 redis 模式
    if LOCK_MODE != "redis" or STORE_MODE != "redis":
        print(f"\n[ERROR] 此探针要求 LOCK_MODE=redis 且 STORE_MODE=redis, "
              f"当前 LOCK_MODE={LOCK_MODE} STORE_MODE={STORE_MODE}")
        print("  原因: 多进程状态一致性验证需 Redis 共享存储 + 分布式锁")
        sys.exit(1)

    # 步骤 1: 预置 Redis 数据
    print("\n[步骤1] 预置 Redis 测试数据")
    if not await seed_test_data():
        print("[ERROR] Redis 预置失败, 请确认 Redis 服务已启动")
        sys.exit(1)

    # 步骤 2: 启动多进程 + 运行场景
    print(f"\n[步骤2] 启动 {len(PORTS)} 个 uvicorn 进程...")
    results = []
    async with MultiProcessUvicorn(ports=PORTS):
        print(f"  全部 {len(PORTS)} 个进程就绪, 开始测试\n")

        # 场景 1: 跨进程冲突
        results.append(("场景1:跨进程冲突", await probe_cross_process_conflict()))
        await asyncio.sleep(0.5)

        # 重新清空 shipping_claims(场景隔离)
        import redis.asyncio as redis
        client = redis.from_url(REDIS_URL, decode_responses=True)
        await client.delete(_k("shipping_claims"))
        await client.aclose()

        # 场景 2: 跨进程幂等
        results.append(("场景2:跨进程幂等", await probe_cross_process_idempotent()))

    # 步骤 3: 汇总结果
    print("\n" + "=" * 72)
    print("  场景结果汇总:")
    failed = 0
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"    {name}: {status}")
        if not passed:
            failed += 1
    print("=" * 72)

    if failed > 0:
        print(f"  X {failed} 个场景失败, exit 1(CI 阻断)")
        sys.exit(1)
    else:
        print("  OK 全部场景通过, exit 0")
        print("  验证项: Redis 分布式锁 shipping:claim:{region} 跨进程互斥 + 状态一致性")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
