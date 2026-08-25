"""AI 自学习层路由(13 管理端端点, v7.8 新增 enforcement 概览/审计)

为 14 个 AI 评分器的自学习闭环暴露管理接口:

    反馈标注 → 在线学习 → 冠军/挑战者评估 → 晋升/回滚 → 漂移监控 → 效果报表

鉴权:
    - 全部为管理端能力: X-Role: admin 头(与 AI 评分层管理端约定一致)

端点:
    GET  /api/ai-learning/overview            自学习状态总览(全部评分器)
    GET  /api/ai-learning/weights/{scorerId}  查看权重档案(冠军/挑战者/默认)
    PUT  /api/ai-learning/weights/{scorerId}  人工覆盖权重(立即生效)
    POST /api/ai-learning/feedback            提交决策反馈(真实结果标注)
    POST /api/ai-learning/learn/{scorerId}    触发一轮 Hedge 在线学习
    GET  /api/ai-learning/history/{scorerId}  版本历史(审计/回滚参考)
    POST /api/ai-learning/promote/{scorerId}  晋升挑战者为冠军
    POST /api/ai-learning/reset/{scorerId}    重置为默认权重
    GET  /api/ai-learning/drift/{scorerId}    漂移统计(EMA vs 基线)
    PUT  /api/ai-learning/config/{scorerId}   更新学习配置(eta/护栏等)
    GET  /api/ai-learning/report/{scorerId}   学习效果报表(v7.6: 版本正确率曲线)
    GET  /api/ai-learning/enforcement/{scorerId}/overview  阻断概览(v7.8: 模式/统计/熔断)
    GET  /api/ai-learning/enforcement/{scorerId}/audit     阻断决策审计(v7.8)

异常映射(项目约定):
    KeyError → 404(未知评分器) / ValueError → 409(业务冲突) / 其余 → 500
"""


from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

from services import ai_learning_service as svc

router = APIRouter()


# ============================================================
# 鉴权与异常映射(对齐项目约定)
# ============================================================

def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """KeyError → 404, ValueError → 409, 其余 → 500"""
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail=str(exc) or "资源不存在")
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class FeedbackFactorInput(PydBaseModel):
    """因子快照(与评分结果 factors 元素对齐)"""
    name: str = Field(..., description="因子名")
    score: float = Field(..., ge=0, le=100, description="因子分")
    weight: float | None = Field(None, ge=0, description="决策时权重")
    contribution: float | None = Field(None, description="风险贡献(score×weight)")


class FeedbackRequest(PydBaseModel):
    """决策反馈: 评分结果 + 真实结果标注"""
    scorerId: str = Field(..., description="评分器ID(如 order_risk)")
    factors: list[FeedbackFactorInput] = Field(..., min_length=1,
                                               description="决策时因子快照")
    scoreAtDecision: float = Field(..., ge=0, le=100, description="决策时总分")
    actualAction: str = Field(..., description="模型实际动作(如 block)")
    expectedAction: str | None = Field(
        None, description="期望动作(人工复核结论), 与 correct 二选一")
    correct: bool | None = Field(None, description="模型决策是否正确")
    note: str | None = Field(None, max_length=500, description="备注")
    weightVersion: str | None = Field(None, description="决策时权重版本")


class WeightsOverrideRequest(PydBaseModel):
    """人工覆盖权重"""
    weights: dict[str, float] = Field(..., description="因子→权重(需与默认因子集一致)")
    reason: str | None = Field(None, max_length=200, description="覆盖原因")


class LearningConfigRequest(PydBaseModel):
    """学习配置更新"""
    eta: float | None = Field(None, gt=0, le=5, description="Hedge 学习率")
    min_feedback: int | None = Field(None, ge=1, le=1000,
                                        description="触发学习的最小待学习反馈数")
    auto_apply: bool | None = Field(None, description="评估更优时自动晋升")
    guardrail: float | None = Field(None, ge=1.1, le=10,
                                       description="权重护栏倍数")


# ============================================================
# 端点
# ============================================================

@router.get("/api/ai-learning/overview", tags=["AI自学习层"])
async def learning_overview(x_role: str | None = Header(None, alias="X-Role")):
    """自学习状态总览: 全部评分器的冠军版本/挑战者/反馈量/漂移"""
    _require_admin(x_role)
    try:
        return await svc.overview()
    except Exception as exc:
        _handle(exc)


@router.get("/api/ai-learning/weights/{scorer_id}", tags=["AI自学习层"])
async def get_weights(scorer_id: str,
                      x_role: str | None = Header(None, alias="X-Role")):
    """查看权重档案: 冠军/挑战者/默认权重/学习配置/决策阈值"""
    _require_admin(x_role)
    try:
        return await svc.get_weights_view(scorer_id)
    except Exception as exc:
        _handle(exc)


@router.put("/api/ai-learning/weights/{scorer_id}", tags=["AI自学习层"])
async def override_weights(scorer_id: str, data: WeightsOverrideRequest,
                           x_role: str | None = Header(None, alias="X-Role")):
    """人工覆盖权重(立即生效为新冠军, 旧版本入历史)"""
    _require_admin(x_role)
    try:
        return await svc.manual_override_weights(
            scorer_id, data.weights, data.reason or "")
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-learning/feedback", tags=["AI自学习层"])
async def submit_feedback(data: FeedbackRequest,
                          x_role: str | None = Header(None, alias="X-Role")):
    """提交决策反馈(真实结果标注), 同时更新漂移统计"""
    _require_admin(x_role)
    try:
        payload = data.model_dump()
        payload["factors"] = [f.model_dump() for f in data.factors]
        return await svc.submit_feedback(payload)
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-learning/learn/{scorer_id}", tags=["AI自学习层"])
async def run_learning(scorer_id: str,
                       x_role: str | None = Header(None, alias="X-Role")):
    """触发一轮 Hedge 在线学习(待学习反馈 → 新挑战者/自动晋升)"""
    _require_admin(x_role)
    try:
        return await svc.run_learning_cycle(scorer_id)
    except Exception as exc:
        _handle(exc)


