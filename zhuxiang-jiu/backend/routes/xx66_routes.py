"""66号·AI智能工程师大模块路由(P0 生命体征 + P1 支持引擎)

端点(观测面 4 + 决策面 4):
    GET  /api/xx66/status            模块状态(模式/评分器
                                     入册/快照量级; admin;
                                     观测面——永不关停)
    GET  /api/xx66/vitals            生命体征图谱(四区红黄绿
                                     +总评; admin; 纯读聚合)
    GET  /api/xx66/vitals/history   快照时序(观测面; admin)
    GET  /api/xx66/explain/{subject}
                                     透明化解释三件套
                                     (order/profile/rule;
                                     观测面——永不关停)
    POST /api/xx66/vitals/scan       主动巡检(决策面:
                                     聚合→落快照→黄/红区起
                                     27号自愈链(纯记录零执行);
                                     XX66_MODE off → 409)
    POST /api/xx66/support/chat      支持对话(决策面: 情绪轨
                                     ×三模式×评分门; off → 409)
    POST /api/xx66/support/vision    截图诊断(决策面: LLM 视觉
                                     ×规则域映射; off → 409)
    POST /api/xx66/support/settle    服务终态(满意度回填 1-5
                                     +≥4 授勋——回流通道,
                                     不受 MODE 影响(宪法口径))

鉴权: X-Role: admin(与 47号 hub/既有 xx 模块同款口径)。

统一口径:
    - 观测面永不关停(宪法); 决策面三态开关
      XX66_MODE: off(默认)/shadow/assist
    - KeyError → 404 / ValueError → 409(全站约定)
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

router = APIRouter(prefix="/api/xx66",
                   tags=["AI智能工程师(66号)"])


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")


@router.get("/status")
async def xx66_status(
    x_role: str = Header(default="", alias="X-Role"),
):
    """模块状态(模式/LLM 轨/评分器入册——观测面永不关停)"""
    _require_admin(x_role)
    try:
        from services.xx66_service import Xx66Service
        return await Xx66Service().status()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.get("/vitals")
async def xx66_vitals(
    x_role: str = Header(default="", alias="X-Role"),
):
    """生命体征图谱(四区: system/business/ai_services/trust
    红黄绿灯+总评——纯读聚合, fail-soft 分区)"""
    _require_admin(x_role)
    try:
        from services.xx66_service import Xx66Service
        return await Xx66Service().vitals()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.get("/vitals/history")
async def xx66_vitals_history(
    limit: int = 20,
    x_role: str = Header(default="", alias="X-Role"),
):
    """快照时序(最近 N 条——观测面永不关停)"""
    _require_admin(x_role)
    try:
        from services.xx66_service import Xx66Service
        return await Xx66Service().vitals_history(limit)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/vitals/scan")
async def xx66_vitals_scan(
    x_role: str = Header(default="", alias="X-Role"),
):
    """主动巡检(决策面: 真实聚合四源生成快照 + 黄/红区起
    27号自愈链(纯状态机记录零执行)——替代 inspect_all
    硬编码清单; XX66_MODE=off → 409)"""
    _require_admin(x_role)
    from services.xx66_service import Xx66Service
    try:
        return await Xx66Service().scan()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


# ============================================================
# P1 角色支持引擎
# ============================================================

class SupportChatRequest(PydBaseModel):
    """支持对话请求"""
    message: str = Field(..., min_length=1,
                         max_length=2000,
                         description="用户消息(先 PII 脱敏)")
    memberMeta: dict | None = Field(
        None, description="角色上下文 {registeredDays, "
                          "ticketCount, roleTier, memberId}")


class VisionRequest(PydBaseModel):
    """截图诊断请求"""
    imageUrl: str = Field(..., min_length=1,
                           max_length=500,
                           description="截图公网地址")
    mediaType: str = Field("image",
                           description="image|video")
    contextHint: str = Field("", max_length=500,
                             description="附加上下文提示")


class SettleRequest(PydBaseModel):
    """服务终态请求(满意度回填)"""
    statId: int = Field(..., ge=1,
                        description="chat 返回的 statId")
    satisfaction: int = Field(..., ge=1, le=5,
                              description="满意度 1-5")
    memberId: int | None = Field(
        None, description="授勋目标成员(满意度≥4 授勋)")


@router.post("/support/chat")
async def xx66_support_chat(
    data: SupportChatRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """支持对话(决策面: PII 脱敏→情绪轨(规则+LLM 双轨)
    →三模式沟通(安抚/教学/高效)→engineer_service 评分门
    →彩蛋愉悦组件→情绪匿名统计; XX66_MODE=off → 409)"""
    _require_admin(x_role)
    try:
        from services.xx66_support_service import (
            Xx66SupportService,
        )
        return await Xx66SupportService().chat(
            data.message, data.memberMeta)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/support/vision")
async def xx66_support_vision(
    data: VisionRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """截图诊断(决策面: LLM 视觉提取错误码/界面要素
    (双轨——LLM 不可用规则轨兜底)→域映射(支付/信值/订单)
    →确定性方案; XX66_MODE=off → 409)"""
    _require_admin(x_role)
    try:
        from services.xx66_support_service import (
            Xx66SupportService,
        )
        return await Xx66SupportService().vision_diagnose(
            data.imageUrl, data.mediaType, data.contextHint)
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/support/settle")
async def xx66_support_settle(
    data: SettleRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """服务终态(回流通道——不受 MODE 影响, 宪法口径:
    观测面/回流通道永不关停): 满意度回填 1-5(幂等)+
    满意度≥4 授虚拟勋章(纯展示; 小额信值奖励走 46号
    审批永不自动)"""
    _require_admin(x_role)
    try:
        from services.xx66_support_service import (
            Xx66SupportService,
        )
        return await Xx66SupportService().settle(
            data.statId, data.satisfaction, data.memberId)
    except KeyError as exc:
        msg = str(exc) if str(exc) else "情绪统计不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404,
                            detail=msg) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.get("/explain/{subject}")
async def xx66_explain(
    subject: str,
    orderId: int | None = None,
    trustId: int | None = None,
    question: str = "",
    x_role: str = Header(default="", alias="X-Role"),
):
    """透明化解释三件套(观测面——永不关停):
    order → 64号确定性重算三栏(计算过程/规则依据/历史记录);
    profile → 47号画像+复核通道指引;
    rule → 57号知识库检索"""
    _require_admin(x_role)
    try:
        from services.xx66_support_service import (
            Xx66SupportService,
        )
        return await Xx66SupportService().explain(
            subject, order_id=orderId, trust_id=trustId,
            question=question)
    except KeyError as exc:
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404,
                            detail=msg) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


def register_xx66_routes(app) -> None:
    app.include_router(router)
