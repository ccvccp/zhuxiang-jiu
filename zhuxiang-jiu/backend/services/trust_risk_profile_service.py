"""47号·L2/L3 信值验真风控模块 P0 核心服务
(角色风险画像 + 事件回流管道 + 信任分层 + 人工校准)

计划(docs/47号_L2L3信值验真风控模块实施计划.md §三):
    ① 画像仓库回流管道(零侵入挂钩):
        - record_event P6 守门命中 → hitCounts+1 + riskEMA
        - submit_deposit/submit_repair P7 验真完成 → 组件分
          <0.5 的维度数沉淀(verified 通过不加分——画像只
          记风险不记功劳, 功劳由信值分承载, 职责分离)
        - fail-soft: 画像沉淀异常不阻断主流程
    ② 风险指数数学:
        riskEMA' = riskEMA×(1−α) + risk_event×α   (α=0.2)
        risk_event = 单事件风险分(0-1):
            守门命中 1.0(×命中数, 封顶 1.0)/
            P7 组件最低分 <0.5 的维度数×0.25(封顶 1.0)
    ③ 信任分层(仅标签, 不直接处罚):
        ≥0.8 trusted / ≥0.5 standard / <0.5 watched /
        <0.3 restricted
        trustLevel = 1 − riskEMA(带校准覆盖)
    ④ 人工校准(复核闭环前置):
        calibrate(trustLevel 覆盖值 + 理由留痕)——零不可逆,
        可再校准; 重放回流不冲掉校准覆盖

设计红线(计划 §八):
    - 画像不处罚: P0 只沉淀观察, 不接任何入分/验真通道
    - verified 通过不加分: 画像只记风险
    - 既有 45号调用零改动: 回流经 record_risk_event() 显式
      调用(P6/P7 挂钩点在本模块内, 主流程 try 包裹)
"""

import logging

from core.helpers import ts

from repositories.trust_risk_repository import (
    TrustRisk47Repository, RISK_SIGNAL_VALUES,
)

logger = logging.getLogger(__name__)

# EMA 平滑系数(计划 §三 3.2: α=0.2)
RISK_ALPHA = 0.2

# 组件风险折算系数(P7 组件最低分 <0.5 的维度数 × 0.25)
COMPONENT_RISK_UNIT = 0.25
COMPONENT_RISK_THRESHOLD = 0.5

# 信任分层阈值(trustLevel = 1 − riskEMA)
TRUST_TIERS = ((0.8, "trusted"), (0.5, "standard"),
               (0.3, "watched"), (0.0, "restricted"))

# riskHistory 滚动截断(防画像无限膨胀)
RISK_HISTORY_MAX = 20

# P6 守门 tag → 画像信号映射(守门 tag 带 _alert/_discount
# 后缀; 画像枚举为短名——P7 riskTags 已是短名无需映射)
GATE_TAG_TO_SIGNAL = {
    "hypocrisy_alert": "hypocrisy",
    "self_promotion_discount": "self_promotion",
    "recurrence": "recurrence",
    "behavior_burst": "behavior_burst",
    "semantic_reuse": "semantic_reuse",
    "value_anomaly": "value_anomaly",
    "collusive_suspect": "collusive_suspect",
}


def normalize_signals(tags: list) -> list:
    """守门 tag/P7 riskTags 归一化为画像信号短名"""
    out = []
    for t in (tags or []):
        sig = GATE_TAG_TO_SIGNAL.get(t, t)
        if sig in RISK_SIGNAL_VALUES and sig not in out:
            out.append(sig)
    return out


def update_ema(current: float, risk_event: float,
               alpha: float = RISK_ALPHA) -> float:
    """EMA 更新: riskEMA' = riskEMA×(1−α) + risk_event×α"""
    current = max(0.0, min(1.0, float(current or 0)))
    risk_event = max(0.0, min(1.0, float(risk_event or 0)))
    return round(
        current * (1 - alpha) + risk_event * alpha, 4)