@router.get("/api/ai-learning/history/{scorer_id}", tags=["AI自学习层"])
async def get_history(scorer_id: str,
                      x_role: str | None = Header(None, alias="X-Role")):
    """查看权重版本历史(审计与回滚参考)"""
    _require_admin(x_role)
    try:
        return await svc.get_history(scorer_id)
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-learning/promote/{scorer_id}", tags=["AI自学习层"])
async def promote(scorer_id: str,
                  x_role: str | None = Header(None, alias="X-Role")):
    """晋升挑战者为冠军(人工决策通道)"""
    _require_admin(x_role)
    try:
        return await svc.promote_challenger(scorer_id)
    except Exception as exc:
        _handle(exc)


@router.post("/api/ai-learning/reset/{scorer_id}", tags=["AI自学习层"])
async def reset(scorer_id: str,
                x_role: str | None = Header(None, alias="X-Role")):
    """重置为默认权重(清除挑战者, 历史保留)"""
    _require_admin(x_role)
    try:
        return await svc.reset_weights(scorer_id)
    except Exception as exc:
        _handle(exc)


@router.get("/api/ai-learning/drift/{scorer_id}", tags=["AI自学习层"])
async def get_drift(scorer_id: str,
                    x_role: str | None = Header(None, alias="X-Role")):
    """查看漂移统计(因子分数 EMA 相对基线的偏离)"""
    _require_admin(x_role)
    try:
        return await svc.get_drift_view(scorer_id)
    except Exception as exc:
        _handle(exc)


@router.get("/api/ai-learning/report/{scorer_id}", tags=["AI自学习层"])
async def get_report(scorer_id: str,
                     x_role: str | None = Header(None, alias="X-Role")):
    """学习效果报表: 按权重版本聚合正确率 + 版本演进曲线 + 近期趋势"""
    _require_admin(x_role)
    try:
        return await svc.learning_report(scorer_id)
    except Exception as exc:
        _handle(exc)


@router.put("/api/ai-learning/config/{scorer_id}", tags=["AI自学习层"])
async def update_config(scorer_id: str, data: LearningConfigRequest,
                        x_role: str | None = Header(None, alias="X-Role")):
    """更新学习配置(学习率/最小反馈数/自动晋升/护栏)"""
    _require_admin(x_role)
    try:
        return await svc.update_learning_config(
            scorer_id, data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


# ============================================================
# 决策阻断(v7.8): 审计/统计只读端点
# ============================================================

@router.get("/api/ai-learning/enforcement/{scorer_id}/overview", tags=["AI自学习层"])
async def enforcement_overview(scorer_id: str,
                               x_role: str | None = Header(None, alias="X-Role")):
    """阻断运行概览: 当前模式/累计统计/熔断窗口/保护参数"""
    _require_admin(x_role)
    try:
        svc._require_scorer(scorer_id)   # 未知评分器 → 404
        from services import ai_enforcement
        return await ai_enforcement.enforcement_overview(scorer_id)
    except Exception as exc:
        _handle(exc)


@router.get("/api/ai-learning/enforcement/{scorer_id}/audit", tags=["AI自学习层"])
async def enforcement_audit(scorer_id: str, limit: int = 50,
                            x_role: str | None = Header(None, alias="X-Role")):
    """阻断决策审计记录(新→旧, 默认 50 条, 0=全部)"""
    _require_admin(x_role)
    try:
        svc._require_scorer(scorer_id)
        from repositories.ai_learning_repository import AiLearningRepository
        records = await AiLearningRepository().list_enforcement_audit(
            scorer_id, limit=max(0, limit))
        return {"success": True, "scorerId": scorer_id,
                "count": len(records), "records": records}
    except Exception as exc:
        _handle(exc)


@router.get("/api/ai-learning/kb/{scorer_id}", tags=["AI自学习层"])
async def kb_cases(scorer_id: str, limit: int = 50,
                   x_role: str | None = Header(None, alias="X-Role")):
    """案例知识库: 案例列表(新→旧, 默认 50 条, 0=全部) + 库存统计

    v7.9 知识库只读端点: 学习后反馈归档的案例(经验回放缓冲),
    供驾驶舱展示与 RAG 检索效果核对。
    """
    _require_admin(x_role)
    try:
        svc._require_scorer(scorer_id)
        from repositories.ai_knowledge_repository import (
            AiKnowledgeRepository, kb_enabled,
        )
        repo = AiKnowledgeRepository()
        cases = await repo.list_cases(scorer_id, limit=max(0, limit))
        total = await repo.count_cases(scorer_id)
        labeled = sum(1 for c in cases if c.get("correct") is not None)
        return {
            "success": True, "scorerId": scorer_id,
            "enabled": kb_enabled(),
            "count": len(cases), "totalCases": total,
            "labeledInView": labeled,
            "maxCasesPerScorer": repo.KB_MAX_CASES,
            "cases": cases,
        }
    except Exception as exc:
        _handle(exc)


def register_ai_learning_routes(app) -> None:
    """注册 AI 自学习层路由"""
    app.include_router(router)
