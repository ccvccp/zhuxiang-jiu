# Redis 持久化存储迁移方案(Scheme B+)

> 版本: v1.0 | 日期: 2026-08-20 | 作者: AI 决策筹划模块(29)
> 状态: 待评审 → 待实施

## 1. 背景与目标

### 1.1 当前状态

| 层 | 现状 | 问题 |
|----|------|------|
| 锁层 | `core/locks.py` 双模式锁工厂已实现(asyncio/redis),默认 `asyncio` | 默认模式仍为单进程锁,多 worker 下失效 |
| 存储层 | `repositories/store.py` 的 `_mock_store` 是进程内字典 | 多 worker 部署时各进程持有独立副本,**状态分裂** |
| 依赖 | `requirements.txt` 已含 `redis>=5.0.0` | ✅ 就绪 |
| 容器 | `docker-compose.yml` 已配置 Redis 7-alpine + appendonly 持久化 | ✅ 就绪 |
| 探针 | `probe_concurrency_multiworker.py` 已验证 Redis 锁跨进程有效 | ✅ 就绪 |

### 1.2 核心问题

方案 B(已完成)只解决了**锁**的跨进程互斥,未解决**存储**的跨进程一致性:

```
Worker A: 扣减库存 → _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 470 (本地副本)
Worker B: 扣减库存 → _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 490 (本地副本, 与 A 无关)
                                              ↑
                            即使 Redis 锁保证串行执行, 两个 worker 看到的仍是各自的内存副本
```

### 1.3 迁移目标

1. **锁默认模式**:`LOCK_MODE` 默认值从 `asyncio` 切换为 `redis`
2. **存储层持久化**:`_mock_store` 数据迁移到 Redis,多 worker 共享同一数据源
3. **兼容性保留**:内存模式(`LOCK_MODE=asyncio`)下 `_mock_store` 仍可用,现有 187 个测试零改动
4. **透明切换**:Repository 层根据 `LOCK_MODE` 自动选择内存后端或 Redis 后端,Service/Route 层无感知

## 2. 目标架构

```
┌─────────────────────────────────────────────────────────┐
│  routes/  (HTTP 路由层, 不变)                            │
├─────────────────────────────────────────────────────────┤
│  services/ (业务逻辑层, 不变)                            │
│    └─ 仅通过 Repository 接口访问数据                      │
├─────────────────────────────────────────────────────────┤
│  repositories/ (数据访问层, 本次改造重点)                │
│    ├─ BackendSelector: 根据 STORE_MODE 选择后端          │
│    ├─ InMemoryBackend:  现有 _mock_store 字典            │
│    └─ RedisBackend:     Redis 持久化后端(新增)          │
├─────────────────────────────────────────────────────────┤
│  core/locks.py (锁层, 仅改默认值)                       │
│    └─ LOCK_MODE 默认 asyncio → redis                    │
├─────────────────────────────────────────────────────────┤
│  Redis 7 (appendonly 持久化)                            │
└─────────────────────────────────────────────────────────┘
```

## 3. Redis 数据结构设计

### 3.1 Key 命名规范

所有 key 统一前缀 `zhuxiang:` 避免与其他服务冲突。

| 实体 | Redis 类型 | Key 格式 | 示例 |
|------|-----------|----------|------|
| 代理商 | Hash | `zhuxiang:agent:{id}` | `zhuxiang:agent:1` |
| 库存 | Hash | `zhuxiang:inventory:{productId}` | `zhuxiang:inventory:ZX42-2026L07` |
| 仓储库位 | Hash | `zhuxiang:warehouse:slots` | 单个 Hash,field=slot, value=productId |
| 入库日志 | List | `zhuxiang:warehouse:inbound_log` | RPUSH 追加 |
| 出库日志 | List | `zhuxiang:warehouse:outbound_log` | RPUSH 追加 |
| 订单 | List | `zhuxiang:orders` | RPUSH 追加(JSON) |
| 区域认领 | Hash | `zhuxiang:shipping_claims` | field=region, value=agentId |
| 序列号 | String | `zhuxiang:seq:order` | INCR 生成订单号 |

### 3.2 Hash 字段映射

