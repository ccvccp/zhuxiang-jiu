"""75号·进化引擎(P2, 2026-10-05)

方案 EvolutionEngine 落地: 失败任务 → LLM 提议改进(prompt 层)
→ 结构化校验 → pending 候选 → 人工 approve 生效(灰度闸门)
→ rollback 一键回退。方案自荐口径「先从 Prompt 层进化开始」,
本层仅支持 type=prompt;skill/workflow 层留待 P3。

安全边界(方案原文):
    - 提议不自动生效(approve 是人工动作)
    - 生效面只覆盖 prompt 文案(不触代码/配置)
    - rollback 即刻还原内置注册表
存储: AGENT_TASK_DIR/evolution_candidates.json + active_prompts.json
"""

import json
import logging
import re
import time
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from services.agent75_orchestrator_service import (
    AGENT_TASK_DIR,
    AgentModelGateway,
)

logger = logging.getLogger(__name__)

EVOLUTION_TYPES = ("prompt",)
INTENTS = ("chat", "summary", "draft")
# 红线词基线校验(与站内助手口径一致: 不劝酒/不功效宣称)
_BANNED = re.compile(r"(功效|治疗|养生疗效|包治|解酒|多喝|劝酒)")


class CandidateStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class EvolutionCandidate(BaseModel):
    evolution_id: str
    source_task_id: str
    type: str                        # P2 仅 prompt
    intent: str
    proposed_change: dict[str, Any]  # {prompt_text, rationale}
    validation_status: str           # pending/approved/...
    created_at: str = ""
    applied_at: str = ""


class AgentEvolutionService:

    def __init__(self, gateway: AgentModelGateway | None = None) -> None:
        self.gateway = gateway or AgentModelGateway()
        AGENT_TASK_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- 存储 ----------

    @staticmethod
    def _cand_path() -> Path:
        return AGENT_TASK_DIR / "evolution_candidates.json"

    @staticmethod
    def _active_path() -> Path:
        return AGENT_TASK_DIR / "active_prompts.json"

    def _load_cands(self) -> dict[str, dict[str, Any]]:
        p = self._cand_path()
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:   # noqa: BLE001
                pass
        return {}

    def _save_cands(self, cands: dict[str, dict[str, Any]]) -> None:
        self._cand_path().write_text(
            json.dumps(cands, ensure_ascii=False, indent=1),
            encoding="utf-8")

    def _load_active(self) -> dict[str, str]:
        p = self._active_path()
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:   # noqa: BLE001
                pass
        return {}

    # ---------- 提议(方案 propose: LLM 生成改进候选) ----------

    _PROPOSE_SYSTEM = (
        "你是 Prompt 进化顾问。根据失败任务的意图与回答, 为该意图"
        "写一个改进版的中文 system prompt(角色定位+输出要求), "
        "修复暴露的问题。只输出 JSON: "
        '{"type":"prompt","intent":"chat|summary|draft",'
        '"prompt_text":"改进后的完整 system prompt",'
        '"rationale":"一句话改进理由"}  红线: 不得劝酒/功效宣称。'
    )

    def propose(self, task) -> EvolutionCandidate | None:
        """失败任务 → pending 候选(校验不过/LLM 失败 → None)"""
        if not (task.evaluation or {}).get("needs_evolution"):
            return None
        user = (f"意图: {task.intent}\n用户输入: {task.query[:200]}\n"
                f"失败回答: {task.answer[:200]}\n"
                f"问题: {task.evaluation.get('issues')}")
        content, ok, _ = self.gateway.call("glm-reason",
                                           self._PROPOSE_SYSTEM, user)
        if not ok:
            return None
        data = self._extract_json(content or "")
        cand = self._validate(data, task)
        if cand is None:
            return None
        cands = self._load_cands()
        cands[cand.evolution_id] = cand.model_dump()
        self._save_cands(cands)
        logger.info("agent75_evolution_proposed id=%s intent=%s",
                    cand.evolution_id, cand.intent)
        return cand

    def _validate(self, data: dict | None,
                  task) -> EvolutionCandidate | None:
        """结构校验: type/intent/长度/红线词(方案 validate 步)"""
        if not isinstance(data, dict):
            return None
        if data.get("type") not in EVOLUTION_TYPES:
            return None
        intent = str(data.get("intent") or "")
        text = str(data.get("prompt_text") or "").strip()
        if intent not in INTENTS or not (10 <= len(text) <= 500):
            return None
        if _BANNED.search(text):
            return None
        return EvolutionCandidate(
            evolution_id=f"evo_{int(time.time() * 1000):x}",
            source_task_id=task.task_id,
            type="prompt", intent=intent,
            proposed_change={"prompt_text": text,
                             "rationale": str(
                                 data.get("rationale") or "")[:120]},
            validation_status=CandidateStatus.PENDING.value,
            created_at=task.updated_at or "")

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:   # noqa: BLE001
            return None

    # ---------- 审批/回滚(方案 approve/reject/rollback) ----------

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = sorted(self._load_cands().values(),
                      key=lambda c: c.get("created_at") or "",
                      reverse=True)
        return rows[:limit]

    def get(self, evolution_id: str) -> dict[str, Any] | None:
        return self._load_cands().get(evolution_id)

    def approve(self, evolution_id: str) -> dict[str, Any]:
        """pending → approved: prompt 覆盖即时生效(灰度=覆盖单意图)"""
        cands = self._load_cands()
        c = cands.get(evolution_id)
        if not c:
            raise KeyError(evolution_id)
        if c["validation_status"] != CandidateStatus.PENDING.value:
            raise ValueError(f"状态 {c['validation_status']} 不可审批")
        active = self._load_active()
        active[c["intent"]] = c["proposed_change"]["prompt_text"]
        self._active_path().write_text(
            json.dumps(active, ensure_ascii=False), encoding="utf-8")
        c["validation_status"] = CandidateStatus.APPROVED.value
        c["applied_at"] = self._now()
        cands[evolution_id] = c
        self._save_cands(cands)
        return c

    def reject(self, evolution_id: str) -> dict[str, Any]:
        cands = self._load_cands()
        c = cands.get(evolution_id)
        if not c:
            raise KeyError(evolution_id)
        if c["validation_status"] != CandidateStatus.PENDING.value:
            raise ValueError(f"状态 {c['validation_status']} 不可驳回")
        c["validation_status"] = CandidateStatus.REJECTED.value
        cands[evolution_id] = c
        self._save_cands(cands)
        return c

    def rollback(self, intent: str) -> dict[str, str]:
        """按意图回退: 移除覆盖, 回落内置注册表(方案 rollback)"""
        active = self._load_active()
        if intent not in active:
            raise KeyError(intent)
        del active[intent]
        self._active_path().write_text(
            json.dumps(active, ensure_ascii=False), encoding="utf-8")
        for c in self._load_cands().values():
            if (c["intent"] == intent and c["validation_status"]
                    == CandidateStatus.APPROVED.value):
                c["validation_status"] = CandidateStatus.ROLLED_BACK.value
        return {"intent": intent, "active": sorted(active)}

    def active_prompt(self, intent: str) -> str | None:
        return self._load_active().get(intent)

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
