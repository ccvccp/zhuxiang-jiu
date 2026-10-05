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
    intent: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    answer: str = ""
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)
    evaluation: Optional[dict[str, Any]] = None
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
    "chat": ("你是竹香酒品牌网站 zxjiu.com 的站内助手, 用中文简洁"
             "友好地回答用户, 涉及产品时自然提及竹香型白酒特点, "
             "不夸大功效、不劝酒。"),
    "summary": ("你是站内摘要助手, 将用户给出的内容压缩为要点清晰"
                "的中文简报, 保留关键数字与结论。"),
    "draft": ("你是站内文案助手, 按用户要求起草中文文案, 语气贴合"
              "竹香酒品牌调性(沉稳、有故事感), 不作功效宣称。"),
}


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

    def _plan_via_skill(self, rt, query: str) -> tuple[str, dict[str, Any]]:
        s = rt.resolve("classify")
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

    def _execute_via_skill(self, rt, query: str, intent: str
                           ) -> tuple[str, dict[str, Any]]:
        s = rt.resolve("generate", intent)
        if s is None:
            return self._execute(query, intent)
        if s.kind.value == "code_tool":
            return self._execute_code_tool(rt, s, query, intent)
        if s.kind.value != "prompt_tool":
            return self._execute(query, intent)
        model_id = s.spec.get("model_id", "glm-reason")
        # prompt 优先级: P2 意图覆盖(仅当命中内置技能时) > 技能自身
        # 模板 > 内置注册表——进化技能被选中即用其自带 prompt
        if s.source == "builtin" and intent in _PROMPTS:
            prompt = self._prompt_for(intent)
        else:
            prompt = (s.spec.get("prompt_template")
                      or _PROMPTS.get(intent, _PROMPTS["chat"]))
        content, ok, ms = self.gateway.call(model_id, prompt, query)
        retries = 0
        if not ok:
            retries = 1
            content, ok, ms2 = self.gateway.call(model_id, prompt, query)
            ms += ms2
        self.router.record(model_id, "generation", ok, ms)
        rt.record_invocation(s.skill_id, ok, ms)
        return content, {"step_id": "exec_answer", "skill_id": s.skill_id,
                         "model_id": model_id, "retries": retries,
                         "status": "success" if ok else "failed",
                         "latency_ms": ms}

    # -- P3b: code_tool 沙箱执行路径(失败语义: 预检/超限/不可达
    #    → 同槽 prompt_tool 兜底 → 仍败回落直连——审计全程留痕) --

    def _execute_code_tool(self, rt, s, query: str, intent: str
                           ) -> tuple[str, dict[str, Any]]:
        from services import agent75_sandbox_service as sb
        code = str(s.spec.get("code") or "")
        trace = {"step_id": "exec_answer", "skill_id": s.skill_id,
                 "model_id": "sandbox", "retries": 0,
                 "status": "failed", "latency_ms": 0}
        ok, answer = False, ""
        if not sb.sandbox_kill():
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
            content, t2 = self._execute_via_skill_prompt(fallback, query)
            t2["fallback_from"] = s.skill_id
            return content, t2
        content, t2 = self._execute(query, intent)
        t2["fallback_from"] = s.skill_id
        return content, t2

    def _execute_via_skill_prompt(self, s, query: str
                                  ) -> tuple[str, dict[str, Any]]:
        """指定 prompt_tool 技能直调(兜底路径复用)"""
        model_id = s.spec.get("model_id", "glm-reason")
        prompt = (s.spec.get("prompt_template")
                  or _PROMPTS.get(s.intent or "chat", _PROMPTS["chat"]))
        content, ok, ms = self.gateway.call(model_id, prompt, query)
        retries = 0
        if not ok:
            retries = 1
            content, ok, ms2 = self.gateway.call(model_id, prompt, query)
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
                             args=(task_id,), daemon=True).start()
        except Exception:   # noqa: BLE001 共鸣面异常不碰主链
            pass

    @staticmethod
    def _run_resonance(task_id: str) -> None:
        try:
            from services.agent75_resonance_service import (
                ResonanceService, resonance_kill)
            if resonance_kill():
                return
            ResonanceService().replay_task(task_id)
        except Exception as exc:   # noqa: BLE001
            logger.warning("agent75_resonance_replay_skip: %s", exc)

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

    def _execute(self, query: str, intent: str
                 ) -> tuple[str, dict[str, Any]]:
        cfg = self.router.route("generation")
        content, ok, ms = self.gateway.call(
            cfg.model_id, self._prompt_for(intent), query)
        retries = 0
        if not ok:                      # 重试一次(同档, 历史已记录失败)
            retries = 1
            content, ok, ms2 = self.gateway.call(
                cfg.model_id, self._prompt_for(intent), query)
            ms += ms2
        self.router.record(cfg.model_id, "generation", ok, ms)
        return content, {"step_id": "exec_answer", "skill_id": "answer",
                         "model_id": cfg.model_id, "retries": retries,
                         "status": "success" if ok else "failed",
                         "latency_ms": ms}

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
            context: dict[str, Any] | None = None) -> TaskRecord:
        task = TaskRecord(task_id=f"t_{uuid.uuid4().hex[:12]}",
                          user_id=user_id, query=query,
                          status=TaskStatus.PLANNING,
                          created_at="", updated_at="")
        try:
            rt = self._skill_runtime()      # P3a: 技能层(可 None)
            if rt is not None:
                intent, trace = self._plan_via_skill(rt, query)
            else:
                intent, trace = self._plan(query)
            task.intent = intent
            task.execution_trace.append(trace)
            self._save(task)

            task.status = TaskStatus.EXECUTING
            if rt is not None:
                answer, trace = self._execute_via_skill(rt, query, intent)
                self._spawn_shadows(rt, query, intent)   # P3c 影子
            else:
                answer, trace = self._execute(query, intent)
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
