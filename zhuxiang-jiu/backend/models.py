"""
AI决策筹划模块(模块29·AI大脑中枢) Pydantic 模型定义
参照 OpenAPI 3.0 规范 ai-decision-module-29.openapi.json
6层架构: 感知→知识→决策→编排→执行→反馈
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
#  通用模型
# ============================================================

class LogEntry(BaseModel):
    stage: str = Field(..., description="执行阶段")
    message: str = Field(..., description="日志消息")
    data: dict[str, Any] | None = Field(None, description="附加数据")


class BaseSuccessResponse(BaseModel):
    success: bool = True
    operation: str | None = None
    details: dict[str, Any] | None = None
    logs: list[LogEntry] = Field(default_factory=list)
    asyncOps: list[str] = Field(default_factory=list, alias="asyncOps")


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    errorCode: str | None = Field(None, alias="errorCode")
    failedStage: str | None = Field(None, alias="failedStage")
    logs: list[LogEntry] = Field(default_factory=list)


class RoleEnum(StrEnum):
    member = "member"
    agent = "agent"
    guest = "guest"
    store_owner = "store_owner"
    admin = "admin"


class DecisionErrorCode(StrEnum):
    e001 = "DECISION_001"
    e002 = "DECISION_002"
    e003 = "DECISION_003"
    e004 = "DECISION_004"
    e005 = "DECISION_005"
    e006 = "DECISION_006"
    e007 = "DECISION_007"
    e008 = "DECISION_008"
    e009 = "DECISION_009"
    e010 = "DECISION_010"
    e011 = "DECISION_011"
    e012 = "DECISION_012"
    e013 = "DECISION_013"
    e014 = "DECISION_014"


# ============================================================
#  感知层模型
# ============================================================

class DataIngestPayloadItem(BaseModel):
    key: str
    value: float
    timestamp: datetime


class DataIngestRequest(BaseModel):
    source: str = Field(..., description="数据来源,格式 module:{id}", example="module:04")
    dataType: str = Field(..., description="数据类型", pattern="^(metrics|events|logs|alerts)$")
    payload: list[DataIngestPayloadItem]
    realtime: bool = False


class DataIngestDetails(BaseModel):
    ingestedCount: int
    source: str
    bufferState: str = "healthy"
    processedAt: datetime


# ============================================================
#  知识层模型
# ============================================================

class KnowledgeResult(BaseModel):
    content: str
    source: str
    confidence: float = Field(..., ge=0, le=1)
    tags: list[str] = Field(default_factory=list)


class KnowledgeContext(BaseModel):
    semanticGraph: str | None = None
    orgMemory: str | None = None


class KnowledgeQueryDetails(BaseModel):
    query: str
    recallRate: str = "93%"
    results: list[KnowledgeResult] = Field(default_factory=list)
    context: KnowledgeContext | None = None


class KnowledgeIngestRequest(BaseModel):
    category: str = Field(..., pattern="^(rule|lesson_learned|constraint|red_line)$")
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source: str | None = None


class KnowledgeIngestDetails(BaseModel):
    knowledgeId: str
    category: str
    indexed: bool = True
    graphUpdated: bool = True


# ============================================================
#  决策层模型
# ============================================================

class StrategyConstraints(BaseModel):
    budget: float | None = None
    timeframe: str | None = None
    riskTolerance: str | None = Field(None, pattern="^(low|medium|high)$")


class WhatIfScenario(BaseModel):
    name: str
    params: dict[str, Any]


class StrategyPlanRequest(BaseModel):
    role: RoleEnum
    goal: str
    constraints: StrategyConstraints
    whatIfScenarios: list[WhatIfScenario] = Field(default_factory=list)


class StrategyPlanDetails(BaseModel):
    goalDecomposed: list[dict[str, Any]]
    resourceAssessment: dict[str, Any]
    recommendedPath: str
    whatIfResults: list[dict[str, Any]] = Field(default_factory=list)
    planningEfficiency: str = "60%"
    needsApproval: bool = False


class ForecastSimulateRequest(BaseModel):
    target: str = Field(..., pattern="^(sales_volume|inventory|capacity|churn)$")
    timeframe: str = Field(..., pattern="^(next_7d|next_30d|next_90d)$")
    method: str = Field("monte_carlo", pattern="^(monte_carlo|lstm|arima)$")
    iterations: int = Field(10000, ge=100, le=100000)
    factors: list[str] = Field(default_factory=list)


class ForecastResult(BaseModel):
    p50: float
    p75: float
    p95: float
    confidenceInterval: list[float]


class ForecastSimulateDetails(BaseModel):
    target: str
    forecast: ForecastResult
    accuracy: str = "90%"
    factorsWeight: dict[str, float] = Field(default_factory=dict)
    method: str
    iterations: int


class ProposedAction(BaseModel):
    action: str
    target: str
    params: dict[str, Any] | None = None


class GovernanceRequest(BaseModel):
    proposedAction: ProposedAction
    ruleCheck: bool = True
    requireApproval: str = Field("auto", pattern="^(auto|manual)$")


class RuleCheckResult(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)
    checkedRules: int


class BlockchainNotarize(BaseModel):
    hash: str
    type: str


class GovernanceDetails(BaseModel):
    action: str
    ruleCheckResult: RuleCheckResult
    permissionGranted: bool
    executionMode: str
    blockchainNotarize: BlockchainNotarize | None = None
    complianceRate: str = "100%"


class RiskControlRequest(BaseModel):
    checkType: str = Field(..., pattern="^(transaction_anomaly|compliance|fraud|abuse)$")
    target: dict[str, Any]
    thresholds: dict[str, Any] | None = None
    autoCircuitBreak: bool = True


class RiskControlDetails(BaseModel):
    riskLevel: str = Field(..., pattern="^(low|medium|high|critical)$")
    anomalies: list[dict[str, Any]] = Field(default_factory=list)
    circuitBreaker: str = Field("standby", pattern="^(standby|tripped|open)$")
    recommendation: str
    coverage: str = "96%"
    action: str


# ============================================================
#  编排层模型
# ============================================================

class OrchestrateRequest(BaseModel):
    workflow: str
    modules: list[str]
    context: dict[str, Any]
    decompose: bool = True
    parallel: bool = True


class OrchestrateDetails(BaseModel):
    workflow: str
    tasks: list[dict[str, Any]]
    parallelGroups: list[list[str]] = Field(default_factory=list)
    successRate: str = "95%"
    duration: str


class CapabilityRouteRequest(BaseModel):
    requiredCapabilities: list[str]
    task: str
    budget: dict[str, Any] | None = None
    preferPlugins: list[str] = Field(default_factory=list)


class CapabilityRouteDetails(BaseModel):
    selectedPlugins: list[dict[str, Any]]
    composition: str
    totalLatency: str
    totalCost: str
    reuseRate: str = "78%"
    pluginPool: int = 120


# ============================================================
#  执行层模型
# ============================================================

class RoleCopilotRequest(BaseModel):
    role: RoleEnum
    level: str | None = None
    intent: str
    context: dict[str, Any] | None = None
    mode: str = Field("copilot", pattern="^(copilot|agent)$")


class RoleCopilotDetails(BaseModel):
    role: str
    recommendations: list[dict[str, Any]]
    decisionAccuracy: str = "92%"
    executionMode: str
    needsUserConfirm: bool = True


# ============================================================
#  反馈层模型
# ============================================================

class FeedbackOutcome(BaseModel):
    delivered: bool
    clicked: bool
    ordered: bool
    complained: bool = False


class FeedbackLoopRequest(BaseModel):
    actionId: str
    outcome: FeedbackOutcome
    cost: float | None = None
    reflowMetrics: list[str] = Field(default_factory=list)


class FeedbackLoopDetails(BaseModel):
    actionId: str
    evaluation: dict[str, Any]
    modelUpdate: dict[str, Any]
    feedbackLatency: str = "<24h"
    blockchainNotarize: BlockchainNotarize | None = None


class RetrospectiveRequest(BaseModel):
    event: str
    period: str
    scope: list[str] = Field(default_factory=list)
    depth: str = Field("summary", pattern="^(summary|root_cause)$")


class RetrospectiveDetails(BaseModel):
    event: str
    analysis: dict[str, Any]
    lessonsLearned: list[dict[str, Any]]
    strategyOptimization: str
    coverage: str = "85%"


# ============================================================
#  系统模型
# ============================================================

class HealthDetails(BaseModel):
    status: str = "healthy"
    module: str = "AI决策筹划模块(29)"
    aiRate: str = "95%"
    layers: str = "感知→知识→决策→编排→执行→反馈"
    pluginPool: int = 120
    uptime: str
    mockMode: bool = True


class ModeDetails(BaseModel):
    mode: str = "mock"
    apiBase: str = "/api/decision"
    endpoints: int = 15
    aiCapabilities: int = 10


class ModeSwitchRequest(BaseModel):
    mode: str = Field(..., pattern="^(mock|live)$")
    apiBase: str | None = None


class ModeSwitchDetails(BaseModel):
    mode: str
    apiBase: str
