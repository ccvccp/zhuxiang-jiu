"""56号·AI智能升级管理 决策引擎+提案底座
(aiup56_service, P0)

计划(docs/56号_AI智能升级管理模块实施计划.md §一/§九 P0):
    ① 信号采集(SIGNAL_REGISTRY 10 项——46/52/55号
       观测面+44号池纯读取, fail-soft)
    ② 信号融合(命中×权重 → Upgrade_Necessity_Score)
    ③ 第31档案八因子评分(信任分→三级决策
       defer/propose/escalate)
    ④ 提案底座(状态机 draft 起 + 《升级提案摘要》
       ——人类快速审阅入口)
    ⑤ 全链事件留痕(signal_scan/proposal_create)

模式口径(AIUP56_MODE):
    off    决策面关闭(signals/scan 拒绝 409——
           registry 观测面不受影响)
    shadow 观察学习期(仅决策+提案——后续期消费)
    assist 辅助开发期(P1+ 资产生产)

预算铁律: 提案级预算封顶(默认 0.1/提案, P0 落
proposal.budgetCap 字段——P2 沙箱消费)。
"""

import logging
import os

from core.helpers import ts

from repositories.aiup56_repository import (
    Aiup56Repository,
)

logger = logging.getLogger("aiup56_service")

MODEL_VERSION = "v1-aiup56-service"

SCORER_ID = "upgrade_orchestration"

# 提案级预算封顶(计划 §〇 三铁律——P2 沙箱消费上限)
DEFAULT_PROPOSAL_BUDGET_CAP = 0.1

# 必要性前置门槛(计划 §一-①: 分数 > 阈值才触发
# 升级流程——环境因子不构成升级理由)
NECESSITY_GATE = 20.0

MODE_KEY = "AIUP56_MODE"


