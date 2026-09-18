"""织智·Synapse-Weave(76号)路由层

设计依据: 《Synapse-Weave(织智) 创新方案》工程化裁剪
——全站融合优化落地(四档灰度/护栏/决策门控/评分器入册)。

端点(15):
    观测面 GET(永不关停——人格经线/规则/指标为公开锚点):
        GET  /api/synapse/persona            人格经线公示
        GET  /api/synapse/router/rules       经纬权重规则
        GET  /api/synapse/metrics            织造统计
        GET  /api/synapse/weaves             织造留痕
        GET  /api/synapse/hotspots           热点源列表
        GET  /api/synapse/evolution          织补+语料总览
        GET  /api/synapse/diary              织智日记
        GET  /api/synapse/mode               灰度总览
    决策面 POST(@_decision 门控, off 返回 409):
        POST /api/synapse/weave              织机织造(核心)
        POST /api/synapse/hotspot/rewrite    热点人格化重写
        POST /api/synapse/evaluate           织造评估
        POST /api/synapse/feedback           反馈采集
    管理面(X-Role: admin——JWT 注入 x-role):
        POST /api/synapse/patch              织补式进化
        POST /api/synapse/corpus/crystallize  知识结晶
        POST /api/synapse/mode/override       运行时切档
        POST /api/synapse/mode/guard          护栏巡检
        POST /api/synapse/mode/resume         护栏恢复

鉴权惯例: strict 模式下 JWT 中间件注入 x-member-id/
x-role; 观测面无鉴权头要求(公开白名单 GET only)。
"""

import functools
import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.synapse_mode_service import (
    SynapseModeService,
)
from services.synapse_service import SynapseService

logger = logging.getLogger("synapse_routes")

router = APIRouter(tags=["织智Synapse-Weave大模型(76号)"])


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    logger.exception("synapse_internal_error")
    raise HTTPException(status_code=500,
                        detail="服务内部错误") from exc


# ============================================================
# 大模型四档灰度(全站范式·73/74/75 同源): 决策面门控
# ============================================================

def _decision(fn):
    """决策端点装饰器: 门控(off 409) + shadow/assist/full
    留痕(synapseMode) + full 档自主巡检(auto_patrol)

    决策面 = 新增织造入口(织造/热点重写/评估/反馈)——
    off 只锁生成; 人格与规则观测面永不关停。
    鉴权 401/403 优先于门控 409(JWT 中间件注入在前)。
    """
    from services.synapse_mode_service import MODE_VALUES

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            mode_svc = SynapseModeService()
            mode_state = await mode_svc \
                .require_decision_mode()
        except ValueError as e:
            raise HTTPException(
                status_code=409, detail=str(e)) from e
        result = await fn(*args, **kwargs)
        if isinstance(result, dict) \
                and mode_state.get("mode") in MODE_VALUES[1:]:
            result = {**result,
                      "synapseMode": mode_state["mode"]}
        # full 档自主巡检(L1 自主域 auto_patrol)
        if mode_state.get("mode") == "full":
            try:
                await mode_svc \
                    .note_decision_and_maybe_patrol()
            except Exception:
                logger.warning(
                    "synapse_auto_patrol_failed",
                    exc_info=True)
        return result
    return wrapper


# ============================================================
# 请求模型
# ============================================================

class WeaveRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=2000,
                      description="织造任务(含关键词供"
                                  "Router 判定任务域)")
    points: list[str] | None = Field(
        default=None, max_length=10,
        description="经线要点(缺省从任务分句提取)")
    hotspotId: str | None = Field(
        default=None, max_length=64,
        description="关联热点 ID(热点共生)")


class HotspotRewriteRequest(BaseModel):
    hotspotId: str = Field(..., max_length=64,
                           description="热点 ID")


class EvaluateRequest(BaseModel):
    weaveId: int = Field(..., ge=1,
                         description="织造 ID")


class FeedbackRequest(BaseModel):
    weaveId: int = Field(..., ge=1)
    feedbackType: str = Field(...,
                              description="like/dislike/"
                                          "rewrite/follow_up")


class PatchRequest(BaseModel):
    taskType: str = Field(..., min_length=1,
                          max_length=64,
                          description="损伤任务域")
    repairSamples: list[dict] = Field(
        ..., min_length=1, max_length=20,
        description="修复样本({points, expect})")


class CrystallizeRequest(BaseModel):
    weaveId: int = Field(..., ge=1)


class ModeOverrideRequest(BaseModel):
    mode: str = Field(...,
                      description="目标灰度态(off/shadow/"
                                  "assist/full; "
                                  "空串清除 override)")


class ResumeRequest(BaseModel):
    note: str | None = Field(default=None, max_length=200)


# ============================================================
# 观测面(永不关停)
# ============================================================

@router.get("/api/synapse/persona")
async def get_persona():
    """人格经线公示(人设/词表/红线——公开锚点)"""
    return await SynapseService().get_persona()


@router.get("/api/synapse/router/rules")
async def get_router_rules():
    """Meta-Router 经纬权重规则(任务域×权重)"""
    return await SynapseService().get_router_rules()


