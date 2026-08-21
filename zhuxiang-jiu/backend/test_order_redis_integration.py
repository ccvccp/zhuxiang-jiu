"""订单管理模块 Redis 后端集成测试(需 Redis 服务)

验证内容:
    1. OrderRepository 在 Redis 模式下 CRUD 正常(主表 + 用户索引)
    2. 类型一致性(items 是 list / priceDetail 是 dict / status 是 str)
    3. 持久化:数据写入后可被新实例读取
    4. OrderService 全状态流转在 Redis 模式下正常
        (创建/支付/取消/发货/收货/评价/退货/退款/超时处理/删除)
    5. 全链路:创建→支付→发货→收货→评价→退货→退款(Redis 持久化)
    6. 库存集成:创建扣库存/取消释放库存/退款回滚库存
    7. 积分集成:创建冻结积分/取消退还积分/退款扣回积分

运行前提:
    - Redis 服务已启动(默认 redis://127.0.0.1:6379/0)
    - 已执行 seed_redis.py 初始化数据(测试中自动 seed)

运行方式:
    # 仅运行订单 Redis 集成测试
    py -m pytest test_order_redis_integration.py -m redis -v

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
def order_repo():
    """OrderRepository(Redis 模式)"""
    from repositories.order_repository import OrderRepository
    return OrderRepository()


@pytest.fixture
def order_service():
    """OrderService(Redis 模式, 含锁; 复用 Member/Inventory Repository)"""
    from services.order_service import OrderService
    return OrderService()


@pytest.fixture
def inventory_repo():
    """InventoryRepository(Redis 模式, 用于库存断言)"""
    from repositories.inventory_repository import InventoryRepository
    return InventoryRepository()


@pytest.fixture
def member_repo():
    """MemberRepository(Redis 模式, 用于积分断言)"""
    from repositories.member_repository import MemberRepository
    return MemberRepository()


# ============================================================
# 公共数据(seed: ZX42-2026L07 stock=500, 会员 1 points=100 level=1)
# ============================================================

ITEMS = [
    {
        "productId": "ZX42-2026L07",
        "productName": "竹奕·竹香型 42° 500ml",
        "quantity": 2,
        "unitPrice": 268.00,
    }
]

ADDRESS = {
    "name": "张三",
    "phone": "13800000001",
    "province": "山东省",
    "city": "泰安市",
    "district": "泰山区",
    "detail": "竹香路1号",
}


# ============================================================
# 测试类: Repository CRUD
# ============================================================

class TestOrderRepoCRUD:
    """OrderRepository 在 Redis 模式下的 CRUD(主表 SET + 用户索引 SADD)"""

    async def test_create(self, seeded_redis, order_repo):
        """create: 写入订单主表 + 用户订单索引"""
        order_id = "RT_REPO_CREATE_001"
        order = {
            "orderId": order_id,
            "memberId": 1,
            "status": "PENDING",
            "items": [{"productId": "ZX42-2026L07", "quantity": 2}],
            "priceDetail": {"actualAmount": 536.00},
            "createdAt": "2026-08-21T00:00:00",
        }
        returned_id = await order_repo.create(order)
        assert returned_id == order_id

        # 主表可读
        fetched = await order_repo.get_by_id(order_id)
        assert fetched is not None
        assert fetched["orderId"] == order_id
        assert fetched["memberId"] == 1

        # 用户订单索引(zhuxiang:order:user:1)
        from repositories.backend import _k
        member_orders = await seeded_redis.smembers(_k("order", "user", 1))
        assert order_id in member_orders

    async def test_get_by_id(self, seeded_redis, order_repo):
        """get_by_id: 查询存在的订单"""
        order_id = "RT_REPO_GET_001"
        await order_repo.create({
            "orderId": order_id, "memberId": 1, "status": "PENDING",
            "items": [], "createdAt": "2026-08-21T00:00:00",
        })

        fetched = await order_repo.get_by_id(order_id)
        assert fetched is not None
        assert fetched["orderId"] == order_id
        assert fetched["status"] == "PENDING"

        # 不存在的订单返回 None
        assert await order_repo.get_by_id("NOT_EXIST") is None

    async def test_get_by_member(self, seeded_redis, order_repo):
        """get_by_member: 按会员查询订单(含状态筛选)"""
        await order_repo.create({
            "orderId": "RT_MEMBER_001", "memberId": 1, "status": "PENDING",
            "items": [], "createdAt": "2026-08-21T00:00:00",
        })
        await order_repo.create({
            "orderId": "RT_MEMBER_002", "memberId": 1, "status": "PAID",
            "items": [], "createdAt": "2026-08-21T00:01:00",
        })

        # 全部订单
        orders = await order_repo.get_by_member(1)
        assert len(orders) == 2
        order_ids = {o["orderId"] for o in orders}
        assert order_ids == {"RT_MEMBER_001", "RT_MEMBER_002"}

        # 按状态筛选
        paid_orders = await order_repo.get_by_member(1, status="PAID")
        assert len(paid_orders) == 1
        assert paid_orders[0]["orderId"] == "RT_MEMBER_002"

    async def test_update_status(self, seeded_redis, order_repo):
        """update_status: 更新状态, 返回旧状态"""
        await order_repo.create({
            "orderId": "RT_STATUS_001", "memberId": 1, "status": "PENDING",
            "items": [], "createdAt": "2026-08-21T00:00:00",
        })

        old_status = await order_repo.update_status("RT_STATUS_001", "PAID")
        assert old_status == "PENDING"

        fetched = await order_repo.get_by_id("RT_STATUS_001")
        assert fetched["status"] == "PAID"

    async def test_update_fields(self, seeded_redis, order_repo):
        """update_fields: 部分字段更新, 返回完整订单"""
        await order_repo.create({
            "orderId": "RT_FIELDS_001", "memberId": 1, "status": "PENDING",
            "items": [], "remark": "", "createdAt": "2026-08-21T00:00:00",
        })

        updated = await order_repo.update_fields("RT_FIELDS_001", {
            "remark": "加急订单", "status": "PAID",
        })
        assert updated["remark"] == "加急订单"
        assert updated["status"] == "PAID"
        # 其他字段保留
        assert updated["memberId"] == 1
        assert updated["orderId"] == "RT_FIELDS_001"

    async def test_list_by_status(self, seeded_redis, order_repo):
        """list_by_status: 按状态查询订单"""
        await order_repo.create({
            "orderId": "RT_LS_001", "memberId": 1, "status": "PENDING",
            "items": [], "createdAt": "2026-08-21T00:00:00",
        })
        await order_repo.create({
            "orderId": "RT_LS_002", "memberId": 1, "status": "PAID",
            "items": [], "createdAt": "2026-08-21T00:01:00",
        })
        await order_repo.create({
            "orderId": "RT_LS_003", "memberId": 1, "status": "PENDING",
            "items": [], "createdAt": "2026-08-21T00:02:00",
        })

        pending = await order_repo.list_by_status("PENDING")
        assert len(pending) == 2
        paid = await order_repo.list_by_status("PAID")
        assert len(paid) == 1
        assert paid[0]["orderId"] == "RT_LS_002"

    async def test_save(self, seeded_redis, order_repo):
        """save: 覆盖保存订单(保留 orderId + 重建用户索引)"""
        await order_repo.create({
            "orderId": "RT_SAVE_001", "memberId": 1, "status": "PENDING",
            "items": [{"productId": "X", "quantity": 1}],
            "createdAt": "2026-08-21T00:00:00",
        })

        # 覆盖保存
        new_data = {
            "memberId": 1, "status": "COMPLETED",
            "items": [{"productId": "Y", "quantity": 2}],
            "updatedAt": "2026-08-21T01:00:00",
        }
        saved = await order_repo.save("RT_SAVE_001", new_data)
        assert saved["orderId"] == "RT_SAVE_001"  # orderId 被保留
        assert saved["status"] == "COMPLETED"

        # 验证落库
        fetched = await order_repo.get_by_id("RT_SAVE_001")
        assert fetched["status"] == "COMPLETED"
        assert fetched["items"] == [{"productId": "Y", "quantity": 2}]

        # 用户索引仍在
        from repositories.backend import _k
        member_orders = await seeded_redis.smembers(_k("order", "user", 1))
        assert "RT_SAVE_001" in member_orders

    async def test_delete(self, seeded_redis, order_repo):
        """delete: 删除订单主表 + 用户索引"""
        await order_repo.create({
            "orderId": "RT_DEL_001", "memberId": 1, "status": "PENDING",
            "items": [], "createdAt": "2026-08-21T00:00:00",
        })
        assert await order_repo.get_by_id("RT_DEL_001") is not None

        await order_repo.delete("RT_DEL_001")

        # 主表已删
        assert await order_repo.get_by_id("RT_DEL_001") is None
        # 用户索引已删
        from repositories.backend import _k
        member_orders = await seeded_redis.smembers(_k("order", "user", 1))
        assert "RT_DEL_001" not in member_orders

    async def test_delete_not_found(self, seeded_redis, order_repo):
        """delete: 订单不存在 KeyError"""
        with pytest.raises(KeyError):
            await order_repo.delete("NOT_EXIST_999")


# ============================================================
# 测试类: 持久化(新实例读取)
# ============================================================

class TestOrderRepoPersistence:
    """验证数据持久化:新 Repository 实例可读到之前写入的数据"""

    async def test_new_instance_reads_data(self, seeded_redis, order_repo):
        """新 Repository 实例可读取已写入的订单"""
        await order_repo.create({
            "orderId": "RT_PERSIST_001", "memberId": 1, "status": "PENDING",
            "items": [{"productId": "ZX42-2026L07", "quantity": 2}],
            "createdAt": "2026-08-21T00:00:00",
        })

        from repositories.order_repository import OrderRepository
        new_repo = OrderRepository()
        fetched = await new_repo.get_by_id("RT_PERSIST_001")
        assert fetched is not None
        assert fetched["orderId"] == "RT_PERSIST_001"
        assert fetched["memberId"] == 1

    async def test_new_instance_reads_new_order(self, seeded_redis, order_repo):
        """新实例可读取新创建订单(含用户索引)"""
        await order_repo.create({
            "orderId": "RT_PERSIST_002", "memberId": 1, "status": "PAID",
            "items": [{"productId": "ZX42-2026L05", "quantity": 1}],
            "createdAt": "2026-08-21T00:00:00",
        })

        from repositories.order_repository import OrderRepository
        new_repo = OrderRepository()
        orders = await new_repo.get_by_member(1)
        order_ids = {o["orderId"] for o in orders}
        assert "RT_PERSIST_002" in order_ids

        # count 也应包含新订单
        assert await new_repo.count() >= 1


# ============================================================
# 测试类: 类型一致性
# ============================================================

class TestOrderRepoTypeConsistency:
    """验证 Redis 读写后字段类型保持一致(JSON 反序列化)"""

    async def test_items_is_list(self, seeded_redis, order_repo):
        """items 字段类型保持为 list"""
        await order_repo.create({
            "orderId": "RT_TYPE_001", "memberId": 1, "status": "PENDING",
            "items": [
                {"productId": "ZX42-2026L07", "quantity": 2, "unitPrice": 268.0},
                {"productId": "ZX42-2026L05", "quantity": 1, "unitPrice": 198.0},
            ],
            "priceDetail": {"actualAmount": 734.0},
            "createdAt": "2026-08-21T00:00:00",
        })
        fetched = await order_repo.get_by_id("RT_TYPE_001")
        assert isinstance(fetched["items"], list)
        assert len(fetched["items"]) == 2
        assert isinstance(fetched["items"][0], dict)
        assert fetched["items"][0]["quantity"] == 2

    async def test_price_detail_is_dict(self, seeded_redis, order_repo):
        """priceDetail 字段类型保持为 dict"""
        await order_repo.create({
            "orderId": "RT_TYPE_002", "memberId": 1, "status": "PENDING",
            "items": [],
            "priceDetail": {
                "goodsTotal": 536.0, "memberDiscount": 0.0,
                "actualAmount": 536.0, "discountRate": 1.0,
            },
            "createdAt": "2026-08-21T00:00:00",
        })
        fetched = await order_repo.get_by_id("RT_TYPE_002")
        assert isinstance(fetched["priceDetail"], dict)
        assert fetched["priceDetail"]["goodsTotal"] == 536.0
        assert fetched["priceDetail"]["actualAmount"] == 536.0

    async def test_status_is_str(self, seeded_redis, order_repo):
        """status 字段类型保持为 str"""
        await order_repo.create({
            "orderId": "RT_TYPE_003", "memberId": 1, "status": "PENDING",
            "items": [], "createdAt": "2026-08-21T00:00:00",
        })
        fetched = await order_repo.get_by_id("RT_TYPE_003")
        assert isinstance(fetched["status"], str)
        assert fetched["status"] == "PENDING"

        # 状态更新后仍是 str
        await order_repo.update_status("RT_TYPE_003", "PAID")
        fetched = await order_repo.get_by_id("RT_TYPE_003")
        assert isinstance(fetched["status"], str)
        assert fetched["status"] == "PAID"


# ============================================================
# 测试类: Service 层全状态流转(Redis 模式 + 锁)
# ============================================================

class TestOrderServiceRedis:
    """OrderService 在 Redis 模式下的全状态流转"""

    async def test_create(self, seeded_redis, order_service):
        """创建订单: PENDING + 库存预扣 + 订单落库"""
        result = await order_service.create(
            member_id=1, items=ITEMS, address=ADDRESS,
            use_points=0, remark="测试订单",
        )
        assert result["success"] is True
        assert result["status"] == "PENDING"
        assert result["statusName"] == "待付款"
        assert result["memberId"] == 1
        assert result["orderId"].startswith("RT")
        assert result["priceDetail"]["actualAmount"] == 536.0

        # 订单已落库, 可通过 get_by_id 读取
        fetched = await order_service.get_by_id(result["orderId"])
        assert fetched["order"]["orderId"] == result["orderId"]
        assert fetched["order"]["status"] == "PENDING"

    async def test_pay(self, seeded_redis, order_service):
        """支付: PENDING → PAID(含会员消费 +成长值/积分)"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]

        result = await order_service.pay(order_id, "wechat")
        assert result["success"] is True
        assert result["status"] == "PAID"
        assert result["statusName"] == "待发货"
        assert result["payment"]["method"] == "wechat"
        assert result["payment"]["tradeNo"] != ""
        assert result["consumedPoints"] == 536  # int(actualAmount)

        # 状态异常: 重复支付 ValueError
        with pytest.raises(ValueError, match="订单状态异常"):
            await order_service.pay(order_id, "wechat")

    async def test_cancel(self, seeded_redis, order_service):
        """取消: PENDING → CANCELLED(释放库存 + 退还积分)"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]

        result = await order_service.cancel(order_id, "用户取消")
        assert result["success"] is True
        assert result["status"] == "CANCELLED"
        assert result["statusName"] == "已取消"

        # 状态异常: 已取消再取消 ValueError
        with pytest.raises(ValueError, match="订单状态异常"):
            await order_service.cancel(order_id, "再次取消")

    async def test_ship(self, seeded_redis, order_service):
        """发货: PAID → SHIPPED"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]
        await order_service.pay(order_id, "wechat")

        result = await order_service.ship(order_id, "顺丰速运", "SF12345678")
        assert result["success"] is True
        assert result["status"] == "SHIPPED"
        assert result["statusName"] == "待收货"
        assert result["logistics"]["carrier"] == "顺丰速运"
        assert result["logistics"]["waybillNo"] == "SF12345678"
        assert result["logistics"]["shippedAt"] != ""

    async def test_confirm(self, seeded_redis, order_service):
        """确认收货: SHIPPED → RECEIVED"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]
        await order_service.pay(order_id, "wechat")
        await order_service.ship(order_id, "顺丰速运", "SF12345678")

        result = await order_service.confirm(order_id)
        assert result["success"] is True
        assert result["status"] == "RECEIVED"
        assert result["statusName"] == "待评价"

        # 物流签收时间已写入
        fetched = await order_service.get_by_id(order_id)
        assert fetched["order"]["logistics"]["signedAt"] != ""

    async def test_review(self, seeded_redis, order_service):
        """评价: RECEIVED → COMPLETED(返还评价积分)"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]
        await order_service.pay(order_id, "wechat")
        await order_service.ship(order_id, "顺丰速运", "SF12345678")
        await order_service.confirm(order_id)

        result = await order_service.review(order_id, 5, "酒香醇厚, 好评")
        assert result["success"] is True
        assert result["status"] == "COMPLETED"
        assert result["statusName"] == "已完成"
        assert result["rewardPoints"] == 100  # 5 星返 100

        # 评价已落库
        fetched = await order_service.get_by_id(order_id)
        assert fetched["order"]["review"]["rating"] == 5
        assert fetched["order"]["review"]["content"] == "酒香醇厚, 好评"

    async def test_apply_return(self, seeded_redis, order_service):
        """申请退货: COMPLETED → RETURNING"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]
        await order_service.pay(order_id, "wechat")
        await order_service.ship(order_id, "顺丰速运", "SF12345678")
        await order_service.confirm(order_id)
        await order_service.review(order_id, 5, "好评")

        result = await order_service.apply_return(order_id, "商品有瑕疵")
        assert result["success"] is True
        assert result["status"] == "RETURNING"
        assert result["statusName"] == "退货中"

        fetched = await order_service.get_by_id(order_id)
        assert fetched["order"]["refund"]["reason"] == "商品有瑕疵"

    async def test_refund(self, seeded_redis, order_service):
        """退款: RETURNING → REFUNDED(库存回滚 + 积分扣回)"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]
        await order_service.pay(order_id, "wechat")
        await order_service.ship(order_id, "顺丰速运", "SF12345678")
        await order_service.confirm(order_id)
        await order_service.review(order_id, 5, "好评")
        await order_service.apply_return(order_id, "不喜欢")

        result = await order_service.refund(order_id)
        assert result["success"] is True
        assert result["status"] == "REFUNDED"
        assert result["statusName"] == "已退款"
        assert result["refundedAmount"] == 536.0

        fetched = await order_service.get_by_id(order_id)
        assert fetched["order"]["refund"]["refundedAt"] != ""
        assert fetched["order"]["refund"]["refundedAmount"] == 536.0

    async def test_timeout_close(self, seeded_redis, order_service):
        """超时关闭: PENDING → CLOSED(释放库存 + 退还积分)"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]

        result = await order_service.timeout_close(order_id)
        assert result["success"] is True
        assert result["status"] == "CLOSED"
        assert result["statusName"] == "已关闭"

        # 状态异常: 已关闭再关闭 ValueError
        with pytest.raises(ValueError, match="订单状态异常"):
            await order_service.timeout_close(order_id)

    async def test_timeout_confirm(self, seeded_redis, order_service):
        """超时自动确认收货: SHIPPED → RECEIVED"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]
        await order_service.pay(order_id, "wechat")
        await order_service.ship(order_id, "顺丰速运", "SF12345678")

        result = await order_service.timeout_confirm(order_id)
        assert result["success"] is True
        assert result["status"] == "RECEIVED"
        assert result["statusName"] == "待评价"

        fetched = await order_service.get_by_id(order_id)
        assert fetched["order"]["logistics"]["signedAt"] != ""

    async def test_timeout_complete(self, seeded_redis, order_service):
        """超时自动完成: RECEIVED → COMPLETED(默认五星)"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]
        await order_service.pay(order_id, "wechat")
        await order_service.ship(order_id, "顺丰速运", "SF12345678")
        await order_service.confirm(order_id)

        result = await order_service.timeout_complete(order_id)
        assert result["success"] is True
        assert result["status"] == "COMPLETED"
        assert result["statusName"] == "已完成"
        assert result["rewardPoints"] == 100  # 默认五星返 100

        fetched = await order_service.get_by_id(order_id)
        assert fetched["order"]["review"]["rating"] == 5
        assert fetched["order"]["review"]["content"] == "系统默认五星好评"

    async def test_delete(self, seeded_redis, order_service):
        """删除订单: 仅终态(CANCELLED/CLOSED/COMPLETED/REFUNDED)可删"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]
        await order_service.cancel(order_id, "测试删除")

        result = await order_service.delete(order_id)
        assert result["success"] is True

        # 已删除: get_by_id 抛 KeyError
        with pytest.raises(KeyError):
            await order_service.get_by_id(order_id)

        # 非终态不可删除: 创建后(PENDING)直接删除应 ValueError
        create_result2 = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        with pytest.raises(ValueError, match="仅终态订单可删除"):
            await order_service.delete(create_result2["orderId"])


