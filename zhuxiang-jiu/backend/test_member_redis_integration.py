"""会员模块 Redis 后端集成测试(需 Redis 服务)

验证内容:
    1. seed_redis.py 写入的会员初始数据可被 Repository 正确读取
    2. MemberRepository 在 Redis 模式下 CRUD 正常
    3. 手机号唯一索引(并发注册同一手机号)
    4. 积分/成长值原子累加
    5. 收货地址 CRUD(Redis Hash + JSON)
    6. 默认地址互斥(clear_default_addresses)
    7. 类型还原(int 字段反序列化正确)
    8. 持久化:数据写入后可被新实例读取
    9. 异常路径:KeyError / ValueError

运行前提:
    - Redis 服务已启动(默认 redis://127.0.0.1:6379/0)
    - 已执行 seed_redis.py 初始化数据(测试中自动 seed)

运行方式:
    # 仅运行会员 Redis 集成测试
    py -m pytest test_member_redis_integration.py -m redis -v

    # 跳过 Redis 测试(无 Redis 环境时)
    py -m pytest -m "not redis"
"""

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
    """强制 Redis 模式(仅对本文件的测试生效, 测试结束自动恢复)"""
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
    sys.path.insert(0, str(BACKEND_DIR / "scripts"))
    from seed_redis import (clear_existing_data, seed_agents, seed_inventory,
                            seed_warehouse_slots, seed_members, seed_member_addresses)

    await clear_existing_data(redis_client)
    await seed_agents(redis_client)
    await seed_inventory(redis_client)
    await seed_warehouse_slots(redis_client)
    await seed_members(redis_client)
    await seed_member_addresses(redis_client)

    yield redis_client


@pytest.fixture
def member_repo():
    """MemberRepository(Redis 模式)"""
    from repositories.member_repository import MemberRepository
    return MemberRepository()


@pytest.fixture
def member_service():
    """MemberService(Redis 模式,含锁)"""
    from services.member_service import MemberService
    return MemberService()


# ============================================================
# 测试类: seed 数据验证
# ============================================================

class TestMemberSeedData:
    """验证 seed_redis.py 写入的会员数据可被正确读取"""

    async def test_seed_member_readable(self, seeded_redis, member_repo):
        """seed 写入的会员 1 可被 Repository 读取"""
        member = await member_repo.get_by_id(1)
        assert member is not None
        assert member["id"] == 1
        assert member["phone"] == "13800000001"
        assert member["nickname"] == "测试会员小竹"
        assert member["level"] == 1
        assert member["growth_value"] == 0
        assert member["points"] == 100
        assert member["status"] == 1

    async def test_seed_member_phone_index(self, seeded_redis, redis_client):
        """手机号索引指向正确的会员 ID"""
        from repositories.backend import _k
        member_id = await redis_client.get(_k("member", "phone", "13800000001"))
        assert member_id is not None
        assert int(member_id) == 1

    async def test_seed_member_seq(self, seeded_redis, redis_client):
        """ID 序列已设置(新注册的 ID > 1)"""
        from repositories.backend import _k
        seq = await redis_client.get(_k("member", "seq"))
        assert seq is not None
        assert int(seq) >= 1

    async def test_seed_member_by_phone(self, seeded_redis, member_repo):
        """通过手机号查询会员"""
        member = await member_repo.get_by_phone("13800000001")
        assert member is not None
        assert member["id"] == 1
        assert member["nickname"] == "测试会员小竹"

    async def test_seed_member_password_hashed(self, seeded_redis, member_repo):
        """seed 的密码已被正确哈希(不是明文)"""
        member = await member_repo.get_by_id(1)
        assert member is not None
        # 哈希值应是 64 位 hex(sha256)
        assert len(member["password"]) == 64
        assert member["password"] != "test123456"
        assert member["password"] != "mock_hashed_password_placeholder"

    async def test_seed_address_readable(self, seeded_redis, member_repo):
        """seed 写入的收货地址可被读取"""
        addrs = await member_repo.list_addresses(1)
        assert len(addrs) == 1
        assert addrs[0]["id"] == "addr_seed_001"
        assert addrs[0]["name"] == "张三"
        assert addrs[0]["is_default"] == 1


# ============================================================
# 测试类: Repository CRUD
# ============================================================

