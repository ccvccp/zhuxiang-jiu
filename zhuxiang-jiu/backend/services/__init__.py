"""services 包:业务逻辑层

职责:
    - 封装跨 Repository 的业务编排(锁/事务/规则校验/日志构造)
    - 不感知 HTTP,FastAPI 路由层只做参数校验 + 调 service + 格式化响应
    - 异常约定:
        KeyError(message)  → 路由层映射为 404
        ValueError(message) → 路由层映射为 409(资源冲突)
    - 并发安全: 涉及 RMW 的操作(per-key 锁)由 service 负责
"""
# 已有模块(15)
from services.agent_service import AgentService
from services.checkout_service import CheckoutService
from services.inventory_service import InventoryService
from services.warehouse_service import WarehouseService
from services.shipping_service import ShippingClaimService
from services.member_service import MemberService
from services.order_service import OrderService
from services.product_service import ProductService
from services.finance_service import FinanceService
from services.wallet_service import WalletService
from services.payment_service import PaymentService
from services.logistics_service import LogisticsService
from services.groupbuy_service import GroupBuyService
from services.citystore_service import CityStoreService
from services.points_service import PointsService
# 新增模块(14)
from services.credit_service import CreditService
from services.activity_service import ActivityService
from services.traffic_service import TrafficService
from services.message_service import MessageService
from services.chat_service import ChatService
from services.ad_service import AdService
from services.cooperation_service import CooperationService
from services.agreement_service import AgreementService
from services.recycle_service import RecycleService
from services.trace_service import TraceService
from services.compliance_service import ComplianceService
from services.location_service import LocationService
from services.admin_service import AdminService
from services.venue_service import VenueService

__all__ = [
    # 已有(15)
    "AgentService",
    "CheckoutService",
    "InventoryService",
    "WarehouseService",
    "ShippingClaimService",
    "MemberService",
    "OrderService",
    "ProductService",
    "FinanceService",
    "WalletService",
    "PaymentService",
    "LogisticsService",
    "GroupBuyService",
    "CityStoreService",
    "PointsService",
    # 新增(14)
    "CreditService",
    "ActivityService",
    "TrafficService",
    "MessageService",
    "ChatService",
    "AdService",
    "CooperationService",
    "AgreementService",
    "RecycleService",
    "TraceService",
    "ComplianceService",
    "LocationService",
    "AdminService",
    "VenueService",
]
