"""71号·AI智能支付端口大模型路由(P0-P2)

端点(P0 8 + P1 8 + P2 8):
    GET  /api/pay71/ports               端口池字典公示(admin, 观测面)
    GET  /api/pay71/ports/{id}          单端口详情(admin, 观测面)
    GET  /api/pay71/panorama            健康度全景(admin, 观测面——聚合 69号 P0 只读)
    POST /api/pay71/ports/{id}/state     端口态人工登记(admin, 人工——不受开关影响)
    POST /api/pay71/signals/report      前兆信号上报(admin, 快环观测——不受开关影响)
    GET  /api/pay71/signals             前兆信号留痕视图(admin, 观测面)
    GET  /api/pay71/events              全链事件视图(admin, 观测面)
    GET  /api/pay71/model/status        模型状态(admin, 观测面)
    GET  /api/pay71/selfheal/dict       自愈编排字典公示(admin, 观测面——P1)
    POST /api/pay71/selfheal/orchestrate 保护方向自愈编排(admin, 保护面——不受开关——P1)
    POST /api/pay71/selfheal/probe      半开探针(admin, 保护面——不受开关——P1)
    POST /api/pay71/selfheal/onboard    端口纳管(影子冷启动)(admin, 人工面——不受开关——P1)
    GET  /api/pay71/selfheal/shadows    影子冷启动视图(admin, 观测面——P1)
    POST /api/pay71/selfheal/propose    转正/摘牌建议书发起(admin, 人工面——P1)
    POST /api/pay71/selfheal/proposals/{seq}/decide 建议书终审(admin, 人工——不受开关——P1)
    GET  /api/pay71/selfheal/traces     自愈轨迹视图(admin, 观测面——P1)
    GET  /api/pay71/predict/dict        预判字典公示(admin, 观测面——P2)
    POST /api/pay71/predict/compute     意图预判+预载建议(admin, 决策面 off 409——P2)
    POST /api/pay71/predict/{seq}/revoke 预载撤销(admin, 决策面 off 409——P2)
    GET  /api/pay71/predict/records     预判留痕视图(admin, 观测面——P2)
    POST /api/pay71/predict/split/propose 大额拆分建议书(admin, 决策面 off 409——P2)
    POST /api/pay71/predict/split/{seq}/confirm 拆分确认(令牌单次)(admin, 决策面 off 409——P2)
    GET  /api/pay71/predict/splits      拆分建议书视图(admin, 观测面——P2)
    POST /api/pay71/predict/retry/report 端口失败重试上报(admin, 快环——不受开关——P2)
    GET  /api/pay71/predict/retries     重试统计视图(admin, 观测面——P2)
    GET  /api/pay71/allocation/dict     调配字典公示(admin, 观测面——P3)
    POST /api/pay71/allocation/compute  帕累托调配计算(admin, 决策面 off 409——P3)
    GET  /api/pay71/allocation/records  调配留痕视图(admin, 观测面——P3)
    GET  /api/pay71/allocation/weights  激活权重视图(出厂+覆盖——P3)
    POST /api/pay71/allocation/external/report 外部信号登记+建议书(admin, 快环摄取——不受开关——P3)
    POST /api/pay71/allocation/external/{seq}/decide 外部信号终审(admin, 人工——不受开关——P3)
    GET  /api/pay71/allocation/external 外部信号建议书视图(admin, 观测面——P3)
    GET  /api/pay71/recon/dict          对账自愈字典公示(admin, 观测面——P4)
    POST /api/pay71/recon/verify        T+0 三向核验(admin, 决策面 off 409——P4)
    GET  /api/pay71/recon/verifies      核验留痕视图(admin, 观测面——P4)
    POST /api/pay71/recon/heal          差错处置入口(幂等补单/转人工)(admin, 决策面 off 409——P4)
    POST /api/pay71/recon/retry         补单重试结果上报(admin, 决策面 off 409——P4)
    GET  /api/pay71/recon/records       补单账本视图(admin, 观测面——P4)
    GET  /api/pay71/recon/baseline      渠道对账延迟差错基线(admin, 观测面——P4)
    GET  /api/pay71/narrative/dict      风控叙事字典公示(admin, 观测面——P5)
    POST /api/pay71/narrative/generate  风控叙事生成(admin, 决策面 off 409——P5)
    GET  /api/pay71/narrative/records    叙事留痕视图(admin, 观测面——P5)
    POST /api/pay71/narrative/incident/report 欺诈案例归档(admin, 快环摄取——不受开关——P5)
    POST /api/pay71/narrative/incident/{seq}/verify 案例核实(admin, 人工——不受开关——P5)
    POST /api/pay71/narrative/misjudge/report 误拦归因回流(admin, 快环摄取——不受开关——P5)
    GET  /api/pay71/narrative/library   案例库+误拦统计视图(admin, 观测面——P5)

鉴权: 管理面 X-Role: admin(69号同款口径)。
统一口径(69号范式):
    - 观测面不受 PAY71_MODE 影响
    - 快环观测上报(signals/retry report/
      external report/incident report/
      misjudge report)不受开关影响
    - 人工面(port state/onboard/propose/
      decide/external decide/incident
      verify)不受开关影响
    - 保护面(orchestrate/probe)不受开关
      影响——保护方向永续铁律(规划 §4.1)
    - 决策面(predict compute/revoke/
      split propose/confirm/allocation
      compute/recon verify/heal/retry/
      narrative generate)off=409
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/pay71",
                   tags=["AI智能支付端口大模型(71号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


def _map(exc: Exception) -> HTTPException:
    """统一异常映射(69号口径)"""
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

class PortStateBody(BaseModel):
    portState: str = Field(description="端口态"
                           "(healthy/degraded/broken)")


class SignalReportBody(BaseModel):
    portId: str = Field(description="端口 ID")
    signal: str = Field(description="前兆信号"
                        "(latency_rising/errorcode_"
                        "climb/success_decline/"
                        "callback_delay)")
    latencyDelta: float = Field(
        default=0, description="延迟增量(ms)")
    errorRateDelta: float = Field(
        default=0, description="错误率增量")
    callbackMs: float = Field(
        default=0, description="回调耗时(ms)")


class OrchestrateBody(BaseModel):
    portId: str = Field(
        default="",
        description="指定端口 ID(空=全端口评估)")


class ProbeBody(BaseModel):
    portId: str = Field(description="端口 ID")
    success: bool = Field(description="探针结果")


class OnboardBody(BaseModel):
    portId: str = Field(description="端口 ID")


class ProposeBody(BaseModel):
    portId: str = Field(description="端口 ID")
    kind: str = Field(description="建议书种类"
                      "(promote 转正/offboard 摘牌)")


class DecideBody(BaseModel):
    approve: bool = Field(description="批准/驳回")


class PredictComputeBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    amount: float = Field(gt=0, description="金额(元)")
    trustTier: str = Field(
        default="", description="信值档(45号)")
    newDevice: bool = Field(
        default=False, description="陌生设备")
    oddHour: bool = Field(
        default=False, description="凌晨时段")
    newLocation: bool = Field(
        default=False, description="异常地点")
    tvEligible: bool = Field(
        default=False,
        description="credit_tv 信值资格"
                    "(69号 P1 资格门范式)")


class SplitProposeBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    totalAmount: float = Field(
        gt=0, description="总金额(元)")
    tvEligible: bool = Field(
        default=False,
        description="credit_tv 信值资格")


class SplitConfirmBody(BaseModel):
    confirmToken: str = Field(
        description="确认令牌(单次消费)")
    approve: bool = Field(
        default=True, description="确认/拒绝")


class RetryReportBody(BaseModel):
    portId: str = Field(description="端口 ID")
    failCount: int = Field(
        default=1, ge=1, description="失败次数")


class AllocationComputeBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    amount: float = Field(gt=0, description="金额(元)")
    context: str = Field(
        default="balanced",
        description="情境档(price_sensitive/"
                    "high_value/large_amount/"
                    "balanced)")
    tvEligible: bool = Field(
        default=False,
        description="credit_tv 信值资格")


class ExternalReportBody(BaseModel):
    kind: str = Field(description="信号种类"
                      "(fee_change/fx_fluctuation"
                      "/policy_change)")
    note: str = Field(
        default="", description="信号备注")
    affectedPorts: list[str] = Field(
        default_factory=list,
        description="受影响端口(观测留痕)")


class ExternalDecideBody(BaseModel):
    approve: bool = Field(description="批准/驳回")


class ReconVerifyBody(BaseModel):
    orderId: str = Field(description="订单号")
    orderAmount: float = Field(
        gt=0, description="订单金额(元)")
    flowAmount: float = Field(
        gt=0, description="流水金额(元)")
    receiptAmount: float = Field(
        gt=0, description="回执金额(元)")
    portId: str = Field(
        default="", description="端口 ID")
    flowAt: str = Field(
        default="", description="流水时间"
                              "(ISO——空=不校验)")
    receiptAt: str = Field(
        default="", description="回执时间"
                              "(ISO——空=不校验)")


class ReconHealBody(BaseModel):
    verifySeq: int = Field(
        description="核验留痕序号")


class ReconRetryBody(BaseModel):
    reconSeq: int = Field(
        description="补单记录序号")
    success: bool = Field(
        description="重试结果(成功/失败)")


class NarrativeGenerateBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    amount: float = Field(gt=0, description="金额(元)")
    trustTier: str = Field(
        default="", description="信值档(45号)")
    channelId: str = Field(
        default="", description="通道 ID")
    newDevice: bool = Field(
        default=False, description="陌生设备")
    oddHour: bool = Field(
        default=False, description="凌晨时段")
    newLocation: bool = Field(
        default=False, description="异常地点")


class IncidentReportBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    pattern: str = Field(description="欺诈手法"
                         "(credential_theft/"
                         "account_takeover/"
                         "collusive_cashout/"
                         "stolen_device)")
    summary: str = Field(
        default="", description="案例摘要")
    entropyAxes: dict = Field(
        default_factory=dict,
        description="熵轴指纹(查询层原值)")
    deviceClues: dict = Field(
        default_factory=dict,
        description="设备线索(脱敏后)")


class IncidentVerifyBody(BaseModel):
    confirmedFraud: bool = Field(
        description="核实结果(欺诈/合法)")


class MisjudgeReportBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    kind: str = Field(description="归因类型"
                      "(context_blind/"
                      "baseline_stale/"
                      "threshold_high)")
    narrativeSeq: int = Field(
        default=0, description="关联叙事序号")
    note: str = Field(
        default="", description="备注")


# ============================================================
# P0 端点
# ============================================================

@router.get("/ports")
async def ports(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """端口池字典公示(69号通道注册表只读
    消费+端口态/前兆信号/调配口径自描述
    ——观测面)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    return Pay71P0Service().port_dict()


