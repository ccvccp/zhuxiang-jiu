"""数字人直播弹幕 QA 路由(蓝图对照分析近期项④, 2 端点)

鉴权:
    - 公开(1): 弹幕问答(直播中控/RPA 轮询调用, 只读无副作用)
    - 管理端(1): 统计汇总(X-Role: admin)

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
    """弹幕问答统计(admin; total/unsolved/filtered/cached/高频问top5)"""
    _require_admin(x_role)
    return {"code": 0, "msg": "ok", "data": _service.stats(room_id=roomId)}


def register_live_qa_routes(app) -> None:
    """注册路由(遵循项目注册模式)"""
    app.include_router(router)
