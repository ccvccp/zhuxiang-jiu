"""内存态 Mock 存储单例

生产环境应替换为数据库(SQLAlchemy/MongoDB),只需保留 Repository 接口。
当前为开发/测试用途:进程内字典,重启即丢失。

注意:
    - _mock_store 是模块级单例,所有 Repository 默认共享此实例
    - 测试文件通过 `from main import _mock_store` 直接修改,
      与 Repository 通过此单例间接读写保持一致
    - 多进程部署时每个 worker 持有独立副本(状态分裂),
      方案 B 锁仅保证跨进程互斥,不解决存储分裂
"""


def _hash_member_pwd(password: str) -> str:
    """会员密码哈希(Mock, 与 member_service._hash_password 一致)"""
    import hashlib
    salt = "zhuxiang_member_salt_v1"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _build_initial_products() -> dict:
    """构建 11 款产品初始数据字典(product_id → product)

    与 repositories/product_repository.py 的 _INITIAL_PRODUCTS 数据源一致,
    通过导入复用避免数据重复维护。
    """
    # 局部导入避免循环依赖(product_repository 依赖 backend, backend 依赖本模块)
    from repositories.product_repository import _initial_products
    return {p["product_id"]: p for p in _initial_products()}


def _build_initial_product_reviews() -> dict:
    """构建产品评价初始数据字典(product_id → [review, ...])"""
    from repositories.product_repository import _initial_reviews
    import copy
    return copy.deepcopy(_initial_reviews())


def _build_initial_inventory() -> dict:
    """构建库存初始数据(11 款产品)

    保留 ZX42-2026L07(500)/ZX42-2026L05(300) 与原值一致以兼容订单测试,
    其余 9 款产品补充初始库存。
    """
    return {
        "ZX42-2026L07": {"stock": 500, "reserved": 0},
        "ZX42-2026L05": {"stock": 300, "reserved": 0},
        "ZX53-2026Z01": {"stock": 200, "reserved": 0},
        "ZX53-2026N10": {"stock": 120, "reserved": 0},
        "ZX53-2026N20": {"stock": 40, "reserved": 0},
        "ZX52-2026L02": {"stock": 150, "reserved": 0},
        "ZX42-2026B01": {"stock": 800, "reserved": 0},
        "ZX50-2026D01": {"stock": 100, "reserved": 0},
        "ZX52-2026X01": {"stock": 300, "reserved": 0},
        "ZX52-2026X02": {"stock": 180, "reserved": 0},
        "ZX52-2026X03": {"stock": 60, "reserved": 0},
    }


_mock_store: dict = {
    "agents": {
        1: {"id": 1, "name": "泰安市级代理商", "level": "C", "wallet": 50000},
        2: {"id": 2, "name": "济南核心代理商", "level": "B", "wallet": 120000},
    },
    "inventory": _build_initial_inventory(),
    "warehouse": {
        "slots": {"A1": "ZX42-2026L07", "A2": "ZX42-2026L05"},
        "inbound_log": [],
        "outbound_log": [],
    },
    "orders": [],
    "orders_v2": {},
    "shipping_claims": {},
    # 会员模块(MemberRepository 自管理 seq, 此处仅初始数据)
    "members": {
        1: {
            "id": 1, "phone": "13800000001", "password": _hash_member_pwd("test123456"),
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
    "_member_seq": 1,
    # 产品展示模块(11 款产品 + 评价)
    "products": _build_initial_products(),
    "product_reviews": _build_initial_product_reviews(),
}


def reset_store() -> dict:
    """重置 _mock_store 到初始状态(测试辅助)

    注意:此函数仅用于测试 setup,生产环境不应调用。
    """
    import copy
    initial = {
        "agents": {
            1: {"id": 1, "name": "泰安市级代理商", "level": "C", "wallet": 50000},
            2: {"id": 2, "name": "济南核心代理商", "level": "B", "wallet": 120000},
        },
        "inventory": _build_initial_inventory(),
        "warehouse": {
            "slots": {"A1": "ZX42-2026L07", "A2": "ZX42-2026L05"},
            "inbound_log": [],
            "outbound_log": [],
        },
        "orders": [],
    "orders_v2": {},
        "shipping_claims": {},
        "members": {
            1: {
                "id": 1, "phone": "13800000001", "password": _hash_member_pwd("test123456"),
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
        "_member_seq": 1,
        # 产品展示模块(11 款产品 + 评价)
        "products": _build_initial_products(),
        "product_reviews": _build_initial_product_reviews(),
    }
    _mock_store.clear()
    _mock_store.update(copy.deepcopy(initial))
    return _mock_store
