"""AI 智能维护模块数据访问层(双模式: 内存 + Redis)

表清单:
    maintenance_tasks:        维护任务表(备份/清理/优化/巡检/重启/扩容)
    maintenance_health:       健康检查表(服务健康状态检查项+结果)
    maintenance_recovery:     故障自愈表(检测→诊断→恢复策略→执行结果)
    maintenance_optimization: 性能优化表(建议→审批→执行→效果评估)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 任务状态: pending/running/success/failed/cancelled
    - 任务类型: backup/cleanup/optimize/inspect/restart/scale
    - 健康状态: healthy/degraded/unhealthy/unknown
    - 自愈状态: detected/diagnosing/recovering/recovered/failed/manual_required
    - 自愈级别: auto/assisted/manual
    - 优化状态: proposed/approved/executing/completed/rejected
"""

import json
from datetime import datetime

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 任务状态
# ============================================================

TASK_STATUS_PENDING = "pending"      # 待执行
TASK_STATUS_RUNNING = "running"      # 执行中
TASK_STATUS_SUCCESS = "success"      # 成功
TASK_STATUS_FAILED = "failed"        # 失败
TASK_STATUS_CANCELLED = "cancelled"   # 已取消

# 任务类型
TASK_TYPE_BACKUP = "backup"          # 备份
TASK_TYPE_CLEANUP = "cleanup"        # 清理
TASK_TYPE_OPTIMIZE = "optimize"       # 优化
TASK_TYPE_INSPECT = "inspect"        # 巡检
TASK_TYPE_RESTART = "restart"        # 重启
TASK_TYPE_SCALE = "scale"            # 扩容

# 触发类型
TRIGGER_MANUAL = "manual"            # 手动
TRIGGER_SCHEDULED = "scheduled"       # 定时

# ============================================================
# 健康状态
# ============================================================

HEALTH_HEALTHY = "healthy"           # 健康
HEALTH_DEGRADED = "degraded"         # 降级
HEALTH_UNHEALTHY = "unhealthy"       # 不健康
HEALTH_UNKNOWN = "unknown"           # 未知

# 检查类型
CHECK_TYPE_HTTP = "http"             # HTTP 检查
CHECK_TYPE_TCP = "tcp"               # TCP 检查
CHECK_TYPE_RESOURCE = "resource"     # 资源检查
CHECK_TYPE_CUSTOM = "custom"         # 自定义

# ============================================================
# 自愈状态
# ============================================================

RECOVERY_STATUS_DETECTED = "detected"               # 检测到
RECOVERY_STATUS_DIAGNOSING = "diagnosing"            # 诊断中
RECOVERY_STATUS_RECOVERING = "recovering"            # 恢复中
RECOVERY_STATUS_RECOVERED = "recovered"              # 已恢复
RECOVERY_STATUS_FAILED = "failed"                    # 恢复失败
RECOVERY_STATUS_MANUAL_REQUIRED = "manual_required"  # 需人工

# 自愈级别
RECOVERY_LEVEL_AUTO = "auto"           # 自动
RECOVERY_LEVEL_ASSISTED = "assisted"   # 辅助
RECOVERY_LEVEL_MANUAL = "manual"       # 人工

# ============================================================
# 优化状态
# ============================================================

OPTIMIZATION_STATUS_PROPOSED = "proposed"      # 建议中
OPTIMIZATION_STATUS_APPROVED = "approved"      # 已批准
OPTIMIZATION_STATUS_EXECUTING = "executing"    # 执行中
OPTIMIZATION_STATUS_COMPLETED = "completed"   # 已完成
OPTIMIZATION_STATUS_REJECTED = "rejected"     # 已驳回