**代理商** `zhuxiang:agent:{id}`:
```
HSET zhuxiang:agent:1 id 1 name "泰安市级代理商" level "C" wallet 50000
```

**库存** `zhuxiang:inventory:{productId}`:
```
HSET zhuxiang:inventory:ZX42-2026L07 stock 500 reserved 0
```

### 3.3 原子性保证

涉及 RMW(读-改-写)的操作必须在 Redis 锁内执行:

| 操作 | 锁键 | 原子命令 |
|------|------|---------|
| 库存扣减 | `lock:stock:{productId}` | `HGET` → 比较 → `HINCRBY stock -qty` |
| 库存回补 | `lock:stock:{productId}` | `HINCRBY stock qty` |
| 代理商升级 | `lock:agent:{agentId}` | `HSET level` + `HINCRBY wallet payAmount` |
| 区域认领 | `lock:shipping:{region}` | `HSETNX` (不存在才设置) |

> 锁键复用 `core/locks.py` 的 `get_lock()`,前缀 `lock:` 已在 `_RedisLockWrapper` 中硬编码。

## 4. Repository 层适配设计

### 4.1 后端选择器

新增 `repositories/backend.py`:

```python
"""存储后端选择器:根据 STORE_MODE 透明切换内存/Redis"""

import os
from repositories.store import _mock_store  # 内存后端单例

STORE_MODE = os.environ.get("STORE_MODE", os.environ.get("LOCK_MODE", "asyncio"))
# STORE_MODE 优先级: STORE_MODE > LOCK_MODE > asyncio
# 这样 LOCK_MODE=redis 时存储也自动切到 Redis, 单一开关

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
_redis_client = None


async def get_redis_client():
    """懒加载 Redis 连接(单例,与 locks.py 共享配置)"""
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as redis
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def is_redis_mode() -> bool:
    """当前是否为 Redis 存储模式"""
    return STORE_MODE == "redis"


def get_in_memory_store() -> dict:
    """获取内存后端(兼容 _mock_store 契约)"""
    return _mock_store
```

### 4.2 Repository 双模式适配

以 `InventoryRepository` 为例,其他 4 个 Repository 同模式:

```python
"""库存 Repository:双模式(内存/Redis)透明切换"""

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store


class InventoryRepository:
    async def get(self, product_id) -> dict | None:
        if is_redis_mode():
            return await self._redis_get(product_id)
        return self._mem_get(product_id)

    async def deduct(self, product_id, quantity: int) -> int:
        if is_redis_mode():
            return await self._redis_deduct(product_id, quantity)
        return self._mem_deduct(product_id, quantity)

    # ---------- 内存后端(原逻辑,保持不变) ----------
    def _mem_get(self, product_id):
        return get_in_memory_store()["inventory"].get(str(product_id))

    def _mem_deduct(self, product_id, quantity):
        # 原有 _mock_store 字典操作
        ...

    # ---------- Redis 后端 ----------
    async def _redis_get(self, product_id):
        client = await get_redis_client()
        data = await client.hgetall(f"zhuxiang:inventory:{product_id}")
        return {"stock": int(data["stock"]), "reserved": int(data["reserved"])} if data else None

    async def _redis_deduct(self, product_id, quantity):
        client = await get_redis_client()
        # HINCRBY 负数即扣减, 原子操作(但仍需锁保证 check-then-act)
        new_stock = await client.hincrby(
            f"zhuxiang:inventory:{product_id}", "stock", -quantity
        )
        return new_stock
```

### 4.3 重要:Repository 方法改为 async

**破坏性变更**:Repository 方法从同步改为 async。影响:
- `services/*.py` 调用处需加 `await`(已有 service 是 async,改动小)
- 测试直接修改 `_mock_store` 的模式不受影响(走内存后端,同步路径)

### 4.4 兼容层:_mock_store 在 Redis 模式下的行为

```python
# repositories/store.py 保持不变
_mock_store: dict = {...}  # 内存模式下单例

# 但在 Redis 模式下, Repository 不再读写 _mock_store
# 测试若在 Redis 模式下直接改 _mock_store, 不会影响 Redis 数据
# 因此测试需在 LOCK_MODE=asyncio 下运行(默认), 仅集成测试用 LOCK_MODE=redis
```

