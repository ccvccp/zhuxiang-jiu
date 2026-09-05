"""55号·二维码AI智能管理 四区看板
(qr55_dashboard_service, P5)

计划(docs/55号_二维码AI智能管理模块实施计划.md §六 P5):
    四区看板(码量/服务分布/回流漏斗/漂移+防御区)

四区(52号 dashboard 范式——纯数据层聚合):
    ① volume   码量区: 生成/扫码/完成/过期/
               篡改计数+状态分布+日趋势
    ② services 服务分布区: serviceId 维度生成/
               扫码计数+模板四类分组+敏感度结构
    ③ funnel   回流漏斗区: 生成→扫码→完成漏斗
               转化+七类信号分布+44号池/45号结算
    ④ defense  防御区: 投毒护栏健康(护栏约束+
               标注源集中度)+漂移(模型版本链
               +因子权重对比基线)

设计约定:
    - 纯读取式聚合(无落库副作用)
    - 数字全部来自 qr55 三表+44号视图
      (零外部依赖——与六指标同数据源)
"""

import logging

from core.helpers import ts

from repositories.qr55_repository import (
    Qr55Repository,
)

logger = logging.getLogger("qr55_dashboard_service")

MODEL_VERSION = "v1-qr55-dashboard"

SCORER_ID = "qr_orchestration"

# 标注源集中度告警阈值(P5 红队口径——54号 同款)
CONCENTRATION_ALERT_RATIO = 0.8

# 漂移检测窗口(最近 N 个 metrics_snapshot)
DRIFT_SNAPSHOT_WINDOW = 5