def trust_level_of(risk_ema: float) -> float:
    """信任度 = 1 − 风险指数(0-1)"""
    return round(1.0 - max(0.0, min(1.0,
                            float(risk_ema or 0))), 4)


def tier_of(trust_level: float) -> str:
    """信任分层(仅标签): trusted/standard/watched/restricted"""
    for threshold, name in TRUST_TIERS:
        if trust_level >= threshold:
            return name
    return "restricted"


def risk_event_score(signals: list = None,
                     components: dict = None) -> float:
    """单事件风险分(0-1)

    Args:
        signals: 命中信号列表(守门 tag/P7 riskTags——
        自动归一化; 每命中计 1.0, 多命中封顶 1.0)
        components: P7 组件分 {content/temporal/
        cross_source/intent}(最低分 <0.5 的维度数
        ×0.25 封顶 1.0——中分事件是灰色地带而非风险)
    """
    sig_risk = min(1.0, float(len(
        normalize_signals(signals))))
    comp_risk = 0.0
    if isinstance(components, dict) and components:
        low = sum(1 for v in components.values()
                  if isinstance(v, (int, float))
                  and v < COMPONENT_RISK_THRESHOLD)
        comp_risk = min(1.0, low * COMPONENT_RISK_UNIT)
    return round(max(sig_risk, comp_risk), 4)


