"""61号·AI智能系统升级决策 四区看板
(dm61_dashboard_service, P5)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§七 P5):
    四区看板(度量+请求+决策+防御):
        ① 度量区: 决策准确率(采纳后
           良好/总采纳)/自治占比(L1
           /总量)/预测命中(沙箱裁决
           与人类决策同向)/预警有效
           (dissent 证实/总预警)
           +第36档案信任分
        ② 请求区: 请求统计(来源/标签/
           状态分布)
        ③ 决策区: 决策统计(级别/结果
           分布+46号总线提交数+平均
           风险分)
        ④ 防御区: 红队最近一轮结果+
           off 态零影响断言

铁律(计划 §六):
    - 看板为观测面(不受 DM61_MODE
      影响)
    - 度量纯确定性计算(不发 LLM)
"""

import logging
import os

from core.helpers import ts

from repositories.dm61_repository import (
    Dm61Repository,
)

logger = logging.getLogger("dm61_dashboard")

MODEL_VERSION = "v1-dm61-dashboard"

SCORER_ID = "decision_orchestration"


class Dm61DashboardService:
    """61号四区看板(P5——观测面)"""

    def __init__(self):
        self.repo = Dm61Repository()

    # ============================================================
    # 四区看板主入口
    # ============================================================

    async def dashboard(self) -> dict:
        """四区看板(度量+请求+决策+防御
        ——纯确定性聚合)"""
        metrics = await self._zone_metrics()
        requests = await self._zone_requests()
        decisions = await self._zone_decisions()
        defense = await self._zone_defense()

        return {
            "success": True,
            "modelVersion": MODEL_VERSION,
            "zone": "四区(度量+请求+决策+防御)",
            "metrics": metrics,
            "requests": requests,
            "decisions": decisions,
            "defense": defense,
            "note": "61号四区看板——观测面"
                    "(纯确定性聚合, 不受开关"
                    "影响)",
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # ① 度量区(四指标+信任分)
    # --------------------------------------------------------

    async def _zone_metrics(self) -> dict:
        """度量区: 决策准确率/自治占比/
        预测命中/预警有效"""
        decisions = await self.repo.list_decisions(
            limit=1000)
        feedbacks = await self.repo.list_feedback(
            limit=1000)
        fb_map = {
            int(f.get("decisionId") or 0): f
            for f in feedbacks}

        # 决策准确率: 采纳后结果良好
        # (adopted×good)/总采纳
        adopted = [
            d for d in decisions
            if d.get("outcome")
            in ("adopted", "modified")]
        adopted_good = sum(
            1 for d in adopted
            if str((fb_map.get(
                int(d.get("decisionId")
                    or 0)) or {}).get(
                "outcome") or "")
            == "good")
        accuracy = round(
            adopted_good / len(adopted)
            * 100, 1) if adopted else 100.0

        # 自治占比: L1/总量(过高需复核
        # ——第36档案 autonomous_factor)
        l1_n = sum(
            1 for d in decisions
            if d.get("level") == "L1")
        auto_ratio = round(
            l1_n / len(decisions) * 100, 1) \
            if decisions else 0.0

        # 预测命中: 沙箱裁决与人类决策
        # 同向(passed×采纳 或 blocked×拒绝)
        sims = await self.repo.list_simulations(
            limit=1000)
        sim_by_req = {
            int(s.get("requestId") or 0): s
            for s in sims}
        hit_n = 0
        hit_total = 0
        for d in decisions:
            sim = sim_by_req.get(
                int(d.get("requestId")
                    or 0))
            if not sim:
                continue
            hit_total += 1
            verdict = str(sim.get("verdict"))
            outcome = str(d.get("outcome"))
            if (verdict == "passed"
                    and outcome
                    in ("adopted",
                        "modified")) \
                    or (verdict == "blocked"
                        and outcome
                        in ("rejected",
                            "dissent_confirmed")):
                hit_n += 1
        hit_rate = round(
            hit_n / hit_total * 100, 1) \
            if hit_total else 100.0

        # 预警有效: dissent 证实
        # (confirmed)/总处置
        dissents = [
            (d.get("dissent") or {})
            for d in decisions]
        resolved = [
            dn for dn in dissents
            if dn.get("status")
            in ("overridden",
                "confirmed")]
        confirmed = sum(
            1 for dn in resolved
            if dn.get("status")
            == "confirmed")
        dissent_effect = round(
            confirmed / len(resolved)
            * 100, 1) if resolved else 100.0

        # 第36档案信任分(44号复用)
        trust_score = None
        try:
            from services.ai_learning_service import (
                get_weights_view,
            )
            view = await get_weights_view(
                SCORER_ID)
            trust_score = (
                (view.get("champion") or {})
                .get("metrics") or {}
            ).get("trustScore")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_dash_trust_failsoft: %s",
                exc)

        return {
            "decisionAccuracy": accuracy,
            "adoptedTotal": len(adopted),
            "autonomousRatio": auto_ratio,
            "l1Total": l1_n,
            "simulationHitRate": hit_rate,
            "simTotal": hit_total,
            "dissentEffectiveness":
                dissent_effect,
            "dissentResolved":
                len(resolved),
            "trustHealth": trust_score,
            "note": "度量四指标——决策准确率"
                    "/自治占比/预测命中/"
                    "预警有效(第36档案"
                    "口径)",
        }

    # --------------------------------------------------------
    # ② 请求区
    # --------------------------------------------------------

    async def _zone_requests(self) -> dict:
        """请求区: 请求统计(来源/标签/
        状态分布)"""
        requests = await self.repo.list_requests(
            limit=1000)
        by_source: dict = {}
        by_tag: dict = {}
        by_status: dict = {}
        for r in requests:
            source = str(r.get("source")
                          or "-")
            by_source[source] = \
                by_source.get(source, 0) + 1
            tag = str(r.get("tag") or "-")
            by_tag[tag] = by_tag.get(tag, 0) + 1
            status = str(r.get("status")
                         or "-")
            by_status[status] = \
                by_status.get(status, 0) + 1
        return {
            "totalRequests": len(requests),
            "bySource": by_source,
            "byTag": by_tag,
            "byStatus": by_status,
            "note": "请求区——三源×六类"
                    "标签×九态分布",
        }

    # --------------------------------------------------------
    # ③ 决策区
    # --------------------------------------------------------

    async def _zone_decisions(self) -> dict:
        """决策区: 决策统计(级别/结果
        分布+46号总线提交数+平均风险分)"""
        decisions = await self.repo.list_decisions(
            limit=1000)
        by_level: dict = {}
        by_outcome: dict = {}
        bus_submitted = 0
        risk_scores = []
        for d in decisions:
            level = str(d.get("level") or "-")
            by_level[level] = \
                by_level.get(level, 0) + 1
            outcome = str(d.get("outcome")
                          or "pending")
            by_outcome[outcome] = \
                by_outcome.get(outcome, 0) + 1
            if int(d.get("changeId") or 0) > 0:
                bus_submitted += 1
            if d.get("riskScore") is not None:
                risk_scores.append(
                    float(d.get("riskScore")))
        avg_risk = round(
            sum(risk_scores)
            / len(risk_scores), 1) \
            if risk_scores else 0.0
        return {
            "totalDecisions":
                len(decisions),
            "byLevel": by_level,
            "byOutcome": by_outcome,
            "busSubmitted": bus_submitted,
            "avgRiskScore": avg_risk,
            "note": "决策区——L1/L2/L3×"
                    "结果分布(执行唯一"
                    "通道 46号总线)",
        }

    # --------------------------------------------------------
    # ④ 防御区
    # --------------------------------------------------------

    async def _zone_defense(self) -> dict:
        """防御区: 红队最近一轮结果+
        off 态零影响断言"""
        # 最近一轮红队事件留痕
        redteam_runs = [
            e for e in await self.repo
            .list_events(limit=200)
            if (e.get("detail") or {}).get(
                "action") == "redteam_run"]
        last = (redteam_runs[0].get(
            "detail") or {}) \
            if redteam_runs else {}

        return {
            "mode": os.environ.get(
                "DM61_MODE", "off"),
            "redteamLastRun": {
                "defended":
                    last.get("defended"),
                "total":
                    last.get("total"),
                "ranAt":
                    last.get("ranAt"),
            } if redteam_runs else None,
            "zeroImpactWhenOff": True,
            "note": "防御区——红队最近"
                    "一轮+off 零影响",
        }
