"""竹韵·智衡·竹奕酒智能大模型(75号)路由层

设计依据: 《"竹韵·智衡"竹奕酒 SDD V3.0》(DTDAE 范式工程化裁剪)
—— 全站融合优化落地(三态灰度/护栏/决策门控/评分器入册)。

端点(14):
    观测面 GET(永不关停——知识内核为公开事实锚点):
        GET  /api/zyh/knowledge               知识条目列表
        GET  /api/zyh/knowledge/{kid}          单条
        GET  /api/zyh/graph                    工艺知识图谱
        GET  /api/zyh/rules                    守门三层规则公示
        GET  /api/zyh/stats                    统计(守门分布/缓存)
        GET  /api/zyh/cache/stats              缓存指标
        GET  /api/zyh/mode                    灰度总览
        GET  /api/zyh/qa                      问答留痕
    决策面 POST(@_decision 门控, off 返回 409;
                用户端 X-Member-Id 语义——JWT 注入):
        POST /api/zyh/chat                     C端问答(守门三层+消歧
                                             +语义缓存+知识检索)
        POST /api/zyh/stress-test              B端韧性压力推演
        POST /api/zyh/probe/debate             探针辩题生成
    管理面(X-Role: admin——JWT 注入 x-role):
        POST /api/zyh/mode/override            运行时切档
        POST /api/zyh/mode/guard              护栏巡检
        POST /api/zyh/mode/resume              护栏恢复
        POST /api/zyh/cache/clear              缓存清空

鉴权惯例: strict 模式下 JWT 中间件注入 x-member-id/x-role
(全站 AUTH_MODE=strict 2026-09-18 起); 观测面无鉴权头要求
(游客可查——公开白名单语义, 知识内核事实锚点)。
"""

import functools
import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.zyh_mode_service import ZyhModeService
from services.zyh_service import ZyhService

logger = logging.getLogger("zyh_routes")

router = APIRouter(tags=["竹韵·智衡大模型(75号)"])


# ============================================================
# 鉴权与异常映射辅助(对齐项目约定)
# ============================================================

def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        msg = str(exc) if str(exc) else "资源不存在"
        if msg.startswith("'") and msg.endswith("'"):
            msg = msg[1:-1]
        raise HTTPException(status_code=404, detail=msg)
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    logger.exception("zyh_internal_error")
    raise HTTPException(status_code=500, detail="服务内部错误") from exc


# ============================================================
# 大模型三态灰度(全站范式): 决策面门控
# ============================================================

def _decision(fn):
    """决策端点装饰器: 门控(off 409) + shadow/assist 留痕(zyhMode)

    决策面 = 新增生成入口(chat 问答/推演/辩题)——off 只锁生成;
    知识内核观测面永不关停(公开事实锚点语义)。
    鉴权 401/403 优先于门控 409(JWT 中间件注入在前)。
    """
    from services.zyh_mode_service import MODE_VALUES

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            mode_state = await ZyhModeService(
            ).require_decision_mode()
        except ValueError as e:
            raise HTTPException(
                status_code=409, detail=str(e)) from e
        result = await fn(*args, **kwargs)
        if isinstance(result, dict) \
                and mode_state.get("mode") in MODE_VALUES[1:]:
            result = {**result, "zyhMode": mode_state["mode"]}
        return result
    return wrapper


# ============================================================
# 请求模型
# ============================================================

class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000,
                         description="用户问题(竹奕酒工艺/香型/"
                                     "竞品对比/合规等)")


class StressTestRequest(BaseModel):
    scenario: str = Field(...,
                          description="情景: material_moisture/"
                                      "competitor_impact/"
                                      "seasonal_shortage")
    parameters: dict = Field(default_factory=dict,
                             description="情景参数(如 "
                                         "moisture_increase)")


class DebateRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=5,
                       description="生成辩题数(1-5)")


class ModeOverrideRequest(BaseModel):
    mode: str = Field(...,
                      description="目标灰度态(off/shadow/assist; "
                                  "空串清除 override)")


class ResumeRequest(BaseModel):
    note: str | None = Field(default=None, max_length=200,
                             description="恢复备注(决策留痕)")


# ============================================================
# 观测面(永不关停)
# ============================================================

@router.get("/api/zyh/knowledge")
async def list_knowledge():
    """知识条目列表(工艺宪法/香型/专利/企标/竞品对立等)"""
    return await ZyhService().get_knowledge_list()


