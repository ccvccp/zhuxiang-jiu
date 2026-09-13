"""74号·NexusFlow(智枢·流)路由
(nexus74_routes, P1——/api/nexus74)

规划(docs/74号_NexusFlow智枢流_AI智能全域
发布大模型_创新规划方案.md §六 P1):
    8 端点(规则中枢与合规引擎):
        GET  /rules              规则列表
        POST /rules              规则录入
        GET  /rules/{id}         规则详情
        POST /compliance/check   合规检测
        POST /compliance/scan    批量扫描
        GET  /compliance/dict    合规字典
        POST /warning/inject     警示语注入
        GET  /personas           人格档案

铁律(规划 §九):
    - 合规检测/规则库/人格档案=观测面
      常开(不受 MODE 影响)
    - 录入类操作 admin 门禁
    - 异常映射: KeyError→404,
      ValueError→409(71号口径)
"""

import logging

from fastapi import APIRouter, Header, \
    HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(
    "nexus74_routes")

router = APIRouter(
    prefix="/api/nexus74",
    tags=["74NexusFlow智枢流"])


def _require_admin(x_role):
    if x_role != "admin":
        raise HTTPException(
            status_code=401,
            detail="须 X-Role: admin")


def _map(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(
            status_code=404,
            detail=f"不存在: {exc.args[0]}")
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=409, detail=str(exc))
    return HTTPException(
        status_code=500,
        detail=f"内部错误: {exc}")


def _service():
    from services.nexus74_p1_service import (
        Nexus74P1Service,
    )
    return Nexus74P1Service()


# ============================================================
# 请求模型
# ============================================================

class RuleCreateRequest(BaseModel):
    platform: str = Field(
        "all_platforms",
        description="平台: all_platforms/"
                    "wechat_mp/douyin/"
                    "xiaohongshu/zhihu/"
                    "bilibili/toutiao")
    ruleType: str = Field(
        ..., description="规则类型: "
                         "prohibition/"
                         "conditional_"
                         "prohibition/"
                         "mandatory_"
                         "requirement/"
                         "recommendation")
    redline: str = Field(
        "", description="关联红线(可空): "
                        "R1_induce 等")
    content: str = Field(
        ..., max_length=500,
        description="规则内容")
    patterns: list = Field(
        default_factory=list,
        description="禁止模式词表")
    safeHarbor: list = Field(
        default_factory=list,
        description="安全港例外")
    legalBasis: str = Field(
        "", max_length=200,
        description="法规依据")
    confidence: float = Field(
        0.8, gt=0, le=1,
        description="置信度")


class ComplianceCheckRequest(BaseModel):
    text: str = Field(
        ..., max_length=10000,
        description="待检文本")
    platform: str = Field(
        "all_platforms",
        description="目标平台")
    hasWarning: bool = Field(
        False, description="发布包是否"
                          "已含警示语")
    contentType: str = Field(
        "article", max_length=30,
        description="内容形态")


class ComplianceScanRequest(BaseModel):
    texts: list = Field(
        ..., description="文本列表"
                        "(上限 50)")
    platform: str = Field(
        "all_platforms",
        description="目标平台")


class WarningInjectRequest(BaseModel):
    text: str = Field(
        ..., max_length=10000,
        description="待注入文本")


# ============================================================
# ① 合规规则库(观测面——常开)
# ============================================================

@router.get("/rules")
async def list_rules(
        platform: str = "",
        ruleType: str = "",
        x_role: str = Header(
            default=None, alias="X-Role")):
    """规则库列表(观测面——platform/
    ruleType 筛选)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .list_rules(
                    platform=platform,
                    rule_type=ruleType)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/rules")
async def create_rule(
        body: RuleCreateRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """规则录入(admin——Schema 对象化)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .create_rule(
                    body.model_dump())}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/rules/{rule_id}")
async def get_rule(
        rule_id: int,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """规则详情(观测面)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .get_rule(rule_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# ② 确定性合规引擎(观测面——常开)
# ============================================================

@router.post("/compliance/check")
async def compliance_check(
        body: ComplianceCheckRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """合规检测(四态输出——确定性
    引擎, LLM 禁入)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .compliance_check(
                    text=body.text,
                    platform=body.platform,
                    has_warning=body
                    .hasWarning,
                    content_type=body
                    .contentType)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/compliance/scan")
async def compliance_scan(
        body: ComplianceScanRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """批量合规扫描(源内容预检)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .compliance_scan(
                    texts=body.texts,
                    platform=body
                    .platform)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/compliance/dict")
async def compliance_dict(
        x_role: str = Header(
            default=None, alias="X-Role")):
    """合规字典公示(六红线/警示语/
    边界词/安全港)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": _service()
                .compliance_dict()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# ③ 警示语注入(R6 修复——确定性)
# ============================================================

@router.post("/warning/inject")
async def warning_inject(
        body: WarningInjectRequest,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """警示语注入(footer 显著位置
    ——幂等)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .warning_inject(
                    text=body.text)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# ⑤ 平台人格档案(观测面——常开)
# ============================================================

@router.get("/personas")
async def list_personas(
        platform: str = "",
        x_role: str = Header(
            default=None, alias="X-Role")):
    """平台人格档案列表(六平台+状态机)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .list_personas(
                    platform=platform)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.get("/persona/state")
async def persona_state(
        platform: str,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """人设状态机当前态(defaultState+
    override)"""
    try:
        _require_admin(x_role)
        return {"code": 0,
                "data": await _service()
                .persona_state(platform)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# 模型状态(观测面——P5 完整版预占位)
# ============================================================

@router.get("/model/status")
async def model_status(
        x_role: str = Header(
            default=None, alias="X-Role")):
    """模型状态(mode/kill/版本——
    观测面)"""
    try:
        _require_admin(x_role)
        from services.nexus74_registry import (
            IMMUNITY_UNFREEZE_ENV,
            MODEL_VERSION,
            PLATFORMS, REDLINES,
            current_mode, is_kill,
        )
        return {"code": 0,
                "data": {
                    "modelVersion":
                        MODEL_VERSION,
                    "mode": current_mode(),
                    "kill": is_kill(),
                    "platformCount": len(
                        PLATFORMS),
                    "redlines": list(
                        REDLINES),
                    "unfreezeEnv":
                        f"{IMMUNITY_UNFREEZE_ENV}"
                        f"=1",
                    "note": ("P1 规则中枢"
                             "已交付; 发布"
                             "面随 P3 上线")}}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


def register_nexus74_routes(app):
    """路由注册(main.py 调用)"""
    app.include_router(router)
