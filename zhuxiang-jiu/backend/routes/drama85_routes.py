"""85号·短剧智能研判引擎路由(12 端点)

写操作(采集/改编/生成/回流/合规检查) admin 鉴权;
读操作(候选排行/知识库/进化/看板) 公开——引流侧前端可直读。
DRAMA85_MODE=off 全端点 409(总开关, 对齐 84号范式)。
"""
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.drama85_intel_service import (
    Drama85IntelService, drama85_mode,
)

router = APIRouter()
_service = Drama85IntelService()


def _require_admin(x_role: str | None) -> None:
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _gate() -> None:
    if drama85_mode() == "off":
        raise HTTPException(
            status_code=409,
            detail="短剧研判引擎已关闭(DRAMA85_MODE=off)")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) or "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


class CollectRequest(PydBaseModel):
    n: int = Field(20, ge=1, le=100, description="本次采集条数")
    offset: int = Field(0, ge=0, description="数据源偏移(翻页)")


class AdaptRequest(PydBaseModel):
    variants: int = Field(2, ge=1, le=2, description="A/B 版本数")


class ScriptRequest(PydBaseModel):
    adaptId: str = Field(..., description="改编方案ID(/adapt 返回)")
    episodes: int = Field(6, ge=4, le=80, description="集数(84号规格)")
    variant: str = Field("A", description="A/B 开场版本")


class ComplianceRequest(PydBaseModel):
    text: str = Field(..., min_length=1, max_length=5000,
                      description="待检文本")


class FeedbackRequest(PydBaseModel):
    adaptId: str = Field(..., description="改编方案ID")
    plays: int = Field(..., ge=0, description="播放量")
    completionRate: float = Field(..., ge=0, le=1,
                                  description="完播率(0-1)")
    likes: int = Field(0, ge=0, description="点赞数")
    variant: str | None = Field(None, description="A/B 版本(缺省取脚本版本)")


