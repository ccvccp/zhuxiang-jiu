"""合规合法智能监控模块路由(16 端点)

鉴权:
    - 管理端: X-Role: admin 头(全部接口需管理员权限)

端点分布:
    - 行为监控(3):    behavior-monitor / behavior-list / behavior-detail
    - 条款监控(2):    terms-monitor / terms-list
    - 法律知识(2):    legal-add / legal-search
    - 风险预警(2):    risk-warning / risk-list
    - 监管报送(2):    regulatory-report / regulatory-accept
    - 区块链存证(2):  blockchain-evidence / blockchain-verify
    - 分析报告(1):    analysis-report
    - 持续优化(1):    optimization-update
    - 统计(1):        stats
"""

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.compliance_service import ComplianceService
from repositories.compliance_repository import (
    RISK_LEVEL_LOW,
)


router = APIRouter()
_service = ComplianceService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class BehaviorMonitorRequest(PydBaseModel):
    moduleName: str = Field(..., description="监控模块名称")
    behaviorType: str = Field(..., description="行为类型")
    behaviorData: dict[str, Any] = Field(default_factory=dict, description="行为数据")
    complianceCheck: dict[str, Any] = Field(default_factory=dict, description="合规自检")
    anomalyIdentify: dict[str, Any] = Field(default_factory=dict, description="异常识别")
    riskLevel: str = Field(RISK_LEVEL_LOW, description="风险等级")
    aiAutomationRate: float = Field(90.0, description="AI自动化率")


class TermsMonitorRequest(PydBaseModel):
    termsType: str = Field(..., description="条款类型")
    termsName: str = Field(..., description="条款名称")
    termsContent: str = Field("", description="条款内容")
    legalityReview: dict[str, Any] = Field(default_factory=dict, description="合法性审查")
    complianceReview: dict[str, Any] = Field(default_factory=dict, description="合规审查")
    validityVerify: dict[str, Any] = Field(default_factory=dict, description="有效性验证")
    riskTermsIdentify: dict[str, Any] = Field(default_factory=dict, description="风险条款识别")
    riskLevel: str = Field(RISK_LEVEL_LOW, description="风险等级")
    aiAutomationRate: float = Field(85.0, description="AI自动化率")


class LegalKnowledgeRequest(PydBaseModel):
    lawName: str = Field(..., description="法律名称")
    lawCategory: str = Field(..., description="法律类别")
    lawArticles: str = Field("", description="法律条文")
    lawInterpretation: str = Field("", description="法律解读")
    caseLibrary: str = Field("", description="案例库")
    ruleLibrary: str = Field("", description="规则库")


class RiskWarningRequest(PydBaseModel):
    riskType: str = Field(..., description="风险类型")
    riskSource: str = Field(..., description="风险来源")
    riskIdentify: dict[str, Any] = Field(default_factory=dict, description="风险识别")
    riskScore: float = Field(0.0, description="风险评分")
    riskLevel: str | None = Field(None, description="风险等级(为空则自动分级)")
    aiAutomationRate: float = Field(95.0, description="AI自动化率")


class RegulatoryReportRequest(PydBaseModel):
    reportType: str = Field(..., description="报送类型")
    reportTarget: str = Field(..., description="报送对象")
    reportData: dict[str, Any] = Field(default_factory=dict, description="报送数据")
    aiAutomationRate: float = Field(90.0, description="AI自动化率")


class BlockchainEvidenceRequest(PydBaseModel):
    evidenceType: str = Field(..., description="存证类型")
    evidenceData: str = Field("", description="存证数据")
    aiAutomationRate: float = Field(95.0, description="AI自动化率")


class AnalysisReportRequest(PydBaseModel):
    analysisPeriod: str = Field(..., description="分析周期(daily/weekly/monthly)")
    effectAnalysis: dict[str, Any] = Field(default_factory=dict, description="效果分析")
    roiEvaluation: dict[str, Any] = Field(default_factory=dict, description="ROI评估")
    trendPrediction: dict[str, Any] = Field(default_factory=dict, description="趋势预测")
    experienceRetention: dict[str, Any] = Field(default_factory=dict, description="经验沉淀")
    aiAutomationRate: float = Field(85.0, description="AI自动化率")


class OptimizationRequest(PydBaseModel):
    optimizationType: str = Field(..., description="优化类型")
    ruleOptimize: dict[str, Any] = Field(default_factory=dict, description="规则优化")
    knowledgeUpdate: dict[str, Any] = Field(default_factory=dict, description="知识更新")
    experienceRetention: dict[str, Any] = Field(default_factory=dict, description="经验沉淀")
    continuousImprove: dict[str, Any] = Field(default_factory=dict, description="持续改进")
    aiAutomationRate: float = Field(85.0, description="AI自动化率")


# ============================================================
# P0 接口(12 个)
# ============================================================

# --- 行为监控 ---

