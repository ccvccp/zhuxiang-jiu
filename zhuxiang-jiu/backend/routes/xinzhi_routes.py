"""68号 P0/P1·信值·臻选路由(五维雷达+臻选货架)

端点(5):
    GET  /api/xinzhi/radar               五维雷达(最新快照/即时计算)
    GET  /api/xinzhi/radar/history        90 日历史曲线
    POST /api/xinzhi/products/score       商品三维评分批次(P1)
    GET  /api/xinzhi/prime                臻选货架 L1(信值加权排序)
    GET  /api/xinzhi/products/{id}/score  商品评分明细(可解释)

鉴权: X-Member-Id(会员本人域; P0 与 /api/member/profile
同口径)
异常映射: KeyError→404 / ValueError→409(项目约定)
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

router = APIRouter()


def _member_id(x_member_id: str | None) -> int:
    if not (x_member_id or "").strip():
        raise HTTPException(status_code=401,
                            detail="需要登录(X-Member-Id)")
    try:
        return int(x_member_id)
    except ValueError:
        raise HTTPException(status_code=401,
                            detail="X-Member-Id 非法")


def _handle(e: Exception):
    if isinstance(e, KeyError):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, ValueError):
        raise HTTPException(status_code=409, detail=str(e))
    raise HTTPException(status_code=500, detail=str(e))


def _service():
    from services.xinzhi_radar_service import (
        XinzhiRadarService)
    return XinzhiRadarService()


def _prime_service():
    from services.xinzhi_prime_service import (
        XinzhiPrimeService)
    return XinzhiPrimeService()


class ScoreRequest(PydBaseModel):
    productIds: list = Field(None,
                             description="指定商品(空=全量)")


@router.get("/api/xinzhi/radar",
            tags=["信值臻选购物平台"])
async def xinzhi_radar(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """信值五维雷达(诚信30/互助25/专业20/活跃15/成长10——
    分段Sigmoid+熔断+衰减; 每维影响因素TOP3 可解释)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _service().get_radar(mid)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/radar/history",
            tags=["信值臻选购物平台"])
async def xinzhi_radar_history(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None,
        limit: int = 90):
    """信值 90 日历史曲线(快照时序)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _service().get_history(mid, limit=limit)}
    except Exception as e:
        _handle(e)


@router.post("/api/xinzhi/products/score",
             tags=["信值臻选购物平台"])
async def xinzhi_products_score(
        req: ScoreRequest,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """商品三维评分批次(契合×安全硬闸×转化 → L1-L4;
    信值加权排序——文档"信值加权排序算法"公式)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _prime_service().score_products(
                    mid, product_ids=req.productIds)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/prime",
            tags=["信值臻选购物平台"])
async def xinzhi_prime_shelf(
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None,
        limit: int = 20):
    """臻选货架(L1 臻选位, 信值加权排序降序——懒加载)"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _prime_service().prime_shelf(
                    mid, limit=limit)}
    except Exception as e:
        _handle(e)


@router.get("/api/xinzhi/products/{product_id}/score",
            tags=["信值臻选购物平台"])
async def xinzhi_product_detail(
        product_id: str,
        x_member_id: Annotated[str | None,
                               Header(alias="X-Member-Id")]
        = None):
    """商品三维分明细(可解释——文档"透明化决策解释")"""
    mid = _member_id(x_member_id)
    try:
        return {"success": True, "data":
                await _prime_service().product_detail(
                    mid, product_id)}
    except Exception as e:
        _handle(e)


def register_xinzhi_routes(app) -> None:
    """注册信值臻选路由(main.py startup 调用)"""
    app.include_router(router)
