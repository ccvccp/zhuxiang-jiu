"""47号·信值验真风控中枢统一调度(批次七·全站融合收官)

定位:
    47号是全站"信值风险中枢"——riskEMA 画像 + tier 分层被
    12+ 模块被动消费(pull 模式: 各消费方 fail-soft 只读
    get_profile)。本服务把分散的被动消费聚合成一张
    "中枢统一调度面", 并提供一键式统一调度(串联画像分布
    扫描 + P2 协同扫描 + 46号公平性桥接 + 44号学习域摘要)。

三大能力:
    ① 消费方注册表(CONSUMERS): 哪些模块在消费 47号 tier、
       消费方式(同步前置查/被动读取/回流沉淀)、场景说明——
       全站融合的"信值轴心"实证清单
    ② 中枢总览(hub_overview): tier 分层分布 + 消费方全景 +
       三大联动子系统状态(45号信值档案/44号学习域/46号治理)
       ——全部 fail-soft, 单源异常不阻断总览
    ③ 统一调度(dispatch): 一键串联画像分布扫描 → P2 协同
       扫描 → 46号公平性桥接 → 44号学习域摘要, 聚合返回
       (画像不处罚红线不变——dispatch 只扫描+桥接+留痕,
       零自动处置)

设计红线(继承 47号 P0-P4):
    - 画像不处罚: dispatch 零自动处置, 嫌疑仅标记
    - 只读零侵入: 消费方状态均为静态注册 + 运行时 fail-soft
      读取, 不反向写入任何消费方模块
    - 单源异常 fail-soft: 任何联动子系统读取失败只留
      {"error": ...}, 不阻断调度面
"""

import logging

from core.helpers import ts

from repositories.trust_risk_repository import (
    TrustRisk47Repository,
)

logger = logging.getLogger(__name__)

# ============================================================
# ① 消费方注册表(全站融合实证——勘察批次七时从代码提取)
# ============================================================

# 消费方式:
#   sync_gate   同步前置查(业务入口同步调 47号画像做 tier 摩擦)
#   passive     被动读取(业务流程中 fail-soft 读画像分层)
#   feedback    回流沉淀(业务终态 → record_risk_event 画像沉淀)

CONSUMERS = [
    {"module": "64号·信值消费风控", "consumer": "xx64_risk_service",
     "channel": "sync_gate",
     "scene": "支付/积分兑换前置查(tier 摩擦 0.6~1.2)"},
    {"module": "60号·支付体验", "consumer": "pay60_risk_service",
     "channel": "passive",
     "scene": "支付风控 tier 读取(信任分层)"},
    {"module": "60号·支付体验", "consumer": "pay60_checkout_service",
     "channel": "passive", "scene": "结账流程 tier 读取"},
    {"module": "63号·AB实验", "consumer": "ab63_submission_service",
     "channel": "passive", "scene": "实验提交 tier 分层"},
    {"module": "63号·AB实验", "consumer": "ab63_service",
     "channel": "passive", "scene": "实验配置 tier 分层"},
    {"module": "61号·人机协同决策", "consumer": "dm61_assess_service",
     "channel": "passive", "scene": "裁决评估 tier 分层"},
    {"module": "59号·智能搜索", "consumer": "ii59_search_service",
     "channel": "passive", "scene": "搜索分层 tier 读取"},
    {"module": "58号·智能信息", "consumer": "ii58_service",
     "channel": "passive", "scene": "信息流 tier 分层"},
    {"module": "50号·语音助手", "consumer": "xiaozhu_voice50_service",
     "channel": "passive", "scene": "反欺诈问询配合分"},
    {"module": "65号·内容经营", "consumer": "xx65_service",
     "channel": "passive", "scene": "开店准入画像读取"},
    {"module": "46号·信值雷达", "consumer": "trust_radar_service",
     "channel": "feedback",
     "scene": "P3 入分守门(tier 乘性修正, RISK_PRIOR_MODE)"},
    {"module": "23号·信用底座", "consumer": "trust_scoring_service",
     "channel": "feedback", "scene": "信用事件画像回流"},
    {"module": "47号·修复通道", "consumer": "trust_repair_service",
     "channel": "feedback", "scene": "修复提交画像回流"},
]

CHANNEL_LABELS = {"sync_gate": "同步前置查", "passive": "被动读取",
                  "feedback": "回流沉淀"}


