"""69号·AI智能支付大模型 风险熵引擎
(pay69_entropy_service, P2)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§4.4/§七 P2):
    ① 六轴确定性熵计算(金额×信值×行为
       ×环境×通道×历史——查表加权,
       LLM 禁入判定链)
    ② 熵值→步进梯度(免密→OTP→生物
       →双人复核; 摩擦感与信任等级成
       反比铁律)
    ③ 隐式行为基线(EMA 快环+偏离度
       确定性计算)
    ④ 60号 riskTier 语义对齐(纯映射
       消费——60号代码零改动)

铁律(规划 §1.2/§4.4):
    - fail-soft: 熵引擎故障→light 档
      +留痕(风控设施故障不阻断业务)
    - 熵计算全确定性(六轴数值明细
      留痕——可复现可审计)
    - 基线漂移→熵升高→认证增强(
      方向单一——安全侧)
"""

import logging

from core.helpers import ts

from repositories.pay69_repository import (
    Pay69Repository,
)
from services.pay69_registry import (
    AMOUNT_BANDS, CHANNEL_RISK,
    ENTROPY_AXES, ENTROPY_WEIGHTS,
    STEP_LADDER, TRUST_BANDS,
    MODEL_VERSION,
)

logger = logging.getLogger("pay69_entropy")

# 环境轴异常项权重(确定性叠加)
ENV_RISKS = {
    "newDevice": 0.40,     # 新设备
    "oddHour": 0.25,       # 异常时段(0-6 点)
    "newLocation": 0.30,   # 异常地点
}
# 环境轴封顶(三项全中 0.95)
ENV_CAP = 0.95

# 行为偏离分档(基线偏离比→熵)
DEVIATION_BANDS = (
    (1.30, 0.10),   # 偏离≤30% → 0.10(基线内)
    (1.80, 0.40),   # ≤80% → 0.40(轻微)
    (2.50, 0.70),   # ≤150% → 0.70(中度)
    (float("inf"), 0.95),  # 严重
)


