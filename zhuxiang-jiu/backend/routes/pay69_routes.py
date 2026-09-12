"""69号·AI智能支付大模型路由(P0-P8)

端点(P0 8 + P1 7 + P2 6 + P3 7 + P4 6 + P5 6 + P6 6 + P7 10 + P8 7 = 63):
    GET  /api/pay69/channels            七通道字典公示(admin, 观测面)
    GET  /api/pay69/channels/{id}       单通道详情(admin, 观测面)
    GET  /api/pay69/health              健康度观测面(admin, 观测面)
    POST /api/pay69/health/report       通道健康度上报(admin, 快环观测——不受开关影响)
    POST /api/pay69/health/{id}/freeze  通道冻结/解冻(admin, 人工专属)
    GET  /api/pay69/intents             意图留痕视图(admin, 观测面)
    POST /api/pay69/intents/parse       意图标签解析+留痕(admin, 决策面 off 409)
    GET  /api/pay69/model/status        模型状态(admin, 观测面)
    GET  /api/pay69/route/dict          路由字典公示(admin, 观测面——P1)
    POST /api/pay69/route/compute       多因子评分选道(admin, 决策面 off 409——P1)
    POST /api/pay69/route/execute       沙盘执行+静默备选(admin, 执行面需 assist——P1)
    POST /api/pay69/route/habit/report  会员通道习惯上报(admin, 快环——P1)
    GET  /api/pay69/route/habits/{id}   会员习惯视图(admin, 观测面——P1)
    GET  /api/pay69/route/flows         路由执行留痕视图(admin, 观测面——P1)
    GET  /api/pay69/route/window        通道滚动窗口统计(admin, 观测面——P1)
    GET  /api/pay69/entropy/dict        熵引擎字典公示(admin, 观测面——P2)
    POST /api/pay69/entropy/compute     六轴熵计算+步进梯度(admin, 决策面 off 409——P2)
    POST /api/pay69/behavior/report     行为样本上报(admin, 快环 EMA 基线——P2)
    GET  /api/pay69/behavior/baseline/{id} 行为基线视图(admin, 观测面——P2)
    POST /api/pay69/behavior/deviation 行为偏离度预览(admin, 决策面 off 409——P2)
    GET  /api/pay69/entropy/records     熵评估留痕视图(admin, 观测面——P2)
    GET  /api/pay69/credit/dict         授信规则表公示(admin, 观测面——P3)
    POST /api/pay69/credit/evaluate     交易级授信评估(admin, 决策面 off 409——P3)
    POST /api/pay69/credit/adjustment/propose  调额建议书发起(admin, 决策面 off 409——P3)
    POST /api/pay69/credit/adjustment/{id}/decide 调额终审(admin, 人工——不受开关影响——P3)
    POST /api/pay69/credit/repayment/report 还款事件回流(admin, 快环——P3)
    GET  /api/pay69/credit/records      授信留痕视图(admin, 观测面——P3)
    GET  /api/pay69/credit/adjustments  调额建议书视图(admin, 观测面——P3)
    GET  /api/pay69/biometric/dict      生物字典公示(admin, 观测面——P4)
    POST /api/pay69/biometric/challenge  FIDO 挑战发起(admin, 决策面 off 409——P4)
    POST /api/pay69/biometric/verify     生物验证(挑战+胁迫判定)(admin, 决策面 off 409——P4)
    POST /api/pay69/biometric/template/register 端侧模板版本登记(admin, 决策面 off 409——P4)
    GET  /api/pay69/biometric/template/{id} 模板账本视图(admin, 观测面——P4)
    GET  /api/pay69/biometric/events    生物事件视图(admin, 观测面——P4)
    GET  /api/pay69/smartcode/dict      情境码字典公示(admin, 观测面——P5)
    POST /api/pay69/smartcode/generate  情境码生成(admin, 决策面 off 409——P5)
    POST /api/pay69/smartcode/redeem    码核销(admin, 决策面 off 409——P5)
    GET  /api/pay69/smartcode/events    情境码事件视图(admin, 观测面——P5)
    GET  /api/pay69/smartcode/merchant/{id}/summary 商户对账摘要(admin, 观测面——P5)
    GET  /api/pay69/smartcode/stats     情境风险统计(admin, 观测面——P5)
    GET  /api/pay69/modality/dict       多模态字典公示(admin, 观测面——P6)
    POST /api/pay69/modality/parse      多模态意图解析(admin, 决策面 off 409——P6)
    POST /api/pay69/modality/confirm/request 确认请求(admin, 决策面 off 409——P6)
    POST /api/pay69/modality/confirm/pay 确认支付(admin, 决策面 off 409——P6)
    GET  /api/pay69/modality/events     多模态事件视图(admin, 观测面——P6)
    GET  /api/pay69/modality/stats      群体模态统计(admin, 观测面——P6)
    GET  /api/pay69/evolution/dict      进化引擎字典公示(admin, 观测面——P7)
    POST /api/pay69/evolution/drift/detect  快环漂移检测(admin, 快环——不受开关影响——P7)
    POST /api/pay69/evolution/hypothesis/propose 进化假设生成(admin, 决策面 off 409——P7)
    POST /api/pay69/evolution/hypothesis/{id}/submit 46号审批提交(admin, 决策面 off 409——P7)
    POST /api/pay69/evolution/hypothesis/{id}/reject  46号驳回留痕(admin, 人工——不受开关——P7)
    POST /api/pay69/evolution/params/{v}/publish 参数版本发布(admin, 决策面 off 409——P7)
    POST /api/pay69/evolution/params/{v}/rollback 版本回滚(admin, 决策面 off 409——P7)
    POST /api/pay69/evolution/kill     紧急制动(admin, 人工——不受开关——P7)
    GET  /api/pay69/evolution/hypotheses 假设视图(admin, 观测面——P7)
    GET  /api/pay69/evolution/governance L0-L2 治理观测(admin, 观测面——P7)
    GET  /api/pay69/immunity/dict      免疫字典公示(admin, 观测面——P8)
    GET  /api/pay69/immunity            免疫看板(admin, 观测面——P8)
    POST /api/pay69/immunity/monitor    分布监控+自动冻结(admin, 快环——不受开关——P8)
    POST /api/pay69/immunity/freeze     人工冻结进化(admin, 人工——不受开关——P8)
    POST /api/pay69/immunity/unfreeze   解冻(admin, 人工专属——P8)
    POST /api/pay69/immunity/redteam    红队四向量执行(admin, 决策面 off 409——P8)
    GET  /api/pay69/immunity/redteam/runs 红队批次历史(admin, 观测面——P8)

鉴权: 管理面 X-Role: admin(60号同款口径)。
统一口径(60号范式):
    - 观测面不受 PAY69_MODE 影响
    - 快环观测上报(health report/
      habit report/behavior report)
      不受开关影响
    - 决策面(compute/intents parse/
      entropy compute/deviation):
      off=拒绝(409)
    - 执行面(execute): 需 assist
      (影子期不执行)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/pay69",
                   tags=["AI智能支付大模型(69号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


def _map(exc: Exception) -> HTTPException:
    """统一异常映射(60号口径)"""
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

class HealthReportBody(BaseModel):
    channelId: str = Field(description="通道 ID")
    attemptCount: int = Field(ge=0,
                             description="尝试次数")
    successCount: int = Field(ge=0,
                              description="成功次数")
    avgLatencyMs: float = Field(default=0,
                                description="平均耗时(ms)")


class FreezeBody(BaseModel):
    frozen: bool = Field(description="冻结/解冻")


class IntentParseBody(BaseModel):
    intentText: str = Field(min_length=1,
                            description="意图文本")
    memberId: int = Field(default=0,
                          description="会员 ID(归因)")
    intentId: int = Field(default=0,
                          description="60号意图 ID(透传)")
    sessionId: int = Field(default=0,
                           description="会话 ID(透传)")


# ============================================================
# P0 端点
# ============================================================

@router.get("/channels")
async def channels(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """七通道字典公示(费率/限额/时效
    /特征/健康度阈值——观测面)"""
    _require_admin(x_role)
    from services.pay69_p0_service import (
        Pay69P0Service,
    )
    return Pay69P0Service().channel_dict()


@router.get("/channels/{channel_id}")
async def channel_detail(
        channel_id: str,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """单通道详情(注册表+实时健康度
    ——观测面)"""
    _require_admin(x_role)
    from services.pay69_p0_service import (
        Pay69P0Service,
    )
    try:
        return Pay69P0Service()\
            .channel_detail(channel_id)
    except Exception as e:
        raise _map(e) from e


@router.get("/health")
async def health_view(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """七通道健康度观测面(未观测默认
    healthy——零影响)"""
    _require_admin(x_role)
    from services.pay69_p0_service import (
        Pay69P0Service,
    )
    return await Pay69P0Service().health_view()


@router.post("/health/report")
async def health_report(
        body: HealthReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """通道健康度上报(快环观测——确定性
    统计基线, 不受 PAY69_MODE 影响;
    frozen 永不被上报改写)"""
    _require_admin(x_role)
    from services.pay69_p0_service import (
        Pay69P0Service,
    )
    try:
        return await Pay69P0Service()\
            .report_health(
                body.channelId,
                body.attemptCount,
                body.successCount,
                body.avgLatencyMs)
    except Exception as e:
        raise _map(e) from e


@router.post("/health/{channel_id}/freeze")
async def freeze_channel(
        channel_id: str,
        body: FreezeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """通道冻结/解冻(人工专属——frozen
    永不可由成功率导出铁律; P1 路由
    永不选中 frozen 通道)"""
    _require_admin(x_role)
    from services.pay69_p0_service import (
        Pay69P0Service,
    )
    try:
        return await Pay69P0Service().set_frozen(
            channel_id, body.frozen)
    except Exception as e:
        raise _map(e) from e


@router.get("/intents")
async def intent_view(
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """意图留痕视图(观测面)"""
    _require_admin(x_role)
    from services.pay69_p0_service import (
        Pay69P0Service,
    )
    return await Pay69P0Service()\
        .intent_view(limit=limit)


@router.post("/intents/parse")
async def intent_parse(
        body: IntentParseBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """意图标签解析+留痕(规则轨——LLM
    禁入判定链; 决策面 off 409)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_p0_service import (
        Pay69P0Service,
    )
    try:
        return await Pay69P0Service()\
            .record_intent(
                body.intentText,
                body.memberId,
                body.intentId,
                body.sessionId)
    except Exception as e:
        raise _map(e) from e


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(观测面——off 不受影响)"""
    _require_admin(x_role)
    from services.pay69_p0_service import (
        Pay69P0Service,
    )
    return await Pay69P0Service().model_status()


def register_pay69_routes(app) -> None:
    """注册69号路由(main.py startup 调用)"""
    app.include_router(router)


# ============================================================
# P1 智能路由(7 端点)
# ============================================================

class RouteComputeBody(BaseModel):
    memberId: int = Field(default=0,
                          description="会员 ID")
    amount: float = Field(gt=0,
                          description="支付金额(元)")
    tags: list[str] = Field(
        default_factory=list,
        description="意图标签(显式——P0 八类)")
    intentText: str = Field(default="",
                            description="意图文本(无标签时规则轨解析)")
    tvEligible: bool = Field(
        default=False,
        description="信值资格(credit_tv 参评前置——45号 TV 资金源)")


class RouteExecuteBody(RouteComputeBody):
    simulateFail: list[str] = Field(
        default_factory=list,
        description="沙盘失败注入通道(测试钩子)")


class HabitReportBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    channelId: str = Field(description="通道 ID")
    count: int = Field(default=1, gt=0,
                       description="使用次数")


@router.get("/route/dict")
async def route_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """路由字典公示(权重+因子口径+窗口
    ——观测面)"""
    _require_admin(x_role)
    from services.pay69_router_service import (
        Pay69RouterService,
    )
    return Pay69RouterService().route_dict()


@router.post("/route/compute")
async def route_compute(
        body: RouteComputeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """多因子确定性评分选道(决策面——
    off 409; routeScore 四因子数值明细
    留痕, LLM 禁入)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_router_service import (
        Pay69RouterService,
    )
    try:
        return await Pay69RouterService()\
            .compute_route(
                body.memberId, body.amount,
                body.tags, body.intentText,
                tv_eligible=body.tvEligible)
    except Exception as e:
        raise _map(e) from e


