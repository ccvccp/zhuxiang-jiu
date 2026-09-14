"""72号·AI智能自动引流大模型路由(P1 感知跃迁 + P2 因果认知)

端点(P1 6 个——观测面常开, 不受 ATTRACT72_MODE
影响; 感知层为观测/快环口径):
    GET  /api/attract72/personas              画像列表(admin, 观测面)
    POST /api/attract72/personas/sync        感知面同步(画像生成+信号摄取)(admin, 观测面)
    GET  /api/attract72/personas/{id}        画像详情(admin, 观测面)
    POST /api/attract72/clicks/enrich        点击补意图快照(admin, 快环)
    GET  /api/attract72/signals              信号流列表(admin, 观测面)
    GET  /api/attract72/intent/{click_id}    意图快照查询(admin, 观测面)

端点(P2 7 个——因果推理为观测/快环; 结晶/发布
为决策面 off 409):
    POST /api/attract72/causal/run           反事实对照推理(admin, 观测/快环)
    GET  /api/attract72/causal/insights      因果洞察列表(admin, 观测面)
    POST /api/attract72/knowledge/crystallize  洞察→定律结晶(admin, 决策面 off 409)
    POST /api/attract72/knowledge/laws/{id}/publish  定律发布(admin, 决策面 off 409)
    GET  /api/attract72/knowledge/laws       定律台账(admin, 观测面)
    GET  /api/attract72/knowledge/anti       反知识清单(admin, 观测面)
    POST /api/attract72/knowledge/query      自然语言查询(admin, 观测面)

端点(P3 6 个——生成/建议/执行=决策面 off 409;
偏差重博弈=快环域内自动):
    POST /api/attract72/budget/forecast/generate  72h 预分配生成(admin, 决策面 off 409)
    GET  /api/attract72/budget/forecast      方案+偏差+历史(admin, 观测面)
    POST /api/attract72/budget/rebalance/auto 偏差重博弈(admin, 快环——不受 MODE)
    GET  /api/attract72/budget/exploration   探索基金状态(admin, 观测面)
    POST /api/attract72/budget/rates/propose 系数建议→46号(admin, 决策面 off 409)
    POST /api/attract72/budget/rates/apply    系数执行·显式动作(admin, 决策面 off 409)

端点(P6 10 个——元认知收官; 实验/红队/转段
=决策面 off 409; 健康度/日志/台账=观测面):
    POST /api/attract72/experiment/propose   沙箱实验提案→46号(admin, 决策面 off 409)
    GET  /api/attract72/experiments          实验台账(admin, 观测面)
    POST /api/attract72/experiment/{id}/conclude  实验结论·双向结晶(admin, 决策面 off 409)
    GET  /api/attract72/meta/health          健康度三指标(?refresh=1 触发快环检查)(admin, 观测面)
    POST /api/attract72/meta/unfreeze       健康度人工解冻(admin+环境变量双保险)
    GET  /api/attract72/evolution/log        进化日志(admin, 观测面)
    POST /api/attract72/redteam/run          红队四向量执行(admin, 决策面 off 409)
    GET  /api/attract72/redteam              红队台账(admin, 观测面)
    GET  /api/attract72/model/status         模型状态(admin, 观测面)
    POST /api/attract72/mode/{target}        转段四档(admin+confirm)

鉴权: 管理面 X-Role: admin(71号同款口径)。
统一口径(71号范式):
    - 感知层/因果推理/偏差重博弈全部为观测面/快环
      ——off 档常开(重博弈仅受 KILL 制动)
    - 决策面(结晶/发布/预分配生成/系数建议/执行)
      off=409; 观测面不受影响
    - KeyError → 404 / ValueError → 409
    - 同步请求可携带 today(YYYY-MM-DD,
      演示/测试口径——空则系统当前日期);
      预分配 anchor/偏差度量 now 同为演示口径
"""

from fastapi import (APIRouter, Header,
                     HTTPException, Query)
from pydantic import BaseModel, Field

from services.attract72_p1_service import (
    Attract72P1Service,
)

router = APIRouter(
    prefix="/api/attract72",
    tags=["AI智能自动引流大模型(72号)"],
)

_service = Attract72P1Service()


def _require_admin(x_role: str | None) -> None:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


