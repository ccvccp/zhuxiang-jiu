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


# ------------------------------------------------------------
# P3c(2026-10-05): 影子双跑灰度 + ready 转正判据
# ------------------------------------------------------------

def test_t15_shadow_dual_run(_skill_dirs, monkeypatch):
    import time

    class ShadowGW(MockGateway):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def call(self, model_id, system, user):
            if model_id != "glm-flash":
                self.prompts.append(system)
            return super().call(model_id, system, user)

    SkillRuntime().upsert(SkillDefinition(
        skill_id="shadow_gen", name="影子技能",
        status=SkillStatus.TESTING, kind=SkillKind.PROMPT_TOOL,
        slot="generate", intent="summary",
        spec={"model_id": "glm-reason", "prompt_template": "影子标记专用提示"},
        source="admin"))
    gw = ShadowGW()
    task = WebsiteAgentOrchestrator(gw).run("总结这篇")
    # 主链零污染: 结果仍来自内置 active 技能
    assert task.status == "completed"
    assert task.execution_trace[1]["skill_id"] == "answer_summary"
    # 影子已跑(轮询等后台线程落盘)
    for _ in range(50):
        if SkillRuntime().get("shadow_gen").metrics.get(
                "shadow_runs", 0) >= 1:
            break
        time.sleep(0.1)
    s = SkillRuntime().get("shadow_gen")
    assert s.metrics["shadow_runs"] == 1
    assert any("影子标记" in p for p in gw.prompts)

    # SHADOW=off → testing 静默(影子计数不增)
    monkeypatch.setenv("AGENT75_SKILL_SHADOW", "off")
    WebsiteAgentOrchestrator(ShadowGW()).run("再总结")
    time.sleep(0.4)
    assert SkillRuntime().get("shadow_gen").metrics["shadow_runs"] == 1
    monkeypatch.delenv("AGENT75_SKILL_SHADOW")


def test_t16_ready_gate(_skill_dirs):
    rt = SkillRuntime()
    act = rt.get("answer_summary")
    act.metrics["recent"] = [{"ok": True, "latency_ms": 100}] * 10
    act.metrics["avg_latency_ms"] = 100

    def mk(runs, ok_n, lat):
        s = SkillDefinition(
            skill_id=f"t_{ok_n}_{lat}", name="影子", 
            status=SkillStatus.TESTING, kind=SkillKind.PROMPT_TOOL,
            slot="generate", intent="summary",
            spec={"model_id": "glm-reason", "prompt_template": "p" * 12},
            source="admin")
        s.metrics["shadow_runs"] = runs
        s.metrics["shadow_recent"] = [
            {"ok": i < ok_n, "latency_ms": lat} for i in range(runs)]
        return s

    rt.upsert(mk(20, 20, 120))   # 量足/率达标/延迟 1.2x → ready
    rt.upsert(mk(15, 15, 100))   # 量不足
    rt.upsert(mk(20, 14, 100))   # 率 0.7 < 1.0-5pp
    rt.upsert(mk(20, 20, 200))   # 延迟 2x > 1.5x
    flags = {r["skill_id"]: r.get("ready")
             for r in rt.list() if r["status"] == "testing"}
    assert flags["t_20_120"] is True
    assert flags["t_15_100"] is False
    assert flags["t_14_100"] is False
    assert flags["t_20_200"] is False


# ------------------------------------------------------------
# RA-P0(2026-10-05): 增强型 Agent 感知认知核心(共鸣面)
# ------------------------------------------------------------

from services.agent75_resonance_service import (  # noqa: E402
    DualAxisScorer, EmotionEngine, EmotionState, PerceptionRecord,
    ResonanceService, transpose_agent_task)
from services.agent75_orchestrator_service import (  # noqa: E402
    TaskRecord, TaskStatus)


def _ev(etype, session="s1", **payload):
    return PerceptionRecord(session_id=session, event_type=etype,
                            payload=payload)


def test_t17_emotion_engine_and_scorer():
    eng = EmotionEngine()
    # 焦虑: 快速连点(interval<300ms)
    snap = eng.compute([_ev("click", interval_ms=120),
                        _ev("click", interval_ms=200),
                        _ev("scroll")])
    assert snap.emotion is EmotionState.ANXIOUS and snap.anxiety_score > 0.5
    # 困惑: 返回+报错
    snap = eng.compute([_ev("back"), _ev("error"), _ev("scroll")])
    assert snap.emotion is EmotionState.CONFUSED
    # 愉悦: 完成+长停留
    snap = eng.compute([_ev("complete"), _ev("long_stay")])
    assert snap.emotion is EmotionState.PLEASANT
    # 挫败: 报错+重试
    snap = eng.compute([_ev("error"), _ev("click", retry=True)])
    assert snap.emotion is EmotionState.FRUSTRATED
    # 双螺旋: 粘性公式 + 四模式
    sc = DualAxisScorer()
    assert sc.compute(1.0, 1.0) == 1.0
    assert sc.decide_intent(0.9, 0.2)["mode"] == "efficiency"
    assert sc.decide_intent(0.2, 0.9)["mode"] == "explore"
    assert sc.decide_intent(0.8, 0.8)["mode"] == "flow"
    assert sc.decide_intent(0.5, 0.5)["mode"] == "balanced"
    assert abs(sc.decide_intent(0.8, 0.6)["stickiness_score"]
               - round(0.6 * 0.8 + 0.4 * 0.6, 4)) < 1e-9