class TrustRiskProfileService:
    """角色风险画像(P0: 沉淀/查询/校准)"""

    def __init__(self,
                 repo: TrustRisk47Repository = None):
        self.repo = repo or TrustRisk47Repository()

    # --------------------------------------------------------
    # ① 事件回流管道(fail-soft——主流程零侵入)
    # --------------------------------------------------------

    async def record_risk_event(
            self, trust_id: int, source: str,
            signals: list = None,
            components: dict = None,
            detail: str = "") -> dict | None:
        """沉淀一条风险事件到画像(fail-soft)

        Args:
            source: 事件来源标识(profile/repair/
                scan 等——画像视图回放用)
            signals: 命中信号(P6 守门 tags/P7 riskTags)
            components: P7 组件分(四维)
        Returns:
            更新后画像(异常返回 None——调用方零感知)
        """
        try:
            profile = await self._load(trust_id)
            risk = risk_event_score(signals, components)
            normalized = normalize_signals(signals)
            hits = dict(profile.get("hitCounts") or {})
            for s in normalized:
                hits[s] = int(hits.get(s) or 0) + 1
            profile["hitCounts"] = hits
            profile["riskEMA"] = update_ema(
                profile.get("riskEMA"), risk)
            profile["eventCount"] = int(
                profile.get("eventCount") or 0) + 1
            history = list(
                profile.get("riskHistory") or [])
            history.insert(0, {
                "source": source,
                "risk": risk,
                "signals": normalized,
                "detail": str(detail or "")[:120],
                "ts": ts(),
            })
            profile["riskHistory"] = \
                history[:RISK_HISTORY_MAX]
            profile["lastUpdated"] = ts()
            await self.repo.save_profile(profile)
            logger.info("trust47_risk_event trustId=%s "
                        "risk=%.4f ema=%.4f", trust_id,
                        risk, profile["riskEMA"])
            return profile
        except Exception as exc:
            logger.warning("trust47_record_failsoft "
                           "trustId=%s: %s", trust_id, exc)
            return None

    async def _load(self, trust_id: int) -> dict:
        """读画像(无则初始化——校准覆盖保留语义)"""
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            return {
                "trustId": trust_id, "riskEMA": 0.0,
                "hitCounts": {}, "eventCount": 0,
                "calibrateOverride": "",
                "calibrateNote": "", "calibrateAt": "",
                "createdAt": ts(), "lastUpdated": ts(),
                "riskHistory": [],
            }
        return profile

    # --------------------------------------------------------
    # ② 人工校准(复核闭环前置; 零不可逆)
    # --------------------------------------------------------

    async def calibrate(self, trust_id: int,
                        trust_level: float,
                        note: str) -> dict:
        """人工校准信任度覆盖(留痕; 可反复修正)

        校准后的 trustLevel 覆盖计算值(trustLevel_of);
        清除校准传 trust_level=None 语义由
        clear_calibration 承载。

        Raises:
            ValueError: trust_level 越界/理由必填
        """
        note = (note or "").strip()
        if not note or len(note) > 300:
            raise ValueError("校准理由必填(1-300 字符)")
        trust_level = float(trust_level)
        if not 0 <= trust_level <= 1:
            raise ValueError("trust_level 需在 [0,1] 区间")
        profile = await self._load(trust_id)
        profile["calibrateOverride"] = round(
            trust_level, 4)
        profile["calibrateNote"] = note
        profile["calibrateAt"] = ts()
        profile["lastUpdated"] = ts()
        await self.repo.save_profile(profile)
        logger.info("trust47_calibrated trustId=%s "
                    "override=%.4f", trust_id, trust_level)
        return await self.get_profile(trust_id)

    async def clear_calibration(
            self, trust_id: int, note: str) -> dict:
        """清除校准覆盖(回到计算值; 留痕)"""
        note = (note or "").strip()
        if not note or len(note) > 300:
            raise ValueError("清除理由必填(1-300 字符)")
        profile = await self._load(trust_id)
        profile["calibrateOverride"] = ""
        profile["calibrateNote"] = \
            f"[清除] {note}"
        profile["calibrateAt"] = ts()
        profile["lastUpdated"] = ts()
        await self.repo.save_profile(profile)
        return await self.get_profile(trust_id)

    # --------------------------------------------------------
    # ③ 画像视图
    # --------------------------------------------------------

    async def get_profile(self, trust_id: int) -> dict:
        """画像视图(风险指数/信任分层/命中明细/历史)

        Raises:
            KeyError: trustId 无档案(trust45 侧不存在)
        """
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        exists = await TrustValue45Repository(
        ).get_profile(trust_id)
        if exists is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")
        profile = await self._load(trust_id)
        risk = float(profile.get("riskEMA") or 0)
        computed = trust_level_of(risk)
        override = profile.get("calibrateOverride")
        effective = (float(override)
                     if override not in ("", None)
                     else computed)
        return {
            "success": True,
            "trustId": trust_id,
            "role": exists.get("role"),
            "riskEMA": risk,
            "trustLevel": effective,
            "trustLevelComputed": computed,
            "calibrated": override not in ("", None),
            "tier": tier_of(effective),
            "eventCount": profile.get("eventCount"),
            "hitCounts": profile.get("hitCounts") or {},
            "riskHistory": profile.get("riskHistory") or [],
            "calibrateNote": profile.get("calibrateNote"),
            "createdAt": profile.get("createdAt"),
            "lastUpdated": profile.get("lastUpdated"),
        }

    async def list_profiles(self) -> dict:
        """全档案风险排行(最高风险在前 + 分层统计)"""
        profiles = await self.repo.list_profiles(limit=500)
        entries = []
        by_tier: dict = {}
        for p in profiles:
            risk = float(p.get("riskEMA") or 0)
            computed = trust_level_of(risk)
            override = p.get("calibrateOverride")
            effective = (float(override)
                         if override not in ("", None)
                         else computed)
            tier = tier_of(effective)
            by_tier[tier] = by_tier.get(tier, 0) + 1
            entries.append({
                "trustId": p.get("trustId"),
                "riskEMA": risk,
                "trustLevel": effective,
                "tier": tier,
                "eventCount": p.get("eventCount"),
                "hitCounts": p.get("hitCounts") or {},
                "calibrated": override not in ("", None),
                "lastUpdated": p.get("lastUpdated"),
            })
        return {
            "success": True, "total": len(entries),
            "byTier": by_tier, "profiles": entries,
            "tiers": [t for _, t in TRUST_TIERS],
            "fetchedAt": ts(),
        }
