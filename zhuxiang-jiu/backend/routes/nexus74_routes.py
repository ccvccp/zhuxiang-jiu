"""74号·NexusFlow(智枢·流)路由
(nexus74_routes, P1——/api/nexus74)

规划(docs/74号_NexusFlow智枢流_AI智能全域
发布大模型_创新规划方案.md §六 P1):
    8 端点(规则中枢与合规引擎):
        GET  /rules              规则列表
        POST /rules              规则录入
        GET  /rules/{id}         规则详情
        POST /compliance/check   合规检测
        POST /compliance/scan    批量扫描
        GET  /compliance/dict    合规字典
        POST /warning/inject     警示语注入
        GET  /personas           人格档案

铁律(规划 §九):
    - 合规检测/规则库/人格档案=观测面
      常开(不受 MODE 影响)
    - 录入类操作 admin 门禁
    - 异常映射: KeyError→404,
      ValueError→409(71号口径)
"""

import logging

from fastapi import APIRouter, Header, \
    HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(
    "nexus74_routes")

router = APIRouter(
    prefix="/api/nexus74",
    tags=["74NexusFlow智枢流"])


def _require_admin(x_role):
    if x_role != "admin":
        raise HTTPException(
            status_code=401,
            detail="须 X-Role: admin")


def _map(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(
            status_code=404,
            detail=f"不存在: {exc.args[0]}")
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=409, detail=str(exc))
    return HTTPException(
        status_code=500,
        detail=f"内部错误: {exc}")


def _service():
    from services.nexus74_p1_service import (
        Nexus74P1Service,
    )
    return Nexus74P1Service()


# ============================================================
# 请求模型
# ============================================================

class RuleCreateRequest(BaseModel):
    platform: str = Field(
        "all_platforms",
        description="平台: all_platforms/"
                    "wechat_mp/douyin/"
                    "xiaohongshu/zhihu/"
                    "bilibili/toutiao")
    ruleType: str = Field(
        ..., description="规则类型: "
                         "prohibition/"
                         "conditional_"
                         "prohibition/"
                         "mandatory_"
                         "requirement/"
                         "recommendation")
    redline: str = Field(
        "", description="关联红线(可空): "
                        "R1_induce 等")
    content: str = Field(
        ..., max_length=500,
        description="规则内容")
    patterns: list = Field(
        default_factory=list,
        description="禁止模式词表")
    safeHarbor: list = Field(
        default_factory=list,
        description="安全港例外")
    legalBasis: str = Field(
        "", max_length=200,
        description="法规依据")
    confidence: float = Field(
        0.8, gt=0, le=1,
        description="置信度")


class ComplianceCheckRequest(BaseModel):
    text: str = Field(
        ..., max_length=10000,
        description="待检文本")
    platform: str = Field(
        "all_platforms",
        description="目标平台")
    hasWarning: bool = Field(
        False, description="发布包是否"
                          "已含警示语")
    contentType: str = Field(
        "article", max_length=30,
        description="内容形态")


class ComplianceScanRequest(BaseModel):
    texts: list = Field(
        ..., description="文本列表"
                        "(上限 50)")
    platform: str = Field(
        "all_platforms",
        description="目标平台")


class WarningInjectRequest(BaseModel):
    text: str = Field(
        ..., max_length=10000,
        description="待注入文本")


class SourceCreateRequest(BaseModel):
    title: str = Field(
        ..., max_length=200,
        description="源标题")
    body: str = Field(
        ..., max_length=20000,
        description="源正文")
    intent: str = Field(
        ..., description="意图: news/"
                         "tutorial/seeding/"
                         "opinion")
    keywords: list = Field(
        default_factory=list,
        description="关键词"
                    "(上限 10)")
    hasImage: bool = Field(
        False, description="含图素材")
    hasVideo: bool = Field(
        False, description="含视频素材")


class AdaptRequest(BaseModel):
    sourceId: int = Field(
        ..., gt=0,
        description="源内容 ID")
    platform: str = Field(
        ..., description="目标平台")
    personaState: str = Field(
        "", description="人设状态覆盖"
                       "(可空: professional/"
                       "observer/companion)")


class AdaptBatchRequest(BaseModel):
    sourceId: int = Field(
        ..., gt=0,
        description="源内容 ID")
    personaState: str = Field(
        "", description="人设状态覆盖")
    topN: int = Field(
        0, ge=0, le=6,
        description="Top-N(0=默认 3)")