class MaintenanceRepository:
    """AI 智能维护数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_task_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("task")
        return self._mem_next_id("_maintenance_task_seq")

    async def next_health_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("health")
        return self._mem_next_id("_maintenance_health_seq")

    async def next_recovery_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("recovery")
        return self._mem_next_id("_maintenance_recovery_seq")

    async def next_optimization_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("optimization")
        return self._mem_next_id("_maintenance_optimization_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("maintenance", entity, "seq"))

    # ============================================================
    # 维护任务表 CRUD
    # ============================================================

    async def create_task(self, record: dict) -> int:
        record_id = await self.next_task_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "taskStatus" not in record:
            record["taskStatus"] = TASK_STATUS_PENDING
        if is_redis_mode():
            await self._redis_create("task", record_id, record)
        else:
            self._mem_create("maintenance_tasks", record_id, record)
        return record_id

    async def get_task(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("task", record_id)
        return self._mem_get("maintenance_tasks", record_id)

    async def list_tasks(self, task_type: str = None,
                          task_status: str = None,
                          limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list("task", task_type, "taskType",
                                          task_status, "taskStatus", limit)
        return self._mem_list("maintenance_tasks", task_type, "taskType",
                              task_status, "taskStatus", limit)

    async def update_task(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("task", record_id, updates)
        else:
            self._mem_update("maintenance_tasks", record_id, updates)

    # ============================================================
    # 健康检查表 CRUD
    # ============================================================

    async def create_health(self, record: dict) -> int:
        record_id = await self.next_health_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "healthStatus" not in record:
            record["healthStatus"] = HEALTH_UNKNOWN
        if is_redis_mode():
            await self._redis_create("health", record_id, record)
        else:
            self._mem_create("maintenance_health", record_id, record)
        return record_id

    async def get_health(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("health", record_id)
        return self._mem_get("maintenance_health", record_id)

    async def list_health(self, service_name: str = None,
                          health_status: str = None,
                          limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list("health", service_name, "serviceName",
                                          health_status, "healthStatus", limit)
        return self._mem_list("maintenance_health", service_name, "serviceName",
                              health_status, "healthStatus", limit)

    async def update_health(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("health", record_id, updates)
        else:
            self._mem_update("maintenance_health", record_id, updates)

    # ============================================================
    # 故障自愈表 CRUD
    # ============================================================

    async def create_recovery(self, record: dict) -> int:
        record_id = await self.next_recovery_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "recoveryStatus" not in record:
            record["recoveryStatus"] = RECOVERY_STATUS_DETECTED
        if "recoveryLevel" not in record:
            record["recoveryLevel"] = RECOVERY_LEVEL_AUTO
        if is_redis_mode():
            await self._redis_create("recovery", record_id, record)
        else:
            self._mem_create("maintenance_recovery", record_id, record)
        return record_id

    async def get_recovery(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("recovery", record_id)
        return self._mem_get("maintenance_recovery", record_id)

    async def list_recoveries(self, fault_type: str = None,
                               recovery_status: str = None,
                               limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list("recovery", fault_type, "faultType",
                                          recovery_status, "recoveryStatus", limit)
        return self._mem_list("maintenance_recovery", fault_type, "faultType",
                              recovery_status, "recoveryStatus", limit)

    async def update_recovery(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("recovery", record_id, updates)
        else:
            self._mem_update("maintenance_recovery", record_id, updates)

    # ============================================================
    # 性能优化表 CRUD
    # ============================================================

    async def create_optimization(self, record: dict) -> int:
        record_id = await self.next_optimization_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "optimizationStatus" not in record:
            record["optimizationStatus"] = OPTIMIZATION_STATUS_PROPOSED
        if is_redis_mode():
            await self._redis_create("optimization", record_id, record)
        else:
            self._mem_create("maintenance_optimization", record_id, record)
        return record_id

    async def get_optimization(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("optimization", record_id)
        return self._mem_get("maintenance_optimization", record_id)

    async def list_optimizations(self, optimization_type: str = None,
                                  optimization_status: str = None,
                                  limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list("optimization", optimization_type,
                                          "optimizationType", optimization_status,
                                          "optimizationStatus", limit)
        return self._mem_list("maintenance_optimization", optimization_type,
                              "optimizationType", optimization_status,
                              "optimizationStatus", limit)

    async def update_optimization(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("optimization", record_id, updates)
        else:
            self._mem_update("maintenance_optimization", record_id, updates)

    # ============================================================
    # 内存模式通用实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含维护模块的键(懒初始化)"""
        if "maintenance_tasks" not in self.store:
            self.store["maintenance_tasks"] = {}
            self.store["maintenance_health"] = {}
            self.store["maintenance_recovery"] = {}
            self.store["maintenance_optimization"] = {}
            self.store["_maintenance_task_seq"] = 0
            self.store["_maintenance_health_seq"] = 0
            self.store["_maintenance_recovery_seq"] = 0
            self.store["_maintenance_optimization_seq"] = 0

    def _mem_create(self, table: str, record_id: int, record: dict) -> None:
        self._ensure_store()
        self.store[table][record_id] = record

    def _mem_get(self, table: str, record_id: int) -> dict | None:
        self._ensure_store()
        return self.store[table].get(record_id)

    def _mem_update(self, table: str, record_id: int, updates: dict) -> None:
        self._ensure_store()
        record = self.store[table].get(record_id)
        if record:
            record.update(updates)

    def _mem_list(self, table: str, filter_value: str, filter_key: str,
                    filter_value2: str, filter_key2: str, limit: int) -> list[dict]:
        self._ensure_store()
        records = list(self.store[table].values())
        if filter_value:
            records = [r for r in records if r.get(filter_key) == filter_value]
        if filter_value2:
            records = [r for r in records if r.get(filter_key2) == filter_value2]
        records.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return records[:limit]

    # ============================================================
    # Redis 模式通用实现
    # ============================================================

    async def _redis_create(self, entity: str, record_id: int,
                              record: dict) -> None:
        client = await get_redis_client()
        await client.set(_k("maintenance", entity, record_id),
                         json.dumps(record, ensure_ascii=False))

    async def _redis_get(self, entity: str,
                           record_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("maintenance", entity, record_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_update(self, entity: str, record_id: int,
                              updates: dict) -> None:
        client = await get_redis_client()
        data = await client.get(_k("maintenance", entity, record_id))
        if data:
            record = json.loads(data)
            record.update(updates)
            await client.set(_k("maintenance", entity, record_id),
                             json.dumps(record, ensure_ascii=False))

    async def _redis_list(self, entity: str,
                            filter_value: str, filter_key: str,
                            filter_value2: str, filter_key2: str,
                            limit: int) -> list[dict]:
        records = await self._redis_list_all(entity, limit * 5)
        if filter_value:
            records = [r for r in records if r.get(filter_key) == filter_value]
        if filter_value2:
            records = [r for r in records if r.get(filter_key2) == filter_value2]
        return records[:limit]

    async def _redis_list_all(self, entity: str, limit: int) -> list[dict]:
        client = await get_redis_client()
        records = []
        keys = await client.keys(_k("maintenance", entity, "*"))
        for key in keys:
            if "seq" in key:
                continue
            data = await client.get(key)
            if data:
                records.append(json.loads(data))
        records.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return records[:limit]