def current_mode() -> str:
    """模块开关(动态读取——运行时可切换)"""
    return os.environ.get(MODE_KEY, "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝——shadow/assist 开放)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"AIUP56_MODE={mode}(默认 off——决策面"
            f"关闭, registry 观测面不受影响)")


class Aiup56Service:
    """56号决策引擎+提案底座(P0)"""

    def __init__(self):
        self.repo = Aiup56Repository()

    # --------------------------------------------------------
    # 观测面(注册表自描述)
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        """信号注册表视图(白名单+四侧——观测面
        不受开关影响)"""
        from services.aiup56_registry import (
            registry_view,
        )
        view = registry_view()
        view.update({
            "scorer": {
                "scorerId": SCORER_ID,
                "factors": 8,
                "decisions": ("defer", "propose",
                              "escalate"),
            },
            "proposalBudgetCap":
                DEFAULT_PROPOSAL_BUDGET_CAP,
            "note": "P0 底座: 信号注册表+决策引擎+"
                    "提案底座(四 Agent P1 接入)",
        })
        return view

    # ============================================================
    # ① 信号采集(纯读取——fail-soft 单源跳过)
    # ============================================================

    async def scan_signals(self) -> dict:
        """采集一轮升级信号(SIGNAL_REGISTRY 全量)

        Returns:
            {hits: [{signalId, value, evidence}],
             necessityScore, sideCoverage, ctx}
        """
        require_active_mode()
        hits = []
        skipped = []

        # --- 模型侧: 46号三检测器 + 冻结态 ---
        gov_entry = await self._gov_health_entry()
        if gov_entry is not None:
            signals = gov_entry.get("signals") or []
            if "stagnation" in signals:
                hits.append(self._hit(
                    "gov46_stagnation",
                    len(signals),
                    "46号 live_health 停滞命中"))
            if "drift_high" in signals:
                hits.append(self._hit(
                    "gov46_drift_high",
                    len(signals),
                    "46号 live_health 漂移高"))
            if gov_entry.get("govStatus") == "frozen" \
                    or (gov_entry.get("details") or {}
                        ).get("frozen"):
                hits.append(self._hit(
                    "scorer_frozen", 1,
                    "46号档案冻结中"))
        else:
            skipped.append("gov46")

        # --- 模型侧: 44号池 rewardAlignment 环比 ---
        pool_drop = await self._pool_alignment_drop()
        if pool_drop is not None:
            if pool_drop >= 0.05:
                hits.append(self._hit(
                    "pool44_alignment",
                    round(pool_drop, 4),
                    f"44号池对齐度环比降 "
                    f"{pool_drop:.4f}"))
        else:
            skipped.append("pool44")

        # --- 用户侧+系统侧: 55号六指标环比 ---
        qr_deltas = await self._qr55_metric_deltas()
        if qr_deltas is not None:
            sat = qr_deltas.get("satisfactionScore")
            if sat is not None and sat <= -0.1:
                hits.append(self._hit(
                    "qr55_satisfaction_drop", sat,
                    f"满意度环比降 {abs(sat):.1f}"))
            cla = qr_deltas.get("clarifyEfficiency")
            if cla is not None and cla <= -0.2:
                hits.append(self._hit(
                    "qr55_clarify_bloat", cla,
                    f"澄清效率环比降 {abs(cla):.4f}"))
            waste = qr_deltas.get("penetrationWaste")
            if waste is not None and waste >= 0.3:
                hits.append(self._hit(
                    "qr55_generation_waste", waste,
                    f"生成过剩占比 {waste:.2f}"))
        else:
            skipped.append("qr55")

        # --- 用户侧: 52号五维可用性环比 ---
        us_drop = await self._us52_usability_drop()
        if us_drop is not None:
            if us_drop >= 0.1:
                hits.append(self._hit(
                    "us52_usability_drop",
                    round(us_drop, 4),
                    f"52号可用性环比降 {us_drop:.4f}"))
        else:
            skipped.append("us52")

        # --- 合规侧: 46号告警未决+挂起变更 ---
        open_alerts, pending_changes = \
            await self._gov_compliance_state()
        if open_alerts is not None:
            if open_alerts >= 1:
                hits.append(self._hit(
                    "gov46_alert_open", open_alerts,
                    f"{open_alerts} 条告警未决"))
        if pending_changes is not None:
            if pending_changes >= 1:
                hits.append(self._hit(
                    "registry_pending",
                    pending_changes,
                    f"{pending_changes} 项变更待审批"))

        # 融合评分(命中×权重——negative 信号抑制)
        from services.aiup56_registry import (
            SIGNAL_REGISTRY,
        )
        necessity = 0.0
        for h in hits:
            sig = SIGNAL_REGISTRY.get(h["signalId"])
            if not sig:
                continue
            w = float(sig.get("weight") or 0)
            if sig.get("direction") == "positive":
                necessity += w * 100
            else:
                necessity -= w * 100
        necessity = round(max(0.0, necessity), 2)

        # 四侧覆盖度
        sides_hit = {
            SIGNAL_REGISTRY[h["signalId"]]["side"]
            for h in hits
            if h["signalId"] in SIGNAL_REGISTRY}
        side_coverage = round(
            len(sides_hit) / 4, 4)

        return {
            "success": True,
            "hits": hits,
            "skipped": skipped,
            "hitCount": len(hits),
            "necessityScore": necessity,
            "sideCoverage": side_coverage,
            "signalCount": len(SIGNAL_REGISTRY),
            "scannedAt": ts(),
        }

    # ============================================================
    # ②-④ 决策引擎+提案底座
    # ============================================================

    async def evaluate_and_propose(self) -> dict:
        """决策主链: 信号采集→必要性前置门槛→八因子
        评分→三级决策→提案落库(propose/escalate 级)
        或 defer 留痕

        前置门槛(计划 §一-①"仅当分数>阈值且预算充足
        时触发升级流程"): 必要性 <20 时无论环境
        因子多优, 一律 defer(环境因子不构成升级理由)。

        Raises:
            ValueError: off 态
        """
        scan = await self.scan_signals()

        # 决策上下文富化(fail-soft)
        ctx = await self._build_ctx(scan)
        from services.aiup56_scorer import Aiup56Scorer
        scored = await Aiup56Scorer().score(ctx)
        decision = scored.get("decision")

        # 必要性前置门槛(信号不足 → defer——
        # 环境因子(预算/健康/历史)不构成升级理由)
        if scan["necessityScore"] \
                < NECESSITY_GATE:
            decision = "defer"

        result = {
            "success": True,
            "decision": decision,
            "decisionName": (
                "观察(留痕不提案)"
                if decision == "defer"
                else scored.get("decisionName")),
            "necessityScore":
                scan["necessityScore"],
            "hitCount": scan["hitCount"],
            "sideCoverage": scan["sideCoverage"],
            "scoring": {
                "trustScore":
                    scored.get("trustScore"),
                "decision":
                    scored.get("decision"),
                "confidence":
                    scored.get("confidence"),
            },
            "necessityGate": NECESSITY_GATE,
            "gated": decision == "defer"
            and scored.get("decision") != "defer",
            "skipped": scan["skipped"],
            "evaluatedAt": ts(),
        }

        if decision == "defer":
            # 观察留痕(signal_scan 事件承载)
            await self._track(0, "signal_scan", {
                "decision": decision,
                "necessityScore":
                    scan["necessityScore"],
                "hitCount": scan["hitCount"],
                "deferred": True,
            })
            result["note"] = ("必要性不足——defer 观察"
                             "留痕(不建提案)")
            return result

        # propose/escalate → 提案落库
        proposal = await self._create_proposal(
            scan, scored, ctx)
        result.update({
            "proposalId": proposal["proposalId"],
            "proposalStatus": proposal["status"],
            "budgetCap": proposal["budgetCap"],
            "summary": proposal["summary"],
            "note": "提案已创建——P1 规划/编码 Agent "
                    "接管(资产生产)",
        })
        return result

    # --------------------------------------------------------
    # 提案落库
    # --------------------------------------------------------

    async def _create_proposal(self, scan: dict,
                               scored: dict,
                               ctx: dict) -> dict:
        """提案创建(draft 态+《升级提案摘要》+
        预算封顶)"""
        proposal_id = await \
            self.repo.next_proposal_id()
        escalated = scored.get("decision") \
            == "escalate"
        summary = self._proposal_summary(
            scan, scored)
        record = {
            "proposalId": proposal_id,
            "status": "draft",
            "decision": scored.get("decision"),
            "escalated": escalated,
            "dualReview": escalated,
            "signalSnapshot": {
                "hits": scan["hits"],
                "necessityScore":
                    scan["necessityScore"],
                "sideCoverage":
                    scan["sideCoverage"],
            },
            "necessityScore":
                scan["necessityScore"],
            "trustScore":
                scored.get("trustScore"),
            "summary": summary,
            "riskAssessment": {
                "hitCount": scan["hitCount"],
                "skippedSources":
                    scan["skipped"],
                "dualReview": escalated,
            },
            "budgetCap": DEFAULT_PROPOSAL_BUDGET_CAP,
            "budgetSpent": 0.0,
            "estimatedGain": 0.0,
            "actualGain": 0.0,
            "llmCalls": 0,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_proposal(record)
        await self._track(proposal_id,
                         "proposal_create", {
            "decision": scored.get("decision"),
            "necessityScore":
                scan["necessityScore"],
            "trustScore":
                scored.get("trustScore"),
            "escalated": escalated,
        })
        return record

    @staticmethod
    def _proposal_summary(scan: dict,
                          scored: dict) -> dict:
        """《升级提案摘要》(人类快速审阅入口——
        计划 §一-①风险预评估)"""
        hits = scan.get("hits") or []
        top = sorted(
            hits,
            key=lambda h: -float(
                h.get("value") or 0))[:3]
        return {
            "headline": (
                f"升级提案——{scan['hitCount']} 项"
                f"信号命中(必要性 "
                f"{scan['necessityScore']:.1f}, "
                f"信任分 "
                f"{scored.get('trustScore')})"),
            "topSignals": [
                {"signalId": h["signalId"],
                 "value": h.get("value"),
                 "evidence": h.get("evidence")}
                for h in top],
            "sideCoverage": scan.get("sideCoverage"),
            "riskNote": "风险预评估——规划 Agent 任务"
                        "拆解后细化(P1)",
            "budgetCap": DEFAULT_PROPOSAL_BUDGET_CAP,
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # 决策上下文富化(fail-soft——评分器八因子输入)
    # --------------------------------------------------------

    async def _build_ctx(self, scan: dict) -> dict:
        ctx = {
            "signalHits": scan.get("hitCount"),
            "sideCoverage":
                scan.get("sideCoverage"),
            "necessityScore":
                scan.get("necessityScore"),
        }
        # 预算余量(49号系统态近似——
        # 提案无个人主体, 用预算池视角)
        try:
            from services.aiup56_registry import (
                SIGNAL_REGISTRY,
            )
            # 风险态势(46号)
            gov = await self._gov_health_entry()
            if gov is not None:
                ctx["govHealthScore"] = gov.get(
                    "healthScore")
                ctx["alertDensity"] = round(min(
                    1.0, len(gov.get("signals") or [])
                    / 3), 4)
                if gov.get("govStatus") == "frozen":
                    ctx["riskFlagged"] = True
            # 模型健康(44号池对齐)
            pool = await self._pool_alignment(
                "upgrade_orchestration")
            if pool is not None:
                ctx["poolAlignment"] = pool
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_ctx_enrich_failed: %s", exc)
        return ctx

    # --------------------------------------------------------
    # 信号源采集(纯读取——fail-soft)
    # --------------------------------------------------------

    @staticmethod
    async def _gov_health_entry() -> dict | None:
        """46号 live_health 中第31档案条目"""
        try:
            from services.ai_governance_health \
                import AiGovernanceHealthService
            health = await (
                AiGovernanceHealthService()
                .live_health())
            for e in health.get("entries") or []:
                if e.get("scorerId") == SCORER_ID:
                    return e
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_gov_entry_failed: %s", exc)
            return None

    @staticmethod
    async def _pool_alignment(scorer_id: str
                             ) -> float | None:
        """44号池 rewardAlignment(近期)"""
        try:
            import services.ai_learning_service as als
            from repositories.ai_learning_repository \
                import AiLearningRepository
            repo = AiLearningRepository()
            recent = await repo.list_feedback(
                scorer_id, limit=50)
            if not recent:
                return None
            view = await Aiup56Service \
                ._champion_weights_view(scorer_id)
            metrics = als._evaluate(
                view, recent, scorer_id)
            return metrics.get("rewardAlignment")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_pool_alignment_failed: %s", exc)
            return None

    @staticmethod
    async def _champion_weights_view(
            scorer_id: str) -> dict:
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(scorer_id)
        return (view.get("champion")
                or {}).get("weights") or {}

    async def _pool_alignment_drop(self
                                   ) -> float | None:
        """44号池对齐度环比(全站视角——任一档案
        显著下降即命中)"""
        try:
            from services.ai_learning_service import (
                SCORER_REGISTRY,
            )
            worst = None
            for scorer_id in SCORER_REGISTRY:
                current = await \
                    self._pool_alignment(scorer_id)
                if current is None:
                    continue
                if worst is None \
                        or current < worst:
                    worst = current
            if worst is None:
                return None
            # 对齐度 1.0 为完美——降幅=1-当前
            return round(1.0 - worst, 4)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_pool_drop_failed: %s", exc)
            return None

    @staticmethod
    async def _qr55_metric_deltas() -> dict | None:
        """55号六指标环比(最近两帧 metrics_snapshot)"""
        try:
            from repositories.qr55_repository import (
                Qr55Repository,
            )
            events = await Qr55Repository(
            ).list_model_events(limit=100)
            snaps = [
                (e.get("detail") or {}).get("metrics")
                for e in events
                if e.get("eventType")
                == "metrics_snapshot"]
            snaps = [m for m in snaps if m]
            if len(snaps) < 2:
                return None
            latest, prev = snaps[0], snaps[1]
            deltas = {}
            for key in ("satisfactionScore",
                        "clarifyEfficiency",
                        "intentSatisfactionRate"):
                a, b = latest.get(key), prev.get(key)
                if a is None or b is None:
                    continue
                try:
                    deltas[key] = round(
                        float(a) - float(b), 4)
                except (TypeError, ValueError):
                    continue
            # 生成过剩: 未扫码占比(1-渗透率)
            pen_l = latest.get("penetrationRate")
            pen_p = prev.get("penetrationRate")
            if pen_l is not None:
                deltas["penetrationWaste"] = round(
                    1.0 - float(pen_l), 4)
            elif pen_p is not None:
                deltas["penetrationWaste"] = round(
                    1.0 - float(pen_p), 4)
            return deltas
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_qr55_deltas_failed: %s", exc)
            return None

    @staticmethod
    async def _us52_usability_drop() -> float | None:
        """52号五维可用性环比(最近两帧快照)"""
        try:
            from repositories.us52_repository \
                import Us52Repository
            snaps = await Us52Repository(
            ).list_snapshots(limit=2)
            if len(snaps) < 2:
                return None
            latest, prev = snaps[0], snaps[1]
            scores = []
            for snap in (latest, prev):
                dims = snap.get("dimensions") or {}
                vals = [float(v) for v in
                        dims.values()
                        if v is not None]
                if not vals:
                    return None
                scores.append(sum(vals) / len(vals))
            return round(scores[1] - scores[0], 4)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_us52_drop_failed: %s", exc)
            return None

    @staticmethod
    async def _gov_compliance_state() -> tuple:
        """46号合规态(未决告警数+挂起变更数)"""
        open_alerts = None
        pending_changes = None
        try:
            from repositories.ai_governance_repository \
                import AiGovernance46Repository
            repo = AiGovernance46Repository()
            alerts = await repo.list_alerts(
                limit=1000)
            open_alerts = sum(
                1 for a in alerts
                if (a.get("status") or "")
                == "open")
            changes = await repo.list_changes(
                status="pending", limit=1000)
            pending_changes = len(changes)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_gov_compliance_failed: %s",
                exc)
        return open_alerts, pending_changes

    # --------------------------------------------------------
    # 模型状态视图(44号复用——观测面)
    # --------------------------------------------------------

    async def model_status(self) -> dict:
        """模型状态(champion/challenger/八因子
        ——44号 get_weights_view 复用)"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(SCORER_ID)
        view.update({
            "module": "aiup56",
            "mode": current_mode(),
            "scorerId": SCORER_ID,
            "factorsMeta": {
                "signal_quality": "信号质量",
                "necessity_score": "升级必要性",
                "budget_sufficiency": "预算余量",
                "risk_posture": "风险态势",
                "model_health": "模型健康",
                "history_success": "历史成功率",
                "compliance_posture": "合规态势",
                "human_load": "人工负载",
            },
            "decisions": ["defer", "propose",
                          "escalate"],
            "note": "44号学习闭环复用——第31档案",
        })
        return {"success": True, "status": view}

    # --------------------------------------------------------
    # 事件埋点(fail-soft)
    # --------------------------------------------------------

    async def _track(self, proposal_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "proposalId": int(proposal_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aiup56_track_failed %s: %s",
                event_type, exc)

    @staticmethod
    def _hit(signal_id: str, value,
             evidence: str) -> dict:
        return {"signalId": signal_id,
                "value": value,
                "evidence": evidence}