class TrustHubService:
    """信值验真风控中枢统一调度(批次七融合面)"""

    def __init__(self,
                 repo: TrustRisk47Repository = None):
        self.repo = repo or TrustRisk47Repository()

    # --------------------------------------------------------
    # ② 中枢总览(消费方全景 + tier 分布 + 三联动子系统)
    # --------------------------------------------------------

    async def hub_overview(self) -> dict:
        """中枢统一调度总览(fail-soft 分区)"""
        zones = {}

        async def _zone(name, fn):
            try:
                zones[name] = await fn()
            except Exception as exc:
                logger.warning("trust47_hub_zone_%s_failsoft: %s",
                               name, exc)
                zones[name] = {"error": str(exc)[:120]}

        await _zone("tiers", self._zone_tier_distribution)
        await _zone("linkage45", self._zone_linkage_value45)
        await _zone("linkage44", self._zone_linkage_learning)
        await _zone("linkage46", self._zone_linkage_governance)
        zones["consumers"] = self._zone_consumers()

        by_channel: dict[str, int] = {}
        for c in CONSUMERS:
            by_channel[c["channel"]] = \
                by_channel.get(c["channel"], 0) + 1
        return {
            "success": True,
            "module": "trust-hub-47",
            "generatedAt": ts(),
            "consumerCount": len(CONSUMERS),
            "consumerChannels": {
                k: {"label": CHANNEL_LABELS.get(k, k), "count": v}
                for k, v in by_channel.items()},
            "zones": zones,
            "meta": {
                "position": "信值轴心——12+ 模块被动消费 tier 画像",
                "redline": "画像不处罚; dispatch 零自动处置",
                "priorMode": self._prior_mode(),
            },
        }

    async def _zone_tier_distribution(self) -> dict:
        """tier 分层分布(读画像档案, 47号自身 P0 数据)"""
        profiles = await self.repo.list_profiles(limit=1000)
        dist: dict[str, int] = {}
        watched_top = []
        for p in profiles:
            tier = p.get("tier") or "standard"
            dist[tier] = dist.get(tier, 0) + 1
            if tier in ("watched", "restricted"):
                watched_top.append({
                    "trustId": p.get("trustId"),
                    "trustLevel": p.get("trustLevel"),
                    "tier": tier,
                    "riskEMA": p.get("riskEMA"),
                })
        watched_top.sort(key=lambda x: x.get("riskEMA") or 0,
                         reverse=True)
        return {
            "totalProfiles": len(profiles),
            "distribution": dist,
            "watchedList": watched_top[:10],
        }

    def _zone_consumers(self) -> dict:
        """消费方注册表(静态清单——勘察实证)"""
        return {"total": len(CONSUMERS), "list": CONSUMERS}

    async def _zone_linkage_value45(self) -> dict:
        """45号信值总账联动(档案量级摘要, 只读)"""
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        profiles = await TrustValue45Repository() \
            .list_profiles(limit=1000)
        return {"linkage": "45号·信值总账",
                "totalProfiles": len(profiles or [])}

    async def _zone_linkage_learning(self) -> dict:
        """44号学习域联动(评分器数 + 模式分布摘要, 只读)"""
        from services.ai_learning_service import (
            SCORER_REGISTRY, panorama,
        )
        p = await panorama()
        return {
            "linkage": "44号·AI学习域",
            "scorerCount": len(SCORER_REGISTRY),
            "modeDistribution": p.get("modeDistribution", {}),
            "health": p.get("health", {}),
        }

    async def _zone_linkage_governance(self) -> dict:
        """46号治理台账联动(档案状态分布摘要, 只读)"""
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        reg = await AiGovernanceService().list_registry()
        return {"linkage": "46号·AI治理台账",
                "byStatus": reg.get("byStatus", {}),
                "byBatch": reg.get("byBatch", {})}

    def _prior_mode(self) -> bool:
        from services.trust_risk_profile_service import (
            prior_mode_enabled,
        )
        return prior_mode_enabled()

    # --------------------------------------------------------
    # ③ 统一调度(一键式: 分布扫描→协同→公平桥接→学习摘要)
    # --------------------------------------------------------

    async def dispatch(self) -> dict:
        """统一调度执行(fail-soft 分步, 零自动处置)

        调度序列(画像不处罚红线不变):
            step1 画像分布扫描(P0 分层统计)
            step2 P2 协同扫描(互证对+指纹共享——只标记)
            step3 46号公平性桥接(tier 维度上报)
            step4 44号学习域摘要(全站融合观测)
        """
        steps = {}

        async def _step(name, fn):
            try:
                steps[name] = await fn()
            except Exception as exc:
                logger.warning("trust47_hub_step_%s_failsoft: %s",
                               name, exc)
                steps[name] = {"error": str(exc)[:120]}

        await _step("scan_tiers", self._zone_tier_distribution)

        async def _collusion():
            from services.trust_risk_collusion_service import (
                TrustRiskCollusionService,
            )
            return await TrustRiskCollusionService().scan()
        await _step("collusion_scan", _collusion)

        async def _fairness():
            from services.trust_risk_dashboard_service import (
                TrustRiskDashboardService,
            )
            return await TrustRiskDashboardService() \
                .bridge_fairness()
        await _step("fairness_bridge", _fairness)

        async def _learning():
            from services.ai_learning_service import panorama
            p = await panorama()
            return {"scorerCount": p.get("scorerCount"),
                    "modeDistribution": p.get("modeDistribution"),
                    "health": p.get("health")}
        await _step("learning_summary", _learning)

        ok_steps = sum(1 for v in steps.values()
                       if not (isinstance(v, dict) and "error" in v))
        return {
            "success": True,
            "module": "trust-hub-47",
            "dispatchedAt": ts(),
            "steps": steps,
            "stepTotal": len(steps),
            "stepOk": ok_steps,
            "note": "统一调度零自动处置(画像不处罚红线)——"
                    "只扫描+桥接+留痕",
        }
