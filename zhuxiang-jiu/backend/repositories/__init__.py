"""repositories 包:数据访问层(Repository Pattern)

职责:
    - 封装 _mock_store 的读写细节
    - 提供领域对象(Agent/Inventory/Warehouse/Order/ShippingClaim)的 CRUD 接口
    - 业务层(services)只通过 Repository 访问数据,不直接操作 _mock_store

设计:
    - store.py 持有 _mock_store 单例(进程内字典)
    - 5 个 Repository 默认绑定同一 store 实例,保证与现有测试契约一致
        (测试通过 `from main import _mock_store` 直接修改状态)
    - Repository 方法返回基本类型(dict/None),不返回 ORM 对象
    - 异常约定: 找不到资源时抛 KeyError(agent_id/product_id),
                业务校验失败抛 ValueError(库存不足等)
"""
from repositories.store import _mock_store, reset_store
from repositories.agent_repository import AgentRepository
from repositories.inventory_repository import InventoryRepository
from repositories.warehouse_repository import WarehouseRepository
from repositories.order_repository import OrderRepository
from repositories.shipping_repository import ShippingClaimRepository
from repositories.member_repository import MemberRepository

__all__ = [
    "_mock_store",
    "reset_store",
    "AgentRepository",
    "InventoryRepository",
    "WarehouseRepository",
    "OrderRepository",
    "ShippingClaimRepository",
    "MemberRepository",
]
