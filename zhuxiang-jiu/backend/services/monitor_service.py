"""AI 智能监控模块业务逻辑层

核心业务:
    - 指标采集: 系统指标+业务指标+异常检测+阈值校验
    - 告警管理: 创建+确认+解决+抑制(状态机)
    - 故障事件: 创建+调查+处理+解决+复盘(状态机)
    - 仪表盘配置: 自定义监控视图
    - 健康检查: 系统运行状态概览
    - 统计: 监控数据汇总

锁保护:
    - 指标采集: lock:monitor:metrics:{source}:{metricName}
    - 告警管理: lock:monitor:alerts:{alert_id}
    - 故障事件: lock:monitor:incidents:{incident_id}
    - 仪表盘: lock:monitor:dashboards:{dashboardName}

异常约定:
    - KeyError → 404(记录不存在)
    - ValueError → 409(状态非法/参数无效)
"""

from typing import Optional

from core.locks import get_lock
from core.helpers import ts
from repositories.monitor_repository import (
    MonitorRepository,
    # 告警状态
    ALERT_STATUS_PENDING, ALERT_STATUS_ACKNOWLEDGED,
    ALERT_STATUS_RESOLVED, ALERT_STATUS_SUPPRESSED,
    # 告警级别
    ALERT_LEVEL_INFO, ALERT_LEVEL_WARNING,
    ALERT_LEVEL_CRITICAL, ALERT_LEVEL_FATAL,
    # 故障状态
    INCIDENT_STATUS_DETECTED, INCIDENT_STATUS_INVESTIGATING,
    INCIDENT_STATUS_MITIGATING, INCIDENT_STATUS_RESOLVED,
    INCIDENT_STATUS_POSTMORTEM,
    # 故障级别
    INCIDENT_LEVEL_P0, INCIDENT_LEVEL_P1, INCIDENT_LEVEL_P2, INCIDENT_LEVEL_P3,
    # 指标类型
    METRIC_TYPE_SYSTEM, METRIC_TYPE_BUSINESS,
    METRIC_TYPE_PERFORMANCE, METRIC_TYPE_ERROR,
    # 仪表盘类型
    DASHBOARD_TYPE_SYSTEM, DASHBOARD_TYPE_BUSINESS,
    DASHBOARD_TYPE_INCIDENT, DASHBOARD_TYPE_CUSTOM,
)


# ============================================================
# 合法状态集合
# ============================================================

_ALERT_LEVELS = (ALERT_LEVEL_INFO, ALERT_LEVEL_WARNING,
                  ALERT_LEVEL_CRITICAL, ALERT_LEVEL_FATAL)

_ALERT_STATUSES = (ALERT_STATUS_PENDING, ALERT_STATUS_ACKNOWLEDGED,
                    ALERT_STATUS_RESOLVED, ALERT_STATUS_SUPPRESSED)

_INCIDENT_LEVELS = (INCIDENT_LEVEL_P0, INCIDENT_LEVEL_P1,
                      INCIDENT_LEVEL_P2, INCIDENT_LEVEL_P3)

_INCIDENT_STATUSES = (INCIDENT_STATUS_DETECTED, INCIDENT_STATUS_INVESTIGATING,
                        INCIDENT_STATUS_MITIGATING, INCIDENT_STATUS_RESOLVED,
                        INCIDENT_STATUS_POSTMORTEM)

_METRIC_TYPES = (METRIC_TYPE_SYSTEM, METRIC_TYPE_BUSINESS,
                  METRIC_TYPE_PERFORMANCE, METRIC_TYPE_ERROR)

_DASHBOARD_TYPES = (DASHBOARD_TYPE_SYSTEM, DASHBOARD_TYPE_BUSINESS,
                      DASHBOARD_TYPE_INCIDENT, DASHBOARD_TYPE_CUSTOM)

# ============================================================
# 状态流转表(合法前驱态)
# ============================================================

