"""62号·AI智能无形资产估值 公平性审计
(av62_fairness_service, P3)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§3.4/§七 P3):
    分角色/群体估值分布差异检测
    (46号 fairness 口径复用——
    均值差/通过率差双指标)
    +超阈告警(仅标记不下结论
    ——人工复核)

    采样源(内部派生——不新增采集面):
        每资产最新评估记录 →
        {group: role, score: baseValue,
         passed: assetStatus=="active"}

    指标(46号 ai_governance_fairness
    compute_metrics 纯函数零改动复用):
        ① 均值差异比 = max|mean_g- mean_all|
           / max(mean_all, 1) → 阈值 20%
        ② 通过率差(max-min, 百分点)
           → 阈值 15pp
        空群体/小样本跳过(误报防线)

铁律(计划 §1.3/§八):
    - flagged 仅标记不下结论
      (公平性误报防线)
    - 数字来自数据层(可溯源)
    - 观测面不受开关影响
"""

import logging

from core.helpers import ts

from repositories.av62_repository import (
    Av62Repository,
)

logger = logging.getLogger("av62_fairness")

MODEL_VERSION = "v1-av62-fairness"

SCORER_ID = "asset_valuation"


class Av62FairnessService:
    """62号公平性审计(P3——46号口径
    零改动复用)"""

    def __init__(self):
        self.repo = Av62Repository()

    # ============================================================
    # 采样派生(内部——每资产最新评估)
    # ============================================================

    async def _derive_samples(self) -> list:
        """派生采样(每资产最新评估——
        group=role/score=baseValue/
        passed=active 生效)"""
        assets = await self.repo.list_assets(
            limit=500)
        samples = []
        for asset in assets:
            latest = await self.repo \
                .list_assessments(
                    asset_id=int(
                        asset.get("assetId")),
                    limit=1)
            if not latest:
                continue
            score = latest[0].get("baseValue")
            if score is None:
                continue
            samples.append({
                "group": asset.get("role"),
                "score": round(
                    float(score), 1),
                "passed": bool(
                    asset.get("status")
                    == "active"),
            })
        return samples

    # ============================================================
    # 审计触发(指标计算→落库→告警留痕)
    # ============================================================

    async def run_audit(self,
                        triggered_by: str = "admin"
                        ) -> dict:
        """触发公平性审计(分角色估值
        分布——46号双指标+超阈告警)

        Returns:
            {success, reportId, metrics,
             flagged, conclusion, ...}
        """
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        samples = await self._derive_samples()
        metrics = (
            AiGovernanceFairnessService
            .compute_metrics(samples))

        report_id = await \
            self.repo.next_report_id()
        record = {
            "reportId": report_id,
            "scorerId": SCORER_ID,
            "triggeredBy": str(
                triggered_by or "admin"),
            "generatedAt": ts(),
            **metrics,
        }
        await self.repo.save_fairness(record)

        # 超阈告警留痕(仅标记——
        # 不下结论, 人工复核)
        if metrics.get("flagged"):
            await self._track(
                "fairness_alert", {
                    "reportId": report_id,
                    "meanDiffRatio":
                        metrics.get(
                            "meanDiffRatio"),
                    "passRateGap":
                        metrics.get(
                            "passRateGap"),
                    "conclusion":
                        metrics.get(
                            "conclusion"),
                })
            logger.warning(
                "av62_fairness_flagged report=%s "
                "meanDiff=%s passGap=%s",
                report_id,
                metrics.get("meanDiffRatio"),
                metrics.get("passRateGap"))
        return {
            "success": True,
            "reportId": report_id,
            "scorerId": SCORER_ID,
            "triggeredBy": triggered_by,
            **metrics,
            "note": "分角色估值分布审计——"
                    "46号 fairness 口径"
                    "(均值差/通过率差双指标"
                    "——flagged 仅标记"
                    "请人工复核)",
            "generatedAt": record[
                "generatedAt"],
        }

    # ============================================================
    # 观测面
    # ============================================================

    async def get_report(self) -> dict:
        """最新审计报告(阈值+分组统计
        ——观测面不受开关影响)"""
        from services.ai_governance_fairness import (
            MEAN_DIFF_RATIO_THRESHOLD,
            MIN_GROUP_SAMPLES, MIN_SAMPLES,
            PASS_RATE_GAP_THRESHOLD,
        )
        records = await self.repo \
            .list_fairness(limit=10)
        report = records[0] \
            if records else None
        return {
            "success": True,
            "report": report,
            "historyCount": len(records),
            "thresholds": {
                "meanDiffRatio":
                    MEAN_DIFF_RATIO_THRESHOLD,
                "passRateGap":
                    PASS_RATE_GAP_THRESHOLD,
                "minSamples": MIN_SAMPLES,
                "minGroupSamples":
                    MIN_GROUP_SAMPLES,
            },
            "note": "公平性审计报告——分角色"
                    "估值分布(触发审计见 "
                    "P5 看板/手动)",
            "fetchedAt": ts(),
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "assetId": 0,
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_track_failed %s: %s",
                event_type, exc)
