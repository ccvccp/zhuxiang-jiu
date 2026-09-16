"""智图·AI智能地图大模型——三态灰度与护栏服务

依据: 小竹/钱包/信用模式层范式(全站大模型转段标准——
68/45/智运/62/65 同源; 62号同步桥接)。

三态灰度(ZT_MODE, 智图前缀——避开既有 LOCATION_* 命名):
    off(默认)    决策面关闭(409)——既有 location 13 端点零影响铁律
    shadow       观察学习期(决策放行+响应留痕标记 ztMode)
    assist       辅助生产期(决策生效)

    读取链(优先级): 护栏暂停 > 运行时 override >
    环境变量 ZT_MODE > 默认 off。
    观测面永不关停(意图本体/角色/POI 查询/月台状态/
    履约看板/态势/工单列表/驾驶舱/风险雷达/行为/绩效/
    反馈列表——全域只读聚合)。

护栏(智图时空调度语义三指标, 恶化>3% 自动暂停):
    建议驳回率 rejectRate       (rejected 反馈占比——AI 建议质量恶化代理)
    调度误报率 falseAlarmRate   (dismissed / 已处置工单 acked+dismissed
                                ——异常扫描误报恶化代理)
    月台饱和率 dockSaturationRate (峰值时段预约车数 / 月台总容量
                                ——月台运力饱和恶化代理)
    口径铁律: 智图工单为《处置预案草稿》(人工确认制),
    pending 为业务常态——已处置工单才进误报分母,
    冷启动/未使用不误判(分母 0 取 0)。
    (cur-base)/base > 3% → 自动暂停(等效 off, 留痕);
    恢复须人工 resume(决策留痕)。护栏暂停是保护机制
    (功能开关, 非处罚), 可自动。

红线(宪法域):
    - 既有位置地图 13 端点(/api/location/*)零改动:
      mode 只挡智图 AI 决策面(/api/map-ai/* POST)
    - 观测面永不关停: 全域查询/看板/雷达常开
    - 建议书模式永不自动执行(月台分配/仓推荐/沙盘选址
      均为建议书制——人工确认生效)不受 mode 影响
    - 一代 20号位置地图 AI 决策门(delivery_point,
      ai_enforcement_longtail)独立 AI_ENFORCE_MODE, 不受
      ZT_MODE 影响
    - LLM 禁入: 护栏=阈值比较, 全确定性

状态存储: zhuxiang:zt:mode_state 单键整档 JSON
(对齐 _ZtStore 表键空间 zhuxiang:zt:{table}:{id}——
mode_state 不属 ZT_TABLES 六表, scan 模式互不匹配)。
"""

import json
import logging
import os
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-ztmap-mode"

# ============================================================
# 常量(全站范式)
# ============================================================

# 三态
MODE_VALUES = ("off", "shadow", "assist")
DEFAULT_MODE = "off"

# 状态存储键(Redis / 内存)
_STATE_KEY_REDIS = "zhuxiang:zt:mode_state"
_STATE_KEY_MEM = "zt_mode_state"

# 护栏指标域(封闭三指标——智图时空调度语义)
GUARD_METRICS = ("rejectRate", "falseAlarmRate",
                 "dockSaturationRate")
GUARD_METRIC_LABELS = {
    "rejectRate": "建议驳回率",
    "falseAlarmRate": "调度误报率",
    "dockSaturationRate": "月台饱和率",
}

# 恶化阈值(任一指标相对基线 >3% 自动暂停)
GUARD_DETERIORATION = 0.03

# 指标留痕上限(防无限膨胀)
GUARD_METRICS_MAX = 50

# 进程级快照(62号 av62 范式——服务层一代
# 同步门控 legacy_current_mode 数据源;
# mode service 异步操作后刷新)
_G: dict = {"override": "", "paused": False}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _valid_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODE_VALUES else DEFAULT_MODE


