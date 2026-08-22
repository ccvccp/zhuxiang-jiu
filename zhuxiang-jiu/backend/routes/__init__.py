"""routes 包:HTTP 路由层

职责:
    - 仅处理 HTTP 协议:参数校验(Pydantic)、调用 service、格式化响应
    - 不写业务逻辑(业务在 services 层)
    - 异常转换:KeyError → 404, ValueError → 409

注册模式:
    每个 *_routes.py 暴露 register_xxx_routes(app) 函数,
    main.py 在应用初始化时调用。
"""
# 已有模块(15)
from routes.decision_routes import register_decision_routes
from routes.system_routes import register_system_routes
from routes.business_routes import register_business_routes
from routes.member_routes import register_member_routes
from routes.order_routes import register_order_routes
from routes.product_routes import register_product_routes
from routes.finance_routes import register_finance_routes
from routes.agent_routes import register_agent_routes
from routes.wallet_routes import register_wallet_routes
from routes.payment_routes import register_payment_routes
from routes.logistics_routes import register_logistics_routes
from routes.groupbuy_routes import register_groupbuy_routes
from routes.citystore_routes import register_citystore_routes
from routes.points_routes import register_points_routes
# 新增模块(14)
from routes.credit_routes import register_credit_routes
from routes.activity_routes import register_activity_routes
from routes.traffic_routes import register_traffic_routes
from routes.message_routes import register_message_routes
from routes.chat_routes import register_chat_routes
from routes.ad_routes import register_ad_routes
from routes.cooperation_routes import register_cooperation_routes
from routes.agreement_routes import register_agreement_routes
from routes.recycle_routes import register_recycle_routes
from routes.trace_routes import register_trace_routes
from routes.compliance_routes import register_compliance_routes
from routes.location_routes import register_location_routes
from routes.admin_routes import register_admin_routes
from routes.venue_routes import register_venue_routes
from routes.maintenance_routes import register_maintenance_routes
from routes.monitor_routes import register_monitor_routes
# 用户认证模块
from routes.auth_routes import register_auth_routes
# 限时秒杀模块
from routes.flashsale_routes import register_flashsale_routes
# 推广码矩阵模块
from routes.promotion_routes import register_promotion_routes

__all__ = [
    # 已有(15)
    "register_decision_routes",
    "register_system_routes",
    "register_business_routes",
    "register_member_routes",
    "register_order_routes",
    "register_product_routes",
    "register_finance_routes",
    "register_agent_routes",
    "register_wallet_routes",
    "register_payment_routes",
    "register_logistics_routes",
    "register_groupbuy_routes",
    "register_citystore_routes",
    "register_points_routes",
    # 新增(14)
    "register_credit_routes",
    "register_activity_routes",
    "register_traffic_routes",
    "register_message_routes",
    "register_chat_routes",
    "register_ad_routes",
    "register_cooperation_routes",
    "register_agreement_routes",
    "register_recycle_routes",
    "register_trace_routes",
    "register_compliance_routes",
    "register_location_routes",
    "register_admin_routes",
    "register_venue_routes",
    "register_monitor_routes",
    "register_maintenance_routes",
    "register_auth_routes",
    "register_promotion_routes",
    "register_flashsale_routes",
]