class TestMemberRepositoryCRUD:
    """MemberRepository 在 Redis 模式下的 CRUD"""

    async def test_create_member(self, seeded_redis, member_repo):
        """新增会员:自增 ID + 手机号索引"""
        member = await member_repo.create({
            "phone": "13900000000",
            "password": "hashed_pwd",
            "nickname": "新用户",
            "level": 1, "growth_value": 0, "points": 100,
            "status": 1, "reg_source": "phone",
        })
        assert member["id"] > 1  # ID 自增
        assert member["phone"] == "13900000000"

        # 可通过 ID 和手机号查询
        by_id = await member_repo.get_by_id(member["id"])
        by_phone = await member_repo.get_by_phone("13900000000")
        assert by_id is not None
        assert by_phone is not None
        assert by_id["id"] == by_phone["id"] == member["id"]

    async def test_create_duplicate_phone(self, seeded_redis, member_repo):
        """手机号重复: ValueError"""
        with pytest.raises(ValueError, match="已注册"):
            await member_repo.create({
                "phone": "13800000001",  # 已存在
                "password": "x", "nickname": "dup",
            })

    async def test_update_fields(self, seeded_redis, member_repo):
        """部分字段更新"""
        updated = await member_repo.update_fields(1, {"nickname": "新昵称", "gender": 2})
        assert updated["nickname"] == "新昵称"
        assert updated["gender"] == 2
        # 其他字段保留
        assert updated["phone"] == "13800000001"
        assert updated["points"] == 100

    async def test_update_fields_not_found(self, seeded_redis, member_repo):
        """更新不存在的会员: KeyError"""
        with pytest.raises(KeyError):
            await member_repo.update_fields(999, {"nickname": "x"})

    async def test_add_growth(self, seeded_redis, member_repo):
        """成长值累加"""
        new_growth = await member_repo.add_growth(1, 500)
        assert new_growth == 500
        new_growth = await member_repo.add_growth(1, 300)
        assert new_growth == 800

    async def test_add_growth_not_found(self, seeded_redis, member_repo):
        """成长值累加:会员不存在 KeyError"""
        with pytest.raises(KeyError):
            await member_repo.add_growth(999, 100)

    async def test_add_points_positive(self, seeded_redis, member_repo):
        """积分累加(正数)"""
        new_points = await member_repo.add_points(1, 50)
        assert new_points == 150

    async def test_add_points_negative(self, seeded_redis, member_repo):
        """积分扣减(负数)"""
        new_points = await member_repo.add_points(1, -50)
        assert new_points == 50

    async def test_add_points_insufficient(self, seeded_redis, member_repo):
        """积分不足: ValueError"""
        with pytest.raises(ValueError, match="积分不足"):
            await member_repo.add_points(1, -200)  # 当前 100, 扣 200

    async def test_add_points_not_found(self, seeded_redis, member_repo):
        """积分操作:会员不存在 KeyError"""
        with pytest.raises(KeyError):
            await member_repo.add_points(999, 100)

    async def test_get_points(self, seeded_redis, member_repo):
        """查询积分"""
        points = await member_repo.get_points(1)
        assert points == 100

    async def test_get_level(self, seeded_redis, member_repo):
        """查询等级"""
        level = await member_repo.get_level(1)
        assert level == 1

    async def test_update_level(self, seeded_redis, member_repo):
        """更新等级,返回旧等级"""
        old_level = await member_repo.update_level(1, 3)
        assert old_level == 1
        new_level = await member_repo.get_level(1)
        assert new_level == 3

    async def test_list_all(self, seeded_redis, member_repo):
        """列出所有会员"""
        # 初始 1 个
        members = await member_repo.list_all()
        assert len(members) >= 1
        # 新增 1 个
        await member_repo.create({
            "phone": "13900000000", "password": "x", "nickname": "新",
            "level": 1, "growth_value": 0, "points": 0, "status": 1,
        })
        members = await member_repo.list_all()
        assert len(members) >= 2

    async def test_delete_member(self, seeded_redis, member_repo):
        """删除会员"""
        # 新增一个临时会员
        member = await member_repo.create({
            "phone": "13900000000", "password": "x", "nickname": "临时",
            "level": 1, "growth_value": 0, "points": 0, "status": 1,
        })
        mid = member["id"]
        await member_repo.delete(mid)
        assert await member_repo.get_by_id(mid) is None
        # 手机号索引也应删除
        assert await member_repo.get_by_phone("13900000000") is None

    async def test_delete_not_found(self, seeded_redis, member_repo):
        """删除不存在的会员: KeyError"""
        with pytest.raises(KeyError):
            await member_repo.delete(999)


