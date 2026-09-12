"""智客·AI智能会员大模型路由(P0-P3 全量, 20 端点)

会员管理模块叠加升级: 智客·AI智能会员大模型(会员智能运营中枢)。
鉴权: 全部管理端 X-Role: admin(会员运营敏感域)。
既有 /api/member/* 14 端点保留零改动(叠加式升级)。

端点分布(/api/member-ai/*):
    - P0 洞察中枢: status / overview / qa / health/{memberId}
                   / portrait/{memberId} / portraits
    - P1 留存引擎: churn-scan / churns / ltv/{memberId} / sandbox
    - P2 权益运营: benefit-match/{memberId} / points-analysis
                   / wakeup-suggest / wakeups
    - P3 进化闭环: feedback / feedbacks / params / detect
                   / memo / memos

铁律: 全部确定性规则引擎(LLM 禁入); 建议书永不自动执行;
不输出信值五维与 TV 资产(68 号雷达域)。

异常映射: KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.zk_fabric_service import _ZkStore, ZkFabricService
from services.zk_insight_service import ZkInsightService
from services.zk_retention_service import ZkRetentionService
from services.zk_operation_service import ZkOperationService
from services.zk_evolution_service import ZkEvolutionService

router = APIRouter()
_store = _ZkStore()
_fabric = ZkFabricService(store=_store)
_insight = ZkInsightService(fabric=_fabric, store=_store)
_retention = ZkRetentionService(fabric=_fabric, store=_store)
_operation = ZkOperationService(fabric=_fabric, insight=_insight,
                                store=_store)
_evolution = ZkEvolutionService(fabric=_fabric, store=_store)


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class QARequest(PydBaseModel):
    text: str = Field(..., min_length=1, max_length=200,
                      description="自然语言会员问题")


class SandboxRequest(PydBaseModel):
    memberId: int = Field(..., ge=1)
    consumeDelta: float = Field(0.0, ge=-0.9, le=3.0,
                                description="未来月消费变动(0.1=+10%)")
    growthDelta: int = Field(0, ge=0, le=99_999,
                             description="一次性成长值加成")


class FeedbackRequest(PydBaseModel):
    targetType: str = Field(..., description="ltv/churn_scan/wakeup/"
                                             "benefit_match/sandbox/portrait")
    verdict: str = Field(..., description="adopted/corrected/rejected")
    note: str = Field("", max_length=200)


class MemoRequest(PydBaseModel):
    topic: str = Field(..., description="member_day/level_threshold")
    notes: str = Field("", max_length=200)


# ============================================================
# P0: 洞察中枢(admin)
# ============================================================

@router.get("/api/member-ai/status", tags=["智客AI智能会员大模型"])
async def status(x_role: str = Header(None, alias="X-Role")):
    """服务状态与能力声明"""
    _require_admin(x_role)
    return {"success": True, "data": {
        "service": "智客·AI智能会员大模型",
        "phase": "P0-P3 全量上线",
        "capabilities": ["NL问答", "健康度五维", "RFM画像",
                         "三信号流失预警", "LTV预测", "等级沙盘",
                         "权益匹配", "积分运营", "沉睡唤醒",
                         "反馈学习", "三检测器", "决策备忘"],
        "rules": ["全部确定性规则引擎(LLM 禁入判定链)",
                  "数字 100% 来自织物查询层",
                  "建议书模式: 运营策略永不自动执行",
                  "推理链留痕: 评分/预测带 formula",
                  "边界红线: 只做消费频次/等级生命周期/积分行为/"
                  "运营决策, 雷达五维与资产评分不在此域输出"],
        "tables": sorted(k for k in _store._store
                         if k.startswith("zk_")),
        "status": "ok",
    }}


@router.get("/api/member-ai/overview", tags=["智客AI智能会员大模型"])
async def overview(x_role: str = Header(None, alias="X-Role")):
    """会员总览(量/等级分布/消费/积分/注册时序)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _fabric.overview()}
    except Exception as e:
        _handle(e)


@router.post("/api/member-ai/qa", tags=["智客AI智能会员大模型"])
async def qa(data: QARequest,
             x_role: str = Header(None, alias="X-Role")):
    """自然语言会员问答(五域关键词路由, 数字全插值)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _insight.qa(data.text)}
    except Exception as e:
        _handle(e)


@router.get("/api/member-ai/health/{member_id}",
            tags=["智客AI智能会员大模型"])
async def health(member_id: int,
                 x_role: str = Header(None, alias="X-Role")):
    """会员健康度五维评分(Sigmoid, 带 formula)"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _insight.health(member_id)}
    except Exception as e:
        _handle(e)


@router.get("/api/member-ai/portrait/{member_id}",
            tags=["智客AI智能会员大模型"])
async def portrait(member_id: int,
                   x_role: str = Header(None, alias="X-Role")):
    """单会员 RFM 确定性分层"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _insight.portrait(member_id)}
    except Exception as e:
        _handle(e)


@router.get("/api/member-ai/portraits", tags=["智客AI智能会员大模型"])
async def portraits(x_role: str = Header(None, alias="X-Role")):
    """全量会员 RFM 分层列表"""
    _require_admin(x_role)
    try:
        rows = await _insight.portraits()
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


# ============================================================
# P1: 留存引擎(admin)
# ============================================================

@router.get("/api/member-ai/churn-scan", tags=["智客AI智能会员大模型"])
async def churn_scan(x_role: str = Header(None, alias="X-Role")):
    """三信号流失预警全量扫描(红/黄/绿分级)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _retention.churn_scan()}
    except Exception as e:
        _handle(e)