def _map(exc: Exception) -> HTTPException:
    """统一异常映射(71号口径)"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        return HTTPException(status_code=404,
                             detail=msg)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409,
                             detail=str(exc))
    return HTTPException(status_code=500,
                         detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class PersonaSyncRequest(BaseModel):
    today: str = Field(
        "", max_length=10,
        description="同步基准日(YYYY-MM-DD, 空=今天——演示口径)")


class ClickEnrichRequest(BaseModel):
    clickId: int = Field(..., gt=0,
                         description="attract v1.0 点击 ID")
    deviceFingerprint: str = Field(
        "", max_length=64,
        description="设备指纹(空则 UA 哈希脱敏)")
    userAgent: str = Field(
        "", max_length=300,
        description="User-Agent(指纹兜底源)")
    dwellSeconds: float = Field(
        0.0, ge=0, le=86400,
        description="落地页停留秒数")
    text: str = Field(
        "", max_length=500,
        description="行为文本素材(评论/搜索词, 可选)")


class CrystallizeRequest(BaseModel):
    insightId: int = Field(..., gt=0,
                           description="洞察 ID(须 verified)")


class KnowledgeQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=200,
                          description="自然语言问题(确定性关键词路由)")


class ForecastGenerateRequest(BaseModel):
    anchor: str = Field(
        "", max_length=30,
        description="窗口锚点(ISO 8601, 空=当前时刻——演示/测试口径)")


class RebalanceRequest(BaseModel):
    now: str = Field(
        "", max_length=30,
        description="偏差度量时刻(ISO 8601, 空=当前——演示/测试口径)")


# ============================================================
# ① 渠道人格画像(观测面)
# ============================================================

@router.get("/personas")
async def list_personas(
        subjectType: str = "",
        personaType: str = "",
        limit: int = 100,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """画像列表(按主体/人格筛选)"""
    try:
        _require_admin(x_role)
        return {
            "code": 0,
            "data": await _service.list_personas(
                subject_type=subjectType or None,
                persona_type=personaType or None,
                limit=limit),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/personas/sync")
async def sync_personas(
        req: PersonaSyncRequest = None,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """感知面同步(博主/会员画像生成+信号总线摄取)"""
    try:
        _require_admin(x_role)
        body = req or PersonaSyncRequest()
        return {"code": 0,
                "data": await _service.sync_personas(
                    today=body.today)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/personas/{persona_id}")
async def get_persona(
        persona_id: int,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """画像详情(含 stats/history)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service.get_persona(
                    persona_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# ② 意图快照(观测面/快环)
# ============================================================

@router.post("/clicks/enrich")
async def enrich_click(
        req: ClickEnrichRequest,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """点击补意图快照(词表密度确定性解析)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service
                .enrich_click_intent(
                    click_id=req.clickId,
                    device_fingerprint=(
                        req.deviceFingerprint),
                    user_agent=req.userAgent,
                    dwell_seconds=req.dwellSeconds,
                    text=req.text)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/intent/{click_id}")
async def get_intent(
        click_id: int,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """意图快照查询"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service.get_intent(
                    click_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# ③ 外部信号总线(观测面)
# ============================================================

@router.get("/signals")
async def list_signals(
        type: str = "",
        limit: int = 100,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """信号流列表(节日/雷达事件)"""
    try:
        _require_admin(x_role)
        return {
            "code": 0,
            "data": await _service.list_signals(
                signal_type=type or None,
                limit=limit),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P2 ④ 因果推理引擎(观测面/快环——不受 MODE 影响)
# ============================================================

@router.post("/causal/run")
async def run_causal(
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """反事实对照推理(要素×出现/不出现分组
    ——确定性公式全留痕)"""
    try:
        _require_admin(x_role)
        from services.attract72_p2_service import (
            Attract72P2Service,
        )
        return {"code": 0,
                "data": await
                Attract72P2Service().run_causal()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/causal/insights")
async def list_causal_insights(
        dimension: str = "",
        effectType: str = "",
        status: str = "",
        limit: int = 100,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """因果洞察列表(驱动/流失/中性)"""
    try:
        _require_admin(x_role)
        from services.attract72_p2_service import (
            Attract72P2Service,
        )
        return {"code": 0,
                "data": await
                Attract72P2Service()
                .list_insights(
                    dimension=dimension or None,
                    effect_type=effectType or None,
                    status=status or None,
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P2 ⑤ 定律结晶与知识(结晶/发布=决策面 off 409)
# ============================================================

def _require_decision_plane() -> None:
    """决策面开关(71号范式——off=409)"""
    from services.attract72_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="ATTRACT72_MODE=off(默认 off"
                  "——决策面关闭, 观测面不受"
                  "影响)")


@router.post("/knowledge/crystallize")
async def crystallize(
        body: CrystallizeRequest,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """洞察 → 定律结晶(46号建议书纯调用;
    driver→law / loss→anti)"""
    try:
        _require_admin(x_role)
        _require_decision_plane()
        from services.attract72_p2_service import (
            Attract72P2Service,
        )
        return {"code": 0,
                "data": await
                Attract72P2Service().crystallize(
                    body.insightId)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/knowledge/laws/{law_id}/publish")
async def publish_law(
        law_id: int,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """定律发布(46号 approved 前置的显式
    动作; 57号知识库同步 best-effort)"""
    try:
        _require_admin(x_role)
        _require_decision_plane()
        from services.attract72_p2_service import (
            Attract72P2Service,
        )
        return {"code": 0,
                "data": await
                Attract72P2Service().publish_law(
                    law_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/knowledge/laws")
async def list_laws(
        kind: str = "",
        status: str = "",
        limit: int = 100,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """定律台账(观测面)"""
    try:
        _require_admin(x_role)
        from services.attract72_p2_service import (
            Attract72P2Service,
        )
        return {"code": 0,
                "data": await
                Attract72P2Service().list_laws(
                    kind=kind or None,
                    status=status or None,
                    limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/knowledge/anti")
async def list_anti_laws(
        limit: int = 100,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """反知识清单(anti 定律——验证无效的
    要素组合结晶, 防重复试错)"""
    try:
        _require_admin(x_role)
        from services.attract72_p2_service import (
            Attract72P2Service,
        )
        return {"code": 0,
                "data": await
                Attract72P2Service().list_laws(
                    kind="anti", limit=limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/knowledge/query")
async def knowledge_query(
        body: KnowledgeQueryRequest,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """自然语言查询(确定性关键词路由——
    数字 100% 查询层插值)"""
    try:
        _require_admin(x_role)
        from services.attract72_p2_service import (
            Attract72P2Service,
        )
        return {"code": 0,
                "data": await
                Attract72P2Service().nl_query(
                    body.question)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P3 ⑥ 预算预判(生成/建议/执行=决策面 off 409;
# 偏差重博弈=快环域内自动; 状态/基金=观测面)
# ============================================================

@router.post("/budget/forecast/generate")
async def generate_forecast(
        body: ForecastGenerateRequest = None,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """生成 72h 预分配(历史分布×雷达提升×
    定律助推+探索基金——确定性公式)"""
    try:
        _require_admin(x_role)
        _require_decision_plane()
        from services.attract72_p3_service import (
            Attract72P3Service,
        )
        req = body or ForecastGenerateRequest()
        return {"code": 0,
                "data": await
                Attract72P3Service()
                .generate_forecast(
                    anchor=req.anchor)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/budget/forecast")
async def forecast_status(
        now: str = "",
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """当前方案+偏差+历史(观测面)"""
    try:
        _require_admin(x_role)
        from services.attract72_p3_service import (
            Attract72P3Service,
        )
        return {"code": 0,
                "data": await
                Attract72P3Service()
                .forecast_status(now=now)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/budget/rebalance/auto")
async def rebalance_auto(
        body: RebalanceRequest = None,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """偏差重博弈(>15% 域内自动——快环,
    不受 MODE 影响; 系数变更永不自动)"""
    try:
        _require_admin(x_role)
        from services.attract72_p3_service import (
            Attract72P3Service,
        )
        req = body or RebalanceRequest()
        return {"code": 0,
                "data": await
                Attract72P3Service()
                .rebalance_auto(now=req.now)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/budget/exploration")
async def exploration_status(
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """探索基金状态(5%-15% 浮动+候选渠道)"""
    try:
        _require_admin(x_role)
        from services.attract72_p3_service import (
            Attract72P3Service,
        )
        return {"code": 0,
                "data": await
                Attract72P3Service()
                .exploration_status()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/budget/rates/propose")
async def propose_rates(
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """系数建议 → 46号建议书(纯调用——
    奖励系数变更永不自动铁律)"""
    try:
        _require_admin(x_role)
        _require_decision_plane()
        from services.attract72_p3_service import (
            Attract72P3Service,
        )
        return {"code": 0,
                "data": await
                Attract72P3Service()
                .propose_rates()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/budget/rates/apply")
async def apply_rates(
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """系数执行(46号留痕后的 admin 显式
    动作——v1.0 currentRate 双轨变更)"""
    try:
        _require_admin(x_role)
        _require_decision_plane()
        from services.attract72_p3_service import (
            Attract72P3Service,
        )
        return {"code": 0,
                "data": await
                Attract72P3Service().apply_rates()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


def register_attract72_routes(app) -> None:
    """路由注册(main.py 挂载)"""
    app.include_router(router)


# ============================================================
# P4 短链记忆(观测/快环——不受 MODE 门控)
# ============================================================

class LandingDecideRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64,
                      description="短链码(v1.0 兼容)")
    fingerprint: str = Field("", max_length=64,
                             description="设备指纹(空则 ctx)")
    ctx: str = Field("", max_length=512,
                     description="base64 上下文(可选)")


class VisitRecordRequest(BaseModel):
    fingerprint: str = Field(..., min_length=8, max_length=64,
                             description="设备指纹")
    dwellSeconds: float = Field(0.0, ge=0, le=86400,
                                description="停留秒数")
    converted: bool = Field(False, description="是否转化")
    registered: bool = Field(False, description="是否注册(归并)")


@router.post("/landing/dynamic")
async def landing_dynamic(
        body: LandingDecideRequest,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """动态落地页决策(观测面——变体查表,
    反作弊隔离态降级 default)"""
    try:
        _require_admin(x_role)
        from services.attract72_p4_service import (
            Attract72P4Service,
        )
        return {"code": 0,
                "data": await
                Attract72P4Service()
                .decide_landing(code=body.code,
                                fingerprint=body.fingerprint,
                                ctx=body.ctx)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/memory/visit")
async def memory_visit(
        body: VisitRecordRequest,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """跨会话记忆累积(快环——指纹 upsert,
    频次超线自动隔离)"""
    try:
        _require_admin(x_role)
        from services.attract72_p4_service import (
            Attract72P4Service,
        )
        return {"code": 0,
                "data": await
                Attract72P4Service()
                .record_visit(fingerprint=body.fingerprint,
                              dwell_seconds=body.dwellSeconds,
                              converted=body.converted,
                              registered=body.registered)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/memory/{fingerprint}")
async def memory_query(
        fingerprint: str,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """跨会话画像查询(观测面)"""
    try:
        _require_admin(x_role)
        from services.attract72_p4_service import (
            Attract72P4Service,
        )
        return {"code": 0,
                "data": await
                Attract72P4Service()
                .get_memory(fingerprint)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/memory/{fingerprint}/release")
async def memory_release(
        fingerprint: str,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """反作弊人工解冻(admin——解冻人工专属)"""
    try:
        _require_admin(x_role)
        from services.attract72_p4_service import (
            Attract72P4Service,
        )
        return {"code": 0,
                "data": await
                Attract72P4Service()
                .release_fingerprint(fingerprint)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/landing/variants")
async def landing_variants(
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """变体分布台账(观测面)"""
    try:
        _require_admin(x_role)
        from services.attract72_p4_service import (
            Attract72P4Service,
        )
        return {"code": 0,
                "data": await
                Attract72P4Service()
                .list_variants()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P5 热点卡位(机会=观测面; 决策=决策面 off 409)
# ============================================================

class HotspotDecideRequest(BaseModel):
    radarEventId: int = Field(..., gt=0,
                              description="雷达事件 ID")


class HotspotOutcomeRequest(BaseModel):
    impressions: int = Field(0, ge=0,
                             description="实际曝光")
    conversions: int = Field(0, ge=0,
                             description="实际转化")
    actualRoi: float = Field(0.0, ge=0,
                             description="实际 ROI")


@router.get("/hotspot/opportunities")
async def hotspot_opportunities(
        limit: int = Query(20, ge=1, le=100),
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """热点机会清单(观测面——雷达×四维势能)"""
    try:
        _require_admin(x_role)
        from services.attract72_p5_service import (
            Attract72P5Service,
        )
        return {"code": 0,
                "data": await
                Attract72P5Service()
                .list_opportunities(limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/hotspot/decide")
async def hotspot_decide(
        body: HotspotDecideRequest,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """卡位决策(决策面——shadow 落档/assist
    方案+人工确认; 高风险自动拒追)"""
    try:
        _require_admin(x_role)
        from services.attract72_p5_service import (
            Attract72P5Service,
        )
        return {"code": 0,
                "data": await
                Attract72P5Service()
                .decide(body.radarEventId)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/hotspot/decisions")
async def hotspot_decisions(
        verdict: str = Query(None),
        status: str = Query(None),
        limit: int = Query(100, ge=1, le=500),
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """卡位决策台账(观测面)"""
    try:
        _require_admin(x_role)
        from services.attract72_p5_service import (
            Attract72P5Service,
        )
        return {"code": 0,
                "data": await
                Attract72P5Service()
                .list_decisions(verdict, status, limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/hotspot/{decision_id}/confirm")
async def hotspot_confirm(
        decision_id: int,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """卡位人工确认(assist 档——shadow 期拒绝)"""
    try:
        _require_admin(x_role)
        from services.attract72_p5_service import (
            Attract72P5Service,
        )
        return {"code": 0,
                "data": await
                Attract72P5Service()
                .confirm_decision(decision_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/hotspot/{decision_id}/execute")
async def hotspot_execute(
        decision_id: int,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """卡位执行(方案注入 40号创作情境参考——
    永不直接操作账号铁律)"""
    try:
        _require_admin(x_role)
        from services.attract72_p5_service import (
            Attract72P5Service,
        )
        return {"code": 0,
                "data": await
                Attract72P5Service()
                .execute_decision(decision_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/hotspot/{decision_id}/outcome")
async def hotspot_outcome(
        decision_id: int,
        body: HotspotOutcomeRequest,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """结果回流(预期 vs 实际闭环——观测面)"""
    try:
        _require_admin(x_role)
        from services.attract72_p5_service import (
            Attract72P5Service,
        )
        return {"code": 0,
                "data": await
                Attract72P5Service()
                .record_outcome(decision_id,
                                body.impressions,
                                body.conversions,
                                body.actualRoi)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# P6 元认知与治理(健康度+红队+沙箱实验
# +进化日志+转段)
# ============================================================

class ExperimentProposeRequest(BaseModel):
    hypothesis: str = Field(
        ..., min_length=1, max_length=500,
        description="实验假设(元认知盲区)")
    variable: str = Field(
        ..., max_length=64,
        description="实验变量(L1 白名单域)")
    channels: list[str] = Field(
        None, description="沙箱渠道(空=全部"
                          "非核心渠道)")
    budget: float = Field(
        0.0, ge=0, le=10000,
        description="实验预算(≤50 元)")
    sampleSize: int = Field(
        0, ge=0, description="灰度样本量(≥100)")
    successCriteria: str = Field(
        ..., min_length=1, max_length=200,
        description="成功标准")
    proposedBy: str = Field(
        "ai", description="提案方(ai/human)")


class ExperimentConcludeRequest(BaseModel):
    outcome: str = Field(
        ..., max_length=20,
        description="结论(success/failure/"
                    "inconclusive)")
    note: str = Field("", max_length=200,
                      description="结论备注")


@router.post("/experiment/propose")
async def experiment_propose(
        body: ExperimentProposeRequest,
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """沙箱实验提案(决策面 off 409; L1
    白名单外变量拒绝; 46号建议书留痕)"""
    try:
        _require_admin(x_role)
        from services.attract72_p6_service import (
            Attract72P6Service,
        )
        return {"code": 0,
                "data": await
                Attract72P6Service()
                .propose_experiment(
                    hypothesis=body.hypothesis,
                    variable=body.variable,
                    channels=body.channels,
                    budget=body.budget,
                    sample_size=body.sampleSize,
                    success_criteria=
                    body.successCriteria,
                    proposed_by=body.proposedBy)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/experiments")
async def experiments_list(
        status: str = Query(None),
        limit: int = Query(100, ge=1, le=500),
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """实验台账(观测面)"""
    try:
        _require_admin(x_role)
        from services.attract72_p6_service import (
            Attract72P6Service,
        )
        return {"code": 0,
                "data": await
                Attract72P6Service()
                .list_experiments(status, limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/experiment/{experiment_id}/conclude")
async def experiment_conclude(
        experiment_id: int,
        body: ExperimentConcludeRequest,
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """实验结论·双向结晶(决策面 off 409;
    success→知识/failure→反知识)"""
    try:
        _require_admin(x_role)
        from services.attract72_p6_service import (
            Attract72P6Service,
        )
        return {"code": 0,
                "data": await
                Attract72P6Service()
                .conclude_experiment(
                    experiment_id,
                    body.outcome,
                    body.note)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/meta/health")
async def meta_health(
        refresh: int = Query(
            0, ge=0, le=1,
            description="1=触发快环检查"
                        "(默认读最近)"),
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """健康度三指标(观测面; refresh=1
    触发检查——任一越界自动冻结)"""
    try:
        _require_admin(x_role)
        from services.attract72_p6_service import (
            Attract72P6Service,
        )
        svc = Attract72P6Service()
        if refresh:
            data = await svc.check_health()
        else:
            data = await svc.repo \
                .latest_health()
            if data is None:
                data = await svc.check_health()
        return {"code": 0, "data": data}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/meta/unfreeze")
async def meta_unfreeze(
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """健康度人工解冻(admin + 环境变量
    ATTRACT72_IMMUNITY=1 双保险——免疫
    自动永不解冻铁律)"""
    try:
        _require_admin(x_role)
        from services.attract72_p6_service import (
            Attract72P6Service,
        )
        return {"code": 0,
                "data": await
                Attract72P6Service()
                .unfreeze()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/evolution/log")
async def evolution_log(
        limit: int = Query(50, ge=1, le=200),
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """进化日志(观测面——定律/实验/卡位/
    预算/红队/健康度聚合)"""
    try:
        _require_admin(x_role)
        from services.attract72_p6_service import (
            Attract72P6Service,
        )
        return {"code": 0,
                "data": await
                Attract72P6Service()
                .evolution_log(limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/redteam/run")
async def redteam_run(
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """红队四向量执行(决策面 off 409——
    刷量注入/归因投毒/博弈操纵/人格漂移;
    未防御自动冻结)"""
    try:
        _require_admin(x_role)
        from services.attract72_p6_service import (
            Attract72P6Service,
        )
        return {"code": 0,
                "data": await
                Attract72P6Service()
                .run_redteam()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/redteam")
async def redteam_list(
        limit: int = Query(20, ge=1, le=100),
        x_role: str = Header(default=None,
                             alias="X-Role")):
    """红队台账(观测面)"""
    try:
        _require_admin(x_role)
        from services.attract72_p6_service import (
            Attract72P6Service,
        )
        return {"code": 0,
                "data": await
                Attract72P6Service()
                .list_redteam_runs(limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/model/status")
async def model_status(
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """模型状态(mode/健康/红队/L1 白名单
    公示——观测面)"""
    try:
        _require_admin(x_role)
        from services.attract72_p6_service import (
            Attract72P6Service,
        )
        return {"code": 0,
                "data": await
                Attract72P6Service()
                .model_status()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/mode/{target}")
async def mode_transfer(
        target: str,
        confirm: bool = Query(
            False,
            description="二次确认(必填 true)"),
        x_role: str = Header(default=None,
                              alias="X-Role")):
    """转段四档(off/shadow/assist/full;
    admin+confirm——升档逐档+健康度门控
    +assist→full 须红队全防御)"""
    try:
        _require_admin(x_role)
        from services.attract72_p6_service import (
            Attract72P6Service,
        )
        return {"code": 0,
                "data": await
                Attract72P6Service()
                .transfer(target, confirm)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc
