"""竹鉴·BambooVerify(77号)——四档灰度与护栏服务

依据: 全站大模型转段标准(75/76 号四档范式同源)。

四档灰度(ZJIAN_MODE, zjian 前缀):
    off(默认)    决策面关闭(409)——仅锁"新增质检问答入口"
    shadow       观察学习期(决策放行+响应留痕 zjianMode)
    assist       辅助生产期(决策生效)
    full         自主生产期(决策生效+低风险域自主——
                 护栏自动巡检 auto_patrol)
    读取链: 护栏暂停 > 运行时 override > 环境变量 > off

full 档 L1 自主域(封闭白名单):
    auto_patrol   护栏自动巡检(决策面每 10 次调用节流触发)
永不自主铁律(高风险域——full 档亦然):
    报告典藏锚定(检测数据人工录入——原文事实源)、
    护栏恢复 resume。

护栏(竹鉴语义三指标, 恶化>3% 自动暂停; 全确定性——
漏网口径铁律: 拦截=守门成功, 只计漏网):
    判定失真率 benchmarkFailRate
        = 应答指标值/判定与典藏不符数 / 放行应答数
    引证错失率 citationMissRate(漏网防线——结构恒零)
        = 放行技术应答缺报告引证占比
    断章率 outOfContextRate(漏网防线)
        = 放行引证偏离问题指标域占比
    (cur-base)/base > 3% → 自动暂停(等效 off, 留痕);
    恢复须人工 resume(决策留痕)。
    小样本保护: 分母不足 10 跳过恶化判定。

状态存储: zhuxiang:zjian:mode_state 单键整档 JSON。
"""

import json
import logging
import os
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-zjian-mode"

MODE_VALUES = ("off", "shadow", "assist", "full")
DEFAULT_MODE = "off"

L1_AUTONOMY_DOMAINS = ("auto_patrol",)
AUTO_PATROL_EVERY = 10

_STATE_KEY_REDIS = "zhuxiang:zjian:mode_state"
_STATE_KEY_MEM = "zjian_mode_state"

GUARD_METRICS = ("benchmarkFailRate", "citationMissRate",
                 "outOfContextRate")
GUARD_METRIC_LABELS = {
    "benchmarkFailRate": "判定失真率",
    "citationMissRate": "引证错失率",
    "outOfContextRate": "断章率",
}

GUARD_DETERIORATION = 0.03
GUARD_METRICS_MAX = 50
_MIN_SAMPLE = 10


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _valid_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODE_VALUES else DEFAULT_MODE


