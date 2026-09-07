"""66号·AI智能工程师大模块 P0 服务层
(生命体征底座: 四区聚合 + 快照 + 主动巡检)

《66号_AI智能工程师大模型实施计划》§四 4.1.1:

四区聚合(全部只读数据源, fail-soft 分区):
    system      26号 system/performance/error 三类指标
                (异常分聚合) + fatal 告警计数
    business    26号 business 类指标 + 45号信值流水速率
    ai_services 35号入口健康 + 44号学习域全景
    trust       47号中枢总览(tier 分布/三联动子系统)
                + 对账结果(P3 交付, P0 占位 not_implemented)

红黄绿灯口径(确定性——零 LLM):
    每区 0(绿)/1(黄)/2(红); 总分 0-8:
        0-1 healthy / 2-4 degraded / >=5 critical
    黄: 指标异常分均值 >0.5 或 hub degraded
        或漂移告警>0 或 watched+restricted 占比>30%
    红: fatal 命令>0 或 P0 活跃故障 或
        (hub degraded 且漂移告警>=3)

主动巡检 scan(替代 27号 inspect_all 硬编码清单——G3):
    真实聚合四源生成快照 + 对黄/红区调 27号
    detect_fault 起链(纯状态机记录, 零执行——
    自愈执行在 P2 经白名单+预演+46号审批)

模式闸门:
    XX66_MODE: off(默认)/shadow/assist——决策面
    off → 409; 观测面(vitals/history/status)
    永不关停(宪法口径)。
"""

import logging
import os

from core.helpers import ts

from repositories.xx66_repository import Xx66Repository

logger = logging.getLogger(__name__)

# 红黄绿口径常量(确定性阈值)
ZONE_GREEN, ZONE_YELLOW, ZONE_RED = 0, 1, 2
ZONE_NAMES = {0: "green", 1: "yellow", 2: "red"}

# trust 区 watched+restricted 占比黄线(样本>=5 才判)
TRUST_WATCH_RATIO_YELLOW = 0.30
TRUST_MIN_SAMPLES = 5

# 指标聚合窗口(拉取条数)
METRIC_SCAN_LIMIT = 200


def current_mode() -> str:
    """模块模式(XX66_MODE: off 默认/shadow/assist)"""
    return os.environ.get("XX66_MODE", "off").lower()


