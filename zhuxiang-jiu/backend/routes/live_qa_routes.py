"""数字人直播弹幕 QA 路由(蓝图对照分析近期项④, 4 端点)

鉴权:
    - 公开(1): 弹幕问答(直播中控/RPA 轮询调用, 只读无副作用)
    - 管理端(3): 统计汇总 / 直播缺口列表 / 缺口转知识草稿(X-Role: admin)

异常映射(对齐 knowledge_routes 约定):
    - KeyError → 404(缺口不存在)
    - ValueError → 409(参数非法/知识治理拦截)
    - 权限校验 → 403

响应包裹形态对齐 knowledge_routes: {code: 0, msg: "ok", data: ...}。
"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.live_qa_service import LiveQaService

router = APIRouter()
_service = LiveQaService()


def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403(对齐 knowledge_routes 写法)"""
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射(对齐 knowledge_routes: KeyError→404/ValueError→409)"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=f"服务内部错误: {exc}")


class LiveQaAnswerRequest(PydBaseModel):
    """弹幕问答请求(对齐 knowledge_routes AskRequest 风格)"""
    roomId: str = Field(..., min_length=1, description="直播间ID")
    question: str = Field(..., description="弹幕问题(strip 后 1-200 字, 服务端复核)")


@router.post("/api/live-qa/answer", tags=["数字人直播弹幕QA"])
async def answer(
    data: LiveQaAnswerRequest,
):
    """弹幕问答(公开; 复用 knowledge RAG 规则轨, 只读无副作用)

    模式闸 LIVE_QA_MODE=off 时返回统一 disabled 话术;
    同直播间 60s 同问去重(命中 cached=True)。
    """
    return {"code": 0, "msg": "ok",
            "data": await _service.answer(data.roomId, data.question)}


@router.get("/api/live-qa/stats", tags=["数字人直播弹幕QA"])
async def stats(
    roomId: str = Query(None, description="按直播间过滤(缺省全量)"),
    x_role: str = Header(None, alias="X-Role"),
):
    """弹幕问答统计(admin; total/unsolved/filtered/cached/高频问top5
    + 小时分桶 hourlyBuckets/unsolved 率趋势 unsolvedRateTrend)"""
    _require_admin(x_role)
    return {"code": 0, "msg": "ok", "data": _service.stats(room_id=roomId)}


@router.get("/api/live-qa/gaps", tags=["数字人直播弹幕QA"])
async def gaps(
    limit: int = Query(100, ge=1, le=500,
                       description="返回条数上限(按频次倒序取前 N)"),
    x_role: str = Header(None, alias="X-Role"),
):
    """直播来源知识缺口列表(admin; unsolved 未解问题 best-effort
    record_gap 沉淀, 按 sessionId 前缀 live_qa: 过滤, 含频次/最近时间,
    按提问频次倒序——「unsolved 高→补条目」运营出口)"""
    _require_admin(x_role)
    try:
        rows = await _service.list_gaps(limit=limit)
        return {"code": 0, "msg": "ok",
                "data": {"total": len(rows), "gaps": rows}}
    except Exception as exc:
        _handle(exc)


class LiveQaGapToDraftRequest(PydBaseModel):
    """缺口转知识草稿请求(gapId 与 question 二选一)"""
    gapId: int = Field(0, description="缺口ID(优先, 精确匹配)")
    question: str = Field("", description="缺口问题文本(gapId 缺省时匹配)")


@router.post("/api/live-qa/gaps/to-draft", tags=["数字人直播弹幕QA"])
async def gap_to_draft(
    data: LiveQaGapToDraftRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """直播缺口转知识草稿(admin; 落 status=pending 进候选池,
    先审后显铁律——绝不直接 publish, 正式答案由运营补充后送审)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "msg": "ok",
                "data": await _service.gap_to_draft(
                    gap_id=data.gapId, question=data.question)}
    except Exception as exc:
        _handle(exc)


def register_live_qa_routes(app) -> None:
    """注册路由(遵循项目注册模式)"""
    app.include_router(router)