# ============================================================
# 测试类: 类型一致性
# ============================================================

class TestMemberTypeConsistency:
    """验证 Redis 读写后类型保持一致"""

    async def test_int_fields_restored(self, seeded_redis, member_repo):
        """int 字段反序列化正确"""
        member = await member_repo.get_by_id(1)
        assert isinstance(member["id"], int)
        assert isinstance(member["level"], int)
        assert isinstance(member["growth_value"], int)
        assert isinstance(member["points"], int)
        assert isinstance(member["status"], int)
        assert isinstance(member["gender"], int)

    async def test_add_growth_returns_int(self, seeded_redis, member_repo):
        """add_growth 返回 int 类型"""
        result = await member_repo.add_growth(1, 100)
        assert isinstance(result, int)

    async def test_add_points_returns_int(self, seeded_redis, member_repo):
        """add_points 返回 int 类型"""
        result = await member_repo.add_points(1, 50)
        assert isinstance(result, int)


# ============================================================
# 测试类: 收货地址
# ============================================================

class TestMemberAddresses:
    """收货地址 CRUD(Redis 模式)"""

    async def test_list_addresses(self, seeded_redis, member_repo):
        """地址列表"""
        addrs = await member_repo.list_addresses(1)
        assert len(addrs) == 1
        assert addrs[0]["name"] == "张三"

    async def test_list_addresses_member_not_found(self, seeded_redis, member_repo):
        """地址列表:会员不存在 KeyError"""
        with pytest.raises(KeyError):
            await member_repo.list_addresses(999)

    async def test_save_and_get_address(self, seeded_redis, member_repo):
        """新增并查询地址"""
        addr_data = {
            "name": "李四", "phone": "13900000000",
            "province": "北京市", "city": "北京市",
            "district": "朝阳区", "detail": "三里屯",
            "is_default": 0, "created_at": "2026-08-21",
        }
        saved = await member_repo.save_address(1, "addr_test_001", addr_data)
        assert saved["id"] == "addr_test_001"
        assert saved["user_id"] == 1

        # 查询
        addr = await member_repo.get_address(1, "addr_test_001")
        assert addr is not None
        assert addr["name"] == "李四"
        assert addr["detail"] == "三里屯"

    async def test_get_address_not_found(self, seeded_redis, member_repo):
        """查询不存在的地址:返回 None"""
        addr = await member_repo.get_address(1, "no_such_addr")
        assert addr is None

    async def test_delete_address(self, seeded_redis, member_repo):
        """删除地址"""
        await member_repo.save_address(1, "addr_del", {
            "name": "x", "phone": "x", "province": "x",
            "city": "x", "district": "x", "detail": "x",
            "is_default": 0, "created_at": "x",
        })
        deleted = await member_repo.delete_address(1, "addr_del")
        assert deleted is True
        # 再删一次应失败
        deleted2 = await member_repo.delete_address(1, "addr_del")
        assert deleted2 is False

    async def test_clear_default_addresses(self, seeded_redis, member_repo):
        """清除默认标记"""
        # 初始 addr_seed_001 是默认
        addrs = await member_repo.list_addresses(1)
        assert addrs[0]["is_default"] == 1

        # 清除默认
        await member_repo.clear_default_addresses(1)

        # 验证
        addrs = await member_repo.list_addresses(1)
        assert addrs[0]["is_default"] == 0

    async def test_overwrite_address(self, seeded_redis, member_repo):
        """覆盖已有地址"""
        # 修改 addr_seed_001
        await member_repo.save_address(1, "addr_seed_001", {
            "name": "张三丰", "phone": "13800000001",
            "province": "山东省", "city": "泰安市",
            "district": "泰山区", "detail": "新地址",
            "is_default": 1, "created_at": "2026-08-21",
        })
        addr = await member_repo.get_address(1, "addr_seed_001")
        assert addr["name"] == "张三丰"
        assert addr["detail"] == "新地址"

    async def test_address_json_integrity(self, seeded_redis, member_repo):
        """地址 JSON 序列化/反序列化完整性(中文不乱码)"""
        addr_data = {
            "name": "王小明", "phone": "13800000000",
            "province": "黑龙江省", "city": "哈尔滨市",
            "district": "南岗区", "detail": "中央大街 123 号",
            "is_default": 1, "created_at": "2026-08-21",
        }
        await member_repo.save_address(1, "addr_cn_test", addr_data)
        addr = await member_repo.get_address(1, "addr_cn_test")
        assert addr["name"] == "王小明"
        assert addr["province"] == "黑龙江省"
        assert addr["city"] == "哈尔滨市"
        assert addr["detail"] == "中央大街 123 号"


