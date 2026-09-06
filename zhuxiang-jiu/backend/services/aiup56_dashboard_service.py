"""56号·AI智能升级管理 四区看板
(aiup56_dashboard_service, P5)

计划(docs/56号_AI智能升级管理模块实施计划.md §九 P5):
    四区看板(提案漏斗/资产产出/审计合规/回滚防御)

四区(52/55号 dashboard 范式——纯数据层聚合):
    ① funnel      提案漏斗区: 九态状态分布+阶段
                  转化+决策分布(defer/propose/escalate)
    ② assets      资产产出区: 资产数/版本/drafts/
                  VALUE_REASONs/llmCalls 计量+沙箱
                  verdict 分布
    ③ compliance  审计合规区: 审计 verdict 分布+
                  一票否决计数+审批结果+强制确认
                  完整率+escalate 双人复核
    ④ defense     回滚防御区: 回滚计数+45号补偿
                  落账+预算熔断+七类回流信号分布
                  +标注源集中度(投毒防御)+护栏健康
                  (第31档案 champion 权重 [0.5,2.0] 倍)

设计约定:
    - 纯读取式聚合(无落库副作用)
    - 数字全部来自 aiup56 六表+44号视图
      (零外部依赖)
    - 观测面(不受 AIUP56_MODE 影响)
"""

import logging

from core.helpers import ts

from repositories.aiup56_repository import (
    Aiup56Repository,
)

logger = logging.getLogger("aiup56_dashboard_service")

MODEL_VERSION = "v1-aiup56-dashboard"

SCORER_ID = "upgrade_orchestration"

# 标注源集中度告警阈值(投毒防御——54/55号同款)
CONCENTRATION_ALERT_RATIO = 0.8

# 漏斗阶段链(九态状态机主径)
FUNNEL_STAGES = (
    "draft", "planned", "coded", "tested",
    "audited", "approved", "delivered",
    "rolled_back", "archived")