class Pay69EntropyService:
    """69号风险熵引擎(P2)"""

    def __init__(self):
        self.repo = Pay69Repository()

    # ============================================================
    # 六轴计算(确定性——查表)
    # ============================================================

    def _amount_axis(self, amount: float) -> float:
        """金额轴(分档查表)"""
        for cap, score in AMOUNT_BANDS:
            if amount <= cap:
                return score
        return 0.95

    def _trust_axis(self, trust_tier: str) -> float:
        """信值轴(45号 tier 口径——未知
        档回退 0.60 中性偏保守)"""
        return TRUST_BANDS.get(
            str(trust_tier or ""), 0.60)

    def _behavior_axis(
            self, deviation: float | None) -> float:
        """行为轴(偏离比查表; 无基线=0.30
        中性——新会员不惩罚不奖励)"""
        if deviation is None:
            return 0.30
        for cap, score in DEVIATION_BANDS:
            if deviation <= cap:
                return score
        return 0.95

    def _environment_axis(
            self, new_device: bool,
            odd_hour: bool,
            new_location: bool) -> float:
        """环境轴(异常项确定性叠加)"""
        risk = 0.0
        if new_device:
            risk += ENV_RISKS["newDevice"]
        if odd_hour:
            risk += ENV_RISKS["oddHour"]
        if new_location:
            risk += ENV_RISKS["newLocation"]
        return min(round(risk, 4), ENV_CAP)

    def _channel_axis(
            self, channel_id: str) -> float:
        """通道轴(封闭映射; 未指定=0.50
        中性)"""
        if not channel_id:
            return 0.50
        return CHANNEL_RISK.get(
            str(channel_id), 0.50)

    def _history_axis(
            self, anomaly_rate: float) -> float:
        """历史轴(支付异常率 0-1 线性)"""
        rate = max(0.0, min(1.0,
                            float(anomaly_rate
                                  or 0)))
        return round(0.1 + rate * 0.85, 4)

    # ============================================================
    # 行为偏离度(基线对比——确定性)
    # ============================================================

    def deviation_of(
            self, baseline: dict | None,
            interval_ms: float,
            typing_speed_ms: float) -> float | None:
        """当前行为 vs 基线偏离比(无基线
        =None——中性; 双指标取最大偏离)"""
        if not baseline \
                or not baseline.get("samples"):
            return None
        base_i = float(baseline.get(
            "avgIntervalMs", 0) or 0)
        base_t = float(baseline.get(
            "avgTypingMs", 0) or 0)
        if base_i <= 0 or base_t <= 0:
            return None
        dev_i = abs(
            float(interval_ms) - base_i) / base_i
        dev_t = abs(
            float(typing_speed_ms)
            - base_t) / base_t
        return round(max(dev_i, dev_t) + 1.0, 4)

    # ============================================================
    # 熵计算+步进梯度(决策面)
    # ============================================================

    async def compute_entropy(
            self, member_id: int, amount: float,
            trust_tier: str = "",
            channel_id: str = "",
            new_device: bool = False,
            odd_hour: bool = False,
            new_location: bool = False,
            anomaly_rate: float = 0.0,
            interval_ms: float = 0,
            typing_speed_ms: float = 0,
            fail_soft: bool = False) -> dict:
        """六轴熵计算+步进梯度解析

        fail_soft=True 时引擎故障→light
        档+留痕(不阻断业务铁律)

        Raises:
            ValueError: 金额非法
        """
        amount = round(float(amount or 0), 2)
        if amount <= 0:
            raise ValueError(
                f"金额非法: {amount}(须>0)")
        try:
            baseline = await self.repo\
                .get_baseline(member_id)
            deviation = self.deviation_of(
                baseline, interval_ms,
                typing_speed_ms)
            axes = {
                "amount": self._amount_axis(
                    amount),
                "trust": self._trust_axis(
                    trust_tier),
                "behavior": self._behavior_axis(
                    deviation),
                "environment": self._environment_axis(
                    new_device, odd_hour,
                    new_location),
                "channel": self._channel_axis(
                    channel_id),
                "history": self._history_axis(
                    anomaly_rate),
            }
            entropy = round(sum(
                ENTROPY_WEIGHTS[k] * axes[k]
                for k in ENTROPY_AXES), 4)
            step, tier = self._step_of(entropy)
            record = {
                "memberId": int(member_id or 0),
                "amount": amount,
                "trustTier": str(trust_tier or ""),
                "channelId": str(channel_id or ""),
                "axes": axes,
                "deviation": deviation,
                "entropy": entropy,
                "step": step,
                "riskTier": tier,
                "failSoft": False,
                "computedAt": ts(),
            }
        except Exception:
            if not fail_soft:
                raise
            # fail-soft 铁律: light 档+留痕
            record = {
                "memberId": int(member_id or 0),
                "amount": amount,
                "trustTier": str(trust_tier or ""),
                "channelId": str(channel_id or ""),
                "axes": None,
                "deviation": None,
                "entropy": None,
                "step": "free",
                "riskTier": "light",
                "failSoft": True,
                "computedAt": ts(),
            }
            logger.exception(
                "pay69 entropy fail-soft "
                "member=%r", member_id)
        await self.repo.save_entropy(
            dict(record))
        return record

    @staticmethod
    def _step_of(entropy: float):
        """熵值→(步进档, 60号 riskTier
        对齐)——封闭梯度查表"""
        for step, cap, _label, tier \
                in STEP_LADDER:
            if entropy < cap:
                return step, tier
        return "dual", "enhanced"

    # ============================================================
    # 行为基线(快环)
    # ============================================================

    async def report_behavior(
            self, member_id: int,
            interval_ms: float,
            typing_speed_ms: float) -> dict:
        """行为样本上报(快环——EMA 基线
        增量更新, 不受 PAY69_MODE 影响)

        Raises:
            ValueError: 参数非法(非正)
        """
        if interval_ms <= 0 or typing_speed_ms <= 0:
            raise ValueError(
                "行为样本须为正(间隔/速度>0)")
        baseline = await self.repo\
            .update_baseline(
                member_id, interval_ms,
                typing_speed_ms)
        await self.repo.save_event({
            "type": "behavior_report",
            "memberId": int(member_id or 0),
            "detail": {
                "intervalMs": float(interval_ms),
                "typingMs": float(
                    typing_speed_ms),
                "samples": baseline["samples"],
            },
            "at": ts(),
        })
        return baseline

    async def baseline_view(
            self, member_id: int) -> dict:
        """会员行为基线视图(观测面)"""
        baseline = await self.repo.get_baseline(
            member_id)
        return {
            "memberId": int(member_id),
            "baseline": baseline,
            "established": bool(
                baseline and baseline.get(
                    "samples")),
        }

    # ============================================================
    # 观测面
    # ============================================================

    def entropy_dict(self) -> dict:
        """熵引擎字典公示(六轴+权重+
        梯度——观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "axes": list(ENTROPY_AXES),
            "weights": dict(ENTROPY_WEIGHTS),
            "ladder": [
                {"step": s, "cap": c,
                 "label": lb, "riskTier": t}
                for s, c, lb, t in STEP_LADDER
            ],
            "amountBands": [
                {"cap": (c if c != float("inf")
                         else None),
                 "score": s}
                for c, s in AMOUNT_BANDS
            ],
            "trustBands": dict(TRUST_BANDS),
            "channelRisk": dict(CHANNEL_RISK),
            "ironRules": (
                "fail-soft(引擎故障→light+留痕)",
                "LLM 禁入(六轴全查表可复现)",
                "梯度阈值变更走 46号审批(慢环)"),
        }

    async def entropy_view(
            self, member_id: int = None,
            limit: int = 50) -> dict:
        """熵评估留痕视图(观测面)"""
        records = await self.repo.list_entropy(
            member_id, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(records),
            "records": records,
        }
