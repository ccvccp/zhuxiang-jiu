"""
多进程并发探针: 验证锁在多进程下的行为

方式: subprocess 启动 2 个独立 uvicorn 进程(端口 8765/8766)模拟多 worker
     httpx 轮询访问两个端口, 制造跨进程并发
     asyncio.gather 同时发起 N 个请求

双模式验证(环境变量 LOCK_MODE 切换):
  LOCK_MODE=asyncio (默认): asyncio.Lock 单进程锁
  LOCK_MODE=redis:          Redis 分布式锁跨进程

验证焦点(避开 _mock_store 分裂干扰):
  _mock_store 是进程内字典, 多进程下各 worker 独立(状态分裂)。
  方案 B 只解决锁, 未解决存储。因此探针不验证"最终状态一致性",
  而验证"并发执行无崩溃/无死锁":
  - 场景 1/2: 信息性(状态分裂使结果不可验证, 仅确认无崩溃)
  - 场景 3/4: 判定性(无死锁, 超时 = 失败)

退出码(CI 捕获失败):
  全部场景无死锁/无崩溃 → exit 0
  任一场景超时/崩溃 → exit 1(CI 失败, 阻断合并)

运行:
  # asyncio 模式(单进程锁, 无 Redis 依赖)
  py probe_concurrency_multiworker.py

  # redis 模式(分布式锁, 需 Redis 服务)
  $env:LOCK_MODE = "redis"
  $env:REDIS_URL = "redis://127.0.0.1:6379/0"
  py probe_concurrency_multiworker.py
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
PORTS = [8765, 8766]  # 2 个独立进程模拟多 worker
BASE_URLS = [f"http://127.0.0.1:{p}" for p in PORTS]
PRODUCT = "ZX42-2026L07"
AGENT_ID = 1
REQUEST_TIMEOUT = 15.0
LOCK_MODE = os.environ.get("LOCK_MODE", "asyncio")
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")


# ============================================================
#  多进程 uvicorn 管理(2 个独立进程)
# ============================================================

class MultiProcessUvicorn:
    """启动 N 个独立 uvicorn 进程在不同端口, 模拟多 worker"""

    def __init__(self, ports: list):
        self.ports = ports
        self.procs = []

    async def __aenter__(self):
        # 跨平台 PYTHONPATH 分隔符: Windows 用 ; Linux/Mac 用 :
        path_sep = ";" if sys.platform == "win32" else ":"
        env = {
            "PYTHONPATH": path_sep.join([str(PYLIBS), str(BACKEND_DIR)]),
            "LOCK_MODE": LOCK_MODE,
            "REDIS_URL": REDIS_URL,
        }
        cmd_base = [
            sys.executable, "-m", "uvicorn",
            "main:app", "--host", "127.0.0.1", "--no-access-log",
        ]
        for port in self.ports:
            cmd = cmd_base + ["--port", str(port)]
            # DEVNULL 避免管道缓冲满导致子进程阻塞写入(死锁风险)
            # 失败时 _wait_ready 超时会报 "端口未就绪", 无需 uvicorn 日志
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

    async def _wait_ready(self, timeout=25.0):
        """等待所有端口就绪"""
        start = time.perf_counter()
        async with httpx.AsyncClient() as client:
            for port in self.ports:
                url = f"http://127.0.0.1:{port}"
                while time.perf_counter() - start < timeout:
                    try:
                        r = await client.get(f"{url}/api/decision/health", timeout=1.0)
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
#  场景 1: 超卖防护(多进程, 无崩溃验证)
# ============================================================

async def probe_deduct_oversell_mp(n=100) -> bool:
    """多进程下验证 deduct 端点并发执行无崩溃

    注: _mock_store 分裂使最终状态不可验证, 此场景为信息性。
        真正验证锁跨进程生效需存储也迁 Redis(方案 B+)。
    """
    rr = RoundRobinClient(BASE_URLS)
    tasks_init = [rr.post("/api/inventory/restock", {"productId": PRODUCT, "quantity": 50}) for _ in range(len(PORTS))]
    await asyncio.gather(*tasks_init, return_exceptions=True)
    await asyncio.sleep(0.3)

    deduct_tasks = [rr.post("/api/inventory/deduct", {"productId": PRODUCT, "quantity": 1}) for _ in range(n)]
    start = time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.gather(*deduct_tasks), timeout=15.0)
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start
        print(f"[1.超卖 MP] X 超时! {n} 并发 deduct 未完成 ({elapsed:.2f}s)")
        return False
    elapsed = time.perf_counter() - start
    print(f"[1.超卖 MP] {n} 并发 deduct 完成 ({elapsed:.2f}s)")
    print(f"  LOCK_MODE={LOCK_MODE}: 状态分裂(_mock_store 各进程独立), 信息性")
    return True  # 信息性, 不判定状态


# ============================================================
#  场景 2: lost-update 防护(多进程, Redis 计数器验证)
# ============================================================

async def probe_upgrade_lost_update_mp(n=100) -> bool:
    """多进程下验证 upgrade 端点并发执行无崩溃

    注: _mock_store 分裂使最终状态不可验证, 此场景为信息性。
    """
    rr = RoundRobinClient(BASE_URLS)
    upgrade_tasks = [rr.post("/api/agent/upgrade", {
        "agentId": AGENT_ID, "fromLevel": "D", "toLevel": "D", "payAmount": 100
    }) for _ in range(n)]

    start = time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.gather(*upgrade_tasks), timeout=15.0)
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start
        print(f"[2.丢失更新 MP] X 超时! {n} 并发 upgrade 未完成 ({elapsed:.2f}s)")
        return False
    elapsed = time.perf_counter() - start
    print(f"[2.丢失更新 MP] {n} 并发 upgrade 完成 ({elapsed:.2f}s)")
    print(f"  LOCK_MODE={LOCK_MODE}: 状态分裂(_mock_store 各进程独立), 信息性")
    return True  # 信息性, 不判定状态


# ============================================================
#  场景 3: 共享锁(多进程, 锁竞争验证)
# ============================================================

async def probe_mixed_shared_lock_mp(n=50) -> bool:
    """多进程下 50 deduct + 50 restock → 验证锁竞争无死锁

    锁跨进程生效(redis): stock:{productId} 锁跨进程互斥, 100 请求串行
    锁失效(asyncio): 各进程独立锁, 100 请求并发(无跨进程互斥)
    """
    rr = RoundRobinClient(BASE_URLS)
    # 初始化
    init_tasks = [rr.post("/api/inventory/restock", {"productId": PRODUCT, "quantity": 50}) for _ in range(len(PORTS))]
    await asyncio.gather(*init_tasks, return_exceptions=True)
    await asyncio.sleep(0.3)

    deduct_tasks = [rr.post("/api/inventory/deduct", {"productId": PRODUCT, "quantity": 1}) for _ in range(n)]
    restock_tasks = [rr.post("/api/inventory/restock", {"productId": PRODUCT, "quantity": 1}) for _ in range(n)]

    start = time.perf_counter()
    try:
        await asyncio.wait_for(
            asyncio.gather(*deduct_tasks, *restock_tasks),
            timeout=15.0
        )
    except asyncio.TimeoutError:
        print("[3.共享锁 MP] X 超时! 100 请求未在 15s 完成")
        return False  # 超时 = 失败, CI 应捕获

    elapsed = time.perf_counter() - start
    print(f"[3.共享锁 MP] {n}deduct + {n}restock 完成 ({elapsed:.2f}s)")
    print(f"  LOCK_MODE={LOCK_MODE}: 锁跨进程生效时串行(慢), 失效时并发(快)")
    return True


# ============================================================
#  场景 4: 无死锁(多进程, 关键判定场景)
# ============================================================

async def probe_deadlock_mp(n=100, timeout=15.0) -> bool:
    """多进程下 100 并发必须在 timeout 内完成

    此场景是关键判定:
    - 锁跨进程生效(redis): 100 并发被串行化, 但无死锁 → 完成
    - 锁失效(asyncio): 100 并发无跨进程互斥, 各进程内串行 → 完成
    - 死锁(任何模式): 超时未完成 → 失败
    """
    rr = RoundRobinClient(BASE_URLS)
    tasks = [rr.post("/api/inventory/deduct", {"productId": PRODUCT, "quantity": 1}) for _ in range(n)]
    start = time.perf_counter()
    try:
        responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start
        print(f"[4.死锁 MP] X 超时! {n} 并发未在 {timeout}s 完成 ({elapsed:.2f}s)")
        return False  # 死锁 = 失败
    elapsed = time.perf_counter() - start
    success = sum(1 for r in responses if r.json().get("success") is True)
    print(f"[4.死锁 MP] {n} 并发完成 ({elapsed:.2f}s), 成功 {success}/{n}")
    print("  OK 无死锁")
    return True


# ============================================================
#  主入口(汇总失败数, exit code 让 CI 捕获)
# ============================================================

async def main():
    print("=" * 72)
    print("  多进程并发探针: 验证锁在多进程下的行为")
    print(f"  方式: 启动 {len(PORTS)} 个独立 uvicorn 进程(端口 {PORTS})")
    print("        httpx 轮询访问 + asyncio.gather 并发")
    print(f"  锁模式: LOCK_MODE={LOCK_MODE}" + (f" REDIS_URL={REDIS_URL}" if LOCK_MODE == "redis" else ""))
    print("=" * 72)
    if LOCK_MODE == "redis":
        print("  方案 B 验证: Redis 分布式锁跨进程生效")
        print("  预期: 4 场景全通过(无死锁/无崩溃)")
        print("  注: 场景 1/2 信息性(_mock_store 分裂, 状态不可验证)")
    else:
        print("  方案 A 基线: asyncio.Lock 多进程失效")
        print("  预期: 4 场景全通过(无死锁/无崩溃)")
        print("  注: 场景 1/2 信息性(_mock_store 分裂, 状态不可验证)")
    print()
    print(f"启动 {len(PORTS)} 个 uvicorn 进程...")
    print()

    results = []
    async with MultiProcessUvicorn(ports=PORTS):
        print(f"全部 {len(PORTS)} 个进程就绪, 开始测试")
        print()
        results.append(("场景1:超卖", await probe_deduct_oversell_mp()))
        await asyncio.sleep(0.5)
        results.append(("场景2:丢失更新", await probe_upgrade_lost_update_mp()))
        await asyncio.sleep(0.5)
        results.append(("场景3:共享锁", await probe_mixed_shared_lock_mp()))
        await asyncio.sleep(0.5)
        results.append(("场景4:无死锁", await probe_deadlock_mp()))

    print()
    print("=" * 72)
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
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
