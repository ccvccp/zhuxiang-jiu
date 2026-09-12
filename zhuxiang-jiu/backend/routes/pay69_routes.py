"""69号·AI智能支付大模型路由(P0)

端点(P0 6):
    GET  /api/pay69/channels            七通道字典公示(admin, 观测面)
    GET  /api/pay69/channels/{id}       单通道详情(admin, 观测面)
    GET  /api/pay69/health              健康度观测面(admin, 观测面)
    POST /api/pay69/health/report       通道健康度上报(admin, 快环观测——不受开关影响)
    POST /api/pay69/health/{id}/freeze  通道冻结/解冻(admin, 人工专属)
    GET  /api/pay69/intents             意图留痕视图(admin, 观测面)
    POST /api/pay69/intents/parse       意图标签解析+留痕(admin, 决策面 off 409)
    GET  /api/pay69/model/status        模型状态(admin, 观测面)

鉴权: 管理面 X-Role: admin(60号同款口径)。
统一口径(60号范式):
    - 观测面(channels/health/intents/
      model status)不受 PAY69_MODE 影响
    - 观测上报(health report)不受开关
      影响(纯统计基线——快环)
    - 决策面(intents/parse——产生可
      消费数据): off=拒绝(409)
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
