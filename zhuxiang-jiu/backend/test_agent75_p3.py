"""75号·P3 四件套测试(2026-10-16)

对照《小竹AgentAI升级蓝图与现有实现对照分析》§五 P3 第 4-7 条:
    ① 四角色 Sub-Agent 拓扑(role 字段/技能角色面/角色独立 kill/身份行)
    ② 跨会话记忆注入(memoryLine + observe/on/off 三态)
    ③ 多步编排+步数熔断(workflow 激活/MAX_STEPS 截断/转人工告警)
    ④ 工具避坑表(失败签名熔断/短路/half-open/成功复位/off)

LLM 一律打桩(离线网关, 仿 test_agent75_orchestrator.MockGateway)。

运行: pytest test_agent75_p3.py
"""

import json
import time

import pytest

import services.agent75_orchestrator_service as orch_module
from services.agent75_orchestrator_service import (
    ROLE_PROMPTS,
    AgentModelGateway,
    WebsiteAgentOrchestrator,
    max_steps,
    max_steps_answer,
    role_kill,
)
from services.agent75_skill_service import (
    SkillDefinition,
    SkillKind,
    SkillRuntime,
    SkillStatus,
)


class P3MockGateway(AgentModelGateway):
    """离线网关: 意图步固定词, 生成步固定回答; 捕获 prompt 供断言"""

    def __init__(self, intent_reply="summary",
                 answer="好的，这是要点简报。"):
        super().__init__()
        self.intent_reply = intent_reply
        self.answer = answer
        self.systems: list[str] = []
        self.users: list[str] = []
        self.gen_calls = 0

    def call(self, model_id, system, user):
        self.systems.append(system)
        self.users.append(user)
        if model_id == "glm-flash":
            return self.intent_reply, True, 3
        self.gen_calls += 1
        return self.answer, True, 5


