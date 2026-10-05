"""75号·Skill 层(P3a, 2026-10-05)——《P3 详细设计方案》§1 落地

技能注册表 + 工作流编排 + 生命周期(draft→testing→active→deprecated)
+ metrics 评分与淘汰。P0-P2 的 plan/execute/critic 逻辑收编为内置
技能(行为不变), 为后续 code_tool(P3b)/灰度(P3c)铺路。

口径:
    - slot=classify/generate/review 三类执行位; 工作流步引用 slot,
      同 slot 同 intent 的 active 技能按 metrics 选(与 ModelRouter
      同构评分); testing 技能不入主链(P3c 影子)
    - AGENT75_SKILL_KILL=on → 编排器整层旁路回落 P0 直连路径
    - 淘汰: invocations>=20 且滚动50 success_rate<0.6 → 自动降回
      testing + 告警日志(观测面动作, 不删不改)
存储: AGENT_TASK_DIR/skills.json(首载种子=内置注册表, 此后文件权威)
"""

import json
import logging
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from services.agent75_orchestrator_service import AGENT_TASK_DIR

logger = logging.getLogger(__name__)

SLOTS = ("classify", "generate", "review")
INTENTS = ("chat", "summary", "draft")


def skill_kill() -> bool:
    """技能层一键旁路(降级不降能: 回落 P0 直连)"""
    return os.environ.get(
        "AGENT75_SKILL_KILL", "").strip().lower() in ("on", "1", "true")


def shadow_enabled() -> bool:
    """P3c 影子双跑开关(默认 on; off 则 testing 技能静默省调用费)"""
    return os.environ.get(
        "AGENT75_SKILL_SHADOW", "on").strip().lower() not in ("off", "0",
                                                              "false")


class SkillKind(str, Enum):
    PROMPT_TOOL = "prompt_tool"   # prompt 模板+模型档(P3a)
    BUILTIN_FN = "builtin_fn"    # 本地函数收编(critic 等零代码技能)
    CODE_TOOL = "code_tool"      # 沙箱执行(P3b, 预留)


class SkillStatus(str, Enum):
    DRAFT = "draft"
    TESTING = "testing"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class SkillDefinition(BaseModel):
    skill_id: str
    name: str
    version: str = "1.0.0"
    status: SkillStatus = SkillStatus.DRAFT
    kind: SkillKind
    slot: str                        # classify/generate/review
    intent: str | None = None        # classify 为 None(意图无关)
    spec: dict[str, Any] = {}        # prompt_tool: {model_id, prompt_template}
                                     # builtin_fn: {fn: "critic"}
    metrics: dict[str, Any] = {
        "invocations": 0, "successes": 0, "recent": [],
        "avg_latency_ms": 0,
        "shadow_runs": 0, "shadow_recent": []}
    source: str = "builtin"
    created_at: str = ""
    updated_at: str = ""


class WorkflowStep(BaseModel):
    step_id: str                     # 与 P0 轨迹命名保持(plan_intent 等)
    slot: str
    retry: int = 1
    timeout_ms: int = 30000


class WorkflowDefinition(BaseModel):
    workflow_id: str
    intent: str
    steps: list[WorkflowStep]


# ---------- 内置技能(收编 P0-P2 现有逻辑, 首批 5 件) ----------

