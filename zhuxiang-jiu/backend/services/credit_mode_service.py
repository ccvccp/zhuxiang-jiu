"""信用管理模块 大模型三态灰度与护栏服务

依据: 智运 ZwModeService / 65号 Xx65ModeService /
小竹 XiaozhuModeService / 钱包 WalletModeService 范式
(全站大模型转段标准)

三态灰度(CREDIT_MODE, 信用前缀):
    off(默认)    决策面关闭(409)——管理与先用后付入口
                (调整/升降级/黑名单/恢复/下单/审批/结算)
                不受理; 履约与兑换权永不关停
    shadow       信用观察期(决策放行+留痕, 不初始化)
    assist       信用辅助期(管理开放)

    读取链(优先级): 护栏暂停 > 运行时 override >
    环境变量 CREDIT_MODE > 默认 off。
    观测面永不关停(list/stats/report/quota/score/
    level evaluation/exchange recommend+catalog/
    quarterly/exchanges/paylater orders)。
    宪法豁免面永不关停:
        paylater repay(还款履约权——已生效订单的
        偿还义务永远可以履行)
        exchange(积分兑换权——既得信用积分的权益
        兑换不可设障)

护栏(信用业务语义三指标, 恶化>3% 自动暂停):
    逾期还款率 overdueRepayRate (overdue 订单/
        (repaid+overdue) 终态订单——信用履约恶化代理)
    paylater 拒单率 paylaterRejectRate (rejected
        订单/总订单——先用后付风控压力代理)
    黑名单占比 blacklistRate (blacklist 账户/
        总账户——信用恶化面代理)
    (cur-base)/base > 3% → 自动暂停(等效 off, 留痕);
    恢复须人工 resume(决策留痕)。护栏暂停是保护机制
    (功能开关, 非处罚), 可自动。

红线(宪法域):
    - 一代 credit_scoring 评分器(batch25 档案)与
      enforce 决策门语义保留(LLM 禁入判定链)
    - L1-L4 等级规则/硬规则(L2 才能用 B 额度等)不受
      mode 影响
    - 还款履约与积分兑换权不受开关影响

状态存储: zhuxiang:credit:mode_state 单键整档 JSON
(对齐智运范式——整体序列化, 无字段级清单风险,
规避 45号 P0 序列化教训)。
"""

import json
import logging
import os
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-credit-mode"

# 进程级快照(62号 av62 范式——服务层一代
# 同步门控 legacy_current_mode 数据源;
# mode service 异步操作后刷新)
_G: dict = {"override": "", "paused": False}


# ============================================================
# 常量(全站范式)
# ============================================================

# 三态
MODE_VALUES = ("off", "shadow", "assist")
DEFAULT_MODE = "off"

# 状态存储键(Redis / 内存)
_STATE_KEY_REDIS = "zhuxiang:credit:mode_state"
_STATE_KEY_MEM = "credit_mode_state"

# 护栏指标域(封闭三指标——信用管理语义)
GUARD_METRICS = ("overdueRepayRate",
                 "paylaterRejectRate",
                 "blacklistRate")
GUARD_METRIC_LABELS = {
    "overdueRepayRate": "逾期还款率",
    "paylaterRejectRate": "paylater 拒单率",
    "blacklistRate": "黑名单占比",
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


class CreditModeService:
    """信用管理大模型三态灰度 + 护栏"""

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
        else:
            _refresh_legacy(st)
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
            os.environ.get("CREDIT_MODE"))
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
                f"CREDIT_MODE=off({state['source']}——"
                f"决策面关闭(管理与先用后付入口不受理); "
                f"还款履约与积分兑换"
                f"不受开关影响)")
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
        logger.info("credit_mode_override=%s by=%s",
                    m or "清除", operator)
        return await self.current_mode()

    # ============================================================
    # 护栏(三指标恶化 >3% 自动暂停)
    # ============================================================

    async def guard_check(
            self, overdue_repay_rate: float,
            paylater_reject_rate: float,
            blacklist_rate: float,
            baseline: dict = None) -> dict:
        """护栏检查(确定性阈值比较; 恶化>3% 自动暂停)

        Args:
            overdue_repay_rate: 逾期还款率
                (overdue/(repaid+overdue) 终态订单)
            paylater_reject_rate: paylater 拒单率
                (rejected/总订单)
            blacklist_rate: 黑名单占比
                (blacklist 账户/总账户)
            baseline: 基线三指标(缺省用内置常量基线)

        恶化口径: (cur - base) / base > 3%
        (基线=0 时按绝对值>0 判恶化——防除零)。
        """
        base = {
            "overdueRepayRate": 0.10,
            "paylaterRejectRate": 0.10,
            "blacklistRate": 0.05,
        }
        base.update({k: max(0.0, float(v))
                    for k, v in (baseline or {}).items()
                    if k in GUARD_METRICS})
        current = {
            "overdueRepayRate": max(
                0.0, float(overdue_repay_rate or 0)),
            "paylaterRejectRate": max(
                0.0, float(paylater_reject_rate or 0)),
            "blacklistRate": max(
                0.0, float(blacklist_rate or 0)),
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
            # 自动暂停(保护机制——非处罚, 可自动;
            # 还款履约与兑换权不受影响——豁免面永不关停)
            reasons = "; ".join(
                f"{b['label']}{b['baseline']}→"
                f"{b['current']}"
                f"(+{b['deterioration']:.1%})"
                for b in breaches)
            st["paused"] = True
            st["pausedReason"] = (
                f"信用大模型护栏自动暂停: {reasons}")
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({
                "at": st["pausedAt"],
                "reason": st["pausedReason"],
                "breaches": breaches})
            st["breachTrail"] = trail[-GUARD_METRICS_MAX:]
            logger.warning("credit_mode_guard_paused: %s",
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
        logger.info("credit_mode_resumed by=%s note=%s",
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
                "list/stats/report/quota/score/"
                "level evaluation/exchange recommend+catalog/"
                "quarterly/exchanges/paylater orders"
                "——观测面永不关停(宪法口径)"),
            "exemptNeverOff": (
                "paylater repay(还款履约权——已生效订单"
                "的偿还义务永远可以履行)/"
                "exchange(积分兑换权——既得信用积分的"
                "权益兑换不可设障)"
                "——宪法豁免面永不关停"),
            "decisionSurfaces": (
                "adjust 调整, upgrade/downgrade 升降级, "
                "blacklist 黑名单, restore 恢复, "
                "paylater order 下单, review 审批, "
                "quarterly settle 季度结算"
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

    供信用一代服务同步门控委托; env 实时读,
    override/paused 来自进程快照(异步操作刷新)。
    """
    if _G["paused"]:
        return "off"
    if _G["override"]:
        return _valid_mode(_G["override"])
    return _valid_mode(
        os.environ.get("CREDIT_MODE"))
