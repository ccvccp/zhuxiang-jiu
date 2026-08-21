"""Redis 数据初始化脚本:写入初始 _mock_store 数据

用途:
    - Phase 3 of Scheme B+: Redis 持久化存储迁移
    - 在启动后端服务前执行,确保 Redis 中有初始数据
    - 幂等设计:可重复执行,会先清空旧数据再写入

数据来源:
    与 repositories/store.py 的 _mock_store 初始值完全一致,
    保证内存模式与 Redis 模式下看到的初始状态相同。

Key 命名规范(对齐 repositories/backend.py 的 _k 函数):
    zhuxiang:agent:{id}              Hash(代理商)
    zhuxiang:inventory:{productId}   Hash(库存)
    zhuxiang:warehouse:slots         Hash(库位映射)
    zhuxiang:warehouse:inbound_log   List(入库日志, 初始为空)
    zhuxiang:warehouse:outbound_log   List(出库日志, 初始为空)
    zhuxiang:orders                  List(订单, 初始为空)
    zhuxiang:shipping_claims         Hash(区域认领, 初始为空)

运行:
    # 默认连接本地 Redis
    py scripts/seed_redis.py

    # 指定 Redis 地址
    $env:REDIS_URL = "redis://127.0.0.1:6379/0"
    py scripts/seed_redis.py

    # Docker 环境
    docker-compose exec backend py scripts/seed_redis.py

依赖:
    redis>=5.0.0 (redis-py 的 asyncio 客户端)

退出码:
    0 = 成功
    1 = 连接失败 / 写入失败
"""

import asyncio
import os
import sys
from pathlib import Path

# ============================================================
# 路径配置:确保可独立运行(不依赖 PYTHONPATH)
# ============================================================

BACKEND_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(BACKEND_DIR))


# ============================================================
# 配置
# ============================================================

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
KEY_PREFIX = "zhuxiang:"  # 必须与 repositories/backend.py 的 KEY_PREFIX 一致


def _k(entity: str, *parts) -> str:
    """构造 Redis Key(复制自 repositories/backend.py, 避免循环导入)"""
    return KEY_PREFIX + entity + ":" + ":".join(str(p) for p in parts)


# ============================================================
# 初始数据(与 repositories/store.py 的 _mock_store 完全一致)
# ============================================================

SEED_DATA = {
    "agents": {
        1: {"id": 1, "name": "泰安市级代理商", "level": "C", "wallet": 50000},
        2: {"id": 2, "name": "济南核心代理商", "level": "B", "wallet": 120000},
    },
    "inventory": {
        "ZX42-2026L07": {"stock": 500, "reserved": 0},
        "ZX42-2026L05": {"stock": 300, "reserved": 0},
    },
    "warehouse_slots": {
        "A1": "ZX42-2026L07",
        "A2": "ZX42-2026L05",
    },
    "members": {
        1: {
            "id": 1, "phone": "13800000001", "password": "mock_hashed_password_placeholder",
            "nickname": "测试会员小竹", "avatar": "", "gender": 1,
            "level": 1, "growth_value": 0, "points": 100,
            "status": 1, "reg_source": "phone",
            "created_at": "2026-08-21T00:00:00+00:00", "last_login_at": "",
        },
    },
    "member_addresses": {
        1: {
            "addr_seed_001": {
                "id": "addr_seed_001", "user_id": 1, "name": "张三",
                "phone": "13800000001", "province": "山东省", "city": "泰安市",
                "district": "泰山区", "detail": "竹香路 1 号", "is_default": 1,
                "created_at": "2026-08-21T00:00:00+00:00",
            },
        },
    },
}


# ============================================================
# Seed 实现
# ============================================================

async def clear_existing_data(client) -> int:
    """清空 zhuxiang: 前缀的所有 key(幂等保证)

    使用 SCAN 避免阻塞 Redis 生产环境(KEYS 命令会阻塞)。
    """
    deleted = 0
    async for key in client.scan_iter(f"{KEY_PREFIX}*"):
        await client.delete(key)
        deleted += 1
    return deleted


async def seed_agents(client) -> int:
    """写入代理商数据(Hash)"""
    count = 0
    for agent_id, data in SEED_DATA["agents"].items():
        # HSET mapping 要求所有 value 为 str/bytes/int/float
        # wallet 是 float, Redis 会自动转 str 存储, 读取时 Repository 做类型转换
        await client.hset(_k("agent", agent_id), mapping={
            "id": data["id"],
            "name": data["name"],
            "level": data["level"],
            "wallet": data["wallet"],
        })
        count += 1
    return count


async def seed_inventory(client) -> int:
    """写入库存数据(Hash)"""
    count = 0
    for product_id, data in SEED_DATA["inventory"].items():
        await client.hset(_k("inventory", product_id), mapping={
            "stock": data["stock"],
            "reserved": data["reserved"],
        })
        count += 1
    return count


async def seed_warehouse_slots(client) -> int:
    """写入仓储库位映射(Hash, field=slot, value=productId)

    注意: B1=None 的库位不写入, 读取时 hgetall 不包含该 field 即表示空库位。
    WarehouseRepository._redis_get_slots 返回 dict, 与内存模式行为一致。
    """
    slots = {k: v for k, v in SEED_DATA["warehouse_slots"].items() if v is not None}
    if slots:
        await client.hset(_k("warehouse", "slots"), mapping=slots)
    return len(slots)


