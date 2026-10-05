"""75号·增强型 Agent 感知认知核心(RA-P0, 2026-10-05)

《增强型 Agent 架构.docx》感知层+认知层落地——EmotionEngine(行为
信号→情绪四算子)与 DualAxisScorer(双螺旋粘性评分+四模式)按原文
公式原样; 创新点为数据源反转(《创新规划优化设计方案》§1.1):
75号 agent 任务轨迹回放转事件流, 零前端改造先行。

口径:
    - 行为信号 MVP(不碰摄像头/麦克风——原文隐私边界全盘采纳)
    - 决策三 Agent(UI/Content/Incentive)为纯函数产出 Decision
      记录随快照落盘, 不接线不自动执行(RA-P2 门控)
    - AGENT75_RESONANCE_KILL=on → 会话面 409(75号 kill 家族第四员)
    - 存储: AGENT_TASK_DIR/resonance_sessions.jsonl 追加式(观测面)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from services.agent75_orchestrator_service import AGENT_TASK_DIR

logger = logging.getLogger(__name__)

MAX_EVENTS_PER_BATCH = 200


def resonance_kill() -> bool:
    return os.environ.get(
        "AGENT75_RESONANCE_KILL", "").strip().lower() in ("on", "1", "true")


def comfort_enabled() -> bool:
    """RA-P2 安抚注入开关(AGENT75_COMFORT=off 独立停)"""
    return os.environ.get(
        "AGENT75_COMFORT", "on").strip().lower() not in ("off", "0", "false")


COMFORT_PREFIX = ("注意: 该用户上一会话体验不顺, 回答开头先用一句简短"
                  "暖心话致意, 再进入正题。\n\n")
FRUSTRATED_STREAK_TRIGGER = 3        # 连续挫败会话 → 进化提议
_EVOLVE_MIN_INTERVAL_S = 600         # 提议节流(防刷)
_last_evolve_at: float = 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- 数据结构(原文 pydantic 原样) ----------

class EmotionState(str, Enum):
    NEUTRAL = "neutral"
    ANXIOUS = "anxious"
    CONFUSED = "confused"
    PLEASANT = "pleasant"
    FRUSTRATED = "frustrated"


class UserIntentType(str, Enum):
    TASK = "task"
    BROWSE = "browse"
    LEARN = "learn"
    SOCIAL = "social"
    ENTERTAIN = "entertain"


class PerceptionRecord(BaseModel):
    record_id: str = ""
    user_id: Optional[str] = None
    session_id: str
    page_id: str = ""
    event_type: str        # click/scroll/hover/error/back/like/share/
                           # complete/long_stay/search/view_detail/...
    timestamp: str = ""
    payload: dict = {}


class EmotionSnapshot(BaseModel):
    session_id: str
    emotion: EmotionState = EmotionState.NEUTRAL
    confidence: float = 0.0
    anxiety_score: float = 0.0
    confusion_score: float = 0.0
    pleasure_score: float = 0.0
    frustration_score: float = 0.0
    created_at: str = ""


# ---------- 情感引擎(原文四算子公式原样) ----------

class EmotionEngine:

    def compute(self, events: list[PerceptionRecord]) -> EmotionSnapshot:
        anxiety = self._compute_anxiety(events)
        confusion = self._compute_confusion(events)
        pleasure = self._compute_pleasure(events)
        frustration = self._compute_frustration(events)
        scores = {
            EmotionState.ANXIOUS: anxiety,
            EmotionState.CONFUSED: confusion,
            EmotionState.PLEASANT: pleasure,
            EmotionState.FRUSTRATED: frustration,
        }
        emotion = max(scores, key=scores.get)
        confidence = scores[emotion]
        return EmotionSnapshot(
            session_id=events[0].session_id if events else "",
            emotion=emotion, confidence=round(confidence, 4),
            anxiety_score=round(anxiety, 4),
            confusion_score=round(confusion, 4),
            pleasure_score=round(pleasure, 4),
            frustration_score=round(frustration, 4),
            created_at=_now())

    def _compute_anxiety(self, events) -> float:
        rapid = sum(1 for e in events if e.event_type == "click"
                    and e.payload.get("interval_ms", 999) < 300)
        return min(1.0, rapid / max(1, len(events)) * 2)

    def _compute_confusion(self, events) -> float:
        back = sum(1 for e in events if e.event_type == "back")
        error = sum(1 for e in events if e.event_type == "error")
        return min(1.0, (back + error) / max(1, len(events)) * 1.5)

    def _compute_pleasure(self, events) -> float:
        positive = sum(1 for e in events if e.event_type in
                       ("like", "share", "complete", "long_stay"))
        return min(1.0, positive / max(1, len(events)) * 1.2)

    def _compute_frustration(self, events) -> float:
        errors = sum(1 for e in events if e.event_type == "error")
        retries = sum(1 for e in events if e.payload.get("retry", False))
        return min(1.0, (errors + retries) / max(1, len(events)) * 1.5)


# ---------- 双螺旋评分(原文公式原样) ----------

class DualAxisScorer:

    def __init__(self, default_alpha: float = 0.6,
                 default_beta: float = 0.4):
        self.alpha = default_alpha
        self.beta = default_beta

    def compute(self, material_score: float, emotional_score: float) -> float:
        m = max(0.0, min(1.0, material_score))
        e = max(0.0, min(1.0, emotional_score))
        return round(self.alpha * m + self.beta * e, 4)

    def decide_intent(self, material_score: float,
                      emotional_score: float) -> dict:
        s = self.compute(material_score, emotional_score)
        if material_score > 0.7 and emotional_score < 0.4:
            mode = "efficiency"
        elif emotional_score > 0.7 and material_score < 0.5:
            mode = "explore"
        elif material_score > 0.6 and emotional_score > 0.6:
            mode = "flow"
        else:
            mode = "balanced"
        return {"stickiness_score": s, "mode": mode,
                "alpha": self.alpha, "beta": self.beta}


# ---------- 决策三 Agent(原文逻辑原样; 纯函数仅随快照落盘) ----------

def decide_ui(intent: dict, emotion: EmotionSnapshot) -> dict:
    if emotion.emotion == EmotionState.FRUSTRATED:
        layout, theme, comps = "comfort", "warm", [
            {"type": "安抚文案", "priority": 1},
            {"type": "人工客服入口", "priority": 2},
            {"type": "简化任务入口", "priority": 3}]
    elif intent["material_score"] > 0.7:
        layout, theme, comps = "minimal", "neutral", [
            {"type": "搜索框", "priority": 1},
            {"type": "最近任务", "priority": 2}]
    else:
        layout, theme, comps = "explore", "neutral", [
            {"type": "推荐瀑布流", "priority": 1},
            {"type": "社区动态", "priority": 2}]
    return {"layout_mode": layout, "theme": theme,
            "components": comps,
            "rationale": f"emotion={emotion.emotion.value},"
                         f"material={intent['material_score']}"}


def decide_content(intent: dict) -> dict:
    strategy = "balanced"
    if intent["material_score"] > 0.7:
        strategy = "efficiency"
    elif intent["emotional_score"] > 0.7:
        strategy = "comfort"
    elif intent.get("material_intent") == UserIntentType.SOCIAL:
        strategy = "social"
    return {"strategy": strategy}


def decide_incentive(emotion: EmotionSnapshot, intent: dict) -> dict | None:
    if emotion.emotion == EmotionState.FRUSTRATED:
        return {"type": "support",
                "payload": {"message": "看起来有点不顺，要不要我帮你继续？"},
                "trigger_reason": "frustration_recovery"}
    if intent["stickiness_score"] > 0.85:
        return {"type": "easter_egg",
                "payload": {"message": "今天状态不错，送你一个专属推荐。"},
                "trigger_reason": "high_engagement"}
    return None


# ---------- 任务轨迹回放(创新点 §1.1: 75号→事件流) ----------

def transpose_agent_task(task) -> list[PerceptionRecord]:
    """75号 TaskRecord → 事件流:
    plan fallback→back(困惑) / exec failed→error / retries→retry /
    任务 FAILED→error / 进化提议→retry / COMPLETED→complete(愉悦)"""
    sid = getattr(task, "task_id", "") or ""
    out: list[PerceptionRecord] = []

    def ev(etype: str, payload: dict | None = None) -> None:
        out.append(PerceptionRecord(
            record_id=f"r_{len(out)}_{int(time.time() * 1000):x}",
            session_id=sid, page_id="agent75", event_type=etype,
            timestamp=_now(), payload=payload or {}))

    for t in (getattr(task, "execution_trace", None) or []):
        if t.get("step_id") == "plan_intent" and t.get("status") == "fallback":
            ev("back", {"reason": "intent_fallback"})
        if t.get("step_id") == "exec_answer":
            if t.get("status") == "failed":
                ev("error", {"step": "exec"})
            if t.get("retries"):
                ev("click", {"retry": True})
        if t.get("step_id") == "evolution_propose":
            ev("click", {"retry": True, "reason": "evolution_proposed"})
    if getattr(task, "status", "") and task.status.value == "completed":
        ev("complete", {})
    elif getattr(task, "status", "") and task.status.value == "failed":
        ev("error", {"step": "task"})
    return out


def estimate_material_score(events: list[PerceptionRecord]) -> float:
    """原文 _estimate_material_score 口径(任务型事件占比)"""
    task_events = sum(1 for e in events if e.event_type in
                      ("search", "view_detail", "add_to_cart", "complete"))
    return min(1.0, task_events / max(1, len(events)) * 1.5)


# ---------- 会话编排(原文 run_session 的 RA-P0 裁剪版) ----------

class ResonanceService:

    def __init__(self) -> None:
        self.emotion_engine = EmotionEngine()
        self.scorer = DualAxisScorer()

    @staticmethod
    def _store() -> Any:
        return AGENT_TASK_DIR / "resonance_sessions.jsonl"

    def run_session(self, session_id: str,
                    events: list[PerceptionRecord],
                    user_id: str | None = None) -> dict[str, Any]:
        emotion = self.emotion_engine.compute(events)
        material = estimate_material_score(events)
        emotional = max(0.0, min(
            1.0, emotion.pleasure_score - emotion.frustration_score * 0.5))
        result = self.scorer.decide_intent(material, emotional)
        intent = {"user_id": user_id, "session_id": session_id,
                  "material_intent": UserIntentType.TASK,
                  "emotional_intent": emotion.emotion,
                  "material_score": round(material, 4),
                  "emotional_score": round(emotional, 4),
                  "stickiness_score": result["stickiness_score"],
                  "mode": result["mode"]}
        snapshot = {
            "session_id": session_id, "user_id": user_id,
            "events": len(events),
            "emotion": emotion.model_dump(),
            "intent": intent,
            "decisions": {
                "ui": decide_ui(intent, emotion),
                "content": decide_content(intent),
                "incentive": decide_incentive(emotion, intent)},
            "created_at": _now()}
        self.persist(snapshot)
        return snapshot

    def persist(self, snapshot: dict) -> None:
        try:
            AGENT_TASK_DIR.mkdir(parents=True, exist_ok=True)
            with open(self._store(), "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
        except Exception as exc:   # noqa: BLE001 存储失败不拖累计算
            logger.warning("agent75_resonance_persist_failed: %s", exc)

    def list_sessions(self, limit: int = 20) -> list[dict]:
        p = self._store()
        if not p.is_file():
            return []
        rows = []
        for line in reversed(p.read_text(
                encoding="utf-8").strip().splitlines()):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(rows) >= limit:
                break
        return rows

    # ---------- RA-P2: 决策消费面(仅产出记录, 人工/后续消费) ----------

    def recent_decisions(self, limit: int = 30) -> list[dict]:
        """决策清单: 快照 → 扁平决策行(安抚/support 优先可见)"""
        out = []
        for s in self.list_sessions(max(limit * 2, 60)):
            emo = (s.get("emotion") or {}).get("emotion")
            dec = s.get("decisions") or {}
            inc = dec.get("incentive")
            out.append({
                "session_id": s.get("session_id"),
                "user_id": s.get("user_id"),
                "emotion": emo,
                "mode": (s.get("intent") or {}).get("mode"),
                "strategy": (dec.get("content") or {}).get("strategy"),
                "ui_layout": (dec.get("ui") or {}).get("layout_mode"),
                "incentive": (inc or {}).get("type"),
                "incentive_message": (inc or {}).get("payload", {}).get(
                    "message"),
                "created_at": s.get("created_at")})
            if len(out) >= limit:
                break
        # 安抚类置顶(运营优先看)
        return sorted(out, key=lambda r: r.get("incentive") != "support")

    def should_comfort(self, user_id: str | None) -> bool:
        """该用户最近一次会话是否挫败(通道A快照带 user_id)"""
        if not user_id or not comfort_enabled():
            return False
        for s in self.list_sessions(50):
            if s.get("user_id") == user_id:
                return ((s.get("emotion") or {}).get("emotion")
                        == "frustrated")
        return False

    # ---------- RA-P2: 挫败连击 → 会话级进化提议 ----------

    def _frustrated_streak(self) -> int:
        n = 0
        for s in self.list_sessions(FRUSTRATED_STREAK_TRIGGER + 5):
            if ((s.get("emotion") or {}).get("emotion")
                    == "frustrated"):
                n += 1
            else:
                break
        return n

    def propose_evolution(self, gateway=None) -> dict | None:
        """连续挫败 ≥3 → 既有 EvolutionEngine 提议链(prompt 安抚话术);
        节流 600s; 复用 propose→人工 approve 闸门, 不自动生效"""
        global _last_evolve_at
        if self._frustrated_streak() < FRUSTRATED_STREAK_TRIGGER:
            return None
        now = time.time()
        if now - _last_evolve_at < _EVOLVE_MIN_INTERVAL_S:
            return None
        _last_evolve_at = now
        try:
            from services.agent75_evolution_service import (
                AgentEvolutionService)
            from services.agent75_orchestrator_service import (
                AgentModelGateway, TaskRecord, TaskStatus)
            shim = TaskRecord(
                task_id=f"resonance_streak_{int(now):x}",
                user_id=None, query="(会话级连续挫败——安抚话术改进)",
                intent="chat", status=TaskStatus.FAILED,
                evaluation={"needs_evolution": True,
                            "issues": ["frustrated_streak"],
                            "score": 0.0})
            cand = AgentEvolutionService(
                gateway or AgentModelGateway()).propose(shim)
            if cand:
                logger.info("agent75_resonance_evolution_proposed "
                            "id=%s", cand.evolution_id)
                return cand.model_dump()
        except Exception as exc:   # noqa: BLE001
            logger.warning("agent75_resonance_evolve_skip: %s", exc)
        return None

    def replay_task(self, task_id: str, gateway=None) -> dict[str, Any] | None:
        """通道 A: 75号任务轨迹回放(创新点数据源反转);
        通道A跑在 daemon 线程——连击进化提议在此触发(不占请求时延)"""
        from services.agent75_orchestrator_service import (
            WebsiteAgentOrchestrator)
        task = WebsiteAgentOrchestrator().load(task_id)
        if task is None:
            return None
        events = transpose_agent_task(task)
        if not events:
            events = [PerceptionRecord(
                session_id=task_id, page_id="agent75",
                event_type="click", timestamp=_now())]
        snap = self.run_session(f"task_{task_id}", events,
                                user_id=task.user_id)
        self.propose_evolution(gateway=gateway)   # RA-P2 连击进化
        return snap
