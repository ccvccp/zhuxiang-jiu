"""62号·AI智能无形资产估值——大模型三态灰度与护栏服务

依据: 68/45/智运 全站大模型转段范式。62号原为各服务内联
AV62_MODE env 直读(一代开关), 本次升级为集中模式层:

三态灰度(AV62_MODE):
    off(默认)    决策面关闭(409)
    shadow       观察学习期(决策放行+响应留痕标记 av62Mode)
    assist       辅助生产期(决策生效)

    读取链(优先级): 护栏暂停 > 运行时 override >
    环境变量 AV62_MODE > 默认 off。
    观测面(registry/assets/assessments/scenarios/
    thresholds/fairness-report/dashboard/learn-status)
    与纠错面(申诉提交/裁决/回流)永不关停。

护栏(62号语义三指标, 恶化>3% 自动暂停):
    申诉翻案率 appealOverturnRate     (裁决 overturn 占比——误判代理)
    压测失效率 stressFailRate         (反事实压测不通过占比)
    公平异常率 fairnessAnomalyRate    (公平审计超阈占比)
    (cur-base)/base > 3% → 自动暂停(等效 off, 留痕);
    恢复须人工 resume(决策留痕)。

同步桥接(本模块特有——一代开关平滑迁移):
    既有服务内联 require_active_mode() 为同步函数, 调用点深入
    服务层。为零改动迁移, 提供进程级快照 _G + 同步读取器:
    - legacy_current_mode(): 强制态 > 暂停 > override > env > off
    - legacy_require_active_mode(): off 拒绝(消息格式不变)
    - legacy_forced(mode): 上下文强制态——申诉重估专用
      (申诉不受开关影响铁律: 暂停/override 期间仍可强制重估)
    _G 由异步操作(override/guard/resume/首次决策门控)刷新并
    持久化 Redis; env 每次实时读取。

状态存储: zhuxiang:av62:mode_state 单键整档 JSON
(类型天然保留——45号 P0 教训规避)。
"""

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-av62-mode"

# 三态
MODE_VALUES = ("off", "shadow", "assist")
DEFAULT_MODE = "off"

# 状态存储键
_STATE_KEY_REDIS = "zhuxiang:av62:mode_state"
_STATE_KEY_MEM = "av62_mode_state"

# 护栏指标域(封闭三指标——62号无形资产估值语义)
GUARD_METRICS = ("appealOverturnRate", "stressFailRate",
                 "fairnessAnomalyRate")
GUARD_METRIC_LABELS = {
    "appealOverturnRate": "申诉翻案率",
    "stressFailRate": "压测失效率",
    "fairnessAnomalyRate": "公平异常率",
}

# 恶化阈值(任一指标相对基线 >3% 自动暂停)
GUARD_DETERIORATION = 0.03

# 指标留痕上限(防无限膨胀)
GUARD_METRICS_MAX = 50


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _valid_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODE_VALUES else DEFAULT_MODE


# ============================================================
# 进程级快照(同步桥接数据源; 异步操作刷新)
# ============================================================

_G = {
    "loaded": False,       # Redis 状态已载入
    "override": "",        # 运行时 override(空=无)
    "paused": False,       # 护栏暂停位
    "force": None,         # 强制态(申诉重估专用, 最高优先)
}


