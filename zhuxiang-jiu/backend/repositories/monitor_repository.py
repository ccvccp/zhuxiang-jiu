"""AI 智能监控模块数据访问层(双模式: 内存 + Redis)

表清单:
    monitor_metrics:    监控指标表(CPU/内存/QPS/延迟/错误率/业务量)
    monitor_alerts:     告警记录表(异常检测→分级→通知)
    monitor_dashboards: 仪表盘配置表(自定义监控视图)
    monitor_incidents:  故障事件表(故障追踪→定级→处理→复盘)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 告警状态: pending/acknowledged/resolved/suppressed
    - 告警级别: info/warning/critical/fatal
    - 故障状态: detected/investigating/mitigating/resolved/postmortem
    - 故障级别: P0/P1/P2/P3
"""

import json
from datetime import datetime
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 告警状态
# ============================================================

ALERT_STATUS_PENDING = "pending"            # 待处理
ALERT_STATUS_ACKNOWLEDGED = "acknowledged"  # 已确认
ALERT_STATUS_RESOLVED = "resolved"           # 已解决
ALERT_STATUS_SUPPRESSED = "suppressed"       # 已抑制

# 告警级别
ALERT_LEVEL_INFO = "info"          # 信息
ALERT_LEVEL_WARNING = "warning"    # 警告
ALERT_LEVEL_CRITICAL = "critical"  # 严重
ALERT_LEVEL_FATAL = "fatal"       # 致命

# 故障状态
INCIDENT_STATUS_DETECTED = "detected"            # 已发现
INCIDENT_STATUS_INVESTIGATING = "investigating"  # 调查中
INCIDENT_STATUS_MITIGATING = "mitigating"        # 处理中
INCIDENT_STATUS_RESOLVED = "resolved"            # 已解决
INCIDENT_STATUS_POSTMORTEM = "postmortem"        # 已复盘

# 故障级别
INCIDENT_LEVEL_P0 = "P0"  # 致命
INCIDENT_LEVEL_P1 = "P1"  # 严重
INCIDENT_LEVEL_P2 = "P2"  # 中等
INCIDENT_LEVEL_P3 = "P3"  # 轻微

# 指标类型
METRIC_TYPE_SYSTEM = "system"            # 系统指标
METRIC_TYPE_BUSINESS = "business"        # 业务指标
METRIC_TYPE_PERFORMANCE = "performance"  # 性能指标
METRIC_TYPE_ERROR = "error"              # 错误指标

# 仪表盘类型
DASHBOARD_TYPE_SYSTEM = "system"      # 系统仪表盘
DASHBOARD_TYPE_BUSINESS = "business"  # 业务仪表盘
DASHBOARD_TYPE_INCIDENT = "incident"  # 故障仪表盘
DASHBOARD_TYPE_CUSTOM = "custom"      # 自定义仪表盘


