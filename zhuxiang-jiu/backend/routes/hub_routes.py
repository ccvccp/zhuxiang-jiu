"""AI智能中枢模块(35号·AI Hub)路由层

端点(设计文档 v1.0 第七章, P0 子集 7 个):
    输入:  POST /api/hub/asr              语音转文字(base64 JSON, 限流+降级)
    输入:  POST /api/hub/input/intent     意图分类(规则轨)
    入口:  GET  /api/hub/panel             角色能力面板(chips)
    入口:  GET  /api/hub/health            入口健康(聚合绿灯)
    路由:  GET  /api/hub/capabilities      能力注册表查询(admin)
    路由:  POST /api/hub/capabilities/{id}/toggle   上下架(admin)
    治理:  GET  /api/hub/ops/intents       意图分布统计(admin)

鉴权惯例(对齐 role_routes): X-Member-Id(用户) / X-Role: admin(管理)。

音频传输: base64 JSON(对齐 knowledge 模块 JSON+URL 惯例, 全站零 python-multipart 依赖)。
"""

import base64
import binascii
import logging
from datetime import datetime, timedelta, UTC

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.hub_service import hub_service

logger = logging.getLogger("hub_routes")

router = APIRouter(tags=["AI智能中枢模块(35)"])


# ============================================================
# 鉴权辅助(对齐 role_routes 惯例)
# ============================================================

def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """统一异常映射(对齐 role_routes._handle)"""
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    logger.exception("hub_internal_error")
    raise HTTPException(status_code=500, detail="服务内部错误") from exc


# ============================================================
# 请求/响应模型
# ============================================================

class IntentRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000,
                      description="待分类文本")


class AsrRequest(BaseModel):
    """语音转写请求(音频 base64; 格式 webm/mp3/wav, ≤2MB, ≤60s)"""
    audio_b64: str = Field(..., min_length=1,
                           description="音频文件 base64 编码")
    fmt: str = Field(default="webm", description="音频格式: webm/mp3/wav")


class ToggleRequest(BaseModel):
    enabled: bool = Field(..., description="目标状态")


# ============================================================
# 输入引擎: ASR(设计文档 5.2.1)
# ============================================================

@router.post("/api/hub/asr")
async def asr_transcribe(
    req: AsrRequest,
    x_member_id: str | None = Header(None, alias="X-Member-Id"),
):
    """语音转文字(按住说话链路后端, base64 JSON 传输).

    成功: {"success": true, "text": "...", "model": "glm-asr-2512"}
    降级: 200 + success=false + fallback_hint=keyboard(前端提示改键盘, 不白屏).
    """
    member_id = None
    if x_member_id:
        try:
            member_id = int(x_member_id)
        except ValueError:
            member_id = None  # 游客头格式异常不阻断, 走无限流轨
    try:
        raw = base64.b64decode(req.audio_b64, validate=False)
    except (binascii.Error, ValueError):
        return JSONResponse({"success": False, "error": "音频 base64 解码失败",
                             "fallback_hint": "keyboard"})
    result = await hub_service.transcribe_upload(
        raw, filename=f"voice.{req.fmt or 'webm'}", member_id=member_id)
    # 降级也返回 200(结构化错误), 前端按 fallback_hint 处理
    return JSONResponse(result)


# ============================================================
# 输入引擎: 意图分类(设计文档 5.2.3)
# ============================================================

@router.post("/api/hub/input/intent")
async def classify_intent(req: IntentRequest):
    """意图分类(规则轨优先, <5ms; LLM 增强轨 P1)"""
    return await hub_service.classify_intent(req.text)


# ============================================================
# 入口: 角色能力面板(设计文档 5.1.2)
# ============================================================

@router.get("/api/hub/panel")
async def get_panel(role: str = Query(default="guest",
                                     description="角色: guest/member/cs_staff/admin")):
    """角色能力面板配置(chips ≤6, 点击注入快捷指令)"""
    return await hub_service.get_panel(role)


@router.get("/api/hub/health")
async def get_health():
    """入口健康(聚合各能力绿灯; degraded 表示有能力被熔断摘除)"""
    return await hub_service.get_health()


# ============================================================
# 能力注册表(设计文档 5.3.1, P0 只读 + 上下架)
# ============================================================

@router.get("/api/hub/capabilities")
async def list_capabilities(
    x_role: str | None = Header(None, alias="X-Role"),
):
    """能力注册表查询(admin)"""
    _require_admin(x_role)
    caps = await hub_service.repo.list_capabilities()
    return {"success": True, "total": len(caps), "capabilities": caps}


@router.post("/api/hub/capabilities/{cap_id}/toggle")
async def toggle_capability(
    cap_id: str, req: ToggleRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """能力上下架(admin; 下架后路由器不再分发该能力, P1 熔断联动)"""
    _require_admin(x_role)
    cap = await hub_service.repo.get_capability(cap_id)
    if cap is None:
        raise HTTPException(status_code=404, detail=f"能力不存在: {cap_id}")
    cap["enabled"] = req.enabled
    await hub_service.repo.upsert_capability(cap)
    return {"success": True, "id": cap_id, "enabled": req.enabled}


# ============================================================
# 治理: 意图分布统计(设计文档 6 章 intent_stats)
# ============================================================

@router.get("/api/hub/ops/intents")
async def get_intent_stats(
    days: int = Query(default=7, ge=1, le=30),
    x_role: str | None = Header(None, alias="X-Role"),
):
    """意图分布统计(admin; 近 N 日)"""
    _require_admin(x_role)
    stats = {}
    now = datetime.now(UTC)
    for i in range(days):
        day = (now - timedelta(days=i)).strftime("%Y%m%d")
        day_stats = await hub_service.repo.get_intent_stats(day)
        if day_stats:
            stats[day] = day_stats
    return {"success": True, "days": days, "intentStats": stats}


def register_hub_routes(app):
    """注册AI智能中枢模块路由"""
    app.include_router(router)
