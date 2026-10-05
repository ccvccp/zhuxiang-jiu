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


# ------------------------------------------------------------
# P3a(2026-10-05): Skill 层(注册表/工作流/生命周期/淘汰/进化技能)
# ------------------------------------------------------------

from services.agent75_skill_service import (  # noqa: E402
    SkillDefinition, SkillKind, SkillRuntime, SkillStatus)


@pytest.fixture()
def _skill_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.agent75_orchestrator_service.AGENT_TASK_DIR",
        tmp_path)
    monkeypatch.setattr(
        "services.agent75_evolution_service.AGENT_TASK_DIR",
        tmp_path)
    monkeypatch.setattr(
        "services.agent75_skill_service.AGENT_TASK_DIR",
        tmp_path)
    return tmp_path


def test_t9_skill_registry_lifecycle_demote(_skill_dirs):
    rt = SkillRuntime()
    assert {"intent_classify", "answer_chat", "answer_summary",
            "draft_copy", "critic_review"} <= set(rt.skills)
    # slot 解析
    assert rt.resolve("classify").skill_id == "intent_classify"
    assert rt.resolve("generate", "draft").skill_id == "draft_copy"
    # 生命周期可逆: deprecate → 该槽无 active → activate 恢复
    rt.set_status("draft_copy", SkillStatus.DEPRECATED)
    assert rt.resolve("generate", "draft") is None
    rt.set_status("draft_copy", SkillStatus.ACTIVE)
    assert rt.resolve("generate", "draft").skill_id == "draft_copy"
    # 淘汰: 连续 20 败 → 自动降 testing(19 次不动, 第 20 次触发)
    for _ in range(19):
        assert rt.record_invocation("draft_copy", False, 8) is None
    d = rt.record_invocation("draft_copy", False, 8)
    assert d and d["skill_id"] == "draft_copy"
    assert rt.get("draft_copy").status is SkillStatus.TESTING
    # 重启不丢(文件权威)
    rt2 = SkillRuntime()
    assert rt2.get("draft_copy").status is SkillStatus.TESTING


def test_t10_skill_layer_execution_and_kill(_skill_dirs, monkeypatch):
    o = WebsiteAgentOrchestrator(MockGateway())
    task = o.run("帮我总结这篇内容")
    assert task.status == "completed"
    # 轨迹走技能层(带 skill_id), 行为与 P0 等价
    assert task.execution_trace[0]["skill_id"] == "intent_classify"
    assert task.execution_trace[1]["skill_id"] == "answer_summary"
    # metrics 已回写
    rt = SkillRuntime()
    assert rt.get("intent_classify").metrics["invocations"] == 1
    # SKILL_KILL → 整层旁路回落 P0 直连(降级不降能):
    # 直连轨迹的 skill_id 是语义标签("intent"), 非注册表技能 id
    monkeypatch.setenv("AGENT75_SKILL_KILL", "on")
    t2 = WebsiteAgentOrchestrator(MockGateway()).run("再来一条")
    assert t2.status == "completed"
    assert t2.execution_trace[0]["skill_id"] == "intent"
    monkeypatch.delenv("AGENT75_SKILL_KILL")


class SkillProposeMockGateway(AgentModelGateway):
    """意图=draft / 生成空回答 / 进化提议=skill 型"""

    PROPOSAL = ('{"type":"skill","intent":"draft",'
                '"prompt_text":"你是进化文案技能，写有故事感的短文案。",'
                '"model_id":"glm-reason","rationale":"补 draft 生成位"}')

    def call(self, model_id, system, user):
        if model_id == "glm-flash":
            return "draft", True, 3
        if "进化顾问" in system:
            return self.PROPOSAL, True, 4
        return "", True, 5


def test_t11_evolution_skill_loop(_skill_dirs):
    task = WebsiteAgentOrchestrator(
        SkillProposeMockGateway()).run("写一段文案")
    assert task.status == "failed"
    cand_ids = [t["candidate_id"] for t in task.execution_trace
                if t.get("step_id") == "evolution_propose"]
    assert cand_ids
    evo = AgentEvolutionService()
    assert evo.get(cand_ids[0])["type"] == "skill"

    # approve → 注册表落 testing(不直接 active——灰度闸门)
    evo.approve(cand_ids[0])
    rt = SkillRuntime()
    sid = f"evo_{cand_ids[0]}_draft"
    s = rt.get(sid)
    assert s.status is SkillStatus.TESTING
    assert s.kind is SkillKind.PROMPT_TOOL and s.slot == "generate"
    # testing 不入主链: draft 生成位仍是内置
    assert rt.resolve("generate", "draft").skill_id == "draft_copy"
    # 人工 activate + 内置让位 → 进化技能上岗
    rt.set_status("draft_copy", SkillStatus.DEPRECATED)
    rt.set_status(sid, SkillStatus.ACTIVE)
    assert rt.resolve("generate", "draft").skill_id == sid
    # 候选回滚 → 该来源技能降 deprecated(新实例复读文件)
    out = evo.rollback_candidate(cand_ids[0])
    assert out["type"] == "skill" and out["skill_id"] == sid
    assert SkillRuntime().get(sid).status is SkillStatus.DEPRECATED


# ------------------------------------------------------------
# P3b(2026-10-05): 沙箱容器 + code_tool
# ------------------------------------------------------------

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "sandbox_app",
    Path(__file__).resolve().parent.parent / "sandbox" / "sandbox_app.py")
sandbox_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sandbox_app)