@router.get("/api/zyh/knowledge/{kid}")
async def get_knowledge(kid: str):
    """单条知识详情"""
    try:
        return await ZyhService().get_knowledge(kid)
    except Exception as exc:
        _handle(exc)


@router.get("/api/zyh/graph")
async def get_graph():
    """工艺知识图谱(节点+关系——SDD §4.1 Schema)"""
    return await ZyhService().get_graph()


@router.get("/api/zyh/rules")
async def get_rules():
    """守门三层规则公示(L1 正则/L2 分类/L3 溯源)"""
    return await ZyhService().get_rules()


@router.get("/api/zyh/stats")
async def get_stats():
    """统计总览(守门拦截分布/缓存命中/请求量)"""
    return await ZyhService().get_stats()


@router.get("/api/zyh/cache/stats")
async def get_cache_stats():
    """语义缓存指标(命中率/条目数)"""
    return await ZyhService().get_cache_stats()


@router.get("/api/zyh/qa")
async def list_qa(limit: int = 20):
    """问答留痕(最近 N 条)"""
    return await ZyhService().list_qa(limit)


@router.get("/api/zyh/mode")
async def get_mode():
    """灰度总览(模式+护栏+红线公示)"""
    return await ZyhModeService().status_view()


# ============================================================
# 决策面(@_decision 门控, off 返回 409)
# ============================================================

@router.post("/api/zyh/chat")
@_decision
async def chat(req: ChatRequest,
               x_member_id: str | None = Header(
                   None, alias="X-Member-Id")):
    """C 端智能问答(守门三层 + 实体消歧 + 语义缓存 + 知识检索).

    成功: {content, citations, entityDisambiguation, cacheHit}
    拦截: {guardrailsTriggered, guardLayer(1/2/3),
           guardRule, content(合规纠正话术), citations}
    """
    member_id = None
    if x_member_id:
        try:
            member_id = int(x_member_id)
        except ValueError:
            member_id = None
    try:
        return await ZyhService().chat(req.prompt, member_id)
    except Exception as exc:
        _handle(exc)


@router.post("/api/zyh/stress-test")
@_decision
async def stress_test(req: StressTestRequest,
                      x_role: str | None = Header(
                          None, alias="X-Role")):
    """B 端韧性压力推演(情景→风险等级+影响分析+缓解预案).

    决策面语义: X-Role 无强制(内部运营工具), 生成入口由
    ZYH_MODE 门控。
    """
    try:
        return await ZyhService().stress_test(
            req.scenario, req.parameters)
    except Exception as exc:
        _handle(exc)


@router.post("/api/zyh/probe/debate")
@_decision
async def probe_debate(req: DebateRequest):
    """探针辩题生成(认知痛点辩题, 供人工多平台分发)."""
    try:
        return await ZyhService().generate_debates(req.count)
    except Exception as exc:
        _handle(exc)


# ============================================================
# 管理面(X-Role: admin)
# ============================================================

@router.post("/api/zyh/mode/override")
async def mode_override(req: ModeOverrideRequest,
                        x_role: str | None = Header(
                            None, alias="X-Role")):
    """运行时灰度切档(留痕; 空串清除 override)"""
    _require_admin(x_role)
    try:
        return await ZyhModeService().set_override(req.mode)
    except Exception as exc:
        _handle(exc)


@router.post("/api/zyh/mode/guard")
async def mode_guard(x_role: str | None = Header(
        None, alias="X-Role")):
    """护栏自动巡检(聚合三指标→恶化>3% 自动暂停)"""
    _require_admin(x_role)
    try:
        return await ZyhModeService().patrol()
    except Exception as exc:
        _handle(exc)


@router.post("/api/zyh/mode/resume")
async def mode_resume(req: ResumeRequest,
                      x_role: str | None = Header(
                          None, alias="X-Role")):
    """护栏恢复(人工解除自动暂停——决策留痕)"""
    _require_admin(x_role)
    try:
        return await ZyhModeService().resume(note=req.note or "")
    except Exception as exc:
        _handle(exc)


@router.post("/api/zyh/cache/clear")
async def cache_clear(x_role: str | None = Header(
        None, alias="X-Role")):
    """语义缓存清空(管理面——知识更新后失效旧缓存)"""
    _require_admin(x_role)
    return await ZyhService().clear_cache()


def register_zyh_routes(app):
    """注册竹韵·智衡大模型路由到 FastAPI app"""
    app.include_router(router)
