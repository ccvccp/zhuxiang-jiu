"""57号·AI智能知识库 趋势诊断引擎+缺口底座
(kb57_service, P0)

计划(docs/57号_AI智能知识库模块实施计划.md §一/§二):
    ① 缺口信号采集(GAP_SIGNAL_REGISTRY 10 项——
       46/52/55号+44号池+既有 knowledge_*缺口/统计
       +48号失败挖掘纯读取, fail-soft)
    ② 信号融合(命中×权重 → Knowledge_Necessity_Score)
    ③ 第32档案八因子评分(信任分→三级决策
       defer/collect/urgent)
    ④ 知识缺口底座(状态机 open 起+《知识补全
       优先级清单》——采集建议源映射)
    ⑤ 全链事件留痕(gap_scan/gap_create)

模式口径(KB57_MODE):
    off    决策面关闭(gaps/scan 拒绝 409——
           registry 观测面不受影响)
    shadow 观察学习期(仅缺口诊断+采集留痕)
    assist 辅助生产期(P1+ 鉴别/种子/植入开放)

预算铁律: 缺口级预算封顶(默认 0.1/缺口, P1 采集
消费)。
"""

import logging
import os

from core.helpers import ts

from repositories.kb57_repository import (
    Kb57Repository,
)

logger = logging.getLogger("kb57_service")

MODEL_VERSION = "v1-kb57-service"

SCORER_ID = "knowledge_orchestration"

# 缺口级预算封顶(计划 §〇 三铁律之三——P1 采集消费上限)
DEFAULT_GAP_BUDGET_CAP = 0.1

# 必要性前置门槛(计划 §二: 环境因子不构成采集理由)
NECESSITY_GATE = 20.0

MODE_KEY = "KB57_MODE"

# 采集建议源映射(信号→建议采集源——白名单内)
SIGNAL_SUGGESTED_SOURCES = {
    "kb_gap_open": ["gov_policy_official",
                    "ops_manual"],
    "kb_search_miss": ["gov_policy_official",
                       "ops_manual"],
    "us52_inclusion_drop": ["academic_open",
                            "gov_policy_official"],
    "qr55_satisfaction_drop": ["ops_manual",
                               "gov_policy_official"],
    "gov46_stagnation": ["academic_open"],
    "xz48_failure_high": ["ops_manual"],
    "gov46_alert_open": ["gov_policy_official"],
    "gov46_drift_high": ["academic_open"],
    "pool44_alignment": ["academic_open"],
    "kb_freshness_stale": ["gov_policy_official",
                           "authority_clauses_18"],
}


