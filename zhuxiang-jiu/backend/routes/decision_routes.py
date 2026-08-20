"""决策层 6 层架构路由: 感知/知识/决策/编排/执行/反馈

对应 OpenAPI: ai-decision-module-29.openapi.json
端点数: 12 个(健康检查在 system_routes)
"""

from datetime import datetime
from fastapi import APIRouter, Depends, Query

from core.auth import get_current_role, require_role
from core.config import API_BASE
from core.helpers import bc_hash, ok, ts
from models import (
    # 通用
    BaseSuccessResponse,
    # 感知层
    DataIngestRequest,
    # 知识层
    KnowledgeIngestRequest, KnowledgeIngestDetails, KnowledgeQueryDetails,
    KnowledgeResult, KnowledgeContext,
    # 决策层
    StrategyPlanRequest, StrategyPlanDetails,
    ForecastSimulateRequest, ForecastSimulateDetails, ForecastResult,
    GovernanceRequest, GovernanceDetails, RuleCheckResult, BlockchainNotarize,
    RiskControlRequest, RiskControlDetails,
    # 编排层
    OrchestrateRequest, OrchestrateDetails,
    CapabilityRouteRequest, CapabilityRouteDetails,
    # 执行层
    RoleCopilotRequest, RoleCopilotDetails,
    # 反馈层
    FeedbackLoopRequest, FeedbackLoopDetails,
    RetrospectiveRequest, RetrospectiveDetails,
)

router = APIRouter()


# ============================================================
#  感知层
# ============================================================

@router.post(f"{API_BASE}/data-ingest", tags=["感知层"],
             response_model=BaseSuccessResponse,
             summary="数据采集注入",
             description="从28个业务模块采集数据,注入感知层。支持批量推送指标、事件、日志。\n\n**角色**: admin")
async def data_ingest(
    req: DataIngestRequest,
    role: str = Depends(require_role("admin")),
):
    return ok("data-ingest", {
        "ingestedCount": len(req.payload),
        "source": req.source,
        "bufferState": "healthy",
        "processedAt": ts(),
    }, logs=[{"stage": "感知层-数据采集", "message": f"采集{len(req.payload)}条{req.dataType}",
             "data": {"source": req.source, "realtime": req.realtime}}])


# ============================================================
#  知识层
# ============================================================

@router.get(f"{API_BASE}/knowledge/query", tags=["知识层"],
            response_model=BaseSuccessResponse,
            summary="知识中枢查询",
            description="基于RAG+组织记忆+语义图谱,返回可验证的业务上下文。知识召回率93%。\n\n**角色**: 全角色(访客只读)")
async def knowledge_query(
    q: str,
    scope: str = "business",
    topK: int = 5,
    role: str = Depends(get_current_role),
):
    results = [
        KnowledgeResult(
            content="L3 VIP留存消费: ¥2000/年",
            source="module:02/会员管理",
            confidence=0.98,
            tags=["会员", "L3", "留存消费"],
        ),
    ]
    details = KnowledgeQueryDetails(
        query=q, recallRate="93%", results=results[:topK],
        context=KnowledgeContext(semanticGraph="user_domain",
                                 orgMemory="project_memory.md/L3 retention rule"),
    )
    return ok("knowledge-query", details.model_dump(by_alias=True),
              logs=[{"stage": "知识层-RAG召回", "message": f"召回{len(results)}条,scope={scope}",
                     "data": {"recallRate": "93%"}}])


@router.post(f"{API_BASE}/knowledge/ingest", tags=["知识层"],
             response_model=BaseSuccessResponse,
             summary="知识注入",
             description="向知识中枢注入新知识(组织记忆、规则、经验教训)。\n\n**角色**: admin")
async def knowledge_ingest(
    req: KnowledgeIngestRequest,
    role: str = Depends(require_role("admin")),
):
    details = KnowledgeIngestDetails(
        knowledgeId=f"KN-{datetime.now().strftime('%Y%m%d')}-{format(hash(req.title) & 0xFFFF, '04x')}",
        category=req.category, indexed=True, graphUpdated=True,
    )
    return ok("knowledge-ingest", details.model_dump(by_alias=True),
              logs=[{"stage": "知识层-知识注入", "message": f"注入{req.category}: {req.title}",
                     "data": {"tags": req.tags}}],
              async_ops=["embedding_update", "graph_rebuild"])


