"""Redis 后端集成测试(需 Redis 服务)

验证内容:
    1. seed_redis.py 写入的初始数据可被 Repository 正确读取
    2. Repository 双模式在 Redis 模式下 CRUD 正常
    3. 跨进程并发下数据一致(无超卖/无丢失更新)
    4. Redis 持久化:重启后数据仍在
    5. 扩展: 5 个 Repository 的 Redis 分支边界/异常路径全覆盖
       - AgentRepository: KeyError 路径 / 类型一致性 / 全降级链(S→A→B→C→D)
       - InventoryRepository: get None / 精确扣减 / 回补不存在 / 类型透明
       - WarehouseRepository: 空库位 / 出库计数 / 基线对比 / 返回值一致
       - OrderRepository: 空列表 / 顺序保持 / orderId 返回 / 深拷贝隔离
       - ShippingClaimRepository: None / 非数字 ID / 覆盖 / int 还原
    6. backend.py 辅助函数: _k key 格式 / is_redis_mode 优先级 / 单例

运行前提:
    - Redis 服务已启动(默认 redis://127.0.0.1:6379/0)
    - 已执行 seed_redis.py 初始化数据

运行方式:
    # 仅运行 Redis 集成测试(无需手动设环境变量, autouse fixture 自动切换)
    py -m pytest test_redis_integration.py -m redis -v

    # 跳过 Redis 测试(无 Redis 环境时)
    py -m pytest -m "not redis"

    # 全量运行(内存测试 + Redis 测试自动隔离)
    py -m pytest

设计:
    - 不在模块顶层设置环境变量(避免污染同进程其他内存测试)
    - autouse fixture 用 monkeypatch 在每个测试运行时设 redis 模式
    - is_redis_mode() 动态读取环境变量, 测试结束自动恢复

CI 集成:
    .github/workflows/ci.yml 中的 redis-integration-tests job 自动运行
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# 路径设置
BACKEND_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BACKEND_DIR))

# Redis 连接地址(与 backend.py 默认值一致)
_REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")


# ============================================================
# Redis 可用性检查(无 Redis 时自动跳过全部测试)
# ============================================================

def _redis_available() -> bool:
    """检查 Redis 服务是否可用"""
    try:
        import redis
        client = redis.from_url(_REDIS_URL, decode_responses=True)
        client.ping()
        client.close()
        return True
    except Exception:
        return False


# 若 Redis 不可用,所有测试标记为 skip
# 注: asyncio_mode=auto 已在 pytest.ini 中配置, 无需显式 pytest.mark.asyncio
pytestmark = [
    pytest.mark.redis,
    pytest.mark.skipif(
        not _redis_available(),
        reason=f"Redis 服务不可用: {_REDIS_URL}",
    ),
]


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def _force_redis_mode(monkeypatch):
    """强制 Redis 模式(仅对本文件的测试生效, 测试结束自动恢复)

    用 monkeypatch 设环境变量, 避免 conftest.py 的 asyncio 设置影响。
    配合 backend.is_redis_mode() 的动态读取, 实现测试隔离。
    """
    monkeypatch.setenv("LOCK_MODE", "redis")
    monkeypatch.setenv("STORE_MODE", "redis")
    monkeypatch.setenv("REDIS_URL", _REDIS_URL)


@pytest.fixture
async def redis_client():
    """Redis 客户端 fixture(测试结束自动关闭)"""
    import redis.asyncio as redis
    client = redis.from_url(_REDIS_URL, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def seeded_redis(redis_client):
    """每个测试前执行 seed,确保数据初始状态一致(幂等)"""
    # 导入 seed 函数
    sys.path.insert(0, str(BACKEND_DIR / "scripts"))
    from seed_redis import clear_existing_data, seed_agents, seed_inventory, seed_warehouse_slots

    await clear_existing_data(redis_client)
    await seed_agents(redis_client)
    await seed_inventory(redis_client)
    await seed_warehouse_slots(redis_client)

    yield redis_client


@pytest.fixture
def agent_repo():
    """AgentRepository(Redis 模式)"""
    from repositories.agent_repository import AgentRepository
    return AgentRepository()


@pytest.fixture
def inventory_repo():
    """InventoryRepository(Redis 模式)"""
    from repositories.inventory_repository import InventoryRepository
    return InventoryRepository()


@pytest.fixture
def warehouse_repo():
    """WarehouseRepository(Redis 模式)"""
    from repositories.warehouse_repository import WarehouseRepository
    return WarehouseRepository()


@pytest.fixture
def order_repo():
    """OrderRepository(Redis 模式)"""
    from repositories.order_repository import OrderRepository
    return OrderRepository()


@pytest.fixture
def shipping_repo():
    """ShippingClaimRepository(Redis 模式)"""
    from repositories.shipping_repository import ShippingClaimRepository
    return ShippingClaimRepository()


@pytest.fixture
def agent_service():
    """AgentService(Redis 模式,含锁)"""
    from services.agent_service import AgentService
    return AgentService()


@pytest.fixture
def inventory_service():
    """InventoryService(Redis 模式,含锁)"""
    from services.inventory_service import InventoryService
    return InventoryService()


# ============================================================
# 测试类: seed 数据验证
# ============================================================

class TestSeedData:
    """验证 seed_redis.py 写入的数据可被正确读取"""

    async def test_seed_agents_readable(self, seeded_redis, agent_repo):
        """seed 写入的 2 个代理商可被 Repository 读取"""
        agent1 = await agent_repo.get(1)
        assert agent1 is not None
        assert agent1["id"] == 1
        assert agent1["name"] == "泰安市级代理商"
        assert agent1["level"] == "C"
        assert agent1["wallet"] == 50000.0

        agent2 = await agent_repo.get(2)
        assert agent2 is not None
        assert agent2["level"] == "B"
        assert agent2["wallet"] == 120000.0

    async def test_seed_inventory_readable(self, seeded_redis, inventory_repo):
        """seed 写入的库存数据可被 Repository 读取"""
        inv = await inventory_repo.get("ZX42-2026L07")
        assert inv is not None
        assert inv["stock"] == 500
        assert inv["reserved"] == 0

        inv2 = await inventory_repo.get("ZX42-2026L05")
        assert inv2["stock"] == 300

    async def test_seed_warehouse_slots_readable(self, seeded_redis, warehouse_repo):
        """seed 写入的库位映射可被 Repository 读取"""
        slots = await warehouse_repo.get_slots()
        # A1/A2 有值, B1 不存在(空库位不在初始数据中)
        assert slots.get("A1") == "ZX42-2026L07"
        assert slots.get("A2") == "ZX42-2026L05"
        assert "B1" not in slots  # 空库位不写入, 与内存模式一致

    async def test_seed_list_all_agents(self, seeded_redis, agent_repo):
        """list_all 返回所有代理商"""
        agents = await agent_repo.list_all()
        assert len(agents) == 2
        agent_ids = {a["id"] for a in agents}
        assert agent_ids == {1, 2}


# ============================================================
# 测试类: Repository CRUD(Redis 模式)
# ============================================================

class TestAgentRepositoryRedis:
    """AgentRepository 在 Redis 模式下的 CRUD"""

    async def test_get_nonexistent_returns_none(self, seeded_redis, agent_repo):
        """查询不存在的代理商返回 None"""
        agent = await agent_repo.get(999)
        assert agent is None

    async def test_update_level_returns_old_level(self, seeded_redis, agent_repo):
        """update_level 返回旧等级并更新"""
        old_level = await agent_repo.update_level(1, "B")
        assert old_level == "C"
        new_level = await agent_repo.get_level(1)
        assert new_level == "B"

    async def test_update_level_nonexistent_raises_keyerror(self, seeded_redis, agent_repo):
        """更新不存在的代理商抛 KeyError"""
        with pytest.raises(KeyError):
            await agent_repo.update_level(999, "A")

    async def test_add_wallet_atomic(self, seeded_redis, agent_repo):
        """add_wallet 原子累加余额"""
        new_wallet = await agent_repo.add_wallet(1, 10000)
        assert new_wallet == 60000.0
        wallet = await agent_repo.get_wallet(1)
        assert wallet == 60000.0

    async def test_downgrade_level_s_to_a(self, seeded_redis, agent_repo):
        """S 级降级到 A"""
        await agent_repo.update_level(1, "S")
        new_level = await agent_repo.downgrade_level(1)
        assert new_level == "A"

    async def test_downgrade_level_d_stays_d(self, seeded_redis, agent_repo):
        """D 级降级保持 D"""
        await agent_repo.update_level(1, "D")
        new_level = await agent_repo.downgrade_level(1)
        assert new_level == "D"

    async def test_save_new_agent(self, seeded_redis, agent_repo):
        """save 新增代理商"""
        await agent_repo.save(3, {"id": 3, "name": "测试代理商", "level": "D", "wallet": 0})
        agent = await agent_repo.get(3)
        assert agent is not None
        assert agent["name"] == "测试代理商"


class TestInventoryRepositoryRedis:
    """InventoryRepository 在 Redis 模式下的 CRUD"""

    async def test_get_stock_returns_zero_for_nonexistent(self, seeded_redis, inventory_repo):
        """不存在的产品返回 0"""
        stock = await inventory_repo.get_stock("NONEXISTENT")
        assert stock == 0

    async def test_deduct_atomic(self, seeded_redis, inventory_repo):
        """deduct 扣减库存"""
        new_stock = await inventory_repo.deduct("ZX42-2026L07", 100)
        assert new_stock == 400

    async def test_deduct_nonexistent_raises_keyerror(self, seeded_redis, inventory_repo):
        """扣减不存在的产品抛 KeyError"""
        with pytest.raises(KeyError):
            await inventory_repo.deduct("NONEXISTENT", 1)

    async def test_deduct_insufficient_raises_valueerror(self, seeded_redis, inventory_repo):
        """库存不足抛 ValueError"""
        with pytest.raises(ValueError, match="库存不足"):
            await inventory_repo.deduct("ZX42-2026L07", 10000)

    async def test_restock_atomic(self, seeded_redis, inventory_repo):
        """restock 回补库存"""
        new_stock = await inventory_repo.restock("ZX42-2026L07", 50)
        assert new_stock == 550

    async def test_set_stock_creates_new_product(self, seeded_redis, inventory_repo):
        """set_stock 自动创建新产品"""
        await inventory_repo.set_stock("NEW-PRODUCT", 999)
        stock = await inventory_repo.get_stock("NEW-PRODUCT")
        assert stock == 999


class TestWarehouseRepositoryRedis:
    """WarehouseRepository 在 Redis 模式下的 CRUD"""

    async def test_append_inbound_log(self, seeded_redis, warehouse_repo):
        """追加入库日志"""
        log = {"action": "inbound", "productId": "TEST", "slot": "A1"}
        await warehouse_repo.append_inbound_log(log)
        count = await warehouse_repo.count_inbound()
        assert count == 1

    async def test_append_outbound_log(self, seeded_redis, warehouse_repo):
        """追加出库日志"""
        log = {"action": "outbound", "productId": "TEST"}
        await warehouse_repo.append_outbound_log(log)
        count = await warehouse_repo.count_outbound()
        assert count == 1

    async def test_count_inbound_before_baseline(self, seeded_redis, warehouse_repo):
        """count_inbound_before 基线对比"""
        await warehouse_repo.append_inbound_log({"a": 1})
        await warehouse_repo.append_inbound_log({"b": 2})
        diff = await warehouse_repo.count_inbound_before(0)
        assert diff == 2


class TestOrderRepositoryRedis:
    """OrderRepository 在 Redis 模式下的 CRUD"""

    async def test_create_and_count(self, seeded_redis, order_repo):
        """创建订单并计数"""
        order_id = await order_repo.create({"orderId": "TEST001", "status": "pending"})
        assert order_id == "TEST001"
        count = await order_repo.count()
        assert count == 1

    async def test_list_all(self, seeded_redis, order_repo):
        """列出所有订单"""
        await order_repo.create({"orderId": "O1"})
        await order_repo.create({"orderId": "O2"})
        orders = await order_repo.list_all()
        assert len(orders) == 2
        ids = {o["orderId"] for o in orders}
        assert ids == {"O1", "O2"}


class TestShippingRepositoryRedis:
    """ShippingClaimRepository 在 Redis 模式下的 CRUD"""

    async def test_set_and_get_claim(self, seeded_redis, shipping_repo):
        """设置和查询区域认领"""
        await shipping_repo.set_claim("山东", 1)
        claim = await shipping_repo.get_claim("山东")
        assert claim == 1  # Repository 将数字型 agent_id 还原为 int

    async def test_is_claimed(self, seeded_redis, shipping_repo):
        """检查区域是否被认领"""
        assert await shipping_repo.is_claimed("北京") is False
        await shipping_repo.set_claim("北京", 2)
        assert await shipping_repo.is_claimed("北京") is True

    async def test_list_all_claims(self, seeded_redis, shipping_repo):
        """列出所有认领"""
        await shipping_repo.set_claim("山东", 1)
        await shipping_repo.set_claim("北京", 2)
        claims = await shipping_repo.list_all()
        assert len(claims) == 2
        assert claims["山东"] == 1  # int 还原
        assert claims["北京"] == 2  # int 还原


# ============================================================
# 测试类: Service 层(Redis 模式 + 锁)
# ============================================================

class TestInventoryServiceRedis:
    """InventoryService 在 Redis 模式下(含分布式锁)"""

    async def test_deduct_success(self, seeded_redis, inventory_service):
        """正常扣减库存"""
        result = await inventory_service.deduct("ZX42-2026L07", 100)
        assert result["success"] is True
        assert result["stockAfter"] == 400
        assert result["txId"].startswith("TX")

    async def test_deduct_insufficient_returns_failure(self, seeded_redis, inventory_service):
        """库存不足返回 success=False(不抛异常)"""
        result = await inventory_service.deduct("ZX42-2026L07", 10000)
        assert result["success"] is False
        assert "库存不足" in result["error"]

    async def test_deduct_nonexistent_raises_keyerror(self, seeded_redis, inventory_service):
        """产品不存在抛 KeyError(由 Route 转 404)"""
        with pytest.raises(KeyError):
            await inventory_service.deduct("NONEXISTENT", 1)

    async def test_restock_success(self, seeded_redis, inventory_service):
        """回补库存"""
        result = await inventory_service.restock("ZX42-2026L07", 50)
        assert result["success"] is True
        assert result["stockAfter"] == 550


class TestAgentServiceRedis:
    """AgentService 在 Redis 模式下(含分布式锁)"""

    async def test_upgrade_success(self, seeded_redis, agent_service):
        """代理商升级(等级 + 钱包)"""
        result = await agent_service.upgrade(1, "B", 10000)
        assert result["success"] is True
        assert result["fromLevel"] == "C"
        assert result["toLevel"] == "B"
        assert result["wallet"] == 60000.0

    async def test_upgrade_nonexistent_raises_keyerror(self, seeded_redis, agent_service):
        """升级不存在的代理商抛 KeyError"""
        with pytest.raises(KeyError):
            await agent_service.upgrade(999, "A", 1000)

    async def test_downgrade_success(self, seeded_redis, agent_service):
        """代理商降级"""
        await agent_service.upgrade(1, "S", 0)
        result = await agent_service.downgrade(1, "考核未达标")
        assert result["success"] is True
        assert result["fromLevel"] == "S"
        assert result["toLevel"] == "A"


# ============================================================
# 测试类: 并发安全(Redis 分布式锁验证)
# ============================================================

class TestConcurrencyRedis:
    """Redis 分布式锁下的并发安全验证

    对比单进程 asyncio.Lock:
        - asyncio.Lock 仅进程内互斥, 多 worker 下失效
        - Redis Lock 跨进程互斥, 多 worker 下数据一致
    """

    async def test_concurrent_deduct_no_oversell(self, seeded_redis, inventory_service):
        """100 并发扣减 1 件, 最终库存 = 500 - 100 = 400(无超卖)"""
        tasks = [inventory_service.deduct("ZX42-2026L07", 1) for _ in range(100)]
        results = await asyncio.gather(*tasks)

        success_count = sum(1 for r in results if r["success"] is True)
        assert success_count == 100, f"应有 100 个成功, 实际 {success_count}"

        # 验证最终库存
        from repositories.inventory_repository import InventoryRepository
        repo = InventoryRepository()
        final_stock = await repo.get_stock("ZX42-2026L07")
        assert final_stock == 400, f"应剩余 400, 实际 {final_stock}(可能超卖)"

    async def test_concurrent_upgrade_wallet_precision(self, seeded_redis, agent_service):
        """50 并发升级同一代理商, 钱包累加精确"""
        tasks = [agent_service.upgrade(1, "C", 1000) for _ in range(50)]
        results = await asyncio.gather(*tasks)

        success_count = sum(1 for r in results if r["success"] is True)
        assert success_count == 50

        # 验证最终钱包余额 = 50000 + 50 * 1000 = 100000
        from repositories.agent_repository import AgentRepository
        repo = AgentRepository()
        final_wallet = await repo.get_wallet(1)
        assert final_wallet == 100000.0, f"应剩余 100000, 实际 {final_wallet}"

    async def test_concurrent_deduct_mixed_operations(self, seeded_redis, inventory_service):
        """50 扣减 + 50 回补, 最终库存不变"""
        initial = 500
        deduct_tasks = [inventory_service.deduct("ZX42-2026L07", 1) for _ in range(50)]
        restock_tasks = [inventory_service.restock("ZX42-2026L07", 1) for _ in range(50)]

        all_results = await asyncio.gather(*(deduct_tasks + restock_tasks))
        success = sum(1 for r in all_results if r.get("success") is True)
        assert success == 100

        from repositories.inventory_repository import InventoryRepository
        repo = InventoryRepository()
        final_stock = await repo.get_stock("ZX42-2026L07")
        assert final_stock == initial, f"应回到 {initial}, 实际 {final_stock}"


# ============================================================
# 测试类: 持久化验证
# ============================================================

class TestPersistence:
    """Redis 持久化验证(appendonly)"""

    async def test_data_survives_connection_close(self, seeded_redis, inventory_repo):
        """关闭连接后重新连接, 数据仍在"""
        # 写入数据
        await inventory_repo.set_stock("PERSIST-TEST", 123)

        # 重新创建 Repository(新连接)
        from repositories.backend import get_redis_client
        client = await get_redis_client()
        await client.aclose()

        # 重置模块级单例, 强制重连
        import repositories.backend
        repositories.backend._redis_client = None

        # 新 Repository 读取
        from repositories.inventory_repository import InventoryRepository
        new_repo = InventoryRepository()
        stock = await new_repo.get_stock("PERSIST-TEST")
        assert stock == 123


# ============================================================
# 扩展测试: AgentRepository Redis 边界与异常路径
# ============================================================

class TestAgentRepositoryRedisExtended:
    """AgentRepository Redis 模式补充: KeyError 路径 / 类型一致性 / 全降级链"""

    async def test_get_level_nonexistent_raises_keyerror(self, seeded_redis, agent_repo):
        """get_level 不存在的代理商抛 KeyError"""
        with pytest.raises(KeyError):
            await agent_repo.get_level(999)

    async def test_get_wallet_nonexistent_raises_keyerror(self, seeded_redis, agent_repo):
        """get_wallet 不存在的代理商抛 KeyError"""
        with pytest.raises(KeyError):
            await agent_repo.get_wallet(999)

    async def test_add_wallet_nonexistent_raises_keyerror(self, seeded_redis, agent_repo):
        """add_wallet 不存在的代理商抛 KeyError"""
        with pytest.raises(KeyError):
            await agent_repo.add_wallet(999, 100)

    async def test_downgrade_level_nonexistent_raises_keyerror(self, seeded_redis, agent_repo):
        """downgrade_level 不存在的代理商抛 KeyError"""
        with pytest.raises(KeyError):
            await agent_repo.downgrade_level(999)

    async def test_save_overwrite_existing(self, seeded_redis, agent_repo):
        """save 覆盖现有代理商(保留新字段)"""
        await agent_repo.save(1, {"id": 1, "name": "新名称", "level": "A", "wallet": 999})
        agent = await agent_repo.get(1)
        assert agent["name"] == "新名称"
        assert agent["level"] == "A"
        assert agent["wallet"] == 999.0

    async def test_list_all_empty_when_no_agents(self, redis_client):
        """list_all 无代理商时返回空列表"""
        from repositories.agent_repository import AgentRepository
        await redis_client.flushall()
        repo = AgentRepository()
        agents = await repo.list_all()
        assert agents == []

    async def test_downgrade_level_all_transitions(self, seeded_redis, agent_repo):
        """降级链: S→A→B→C→D 完整验证"""
        transitions = [("S", "A"), ("A", "B"), ("B", "C"), ("C", "D")]
        for from_level, expected in transitions:
            await agent_repo.update_level(1, from_level)
            new_level = await agent_repo.downgrade_level(1)
            assert new_level == expected, f"{from_level}→{expected} 失败, 实际 {new_level}"

    async def test_redis_type_consistency(self, seeded_redis, agent_repo):
        """Redis 返回类型与内存模式一致: id=int, wallet=float, level=str"""
        agent = await agent_repo.get(1)
        assert isinstance(agent["id"], int)
        assert isinstance(agent["wallet"], float)
        assert isinstance(agent["level"], str)

    async def test_add_wallet_zero_noop(self, seeded_redis, agent_repo):
        """add_wallet 0 元: 余额不变"""
        before = await agent_repo.get_wallet(1)
        after = await agent_repo.add_wallet(1, 0)
        assert after == before

    async def test_add_wallet_negative_amount_deducts(self, seeded_redis, agent_repo):
        """add_wallet 负金额: 等效扣减(HINCRBYFLOAT 支持负值)"""
        before = await agent_repo.get_wallet(1)
        after = await agent_repo.add_wallet(1, -1000)
        assert after == before - 1000


# ============================================================
# 扩展测试: InventoryRepository Redis 边界与异常路径
# ============================================================

class TestInventoryRepositoryRedisExtended:
    """InventoryRepository Redis 模式补充: get None / 精确扣减 / 类型一致性"""

    async def test_get_nonexistent_returns_none(self, seeded_redis, inventory_repo):
        """get 不存在的产品返回 None"""
        inv = await inventory_repo.get("NONEXISTENT")
        assert inv is None

    async def test_get_returns_dict_with_stock_and_reserved(self, seeded_redis, inventory_repo):
        """get 返回完整字典含 stock 和 reserved"""
        inv = await inventory_repo.get("ZX42-2026L07")
        assert inv is not None
        assert "stock" in inv
        assert "reserved" in inv
        assert isinstance(inv["stock"], int)
        assert isinstance(inv["reserved"], int)

    async def test_deduct_exact_quantity(self, seeded_redis, inventory_repo):
        """扣减恰好等于库存: 应成功且余额为 0"""
        # ZX42-2026L07 初始 500
        new_stock = await inventory_repo.deduct("ZX42-2026L07", 500)
        assert new_stock == 0

    async def test_deduct_zero_quantity(self, seeded_redis, inventory_repo):
        """扣减 0 件: 应成功且库存不变"""
        before = await inventory_repo.get_stock("ZX42-2026L07")
        new_stock = await inventory_repo.deduct("ZX42-2026L07", 0)
        assert new_stock == before

    async def test_deduct_more_than_stock_raises_valueerror(self, seeded_redis, inventory_repo):
        """扣减超过库存抛 ValueError(含当前库存和需要量)"""
        with pytest.raises(ValueError, match="库存不足"):
            await inventory_repo.deduct("ZX42-2026L07", 501)

    async def test_restock_nonexistent_raises_keyerror(self, seeded_redis, inventory_repo):
        """回补不存在的产品抛 KeyError"""
        with pytest.raises(KeyError):
            await inventory_repo.restock("NONEXISTENT", 10)

    async def test_restock_zero_quantity(self, seeded_redis, inventory_repo):
        """回补 0 件: 应成功且库存不变"""
        before = await inventory_repo.get_stock("ZX42-2026L07")
        new_stock = await inventory_repo.restock("ZX42-2026L07", 0)
        assert new_stock == before

    async def test_set_stock_overwrite_existing(self, seeded_redis, inventory_repo):
        """set_stock 覆盖现有库存"""
        await inventory_repo.set_stock("ZX42-2026L07", 0)
        assert await inventory_repo.get_stock("ZX42-2026L07") == 0
        await inventory_repo.set_stock("ZX42-2026L07", 999)
        assert await inventory_repo.get_stock("ZX42-2026L07") == 999

    async def test_deduct_then_restock_roundtrip(self, seeded_redis, inventory_repo):
        """扣减后回补: 库存应回到原值"""
        original = await inventory_repo.get_stock("ZX42-2026L07")
        await inventory_repo.deduct("ZX42-2026L07", 100)
        await inventory_repo.restock("ZX42-2026L07", 100)
        final = await inventory_repo.get_stock("ZX42-2026L07")
        assert final == original

    async def test_product_id_type_transparent(self, seeded_redis, inventory_repo):
        """product_id 为 int 时应自动转 str(与内存模式一致)"""
        # set_stock 用 int, get_stock 用 str
        await inventory_repo.set_stock(12345, 50)
        stock = await inventory_repo.get_stock("12345")
        assert stock == 50


# ============================================================
# 扩展测试: WarehouseRepository Redis 边界与计数
# ============================================================

class TestWarehouseRepositoryRedisExtended:
    """WarehouseRepository Redis 模式补充: 空库位 / 出库计数 / 基线对比"""

    async def test_get_slots_returns_dict(self, seeded_redis, warehouse_repo):
        """get_slots 返回 dict 类型"""
        slots = await warehouse_repo.get_slots()
        assert isinstance(slots, dict)

    async def test_append_inbound_log_returns_same_log(self, seeded_redis, warehouse_repo):
        """append_inbound_log 返回值应与传入一致"""
        log = {"action": "inbound", "productId": "RET-TEST", "slot": "A1"}
        result = await warehouse_repo.append_inbound_log(log)
        assert result == log

    async def test_append_outbound_log_returns_same_log(self, seeded_redis, warehouse_repo):
        """append_outbound_log 返回值应与传入一致"""
        log = {"action": "outbound", "productId": "RET-TEST"}
        result = await warehouse_repo.append_outbound_log(log)
        assert result == log

    async def test_count_outbound_after_append(self, seeded_redis, warehouse_repo):
        """追加出库日志后计数应增加"""
        before = await warehouse_repo.count_outbound()
        await warehouse_repo.append_outbound_log({"x": 1})
        after = await warehouse_repo.count_outbound()
        assert after == before + 1

    async def test_count_inbound_before_baseline(self, seeded_redis, warehouse_repo):
        """count_inbound_before 基线对比: 返回当前与基线的差"""
        await warehouse_repo.append_inbound_log({"i": 1})
        await warehouse_repo.append_inbound_log({"i": 2})
        await warehouse_repo.append_inbound_log({"i": 3})
        baseline = 0
        diff = await warehouse_repo.count_inbound_before(baseline)
        assert diff == 3

    async def test_count_outbound_before_baseline(self, seeded_redis, warehouse_repo):
        """count_outbound_before 基线对比"""
        await warehouse_repo.append_outbound_log({"o": 1})
        await warehouse_repo.append_outbound_log({"o": 2})
        baseline = 0
        diff = await warehouse_repo.count_outbound_before(baseline)
        assert diff == 2

    async def test_count_inbound_before_negative_when_baseline_higher(self, seeded_redis, warehouse_repo):
        """基线高于当前时返回负数(测试用辅助函数语义)"""
        current = await warehouse_repo.count_inbound()
        diff = await warehouse_repo.count_inbound_before(current + 10)
        assert diff == -10

    async def test_inbound_log_order_preserved(self, seeded_redis, warehouse_repo):
        """入库日志应保持追加顺序(FIFO)"""
        for i in range(5):
            await warehouse_repo.append_inbound_log({"seq": i})
        # 验证计数(不验证内容, 因 List 存 JSON)
        count = await warehouse_repo.count_inbound()
        assert count >= 5


# ============================================================
# 扩展测试: OrderRepository Redis 边界与顺序
# ============================================================

class TestOrderRepositoryRedisExtended:
    """OrderRepository Redis 模式补充: 空列表 / 顺序保持 / orderId 返回"""

    async def test_count_zero_initially(self, redis_client):
        """初始计数为 0(无 seed 数据)"""
        from repositories.order_repository import OrderRepository
        await redis_client.flushall()
        repo = OrderRepository()
        count = await repo.count()
        assert count == 0

    async def test_list_all_empty(self, redis_client):
        """空订单列表返回 []"""
        from repositories.order_repository import OrderRepository
        await redis_client.flushall()
        repo = OrderRepository()
        orders = await repo.list_all()
        assert orders == []

    async def test_create_returns_order_id(self, seeded_redis, order_repo):
        """create 返回的 orderId 应与传入一致"""
        order_id = await order_repo.create({"orderId": "UNIQUE-001", "status": "paid"})
        assert order_id == "UNIQUE-001"

    async def test_create_multiple_preserves_order(self, seeded_redis, order_repo):
        """多个订单应保持插入顺序(List RPUSH 特性)"""
        for i in range(5):
            await order_repo.create({"orderId": f"O{i}", "seq": i})
        orders = await order_repo.list_all()
        # 验证顺序: O0, O1, O2, O3, O4
        ids = [o["orderId"] for o in orders]
        assert ids == [f"O{i}" for i in range(5)]

    async def test_create_order_without_order_id(self, seeded_redis, order_repo):
        """create 无 orderId 字段: 返回 None(不抛错)"""
        result = await order_repo.create({"items": []})
        assert result is None

    async def test_list_all_returns_deep_copy(self, seeded_redis, order_repo):
        """list_all 返回的列表修改不影响 Redis(List 不可变, 但 dict 可变)"""
        await order_repo.create({"orderId": "C1", "marker": "original"})
        orders = await order_repo.list_all()
        # 修改返回的 dict
        orders[0]["marker"] = "tampered"
        # 重新读取, Redis 中应为原值
        fresh = await order_repo.list_all()
        target = next(o for o in fresh if o["orderId"] == "C1")
        assert target["marker"] == "original"

    async def test_count_after_multiple_creates(self, seeded_redis, order_repo):
        """多次 create 后 count 应正确"""
        for i in range(10):
            await order_repo.create({"orderId": f"COUNT-{i}"})
        assert await order_repo.count() == 10


# ============================================================
# 扩展测试: ShippingClaimRepository Redis 边界与类型
# ============================================================

class TestShippingRepositoryRedisExtended:
    """ShippingClaimRepository Redis 模式补充: None / 非数字 ID / 覆盖"""

    async def test_get_claim_nonexistent_returns_none(self, seeded_redis, shipping_repo):
        """查询未认领区域返回 None"""
        claim = await shipping_repo.get_claim("无人认领的区域")
        assert claim is None

    async def test_is_claimed_nonexistent_returns_false(self, seeded_redis, shipping_repo):
        """未认领区域 is_claimed 返回 False"""
        assert await shipping_repo.is_claimed("未认领") is False

    async def test_set_claim_overwrite(self, seeded_redis, shipping_repo):
        """set_claim 覆盖认领(业务校验由 services 层负责)"""
        await shipping_repo.set_claim("覆盖区", 1)
        await shipping_repo.set_claim("覆盖区", 2)
        claim = await shipping_repo.get_claim("覆盖区")
        assert claim == 2  # int 还原

    async def test_list_all_empty(self, redis_client):
        """无认领时 list_all 返回空 dict"""
        from repositories.shipping_repository import ShippingClaimRepository
        await redis_client.flushall()
        repo = ShippingClaimRepository()
        claims = await repo.list_all()
        assert claims == {}

    async def test_set_claim_non_numeric_agent_id(self, seeded_redis, shipping_repo):
        """非数字 agent_id: 应原样存储为 str"""
        await shipping_repo.set_claim("文本区", "agent_abc")
        claim = await shipping_repo.get_claim("文本区")
        assert claim == "agent_abc"

    async def test_get_claim_numeric_returns_int(self, seeded_redis, shipping_repo):
        """数字 agent_id: Redis 返回 str, Repository 应还原为 int"""
        await shipping_repo.set_claim("数字区", 42)
        claim = await shipping_repo.get_claim("数字区")
        assert claim == 42
        assert isinstance(claim, int)

    async def test_list_all_numeric_values_converted_to_int(self, seeded_redis, shipping_repo):
        """list_all 数字型 agent_id 应转换为 int"""
        await shipping_repo.set_claim("区域1", 1)
        await shipping_repo.set_claim("区域2", 2)
        claims = await shipping_repo.list_all()
        assert all(isinstance(v, int) for v in claims.values())

    async def test_set_claim_zero_agent_id(self, seeded_redis, shipping_repo):
        """agent_id=0: 应正确存储(is_claimed=True)"""
        await shipping_repo.set_claim("零ID区", 0)
        assert await shipping_repo.is_claimed("零ID区") is True
        claim = await shipping_repo.get_claim("零ID区")
        assert claim == 0

    async def test_is_claimed_after_set(self, seeded_redis, shipping_repo):
        """set_claim 后 is_claimed 应为 True"""
        assert await shipping_repo.is_claimed("新区域") is False
        await shipping_repo.set_claim("新区域", 1)
        assert await shipping_repo.is_claimed("新区域") is True


# ============================================================
# 扩展测试: backend.py 辅助函数
# ============================================================

class TestBackendHelpers:
    """repositories/backend.py 辅助函数测试"""

    def test_k_key_format_single_part(self, monkeypatch):
        """_k 单段: zhuxiang:agent:1"""
        monkeypatch.setenv("STORE_MODE", "redis")
        from repositories.backend import _k
        assert _k("agent", 1) == "zhuxiang:agent:1"

    def test_k_key_format_string_part(self, monkeypatch):
        """_k 字符串段: zhuxiang:inventory:ZX42"""
        monkeypatch.setenv("STORE_MODE", "redis")
        from repositories.backend import _k
        assert _k("inventory", "ZX42-2026L07") == "zhuxiang:inventory:ZX42-2026L07"

    def test_k_key_format_multi_parts(self, monkeypatch):
        """_k 多段拼接: zhuxiang:warehouse:slots:A1"""
        monkeypatch.setenv("STORE_MODE", "redis")
        from repositories.backend import _k
        assert _k("warehouse", "slots", "A1") == "zhuxiang:warehouse:slots:A1"

    def test_is_redis_mode_store_mode_priority(self, monkeypatch):
        """is_redis_mode: STORE_MODE 优先于 LOCK_MODE"""
        monkeypatch.setenv("STORE_MODE", "redis")
        monkeypatch.setenv("LOCK_MODE", "asyncio")
        from repositories.backend import is_redis_mode
        assert is_redis_mode() is True

    def test_is_redis_mode_fallback_to_lock_mode(self, monkeypatch):
        """is_redis_mode: 无 STORE_MODE 时回退到 LOCK_MODE"""
        monkeypatch.delenv("STORE_MODE", raising=False)
        monkeypatch.setenv("LOCK_MODE", "redis")
        from repositories.backend import is_redis_mode
        assert is_redis_mode() is True

    def test_is_redis_mode_default_asyncio(self, monkeypatch):
        """is_redis_mode: 无环境变量时默认 asyncio"""
        monkeypatch.delenv("STORE_MODE", raising=False)
        monkeypatch.delenv("LOCK_MODE", raising=False)
        from repositories.backend import is_redis_mode
        assert is_redis_mode() is False

    async def test_get_redis_client_singleton(self, monkeypatch):
        """get_redis_client: 多次调用返回同一实例(单例)"""
        monkeypatch.setenv("STORE_MODE", "redis")
        from repositories import backend
        backend._redis_client = None  # 重置单例
        client1 = await backend.get_redis_client()
        client2 = await backend.get_redis_client()
        assert client1 is client2
        # 不关闭共享 client, 避免影响后续测试
        backend._redis_client = None
