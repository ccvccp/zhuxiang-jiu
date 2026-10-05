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


def register_agent75_routes(app):
    """注册75号·网站Agent中枢路由(任务+进化+技能三面)"""
    app.include_router(router)
    app.include_router(evo_router)
    app.include_router(skills_router)
