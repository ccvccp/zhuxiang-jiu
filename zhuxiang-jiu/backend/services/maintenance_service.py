"""AI 智能维护模块业务逻辑层

核心业务:
    - 维护任务: 备份/清理/优化/巡检/重启/扩容 任务的状态机流转
    - 健康检查: 服务健康状态检测(healthy/degraded/unhealthy/unknown)
    - 故障自愈: 检测→诊断→恢复策略→执行结果(auto/assisted/manual)
    - 性能优化: 建议→审批→执行→效果评估
    - 一键巡检: 串联健康检查+故障检测+自愈全流程
    - 统计: 维护模块全景统计

锁保护:
    - 任务执行/取消: lock:maintenance:task:{task_id}
    - 健康检查: lock:maintenance:health:{check_id}
    - 故障诊断/恢复: lock:maintenance:recovery:{recovery_id}
    - 优化审批/执行: lock:maintenance:optimization:{optimization_id}

异常约定:
    - KeyError → 404(记录不存在)
    - ValueError → 409(状态非法/参数无效/状态流转违规)
"""


from core.locks import get_lock
from core.helpers import ts
from repositories.maintenance_repository import (
    MaintenanceRepository,
    # 任务状态
    TASK_STATUS_PENDING, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS,
    TASK_STATUS_FAILED, TASK_STATUS_CANCELLED,
    # 任务类型
    TASK_TYPE_BACKUP, TASK_TYPE_CLEANUP, TASK_TYPE_OPTIMIZE,
    TASK_TYPE_INSPECT, TASK_TYPE_RESTART, TASK_TYPE_SCALE,
    # 触发类型
    TRIGGER_MANUAL, TRIGGER_SCHEDULED,
    # 健康状态
    HEALTH_HEALTHY, HEALTH_DEGRADED, HEALTH_UNHEALTHY, HEALTH_UNKNOWN,
    # 检查类型
    CHECK_TYPE_HTTP, CHECK_TYPE_TCP, CHECK_TYPE_RESOURCE, CHECK_TYPE_CUSTOM,
    # 自愈状态
    RECOVERY_STATUS_DETECTED, RECOVERY_STATUS_DIAGNOSING,
    RECOVERY_STATUS_RECOVERING, RECOVERY_STATUS_RECOVERED,
    RECOVERY_STATUS_FAILED, RECOVERY_STATUS_MANUAL_REQUIRED,
    # 自愈级别
    RECOVERY_LEVEL_AUTO, RECOVERY_LEVEL_ASSISTED, RECOVERY_LEVEL_MANUAL,
    # 优化状态
    OPTIMIZATION_STATUS_PROPOSED, OPTIMIZATION_STATUS_APPROVED,
    OPTIMIZATION_STATUS_EXECUTING, OPTIMIZATION_STATUS_COMPLETED,
    OPTIMIZATION_STATUS_REJECTED,
)


# ============================================================
# 合法值集合
# ============================================================

VALID_TASK_TYPES = {
    TASK_TYPE_BACKUP, TASK_TYPE_CLEANUP, TASK_TYPE_OPTIMIZE,
    TASK_TYPE_INSPECT, TASK_TYPE_RESTART, TASK_TYPE_SCALE,
}

VALID_TASK_STATUSES = {
    TASK_STATUS_PENDING, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS,
    TASK_STATUS_FAILED, TASK_STATUS_CANCELLED,
}

VALID_HEALTH_STATUSES = {
    HEALTH_HEALTHY, HEALTH_DEGRADED, HEALTH_UNHEALTHY, HEALTH_UNKNOWN,
}

VALID_CHECK_TYPES = {
    CHECK_TYPE_HTTP, CHECK_TYPE_TCP, CHECK_TYPE_RESOURCE, CHECK_TYPE_CUSTOM,
}

VALID_RECOVERY_STATUSES = {
    RECOVERY_STATUS_DETECTED, RECOVERY_STATUS_DIAGNOSING,
    RECOVERY_STATUS_RECOVERING, RECOVERY_STATUS_RECOVERED,
    RECOVERY_STATUS_FAILED, RECOVERY_STATUS_MANUAL_REQUIRED,
}

