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
    zhuxiang:product:{productId}     Hash(产品主信息, 嵌套字段序列化为 JSON)
    zhuxiang:product:categories      List(产品分类树, 每元素为 JSON)
    zhuxiang:product:reviews:{pid}   List(产品评价, 每元素为 JSON)

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
import json
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


# 局部导入: 产品 / 评价 / 分类 / 库存数据源(与 store.py 复用同一份数据, 避免重复维护)
from repositories.product_repository import (
    _initial_products, _initial_reviews, PRODUCT_CATEGORIES,
)
from repositories.store import (
    _build_initial_inventory, _build_initial_agents,
    _build_initial_agent_rebates, _build_initial_agent_risks,
)


# ============================================================
# 初始数据(与 repositories/store.py 的 _mock_store 完全一致)
# ============================================================

SEED_DATA = {
    "agents": _build_initial_agents(),
    "inventory": _build_initial_inventory(),
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
            "ageConfirmed": 1, "birthdate": "1990-01-01", "ageVerified": 1,
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


def _serialize_agent(agent: dict) -> dict:
    """将代理商 dict 序列化为 Redis Hash 兼容的 mapping

    与 AgentRepository._serialize_agent 逻辑一致:
        None 跳过, bool→0/1, int/float 原样, 其余转 str。
    """
    result = {}
    for k, v in agent.items():
        if v is None:
            continue
        if isinstance(v, bool):
            result[k] = 1 if v else 0
        elif isinstance(v, (int, float)):
            result[k] = v
        else:
            result[k] = str(v)
    return result


async def seed_agents(client) -> int:
    """写入代理商数据(Hash, 含扩展档案字段) + 代理商 ID 序列

    Key: zhuxiang:agent:{agentId}            Hash(代理商主信息)
         zhuxiang:agent:seq                  String(自增序列, 初始化为已有最大 ID)
         zhuxiang:agent_apply:seq            String(申请单自增序列, 初始化为 0)
    """
    count = 0
    max_id = 0
    for agent_id, data in SEED_DATA["agents"].items():
        # HSET mapping 要求所有 value 为 str/bytes/int/float
        # wallet 是 float, Redis 会自动转 str 存储, 读取时 Repository 做类型转换
        await client.hset(_k("agent", agent_id), mapping=_serialize_agent(data))
        count += 1
        try:
            if int(agent_id) > max_id:
                max_id = int(agent_id)
        except (TypeError, ValueError):
            pass
    # 代理商 ID 序列(确保 audit 通过新建的代理商 ID > 已有最大 ID)
    await client.set(_k("agent", "seq"), max_id)
    # 申请单自增序列(初始 0)
    await client.set(_k("agent_apply", "seq"), 0)
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
    count = 0
    for member_id, addrs in SEED_DATA["member_addresses"].items():
        if not addrs:
            continue
        mapping = {addr_id: json.dumps(addr, ensure_ascii=False) for addr_id, addr in addrs.items()}
        await client.hset(_k("member", "addresses", member_id), mapping=mapping)
        count += len(addrs)
    return count


def _serialize_product(product: dict) -> dict:
    """将产品 dict 序列化为 Redis Hash 兼容的 mapping

    注意: 此函数必须与 ProductRepository._serialize_product 逻辑保持一致,
    否则 seed 写入的数据 Repository 读取时会反序列化失败。
    嵌套结构(tags/scenes/attributes/images)序列化为 JSON 字符串,
    bool 转 0/1, 其余按原类型(str/int/float)存储。
    """
    json_fields = ("tags", "scenes", "attributes", "images")
    result = {}
    for k, v in product.items():
        if v is None:
            continue
        if k in json_fields:
            result[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            result[k] = 1 if v else 0
        elif isinstance(v, (int, float)):
            result[k] = v
        else:
            result[k] = str(v)
    return result


async def seed_products(client) -> int:
    """写入产品主信息(Hash, 11 款产品)

    Key: zhuxiang:product:{product_id}
    嵌套字段(tags/scenes/attributes/images)以 JSON 字符串存储,
    由 ProductRepository._deserialize_product 读取时还原。
    """
    count = 0
    for product in _initial_products():
        mapping = _serialize_product(product)
        await client.hset(_k("product", product["product_id"]), mapping=mapping)
        count += 1
    return count


async def seed_product_categories(client) -> int:
    """写入产品分类树(List, 5 个顶级分类)

    Key: zhuxiang:product:categories
    每个元素为分类项的 JSON 字符串, 由 ProductRepository._redis_get_categories 读取。
    """
    items = [json.dumps(c, ensure_ascii=False) for c in PRODUCT_CATEGORIES]
    # 先清空再写入(避免重复执行时追加)
    await client.delete(_k("product", "categories"))
    if items:
        await client.rpush(_k("product", "categories"), *items)
    return len(items)


async def seed_product_reviews(client) -> int:
    """写入产品评价(List, 每款产品一个 List)

    Key: zhuxiang:product:reviews:{product_id}
    每个元素为评价的 JSON 字符串, 由 ProductRepository._redis_get_reviews 读取。
    """
    count = 0
    for product_id, reviews in _initial_reviews().items():
        if not reviews:
            continue
        items = [json.dumps(r, ensure_ascii=False) for r in reviews]
        await client.rpush(_k("product", "reviews", product_id), *items)
        count += len(reviews)
    return count


# ============================================================
# 财务管理模块 seed(凭证/发票/申报/付款/对账)
# ============================================================

# 财务 seed 数据(与 repositories/store.py 的 _build_initial_finance 完全一致)
# 使用同一份函数避免数据重复维护
from repositories.store import _build_initial_finance as _build_finance_seed


def _serialize_finance_hash(data: dict) -> dict:
    """将财务 dict 序列化为 Redis Hash 兼容的 mapping

    与 FinanceRepository._serialize_hash 逻辑一致:
        None 跳过, bool→0/1, list/dict→JSON, 其余原样。
    """
    result = {}
    for k, v in data.items():
        if v is None:
            continue
        if isinstance(v, bool):
            result[k] = 1 if v else 0
        elif isinstance(v, (list, dict)):
            result[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, (int, float)):
            result[k] = v
        else:
            result[k] = str(v)
    return result


async def seed_finance_vouchers(client) -> int:
    """写入财务凭证(Hash + 分录 List + 账期索引 Set)

    Key 设计:
        zhuxiang:finance:voucher:{voucherNo}        Hash(主信息)
        zhuxiang:finance:voucher:entries:{voucherNo} List(分录 JSON)
        zhuxiang:finance:voucher:index:{period}     Set(账期索引)
    """
    finance = _build_finance_seed()
    vouchers = finance["finance_vouchers"]
    count = 0
    for voucher_no, voucher in vouchers.items():
        entries = voucher.get("entries", [])
        main = {k: v for k, v in voucher.items() if k != "entries"}
        await client.hset(_k("finance", "voucher", voucher_no),
                          mapping=_serialize_finance_hash(main))
        entries_key = _k("finance", "voucher", "entries", voucher_no)
        await client.delete(entries_key)
        for entry in entries:
            await client.rpush(entries_key, json.dumps(entry, ensure_ascii=False))
        period = voucher.get("period", "")
        await client.sadd(_k("finance", "voucher", "index", period), voucher_no)
        count += 1
    return count


async def seed_finance_invoices(client) -> int:
    """写入发票(Hash + 账期索引 Set)"""
    finance = _build_finance_seed()
    invoices = finance["finance_invoices"]
    count = 0
    for invoice_no, invoice in invoices.items():
        await client.hset(_k("finance", "invoice", invoice_no),
                          mapping=_serialize_finance_hash(invoice))
        period = invoice.get("period", "")
        await client.sadd(_k("finance", "invoice", "index", period), invoice_no)
        count += 1
    return count


async def seed_finance_tax_declarations(client) -> int:
    """写入税务申报(Hash + 账期索引 Set)"""
    finance = _build_finance_seed()
    decls = finance["finance_tax_declarations"]
    count = 0
    for decl_no, decl in decls.items():
        await client.hset(_k("finance", "tax", decl_no),
                          mapping=_serialize_finance_hash(decl))
        period = decl.get("period", "")
        await client.sadd(_k("finance", "tax", "index", period), decl_no)
        count += 1
    return count


async def seed_finance_payments(client) -> int:
    """写入付款(Hash + 类型索引 Set)"""
    finance = _build_finance_seed()
    payments = finance["finance_payments"]
    count = 0
    for payment_no, payment in payments.items():
        await client.hset(_k("finance", "payment", payment_no),
                          mapping=_serialize_finance_hash(payment))
        ptype = payment.get("type", "")
        await client.sadd(_k("finance", "payment", "index", ptype), payment_no)
        count += 1
    return count


async def seed_finance_reconciliations(client) -> int:
    """写入对账记录(Hash, 主键 date+type 唯一)"""
    finance = _build_finance_seed()
    recs = finance["finance_reconciliations"]
    count = 0
    for _recon_id, recon in recs.items():
        date = recon["date"]
        recon_type = recon["type"]
        await client.hset(_k("finance", "recon", date, recon_type),
                          mapping=_serialize_finance_hash(recon))
        count += 1
    return count


async def seed_finance_seqs(client) -> int:
    """写入财务序列号计数器(对应各实体的初始序号)"""
    finance = _build_finance_seed()
    seq = finance["_finance_seq"]
    count = 0
    for kind_prefix, value in seq.items():
        kind, prefix = kind_prefix.split(":", 1)
        await client.set(_k("finance", f"{kind}:seq", prefix), value)
        count += 1
    return count


# ============================================================
# 代理商返利/风控模块 seed
# ============================================================

def _serialize_agent_rebate(rebate: dict) -> dict:
    """将返利记录 dict 序列化为 Redis Hash 兼容的 mapping

    与 AgentRepository._serialize_rebate 逻辑一致。
    """
    result = {}
    for k, v in rebate.items():
        if v is None:
            continue
        if isinstance(v, bool):
            result[k] = 1 if v else 0
        elif isinstance(v, (int, float)):
            result[k] = v
        else:
            result[k] = str(v)
    return result


def _serialize_agent_risk(risk: dict) -> dict:
    """将风控记录 dict 序列化为 Redis Hash 兼容的 mapping

    与 AgentRepository._serialize_risk 逻辑一致:
        嵌套结构(indicators/alerts)→JSON, bool→0/1, 其余原样。
    """
    json_fields = ("indicators", "alerts")
    result = {}
    for k, v in risk.items():
        if v is None:
            continue
        if k in json_fields:
            result[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            result[k] = 1 if v else 0
        elif isinstance(v, (int, float)):
            result[k] = v
        else:
            result[k] = str(v)
    return result


async def seed_agent_rebates(client) -> int:
    """写入代理商返利记录(Hash + 代理商索引 Set)

    Key 设计:
        zhuxiang:agent_rebate:{rebateId}        Hash(返利记录)
        zhuxiang:agent_rebate:index:{agentId}   Set(该代理商的返利记录 ID 集合)
    """
    rebates = _build_initial_agent_rebates()
    count = 0
    for rebate_id, rebate in rebates.items():
        await client.hset(_k("agent_rebate", rebate_id),
                          mapping=_serialize_agent_rebate(rebate))
        agent_id = rebate.get("agentId")
        if agent_id is not None:
            await client.sadd(_k("agent_rebate", "index", agent_id), rebate_id)
        count += 1
    return count


async def seed_agent_risks(client) -> int:
    """写入代理商风控记录(Hash + 代理商索引 Set)

    Key 设计:
        zhuxiang:agent_risk:{riskId}           Hash(风控记录)
        zhuxiang:agent_risk:index:{agentId}    Set(该代理商的风控记录 ID 集合)
    """
    risks = _build_initial_agent_risks()
    count = 0
    for risk_id, risk in risks.items():
        await client.hset(_k("agent_risk", risk_id),
                          mapping=_serialize_agent_risk(risk))
        agent_id = risk.get("agentId")
        if agent_id is not None:
            await client.sadd(_k("agent_risk", "index", agent_id), risk_id)
        count += 1
    return count


async def verify_seed(client) -> dict:
    """验证写入结果,返回各实体的记录数"""
    agents = [
        k for k in (await client.keys(_k("agent", "*")))
        if not k.endswith(":agent:seq")
    ]
    inventory = await client.keys(_k("inventory", "*"))
    slots_count = await client.hlen(_k("warehouse", "slots"))

    # 抽样验证代理商 1 的数据(含扩展档案字段)
    agent1 = await client.hgetall(_k("agent", 1))
    agent_seq = await client.get(_k("agent", "seq"))
    apply_seq = await client.get(_k("agent_apply", "seq"))
    inv1 = await client.hgetall(_k("inventory", "ZX42-2026L07"))

    # 会员验证
    members = await client.keys(_k("member", "*"))
    member_seq = await client.get(_k("member", "seq"))
    member1 = await client.hgetall(_k("member", 1))
    member1_phone_idx = await client.get(_k("member", "phone", "13800000001"))
    addrs_count = await client.hlen(_k("member", "addresses", 1))

    # 产品验证(排除 categories / reviews:* 键)
    product_keys = [
        k for k in (await client.keys(_k("product", "*")))
        if not k.endswith(":product:categories") and ":reviews:" not in k
    ]
    categories_count = await client.llen(_k("product", "categories"))
    sample_product = await client.hgetall(_k("product", "ZX42-2026L07"))
    reviews_keys = [
        k for k in (await client.keys(_k("product", "reviews", "*")))
        if ":reviews:" in k
    ]
    sample_reviews_count = await client.llen(_k("product", "reviews", "ZX42-2026L07"))

    # 财务验证
    voucher_keys = [
        k for k in (await client.keys(_k("finance", "voucher", "*")))
        if ":index:" not in k and ":entries:" not in k
        and not k.endswith(":voucher:seq")
    ]
    invoice_keys = [
        k for k in (await client.keys(_k("finance", "invoice", "*")))
        if ":index:" not in k and not k.endswith(":invoice:seq")
    ]
    tax_keys = [
        k for k in (await client.keys(_k("finance", "tax", "*")))
        if ":index:" not in k and not k.endswith(":tax:seq")
    ]
    payment_keys = [
        k for k in (await client.keys(_k("finance", "payment", "*")))
        if ":index:" not in k and not k.endswith(":payment:seq")
    ]
    recon_keys = await client.keys(_k("finance", "recon", "*"))
    sample_voucher = await client.hgetall(_k("finance", "voucher", "FZ20260820001"))
    sample_invoice = await client.hgetall(_k("finance", "invoice", "FP20260820001"))
    sample_tax = await client.hgetall(_k("finance", "tax", "SB202608001"))
    sample_payment = await client.hgetall(_k("finance", "payment", "FK20260820001"))
    sample_recon = await client.hgetall(_k("finance", "recon", "2026-08-19", "daily"))

    # 返利/风控验证
    rebate_keys = [
        k for k in (await client.keys(_k("agent_rebate", "*")))
        if ":index:" not in k
    ]
    risk_keys = [
        k for k in (await client.keys(_k("agent_risk", "*")))
        if ":index:" not in k
    ]
    sample_rebate = await client.hgetall(_k("agent_rebate", "RB20260701001"))
    sample_risk = await client.hgetall(_k("agent_risk", "RK20260701001"))

    return {
        "agents_count": len(agents),
        "inventory_count": len(inventory),
        "slots_count": slots_count,
        "sample_agent_1": agent1,
        "agent_seq": agent_seq,
        "agent_apply_seq": apply_seq,
        "sample_inventory_ZX42-2026L07": inv1,
        "members_count": len(members),
        "member_seq": member_seq,
        "sample_member_1": member1,
        "member1_phone_index": member1_phone_idx,
        "member1_addresses_count": addrs_count,
        "products_count": len(product_keys),
        "categories_count": categories_count,
        "sample_product_ZX42-2026L07": sample_product,
        "product_reviews_keys_count": len(reviews_keys),
        "sample_reviews_ZX42-2026L07_count": sample_reviews_count,
        "finance_vouchers_count": len(voucher_keys),
        "finance_invoices_count": len(invoice_keys),
        "finance_tax_count": len(tax_keys),
        "finance_payments_count": len(payment_keys),
        "finance_reconciliations_count": len(recon_keys),
        "sample_finance_voucher_FZ20260820001": sample_voucher,
        "sample_finance_invoice_FP20260820001": sample_invoice,
        "sample_finance_tax_SB202608001": sample_tax,
        "sample_finance_payment_FK20260820001": sample_payment,
        "sample_finance_recon_2026-08-19_daily": sample_recon,
        "agent_rebates_count": len(rebate_keys),
        "agent_risks_count": len(risk_keys),
        "sample_agent_rebate_RB20260701001": sample_rebate,
        "sample_agent_risk_RK20260701001": sample_risk,
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
        print("[OK] Redis 连接成功")

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

        # 4d. 写入产品主信息(11 款产品)
        products_n = await seed_products(client)
        print(f"[OK] 产品写入: {products_n} 条")

        # 4e. 写入产品分类树(5 个顶级分类)
        cats_n = await seed_product_categories(client)
        print(f"[OK] 产品分类树写入: {cats_n} 个分类")

        # 4f. 写入产品评价(种子评价)
        reviews_n = await seed_product_reviews(client)
        print(f"[OK] 产品评价写入: {reviews_n} 条")

        # 4g. 写入财务管理模块(凭证/发票/申报/付款/对账 + 序列号)
        vouchers_n = await seed_finance_vouchers(client)
        print(f"[OK] 财务凭证写入: {vouchers_n} 条(含分录 List + 账期索引)")
        invoices_n = await seed_finance_invoices(client)
        print(f"[OK] 财务发票写入: {invoices_n} 条")
        taxes_n = await seed_finance_tax_declarations(client)
        print(f"[OK] 税务申报写入: {taxes_n} 条")
        payments_n = await seed_finance_payments(client)
        print(f"[OK] 付款记录写入: {payments_n} 条")
        recs_n = await seed_finance_reconciliations(client)
        print(f"[OK] 对账记录写入: {recs_n} 条")
        seqs_n = await seed_finance_seqs(client)
        print(f"[OK] 财务序列号计数器写入: {seqs_n} 个")

        # 4h. 写入代理商返利记录
        rebates_n = await seed_agent_rebates(client)
        print(f"[OK] 代理商返利记录写入: {rebates_n} 条(含代理商索引)")

        # 4i. 写入代理商风控记录
        risks_n = await seed_agent_risks(client)
        print(f"[OK] 代理商风控记录写入: {risks_n} 条(含代理商索引)")

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
        print("  4. 验证产品: curl http://localhost:8000/api/product/categories")
        print("     curl http://localhost:8000/api/product/list")
        print("     curl http://localhost:8000/api/product/ZX42-2026L07")
        print("  5. 验证财务: curl -H 'X-Role: admin' http://localhost:8000/api/finance/voucher/list")
        return 0

    finally:
        await client.aclose()


if __name__ == "__main__":
    exit_code = asyncio.run(seed())
    sys.exit(exit_code)
