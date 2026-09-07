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

from core.helpers import ts

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


# ============================================================
# P2 诊断与自愈编排
# ============================================================

class DiagnoseRequest(PydBaseModel):
    """根因分析请求"""
    recoveryId: int = Field(..., ge=1,
                            description="27号自愈记录 ID")


class HealRequest(PydBaseModel):
    """自愈编排请求"""
    recoveryId: int = Field(..., ge=1,
                            description="27号自愈记录 ID")


@router.post("/diagnose")
async def xx66_diagnose(
    data: DiagnoseRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """根因分析(决策面: 规则知识库+LLM 归因描述[不产
    数字]→engineer_log 留痕; XX66_MODE=off → 409)"""
    _require_admin(x_role)
    try:
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        return await Xx66HealService().diagnose(
            data.recoveryId)
    except KeyError as exc:
        msg = str(exc) if str(exc) else "自愈记录不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404,
                            detail=msg) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/heal")
async def xx66_heal(
    data: HealRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """自愈编排(决策面: 27号 diagnose/attempt 补链
    +动作白名单+沙箱预演; manual 级强制人工; shadow 态
    建议书留痕; assist 态经预演走 27号任务轨——纯状态机
    记录零执行; XX66_MODE=off → 409)"""
    _require_admin(x_role)
    try:
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        return await Xx66HealService().heal(
            data.recoveryId)
    except KeyError as exc:
        msg = str(exc) if str(exc) else "自愈记录不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404,
                            detail=msg) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/predict")
async def xx66_predict(
    x_role: str = Header(default="", alias="X-Role"),
):
    """故障预测扫描(决策面: 特征工程×三条件与预警——
    确定性零 ML; 特征全量留痕可复现;
    XX66_MODE=off → 409)"""
    _require_admin(x_role)
    try:
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        return await Xx66HealService().predict()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.get("/predictions")
async def xx66_predictions(
    limit: int = 20,
    x_role: str = Header(default="", alias="X-Role"),
):
    """预测预警列表(观测面——永不关停)"""
    _require_admin(x_role)
    from repositories.xx66_repository import (
        Xx66Repository,
    )
    rows = await Xx66Repository().list_predictions(
        limit=max(1, min(int(limit or 20), 100)))
    return {"success": True, "count": len(rows),
            "predictions": rows,
            "generatedAt": ts()}


@router.get("/log/verify")
async def xx66_log_verify(
    x_role: str = Header(default="", alias="X-Role"),
):
    """指纹链完整性校验(观测面——逐条 prev_hash 串联
    核对, 防篡改可审计)"""
    _require_admin(x_role)
    from services.xx66_heal_service import Xx66HealService
    return await Xx66HealService().verify_log_chain()


# ============================================================
# P3 信值安全+补偿
# ============================================================

@router.post("/reconcile/run")
async def xx66_reconcile_run(
    x_role: str = Header(default="", alias="X-Role"),
):
    """执行对账(决策面: 四不变式全量只读校验+差异
    分级——danger 只生成建议书永不自动冻结;
    XX66_MODE=off → 409)"""
    _require_admin(x_role)
    try:
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        return await Xx66ReconService().run_recon()
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.get("/reconcile/report")
async def xx66_reconcile_report(
    x_role: str = Header(default="", alias="X-Role"),
):
    """对账报告(观测面——最近轮次+历史)"""
    _require_admin(x_role)
    from services.xx66_recon_service import (
        Xx66ReconService,
    )
    return await Xx66ReconService().recon_report()


class ReversalProposeRequest(PydBaseModel):
    """冲正建议书请求"""
    runId: int = Field(..., ge=1)
    trustId: int = Field(..., ge=1)


class AdviceIdRequest(PydBaseModel):
    """建议书执行请求"""
    adviceId: int = Field(..., ge=1)


@router.post("/reconcile/reversal/propose")
async def xx66_reversal_propose(
    data: ReversalProposeRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """冲正建议书(决策面: 基于对账 I1 差异生成
    direction/amount/reserve_ref 锚定; off → 409)"""
    _require_admin(x_role)
    try:
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        return await Xx66ReconService() \
            .propose_reversal(data.runId, data.trustId)
    except KeyError as exc:
        msg = str(exc) if str(exc) else "对账轮次不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404,
                            detail=msg) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/reconcile/reversal/apply")