## 5. 锁默认模式切换

### 5.1 改动点

`core/locks.py` 第 17 行:
```python
# 改前
LOCK_MODE = os.environ.get("LOCK_MODE", "asyncio")

# 改后
LOCK_MODE = os.environ.get("LOCK_MODE", "redis")
```

### 5.2 影响分析

- **生产环境**(`docker-compose.yml` 已设 `LOCK_MODE=redis`):无变化
- **本地开发**(未设环境变量):默认切到 Redis,需本地启动 Redis 或 `LOCK_MODE=asyncio` 回退
- **单元测试**:pytest.ini 设置 `LOCK_MODE=asyncio` 环境变量(见第 7 节)

## 6. 数据初始化与迁移

### 6.1 Seed 脚本

新增 `scripts/seed_redis.py`:

```python
"""Redis 数据初始化脚本:写入初始 _mock_store 数据

运行: py scripts/seed_redis.py
环境: REDIS_URL=redis://127.0.0.1:6379/0
"""

import asyncio
import os
import redis.asyncio as redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

SEED_DATA = {
    "agents": {
        1: {"id": 1, "name": "泰安市级代理商", "level": "C", "wallet": 50000},
        2: {"id": 2, "name": "济南核心代理商", "level": "B", "wallet": 120000},
    },
    "inventory": {
        "ZX42-2026L07": {"stock": 500, "reserved": 0},
        "ZX42-2026L05": {"stock": 300, "reserved": 0},
    },
    "warehouse_slots": {"A1": "ZX42-2026L07", "A2": "ZX42-2026L05", "B1": None},
}


async def seed():
    client = redis.from_url(REDIS_URL, decode_responses=True)

    # 清空旧数据(仅 zhuxiang: 前缀, 不影响其他)
    async for key in client.scan_iter("zhuxiang:*"):
        await client.delete(key)

    # 代理商
    for aid, data in SEED_DATA["agents"].items():
        await client.hset(f"zhuxiang:agent:{aid}", mapping=data)

    # 库存
    for pid, data in SEED_DATA["inventory"].items():
        await client.hset(f"zhuxiang:inventory:{pid}", mapping=data)

    # 仓储库位(B1=None 用 HDEL 处理)
    await client.hset("zhuxiang:warehouse:slots", mapping=SEED_DATA["warehouse_slots"])
    await client.hdel("zhuxiang:warehouse:slots", "B1")  # None 值不存储

    print("✅ Redis 数据初始化完成")
    print(f"   agents: {len(SEED_DATA['agents'])} 条")
    print(f"   inventory: {len(SEED_DATA['inventory'])} 条")
    print(f"   warehouse slots: {len(SEED_DATA['warehouse_slots'])} 条")


if __name__ == "__main__":
    asyncio.run(seed())
```

### 6.2 从内存迁移现有数据(可选)

如果生产环境已有运行中的内存数据需迁移,新增 `scripts/migrate_mem_to_redis.py`:
- 通过 `/api/decision/health` 或专用导出端点读取当前 `_mock_store`
- 转换为 Redis 命令批量写入
- 当前为 Mock 环境,无生产数据,可跳过此步,直接用 seed 脚本

## 7. 测试兼容性设计

### 7.1 pytest.ini 环境变量

```ini
[pytest]
# ... 现有配置 ...
asyncio_mode = auto

# 强制内存模式, 保证现有 187 个测试零改动
env =
    LOCK_MODE=asyncio
    STORE_MODE=asyncio
```

> pytest 默认不读取 `env` 配置,需安装 `pytest-env` 或在 `conftest.py` 中设置:

```python
# conftest.py
import os
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
```

### 7.2 新增 Redis 集成测试

新增 `test_redis_integration.py`(需 Redis 服务,CI 中标记 `@pytest.mark.redis`):

