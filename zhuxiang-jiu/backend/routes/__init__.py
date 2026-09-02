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
# AI 语义评分层(v7.2: 5 个高落差模块补齐 AI 语义)
from routes.ai_scoring_routes import register_ai_scoring_routes
# AI 语义评分层·第二批(v7.3: 剩余 8 个 B 级模块补齐 AI 语义)
from routes.ai_scoring_ext_routes import register_ai_scoring_ext_routes
# AI 语义评分层·第三批(v7.4: 用户认证混合模式 AI 登录风控)
from routes.ai_scoring_auth_routes import register_ai_scoring_auth_routes
# AI 自学习层路由(v7.5: 评分器自学习闭环管理端)
from routes.ai_learning_routes import register_ai_learning_routes
# 顺手赚钱模块
from routes.pocket_routes import register_pocket_routes
# 网站图标智能管理模块
from routes.site_theme_routes import register_site_theme_routes
# 权限AI智能管理模块
from routes.perm_routes import register_perm_routes
# 产品溯源管理模块
from routes.trace_prod_routes import register_trace_prod_routes
# 客服工单模块
from routes.ticket_routes import register_ticket_routes
# AI智能管理模块(角色经济中枢)
from routes.role_routes import register_role_routes
# AI智能自动引流模块
from routes.attract_routes import register_attract_routes
# AI智能知识库训练模块
from routes.knowledge_routes import register_knowledge_routes
# AI智能中枢模块(35号·全站AI大模型总调度)
from routes.hub_routes import register_hub_routes
# AI智能推广模块(36号·热点雷达+GLM-5.3 Agent内容工厂)
from routes.promo_routes import register_promo_routes
# AI智能网站同盟模块(37号·酒水不分家商户同盟平台)
from routes.alliance_routes import register_alliance_routes
# AI智能产品管理模块(38号·权限审核×上下架×图片设计更换)
from routes.pdm_routes import register_pdm_routes
# AI智能网站入口管理模块(39号·AI自适应认证+扫码登录+设备指纹)
from routes.entry_routes import register_entry_routes
# 平台流量DV博主模块(40号·博主雷达+三段式跟随+归因闭环)
from routes.blogger_routes import register_blogger_routes
# AI智能代驾模块(41号·满额赠券+司机资格审查+三轨派单)
from routes.ride_routes import register_ride_routes
# AI无感开票模块(42号·抬头簿+决策评分+自动开具/红冲)
from routes.invoice_routes import register_invoice_routes

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
    "register_ai_scoring_routes",
    "register_ai_scoring_ext_routes",
    "register_ai_scoring_auth_routes",
    "register_ai_learning_routes",
    "register_pocket_routes",
    "register_site_theme_routes",
    "register_perm_routes",
    "register_trace_prod_routes",
    "register_ticket_routes",
    "register_role_routes",
    "register_attract_routes",
    "register_knowledge_routes",
    "register_hub_routes",
    "register_promo_routes",
    "register_alliance_routes",
    "register_pdm_routes",
    "register_entry_routes",
    "register_blogger_routes",
    "register_ride_routes",
    "register_invoice_routes",
]