def test_t12_sandbox_gates():
    """静态闸: 白名单 import/危险名目/run 入口"""
    ok, _ = sandbox_app.check_code(
        "import json\n\ndef run(x):\n    return {'answer': 'a'}")
    assert ok
    for bad in ("import os\n\ndef run(x):\n    return {}",
                "def run(x):\n    return __import__('os')",
                "def run(x):\n    return eval('1')",
                "def run(x):\n    f = open('/etc/passwd')\n    return {}",
                "def notrun(x):\n    return {}"):
        ok, err = sandbox_app.check_code(bad)
        assert not ok, bad
        assert err


def test_t12b_sandbox_exec_real():
    """执行闸(本地真实子进程): happy/超时硬杀/递归炸弹"""
    good = ("import json\n\n"
            "def run(x):\n"
            "    return {'answer': '程序回答: ' + str(x.get('query'))}")
    r = sandbox_app.do_exec(good, {"query": "hi"}, 3000)
    assert r["ok"] is True and r["result"]["answer"].startswith("程序回答")

    bomb = "def run(x):\n    while True:\n        pass"
    r = sandbox_app.do_exec(bomb, {}, 800)
    assert r["ok"] is False and r["error"] == "timeout"

    rec = ("def run(x):\n    def f():\n        return f()\n    return f()")
    r = sandbox_app.do_exec(rec, {}, 3000)
    assert r["ok"] is False and r["error"].startswith("exit_")


def _mk_code_tool(rt, sid="codex_summary", intent="summary"):
    rt.upsert(SkillDefinition(
        skill_id=sid, name="代码技能", status=SkillStatus.ACTIVE,
        kind=SkillKind.CODE_TOOL, slot="generate", intent=intent,
        spec={"code": "def run(x):\n    return {'answer': 'x'}",
              "timeout_ms": 1000},
        source="admin"))
    rt.set_status("answer_summary", SkillStatus.DEPRECATED)


def test_t13_code_tool_paths(_skill_dirs, monkeypatch):
    from services import agent75_sandbox_service as sb

    _mk_code_tool(SkillRuntime())   # code_tool 上岗 + 内置 summary 让位

    # ① 沙箱成功: 轨迹 model_id=sandbox, 任务 completed
    monkeypatch.setattr(sb, "exec_code",
                        lambda *a, **k: {"ok": True, "result": {
                            "answer": "代码生成的摘要回答。"},
                            "latency_ms": 42})
    t = WebsiteAgentOrchestrator(MockGateway()).run("总结一下")
    assert t.status == "completed"
    gen = t.execution_trace[1]
    assert gen["model_id"] == "sandbox" and gen["skill_id"] == "codex_summary"

    # ② 沙箱失败 → 同槽 prompt_tool 兜底(fallback_from 留痕)
    monkeypatch.setattr(sb, "exec_code",
                        lambda *a, **k: {"ok": False,
                                         "error": "timeout",
                                         "latency_ms": 800})
    t2 = WebsiteAgentOrchestrator(MockGateway()).run("再总结")
    assert t2.status == "completed"
    assert t2.execution_trace[1]["fallback_from"] == "codex_summary"

    # ③ SANDBOX_KILL → 拒跑直落兜底 + 审计行落盘
    monkeypatch.setenv("AGENT75_SANDBOX_KILL", "on")
    t3 = WebsiteAgentOrchestrator(MockGateway()).run("三次总结")
    assert t3.status == "completed"
    assert t3.execution_trace[1]["fallback_from"] == "codex_summary"
    lines = (_skill_dirs / "sandbox_runs.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2      # ①② 各一条审计
    monkeypatch.delenv("AGENT75_SANDBOX_KILL")


def test_t14_code_tool_register_gate(_skill_dirs, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.agent75_routes import register_agent75_routes
    from services import agent75_sandbox_service as sb

    app = FastAPI()
    register_agent75_routes(app)
    client = TestClient(app)
    admin = {"X-Role": "admin"}
    good_code = ("import json\n\n"
                 "def run(x):\n    return {'answer': '格式化回答'}")
    body = {"skill_id": "fmt_tool", "name": "格式化",
            "intent": "summary", "code": good_code,
            "sample_input": {"query": "t"}}

    # 静态预检失败(import os) → 409 不入库
    r = client.post("/api/v1/skills", headers=admin,
                    json={**body, "skill_id": "os_tool",
                          "code": "import os\n\ndef run(x):\n"
                                  "    return {'answer': 'a'}"})
    assert r.status_code == 409 and "预检" in r.json()["detail"]

    # dry-run 失败 → 409
    monkeypatch.setattr(sb, "exec_code",
                        lambda *a, **k: {"ok": False,
                                         "error": "timeout",
                                         "latency_ms": 1})
    r = client.post("/api/v1/skills", headers=admin, json=body)
    assert r.status_code == 409 and "dry-run" in r.json()["detail"]

    # dry-run 通过 → 200 落 testing
    monkeypatch.setattr(sb, "exec_code",
                        lambda *a, **k: {"ok": True, "result": {
                            "answer": "格式化回答"}, "latency_ms": 5})
    r = client.post("/api/v1/skills", headers=admin, json=body)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "testing" and d["kind"] == "code_tool"
    assert SkillRuntime().get("fmt_tool").kind is SkillKind.CODE_TOOL

    # 重复 id → 409; kill → 409
    r = client.post("/api/v1/skills", headers=admin, json=body)
    assert r.status_code == 409
    monkeypatch.setenv("AGENT75_SANDBOX_KILL", "on")
    r = client.post("/api/v1/skills", headers=admin,
                    json={**body, "skill_id": "fmt_tool2"})
    assert r.status_code == 409
    monkeypatch.delenv("AGENT75_SANDBOX_KILL")