@router.get("/api/synapse/metrics")
async def get_metrics():
    """织造统计(护栏分布/路由分布/反馈)"""
    return await SynapseService().get_metrics()


@router.get("/api/synapse/weaves")
async def list_weaves(limit: int = 20):
    """织造留痕(最近 N 条)"""
    return await SynapseService().list_weaves(limit)


@router.get("/api/synapse/hotspots")
async def list_hotspots():
    """热点源列表"""
    return await SynapseService().list_hotspots()


@router.get("/api/synapse/evolution")
async def list_evolution(limit: int = 20):
    """织补式进化总览(织补史+黄金语料计数)"""
    return await SynapseService().list_evolution(limit)


@router.get("/api/synapse/diary")
async def get_diary(date: str = ""):
    """织智日记(品牌语言指标转译)"""
    try:
        return await SynapseService().get_diary_view(date)
    except Exception as exc:
        _handle(exc)


@router.get("/api/synapse/mode")
async def get_mode():
    """灰度总览(四档+护栏+full 自主域)"""
    return await SynapseModeService().status_view()


# ============================================================
# 决策面(@_decision 门控, off 返回 409)
# ============================================================

@router.post("/api/synapse/weave")
@_decision
async def weave(req: WeaveRequest):
    """织机织造(Meta-Router 权重 → 经纬交织 + 双维评分
    + PDS + 交叉验证)

    成功: {text, route{domain,weights}, scoreLogic,
          scoreHuman, pds, validation{passed}}
    拦截: validation.complianceHit 命中红线词(留痕)
    """
    try:
        return await SynapseService().weave(
            req.task, req.points,
            req.hotspotId or "")
    except Exception as exc:
        _handle(exc)


@router.post("/api/synapse/hotspot/rewrite")
@_decision
async def hotspot_rewrite(req: HotspotRewriteRequest):
    """热点人格化重写(事实骨架→人格织造→交叉验证)"""
    try:
        return await SynapseService().hotspot_rewrite(
            req.hotspotId)
    except Exception as exc:
        _handle(exc)


@router.post("/api/synapse/evaluate")
@_decision
async def evaluate(req: EvaluateRequest):
    """织造评估(SW-Eval——双维+PDS 复核)"""
    try:
        svc = SynapseService()
        rec = await svc.repo.get_weave(req.weaveId)
        if rec is None:
            raise KeyError(f"织造不存在: {req.weaveId}")
        scores = svc._score(rec)
        return {"success": True,
                "weaveId": req.weaveId, **scores}
    except Exception as exc:
        _handle(exc)


@router.post("/api/synapse/feedback")
@_decision
async def feedback(req: FeedbackRequest):
    """反馈采集(like/dislike/rewrite/follow_up)"""
    try:
        return await SynapseService().feedback(
            req.weaveId, req.feedbackType)
    except Exception as exc:
        _handle(exc)


# ============================================================
# 管理面(X-Role: admin——织补/结晶/切档/巡检/恢复)
# ============================================================

@router.post("/api/synapse/patch")
async def patch(req: PatchRequest,
                x_role: str | None = Header(
                    None, alias="X-Role")):
    """织补式进化(损伤定位→修复→缝合验证——建议项
    crystallize/rollback-review 由人工决策)"""
    _require_admin(x_role)
    try:
        return await SynapseService().patch(
            req.taskType, req.repairSamples)
    except Exception as exc:
        _handle(exc)


@router.post("/api/synapse/corpus/crystallize")
async def crystallize(req: CrystallizeRequest,
                      x_role: str | None = Header(
                          None, alias="X-Role")):
    """知识结晶(织造→黄金语料; 永不自主——人工显式)"""
    _require_admin(x_role)
    try:
        return await SynapseService().crystallize(
            req.weaveId)
    except Exception as exc:
        _handle(exc)


@router.post("/api/synapse/mode/override")
async def mode_override(req: ModeOverrideRequest,
                        x_role: str | None = Header(
                            None, alias="X-Role")):
    """运行时灰度切档(留痕; 空串清除 override)"""
    _require_admin(x_role)
    try:
        return await SynapseModeService().set_override(
            req.mode)
    except Exception as exc:
        _handle(exc)


@router.post("/api/synapse/mode/guard")
async def mode_guard(x_role: str | None = Header(
        None, alias="X-Role")):
    """护栏自动巡检(三指标聚合→恶化>3% 自动暂停)"""
    _require_admin(x_role)
    try:
        return await SynapseModeService().patrol()
    except Exception as exc:
        _handle(exc)


@router.post("/api/synapse/mode/resume")
async def mode_resume(req: ResumeRequest,
                      x_role: str | None = Header(
                          None, alias="X-Role")):
    """护栏恢复(人工解除自动暂停——决策留痕)"""
    _require_admin(x_role)
    try:
        return await SynapseModeService().resume(
            note=req.note or "")
    except Exception as exc:
        _handle(exc)


def register_synapse_routes(app):
    """注册织智·Synapse-Weave 路由到 FastAPI app"""
    app.include_router(router)
