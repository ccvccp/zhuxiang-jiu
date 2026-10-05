"""75号·网站自研 Agent 中枢测试(P0 编排闭环, 2026-10-05)

T1 模型注册表(双档+类型区分)
T2 编排全链(mock 网关): 意图识别→生成→评估→completed, 轨迹 3 步
T3 意图回落(分类失败→chat)与 Critic 打回(空回答→failed)
T4 持久化: 任务 JSON 可回读 + tasks.jsonl 追加
T5 路由: 200/回显 + 非 admin 403 + kill 409 + 404
"""

import json

import pytest

from services.agent75_orchestrator_service import (
    AgentModelGateway,
    ModelType,
    WebsiteAgentOrchestrator,
)


class MockGateway(AgentModelGateway):
    """离线网关: 意图步返回固定词, 生成步按 prompt 前缀路由输出"""

    def __init__(self, intent_reply="summary", answer="好的，这是要点简报。"):
        super().__init__()
        self.intent_reply = intent_reply
        self.answer = answer

    def call(self, model_id, system, user):
        if model_id == "glm-flash":
            return self.intent_reply, True, 3
        return self.answer, True, 5


@pytest.fixture(autouse=True)
def _tmp_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.agent75_orchestrator_service.AGENT_TASK_DIR",
        tmp_path)
    return tmp_path


def test_t1_model_registry():
    gw = AgentModelGateway()
    assert set(gw.models) == {"glm-flash", "glm-reason"}
    assert (gw.models["glm-flash"].model_type
            is ModelType.LIGHTWEIGHT)
    assert (gw.models["glm-reason"].model_type
            is ModelType.REASONING)
    assert gw.get("no-such") is None


def test_t2_orchestrator_happy_path():
    o = WebsiteAgentOrchestrator(MockGateway())
    task = o.run("帮我总结这篇产品介绍", user_id="u_001")
    assert task.status == "completed"
    assert task.intent == "summary"
    assert len(task.execution_trace) == 2      # plan + execute
    assert task.execution_trace[0]["output"] == "summary"
    assert task.evaluation["issues"] == []
    assert task.answer


def test_t3_intent_fallback_and_critic():
    # 意图分类输出越界词 → 回落 chat
    o = WebsiteAgentOrchestrator(
        MockGateway(intent_reply="乱答", answer="这是助手的回答内容。"))
    task = o.run("你好")
    assert task.intent == "chat"

    # Critic 打回: 空回答 → failed + issues
    o2 = WebsiteAgentOrchestrator(MockGateway(answer=""))
    task2 = o2.run("随便问点")
    assert task2.status == "failed"
    assert "empty_answer" in task2.evaluation["issues"]


def test_t4_persistence(tmp_path):
    o = WebsiteAgentOrchestrator(MockGateway())
    task = o.run("起草一段寒露文案")
    p = tmp_path / f"{task.task_id}.json"
    assert p.is_file()
    back = o.load(task.task_id)
    assert back.status == "completed" and back.intent == "summary"
    lines = (tmp_path / "tasks.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(lines) == 3                    # planning/executing/终态
    assert json.loads(lines[-1])["status"] == "completed"
    assert o.load("t_nope") is None
    assert len(o.list_recent()) == 1


def test_t5_routes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.agent75_routes import register_agent75_routes

    app = FastAPI()
    register_agent75_routes(app)
    client = TestClient(app)
    admin = {"X-Role": "admin"}

    monkeypatch.setattr(
        "routes.agent75_routes._orchestrator",
        WebsiteAgentOrchestrator(MockGateway()))

    # 非 admin 403
    r = client.post("/api/v1/agent/tasks", json={"query": "你好"})
    assert r.status_code == 403

    # 正常链 200 + 回显
    r = client.post("/api/v1/agent/tasks", headers=admin,
                    json={"query": "总结一下", "user_id": "u_1"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "completed" and d["task_id"].startswith("t_")

    # 详情 + 清单
    r = client.get(f"/api/v1/agent/tasks/{d['task_id']}",
                   headers=admin)
    assert r.status_code == 200
    r = client.get("/api/v1/agent/tasks", headers=admin)
    assert r.status_code == 200

    # 404
    r = client.get("/api/v1/agent/tasks/t_nope", headers=admin)
    assert r.status_code == 404

    # kill 一键回退 409
    monkeypatch.setenv("AGENT75_KILL", "on")
    r = client.post("/api/v1/agent/tasks", headers=admin,
                    json={"query": "再来一条"})
    assert r.status_code == 409