def builtin_skills() -> list[SkillDefinition]:
    """从编排器注册表生成——prompt 单一来源, 收编零行为变化"""
    from services.agent75_orchestrator_service import (
        _INTENT_PROMPT, _PROMPTS)

    def mk(sid, name, slot, intent, model, prompt):
        return SkillDefinition(
            skill_id=sid, name=name, status=SkillStatus.ACTIVE,
            kind=SkillKind.PROMPT_TOOL, slot=slot, intent=intent,
            spec={"model_id": model, "prompt_template": prompt})

    def mkfn(sid, name, slot):
        return SkillDefinition(
            skill_id=sid, name=name, status=SkillStatus.ACTIVE,
            kind=SkillKind.BUILTIN_FN, slot=slot, intent=None,
            spec={"fn": "critic"})

    return [
        mk("intent_classify", "意图识别", "classify", None,
           "glm-flash", _INTENT_PROMPT),
        mk("answer_chat", "通用回答·chat", "generate", "chat",
           "glm-reason", _PROMPTS["chat"]),
        mk("answer_summary", "摘要回答·summary", "generate", "summary",
           "glm-reason", _PROMPTS["summary"]),
        mk("draft_copy", "品牌文案起草·draft", "generate", "draft",
           "glm-reason", _PROMPTS["draft"]),
        mkfn("critic_review", "回答质量复核", "review"),
    ]


def builtin_workflows() -> list[WorkflowDefinition]:
    """三意图内置工作流(与 P0 行为等价: 分类→生成→复核)"""
    steps = [
        WorkflowStep(step_id="plan_intent", slot="classify"),
        WorkflowStep(step_id="exec_answer", slot="generate"),
        WorkflowStep(step_id="critic_review", slot="review"),
    ]
    return [WorkflowDefinition(workflow_id=f"flow_{i}", intent=i,
                               steps=steps) for i in INTENTS]


# ---------- 注册表运行时 ----------

