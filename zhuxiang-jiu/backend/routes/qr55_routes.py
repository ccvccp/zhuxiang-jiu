"""55号·二维码AI智能管理路由(P0+P1+P2)

端点(P0 4 + P1 3 + P2 4 = 11):
    GET  /api/qr55/registry          注册表自描述(admin, 观测面)
    POST /api/qr55/intent/parse      意图解析演示(admin, 观测面)
    POST /api/qr55/code/generate     签名码生成(admin, on, P0 能力验证)
    GET  /api/qr55/model/status      模型状态(admin, 观测面)
    POST /api/qr55/generate          智能生码编排(admin+member, P1)
    POST /api/qr55/scan              扫码核销(member, P1)
    POST /api/qr55/clarify           澄清问句生成(member, P1)
    GET  /api/qr55/codes             码实例列表(admin, 观测面, P2)
    GET  /api/qr55/code/{codeId}     码实例详情+事件链(admin, 观测面, P2)
    GET  /api/qr55/stats             六指标快照(admin, 观测面, P2)
    POST /api/qr55/feedback/collect  决策回流补标(admin, 管理面, P2)

鉴权: 管理面 X-Role: admin(43-54号同款口径);
      会员面(member) generate/scan 携 memberId。
统一口径:
    - 观测面(registry/model/status/codes/code/stats)
      不受 QR55_MODE 影响; intent/parse 为规则轨
      确定性演示亦开放
    - 生成面(generate): off=拒绝(409——存量二维码
      链路零影响)
    - 核销面(scan): off=拒绝(409)
    - 管理面(feedback/collect): off 亦可用(回流
      采集不依赖生成面——54号 collect 同范式)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/qr55",
                   tags=["二维码AI智能管理(55号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")
    return x_role


class IntentParseIn(BaseModel):
    """意图解析入参"""
    text: str
    audience: str | None = None


class GenerateIn(BaseModel):
    """签名码生成入参(P0 能力验证)"""
    serviceId: str
    params: dict = {}
    memberId: int
    ttlSeconds: int = 300


class OrchestrateIn(BaseModel):
    """智能生码编排入参(P1)"""
    memberId: int
    text: str
    audience: str | None = None
    confirmParams: dict | None = None
    accessibility: bool = False


class ScanIn(BaseModel):
    """扫码核销入参(P1)"""
    code: str
    memberId: int | None = None
    continueOn: str = "mobile"
    accessibility: bool = False


class ClarifyIn(BaseModel):
    """澄清问句入参(P1)"""
    text: str
    memberId: int | None = None
    audience: str | None = None


@router.get("/registry")
async def get_registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """注册表自描述(12 项服务白名单+模板四类+
    敏感度口径——观测面不受开关影响)"""
    _require_admin(x_role)
    from services.qr55_service import Qr55Service
    return Qr55Service.registry()


@router.post("/intent/parse")
async def intent_parse(
        body: IntentParseIn,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """意图解析(规则轨三态 resolved/partial/clarify
    ——白名单映射零 LLM 依赖; 观测演示)"""
    _require_admin(x_role)
    from services.qr55_service import Qr55Service
    try:
        return Qr55Service().parse_intent(
            body.text, audience=body.audience)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/code/generate")
async def code_generate(
        body: GenerateIn,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """签名码生成(白名单校验+HMAC+exp+nonce
    ——P0 能力验证; off 态 409)"""
    _require_admin(x_role)
    from services.qr55_service import Qr55Service
    try:
        return await Qr55Service().generate_code(
            body.serviceId, body.params,
            body.memberId,
            ttl_seconds=body.ttlSeconds)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(champion/challenger/八因子
    ——44号 get_weights_view 复用; 观测面)"""
    _require_admin(x_role)
    from services.qr55_service import Qr55Service
    return await Qr55Service().model_status()


@router.post("/generate")
async def smart_generate(
        body: OrchestrateIn,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """智能生码编排(意图→评分→策略→生成——
    direct/confirm/clarify 三态分派; off 409)"""
    _require_admin(x_role)
    from services.qr55_generate_service import (
        Qr55GenerateService,
    )
    try:
        return await Qr55GenerateService().orchestrate(
            body.memberId, body.text,
            audience=body.audience,
            confirm_params=body.confirmParams,
            accessibility=body.accessibility)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/scan")
async def scan_code(
        body: ScanIn,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """扫码核销(验签→防重放→预算扣减→千面落地
    →状态翻转; 四态语义; off 409)"""
    _require_admin(x_role)
    from services.qr55_scan_service import (
        Qr55ScanService,
    )
    try:
        return await Qr55ScanService().scan(
            body.code, member_id=body.memberId,
            continue_on=body.continueOn,
            accessibility=body.accessibility)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/clarify")
async def clarify(
        body: ClarifyIn,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """澄清问句生成(规则轨解析+mock 确定性模板
    /LLM real 润色三态)"""
    _require_admin(x_role)
    from services.qr55_intent_service import (
        Qr55IntentService,
    )
    try:
        intent = Qr55IntentService().parse_intent(
            body.text, audience=body.audience)
        result = Qr55IntentService(
        ).generate_clarify(intent,
                           member_id=body.memberId)
        result.update({
            "intentStatus": intent.get("status"),
            "candidates": intent.get(
                "candidates") or [],
            "params": intent.get("params") or {},
        })
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/codes")
async def list_codes(
        status: str | None = None,
        memberId: int | None = None,
        limit: int = 100,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """码实例列表(状态/会员过滤——观测面,
    不受开关影响)"""
    _require_admin(x_role)
    from repositories.qr55_repository import (
        Qr55Repository,
    )
    limit = max(1, min(int(limit or 100), 500))
    codes = await Qr55Repository().list_codes(
        status=status, member_id=memberId,
        limit=limit)
    return {
        "success": True, "total": len(codes),
        "codes": codes,
        "note": "码实例列表(载荷脱敏——code 字段"
                "含签名, 观测面完整呈现)",
    }


@router.get("/code/{code_id}")
async def get_code_detail(
        code_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """码实例详情+事件链(全链追踪——生成→扫码→
    完成→信值结算观测)"""
    _require_admin(x_role)
    from repositories.qr55_repository import (
        Qr55Repository,
    )
    repo = Qr55Repository()
    code = await repo.get_code(int(code_id))
    if code is None:
        raise HTTPException(status_code=404,
                            detail=f"码 {code_id} 不存在")
    events = await repo.list_events(
        code_id=int(code_id))
    return {
        "success": True, "code": code,
        "events": events,
        "note": "码实例全链事件(generate/scan/"
                "complete/expire/tamper/replay/settle)",
    }


@router.get("/stats")
async def get_stats(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """六指标快照(意图满足率/渗透率/预算健康度/
    拦截有效率/满意度/澄清效率——观测面)"""
    _require_admin(x_role)
    from services.qr55_metrics_service import (
        Qr55MetricsService,
    )
    return await Qr55MetricsService().compute_snapshot()


@router.post("/feedback/collect")
async def feedback_collect(
        memberId: int | None = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """决策回流补标(七类信号真值标注+44号池双写+
    45号信值结算——幂等批量扫描; off 亦可用)"""
    _require_admin(x_role)
    from services.qr55_feedback_service import (
        Qr55FeedbackService,
    )
    try:
        return await Qr55FeedbackService(
        ).collect_feedback(member_id=memberId)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


def register_qr55_routes(app) -> None:
    """注册55号路由(main.py startup 调用)"""
    app.include_router(router)
