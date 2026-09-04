"""47号·L2/L3 信值验真风控模块 P4 风控看板
(五区块聚合 + 46号公平性桥接)

计划(docs/47号_L2L3信值验真风控模块实施计划.md §七):
    ① 风险排行(trustLevel 分层分布 + watched/restricted
       名单)
    ② 命中统计(七类命中计数趋势)
    ③ 嫌疑视图(collusive_suspect 对列表 + 证据链入口)
    ④ 复核队列(待复核申诉 + 一键校准)
    ⑤ 回流状态(先验开关状态 + 近期折扣效果统计)

    桥接 46号公平性: 风险等级(trusted/standard/watched/
    restricted)注册为公平性采样的 group 维度——验证
    "通道收窄是否在群体间造成系统性偏差"(风控自身的
    公平性审计, 防守门误伤)。

设计范式(43-46号看板惯例):
    - 单端点聚合: 一次 GET 五区块全量, 前端零拼装
    - fail-soft 分区: 单区块数据源异常不阻断看板
      (区块级 error 留痕, 其余照常)
    - 数字来自数据层: 各区块直接引用 P0-P3 服务真实
      计算结果, 不做二次推导
    - 桥接只读零侵入: 公平性样本经显式 import 上报
      (46号 submit_samples 范式, trustId 不出库——
      group 为 tier 标签, 无个人标识)
"""

import logging
from collections import defaultdict

from core.helpers import ts

from repositories.trust_risk_repository import (
    TrustRisk47Repository,
)
from services.trust_risk_profile_service import (
    TrustRiskProfileService, prior_mode_enabled,
    TRUST_TIERS,
)

logger = logging.getLogger(__name__)

# 桥接 46号公平性的档案(trust_value 既有档案)
BRIDGE_SCORER_ID = "trust_value"


