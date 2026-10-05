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
    # P1 后: planning/executing/终态/进化检查尾存 = 4 次落盘
    assert len(lines) == 4
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


# ------------------------------------------------------------
# P1/P2(2026-10-05): 模型路由历史 + 进化引擎审批闭环
# ------------------------------------------------------------

from services.agent75_orchestrator_service import ModelRouter  # noqa: E402
from services.agent75_evolution_service import (  # noqa: E402
    AgentEvolutionService)


class ProposeMockGateway(AgentModelGateway):
    """意图步/生成步/进化提议步 三态 mock"""

    PROPOSAL = ('{"type":"prompt","intent":"summary",'
                '"prompt_text":"你是升级版摘要助手，输出要点加结论。",'
                '"rationale":"修复过短回答"}')

    def __init__(self, answer=""):
        super().__init__()
        self.answer = answer
        self.seen_prompts: list[str] = []

    def call(self, model_id, system, user):
        if model_id == "glm-flash":
            return "summary", True, 3
        if "进化顾问" in system:
            return self.PROPOSAL, True, 4
        self.seen_prompts.append(system)
        return self.answer, True, 5


def test_t6_model_router_history(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "services.agent75_orchestrator_service.AGENT_TASK_DIR",
        tmp_path)
    gw = AgentModelGateway()
    router = ModelRouter(gw)
    # 类型路由: 分类→轻量档, 生成→推理档
    assert router.route("classification").model_id == "glm-flash"
    assert router.route("generation").model_id == "glm-reason"
    assert router.route("unknown_type").model_id == "glm-flash"  # 默认轻量
    # 成功率历史回写 + 持久化(重启不丢)
    router.record("glm-flash", "classification", True, 10)
    router.record("glm-flash", "classification", False, 20)
    assert (tmp_path / "router_history.json").is_file()
    router2 = ModelRouter(gw)
    recs = router2.history["glm-flash:classification"]
    assert len(recs) == 2 and recs[-1]["success"] is False
    monkeypatch.undo()


def test_t7_evolution_closed_loop(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "services.agent75_orchestrator_service.AGENT_TASK_DIR",
        tmp_path)
    # 同步 evolution service 引用的模块级常量
    monkeypatch.setattr(
        "services.agent75_evolution_service.AGENT_TASK_DIR",
        tmp_path)

    gw = ProposeMockGateway(answer="")   # 生成步空回答 → 任务失败
    o = WebsiteAgentOrchestrator(gw)
    task = o.run("总结这篇内容")
    assert task.status == "failed"
    assert task.evaluation["needs_evolution"] is True
    # 进化提议已生成 pending 候选 + 轨迹记录
    cand_ids = [t["candidate_id"] for t in task.execution_trace
                if t.get("step_id") == "evolution_propose"]
    assert cand_ids

    evo = AgentEvolutionService()
    cand = evo.get(cand_ids[0])
    assert cand["validation_status"] == "pending"
    assert cand["intent"] == "summary"

    # 覆盖未生效前: 内置 prompt
    assert "升级版" not in o._prompt_for("summary")
    # 审批生效 → 覆盖即刻可被编排器消费
    evo.approve(cand_ids[0])
    assert "升级版" in o._prompt_for("summary")
    # 下一任务实际吃到覆盖 prompt
    gw2 = ProposeMockGateway(answer="要点一：xxx；结论：好。")
    o2 = WebsiteAgentOrchestrator(gw2)
    o2.run("再总结一次")
    assert any("升级版" in s for s in gw2.seen_prompts)
    # 回滚 → 回落内置
    evo.rollback("summary")
    assert "升级版" not in o2._prompt_for("summary")
    monkeypatch.undo()


def test_t8_evolution_routes(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.agent75_routes import register_agent75_routes

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "services.agent75_orchestrator_service.AGENT_TASK_DIR",
        tmp_path)
    monkeypatch.setattr(
        "services.agent75_evolution_service.AGENT_TASK_DIR",
        tmp_path)

    app = FastAPI()
    register_agent75_routes(app)
    client = TestClient(app)
    admin = {"X-Role": "admin"}

    # 制造一个 pending 候选(经真实失败任务链)
    monkeypatch.setattr(
        "routes.agent75_routes._orchestrator",
        WebsiteAgentOrchestrator(ProposeMockGateway(answer="")))
    r = client.post("/api/v1/agent/tasks", headers=admin,
                    json={"query": "总结"})
    assert r.status_code == 200

    r = client.get("/api/v1/evolution/candidates", headers=admin)
    assert r.status_code == 200
    rows = r.json()["data"]
    assert rows and rows[0]["validation_status"] == "pending"
    eid = rows[0]["evolution_id"]

    # approve → approved; reject 已决态 → 409
    r = client.post(f"/api/v1/evolution/candidates/{eid}/approve",
                    headers=admin)
    assert r.status_code == 200
    assert r.json()["data"]["validation_status"] == "approved"
    r = client.post(f"/api/v1/evolution/candidates/{eid}/reject",
                    headers=admin)
    assert r.status_code == 409

    # rollback → active 清空; 未知候选 404
    r = client.post(f"/api/v1/evolution/candidates/{eid}/rollback",
                    headers=admin)
    assert r.status_code == 200
    r = client.post("/api/v1/evolution/candidates/evo_nope/approve",
                    headers=admin)
    assert r.status_code == 404
    monkeypatch.undo()
