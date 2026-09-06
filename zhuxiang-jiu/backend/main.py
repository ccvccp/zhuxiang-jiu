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

import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.auth_middleware import JWTAuthMiddleware
from core.api_key_middleware import ApiKeyMiddleware
from core.security_gateway import SecurityGatewayMiddleware
from routes.security_routes import register_security_routes
from routes.api_manager_routes import register_api_manager_routes
from routes.trust_value_routes import register_trust_value_routes
from routes.ai_governance_routes import register_ai_governance_routes
from routes.xiaozhu_routes import register_xiaozhu_routes
from core.config import ALLOW_HEADERS, ALLOW_METHODS, CORS_ORIGINS
from core.errors import register_exception_handlers
from core.helpers import uptime
from core.locks import close_redis_client, _get_lock_mode
from repositories.store import _mock_store
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
    register_points_routes,
    # 新增14模块
    register_credit_routes,
    register_activity_routes,
    register_traffic_routes,
    register_message_routes,
    register_chat_routes,
    register_ad_routes,
    register_cooperation_routes,
    register_agreement_routes,
    register_recycle_routes,
    register_trace_routes,
    register_compliance_routes,
    register_location_routes,
    register_admin_routes,
    register_venue_routes,
    register_monitor_routes,
    register_maintenance_routes,
    # 用户认证模块
    register_auth_routes,
    # 推广码矩阵模块
    register_promotion_routes,
    # 顺手赚钱模块
    register_pocket_routes,
    # 网站图标智能管理模块
    register_site_theme_routes,
    # 权限AI智能管理模块
    register_perm_routes,
    # 产品溯源管理模块
    register_trace_prod_routes,
    # 客服工单模块
    register_ticket_routes,
    # AI智能管理模块(角色经济中枢)
    register_role_routes,
    # AI智能自动引流模块
    register_attract_routes,
    # AI智能知识库训练模块
    register_knowledge_routes,
    # AI智能中枢模块(35号·全站AI大模型总调度)
    register_hub_routes,
    # 限时秒杀模块
    register_flashsale_routes,
    # AI 语义评分层(v7.2)
    register_ai_scoring_routes,
    # AI 语义评分层·第二批(v7.3)
    register_ai_scoring_ext_routes,
    # AI 语义评分层·第三批(v7.4)
    register_ai_scoring_auth_routes,
    # AI 自学习层路由(v7.5)
    register_ai_learning_routes,
    # AI智能推广模块(36号·热点雷达+GLM-5.3 Agent内容工厂)
    register_promo_routes,
    # AI智能网站同盟模块(37号·酒水不分家商户同盟平台)
    register_alliance_routes,
    # AI智能产品管理模块(38号·权限审核×上下架×图片设计更换)
    register_pdm_routes,
    # AI智能网站入口管理模块(39号·AI自适应认证+扫码登录+设备指纹)
    register_entry_routes,
    # 平台流量DV博主模块(40号·博主雷达+三段式跟随+归因闭环)
    register_blogger_routes,
    # AI智能代驾模块(41号·满额赠券+司机资格审查+三轨派单)
    register_ride_routes,
    # AI无感开票模块(42号·抬头簿+决策评分+自动开具/红冲)
    register_invoice_routes,
)

__all__ = ["app", "_mock_store"]


# ============================================================
#  FastAPI 应用初始化
# ============================================================

logger = logging.getLogger(__name__)

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
        {"name": "会员积分模块", "description": "签到/消费返分/积分抵现/过期处理"},
        {"name": "信用管理模块", "description": "竹信分/5级信用等级/先享后付额度"},
        {"name": "活动管理模块", "description": "8类活动/报名/擂台赛/状态机"},
        {"name": "流量管理模块", "description": "多平台引流/推广员裂变/佣金计算"},
        {"name": "信息管理模块", "description": "站内信/短信/邮件/模板/批量推送"},
        {"name": "AI客服模块", "description": "AI会话/人工转接/知识库/满意度"},
        {"name": "广告管理模块", "description": "广告CRUD/广告位/投放策略/审核"},
        {"name": "合作接口模块", "description": "合作申请/审核/协议/分级/状态流转"},
        {"name": "条款协议模块", "description": "条款版本/用户同意/角色协议"},
        {"name": "老酒兑换模块", "description": "估价/回收/兑换新酒/状态流转"},
        {"name": "双码追溯模块", "description": "箱码/生命码/扫码追溯/防窜货"},
        {"name": "合规监控模块", "description": "规则管理/违规检测/预警/处罚/申诉"},
        {"name": "位置地图模块", "description": "LBS定位/附近搜索/地理围栏"},
        {"name": "后台管理模块", "description": "管理员/角色权限/操作日志/系统配置"},
        {"name": "酒店酒吧模块", "description": "合作商/场地/铺货/分级/佣金结算"},
        {"name": "AI智能监控模块", "description": "监控指标采集/告警/故障/仪表盘/健康检查"},
        {"name": "AI智能维护模块", "description": "维护任务/健康检查/故障自愈/性能优化/一键巡检"},
        {"name": "推广码矩阵模块", "description": "专属推广码/矩阵绑定/两级奖励/奖励余额购物/领酒发货"},
        {"name": "AI智能中枢模块(35)", "description": "统一AI智能入口/多模态输入(文字语音图片)/意图路由/能力注册表编排/AI训练治理"},
    ],
)