@pytest.fixture(autouse=True)
def _p3_env(tmp_path, monkeypatch):
    """任务目录隔离 + P3 进程级状态清零 + 开关回默认值"""
    for mod in ("services.agent75_orchestrator_service",
                "services.agent75_evolution_service",
                "services.agent75_skill_service",
                "services.agent75_resonance_service"):
        monkeypatch.setattr(f"{mod}.AGENT_TASK_DIR", tmp_path)
    from services.agent75_pitfall_service import get_guard
    get_guard().reset()
    orch_module._STEPS_ALERT_DEDUPE.clear()
    for key in ("AGENT75_KILL", "AGENT75_ROLE_KILL_MEMBER",
                "AGENT75_ROLE_KILL_MERCHANT", "AGENT75_ROLE_KILL_OPS",
                "AGENT75_ROLE_KILL_CS", "AGENT75_MEMORY_MODE",
                "AGENT75_WORKFLOW_MODE", "AGENT75_MAX_STEPS",
                "AGENT75_PITFALL_MODE", "AGENT75_SKILL_KILL",
                "AGENT75_SKILL_SHADOW"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT75_SKILL_SHADOW", "off")  # 影子线程不干扰
    yield tmp_path


def _client(monkeypatch, gateway):
    """装配任务路由 + 打桩编排器(仿既有 t5/t8 路由测试)"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agent75_routes as R
    from routes.agent75_routes import register_agent75_routes

    app = FastAPI()
    register_agent75_routes(app)
    monkeypatch.setattr(R, "_orchestrator",
                        WebsiteAgentOrchestrator(gateway))
    return TestClient(app)


# ------------------------------------------------------------
# ① 四角色 Sub-Agent 拓扑
# ------------------------------------------------------------

def test_p3_1_role_route_trace_and_detail(monkeypatch):
    client = _client(monkeypatch, P3MockGateway())
    admin = {"X-Role": "admin"}

    r = client.post("/api/v1/agent/tasks", headers=admin,
                    json={"query": "上月经营数据小结", "role": "merchant"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["role"] == "merchant"

    # GET 详情含 role(TaskRecord.model_dump)
    r2 = client.get(f"/api/v1/agent/tasks/{d['task_id']}", headers=admin)
    assert r2.status_code == 200
    assert r2.json()["data"]["role"] == "merchant"

    # 落盘 JSON 回读 role; exec trace 带 role; 身份行进 system prompt
    orch = orch_module  # noqa: F841  (可读性占位)
    from routes import agent75_routes as R
    task = R._orchestrator.load(d["task_id"])
    assert task.role == "merchant"
    gen = [t for t in task.execution_trace
           if t["step_id"] == "exec_answer"][0]
    assert gen["role"] == "merchant"
    assert any(ROLE_PROMPTS["merchant"] in s
               for s in R._orchestrator.gateway.systems)


def test_p3_2_role_default_and_invalid(monkeypatch):
    client = _client(monkeypatch, P3MockGateway())
    admin = {"X-Role": "admin"}

    # 无 role → 默认 member(存量调用兼容)
    r = client.post("/api/v1/agent/tasks", headers=admin,
                    json={"query": "你好"})
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "member"

    # 非法 role → 422(白名单封闭集合)
    r = client.post("/api/v1/agent/tasks", headers=admin,
                    json={"query": "你好", "role": "hacker"})
    assert r.status_code == 422


def test_p3_3_role_kill_independent(monkeypatch):
    client = _client(monkeypatch, P3MockGateway())
    admin = {"X-Role": "admin"}

    monkeypatch.setenv("AGENT75_ROLE_KILL_OPS", "on")
    assert role_kill("ops") is True
    assert role_kill("member") is False
    # ops 任务 409; 其余角色不受影响
    r = client.post("/api/v1/agent/tasks", headers=admin,
                    json={"query": "巡检", "role": "ops"})
    assert r.status_code == 409
    assert "ops" in r.json()["detail"]
    r = client.post("/api/v1/agent/tasks", headers=admin,
                    json={"query": "正常提问", "role": "member"})
    assert r.status_code == 200

    # 全局 AGENT75_KILL 仍优先(所有角色 409)
    monkeypatch.setenv("AGENT75_KILL", "on")
    r = client.post("/api/v1/agent/tasks", headers=admin,
                    json={"query": "再问", "role": "member"})
    assert r.status_code == 409
    assert r.json()["detail"] == "agent75 killed"


def test_p3_4_skill_role_filter_and_fallback():
    rt = SkillRuntime()
    rt.upsert(SkillDefinition(
        skill_id="ops_report", name="运营报表", status=SkillStatus.ACTIVE,
        kind=SkillKind.PROMPT_TOOL, slot="generate", intent="summary",
        roles=["ops"],
        spec={"model_id": "glm-reason", "prompt_template": "运营专用摘要提示"},
        source="admin"))
    rt.set_status("answer_summary", SkillStatus.DEPRECATED)

    # 直查: ops 命中专属; member 被滤(None → 编排回落 P0 直连);
    # role=None 旧调用不滤(answer_chat 仍可用)
    assert rt.resolve("generate", "summary",
                      role="ops").skill_id == "ops_report"
    assert rt.resolve("generate", "summary", role="member") is None
    assert rt.resolve("generate", "chat",
                      role="member").skill_id == "answer_chat"
    # 存量技能 roles=None → 任意角色可用(零迁移)
    assert rt.resolve("generate", "chat",
                      role="cs").skill_id == "answer_chat"

    # 编排层: member 回落 P0 直连(trace skill_id=answer); ops 吃到专属技能
    gw_m = P3MockGateway()
    t_member = WebsiteAgentOrchestrator(gw_m).run("总结", role="member")
    assert t_member.execution_trace[1]["skill_id"] == "answer"
    assert ROLE_PROMPTS["member"] in gw_m.systems[1]

    gw_o = P3MockGateway()
    t_ops = WebsiteAgentOrchestrator(gw_o).run("总结", role="ops")
    assert t_ops.execution_trace[1]["skill_id"] == "ops_report"
    assert "运营专用摘要提示" in gw_o.systems[1]
    assert ROLE_PROMPTS["ops"] in gw_o.systems[1]


# ------------------------------------------------------------
# ② 跨会话记忆注入
# ------------------------------------------------------------

def _seed_voice82(member_id, sessions=20, role="dealer",
                  top_goal="价格", intent_dist=None):
    """内存态种画像三表(get_profile/_hgetall 真实读取路径)"""
    from repositories.backend import get_in_memory_store
    key = f"m{member_id}"
    store = get_in_memory_store()
    store.setdefault("voice82_role", {})[key] = {
        "role": role, "confidence": "0.8", "sources": "[]",
        "updatedAt": ""}
    store.setdefault("voice82_dialog", {})[key] = {
        "_sessions": str(sessions), "_turns": str(sessions * 2),
        "ema": "v1"}
    store.setdefault("voice82_biz", {})[key] = {
        "intentDist": json.dumps(intent_dist if intent_dist is not None
                                 else {"price": 5, "stock": 3,
                                       "logistics": 2, "chat": 1}),
        "bizDist": json.dumps({"价格": 0.5}),
        "topGoal": top_goal, "updatedAt": ""}


def test_p3_5_memory_line_assemble_and_observe():
    _seed_voice82(907001)          # 经画像三表真实读取(async 同步包装路径)
    gw = P3MockGateway()
    o = WebsiteAgentOrchestrator(gw)
    task = o.run("帮我总结", user_id="907001")
    assert task.memoryLine == (
        "会员画像:角色=dealer,主诉求=价格,高频意图=price/stock/logistics")
    # 默认 observe: 只留痕(TaskRecord + trace), 不进 prompt
    assert task.execution_trace[1]["memory_mode"] == "observe"
    assert all("会员画像" not in u for u in gw.users)


def test_p3_6_memory_cold_start_and_failure():
    _seed_voice82(907002, sessions=5)      # 冷启动(<10 会话) → 空串
    o = WebsiteAgentOrchestrator(P3MockGateway())
    assert o.run("总结", user_id="907002").memoryLine == ""
    # 画像未构建(三表无记录) → 空串
    assert o.run("总结", user_id="907003").memoryLine == ""
    # 游客 / 非数字 id → 空串
    assert o.run("总结", user_id=None).memoryLine == ""
    assert o.run("总结", user_id="u_abc").memoryLine == ""


def test_p3_7_memory_on_injects_prompt(monkeypatch):
    _seed_voice82(907004)
    monkeypatch.setenv("AGENT75_MEMORY_MODE", "on")
    gw = P3MockGateway()
    task = WebsiteAgentOrchestrator(gw).run("帮我总结", user_id="907004")
    assert task.memoryLine.startswith("会员画像:角色=dealer")
    # on: 画像行前缀进 generate user prompt(第二通调用)
    gen_user = gw.users[1]
    assert gen_user.splitlines()[0] == task.memoryLine
    assert "帮我总结" in gen_user
    assert task.execution_trace[1]["memory_mode"] == "on"


def test_p3_8_memory_off_zero_trace(monkeypatch):
    _seed_voice82(907005)
    monkeypatch.setenv("AGENT75_MEMORY_MODE", "off")
    fetches: list[int] = []
    monkeypatch.setattr(
        WebsiteAgentOrchestrator, "_fetch_voice82_profile",
        lambda self, mid: fetches.append(mid) or {"profile": {},
                                                  "intentDist": {}})
    gw = P3MockGateway()
    task = WebsiteAgentOrchestrator(gw).run("总结", user_id="907005")
    # off: 零痕迹——不读画像/字段空/trace 无 memory_mode
    assert fetches == []
    assert task.memoryLine == ""
    assert all("memory_mode" not in t for t in task.execution_trace)
    assert all("会员画像" not in u for u in gw.users)


# ------------------------------------------------------------
# ③ 多步编排 + 步数熔断
# ------------------------------------------------------------

def test_p3_9_workflow_off_zero_change():
    gw = P3MockGateway()
    task = WebsiteAgentOrchestrator(gw).run("帮我总结这篇内容")
    # off 默认: 单步管线逐字不变(plan + execute 两步, 无 review 步)
    assert [t["step_id"] for t in task.execution_trace] == [
        "plan_intent", "exec_answer"]
    assert task.status == "completed"
    assert task.interrupted_reason == ""
    assert gw.gen_calls == 1


def test_p3_10_workflow_on_three_steps(monkeypatch):
    monkeypatch.setenv("AGENT75_WORKFLOW_MODE", "on")
    gw = P3MockGateway()
    task = WebsiteAgentOrchestrator(gw).run("帮我总结这篇内容")
    assert task.status == "completed"
    # classify→generate→review 三步链(classify 由规划步承担)
    assert [t["step_id"] for t in task.execution_trace] == [
        "plan_intent", "exec_answer", "critic_review"]
    # 步间传递: classify 输出(意图词)进 generate user prompt
    assert "[上步输出] summary" in gw.users[1]
    # review 步消费 critic_review builtin_fn 且基线通过(无 LLM)
    rev = task.execution_trace[2]
    assert rev["skill_id"] == "critic_review"
    assert rev["status"] == "success" and rev["issues"] == []
    assert gw.gen_calls == 1


def test_p3_11_workflow_max_steps_truncates(monkeypatch):
    monkeypatch.setenv("AGENT75_WORKFLOW_MODE", "on")
    monkeypatch.setenv("AGENT75_MAX_STEPS", "2")
    gw = P3MockGateway()
    task = WebsiteAgentOrchestrator(gw).run("帮我总结这篇内容")
    # 第 3 步(review)前截断: status failed + 确定性话术 + 原因留痕
    assert task.status == "failed"
    assert task.interrupted_reason == "max_steps"
    assert task.answer == max_steps_answer(2)
    assert max_steps() == 2
    assert [t["step_id"] for t in task.execution_trace] == [
        "plan_intent", "exec_answer", "critic_review"]
    assert task.execution_trace[2]["status"] == "interrupted"
    assert task.execution_trace[2]["interrupted_reason"] == "max_steps"
    # 截断后不再评估
    assert task.evaluation is None
    assert gw.gen_calls == 1              # generate 已跑, review 无 LLM


def test_p3_12_steps_alert_count_and_idempotent(monkeypatch):
    monkeypatch.setenv("AGENT75_WORKFLOW_MODE", "on")
    monkeypatch.setenv("AGENT75_MAX_STEPS", "1")   # generate 前即截断
    calls: list[tuple] = []

    async def fake_notify(task_id, intent, steps):
        calls.append((task_id, intent, steps))
        return True

    monkeypatch.setattr(orch_module, "_notify_agent_steps_exceeded",
                        fake_notify)
    t1 = WebsiteAgentOrchestrator(P3MockGateway()).run("总结一")
    t2 = WebsiteAgentOrchestrator(P3MockGateway()).run("总结二")
    assert t1.interrupted_reason == t2.interrupted_reason == "max_steps"
    for _ in range(50):                   # 轮询 daemon 线程送达
        if calls:
            break
        time.sleep(0.1)
    time.sleep(0.3)
    # 两次截断只告警一次(24h 进程级幂等去重)
    assert len(calls) == 1
    assert calls[0][1] == "summary" and calls[0][2] >= 1


def test_p3_13_steps_alert_failsoft_no_admin(monkeypatch):
    import asyncio

    class _NoAdminRepo:
        async def list_all(self):
            return []

    monkeypatch.setattr(
        "repositories.member_repository.MemberRepository", _NoAdminRepo)
    # 无管理员收件人 → False 不抛(fail-soft)
    assert asyncio.run(orch_module._notify_agent_steps_exceeded(
        "t_x", "summary", 2)) is False


# ------------------------------------------------------------
# ④ 工具避坑表
# ------------------------------------------------------------

def _mk_failing_code_tool(sid="pit_tool"):
    rt = SkillRuntime()
    rt.upsert(SkillDefinition(
        skill_id=sid, name="避坑测试工具", status=SkillStatus.ACTIVE,
        kind=SkillKind.CODE_TOOL, slot="generate", intent="summary",
        spec={"code": "def run(x):\n    return {'answer': 'x'}",
              "timeout_ms": 500}, source="admin"))
    rt.set_status("answer_summary", SkillStatus.DEPRECATED)
    return rt


def test_p3_14_pitfall_shortcircuit_after_3(monkeypatch):
    from services import agent75_sandbox_service as sb
    from services.agent75_pitfall_service import get_guard

    _mk_failing_code_tool()
    box = {"n": 0}

    def failing_exec(*a, **k):
        box["n"] += 1
        return {"ok": False, "error": "unreachable",
                "latency_ms": 0}

    monkeypatch.setattr(sb, "exec_code", failing_exec)
    for i in range(3):                    # 3 次失败 → open
        t = WebsiteAgentOrchestrator(P3MockGateway()).run(f"总结{i}")
        assert t.status == "completed"    # 三级兜底链兜住
    assert box["n"] == 3

    # 第 4 次: 同签名已 open → 短路, 沙箱零调用
    t4 = WebsiteAgentOrchestrator(P3MockGateway()).run("总结4")
    assert box["n"] == 3
    gen = t4.execution_trace[1]
    assert gen.get("pitfall_shortcircuited") is True
    assert gen["fallback_from"] == "pit_tool"
    assert t4.status == "completed"
    st = get_guard().stats()
    # 签名格式: {skill_id}:{trace.status}=sandbox_unreachable
    assert "pit_tool:sandbox_unreachable" in st["open"]


def test_p3_15_pitfall_halfopen_and_reset(monkeypatch):
    import services.agent75_pitfall_service as pit
    from services.agent75_pitfall_service import get_guard

    g = get_guard()
    sig = "sk1:sandbox_timeout"
    for _ in range(3):
        g.record_failure(sig)
    assert g.should_skip(sig) is True           # open 短路
    assert g.should_skip_skill("sk1") is True

    real_mono = pit.time.monotonic
    offset = {"s": 0}
    monkeypatch.setattr(pit.time, "monotonic",
                        lambda: real_mono() + offset["s"])
    offset["s"] = 11 * 60               # 时间前行 11min
    # open 窗口过期 → half-open 放行一次试探
    assert g.should_skip_skill("sk1") is False
    assert g.is_half_open(sig) is True
    # 试探成功 → 复位(窗计数/短路态/试探态全清)
    g.record_success_skill("sk1")
    assert g.should_skip(sig) is False
    assert g.stats()["half_open"] == []
    assert g.stats()["tracked"] == {}
    # 试探失败路径: 再三败 → 重开 → 过窗 → half-open → 试探失败即重开
    for _ in range(3):
        g.record_failure(sig)
    assert g.should_skip(sig) is True
    offset["s"] = 23 * 60               # 再前行 12min(相对重开窗口过期)
    assert g.should_skip_skill("sk1") is False  # 又过窗 → half-open
    g.record_failure(sig)
    assert g.should_skip(sig) is True           # 试探失败重开


def test_p3_16_pitfall_mode_off(monkeypatch):
    from services import agent75_sandbox_service as sb
    from services.agent75_pitfall_service import get_guard

    monkeypatch.setenv("AGENT75_PITFALL_MODE", "off")
    _mk_failing_code_tool()
    box = {"n": 0}

    def failing_exec(*a, **k):
        box["n"] += 1
        return {"ok": False, "error": "sandbox_unreachable",
                "latency_ms": 0}

    monkeypatch.setattr(sb, "exec_code", failing_exec)
    for i in range(4):
        t = WebsiteAgentOrchestrator(P3MockGateway()).run(f"关{i}")
        assert t.status == "completed"
        assert "pitfall_shortcircuited" not in t.execution_trace[1]
    # off: 完全不查不记——第 4 次仍打沙箱, 避坑表零记录
    assert box["n"] == 4
    st = get_guard().stats()
    assert st["open"] == [] and st["tracked"] == {}
