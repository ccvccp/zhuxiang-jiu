"""智能知识库训练模型 · 双师对抗-协同三态灰度服务

对齐全站范式(chat_mode_service 同构):

三态(DUAL_MODE):
    - off:    双师轨关闭(单轨 rule/llm 照常, 零影响——默认)
    - shadow: 双师轨留痕不呈现(对抗-协同结果落库审计)
    - assist: 双师轨呈现(胜出候选为答案)

读取链: 护栏暂停 > 运行时 override > 环境变量 > 默认 off。

护栏(对抗平衡监控):
    - 生成器否决率健康带 50%±10%, 超带自动降档 off
    - 护栏是保护机制(非处罚), 可自动; 恢复须人工 resume 留痕

状态存储: 内存/Redis 双轨(键 knowledge:dual:state)。
"""

import json
import logging
import os
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

logger = logging.getLogger("knowledge_dual_mode_service")

MODEL_VERSION = "v1-knowledge-dual-mode"

# 三态(全站范式)
MODE_VALUES = ("off", "shadow", "assist")
DEFAULT_MODE = "off"

# 对抗平衡监控: 生成器否决率健康带(50%±10%)
DUAL_REJECTION_HEALTHY_BAND = 0.10
DUAL_REJECTION_CENTER = 0.50

# 护栏恶化阈值(否决率偏离健康带即降档)
GUARD_DETERIORATION = 0.03

# 统计留痕上限(防无限膨胀)
STATS_MAX = 200

_STATE_KEY = _k("knowledge", "dual", "state")
_STATS_KEY = _k("knowledge", "dual", "stats")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _valid_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODE_VALUES else DEFAULT_MODE


def _env_mode() -> str:
    return _valid_mode(os.environ.get("DUAL_MODE"))


class KnowledgeDualModeService:
    """知识库双师对抗-协同·三态灰度 + 对抗平衡护栏(双轨存储)"""

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
                "resumedBy": "",
                "resumedAt": "",
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
        logger.info("knowledge_dual_mode_override=%s by=%s",
                    m or "清除", operator)
        return await self.current_mode()

    async def guard_check(self, rejection_rate: float) -> dict:
        """对抗平衡护栏(生成器否决率健康带 50%±10%)

        超出健康带判定为对抗失衡(一方过强/过弱)自动降档 off
        ——保护机制非处罚, 可自动; 恢复须 resume 人工留痕。
        """
        rate = max(0.0, float(rejection_rate or 0))
        low = DUAL_REJECTION_CENTER - DUAL_REJECTION_HEALTHY_BAND
        high = DUAL_REJECTION_CENTER + DUAL_REJECTION_HEALTHY_BAND
        breached = rate < low or rate > high
        st = await self._state()
        # 指标留痕(滚动截断)
        entry = {
            "checkedAt": _now_iso(),
            "rejectionRate": rate,
            "healthyBand": [low, high],
            "breached": breached,
        }
        metrics = list(st.get("metrics") or [])
        metrics.append(entry)
        st["metrics"] = metrics[-STATS_MAX:]
        result = {
            "breached": breached,
            "rejectionRate": rate,
            "healthyBand": [round(low, 4), round(high, 4)],
            "threshold": GUARD_DETERIORATION,
        }
        if breached:
            reason = (f"对抗失衡: 生成器否决率 {rate:.1%} "
                      f"超出健康带 [{low:.0%}, {high:.0%}]")
            st["paused"] = True
            st["pausedReason"] = f"双师护栏自动降档: {reason}"
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({"at": st["pausedAt"], "reason": reason})
            st["breachTrail"] = trail[-STATS_MAX:]
            logger.warning("knowledge_dual_guard_paused: %s", reason)
        st["updatedAt"] = _now_iso()
        await self._save_state(st)
        result["pausedNow"] = breached
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
        logger.info("knowledge_dual_mode_resumed by=%s note=%s",
                    operator, note[:50])
        return await self.current_mode()