class TrustRiskDashboardService:
    """P4 风控看板(五区块聚合, fail-soft 分区)"""

    def __init__(self,
                 repo: TrustRisk47Repository = None):
        self.repo = repo or TrustRisk47Repository()

    async def build(self) -> dict:
        """五区块聚合(单次 GET, fail-soft 分区)"""
        zones = {}
        prior_mode = prior_mode_enabled()

        async def _zone(name, fn):
            try:
                zones[name] = await fn()
            except Exception as exc:
                logger.warning("trust47_zone_%s_failsoft: %s",
                               name, exc)
                zones[name] = {"error": str(exc)[:120]}

        await _zone("ranking", self._zone_ranking)
        await _zone("hits", self._zone_hits)
        await _zone("collusion", self._zone_collusion)
        await _zone("reviews", self._zone_reviews)
        zones["prior"] = self._zone_prior(prior_mode)
        return {
            "success": True,
            "module": "trust-risk-47",
            "generatedAt": ts(),
            "priorMode": prior_mode,
            "zones": zones,
            "meta": {
                "tiers": [t for _, t in TRUST_TIERS],
                "signals": [
                    "hypocrisy", "self_promotion",
                    "recurrence", "behavior_burst",
                    "semantic_reuse", "value_anomaly",
                    "collusive_suspect"],
                "note": "画像不处罚(红线); 嫌疑仅标记, "
                        "处罚走人工复核",
            },
        }

    # --------------------------------------------------------
    # ① 风险排行(分层分布 + watched/restricted 名单)
    # --------------------------------------------------------

    async def _zone_ranking(self) -> dict:
        profiles = await self.repo.list_profiles(limit=500)
        by_tier: dict = defaultdict(int)
        entries = []
        for p in profiles:
            risk = float(p.get("riskEMA") or 0)
            from services.trust_risk_profile_service import (
                trust_level_of, tier_of,
            )
            override = p.get("calibrateOverride")
            effective = (float(override)
                        if override not in ("", None)
                        else trust_level_of(risk))
            tier = tier_of(effective)
            by_tier[tier] += 1
            if tier in ("watched", "restricted"):
                entries.append({
                    "trustId": p.get("trustId"),
                    "riskEMA": risk,
                    "trustLevel": effective,
                    "tier": tier,
                    "eventCount": p.get("eventCount"),
                    "hitCounts": p.get("hitCounts") or {},
                    "lastUpdated": p.get("lastUpdated")})
        entries.sort(key=lambda e: -e["riskEMA"])
        return {
            "total": len(profiles),
            "byTier": dict(by_tier),
            "watchlist": entries[:50],
        }

    # --------------------------------------------------------
    # ② 命中统计(七类命中计数汇总)
    # --------------------------------------------------------

    async def _zone_hits(self) -> dict:
        profiles = await self.repo.list_profiles(limit=500)
        totals: dict = defaultdict(int)
        affected: dict = defaultdict(int)
        for p in profiles:
            hits = p.get("hitCounts") or {}
            for sig, n in hits.items():
                try:
                    n = int(n)
                except (TypeError, ValueError):
                    continue
                totals[sig] += n
                if n > 0:
                    affected[sig] += 1
        return {
            "totals": dict(sorted(totals.items(),
                                  key=lambda kv: -kv[1])),
            "affectedProfiles": dict(affected),
            "totalEvents": sum(int(p.get("eventCount")
                                   or 0) for p in profiles),
        }

    # --------------------------------------------------------
    # ③ 嫌疑视图(团伙嫌疑 + 证据链入口)
    # --------------------------------------------------------

    async def _zone_collusion(self) -> dict:
        from services.trust_risk_collusion_service import (
            TrustRiskCollusionService,
        )
        view = await TrustRiskCollusionService(
            repo=self.repo).view()
        return {
            "totals": view.get("totals") or {},
            "suspects": view.get("suspects") or [],
            "mutualPairs": [
                {k: p.get(k) for k in
                 ("a", "b", "mutual", "suspect")}
                for p in view.get("mutualPairs") or []],
            "note": view.get("note", ""),
        }

    # --------------------------------------------------------
    # ④ 复核队列(待复核申诉 + 近期决定)
    # --------------------------------------------------------

    async def _zone_reviews(self) -> dict:
        profiles = await self.repo.list_profiles(limit=500)
        pending = []
        recent = []
        for p in profiles:
            for r in p.get("reviewRequests") or []:
                entry = {
                    "trustId": p.get("trustId"),
                    "tier": None, **r}
                if r.get("status") == "pending":
                    pending.append(entry)
                elif r.get("resolvedAt"):
                    recent.append(entry)
        pending.sort(key=lambda e: e.get("requestedAt") or "",
                     reverse=True)
        recent.sort(key=lambda e: e.get("resolvedAt") or "",
                    reverse=True)
        return {
            "pending": pending[:50],
            "pendingCount": len(pending),
            "recent": recent[:20],
        }

    # --------------------------------------------------------
    # ⑤ 回流状态(开关 + 近期折扣效果统计)
    # --------------------------------------------------------

    def _zone_prior(self, prior_mode: bool) -> dict:
        """先验回流状态(开关静态呈现 + 口径说明; 折扣
        效果经存证留痕的 riskHistory.source=deposit 追溯,
        不在此做二次推导——数字来自数据层铁律)"""
        return {
            "enabled": prior_mode,
            "envVar": "RISK_PRIOR_MODE",
            "tierGates": {"restricted": 0.5, "watched": 0.8,
                          "standard": 1.0, "trusted": 1.1},
            "combinedFloor": 0.4,
            "accelCap": 1.15,
            "note": ("on: 画像信任度回流验真通道(起点折扣+"
                     "入分守门)" if prior_mode else
                     "off: 画像只沉淀不干预(默认零影响态)"),
        }

    # --------------------------------------------------------
    # 桥接 46号公平性(tier 维度采样上报)
    # --------------------------------------------------------

    async def bridge_fairness(self) -> dict:
        """风险等级注册为公平性采样 group 维度

        只读画像层 → 按 tier 聚合(信值分均值) → 46号
        submit_samples 显式上报(group=tier 标签, 无个人
        标识字段——trustId 不出库); 幂等: 每 tier 每 round
        一条(以画像快照为准, 重复调用刷新统计不膨胀——
        46号侧样本按 scorerId 追加, 故以人工触发节奏
        控制采样频率, 与 trust45 适配器同款一次性范式)。
        """
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        from services.trust_risk_profile_service import (
            trust_level_of, tier_of,
        )
        profiles = await self.repo.list_profiles(limit=500)
        t45 = {p.get("trustId"): p for p in
               await TrustValue45Repository().list_profiles(
                   limit=5000)}
        buckets: dict = defaultdict(
            lambda: {"n": 0, "scoreSum": 0.0})
        for p in profiles:
            risk = float(p.get("riskEMA") or 0)
            override = p.get("calibrateOverride")
            effective = (float(override)
                         if override not in ("", None)
                         else trust_level_of(risk))
            tier = tier_of(effective)
            t = t45.get(p.get("trustId"))
            if t is None or t.get("score") is None:
                continue
            buckets[tier]["n"] += 1
            buckets[tier]["scoreSum"] += float(t.get("score"))
        samples = []
        for tier, b in sorted(buckets.items()):
            if b["n"] < 5:
                continue   # 46号 MIN_GROUP_SAMPLES 口径
            samples.append({
                "group": f"risk_{tier}",
                "score": round(b["scoreSum"] / b["n"], 1),
                "passed": None})
        if not samples:
            return {"success": True, "bridged": 0,
                    "note": "无有效分组(画像样本不足)"}
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        result = await AiGovernanceFairnessService(
        ).submit_samples(
            BRIDGE_SCORER_ID, samples, source="report")
        logger.info("trust47_fairness_bridged groups=%s",
                    len(samples))
        return {"success": True,
                "bridged": result.get("accepted"),
                "groups": [s["group"] for s in samples]}