# 告警状态流转: 目标态 → 允许的来源态集合
_ALERT_TRANSITIONS = {
    ALERT_STATUS_ACKNOWLEDGED: {ALERT_STATUS_PENDING},
    ALERT_STATUS_RESOLVED: {ALERT_STATUS_ACKNOWLEDGED},
    ALERT_STATUS_SUPPRESSED: {ALERT_STATUS_PENDING, ALERT_STATUS_ACKNOWLEDGED,
                                ALERT_STATUS_RESOLVED},
}

# 故障状态流转: 目标态 → 允许的来源态集合
_INCIDENT_TRANSITIONS = {
    INCIDENT_STATUS_INVESTIGATING: {INCIDENT_STATUS_DETECTED},
    INCIDENT_STATUS_MITIGATING: {INCIDENT_STATUS_INVESTIGATING},
    INCIDENT_STATUS_RESOLVED: {INCIDENT_STATUS_MITIGATING},
    INCIDENT_STATUS_POSTMORTEM: {INCIDENT_STATUS_RESOLVED},
}


class MonitorService:
    """AI 智能监控业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: MonitorRepository = MonitorRepository()):
        self.repo = repo

    # ============================================================
    # 1. 指标采集
    # ============================================================

    async def collect_metric(self, metric_name: str, metric_type: str,
                                metric_value: float, source: str,
                                metric_unit: str = "",
                                tags: dict = None,
                                threshold: dict = None,
                                ai_automation_rate: float = 95.0) -> dict:
        """采集监控指标

        规则:
            - 校验指标类型合法
            - 异常检测(对照阈值)
            - 写入存储
        """
        if not metric_name or not source:
            raise ValueError("指标名称和采集来源不可为空")

        if metric_type not in _METRIC_TYPES:
            raise ValueError(f"非法指标类型: {metric_type}")

        # 异常检测(对照阈值)
        anomaly = self._detect_anomaly(metric_value, threshold)

        lock_key = f"monitor:metrics:{source}:{metric_name}"
        async with get_lock(lock_key):
            record = {
                "metricName": metric_name,
                "metricType": metric_type,
                "metricValue": metric_value,
                "metricUnit": metric_unit,
                "source": source,
                "tags": tags or {},
                "threshold": threshold or {},
                "anomalyDetect": anomaly,
                "aiAutomationRate": ai_automation_rate,
                "createdAt": ts(),
            }
            record_id = await self.repo.create_metric(record)
            record["id"] = record_id
            return record

    async def get_metric(self, record_id: int) -> dict:
        """查询指标详情"""
        record = await self.repo.get_metric(record_id)
        if record is None:
            raise KeyError(f"监控指标不存在(id={record_id})")
        return record

    async def list_metrics(self, metric_name: str = None,
                             metric_type: str = None,
                             source: str = None,
                             limit: int = 50) -> list[dict]:
        """查询指标列表"""
        return await self.repo.list_metrics(metric_name, metric_type, source, limit)

    # ============================================================
    # 2. 告警管理
    # ============================================================

    async def raise_alert(self, alert_name: str, alert_type: str,
                            alert_level: str, source: str,
                            metric_id: int = None,
                            threshold: dict = None,
                            current_value: float = 0.0,
                            description: str = "",
                            notification: dict = None,
                            ai_automation_rate: float = 95.0) -> dict:
        """创建告警

        规则:
            - 校验告警级别合法
            - 状态初始化为 pending
            - 根据级别填充通知渠道
        """
        if not alert_name or not alert_type or not source:
            raise ValueError("告警名称、类型和来源不可为空")

        if alert_level not in _ALERT_LEVELS:
            raise ValueError(f"非法告警级别: {alert_level}")

        lock_key = f"monitor:alerts:{alert_type}:{source}"
        async with get_lock(lock_key):
            # 自动填充通知渠道
            if notification is None:
                notification = self._default_notification(alert_level)

            record = {
                "alertName": alert_name,
                "alertType": alert_type,
                "alertLevel": alert_level,
                "alertStatus": ALERT_STATUS_PENDING,
                "source": source,
                "metricId": metric_id,
                "threshold": threshold or {},
                "currentValue": current_value,
                "description": description,
                "notification": notification,
                "acknowledgedBy": "",
                "acknowledgedAt": "",
                "resolvedBy": "",
                "resolvedAt": "",
                "suppressedBy": "",
                "suppressedAt": "",
                "aiAutomationRate": ai_automation_rate,
                "createdAt": ts(),
            }
            record_id = await self.repo.create_alert(record)
            record["id"] = record_id
            return record

    async def acknowledge_alert(self, alert_id: int,
                                  acknowledger: str = "admin") -> dict:
        """确认告警(pending → acknowledged)"""
        lock_key = f"monitor:alerts:{alert_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_alert(alert_id)
            if record is None:
                raise KeyError(f"告警记录不存在(id={alert_id})")

            self._check_transition("alert", record["alertStatus"],
                                     ALERT_STATUS_ACKNOWLEDGED)

            updates = {
                "alertStatus": ALERT_STATUS_ACKNOWLEDGED,
                "acknowledgedBy": acknowledger,
                "acknowledgedAt": ts(),
            }
            await self.repo.update_alert(alert_id, updates)
            record.update(updates)
            return record

    async def resolve_alert(self, alert_id: int,
                               resolver: str = "admin") -> dict:
        """解决告警(acknowledged → resolved)"""
        lock_key = f"monitor:alerts:{alert_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_alert(alert_id)
            if record is None:
                raise KeyError(f"告警记录不存在(id={alert_id})")

            self._check_transition("alert", record["alertStatus"],
                                     ALERT_STATUS_RESOLVED)

            updates = {
                "alertStatus": ALERT_STATUS_RESOLVED,
                "resolvedBy": resolver,
                "resolvedAt": ts(),
            }
            await self.repo.update_alert(alert_id, updates)
            record.update(updates)
            return record

    async def suppress_alert(self, alert_id: int,
                                suppressor: str = "admin") -> dict:
        """抑制告警(任意态 → suppressed)"""
        lock_key = f"monitor:alerts:{alert_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_alert(alert_id)
            if record is None:
                raise KeyError(f"告警记录不存在(id={alert_id})")

            self._check_transition("alert", record["alertStatus"],
                                     ALERT_STATUS_SUPPRESSED)

            updates = {
                "alertStatus": ALERT_STATUS_SUPPRESSED,
                "suppressedBy": suppressor,
                "suppressedAt": ts(),
            }
            await self.repo.update_alert(alert_id, updates)
            record.update(updates)
            return record

    async def get_alert(self, record_id: int) -> dict:
        """查询告警详情"""
        record = await self.repo.get_alert(record_id)
        if record is None:
            raise KeyError(f"告警记录不存在(id={record_id})")
        return record

    async def list_alerts(self, alert_type: str = None,
                            alert_level: str = None,
                            alert_status: str = None,
                            limit: int = 50) -> list[dict]:
        """查询告警列表"""
        return await self.repo.list_alerts(alert_type, alert_level, alert_status, limit)

    # ============================================================
    # 3. 故障事件
    # ============================================================

    async def raise_incident(self, incident_name: str, incident_type: str,
                               incident_level: str, source: str,
                               impact: dict = None,
                               alert_ids: list = None,
                               assignee: str = "",
                               ai_automation_rate: float = 85.0) -> dict:
        """创建故障事件

        规则:
            - 校验故障级别合法
            - 状态初始化为 detected
            - 时间线写入"检测"事件
        """
        if not incident_name or not incident_type or not source:
            raise ValueError("故障名称、类型和来源不可为空")

        if incident_level not in _INCIDENT_LEVELS:
            raise ValueError(f"非法故障级别: {incident_level}")

        lock_key = f"monitor:incidents:{incident_type}:{source}"
        async with get_lock(lock_key):
            now = ts()
            record = {
                "incidentName": incident_name,
                "incidentType": incident_type,
                "incidentLevel": incident_level,
                "incidentStatus": INCIDENT_STATUS_DETECTED,
                "source": source,
                "alertIds": alert_ids or [],
                "impact": impact or {},
                "rootCause": "",
                "mitigation": "",
                "timeline": [{"at": now, "event": "检测", "operator": assignee}],
                "assignee": assignee,
                "resolvedAt": "",
                "postmortemAt": "",
                "postmortemDoc": "",
                "aiAutomationRate": ai_automation_rate,
                "createdAt": now,
            }
            record_id = await self.repo.create_incident(record)
            record["id"] = record_id
            return record

    async def investigate_incident(self, incident_id: int,
                                       operator: str = "admin",
                                       root_cause: str = "") -> dict:
        """推进故障至调查中(detected → investigating)"""
        lock_key = f"monitor:incidents:{incident_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_incident(incident_id)
            if record is None:
                raise KeyError(f"故障事件不存在(id={incident_id})")

            self._check_transition("incident", record["incidentStatus"],
                                     INCIDENT_STATUS_INVESTIGATING)

            now = ts()
            timeline = record.get("timeline", [])
            timeline.append({"at": now, "event": "开始调查", "operator": operator})
            updates = {
                "incidentStatus": INCIDENT_STATUS_INVESTIGATING,
                "rootCause": root_cause or record.get("rootCause", ""),
                "timeline": timeline,
            }
            await self.repo.update_incident(incident_id, updates)
            record.update(updates)
            return record

    async def mitigate_incident(self, incident_id: int,
                                   operator: str = "admin",
                                   mitigation: str = "") -> dict:
        """推进故障至处理中(investigating → mitigating)"""
        lock_key = f"monitor:incidents:{incident_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_incident(incident_id)
            if record is None:
                raise KeyError(f"故障事件不存在(id={incident_id})")

            self._check_transition("incident", record["incidentStatus"],
                                     INCIDENT_STATUS_MITIGATING)

            now = ts()
            timeline = record.get("timeline", [])
            timeline.append({"at": now, "event": "处置中", "operator": operator})
            updates = {
                "incidentStatus": INCIDENT_STATUS_MITIGATING,
                "mitigation": mitigation or record.get("mitigation", ""),
                "timeline": timeline,
            }
            await self.repo.update_incident(incident_id, updates)
            record.update(updates)
            return record

    async def resolve_incident(self, incident_id: int,
                                   operator: str = "admin") -> dict:
        """推进故障至已解决(mitigating → resolved)"""
        lock_key = f"monitor:incidents:{incident_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_incident(incident_id)
            if record is None:
                raise KeyError(f"故障事件不存在(id={incident_id})")

            self._check_transition("incident", record["incidentStatus"],
                                     INCIDENT_STATUS_RESOLVED)

            now = ts()
            timeline = record.get("timeline", [])
            timeline.append({"at": now, "event": "已解决", "operator": operator})
            updates = {
                "incidentStatus": INCIDENT_STATUS_RESOLVED,
                "resolvedAt": now,
                "timeline": timeline,
            }
            await self.repo.update_incident(incident_id, updates)
            record.update(updates)
            return record

    async def postmortem_incident(self, incident_id: int,
                                     operator: str = "admin",
                                     postmortem_doc: str = "") -> dict:
        """推进故障至已复盘(resolved → postmortem)"""
        lock_key = f"monitor:incidents:{incident_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_incident(incident_id)
            if record is None:
                raise KeyError(f"故障事件不存在(id={incident_id})")

            self._check_transition("incident", record["incidentStatus"],
                                     INCIDENT_STATUS_POSTMORTEM)

            now = ts()
            timeline = record.get("timeline", [])
            timeline.append({"at": now, "event": "复盘完成", "operator": operator})
            updates = {
                "incidentStatus": INCIDENT_STATUS_POSTMORTEM,
                "postmortemAt": now,
                "postmortemDoc": postmortem_doc,
                "timeline": timeline,
            }
            await self.repo.update_incident(incident_id, updates)
            record.update(updates)
            return record

    async def get_incident(self, record_id: int) -> dict:
        """查询故障详情"""
        record = await self.repo.get_incident(record_id)
        if record is None:
            raise KeyError(f"故障事件不存在(id={record_id})")
        return record

    async def list_incidents(self, incident_type: str = None,
                                incident_level: str = None,
                                incident_status: str = None,
                                limit: int = 50) -> list[dict]:
        """查询故障列表"""
        return await self.repo.list_incidents(incident_type, incident_level,
                                                 incident_status, limit)

    # ============================================================
    # 4. 仪表盘配置
    # ============================================================

    async def create_dashboard(self, dashboard_name: str, dashboard_type: str,
                                  owner: str = "admin",
                                  widgets: list = None,
                                  layout: dict = None,
                                  filters: dict = None,
                                  refresh_interval: int = 30,
                                  is_shared: bool = False,
                                  ai_automation_rate: float = 85.0) -> dict:
        """创建仪表盘

        规则:
            - 校验仪表盘类型合法
            - 写入配置
        """
        if not dashboard_name:
            raise ValueError("仪表盘名称不可为空")

        if dashboard_type not in _DASHBOARD_TYPES:
            raise ValueError(f"非法仪表盘类型: {dashboard_type}")

        lock_key = f"monitor:dashboards:{dashboard_name}"
        async with get_lock(lock_key):
            record = {
                "dashboardName": dashboard_name,
                "dashboardType": dashboard_type,
                "owner": owner,
                "widgets": widgets or [],
                "layout": layout or {},
                "filters": filters or {},
                "refreshInterval": refresh_interval,
                "isShared": is_shared,
                "aiAutomationRate": ai_automation_rate,
                "createdAt": ts(),
            }
            record_id = await self.repo.create_dashboard(record)
            record["id"] = record_id
            return record

    async def get_dashboard(self, record_id: int) -> dict:
        """查询仪表盘详情"""
        record = await self.repo.get_dashboard(record_id)
        if record is None:
            raise KeyError(f"仪表盘不存在(id={record_id})")
        return record

    async def list_dashboards(self, dashboard_type: str = None,
                                owner: str = None,
                                limit: int = 50) -> list[dict]:
        """查询仪表盘列表"""
        return await self.repo.list_dashboards(dashboard_type, owner, limit)

    # ============================================================
    # 5. 健康检查
    # ============================================================

    async def health_check(self) -> dict:
        """健康检查(系统运行状态概览)"""
        metrics = await self.repo.list_metrics(limit=10000)
        alerts = await self.repo.list_alerts(limit=10000)
        incidents = await self.repo.list_incidents(limit=10000)
        dashboards = await self.repo.list_dashboards(limit=10000)

        # 待处理告警数
        pending_alerts = sum(1 for a in alerts
                              if a.get("alertStatus") == ALERT_STATUS_PENDING)
        # 活跃故障数(非 resolved/postmortem)
        active_incidents = sum(1 for i in incidents
                                if i.get("incidentStatus") in
                                (INCIDENT_STATUS_DETECTED,
                                 INCIDENT_STATUS_INVESTIGATING,
                                 INCIDENT_STATUS_MITIGATING))
        # 致命告警数
        fatal_alerts = sum(1 for a in alerts
                            if a.get("alertLevel") == ALERT_LEVEL_FATAL)
        # P0 故障数
        p0_incidents = sum(1 for i in incidents
                            if i.get("incidentLevel") == INCIDENT_LEVEL_P0)

        # 系统状态判定
        if p0_incidents > 0 or fatal_alerts > 0:
            status = "critical"
        elif active_incidents > 0 or pending_alerts > 0:
            status = "warning"
        else:
            status = "healthy"

        return {
            "status": status,
            "totalMetrics": len(metrics),
            "totalAlerts": len(alerts),
            "totalIncidents": len(incidents),
            "totalDashboards": len(dashboards),
            "pendingAlerts": pending_alerts,
            "activeIncidents": active_incidents,
            "fatalAlerts": fatal_alerts,
            "p0Incidents": p0_incidents,
            "checkedAt": ts(),
        }

    # ============================================================
    # 6. 统计
    # ============================================================

    async def get_stats(self) -> dict:
        """监控统计"""
        metrics = await self.repo.list_metrics(limit=10000)
        alerts = await self.repo.list_alerts(limit=10000)
        incidents = await self.repo.list_incidents(limit=10000)
        dashboards = await self.repo.list_dashboards(limit=10000)

        # 告警状态分布
        alert_status_count = {}
        for a in alerts:
            s = a.get("alertStatus", "unknown")
            alert_status_count[s] = alert_status_count.get(s, 0) + 1

        # 告警级别分布
        alert_level_count = {}
        for a in alerts:
            lv = a.get("alertLevel", "unknown")
            alert_level_count[lv] = alert_level_count.get(lv, 0) + 1

        # 故障状态分布
        incident_status_count = {}
        for i in incidents:
            s = i.get("incidentStatus", "unknown")
            incident_status_count[s] = incident_status_count.get(s, 0) + 1

        # 故障级别分布
        incident_level_count = {}
        for i in incidents:
            lv = i.get("incidentLevel", "unknown")
            incident_level_count[lv] = incident_level_count.get(lv, 0) + 1

        # 指标类型分布
        metric_type_count = {}
        for m in metrics:
            t = m.get("metricType", "unknown")
            metric_type_count[t] = metric_type_count.get(t, 0) + 1

        return {
            "totalMetrics": len(metrics),
            "totalAlerts": len(alerts),
            "totalIncidents": len(incidents),
            "totalDashboards": len(dashboards),
            "alertStatusCount": alert_status_count,
            "alertLevelCount": alert_level_count,
            "incidentStatusCount": incident_status_count,
            "incidentLevelCount": incident_level_count,
            "metricTypeCount": metric_type_count,
        }

    # ============================================================
    # 辅助方法
    # ============================================================

    def _detect_anomaly(self, value: float, threshold: dict) -> dict:
        """异常检测(对照阈值)"""
        if not threshold:
            return {"detected": False, "score": 0.0}

        warning = threshold.get("warning")
        critical = threshold.get("critical")

        if critical is not None and value >= critical:
            return {"detected": True, "score": 1.0, "level": ALERT_LEVEL_CRITICAL}
        if warning is not None and value >= warning:
            return {"detected": True, "score": 0.5, "level": ALERT_LEVEL_WARNING}
        return {"detected": False, "score": 0.0}

    def _default_notification(self, alert_level: str) -> dict:
        """按级别填充默认通知渠道"""
        channels_map = {
            ALERT_LEVEL_INFO: ["log"],
            ALERT_LEVEL_WARNING: ["inbox"],
            ALERT_LEVEL_CRITICAL: ["sms", "inbox"],
            ALERT_LEVEL_FATAL: ["phone", "sms", "inbox"],
        }
        return {
            "channels": channels_map.get(alert_level, ["log"]),
            "sent": False,
        }

    def _check_transition(self, entity: str, current: str, target: str) -> None:
        """校验状态流转是否合法

        Args:
            entity: "alert" 或 "incident"
            current: 当前状态
            target: 目标状态

        Raises:
            ValueError: 状态流转非法
        """
        transitions = _ALERT_TRANSITIONS if entity == "alert" else _INCIDENT_TRANSITIONS
        if target not in transitions:
            raise ValueError(f"非法目标状态: {target}")
        allowed_sources = transitions[target]
        if current not in allowed_sources:
            raise ValueError(
                f"状态流转非法(当前{current}, 须为{allowed_sources}才能流转至{target})"
            )
