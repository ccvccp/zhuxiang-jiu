"""75号·网站自研 Agent 中枢(P0 编排闭环, 2026-10-05)

用户方案《开发阶段的技术方案》P0 落地: Orchestrator + 模型网关
+ 执行日志——不依赖外部 Agent 平台, 模型推理经既有 llm_client
(第五轮甄别定案的「不重建, 长在现有栈上」口径)。

架构(方案原文映射):
    WebsiteAgentOrchestrator  任务接收/状态机/轨迹落盘
      ├─ Planner   P0=轻量意图识别(chat/summary/draft/unknown)
      ├─ Executor  P0=按意图路由 system prompt 的单步生成
      └─ Critic    P0=基线评估(非空/字数/无拒答句式)
    AgentModelGateway         模型池网关(注册表+统一调用)

P0 范围(方案路线图): 编排器+网关+执行日志;
P1(模型路由历史成功率/技能库)与 P2(进化引擎/审计回滚)未含。

状态机: pending → planning → executing → evaluating
        → completed | failed
持久化: AGENT_TASK_DIR(默认 ./agent_tasks) 每任务一 JSON
        + tasks.jsonl 追加式执行日志(观测面)。
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

AGENT_TASK_DIR = Path(os.environ.get("AGENT_TASK_DIR", "agent_tasks"))

INTENTS = ("chat", "summary", "draft", "unknown")


def is_kill() -> bool:
    """决策面一键回退(AGENT75_KILL=on → 409, 73号同款口径)"""
    return os.environ.get("AGENT75_KILL", "").strip().lower() in ("on", "1", "true")


# ---------- P3-①: 四角色 Sub-Agent 拓扑(角色轴+独立 kill) ----------

AGENT_ROLES = ("member", "merchant", "ops", "cs")

# 角色身份行(单行确定性模板——execute 侧 system prompt 前缀拼入)
ROLE_PROMPTS = {
    "member": "角色定位:面向C端会员的导购与售后助手, 语气亲切易懂。",
    "merchant": "角色定位:面向B端商家的经营分析助手, 输出侧重数据与经营建议。",
    "ops": "角色定位:面向内部运营的配置巡检助手, 输出侧重检查项与风险提示。",
    "cs": "角色定位:面向客服的话术与工单辅助助手, 输出侧重可复用话术与工单步骤。",
}


def role_kill(role: str) -> bool:
    """角色独立 kill-switch(AGENT75_ROLE_KILL_{ROLE大写}=on → 该角色
    任务 409, 其余角色不受影响; 全局 AGENT75_KILL 仍优先——拦截均在
    路由面, 服务层不查, 对齐 is_kill 形态)"""
    env = f"AGENT75_ROLE_KILL_{str(role).strip().upper()}"
    return os.environ.get(env, "").strip().lower() in ("on", "1", "true")


# ---------- P3-②: 跨会话记忆注入开关(默认 observe 只留痕零行为变化) ----------


def memory_mode() -> str:
    """off=零痕迹 / observe=只落 TaskRecord.memoryLine / on=进 prompt"""
    return os.environ.get("AGENT75_MEMORY_MODE", "observe").strip().lower()


# ---------- P3-③: 多步编排+步数熔断开关(默认 off 零行为变化) ----------


def workflow_enabled() -> bool:
    return os.environ.get("AGENT75_WORKFLOW_MODE", "off"
                          ).strip().lower() in ("on", "1", "true")


def max_steps() -> int:
    """AGENT75_MAX_STEPS 硬上限(默认 5; 非法/越界值回落 5)"""
    try:
        n = int(os.environ.get("AGENT75_MAX_STEPS", "5"))
        return n if n >= 1 else 5
    except (TypeError, ValueError):
        return 5


def max_steps_answer(limit: int) -> str:
    """超限截断话术(确定性模板拼装——不经 LLM)"""
    return (f"任务执行步数已达上限({limit}步), 已自动停止并转人工处理, "
            "请稍后再试或联系客服。")


# ---------- 数据结构(方案 pydantic P0 裁剪版) ----------

class ModelType(str, Enum):
    REASONING = "reasoning"
    LIGHTWEIGHT = "lightweight"


class TaskStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"


class ModelConfig(BaseModel):
    model_config = {"protected_namespaces": ()}

    model_id: str
    name: str                       # llm_client 侧真实模型名
    model_type: ModelType
    temperature: float = 0.3
    max_reply_chars: int = 2000     # P0 无 max_tokens 语义, 字数封顶


class TaskRecord(BaseModel):
    task_id: str
    user_id: Optional[str] = None
    query: str
    role: str = "member"          # P3-① 任务角色(默认值兼容存量 JSON)
    intent: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    answer: str = ""
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)
    evaluation: Optional[dict[str, Any]] = None
    memoryLine: str = ""          # P3-② 会员画像单行(observe 留痕)
    interrupted_reason: str = ""  # P3-③ 熔断截断原因(如 max_steps)
    created_at: str = ""
    updated_at: str = ""


# ---------- 模型网关(方案 ModelGateway: 注册表+统一调用) ----------

class AgentModelGateway:
    """模型池网关——P0 双档注册, 全部经 llm_client 主链路

    glm-flash:   轻量档(意图识别/短交互, temp 0.1)
    glm-reason:  推理档(正文生成, temp 0.4)
    """

    def __init__(self) -> None:
        self.models: dict[str, ModelConfig] = {
            c.model_id: c for c in (
                ModelConfig(model_id="glm-flash",
                            name="glm-4-flash",
                            model_type=ModelType.LIGHTWEIGHT,
                            temperature=0.1),
                ModelConfig(model_id="glm-reason",
                            name="glm-4-flash",
                            model_type=ModelType.REASONING,
                            temperature=0.4),
            )
        }

    def get(self, model_id: str) -> ModelConfig | None:
        return self.models.get(model_id)

    def call(self, model_id: str, system: str, user: str
             ) -> tuple[str, bool, int]:
        """统一调用: (content, success, latency_ms)——失败不抛异常"""
        cfg = self.models.get(model_id)
        if cfg is None:
            return "", False, 0
        start = time.time()
        try:
            reply = self._chat(system, user, cfg)
            latency_ms = int((time.time() - start) * 1000)
            if not (reply or "").strip():
                return "", False, latency_ms
            return reply.strip()[:cfg.max_reply_chars], True, latency_ms
        except Exception as exc:   # noqa: BLE001
            latency_ms = int((time.time() - start) * 1000)
            logger.warning("agent75_call_error model=%s: %s",
                           model_id, exc)
            return "", False, latency_ms

    @staticmethod
    def _chat(system: str, user: str, cfg: ModelConfig) -> str | None:
        from services.llm_client import provider_client, llm_enabled
        if not llm_enabled():
            return None
        return provider_client.chat(system, user,
                                    temperature=cfg.temperature,
                                    model=cfg.name)


# ---------- P1: 模型路由(方案 ModelRouter——成功率历史驱动) ----------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelRouter:
    """任务类型→模型路由: 类型过滤 + 成本/延迟/成功率历史评分

    方案 _score 口径: 基础 10 - cost_weight*0.3 - latency_weight*0.2
    + 近 50 条成功率*2.0; 历史持久化 router_history.json(重启不丢),
    record() 由编排器每次网关调用后回写。
    """

    TYPE_MAP = {
        "planning": ModelType.LIGHTWEIGHT,
        "reasoning": ModelType.REASONING,
        "classification": ModelType.LIGHTWEIGHT,
        "summary": ModelType.LIGHTWEIGHT,
        "generation": ModelType.REASONING,
        "evaluation": ModelType.REASONING,
    }

    def __init__(self, gateway: AgentModelGateway) -> None:
        self.gateway = gateway
        self.history: dict[str, list[dict[str, Any]]] = self._load()

    @staticmethod
    def _path() -> Path:
        return AGENT_TASK_DIR / "router_history.json"

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        p = self._path()
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:   # noqa: BLE001 损坏回落空表
                return {}
        return {}

    def _flush(self) -> None:
        self._path().write_text(
            json.dumps(self.history, ensure_ascii=False), encoding="utf-8")

    def route(self, task_type: str) -> ModelConfig:
        """按任务类型选模型: 类型过滤(空则全池)+历史成功率评分"""
        need = self.TYPE_MAP.get(task_type, ModelType.LIGHTWEIGHT)
        cands = [m for m in self.gateway.models.values()
                 if m.model_type is need]
        if not cands:
            cands = list(self.gateway.models.values())

        def score(m: ModelConfig) -> float:
            s = 10.0 - 0.3 - 0.2
            recs = self.history.get(f"{m.model_id}:{task_type}",
                                    [])[-50:]
            if recs:
                s += 2.0 * sum(1 for r in recs
                               if r.get("success")) / len(recs)
            return s

        return sorted(cands, key=score, reverse=True)[0]

    def record(self, model_id: str, task_type: str,
               success: bool, latency_ms: int) -> None:
        """调用结果回写(每键滚动保留最近 50 条)"""
        key = f"{model_id}:{task_type}"
        recs = self.history.setdefault(key, [])
        recs.append({"success": bool(success),
                     "latency_ms": latency_ms, "at": _now_iso()})
        del recs[:-50]
        self._flush()


# ---------- 编排器(方案 WebsiteAgentOrchestrator P0) ----------

_INTENT_PROMPT = (
    "你是任务意图分类器。将用户输入分为以下之一, 只输出一个词:\n"
    "chat(闲聊/咨询) summary(要求总结/归纳) draft(要求写作/起草)"
    " unknown(无法判断)"
)

_PROMPTS = {
    "chat": ("你是竹香酒品牌网站 zyjiu.com 的站内助手, 用中文简洁"
             "友好地回答用户, 涉及产品时自然提及竹香型白酒特点, "
             "不夸大功效、不劝酒。"),
    "summary": ("你是站内摘要助手, 将用户给出的内容压缩为要点清晰"
                "的中文简报, 保留关键数字与结论。"),
    "draft": ("你是站内文案助手, 按用户要求起草中文文案, 语气贴合"
              "竹香酒品牌调性(沉稳、有故事感), 不作功效宣称。"),
}


# ---------- P3-③: 步数熔断转人工告警(security_alert/live_qa 同形态) ----------

_STEPS_ALERT_DEDUPE: dict[str, float] = {}     # 进程级 24h 幂等去重窗
_STEPS_ALERT_WINDOW_S = 24 * 3600


def _claim_steps_alert() -> bool:
    """进程级时间窗去重(复刻 security_alert _claim 的 SETNX 语义;
    取进程 dict 时间窗——agent75 单进程部署, 无跨进程去重诉求)"""
    now = time.time()
    expiry = _STEPS_ALERT_DEDUPE.get("max_steps", 0.0)
    if now < expiry:
        return False
    _STEPS_ALERT_DEDUPE["max_steps"] = now + _STEPS_ALERT_WINDOW_S
    return True


async def _notify_agent_steps_exceeded(task_id: str, intent: str,
                                       steps: int) -> bool:
    """超限转人工告警 → 管理员站内信(仿 live_qa _notify_admins_unsolved
    调用面: MemberRepository role=admin → MessageService send_message
    inmail CATEGORY_SECURITY/P1); 整体 fail-soft 绝不碰主链"""
    try:
        from repositories.member_repository import MemberRepository
        admins = sorted(int(m["id"]) for m in
                        await MemberRepository().list_all()
                        if m.get("role") == "admin" and m.get("id"))
        if not admins:
            logger.warning("agent75_steps_alert_no_admin_recipient")
            return False
        from services.message_service import (
            MessageService, CHANNEL_INMAIL, CATEGORY_SECURITY, PRIORITY_P1)
        title = "[网站Agent] 任务步数超限熔断转人工告警"
        content = (
            f"75号 Agent 任务 {task_id}(意图 {intent or 'unknown'}, "
            f"已执行 {steps} 步)超过 AGENT75_MAX_STEPS 上限"
            f"({max_steps()}步), 已熔断截断并停止执行。\n"
            "处置建议: 检查该意图 workflow 步数配置或技能链路"
            "(GET /api/v1/agent/tasks 观测面)。\n"
            "(本告警仅发管理员; 24 小时内不重复发送)")
        svc = MessageService()
        sent = 0
        for admin_id in admins:
            try:
                await svc.send_message(
                    admin_id, CHANNEL_INMAIL, title, content,
                    category=CATEGORY_SECURITY, priority=PRIORITY_P1)
                sent += 1
            except Exception as exc:   # noqa: BLE001 单人失败不阻断其余
                logger.warning("agent75_steps_alert_send_failed "
                               "admin=%s: %s", admin_id, exc)
        return sent > 0
    except Exception as exc:   # noqa: BLE001 告警面异常不碰主链
        logger.warning("agent75_steps_alert_skip: %s", exc)
        return False


def _spawn_steps_alert(task: "TaskRecord") -> None:
    """daemon 线程外发告警(主链零延迟; _spawn_resonance 同形态)"""
    try:
        if not _claim_steps_alert():
            return
        threading.Thread(
            target=_run_steps_alert,
            args=(task.task_id, task.intent or "",
                  len(task.execution_trace)), daemon=True).start()
    except Exception:   # noqa: BLE001
        pass


def _run_steps_alert(task_id: str, intent: str, steps: int) -> None:
    try:
        import asyncio
        asyncio.run(_notify_agent_steps_exceeded(task_id, intent, steps))
    except Exception as exc:   # noqa: BLE001
        logger.warning("agent75_steps_alert_thread_skip: %s", exc)


class WebsiteAgentOrchestrator:
    """主脑 P0+P1+P2: 意图→路由→生成→评估→(进化提议)→轨迹落盘"""

    def __init__(self, gateway: AgentModelGateway | None = None) -> None:
        self.gateway = gateway or AgentModelGateway()
        self.router = ModelRouter(self.gateway)
        AGENT_TASK_DIR.mkdir(parents=True, exist_ok=True)

    def _prompt_for(self, intent: str) -> str:
        """P2: 已审批的 prompt 进化覆盖优先, 无则内置注册表"""
        try:
            from services.agent75_evolution_service import (
                AgentEvolutionService)
            override = AgentEvolutionService().active_prompt(intent)
            if override:
                return override
        except Exception:   # noqa: BLE001 进化面异常不拖累主链
            pass
        return _PROMPTS[intent]

    @staticmethod
    def _role_prefix(role: str) -> str:
        """P3-① 角色身份行前缀(确定性单行模板; 未知角色空串)"""
        line = ROLE_PROMPTS.get(role)
        return f"{line}\n" if line else ""

    @staticmethod
    def _compose_user_prompt(query: str, memory: str = "",
                             step_ctx: str = "") -> str:
        """P3-② on 态画像行 + P3-③ 步间上下文的前置拼装(确定性顺序)"""
        parts: list[str] = []
        if memory:
            parts.append(memory[:200])
        if step_ctx:
            parts.append(step_ctx[:200])
        parts.append(query)
        return "\n".join(parts)

    # -- P3a: 技能层执行路径(SKILL_KILL=on 或技能面异常 → 回落直连) --

    @staticmethod
    def _skill_runtime():
        try:
            from services.agent75_skill_service import skill_kill, SkillRuntime
            if skill_kill():
                return None
            return SkillRuntime()
        except Exception:   # noqa: BLE001
            return None

    def _plan_via_skill(self, rt, query: str, role: str = "member"
                        ) -> tuple[str, dict[str, Any]]:
        s = rt.resolve("classify", role=role)   # P3-① 角色面过滤
        if s is None or s.kind.value != "prompt_tool":
            return self._plan(query)
        model_id = s.spec.get("model_id", "glm-flash")
        content, ok, ms = self.gateway.call(
            model_id, s.spec.get("prompt_template", _INTENT_PROMPT),
            f"用户输入: {query[:200]}")
        self.router.record(model_id, "classification", ok, ms)
        rt.record_invocation(s.skill_id, ok, ms)
        intent = content.strip().lower() if ok else ""
        if intent not in INTENTS or intent == "unknown":
            intent = "chat"
        return intent, {"step_id": "plan_intent", "skill_id": s.skill_id,
                        "model_id": model_id,
                        "status": "success" if ok else "fallback",
                        "latency_ms": ms, "output": intent}

    def _execute_via_skill(self, rt, query: str, intent: str,
                           user_id: str | None = None,
                           role: str = "member", memory: str = "",
                           step_ctx: str = ""
                           ) -> tuple[str, dict[str, Any]]:
        s = rt.resolve("generate", intent, role=role)   # P3-① 角色面
        if s is None:
            return self._execute(query, intent, role=role, memory=memory)
        if s.kind.value == "code_tool":
            return self._execute_code_tool(rt, s, query, intent,
                                           role=role, memory=memory)
        if s.kind.value != "prompt_tool":
            return self._execute(query, intent, role=role, memory=memory)
        model_id = s.spec.get("model_id", "glm-reason")
        # prompt 优先级: P2 意图覆盖(仅当命中内置技能时) > 技能自身
        # 模板 > 内置注册表——进化技能被选中即用其自带 prompt
        if s.source == "builtin" and intent in _PROMPTS:
            prompt = self._prompt_for(intent)
        else:
            prompt = (s.spec.get("prompt_template")
                      or _PROMPTS.get(intent, _PROMPTS["chat"]))
        prompt = self._role_prefix(role) + prompt   # P3-① 角色身份行
        # RA-P2 挫败安抚: 上会话挫败的用户 → 暖心前缀(COMFORT=off 停)
        comfort = False
        try:
            from services.agent75_resonance_service import (
                COMFORT_PREFIX, ResonanceService)
            if ResonanceService().should_comfort(user_id):
                prompt = COMFORT_PREFIX + prompt
                comfort = True
        except Exception:   # noqa: BLE001 共鸣面异常不拖累主链
            pass
        user = self._compose_user_prompt(query, memory, step_ctx)
        content, ok, ms = self.gateway.call(model_id, prompt, user)
        retries = 0
        if not ok:
            retries = 1
            content, ok, ms2 = self.gateway.call(model_id, prompt, user)
            ms += ms2
        self.router.record(model_id, "generation", ok, ms)
        rt.record_invocation(s.skill_id, ok, ms)
        trace = {"step_id": "exec_answer", "skill_id": s.skill_id,
                 "model_id": model_id, "retries": retries,
                 "comfort": comfort, "role": role,
                 "status": "success" if ok else "failed",
                 "latency_ms": ms}
        m_mode = memory_mode()          # P3-② observe/on 留痕(off 无痕)
        if m_mode != "off":
            trace["memory_mode"] = m_mode
        return content, trace

    # -- P3b: code_tool 沙箱执行路径(失败语义: 预检/超限/不可达
    #    → 同槽 prompt_tool 兜底 → 仍败回落直连——审计全程留痕) --

    def _execute_code_tool(self, rt, s, query: str, intent: str,
                           role: str = "member", memory: str = ""
                           ) -> tuple[str, dict[str, Any]]:
        from services import agent75_sandbox_service as sb
        # P3-④ 避坑表(懒加载; 异常/off 视同无表, fail-soft)
        guard = None
        try:
            from services.agent75_pitfall_service import (
                get_guard, pitfall_enabled)
            if pitfall_enabled():
                guard = get_guard()
        except Exception:   # noqa: BLE001
            guard = None
        code = str(s.spec.get("code") or "")
        trace = {"step_id": "exec_answer", "skill_id": s.skill_id,
                 "model_id": "sandbox", "retries": 0,
                 "status": "failed", "latency_ms": 0}
        ok, answer = False, ""
        skip = guard is not None and guard.should_skip_skill(s.skill_id)
        if skip:
            # P3-④ 同签名近期反复失败 → 跳过沙箱直落兜底链
            # (不记 audit/invocation——沙箱未实际执行)
            trace["status"] = "pitfall_shortcircuited"
            trace["pitfall_shortcircuited"] = True
        elif not sb.sandbox_kill():
            valid, err = sb.validate_code(code)
            if valid:
                r = sb.exec_code(code, {"query": query, "intent": intent},
                                 int(s.spec.get("timeout_ms", 3000)))
                ok = bool(r.get("ok"))
                trace["latency_ms"] = r.get("latency_ms", 0)
                if ok:
                    answer = str(
                        (r.get("result") or {}).get("answer") or "")[:2000]
                    if not answer.strip():
                        ok = False
                        trace["status"] = "empty_result"
                else:
                    trace["status"] = f"sandbox_{r.get('error', 'fail')}"
            else:
                trace["status"] = f"precheck_{err}"
            sb.audit_run(s.skill_id, ok, trace["latency_ms"], code)
            rt.record_invocation(s.skill_id, ok, trace["latency_ms"])
            # P3-④ 失败记签名(成功复位 half-open; 自身异常不阻断主链)
            try:
                if guard is not None:
                    if ok:
                        guard.record_success_skill(s.skill_id)
                    else:
                        guard.record_failure(
                            f"{s.skill_id}:{trace['status']}")
            except Exception:   # noqa: BLE001
                pass
        else:
            trace["status"] = "sandbox_killed"
        if ok:
            trace["status"] = "success"
            return answer, trace
        # 兜底: 同槽同意图 prompt_tool(排除 code_tool 自身)
        fallback = next(
            (x for x in rt.skills.values()
             if x.slot == "generate" and x.kind.value == "prompt_tool"
             and x.status.value == "active"
             and (x.intent in (None, intent))), None)
        if fallback is not None:
            content, t2 = self._execute_via_skill_prompt(
                fallback, query, role=role, memory=memory)
            t2["fallback_from"] = s.skill_id
            if skip:
                t2["pitfall_shortcircuited"] = True
            return content, t2
        content, t2 = self._execute(query, intent, role=role, memory=memory)
        t2["fallback_from"] = s.skill_id
        if skip:
            t2["pitfall_shortcircuited"] = True
        return content, t2

    def _execute_via_skill_prompt(self, s, query: str, role: str = "member",
                                  memory: str = ""
                                  ) -> tuple[str, dict[str, Any]]:
        """指定 prompt_tool 技能直调(兜底路径复用)"""
        model_id = s.spec.get("model_id", "glm-reason")
        prompt = (s.spec.get("prompt_template")
                  or _PROMPTS.get(s.intent or "chat", _PROMPTS["chat"]))
        prompt = self._role_prefix(role) + prompt   # P3-① 角色身份行
        user = self._compose_user_prompt(query, memory)
        content, ok, ms = self.gateway.call(model_id, prompt, user)
        retries = 0
        if not ok:
            retries = 1
            content, ok, ms2 = self.gateway.call(model_id, prompt, user)
            ms += ms2
        self.router.record(model_id, "generation", ok, ms)
        return content, {"step_id": "exec_answer",
                         "skill_id": s.skill_id, "model_id": model_id,
                         "retries": retries,
                         "status": "success" if ok else "failed",
                         "latency_ms": ms}

    # -- P3c: 影子双跑(testing 技能后台同输入并发, 结果丢弃只记
    #    metrics——主链零延迟零污染; SHADOW=off 静默省调用费) --

    def _spawn_shadows(self, rt, query: str, intent: str) -> None:
        try:
            from services.agent75_skill_service import shadow_enabled
            if not shadow_enabled():
                return
            ids = [x.skill_id for x in rt.skills.values()
                   if x.slot == "generate"
                   and x.status.value == "testing"
                   and x.intent in (None, intent)]
            if ids:
                threading.Thread(
                    target=self._run_shadows,
                    args=(ids, query, intent), daemon=True).start()
        except Exception:   # noqa: BLE001 影子面异常不碰主链
            pass

    def _run_shadows(self, skill_ids: list[str], query: str,
                     intent: str) -> None:
        from services.agent75_skill_service import SkillRuntime
        for sid in skill_ids:
            try:
                rt = SkillRuntime()   # 每技能重读(低流量下文件权威)
                s = rt.get(sid)
                if s is None or s.status.value != "testing":
                    continue
                if s.kind.value == "code_tool":
                    from services import agent75_sandbox_service as sb
                    code = str(s.spec.get("code") or "")
                    valid, err = sb.validate_code(code)
                    if not valid:
                        rt.record_shadow(sid, False, 0)
                        continue
                    r = sb.exec_code(
                        code, {"query": query, "intent": intent},
                        int(s.spec.get("timeout_ms", 3000)))
                    answer = str((r.get("result") or {}).get("answer")
                                 or "") if r.get("ok") else ""
                    ok = bool(answer.strip())
                    sb.audit_run(sid, ok, r.get("latency_ms", 0), code)
                    rt.record_shadow(sid, ok, r.get("latency_ms", 0))
                else:
                    model_id = s.spec.get("model_id", "glm-reason")
                    prompt = (s.spec.get("prompt_template")
                              or _PROMPTS.get(intent, _PROMPTS["chat"]))
                    answer, ok, ms = self.gateway.call(
                        model_id, prompt, query)
                    # 影子成败=Critic 基线(非空+无拒答句式)
                    ok = bool(ok) and not self._evaluate(
                        query, answer or "")["issues"]
                    rt.record_shadow(sid, ok, ms)
            except Exception as exc:   # noqa: BLE001
                logger.warning("agent75_shadow_failed id=%s: %s",
                               sid, exc)

    # -- RA-P1 通道A: 任务尾自动共鸣回放(daemon 线程, 主链零延迟;
    #    RESONANCE_AUTO=off 独立停, RESONANCE_KILL 总闸优先) --

    def _spawn_resonance(self, task_id: str) -> None:
        try:
            import os
            if os.environ.get("AGENT75_RESONANCE_AUTO", "on"
                              ).strip().lower() in ("off", "0", "false"):
                return
            threading.Thread(target=self._run_resonance,
                             args=(task_id, self.gateway),
                             daemon=True).start()
        except Exception:   # noqa: BLE001 共鸣面异常不碰主链
            pass

    @staticmethod
    def _run_resonance(task_id: str, gateway=None) -> None:
        try:
            from services.agent75_resonance_service import (
                ResonanceService, resonance_kill)
            if resonance_kill():
                return
            ResonanceService().replay_task(task_id, gateway=gateway)
        except Exception as exc:   # noqa: BLE001
            logger.warning("agent75_resonance_replay_skip: %s", exc)

    # -- P3-②: 跨会话记忆行(voice82 画像三表只读消费, fail-soft) --

    def _memory_line(self, user_id: str | None) -> str:
        """组装单行会员画像(角色/主诉求/高频意图 top3);
        off/游客/非数字 id/冷启动(<10 会话)/读取失败 → 空串"""
        if memory_mode() == "off" or user_id is None:
            return ""
        try:
            member_id = int(str(user_id).strip())
        except (TypeError, ValueError):
            return ""
        try:
            bundle = self._fetch_voice82_profile(member_id)
            if not bundle:
                return ""
            p = bundle.get("profile") or {}
            sessions = int((p.get("meta") or {}).get("sessions") or 0)
            if sessions < 10:     # 冷启动阈值(voice82 COLD_START_SESSIONS)
                return ""
            role = (p.get("role") or {}).get("role") or ""
            top_goal = str(p.get("topGoal") or "")
            dist = bundle.get("intentDist") or {}
            top3 = [str(k) for k, _ in sorted(
                dist.items(), key=lambda kv: -float(kv[1] or 0))[:3]]
            line = (f"会员画像:角色={role or '未知'},"
                    f"主诉求={top_goal or '未知'},"
                    f"高频意图={'/'.join(top3) if top3 else '未知'}")
            return line[:200]
        except Exception as exc:   # noqa: BLE001 画像面异常不拖累主链
            logger.warning("agent75_memory_line_skip: %s", exc)
            return ""

    def _fetch_voice82_profile(self, member_id: int) -> dict | None:
        """同步读 async 画像——决策: run() 常在 FastAPI 事件循环线程内被
        同步调用(POST /tasks 为 async 路由), 直接 asyncio.run 会抛
        "cannot be called from a running event loop"; 故在独立线程跑专属
        事件循环 + 有界 join(2s)——同步可用/超时可控/fail-soft 三得。
        红线: 只读消费 voice82 标签与统计(role/topGoal/intentDist),
        不引入对话原文。"""
        import asyncio
        import concurrent.futures

        async def _go():
            from services.voice82_dialog_service import (
                Voice82DialogService, profile_key)
            svc = Voice82DialogService()
            prof = await svc.get_profile(member_id)
            if not prof.get("success"):
                return None
            intent_dist: dict = {}
            try:   # intentDist 不在 get_profile 返回面 → 直读 biz 表
                key = profile_key(member_id)
                if key:
                    row = await svc._hgetall(svc.TBL_BIZ, key)
                    intent_dist = json.loads(row.get("intentDist") or "{}")
            except Exception:   # noqa: BLE001
                intent_dist = {}
            return {"profile": prof.get("profile") or {},
                    "intentDist": intent_dist}

        try:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=1) as ex:
                return ex.submit(asyncio.run, _go()).result(timeout=2.0)
        except Exception as exc:   # noqa: BLE001 读取失败回落空串
            logger.warning("agent75_memory_profile_skip: %s", exc)
            return None

    # -- P3-③: 多步编排(激活 builtin_workflows 死骨架)+步数熔断 --

    @staticmethod
    def _workflow_for(rt, intent: str):
        """按意图查工作流(异常/未命中 → None 回落单步现行为)"""
        try:
            return getattr(rt, "workflows", {}).get(intent)
        except Exception:   # noqa: BLE001
            return None

    def _run_workflow(self, rt, task: TaskRecord, query: str, intent: str,
                      wf, user_id: str | None, role: str, memory: str
                      ) -> tuple[str, list[dict[str, Any]], bool]:
        """按 steps 循环执行: classify 步已由规划阶段完成(计 1 步),
        generate/review 依次执行; 前步输出摘要(≤200 字)进后步 user
        prompt; 超 AGENT75_MAX_STEPS 立即截断(返回 interrupted=True)。
        注: WorkflowStep.timeout_ms 现执行路径未消费(单步沿用既有
        网关重试/沙箱超时机制), 本轮不强接。"""
        limit = max_steps()
        traces: list[dict[str, Any]] = []
        answer = ""
        executed = 1                      # 规划步(classify)已执行
        carry = str(intent or "")         # 规划步输出=意图词
        for step in wf.steps:
            if step.slot == "classify":
                continue
            executed += 1
            if executed > limit:
                traces.append({"step_id": step.step_id,
                               "skill_id": step.slot,
                               "status": "interrupted",
                               "interrupted_reason": "max_steps"})
                task.answer = max_steps_answer(limit)
                task.interrupted_reason = "max_steps"
                task.status = TaskStatus.FAILED
                return answer, traces, True
            if step.slot == "generate":
                step_ctx = (f"[上步输出] {carry[:200]}" if carry else "")
                answer, t = self._execute_via_skill(
                    rt, query, intent, user_id=user_id, role=role,
                    memory=memory, step_ctx=step_ctx)
                traces.append(t)
                carry = str(answer or "")
            elif step.slot == "review":
                traces.append(self._run_review_step(
                    rt, step, query, answer, role))
            else:
                traces.append({"step_id": step.step_id,
                               "status": "skipped_unknown_slot"})
        return answer, traces, False

    def _run_review_step(self, rt, step, query: str, answer: str,
                         role: str) -> dict[str, Any]:
        """review 步消费 critic_review builtin_fn(fn=critic → Critic
        基线评估, 确定性无 LLM); 非该形态留痕跳过不动作"""
        s = rt.resolve("review", role=role)
        if s is None:
            return {"step_id": step.step_id, "skill_id": step.slot,
                    "status": "skipped_no_active_skill"}
        if s.kind.value != "builtin_fn" or s.spec.get("fn") != "critic":
            return {"step_id": step.step_id, "skill_id": s.skill_id,
                    "status": "skipped_unsupported_kind"}
        issues = self._evaluate(query, answer or "")["issues"]
        return {"step_id": step.step_id, "skill_id": s.skill_id,
                "status": "success" if not issues else "review_issues",
                "issues": issues}

    # -- Planner(意图识别走分类路由; 失败回落 chat) --

    def _plan(self, query: str) -> tuple[str, dict[str, Any]]:
        cfg = self.router.route("classification")
        content, ok, ms = self.gateway.call(
            cfg.model_id, _INTENT_PROMPT, f"用户输入: {query[:200]}")
        self.router.record(cfg.model_id, "classification", ok, ms)
        intent = content.strip().lower() if ok else ""
        if intent not in INTENTS or intent == "unknown":
            intent = "chat"
        return intent, {"step_id": "plan_intent", "skill_id": "intent",
                        "model_id": cfg.model_id,
                        "status": "success" if ok else "fallback",
                        "latency_ms": ms, "output": intent}

    # -- Executor(生成路由 + 失败重试一次, 方案 WorkflowStep.retry=1) --

    def _execute(self, query: str, intent: str, role: str = "member",
                 memory: str = ""
                 ) -> tuple[str, dict[str, Any]]:
        cfg = self.router.route("generation")
        prompt = self._role_prefix(role) + self._prompt_for(intent)  # ①
        user = self._compose_user_prompt(query, memory)              # ②
        content, ok, ms = self.gateway.call(cfg.model_id, prompt, user)
        retries = 0
        if not ok:                      # 重试一次(同档, 历史已记录失败)
            retries = 1
            content, ok, ms2 = self.gateway.call(
                cfg.model_id, prompt, user)
            ms += ms2
        self.router.record(cfg.model_id, "generation", ok, ms)
        trace = {"step_id": "exec_answer", "skill_id": "answer",
                 "model_id": cfg.model_id, "retries": retries,
                 "role": role,
                 "status": "success" if ok else "failed",
                 "latency_ms": ms}
        m_mode = memory_mode()          # P3-② observe/on 留痕(off 无痕)
        if m_mode != "off":
            trace["memory_mode"] = m_mode
        return content, trace

    # -- Critic(P0 基线 + P2 进化触发位) --

    @staticmethod
    def _evaluate(query: str, answer: str) -> dict[str, Any]:
        issues: list[str] = []
        if not answer.strip():
            issues.append("empty_answer")
        elif len(answer.strip()) < 10:
            issues.append("too_short")
        if re.search(r"(我无法|我不能|作为AI|作为 AI)", answer):
            issues.append("refusal_pattern")
        return {"score": 1.0 if not issues else 0.0,
                "issues": issues,
                "needs_evolution": bool(issues)}   # P2 进化引擎入口

    # -- 持久化(执行日志: 每任务 JSON + jsonl 追加) --

    def _save(self, task: TaskRecord) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not task.created_at:
            task.created_at = now
        task.updated_at = now
        (AGENT_TASK_DIR / f"{task.task_id}.json").write_text(
            task.model_dump_json(), encoding="utf-8")
        with open(AGENT_TASK_DIR / "tasks.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"task_id": task.task_id,
                                "status": task.status,
                                "intent": task.intent,
                                "steps": len(task.execution_trace),
                                "updated_at": now},
                               ensure_ascii=False) + "\n")

    def load(self, task_id: str) -> TaskRecord | None:
        p = AGENT_TASK_DIR / f"{task_id}.json"
        if not p.is_file():
            return None
        return TaskRecord.model_validate_json(p.read_text(encoding="utf-8"))

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for p in sorted(AGENT_TASK_DIR.glob("t_*.json"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            rows.append({"task_id": p.stem,
                         "status": json.loads(
                             p.read_text(encoding="utf-8")).get("status")})
            if len(rows) >= limit:
                break
        return rows

    # -- 主入口 --

    def run(self, query: str, user_id: str | None = None,
            context: dict[str, Any] | None = None,
            role: str = "member") -> TaskRecord:
        task = TaskRecord(task_id=f"t_{uuid.uuid4().hex[:12]}",
                          user_id=user_id, query=query,
                          role=role if role in AGENT_ROLES else "member",
                          status=TaskStatus.PLANNING,
                          created_at="", updated_at="")
        try:
            rt = self._skill_runtime()      # P3a: 技能层(可 None)
            if rt is not None:
                intent, trace = self._plan_via_skill(rt, query, task.role)
            else:
                intent, trace = self._plan(query)
            task.intent = intent
            task.execution_trace.append(trace)
            # P3-②: 跨会话记忆行(off 零痕迹/observe 留痕/on 进 prompt)
            task.memoryLine = self._memory_line(user_id)
            self._save(task)

            task.status = TaskStatus.EXECUTING
            memory = (task.memoryLine if memory_mode() == "on" else "")
            if rt is not None:
                # P3-③: on 态查工作流(按 intent 匹配), 命中走多步编排,
                # 未命中/off 回落单步现行为(逐字不变)
                wf = (self._workflow_for(rt, intent)
                      if workflow_enabled() else None)
                if wf is not None:
                    answer, wtraces, interrupted = self._run_workflow(
                        rt, task, query, intent, wf,
                        user_id=task.user_id, role=task.role,
                        memory=memory)
                    task.execution_trace.extend(wtraces)
                    self._save(task)
                    if interrupted:    # 熔断截断: 不再评估/进化/回放
                        _spawn_steps_alert(task)   # 话术由熔断分支写入
                        logger.info("agent75_task_interrupted id=%s "
                                    "reason=max_steps", task.task_id)
                        return task
                    task.answer = answer
                    self._save(task)
                else:
                    answer, trace = self._execute_via_skill(
                        rt, query, intent, user_id=task.user_id,
                        role=task.role, memory=memory)
                    task.answer = answer
                    task.execution_trace.append(trace)
                    self._save(task)
                self._spawn_shadows(rt, query, intent)   # P3c 影子
            else:
                answer, trace = self._execute(query, intent,
                                              role=task.role,
                                              memory=memory)
                task.answer = answer
                task.execution_trace.append(trace)
                self._save(task)

            task.status = TaskStatus.EVALUATING
            task.evaluation = self._evaluate(query, answer)
            task.status = (TaskStatus.COMPLETED
                           if not task.evaluation["issues"]
                           else TaskStatus.FAILED)
            self._save(task)
            # P2: 失败任务 → 进化引擎提议(仅生成 pending 候选,
            # 不自动生效——人工审批闸门, 方案灰度口径)
            if task.evaluation["needs_evolution"]:
                try:
                    from services.agent75_evolution_service import (
                        AgentEvolutionService)
                    cand = AgentEvolutionService(
                        self.gateway).propose(task)
                    if cand:
                        task.execution_trace.append(
                            {"step_id": "evolution_propose",
                             "skill_id": "evolution",
                             "status": "proposed",
                             "candidate_id": cand.evolution_id})
                except Exception as exc:   # noqa: BLE001
                    logger.warning("agent75_evolution_propose_skip: %s",
                                   exc)
            self._save(task)
            self._spawn_resonance(task.task_id)   # RA-P1 通道A 自动回放
            logger.info("agent75_task_done id=%s intent=%s status=%s",
                        task.task_id, intent, task.status)
        except Exception as exc:   # noqa: BLE001
            task.status = TaskStatus.FAILED
            task.execution_trace.append(
                {"step_id": "orchestrator", "status": "failed",
                 "error": str(exc)})
            self._save(task)
            logger.warning("agent75_task_error: %s", exc)
        return task