class PublishRequest(BaseModel):
    sourceId: int = Field(
        ..., gt=0,
        description="源内容 ID")
    platform: str = Field(
        ..., description="目标平台")
    adaptationId: int = Field(
        0, ge=0,
        description="适配版本 ID"
                    "(0=取最新)")
    auto: bool = Field(
        False, description="自主发布"
                       "(仅 full 档 A 档)")
    now: str = Field(
        "", description="时刻(ISO 8601——"
                       "测试确定性)")


class ReceiptRequest(BaseModel):
    result: str = Field(
        ..., description="回执结果: "
                         "published/rejected/"
                         "throttled")
    message: str = Field(
        "", max_length=500,
        description="平台返回信息")
    externalId: str = Field(
        "", max_length=100,
        description="平台内容 ID")
    now: str = Field(
        "", description="时刻(测试确定性)")


class SilenceRequest(BaseModel):
    enabled: bool = Field(
        True, description="静默窗开关")
    hours: list = Field(
        ..., description="静默时段"
                        "(0-23 整数列表)")


class MetricsRequest(BaseModel):
    readCount: int = Field(
        0, ge=0, description="阅读数")
    likeCount: int = Field(
        0, ge=0, description="点赞数")
    commentCount: int = Field(
        0, ge=0, description="评论数")
    shareCount: int = Field(
        0, ge=0, description="分享数")


class AuditReflowRequest(BaseModel):
    publicationId: int = Field(
        ..., gt=0,
        description="发布记录 ID")
    result: str = Field(
        ..., description="审核结果: "
                         "passed/rejected/"
                         "throttled")
    message: str = Field(
        "", max_length=500,
        description="平台审核信息")


# ============================================================
# ① 合规规则库(观测面——常开)
# ============================================================

@router.get("/rules")
async def list_rules(
        platform: str = "",
        ruleType: str = "",
        x_role: str = Header(
            default=None, alias="X-Role")):
    """规则库列表(观测面——platform/
    ruleType 筛选)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .list_rules(
                    platform=platform,
                    rule_type=ruleType)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/rules")
async def create_rule(
        body: RuleCreateRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """规则录入(admin——Schema 对象化)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .create_rule(
                    body.model_dump())}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/rules/{rule_id}")