@router.post("/api/drama85/collect", tags=["85号短剧智能研判引擎"])
async def run_collect(
    data: CollectRequest = None,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """触发采集管道(mock 确定性数据源→清洗→标签→爆款指数→入池)"""
    _gate()
    _require_admin(x_role)
    data = data or CollectRequest()
    try:
        result = await _service.run_collect(
            n=data.n, offset=data.offset)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/drama85/dramas", tags=["85号短剧智能研判引擎"])
async def list_dramas(
    theme: str | None = Query(None, description="题材筛选"),
    minQuality: float = Query(0.0, ge=0, le=1,
                              description="质量分下限(剔刷量)"),
    limit: int = Query(50, ge=1, le=200),
):
    """采集池列表(按爆款指数降序)"""
    _gate()
    try:
        items = await _service.repo.list_dramas(
            theme=theme, min_quality=minQuality, limit=limit)
        return {"success": True, "data": items, "count": len(items)}
    except Exception as e:
        _handle(e)


@router.get("/api/drama85/dramas/{drama_id}",
            tags=["85号短剧智能研判引擎"])
async def get_drama(drama_id: str):
    """短剧详情(四段结构+标签+指数分解)"""
    _gate()
    try:
        drama = await _service.repo.get_drama(drama_id)
        if drama is None:
            raise KeyError(f"短剧不存在({drama_id})")
        return {"success": True, "data": drama}
    except Exception as e:
        _handle(e)


@router.get("/api/drama85/hot-index", tags=["85号短剧智能研判引擎"])
async def hot_index_ranking(
    top: int = Query(50, ge=1, le=200),
):
    """改编优先级列表(Top N 候选: 指数≥60 且风险≤30 可入改编)"""
    _gate()
    try:
        items = await _service.hot_index_ranking(top)
        return {"success": True, "data": items, "count": len(items)}
    except Exception as e:
        _handle(e)


@router.post("/api/drama85/adapt/{drama_id}",
             tags=["85号短剧智能研判引擎"])
async def derive_adaptation(
    drama_id: str,
    data: AdaptRequest = None,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """生成改编策略(A/B 双开场, 结构规律保留+内容全替换)"""
    _gate()
    _require_admin(x_role)
    data = data or AdaptRequest()
    try:
        result = await _service.derive_adaptation(
            drama_id, variants=data.variants)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/drama85/script", tags=["85号短剧智能研判引擎"])
async def generate_script(
    data: ScriptRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """改编→84号连续剧本引擎(需先 POST /adapt 拿 adaptId)"""
    _gate()
    _require_admin(x_role)
    try:
        adapt_id = (data.model_dump().get("adaptId") or "")
        if not adapt_id:
            raise ValueError("缺少 adaptId(先调 /adapt/{dramaId})")
        result = await _service.generate_script(
            adapt_id, episodes=data.episodes,
            variant=data.variant)
        return {"success": True, "data": {
            k: v for k, v in result.items() if k != "series"}}
    except Exception as e:
        _handle(e)


@router.get("/api/drama85/adapts", tags=["85号短剧智能研判引擎"])
async def list_adapts(limit: int = Query(50, ge=1, le=200)):
    """改编方案列表(状态 draft/scripted/testing/evolved)"""
    _gate()
    try:
        items = await _service.repo.list_adapts(limit)
        return {"success": True, "data": items, "count": len(items)}
    except Exception as e:
        _handle(e)


@router.get("/api/drama85/adapts/{adapt_id}",
            tags=["85号短剧智能研判引擎"])
async def get_adapt(adapt_id: str):
    """改编方案详情(策略+A/B+回流记录)"""
    _gate()
    try:
        adapt = await _service.repo.get_adapt(adapt_id)
        if adapt is None:
            raise KeyError(f"改编方案不存在({adapt_id})")
        return {"success": True, "data": adapt}
    except Exception as e:
        _handle(e)


@router.get("/api/drama85/kb", tags=["85号短剧智能研判引擎"])
async def kb_overview():
    """知识库四库概览(题材/爽点模式/人物场景/合规规则)"""
    _gate()
    try:
        return {"success": True, "data": await _service.kb_overview()}
    except Exception as e:
        _handle(e)


@router.get("/api/drama85/kb/search", tags=["85号短剧智能研判引擎"])
async def kb_search(
    q: str = Query(..., min_length=2, description="检索词"),
    kbType: str | None = Query(None,
                               description="限定库(theme/hook_pattern/persona_scene/compliance)"),
    top: int = Query(5, ge=1, le=20),
):
    """知识库检索(轻量关键词重叠; embedding 轨预留)"""
    _gate()
    try:
        items = await _service.kb_search(
            q, kb_type=kbType, top_k=top)
        return {"success": True, "data": items, "count": len(items)}
    except Exception as e:
        _handle(e)


@router.post("/api/drama85/compliance/check",
             tags=["85号短剧智能研判引擎"])
async def compliance_check(
    data: ComplianceRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """文本合规检查(低俗/暴力/绝对化用语/功效宣称/未成年)"""
    _gate()
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _service.compliance_check(data.text)}
    except Exception as e:
        _handle(e)


@router.post("/api/drama85/feedback", tags=["85号短剧智能研判引擎"])
async def submit_feedback(
    data: FeedbackRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """发布数据回流(A/B 效果→题材权重/钩子策略更新+进化日志)"""
    _gate()
    _require_admin(x_role)
    adapt_id = data.model_dump().get("adaptId") or ""
    try:
        if not adapt_id:
            raise ValueError("缺少 adaptId")
        result = await _service.submit_feedback(
            adapt_id, plays=data.plays,
            completion_rate=data.completionRate,
            likes=data.likes, variant=data.variant)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/drama85/evolution", tags=["85号短剧智能研判引擎"])
async def evolution(
    limit: int = Query(50, ge=1, le=200),
):
    """进化日志(回流触发记录, 版本管理审计)"""
    _gate()
    try:
        items = await _service.evolution_log(limit)
        return {"success": True, "data": items, "count": len(items)}
    except Exception as e:
        _handle(e)


@router.get("/api/drama85/strategy", tags=["85号短剧智能研判引擎"])
async def get_strategy():
    """当前策略权重(题材热度调节×钩子强度, 版本化)"""
    _gate()
    try:
        return {"success": True,
                "data": await _service.get_strategy()}
    except Exception as e:
        _handle(e)


@router.get("/api/drama85/stats", tags=["85号短剧智能研判引擎"])
async def stats():
    """引擎看板(池量/候选/改编/题材分布/策略版本)"""
    _gate()
    try:
        return {"success": True, "data": await _service.stats()}
    except Exception as e:
        _handle(e)


def register_drama85_routes(app):
    """注册 85号路由"""
    app.include_router(router)