class Qr55DashboardService:
    """55号四区看板(码量/服务分布/回流漏斗/防御)"""

    def __init__(self):
        self.repo = Qr55Repository()

    # ============================================================
    # 看板入口
    # ============================================================

    async def build(self) -> dict:
        """构建四区看板(观测面——GET /dashboard)"""
        codes = await self.repo.list_codes(limit=10000)
        events = await self.repo.list_events(limit=10000)
        feedback = await self.repo.list_feedback(
            limit=10000)
        model_events = await \
            self.repo.list_model_events(limit=200)

        return {
            "success": True,
            "modelVersion": MODEL_VERSION,
            "zones": {
                "volume": await self._zone_volume(
                    codes, events),
                "services": self._zone_services(
                    codes, events),
                "funnel": self._zone_funnel(
                    codes, events, feedback),
                "defense": await self._zone_defense(
                    feedback, model_events),
            },
            "note": "四区看板——码量/服务分布/回流漏斗/"
                    "漂移+防御(52号范式纯数据层聚合)",
            "generatedAt": ts(),
        }

    # ============================================================
    # ① 码量区
    # ============================================================

    @staticmethod
    async def _zone_volume(codes: list,
                           events: list) -> dict:
        """码量区(状态分布+日趋势+事件计数)"""
        by_status: dict = {}
        for c in codes:
            status = str(c.get("status") or "unknown")
            by_status[status] = \
                by_status.get(status, 0) + 1

        # 日趋势(生成码按日聚合)
        by_day: dict = {}
        for c in codes:
            day = str(c.get("createdAt") or "")[:10]
            if day:
                by_day[day] = by_day.get(day, 0) + 1
        trend = [{"day": d, "generated": n}
                 for d, n in sorted(by_day.items())
                 ][-14:]   # 最近 14 日

        event_counts: dict = {}
        for e in events:
            et = str(e.get("eventType") or "unknown")
            event_counts[et] = \
                event_counts.get(et, 0) + 1

        return {
            "totalCodes": len(codes),
            "byStatus": by_status,
            "eventCounts": event_counts,
            "dailyTrend": trend,
            "note": "码量区——生成/状态/事件/日趋势",
        }

    # ============================================================
    # ② 服务分布区
    # ============================================================

    @staticmethod
    def _zone_services(codes: list,
                       events: list) -> dict:
        """服务分布区(serviceId 维度+模板四类+敏感度)"""
        from services.qr55_registry import (
            SERVICE_REGISTRY,
        )

        by_service: dict = {}
        sensitivity: dict = {}
        templates: dict = {}
        for c in codes:
            svc_id = str(c.get("serviceId") or "unknown")
            entry = by_service.setdefault(svc_id, {
                "generated": 0, "scanned": 0,
                "label": c.get("label"),
                "template":
                    (SERVICE_REGISTRY.get(svc_id)
                     or {}).get("template"),
                "sensitivity":
                    (SERVICE_REGISTRY.get(svc_id)
                     or {}).get("sensitivity"),
            })
            entry["generated"] += 1
            if int(c.get("scanCount") or 0) > 0 \
                    or c.get("status") == "redeemed":
                entry["scanned"] += 1
            sens = entry.get("sensitivity") or "unknown"
            sensitivity[sens] = \
                sensitivity.get(sens, 0) + 1
            tmpl = entry.get("template") or "unknown"
            templates[tmpl] = \
                templates.get(tmpl, 0) + 1

        return {
            "byService": by_service,
            "byTemplate": templates,
            "bySensitivity": sensitivity,
            "registrySize": len(SERVICE_REGISTRY),
            "note": "服务分布区——12 项白名单维度"
                    "(模板四类+敏感度结构)",
        }

    # ============================================================
    # ③ 回流漏斗区
    # ============================================================

    @staticmethod
    def _zone_funnel(codes: list, events: list,
                     feedback: list) -> dict:
        """回流漏斗区(生成→扫码→完成+信号分布)"""
        generated = len(codes)
        scanned = sum(
            1 for c in codes
            if int(c.get("scanCount") or 0) > 0
            or c.get("status") == "redeemed")
        completed_codes = {
            int(e.get("codeId") or 0)
            for e in events
            if e.get("eventType") == "complete"}
        completed = len(completed_codes)

        # 七类信号分布+44号池/45号结算
        by_signal: dict = {}
        pooled = settled = 0
        for f in feedback:
            if f.get("status") != "labeled":
                continue
            source = str(f.get("source") or "unknown")
            by_signal[source] = \
                by_signal.get(source, 0) + 1
            if int(f.get("poolFeedbackId") or 0) > 0:
                pooled += 1
        settles = [e for e in events
                   if e.get("eventType") == "settle"]
        settled = sum(
            1 for e in settles
            if (e.get("detail") or {})
            .get("depositVerified") is True)

        return {
            "funnel": {
                "generated": generated,
                "scanned": scanned,
                "completed": completed,
                "scanRate": _ratio(scanned, generated),
                "completeRate": _ratio(
                    completed, scanned),
            },
            "signals": by_signal,
            "labeledFeedback": sum(by_signal.values()),
            "poolSubmitted": pooled,
            "trustSettled": settled,
            "note": "回流漏斗区——生成→扫码→完成+"
                    "七类信号+44号池+45号结算",
        }

    # ============================================================
    # ④ 防御区(投毒护栏+集中度+漂移)
    # ============================================================

    async def _zone_defense(self, feedback: list,
                            model_events: list) -> dict:
        """防御区(护栏约束+集中度告警+漂移视图)"""
        # 护栏健康(champion 权重 [0.5,2.0] 倍)
        from services.qr55_scorer import Qr55Scorer
        guard_healthy = None
        champion_version = None
        challenger_version = None
        try:
            from services.ai_learning_service import (
                get_weights_view,
            )
            view = await get_weights_view(
                SCORER_ID)
            champion = view.get("champion") or {}
            challenger = view.get("challenger") or {}
            champion_version = champion.get("version")
            challenger_version = \
                challenger.get("version")
            weights = champion.get("weights") or {}
            if weights:
                guard_healthy = all(
                    Qr55Scorer.WEIGHTS[k] / 2.0
                    <= float(weights.get(k, 0))
                    <= Qr55Scorer.WEIGHTS[k] * 2.0
                    for k in Qr55Scorer.WEIGHTS)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_dash_guard_failed: %s", exc)

        # 标注源集中度(投毒防御——54号 同款口径)
        by_source: dict = {}
        for f in feedback:
            if f.get("status") != "labeled":
                continue
            src = str(f.get("source") or "unknown")
            by_source[src] = \
                by_source.get(src, 0) + 1
        total = sum(by_source.values())
        top_source, top_ratio = None, 0.0
        if total:
            top_source, top_count = max(
                by_source.items(),
                key=lambda kv: kv[1])
            top_ratio = round(
                top_count / total, 4)

        # 漂移视图(版本链+指标快照对比)
        version_chain = [
            {"type": e.get("eventType"),
             "version":
                 (e.get("detail") or {}).get(
                     "newVersion")
                 or (e.get("detail") or {}).get(
                     "toVersion"),
             "at": e.get("createdAt")}
            for e in model_events
            if e.get("eventType") in (
                "learning", "promoted", "rollback",
                "regression_rollback")][:10]
        snapshots = [
            (e.get("detail") or {}).get("metrics") or {}
            for e in model_events
            if e.get("eventType")
            == "metrics_snapshot"][
                :DRIFT_SNAPSHOT_WINDOW]
        drift = self._detect_drift(snapshots)

        return {
            "guardrail": {
                "healthy": guard_healthy,
                "bounds": "[0.5,2.0]×基线",
                "note": "champion 权重护栏约束"
                        "(44号引擎内建)",
            },
            "sourceConcentration": {
                "bySource": by_source,
                "topSource": top_source,
                "topRatio": top_ratio,
                "alert": top_ratio
                > CONCENTRATION_ALERT_RATIO,
                "threshold":
                    CONCENTRATION_ALERT_RATIO,
            },
            "versionChain": version_chain,
            "championVersion": champion_version,
            "challengerVersion": challenger_version,
            "metricsDrift": drift,
            "note": "防御区——投毒护栏+标注源集中度+"
                    "版本链漂移",
        }

    @staticmethod
    def _detect_drift(snapshots: list) -> dict:
        """指标漂移检测(快照序列首尾对比)"""
        if len(snapshots) < 2:
            return {
                "applicable": False,
                "reason": "快照不足(<2)",
                "window": len(snapshots),
            }
        first, last = snapshots[-1], snapshots[0]
        deltas = {}
        for key in first:
            if key not in last:
                continue
            a, b = first[key], last[key]
            if a is None or b is None:
                continue
            try:
                deltas[key] = round(
                    float(b) - float(a), 4)
            except (TypeError, ValueError):
                continue
        # 最大绝对漂移
        max_key, max_abs = None, 0.0
        for k, v in deltas.items():
            if abs(v) > max_abs:
                max_key, max_abs = k, abs(v)
        return {
            "applicable": True,
            "window": len(snapshots),
            "deltas": deltas,
            "maxDrift": {"metric": max_key,
                         "absDelta": max_abs},
            "note": "快照首尾对比(≥0.3 显著——"
                    "46号漂移口径对齐)",
        }


def _ratio(numerator, denominator):
    """占比(分母 0 → None)"""
    if not denominator:
        return None
    return round(float(numerator)
                 / float(denominator), 4)
