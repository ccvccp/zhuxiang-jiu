"""竹鉴·BambooVerify(77号)路由层

端点(11):
    观测面 GET(永不关停——典藏为公开事实源):
        GET  /api/zjian/reports           报告列表(双规格)
        GET  /api/zjian/reports/{rid}     报告详情(15项指标)
        GET  /api/zjian/metrics           指标目录+统计
        GET  /api/zjian/asks              问答留痕
        GET  /api/zjian/mode              灰度总览
    决策面 POST(@_decision 门控, off 返回 409):
        POST /api/zjian/verify            质检问答(引证应答)
        POST /api/zjian/compare           规格比对
    管理面(X-Role: admin):
        POST /api/zjian/mode/override     运行时切档
        POST /api/zjian/mode/guard        护栏巡检
        POST /api/zjian/mode/resume       护栏恢复
        POST /api/zjian/reports/anchor    典藏锚定(永不自主)

鉴权: strict 模式 JWT 注入; 观测面公开(GET only)。
"""

import functools
import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.zjian_mode_service import ZjianModeService
from services.zjian_service import ZjianService

logger = logging.getLogger("zjian_routes")

router = APIRouter(tags=["竹鉴BambooVerify大模型(77号)"])


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
    logger.exception("zjian_internal_error")
    raise HTTPException(status_code=500,
                        detail="服务内部错误") from exc


def _decision(fn):
    """决策端点装饰器: 门控(off 409) + 三档留痕(zjianMode)
    + full 档自主巡检(auto_patrol)"""
    from services.zjian_mode_service import MODE_VALUES

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            mode_svc = ZjianModeService()
            mode_state = await mode_svc \
                .require_decision_mode()
        except ValueError as e:
            raise HTTPException(
                status_code=409, detail=str(e)) from e
        result = await fn(*args, **kwargs)
        if isinstance(result, dict) \
                and mode_state.get("mode") in MODE_VALUES[1:]:
            result = {**result,
                      "zjianMode": mode_state["mode"]}
        if mode_state.get("mode") == "full":
            try:
                await mode_svc \
                    .note_decision_and_maybe_patrol()
            except Exception:
                logger.warning(
                    "zjian_auto_patrol_failed",
                    exc_info=True)
        return result
    return wrapper


class VerifyRequest(BaseModel):
    question: str = Field(..., min_length=1,
                          max_length=1000,
                          description="质检问题(如: 甲醇检出"
                                      "了吗/52型酒精度多少/"
                                      "安全性怎么样)")


class ModeOverrideRequest(BaseModel):
    mode: str = Field(...,
                      description="目标灰度态(off/shadow/"
                                  "assist/full; "
                                  "空串清除 override)")


class ResumeRequest(BaseModel):
    note: str | None = Field(default=None, max_length=200)


class AnchorRequest(BaseModel):
    reportId: str = Field(..., max_length=64)
    field: str = Field(..., max_length=64,
                       description="锚定字段"
                                   "(conclusion/signDate)")


# ============================================================
# 观测面(永不关停)
# ============================================================

@router.get("/api/zjian/reports")
async def list_reports():
    """检测报告列表(双规格典藏)"""
    return await ZjianService().list_reports()


@router.get("/api/zjian/reports/{rid}")
async def get_report(rid: str):
    """报告详情(15 项指标明细)"""
    try:
        return await ZjianService().get_report(rid)
    except Exception as exc:
        _handle(exc)


@router.get("/api/zjian/metrics")
async def get_metrics():
    """指标目录 + 问答统计"""
    return await ZjianService().get_metrics()


@router.get("/api/zjian/catalog")
async def metric_catalog():
    """指标目录(15 项名称/单位/方法/技术要求)"""
    return await ZjianService().metric_catalog()


@router.get("/api/zjian/asks")
async def list_asks(limit: int = 20):
    """质检问答留痕"""
    return await ZjianService().list_asks(limit)


@router.get("/api/zjian/mode")
async def get_mode():
    """灰度总览(四档+护栏+full 自主域)"""
    return await ZjianModeService().status_view()


# ============================================================
# 决策面(@_decision 门控, off 返回 409)
# ============================================================

@router.post("/api/zjian/verify")
@_decision
async def verify(req: VerifyRequest):
    """质检问答(指标检索 → 引证应答——技术断言必含
    报告引证; 医疗/夸大断言拦截)"""
    try:
        return await ZjianService().verify_chat(
            req.question)
    except Exception as exc:
        _handle(exc)


@router.post("/api/zjian/compare")
@_decision
async def compare():
    """双规格全指标比对(15 项对照)"""
    try:
        return await ZjianService().compare()
    except Exception as exc:
        _handle(exc)


# ============================================================
# 管理面(X-Role: admin)
# ============================================================

@router.post("/api/zjian/reports/anchor")
async def anchor_report(req: AnchorRequest,
                        x_role: str | None = Header(
                            None, alias="X-Role")):
    """典藏锚定(报告字段人工修订——永不自主红线,
    全量修订走部署链; 此端点仅签发日期/结论微调留痕)"""
    _require_admin(x_role)
    try:
        svc = ZjianService()
        r = await svc.repo.get_report(req.reportId)
        if r is None:
            raise KeyError(f"报告不存在: {req.reportId}")
        if req.field not in ("conclusion", "signDate"):
            raise ValueError(
                "锚定字段仅限 conclusion/signDate"
                "(检测数据不可线上修订——原文事实源)")
        return {"success": True,
                "reportId": req.reportId,
                "anchoredField": req.field,
                "note": ("典藏锚定为留痕操作; 检测数据"
                         "修订须走部署链(人工显式)")}
    except Exception as exc:
        _handle(exc)


@router.post("/api/zjian/mode/override")
async def mode_override(req: ModeOverrideRequest,
                        x_role: str | None = Header(
                            None, alias="X-Role")):
    """运行时灰度切档(留痕; 空串清除 override)"""
    _require_admin(x_role)
    try:
        return await ZjianModeService().set_override(
            req.mode)
    except Exception as exc:
        _handle(exc)


@router.post("/api/zjian/mode/guard")
async def mode_guard(x_role: str | None = Header(
        None, alias="X-Role")):
    """护栏自动巡检(三指标聚合→恶化>3% 自动暂停)"""
    _require_admin(x_role)
    try:
        return await ZjianModeService().patrol()
    except Exception as exc:
        _handle(exc)


@router.post("/api/zjian/mode/resume")
async def mode_resume(req: ResumeRequest,
                      x_role: str | None = Header(
                          None, alias="X-Role")):
    """护栏恢复(人工解除自动暂停——决策留痕)"""
    _require_admin(x_role)
    try:
        return await ZjianModeService().resume(
            note=req.note or "")
    except Exception as exc:
        _handle(exc)


def register_zjian_routes(app):
    """注册竹鉴·BambooVerify 路由到 FastAPI app"""
    app.include_router(router)
