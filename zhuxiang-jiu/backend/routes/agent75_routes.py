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


def register_agent75_routes(app):
    """注册75号·网站Agent中枢路由"""
    app.include_router(router)
