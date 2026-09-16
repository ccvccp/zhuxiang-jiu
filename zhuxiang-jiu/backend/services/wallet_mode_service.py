"""钱包盈利模块 大模型三态灰度与护栏服务

依据: 智运 ZwModeService / 65号 Xx65ModeService /
小竹 XiaozhuModeService 范式(全站大模型转段标准)

三态灰度(WALLET_MODE, 钱包前缀):
    off(默认)    决策面关闭(409)——新增盈利入口(开通/充值/
                消费/转定期/结付/发货)不受理;
                资金退出权永不关停(宪法豁免面)
    shadow       钱包观察期(决策放行+留痕, 不初始化)
    assist       钱包辅助期(盈利功能开放)

    读取链(优先级): 护栏暂停 > 运行时 override >
    环境变量 WALLET_MODE > 默认 off。
    观测面永不关停(info/提现单详情/待审核列表/交易明细/
    日收益预估/收益规则/定期列表/奖品列表)。
    宪法豁免面永不关停——资金红线(《消保法》第9条选择权+
    设计文档 1.7「不得限制提现/不得设置退出障碍」):
        withdraw 提现申请(资金退出权)
        withdrawal approve/paid(在途提现审核链)
        refund 退款(资金权利)
        deposit settle/early-settle(定期本金取出权)
        reward claim/sign(奖品领取与签收权)

护栏(钱包业务语义三指标, 恶化>3% 自动暂停):
    提现拒绝率 withdrawRejRate (rejected 提现单/总提现单
        ——审批健康度代理)
    提前取出率 earlySettleRate (early_settled 定期/总结清
        定期——盈利模式健康度代理, 提前取出=收益+奖品流失)
    账户冻结率 accountFrozenRate (frozen 账户/总账户
        ——风控压力代理)
    (cur-base)/base > 3% → 自动暂停(等效 off, 留痕);
    恢复须人工 resume(决策留痕)。护栏暂停是保护机制
    (功能开关, 非处罚), 可自动。

红线(宪法域):
    - LPR 4倍(13.8%)综合收益率校验不受 mode 影响
    - 资金退出权(提现/取出/退款/领奖)不受开关影响
    - 大额交易报送不受开关影响(反洗钱义务)
    - LLM 禁入判定链(护栏=阈值比较, 全确定性)

状态存储: zhuxiang:wallet:mode_state 单键整档 JSON
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

MODEL_VERSION = "v1-wallet-mode"

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
_STATE_KEY_REDIS = "zhuxiang:wallet:mode_state"
_STATE_KEY_MEM = "wallet_mode_state"

# 护栏指标域(封闭三指标——钱包盈利语义)
GUARD_METRICS = ("withdrawRejRate",
                 "earlySettleRate",
                 "accountFrozenRate")
GUARD_METRIC_LABELS = {
    "withdrawRejRate": "提现拒绝率",
    "earlySettleRate": "提前取出率",
    "accountFrozenRate": "账户冻结率",
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


class WalletModeService:
    """钱包盈利大模型三态灰度 + 护栏"""

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
            os.environ.get("WALLET_MODE"))
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
                f"WALLET_MODE=off({state['source']}——"
                f"决策面关闭(新增盈利入口不受理); "
                f"资金退出权(提现/取出/退款/领奖)"
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
        logger.info("wallet_mode_override=%s by=%s",
                    m or "清除", operator)
        return await self.current_mode()

    # ============================================================
    # 护栏(三指标恶化 >3% 自动暂停)
    # ============================================================

    async def guard_check(
            self, withdraw_rej_rate: float,
            early_settle_rate: float,
            account_frozen_rate: float,
            baseline: dict = None) -> dict:
        """护栏检查(确定性阈值比较; 恶化>3% 自动暂停)

        Args:
            withdraw_rej_rate: 提现拒绝率
                (rejected 提现单/总提现单)
            early_settle_rate: 提前取出率
                (early_settled 定期/总结清定期)
            account_frozen_rate: 账户冻结率
                (frozen 账户/总账户)
            baseline: 基线三指标(缺省用内置常量基线)

        恶化口径: (cur - base) / base > 3%
        (基线=0 时按绝对值>0 判恶化——防除零)。
        """
        base = {
            "withdrawRejRate": 0.10,
            "earlySettleRate": 0.10,
            "accountFrozenRate": 0.05,
        }
        base.update({k: max(0.0, float(v))
                    for k, v in (baseline or {}).items()
                    if k in GUARD_METRICS})
        current = {
            "withdrawRejRate": max(
                0.0, float(withdraw_rej_rate or 0)),
            "earlySettleRate": max(
                0.0, float(early_settle_rate or 0)),
            "accountFrozenRate": max(
                0.0, float(account_frozen_rate or 0)),
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
            # 资金退出权不受影响——豁免面永不关停)
            reasons = "; ".join(
                f"{b['label']}{b['baseline']}→"
                f"{b['current']}"
                f"(+{b['deterioration']:.1%})"
                for b in breaches)
            st["paused"] = True
            st["pausedReason"] = (
                f"钱包大模型护栏自动暂停: {reasons}")
            st["pausedAt"] = _now_iso()
            trail = list(st.get("breachTrail") or [])
            trail.append({
                "at": st["pausedAt"],
                "reason": st["pausedReason"],
                "breaches": breaches})
            st["breachTrail"] = trail[-GUARD_METRICS_MAX:]
            logger.warning("wallet_mode_guard_paused: %s",
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
        logger.info("wallet_mode_resumed by=%s note=%s",
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
                "info/提现单详情/待审核列表/交易明细/"
                "日收益预估/收益规则/定期列表/奖品列表"
                "——观测面永不关停(宪法口径)"),
            "exemptNeverOff": (
                "withdraw 提现申请(资金退出权——"
                "《消保法》第9条+文档红线「不得限制提现」)/"
                "withdrawal approve+paid(在途提现审核链)/"
                "refund 退款(资金权利)/"
                "deposit settle+early-settle(定期本金取出权)/"
                "reward claim+sign(奖品领取与签收权)"
                "——宪法豁免面永不关停"),
            "decisionSurfaces": (
                "open 开通, deposit 充值, pay 消费支付, "
                "transfer-regular 转定期, "
                "interest settle-monthly 月度结付, "
                "reward ship 奖品发货(admin)"
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

    供钱包一代服务同步门控委托; env 实时读,
    override/paused 来自进程快照(异步操作刷新)。
    """
    if _G["paused"]:
        return "off"
    if _G["override"]:
        return _valid_mode(_G["override"])
    return _valid_mode(
        os.environ.get("WALLET_MODE"))
