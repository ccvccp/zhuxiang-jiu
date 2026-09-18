"""竹韵·智衡·竹奕酒智能大模型(75号)——四档灰度与护栏服务

依据: SDD V3.0 §6.2 成本熔断 + 全站大模型转段标准
(73/74 四档范式同源——member73/nexus74 full 档先例)。

四档灰度(ZYH_MODE, zyh 前缀):
    off(默认)    决策面关闭(409)——仅锁"新增生成入口"
    shadow       观察学习期(决策放行+响应留痕 zyhMode)
    assist       辅助生产期(决策生效)
    full         自主生产期(决策生效+低风险域自主——
                 护栏自动巡检 auto_patrol, 无需人工定期触发)
    读取链: 护栏暂停 > 运行时 override > 环境变量 ZYH_MODE > off

full 档 L1 自主域(封闭白名单——73号 L1_AUTONOMY_DOMAINS 范式):
    auto_patrol   护栏自动巡检(决策面每 10 次调用节流触发
                  一次 patrol——确定性聚合+阈值比较, LLM 禁入;
                  assist 档须人工显式 POST /mode/guard)
永不自主铁律(高风险域——full 档亦然):
    知识条目 upsert(单一事实源——专利/企标人工锚定)、
    守门规则变更(L1/L2/L3)、护栏恢复 resume(人工决策留痕)、
    缓存清空 cache/clear(管理面 X-Role)。

宪法豁免面(永不关停——对齐叫帮/钱包"off 只锁新增"范式):
    知识条目/工艺图谱/守门规则/统计/缓存指标/灰度总览——
    知识内核是公开事实锚点(SDD: 专利/企标为终极事实源),
    观测面不受 mode 影响; 守门三层随知识查询始终可用。

护栏(竹韵·智衡语义三指标, 恶化>3% 自动暂停; 全确定性):
    工艺误述率 craftMisstateRate
        = L1 旧工艺表述拦截数 / 总问答请求
          (SDD §6.1 L2 守门核心目标: 工艺混淆拦截)
    等同混淆率 equivalenceRate
        = L2 他企等同化拦截数 / 总问答请求
          (竹筒酒≠竹奕酒认知护城河核心指标)
    溯源缺失率 citationMissRate
        = L3 溯源拦截数 / 产出技术断言的应答数
          (SDD §6.1 L3: 工艺断言必含 ZZ26SW1489303A,
           香型断言必含 ZZ26SW1489404B)
    (cur-base)/base > 3% → 自动暂停(等效 off, 留痕);
    恢复须人工 resume(决策留痕)。
    小样本保护: 分母不足 10 跳过恶化判定(指标照常留痕)。

红线:
    - LLM 禁入: 护栏=阈值比较, 全确定性
    - 决策面 = chat(问答生成)/stress-test(推演生成)/
      probe/debate(辩题生成)——新增生成入口;
      cache/clear 为管理面(X-Role)不受 mode 影响

状态存储: zhuxiang:zyh:mode_state 单键整档 JSON。
"""

import json
import logging
import os
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-zyh-mode"

MODE_VALUES = ("off", "shadow", "assist", "full")
DEFAULT_MODE = "off"

# full 档 L1 自主域(封闭白名单——永不扩容铁律:
# 知识条目/守门规则/resume/cache clear 永不入白名单)
L1_AUTONOMY_DOMAINS = ("auto_patrol",)
# full 档自主巡检节流: 决策面每 N 次调用巡检一次
AUTO_PATROL_EVERY = 10

_STATE_KEY_REDIS = "zhuxiang:zyh:mode_state"
_STATE_KEY_MEM = "zyh_mode_state"

GUARD_METRICS = ("craftMisstateRate", "equivalenceRate",
                 "citationMissRate")
GUARD_METRIC_LABELS = {
    "craftMisstateRate": "工艺误述率",
    "equivalenceRate": "等同混淆率",
    "citationMissRate": "溯源缺失率",
}

GUARD_DETERIORATION = 0.03
GUARD_METRICS_MAX = 50
_MIN_SAMPLE = 10


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _valid_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODE_VALUES else DEFAULT_MODE