# ============================================================
#  中间件 + 异常处理 + 路由注册
# ============================================================

# JWT 认证中间件(内层: 校验 Token 并注入身份头)
# 注意: 先于 CORS 添加, 使 CORS 位于外层(预检请求由 CORS 直接响应,
# 认证错误响应也会带上 CORS 头)
app.add_middleware(JWTAuthMiddleware)

# 44号 API Key 消费方凭证网关(默认 off 全直通, API_MANAGER_MODE=on 开启)
# 挂载次序(后添加者在外层): CORS(最外) → SecurityGateway → ApiKey(44号)
#   → JWT(最内)——ApiKey 先于 JWTAuth 注入身份(Key 面双头校验通过后
#   inject_identity, compat 模式无 Authorization 头时透传注入值)
app.add_middleware(ApiKeyMiddleware)

# 43号安全网关中间件(中层层: 攻击请求在鉴权层之前被评分拦截)
# 挂载次序(后添加者在外层): CORS(最外) → SecurityGateway → JWT(最内)
# P0 默认 observe 灰度(只留痕不处置), SECURITY_ENFORCE_LEVEL=enforce 生效
app.add_middleware(SecurityGatewayMiddleware)

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
register_points_routes(app)
# 新增14模块
register_credit_routes(app)
register_activity_routes(app)
register_traffic_routes(app)
register_message_routes(app)
register_chat_routes(app)
register_ad_routes(app)
register_cooperation_routes(app)
register_agreement_routes(app)
register_recycle_routes(app)
register_trace_routes(app)
register_compliance_routes(app)
register_location_routes(app)
register_admin_routes(app)
register_venue_routes(app)
register_monitor_routes(app)
register_maintenance_routes(app)
register_auth_routes(app)
register_promotion_routes(app)
register_pocket_routes(app)
register_site_theme_routes(app)
register_perm_routes(app)
register_trace_prod_routes(app)
register_ticket_routes(app)
register_role_routes(app)
register_attract_routes(app)
register_knowledge_routes(app)
register_hub_routes(app)
register_flashsale_routes(app)
register_ai_scoring_routes(app)
register_ai_scoring_ext_routes(app)
register_ai_scoring_auth_routes(app)
register_ai_learning_routes(app)
# AI智能推广模块(36号)
register_promo_routes(app)
# AI智能网站同盟模块(37号)
register_alliance_routes(app)
# AI智能产品管理模块(38号)
register_pdm_routes(app)
# AI智能网站入口管理模块(39号)
register_entry_routes(app)
# 平台流量DV博主模块(40号)
register_blogger_routes(app)
# AI智能代驾模块(41号)
register_ride_routes(app)
# AI无感开票模块(42号)
register_invoice_routes(app)
register_security_routes(app)
# AI智能API管理模块(44号)
register_api_manager_routes(app)
# 信值模块(45号)
register_trust_value_routes(app)
# AI治理与合规中枢(46号)
register_ai_governance_routes(app)
# 信值验真风控模块(47号)
from routes.trust_risk_routes import register_trust_risk_routes
register_trust_risk_routes(app)
# 小竹智能语音中枢(48号)
register_xiaozhu_routes(app)
# 小竹可信知识图谱(51号)
from routes.kg51_routes import register_kg51_routes
register_kg51_routes(app)
# 小竹语音可用性评估引擎(52号)
from routes.us52_routes import register_us52_routes
register_us52_routes(app)
# 小竹智能登录引擎(53号)
from routes.login53_routes import register_login53_routes
register_login53_routes(app)
# 小竹登录引擎大模型(54号)
from routes.login54_routes import register_login54_routes
register_login54_routes(app)

from routes.qr55_routes import register_qr55_routes
register_qr55_routes(app)

from routes.aiup56_routes import register_aiup56_routes
register_aiup56_routes(app)

# AI智能知识库(57号)
from routes.kb57_routes import register_kb57_routes
register_kb57_routes(app)

