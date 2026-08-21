"""services 包:业务逻辑层

职责:
    - 封装跨 Repository 的业务编排(锁/事务/规则校验/日志构造)
    - 不感知 HTTP,FastAPI 路由层只做参数校验 + 调 service + 格式化响应
    - 异常约定:
        KeyError(message)  → 路由层映射为 404
        ValueError(message) → 路由层映射为 409(资源冲突)
    - 并发安全: 涉及 RMW 的操作(per-key 锁)由 service 负责
"""
from services.agent_service import AgentService
from services.checkout_service import CheckoutService
from services.inventory_service import InventoryService
from services.warehouse_service import WarehouseService
from services.shipping_service import ShippingClaimService
from services.member_service import MemberService
from services.order_service import OrderService
from services.product_service import ProductService
from services.finance_service import FinanceService

__all__ = [
    "AgentService",
    "CheckoutService",
    "InventoryService",
    "WarehouseService",
    "ShippingClaimService",
    "MemberService",
    "OrderService",
    "ProductService",
    "FinanceService",
]