class ZjianModeService:
    """竹鉴四档灰度 + 护栏"""

    def __init__(self):
        self._store = get_in_memory_store()

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
        st = await self._load_state()
        if st is None:
            st = {
                "stateId": 1,
                "override": "",
                "paused": False,
                "pausedReason": "",
                "pausedAt": "",
                "decisionSeq": 0,
                "metrics": [],
                "breachTrail": [],
                "updatedAt": _now_iso(),
            }
            await self._save_state(st)
        return st

    async def current_mode(self) -> dict:
        """当前灰度态(读取链: 暂停>override>env>off)"""
        st = await self._state()
        env_mode = _valid_mode(os.environ.get("ZJIAN_MODE"))
        if st.get("paused"):
            return {"mode": "off", "source": "guard_pause",
                    "paused": True,
                    "override": st.get("override", ""),
                    "envMode": env_mode,
                    "pausedReason": st.get("pausedReason", "")}
        if st.get("override"):
            return {"mode": _valid_mode(st["override"]),
                    "source": "runtime_override",
                    "paused": False,
                    "override": st["override"],
                    "envMode": env_mode,
                    "pausedReason": ""}
        return {"mode": env_mode, "source": "env",
                "paused": False, "override": "",
                "envMode": env_mode, "pausedReason": ""}

    async def require_decision_mode(self) -> dict:
        """决策面门槛(off 拒绝——全站范式)

        Raises:
            ValueError: mode=off(决策面关闭)
        """
        state = await self.current_mode()
        if state["mode"] == "off":
            raise ValueError(
                f"ZJIAN_MODE=off({state['source']}——"
                f"决策面关闭(质检问答/规格比对入口), "
                f"报告典藏观测面不受影响)")
        return state

    async def note_decision_and_maybe_patrol(
            self) -> dict | None:
        """full 档自主巡检(L1 自主域 auto_patrol)"""
        state = await self.current_mode()
        if state["mode"] != "full":
            return None
        st = await self._state()
        seq = int(st.get("decisionSeq") or 0) + 1
        st["decisionSeq"] = seq
        st["updatedAt"] = _now_iso()
        await self._save_state(st)
        if seq % AUTO_PATROL_EVERY != 0:
            return None
        result = await self.patrol()
        result["autoPatrol"] = True
        result["decisionSeq"] = seq
        logger.info(
            "zjian_auto_patrol seq=%s breaches=%s",
            seq, result.get("breaches"))
        return result

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
        logger.info("zjian_mode_override=%s by=%s",
                    m or "清除", operator)
        return await self.current_mode()

    async def guard_check(self, bench_rate: float,
                          cite_rate: float,
                          context_rate: float,
                          baseline: dict = None,
                          skip_metrics: tuple = ()) -> dict:
        """护栏检查(恶化>3% 自动暂停; 基线缺省内置)"""
        base = {
            "benchmarkFailRate": 0.10,
            "citationMissRate": 0.05,
            "outOfContextRate": 0.05,
        }
        base.update({
            k: max(0.0, float(v))
            for k, v in (baseline or {}).items()
            if k in GUARD_METRICS})
        current = {
            "benchmarkFailRate": max(
                0.0, float(bench_rate or 0)),
            "citationMissRate": max(
                0.0, float(cite_rate or 0)),
            "outOfContextRate": max(
                0.0, float(context_rate or 0)),
        }
        breaches = []
        for key in GUARD_METRICS:
            if key in skip_metrics:
                continue
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
                  "metrics": current, "baseline": base}
        if breaches:
            reasons = "; ".join(
                f"{b['label']}{b['baseline']}→{b['current']}"
                f"(+{b['deterioration']:.1%})"
                for b in breaches)
            st["paused"] = True
            st["pausedReason"] = (
                "竹鉴大模型护栏自动暂停: " + reasons)
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({"at": st["pausedAt"],
                          "reason": st["pausedReason"],
                          "breaches": breaches})
            st["breachTrail"] = trail[-GUARD_METRICS_MAX:]
            logger.warning("zjian_mode_guard_paused: %s",
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
        logger.info("zjian_mode_resumed by=%s", operator)
        return await self.current_mode()

    async def aggregate_guard_metrics(self) -> dict:
        """从仓储确定性聚合三指标(巡检数据源)"""
        from repositories.zjian_repository import (
            ZjianRepository,
        )
        repo = ZjianRepository()
        guard = await repo.get_stats("guard")
        total = guard.get("total", 0)
        # 判定失真: 放行应答中指标不符(bench_fail 计
        # 拦截——漏网结构恒零, 此处聚合漏网计数)
        return {
            "benchmarkFailRate": round(
                guard.get("bench_leak", 0) / total, 4)
            if total else 0.0,
            "citationMissRate": round(
                guard.get("cite_miss", 0) / total, 4)
            if total else 0.0,
            "outOfContextRate": round(
                guard.get("out_context", 0) / total, 4)
            if total else 0.0,
            "sample": {"totalAsks": total},
        }

    async def patrol(self) -> dict:
        """护栏自动巡检(聚合 → 检查; 小样本保护)"""
        metrics = await self.aggregate_guard_metrics()
        sample = metrics.pop("sample", {})
        skip = set()
        if sample.get("totalAsks", 0) < _MIN_SAMPLE:
            skip.update(GUARD_METRICS)
        result = await self.guard_check(
            bench_rate=metrics["benchmarkFailRate"],
            cite_rate=metrics["citationMissRate"],
            context_rate=metrics["outOfContextRate"],
            skip_metrics=tuple(skip))
        result["sample"] = sample
        result["skippedSmallSample"] = sorted(skip)
        return result

    async def status_view(self) -> dict:
        """灰度总览(四档+护栏+full 自主域+红线公示)"""
        state = await self.current_mode()
        st = await self._state()
        return {
            **state,
            "modeValues": list(MODE_VALUES),
            "fullAutonomy": {
                "domains": list(L1_AUTONOMY_DOMAINS),
                "autoPatrolEvery": AUTO_PATROL_EVERY,
                "decisionSeq": int(
                    st.get("decisionSeq") or 0),
                "note": ("full 档低风险自主域(封闭白名单); "
                         "assist 期巡检须人工 POST "
                         "/api/zjian/mode/guard"),
            },
            "neverAutonomous": (
                "report 典藏锚定(检测数据人工录入——"
                "原文事实源)/护栏 resume(人工留痕)"
                "——full 档亦永不自主"),
            "observablesNeverOff": (
                "reports/report detail/metrics/ask/"
                "mode——典藏观测面永不关停"),
            "decisionSurfaces": (
                "verify chat 质检问答/compare 规格比对"
                "——off 拒绝(409)"),
            "guard": {
                "metrics": [
                    {"key": k,
                     "label": GUARD_METRIC_LABELS[k]}
                    for k in GUARD_METRICS],
                "threshold": GUARD_DETERIORATION,
                "pausedAt": st.get("pausedAt", ""),
                "pausedReason": st.get("pausedReason", ""),
                "checkCount": len(st.get("metrics") or []),
                "breachCount": len(
                    st.get("breachTrail") or []),
            },
            "modelVersion": MODEL_VERSION,
        }