# AI智能优化意图识别(58号)
from routes.ii58_routes import register_ii58_routes
register_ii58_routes(app)

# AI智能服务编排(59号)
from routes.ii59_routes import register_ii59_routes
register_ii59_routes(app)

# AI智能后台管理(63号)
from routes.ab63_routes import register_ab63_routes
register_ab63_routes(app)

# AI智能支付管理(60号)
from routes.pay60_routes import register_pay60_routes
register_pay60_routes(app)

# AI智能系统升级决策(61号)
from routes.dm61_routes import register_dm61_routes
register_dm61_routes(app)

# AI智能无形资产估值(62号)
from routes.av62_routes import register_av62_routes
register_av62_routes(app)
from routes.xx64_routes import register_xx64_routes
register_xx64_routes(app)


# ============================================================
# 静态媒体服务(35号 AI Hub P3: /media/voice|image/xxx, 本地卷持久化)
# ============================================================

_MEDIA_ROOT = os.environ.get(
    "HUB_MEDIA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "media"))
for _sub in ("voice", "image"):
    os.makedirs(os.path.join(_MEDIA_ROOT, _sub), exist_ok=True)
app.mount("/media", StaticFiles(directory=_MEDIA_ROOT), name="media")


# ============================================================
# 生命周期管理: startup 启动后台调度 / shutdown 时关闭 Redis 连接
# ============================================================

@app.on_event("startup")
async def _on_startup():
    """应用启动时拉起后台调度任务(幂等, 环境开关可控)"""
    # P4.1 部署加固: LLM 各轨开关状态日志(密钥遗漏/静默回退即时可见)
    from services.llm_client import log_feature_status
    log_feature_status()
    # AI 自学习调度(v7.6, AI_LEARNING_AUTO=off 可关闭)
    from services.ai_learning_scheduler import start_scheduler as start_ai_learning
    start_ai_learning()
    # 订单超时自动处理(P1-13, ORDER_TIMEOUT_AUTO=off 可关闭)
    from services.order_timeout_scheduler import start_scheduler as start_order_timeout
    start_order_timeout()
    # 知识库质量进化调度(P2, KNOWLEDGE_QUALITY_AUTO=off 可关闭)
    from services.knowledge_quality_scheduler import (
        start_scheduler as start_knowledge_quality)
    start_knowledge_quality()
    # 城市门店月度考核(P1-10, CITYSTORE_ASSESSMENT_AUTO=off 可关闭)
    from services.citystore_assessment_scheduler import (
        start_scheduler as start_citystore_assessment)
    start_citystore_assessment()
    # 36号·AI智能推广: 热点雷达 + 发布出队(PROMO_RADAR_AUTO/PROMO_PUBLISH_AUTO=off 可关闭)
    from services.promo_scheduler import (
        start_radar_scheduler, start_publish_scheduler)
    start_radar_scheduler()
    start_publish_scheduler()
    # 37号·AI智能网站同盟: T+1 结算(ALLIANCE_SETTLE_AUTO=off 可关闭)
    from services.alliance_settle_scheduler import start_scheduler as start_alliance_settle
    start_alliance_settle()
    # 40号·平台流量DV博主: 作品雷达 + 发布出队 + 学习回流(BLOGGER_RADAR_AUTO/BLOGGER_PUBLISH_AUTO/BLOGGER_LEARNING_AUTO=off 可关闭)
    from services.blogger_scheduler import (
        start_radar_scheduler as start_blogger_radar,
        start_publish_scheduler as start_blogger_publish,
        start_learning_scheduler as start_blogger_learning)
    start_blogger_radar()
    start_blogger_publish()
    start_blogger_learning()
    # 41号·AI智能代驾: 学习回流(RIDE_LEARNING_AUTO=off 可关闭)
    from services.ride_scheduler import start_learning_scheduler as start_ride_learning
    start_ride_learning()
    # 43号·AI智能安全管理: UEBA基线日度重建+态势空窗评估
    # (SECURITY_SCHEDULER_MODE=on 开启, 默认 off)
    from services.security_scheduler import start_scheduler as start_security
    start_security()
    # 46号·AI治理与合规中枢: 档案健康日度巡检+新告警管理员触达
    # (AI_GOV_SCHEDULER_MODE=on 开启, 默认 off)
    from services.ai_governance_scheduler import start_scheduler as start_ai_gov
    start_ai_gov()
    # 50号·语音信值积分引擎: T+1 结算调度(L2/L3 pending
    # 聚合 → 45号 deposit 验真; VOICE50_SETTLE_MODE=on
    # 开启, 默认 off)
    from services.xiaozhu_voice50_scheduler import (
        start_scheduler as start_voice50_settle)
    start_voice50_settle()
    # 51号·小竹可信知识图谱: 日度巡检调度(三指标快照;
    # KG_INSPECT_MODE=on 开启, 默认 off)
    from services.kg51_scheduler import (
        start_scheduler as start_kg51_inspect)
    start_kg51_inspect()
    # 52号·小竹语音可用性评估引擎: 阈值告警日度调度
    # (五维基线+漂移告警当日同键去重;
    # US52_ALERT_MODE=on 开启, 默认 off)
    from services.us52_alert_scheduler import (
        start_scheduler as start_us52_alert)
    start_us52_alert()
    # 54号·小竹AI智能登录引擎大模型: 决策回流 T+1
    # 批次补标调度(LOGIN54_LEARN_MODE=on 开启, 默认 off)
    from services.login54_scheduler import (
        start_scheduler as start_login54_learn)
    start_login54_learn()
    # 55号·二维码AI智能管理: 决策回流 T+1 批次补标调度
    # (过期清扫+七类信号补标+指标快照; QR55_LEARN_MODE
    # =on 开启, 默认 off)
    from services.qr55_scheduler import (
        start_scheduler as start_qr55_learn)
    start_qr55_learn()
    # 56号·AI智能升级管理: 决策回流 T+1 补标调度
    # (七类信号+44号池双写; AIUP56_LEARN_MODE=on
    # 开启, 默认 off)
    from services.aiup56_scheduler import (
        start_scheduler as start_aiup56_learn)
    start_aiup56_learn()
    # 57号·AI智能知识库: 决策回流 T+1 补标调度
    # (六类信号+44号池双写+有效期检查;
    #  KB57_LEARN_MODE=on 开启, 默认 off)
    from services.kb57_scheduler import (
        start_scheduler as start_kb57_learn)
    start_kb57_learn()
    # 58号·AI智能优化意图识别: 决策回流 T+1 补标调度
    # (六类真值信号+44号池双写+高置信错误预警;
    #  II58_LEARN_MODE=on 开启, 默认 off)
    from services.ii58_scheduler import (
        start_scheduler as start_ii58_learn)
    start_ii58_learn()
    # 63号·AI智能后台管理: 决策回流+培训推送 T+1 调度
    # (六类终态信号+44号池双写+自动过审错误率预警
    #  +高频驳回点培训推送+7 日转化窗口;
    #  AB63_LEARN_MODE=on 开启, 默认 off)
    from services.ab63_scheduler import (
        start_scheduler as start_ab63_learn)
    start_ab63_learn()
    # 60号·AI智能支付管理: 支付回流+对账+预测 T+1 调度
    # (六类支付事件+44号池双写+对账批次差异检测
    #  +现金流预测缺口预警;
    #  PAY60_LEARN_MODE=on 开启, 默认 off)
    from services.pay60_scheduler import (
        start_scheduler as start_pay60_learn)
    start_pay60_learn()
    # 61号·AI智能系统升级决策: RLHF 反馈回流 T+1 调度
    # (七类终态信号+44号池双写+决策置信度
    #  校准预警;
    #  DM61_LEARN_MODE=on 开启, 默认 off)
    from services.dm61_scheduler import (
        start_scheduler as start_dm61_learn)
    start_dm61_learn()
    # 62号·AI智能无形资产估值: 验证回流+衰减结算 T+1 调度
    # (偏差三档信号+44号池双写 assessId 1:1 幂等
    #  +权重复审建议 46号+衰减批量结算;
    #  AV62_LEARN_MODE=on 开启, 默认 off)
    from services.av62_scheduler import (
        start_scheduler as start_av62_learn)
    start_av62_learn()


@app.on_event("shutdown")
async def _on_shutdown():
    """应用关闭时清理资源(Redis 连接), 避免连接泄漏"""
    logger.info("应用关闭中, 清理 Redis 连接...")
    # 停止后台调度任务
    from services.ai_learning_scheduler import stop_scheduler as stop_ai_learning
    from services.order_timeout_scheduler import stop_scheduler as stop_order_timeout
    stop_ai_learning()
    stop_order_timeout()
    # 知识库质量进化调度
    from services.knowledge_quality_scheduler import (
        stop_scheduler as stop_knowledge_quality)
    stop_knowledge_quality()
    # 城市门店月度考核
    from services.citystore_assessment_scheduler import (
        stop_scheduler as stop_citystore_assessment)
    stop_citystore_assessment()
    # 36号·AI智能推广调度器
    from services.promo_scheduler import stop_schedulers as stop_promo_schedulers
    stop_promo_schedulers()
    # 37号·AI智能网站同盟结算调度器
    from services.alliance_settle_scheduler import stop_scheduler as stop_alliance_settle
    stop_alliance_settle()
    # 40号·平台流量DV博主调度器
    from services.blogger_scheduler import stop_schedulers as stop_blogger_schedulers
    stop_blogger_schedulers()
    # 41号·AI智能代驾调度器
    from services.ride_scheduler import stop_schedulers as stop_ride_schedulers
    stop_ride_schedulers()
    # 43号·AI智能安全管理调度器
    from services.security_scheduler import stop_scheduler as stop_security_scheduler
    stop_security_scheduler()
    # 46号·AI治理与合规中枢调度器
    from services.ai_governance_scheduler import stop_scheduler as stop_ai_gov_scheduler
    stop_ai_gov_scheduler()
    # 50号·语音信值积分 T+1 结算调度器
    from services.xiaozhu_voice50_scheduler import (
        stop_scheduler as stop_voice50_settle)
    stop_voice50_settle()
    # 51号·小竹可信知识图谱巡检调度器
    from services.kg51_scheduler import (
        stop_scheduler as stop_kg51_inspect)
    stop_kg51_inspect()
    # 52号·小竹语音可用性评估告警调度器
    from services.us52_alert_scheduler import (
        stop_scheduler as stop_us52_alert)
    # 54号·小竹AI智能登录引擎学习调度器
    from services.login54_scheduler import (
        stop_scheduler as stop_login54_learn)
    stop_us52_alert()
    stop_login54_learn()
    # 55号·二维码AI智能管理学习调度器
    from services.qr55_scheduler import (
        stop_scheduler as stop_qr55_learn)
    stop_qr55_learn()
    # 56号·AI智能升级管理学习调度器
    from services.aiup56_scheduler import (
        stop_scheduler as stop_aiup56_learn)
    stop_aiup56_learn()
    # 57号·AI智能知识库学习调度器
    from services.kb57_scheduler import (
        stop_scheduler as stop_kb57_learn)
    stop_kb57_learn()
    # 58号·AI智能优化意图识别学习调度器
    from services.ii58_scheduler import (
        stop_scheduler as stop_ii58_learn)
    stop_ii58_learn()
    await close_redis_client()
    logger.info("清理完成")


# ============================================================
#  全局健康检查端点(不依赖任何业务模块, 供 Docker/K8s 探针使用)
# ============================================================

@app.get("/api/health", tags=["系统"])
async def health_check():
    """全局健康检查

    返回应用运行状态、运行时长和锁模式, 供容器编排系统探针使用。
    不依赖任何业务模块, 即使业务层异常也能响应。
    """
    return {
        "success": True,
        "status": "healthy",
        "uptime": uptime(),
        "lockMode": _get_lock_mode(),
    }


# ============================================================
#  P4.2 应用级指标: /metrics(Prometheus 文本格式) + HTTP 埋点中间件
# ============================================================

@app.get("/metrics", tags=["系统"])
async def metrics_endpoint():
    """应用指标暴露(Prometheus 文本格式, 纯标准库采集)

    指标: HTTP(QPS/延迟/状态码) + LLM(调用成功率/延迟/回退)
    + RAG 缓存命中。供既有 Prometheus 栈(见 docker-compose.monitoring)
    抓取: job 配置 targets 指向本端点即可, 无需额外 exporter。
    """
    from core.metrics import metrics_text
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(metrics_text(), media_type="text/plain")


@app.middleware("http")
async def _http_metrics_middleware(request: Request, call_next):
    """HTTP 请求埋点: 计数(QPS/状态码) + 延迟直方图

    /metrics 自身不埋点(避免抓取行为污染业务指标)。
    """
    from core.metrics import (http_request_duration,
                              http_requests_total)
    path = request.url.path
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        if path != "/metrics":
            http_requests_total.inc({"path": path, "code": 500})
            http_request_duration.observe(
                time.perf_counter() - start)
        raise
    if path != "/metrics":
        http_requests_total.inc(
            {"path": path, "code": response.status_code})
        http_request_duration.observe(time.perf_counter() - start)
    return response


# ============================================================
#  启动入口
# ============================================================

if __name__ == "__main__":
    import uvicorn

    _port = int(os.environ.get("PORT", "8000"))
    _host = os.environ.get("HOST", "0.0.0.0")
    _workers = int(os.environ.get("WORKERS", "1"))
    uvicorn.run(
        "main:app",
        host=_host,
        port=_port,
        workers=_workers,
        access_log=os.environ.get("ACCESS_LOG", "false").lower() == "true",
    )
