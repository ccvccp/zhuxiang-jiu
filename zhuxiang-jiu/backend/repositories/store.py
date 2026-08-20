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

_mock_store: dict = {
    "agents": {
        1: {"id": 1, "name": "泰安市级代理商", "level": "C", "wallet": 50000},
        2: {"id": 2, "name": "济南核心代理商", "level": "B", "wallet": 120000},
    },
    "inventory": {
        "ZX42-2026L07": {"stock": 500, "reserved": 0},
        "ZX42-2026L05": {"stock": 300, "reserved": 0},
    },
    "warehouse": {
        "slots": {"A1": "ZX42-2026L07", "A2": "ZX42-2026L05"},
        "inbound_log": [],
        "outbound_log": [],
    },
    "orders": [],
    "shipping_claims": {},
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
        "inventory": {
            "ZX42-2026L07": {"stock": 500, "reserved": 0},
            "ZX42-2026L05": {"stock": 300, "reserved": 0},
        },
        "warehouse": {
            "slots": {"A1": "ZX42-2026L07", "A2": "ZX42-2026L05"},
            "inbound_log": [],
            "outbound_log": [],
        },
        "orders": [],
        "shipping_claims": {},
    }
    _mock_store.clear()
    _mock_store.update(copy.deepcopy(initial))
    return _mock_store