@router.get("/api/member-ai/churns", tags=["智客AI智能会员大模型"])
async def churns(x_role: str = Header(None, alias="X-Role"),
                 limit: int = Query(50, ge=1, le=200)):
    """流失预警留痕列表(按风险分降序)"""
    _require_admin(x_role)
    try:
        rows = await _retention.churns(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/member-ai/ltv/{member_id}",
            tags=["智客AI智能会员大模型"])
async def ltv(member_id: int,
              x_role: str = Header(None, alias="X-Role")):
    """会员 LTV 预测(确定性公式 + 假设标注)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _retention.ltv(member_id)}
    except Exception as e:
        _handle(e)


@router.post("/api/member-ai/sandbox", tags=["智客AI智能会员大模型"])
async def sandbox(data: SandboxRequest,
                  x_role: str = Header(None, alias="X-Role")):
    """等级生命周期 What-if 推演(建议书)"""
    _require_admin(x_role)
    try:
        result = await _retention.sandbox(
            member_id=data.memberId,
            consume_delta=data.consumeDelta,
            growth_delta=data.growthDelta)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# P2: 权益运营(admin)
# ============================================================

@router.get("/api/member-ai/benefit-match/{member_id}",
            tags=["智客AI智能会员大模型"])
async def benefit_match(member_id: int,
                        x_role: str = Header(None, alias="X-Role")):
    """L1-L5 差异化权益建议(等级×RFM, 建议书)"""
    _require_admin(x_role)
    try:
        result = await _operation.benefit_match(member_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/member-ai/points-analysis",
            tags=["智客AI智能会员大模型"])
async def points_analysis(x_role: str = Header(None, alias="X-Role"),
                          memberId: int = Query(None, ge=1)):
    """积分运营分析(单会员/全量, 确定性统计)"""
    _require_admin(x_role)
    try:
        result = await _operation.points_analysis(memberId)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/member-ai/wakeup-suggest",
            tags=["智客AI智能会员大模型"])
async def wakeup_suggest(x_role: str = Header(None, alias="X-Role"),
                         level: int = Query(None, ge=1, le=5)):
    """沉睡会员分级触达建议书(永不自动发送)"""
    _require_admin(x_role)
    try:
        result = await _operation.wakeup_suggest(level)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/member-ai/wakeups", tags=["智客AI智能会员大模型"])
async def wakeups(x_role: str = Header(None, alias="X-Role"),
                  limit: int = Query(50, ge=1, le=200)):
    """唤醒建议书留痕列表"""
    _require_admin(x_role)
    try:
        rows = await _operation.wakeups(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


# ============================================================
# P3: 进化闭环(admin)
# ============================================================

@router.post("/api/member-ai/feedback", tags=["智客AI智能会员大模型"])
async def feedback(data: FeedbackRequest,
                   x_role: str = Header(None, alias="X-Role")):
    """反馈闭环(参数权重学习, clamp 安全阀)"""
    _require_admin(x_role)
    try:
        result = await _evolution.feedback(
            target_type=data.targetType, verdict=data.verdict,
            note=data.note)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/member-ai/feedbacks", tags=["智客AI智能会员大模型"])
async def feedbacks(x_role: str = Header(None, alias="X-Role"),
                    limit: int = Query(50, ge=1, le=200)):
    """反馈留痕列表"""
    _require_admin(x_role)
    try:
        rows = await _evolution.feedbacks(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/member-ai/params", tags=["智客AI智能会员大模型"])
async def params(x_role: str = Header(None, alias="X-Role")):
    """当前可学习参数视图"""
    _require_admin(x_role)
    return {"success": True, "data": await _evolution.params()}


@router.get("/api/member-ai/detect", tags=["智客AI智能会员大模型"])
async def detect(x_role: str = Header(None, alias="X-Role")):
    """三检测器扫描(消费 spike/积分 drop/注册 surge)"""
    _require_admin(x_role)
    try:
        return {"success": True, "data": await _evolution.detect()}
    except Exception as e:
        _handle(e)


@router.post("/api/member-ai/memo", tags=["智客AI智能会员大模型"])
async def memo(data: MemoRequest,
               x_role: str = Header(None, alias="X-Role")):
    """决策备忘录(模板+数据插值+假设标注)"""
    _require_admin(x_role)
    try:
        result = await _evolution.memo(topic=data.topic,
                                       notes=data.notes)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/member-ai/memos", tags=["智客AI智能会员大模型"])
async def memos(x_role: str = Header(None, alias="X-Role"),
                limit: int = Query(50, ge=1, le=200)):
    """决策备忘录列表"""
    _require_admin(x_role)
    try:
        rows = await _evolution.memos(limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


def register_zk_routes(app):
    """注册智客·AI智能会员大模型路由"""
    app.include_router(router)