@router.post("/api/compliance/behavior/monitor", tags=["合规合法智能监控模块"])
async def monitor_behavior(
    data: BehaviorMonitorRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """全网行为监控(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.monitor_behavior(
            module_name=data.moduleName,
            behavior_type=data.behaviorType,
            behavior_data=data.behaviorData,
            compliance_check=data.complianceCheck,
            anomaly_identify=data.anomalyIdentify,
            risk_level=data.riskLevel,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/compliance/behavior/list", tags=["合规合法智能监控模块"])
async def list_behavior_monitors(
    module_name: str = Query(None, description="按模块筛选"),
    risk_level: str = Query(None, description="按风险等级筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询行为监控列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_behavior_monitors(module_name, risk_level, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


@router.get("/api/compliance/behavior/{record_id}", tags=["合规合法智能监控模块"])
async def get_behavior_monitor(record_id: int):
    """查询行为监控详情"""
    try:
        result = await _service.get_behavior_monitor(record_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 条款监控 ---

@router.post("/api/compliance/terms/monitor", tags=["合规合法智能监控模块"])
async def monitor_terms(
    data: TermsMonitorRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """条款协议监控(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.monitor_terms(
            terms_type=data.termsType,
            terms_name=data.termsName,
            terms_content=data.termsContent,
            legality_review=data.legalityReview,
            compliance_review=data.complianceReview,
            validity_verify=data.validityVerify,
            risk_terms_identify=data.riskTermsIdentify,
            risk_level=data.riskLevel,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/compliance/terms/list", tags=["合规合法智能监控模块"])
async def list_terms_monitors(
    terms_type: str = Query(None, description="按条款类型筛选"),
    risk_level: str = Query(None, description="按风险等级筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询条款监控列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_terms_monitors(terms_type, risk_level, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 法律知识 ---

@router.post("/api/compliance/legal/add", tags=["合规合法智能监控模块"])
async def add_legal_knowledge(
    data: LegalKnowledgeRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """新增法律知识(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.add_legal_knowledge(
            law_name=data.lawName,
            law_category=data.lawCategory,
            law_articles=data.lawArticles,
            law_interpretation=data.lawInterpretation,
            case_library=data.caseLibrary,
            rule_library=data.ruleLibrary,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/compliance/legal/search", tags=["合规合法智能监控模块"])
async def search_legal_knowledge(
    keyword: str = Query(None, description="检索关键词"),
    law_category: str = Query(None, description="按法律类别筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """法律知识检索(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.search_legal_knowledge(keyword, law_category, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 风险预警 ---

@router.post("/api/compliance/risk/warning", tags=["合规合法智能监控模块"])
async def raise_risk_warning(
    data: RiskWarningRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """风险预警(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.raise_risk_warning(
            risk_type=data.riskType,
            risk_source=data.riskSource,
            risk_identify=data.riskIdentify,
            risk_score=data.riskScore,
            risk_level=data.riskLevel,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/compliance/risk/list", tags=["合规合法智能监控模块"])
async def list_risk_warnings(
    risk_type: str = Query(None, description="按风险类型筛选"),
    risk_level: str = Query(None, description="按风险等级筛选"),
    limit: int = Query(50, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询风险预警列表(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.list_risk_warnings(risk_type, risk_level, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# --- 监管报送 ---

@router.post("/api/compliance/regulatory/report", tags=["合规合法智能监控模块"])
async def submit_regulatory_report(
    data: RegulatoryReportRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """监管报送(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.submit_regulatory_report(
            report_type=data.reportType,
            report_target=data.reportTarget,
            report_data=data.reportData,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/compliance/regulatory/{record_id}/accept", tags=["合规合法智能监控模块"])
async def accept_regulatory_report(
    record_id: int,
    x_role: str = Header(None, alias="X-Role"),
):
    """受理监管报送(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.accept_regulatory_report(record_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 区块链存证 ---

@router.post("/api/compliance/blockchain/evidence", tags=["合规合法智能监控模块"])
async def add_blockchain_evidence(
    data: BlockchainEvidenceRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """区块链存证(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.add_blockchain_evidence(
            evidence_type=data.evidenceType,
            evidence_data=data.evidenceData,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/compliance/blockchain/verify", tags=["合规合法智能监控模块"])
async def verify_evidence(
    hash: str = Query(..., description="存证哈希"),
):
    """按哈希验证存证"""
    try:
        result = await _service.verify_evidence_by_hash(hash)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 分析报告 ---

@router.post("/api/compliance/analysis/report", tags=["合规合法智能监控模块"])
async def create_analysis_report(
    data: AnalysisReportRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """生成分析报告(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.create_analysis_report(
            analysis_period=data.analysisPeriod,
            effect_analysis=data.effectAnalysis,
            roi_evaluation=data.roiEvaluation,
            trend_prediction=data.trendPrediction,
            experience_retention=data.experienceRetention,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 持续优化 ---

@router.post("/api/compliance/optimization/update", tags=["合规合法智能监控模块"])
async def update_optimization(
    data: OptimizationRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """持续优化(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.update_optimization(
            optimization_type=data.optimizationType,
            rule_optimize=data.ruleOptimize,
            knowledge_update=data.knowledgeUpdate,
            experience_retention=data.experienceRetention,
            continuous_improve=data.continuousImprove,
            ai_automation_rate=data.aiAutomationRate,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# --- 统计 ---

@router.get("/api/compliance/stats", tags=["合规合法智能监控模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """合规统计(管理员)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_compliance_routes(app):
    """注册合规合法智能监控模块路由"""
    app.include_router(router)
