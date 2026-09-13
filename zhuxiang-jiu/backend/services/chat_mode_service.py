"""AI智能客服聊天模块 · P3 三态灰度 + 护栏服务

对齐全站范式(68号 xinzhi_mode_service 最小化):

三态(CHAT_LLM_MODE):
    - off:    LLM 轨关闭(rule 轨照常, 零影响——现状默认)
    - shadow: LLM 轨留痕不呈现(消息 shadowLlm 字段, admin 可审计)
    - assist: LLM 轨呈现(失败自动回退 rule 轨)

读取链: 护栏暂停 > 运行时 override > 环境变量 > 默认 off。
旧 KNOWLEDGE_CHAT_LLM=on 映射为 assist(向后兼容)。

护栏(封闭二指标):
    - 投诉率 / 未解决率 相对基线恶化 >3% 自动降档 off
    - 护栏是保护机制(非处罚), 可自动; 恢复须人工 resume 留痕。

状态存储: 内存/Redis 双轨(键 chat:mode:state)。
"""

import json
import logging
import os
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

logger = logging.getLogger("chat_mode_service")

MODEL_VERSION = "v1-chat-mode"

# 三态(全站范式)
MODE_VALUES = ("off", "shadow", "assist")
DEFAULT_MODE = "off"

# 护栏指标域(封闭二指标——聊天场景口径)
GUARD_METRICS = ("complaintRate", "unresolvedRate")
GUARD_METRIC_LABELS = {
    "complaintRate": "投诉率",
    "unresolvedRate": "未解决率",
}

# 恶化阈值(任一指标相对基线 >3% 自动降档)
GUARD_DETERIORATION = 0.03

# 指标留痕上限(防无限膨胀)
GUARD_METRICS_MAX = 50

_STATE_KEY = _k("chat", "mode", "state")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _valid_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODE_VALUES else DEFAULT_MODE


def _env_mode() -> str:
    """环境变量读取(含旧 KNOWLEDGE_CHAT_LLM 二态兼容映射)"""
    mode = os.environ.get("CHAT_LLM_MODE")
    if mode:
        return _valid_mode(mode)
    # 旧开关兼容: KNOWLEDGE_CHAT_LLM=on → assist
    legacy = os.environ.get("KNOWLEDGE_CHAT_LLM", "off").strip().lower()
    return "assist" if legacy == "on" else DEFAULT_MODE