```python
"""Redis 后端集成测试(需 Redis 服务)

运行: LOCK_MODE=redis STORE_MODE=redis py -m pytest test_redis_integration.py -m redis
"""
import pytest
import redis.asyncio as redis

pytestmark = [pytest.mark.redis, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
async def setup_redis():
    """每个测试前清空并 seed Redis"""
    client = redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
    async for key in client.scan_iter("zhuxiang:*"):
        await client.delete(key)
    # seed...
    yield
    await client.aclose()


class TestRedisInventory:
    async def test_deduct_stock_atomic(self):
        """Redis 后端库存扣减原子性"""
        ...

    async def test_concurrent_deduct_no_oversell(self):
        """100 并发扣减不超卖(跨进程验证)"""
        ...
```

### 7.3 CI 流水线调整

`.github/workflows/ci.yml` 现有 `concurrency-probe-redis` job 已配置 Redis 服务容器,新增:
- `redis-integration-tests` job:运行 `test_redis_integration.py`
- 依赖 Redis 服务容器(已有配置可复用)

## 8. 实施步骤(分阶段)

### Phase 1: 后端选择器 + Repository 适配(无破坏性)

**目标**:新增 Redis 后端代码,默认仍走内存模式,现有功能零影响

| 步骤 | 文件 | 内容 |
|------|------|------|
| 1.1 | `repositories/backend.py` | 新增后端选择器 |
| 1.2 | `repositories/*.py` | 5 个 Repository 增加 `_redis_*` 方法,方法改为 async |
| 1.3 | `services/*.py` | Service 调用 Repository 加 `await` |
| 1.4 | `conftest.py` | 新增,设置 `LOCK_MODE=asyncio` `STORE_MODE=asyncio` |
| 1.5 | 跑现有 187 测试 | 验证零破坏 |

**验证标准**:187 个测试全部通过,内存模式行为不变

### Phase 2: 锁默认模式切换

| 步骤 | 文件 | 内容 |
|------|------|------|
| 2.1 | `core/locks.py` | `LOCK_MODE` 默认值 `asyncio` → `redis` |
| 2.2 | `conftest.py` | 显式覆盖 `LOCK_MODE=asyncio`(保护测试) |
| 2.3 | `docker-compose.yml` | 确认 `LOCK_MODE=redis`(已配置) |
| 2.4 | 本地启动 Redis + 跑探针 | 验证默认模式生效 |

**验证标准**:默认模式为 Redis,测试在内存模式下仍通过

### Phase 3: Seed 脚本 + 数据初始化

| 步骤 | 文件 | 内容 |
|------|------|------|
| 3.1 | `scripts/seed_redis.py` | 新增 seed 脚本 |
| 3.2 | `scripts/migrate_mem_to_redis.py` | 新增迁移脚本(可选) |
| 3.3 | `docker-compose.yml` | backend 服务启动前执行 seed |
| 3.4 | 手动跑 seed | 验证 Redis 数据写入 |

**验证标准**:Redis 中可见初始数据,API 读取正常

### Phase 4: Redis 集成测试 + CI

| 步骤 | 文件 | 内容 |
|------|------|------|
| 4.1 | `test_redis_integration.py` | 新增 Redis 集成测试套件 |
| 4.2 | `pytest.ini` | 注册 `redis` marker |
| 4.3 | `.github/workflows/ci.yml` | 新增 `redis-integration-tests` job |
| 4.4 | 跑 Redis 集成测试 | 验证跨进程一致性 |

**验证标准**:Redis 集成测试通过,多 worker 并发下数据一致

### Phase 5: 文档与发布

| 步骤 | 文件 | 内容 |
|------|------|------|
| 5.1 | `README.md` | 更新部署说明(需 Redis) |
| 5.2 | `docs/redis-persistence-migration-plan.md` | 本文档标记为已实施 |
| 5.3 | Git 提交 | `feat: Redis 持久化存储迁移(Scheme B+)` |
| 5.4 | 推送 | `git push origin master` |

## 9. 回滚策略

### 9.1 紧急回滚(生产故障)

```bash
# 1. 切回内存模式(无需改代码,改环境变量)
export LOCK_MODE=asyncio
export STORE_MODE=asyncio
# 重启服务

# 2. 数据回滚
# Redis 数据仍可用, 内存模式从 seed 重新加载
```

### 9.2 代码回滚