# ============================================================
# 测试类: 全链路流程(Redis 持久化)
# ============================================================

class TestOrderFullFlowRedis:
    """全链路: 创建→支付→发货→收货→评价→退货→退款(Redis 持久化)"""

    async def test_full_flow_persisted(self, seeded_redis, order_service):
        """完整订单生命周期在 Redis 下贯通 + 持久化"""
        # 1. 创建
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "全链路测试")
        order_id = create_result["orderId"]
        assert create_result["status"] == "PENDING"

        # 2. 支付
        pay_result = await order_service.pay(order_id, "wechat")
        assert pay_result["status"] == "PAID"

        # 3. 发货
        ship_result = await order_service.ship(order_id, "顺丰速运", "SF_FLOW_001")
        assert ship_result["status"] == "SHIPPED"

        # 4. 确认收货
        confirm_result = await order_service.confirm(order_id)
        assert confirm_result["status"] == "RECEIVED"

        # 5. 评价
        review_result = await order_service.review(order_id, 5, "全链路好评")
        assert review_result["status"] == "COMPLETED"

        # 6. 申请退货
        apply_result = await order_service.apply_return(order_id, "退货测试")
        assert apply_result["status"] == "RETURNING"

        # 7. 退款
        refund_result = await order_service.refund(order_id)
        assert refund_result["status"] == "REFUNDED"
        assert refund_result["refundedAmount"] == 536.0

        # 持久化验证: 新 Repository 实例可读到最终状态
        from repositories.order_repository import OrderRepository
        new_repo = OrderRepository()
        reloaded = await new_repo.get_by_id(order_id)
        assert reloaded is not None
        assert reloaded["status"] == "REFUNDED"
        assert reloaded["memberId"] == 1
        # timeline 至少包含 7 个状态节点
        assert len(reloaded["timeline"]) >= 7
        timeline_statuses = [t["status"] for t in reloaded["timeline"]]
        for s in ["PENDING", "PAID", "SHIPPED", "RECEIVED",
                  "COMPLETED", "RETURNING", "REFUNDED"]:
            assert s in timeline_statuses


