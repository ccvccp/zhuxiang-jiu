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
    """主脑 P0: 意图→路由→生成→基线评估→轨迹落盘"""

    def __init__(self, gateway: AgentModelGateway | None = None) -> None:
        self.gateway = gateway or AgentModelGateway()
        AGENT_TASK_DIR.mkdir(parents=True, exist_ok=True)

    # -- Planner(P0: 单步轻量意图识别, 失败回落 chat) --

    def _plan(self, query: str) -> tuple[str, dict[str, Any]]:
        content, ok, ms = self.gateway.call(
            "glm-flash", _INTENT_PROMPT,
            f"用户输入: {query[:200]}")
        intent = content.strip().lower() if ok else ""
        if intent not in INTENTS or intent == "unknown":
            intent = "chat"
        return intent, {"step_id": "plan_intent", "skill_id": "intent",
                        "model_id": "glm-flash",
                        "status": "success" if ok else "fallback",
                        "latency_ms": ms, "output": intent}

    # -- Executor(P0: 按意图路由 prompt 的单步生成) --

    def _execute(self, query: str, intent: str
                 ) -> tuple[str, dict[str, Any]]:
        content, ok, ms = self.gateway.call(
            "glm-reason", _PROMPTS[intent], query)
        return content, {"step_id": "exec_answer", "skill_id": "answer",
                         "model_id": "glm-reason",
                         "status": "success" if ok else "failed",
                         "latency_ms": ms}

    # -- Critic(P0 基线: 非空/字数/拒答句式) --

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
                "needs_evolution": False}   # P2 进化引擎挂点

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
            intent, trace = self._plan(query)
            task.intent = intent
            task.execution_trace.append(trace)
            self._save(task)

            task.status = TaskStatus.EXECUTING
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
