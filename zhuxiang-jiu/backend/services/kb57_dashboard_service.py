"""57号·AI智能知识库 四区看板
(kb57_dashboard_service, P5)

计划(docs/57号_AI智能知识库模块实施计划.md §九 P5):
    四区看板(度量区五指标+种子库+合规区+防御区)

四区(52/55/56号 dashboard 范式——纯数据层聚合):
    ① metrics    度量区: 五指标(知识覆盖率/
                合规通过率/种子有效率/学习转化率
                /信值增益总量——计划 §〇 运营度量)
    ② seeds      种子库区: 八态分布+类型分布
                +版本累计+浏览/反馈计数
    ③ compliance 合规区: 鉴别 verdict 分布+
                三关拦截计数+脱敏统计
    ④ defense    防御区: 召回计数+45号补偿落账
                +预算熔断+回流信号分布+第32档案
                护栏健康+源集中度(投毒防御)

设计约定:
    - 纯读取式聚合(无落库副作用)
    - 数字全部来自 kb57 九表+44号视图
    - 观测面(不受 KB57_MODE 影响)
"""

import logging
from datetime import datetime

from core.helpers import ts

from repositories.kb57_repository import (
    Kb57Repository,
)

logger = logging.getLogger(
    "kb57_dashboard_service")

MODEL_VERSION = "v1-kb57-dashboard"

SCORER_ID = "knowledge_orchestration"


