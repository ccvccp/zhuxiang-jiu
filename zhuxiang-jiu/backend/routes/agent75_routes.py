"""75号·网站自研 Agent 中枢路由(P0 编排闭环)

端点(P0 3 个, 方案 /api/v1/agent/* 设计):
    POST /api/v1/agent/tasks          创建并同步执行(决策面, admin)
    GET  /api/v1/agent/tasks/{id}     任务详情+轨迹(观测面, admin)
    GET  /api/v1/agent/tasks          最近任务清单(观测面, admin)

口径(73号sv 范式): 决策面 AGENT75_KILL=on → 409 一键回退;
                   KeyError → 404。
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.agent75_orchestrator_service import (
    WebsiteAgentOrchestrator,
    is_kill,
)

router = APIRouter(
    prefix="/api/v1/agent",
    tags=["网站Agent中枢(75号)"],
)

_orchestrator = WebsiteAgentOrchestrator()


def _require_admin(x_role: str | None) -> None:
    if (x_role or "").strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="admin only")


class TaskCreateRequest(BaseModel):
    query: str = Field(..., min_length=1, description="用户任务输入")
    user_id: str | None = Field(None, description="站点用户标识(可空)")
    context: dict | None = Field(
        None, description="页面/设备等上下文(可空)")


@router.post("/tasks")
async def create_task(body: TaskCreateRequest,
                      x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    if is_kill():
        raise HTTPException(status_code=409, detail="agent75 killed")
    task = _orchestrator.run(body.query, body.user_id, body.context)
    return {"code": 0, "data": {
        "task_id": task.task_id, "status": task.status,
        "intent": task.intent, "answer": task.answer,
        "trace": task.execution_trace,
        "evaluation": task.evaluation}}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str,
                   x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    task = _orchestrator.load(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {"code": 0, "data": task.model_dump()}


@router.get("/tasks")
async def list_tasks(limit: int = 20,
                     x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    return {"code": 0, "data": _orchestrator.list_recent(limit)}


# ---------- P2: 进化候选审批面(方案 /api/v1/evolution/*) ----------

from services.agent75_evolution_service import AgentEvolutionService  # noqa: E402

evo_router = APIRouter(
    prefix="/api/v1/evolution",
    tags=["网站Agent中枢(75号)·进化面"],
)

_evolution = AgentEvolutionService()


@evo_router.get("/candidates")
async def list_candidates(limit: int = 50,
                          x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    return {"code": 0, "data": _evolution.list(limit)}


@evo_router.post("/candidates/{evolution_id}/approve")
async def approve_candidate(evolution_id: str,
                            x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    try:
        return {"code": 0, "data": _evolution.approve(evolution_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="candidate not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@evo_router.post("/candidates/{evolution_id}/reject")
async def reject_candidate(evolution_id: str,
                           x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    try:
        return {"code": 0, "data": _evolution.reject(evolution_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="candidate not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@evo_router.post("/candidates/{evolution_id}/rollback")
async def rollback_candidate(evolution_id: str,
                             x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    try:
        return {"code": 0,
                "data": _evolution.rollback_candidate(evolution_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="candidate not found")


# ---------- P3a: 技能库管理面(方案 /api/v1/skills/*) ----------

from services.agent75_skill_service import (  # noqa: E402
    SkillRuntime, SkillStatus, skill_kill)

skills_router = APIRouter(
    prefix="/api/v1/skills",
    tags=["网站Agent中枢(75号)·技能面"],
)


@skills_router.get("")
async def list_skills(x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    if skill_kill():
        return {"code": 0, "data": [], "detail": "skill layer killed"}
    return {"code": 0, "data": SkillRuntime().list()}


@skills_router.get("/{skill_id}")
async def get_skill(skill_id: str,
                    x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    s = (SkillRuntime().get(skill_id) if not skill_kill() else None)
    if s is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return {"code": 0, "data": s.model_dump()}


@skills_router.post("/{skill_id}/test")
async def test_skill(skill_id: str, body: dict | None = None,
                     x_role: str | None = Header(default=None)):
    """影子调用一次: 输入样例 query, 结果不入主链只记 metrics"""
    _require_admin(x_role)
    rt = SkillRuntime() if not skill_kill() else None
    s = rt.get(skill_id) if rt else None
    if s is None:
        raise HTTPException(status_code=404, detail="skill not found")
    query = str((body or {}).get("query") or "示例输入")
    from services.agent75_orchestrator_service import AgentModelGateway
    content, ok, ms = AgentModelGateway().call(
        s.spec.get("model_id", "glm-reason"),
        s.spec.get("prompt_template", ""), query)
    demoted = rt.record_invocation(skill_id, ok, ms)
    return {"code": 0, "data": {
        "skill_id": skill_id, "ok": ok, "latency_ms": ms,
        "answer": content[:200], "demoted": demoted}}


@skills_router.post("/{skill_id}/activate")
async def activate_skill(skill_id: str,
                         x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    try:
        return {"code": 0, "data": SkillRuntime().set_status(
            skill_id, SkillStatus.ACTIVE).model_dump()}
    except KeyError:
        raise HTTPException(status_code=404, detail="skill not found")


@skills_router.post("/{skill_id}/deprecate")
async def deprecate_skill(skill_id: str,
                          x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    try:
        return {"code": 0, "data": SkillRuntime().set_status(
            skill_id, SkillStatus.DEPRECATED).model_dump()}
    except KeyError:
        raise HTTPException(status_code=404, detail="skill not found")


# ---------- P3b: code_tool 注册(强制 dry-run)+沙箱健康 ----------

from pydantic import BaseModel as _BM, Field as _F  # noqa: E402


class CodeToolCreateRequest(_BM):
    skill_id: str = _F(..., min_length=3, max_length=48,
                       description="技能 id(字母数字下划线)")
    name: str = _F(..., min_length=2, max_length=32)
    intent: str = _F(..., description="chat/summary/draft")
    code: str = _F(..., min_length=10, description="python 片段, 需定义 run(input)->dict, 结果含 answer 字段")
    sample_input: dict = _F(default_factory=dict,
                            description="dry-run 样例输入")
    timeout_ms: int = _F(3000, ge=100, le=10_000)


@skills_router.post("")
async def create_code_tool(body: CodeToolCreateRequest,
                           x_role: str | None = Header(default=None)):
    """code_tool 注册: 静态预检 + 沙箱 dry-run 空跑双闸, 过则落
    status=testing(灰度); 任一闸失败 409 不入库"""
    _require_admin(x_role)
    from services import agent75_sandbox_service as sb
    from services.agent75_skill_service import (
        SkillDefinition, SkillKind, SkillRuntime, SkillStatus)
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9_]+", body.skill_id):
        raise HTTPException(status_code=409, detail="skill_id 非法字符")
    if sb.sandbox_kill():
        raise HTTPException(status_code=409, detail="sandbox killed")
    ok, err = sb.validate_code(body.code)
    if not ok:
        raise HTTPException(status_code=409,
                            detail=f"静态预检未过: {err}")
    rt = SkillRuntime()
    if rt.get(body.skill_id) is not None:
        raise HTTPException(status_code=409, detail="skill_id 已存在")
    # dry-run 闸门(方案: approve 前必须沙箱空跑通过)
    r = sb.exec_code(body.code, body.sample_input, body.timeout_ms)
    if not (r.get("ok") and isinstance(r.get("result"), dict)
            and str((r.get("result") or {}).get("answer") or "").strip()):
        raise HTTPException(
            status_code=409,
            detail=f"dry-run 未过: {r.get('error') or 'answer 缺失'}")
    sb.audit_run(body.skill_id, True, r.get("latency_ms", 0), body.code)
    skill = rt.upsert(SkillDefinition(
        skill_id=body.skill_id, name=body.name,
        status=SkillStatus.TESTING, kind=SkillKind.CODE_TOOL,
        slot="generate", intent=body.intent,
        spec={"code": body.code, "timeout_ms": body.timeout_ms},
        source="admin"))
    return {"code": 0, "data": skill.model_dump()}


sbx_router = APIRouter(
    prefix="/api/v1/sandbox",
    tags=["网站Agent中枢(75号)·沙箱面"],
)


@sbx_router.get("/healthz")
async def sandbox_health(x_role: str | None = Header(default=None)):
    _require_admin(x_role)
    from services import agent75_sandbox_service as sb
    return {"code": 0, "data": {
        "healthy": sb.healthy(), "url": sb.SANDBOX_URL,
        "kill": sb.sandbox_kill()}}


def register_agent75_routes(app):
    """注册75号·网站Agent中枢路由(任务+进化+技能+沙箱+共鸣五面)"""
    app.include_router(router)
    app.include_router(evo_router)
    app.include_router(skills_router)
    app.include_router(sbx_router)
    app.include_router(res_router)


# ---------- RA-P0: 增强型 Agent 感知认知面(共鸣) ----------

from pydantic import BaseModel as _BM2, Field as _F2  # noqa: E402
from services.agent75_resonance_service import (  # noqa: E402
    MAX_EVENTS_PER_BATCH, PerceptionRecord, ResonanceService,
    resonance_kill)

res_router = APIRouter(
    prefix="/api/v1/resonance",
    tags=["网站Agent中枢(75号)·共鸣面"],
)


def _res_guard(x_role: str | None) -> None:
    _require_admin(x_role)
    if resonance_kill():
        raise HTTPException(status_code=409, detail="resonance killed")


class EventItem(_BM2):
    event_type: str = _F2(..., min_length=2, max_length=32)
    page_id: str = _F2("", max_length=64)
    payload: dict = _F2(default_factory=dict)


class EventsRequest(_BM2):
    session_id: str = _F2(..., min_length=3, max_length=64)
    user_id: str | None = None
    events: list[EventItem] = _F2(..., min_length=1)


@res_router.post("/events")
async def ingest_events(body: EventsRequest,
                        x_role: str | None = Header(default=None)):
    """通道 B: 行为事件批量上报(≤200/批)→ 会话快照"""
    _res_guard(x_role)
    if len(body.events) > MAX_EVENTS_PER_BATCH:
        raise HTTPException(status_code=413, detail="batch too large")
    events = [PerceptionRecord(
        session_id=body.session_id, page_id=e.page_id,
        event_type=e.event_type, payload=e.payload)
        for e in body.events]
    snap = ResonanceService().run_session(
        body.session_id, events, user_id=body.user_id)
    return {"code": 0, "data": {
        "session_id": snap["session_id"],
        "emotion": snap["emotion"]["emotion"],
        "mode": snap["intent"]["mode"],
        "stickiness_score": snap["intent"]["stickiness_score"]}}


@res_router.post("/session")
async def replay_session(body: dict,
                         x_role: str | None = Header(default=None)):
    """通道 A: 75号任务轨迹回放 → 会话快照"""
    _res_guard(x_role)
    task_id = str((body or {}).get("task_id") or "")
    if not task_id:
        raise HTTPException(status_code=409, detail="task_id required")
    snap = ResonanceService().replay_task(task_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {"code": 0, "data": snap}


@res_router.get("/sessions")
async def list_res_sessions(limit: int = 20,
                            x_role: str | None = Header(default=None)):
    _res_guard(x_role)
    return {"code": 0, "data": ResonanceService().list_sessions(limit)}


@res_router.get("/decisions")
async def list_res_decisions(limit: int = 30,
                             x_role: str | None = Header(default=None)):
    """RA-P2 决策消费面: 快照决策扁平清单(安抚类置顶, 人工消费)"""
    _res_guard(x_role)
    return {"code": 0, "data": ResonanceService().recent_decisions(limit)}


# ---------- RA-P1 通道B: 公开采集端点(beacon 无 admin 头) ----------
# 安全闸: 事件白名单 + session 正则 + 批量/体积上限 + 内存滑动窗限流
#         + COLLECT 独立停 + RESONANCE_KILL 总闸; 无 PII

import re as _re2  # noqa: E402
import time as _time  # noqa: E402

from fastapi import Request as _Request  # noqa: E402

_COLLECT_EVENTS = {"click", "scroll", "hover", "error", "back", "like",
                   "share", "complete", "long_stay", "search",
                   "view_detail", "pagehide"}
_SESSION_RE = _re2.compile(r"^s_[A-Za-z0-9_]{4,64}$")
_RATE_WINDOW_S = 60
_RATE_MAX_BATCH = 30
_rate_hits: dict[str, list[float]] = {}


def _collect_kill() -> bool:
    import os
    return os.environ.get("AGENT75_RESONANCE_COLLECT", "on"
                          ).strip().lower() in ("off", "0", "false")


def _rate_ok(ip: str) -> bool:
    now = _time.time()
    hits = [t for t in _rate_hits.get(ip, [])
            if now - t < _RATE_WINDOW_S]
    if len(hits) >= _RATE_MAX_BATCH:
        _rate_hits[ip] = hits
        return False
    hits.append(now)
    _rate_hits[ip] = hits
    if len(_rate_hits) > 10000:      # 防 IP 键膨胀
        _rate_hits.clear()
    return True


@res_router.post("/collect")
async def collect_events(request: _Request):
    """公开采集: beacon 上报专用(结构白名单+限流, 无鉴权无 PII)"""
    if resonance_kill():
        raise HTTPException(status_code=409, detail="resonance killed")
    if _collect_kill():
        raise HTTPException(status_code=429, detail="collect disabled")
    ip = (request.client.host if request.client else "") or "?"
    if not _rate_ok(ip):
        raise HTTPException(status_code=429, detail="rate limited")
    try:
        body = await request.json()
    except Exception:   # noqa: BLE001
        raise HTTPException(status_code=400, detail="bad json")
    session_id = str((body or {}).get("session_id") or "")
    if not _SESSION_RE.match(session_id):
        raise HTTPException(status_code=422, detail="bad session_id")
    items = (body or {}).get("events") or []
    if not items:
        raise HTTPException(status_code=422, detail="events empty")
    if len(items) > MAX_EVENTS_PER_BATCH:
        raise HTTPException(status_code=413, detail="batch too large")
    events = []
    for e in items:
        etype = str((e or {}).get("event_type") or "")
        payload = (e or {}).get("payload") or {}
        if etype not in _COLLECT_EVENTS:
            raise HTTPException(status_code=422,
                                detail=f"event not allowed: {etype}")
        if len(str(payload)) > 2048:
            raise HTTPException(status_code=422, detail="payload too large")
        events.append(PerceptionRecord(
            session_id=session_id,
            page_id=str((e or {}).get("page_id") or "")[:64],
            event_type=etype, payload=payload))
    snap = ResonanceService().run_session(
        session_id, events, user_id=None)
    return {"code": 0, "data": {
        "session_id": snap["session_id"],
        "emotion": snap["emotion"]["emotion"],
        "mode": snap["intent"]["mode"],
        "stickiness_score": snap["intent"]["stickiness_score"]}}