def test_t18_transpose_agent_task(_skill_dirs):
    # 失败任务(fallback 意图 + 生成失败重试 + 进化提议) → back/error/retry
    task = TaskRecord(
        task_id="t_x1", query="q", status=TaskStatus.FAILED,
        execution_trace=[
            {"step_id": "plan_intent", "status": "fallback"},
            {"step_id": "exec_answer", "status": "failed", "retries": 1},
            {"step_id": "evolution_propose", "status": "proposed"}])
    evs = transpose_agent_task(task)
    types = [e.event_type for e in evs]
    assert "back" in types and "error" in types
    assert any(e.payload.get("retry") for e in evs)
    # 快照: 失败任务 → 挫败主导 + 安抚决策
    snap = ResonanceService().run_session("task_t_x1", evs)
    assert snap["emotion"]["emotion"] == "frustrated"
    assert snap["decisions"]["incentive"]["type"] == "support"
    # 完成任务 → complete 事件 → 愉悦/物质分
    ok_task = TaskRecord(
        task_id="t_x2", query="q", status=TaskStatus.COMPLETED,
        execution_trace=[{"step_id": "plan_intent", "status": "success"},
                         {"step_id": "exec_answer", "status": "success"}])
    evs2 = transpose_agent_task(ok_task)
    assert "complete" in [e.event_type for e in evs2]
    snap2 = ResonanceService().run_session("task_t_x2", evs2)
    assert snap2["emotion"]["pleasure_score"] > 0