def current_mode() -> str:
    """模块开关(动态读取——运行时可切换)"""
    return os.environ.get(MODE_KEY, "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝——shadow/assist 开放)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"KB57_MODE={mode}(默认 off——决策面"
            f"关闭, registry 观测面不受影响)")


class Kb57Service:
    """57号趋势诊断引擎+缺口底座(P0)"""

    def __init__(self):
        self.repo = Kb57Repository()

    # --------------------------------------------------------
    # 观测面(注册表自描述)
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        """信号+采集源注册表视图(观测面
        不受开关影响)"""
        from services.kb57_registry import (
            registry_view, sources_view,
        )
        view = registry_view()
        src = sources_view()
        view.update({
            "sources": src["sources"],
            "sourceTypes": src["sourceTypes"],
            "scorer": {
                "scorerId": SCORER_ID,
                "factors": 8,
                "decisions": ("defer", "collect",
                              "urgent"),
            },
            "gapBudgetCap": DEFAULT_GAP_BUDGET_CAP,
            "note": "P0 底座: 缺口信号+采集源注册表+"
                    "趋势诊断引擎(三重鉴别 P1 接入)",
        })
        return view

    # ============================================================
    # ① 缺口信号采集(纯读取——fail-soft 单源跳过)
    # ============================================================

    async def scan_signals(self) -> dict:
        """采集一轮缺口信号(GAP_SIGNAL_REGISTRY 全量)

        Returns:
            {hits: [{signalId, value, evidence}],
             necessityScore, sideCoverage, skipped}
        """
        require_active_mode()
        hits = []
        skipped = []

        # --- 业务侧: 既有 knowledge_* 缺口+搜索未命中 ---
        kb_open, kb_miss, kb_stale = \
            await self._knowledge_state()
        if kb_open is not None:
            if kb_open >= 1:
                hits.append(self._hit(
                    "kb_gap_open", kb_open,
                    f"{kb_open} 项既有缺口未决"))
        else:
            skipped.append("knowledge_gaps")
        if kb_miss is not None:
            if kb_miss >= 10:
                hits.append(self._hit(
                    "kb_search_miss", kb_miss,
                    f"累计搜索未命中 {kb_miss} 次"))
        if kb_stale is not None:
            if kb_stale >= 0.3:
                hits.append(self._hit(
                    "kb_freshness_stale",
                    round(kb_stale, 4),
                    f"retired/过期占比 {kb_stale:.0%}"))

        # --- 用户侧: 52号五维 inclusion 环比 ---
        inclusion_drop = \
            await self._us52_inclusion_drop()
        if inclusion_drop is not None:
            if inclusion_drop >= 0.1:
                hits.append(self._hit(
                    "us52_inclusion_drop",
                    round(inclusion_drop, 4),
                    f"52号包容性环比降 "
                    f"{inclusion_drop:.4f}"))
        else:
            skipped.append("us52")

        # --- 用户侧: 55号六指标 satisfaction 环比 ---
        qr_sat = await self._qr55_satisfaction_delta()
        if qr_sat is not None:
            if qr_sat <= -0.1:
                hits.append(self._hit(
                    "qr55_satisfaction_drop", qr_sat,
                    f"满意度环比降 {abs(qr_sat):.1f}"))
        else:
            skipped.append("qr55")

        # --- 系统侧: 46号三检测器(停滞/漂移) ---
        gov_entry = await self._gov_health_entry()
        if gov_entry is not None:
            signals = gov_entry.get("signals") or []
            if "stagnation" in signals:
                hits.append(self._hit(
                    "gov46_stagnation", len(signals),
                    "46号 live_health 停滞命中"))
            if "drift_high" in signals:
                hits.append(self._hit(
                    "gov46_drift_high", len(signals),
                    "46号 live_health 漂移高"))
        else:
            skipped.append("gov46_health")

        # --- 系统侧: 48号失败挖掘队列量 ---
        xz_failures = await self._xiaozhu_failure_count()
        if xz_failures is not None:
            if xz_failures >= 5:
                hits.append(self._hit(
                    "xz48_failure_high", xz_failures,
                    f"48号失败挖掘记录 {xz_failures} 条"))
        else:
            skipped.append("xz48")

        # --- 合规侧: 46号告警未决 ---
        open_alerts = await self._gov_open_alerts()
        if open_alerts is not None:
            if open_alerts >= 1:
                hits.append(self._hit(
                    "gov46_alert_open", open_alerts,
                    f"{open_alerts} 条告警未决"))
        else:
            skipped.append("gov46_alerts")

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

        # 融合评分(命中×权重——negative 信号抑制)
        from services.kb57_registry import (
            GAP_SIGNAL_REGISTRY,
        )
        necessity = 0.0
        for h in hits:
            sig = GAP_SIGNAL_REGISTRY.get(h["signalId"])
            if not sig:
                continue
            w = float(sig.get("weight") or 0)
            if sig.get("direction") == "positive":
                necessity += w * 100
            else:
                necessity -= w * 100
        necessity = round(max(0.0, necessity), 2)

        # 五侧覆盖度
        sides_hit = {
            GAP_SIGNAL_REGISTRY[h["signalId"]]["side"]
            for h in hits
            if h["signalId"] in GAP_SIGNAL_REGISTRY}
        side_coverage = round(
            len(sides_hit) / 5, 4)

        return {
            "success": True,
            "hits": hits,
            "skipped": skipped,
            "hitCount": len(hits),
            "necessityScore": necessity,
            "sideCoverage": side_coverage,
            "signalCount": len(GAP_SIGNAL_REGISTRY),
            "scannedAt": ts(),
        }

    # ============================================================
    # ②-④ 诊断主链(评分→三级决策→缺口底座)
    # ============================================================

    async def diagnose_and_plan(self) -> dict:
        """诊断主链: 信号采集→必要性前置门槛→八因子
        评分→三级决策→缺口落库(collect/urgent 级)
        或 defer 留痕

        Raises:
            ValueError: off 态
        """
        scan = await self.scan_signals()

        # 决策上下文富化(fail-soft)
        ctx = await self._build_ctx(scan)
        from services.kb57_scorer import Kb57Scorer
        scored = await Kb57Scorer().score(ctx)
        decision = scored.get("decision")

        # 必要性前置门槛(信号不足 → defer——
        # 环境因子不构成采集理由)
        if scan["necessityScore"] \
                < NECESSITY_GATE:
            decision = "defer"

        result = {
            "success": True,
            "decision": decision,
            "necessityScore":
                scan["necessityScore"],
            "hitCount": scan["hitCount"],
            "sideCoverage": scan["sideCoverage"],
            "scoring": {
                "trustScore":
                    scored.get("trustScore"),
                "decision":
                    scored.get("decision"),
            },
            "necessityGate": NECESSITY_GATE,
            "skipped": scan["skipped"],
            "evaluatedAt": ts(),
        }

        if decision == "defer":
            # 观察留痕(gap_scan 事件承载)
            await self._track(0, "gap_scan", {
                "decision": decision,
                "necessityScore":
                    scan["necessityScore"],
                "hitCount": scan["hitCount"],
                "deferred": True,
            })
            result["note"] = ("必要性不足——defer 观察"
                             "留痕(不建缺口)")
            return result

        # collect/urgent → 缺口落库
        gap = await self._create_gap(scan, scored)
        result.update({
            "gapId": gap["gapId"],
            "gapStatus": gap["status"],
            "priority": gap["priority"],
            "suggestedSources": gap[
                "suggestedSources"],
            "note": "知识缺口已创建——P1 定向采集"
                    "接管(三重合规鉴别)",
        })
        return result

    # --------------------------------------------------------
    # 缺口落库
    # --------------------------------------------------------

    async def _create_gap(self, scan: dict,
                          scored: dict) -> dict:
        """缺口创建(open 态+《知识补全优先级清单》
        +预算封顶)"""
        gap_id = await self.repo.next_gap_id()
        decision = scored.get("decision")

        # 优先级(urgent→high/collect→medium)
        priority = "high" if decision == "urgent" \
            else "medium"

        # 建议采集源(命中信号映射并集——白名单内)
        from services.kb57_registry import (
            SOURCE_REGISTRY,
        )
        suggested = []
        for h in scan.get("hits") or []:
            for src in SIGNAL_SUGGESTED_SOURCES.get(
                    h.get("signalId"), []):
                if src in SOURCE_REGISTRY \
                        and src not in suggested:
                    suggested.append(src)

        # 缺口主题(top 开放缺口的 question
        # 或信号聚合摘要)
        topic = await self._gap_topic(scan)

        record = {
            "gapId": gap_id,
            "status": "open",
            "priority": priority,
            "topic": topic,
            "decision": decision,
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
            "suggestedSources": suggested,
            "budgetCap": DEFAULT_GAP_BUDGET_CAP,
            "budgetSpent": 0.0,
            "llmCalls": 0,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_gap(record)
        await self._track(gap_id, "gap_create", {
            "decision": decision,
            "priority": priority,
            "necessityScore":
                scan["necessityScore"],
            "trustScore":
                scored.get("trustScore"),
            "suggestedSources": suggested,
        })
        return record

    async def _gap_topic(self, scan: dict) -> str:
        """缺口主题(top 开放缺口问题或信号摘要)"""
        try:
            open_gaps = await self._kb_open_gaps()
            if open_gaps:
                top = max(
                    open_gaps,
                    key=lambda g: int(
                        g.get("askCount") or 1))
                question = str(
                    top.get("question") or "").strip()
                if question:
                    return question[:64]
        except Exception:  # noqa: BLE001
            pass
        hits = scan.get("hits") or []
        return (f"知识缺口——{len(hits)} 项信号命中"
                f"(必要性 {scan['necessityScore']:.1f})")

    # --------------------------------------------------------
    # 观测面(缺口清单/模型状态)
    # --------------------------------------------------------

    async def list_gaps(self, status: str = None
                        ) -> dict:
        """缺口清单(观测面——优先级排序)"""
        records = await self.repo.list_gaps(
            status=status, limit=200)
        order = {"high": 0, "medium": 1, "low": 2}
        records.sort(key=lambda g: (
            order.get(g.get("priority"), 3),
            -int(g.get("necessityScore") or 0)))
        return {
            "success": True,
            "total": len(records),
            "gaps": records,
            "note": "知识补全优先级清单——P1 定向"
                    "采集接管",
        }

    async def model_status(self) -> dict:
        """模型状态(44号 get_weights_view 复用)"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(SCORER_ID)
        view.update({
            "module": "kb57",
            "mode": current_mode(),
            "scorerId": SCORER_ID,
            "factorsMeta": {
                "signal_quality": "信号质量",
                "necessity_score": "知识必要性",
                "budget_sufficiency": "预算余量",
                "risk_posture": "风险态势",
                "source_health": "来源健康",
                "history_success": "历史有效率",
                "compliance_posture": "合规态势",
                "human_load": "人工负载",
            },
            "decisions": ["defer", "collect", "urgent"],
            "note": "44号学习闭环复用——第32档案",
        })
        return {"success": True, "status": view}

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
        try:
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
            # 来源健康(近期采集源可信度均值
            # ——内置白名单均值近似)
            from services.kb57_registry import (
                SOURCE_REGISTRY,
            )
            credibilities = [
                float(v.get("credibility") or 0)
                for v in SOURCE_REGISTRY.values()]
            if credibilities:
                ctx["sourceHealth"] = round(
                    sum(credibilities)
                    / len(credibilities), 4)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_ctx_enrich_failed: %s", exc)
        return ctx

    # --------------------------------------------------------
    # 信号源采集(纯读取——fail-soft)
    # --------------------------------------------------------

    async def _kb_open_gaps(self) -> list:
        """既有 knowledge_* 开放缺口(跨模块只读)"""
        try:
            from repositories.knowledge_repository \
                import KnowledgeRepository
            return await KnowledgeRepository(
            ).list_gaps(status="open", limit=100)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_kb_gaps_failed: %s", exc)
            return []

    async def _knowledge_state(self) -> tuple:
        """既有 knowledge_* 状态(open 缺口数/
        累计未命中/retired 占比)"""
        kb_open = None
        kb_miss = None
        kb_stale = None
        try:
            from repositories.knowledge_repository \
                import KnowledgeRepository
            repo = KnowledgeRepository()
            stats = await repo.stats()
            kb_open = int(stats.get("openGaps") or 0)
            kb_miss = int(stats.get("missCount") or 0)
            by_status = stats.get("byStatus") or {}
            total = sum(by_status.values())
            if total:
                kb_stale = round(
                    (by_status.get("retired", 0)
                     + by_status.get("rejected", 0))
                    / total, 4)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_knowledge_state_failed: %s", exc)
        return kb_open, kb_miss, kb_stale

    @staticmethod
    async def _us52_inclusion_drop() -> float | None:
        """52号五维包容性环比(最近两帧快照)"""
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
                metrics = snap.get("metrics") or {}
                inc = metrics.get("inclusion") or {}
                val = inc.get("value")
                if val is None:
                    return None
                scores.append(float(val))
            return round(scores[1] - scores[0], 4)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_us52_drop_failed: %s", exc)
            return None

    @staticmethod
    async def _qr55_satisfaction_delta() -> float | None:
        """55号六指标 satisfaction 环比(最近两帧)"""
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
            a = latest.get("satisfactionScore")
            b = prev.get("satisfactionScore")
            if a is None or b is None:
                return None
            return round(float(a) - float(b), 4)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_qr55_delta_failed: %s", exc)
            return None

    @staticmethod
    async def _gov_health_entry() -> dict | None:
        """46号 live_health 中第32档案条目"""
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
                "kb57_gov_entry_failed: %s", exc)
            return None

    @staticmethod
    async def _xiaozhu_failure_count() -> int | None:
        """48号失败挖掘队列量(voice48_failures)"""
        try:
            from repositories.xiaozhu_repository \
                import Xiaozhu48Repository
            records = await Xiaozhu48Repository(
            ).list_records(
                "voice48_failures", limit=500)
            return len(records)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_xz48_failures_failed: %s", exc)
            return None

    @staticmethod
    async def _gov_open_alerts() -> int | None:
        """46号未决告警数"""
        try:
            from repositories.ai_governance_repository \
                import AiGovernance46Repository
            alerts = await (
                AiGovernance46Repository()
                .list_alerts(limit=1000))
            return sum(
                1 for a in alerts
                if (a.get("status") or "")
                == "open")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_gov_alerts_failed: %s", exc)
            return None

    @staticmethod
    async def _pool_alignment_drop() -> float | None:
        """44号池对齐度环比(全站视角——任一档案
        显著下降即命中)"""
        try:
            from services.ai_learning_service import (
                SCORER_REGISTRY,
            )
            worst = None
            for scorer_id in SCORER_REGISTRY:
                current = await \
                    Kb57Service._pool_alignment(scorer_id)
                if current is None:
                    continue
                if worst is None \
                        or current < worst:
                    worst = current
            if worst is None:
                return None
            return round(1.0 - worst, 4)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_pool_drop_failed: %s", exc)
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
            view = await Kb57Service \
                ._champion_weights_view(scorer_id)
            metrics = als._evaluate(
                view, recent, scorer_id)
            return metrics.get("rewardAlignment")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_pool_alignment_failed: %s", exc)
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

    # --------------------------------------------------------
    # 事件埋点(fail-soft)
    # --------------------------------------------------------

    async def _track(self, gap_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "gapId": int(gap_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_track_failed %s: %s",
                event_type, exc)

    @staticmethod
    def _hit(signal_id: str, value,
             evidence: str) -> dict:
        return {"signalId": signal_id,
                "value": value,
                "evidence": evidence}