class MonitorRepository:
    """AI 智能监控数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_metric_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("metrics")
        return self._mem_next_id("_monitor_metrics_seq")

    async def next_alert_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("alerts")
        return self._mem_next_id("_monitor_alerts_seq")

    async def next_dashboard_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("dashboards")
        return self._mem_next_id("_monitor_dashboards_seq")

    async def next_incident_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("incidents")
        return self._mem_next_id("_monitor_incidents_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("monitor", entity, "seq"))

    # ============================================================
    # 监控指标表 CRUD
    # ============================================================

    async def create_metric(self, record: dict) -> int:
        record_id = await self.next_metric_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "metricValue" not in record:
            record["metricValue"] = 0.0
        if is_redis_mode():
            await self._redis_create("monitor", "metrics", record_id, record)
        else:
            self._mem_create("monitor_metrics", record_id, record)
        return record_id

    async def get_metric(self, record_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get("monitor", "metrics", record_id)
        return self._mem_get("monitor_metrics", record_id)

    async def list_metrics(self, metric_name: str = None,
                            metric_type: str = None,
                            source: str = None,
                            limit: int = 50) -> list[dict]:
        """支持三重过滤: 指标名 / 指标类型 / 来源"""
        if is_redis_mode():
            records = await self._redis_list_all("monitor", "metrics", limit * 5)
        else:
            records = self._mem_list_all("monitor_metrics", limit * 5)
        if metric_name:
            records = [r for r in records if r.get("metricName") == metric_name]
        if metric_type:
            records = [r for r in records if r.get("metricType") == metric_type]
        if source:
            records = [r for r in records if r.get("source") == source]
        records.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return records[:limit]

    async def update_metric(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("monitor", "metrics", record_id, updates)
        else:
            self._mem_update("monitor_metrics", record_id, updates)

    # ============================================================
    # 告警记录表 CRUD
    # ============================================================

    async def create_alert(self, record: dict) -> int:
        record_id = await self.next_alert_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "alertStatus" not in record:
            record["alertStatus"] = ALERT_STATUS_PENDING
        if "currentValue" not in record:
            record["currentValue"] = 0.0
        if is_redis_mode():
            await self._redis_create("monitor", "alerts", record_id, record)
        else:
            self._mem_create("monitor_alerts", record_id, record)
        return record_id

    async def get_alert(self, record_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get("monitor", "alerts", record_id)
        return self._mem_get("monitor_alerts", record_id)

    async def list_alerts(self, alert_type: str = None,
                            alert_level: str = None,
                            alert_status: str = None,
                            limit: int = 50) -> list[dict]:
        """支持三重过滤: 告警类型 / 级别 / 状态"""
        if is_redis_mode():
            records = await self._redis_list_all("monitor", "alerts", limit * 5)
        else:
            records = self._mem_list_all("monitor_alerts", limit * 5)
        if alert_type:
            records = [r for r in records if r.get("alertType") == alert_type]
        if alert_level:
            records = [r for r in records if r.get("alertLevel") == alert_level]
        if alert_status:
            records = [r for r in records if r.get("alertStatus") == alert_status]
        records.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return records[:limit]

    async def update_alert(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("monitor", "alerts", record_id, updates)
        else:
            self._mem_update("monitor_alerts", record_id, updates)

    # ============================================================
    # 仪表盘配置表 CRUD
    # ============================================================

    async def create_dashboard(self, record: dict) -> int:
        record_id = await self.next_dashboard_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "widgets" not in record:
            record["widgets"] = []
        if "isShared" not in record:
            record["isShared"] = False
        if is_redis_mode():
            await self._redis_create("monitor", "dashboards", record_id, record)
        else:
            self._mem_create("monitor_dashboards", record_id, record)
        return record_id

    async def get_dashboard(self, record_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get("monitor", "dashboards", record_id)
        return self._mem_get("monitor_dashboards", record_id)

    async def list_dashboards(self, dashboard_type: str = None,
                                owner: str = None,
                                limit: int = 50) -> list[dict]:
        if is_redis_mode():
            records = await self._redis_list_all("monitor", "dashboards", limit * 5)
        else:
            records = self._mem_list_all("monitor_dashboards", limit * 5)
        if dashboard_type:
            records = [r for r in records if r.get("dashboardType") == dashboard_type]
        if owner:
            records = [r for r in records if r.get("owner") == owner]
        records.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return records[:limit]

    async def update_dashboard(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("monitor", "dashboards", record_id, updates)
        else:
            self._mem_update("monitor_dashboards", record_id, updates)

    # ============================================================
    # 故障事件表 CRUD
    # ============================================================

    async def create_incident(self, record: dict) -> int:
        record_id = await self.next_incident_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "incidentStatus" not in record:
            record["incidentStatus"] = INCIDENT_STATUS_DETECTED
        if "timeline" not in record:
            record["timeline"] = []
        if is_redis_mode():
            await self._redis_create("monitor", "incidents", record_id, record)
        else:
            self._mem_create("monitor_incidents", record_id, record)
        return record_id

    async def get_incident(self, record_id: int) -> Optional[dict]:
        if is_redis_mode():
            return await self._redis_get("monitor", "incidents", record_id)
        return self._mem_get("monitor_incidents", record_id)

    async def list_incidents(self, incident_type: str = None,
                               incident_level: str = None,
                               incident_status: str = None,
                               limit: int = 50) -> list[dict]:
        """支持三重过滤: 故障类型 / 级别 / 状态"""
        if is_redis_mode():
            records = await self._redis_list_all("monitor", "incidents", limit * 5)
        else:
            records = self._mem_list_all("monitor_incidents", limit * 5)
        if incident_type:
            records = [r for r in records if r.get("incidentType") == incident_type]
        if incident_level:
            records = [r for r in records if r.get("incidentLevel") == incident_level]
        if incident_status:
            records = [r for r in records if r.get("incidentStatus") == incident_status]
        records.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return records[:limit]

    async def update_incident(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("monitor", "incidents", record_id, updates)
        else:
            self._mem_update("monitor_incidents", record_id, updates)

    # ============================================================
    # 内存模式通用实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含监控模块的键(懒初始化)"""
        if "monitor_metrics" not in self.store:
            self.store["monitor_metrics"] = {}
            self.store["monitor_alerts"] = {}
            self.store["monitor_dashboards"] = {}
            self.store["monitor_incidents"] = {}
            self.store["_monitor_metrics_seq"] = 0
            self.store["_monitor_alerts_seq"] = 0
            self.store["_monitor_dashboards_seq"] = 0
            self.store["_monitor_incidents_seq"] = 0

    def _mem_create(self, table: str, record_id: int, record: dict) -> None:
        self._ensure_store()
        self.store[table][record_id] = record

    def _mem_get(self, table: str, record_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store[table].get(record_id)

    def _mem_update(self, table: str, record_id: int, updates: dict) -> None:
        self._ensure_store()
        record = self.store[table].get(record_id)
        if record:
            record.update(updates)

    def _mem_list_all(self, table: str, limit: int) -> list[dict]:
        self._ensure_store()
        records = list(self.store[table].values())
        records.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return records[:limit]

    # ============================================================
    # Redis 模式通用实现
    # ============================================================

    async def _redis_create(self, module: str, entity: str, record_id: int,
                              record: dict) -> None:
        client = await get_redis_client()
        await client.set(_k(module, entity, record_id),
                         json.dumps(record, ensure_ascii=False))

    async def _redis_get(self, module: str, entity: str,
                           record_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k(module, entity, record_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_update(self, module: str, entity: str,
                              record_id: int, updates: dict) -> None:
        client = await get_redis_client()
        data = await client.get(_k(module, entity, record_id))
        if data:
            record = json.loads(data)
            record.update(updates)
            await client.set(_k(module, entity, record_id),
                             json.dumps(record, ensure_ascii=False))

    async def _redis_list_all(self, module: str, entity: str,
                                limit: int) -> list[dict]:
        client = await get_redis_client()
        records = []
        keys = await client.keys(_k(module, entity, "*"))
        for key in keys:
            if "seq" in key:
                continue
            data = await client.get(key)
            if data:
                records.append(json.loads(data))
        records.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return records[:limit]
