# -*- coding: utf-8 -*-
"""模型定义(合并版 2026-10-16 恢复)

上段: AI决策筹划模块(29号) Pydantic 模型——git c444dfb 版, 2026-10-15 10:37 被 85号 ORM 覆盖丢失, 本文件合并恢复;
下段: 85号·持久化层 ORM 四表(Drama/DramaRankSnapshot/CrawlJob, 2026-10-15 Day1 版)。
两段类名零冲突(已核)。主站 8000 因覆盖后重启失败, 恢复后可正常启动。
"""
from __future__ import annotations

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


# ============================================================
#  85号·持久化层 ORM 模型(Drama/DramaRankSnapshot/CrawlJob)
# ============================================================

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import JSON, TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Drama(Base):
    """短剧主表——dedup_key 生成列(itemId 优先/剧名|主平台兜底)由 MySQL 计算, ORM 只读。"""

    __tablename__ = "drama"

    drama_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[str | None] = mapped_column(String(64), comment="平台内视频ID(sv_ 前缀); 双入口归一根键")
    drama_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="剧名(契约 title)")
    main_platform: Mapped[str | None] = mapped_column(String(32), comment="主平台/采集入口")
    platforms: Mapped[str | None] = mapped_column(String(255), comment="多平台分发(逗号分隔)")
    episodes: Mapped[int | None] = mapped_column(Integer, comment="集数; videocollect 无集号 0 如实")
    duration_sec: Mapped[int | None] = mapped_column(Integer, comment="单集时长秒")
    genre_tags: Mapped[dict | None] = mapped_column(JSON, comment="题材标签(契约 tags)")
    is_ai_drama: Mapped[bool] = mapped_column(TINYINT(1), nullable=False, default=False,
                                              server_default=text("0"), comment="AI漫剧/AI短剧标记")
    publisher: Mapped[str | None] = mapped_column(String(255), comment="发行方")
    producer: Mapped[str | None] = mapped_column(String(255), comment="制作方")
    copyright_owner: Mapped[str | None] = mapped_column(String(255), comment="版权方")
    subtitle_text: Mapped[str | None] = mapped_column(Text, comment="简介摘要(截断存储)")
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime, comment="首次入库/上榜")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, comment="最近在池/上榜")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active",
                                        server_default=text("'active'"), comment="active/offline/new")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False,
                                                 server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False,
                                                 server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))
    # MySQL STORED 生成列: 插入/更新不由 ORM 提供该值
    dedup_key: Mapped[str] = mapped_column(
        String(320),
        Computed("COALESCE(NULLIF(item_id, ''), CONCAT(drama_name, '|', main_platform))"),
        comment="归一键: itemId 优先, 剧名|主平台兜底",
    )

    __table_args__ = (
        UniqueConstraint("dedup_key", name="uk_dedup"),
        Index("idx_platform", "main_platform"),
        Index("idx_status", "status"),
        Index("idx_last_seen", "last_seen_at"),
        Index("idx_drama_name", "drama_name"),
        {"comment": "短剧主表"},
    )


class DramaRankSnapshot(Base):
    """榜单/池快照表——share 池日快照与外部榜单期快照共用; uk_snap 天然幂等。"""

    __tablename__ = "drama_rank_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, comment="baidu/wukong/xigua/xinbang/dataeye/kuaishou/maoyan/huitun")
    rank_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="share_pool/hot/rising/new/weekly/monthly")
    rank_no: Mapped[int | None] = mapped_column(Integer, comment="排名; share_pool 可为 NULL")
    drama_id: Mapped[int | None] = mapped_column(BigInteger, comment="关联主表; 匹配失败 NULL 待 matcher 补链")
    drama_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="剧名冗余(原始口径)")
    heat_value: Mapped[int | None] = mapped_column(BigInteger, comment="热度整数; 缺失 NULL 不造数")
    heat_unit: Mapped[str | None] = mapped_column(String(16), comment="wan/yi/raw 换算前口径")
    play_delta: Mapped[int | None] = mapped_column(BigInteger, comment="播放增量")
    period_start: Mapped[str | None] = mapped_column(Date, comment="统计周期起")
    period_end: Mapped[int | None] = mapped_column(Date, comment="统计周期止")
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, nullable=False,
                                                    server_default=text("CURRENT_TIMESTAMP"))
    raw_url: Mapped[str | None] = mapped_column(String(512), comment="原始页面/端点")
    raw_json: Mapped[dict | None] = mapped_column(JSON, comment="原始契约/页面数据(metrics 区间值原样)")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False,
                                                 server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("source", "rank_type", "drama_name", "period_start", "period_end",
                         "snapshot_time", name="uk_snap"),
        Index("idx_source_type", "source", "rank_type", "snapshot_time"),
        Index("idx_period", "period_start", "period_end"),
        Index("idx_drama", "drama_id"),
        {"comment": "榜单/池快照表"},
    )


class DramaRankItem(Base):
    """榜单明细表(跨源对齐规范行, 二期启用)。"""

    __tablename__ = "drama_rank_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="关联快照")
    drama_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="关联主表")
    rank_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="当期排名")
    heat_value: Mapped[int | None] = mapped_column(BigInteger)
    play_delta: Mapped[int | None] = mapped_column(BigInteger)
    period_start: Mapped[str | None] = mapped_column(Date)
    period_end: Mapped[str | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False,
                                                 server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_snapshot", "snapshot_id"),
        Index("idx_drama", "drama_id"),
        Index("idx_source_period", "source", "period_start", "period_end"),
        {"comment": "榜单明细表"},
    )


class CrawlJob(Base):
    """采集任务表——APScheduler 调度对照; uk_job(source,url) 幂等。"""

    __tablename__ = "crawl_job"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, comment="任务源")
    channel: Mapped[int] = mapped_column(TINYINT, nullable=False, default=2,
                                         server_default=text("2"), comment="1=第三方API 2=自建公开页 3=官方开放")
    url: Mapped[str] = mapped_column(String(512), nullable=False, comment="采集地址或端点")
    method: Mapped[str] = mapped_column(String(16), default="html",
                                        server_default=text("'html'"), comment="html/api/contract/manual")
    cron_expr: Mapped[str | None] = mapped_column(String(32), comment="调度表达式(APScheduler 对照)")
    status: Mapped[str] = mapped_column(String(16), default="pending",
                                        server_default=text("'pending'"), comment="success/failed/skipped/pending")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False,
                                                 server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False,
                                                 server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("source", "url", name="uk_job"),
        Index("idx_source_status", "source", "status"),
        Index("idx_next_run", "next_run_at"),
        {"comment": "采集任务表"},
    )