async def xx66_reversal_apply(
    data: AdviceIdRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """执行冲正(前置 46号 approve——建议书状态
    approved 才可执行; 45号 issue 轨锚定发行)"""
    _require_admin(x_role)
    try:
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        return await Xx66ReconService() \
            .apply_reversal(data.adviceId)
    except KeyError as exc:
        msg = str(exc) if str(exc) else "建议书不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404,
                            detail=msg) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


class CompensationProposeRequest(PydBaseModel):
    """补偿建议书请求"""
    ruleId: str = Field(..., description=(
        "trust_misdeduct|downtime_loss|ticket_severe"))
    entityId: str = Field(..., min_length=1,
                          description="申请实体(幂等域)")
    incidentId: str = Field(
        None, max_length=100,
        description="事故事件 ID(幂等键, 与 ticketNo 二选一)")
    ticketNo: str = Field(
        None, max_length=100,
        description="工单号(幂等键, 与 incidentId 二选一)")
    lossAmount: float = Field(
        0.0, ge=0, description="损失额(锚定基数)")
    affectedUsers: int = Field(
        0, ge=0, description="受影响角色数(宕机规则)")
    roleTier: str = Field(
        "standard", description="47号 tier")
    emotionBand: str = Field(
        "calm", description="情绪档(附加系数)")
    deviceFingerprint: str = Field(
        None, max_length=100,
        description="设备指纹(多账号检测)")


@router.post("/compensation/propose")
async def xx66_compensation_propose(
    data: CompensationProposeRequest,
    x_role: str = Header(default="", alias="X-Role"),
):
    """补偿建议书(决策面: DSL 确定性求值→反欺诈门
    (幂等/连环/多账号/封顶/tier 终审)→留痕;
    执行统一 46号审批——永不自动; off → 409)"""
    _require_admin(x_role)
    try:
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        svc = Xx66ReconService()
        return await svc.propose_compensation(
            data.ruleId,
            {"entityId": data.entityId,
             "incidentId": data.incidentId,
             "ticketNo": data.ticketNo,
             "lossAmount": data.lossAmount,
             "affectedUsers": data.affectedUsers,
             "roleTier": data.roleTier,
             "emotionBand": data.emotionBand,
             "deviceFingerprint":
                 data.deviceFingerprint})
    except KeyError as exc:
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404,
                            detail=msg) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.get("/compensation/{advice_id}")
async def xx66_compensation_detail(
    advice_id: int,
    x_role: str = Header(default="", alias="X-Role"),
):
    """补偿详情(观测面——计算依据展开: base/tier 乘数/
    情绪附加/封顶/DSL 版本, 透明化可审计)"""
    _require_admin(x_role)
    try:
        from services.xx66_recon_service import (
            Xx66ReconService,
        )
        return await Xx66ReconService() \
            .get_compensation(advice_id)
    except KeyError as exc:
        msg = str(exc) if str(exc) else "建议书不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404,
                            detail=msg) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/advice/{advice_id}/approve")
async def xx66_advice_approve(
    advice_id: int,
    x_role: str = Header(default="", alias="X-Role"),
):
    """建议书审批状态桥(46号 approve 效果对接点——
    P3 以状态 approved 表征审批通过; P4 切换为
    46号 submit_change 真轨; 不可逆状态推进)"""
    _require_admin(x_role)
    from repositories.xx66_repository import (
        Xx66Repository,
    )

    async def _bridge():
        book = await Xx66Repository() \
            .get_advice_book(advice_id)
        if book is None:
            raise KeyError(f"建议书 {advice_id} 不存在")
        if book.get("status") in ("executed",):
            raise ValueError(
                f"建议书已执行(状态 {book['status']}"
                f" 不可逆)")
        await Xx66Repository() \
            .update_advice_status(advice_id, "approved")
        return {"success": True,
                "adviceId": advice_id,
                "status": "approved",
                "note": "46号 approve 效果桥——P4 切换"
                        "submit_change 真轨",
                "approvedAt": ts()}
    try:
        return await _bridge()
    except KeyError as exc:
        msg = str(exc) if str(exc) else "建议书不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404,
                            detail=msg) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


def register_xx66_routes(app) -> None:
    app.include_router(router)