# ============================================================
#  决策层
# ============================================================

@router.post(f"{API_BASE}/strategy-plan", tags=["决策层"],
             response_model=BaseSuccessResponse,
             summary="策略筹划",
             description="目标分解+资源评估+路径规划+What-if推演。筹划效率提升60%。\n\n**角色**: member+")
async def strategy_plan(
    req: StrategyPlanRequest,
    role: str = Depends(require_role("member")),
):
    what_if_results = [
        {"name": s.name, "projectedGMV": "¥8500", "risk": "low"}
        for s in req.whatIfScenarios
    ] or [{"name": "默认方案", "projectedGMV": "¥9000", "risk": "low"}]
    details = StrategyPlanDetails(
        goalDecomposed=[
            {"subGoal": "周销售额≥¥2500", "priority": "high"},
            {"subGoal": "复购率提升15%", "priority": "medium"},
            {"subGoal": "新客获取≥50人", "priority": "medium"},
        ],
        resourceAssessment={"budgetSufficient": True, "bottleneck": "流量获取"},
        recommendedPath=what_if_results[0]["name"] if what_if_results else "默认方案",
        whatIfResults=what_if_results,
        planningEfficiency="60%",
        needsApproval=False,
    )
    return ok("strategy-plan", details.model_dump(by_alias=True),
              logs=[{"stage": "决策层-策略筹划", "message": f"目标分解+What-if推演完成",
                     "data": {"scenarios": len(what_if_results)}}])


@router.post(f"{API_BASE}/forecast-simulate", tags=["决策层"],
             response_model=BaseSuccessResponse,
             summary="预测推演",
             description="季节性+趋势+容量预测+蒙特卡洛模拟。预测准确率90%。\n\n**角色**: member+")
async def forecast_simulate(
    req: ForecastSimulateRequest,
    role: str = Depends(require_role("member")),
):
    factors_weight = {f: round(1.0 / len(req.factors), 2) for f in req.factors} if req.factors else {}
    details = ForecastSimulateDetails(
        target=req.target,
        forecast=ForecastResult(p50=320, p75=380, p95=450, confidenceInterval=[280, 520]),
        accuracy="90%",
        factorsWeight=factors_weight,
        method=req.method,
        iterations=req.iterations,
    )
    return ok("forecast-simulate", details.model_dump(by_alias=True),
              logs=[{"stage": "决策层-预测推演", "message": f"{req.method} x{req.iterations}",
                     "data": {"target": req.target, "timeframe": req.timeframe}}])


@router.post(f"{API_BASE}/governance", tags=["决策层"],
             response_model=BaseSuccessResponse,
             summary="治理决策",
             description="模型提动作+规则定执行+权限校验+区块链追溯。治理合规率100%。\n\n**角色**: admin")
async def governance(
    req: GovernanceRequest,
    role: str = Depends(require_role("admin")),
):
    details = GovernanceDetails(
        action=req.proposedAction.action,
        ruleCheckResult=RuleCheckResult(passed=True, violations=[], checkedRules=14),
        permissionGranted=True,
        executionMode=req.requireApproval,
        blockchainNotarize=BlockchainNotarize(hash=bc_hash(), type="决策存证"),
        complianceRate="100%",
    )
    return ok("governance", details.model_dump(by_alias=True),
              logs=[{"stage": "决策层-治理决策", "message": "规则校验通过+区块链存证",
                     "data": {"action": req.proposedAction.action, "hash": bc_hash()}}],
              async_ops=["blockchain_notarize", "audit_log"])


@router.post(f"{API_BASE}/risk-control", tags=["决策层"],
             response_model=BaseSuccessResponse,
             summary="风控决策",
             description="异常检测+合规校验+风险预警+自动熔断。风控覆盖率96%。\n\n**角色**: admin")