async def seed_members(client) -> int:
    """写入会员数据(Hash) + 手机号索引(String) + ID 序列"""
    import hashlib
    count = 0
    for member_id, data in SEED_DATA["members"].items():
        # 计算与 member_service 一致的密码哈希(seed 中用占位符, 此处真实计算)
        password = "test123456"  # 与 store.py 初始数据密码一致
        salt = "zhuxiang_member_salt_v1"
        hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        data["password"] = hashed

        await client.hset(_k("member", member_id), mapping=data)
        # 手机号唯一索引
        await client.set(_k("member", "phone", data["phone"]), member_id)
        count += 1
    # ID 序列(确保新注册的 ID > 已有最大 ID)
    await client.set(_k("member", "seq"), count)
    return count


async def seed_member_addresses(client) -> int:
    """写入收货地址(Hash, field=addrId, value=JSON)"""
    import json
    count = 0
    for member_id, addrs in SEED_DATA["member_addresses"].items():
        if not addrs:
            continue
        mapping = {addr_id: json.dumps(addr, ensure_ascii=False) for addr_id, addr in addrs.items()}
        await client.hset(_k("member", "addresses", member_id), mapping=mapping)
        count += len(addrs)
    return count


async def verify_seed(client) -> dict:
    """验证写入结果,返回各实体的记录数"""
    agents = await client.keys(_k("agent", "*"))
    inventory = await client.keys(_k("inventory", "*"))
    slots_count = await client.hlen(_k("warehouse", "slots"))

    # 抽样验证代理商 1 的数据
    agent1 = await client.hgetall(_k("agent", 1))
    inv1 = await client.hgetall(_k("inventory", "ZX42-2026L07"))

    # 会员验证
    members = await client.keys(_k("member", "*"))
    member_seq = await client.get(_k("member", "seq"))
    member1 = await client.hgetall(_k("member", 1))
    member1_phone_idx = await client.get(_k("member", "phone", "13800000001"))
    addrs_count = await client.hlen(_k("member", "addresses", 1))

    return {
        "agents_count": len(agents),
        "inventory_count": len(inventory),
        "slots_count": slots_count,
        "sample_agent_1": agent1,
        "sample_inventory_ZX42-2026L07": inv1,
        "members_count": len(members),
        "member_seq": member_seq,
        "sample_member_1": member1,
        "member1_phone_index": member1_phone_idx,
        "member1_addresses_count": addrs_count,
    }


async def seed() -> int:
    """主流程:清空 → 写入 → 验证"""
    print("=" * 60)
    print("Redis 数据初始化(seed_redis.py)")
    print(f"  REDIS_URL: {REDIS_URL}")
    print(f"  KEY_PREFIX: {KEY_PREFIX}")
    print("=" * 60)

    # 懒导入 redis(脚本可能在没有 Redis 库的环境被误执行)
    try:
        import redis.asyncio as redis
    except ImportError:
        print("[ERROR] 未安装 redis 库, 请执行: py -m pip install redis>=5.0.0")
        return 1

    # 连接 Redis
    client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        # 测试连接
        try:
            await client.ping()
        except Exception as e:
            print(f"[ERROR] Redis 连接失败: {e}")
            print(f"        请确认 Redis 服务已启动: {REDIS_URL}")
            return 1
        print(f"[OK] Redis 连接成功")

        # 1. 清空旧数据
        deleted = await clear_existing_data(client)
        print(f"[OK] 清空旧数据: 删除 {deleted} 个 key")

        # 2. 写入代理商
        agents_n = await seed_agents(client)
        print(f"[OK] 代理商写入: {agents_n} 条")

        # 3. 写入库存
        inv_n = await seed_inventory(client)
        print(f"[OK] 库存写入: {inv_n} 条")

        # 4. 写入仓储库位
        slots_n = await seed_warehouse_slots(client)
        print(f"[OK] 仓储库位写入: {slots_n} 个")

        # 4b. 写入会员数据
        members_n = await seed_members(client)
        print(f"[OK] 会员写入: {members_n} 条(含手机号索引 + ID 序列)")

        # 4c. 写入收货地址
        addrs_n = await seed_member_addresses(client)
        print(f"[OK] 收货地址写入: {addrs_n} 条")

        # 5. 空列表/空 Hash 不需要写入(inbound_log/outbound_log/orders/shipping_claims)
        print("[INFO] inbound_log/outbound_log/orders/shipping_claims: 初始为空, 不写入")

        # 6. 验证
        print("-" * 60)
        print("验证结果:")
        result = await verify_seed(client)
        for k, v in result.items():
            if isinstance(v, dict):
                print(f"  {k}:")
                for fk, fv in v.items():
                    print(f"    {fk} = {fv}")
            else:
                print(f"  {k}: {v}")

        print("=" * 60)
        print("[SUCCESS] Redis 数据初始化完成")
        print()
        print("后续步骤:")
        print("  1. 启动后端: uvicorn main:app --port 8000")
        print("     (默认 LOCK_MODE=redis, STORE_MODE=redis)")
        print("  2. 验证接口: curl http://localhost:8000/api/decision/health")
        print("  3. 验证数据: curl http://localhost:8000/api/warehouse/stocktake")
        return 0

    finally:
        await client.aclose()


if __name__ == "__main__":
    exit_code = asyncio.run(seed())
    sys.exit(exit_code)