def require_active_mode() -> None:
    """决策面门槛(off → ValueError——409 映射)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"XX66_MODE={mode}(默认 off"
            f"——决策面关闭, 观测面不受影响)")


def llm_mode() -> str:
    """LLM 辅助轨开关(XX66_LLM_MODE, 默认 off)"""
    return os.environ.get("XX66_LLM_MODE", "off").lower()


def _zone_score(yellow: bool, red: bool) -> int:
    """红黄绿计算(红优先, 无黄无红为绿)"""
    if red:
        return ZONE_RED
    if yellow:
        return ZONE_YELLOW
    return ZONE_GREEN


class Xx66Service:
    """66号 P0 服务(生命体征聚合/快照/巡检)"""

    def __init__(self, repo: Xx66Repository = None):
        self.repo = repo or Xx66Repository()

    # --------------------------------------------------------
    # 模式观测面(status——观测面永不关停)
    # --------------------------------------------------------

    async def status(self) -> dict:
        """模块状态(模式/评分器入册/快照量级)"""
        registered = False
        scorer_label = ""
        try:
            from services.ai_learning_service import (
                SCORER_REGISTRY,
            )
            meta = SCORER_REGISTRY.get("engineer_service")
            registered = meta is not None
            scorer_label = (meta or {}).get("label", "")
        except Exception as exc:  # pragma: no cover
            logger.warning("xx66_status_registry_failsoft: %s",
                           exc)
        snapshots = await self.repo.list_snapshots(limit=1)
        return {
            "success": True,
            "module": "xx66-ai-engineer",
            "mode": current_mode(),
            "llmMode": llm_mode(),
            "scorerRegistered": registered,
            "scorerId": "engineer_service",
            "scorerLabel": scorer_label,
            "snapshotCount": (snapshots and 1) or 0,
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # 四区聚合(fail-soft 分区)
    # --------------------------------------------------------

    async def vitals(self) -> dict:
        """生命体征图谱(纯读聚合——不落快照, 无模式门槛)"""
        zones = {}

        async def _zone(name, fn):
            try:
                zones[name] = await fn()
            except Exception as exc:
                logger.warning("xx66_zone_%s_failsoft: %s",
                               name, exc)
                zones[name] = {
                    "score": ZONE_YELLOW, "error":
                        str(exc)[:120]}

        await _zone("system", self._zone_system)
        await _zone("business", self._zone_business)
        await _zone("ai_services", self._zone_ai_services)
        await _zone("trust", self._zone_trust)

        zone_scores = {
            k: v.get("score", ZONE_YELLOW)
            for k, v in zones.items()}
        total = sum(zone_scores.values())
        overall = ("healthy" if total <= 1
                   else "critical" if total >= 5
                   else "degraded")
        return {
            "success": True,
            "module": "xx66-ai-engineer",
            "zoneScores": zone_scores,
            "zoneNames": {k: ZONE_NAMES[v]
                          for k, v in zone_scores.items()},
            "totalScore": total,
            "overall": overall,
            "zones": zones,
            "mode": current_mode(),
            "generatedAt": ts(),
        }

    async def _zone_system(self) -> dict:
        """系统区: 26号三类指标异常聚合 + fatal 告警"""
        from repositories.monitor_repository import (
            MonitorRepository,
        )
        repo = MonitorRepository()
        metrics = []
        for mtype in ("system", "performance", "error"):
            metrics.extend(await repo.list_metrics(
                metric_type=mtype, limit=METRIC_SCAN_LIMIT))
        fatal_alerts = [
            a for a in await repo.list_alerts(limit=100)
            if a.get("alertLevel") == "fatal"]

        anomaly_scores = []
        for m in metrics:
            detect = m.get("anomalyDetect") or {}
            score = detect.get("score")
            if isinstance(score, (int, float)):
                anomaly_scores.append(float(score))
        avg_anomaly = (sum(anomaly_scores) / len(anomaly_scores)
                        if anomaly_scores else 0.0)
        yellow = avg_anomaly > 0.5
        red = len(fatal_alerts) > 0
        return {
            "score": _zone_score(yellow, red),
            "metricCount": len(metrics),
            "avgAnomalyScore": round(avg_anomaly, 3),
            "fatalAlerts": len(fatal_alerts),
            "sources": sorted({m.get("source") or ""
                               for m in metrics}),
        }

    async def _zone_business(self) -> dict:
        """业务区: 26号 business 指标 + 45号流水速率 + P0 故障"""
        from repositories.monitor_repository import (
            MonitorRepository,
        )
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        repo = MonitorRepository()
        metrics = await repo.list_metrics(
            metric_type="business", limit=METRIC_SCAN_LIMIT)
        p0_active = [
            i for i in await repo.list_incidents(limit=100)
            if i.get("incidentLevel") == "P0"
            and i.get("incidentStatus") not in (
                "resolved", "postmortem")]

        anomaly_scores = []
        for m in metrics:
            detect = m.get("anomalyDetect") or {}
            score = detect.get("score")
            if isinstance(score, (int, float)):
                anomaly_scores.append(float(score))
        avg_anomaly = (sum(anomaly_scores) / len(anomaly_scores)
                        if anomaly_scores else 0.0)

        try:
            events = await TrustValue45Repository() \
                .list_deposit_events(days=1)
            trust_flow_24h = len(events)
        except Exception:
            trust_flow_24h = 0

        yellow = avg_anomaly > 0.5
        red = len(p0_active) > 0
        return {
            "score": _zone_score(yellow, red),
            "metricCount": len(metrics),
            "avgAnomalyScore": round(avg_anomaly, 3),
            "trustFlow24h": trust_flow_24h,
            "p0ActiveIncidents": len(p0_active),
        }

    async def _zone_ai_services(self) -> dict:
        """AI 服务区: 35号入口健康 + 44号学习域全景"""
        from services.hub_service import hub_service
        hub_health = await hub_service.get_health()
        hub_status = hub_health.get("status", "healthy")

        drift_alerts = 0
        scorer_count = 0
        try:
            from services.ai_learning_service import panorama
            p = await panorama()
            drift_alerts = int(
                (p.get("health") or {}).get(
                    "driftAlerts") or 0)
            scorer_count = p.get("scorerCount") or 0
        except Exception:
            pass

        yellow = (hub_status == "degraded"
                  or drift_alerts > 0)
        red = hub_status == "degraded" and drift_alerts >= 3
        return {
            "score": _zone_score(yellow, red),
            "hubStatus": hub_status,
            "capabilitiesEnabled": hub_health.get(
                "capabilities_enabled"),
            "scorerCount": scorer_count,
            "driftAlerts": drift_alerts,
        }

    async def _zone_trust(self) -> dict:
        """信值区: 47号中枢总览(tier 分布/三联动)
        对账结果 P3 交付——P0 占位 not_implemented"""
        from services.trust_hub_service import TrustHubService
        hub = await TrustHubService().hub_overview()
        tiers = (hub.get("zones") or {}).get("tiers") or {}
        dist = tiers.get("distribution") or {}
        total_profiles = tiers.get("totalProfiles") or 0
        watched = int(dist.get("watched") or 0) \
            + int(dist.get("restricted") or 0)
        watch_ratio = (watched / total_profiles
                       if total_profiles else 0.0)
        yellow = (total_profiles >= TRUST_MIN_SAMPLES
                  and watch_ratio > TRUST_WATCH_RATIO_YELLOW)
        return {
            "score": _zone_score(yellow, False),
            "totalProfiles": total_profiles,
            "watchedRestricted": watched,
            "watchRatio": round(watch_ratio, 3),
            "reconStatus": "not_implemented",
            "note": "对账引擎 P3 交付(四不变式+T+1 调度)",
        }

    # --------------------------------------------------------
    # 快照与巡检(决策面——XX66_MODE 门槛)
    # --------------------------------------------------------

    async def vitals_history(self, limit: int = 20) -> dict:
        """快照时序(观测面——永不关停)"""
        limit = max(1, min(int(limit or 20), 100))
        rows = await self.repo.list_snapshots(limit=limit)
        return {
            "success": True,
            "count": len(rows),
            "snapshots": rows,
            "generatedAt": ts(),
        }

    async def scan(self) -> dict:
        """主动巡检(决策面: 聚合→落快照→黄/红区起 27号自愈链)

        起链语义(对齐 27号 inspect_all 既有行为——纯状态机
        记录零执行): 红区 auto 级 / 黄区 assisted 级
        detect_fault; 自愈执行在 P2 经白名单+预演+46号审批。
        """
        require_active_mode()
        vitals = await self.vitals()

        snapshot_id = await self.repo.next_snapshot_id()
        snapshot = {
            "snapshotId": snapshot_id,
            "zoneScores": vitals["zoneScores"],
            "totalScore": vitals["totalScore"],
            "overall": vitals["overall"],
            "zoneDetails": {
                k: {kk: vv for kk, vv in v.items()
                    if kk != "score"}
                for k, v in vitals["zones"].items()},
            "metricCount": sum(
                int(v.get("metricCount") or 0)
                for v in vitals["zones"].values()),
            "createdAt": ts(),
        }
        await self.repo.save_snapshot(snapshot)

        # 黄/红区起 27号自愈链(纯记录零执行)
        recoveries = []
        try:
            from services.maintenance_service import (
                MaintenanceService,
            )
            msvc = MaintenanceService()
            for zone_name, score in vitals[
                    "zoneScores"].items():
                if score == ZONE_GREEN:
                    continue
                try:
                    rec = await msvc.detect_fault(
                        fault_type="service_degraded"
                        if score == ZONE_YELLOW
                        else "service_down",
                        fault_source=f"xx66:{zone_name}",
                        fault_description=(
                            f"66号巡检: {zone_name} 区"
                            f"{ZONE_NAMES[score]}灯"
                            f"(总分 {vitals['totalScore']})"),
                        recovery_level=("assisted"
                                        if score == ZONE_YELLOW
                                        else "auto"))
                    recoveries.append({
                        "zone": zone_name,
                        "level": ZONE_NAMES[score],
                        "recoveryId": rec.get("id"),
                        "status": rec.get("recoveryStatus"),
                    })
                except Exception as exc:
                    logger.warning(
                        "xx66_scan_fault_%s_failsoft: %s",
                        zone_name, exc)
                    recoveries.append({
                        "zone": zone_name, "error":
                            str(exc)[:80]})
        except Exception as exc:  # pragma: no cover
            logger.warning("xx66_scan_maintenance_failsoft: %s",
                           exc)

        return {
            "success": True,
            "module": "xx66-ai-engineer",
            "mode": current_mode(),
            "overall": vitals["overall"],
            "totalScore": vitals["totalScore"],
            "zoneScores": vitals["zoneScores"],
            "snapshotId": snapshot_id,
            "recoveriesCreated": recoveries,
            "note": "巡检起链为纯状态机记录(零执行)——"
                    "自愈执行在 P2 经动作白名单+沙箱预演"
                    "+46号审批",
            "scannedAt": ts(),
        }