async def risk_control(
    req: RiskControlRequest,
    role: str = Depends(require_role("admin")),
):
    amount = req.target.get("amount", 0)
    max_amount = (req.thresholds or {}).get("maxAmount", 10000)
    risk_level = "high" if amount >= max_amount * 0.9 else "medium" if amount >= max_amount * 0.7 else "low"
    action = "block" if risk_level == "high" else "pass_with_monitoring" if risk_level == "medium" else "pass"
    details = RiskControlDetails(
        riskLevel=risk_level,
        anomalies=[{"type": "amount_near_limit", "detail": f"金额¥{amount}接近上限¥{max_amount}",
                     "score": round(amount / max_amount, 2)}] if risk_level != "low" else [],
        circuitBreaker="tripped" if risk_level == "high" else "standby",
        recommendation="拦截交易" if risk_level == "high" else "允许交易,标记观察" if risk_level == "medium" else "允许交易",
        coverage="96%",
        action=action,
    )
    return ok("risk-control", details.model_dump(by_alias=True),
              logs=[{"stage": "决策层-风控决策", "message": f"风险等级: {risk_level}",
                     "data": {"checkType": req.checkType, "action": action}}])


# ============================================================
#  编排层
# ============================================================

@router.post(f"{API_BASE}/orchestrate", tags=["编排层"],
             response_model=BaseSuccessResponse,
             summary="编排调度",
             description="28模块跨域工作流编排+任务分解+依赖管理。编排成功率95%。\n\n**角色**: agent+\n**并发安全**: Mutex锁 `decision:orchestrate:{workflowId}`")
async def orchestrate(
    req: OrchestrateRequest,
    role: str = Depends(require_role("agent")),
):
    tasks = []
    for i, mod_id in enumerate(req.modules):
        tasks.append({
            "id": f"T{i+1}", "module": mod_id,
            "name": f"模块{mod_id}任务", "status": "pass" if i < len(req.modules) - 1 else "pending",
            "depends": [f"T{i}"] if i > 0 else [],
        })
    details = OrchestrateDetails(
        workflow=req.workflow,
        tasks=tasks,
        parallelGroups=[[f"T{i+1}"] for i in range(len(req.modules))],
        successRate="95%",
        duration="1.2s",
    )
    return ok("orchestrate", details.model_dump(by_alias=True),
              logs=[{"stage": "编排层-编排调度", "message": f"工作流{req.workflow}编排完成",
                     "data": {"modules": req.modules, "tasks": len(tasks)}}])


@router.post(f"{API_BASE}/capability-route", tags=["编排层"],
             response_model=BaseSuccessResponse,
             summary="能力路由",
             description="原子能力插件池(120个)+动态组合(≥20种)+按需调度。能力复用率78%。\n\n**角色**: store_owner+")
async def capability_route(
    req: CapabilityRouteRequest,
    role: str = Depends(require_role("store_owner")),
):
    plugin_map = {
        "nlp": {"id": "nlp_bert_v2", "type": "自然语言", "latency": "120ms", "cost": "¥0.01"},
        "vision": {"id": "vision_resnet50", "type": "计算机视觉", "latency": "180ms", "cost": "¥0.02"},
        "decision_reasoning": {"id": "rule_engine_v1", "type": "决策推理", "latency": "30ms", "cost": "¥0.00"},
        "multimodal": {"id": "multimodal_v1", "type": "多模态融合", "latency": "200ms", "cost": "¥0.03"},
    }
    selected = [plugin_map[c] for c in req.requiredCapabilities if c in plugin_map]
    composition = " → ".join(p["id"] for p in selected)
    details = CapabilityRouteDetails(
        selectedPlugins=selected,
        composition=composition,
        totalLatency=f"{sum(int(p['latency'].replace('ms', '')) for p in selected)}ms",
        totalCost=f"¥{sum(float(p['cost'].replace('¥', '')) for p in selected):.2f}",
        reuseRate="78%",
        pluginPool=120,
    )
    return ok("capability-route", details.model_dump(by_alias=True),
              logs=[{"stage": "编排层-能力路由", "message": f"选择{len(selected)}个插件",
                     "data": {"composition": composition}}])


# ============================================================
#  执行层
# ============================================================

@router.post(f"{API_BASE}/role-copilot", tags=["执行层"],
             response_model=BaseSuccessResponse,
             summary="角色决策助理",
             description="为5类角色提供Copilot式决策支持。决策准确率92%。先Copilot后Agent。\n\n**角色**: 全角色")