async def get_rule(
        rule_id: int,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """规则详情(观测面)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .get_rule(rule_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# ② 确定性合规引擎(观测面——常开)
# ============================================================

@router.post("/compliance/check")
async def compliance_check(
        body: ComplianceCheckRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """合规检测(四态输出——确定性
    引擎, LLM 禁入)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .compliance_check(
                    text=body.text,
                    platform=body.platform,
                    has_warning=body
                    .hasWarning,
                    content_type=body
                    .contentType)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/compliance/scan")
async def compliance_scan(
        body: ComplianceScanRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """批量合规扫描(源内容预检)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .compliance_scan(
                    texts=body.texts,
                    platform=body
                    .platform)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/compliance/dict")
async def compliance_dict(
        x_role: str = Header(
            default=None, alias="X-Role")):
    """合规字典公示(六红线/警示语/
    边界词/安全港)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": _service()
                .compliance_dict()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# ③ 警示语注入(R6 修复——确定性)
# ============================================================

@router.post("/warning/inject")
async def warning_inject(
        body: WarningInjectRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """警示语注入(footer 显著位置
    ——幂等)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .warning_inject(
                    text=body.text)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# ⑤ 平台人格档案(观测面——常开)
# ============================================================

@router.get("/personas")
async def list_personas(
        platform: str = "",
        x_role: str = Header(
            default=None, alias="X-Role")):
    """平台人格档案列表(六平台+状态机)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .list_personas(
                    platform=platform)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/persona/state")
async def persona_state(
        platform: str,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """人设状态机当前态(defaultState+
    override)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .persona_state(platform)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P2 ② 源内容登记(观测面——常开)
# ============================================================

@router.post("/sources")
async def create_source(
        body: SourceCreateRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """源内容登记(intent 感知分类)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p2_service import (
            Nexus74P2Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P2Service()
                .create_source(
                    body.model_dump())}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/sources/{source_id}")
async def get_source(
        source_id: int,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """源内容详情(观测面)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p2_service import (
            Nexus74P2Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P2Service()
                .get_source(source_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/sources")
async def list_sources(
        limit: int = 50,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """源内容列表(观测面)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p2_service import (
            Nexus74P2Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P2Service()
                .list_sources(
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P2 ③ 平台选择矩阵(观测面——常开)
# ============================================================

@router.get("/platform/matrix")
async def platform_matrix(
        intent: str = "",
        x_role: str = Header(
            default=None, alias="X-Role")):
    """平台选择矩阵(intent×平台
    适配分——Top-N)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p2_service import (
            Nexus74P2Service,
        )
        return {"code": 0,
                "data":
                    Nexus74P2Service()
                    .platform_matrix(
                        intent=intent)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P2 ④ 内容适配(决策面——MODE 门控)
# ============================================================

def _require_decision_plane():
    """适配面 MODE 门控(off=409——
    决策面关闭, 观测面不受影响)"""
    from services.nexus74_registry import (
        current_mode,
    )
    mode = current_mode()
    if mode == "off":
        raise HTTPException(
            status_code=409,
            detail="NEXUSFLOW74_MODE=off"
                  "(默认 off——适配决策面"
                  "关闭, 观测面不受影响)")
    return mode


@router.post("/adapt")
async def adapt(
        body: AdaptRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """单平台内容适配(标题/摘要/标签/
    话术包——合规前置+确定性模板)"""
    try:
        _require_admin(x_role)
        _require_decision_plane()
        from services.nexus74_p2_service import (
            Nexus74P2Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P2Service()
                .adapt(
                    source_id=body.sourceId,
                    platform=body.platform,
                    persona_state=body
                    .personaState)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/adapt/batch")
async def adapt_batch(
        body: AdaptBatchRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """多平台批量适配(矩阵驱动
    Top-N——适配分降序)"""
    try:
        _require_admin(x_role)
        _require_decision_plane()
        from services.nexus74_p2_service import (
            Nexus74P2Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P2Service()
                .adapt_batch(
                    source_id=body.sourceId,
                    persona_state=body
                    .personaState,
                    top_n=body.topN)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/adaptations")
async def list_adaptations(
        sourceId: int = 0,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """适配版本列表(观测面)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p2_service import (
            Nexus74P2Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P2Service()
                .list_adaptations(
                    source_id=sourceId
                    or None)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P3 ⑤ 发布编排(决策面——MODE 门控)
# ============================================================

@router.post("/publish")
async def publish(
        body: PublishRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """发布执行(A 档直连/B 档适配包
    ——shadow 留痕不派发)"""
    try:
        _require_admin(x_role)
        _require_decision_plane()
        from services.nexus74_p3_service import (
            Nexus74P3Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P3Service()
                .publish(
                    source_id=body.sourceId,
                    platform=body.platform,
                    adaptation_id=body
                    .adaptationId,
                    auto=body.auto,
                    now=body.now)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/publications")
async def list_publications(
        platform: str = "",
        status: str = "",
        limit: int = 100,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """发布记录列表(观测面——筛选)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p3_service import (
            Nexus74P3Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P3Service()
                .list_publications(
                    platform=platform,
                    status=status,
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/publications/{publication_id}")
async def get_publication(
        publication_id: int,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """发布详情(含 error 归因——观测面)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p3_service import (
            Nexus74P3Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P3Service()
                .get_publication(
                    publication_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/publications/{publication_id}"
             "/retry")
async def retry_publication(
        publication_id: int,
        now: str = "",
        x_role: str = Header(
            default=None, alias="X-Role")):
    """自愈重试(A 档 failed——指数退避/
    换档建议)"""
    try:
        _require_admin(x_role)
        _require_decision_plane()
        from services.nexus74_p3_service import (
            Nexus74P3Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P3Service()
                .retry(
                    publication_id=publication_id,
                    now=now)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/publications/{publication_id}"
             "/receipt")
async def register_receipt(
        publication_id: int,
        body: ReceiptRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """B 档人工回执登记(数据诚实——
    未登记视为未发布, 不受 MODE)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p3_service import (
            Nexus74P3Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P3Service()
                .receipt(
                    publication_id=publication_id,
                    result=body.result,
                    message=body.message,
                    external_id=body.externalId,
                    now=body.now)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/quota/status")
async def quota_status(
        now: str = "",
        x_role: str = Header(
            default=None, alias="X-Role")):
    """频次封顶/静默窗状态(观测面)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p3_service import (
            Nexus74P3Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P3Service()
                .quota_status(now=now)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/silence")
async def set_silence(
        body: SilenceRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """静默窗设置(北京时间夜间免打扰)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p3_service import (
            Nexus74P3Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P3Service()
                .set_silence(
                    enabled=body.enabled,
                    hours=body.hours)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/healthz")
async def adapter_healthz(
        x_role: str = Header(
            default=None, alias="X-Role")):
    """适配器健康(A 档连通性——
    诚实工程)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p3_service import (
            Nexus74P3Service,
        )
        return {"code": 0,
                "data":
                    Nexus74P3Service()
                    .healthz()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P4 数据回流与学习进化(观测面——不受 MODE)
# ============================================================
# 注: 固定路径(/metrics/audit, /metrics/
# summary)须先于参数路径(/metrics/{id})
# 注册——FastAPI 按注册序匹配

@router.post("/metrics/audit")
async def audit_reflow(
        body: AuditReflowRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """审核结果回流(驳回/限流→负样本
    学习+达线提 46号规则强化)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p4_service import (
            Nexus74P4Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P4Service()
                .audit_reflow(
                    publication_id=body
                    .publicationId,
                    result=body.result,
                    message=body.message)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/metrics/summary")
async def metrics_summary(
        x_role: str = Header(
            default=None, alias="X-Role")):
    """全域数据汇总(平台聚合+审核分布
    ——观测面常开)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p4_service import (
            Nexus74P4Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P4Service()
                .metrics_summary()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/learnings")
async def list_learnings(
        kind: str = "",
        limit: int = 100,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """学习留痕列表(形式学习/负样本/
    规则强化——观测面)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p4_service import (
            Nexus74P4Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P4Service()
                .learnings(
                    kind=kind,
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/metrics/{publication_id}")
async def register_metrics(
        publication_id: int,
        body: MetricsRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """指标回流登记(read/like/comment/
    share——触发形式学习)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p4_service import (
            Nexus74P4Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P4Service()
                .register_metrics(
                    publication_id=publication_id,
                    metrics=body
                    .model_dump())}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P5 元认知收官(观测/快环/保护面/
# 决策面——漂移/免疫/红队/进化日志)
# ============================================================

@router.post("/meta/drift")
async def drift_detect(
        day: str = "",
        x_role: str = Header(
            default=None, alias="X-Role")):
    """漂移检测(三信号——快环,
    不受 MODE 影响)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p5_service import (
            Nexus74P5Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P5Service()
                .drift_detect(day=day)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/immunity")
async def immunity_view(
        x_role: str = Header(
            default=None, alias="X-Role")):
    """免疫看板(观测面)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p5_service import (
            Nexus74P5Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P5Service()
                .immunity_view()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/immunity/monitor")
async def immunity_monitor(
        day: str = "",
        x_role: str = Header(
            default=None, alias="X-Role")):
    """分布监控+自动冻结(快环——
    信号数≥规则线自动冻结[保护方向])"""
    try:
        _require_admin(x_role)
        from services.nexus74_p5_service import (
            Nexus74P5Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P5Service()
                .monitor_immunity(
                    day=day)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/immunity/freeze")
async def immunity_freeze(
        x_role: str = Header(
            default=None, alias="X-Role")):
    """人工冻结(保护面——不受 MODE)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p5_service import (
            Nexus74P5Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P5Service()
                .freeze_manual(
                    by="admin")}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/immunity/unfreeze")
async def immunity_unfreeze(
        x_role: str = Header(
            default=None, alias="X-Role")):
    """解冻(人工专属+环境变量双保险
    ——NEXUSFLOW74_IMMUNITY=1)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p5_service import (
            Nexus74P5Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P5Service()
                .unfreeze(by="admin")}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/redteam")
async def redteam(
        x_role: str = Header(
            default=None, alias="X-Role")):
    """红队四向量执行(决策面 off 409
    ——构造→断言→留痕; 失守→冻结)"""
    try:
        _require_admin(x_role)
        from services.nexus74_registry import (
            current_mode,
        )
        if current_mode() == "off":
            raise HTTPException(
                status_code=409,
                detail="NEXUSFLOW74_MODE"
                      "=off(默认 off——决策面"
                      "关闭, 观测面不受影响)")
        from services.nexus74_p5_service import (
            Nexus74P5Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P5Service()
                .redteam()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/redteam/runs")
async def redteam_runs(
        limit: int = 50,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """红队批次历史(观测面)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p5_service import (
            Nexus74P5Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P5Service()
                .list_redteams(
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/evolution/log")
async def evolution_log(
        kind: str = "",
        limit: int = 100,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """进化日志(观测面——七类留痕)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p5_service import (
            Nexus74P5Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P5Service()
                .evolution_log(
                    kind=kind or None,
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# 模型状态(观测面——P5 完整版)
# ============================================================

@router.get("/model/status")
async def model_status(
        x_role: str = Header(
            default=None, alias="X-Role")):
    """模型状态(mode/kill/免疫/平台/
    红线/适配器分级——观测面)"""
    try:
        _require_admin(x_role)
        from services.nexus74_p5_service import (
            Nexus74P5Service,
        )
        return {"code": 0,
                "data": await
                Nexus74P5Service()
                .model_status()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


def register_nexus74_routes(app):
    """路由注册(main.py 调用)"""
    app.include_router(router)