@router.get("/ports/{port_id}")
async def port_detail(
        port_id: str,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """单端口详情(注册表只读+端口态
    扩展口径——观测面)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    try:
        return Pay71P0Service()\
            .port_detail(port_id)
    except Exception as e:
        raise _map(e) from e


@router.get("/panorama")
async def panorama(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """健康度全景视图(69号 P0 健康度
    观测+71号端口态聚合——只读消费
    铁律, 永不写 69号表)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    return await Pay71P0Service().panorama()


@router.post("/ports/{port_id}/state")
async def set_port_state(
        port_id: str,
        body: PortStateBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """端口态人工登记(P0 口径——人工
    专属; P1 引入保护方向自动熔断后
    本轨保留人工覆盖)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    try:
        return await Pay71P0Service()\
            .set_port_state(
                port_id, body.portState)
    except Exception as e:
        raise _map(e) from e


@router.post("/signals/report")
async def signal_report(
        body: SignalReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """前兆信号上报(快环观测——确定性
    权重叠加→预警留痕, 不受 PAY71_MODE
    影响; 预警仅观测, 阈值变更走慢环
    46号审批)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    try:
        return await Pay71P0Service()\
            .report_signal(
                body.portId, body.signal,
                body.latencyDelta,
                body.errorRateDelta,
                body.callbackMs)
    except Exception as e:
        raise _map(e) from e


@router.get("/signals")
async def signal_view(
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """前兆信号留痕视图(观测面)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    return await Pay71P0Service()\
        .signal_view(limit=limit)


@router.get("/events")
async def event_view(
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """全链事件视图(观测面)"""
    _require_admin(x_role)
    from repositories.pay71_repository import (
        Pay71Repository,
    )
    events = await Pay71Repository()\
        .list_events(limit=limit)
    return {"count": len(events),
            "events": events}


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(观测面——off 不受影响)"""
    _require_admin(x_role)
    from services.pay71_p0_service import (
        Pay71P0Service,
    )
    return await Pay71P0Service().model_status()


# ============================================================
# P1 端点(自愈编排)
# ============================================================

@router.get("/selfheal/dict")
async def selfheal_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """自愈编排字典公示(阈值/窗口/探测/
    影子期口径——观测面)"""
    _require_admin(x_role)
    from services.pay71_registry import (
        GOVERNANCE_STATES,
        PRECURSOR_ALERT_THRESHOLD,
        PRECURSOR_FUSE_THRESHOLD,
        PROBE_REQUIRED_SUCCESSES,
        PROPOSAL_KINDS, PROPOSAL_STATES,
        SELFHEAL_ACTIONS,
        SELFHEAL_WINDOW_SIZE, SHADOW_DAYS,
        MODEL_VERSION, current_mode,
    )
    return {
        "modelVersion": MODEL_VERSION,
        "mode": current_mode(),
        "windowSize": SELFHEAL_WINDOW_SIZE,
        "alertThreshold":
            PRECURSOR_ALERT_THRESHOLD,
        "fuseThreshold":
            PRECURSOR_FUSE_THRESHOLD,
        "probeRequired":
            PROBE_REQUIRED_SUCCESSES,
        "shadowDays": SHADOW_DAYS,
        "governanceStates":
            list(GOVERNANCE_STATES),
        "proposalStates":
            list(PROPOSAL_STATES),
        "proposalKinds":
            list(PROPOSAL_KINDS),
        "selfhealActions":
            list(SELFHEAL_ACTIONS),
        "stateMachine": {
            "fuse": "windowScore≥0.80→broken",
            "degrade": "windowScore≥0.50 或 "
                       "69号 critical→degraded",
            "recover": "degraded 且窗口清零→"
                       "healthy; broken 仅经"
                       "半开探测恢复",
        },
    }


@router.post("/selfheal/orchestrate")
async def selfheal_orchestrate(
        body: OrchestrateBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """保护方向自愈编排(纳管端口评估+
    执行——触发窗口/动作/恢复证据全
    留痕; 不受 PAY71_MODE/PAY71_KILL
    影响——保护方向永续铁律)"""
    _require_admin(x_role)
    from services.pay71_p1_service import (
        Pay71P1Service,
    )
    try:
        return await Pay71P1Service()\
            .orchestrate(
                body.portId or None)
    except Exception as e:
        raise _map(e) from e


@router.post("/selfheal/probe")
async def selfheal_probe(
        body: ProbeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """半开探针(broken 态端口专用——
    连续 N 次成功恢复 healthy, 失败
    清零; 恢复证据链全留痕)"""
    _require_admin(x_role)
    from services.pay71_p1_service import (
        Pay71P1Service,
    )
    try:
        return await Pay71P1Service()\
            .probe(body.portId, body.success)
    except Exception as e:
        raise _map(e) from e


@router.post("/selfheal/onboard")
async def selfheal_onboard(
        body: OnboardBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """端口纳管(影子冷启动入口——7 天
    影子期只观测不参与调配; 人工动作
    不受开关影响)"""
    _require_admin(x_role)
    from services.pay71_p1_service import (
        Pay71P1Service,
    )
    try:
        return await Pay71P1Service()\
            .onboard(body.portId)
    except Exception as e:
        raise _map(e) from e


@router.get("/selfheal/shadows")
async def selfheal_shadows(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """影子冷启动视图(纳管端口全景——
    天数/达标判定/观测面)"""
    _require_admin(x_role)
    from services.pay71_p1_service import (
        Pay71P1Service,
    )
    return await Pay71P1Service().shadow_view()


@router.post("/selfheal/propose")
async def selfheal_propose(
        body: ProposeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """转正/摘牌建议书发起(端口正式
    启停永不自动——proposed→admin 终审)"""
    _require_admin(x_role)
    from services.pay71_p1_service import (
        Pay71P1Service,
    )
    try:
        return await Pay71P1Service()\
            .propose(body.portId, body.kind)
    except Exception as e:
        raise _map(e) from e


@router.post("/selfheal/proposals/"
             "{proposal_seq}/decide")
async def selfheal_decide(
        proposal_seq: int,
        body: DecideBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """建议书终审(admin 人工——端口
    启停生效唯一入口, 不受开关影响)"""
    _require_admin(x_role)
    from services.pay71_p1_service import (
        Pay71P1Service,
    )
    try:
        return await Pay71P1Service()\
            .decide(proposal_seq, body.approve)
    except Exception as e:
        raise _map(e) from e


@router.get("/selfheal/traces")
async def selfheal_traces(
        portId: str = "",
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """自愈轨迹视图(触发窗口/动作/恢复
    证据三要素——可审计口径)"""
    _require_admin(x_role)
    from services.pay71_p1_service import (
        Pay71P1Service,
    )
    return await Pay71P1Service().trace_view(
        port_id=portId or None, limit=limit)


# ============================================================
# P2 端点(预判式支付)
# ============================================================

@router.get("/predict/dict")
async def predict_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """预判字典公示(拆分阈值/梯度对齐/
    退避序列——观测面)"""
    _require_admin(x_role)
    from services.pay71_p2_service import (
        Pay71P2Service,
    )
    return Pay71P2Service().dict_view()


def _require_decision_plane() -> None:
    """决策面开关(69号范式——off=409)"""
    from services.pay71_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY71_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")


@router.post("/predict/compute")
async def predict_compute(
        body: PredictComputeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """意图预判+通道预载建议(可撤销
    观测域——不锁定资金铁律; 决策面
    off 409)"""
    _require_admin(x_role)
    _require_decision_plane()
    from services.pay71_p2_service import (
        Pay71P2Service,
    )
    try:
        return await Pay71P2Service()\
            .predict(
                body.memberId, body.amount,
                trust_tier=body.trustTier,
                new_device=body.newDevice,
                odd_hour=body.oddHour,
                new_location=body.newLocation,
                tv_eligible=body.tvEligible)
    except Exception as e:
        raise _map(e) from e


@router.post("/predict/{prediction_seq}/revoke")
async def predict_revoke(
        prediction_seq: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """预载撤销(可撤销观测域的撤销面
    ——显式留痕; 决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_plane()
    from services.pay71_p2_service import (
        Pay71P2Service,
    )
    try:
        return await Pay71P2Service()\
            .revoke(prediction_seq)
    except Exception as e:
        raise _map(e) from e


@router.get("/predict/records")
async def predict_records(
        memberId: int = 0,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """预判留痕视图(观测面)"""
    _require_admin(x_role)
    from services.pay71_p2_service import (
        Pay71P2Service,
    )
    return await Pay71P2Service().records_view(
        member_id=memberId or None,
        limit=limit)


@router.post("/predict/split/propose")
async def split_propose(
        body: SplitProposeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """大额拆分建议书发起(金额/通道
    组合/到账时效三要素+确认令牌——
    用户显式确认流; 决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_plane()
    from services.pay71_p2_service import (
        Pay71P2Service,
    )
    try:
        return await Pay71P2Service()\
            .split_propose(
                body.memberId,
                body.totalAmount,
                tv_eligible=body.tvEligible)
    except Exception as e:
        raise _map(e) from e


@router.post("/predict/split/{split_seq}/confirm")
async def split_confirm(
        split_seq: int,
        body: SplitConfirmBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """拆分建议书确认(令牌单次消费+总额
    回显——executed=False 建议包, 资金
    执行永远 60号收银台显式调用铁律;
    决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_plane()
    from services.pay71_p2_service import (
        Pay71P2Service,
    )
    try:
        return await Pay71P2Service()\
            .split_confirm(
                split_seq,
                body.confirmToken,
                body.approve)
    except Exception as e:
        raise _map(e) from e


@router.get("/predict/splits")
async def predict_splits(
        memberId: int = 0,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """拆分建议书视图(令牌不外泄
    ——观测面)"""
    _require_admin(x_role)
    from services.pay71_p2_service import (
        Pay71P2Service,
    )
    return await Pay71P2Service().splits_view(
        member_id=memberId or None,
        limit=limit)


@router.post("/predict/retry/report")
async def retry_report(
        body: RetryReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """端口失败重试上报(快环观测——
    退避序列确定性, 不受 PAY71_MODE
    影响)"""
    _require_admin(x_role)
    from services.pay71_p2_service import (
        Pay71P2Service,
    )
    try:
        return await Pay71P2Service()\
            .retry_report(
                body.portId, body.failCount)
    except Exception as e:
        raise _map(e) from e


@router.get("/predict/retries")
async def predict_retries(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """重试统计视图(快环观测面)"""
    _require_admin(x_role)
    from services.pay71_p2_service import (
        Pay71P2Service,
    )
    return await Pay71P2Service().retries_view()


# ============================================================
# P3 端点(帕累托调配)
# ============================================================

@router.get("/allocation/dict")
async def allocation_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """调配字典公示(维度/情境权重/合规
    分/支配定义/铁律声明——观测面)"""
    _require_admin(x_role)
    from services.pay71_p3_service import (
        Pay71P3Service,
    )
    return Pay71P3Service().dict_view()


@router.post("/allocation/compute")
async def allocation_compute(
        body: AllocationComputeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """帕累托调配计算(四维向量评分→
    支配分析→情境选解——建议注入 69号
    P1 路由情境参考, 永不直接执行路由;
    决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_plane()
    from services.pay71_p3_service import (
        Pay71P3Service,
    )
    try:
        return await Pay71P3Service()\
            .allocate(
                body.memberId, body.amount,
                context=body.context,
                tv_eligible=body.tvEligible)
    except Exception as e:
        raise _map(e) from e


@router.get("/allocation/records")
async def allocation_records(
        context: str = "",
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """调配留痕视图(可按情境过滤
    ——观测面)"""
    _require_admin(x_role)
    from services.pay71_p3_service import (
        Pay71P3Service,
    )
    return await Pay71P3Service().records_view(
        context=context or None, limit=limit)


@router.get("/allocation/weights")
async def allocation_weights(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """激活权重视图(出厂+覆盖全量
    ——观测面)"""
    _require_admin(x_role)
    from services.pay71_p3_service import (
        Pay71P3Service,
    )
    return await Pay71P3Service().weights_view()


@router.post("/allocation/external/report")
async def allocation_external_report(
        body: ExternalReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """外部信号登记+影响分析+权重建议书
    (快环摄取——不受 PAY71_MODE 影响;
    权重变更永不自动, 仅生成 proposed
    建议书待 admin 终审)"""
    _require_admin(x_role)
    from services.pay71_p3_service import (
        Pay71P3Service,
    )
    try:
        return await Pay71P3Service()\
            .external_report(
                body.kind, body.note,
                tuple(body.affectedPorts))
    except Exception as e:
        raise _map(e) from e


@router.post("/allocation/external/"
             "{external_seq}/decide")
async def allocation_external_decide(
        external_seq: int,
        body: ExternalDecideBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """外部信号建议书终审(admin 人工
    ——权重覆盖激活唯一入口, 不受
    开关影响)"""
    _require_admin(x_role)
    from services.pay71_p3_service import (
        Pay71P3Service,
    )
    try:
        return await Pay71P3Service()\
            .external_decide(
                external_seq, body.approve)
    except Exception as e:
        raise _map(e) from e


@router.get("/allocation/external")
async def allocation_external(
        context: str = "",
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """外部信号建议书视图(观测面)"""
    _require_admin(x_role)
    from services.pay71_p3_service import (
        Pay71P3Service,
    )
    return await Pay71P3Service().external_view(
        context=context or None, limit=limit)


# ============================================================
# P4 端点(对账自愈)
# ============================================================

@router.get("/recon/dict")
async def recon_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """对账自愈字典公示(核验源/差错
    分类/补单状态机/资金铁律声明
    ——观测面)"""
    _require_admin(x_role)
    from services.pay71_p4_service import (
        Pay71P4Service,
    )
    return Pay71P4Service().dict_view()


@router.post("/recon/verify")
async def recon_verify(
        body: ReconVerifyBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """T+0 笔级三向核验(订单/流水/回执
    ——金额+时间窗确定性匹配, 差异自动
    分类; 决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_plane()
    from services.pay71_p4_service import (
        Pay71P4Service,
    )
    try:
        return await Pay71P4Service()\
            .verify(
                body.orderId,
                body.orderAmount,
                body.flowAmount,
                body.receiptAmount,
                port_id=body.portId,
                flow_at=body.flowAt,
                receipt_at=body.receiptAt)
    except Exception as e:
        raise _map(e) from e


@router.get("/recon/verifies")
async def recon_verifies(
        state: str = "",
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """核验留痕视图(可按结果过滤
    ——观测面)"""
    _require_admin(x_role)
    from services.pay71_p4_service import (
        Pay71P4Service,
    )
    return await Pay71P4Service().verify_view(
        state=state or None, limit=limit)


@router.post("/recon/heal")
async def recon_heal(
        body: ReconHealBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """差错处置入口(幂等修复类→自动
    重试补单; 资金类→直接转 60号 P3
    人工终审——冲正/退款永不自动铁律;
    决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_plane()
    from services.pay71_p4_service import (
        Pay71P4Service,
    )
    try:
        return await Pay71P4Service()\
            .heal(body.verifySeq)
    except Exception as e:
        raise _map(e) from e


@router.post("/recon/retry")
async def recon_retry(
        body: ReconRetryBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """补单重试结果上报(成功→auto_
    healed; 失败→退避重试或上限耗尽
    转人工; 决策面 off 409)"""
    _require_admin(x_role)
    _require_decision_plane()
    from services.pay71_p4_service import (
        Pay71P4Service,
    )
    try:
        return await Pay71P4Service()\
            .retry(body.reconSeq,
                   body.success)
    except Exception as e:
        raise _map(e) from e


@router.get("/recon/records")
async def recon_records(
        state: str = "",
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """补单账本视图(可按状态过滤
    ——观测面)"""
    _require_admin(x_role)
    from services.pay71_p4_service import (
        Pay71P4Service,
    )
    return await Pay71P4Service().recon_view(
        state=state or None, limit=limit)


@router.get("/recon/baseline")
async def recon_baseline(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """渠道对账延迟差错基线视图(延迟档
    +差错分布——快环观测面)"""
    _require_admin(x_role)
    from services.pay71_p4_service import (
        Pay71P4Service,
    )
    return await Pay71P4Service().baseline_view()


# ============================================================
# P5 端点(风控叙事)
# ============================================================

@router.get("/narrative/dict")
async def narrative_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """风控叙事字典公示(因果规则/手法
    域/状态机/铁律声明——观测面)"""
    _require_admin(x_role)
    from services.pay71_p5_service import (
        Pay71P5Service,
    )
    return Pay71P5Service().dict_view()


@router.post("/narrative/generate")
async def narrative_generate(
        body: NarrativeGenerateBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """风控叙事生成(69号 P2 熵引擎纯
    调用之上的解释层——判定零重复;
    叙事数字 100% 查询层插值; 决策面
    off 409)"""
    _require_admin(x_role)
    _require_decision_plane()
    from services.pay71_p5_service import (
        Pay71P5Service,
    )
    try:
        return await Pay71P5Service()\
            .narrate(
                body.memberId, body.amount,
                trust_tier=body.trustTier,
                channel_id=body.channelId,
                new_device=body.newDevice,
                odd_hour=body.oddHour,
                new_location=body.newLocation)
    except Exception as e:
        raise _map(e) from e


@router.get("/narrative/records")
async def narrative_records(
        memberId: int = 0,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """叙事留痕视图(可按会员过滤
    ——观测面)"""
    _require_admin(x_role)
    from services.pay71_p5_service import (
        Pay71P5Service,
    )
    return await Pay71P5Service().records_view(
        member_id=memberId or None,
        limit=limit)


@router.post("/narrative/incident/report")
async def narrative_incident_report(
        body: IncidentReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """欺诈案例归档(已确认案例回流
    ——memberId 哈希脱敏铁律; 快环
    摄取不受 PAY71_MODE 影响)"""
    _require_admin(x_role)
    from services.pay71_p5_service import (
        Pay71P5Service,
    )
    try:
        return await Pay71P5Service()\
            .report_incident(
                body.memberId, body.pattern,
                summary=body.summary,
                entropy_axes=body.entropyAxes,
                device_clues=body.deviceClues)
    except Exception as e:
        raise _map(e) from e


@router.post("/narrative/incident/"
             "{incident_seq}/verify")
async def narrative_incident_verify(
        incident_seq: int,
        body: IncidentVerifyBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """案例核实(pending→confirmed_
    fraud/confirmed_legit——误拦平反
    通道; 人工动作不受开关影响)"""
    _require_admin(x_role)
    from services.pay71_p5_service import (
        Pay71P5Service,
    )
    try:
        return await Pay71P5Service()\
            .verify_incident(
                incident_seq,
                body.confirmedFraud)
    except Exception as e:
        raise _map(e) from e


@router.post("/narrative/misjudge/report")
async def narrative_misjudge_report(
        body: MisjudgeReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """误拦归因回流(观测管道——熵轴
    权重建议书走 P7 慢环→46号审批;
    快环摄取不受 PAY71_MODE 影响)"""
    _require_admin(x_role)
    from services.pay71_p5_service import (
        Pay71P5Service,
    )
    try:
        return await Pay71P5Service()\
            .report_misjudge(
                body.memberId, body.kind,
                narrative_seq=body.narrativeSeq,
                note=body.note)
    except Exception as e:
        raise _map(e) from e


@router.get("/narrative/library")
async def narrative_library(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """案例库+误拦归因统计视图
    (观测面)"""
    _require_admin(x_role)
    from services.pay71_p5_service import (
        Pay71P5Service,
    )
    return await Pay71P5Service().library_view()


def register_pay71_routes(app) -> None:
    """挂载 71号路由(main.py 惯例)"""
    app.include_router(router)
