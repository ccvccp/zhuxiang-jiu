"""抖音开放平台对接路由(2026-10-17 入驻收口·admin)

P0: /status 模块诊断 + /probe 凭据全链探活(token 强刷); 视频发布/
内容分发待小程序能力开通后扩展(能力开通前置: 类目发布上线+首次发版)。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from services.douyin_open_service import (
    DouyinAPIError, DouyinConfigError, DouyinOpenService)

router = APIRouter(prefix="/api/douyin", tags=["抖音开放平台对接"])

_svc = DouyinOpenService()


def _require_admin(x_role: str | None) -> None:
    if (x_role or "").strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="admin only")


@router.get("/status")
async def douyin_status(x_role: str | None = Header(default=None)):
    """模块状态(env/token 生命周期诊断, 不触发网络)"""
    _require_admin(x_role)
    return {"code": 0, "data": await _svc.status()}


@router.get("/probe")
async def douyin_probe(x_role: str | None = Header(default=None)):
    """凭据全链探活(强刷 token, 真实验证 appid+secret 网络链路)"""
    _require_admin(x_role)
    try:
        return {"code": 0, "data": await _svc.probe()}
    except DouyinConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except DouyinAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


def register_douyin_routes(app) -> None:
    app.include_router(router)
