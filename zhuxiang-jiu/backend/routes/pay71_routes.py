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

鉴权: 管理面 X-Role: admin(69号同款口径)。
统一口径(69号范式):
    - 观测面不受 PAY71_MODE 影响
    - 快环观测上报(signals/retry report)
      不受开关影响
    - 人工面(port state/onboard/propose/
      decide)不受开关影响
    - 保护面(orchestrate/probe)不受开关
      影响——保护方向永续铁律(规划 §4.1)
    - 决策面(predict compute/revoke/
      split propose/confirm)off=409
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


def register_pay71_routes(app) -> None:
    """挂载 71号路由(main.py 惯例)"""
    app.include_router(router)