class ZyhModeService:
    """竹韵·智衡四档灰度 + 护栏"""

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
        env_mode = _valid_mode(os.environ.get("ZYH_MODE"))
        if st.get("paused"):
            return {"mode": "off", "source": "guard_pause",
                    "paused": True,
                    "override": st.get("override", ""),
                    "envMode": env_mode,
                    "pausedReason": st.get("pausedReason", "")}
        if st.get("override"):
            return {"mode": _valid_mode(st["override"]),
                    "source": "runtime_override", "paused": False,
                    "override": st["override"],
                    "envMode": env_mode, "pausedReason": ""}
        return {"mode": env_mode, "source": "env", "paused": False,
                "override": "", "envMode": env_mode,
                "pausedReason": ""}

    async def require_decision_mode(self) -> dict:
        """决策面门槛(off 拒绝——全站范式)

        Raises:
            ValueError: mode=off(决策面关闭)
        """
        state = await self.current_mode()
        if state["mode"] == "off":
            raise ValueError(
                f"ZYH_MODE=off({state['source']}——"
                f"决策面关闭(问答/推演/辩题生成入口), "
                f"知识内核观测面不受影响)")
        return state

    async def note_decision_and_maybe_patrol(
            self) -> dict | None:
        """full 档自主巡检(L1 自主域 auto_patrol——74号
        "A 档 auto 仅 full" 范式同源)

        决策面每次调用计数 +1; 每 AUTO_PATROL_EVERY 次
        触发一次护栏自动巡检(确定性聚合+阈值比较,
        LLM 禁入)。assist/shadow 档不自主(巡检须人工
        POST /mode/guard)。巡检恶化仍走自动暂停+人工恢复
        (resume 永不自主铁律)。
        """
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
            "zyh_auto_patrol seq=%s breaches=%s",
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
        logger.info("zyh_mode_override=%s by=%s",
                    m or "清除", operator)
        return await self.current_mode()

    async def guard_check(self, craft_rate: float,
                           equiv_rate: float,
                           citation_rate: float,
                           baseline: dict = None,
                           skip_metrics: tuple = ()) -> dict:
        """护栏检查(恶化>3% 自动暂停; 基线缺省内置)"""
        base = {
            "craftMisstateRate": 0.10,
            "equivalenceRate": 0.10,
            "citationMissRate": 0.05,
        }
        base.update({
            k: max(0.0, float(v))
            for k, v in (baseline or {}).items()
            if k in GUARD_METRICS})
        current = {
            "craftMisstateRate": max(0.0, float(craft_rate or 0)),
            "equivalenceRate": max(0.0, float(equiv_rate or 0)),
            "citationMissRate": max(
                0.0, float(citation_rate or 0)),
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
                "竹韵·智衡大模型护栏自动暂停: " + reasons)
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({"at": st["pausedAt"],
                          "reason": st["pausedReason"],
                          "breaches": breaches})
            st["breachTrail"] = trail[-GUARD_METRICS_MAX:]
            logger.warning("zyh_mode_guard_paused: %s",
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
        logger.info("zyh_mode_resumed by=%s", operator)
        return await self.current_mode()

    async def aggregate_guard_metrics(self) -> dict:
        """从仓储确定性聚合三指标(巡检数据源)

        口径(SDD 守门三层映射):
            工艺误述率 = L1 工艺拦截 / 总请求
            等同混淆率 = L2 等同拦截 / 总请求
            溯源缺失率 = L3 拦截 / 技术断言应答
        """
        from repositories.zyh_repository import ZyhRepository
        repo = ZyhRepository()
        guard = await repo.get_stats("guard")
        cache = await repo.get_stats("cache")
        total_req = guard.get("total", 0)
        technical = cache.get("technicalAnswer", 0)
        return {
            "craftMisstateRate": round(
                guard.get("l1_craft", 0) / total_req, 4)
            if total_req else 0.0,
            "equivalenceRate": round(
                guard.get("l2_equivalence", 0) / total_req, 4)
            if total_req else 0.0,
            "citationMissRate": round(
                guard.get("l3_block", 0) / technical, 4)
            if technical else 0.0,
            "sample": {
                "totalRequests": total_req,
                "technicalAnswers": technical,
            },
        }

    async def patrol(self) -> dict:
        """护栏自动巡检(聚合 → 检查; 小样本保护)"""
        metrics = await self.aggregate_guard_metrics()
        sample = metrics.pop("sample", {})
        skip = set()
        if sample.get("totalRequests", 0) < _MIN_SAMPLE:
            skip.add("craftMisstateRate")
            skip.add("equivalenceRate")
        if sample.get("technicalAnswers", 0) < _MIN_SAMPLE:
            skip.add("citationMissRate")
        result = await self.guard_check(
            craft_rate=metrics["craftMisstateRate"],
            equiv_rate=metrics["equivalenceRate"],
            citation_rate=metrics["citationMissRate"],
            skip_metrics=tuple(skip))
        result["sample"] = sample
        result["skippedSmallSample"] = sorted(skip)
        return result

    async def status_view(self) -> dict:
        """灰度总览(模式+护栏+full 自主域+红线公示)"""
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
                         "/mode/guard"),
            },
            "neverAutonomous": (
                "knowledge upsert(单一事实源)/守门规则变更"
                "/护栏 resume(人工留痕)/cache clear"
                "(管理面)——full 档亦永不自主"),
            "observablesNeverOff": (
                "knowledge/graph/rules/stats/cache/mode"
                "——知识内核观测面永不关停"),
            "decisionSurfaces": (
                "chat/stress-test/probe debate"
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
                "breachCount": len(st.get("breachTrail") or []),
            },
            "modelVersion": MODEL_VERSION,
        }
