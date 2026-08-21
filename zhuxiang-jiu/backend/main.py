"""
AI决策筹划模块(模块29·AI大脑中枢) FastAPI 后端入口
参照 OpenAPI 3.0 规范 ai-decision-module-29.openapi.json
6层架构: 感知→知识→决策→编排→执行→反馈
核心原则: 模型提动作,规则定执行(先Copilot后Agent)

架构分层(Phase 1 重构):
    main.py            → 仅 app 初始化 + 中间件 + 路由注册
    core/              → 横切关注点(config/auth/errors/helpers/locks)
    repositories/      → 数据访问层(Repository Pattern,共享 _mock_store)
    services/          → 业务逻辑层(锁/事务/规则校验)
    routes/            → HTTP 路由层(decision/system/business)

启动: uvicorn main:app --reload --port 8000
文档: http://localhost:8000/docs (Swagger UI)

测试兼容:
    from main import app, _mock_store  # 测试仍可直接修改状态
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import ALLOW_HEADERS, ALLOW_METHODS, CORS_ORIGINS
from core.errors import register_exception_handlers
from repositories.store import _mock_store  # 重新导出,兼容测试 `from main import _mock_store`
from routes import (
    register_business_routes,
    register_decision_routes,
    register_system_routes,
    register_member_routes,
    register_order_routes,
    register_product_routes,
    register_finance_routes,
    register_agent_routes,
    register_wallet_routes,
    register_payment_routes,
    register_logistics_routes,
    register_groupbuy_routes,
    register_citystore_routes,
)


# ============================================================
#  FastAPI 应用初始化
# ============================================================

app = FastAPI(
    title="AI决策筹划模块（模块29·AI大脑中枢）API",
    description=(
        "6层架构(感知→知识→决策→编排→执行→反馈)·"
        "双维服务角色与模块·"
        "核心原则: 模型提动作,规则定执行(先Copilot后Agent)\n\n"
        "参照: Microsoft Copilot / 阿里AI中台 / Gartner超自动化 / "
        "KPMG全栈AI / 腾讯AI×数据中台"
    ),
    version="1.0.0",
    openapi_tags=[
        {"name": "感知层", "description": "数据采集·事件流·实时指标(28模块)"},
        {"name": "知识层", "description": "AI智能知识中枢: RAG+组织记忆+语义图谱"},
        {"name": "决策层", "description": "策略筹划·预测推演·治理决策·风控决策"},
        {"name": "编排层", "description": "跨域工作流编排·能力路由·插件池"},
        {"name": "执行层", "description": "角色决策助理(Copilot→Agent)"},
        {"name": "反馈层", "description": "反馈闭环·复盘优化"},
        {"name": "系统", "description": "健康检查·模式切换"},
        {"name": "代理商服务", "description": "代理商升级/降级/区域认领"},
        {"name": "交易服务", "description": "订单结算提交"},
        {"name": "供应链服务", "description": "库存扣减/回补"},
        {"name": "仓储服务", "description": "入库/出库/盘点/库位优化/预测"},
        {"name": "会员服务", "description": "注册/登录/资料/等级/积分/收货地址"},
        {"name": "订单服务", "description": "订单创建/查询/状态流转/售后退款/评价/超时"},
        {"name": "产品展示", "description": "分类导航/产品列表/搜索/详情/热销/主推/评价"},
        {"name": "财务管理", "description": "凭证/发票/税务/对账/付款/财务报表"},
        {"name": "钱包盈利", "description": "钱包开通/充值/提现/消费返利/收益/定期/奖品"},
        {"name": "收款管理", "description": "支付/退款/付款/回调/幂等/对账"},
        {"name": "物流接口管理", "description": "物流下单/轨迹追踪/月结对账/状态机"},
        {"name": "团购模块", "description": "SVIP团购/阶梯折扣/申请审核/状态流转"},
        {"name": "市级网店模块", "description": "SVIP开店/城市独占/月度考核/三档折扣/状态机"},
    ],
)


# ============================================================
#  中间件 + 异常处理 + 路由注册
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=ALLOW_METHODS,
    allow_headers=ALLOW_HEADERS,
)

register_exception_handlers(app)
register_decision_routes(app)
register_system_routes(app)
register_business_routes(app)
register_member_routes(app)
register_order_routes(app)
register_product_routes(app)
register_finance_routes(app)
register_agent_routes(app)
register_wallet_routes(app)
register_payment_routes(app)
register_logistics_routes(app)
register_groupbuy_routes(app)
register_citystore_routes(app)


# ============================================================
#  启动入口
# ============================================================

if __name__ == "__main__":
    import uvicorn

    _port = int(os.environ.get("PORT", "8000"))
    _host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(app, host=_host, port=_port)