```bash
# 回到本次迁移前的提交
git revert <merge-commit>
# 或
git reset --hard <pre-migration-commit>
```

### 9.3 数据回滚

Redis appendonly 持久化已开启,可通过 `redis-cli BGREWRITEAOF` 或 RDB 快照恢复。
内存模式下数据从 `repositories/store.py` 的 `_mock_store` 初始值重新加载。

## 10. 风险与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| Repository 改 async 导致 Service 层连锁修改 | 中 | Phase 1 集中改造,跑全量测试验证 |
| Redis 服务不可用导致服务启动失败 | 高 | backend 服务 `restart: unless-stopped`,依赖 `depends_on: redis` |
| 测试在 Redis 模式下跑会失败(直接改 _mock_store) | 中 | `conftest.py` 强制 `LOCK_MODE=asyncio`,Redis 测试单独 marker |
| 多 worker 并发下 HINCRBY 与锁的边界 | 中 | 复用已验证的 `core/locks.py`,探针覆盖 |
| 数据类型转换(int/str 在 Redis 中都是 str) | 低 | Repository 层做类型转换,不暴露给 Service |
| Redis appendonly 文件膨胀 | 低 | 定期 `BGREWRITEAOF`,配置 `auto-aof-rewrite-percentage` |

## 11. 验收标准

### 11.1 功能验收

- [ ] 默认 `LOCK_MODE=redis`,服务正常启动
- [ ] 5 个 Repository 在 Redis 模式下 CRUD 正常
- [ ] 多 worker 并发扣减库存不超卖(探针验证)
- [ ] 多 worker 并发代理商升级钱包金额精确
- [ ] 区域认领跨进程互斥

### 11.2 兼容性验收

- [ ] 现有 187 个测试在 `LOCK_MODE=asyncio` 下全部通过
- [ ] 新增 Redis 集成测试在 `LOCK_MODE=redis` 下全部通过
- [ ] `docker-compose up` 一键启动正常

### 11.3 CI 验收

- [ ] `concurrency-probe-redis` job 通过
- [ ] 新增 `redis-integration-tests` job 通过
- [ ] 现有 `python-tests` / `concurrency-probe` job 不受影响

## 12. 文件清单

### 新增文件

| 文件 | 用途 |
|------|------|
| `repositories/backend.py` | 后端选择器 |
| `scripts/seed_redis.py` | Redis 数据初始化 |
| `scripts/migrate_mem_to_redis.py` | 内存→Redis 迁移(可选) |
| `test_redis_integration.py` | Redis 集成测试 |
| `conftest.py` | pytest 环境变量设置 |
| `docs/redis-persistence-migration-plan.md` | 本方案文档 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `core/locks.py` | `LOCK_MODE` 默认值 asyncio → redis |
| `repositories/agent_repository.py` | 增加 Redis 后端,方法改 async |
| `repositories/inventory_repository.py` | 同上 |
| `repositories/warehouse_repository.py` | 同上 |
| `repositories/order_repository.py` | 同上 |
| `repositories/shipping_repository.py` | 同上 |
| `services/*.py` | 调用 Repository 加 await |
| `pytest.ini` | 注册 redis marker |
| `docker-compose.yml` | backend 启动前 seed |
| `.github/workflows/ci.yml` | 新增 redis-integration-tests job |

---

## 附录 A: 环境变量速查

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LOCK_MODE` | `redis`(迁移后) | 锁模式: asyncio/redis |
| `STORE_MODE` | 同 `LOCK_MODE` | 存储模式: asyncio/redis |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接地址 |

## 附录 B: 本地开发快速启动

```bash
# 1. 启动 Redis(docker-compose 或本地安装)
docker-compose up -d redis

# 2. 初始化数据
REDIS_URL=redis://127.0.0.1:6379/0 py scripts/seed_redis.py

# 3. 启动后端(默认 Redis 模式)
cd zhuxiang-jiu/backend
uvicorn main:app --reload --port 8000

# 4. 若无需 Redis, 切回内存模式
$env:LOCK_MODE = "asyncio"
$env:STORE_MODE = "asyncio"
uvicorn main:app --reload --port 8000
```