# ============================================================
# 测试类: 持久化(新实例读取)
# ============================================================

class TestMemberPersistence:
    """验证数据持久化:新 Repository 实例可读到之前写入的数据"""

    async def test_new_instance_reads_data(self, seeded_redis, member_repo):
        """新 Repository 实例可读取 seed 数据"""
        # member_repo 是第一个实例
        await member_repo.add_points(1, 50)

        # 创建新实例,应能读到更新后的数据
        from repositories.member_repository import MemberRepository
        new_repo = MemberRepository()
        member = await new_repo.get_by_id(1)
        assert member is not None
        assert member["points"] == 150  # 100 + 50

    async def test_new_instance_reads_new_member(self, seeded_redis, member_repo):
        """新实例可读取新创建的会员"""
        new_member = await member_repo.create({
            "phone": "13900000000", "password": "x", "nickname": "持久化测试",
            "level": 1, "growth_value": 0, "points": 200, "status": 1,
        })

        from repositories.member_repository import MemberRepository
        new_repo = MemberRepository()
        member = await new_repo.get_by_id(new_member["id"])
        assert member is not None
        assert member["nickname"] == "持久化测试"
        assert member["points"] == 200


# ============================================================
# 测试类: Service 层(Redis 模式 + 锁)
# ============================================================

class TestMemberServiceRedis:
    """MemberService 在 Redis 模式下的业务逻辑"""

    async def test_register_success(self, seeded_redis, member_service):
        """注册成功(含手机号索引 + 积分赠送)"""
        result = await member_service.register("13900000000", "abc123456", "新用户")
        assert result["success"] is True
        assert result["phone"] == "13900000000"
        assert result["points"] == 100
        assert "token" in result

    async def test_register_duplicate(self, seeded_redis, member_service):
        """重复注册: ValueError"""
        with pytest.raises(ValueError, match="已注册"):
            await member_service.register("13800000001", "abc123456")

    async def test_login_success(self, seeded_redis, member_service):
        """登录成功(密码校验)"""
        result = await member_service.login("13800000001", "test123456")
        assert result["success"] is True
        assert result["memberId"] == 1

    async def test_login_wrong_password(self, seeded_redis, member_service):
        """密码错误: ValueError"""
        with pytest.raises(ValueError, match="密码错误"):
            await member_service.login("13800000001", "wrongpwd")

    async def test_consume_and_level_up(self, seeded_redis, member_service):
        """消费触发升级"""
        result = await member_service.consume(1, 500)
        assert result["leveledUp"] is True
        assert result["fromLevel"] == 1
        assert result["toLevel"] == 2
        assert result["levelName"] == "竹叶会员"

    async def test_deduct_points_success(self, seeded_redis, member_service):
        """积分抵扣成功"""
        result = await member_service.deduct_points(1, 100, order_amount=1000)
        assert result["success"] is True
        assert result["leftPoints"] == 0

    async def test_deduct_points_insufficient(self, seeded_redis, member_service):
        """积分不足: ValueError"""
        with pytest.raises(ValueError, match="积分不足"):
            await member_service.deduct_points(1, 500)  # 当前 100

    async def test_add_address(self, seeded_redis, member_service):
        """新增地址(含默认清除)"""
        result = await member_service.add_address(
            1, "李四", "13900000000", "北京市", "北京市", "朝阳区", "三里屯", 1
        )
        assert result["success"] is True
        assert result["address"]["name"] == "李四"
        assert result["address"]["is_default"] == 1

        # 旧地址应不再是默认
        from repositories.member_repository import MemberRepository
        repo = MemberRepository()
        old_addr = await repo.get_address(1, "addr_seed_001")
        assert old_addr["is_default"] == 0
