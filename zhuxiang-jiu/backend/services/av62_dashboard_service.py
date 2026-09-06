"""62号·AI智能无形资产估值 四区看板
(av62_dashboard_service, P5)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§七 P5):
    ① 度量区: 估值准确率(验证信号
       累计)+归因锚定率(ruleId 绑定
       占比)+公平达标(最新审计
       flagged)+申诉翻转率(resolved
       中 overturned 占比)+第37档案
       信任分(44号 get_weights_view
       复用)
    ② 资产区: 三角色×九资产域分布
       +负资产占比+流动性档分布
    ③ 评估区: 版本链统计+置信度
       三档分布+objective 生效态
    ④ 防御区: 红队最近一轮读回
       +off 态零影响断言

设计(纯确定性聚合——不发 LLM,
观测面不受开关影响):
    数字全部来自数据层(可溯源)。
"""

import logging
import os

from core.helpers import ts

from repositories.av62_repository import (
    Av62Repository,
)

logger = logging.getLogger("av62_dashboard")

MODEL_VERSION = "v1-av62-dashboard"

SCORER_ID = "asset_valuation"


class Av62DashboardService:
    """62号四区看板(P5——纯聚合)"""

    def __init__(self):
        self.repo = Av62Repository()

    # ============================================================
    # 看板入口(四区全量)
    # ============================================================

    async def get_dashboard(self) -> dict:
        """四区看板(观测面不受开关影响)"""
        return {
            "success": True,
            "modelVersion": MODEL_VERSION,
            "mode": os.environ.get(
                "AV62_MODE", "off"),
            "zones": {
                "metrics":
                    await self
                    ._zone_metrics(),
                "assets":
                    await self
                    ._zone_assets(),
                "assessments":
                    await self
                    ._zone_assessments(),
                "defense":
                    await self
                    ._zone_defense(),
            },
            "note": "62号四区看板——度量"
                    "/资产/评估/防御"
                    "(纯确定性聚合)",
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # ① 度量区
    # --------------------------------------------------------

    async def _zone_metrics(self) -> dict:
        """度量区: 四指标+第37档案
        信任分"""
        assessments = await self.repo \
            .list_assessments(limit=500)
        verified = [
            a for a in assessments
            if a.get("verification")]

        # ① 估值准确率
        accurate = sum(
            1 for a in verified
            if (a.get("verification")
                or {}).get("signal")
            == "within_tolerance")
        accuracy = round(
            accurate / len(verified), 4) \
            if verified else None

        # ② 归因锚定率
        grounded = sum(
            1 for a in assessments
            if a.get("ruleId"))
        grounded_rate = round(
            grounded / len(assessments),
            4) if assessments else 1.0

        # ③ 公平达标(最新报告)
        fairness = None
        reports = await self.repo \
            .list_fairness(limit=1)
        if reports:
            rep = reports[0]
            fairness = {
                "flagged":
                    rep.get(
                        "flagged"),
                "insufficient":
                    rep.get(
                        "insufficient"),
                "meanDiffRatio":
                    rep.get(
                        "meanDiffRatio"),
                "passRateGap":
                    rep.get(
                        "passRateGap"),
                "compliant": bool(
                    not rep.get(
                        "flagged")
                    and not rep.get(
                        "insufficient")),
            }

        # ④ 申诉翻转率
        appeals = await self.repo \
            .list_appeals(limit=500)
        resolved = [
            a for a in appeals
            if a.get("status")
            == "resolved"]
        overturned = [
            a for a in resolved
            if a.get("overturned")]
        overturn_rate = round(
            len(overturned)
            / len(resolved), 4) \
            if resolved else 0.0

        # ⑤ 第37档案信任分
        scorer_view = None
        try:
            from services.ai_learning_service import (
                get_weights_view,
            )
            view = await (
                get_weights_view(
                    SCORER_ID))
            scorer_view = {
                "champion":
                    (view.get(
                        "champion")
                     or {}).get(
                         "version"),
                "trustScore":
                    view.get(
                        "trustScore"),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_dash_scorer_failed: %s",
                exc)

        return {
            "valuationAccuracy":
                accuracy,
            "attributionGrounded":
                grounded_rate,
            "fairness": fairness,
            "appealOverturnRate":
                overturn_rate,
            "scorer": scorer_view,
            "verifiedCount":
                len(verified),
            "assessedCount":
                len(assessments),
        }

    # --------------------------------------------------------
    # ② 资产区
    # --------------------------------------------------------

    async def _zone_assets(self) -> dict:
        """资产区: 三角色×九域+负资产
        +流动性档分布"""
        assets = await self.repo.list_assets(
            limit=1000)
        by_role: dict = {}
        by_domain: dict = {}
        by_liquidity: dict = {}
        by_status: dict = {}
        negative = 0
        from services.av62_registry import (
            liquidity_of,
        )
        for a in assets:
            role = a.get("role") or "?"
            domain = a.get("domain") or "?"
            by_role[role] = \
                by_role.get(role, 0) + 1
            by_domain[domain] = \
                by_domain.get(domain, 0) + 1
            tier = liquidity_of(domain)
            by_liquidity[tier] = \
                by_liquidity.get(
                    tier, 0) + 1
            status = a.get("status") or "?"
            by_status[status] = \
                by_status.get(status, 0) + 1
            if a.get("negative"):
                negative += 1
        return {
            "total": len(assets),
            "byRole": by_role,
            "byDomain": by_domain,
            "byLiquidity":
                by_liquidity,
            "byStatus": by_status,
            "negativeCount": negative,
        }

    # --------------------------------------------------------
    # ③ 评估区
    # --------------------------------------------------------

    async def _zone_assessments(self) -> dict:
        """评估区: 版本链+置信度三档
        +objective 生效态"""
        assessments = await self.repo \
            .list_assessments(limit=500)
        by_tier: dict = {}
        versions = 0
        for a in assessments:
            tier = a.get(
                "confidenceTier") or "?"
            by_tier[tier] = \
                by_tier.get(tier, 0) + 1
            versions = max(
                versions,
                int(a.get("version")
                    or 0))
        objective = "stability"
        try:
            rec = await self.repo \
                .get_threshold(
                    "objective")
            if rec \
                    and rec.get(
                        "status") \
                    == "applied":
                objective = (rec
                             .get("config")
                             or {}).get(
                                 "objective",
                                 "stability")
        except Exception:  # noqa: BLE001
            pass
        return {
            "total": len(assessments),
            "byConfidence": by_tier,
            "maxVersionChain": versions,
            "objective": objective,
            "pooled": sum(
                1 for a in assessments
                if a.get("pooled")),
        }

    # --------------------------------------------------------
    # ④ 防御区
    # --------------------------------------------------------

    async def _zone_defense(self) -> dict:
        """防御区: 红队最近一轮读回
        +off 态零影响断言"""
        events = await self.repo \
            .list_events(limit=50)
        redteam_runs = [
            e for e in events
            if e.get("eventType")
            == "redteam_run"]
        latest = redteam_runs[0] \
            if redteam_runs else None
        return {
            "redteamLatest": (
                latest.get("detail")
                if latest else None),
            "redteamRuns":
                len(redteam_runs),
            "modeOffAssertion":
                os.environ.get(
                    "AV62_MODE", "off")
                == "off",
            "note": "防御区——红队最近"
                    "一轮读回+off 态"
                    "零影响断言",
        }