# ============================================================
# 测试类: 库存集成(创建扣库存/取消释放/退款回滚)
# ============================================================

class TestOrderStockIntegration:
    """订单与库存的集成: 扣减/释放/回滚"""

    async def test_create_deducts_stock(self, seeded_redis, order_service, inventory_repo):
        """创建订单: 扣减库存"""
        # seed 初始库存 500
        assert await inventory_repo.get_stock("ZX42-2026L07") == 500

        # 创建 2 件
        await order_service.create(1, ITEMS, ADDRESS, 0, "")

        # 库存扣减为 498
        assert await inventory_repo.get_stock("ZX42-2026L07") == 498

    async def test_cancel_releases_stock(self, seeded_redis, order_service, inventory_repo):
        """取消订单: 释放库存(回补)"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]
        assert await inventory_repo.get_stock("ZX42-2026L07") == 498

        await order_service.cancel(order_id, "不想要了")

        # 库存恢复到 500
        assert await inventory_repo.get_stock("ZX42-2026L07") == 500

    async def test_refund_rolls_back_stock(self, seeded_redis, order_service, inventory_repo):
        """退款: 回滚库存(退货入库)"""
        create_result = await order_service.create(1, ITEMS, ADDRESS, 0, "")
        order_id = create_result["orderId"]
        await order_service.pay(order_id, "wechat")
        await order_service.ship(order_id, "顺丰速运", "SF12345678")
        await order_service.confirm(order_id)
        await order_service.review(order_id, 5, "好评")
        await order_service.apply_return(order_id, "质量问题")

        # 退款前库存仍为 498(中间流程不回补)
        assert await inventory_repo.get_stock("ZX42-2026L07") == 498

        await order_service.refund(order_id)

        # 退款后库存回滚到 500
        assert await inventory_repo.get_stock("ZX42-2026L07") == 500


# ============================================================
# 测试类: 积分集成(创建冻结/取消退还/退款扣回)
# ============================================================

class TestOrderPointsIntegration:
    """订单与积分的集成: 冻结/退还/扣回"""

    async def test_create_freezes_points(self, seeded_redis, order_service, member_repo):
        """创建订单: 冻结(扣减)积分"""
        # seed 初始积分 100
        assert await member_repo.get_points(1) == 100

        # 使用 100 积分抵扣
        await order_service.create(1, ITEMS, ADDRESS, use_points=100, remark="")

        # 积分被冻结(扣减为 0)
        assert await member_repo.get_points(1) == 0

    async def test_cancel_refunds_points(self, seeded_redis, order_service, member_repo):
        """取消订单: 退还冻结的积分"""
        create_result = await order_service.create(
            1, ITEMS, ADDRESS, use_points=100, remark=""
        )
        order_id = create_result["orderId"]
        assert await member_repo.get_points(1) == 0

        await order_service.cancel(order_id, "不想要了")

        # 积分退还回 100
        assert await member_repo.get_points(1) == 100

    async def test_refund_deducts_consumed_points(self, seeded_redis, order_service, member_repo):
        """退款: 扣回支付时赠送的积分(消费积分)"""
        # 创建订单(不使用积分抵扣)
        create_result = await order_service.create(1, ITEMS, ADDRESS, use_points=0, remark="")
        order_id = create_result["orderId"]

        # 支付: 会员获得 consumedPoints(= int(actualAmount) = 536)
        pay_result = await order_service.pay(order_id, "wechat")
        consumed = pay_result["consumedPoints"]
        assert consumed == 536

        # 支付后积分 = 100 + 536
        assert await member_repo.get_points(1) == 100 + consumed

        # 完成全流程到退款
        await order_service.ship(order_id, "顺丰速运", "SF12345678")
        await order_service.confirm(order_id)
        review_result = await order_service.review(order_id, 5, "好评")
        reward = review_result["rewardPoints"]
        assert reward == 100

        # 评价后积分 = 100 + consumed + reward
        points_before_refund = await member_repo.get_points(1)
        assert points_before_refund == 100 + consumed + reward

        await order_service.apply_return(order_id, "质量问题")
        await order_service.refund(order_id)

        # 退款后: 扣回 consumed, use_points=0 无退还
        # 最终 = 100 + consumed + reward - consumed = 100 + reward
        points_after_refund = await member_repo.get_points(1)
        assert points_after_refund == 100 + reward