def test_t19_resonance_routes(_skill_dirs, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.agent75_routes import register_agent75_routes

    app = FastAPI()
    register_agent75_routes(app)
    client = TestClient(app)
    admin = {"X-Role": "admin"}

    # 通道 B: 行为事件批量上报
    r = client.post("/api/v1/resonance/events", headers=admin, json={
        "session_id": "s_web_001", "user_id": "u_1",
        "events": [{"event_type": "click", "page_id": "home",
                    "payload": {"interval_ms": 150}},
                   {"event_type": "click", "payload": {"interval_ms": 180}},
                   {"event_type": "scroll"}]})
    assert r.status_code == 200
    assert r.json()["data"]["emotion"] == "anxious"

    # 通道 A: 任务回放(先用真实链造一个任务)
    monkeypatch.setattr(
        "routes.agent75_routes._orchestrator",
        WebsiteAgentOrchestrator(ProposeMockGateway(answer="")))
    rt = client.post("/api/v1/agent/tasks", headers=admin,
                     json={"query": "总结"})
    tid = rt.json()["data"]["task_id"]
    r = client.post("/api/v1/resonance/session", headers=admin,
                    json={"task_id": tid})
    assert r.status_code == 200
    assert r.json()["data"]["emotion"]["emotion"] == "frustrated"

    # 清单 + 404 + kill 409
    r = client.get("/api/v1/resonance/sessions", headers=admin)
    assert r.status_code == 200 and len(r.json()["data"]) >= 2
    r = client.post("/api/v1/resonance/session", headers=admin,
                    json={"task_id": "t_nope"})
    assert r.status_code == 404
    monkeypatch.setenv("AGENT75_RESONANCE_KILL", "on")
    r = client.get("/api/v1/resonance/sessions", headers=admin)
    assert r.status_code == 409
    monkeypatch.delenv("AGENT75_RESONANCE_KILL")


# ------------------------------------------------------------
# RA-P1(2026-10-05): 通道A自动挂接 + 通道B公开采集端点
# ------------------------------------------------------------

def test_t20_resonance_auto_hook(_skill_dirs, monkeypatch):
    import time as _t

    task = WebsiteAgentOrchestrator(MockGateway()).run("自动共鸣测试")
    sid = f"task_{task.task_id}"
    for _ in range(50):    # 轮询通道A快照自动落盘
        if any(r.get("session_id") == sid
               for r in ResonanceService().list_sessions(50)):
            break
        _t.sleep(0.1)
    assert any(r["session_id"] == sid
               for r in ResonanceService().list_sessions(50))

    # AUTO=off → 独立停通道A
    monkeypatch.setenv("AGENT75_RESONANCE_AUTO", "off")
    t2 = WebsiteAgentOrchestrator(MockGateway()).run("再来一条")
    _t.sleep(0.5)
    sids = [r["session_id"] for r in ResonanceService().list_sessions(50)]
    assert f"task_{t2.task_id}" not in sids
    monkeypatch.delenv("AGENT75_RESONANCE_AUTO")

    # RESONANCE_KILL 总闸优先
    monkeypatch.setenv("AGENT75_RESONANCE_KILL", "on")
    t3 = WebsiteAgentOrchestrator(MockGateway()).run("总闸测试")
    _t.sleep(0.5)
    sids = [r["session_id"] for r in ResonanceService().list_sessions(50)]
    assert f"task_{t3.task_id}" not in sids
    monkeypatch.delenv("AGENT75_RESONANCE_KILL")


def test_t21_collect_endpoint(_skill_dirs, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agent75_routes as R
    from routes.agent75_routes import register_agent75_routes

    app = FastAPI()
    register_agent75_routes(app)
    client = TestClient(app)
    body = {"session_id": "s_web_t21",
            "events": [{"event_type": "click",
                        "payload": {"interval_ms": 150}},
                       {"event_type": "scroll",
                        "payload": {"scroll_depth": 0.4}}]}

    def fresh():
        R._rate_hits.clear()
        return client.post("/api/v1/resonance/collect", json=body)

    # 合法 → 200 快照落盘
    r = fresh()
    assert r.status_code == 200
    assert r.json()["data"]["emotion"] == "anxious"
    # 白名单外事件 / 坏 session / 空事件 / 超批量
    R._rate_hits.clear()
    r = client.post("/api/v1/resonance/collect", json={
        **body, "events": [{"event_type": "steal_cookie",
                            "payload": {}}]})
    assert r.status_code == 422
    R._rate_hits.clear()
    r = client.post("/api/v1/resonance/collect",
                    json={**body, "session_id": "bad!!"})
    assert r.status_code == 422
    R._rate_hits.clear()
    r = client.post("/api/v1/resonance/collect",
                    json={**body, "events": []})
    assert r.status_code == 422
    R._rate_hits.clear()
    r = client.post("/api/v1/resonance/collect", json={
        **body, "events": [{"event_type": "scroll", "payload": {}}] * 201})
    assert r.status_code == 413
    # 限流: 60s 窗 30 批
    R._rate_hits.clear()
    ok = 0
    for _ in range(31):
        rr = client.post("/api/v1/resonance/collect", json={
            **body, "session_id": "s_web_t21"})
        if rr.status_code == 200:
            ok += 1
        else:
            assert rr.status_code == 429
            break
    assert ok == 30
    R._rate_hits.clear()
    # COLLECT=off → 429; RESONANCE_KILL 总闸 → 409
    monkeypatch.setenv("AGENT75_RESONANCE_COLLECT", "off")
    r = client.post("/api/v1/resonance/collect", json=body)
    assert r.status_code == 429
    monkeypatch.delenv("AGENT75_RESONANCE_COLLECT")
    R._rate_hits.clear()
    monkeypatch.setenv("AGENT75_RESONANCE_KILL", "on")
    r = client.post("/api/v1/resonance/collect", json=body)
    assert r.status_code == 409
    monkeypatch.delenv("AGENT75_RESONANCE_KILL")


# ------------------------------------------------------------
# RA-P2(2026-10-05): 决策接线(安抚注入/决策清单/连击进化提议)
# ------------------------------------------------------------

from services.agent75_resonance_service import (  # noqa: E402
    ResonanceService as _RS)


def _mk_session(rs, sid, user_id, events):
    return rs.run_session(sid, events, user_id=user_id)


def _frustrated_events(sid):
    # error+retry → frustration 1.0 严格占优(避开与困惑平手)
    return [_ev("error", session=sid), _ev("click", session=sid, retry=True),
            _ev("scroll", session=sid)]


def test_t22_should_comfort_and_decisions(_skill_dirs):
    rs = _RS()
    # u_fru 上会话挫败 / u_ok 愉悦 / 陌生人 → 判定三态
    _mk_session(rs, "task_t_f1", "u_fru", _frustrated_events("task_t_f1"))
    _mk_session(rs, "task_t_f2", "u_ok",
                [_ev("complete", session="task_t_f2"),
                 _ev("long_stay", session="task_t_f2")])
    assert rs.should_comfort("u_fru") is True
    assert rs.should_comfort("u_ok") is False
    assert rs.should_comfort("u_nope") is False
    assert rs.should_comfort(None) is False
    # 决策清单: 安抚类置顶 + 字段齐
    rows = rs.recent_decisions(10)
    assert rows, "决策清单为空"
    top = rows[0]
    assert {"session_id", "emotion", "strategy", "ui_layout",
            "incentive", "incentive_message"} <= set(top)
    support_rows = [r for r in rows if r.get("incentive") == "support"]
    assert support_rows and rows.index(support_rows[0]) < \
        next(i for i, r in enumerate(rows) if r.get("incentive") != "support")


def test_t23_comfort_injection(_skill_dirs, monkeypatch):
    class ComfortGW(MockGateway):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def call(self, model_id, system, user):
            if model_id != "glm-flash":
                self.prompts.append(system)
            return super().call(model_id, system, user)

    _RS().run_session(
        "task_t_cf1", [_ev("error"), _ev("click", retry=True)],
        user_id="u_sad")

    gw = ComfortGW()
    task = WebsiteAgentOrchestrator(gw).run("帮帮我", user_id="u_sad")
    assert task.status == "completed"
    assert task.execution_trace[1].get("comfort") is True
    assert any("暖心话" in p for p in gw.prompts)

    # 愉悦用户/无用户 → 不注入
    t2 = WebsiteAgentOrchestrator(ComfortGW()).run("正常问题",
                                                   user_id="u_ok2")
    assert t2.execution_trace[1].get("comfort") is False
    # COMFORT=off → 独立停
    monkeypatch.setenv("AGENT75_COMFORT", "off")
    t3 = WebsiteAgentOrchestrator(ComfortGW()).run("再帮帮我",
                                                   user_id="u_sad")
    assert t3.execution_trace[1].get("comfort") is False
    monkeypatch.delenv("AGENT75_COMFORT")


def test_t24_frustrated_streak_evolution(_skill_dirs):
    import services.agent75_resonance_service as RS
    rs = _RS()
    # 连击 <3 → 不提议
    assert rs.propose_evolution(gateway=AgentModelGateway()) is None
    # 造 3 连挫败(最新在前——追加即最新)
    for i in range(3):
        sid = f"task_t_st{i}"
        _mk_session(rs, sid, None, _frustrated_events(sid))
    out = rs.propose_evolution(
        gateway=ProposeMockGateway(answer=""))
    assert out and out["type"] == "prompt" \
        and out["validation_status"] == "pending"
    # 节流: 立即再提议 → None(600s 窗口)
    assert rs.propose_evolution(
        gateway=ProposeMockGateway(answer="")) is None
    # 候选已入进化面
    from services.agent75_evolution_service import AgentEvolutionService
    evo = AgentEvolutionService()
    assert any(c.get("source_task_id") == out["source_task_id"]
               for c in evo.list())


# ------------------------------------------------------------
# 76号 DP0(2026-10-05): 短剧引流·剧集编排层
# ------------------------------------------------------------

def test_t25_drama_series_service():
    import asyncio
    from repositories.promo_repository import PromoRepository
    from services.drama76_series_service import DramaSeriesService

    async def run():
        shared = PromoRepository()      # 内存态共享(生产为 Redis 权威)
        svc = DramaSeriesService(repo=shared)
        d = await svc.create({
            "drama_id": "drama_t1", "title": "测试剧",
            "free_episodes": 2, "status": "online",
            "episodes": [
                {"episode_id": "101", "title": "第1集",
                 "video_url": "/videos/drama/a.mp4"},
                {"episode_id": "102", "title": "第2集",
                 "video_url": "/videos/drama/b.mp4"},
                {"episode_id": "103", "title": "第3集",
                 "video_url": "/videos/drama/c.mp4"}]})
        assert len(d.episodes) == 3 and d.free_episodes == 2
        # 跨实例可读(生产 Redis 同态)
        rows = await DramaSeriesService(repo=shared).list_online()
        assert any(r["drama_id"] == "drama_t1" for r in rows)
        got = await DramaSeriesService(repo=shared).get("drama_t1")
        eps = svc.episode_states(got)
        assert [e["status"] for e in eps] == ["free", "free", "locked"]
        assert eps[2]["is_checkpoint"] is True
        assert eps[0]["is_checkpoint"] is False
        assert eps[2]["unlock_methods"] == ["register", "invite", "member"]
        assert eps[2]["video_url"] == ""       # 锁集不暴露地址
        assert eps[0]["video_url"].endswith("a.mp4")
        # 会员视角全解锁
        assert all(e["is_unlocked"] for e in
                   svc.episode_states(got, is_member=True))
        # free >= 总集数 → 拒绝(必须至少一集卡点)
        try:
            await svc.create({"title": "x", "free_episodes": 3,
                              "episodes": [{"episode_id": "1"}] * 2})
            raise AssertionError("应拒绝")
        except ValueError:
            pass

    asyncio.run(run())


def test_t26_drama_routes(monkeypatch):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from repositories.promo_repository import PromoRepository
    from routes.drama76_routes import register_drama76_routes
    from services.drama76_series_service import DramaSeriesService

    shared = PromoRepository()
    monkeypatch.setattr(
        "routes.drama76_routes._svc", DramaSeriesService(repo=shared))
    app = FastAPI()
    register_drama76_routes(app)
    client = TestClient(app)
    admin = {"X-Role": "admin"}

    # 组剧: 非 admin 403 / admin 200
    body = {"drama_id": "drama_t2", "title": "路由测试剧",
            "free_episodes": 1, "status": "online",
            "episodes": [{"episode_id": "201", "video_url": "/v/a.mp4"},
                         {"episode_id": "202", "video_url": "/v/b.mp4"}]}
    r = client.post("/api/v1/dramas", json=body)
    assert r.status_code == 403
    r = client.post("/api/v1/dramas", headers=admin, json=body)
    assert r.status_code == 200

    # 公开 GET(游客浏览口径): 清单/详情/分集(store 类级共享, 容错筛)
    r = client.get("/api/v1/dramas")
    assert r.status_code == 200
    assert any(x["drama_id"] == "drama_t2" for x in r.json()["data"])
    r = client.get("/api/v1/dramas/drama_t2/episodes")
    assert r.status_code == 200
    eps = r.json()["data"]["episodes"]
    assert [e["status"] for e in eps] == ["free", "locked"]
    assert eps[1]["is_checkpoint"] is True
    # 404 / 空集 409
    r = client.get("/api/v1/dramas/nope/episodes")
    assert r.status_code == 404
    r = client.post("/api/v1/dramas", headers=admin,
                    json={"title": "空", "episodes": []})
    assert r.status_code == 409


# ------------------------------------------------------------
# 76号 DP1(2026-10-05): 注册解锁 + 卡点配置 + 事件扩容
# ------------------------------------------------------------

from repositories.promo_repository import (  # noqa: E402
    PromoRepository as _PR)

_DP1_REPO = _PR()


def _SHARED_REPO_DP1():
    return _DP1_REPO

def test_t27_unlock_service():
    import asyncio
    from repositories.promo_repository import PromoRepository
    from services.drama76_series_service import DramaSeriesService

    async def run():
        shared = PromoRepository()
        svc = DramaSeriesService(repo=shared)
        await svc.create({
            "drama_id": "drama_dp1", "title": "解锁测试剧",
            "free_episodes": 2, "status": "online",
            "episodes": [
                {"episode_id": "301", "video_url": "/v/1.mp4"},
                {"episode_id": "302", "video_url": "/v/2.mp4"},
                {"episode_id": "303", "video_url": "/v/3.mp4"}]})
        # 解锁卡点集(剧级) + src 归因
        drama, rec = await svc.unlock("u_9", "303", "register", "A-TEST1")
        assert rec["drama_id"] == "drama_dp1" and rec["src"] == "A-TEST1"
        assert rec["unlock_id"] == f"ul_u_9_drama_dp1"
        # 幂等: 重复解锁返回同一记录
        _, rec2 = await svc.unlock("u_9", "303", "register")
        assert rec2["unlock_id"] == rec["unlock_id"]
        assert await svc.unlocked_dramas_of("u_9") == {"drama_dp1"}
        # 状态: 解锁后全集可看, 锁集 url 暴露
        eps = svc.episode_states(drama, unlocked_dramas={"drama_dp1"})
        assert all(e["is_unlocked"] for e in eps)
        assert eps[2]["video_url"].endswith("3.mp4")
        # 免费集不可解锁
        try:
            await svc.unlock("u_9", "301")
            raise AssertionError("应拒绝")
        except ValueError:
            pass
        # 未知集
        try:
            await svc.unlock("u_9", "999")
            raise AssertionError("应 404")
        except KeyError:
            pass

    asyncio.run(run())


def test_t28_checkpoint_route(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from repositories.promo_repository import PromoRepository
    from routes.drama76_routes import register_drama76_routes
    from services.drama76_series_service import DramaSeriesService

    async def seed():
        await DramaSeriesService(repo=_SHARED_REPO_DP1()).create({
            "drama_id": "drama_cp", "title": "卡点测试剧",
            "free_episodes": 1, "status": "online",
            "episodes": [
                {"episode_id": "401", "video_url": "/v/1.mp4"},
                {"episode_id": "402", "video_url": "/v/2.mp4"}]})

    import asyncio
    asyncio.run(seed())
    monkeypatch.setattr(
        "routes.drama76_routes._svc",
        DramaSeriesService(repo=_DP1_REPO))
    app = FastAPI()
    register_drama76_routes(app)
    client = TestClient(app)

    # 锁集 → 卡点配置(register 优先 + 登录回跳)
    r = client.get("/api/v1/dramas/drama_cp/episodes/402/checkpoint"
                   "?src=A-CP")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["checkpoint_type"] == "register_to_unlock"
    assert d["free_preview_seconds"] == 10
    assert d["unlock_methods"][0]["type"] == "register"
    assert "redirect=/drama.html?d=drama_cp" in \
        d["unlock_methods"][0]["action_url"]
    assert d["src"] == "A-CP"
    # 免费集 → 409; 不存在 → 404
    r = client.get("/api/v1/dramas/drama_cp/episodes/401/checkpoint")
    assert r.status_code == 409
    r = client.get("/api/v1/dramas/drama_cp/episodes/999/checkpoint")
    assert r.status_code == 404


def test_t29_unlock_route_and_events(monkeypatch):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.drama76_routes import register_drama76_routes
    from routes.agent75_routes import register_agent75_routes
    from services.drama76_series_service import DramaSeriesService
    from routes import agent75_routes as R

    monkeypatch.setattr(
        "routes.drama76_routes._svc",
        DramaSeriesService(repo=_DP1_REPO))
    app = FastAPI()
    register_drama76_routes(app)
    register_agent75_routes(app)
    client = TestClient(app)
    auth = {"X-Member-Id": "777"}

    # 未登录(无身份头) → 401
    r = client.post("/api/v1/unlocks", json={"episode_id": "303"})
    assert r.status_code == 401
    # 登录解锁 → 200 全集可看 + 幂等
    r = client.post("/api/v1/unlocks", headers=auth, json={
        "episode_id": "303", "method": "register", "src": "A-U1"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["drama_id"] == "drama_dp1"
    assert all(e["is_unlocked"] for e in d["episodes"])
    uid = d["unlock"]["unlock_id"]
    r2 = client.post("/api/v1/unlocks", headers=auth,
                     json={"episode_id": "303"})
    assert r2.json()["data"]["unlock"]["unlock_id"] == uid
    # 免费集 → 409; 未知 → 404
    r = client.post("/api/v1/unlocks", headers=auth,
                    json={"episode_id": "301"})
    assert r.status_code == 409
    r = client.post("/api/v1/unlocks", headers=auth,
                    json={"episode_id": "999"})
    assert r.status_code == 404

    # collect 白名单扩容: drama 事件 200 / 非法仍 422
    R._rate_hits.clear()
    r = client.post("/api/v1/resonance/collect", json={
        "session_id": "s_dp1_evt",
        "events": [{"event_type": "drama_play_start",
                    "payload": {"drama_id": "drama_dp1",
                                "episode_id": "301"}},
                   {"event_type": "unlock_success",
                    "payload": {"drama_id": "drama_dp1"}}]})
    assert r.status_code == 200
    R._rate_hits.clear()
    r = client.post("/api/v1/resonance/collect", json={
        "session_id": "s_dp1_evt",
        "events": [{"event_type": "drama_steal", "payload": {}}]})
    assert r.status_code == 422


# ------------------------------------------------------------
# 76号 DP2(2026-10-05): 超级会员
# ------------------------------------------------------------

def test_t30_super_member_service():
    import asyncio
    from services.drama76_series_service import (
        SUPER_PLANS, DramaSeriesService)

    async def run():
        svc = DramaSeriesService(repo=_DP1_REPO)
        # 套餐表在位
        assert {p["plan_id"] for p in SUPER_PLANS} >= {
            "super_forever", "super_month"}
        # 未开通 → False; 永久开通 → True; 撤销 → False
        assert await svc.is_super("u_sup") is False
        await svc.grant_super("u_sup", days=0, source="admin")
        assert await svc.is_super("u_sup") is True
        assert await svc.revoke_super("u_sup") is True
        assert await svc.is_super("u_sup") is False
        # 月卡(30 天) → True; 过期(倒签 1 天) → False
        await svc.grant_super("u_sup2", days=30)
        assert await svc.is_super("u_sup2") is True
        from datetime import datetime, timedelta, timezone
        store = getattr(_DP1_REPO, "store", None)
        store["drama76_super"]["u_sup3"] = {
            "user_id": "u_sup3", "source": "admin",
            "expire_at": (datetime.now(timezone.utc)
                          - timedelta(days=1)).isoformat(),
            "created_at": ""}
        assert await svc.is_super("u_sup3") is False
        # user_view: 会员全解锁(member_only 态) + 解锁用户全集(unlocked)
        d = await svc.get("drama_dp1")
        await svc.grant_super("u_sup", 0)
        eps = await svc.user_view("u_sup", d)
        assert all(e["is_unlocked"] for e in eps)
        eps2 = await svc.user_view("u_9", d)     # 已解锁用户(T27)
        assert all(e["is_unlocked"] for e in eps2)

    asyncio.run(run())


def test_t31_super_routes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.drama76_routes import register_drama76_routes
    from services.drama76_series_service import DramaSeriesService

    monkeypatch.setattr(
        "routes.drama76_routes._svc",
        DramaSeriesService(repo=_DP1_REPO))
    app = FastAPI()
    register_drama76_routes(app)
    client = TestClient(app)
    admin = {"X-Role": "admin"}
    auth = {"X-Member-Id": "u_sup_r"}

    # plans 公开(无头)
    r = client.get("/api/v1/memberships/plans")
    assert r.status_code == 200 and len(r.json()["data"]) == 2
    # grant: 非 admin 403 / admin 200
    r = client.post("/api/v1/memberships/grant",
                    json={"user_id": "u_sup_r", "days": 0})
    assert r.status_code == 403
    r = client.post("/api/v1/memberships/grant", headers=admin,
                    json={"user_id": "u_sup_r", "days": 0})
    assert r.status_code == 200
    # episodes/me: 会员全集可看 + is_super
    r = client.get("/api/v1/dramas/drama_dp1/episodes/me", headers=auth)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["is_super"] is True
    assert all(e["is_unlocked"] for e in d["episodes"])
    # memberships/check: can_watch + reason=super_member_active
    r = client.get("/api/v1/memberships/check", headers=auth,
                   params={"drama_id": "drama_dp1",
                           "episode_id": "303"})
    assert r.status_code == 200
    c = r.json()["data"]
    assert c["can_watch"] is True
    assert c["reason"] == "super_member_active"
    # 普通用户(已解锁 u_9): reason 走 unlocked
    r = client.get("/api/v1/memberships/check",
                   headers={"X-Member-Id": "u_9"},
                   params={"drama_id": "drama_dp1", "episode_id": "303"})
    assert r.json()["data"]["can_watch"] is True
    # 未登录 401; revoke 后再看 → locked
    r = client.get("/api/v1/dramas/drama_dp1/episodes/me")
    assert r.status_code == 401
    client.post("/api/v1/memberships/revoke", headers=admin,
                json={"user_id": "u_sup_r"})
    r = client.get("/api/v1/memberships/check",
                   headers=auth,
                   params={"drama_id": "drama_dp1", "episode_id": "303"})
    assert r.json()["data"]["can_watch"] is False


# ------------------------------------------------------------
# 76号 DP3(2026-10-06): 邀请裂变
# ------------------------------------------------------------

def test_t32_growth_service():
    import asyncio
    from services.drama76_growth_service import DramaGrowthService
    from services.drama76_series_service import DramaSeriesService

    async def run():
        svc = DramaSeriesService(repo=_DP1_REPO)
        g = DramaGrowthService(repo=_DP1_REPO, series=svc)
        # 邀请码幂等(同用户恒同码, 确定性生成)
        c1 = await g.invite_code("u_inv")
        c1b = await g.invite_code("u_inv")
        assert c1["invite_code"] == c1b["invite_code"]
        assert c1["invite_code"].startswith("zx")
        # 自邀拒 / 码无效拒
        try:
            await g.bind(c1["invite_code"], "u_inv")
            raise AssertionError("自邀应拒")
        except ValueError:
            pass
        try:
            await g.bind("zxdeadbee", "u_a")
            raise AssertionError("无效码应拒")
        except KeyError:
            pass
        # 绑定即转化(登录态 bind 为有效转化证据)
        # 达阶梯时进一步被计入发奖 → rewarded
        rel = await g.bind(c1["invite_code"], "u_friend1")
        assert rel["status"] in ("confirmed", "rewarded")
        assert rel["inviter_id"] == "u_inv"
        # 阶梯1: 邀 1 人 → 解锁一部剧(method=invite)
        unlocked = await svc.unlocked_dramas_of("u_inv")
        assert unlocked, "邀请人应获得一部剧解锁"
        prog = await g.progress("u_inv")
        assert prog["valid_count"] == 1
        assert len(prog["rewards"]) == 1
        assert prog["rewards"][0]["reward_type"] == "unlock_drama"
        assert prog["rewards"][0]["value"] in unlocked
        assert prog["next_ladder"]["threshold"] == 3
        # 发奖幂等: 重复 confirm 不重发
        await g.auto_confirm("u_friend1")
        prog = await g.progress("u_inv")
        assert len(prog["rewards"]) == 1
        # 唯一绑定: 已绑定者再被他人邀 → 返回既有关系
        c2 = await g.invite_code("u_inv2")
        rel2 = await g.bind(c2["invite_code"], "u_friend1")
        assert rel2["inviter_id"] == "u_inv"
        # 阶梯3: 满 3 人 → super 7 天(source=invite)
        await g.bind(c1["invite_code"], "u_friend2")
        await g.bind(c1["invite_code"], "u_friend3")
        assert await svc.is_super("u_inv") is True
        store = getattr(_DP1_REPO, "store", None)
        sup = store["drama76_super"]["u_inv"]
        assert sup["source"] == "invite" and sup["expire_at"]
        prog = await g.progress("u_inv")
        assert {r["threshold"] for r in prog["rewards"]} == {1, 3}
        assert prog["next_ladder"] is None
        # 关系态: 前 3 人 rewarded
        states = {r["invitee_id"]: r["status"] for r in prog["relations"]}
        assert states["u_friend1"] == "rewarded"

    asyncio.run(run())


def test_t33_growth_routes(monkeypatch):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.drama76_routes import register_drama76_routes
    from services.drama76_growth_service import DramaGrowthService
    from services.drama76_series_service import DramaSeriesService

    svc = DramaSeriesService(repo=_DP1_REPO)
    monkeypatch.setattr("routes.drama76_routes._svc", svc)
    monkeypatch.setattr(
        "routes.drama76_routes._growth",
        DramaGrowthService(repo=_DP1_REPO, series=svc))
    app = FastAPI()
    register_drama76_routes(app)
    client = TestClient(app)
    auth = {"X-Member-Id": "u_inv_r"}

    # 三端点未登录 → 401
    assert client.post("/api/v1/growth/invite-code").status_code == 401
    assert client.post(
        "/api/v1/growth/referrals/bind",
        json={"invite_code": "zxwhatever"}).status_code == 401
    assert client.get("/api/v1/growth/invites/me").status_code == 401
    # 取码 200 + 幂等
    r = client.post("/api/v1/growth/invite-code", headers=auth)
    assert r.status_code == 200
    code = r.json()["data"]["invite_code"]
    r2 = client.post("/api/v1/growth/invite-code", headers=auth)
    assert r2.json()["data"]["invite_code"] == code
    # bind: 无效码 404 / 自邀 409 / 正常 200(即时转化)
    r = client.post("/api/v1/growth/referrals/bind",
                    headers={"X-Member-Id": "u_new_r"},
                    json={"invite_code": "zxdeadbeef"})
    assert r.status_code == 404
    r = client.post("/api/v1/growth/referrals/bind", headers=auth,
                    json={"invite_code": code})
    assert r.status_code == 409
    r = client.post("/api/v1/growth/referrals/bind",
                    headers={"X-Member-Id": "u_new_r"},
                    json={"invite_code": code})
    assert r.status_code == 200
    assert r.json()["data"]["status"] in ("confirmed", "rewarded")
    # invites/me: 进度+下一阶梯
    r = client.get("/api/v1/growth/invites/me", headers=auth)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["valid_count"] == 1 and d["invite_code"] == code
    assert d["next_ladder"]["threshold"] == 3
    # episodes/me 尾部 auto-confirm 兜底: 造 pending 关系再访问
    async def seed_pending():
        g2 = DramaGrowthService(repo=_DP1_REPO, series=svc)
        c = await g2.invite_code("u_inv2_r")
        store = getattr(_DP1_REPO, "store", None)
        if isinstance(store, dict):
            store.setdefault("drama76_referrals", {})
        rel = {"referral_id": "ref_u_late", "inviter_id": "u_inv2_r",
               "invitee_id": "u_late", "invite_code": c["invite_code"],
               "status": "pending", "confirmed_at": "", "created_at": ""}
        await _DP1_REPO._save("drama76_referrals", "ref_u_late", rel)
    asyncio.run(seed_pending())
    r = client.get("/api/v1/dramas/drama_dp1/episodes/me",
                   headers={"X-Member-Id": "u_late"})
    assert r.status_code == 200

    async def check():
        return await _DP1_REPO._get("drama76_referrals", "ref_u_late")
    rel = asyncio.run(check())
    assert rel["status"] in ("confirmed", "rewarded")


def test_t34_ladder_edge():
    import asyncio
    from services.drama76_growth_service import DramaGrowthService
    from services.drama76_series_service import DramaSeriesService

    async def run():
        svc = DramaSeriesService(repo=_DP1_REPO)
        g = DramaGrowthService(repo=_DP1_REPO, series=svc)
        # u_all 先解锁全部在线剧 → 阶梯1 发 skipped
        online = await svc.list_online()
        assert online, "DP1 共享仓应有在线剧"
        for d in online:
            free_n = int(d.get("free_episodes") or 0)
            ep = next(e for e in d["episodes"]
                      if int(e["episode_number"]) == free_n + 1)
            await svc.unlock("u_all", ep["episode_id"], "register")
        c = await g.invite_code("u_all")
        await g.bind(c["invite_code"], "u_friend_x")
        prog = await g.progress("u_all")
        r1 = [r for r in prog["rewards"] if r["threshold"] == 1][0]
        assert r1["status"] == "skipped"
        assert prog["valid_count"] == 1
        assert prog["next_ladder"]["threshold"] == 3
        # 阶梯3 仍正常发(不因阶梯1 skipped 中断)
        await g.bind(c["invite_code"], "u_friend_y")
        await g.bind(c["invite_code"], "u_friend_z")
        assert await svc.is_super("u_all") is True
        assert {r["threshold"] for r in
                (await g.progress("u_all"))["rewards"]} == {1, 3}

    asyncio.run(run())