class ChatModeService:
    """聊天模块 P3·三态灰度 + 护栏(内存/Redis 双轨)"""

    # ============================================================
    # 运行时态(双轨存储)
    # ============================================================

    async def _load_state(self) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_STATE_KEY)
            return json.loads(data) if data else None
        return get_in_memory_store().get(_STATE_KEY)

    async def _save_state(self, st: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_STATE_KEY,
                             json.dumps(st, ensure_ascii=False))
        else:
            get_in_memory_store()[_STATE_KEY] = st

    async def _state(self) -> dict:
        """运行时态(缺省创建)"""
        st = await self._load_state()
        if st is None:
            st = {
                "override": "",
                "paused": False,
                "pausedReason": "",
                "pausedAt": "",
                "metrics": [],
                "breachTrail": [],
                "updatedAt": _now_iso(),
            }
            await self._save_state(st)
        return st

    # ============================================================
    # 模式读取
    # ============================================================

    async def current_mode(self) -> dict:
        """当前灰度态(读取链: 暂停>override>env>off)"""
        st = await self._state()
        env_mode = _env_mode()
        if st.get("paused"):
            return {
                "mode": "off",
                "source": "guard_pause",
                "paused": True,
                "override": st.get("override", ""),
                "envMode": env_mode,
                "pausedReason": st.get("pausedReason", ""),
            }
        if st.get("override"):
            return {
                "mode": _valid_mode(st["override"]),
                "source": "runtime_override",
                "paused": False,
                "override": st["override"],
                "envMode": env_mode,
                "pausedReason": "",
            }
        return {
            "mode": env_mode,
            "source": "env",
            "paused": False,
            "override": "",
            "envMode": env_mode,
            "pausedReason": "",
        }

    async def require_llm_track(self) -> dict:
        """LLM 轨门槛(off/shadow 均返回当前态, 由调用方决定留痕或呈现)"""
        return await self.current_mode()

    # ============================================================
    # 运行时切档 / 护栏 / 恢复
    # ============================================================

    async def set_override(self, mode: str,
                           operator: str = "admin") -> dict:
        """运行时 override(人工切档留痕——空串清除)"""
        m = str(mode or "").strip().lower()
        if m and m not in MODE_VALUES:
            raise ValueError(
                f"非法灰度态({mode}, 合法值: {'/'.join(MODE_VALUES)})")
        st = await self._state()
        st["override"] = m
        st["updatedAt"] = _now_iso()
        await self._save_state(st)
        logger.info("chat_mode_override=%s by=%s", m or "清除", operator)
        return await self.current_mode()

    async def guard_check(self, complaint_rate: float,
                          unresolved_rate: float,
                          baseline_complaint_rate: float = 0.0,
                          baseline_unresolved_rate: float = 0.0) -> dict:
        """护栏检查(确定性阈值比较; 恶化>3% 自动降档 off)

        恶化口径: (cur - base) / base > 3%
        (基线=0 时按绝对值>0 判恶化——防除零)。
        """
        base = {
            "complaintRate": max(0.0, float(baseline_complaint_rate or 0)),
            "unresolvedRate": max(0.0, float(baseline_unresolved_rate or 0)),
        }
        current = {
            "complaintRate": max(0.0, float(complaint_rate or 0)),
            "unresolvedRate": max(0.0, float(unresolved_rate or 0)),
        }
        breaches = []
        for key in GUARD_METRICS:
            cur, bs = current[key], base[key]
            delta = ((cur - bs) / bs if bs > 0
                     else (1.0 if cur > 0 else 0.0))
            if delta > GUARD_DETERIORATION:
                breaches.append({
                    "metric": key,
                    "label": GUARD_METRIC_LABELS[key],
                    "baseline": bs, "current": cur,
                    "deterioration": round(delta, 4)})
        # 指标留痕(滚动截断)
        st = await self._state()
        entry = {
            "checkedAt": _now_iso(),
            "metrics": current,
            "baseline": base,
            "breaches": len(breaches),
        }
        metrics = list(st.get("metrics") or [])
        metrics.append(entry)
        st["metrics"] = metrics[-GUARD_METRICS_MAX:]
        result = {"breached": bool(breaches),
                  "breaches": breaches,
                  "threshold": GUARD_DETERIORATION,
                  "metrics": current,
                  "baseline": base}
        if breaches:
            # 自动降档(保护机制——非处罚, 可自动)
            reasons = "; ".join(
                f"{b['label']}{b['baseline']}→{b['current']}"
                f"(+{b['deterioration']:.1%})"
                for b in breaches)
            st["paused"] = True
            st["pausedReason"] = f"聊天护栏自动降档: {reasons}"
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({
                "at": st["pausedAt"],
                "reason": st["pausedReason"],
                "breaches": breaches})
            st["breachTrail"] = trail[-GUARD_METRICS_MAX:]
            logger.warning("chat_mode_guard_paused: %s", st["pausedReason"])
        st["updatedAt"] = _now_iso()
        await self._save_state(st)
        result["pausedNow"] = bool(breaches)
        return result

    async def resume(self, operator: str = "admin",
                     note: str = "") -> dict:
        """人工恢复(护栏暂停解除——决策留痕)"""
        st = await self._state()
        if not st.get("paused"):
            raise ValueError("护栏未处于暂停态, 无需恢复")
        st["paused"] = False
        st["pausedReason"] = ""
        st["resumedBy"] = operator
        st["resumedNote"] = (note or "")[:200]
        st["resumedAt"] = _now_iso()
        st["updatedAt"] = _now_iso()
        await self._save_state(st)
        logger.info("chat_mode_resumed by=%s note=%s", operator, note[:50])
        return await self.current_mode()