class Aiup56DashboardService:
    """56号四区看板(漏斗/资产/合规/防御)"""

    def __init__(self):
        self.repo = Aiup56Repository()

    # ============================================================
    # 看板入口
    # ============================================================

    async def build(self) -> dict:
        """构建四区看板(观测面——GET /dashboard)"""
        proposals = await self.repo.list_proposals(
            limit=10000)
        assets = await self.repo.list_assets(
            limit=10000)
        sandboxes = await self.repo.list_sandboxes(
            limit=10000)
        reviews = await self.repo.list_reviews(
            limit=10000)

        return {
            "success": True,
            "modelVersion": MODEL_VERSION,
            "zones": {
                "funnel": self._zone_funnel(proposals),
                "assets": self._zone_assets(
                    assets, sandboxes),
                "compliance": self._zone_compliance(
                    proposals, reviews),
                "defense": await self._zone_defense(
                    proposals),
            },
            "note": "四区看板——提案漏斗/资产产出/"
                    "审计合规/回滚防御(52号范式"
                    "纯数据层聚合)",
            "generatedAt": ts(),
        }

    # ============================================================
    # ① 提案漏斗区
    # ============================================================

    @staticmethod
    def _zone_funnel(proposals: list) -> dict:
        """提案漏斗区(九态分布+阶段转化+决策分布)"""
        by_status: dict = {s: 0 for s in FUNNEL_STAGES}
        decisions: dict = {}
        escalated = 0
        for p in proposals:
            status = str(p.get("status") or "unknown")
            by_status[status] = \
                by_status.get(status, 0) + 1
            decision = str(p.get("decision") or "")
            if decision:
                decisions[decision] = \
                    decisions.get(decision, 0) + 1
            if p.get("escalated"):
                escalated += 1

        # 漏斗转化(主径深度——各阶段曾抵达计数)
        # 口径: 状态为终态深度的提案计入该级及之前
        # 全部级别(状态机单调前进, 现态即最深级)
        total = len(proposals)
        delivered = (by_status.get("delivered", 0)
                     + by_status.get("rolled_back", 0))
        approved = (delivered
                    + by_status.get("approved", 0))
        audited = (approved
                   + by_status.get("audited", 0))
        tested = (audited + by_status.get("tested", 0)
                  + by_status.get("blocked", 0))
        coded = (tested + by_status.get("coded", 0))
        planned = (coded
                   + by_status.get("planned", 0))

        def _rate(n):
            return (round(n / total, 4)
                    if total else None)

        return {
            "total": total,
            "byStatus": by_status,
            "decisions": decisions,
            "escalated": escalated,
            "conversion": {
                "planned": _rate(planned),
                "coded": _rate(coded),
                "tested": _rate(tested),
                "audited": _rate(audited),
                "approved": _rate(approved),
                "delivered": _rate(delivered),
                "rolledBack":
                    _rate(by_status.get(
                        "rolled_back", 0)),
            },
            "note": "提案漏斗——九态分布+三级决策"
                    "(defer/propose/escalate)",
        }

    # ============================================================
    # ② 资产产出区
    # ============================================================

    @staticmethod
    def _zone_assets(assets: list,
                     sandboxes: list) -> dict:
        """资产产出区(资产/版本/草稿/VALUE_REASON
        /LLM 计量+沙箱 verdict 分布)"""
        drafts = 0
        value_reasons = 0
        llm_calls = 0
        versions = 0
        for a in assets:
            versions += int(
                a.get("assetVersion") or 0)
            drafts += len(a.get("drafts") or [])
            value_reasons += len(
                a.get("VALUE_REASONs") or [])
            llm_calls += int(a.get("llmCalls") or 0)

        by_verdict: dict = {}
        for sb in sandboxes:
            v = str(sb.get("verdict") or "unknown")
            by_verdict[v] = \
                by_verdict.get(v, 0) + 1

        return {
            "totalAssets": len(assets),
            "versionSum": versions,
            "draftsTotal": drafts,
            "valueReasonsTotal": value_reasons,
            "llmCalls": llm_calls,
            "sandboxByVerdict": by_verdict,
            "note": "资产产出——版本化草稿+信值证据"
                    "(VALUE_REASON)+沙箱三关分布",
        }

    # ============================================================
    # ③ 审计合规区
    # ============================================================

    @staticmethod
    def _zone_compliance(proposals: list,
                         reviews: list) -> dict:
        """审计合规区(审计 verdict+一票否决+审批
        结果+强制确认完整率+双人复核)"""
        audit_verdicts: dict = {}
        veto_count = 0
        review_verdicts: dict = {}
        confirmations_complete = 0
        dual_reviews = 0
        for p in proposals:
            av = str(p.get("auditVerdict") or "")
            if av:
                audit_verdicts[av] = \
                    audit_verdicts.get(av, 0) + 1
                if av == "rejected":
                    veto_count += 1
            rv = str(p.get("reviewVerdict") or "")
            if rv:
                review_verdicts[rv] = \
                    review_verdicts.get(rv, 0) + 1
            if p.get("dualReview"):
                dual_reviews += 1

        # 审批记录确认清单完整率(批准路径须全 4 项)
        review_approved = 0
        for r in reviews:
            if r.get("approved") is True:
                review_approved += 1
                if len(r.get("confirmations") or []) \
                        >= 4:
                    confirmations_complete += 1
        complete_rate = (
            round(confirmations_complete
                  / review_approved, 4)
            if review_approved else None)

        return {
            "auditVerdicts": audit_verdicts,
            "vetoCount": veto_count,
            "reviewVerdicts": review_verdicts,
            "dualReviews": dual_reviews,
            "confirmationCompleteRate": complete_rate,
            "requiredConfirmations": 4,
            "note": "审计合规——三重校验+一票否决+"
                    "强制确认清单(防形式化审批)",
        }

    # ============================================================
    # ④ 回滚防御区
    # ============================================================

    async def _zone_defense(self,
                            proposals: list) -> dict:
        """回滚防御区(回滚+补偿+预算熔断+回流信号
        分布+标注源集中度+护栏健康)"""
        rollbacks = 0
        comp_attempted = 0
        comp_compensated = 0
        budget_halts = 0
        by_signal: dict = {}
        for p in proposals:
            status = str(p.get("status") or "")
            if status == "rolled_back":
                rollbacks += 1
                comp = p.get("compensation") or {}
                comp_attempted += int(
                    comp.get("attempted") or 0)
                comp_compensated += int(
                    comp.get("compensated") or 0)
            if str(p.get("testVerdict") or "") \
                    == "budget_halted":
                budget_halts += 1
            src = str(p.get("poolSignal") or "")
            if src:
                by_signal[src] = \
                    by_signal.get(src, 0) + 1

        # 44号池提交计数(第31档案)
        pool_submitted = sum(
            1 for p in proposals
            if int(p.get("pooledFeedbackId") or 0) > 0)

        # 标注源集中度(回流信号投毒防御口径)
        total_signals = sum(by_signal.values())
        top_signal, top_ratio = None, 0.0
        if total_signals:
            top_signal, top_count = max(
                by_signal.items(),
                key=lambda kv: kv[1])
            top_ratio = round(
                top_count / total_signals, 4)

        # 护栏健康(第31档案 champion 权重
        # [0.5,2.0] 倍——44号引擎内建)
        guard_healthy = None
        champion_version = None
        try:
            from services.aiup56_scorer import (
                Aiup56Scorer,
            )
            from services.ai_learning_service import (
                get_weights_view,
            )
            view = await get_weights_view(SCORER_ID)
            champion = view.get("champion") or {}
            champion_version = champion.get("version")
            weights = champion.get("weights") or {}
            if weights:
                guard_healthy = all(
                    Aiup56Scorer.WEIGHTS[k] / 2.0
                    <= float(weights.get(k, 0))
                    <= Aiup56Scorer.WEIGHTS[k] * 2.0
                    for k in Aiup56Scorer.WEIGHTS)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_dash_guard_failed: %s", exc)

        return {
            "rollbacks": rollbacks,
            "compensation": {
                "attempted": comp_attempted,
                "compensated": comp_compensated,
                "note": "45号 L2 受影响用户信值补偿"
                        "(语义回滚联动)",
            },
            "budgetHalts": budget_halts,
            "feedbackSignals": {
                "bySignal": by_signal,
                "poolSubmitted": pool_submitted,
            },
            "signalConcentration": {
                "topSignal": top_signal,
                "topRatio": top_ratio,
                "alert": top_ratio
                > CONCENTRATION_ALERT_RATIO,
                "threshold":
                    CONCENTRATION_ALERT_RATIO,
            },
            "guardrail": {
                "healthy": guard_healthy,
                "championVersion": champion_version,
                "bounds": "[0.5,2.0]×基线",
                "note": "第31档案 champion 权重护栏"
                        "(44号引擎内建)",
            },
            "note": "回滚防御——预案分步+补偿落账+"
                    "预算熔断+回流集中度+护栏",
        }
