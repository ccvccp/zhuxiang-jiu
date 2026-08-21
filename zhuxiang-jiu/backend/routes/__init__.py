"""routes 包:HTTP 路由层

职责:
    - 仅处理 HTTP 协议:参数校验(Pydantic)、调用 service、格式化响应
    - 不写业务逻辑(业务在 services 层)
    - 异常转换:KeyError → 404, ValueError → 409

注册模式:
    每个 *_routes.py 暴露 register_xxx_routes(app) 函数,
    main.py 在应用初始化时调用。
"""
from routes.decision_routes import register_decision_routes
from routes.system_routes import register_system_routes
from routes.business_routes import register_business_routes
from routes.member_routes import register_member_routes
from routes.order_routes import register_order_routes
from routes.product_routes import register_product_routes
from routes.finance_routes import register_finance_routes
from routes.agent_routes import register_agent_routes
from routes.wallet_routes import register_wallet_routes

__all__ = [
    "register_decision_routes",
    "register_system_routes",
    "register_business_routes",
    "register_member_routes",
    "register_order_routes",
    "register_product_routes",
    "register_finance_routes",
    "register_agent_routes",
    "register_wallet_routes",
]