class Av62ModeService:
    """62号·大模型三态灰度 + 护栏 + 同步桥接"""

    def __init__(self):
        self._store = get_in_memory_store()

    # ============================================================
    # 状态存取(整档 JSON)
    # ============================================================

    async def _load_state(self) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_STATE_KEY_REDIS)
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return None
        return self._store.get(_STATE_KEY_MEM)

    async def _save_state(self, st: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _STATE_KEY_REDIS,
                json.dumps(st, ensure_ascii=False))
        else:
            self._store[_STATE_KEY_MEM] = st

    async def _state(self) -> dict:
        """运行时态(缺省创建; 同步刷新 _G)"""
        st = await self._load_state()
        if st is None:
            st = {
                "stateId": 1,
                "override": "",
                "paused": False,
                "pausedReason": "",
                "pausedAt": "",
                "metrics": [],
                "breachTrail": [],
                "updatedAt": _now_iso(),
            }
            await self._save_state(st)
        _refresh_global(st)
        return st

    # ============================================================
    # 异步读取链(路由门控/控制面)
    # ============================================================

    async def current_mode(self) -> dict:
        """当前灰度态(读取链: 暂停>override>env>off)"""
        st = await self._state()
        env_mode = _valid_mode(
            os.environ.get("AV62_MODE"))
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

    async def require_decision_mode(self) -> dict:
        """决策面门槛(off 拒绝——全站范式)"""
        state = await self.current_mode()
        if state["mode"] == "off":
            raise ValueError(
                f"AV62_MODE=off({state['source']}——"
                f"决策面关闭, 观测面不受影响)")
        return state

    async def set_override(self, mode: str,
                           operator: str = "admin") -> dict:
        """运行时 override(人工切档留痕——空串清除)"""
        m = str(mode or "").strip().lower()
        if m and m not in MODE_VALUES:
            raise ValueError(
                f"非法灰度态({mode}, 合法值: "
                f"{'/'.join(MODE_VALUES)})")
        st = await self._state()
        st["override"] = m
        st["updatedAt"] = _now_iso()
        await self._save_state(st)
        logger.info("av62_mode_override=%s by=%s", m or "清除",
                    operator)
        return await self.current_mode()

    # ============================================================
    # 护栏(三指标恶化 >3% 自动暂停)
    # ============================================================

    async def guard_check(
            self, appeal_overturn_rate: float,
            stress_fail_rate: float,
            fairness_anomaly_rate: float,
            baseline: dict = None) -> dict:
        """护栏检查(确定性阈值比较; 恶化>3% 自动暂停)"""
        base = {
            "appealOverturnRate": 0.05,
            "stressFailRate": 0.10,
            "fairnessAnomalyRate": 0.05,
        }
        base.update({k: max(0.0, float(v))
                     for k, v in (baseline or {}).items()
                     if k in GUARD_METRICS})
        current = {
            "appealOverturnRate": max(
                0.0, float(appeal_overturn_rate or 0)),
            "stressFailRate": max(
                0.0, float(stress_fail_rate or 0)),
            "fairnessAnomalyRate": max(
                0.0, float(fairness_anomaly_rate or 0)),
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
            reasons = "; ".join(
                f"{b['label']}{b['baseline']}→"
                f"{b['current']}"
                f"(+{b['deterioration']:.1%})"
                for b in breaches)
            st["paused"] = True
            st["pausedReason"] = (
                f"62号大模型护栏自动暂停: {reasons}")
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({
                "at": st["pausedAt"],
                "reason": st["pausedReason"],
                "breaches": breaches})
            st["breachTrail"] = trail[-GUARD_METRICS_MAX:]
            logger.warning("av62_mode_guard_paused: %s",
                           st["pausedReason"])
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
        logger.info("av62_mode_resumed by=%s note=%s",
                    operator, note[:50])
        return await self.current_mode()

    # ============================================================
    # 观测面
    # ============================================================

    async def status_view(self) -> dict:
        """灰度总览(模式+护栏状态+指标留痕+红线公示)"""
        state = await self.current_mode()
        st = await self._state()
        return {
            **state,
            "modeValues": list(MODE_VALUES),
            "observablesNeverOff": (
                "registry/assets列表详情/model-status/assessments"
                "/scenarios/thresholds/fairness-report/learn-status"
                "/dashboard——观测面永不关停(宪法口径)"),
            "decisionSurfaces": (
                "assets登记/assess估值/scenarios-convert/stress"
                "/activate/threshold-calibrate/redteam"
                "——off 拒绝(409)"),
            "exemptSurfaces": (
                "appeals提交/裁决/review + feedback/collect"
                "——申诉与回流不受开关影响(人工铁律)"),
            "guard": {
                "metrics": [
                    {"key": k,
                     "label": GUARD_METRIC_LABELS[k]}
                    for k in GUARD_METRICS],
                "threshold": GUARD_DETERIORATION,
                "pausedAt": st.get("pausedAt", ""),
                "pausedReason": st.get("pausedReason", ""),
                "checkCount": len(st.get("metrics") or []),
                "breachCount": len(st.get("breachTrail")
                                   or []),
            },
            "modelVersion": MODEL_VERSION,
        }


# ============================================================
# 同步桥接(既有服务内联开关零改动迁移)
# ============================================================

def _refresh_global(st: dict) -> None:
    """异步状态 → 进程级快照(供同步读取)"""
    _G["loaded"] = True
    _G["override"] = str(st.get("override") or "")
    _G["paused"] = bool(st.get("paused"))


def legacy_current_mode() -> str:
    """同步模式读取(强制态 > 暂停 > override > env > off)

    供既有服务内联 current_mode() 委托; env 实时读,
    override/paused 来自进程快照(异步操作刷新)。
    """
    if _G["force"]:
        return _valid_mode(_G["force"])
    if _G["paused"]:
        return "off"
    if _G["override"]:
        return _valid_mode(_G["override"])
    return _valid_mode(
        os.environ.get("AV62_MODE"))


def legacy_require_active_mode() -> None:
    """同步决策门槛(off 拒绝——消息格式与一代一致)"""
    mode = legacy_current_mode()
    if mode == "off":
        raise ValueError(
            f"AV62_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


@contextmanager
def legacy_forced(mode: str):
    """强制态上下文(申诉重估专用——申诉不受开关影响铁律)

    暂停/override 期间仍可强制通过决策门槛(仅限同步桥接口径;
    语义: 申诉重估是纠错通道, 非决策面)。
    """
    prev = _G["force"]
    _G["force"] = _valid_mode(mode)
    try:
        yield
    finally:
        _G["force"] = prev
