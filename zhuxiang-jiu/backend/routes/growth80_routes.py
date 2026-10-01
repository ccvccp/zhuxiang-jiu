"""80号·全域会员智能增长路由(分享积分轨)

守卫: X-Member-Id(会员身份, 同 promotion_routes 惯例)
分段: 分享积分经 settings 开关控制(sharePointsPerAction=0 关)
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.growth80_service import Growth80Service

router = APIRouter()


class ShareReportRequest(BaseModel):
    itemType: str = Field("product", max_length=20,
                          description="分享内容类型: product/promo_code/content")
    itemId: str = Field("", max_length=40, description="内容标识(产品ID/推广码等)")


def _require_member_id(x_member_id: str | None) -> int:
    if not x_member_id:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        return int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="会员身份无效")


@router.post("/api/growth80/share", tags=["全域会员智能增长(80号)"])
async def report_share(
    body: ShareReportRequest,
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """分享事件上报(计分: 日限内每次 N 分, 同内容当日一次)"""
    member_id = _require_member_id(x_member_id)
    try:
        result = await Growth80Service().report_share(
            member_id, item_type=body.itemType, item_id=body.itemId)
        return {"success": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/growth80/share/today", tags=["全域会员智能增长(80号)"])
async def share_today(
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """今日分享计分情况(前端展示剩余次数/每次积分)"""
    member_id = _require_member_id(x_member_id)
    return {"success": True, **(await Growth80Service()
                                .today_share_count(member_id))}


class ShareCopyRequest(BaseModel):
    itemType: str = Field("product", max_length=20,
                          description="分享内容类型: product/promo_code/content")
    itemId: str = Field("", max_length=40, description="内容标识(产品ID/推广码等)")
    channel: str = Field("direct", max_length=24,
                         description="分享平台: wechat_miniprogram/douyin/kuaishou/"
                                     "xiaohongshu/bilibili/taobao/direct")


@router.post("/api/growth80/share/copy", tags=["全域会员智能增长(80号)"])
async def share_copy(
    body: ShareCopyRequest,
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """LLM 动态引流文案(P2-a: 按平台+内容生成, 缓存 24h,
    LLM 失败回退静态模板; 只读不涉积分)"""
    member_id = _require_member_id(x_member_id)
    result = await Growth80Service().generate_share_copy(
        member_id, item_type=body.itemType, item_id=body.itemId,
        channel=body.channel)
    return {"success": True, **result}


def register_growth80_routes(app):
    """路由注册(routes/__init__ register 函数族惯例)"""
    app.include_router(router)