class Kb57DashboardService:
    """57号四区看板(度量/种子库/合规/防御)"""

    def __init__(self):
        self.repo = Kb57Repository()

    # ============================================================
    # 看板入口
    # ============================================================

    async def build(self) -> dict:
        """构建四区看板(观测面——GET /dashboard)"""
        gaps = await self.repo.list_gaps(limit=10000)
        resources = await self.repo.list_resources(
            limit=10000)
        seeds = await self.repo.list_seeds(
            limit=10000)
        compliance = await \
            self.repo.list_compliance(limit=10000)
        feedback = await self.repo.list_feedback(
            limit=10000)
        paths = await self.repo.list_paths(limit=10000)
        pushes = await self.repo.list_pushes(
            limit=10000)

        return {
            "success": True,
            "modelVersion": MODEL_VERSION,
            "zones": {
                "metrics": self._zone_metrics(
                    gaps, resources, seeds,
                    feedback, paths),
                "seeds": self._zone_seeds(seeds),
                "compliance": self._zone_compliance(
                    compliance),
                "defense": await self._zone_defense(
                    gaps, resources, seeds),
            },
            "note": "四区看板——度量五指标/种子库/"
                    "合规/防御(52号范式纯数据层聚合)",
            "generatedAt": ts(),
        }

    # ============================================================
    # ① 度量区(五指标——计划 §〇 运营效果度量)
    # ============================================================

    @staticmethod
    def _zone_metrics(gaps: list, resources: list,
                     seeds: list, feedback: list,
                     paths: list) -> dict:
        """度量区(知识覆盖率/合规通过率/种子有效率
        /学习转化率/信值增益总量)"""
        # ① 知识覆盖率(已解决缺口占比)
        total_gaps = len(gaps)
        resolved = sum(
            1 for g in gaps
            if g.get("status") == "resolved")
        coverage = (round(resolved / total_gaps, 4)
                    if total_gaps else None)

        # ② 合规通过率(passed 占已鉴别比重)
        total_compliance = len(resources)
        compliant = sum(
            1 for r in resources
            if r.get("status") == "compliant")
        pass_rate = (round(compliant / total_compliance,
                           4)
                     if total_compliance else None)

        # ③ 种子有效率(有正反馈的发布态种子占比)
        published = [
            s for s in seeds
            if s.get("status") in (
                "published", "boosted", "downgraded",
                "retired", "recalled")]
        effective = sum(
            1 for s in published
            if int(s.get("positiveCount") or 0)
            > int(s.get("negativeCount") or 0))
        effective_rate = (
            round(effective / len(published), 4)
            if published else None)

        # ④ 学习转化率(完成路径占创建路径比)
        total_paths = len(paths)
        completed = sum(
            1 for p in paths
            if p.get("completed") is True)
        conversion = (round(
            completed / total_paths, 4)
            if total_paths else None)

        # ⑤ 信值增益总量(正向反馈-负向反馈净计数
        # ——45号结算 P4 联动近似)
        positive = sum(
            1 for f in feedback
            if f.get("kind") == "positive")
        negative = sum(
            1 for f in feedback
            if f.get("kind") == "negative")
        value_gain = positive - negative

        return {
            "coverageRate": coverage,
            "compliancePassRate": pass_rate,
            "seedEffectiveRate": effective_rate,
            "learningConversionRate": conversion,
            "valueGainTotal": value_gain,
            "basis": {
                "gaps": total_gaps,
                "resolved": resolved,
                "resources": total_compliance,
                "compliant": compliant,
                "published": len(published),
                "effective": effective,
                "paths": total_paths,
                "completed": completed,
                "positive": positive,
                "negative": negative,
            },
            "note": "度量区——五指标(覆盖率/通过率"
                    "/有效率/转化率/信值增益)",
        }

    # ============================================================
    # ② 种子库区
    # ============================================================

    @staticmethod
    def _zone_seeds(seeds: list) -> dict:
        """种子库区(八态分布+类型分布+版本累计
        +浏览/反馈计数)"""
        by_status: dict = {}
        by_type: dict = {}
        version_sum = 0
        views = 0
        positive = 0
        negative = 0
        for s in seeds:
            status = str(
                s.get("status") or "unknown")
            by_status[status] = \
                by_status.get(status, 0) + 1
            seed_type = str(
                s.get("type") or "unknown")
            by_type[seed_type] = \
                by_type.get(seed_type, 0) + 1
            version_sum += int(
                s.get("seedVersion") or 0)
            views += int(s.get("viewCount") or 0)
            positive += int(
                s.get("positiveCount") or 0)
            negative += int(
                s.get("negativeCount") or 0)

        return {
            "totalSeeds": len(seeds),
            "byStatus": by_status,
            "byType": by_type,
            "versionSum": version_sum,
            "viewCount": views,
            "positiveCount": positive,
            "negativeCount": negative,
            "note": "种子库——八态分布+多模态类型"
                    "+版本化累计+使用计数",
        }

    # ============================================================
    # ③ 合规区
    # ============================================================

    @staticmethod
    def _zone_compliance(compliance: list) -> dict:
        """合规区(鉴别 verdict 分布+三关拦截计数
        +脱敏统计)"""
        by_verdict: dict = {}
        copyright_blocks = 0
        safety_blocks = 0
        masked_total = 0
        budget_halts = 0
        for c in compliance:
            verdict = str(
                c.get("verdict") or "unknown")
            by_verdict[verdict] = \
                by_verdict.get(verdict, 0) + 1
            # 版权关拦截
            copyright_gate = c.get("copyright") or {}
            if verdict == "blocked" \
                    and (copyright_gate.get(
                        "violations") or []):
                copyright_blocks += 1
            # 内容安全关拦截
            safety = c.get("contentSafety") or {}
            if (safety.get("riskLevel")
                    in ("high",)):
                safety_blocks += 1
            # 脱敏统计
            masked_total += len(
                c.get("maskedFields") or [])
            # 预算熔断
            gate = c.get("gate") or {}
            if gate.get("halted"):
                budget_halts += 1

        return {
            "totalReports": len(compliance),
            "byVerdict": by_verdict,
            "copyrightBlocks": copyright_blocks,
            "safetyBlocks": safety_blocks,
            "maskedFieldsTotal": masked_total,
            "budgetHalts": budget_halts,
            "note": "合规区——三关拦截+脱敏统计"
                    "(版权/内容安全/预算)",
        }

    # ============================================================
    # ④ 防御区
    # ============================================================

    async def _zone_defense(self, gaps: list,
                            resources: list,
                            seeds: list) -> dict:
        """防御区(召回+补偿+预算熔断+回流信号
        分布+护栏健康+源集中度)"""
        recalls = 0
        comp_attempted = 0
        comp_compensated = 0
        by_signal: dict = {}
        for s in seeds:
            if s.get("status") == "recalled":
                recalls += 1
            src = str(s.get("poolSignal") or "")
            if src:
                by_signal[src] = \
                    by_signal.get(src, 0) + 1

        # 资源域预算熔断计数
        budget_halts = sum(
            1 for r in resources
            if r.get("budgetHalted") is True)

        # 44号池提交计数(第32档案)
        pool_submitted = sum(
            1 for s in seeds
            if int(s.get("pooledFeedbackId")
                   or 0) > 0)
        gap_pooled = sum(
            1 for g in gaps
            if g.get("gapRecurrence"))

        # 源集中度(采集投毒防御——
        # 54/55/56号同款口径)
        by_source: dict = {}
        for s in seeds:
            source = str(
                s.get("sourceId") or "unknown")
            by_source[source] = \
                by_source.get(source, 0) + 1
        total_sources = sum(by_source.values())
        top_source, top_ratio = None, 0.0
        if total_sources:
            top_source, top_count = max(
                by_source.items(),
                key=lambda kv: kv[1])
            top_ratio = round(
                top_count / total_sources, 4)

        # 护栏健康(第32档案 champion 权重
        # [0.5,2.0] 倍——44号引擎内建)
        guard_healthy = None
        champion_version = None
        try:
            from services.kb57_scorer import (
                Kb57Scorer,
            )
            from services.ai_learning_service import (
                get_weights_view,
            )
            view = await get_weights_view(SCORER_ID)
            champion = view.get("champion") or {}
            champion_version = \
                champion.get("version")
            weights = champion.get("weights") or {}
            if weights:
                guard_healthy = all(
                    Kb57Scorer.WEIGHTS[k] / 2.0
                    <= float(weights.get(k, 0))
                    <= Kb57Scorer.WEIGHTS[k] * 2.0
                    for k in Kb57Scorer.WEIGHTS)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_dash_guard_failed: %s", exc)

        return {
            "recalls": recalls,
            "budgetHalts": budget_halts,
            "feedbackSignals": {
                "bySignal": by_signal,
                "poolSubmitted":
                    pool_submitted + gap_pooled,
            },
            "sourceConcentration": {
                "bySource": by_source,
                "topSource": top_source,
                "topRatio": top_ratio,
                "alert": top_ratio > 0.8,
                "threshold": 0.8,
            },
            "guardrail": {
                "healthy": guard_healthy,
                "championVersion": champion_version,
                "bounds": "[0.5,2.0]×基线",
                "note": "第32档案 champion 权重护栏"
                        "(44号引擎内建)",
            },
            "note": "防御区——召回+预算熔断+回流分布"
                    "+源集中度+护栏",
        }
