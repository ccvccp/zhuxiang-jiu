"""限时秒杀模块 Redis 后端集成测试(需 Redis 服务)

验证内容(全部在 STORE_MODE=redis + LOCK_MODE=redis 下执行):
    1. Repository 双模式切换: Redis Hash 写入/读取/类型还原
    2. 序列化往返: 容器字段(list/dict)JSON 编解码不丢失
    3. 序列号: Redis INCR 递增(编号格式 FS{date}-{seq})
    4. Service 全链路: 建场/加商品/发布/抢购/支付/取消回补
    5. 防超卖: Redis 分布式锁下 10 并发抢 5 库存恰好成交 5
    6. 幂等: 同会员重复下单 409
    7. 持久化: 数据写入后可被新 Repository 实例读取
    8. 参数配置: Redis 单例读写 + 白名单字段过滤

运行前提:
    - Redis 服务已启动(默认 redis://127.0.0.1:6379/0)

运行方式:
    # 仅运行秒杀 Redis 集成测试
    py -m pytest test_flashsale_redis_integration.py -m redis -v

    # 跳过 Redis 测试(无 Redis 环境时)
    py -m pytest -m "not redis"
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone, UTC
from pathlib import Path

import pytest

# 路径设置
BACKEND_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BACKEND_DIR))

# Redis 连接地址(与 backend.py 默认值一致)
_REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

# 测试数据隔离前缀(全部测试通过 _fresh_service 重新生成, 天然隔离)


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


@pytest.fixture(autouse=True)
async def _reset_shared_redis_clients():
    """每个测试后重置共享 Redis 客户端单例(跨事件循环隔离)

    pytest-asyncio 默认每个测试一个新事件循环; repositories.backend 与
    core.locks 的模块级单例客户端会把连接绑定到首个使用它的循环,
    循环关闭后连接池残留死连接 → 后续测试 RuntimeError: Event loop is closed。
    测试后置空单例, 下个测试在自己的循环上重建连接。
    """
    yield
    import contextlib
    import repositories.backend as _backend
    import core.locks as _locks
    for mod in (_backend, _locks):
        client = getattr(mod, "_redis_client", None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()
        mod._redis_client = None


@pytest.fixture
async def seeded_products():
    """播种产品主数据(add_item/purchase 校验产品必须存在, Redis 模式下产品走 Redis)"""
    sys.path.insert(0, str(BACKEND_DIR / "scripts"))
    import redis.asyncio as redis
    from seed_redis import seed_products
    client = redis.from_url(_REDIS_URL, decode_responses=True)
    try:
        await seed_products(client)
    finally:
        await client.aclose()


@pytest.fixture
async def svc(seeded_products):
    """秒杀服务实例(Redis 模式下 member/product/flash 数据均走 Redis)"""
    from services.flashsale_service import FlashSaleService
    return FlashSaleService()


@pytest.fixture
def member_repo():
    """会员 Repository(Redis 模式下数据走 Redis)"""
    from repositories.member_repository import MemberRepository
    return MemberRepository()


async def _mk_member(member_repo, phone: str, hours_old: float = 100,
                     level: int = 3) -> dict:
    """创建测试会员(注册时间可调)"""
    created = datetime.now(UTC) - timedelta(hours=hours_old)
    return await member_repo.create({
        "phone": phone, "nickname": f"测试{phone[-4:]}",
        "password": "x" * 64, "status": 1, "role": "member",
        "level": level, "growth_value": 600, "points": 0,
        "created_at": created.isoformat(),
    })


async def _mk_active_session(service, flash_stock: int = 5,
                             limit: int = 1) -> tuple:
    """创建进行中场次+商品(开始于 1 小时前, 结束于 1 小时后)

    产品用种子数据中的 ZX42-2026B01(原价 88), 秒杀价须低于原价。
    """
    start = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    end = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    session = await service.create_session("Redis集成测试场", start, end)
    item = await service.add_item(session["sessionId"], "ZX42-2026B01",
                                  flash_price=58.0, flash_stock=flash_stock,
                                  limit_per_member=limit)
    await service.publish_session(session["sessionId"])
    return session, item


# ============================================================
# 1. Repository 层: Redis 读写与序列化
# ============================================================

class TestFlashRepoRedis:

    async def test_session_roundtrip(self, svc):
        """场次写入 Redis 后读回, 字段与类型完整"""
        await svc.flash_repo.save_session({
            "sessionId": "FST-RT-001", "name": "往返测试", "status": "draft",
            "startTime": "2026-08-22T10:00:00+00:00",
            "endTime": "2026-08-22T12:00:00+00:00",
            "createdAt": "2026-08-22T09:00:00+00:00",
        })
        loaded = await svc.flash_repo.get_session("FST-RT-001")
        assert loaded is not None
        assert loaded["sessionId"] == "FST-RT-001"
        assert loaded["name"] == "往返测试"
        assert loaded["status"] == "draft"

    async def test_container_field_json_roundtrip(self, svc):
        """容器字段(list/dict)经 JSON 编解码不丢失"""
        await svc.flash_repo.save_item({
            "itemId": "FIT-RT-001", "sessionId": "FST-RT-001",
            "productId": "P10001", "flashPrice": 99.0,
            "flashStock": 5, "soldCount": 2, "limitPerMember": 1,
            "status": "active", "createdAt": "2026-08-22T09:00:00+00:00",
            "tags": ["hot", "new"],                    # list 字段
            "extra": {"channel": "app", "weight": 3},  # dict 字段
        })
        loaded = await svc.flash_repo.get_item("FIT-RT-001")
        assert loaded["tags"] == ["hot", "new"]
        assert loaded["extra"] == {"channel": "app", "weight": 3}

    async def test_persistence_new_repo_instance(self, svc):
        """持久化: 新 Repository 实例可读取已写入数据(证明走 Redis 而非内存)"""
        await svc.flash_repo.save_order({
            "orderNo": "FOT-RT-001", "memberId": 77, "sessionId": "FST-RT-001",
            "itemId": "FIT-RT-001", "quantity": 1, "unitPrice": 99.0,
            "totalAmount": 99.0, "status": "pending_payment",
            "createdAt": "2026-08-22T11:00:00+00:00",
        })
        from repositories.flashsale_repository import FlashSaleRepository
        fresh = FlashSaleRepository()  # 新实例, 内存 store 不含该订单
        loaded = await fresh.get_order("FOT-RT-001")
        assert loaded is not None
        assert loaded["memberId"] == 77
        assert loaded["status"] == "pending_payment"

    async def test_seq_incr_format(self, svc):
        """序列号: Redis INCR 递增, 编号格式 FS{date}-{seq}"""
        first = await svc.flash_repo.next_session_id()
        second = await svc.flash_repo.next_session_id()
        assert first.startswith("FS") and "-" in first
        # 两次编号递增(后缀序号相差 1)
        seq1 = int(first.split("-")[-1])
        seq2 = int(second.split("-")[-1])
        assert seq2 == seq1 + 1

    async def test_settings_singleton(self, svc):
        """参数配置: Redis 单例读写"""
        settings = await svc.flash_repo.get_settings()
        assert settings["enabled"] is True
        settings["orderExpireMinutes"] = 30
        await svc.flash_repo.save_settings(settings)
        reloaded = await svc.flash_repo.get_settings()
        assert reloaded["orderExpireMinutes"] == 30

    async def test_list_orders_by_member_reverse(self, svc):
        """会员订单列表: 按创建时间倒序"""
        for i, no in enumerate(("FOT-M-003", "FOT-M-001", "FOT-M-002")):
            await svc.flash_repo.save_order({
                "orderNo": no, "memberId": 88, "sessionId": "S",
                "itemId": "I", "quantity": 1, "unitPrice": 1.0,
                "totalAmount": 1.0, "status": "pending_payment",
                "createdAt": f"2026-08-22T11:00:0{i}:00+00:00",
            })
        orders = await svc.flash_repo.list_orders_by_member(88)
        assert [o["orderNo"] for o in orders] == ["FOT-M-003", "FOT-M-002", "FOT-M-001"]


# ============================================================
# 2. Service 层: Redis 模式全链路
# ============================================================

class TestFlashServiceRedis:

    async def test_purchase_full_flow(self, svc, member_repo):
        """全链路: 建场→加商品→发布→抢购→支付"""
        member = await _mk_member(member_repo, "13988000001")
        session, item = await _mk_active_session(svc, flash_stock=5)

        order = await svc.purchase(member["id"], session["sessionId"],
                                   item["itemId"], 1)
        assert order["status"] == "pending_payment"
        assert order["totalAmount"] == 58.0

        paid = await svc.pay_order(order["orderNo"])
        assert paid["status"] == "paid"

        detail = await svc.get_session_detail(session["sessionId"])
        assert detail["items"][0]["soldCount"] == 1

    async def test_idempotent_purchase_conflict(self, svc, member_repo):
        """幂等: 同会员+同商品重复下单 → ValueError(409)"""
        member = await _mk_member(member_repo, "13988000002")
        session, item = await _mk_active_session(svc, flash_stock=5)
        await svc.purchase(member["id"], session["sessionId"], item["itemId"], 1)
        with pytest.raises(ValueError):
            await svc.purchase(member["id"], session["sessionId"], item["itemId"], 1)

    async def test_cancel_restocks(self, svc, member_repo):
        """取消订单回补库存"""
        member = await _mk_member(member_repo, "13988000003")
        session, item = await _mk_active_session(svc, flash_stock=5)
        order = await svc.purchase(member["id"], session["sessionId"],
                                   item["itemId"], 1)
        await svc.cancel_order(order["orderNo"], member["id"], "测试取消")
        detail = await svc.get_session_detail(session["sessionId"])
        assert detail["items"][0]["soldCount"] == 0
        assert detail["items"][0]["remainingStock"] == 5

    async def test_insufficient_stock_conflict(self, svc, member_repo):
        """库存不足 → ValueError(409)"""
        member = await _mk_member(member_repo, "13988000004")
        session, item = await _mk_active_session(svc, flash_stock=1)
        await svc.purchase(member["id"], session["sessionId"], item["itemId"], 1)
        other = await _mk_member(member_repo, "13988000005")
        with pytest.raises(ValueError):
            await svc.purchase(other["id"], session["sessionId"], item["itemId"], 1)

    async def test_settings_whitelist_update(self, svc, member_repo):
        """参数白名单: 非法字段被过滤, 合法字段生效"""
        updated = await svc.update_settings(
            {"enabled": True, "orderExpireMinutes": 25, "hackField": "x"})
        assert updated["orderExpireMinutes"] == 25
        assert "hackField" not in updated


# ============================================================
# 3. 并发: Redis 分布式锁下的防超卖
# ============================================================

class TestConcurrencyRedis:

    async def test_concurrent_10_buy_5_stock(self, svc, member_repo):
        """防超卖: 10 并发抢 5 库存, 恰好成交 5 单(Redis 锁)"""
        session, item = await _mk_active_session(svc, flash_stock=5, limit=1)
        members = [(await _mk_member(member_repo, f"1398801{i:04d}"))["id"]
                   for i in range(10)]

        async def try_buy(mid):
            try:
                await svc.purchase(mid, session["sessionId"], item["itemId"], 1)
                return True
            except ValueError:
                return False

        results = await asyncio.gather(*[try_buy(m) for m in members])
        assert sum(results) == 5, f"应恰好成交 5 单, 实际 {sum(results)}"

        detail = await svc.get_session_detail(session["sessionId"])
        assert detail["items"][0]["soldCount"] == 5
        assert detail["items"][0]["remainingStock"] == 0

    async def test_purchase_limit_per_member(self, svc, member_repo):
        """限购: limit=1 时第二单被拒(取消后可再购)"""
        member = await _mk_member(member_repo, "13988000006")
        session, item = await _mk_active_session(svc, flash_stock=10, limit=1)
        order = await svc.purchase(member["id"], session["sessionId"],
                                   item["itemId"], 1)
        with pytest.raises(ValueError):
            await svc.purchase(member["id"], session["sessionId"], item["itemId"], 1)
        # 取消后限购解除
        await svc.cancel_order(order["orderNo"], member["id"], "限购重置")
        again = await svc.purchase(member["id"], session["sessionId"],
                                   item["itemId"], 1)
        assert again["status"] == "pending_payment"