class SkillRuntime:

    def __init__(self) -> None:
        AGENT_TASK_DIR.mkdir(parents=True, exist_ok=True)
        self.skills: dict[str, SkillDefinition] = self._load()
        self.workflows: dict[str, WorkflowDefinition] = {
            w.intent: w for w in builtin_workflows()}

    @staticmethod
    def _path() -> Path:
        return AGENT_TASK_DIR / "skills.json"

    def _load(self) -> dict[str, SkillDefinition]:
        p = self._path()
        if not p.is_file():
            seed = {s.skill_id: s for s in builtin_skills()}
            self._flush(seed)
            return seed
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return {k: SkillDefinition.model_validate(v)
                    for k, v in raw.items()}
        except Exception:   # noqa: BLE001 损坏回落种子
            logger.warning("agent75_skills_file_corrupt, 回落内置种子")
            return {s.skill_id: s for s in builtin_skills()}

    def _flush(self, skills: dict[str, SkillDefinition] | None = None) -> None:
        data = skills or self.skills
        self._path().write_text(
            json.dumps({k: v.model_dump() for k, v in data.items()},
                       ensure_ascii=False, indent=1), encoding="utf-8")

    # -- 解析: 同 slot(+intent 兼容)的 active 技能按 metrics 选 --

    @staticmethod
    def _score(s: SkillDefinition) -> float:
        """与 ModelRouter 同构: 成本/延迟基线 + 滚动成功率加分"""
        m = s.metrics
        recs = m.get("recent") or []
        rate = (sum(1 for r in recs if r.get("ok")) / len(recs)
                if recs else 0.0)
        return 10.0 - 0.3 - 0.2 + 2.0 * rate

    def resolve(self, slot: str, intent: str | None = None
                ) -> SkillDefinition | None:
        cands = [s for s in self.skills.values()
                 if s.slot == slot and s.status == SkillStatus.ACTIVE
                 and (s.intent is None or s.intent == intent
                      or intent is None)]
        if not cands:
            return None
        return sorted(cands, key=self._score, reverse=True)[0]

    def get(self, skill_id: str) -> SkillDefinition | None:
        return self.skills.get(skill_id)

    def list(self) -> list[dict[str, Any]]:
        rows = []
        for s in sorted(self.skills.values(),
                        key=lambda x: (x.slot, -self._score(x))):
            d = s.model_dump()
            if s.status == SkillStatus.TESTING:
                d["ready"] = self.ready_flag(s)   # P3c 转正提示
            rows.append(d)
        return rows

    # -- 调用回写 + 淘汰 --

    def record_invocation(self, skill_id: str, ok: bool,
                          latency_ms: int) -> dict[str, Any] | None:
        s = self.skills.get(skill_id)
        if not s:
            return None
        m = s.metrics
        m["invocations"] = int(m.get("invocations", 0)) + 1
        m["successes"] = int(m.get("successes", 0)) + (1 if ok else 0)
        m["avg_latency_ms"] = round(
            (int(m.get("avg_latency_ms", 0))
             * (m["invocations"] - 1) + latency_ms) / m["invocations"])
        recs = m.setdefault("recent", [])
        recs.append({"ok": bool(ok), "latency_ms": latency_ms,
                     "at": int(time.time())})
        del recs[:-50]
        demoted = None
        if (s.status == SkillStatus.ACTIVE and m["invocations"] >= 20):
            rate = (sum(1 for r in recs if r.get("ok")) / len(recs)
                    if recs else 1.0)
            if rate < 0.6:
                s.status = SkillStatus.TESTING
                demoted = {"skill_id": skill_id, "success_rate":
                           round(rate, 3)}
                logger.warning(
                    "agent75_skill_demoted id=%s rate=%.2f "
                    "(自动降回 testing, 人工可再 activate)",
                    skill_id, rate)
        self._flush()
        return demoted

    # -- P3c 影子调用回写(结果不入主链, 只记 metrics) --

    def record_shadow(self, skill_id: str, ok: bool,
                      latency_ms: int) -> None:
        s = self.skills.get(skill_id)
        if not s:
            return
        m = s.metrics
        m["shadow_runs"] = int(m.get("shadow_runs", 0)) + 1
        recs = m.setdefault("shadow_recent", [])
        recs.append({"ok": bool(ok), "latency_ms": latency_ms})
        del recs[:-50]
        self._flush()

    # -- P3c 转正判据(仅提示不动作——人工 activate 闸门不动) --

    def ready_flag(self, s: SkillDefinition) -> bool:
        """shadow>=20 且 成功率>=现行-5pp 且 延迟<=现行x1.5;
        现行无历史时成功率基线 0.8"""
        if s.status != SkillStatus.TESTING:
            return False
        m = s.metrics
        recs = m.get("shadow_recent") or []
        if int(m.get("shadow_runs", 0)) < 20 or len(recs) < 20:
            return False
        rate = sum(1 for r in recs if r.get("ok")) / len(recs)
        act = next((x for x in self.skills.values()
                    if x.slot == s.slot and x.status == SkillStatus.ACTIVE
                    and x.intent in (None, s.intent)), None)
        a_recs = (act.metrics.get("recent") or []) if act else []
        a_rate = (sum(1 for r in a_recs if r.get("ok")) / len(a_recs)
                  if a_recs else 0.8)
        if rate < a_rate - 0.05:
            return False
        s_avg = sum(r.get("latency_ms", 0) for r in recs) / len(recs)
        a_avg = (act.metrics.get("avg_latency_ms") or 0) if act else 0
        if a_avg and s_avg > a_avg * 1.5:
            return False
        return True

    # -- 生命周期操作(API 面) --

    def set_status(self, skill_id: str, status: SkillStatus
                   ) -> SkillDefinition:
        s = self.skills.get(skill_id)
        if not s:
            raise KeyError(skill_id)
        s.status = status
        self._flush()
        return s

    def upsert(self, skill: SkillDefinition) -> SkillDefinition:
        """进化/人工注册新技能(evolution approve 落 testing)"""
        self.skills[skill.skill_id] = skill
        self._flush()
        return skill

    def deprecate_by_source(self, evolution_id: str) -> str | None:
        """按进化来源回滚: 该来源技能降 deprecated"""
        for s in self.skills.values():
            if s.source == f"evolution:{evolution_id}":
                s.status = SkillStatus.DEPRECATED
                self._flush()
                return s.skill_id
        return None
