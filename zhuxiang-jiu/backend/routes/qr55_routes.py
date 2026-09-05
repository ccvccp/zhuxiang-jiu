"""55号·二维码AI智能管理路由(P0-P4)

端点(P0 4 + P1 3 + P2 4 + P3 3 + P4 4 = 18):
    GET  /api/qr55/registry          注册表自描述(admin, 观测面)
    POST /api/qr55/intent/parse      意图解析演示(admin, 观测面)
    POST /api/qr55/code/generate     签名码生成(admin, on, P0 能力验证)
    GET  /api/qr55/model/status      模型状态+就绪态(admin, 观测面)
    POST /api/qr55/generate          智能生码编排(admin+member, P1)
    POST /api/qr55/scan              扫码核销(member, P1)
    POST /api/qr55/clarify           澄清问句生成(member, P1)
    GET  /api/qr55/codes             码实例列表(admin, 观测面, P2)
    GET  /api/qr55/code/{codeId}     码实例详情+事件链(admin, 观测面, P2)
    GET  /api/qr55/stats             六指标快照(admin, 观测面, P2)
    POST /api/qr55/feedback/collect  决策回流补标(admin, 管理面, P2)
    POST /api/qr55/model/learn       学习轮次(admin, 管理面, P3)
    POST /api/qr55/model/promote     手动晋升(admin, 管理面, P3)
    POST /api/qr55/model/rollback    版本回滚(admin, 管理面, P3)
    GET  /api/qr55/governance/health 治理健康+冻结守卫(admin, P4)
    POST /api/qr55/probe             拨测验证(admin, P4)
    POST /api/qr55/probe/compensate  篡改受害者信值补偿(admin, P4)
    GET  /api/qr55/attribution       LLM 归因报告(admin, P4)

鉴权: 管理面 X-Role: admin(43-54号同款口径);
      会员面(member) generate/scan 携 memberId。
统一口径:
    - 观测面(registry/model/status/codes/code/stats/
      governance/attribution)不受 QR55_MODE 影响;
      intent/parse 为规则轨确定性演示亦开放
    - 生成面(generate): off=拒绝(409——存量二维码
      链路零影响)
    - 核销面(scan): off=拒绝(409)
    - 管理面(feedback/collect+model/*+probe*):
      off 亦可用(治理/拨测面不依赖生成面)
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
    """智能生码编排入参(P1+P4)"""
    memberId: int
    text: str
    audience: str | None = None
    confirmParams: dict | None = None
    accessibility: bool = False
    childMode: bool = False       # P4 儿童简化模式
    confirmed: bool = False       # P4 二次确认回传


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
    """模型状态+学习就绪态(champion/challenger/
    八因子+pending 门槛——44号 get_weights_view 复用;
    观测面)"""
    _require_admin(x_role)
    from services.qr55_service import Qr55Service
    result = await Qr55Service().model_status()
    # P3: 就绪态并入(pending 反馈数/门槛/ready)
    try:
        from services.qr55_learn_service import (
            Qr55LearnService,
        )
        readiness = await Qr55LearnService(
        ).learning_readiness()
        result["status"]["readiness"] = {
            k: readiness.get(k)
            for k in ("pendingFeedback", "minFeedback",
                      "ready", "championVersion",
                      "challengerVersion")}
    except Exception:  # noqa: BLE001
        pass   # 就绪态 fail-soft——状态本体不受影响
    return result


@router.post("/generate")
async def smart_generate(
        body: OrchestrateIn,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """智能生码编排(意图→评分→策略→生成——
    direct/confirm/clarify 三态分派+儿童模式二次
    确认; off 409)"""
    _require_admin(x_role)
    from services.qr55_generate_service import (
        Qr55GenerateService,
    )
    try:
        return await Qr55GenerateService().orchestrate(
            body.memberId, body.text,
            audience=body.audience,
            confirm_params=body.confirmParams,
            accessibility=body.accessibility,
            child_mode=body.childMode,
            confirmed=body.confirmed)
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


@router.post("/model/learn")
async def model_learn(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """学习轮次(44号 Hedge 复用——护栏 [0.5,2.0] 倍
    +归一化+冻结守卫内建; 门槛不足/冻结中 409)"""
    _require_admin(x_role)
    from services.qr55_learn_service import (
        Qr55LearnService,
    )
    try:
        return await Qr55LearnService().run_learning()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/model/promote")
async def model_promote(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """手动晋升挑战者(44号 promote_challenger 复用;
    无挑战者 409)"""
    _require_admin(x_role)
    from services.qr55_learn_service import (
        Qr55LearnService,
    )
    try:
        return await Qr55LearnService().promote()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/model/rollback")
async def model_rollback(
        versionId: str | None = None,
        reason: str = "",
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """版本回滚(指定 versionId; 缺省最新退役;
    无历史 409)"""
    _require_admin(x_role)
    from services.qr55_learn_service import (
        Qr55LearnService,
    )
    try:
        return await Qr55LearnService().rollback(
            version_id=versionId, reason=reason)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/governance/health")
async def governance_health(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """治理健康(46号三检测器只读+冻结守卫+55号域
    治理事实+动作建议——P4)"""
    _require_admin(x_role)
    from services.qr55_governance_service import (
        Qr55GovernanceService,
    )
    return await Qr55GovernanceService(
    ).governance_health()


@router.post("/probe")
async def run_probe(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """拨测验证(白名单 route 可达性+失败重试+
    probe 事件留痕——P4; 拨测失败不计预算铁律)"""
    _require_admin(x_role)
    from services.qr55_probe_service import (
        Qr55ProbeService,
    )
    return await Qr55ProbeService().run_probe()


@router.post("/probe/compensate")
async def probe_compensate(
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """篡改受害者信值补偿(tamper 事件关联会员→
    45号 L2 deposit 验真——幂等 1:1)"""
    _require_admin(x_role)
    from services.qr55_probe_service import (
        Qr55ProbeService,
    )
    return await Qr55ProbeService(
    ).compensate_tamper_victims(
        limit=max(1, min(int(limit or 50), 200)))


@router.get("/attribution")
async def attribution(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """LLM 归因报告(权重变更+回流信号+指标→
    自然语言——mock/real 三态, 数字来自数据层)"""
    _require_admin(x_role)
    from services.qr55_attribution_service import (
        Qr55AttributionService,
    )
    try:
        return await Qr55AttributionService(
        ).attribution()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


def register_qr55_routes(app) -> None:
    """注册55号路由(main.py startup 调用)"""
    app.include_router(router)
