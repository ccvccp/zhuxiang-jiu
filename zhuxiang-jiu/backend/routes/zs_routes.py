"""智搜·AI智能搜索引擎大模型 路由(zs_routes)

端点:
    决策面(2, 公开+可选登录头增强角色):
        POST /api/search-ai/query        统一搜索入口
        POST /api/search-ai/feedback     显式反馈进化闭环(P1)
    观测面(5, admin):
        GET  /api/search-ai/status       大模型总览
        GET  /api/search-ai/intent-stats 意图分布
        GET  /api/search-ai/decisions    决策留痕
        GET  /api/search-ai/feedbacks    反馈留痕+进化参数(P1)
    控制面(2, admin):
        GET  /api/search-ai/mode         三态灰度总览
        POST /api/search-ai/mode/override 运行时切档(留痕)
"""

from fastapi import APIRouter, Header
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field

from services.zs_search_service import (
    ZsSearchService, current_mode, MODEL_VERSION,
)

router = APIRouter()
_service = ZsSearchService()


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


class QueryRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=200,
                      description="自然语言搜索内容")


class FeedbackRequest(BaseModel):
    decisionId: int = Field(..., description="被反馈的决策 ID")
    verdict: str = Field(..., description="useful(有用)/useless(没用)")


class ActionClickRequest(BaseModel):
    decisionId: int = Field(..., description="来源决策 ID")
    actionLabel: str = Field("", max_length=40,
                             description="被点击的动作卡标签")


# ============================================================
# 决策面(公开; 登录态经中间件注入 x-member-id/x-role 增强)
# ============================================================

@router.post("/api/search-ai/query", tags=["智搜AI智能搜索引擎大模型"])
async def query(
    data: QueryRequest,
    x_member_id: str = Header("", alias="X-Member-Id"),
    x_role: str = Header("", alias="X-Role"),
):
    """统一智能搜索入口(意图识别→合规→双路检索→结构化回答)

    游客可用; 登录后同问题获得角色化答案(权益/招商意图)。
    ZS_MODE=off 时 409(决策面门控)。
    """
    try:
        from services.zs_search_service import require_query_mode
        mode = await require_query_mode()
        try:
            mid = int(x_member_id) if x_member_id else 0
        except ValueError:
            mid = 0
        result = await _service.query(
            data.text, member_id=mid,
            role=x_role or "guest")
        result["zsMode"] = mode["mode"]
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/search-ai/feedback", tags=["智搜AI智能搜索引擎大模型"])
async def feedback(
    data: FeedbackRequest,
    x_member_id: str = Header("", alias="X-Member-Id"),
):
    """显式反馈进化闭环(P1)

    回答下方「有用/没用」→ routeBoost ±0.05(clamp [0.8,1.2]);
    合规拦截决策不参与进化(红线); 调整留痕可回滚。
    """
    try:
        try:
            mid = int(x_member_id) if x_member_id else 0
        except ValueError:
            mid = 0
        record = await _service.submit_feedback(
            data.decisionId, data.verdict, member_id=mid)
        return {"success": True, "data": record}
    except Exception as e:
        _handle(e)


@router.post("/api/search-ai/action-click",
             tags=["智搜AI智能搜索引擎大模型"])
async def action_click(data: ActionClickRequest):
    """隐式转化回流(P2)

    回答内动作卡片点击(跳商品/留资/查订单) → 转化正样本,
    routeBoost +0.03; 合规拦截决策不参与进化(红线同显式反馈)。
    """
    try:
        record = await _service.record_action_click(
            data.decisionId, action_label=data.actionLabel)
        return {"success": True, "data": record}
    except Exception as e:
        _handle(e)


# ============================================================
# 观测面(admin)
# ============================================================

@router.get("/api/search-ai/status", tags=["智搜AI智能搜索引擎大模型"])
async def status(x_role: str = Header(None, alias="X-Role")):
    """大模型总览(模式/查询量/拦截率/意图数)"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _service.status()}
    except Exception as e:
        _handle(e)


@router.get("/api/search-ai/intent-stats",
            tags=["智搜AI智能搜索引擎大模型"])
async def intent_stats(x_role: str = Header(None, alias="X-Role")):
    """意图命中分布(观测面)"""
    _require_admin(x_role)
    try:
        return {"success": True,
                "data": await _service.intent_stats()}
    except Exception as e:
        _handle(e)


@router.get("/api/search-ai/decisions",
            tags=["智搜AI智能搜索引擎大模型"])
async def decisions(x_role: str = Header(None, alias="X-Role"),
                    limit: int = 50):
    """决策留痕(意图/槽位/合规/结果数, 近 N 条)"""
    _require_admin(x_role)
    try:
        rows = await _service.decisions(limit=min(limit, 200))
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        _handle(e)


@router.get("/api/search-ai/feedbacks",
            tags=["智搜AI智能搜索引擎大模型"])
async def feedbacks(x_role: str = Header(None, alias="X-Role"),
                    limit: int = 50):
    """反馈留痕 + routeBoost 进化参数(P1 观测面)"""
    _require_admin(x_role)
    try:
        rows = await _service.feedbacks(limit=min(limit, 200))
        return {"success": True,
                "data": {"feedbacks": rows, "count": len(rows),
                         "routeBoost": await _service.evolution_params()},
                "modelVersion": MODEL_VERSION}
    except Exception as e:
        _handle(e)


# ============================================================
# 控制面(admin; 轻量三态——env + 运行时 override)
# ============================================================

@router.get("/api/search-ai/mode", tags=["智搜AI智能搜索引擎大模型"])
async def mode_status(x_role: str = Header(None, alias="X-Role")):
    """三态灰度总览(off/shadow/assist)"""
    _require_admin(x_role)
    return {"success": True, "data": await current_mode()}


@router.post("/api/search-ai/mode/override",
             tags=["智搜AI智能搜索引擎大模型"])
async def mode_override(
    mode: str = "",
    x_role: str = Header(None, alias="X-Role"),
):
    """运行时切档(空串清除回落 env; 留痕)"""
    _require_admin(x_role)
    if mode not in ("", "off", "shadow", "assist"):
        raise HTTPException(status_code=409,
                            detail="mode 须为 off/shadow/assist/空")
    from repositories.backend import is_redis_mode, get_redis_client
    if is_redis_mode():
        client = await get_redis_client()
        if mode:
            await client.set("zhuxiang:zs:mode_override", mode)
        else:
            await client.delete("zhuxiang:zs:mode_override")
    return {"success": True,
            "data": {**await current_mode(),
                     "note": "运行时切档留痕; MVP 轻量护栏",
                     "modelVersion": MODEL_VERSION}}


def register_zs_routes(app):
    """注册智搜·AI智能搜索引擎大模型路由"""
    app.include_router(router)