@router.post("/route/execute")
async def route_execute(
        body: RouteExecuteBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """路由沙盘执行+静默备选重试(执行面
    ——需 assist; 首选失败自动尝试次优,
    全程留痕 flows 供快环窗口消费)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() != "assist":
        raise HTTPException(
            status_code=409,
            detail=f"PAY69_MODE={current_mode()}"
                  f"(执行面需 assist——影子期"
                  f"不执行)")
    from services.pay69_router_service import (
        Pay69RouterService,
    )
    try:
        return await Pay69RouterService()\
            .execute_route(
                body.memberId, body.amount,
                body.tags, body.intentText,
                body.simulateFail,
                tv_eligible=body.tvEligible)
    except Exception as e:
        raise _map(e) from e


@router.post("/route/habit/report")
async def route_habit_report(
        body: HabitReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """会员通道习惯上报(快环观测——
    不受 PAY69_MODE 影响)"""
    _require_admin(x_role)
    from services.pay69_router_service import (
        Pay69RouterService,
    )
    try:
        return await Pay69RouterService()\
            .habit_report(
                body.memberId, body.channelId,
                body.count)
    except Exception as e:
        raise _map(e) from e


@router.get("/route/habits/{member_id}")
async def route_habits(
        member_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """会员通道习惯视图(观测面)"""
    _require_admin(x_role)
    from services.pay69_router_service import (
        Pay69RouterService,
    )
    return await Pay69RouterService()\
        .habits_view(member_id)


@router.get("/route/flows")
async def route_flows(
        channelId: str | None = None,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """路由执行留痕视图(观测面)"""
    _require_admin(x_role)
    from services.pay69_router_service import (
        Pay69RouterService,
    )
    return await Pay69RouterService()\
        .flows_view(channelId, limit=limit)


@router.get("/route/window")
async def route_window(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """七通道滚动窗口统计(快环基线
    ——观测面)"""
    _require_admin(x_role)
    from services.pay69_router_service import (
        Pay69RouterService,
    )
    return await Pay69RouterService()\
        .window_view()


# ============================================================
# P2 认证步进(6 端点)
# ============================================================

class EntropyComputeBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    amount: float = Field(gt=0,
                          description="支付金额(元)")
    trustTier: str = Field(
        default="",
        description="信值等级(45号口径: S/A/B/C/D 或 trusted/standard/watched/restricted)")
    channelId: str = Field(default="",
                          description="支付通道 ID")
    newDevice: bool = Field(
        default=False, description="新设备")
    oddHour: bool = Field(
        default=False, description="异常时段(0-6 点)")
    newLocation: bool = Field(
        default=False, description="异常地点")
    anomalyRate: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="历史支付异常率(0-1)")
    intervalMs: float = Field(
        default=0, ge=0,
        description="本次操作间隔(ms)——行为轴")
    typingSpeedMs: float = Field(
        default=0, ge=0,
        description="本次按键间隔(ms)——行为轴")


class BehaviorReportBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    intervalMs: float = Field(gt=0,
                              description="操作间隔(ms)")
    typingSpeedMs: float = Field(gt=0,
                                 description="按键间隔(ms)")


@router.get("/entropy/dict")
async def entropy_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """熵引擎字典公示(六轴+权重+步进
    梯度——观测面)"""
    _require_admin(x_role)
    from services.pay69_entropy_service import (
        Pay69EntropyService,
    )
    return Pay69EntropyService().entropy_dict()


@router.post("/entropy/compute")
async def entropy_compute(
        body: EntropyComputeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """六轴熵计算+步进梯度解析(决策面
    ——off 409; 六轴数值明细留痕,
    fail-soft 引擎故障→light 档)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_entropy_service import (
        Pay69EntropyService,
    )
    try:
        return await Pay69EntropyService()\
            .compute_entropy(
                body.memberId, body.amount,
                trust_tier=body.trustTier,
                channel_id=body.channelId,
                new_device=body.newDevice,
                odd_hour=body.oddHour,
                new_location=body.newLocation,
                anomaly_rate=body.anomalyRate,
                interval_ms=body.intervalMs,
                typing_speed_ms=body.typingSpeedMs,
                fail_soft=True)
    except Exception as e:
        raise _map(e) from e


@router.post("/behavior/report")
async def behavior_report(
        body: BehaviorReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """行为样本上报(快环——EMA 基线
    增量更新, 不受 PAY69_MODE 影响)"""
    _require_admin(x_role)
    from services.pay69_entropy_service import (
        Pay69EntropyService,
    )
    try:
        return await Pay69EntropyService()\
            .report_behavior(
                body.memberId, body.intervalMs,
                body.typingSpeedMs)
    except Exception as e:
        raise _map(e) from e


@router.get("/behavior/baseline/{member_id}")
async def behavior_baseline(
        member_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """会员行为基线视图(观测面)"""
    _require_admin(x_role)
    from services.pay69_entropy_service import (
        Pay69EntropyService,
    )
    return await Pay69EntropyService()\
        .baseline_view(member_id)


@router.post("/behavior/deviation")
async def behavior_deviation(
        body: BehaviorReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """行为偏离度预览(决策面——off 409;
    不落基线, 供收银台实时预演)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_entropy_service import (
        Pay69EntropyService,
    )
    svc = Pay69EntropyService()
    baseline = await svc.repo.get_baseline(
        body.memberId)
    deviation = svc.deviation_of(
        baseline, body.intervalMs,
        body.typingSpeedMs)
    return {
        "memberId": body.memberId,
        "deviation": deviation,
        "axisScore": svc._behavior_axis(
            deviation),
        "established": bool(
            baseline and baseline.get(
                "samples")),
    }


@router.get("/entropy/records")
async def entropy_records(
        memberId: int | None = None,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """熵评估留痕视图(观测面——全局/
    会员双口径)"""
    _require_admin(x_role)
    from services.pay69_entropy_service import (
        Pay69EntropyService,
    )
    return await Pay69EntropyService()\
        .entropy_view(memberId, limit=limit)


# ============================================================
# P3 交易级授信(6 端点)
# ============================================================

class CreditEvaluateBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    amount: float = Field(gt=0,
                          description="交易金额(元)")
    trustTier: str = Field(
        default="",
        description="信值等级(45号口径: S/A/B/C/D)")
    cashflowIndex: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="现金流指数(0-1, 60号预测/调用方口径)")
    baseLimit: float | None = Field(
        default=None,
        description="现行额度(缺省取等级映射; 调额生效后传生效值)")


class AdjustmentProposeBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    requestedLimit: float = Field(gt=0,
                                  description="目标额度(元)")
    trustTier: str = Field(
        default="",
        description="信值等级(S/A/B/C/D——额度域)")
    reason: str = Field(default="",
                       description="调额理由")
    proposedBy: str = Field(
        default="admin",
        description="发起方(admin/member)")


class AdjustmentDecideBody(BaseModel):
    approve: bool = Field(description="批准/拒绝")
    decidedBy: str = Field(
        default="admin", description="终审人")


class RepaymentReportBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    eventType: str = Field(
        description="还款事件(ontime/late/early)")


@router.get("/credit/dict")
async def credit_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """授信规则表公示(四轴+评级+利率表
    +调额状态机——观测面)"""
    _require_admin(x_role)
    from services.pay69_credit_service import (
        Pay69CreditService,
    )
    return Pay69CreditService().credit_dict()


@router.post("/credit/evaluate")
async def credit_evaluate(
        body: CreditEvaluateBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """交易级授信评估(决策面——off 409;
    四轴确定性+评级+分期方案利率查表,
    LLM 禁定价)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_credit_service import (
        Pay69CreditService,
    )
    try:
        return await Pay69CreditService()\
            .evaluate_credit(
                body.memberId, body.amount,
                trust_tier=body.trustTier,
                cashflow_index=body.cashflowIndex,
                base_limit=body.baseLimit)
    except Exception as e:
        raise _map(e) from e


@router.post("/credit/adjustment/propose")
async def credit_adjustment_propose(
        body: AdjustmentProposeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """调额建议书发起(决策面——off 409;
    proposed 态永不直接生效, 资金域
    永不自动铁律)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_credit_service import (
        Pay69CreditService,
    )
    try:
        return await Pay69CreditService()\
            .propose_adjustment(
                body.memberId,
                body.requestedLimit,
                trust_tier=body.trustTier,
                reason=body.reason,
                proposed_by=body.proposedBy)
    except Exception as e:
        raise _map(e) from e


@router.post("/credit/adjustment/{adj_id}/decide")
async def credit_adjustment_decide(
        adj_id: int,
        body: AdjustmentDecideBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """调额终审(admin 人工——不受开关
    影响, 资金操作人工铁律; approved 即
    生效并覆盖现行额度)"""
    _require_admin(x_role)
    from services.pay69_credit_service import (
        Pay69CreditService,
    )
    try:
        return await Pay69CreditService()\
            .decide_adjustment(
                adj_id, body.approve,
                decided_by=body.decidedBy)
    except Exception as e:
        raise _map(e) from e


@router.post("/credit/repayment/report")
async def credit_repayment_report(
        body: RepaymentReportBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """还款事件回流(快环——不受开关
    影响; ontime/late/early 计数,
    履约轴数据源)"""
    _require_admin(x_role)
    from services.pay69_credit_service import (
        Pay69CreditService,
    )
    try:
        return await Pay69CreditService()\
            .report_repayment(
                body.memberId, body.eventType)
    except Exception as e:
        raise _map(e) from e


@router.get("/credit/records")
async def credit_records(
        memberId: int | None = None,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """授信评估留痕视图(观测面)"""
    _require_admin(x_role)
    from services.pay69_credit_service import (
        Pay69CreditService,
    )
    return await Pay69CreditService()\
        .credit_records_view(
            memberId, limit=limit)


@router.get("/credit/adjustments")
async def credit_adjustments(
        status: str | None = None,
        memberId: int | None = None,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """调额建议书视图(观测面——可按
    状态/会员过滤)"""
    _require_admin(x_role)
    from services.pay69_credit_service import (
        Pay69CreditService,
    )
    return await Pay69CreditService()\
        .adjustments_view(
            status=status,
            member_id=memberId, limit=limit)


# ============================================================
# P4 生物特征(7 端点)
# ============================================================

class ChallengeBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    method: str = Field(
        default="face",
        description="生物方式(face/fingerprint)")


class BiometricVerifyBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    challenge: str = Field(description="FIDO 挑战码")
    match: bool = Field(
        description="特征匹配结果(端侧判定)")
    signs: list[str] = Field(
        default_factory=list,
        description="胁迫语义线索(命中列表)")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="匹配置信度(0-1)")


class TemplateRegisterBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    method: str = Field(
        default="face",
        description="生物方式(face/fingerprint)")
    templateVersion: int | None = Field(
        default=None,
        description="端侧新版本号(顺序+1 校验; 首登记须 1)")
    confidenceHash: str = Field(
        default="",
        description="置信度哈希(原始特征单向哈希——永不上传原始数据)")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="模板置信度(0-1)")


@router.get("/biometric/dict")
async def biometric_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """生物特征字典公示(方式/结果/胁迫
    线索表+阈值——观测面)"""
    _require_admin(x_role)
    from services.pay69_biometric_service import (
        Pay69BiometricService,
    )
    return Pay69BiometricService()\
        .biometric_dict()


@router.post("/biometric/challenge")
async def biometric_challenge(
        body: ChallengeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """FIDO 挑战发起(决策面——off 409;
    一次性+TTL+防重放, 48号 confirmToken
    语义)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_biometric_service import (
        Pay69BiometricService,
    )
    try:
        return await Pay69BiometricService()\
            .issue_challenge(
                body.memberId, body.method)
    except Exception as e:
        raise _map(e) from e


@router.post("/biometric/verify")
async def biometric_verify(
        body: BiometricVerifyBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """生物验证(决策面——off 409; 挑战
    一次性消费+特征匹配+胁迫判定; 胁迫
    →degraded 静默降级+人工留痕)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_biometric_service import (
        Pay69BiometricService,
    )
    try:
        return await Pay69BiometricService()\
            .verify(
                body.memberId, body.challenge,
                body.match, body.signs,
                body.confidence)
    except Exception as e:
        raise _map(e) from e


@router.post("/biometric/template/register")
async def biometric_template_register(
        body: TemplateRegisterBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """端侧模板版本登记/增量(决策面——
    off 409; 版本顺序+1 防回滚; 原始
    特征永不上传——仅版本+置信度哈希)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_biometric_service import (
        Pay69BiometricService,
    )
    try:
        return await Pay69BiometricService()\
            .register_template(
                body.memberId, body.method,
                body.templateVersion,
                body.confidenceHash,
                body.confidence)
    except Exception as e:
        raise _map(e) from e


@router.get("/biometric/template/{member_id}")
async def biometric_template_view(
        member_id: int,
        method: str = "face",
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模板版本账本视图(观测面——服务端
    仅版本+哈希, 无原始特征)"""
    _require_admin(x_role)
    from services.pay69_biometric_service import (
        Pay69BiometricService,
    )
    return await Pay69BiometricService()\
        .template_view(member_id, method)


@router.get("/biometric/events")
async def biometric_events(
        memberId: int | None = None,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """生物验证事件视图(观测面——全局/
    会员双口径)"""
    _require_admin(x_role)
    from services.pay69_biometric_service import (
        Pay69BiometricService,
    )
    return await Pay69BiometricService()\
        .bio_events_view(memberId, limit=limit)


# ============================================================
# P5 情境智能码(6 端点)
# ============================================================

class SmartcodeGenerateBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    merchantId: int = Field(description="商户 ID")
    amount: float = Field(gt=0,
                          description="交易金额(元)")
    productType: str = Field(
        default="",
        description="商品类型(码内参数)")
    hour: int = Field(
        default=12, ge=0, le=23,
        description="当前时段(0-23——0-6 为夜间)")
    newDevice: bool = Field(
        default=False, description="陌生设备")
    remoteLocation: bool = Field(
        default=False, description="异常地点")
    tags: list[str] = Field(
        default_factory=list,
        description="意图标签(推荐方式亲和)")


class SmartcodeRedeemBody(BaseModel):
    merchantId: int = Field(description="商户 ID")
    code: str = Field(description="完整码值")


@router.get("/smartcode/dict")
async def smartcode_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """情境码字典公示(风险因素+阈值
    +状态域+55号口径——观测面)"""
    _require_admin(x_role)
    from services.pay69_smartcode_service import (
        Pay69SmartcodeService,
    )
    return Pay69SmartcodeService()\
        .smartcode_dict()


@router.post("/smartcode/generate")
async def smartcode_generate(
        body: SmartcodeGenerateBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """情境智能码生成(决策面——off 409;
    用户/商户主动触发; 55号签名链+
    情境挑战判定+防伪水印)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_smartcode_service import (
        Pay69SmartcodeService,
    )
    try:
        return await Pay69SmartcodeService()\
            .generate(
                body.memberId, body.merchantId,
                body.amount,
                product_type=body.productType,
                hour=body.hour,
                new_device=body.newDevice,
                remote_location=body.remoteLocation,
                tags=body.tags)
    except Exception as e:
        raise _map(e) from e


@router.post("/smartcode/redeem")
async def smartcode_redeem(
        body: SmartcodeRedeemBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """码核销(商户主动——决策面 off 409;
    55号验签四态+归属商户校验+状态机
    generated→redeemed/expired)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_smartcode_service import (
        Pay69SmartcodeService,
    )
    try:
        return await Pay69SmartcodeService()\
            .redeem(body.merchantId, body.code)
    except Exception as e:
        raise _map(e) from e


@router.get("/smartcode/events")
async def smartcode_events(
        merchantId: int | None = None,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """情境码事件视图(观测面——完整码值
    脱敏)"""
    _require_admin(x_role)
    from services.pay69_smartcode_service import (
        Pay69SmartcodeService,
    )
    return await Pay69SmartcodeService()\
        .smartcode_events_view(
            merchantId, limit=limit)


@router.get("/smartcode/merchant/{merchant_id}/summary")
async def smartcode_merchant_summary(
        merchant_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """商户对账摘要(核销/金额/挑战统计
    聚合——观测面)"""
    _require_admin(x_role)
    from services.pay69_smartcode_service import (
        Pay69SmartcodeService,
    )
    return await Pay69SmartcodeService()\
        .merchant_summary(merchant_id)


@router.get("/smartcode/stats")
async def smartcode_stats(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """情境风险快环统计(因素命中率×
    挑战率——观测面)"""
    _require_admin(x_role)
    from services.pay69_smartcode_service import (
        Pay69SmartcodeService,
    )
    return await Pay69SmartcodeService()\
        .context_stats()


# ============================================================
# P6 多模态普惠(6 端点)
# ============================================================

class ModalityParseBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    text: str = Field(description="模态转写文本(语音识别/手势映射)")
    modality: str = Field(
        default="voice",
        description="模态(voice/gesture/eyegaze/text)")
    accessGroup: str = Field(
        default="standard",
        description="无障碍群体(elderly/motor_impaired/visually_impaired/standard)")


class ConfirmRequestBody(BaseModel):
    memberId: int = Field(description="会员 ID")
    amount: float = Field(gt=0,
                          description="支付金额(元——回显)")
    product: str = Field(default="",
                         description="商品名(回显)")
    channelId: str = Field(default="",
                          description="通道偏好")
    modality: str = Field(
        default="voice",
        description="模态(voice/gesture/eyegaze/text)")
    accessGroup: str = Field(
        default="standard",
        description="无障碍群体")


class ConfirmPaymentBody(BaseModel):
    confirmToken: str = Field(description="确认令牌")
    word: str = Field(description="确认词(含 确认支付)")


@router.get("/modality/dict")
async def modality_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """多模态字典公示(模态/群体/三态
    +无障碍档案+确认词——观测面)"""
    _require_admin(x_role)
    from services.pay69_modality_service import (
        Pay69ModalityService,
    )
    return Pay69ModalityService()\
        .modality_dict()


@router.post("/modality/parse")
async def modality_parse(
        body: ModalityParseBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """多模态意图解析(决策面——off 409;
    规则轨三态 direct/confirm/clarify
    +无障碍适配, LLM 禁入判定链)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_modality_service import (
        Pay69ModalityService,
    )
    try:
        return await Pay69ModalityService()\
            .parse_logged(
                body.memberId, body.text,
                body.modality,
                body.accessGroup)
    except Exception as e:
        raise _map(e) from e


@router.post("/modality/confirm/request")
async def modality_confirm_request(
        body: ConfirmRequestBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """确认请求(决策面——off 409; 金额
    回显+确认令牌——资金确认显式铁律
    第一步)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_modality_service import (
        Pay69ModalityService,
    )
    try:
        return await Pay69ModalityService()\
            .request_confirmation(
                body.memberId, body.amount,
                body.product, body.channelId,
                body.modality,
                body.accessGroup)
    except Exception as e:
        raise _map(e) from e


@router.post("/modality/confirm/pay")
async def modality_confirm_pay(
        body: ConfirmPaymentBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """确认支付(决策面——off 409; 确认词
    匹配+令牌单次消费; 确认后仅生成 60号
    开单建议包——实际开单由 60号收银台
    显式调用, 资金永不自动)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_modality_service import (
        Pay69ModalityService,
    )
    try:
        return await Pay69ModalityService()\
            .confirm_payment(
                body.confirmToken, body.word)
    except Exception as e:
        raise _map(e) from e


@router.get("/modality/events")
async def modality_events(
        memberId: int | None = None,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """多模态事件视图(观测面——全局/
    会员双口径)"""
    _require_admin(x_role)
    from services.pay69_modality_service import (
        Pay69ModalityService,
    )
    return await Pay69ModalityService()\
        .modality_events_view(
            memberId, limit=limit)


@router.get("/modality/stats")
async def modality_stats(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """群体×模态×结果三轴统计+直出率
    (快环基线——观测面)"""
    _require_admin(x_role)
    from services.pay69_modality_service import (
        Pay69ModalityService,
    )
    return await Pay69ModalityService()\
        .group_stats_view()


# ============================================================
# P7 自进化引擎(9 端点)
# ============================================================

class HypothesisProposeBody(BaseModel):
    paramId: str = Field(description="参数 ID(白名单)")
    proposedValue: object = Field(
        description="建议值(权重类须对象且和=1.0)")
    reason: str = Field(description="进化理由")
    expectedGain: str = Field(
        default="", description="预期收益")
    riskAssessment: str = Field(
        default="", description="风险评估")
    proposedBy: str = Field(
        default="admin", description="发起方")


class VersionPublishBody(BaseModel):
    shadowFirst: bool = Field(
        default=False,
        description="先影子态(灰度范式)")


class KillBody(BaseModel):
    activate: bool = Field(description="激活/解除制动(数据面)")


@router.get("/evolution/dict")
async def evolution_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """进化引擎字典公示(分级/参数白名单
    /版本状态机/漂移阈值——观测面)"""
    _require_admin(x_role)
    from services.pay69_evolution_service import (
        Pay69EvolutionService,
    )
    return Pay69EvolutionService()\
        .evolution_dict()


@router.post("/evolution/drift/detect")
async def evolution_drift_detect(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """快环漂移检测(三域确定性统计——
    不受 PAY69_MODE 影响; 仅产生信号,
    不含任何参数变更)"""
    _require_admin(x_role)
    from services.pay69_evolution_service import (
        Pay69EvolutionService,
    )
    return await Pay69EvolutionService()\
        .detect_drift()


@router.post("/evolution/hypothesis/propose")
async def evolution_hypothesis_propose(
        body: HypothesisProposeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """进化假设生成(决策面——off 409;
    disposition 四要素+draft 参数版本;
    高风险参数权重和=1.0 宪法校验)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_evolution_service import (
        Pay69EvolutionService,
    )
    try:
        return await Pay69EvolutionService()\
            .propose_hypothesis(
                body.paramId,
                body.proposedValue,
                body.reason,
                body.expectedGain,
                body.riskAssessment,
                body.proposedBy)
    except Exception as e:
        raise _map(e) from e


@router.post(
    "/evolution/hypothesis/{hyp_id}/submit")
async def evolution_hypothesis_submit(
        hyp_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """假设提交 46号审批总线(决策面——
    off 409; L0 禁止/L1 低风险/L2 全域
    分级门控; submit_change 纯调用)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_evolution_service import (
        Pay69EvolutionService,
    )
    try:
        return await Pay69EvolutionService()\
            .submit_to_governance(hyp_id)
    except Exception as e:
        raise _map(e) from e


@router.post(
    "/evolution/hypothesis/{hyp_id}/reject")
async def evolution_hypothesis_reject(
        hyp_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """46号驳回留痕(假设 submitted→
    rejected; draft 版本随退——不受
    开关影响, 治理留痕人工动作)"""
    _require_admin(x_role)
    from services.pay69_evolution_service import (
        Pay69EvolutionService,
    )
    try:
        return await Pay69EvolutionService()\
            .mark_rejected(hyp_id)
    except Exception as e:
        raise _map(e) from e


@router.post(
    "/evolution/params/{version}/publish")
async def evolution_params_publish(
        version: int,
        body: VersionPublishBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """参数版本发布(决策面——off 409;
    46号审批通过后的显式动作; active
    互斥: 同参数旧版自动 retired)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_evolution_service import (
        Pay69EvolutionService,
    )
    try:
        return await Pay69EvolutionService()\
            .publish_version(
                version,
                shadow_first=body.shadowFirst)
    except Exception as e:
        raise _map(e) from e


@router.post(
    "/evolution/params/{version}/rollback")
async def evolution_params_rollback(
        version: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """版本回滚(决策面——off 409; 指定
    retired 历史版本→active, 当前
    active→retired——可回滚铁律)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 观测面不受影响)")
    from services.pay69_evolution_service import (
        Pay69EvolutionService,
    )
    try:
        return await Pay69EvolutionService()\
            .rollback_version(version)
    except Exception as e:
        raise _map(e) from e


@router.post("/evolution/kill")
async def evolution_kill(
        body: KillBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """紧急制动(admin 人工——不受开关
    影响; 数据面: 全 active/shadow 版本
    退役退回出厂; 进程级 PAY69_KILL=1
    由运维并行设置双保险)"""
    _require_admin(x_role)
    from services.pay69_evolution_service import (
        Pay69EvolutionService,
    )
    return await Pay69EvolutionService()\
        .kill_switch(body.activate)


@router.get("/evolution/hypotheses")
async def evolution_hypotheses(
        status: str | None = None,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """假设建议书视图(观测面)"""
    _require_admin(x_role)
    from services.pay69_evolution_service import (
        Pay69EvolutionService,
    )
    return await Pay69EvolutionService()\
        .hypotheses_view(
            status=status, limit=limit)


@router.get("/evolution/governance")
async def evolution_governance(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """L0-L2 分级治理观测(当前级/
    假设与版本统计——观测面)"""
    _require_admin(x_role)
    from services.pay69_evolution_service import (
        Pay69EvolutionService,
    )
    return await Pay69EvolutionService()\
        .governance_view()


# ============================================================
# P8 安全免疫(7 端点)
# ============================================================

class UnfreezeBody(BaseModel):
    by: str = Field(
        default="admin",
        description="解冻人(人工专属)")


@router.get("/immunity/dict")
async def immunity_dict(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """免疫字典公示(红队向量/冻结
    规则——观测面)"""
    _require_admin(x_role)
    from services.pay69_immunity_service import (
        Pay69ImmunityService,
    )
    return Pay69ImmunityService()\
        .immunity_dict()


@router.get("/immunity")
async def immunity_view(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """免疫看板(冻结状态+红队史——
    观测面)"""
    _require_admin(x_role)
    from services.pay69_immunity_service import (
        Pay69ImmunityService,
    )
    return await Pay69ImmunityService()\
        .immunity_view()


@router.post("/immunity/monitor")
async def immunity_monitor(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """分布监控+自动冻结(快环——不受
    开关影响; critical 通道≥2 或漂移
    信号≥3 自动冻结进化+告警)"""
    _require_admin(x_role)
    from services.pay69_immunity_service import (
        Pay69ImmunityService,
    )
    return await Pay69ImmunityService()\
        .monitor_and_freeze()


@router.post("/immunity/freeze")
async def immunity_freeze(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """人工冻结进化(admin 人工——不受
    开关影响; 安全方向动作, 全留痕)"""
    _require_admin(x_role)
    from services.pay69_immunity_service import (
        Pay69ImmunityService,
    )
    return await Pay69ImmunityService()\
        .freeze_evolution(
            "人工冻结(admin)", "admin")


@router.post("/immunity/unfreeze")
async def immunity_unfreeze(
        body: UnfreezeBody,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """解冻(人工专属——免疫自动永不
    解冻铁律; admin 显式动作)"""
    _require_admin(x_role)
    from services.pay69_immunity_service import (
        Pay69ImmunityService,
    )
    try:
        return await Pay69ImmunityService()\
            .unfreeze_evolution(body.by)
    except Exception as e:
        raise _map(e) from e


@router.post("/immunity/redteam")
async def immunity_redteam(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """红队四向量执行(决策面——off 409;
    RT-01 路由欺骗/RT-02 熵绕过/RT-03
    模板投毒/RT-04 胁迫伪造; 未防御
    向量→自动冻结进化)"""
    _require_admin(x_role)
    from services.pay69_registry import (
        current_mode,
    )
    if current_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="PAY69_MODE=off(默认 off——"
                  "决策面关闭, 红队无攻击面)")
    from services.pay69_immunity_service import (
        Pay69ImmunityService,
    )
    try:
        return await Pay69ImmunityService()\
            .run_redteam()
    except Exception as e:
        raise _map(e) from e


@router.get("/immunity/redteam/runs")
async def immunity_redteam_runs(
        limit: int = 20,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """红队批次历史(观测面)"""
    _require_admin(x_role)
    from repositories.pay69_repository import (
        Pay69Repository,
    )
    runs = await Pay69Repository()\
        .list_redteam_runs(limit=limit)
    return {
        "count": len(runs),
        "runs": runs,
    }
