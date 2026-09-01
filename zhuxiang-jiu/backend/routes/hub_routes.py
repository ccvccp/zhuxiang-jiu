"""AI智能中枢模块(35号·AI Hub)路由层

端点(设计文档 v1.0 第七章, P0 子集 7 个):
    输入:  POST /api/hub/asr              语音转文字(base64 JSON, 限流+降级)
    输入:  POST /api/hub/input/intent     意图分类(规则轨)
    入口:  GET  /api/hub/panel             角色能力面板(chips)
    入口:  GET  /api/hub/health            入口健康(聚合绿灯)
    路由:  GET  /api/hub/capabilities      能力注册表查询(admin)
    路由:  POST /api/hub/capabilities/{id}/toggle   上下架(admin)
    治理:  GET  /api/hub/ops/intents       意图分布统计(admin)
    治理:  GET  /api/hub/ops/overview       治理看板总览(admin, P2)
    治理:  POST /api/hub/ops/learning/retrigger  学习周期重跑(admin, P2)
    治理:  GET  /api/hub/ops/usage          LLM 用量成本视图(admin, P3)
    治理:  GET  /api/hub/ops/learning/approvals   待审批挑战者清单(admin, P3)
    治理:  POST /api/hub/ops/learning/approve/{id} 批准晋升(admin, P3)
    治理:  POST /api/hub/ops/learning/reject/{id}  拒绝晋升(admin, P3)
    媒体:  POST /api/hub/media/voice        语音文件上传(P3)
    媒体:  POST /api/hub/media/image        图片文件上传(P3)

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


class RouteRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000,
                      description="用户输入文本")
    role: str = Field(default="guest",
                      description="角色: guest/member/cs_staff/admin")


class OrchestrateHubRequest(BaseModel):
    segments: list[str] = Field(..., min_length=1, max_length=3,
                                description="复合任务子段(≤3 段并行)")
    role: str = Field(default="guest",
                      description="角色: guest/member/cs_staff/admin")


class RetriggerRequest(BaseModel):
    """学习周期重跑请求(空 scorerId = 全部 16 个评分器档案)"""
    scorerId: str | None = Field(default=None,
                                 description="评分器ID; 缺省重跑全部")


class MediaUploadRequest(BaseModel):
    """媒体上传请求(base64 JSON 惯例, 零 python-multipart 依赖)"""
    data_b64: str = Field(..., min_length=1,
                          description="媒体文件 base64 编码")
    fmt: str = Field(default="", description="扩展名: voice(webm/mp3/wav) / image(jpg/png/webp/gif)")


class RejectRequest(BaseModel):
    """晋升审批拒绝请求"""
    reason: str | None = Field(default=None, max_length=200,
                                description="拒绝原因(进入版本历史 note)")


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
# 意图路由器 + 复合编排(设计文档 5.3, P1)
# ============================================================

@router.post("/api/hub/route")
async def route_intent(req: RouteRequest):
    """意图 → 能力路由(路由器核心: 角色过滤+熔断摘除+健康排序).

    status: routed=已路由 / degraded=意图命中但能力全被摘 / unmatched=通用兜底.
    capability=None 时调用方回退 chat.general.
    """
    return await hub_service.route(req.text, req.role)


@router.post("/api/hub/orchestrate")
async def orchestrate_hub(req: OrchestrateHubRequest):
    """复合任务编排(≤3 段并行路由 + 后置动作)."""
    return await hub_service.orchestrate(req.segments, req.role)


@router.get("/api/hub/ops/circuit/{cap_id}")
async def get_circuit(cap_id: str, x_role: str | None = Header(None, alias="X-Role")):
    """能力熔断状态查询(admin; 滚动窗口+判定)"""
    _require_admin(x_role)
    return await hub_service.get_circuit_status(cap_id)


@router.post("/api/hub/ops/circuit/{cap_id}/probe")
async def probe_circuit(cap_id: str, x_role: str | None = Header(None, alias="X-Role")):
    """半开恢复探测: 清零健康窗口重新统计(admin)"""
    _require_admin(x_role)
    cap = await hub_service.repo.get_capability(cap_id)
    if cap is None:
        raise HTTPException(status_code=404, detail=f"能力不存在: {cap_id}")
    return await hub_service.probe_capability(cap_id)


# ============================================================
# 治理: 运维总览 + 学习周期管理(设计文档 5.4, P2)
# ============================================================

@router.get("/api/hub/ops/overview")
async def get_ops_overview(x_role: str | None = Header(None, alias="X-Role")):
    """治理看板总览(admin): 能力健康矩阵(红黄绿) + 意图分布 + 入口健康"""
    _require_admin(x_role)
    return await hub_service.get_ops_overview()


@router.post("/api/hub/ops/learning/retrigger")
async def retrigger_learning(
    req: RetriggerRequest,
    x_role: str | None = Header(None, alias="X-Role"),
):
    """学习周期管理(admin): 重跑 AI 自学习(单评分器或全部 17 个档案)

    反馈不足的评分器 status=skipped(非错误); 未知评分器 → 404。
    """
    _require_admin(x_role)
    if req.scorerId:
        from services.ai_learning_service import SCORER_REGISTRY
        if req.scorerId not in SCORER_REGISTRY:
            raise HTTPException(status_code=404,
                                detail=f"未知评分器: {req.scorerId}")
    try:
        result = await hub_service.retrigger_learning(req.scorerId)
    except Exception as exc:  # noqa: BLE001
        _handle(exc)
    return {"success": True, **result}


# ============================================================
# 治理: LLM 用量成本 + 晋升审批流(设计文档 5.4, P3)
# ============================================================

@router.get("/api/hub/ops/usage")
async def get_usage_overview(
    days: int = Query(default=7, ge=1, le=30),
    x_role: str | None = Header(None, alias="X-Role"),
):
    """LLM 用量与成本视图(admin; 当日内存指标 + 历史日 Redis 聚合)"""
    _require_admin(x_role)
    return await hub_service.get_usage_overview(days)


@router.get("/api/hub/ops/learning/approvals")
async def list_approvals(x_role: str | None = Header(None, alias="X-Role")):
    """待审批挑战者清单(admin; 16 档案中带 challenger 的)"""
    _require_admin(x_role)
    return await hub_service.list_approvals()


@router.post("/api/hub/ops/learning/approve/{scorer_id}")
async def approve_promotion(scorer_id: str,
                             x_role: str | None = Header(None, alias="X-Role")):
    """批准晋升: 挑战者→冠军(admin; 无挑战者 → 409)"""
    _require_admin(x_role)
    try:
        return await hub_service.approve_promotion(scorer_id)
    except Exception as exc:  # noqa: BLE001
        _handle(exc)


@router.post("/api/hub/ops/learning/reject/{scorer_id}")
async def reject_promotion(scorer_id: str, req: RejectRequest,
                           x_role: str | None = Header(None, alias="X-Role")):
    """拒绝晋升: 丢弃挑战者, 版本退役进历史(admin; 无挑战者 → 409)"""
    _require_admin(x_role)
    try:
        return await hub_service.reject_promotion(scorer_id, req.reason)
    except Exception as exc:  # noqa: BLE001
        _handle(exc)


# ============================================================
# 媒体上传(P3, 设计文档 6 章: 本地卷 + 静态服务, base64 JSON)
# ============================================================

@router.post("/api/hub/media/voice")
async def upload_voice(req: MediaUploadRequest):
    """语音文件上传(≤2MB, webm/mp3/wav), 返回静态 URL 供消息引用"""
    try:
        raw = base64.b64decode(req.data_b64, validate=False)
    except (binascii.Error, ValueError):
        return JSONResponse({"success": False, "error": "音频 base64 解码失败"})
    result = await hub_service.save_media("voice", raw, req.fmt or "webm")
    return JSONResponse(result)


@router.post("/api/hub/media/image")
async def upload_image(req: MediaUploadRequest):
    """图片文件上传(≤5MB, jpg/png/webp/gif), 返回静态 URL"""
    try:
        raw = base64.b64decode(req.data_b64, validate=False)
    except (binascii.Error, ValueError):
        return JSONResponse({"success": False, "error": "图片 base64 解码失败"})
    result = await hub_service.save_media("image", raw, req.fmt or "jpg")
    return JSONResponse(result)


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