class ZtMapModeService:
    """智图大模型三态灰度 + 护栏"""

    def __init__(self):
        self._store = get_in_memory_store()

    # ============================================================
    # 状态存取(整体 JSON——类型安全往返)
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
        _refresh_legacy(st)

    async def _state(self) -> dict:
        """运行时态(缺省创建)"""
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
        return st

    # ============================================================
    # 读取链
    # ============================================================

    async def current_mode(self) -> dict:
        """当前灰度态(读取链: 暂停>override>env>off)

        Returns:
            {mode, source, paused, override,
             envMode, pausedReason}
        """
        st = await self._state()
        env_mode = _valid_mode(
            os.environ.get("ZT_MODE"))
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
        """决策面门槛(off 拒绝——全站范式)

        Raises:
            ValueError: mode=off(决策面关闭)
        """
        state = await self.current_mode()
        if state["mode"] == "off":
            raise ValueError(
                f"ZT_MODE=off({state['source']}——"
                f"决策面关闭, 观测面不受影响; "
                f"既有 location 13 端点零影响)")
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
        logger.info("zt_mode_override=%s by=%s", m or "清除",
                    operator)
        return await self.current_mode()

    # ============================================================
    # 护栏(三指标恶化 >3% 自动暂停)
    # ============================================================

    async def guard_check(
            self, reject_rate: float,
            false_alarm_rate: float,
            dock_saturation_rate: float,
            baseline: dict = None) -> dict:
        """护栏检查(确定性阈值比较; 恶化>3% 自动暂停)

        Args:
            reject_rate: 当期建议驳回率(rejected/总反馈)
            false_alarm_rate: 当期调度误报率
                (dismissed / 已处置工单 acked+dismissed
                ——pending 草稿不进分母, 人工确认制)
            dock_saturation_rate: 当期月台饱和率
                (峰值时段预约车数 / 月台总容量)
            baseline: 基线三指标(缺省用内置常量基线)

        恶化口径: (cur - base) / base > 3%
        (基线=0 时按绝对值>0 判恶化——防除零)。
        """
        base = {
            "rejectRate": 0.10,
            "falseAlarmRate": 0.10,
            "dockSaturationRate": 0.60,
        }
        base.update({k: max(0.0, float(v))
                     for k, v in (baseline or {}).items()
                     if k in GUARD_METRICS})
        current = {
            "rejectRate": max(0.0, float(reject_rate or 0)),
            "falseAlarmRate": max(
                0.0, float(false_alarm_rate or 0)),
            "dockSaturationRate": max(
                0.0, float(dock_saturation_rate or 0)),
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
            # 自动暂停(保护机制——非处罚, 可自动)
            reasons = "; ".join(
                f"{b['label']}{b['baseline']}→"
                f"{b['current']}"
                f"(+{b['deterioration']:.1%})"
                for b in breaches)
            st["paused"] = True
            st["pausedReason"] = (
                f"智图大模型护栏自动暂停: {reasons}")
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({
                "at": st["pausedAt"],
                "reason": st["pausedReason"],
                "breaches": breaches})
            st["breachTrail"] = trail[-GUARD_METRICS_MAX:]
            logger.warning("zt_mode_guard_paused: %s",
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
        logger.info("zt_mode_resumed by=%s note=%s",
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
                "intent/ontology+roles/poi/nearby+pois"
                "/supplier/dock-status/b2b/fulfillment-board"
                "/command/situation+tickets"
                "/agent/cockpit/management/radar"
                "/evolution/behaviors+performance+feedbacks"
                "——观测面永不关停(宪法口径)"),
            "decisionSurfaces": (
                "intent/parse+search+behavior-hints"
                "/poi/register/supplier/dock-booking"
                "/b2b/warehouse-match/command/scan"
                "+tickets/ack/evolution/behavior+sandbox"
                "+feedback"
                "——off 拒绝(409); 既有 location 13 端点零影响"),
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
# 同步桥接(62号 av62 范式——服务层一代门控同源)
# ============================================================

def _refresh_legacy(st: dict) -> None:
    """刷新进程级快照(mode service 读写后调用)"""
    _G["override"] = str(st.get("override") or "")
    _G["paused"] = bool(st.get("paused"))


def legacy_current_mode() -> str:
    """同步模式读取(暂停 > override > env > off)

    供智图服务层同步门控委托; env 实时读,
    override/paused 来自进程快照(异步操作刷新)。
    """
    if _G["paused"]:
        return "off"
    if _G["override"]:
        return _valid_mode(_G["override"])
    return _valid_mode(
        os.environ.get("ZT_MODE"))