VALID_RECOVERY_LEVELS = {
    RECOVERY_LEVEL_AUTO, RECOVERY_LEVEL_ASSISTED, RECOVERY_LEVEL_MANUAL,
}

VALID_OPTIMIZATION_STATUSES = {
    OPTIMIZATION_STATUS_PROPOSED, OPTIMIZATION_STATUS_APPROVED,
    OPTIMIZATION_STATUS_EXECUTING, OPTIMIZATION_STATUS_COMPLETED,
    OPTIMIZATION_STATUS_REJECTED,
}

VALID_TRIGGER_TYPES = {TRIGGER_MANUAL, TRIGGER_SCHEDULED}


class MaintenanceService:
    """AI 智能维护业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: MaintenanceRepository = MaintenanceRepository()):
        self.repo = repo

    # ============================================================
    # 1. 维护任务
    # ============================================================

    async def create_task(self, task_name: str, task_type: str, target: str,
                            trigger_type: str = TRIGGER_MANUAL,
                            params: dict = None,
                            schedule: str = "",
                            ai_automation_rate: float = 90.0) -> dict:
        """创建维护任务(初始状态 pending)

        规则:
            - 校验任务类型/触发类型合法性
            - 任务名/目标不可为空
            - 初始状态为 pending
        """
        if not task_name or not target:
            raise ValueError("任务名称和目标不可为空")

        if task_type not in VALID_TASK_TYPES:
            raise ValueError(f"非法任务类型: {task_type}")

        if trigger_type not in VALID_TRIGGER_TYPES:
            raise ValueError(f"非法触发类型: {trigger_type}")

        record = {
            "taskName": task_name,
            "taskType": task_type,
            "taskStatus": TASK_STATUS_PENDING,
            "triggerType": trigger_type,
            "target": target,
            "params": params or {},
            "schedule": schedule,
            "executedAt": "",
            "completedAt": "",
            "result": {},
            "errorMessage": "",
            "aiAutomationRate": ai_automation_rate,
            "createdAt": ts(),
        }
        record_id = await self.repo.create_task(record)
        record["id"] = record_id
        return record

    async def execute_task(self, task_id: int,
                             result: dict = None,
                             error_message: str = "") -> dict:
        """执行维护任务

        状态机:
            pending → running → success(默认) / failed(传入 error_message)
        """
        lock_key = f"maintenance:task:{task_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_task(task_id)
            if record is None:
                raise KeyError(f"维护任务不存在(id={task_id})")

            if record["taskStatus"] != TASK_STATUS_PENDING:
                raise ValueError(
                    f"任务状态非法(当前{record['taskStatus']}, 须为{TASK_STATUS_PENDING})"
                )

            # pending → running
            await self.repo.update_task(task_id, {
                "taskStatus": TASK_STATUS_RUNNING,
                "executedAt": ts(),
            })
            record["taskStatus"] = TASK_STATUS_RUNNING

            # running → success / failed(模拟执行)
            if error_message:
                new_status = TASK_STATUS_FAILED
                updates = {
                    "taskStatus": new_status,
                    "completedAt": ts(),
                    "errorMessage": error_message,
                    "result": result or {},
                }
            else:
                new_status = TASK_STATUS_SUCCESS
                updates = {
                    "taskStatus": new_status,
                    "completedAt": ts(),
                    "result": result or {"status": "ok"},
                }

            await self.repo.update_task(task_id, updates)
            record.update(updates)
            return record

    async def cancel_task(self, task_id: int) -> dict:
        """取消维护任务

        状态机:
            pending/running → cancelled(终态)
        """
        lock_key = f"maintenance:task:{task_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_task(task_id)
            if record is None:
                raise KeyError(f"维护任务不存在(id={task_id})")

            if record["taskStatus"] in (TASK_STATUS_SUCCESS, TASK_STATUS_FAILED,
                                          TASK_STATUS_CANCELLED):
                raise ValueError(
                    f"任务已终态(当前{record['taskStatus']}), 不可取消"
                )

            await self.repo.update_task(task_id, {
                "taskStatus": TASK_STATUS_CANCELLED,
                "completedAt": ts(),
            })
            record["taskStatus"] = TASK_STATUS_CANCELLED
            return record

    async def get_task(self, task_id: int) -> dict:
        """查询维护任务"""
        record = await self.repo.get_task(task_id)
        if record is None:
            raise KeyError(f"维护任务不存在(id={task_id})")
        return record

    async def list_tasks(self, task_type: str = None,
                          task_status: str = None,
                          limit: int = 50) -> list[dict]:
        """查询维护任务列表"""
        return await self.repo.list_tasks(task_type, task_status, limit)

    # ============================================================
    # 2. 健康检查
    # ============================================================

    async def create_health_check(self, check_name: str, service_name: str,
                                    check_type: str,
                                    check_config: dict = None,
                                    threshold: dict = None,
                                    ai_automation_rate: float = 95.0) -> dict:
        """创建健康检查项(初始状态 unknown)"""
        if not check_name or not service_name:
            raise ValueError("检查项名称和服务名称不可为空")

        if check_type not in VALID_CHECK_TYPES:
            raise ValueError(f"非法检查类型: {check_type}")

        record = {
            "checkName": check_name,
            "serviceName": service_name,
            "checkType": check_type,
            "healthStatus": HEALTH_UNKNOWN,
            "checkConfig": check_config or {},
            "checkResult": {},
            "lastCheckAt": "",
            "responseTime": 0,
            "threshold": threshold or {},
            "aiAutomationRate": ai_automation_rate,
            "createdAt": ts(),
        }
        record_id = await self.repo.create_health(record)
        record["id"] = record_id
        return record

    async def run_health_check(self, check_id: int,
                                 health_status: str = None,
                                 check_result: dict = None,
                                 response_time: int = 0) -> dict:
        """执行健康检查

        状态机:
            unknown/任意 → healthy/degraded/unhealthy
        """
        if health_status is not None and health_status not in VALID_HEALTH_STATUSES:
            raise ValueError(f"非法健康状态: {health_status}")

        lock_key = f"maintenance:health:{check_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_health(check_id)
            if record is None:
                raise KeyError(f"健康检查不存在(id={check_id})")

            # 默认 healthy
            new_status = health_status or HEALTH_HEALTHY
            await self.repo.update_health(check_id, {
                "healthStatus": new_status,
                "checkResult": check_result or {"status": new_status},
                "lastCheckAt": ts(),
                "responseTime": response_time,
            })
            record["healthStatus"] = new_status
            record["checkResult"] = check_result or {"status": new_status}
            record["lastCheckAt"] = ts()
            record["responseTime"] = response_time
            return record

    async def get_health_check(self, check_id: int) -> dict:
        """查询健康检查"""
        record = await self.repo.get_health(check_id)
        if record is None:
            raise KeyError(f"健康检查不存在(id={check_id})")
        return record

    async def list_health_checks(self, service_name: str = None,
                                    health_status: str = None,
                                    limit: int = 50) -> list[dict]:
        """查询健康检查列表"""
        return await self.repo.list_health(service_name, health_status, limit)

    # ============================================================
    # 3. 故障自愈
    # ============================================================

    async def detect_fault(self, fault_type: str, fault_source: str,
                             fault_description: str = "",
                             recovery_level: str = RECOVERY_LEVEL_AUTO,
                             ai_automation_rate: float = 90.0) -> dict:
        """检测故障并创建自愈记录(初始状态 detected)

        规则:
            - 故障类型/来源不可为空
            - 自愈级别决定后续流程
            - manual 级别直接进入 manual_required 终态
        """
        if not fault_type or not fault_source:
            raise ValueError("故障类型和来源不可为空")

        if recovery_level not in VALID_RECOVERY_LEVELS:
            raise ValueError(f"非法自愈级别: {recovery_level}")

        # manual 级别直接进入需人工状态
        initial_status = (RECOVERY_STATUS_MANUAL_REQUIRED
                            if recovery_level == RECOVERY_LEVEL_MANUAL
                            else RECOVERY_STATUS_DETECTED)

        record = {
            "faultType": fault_type,
            "faultSource": fault_source,
            "faultDescription": fault_description,
            "recoveryStatus": initial_status,
            "recoveryLevel": recovery_level,
            "diagnoseResult": {},
            "recoveryStrategy": {},
            "executionResult": {},
            "detectedAt": ts(),
            "recoveredAt": "",
            "aiAutomationRate": ai_automation_rate,
            "createdAt": ts(),
        }
        record_id = await self.repo.create_recovery(record)
        record["id"] = record_id
        return record

    async def diagnose_fault(self, recovery_id: int,
                               diagnose_result: dict = None,
                               recovery_strategy: dict = None) -> dict:
        """诊断故障

        状态机:
            detected → diagnosing
        """
        lock_key = f"maintenance:recovery:{recovery_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_recovery(recovery_id)
            if record is None:
                raise KeyError(f"自愈记录不存在(id={recovery_id})")

            if record["recoveryStatus"] != RECOVERY_STATUS_DETECTED:
                raise ValueError(
                    f"自愈状态非法(当前{record['recoveryStatus']}, 须为{RECOVERY_STATUS_DETECTED})"
                )

            await self.repo.update_recovery(recovery_id, {
                "recoveryStatus": RECOVERY_STATUS_DIAGNOSING,
                "diagnoseResult": diagnose_result or {"rootCause": "unknown"},
                "recoveryStrategy": recovery_strategy or {"actions": []},
            })
            record["recoveryStatus"] = RECOVERY_STATUS_DIAGNOSING
            record["diagnoseResult"] = diagnose_result or {"rootCause": "unknown"}
            record["recoveryStrategy"] = recovery_strategy or {"actions": []}
            return record

    async def attempt_recovery(self, recovery_id: int,
                                 execution_result: dict = None,
                                 success: bool = True) -> dict:
        """执行恢复

        状态机:
            diagnosing → recovering → recovered(success) / failed(!success)
        """
        lock_key = f"maintenance:recovery:{recovery_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_recovery(recovery_id)
            if record is None:
                raise KeyError(f"自愈记录不存在(id={recovery_id})")

            if record["recoveryStatus"] != RECOVERY_STATUS_DIAGNOSING:
                raise ValueError(
                    f"自愈状态非法(当前{record['recoveryStatus']}, 须为{RECOVERY_STATUS_DIAGNOSING})"
                )

            # diagnosing → recovering
            await self.repo.update_recovery(recovery_id, {
                "recoveryStatus": RECOVERY_STATUS_RECOVERING,
            })
            record["recoveryStatus"] = RECOVERY_STATUS_RECOVERING

            # recovering → recovered / failed
            final_status = (RECOVERY_STATUS_RECOVERED if success
                              else RECOVERY_STATUS_FAILED)
            await self.repo.update_recovery(recovery_id, {
                "recoveryStatus": final_status,
                "executionResult": execution_result or {"status": final_status},
                "recoveredAt": ts() if success else "",
            })
            record["recoveryStatus"] = final_status
            record["executionResult"] = execution_result or {"status": final_status}
            if success:
                record["recoveredAt"] = ts()
            return record

    async def get_recovery(self, recovery_id: int) -> dict:
        """查询自愈记录"""
        record = await self.repo.get_recovery(recovery_id)
        if record is None:
            raise KeyError(f"自愈记录不存在(id={recovery_id})")
        return record

    async def list_recoveries(self, fault_type: str = None,
                                recovery_status: str = None,
                                limit: int = 50) -> list[dict]:
        """查询自愈记录列表"""
        return await self.repo.list_recoveries(fault_type, recovery_status, limit)

    # ============================================================
    # 4. 性能优化
    # ============================================================

    async def propose_optimization(self, optimization_type: str, target: str,
                                      proposal: str,
                                      expected_benefit: dict = None,
                                      execution_plan: dict = None,
                                      ai_automation_rate: float = 85.0) -> dict:
        """提交优化建议(初始状态 proposed)"""
        if not optimization_type or not target or not proposal:
            raise ValueError("优化类型/目标/建议不可为空")

        record = {
            "optimizationType": optimization_type,
            "target": target,
            "optimizationStatus": OPTIMIZATION_STATUS_PROPOSED,
            "proposal": proposal,
            "expectedBenefit": expected_benefit or {},
            "executionPlan": execution_plan or {},
            "actualBenefit": {},
            "approvedBy": "",
            "executedAt": "",
            "completedAt": "",
            "aiAutomationRate": ai_automation_rate,
            "createdAt": ts(),
        }
        record_id = await self.repo.create_optimization(record)
        record["id"] = record_id
        return record

    async def approve_optimization(self, optimization_id: int,
                                     approved_by: str = "admin") -> dict:
        """批准优化建议

        状态机:
            proposed → approved
        """
        lock_key = f"maintenance:optimization:{optimization_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_optimization(optimization_id)
            if record is None:
                raise KeyError(f"优化建议不存在(id={optimization_id})")

            if record["optimizationStatus"] != OPTIMIZATION_STATUS_PROPOSED:
                raise ValueError(
                    f"优化状态非法(当前{record['optimizationStatus']}, 须为{OPTIMIZATION_STATUS_PROPOSED})"
                )

            await self.repo.update_optimization(optimization_id, {
                "optimizationStatus": OPTIMIZATION_STATUS_APPROVED,
                "approvedBy": approved_by,
            })
            record["optimizationStatus"] = OPTIMIZATION_STATUS_APPROVED
            record["approvedBy"] = approved_by
            return record

    async def execute_optimization(self, optimization_id: int,
                                     actual_benefit: dict = None,
                                     success: bool = True) -> dict:
        """执行优化

        状态机:
            approved → executing → completed(success) / 执行中状态保持(!success)
        """
        lock_key = f"maintenance:optimization:{optimization_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_optimization(optimization_id)
            if record is None:
                raise KeyError(f"优化建议不存在(id={optimization_id})")

            if record["optimizationStatus"] != OPTIMIZATION_STATUS_APPROVED:
                raise ValueError(
                    f"优化状态非法(当前{record['optimizationStatus']}, 须为{OPTIMIZATION_STATUS_APPROVED})"
                )

            # approved → executing
            await self.repo.update_optimization(optimization_id, {
                "optimizationStatus": OPTIMIZATION_STATUS_EXECUTING,
                "executedAt": ts(),
            })
            record["optimizationStatus"] = OPTIMIZATION_STATUS_EXECUTING
            record["executedAt"] = ts()

            # executing → completed(成功则完成)
            if success:
                await self.repo.update_optimization(optimization_id, {
                    "optimizationStatus": OPTIMIZATION_STATUS_COMPLETED,
                    "actualBenefit": actual_benefit or {},
                    "completedAt": ts(),
                })
                record["optimizationStatus"] = OPTIMIZATION_STATUS_COMPLETED
                record["actualBenefit"] = actual_benefit or {}
                record["completedAt"] = ts()
            return record

    async def reject_optimization(self, optimization_id: int) -> dict:
        """驳回优化建议

        状态机:
            proposed/approved → rejected
        """
        lock_key = f"maintenance:optimization:{optimization_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_optimization(optimization_id)
            if record is None:
                raise KeyError(f"优化建议不存在(id={optimization_id})")

            if record["optimizationStatus"] in (OPTIMIZATION_STATUS_COMPLETED,
                                                  OPTIMIZATION_STATUS_REJECTED):
                raise ValueError(
                    f"优化已终态(当前{record['optimizationStatus']}), 不可驳回"
                )

            await self.repo.update_optimization(optimization_id, {
                "optimizationStatus": OPTIMIZATION_STATUS_REJECTED,
            })
            record["optimizationStatus"] = OPTIMIZATION_STATUS_REJECTED
            return record

    async def get_optimization(self, optimization_id: int) -> dict:
        """查询优化建议"""
        record = await self.repo.get_optimization(optimization_id)
        if record is None:
            raise KeyError(f"优化建议不存在(id={optimization_id})")
        return record

    async def list_optimizations(self, optimization_type: str = None,
                                   optimization_status: str = None,
                                   limit: int = 50) -> list[dict]:
        """查询优化建议列表"""
        return await self.repo.list_optimizations(optimization_type, optimization_status, limit)

    # ============================================================
    # 5. 一键巡检
    # ============================================================

    async def inspect_all(self, services: list = None) -> dict:
        """一键巡检全服务

        规则:
            - 对每个服务创建健康检查并执行(模拟)
            - 不健康的服务自动创建故障自愈记录
            - 返回巡检摘要
        """
        # 默认巡检核心服务
        target_services = services or [
            {"name": "order-service", "type": CHECK_TYPE_HTTP,
             "status": HEALTH_HEALTHY},
            {"name": "payment-service", "type": CHECK_TYPE_HTTP,
             "status": HEALTH_HEALTHY},
            {"name": "inventory-service", "type": CHECK_TYPE_RESOURCE,
             "status": HEALTH_DEGRADED},
            {"name": "message-queue", "type": CHECK_TYPE_TCP,
             "status": HEALTH_UNHEALTHY},
        ]

        health_ids = []
        recovery_ids = []
        status_count = {HEALTH_HEALTHY: 0, HEALTH_DEGRADED: 0,
                          HEALTH_UNHEALTHY: 0, HEALTH_UNKNOWN: 0}

        for svc in target_services:
            # 创建健康检查
            health = await self.create_health_check(
                check_name=f"{svc['name']} 巡检",
                service_name=svc["name"],
                check_type=svc["type"],
                check_config={"auto": True},
            )
            # 执行检查
            result = await self.run_health_check(
                health["id"],
                health_status=svc["status"],
                check_result={"auto": True, "service": svc["name"]},
                response_time=50,
            )
            health_ids.append(health["id"])
            status_count[svc["status"]] = status_count.get(svc["status"], 0) + 1

            # 不健康则创建自愈记录
            if svc["status"] in (HEALTH_UNHEALTHY, HEALTH_DEGRADED):
                level = (RECOVERY_LEVEL_AUTO if svc["status"] == HEALTH_UNHEALTHY
                           else RECOVERY_LEVEL_ASSISTED)
                recovery = await self.detect_fault(
                    fault_type=f"{svc['name']} 服务异常",
                    fault_source=svc["name"],
                    fault_description=f"健康检查状态: {svc['status']}",
                    recovery_level=level,
                )
                recovery_ids.append(recovery["id"])

        return {
            "totalServices": len(target_services),
            "healthIds": health_ids,
            "recoveryIds": recovery_ids,
            "statusCount": status_count,
            "unhealthyCount": status_count.get(HEALTH_UNHEALTHY, 0),
            "degradedCount": status_count.get(HEALTH_DEGRADED, 0),
            "inspectedAt": ts(),
        }

    # ============================================================
    # 6. 统计
    # ============================================================

    async def get_stats(self) -> dict:
        """维护模块统计"""
        tasks = await self.repo.list_tasks(limit=10000)
        health_checks = await self.repo.list_health(limit=10000)
        recoveries = await self.repo.list_recoveries(limit=10000)
        optimizations = await self.repo.list_optimizations(limit=10000)

        # 任务状态分布
        task_status_count = {}
        for t in tasks:
            status = t.get("taskStatus", "unknown")
            task_status_count[status] = task_status_count.get(status, 0) + 1

        # 健康状态分布
        health_status_count = {}
        for h in health_checks:
            status = h.get("healthStatus", "unknown")
            health_status_count[status] = health_status_count.get(status, 0) + 1

        # 自愈状态分布
        recovery_status_count = {}
        for r in recoveries:
            status = r.get("recoveryStatus", "unknown")
            recovery_status_count[status] = recovery_status_count.get(status, 0) + 1

        # 优化状态分布
        optimization_status_count = {}
        for o in optimizations:
            status = o.get("optimizationStatus", "unknown")
            optimization_status_count[status] = optimization_status_count.get(status, 0) + 1

        return {
            "totalTasks": len(tasks),
            "totalHealthChecks": len(health_checks),
            "totalRecoveries": len(recoveries),
            "totalOptimizations": len(optimizations),
            "taskStatusCount": task_status_count,
            "healthStatusCount": health_status_count,
            "recoveryStatusCount": recovery_status_count,
            "optimizationStatusCount": optimization_status_count,
        }