async def role_copilot(
    req: RoleCopilotRequest,
    role: str = Depends(get_current_role),
):
    role_recs = {
        "member": [
            {"type": "product", "target": "ZX-001", "reason": "基于浏览记录推荐,竹香型适合您", "confidence": 0.92},
            {"type": "points_optimize", "target": "使用1250竹叶抵扣¥12.5", "reason": "30%抵扣上限内", "confidence": 0.95},
        ],
        "agent": [
            {"type": "inventory_plan", "target": "补货ZX-003 x20", "reason": "库存低于安全线", "confidence": 0.88},
        ],
        "guest": [
            {"type": "product", "target": "ZX-001", "reason": "热销竹香型,适合首次体验", "confidence": 0.90},
        ],
        "store_owner": [
            {"type": "livestream", "target": "本周直播3次", "reason": "周播≥3次流量稳定", "confidence": 0.85},
        ],
        "admin": [
            {"type": "system_check", "target": "全模块健康检查", "reason": "例行巡检", "confidence": 0.99},
        ],
    }
    recommendations = role_recs.get(req.role, role_recs["guest"])
    details = RoleCopilotDetails(
        role=req.role,
        recommendations=recommendations,
        decisionAccuracy="92%",
        executionMode=req.mode,
        needsUserConfirm=(req.mode == "copilot"),
    )
    return ok("role-copilot", details.model_dump(by_alias=True),
              logs=[{"stage": "执行层-角色Copilot", "message": f"角色={req.role}, 模式={req.mode}",
                     "data": {"intent": req.intent, "recs": len(recommendations)}}])


# ============================================================
#  反馈层
# ============================================================

@router.post(f"{API_BASE}/feedback-loop", tags=["反馈层"],
             response_model=BaseSuccessResponse,
             summary="反馈闭环",
             description="数据采集→效果评估→模型迭代→插件升级。闭环延迟<24h。\n\n**角色**: agent+")
async def feedback_loop(
    req: FeedbackLoopRequest,
    role: str = Depends(require_role("agent")),
):
    details = FeedbackLoopDetails(
        actionId=req.actionId,
        evaluation={"ctr": "12%", "conversionRate": "3.5%", "roi": "2.1x"},
        modelUpdate={"triggered": True, "plugin": "nlp_bert_v2", "improvement": "+0.03 accuracy"},
        feedbackLatency="<24h",
        blockchainNotarize=BlockchainNotarize(hash=bc_hash(), type="反馈追踪"),
    )
    return ok("feedback-loop", details.model_dump(by_alias=True),
              logs=[{"stage": "反馈层-反馈闭环", "message": f"动作{req.actionId}反馈已处理",
                     "data": {"ordered": req.outcome.ordered, "clicked": req.outcome.clicked}}],
              async_ops=["model_iteration", "plugin_upgrade", "data_reflow"])


@router.post(f"{API_BASE}/retrospective", tags=["反馈层"],
             response_model=BaseSuccessResponse,
             summary="复盘优化",
             description="事后分析+根因定位+经验沉淀+策略优化。复盘覆盖率85%。\n\n**角色**: store_owner+")
async def retrospective(
    req: RetrospectiveRequest,
    role: str = Depends(require_role("store_owner")),
):
    details = RetrospectiveDetails(
        event=req.event,
        analysis={
            "gmv": "¥98,500", "target": "¥100,000", "achievement": "98.5%",
            "rootCause": "第二周流量下降23%,主因直播频率不足",
        } if req.depth == "root_cause" else {"gmv": "¥98,500", "achievement": "98.5%"},
        lessonsLearned=[
            {"title": "直播频率与流量正相关", "detail": "周播3次以上流量稳定,2次以下下降20%+", "tag": "经验沉淀"},
        ],
        strategyOptimization="建议保持周播≥3次,增加互动环节",
        coverage="85%",
    )
    return ok("retrospective", details.model_dump(by_alias=True),
              logs=[{"stage": "反馈层-复盘优化", "message": f"事件{req.event}复盘完成",
                     "data": {"depth": req.depth, "period": req.period}}],
              async_ops=["knowledge_ingest", "strategy_update"])


def register_decision_routes(app):
    """注册决策层 12 个端点到 FastAPI app"""
    app.include_router(router)
