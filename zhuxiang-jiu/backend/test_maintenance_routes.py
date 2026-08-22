"""AI 智能维护模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 MaintenanceService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_maintenance_routes.py

覆盖 12 个接口对应的业务方法 + 状态机流转:
    1. 维护任务(3): create_task / list_tasks / execute_task + cancel_task
    2. 健康检查(3): create_health_check / list_health_checks / get_health_check + run_health_check
    3. 故障自愈(2): detect_fault / list_recoveries + diagnose + attempt_recovery
    4. 性能优化(2): propose_optimization / list_optimizations + approve + execute + reject
    5. 一键巡检(1): inspect_all
    6. 统计(1):    get_stats
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.maintenance_service import MaintenanceService
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
from repositories.store import _mock_store, reset_store as _reset_store_impl

# 测试结果收集
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    """重置内存存储, 保证测试隔离"""
    _reset_store_impl()


# ============================================================
# 测试数据
# ============================================================

TARGET_ORDER_SVC = "order-service"
TARGET_PAYMENT_SVC = "payment-service"
TARGET_INVENTORY_SVC = "inventory-service"
TARGET_DB = "mysql-master"
TARGET_CACHE = "redis-cluster"
FAULT_TYPE_OOM = "内存溢出"
FAULT_TYPE_CONN = "连接超时"
FAULT_SOURCE_DB = "mysql-master"
FAULT_SOURCE_MQ = "message-queue"
OPT_TYPE_INDEX = "索引优化"
OPT_TYPE_CACHE = "缓存优化"
SERVICE_NAME_ORDER = "order-service"
SERVICE_NAME_PAYMENT = "payment-service"


# ============================================================
# 测试用例
# ============================================================

class TestMaintenanceTask:
    """维护任务测试"""

    async def run(self, svc):
        # test 1: 创建任务(默认 pending + manual)
        result = await svc.create_task(
            task_name="数据库备份",
            task_type=TASK_TYPE_BACKUP,
            target=TARGET_DB,
        )
        record("test_01_create_task_success",
               result["taskType"] == TASK_TYPE_BACKUP and result["id"] > 0,
               f"expected backup/{'>0'}, got {result.get('taskType')}/{result.get('id')}")

        # test 2: 初始状态为 pending
        record("test_02_initial_status_pending",
               result["taskStatus"] == TASK_STATUS_PENDING,
               f"expected {TASK_STATUS_PENDING}, got {result['taskStatus']}")

        # test 3: 默认触发类型为 manual
        record("test_03_default_trigger_manual",
               result["triggerType"] == TRIGGER_MANUAL,
               f"expected {TRIGGER_MANUAL}, got {result['triggerType']}")

        # test 4: 执行任务(pending → running → success)
        task_id = result["id"]
        exec_result = await svc.execute_task(task_id)
        record("test_04_execute_task_success",
               exec_result["taskStatus"] == TASK_STATUS_SUCCESS,
               f"expected {TASK_STATUS_SUCCESS}, got {exec_result['taskStatus']}")

        # test 5: 重复执行已终态任务(状态非法)
        try:
            await svc.execute_task(task_id)
            record("test_05_re_execute_terminal", False, "应抛出ValueError")
        except ValueError:
            record("test_05_re_execute_terminal", True)

        # test 6: 取消已终态任务(状态非法)
        try:
            await svc.cancel_task(task_id)
            record("test_06_cancel_terminal", False, "应抛出ValueError")
        except ValueError:
            record("test_06_cancel_terminal", True)

        # test 7: 创建并取消 pending 任务
        result2 = await svc.create_task(
            task_name="清理临时文件",
            task_type=TASK_TYPE_CLEANUP,
            target=TARGET_CACHE,
        )
        cancel_result = await svc.cancel_task(result2["id"])
        record("test_07_cancel_pending_task",
               cancel_result["taskStatus"] == TASK_STATUS_CANCELLED,
               f"expected {TASK_STATUS_CANCELLED}, got {cancel_result['taskStatus']}")

        # test 8: 执行任务并标记失败(pending → running → failed)
        result3 = await svc.create_task(
            task_name="服务重启",
            task_type=TASK_TYPE_RESTART,
            target=TARGET_ORDER_SVC,
        )
        fail_result = await svc.execute_task(
            result3["id"],
            error_message="服务重启失败: 进程未响应",
        )
        record("test_08_execute_task_failed",
               fail_result["taskStatus"] == TASK_STATUS_FAILED,
               f"expected {TASK_STATUS_FAILED}, got {fail_result['taskStatus']}")

        # test 9: 错误信息保存
        record("test_09_error_message_saved",
               "进程未响应" in fail_result.get("errorMessage", ""),
               f"expected 进程未响应 in: {fail_result.get('errorMessage')}")

        # test 10: 任务名为空
        try:
            await svc.create_task("", TASK_TYPE_BACKUP, TARGET_DB)
            record("test_10_empty_task_name", False, "应抛出ValueError")
        except ValueError:
            record("test_10_empty_task_name", True)

        # test 11: 非法任务类型
        try:
            await svc.create_task("非法任务", "invalid_type", TARGET_DB)
            record("test_11_invalid_task_type", False, "应抛出ValueError")
        except ValueError:
            record("test_11_invalid_task_type", True)

        # test 12: 非法触发类型
        try:
            await svc.create_task(
                "非法触发", TASK_TYPE_BACKUP, TARGET_DB,
                trigger_type="invalid",
            )
            record("test_12_invalid_trigger_type", False, "应抛出ValueError")
        except ValueError:
            record("test_12_invalid_trigger_type", True)

        # test 13: 执行不存在的任务
        try:
            await svc.execute_task(99999)
            record("test_13_execute_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_13_execute_nonexistent", True)

        # test 14: 查询不存在的任务
        try:
            await svc.get_task(99999)
            record("test_14_get_nonexistent_task", False, "应抛出KeyError")
        except KeyError:
            record("test_14_get_nonexistent_task", True)

        # test 15: 列表查询(按任务类型筛选)
        backups = await svc.list_tasks(task_type=TASK_TYPE_BACKUP)
        record("test_15_list_by_type",
               all(t["taskType"] == TASK_TYPE_BACKUP for t in backups) and len(backups) >= 1,
               f"expected all backup")

        # test 16: 列表查询(按任务状态筛选)
        successes = await svc.list_tasks(task_status=TASK_STATUS_SUCCESS)
        record("test_16_list_by_status",
               all(t["taskStatus"] == TASK_STATUS_SUCCESS for t in successes),
               f"expected all success")

        # test 17: 创建定时任务
        result4 = await svc.create_task(
            task_name="定时巡检",
            task_type=TASK_TYPE_INSPECT,
            target=TARGET_ORDER_SVC,
            trigger_type=TRIGGER_SCHEDULED,
            schedule="0 2 * * *",
        )
        record("test_17_scheduled_task",
               result4["triggerType"] == TRIGGER_SCHEDULED and result4["schedule"] == "0 2 * * *",
               f"expected scheduled/cron")


class TestHealthCheck:
    """健康检查测试"""

    async def run(self, svc):
        # test 18: 创建健康检查(初始 unknown)
        result = await svc.create_health_check(
            check_name="订单服务健康检查",
            service_name=SERVICE_NAME_ORDER,
            check_type=CHECK_TYPE_HTTP,
            check_config={"url": "http://order-service:8080/health"},
            threshold={"responseTime": 500, "errorRate": 0.01},
        )
        record("test_18_create_health_check",
               result["serviceName"] == SERVICE_NAME_ORDER and result["id"] > 0,
               f"expected order-service/{'>0'}")

        # test 19: 初始状态为 unknown
        record("test_19_initial_status_unknown",
               result["healthStatus"] == HEALTH_UNKNOWN,
               f"expected {HEALTH_UNKNOWN}, got {result['healthStatus']}")

        # test 20: 执行健康检查(→ healthy)
        check_id = result["id"]
        run_result = await svc.run_health_check(
            check_id,
            health_status=HEALTH_HEALTHY,
            response_time=120,
        )
        record("test_20_run_health_healthy",
               run_result["healthStatus"] == HEALTH_HEALTHY,
               f"expected {HEALTH_HEALTHY}, got {run_result['healthStatus']}")

        # test 21: 响应时间记录
        record("test_21_response_time_recorded",
               run_result["responseTime"] == 120,
               f"expected 120, got {run_result.get('responseTime')}")

        # test 22: 执行健康检查(→ unhealthy)
        result2 = await svc.create_health_check(
            check_name="支付服务检查",
            service_name=SERVICE_NAME_PAYMENT,
            check_type=CHECK_TYPE_HTTP,
        )
        unhealthy_result = await svc.run_health_check(
            result2["id"],
            health_status=HEALTH_UNHEALTHY,
            response_time=5000,
        )
        record("test_22_run_health_unhealthy",
               unhealthy_result["healthStatus"] == HEALTH_UNHEALTHY,
               f"expected {HEALTH_UNHEALTHY}")

        # test 23: 执行健康检查(→ degraded)
        result3 = await svc.create_health_check(
            check_name="库存服务检查",
            service_name="inventory-service",
            check_type=CHECK_TYPE_RESOURCE,
        )
        degraded_result = await svc.run_health_check(
            result3["id"],
            health_status=HEALTH_DEGRADED,
            response_time=800,
        )
        record("test_23_run_health_degraded",
               degraded_result["healthStatus"] == HEALTH_DEGRADED,
               f"expected {HEALTH_DEGRADED}")

        # test 24: 默认健康状态(healthy)
        result4 = await svc.create_health_check(
            check_name="默认检查",
            service_name="default-svc",
            check_type=CHECK_TYPE_TCP,
        )
        default_result = await svc.run_health_check(result4["id"])
        record("test_24_default_health_status",
               default_result["healthStatus"] == HEALTH_HEALTHY,
               f"expected {HEALTH_HEALTHY}, got {default_result['healthStatus']}")

        # test 25: 检查项名为空
        try:
            await svc.create_health_check("", SERVICE_NAME_ORDER, CHECK_TYPE_HTTP)
            record("test_25_empty_check_name", False, "应抛出ValueError")
        except ValueError:
            record("test_25_empty_check_name", True)

        # test 26: 非法检查类型
        try:
            await svc.create_health_check("检查", SERVICE_NAME_ORDER, "invalid")
            record("test_26_invalid_check_type", False, "应抛出ValueError")
        except ValueError:
            record("test_26_invalid_check_type", True)

        # test 27: 非法健康状态
        try:
            await svc.run_health_check(check_id, health_status="invalid")
            record("test_27_invalid_health_status", False, "应抛出ValueError")
        except ValueError:
            record("test_27_invalid_health_status", True)

        # test 28: 执行不存在的健康检查
        try:
            await svc.run_health_check(99999)
            record("test_28_run_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_28_run_nonexistent", True)

        # test 29: 查询健康检查详情
        detail = await svc.get_health_check(check_id)
        record("test_29_get_health_detail",
               detail["id"] == check_id,
               f"expected id={check_id}")

        # test 30: 查询不存在的健康检查
        try:
            await svc.get_health_check(99999)
            record("test_30_get_nonexistent_health", False, "应抛出KeyError")
        except KeyError:
            record("test_30_get_nonexistent_health", True)

        # test 31: 列表查询(按服务筛选)
        checks = await svc.list_health_checks(service_name=SERVICE_NAME_ORDER)
        record("test_31_list_by_service",
               all(c["serviceName"] == SERVICE_NAME_ORDER for c in checks) and len(checks) >= 1,
               f"expected all order-service")

        # test 32: 列表查询(按健康状态筛选)
        unhealthy_list = await svc.list_health_checks(health_status=HEALTH_UNHEALTHY)
        record("test_32_list_by_status",
               all(c["healthStatus"] == HEALTH_UNHEALTHY for c in unhealthy_list),
               f"expected all unhealthy")


class TestRecovery:
    """故障自愈测试"""

    async def run(self, svc):
        # test 33: 检测故障(自动级别 → detected)
        result = await svc.detect_fault(
            fault_type=FAULT_TYPE_OOM,
            fault_source=FAULT_SOURCE_DB,
            fault_description="MySQL 主库内存使用率 95%",
            recovery_level=RECOVERY_LEVEL_AUTO,
        )
        record("test_33_detect_fault_success",
               result["faultType"] == FAULT_TYPE_OOM and result["id"] > 0,
               f"expected {FAULT_TYPE_OOM}/{'>0'}")

        # test 34: 自动级别初始状态为 detected
        record("test_34_auto_level_detected",
               result["recoveryStatus"] == RECOVERY_STATUS_DETECTED,
               f"expected {RECOVERY_STATUS_DETECTED}, got {result['recoveryStatus']}")

        # test 35: 诊断故障(detected → diagnosing)
        recovery_id = result["id"]
        diag_result = await svc.diagnose_fault(
            recovery_id,
            diagnose_result={"rootCause": "缓存池配置过小"},
            recovery_strategy={"actions": ["扩大 innodb_buffer_pool_size"]},
        )
        record("test_35_diagnose_fault",
               diag_result["recoveryStatus"] == RECOVERY_STATUS_DIAGNOSING,
               f"expected {RECOVERY_STATUS_DIAGNOSING}, got {diag_result['recoveryStatus']}")

        # test 36: 诊断结果保存
        record("test_36_diagnose_result_saved",
               diag_result["diagnoseResult"]["rootCause"] == "缓存池配置过小",
               f"expected 缓存池配置过小")

        # test 37: 执行恢复(diagnosing → recovering → recovered)
        rec_result = await svc.attempt_recovery(
            recovery_id,
            execution_result={"action": "扩容缓存池", "status": "ok"},
            success=True,
        )
        record("test_37_recover_success",
               rec_result["recoveryStatus"] == RECOVERY_STATUS_RECOVERED,
               f"expected {RECOVERY_STATUS_RECOVERED}, got {rec_result['recoveryStatus']}")

        # test 38: 恢复时间记录
        record("test_38_recovered_at_recorded",
               rec_result["recoveredAt"] != "",
               f"expected non-empty recoveredAt")

        # test 39: 重复诊断已恢复记录(状态非法)
        try:
            await svc.diagnose_fault(recovery_id)
            record("test_39_re_diagnose_terminal", False, "应抛出ValueError")
        except ValueError:
            record("test_39_re_diagnose_terminal", True)

        # test 40: 重复恢复已恢复记录(状态非法)
        try:
            await svc.attempt_recovery(recovery_id)
            record("test_40_re_recover_terminal", False, "应抛出ValueError")
        except ValueError:
            record("test_40_re_recover_terminal", True)

        # test 41: 故障恢复失败(diagnosing → recovering → failed)
        result2 = await svc.detect_fault(
            fault_type=FAULT_TYPE_CONN,
            fault_source=FAULT_SOURCE_MQ,
            recovery_level=RECOVERY_LEVEL_AUTO,
        )
        await svc.diagnose_fault(result2["id"])
        failed_result = await svc.attempt_recovery(
            result2["id"],
            execution_result={"action": "重启消息队列", "status": "failed"},
            success=False,
        )
        record("test_41_recover_failed",
               failed_result["recoveryStatus"] == RECOVERY_STATUS_FAILED,
               f"expected {RECOVERY_STATUS_FAILED}, got {failed_result['recoveryStatus']}")

        # test 42: 失败时无恢复时间
        record("test_42_failed_no_recovered_at",
               failed_result["recoveredAt"] == "",
               f"expected empty recoveredAt")

        # test 43: 人工级别直接进入 manual_required
        result3 = await svc.detect_fault(
            fault_type="硬件故障",
            fault_source="disk-node-1",
            recovery_level=RECOVERY_LEVEL_MANUAL,
        )
        record("test_43_manual_level_required",
               result3["recoveryStatus"] == RECOVERY_STATUS_MANUAL_REQUIRED,
               f"expected {RECOVERY_STATUS_MANUAL_REQUIRED}, got {result3['recoveryStatus']}")

        # test 44: 人工级别不可诊断(状态非法)
        try:
            await svc.diagnose_fault(result3["id"])
            record("test_44_manual_cannot_diagnose", False, "应抛出ValueError")
        except ValueError:
            record("test_44_manual_cannot_diagnose", True)

        # test 45: 故障类型为空
        try:
            await svc.detect_fault("", FAULT_SOURCE_DB)
            record("test_45_empty_fault_type", False, "应抛出ValueError")
        except ValueError:
            record("test_45_empty_fault_type", True)

        # test 46: 非法自愈级别
        try:
            await svc.detect_fault(FAULT_TYPE_OOM, FAULT_SOURCE_DB,
                                     recovery_level="invalid")
            record("test_46_invalid_recovery_level", False, "应抛出ValueError")
        except ValueError:
            record("test_46_invalid_recovery_level", True)

        # test 47: 诊断不存在的记录
        try:
            await svc.diagnose_fault(99999)
            record("test_47_diagnose_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_47_diagnose_nonexistent", True)

        # test 48: 恢复不存在的记录
        try:
            await svc.attempt_recovery(99999)
            record("test_48_recover_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_48_recover_nonexistent", True)

        # test 49: 列表查询(按故障类型筛选)
        oom_list = await svc.list_recoveries(fault_type=FAULT_TYPE_OOM)
        record("test_49_list_by_fault_type",
               all(r["faultType"] == FAULT_TYPE_OOM for r in oom_list) and len(oom_list) >= 1,
               f"expected all OOM")

        # test 50: 列表查询(按自愈状态筛选)
        recovered_list = await svc.list_recoveries(recovery_status=RECOVERY_STATUS_RECOVERED)
        record("test_50_list_by_status",
               all(r["recoveryStatus"] == RECOVERY_STATUS_RECOVERED for r in recovered_list),
               f"expected all recovered")


class TestOptimization:
    """性能优化测试"""

    async def run(self, svc):
        # test 51: 提交优化建议(初始 proposed)
        result = await svc.propose_optimization(
            optimization_type=OPT_TYPE_INDEX,
            target=TARGET_DB,
            proposal="为 orders 表添加联合索引 (member_id, created_at)",
            expected_benefit={"querySpeedup": "10x"},
            execution_plan={"steps": ["ALTER TABLE orders ADD INDEX"]},
        )
        record("test_51_propose_optimization",
               result["optimizationType"] == OPT_TYPE_INDEX and result["id"] > 0,
               f"expected {OPT_TYPE_INDEX}/{'>0'}")

        # test 52: 初始状态为 proposed
        record("test_52_initial_status_proposed",
               result["optimizationStatus"] == OPTIMIZATION_STATUS_PROPOSED,
               f"expected {OPTIMIZATION_STATUS_PROPOSED}, got {result['optimizationStatus']}")

        # test 53: 批准优化(proposed → approved)
        opt_id = result["id"]
        approve_result = await svc.approve_optimization(opt_id)
        record("test_53_approve_optimization",
               approve_result["optimizationStatus"] == OPTIMIZATION_STATUS_APPROVED,
               f"expected {OPTIMIZATION_STATUS_APPROVED}, got {approve_result['optimizationStatus']}")

        # test 54: 审批人记录
        record("test_54_approved_by_recorded",
               approve_result["approvedBy"] == "admin",
               f"expected admin, got {approve_result.get('approvedBy')}")

        # test 55: 执行优化(approved → executing → completed)
        exec_result = await svc.execute_optimization(
            opt_id,
            actual_benefit={"querySpeedup": "12x", "latencyReduced": "85%"},
            success=True,
        )
        record("test_55_execute_optimization_completed",
               exec_result["optimizationStatus"] == OPTIMIZATION_STATUS_COMPLETED,
               f"expected {OPTIMIZATION_STATUS_COMPLETED}, got {exec_result['optimizationStatus']}")

        # test 56: 实际收益保存
        record("test_56_actual_benefit_saved",
               exec_result["actualBenefit"]["querySpeedup"] == "12x",
               f"expected 12x")

        # test 57: 重复批准已完成优化(状态非法)
        try:
            await svc.approve_optimization(opt_id)
            record("test_57_re_approve_completed", False, "应抛出ValueError")
        except ValueError:
            record("test_57_re_approve_completed", True)

        # test 58: 重复执行已完成优化(状态非法)
        try:
            await svc.execute_optimization(opt_id)
            record("test_58_re_execute_completed", False, "应抛出ValueError")
        except ValueError:
            record("test_58_re_execute_completed", True)

        # test 59: 驳回 proposed 优化(proposed → rejected)
        result2 = await svc.propose_optimization(
            optimization_type=OPT_TYPE_CACHE,
            target=TARGET_CACHE,
            proposal="增加 Redis 缓存容量",
        )
        reject_result = await svc.reject_optimization(result2["id"])
        record("test_59_reject_proposed",
               reject_result["optimizationStatus"] == OPTIMIZATION_STATUS_REJECTED,
               f"expected {OPTIMIZATION_STATUS_REJECTED}, got {reject_result['optimizationStatus']}")

        # test 60: 驳回已批准优化(approved → rejected)
        result3 = await svc.propose_optimization(
            optimization_type="参数优化",
            target=TARGET_DB,
            proposal="调整 innodb_buffer_pool_size",
        )
        await svc.approve_optimization(result3["id"])
        reject_result2 = await svc.reject_optimization(result3["id"])
        record("test_60_reject_approved",
               reject_result2["optimizationStatus"] == OPTIMIZATION_STATUS_REJECTED,
               f"expected {OPTIMIZATION_STATUS_REJECTED}")

        # test 61: 驳回已完成优化(状态非法)
        try:
            await svc.reject_optimization(opt_id)
            record("test_61_reject_completed", False, "应抛出ValueError")
        except ValueError:
            record("test_61_reject_completed", True)

        # test 62: 重复驳回已驳回优化(状态非法)
        try:
            await svc.reject_optimization(result2["id"])
            record("test_62_re_reject_rejected", False, "应抛出ValueError")
        except ValueError:
            record("test_62_re_reject_rejected", True)

        # test 63: 执行未批准优化(状态非法)
        result4 = await svc.propose_optimization(
            optimization_type="参数优化",
            target=TARGET_DB,
            proposal="调整连接池大小",
        )
        try:
            await svc.execute_optimization(result4["id"])
            record("test_63_execute_not_approved", False, "应抛出ValueError")
        except ValueError:
            record("test_63_execute_not_approved", True)

        # test 64: 优化类型为空
        try:
            await svc.propose_optimization("", TARGET_DB, "建议")
            record("test_64_empty_opt_type", False, "应抛出ValueError")
        except ValueError:
            record("test_64_empty_opt_type", True)

        # test 65: 优化建议为空
        try:
            await svc.propose_optimization(OPT_TYPE_INDEX, TARGET_DB, "")
            record("test_65_empty_proposal", False, "应抛出ValueError")
        except ValueError:
            record("test_65_empty_proposal", True)

        # test 66: 批准不存在的优化
        try:
            await svc.approve_optimization(99999)
            record("test_66_approve_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_66_approve_nonexistent", True)

        # test 67: 执行不存在的优化
        try:
            await svc.execute_optimization(99999)
            record("test_67_execute_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_67_execute_nonexistent", True)

        # test 68: 列表查询(按优化类型筛选)
        index_opts = await svc.list_optimizations(optimization_type=OPT_TYPE_INDEX)
        record("test_68_list_by_type",
               all(o["optimizationType"] == OPT_TYPE_INDEX for o in index_opts) and len(index_opts) >= 1,
               f"expected all index")

        # test 69: 列表查询(按优化状态筛选)
        completed_opts = await svc.list_optimizations(optimization_status=OPTIMIZATION_STATUS_COMPLETED)
        record("test_69_list_by_status",
               all(o["optimizationStatus"] == OPTIMIZATION_STATUS_COMPLETED for o in completed_opts),
               f"expected all completed")


class TestInspectAll:
    """一键巡检测试"""

    async def run(self, svc):
        # test 70: 一键巡检(默认服务列表)
        result = await svc.inspect_all()
        record("test_70_inspect_all_success",
               result["totalServices"] > 0 and len(result["healthIds"]) > 0,
               f"expected >0 services/health ids, got {result.get('totalServices')}/{len(result.get('healthIds', []))}")

        # test 71: 巡检创建健康检查记录
        record("test_71_health_ids_created",
               len(result["healthIds"]) == result["totalServices"],
               f"expected {result['totalServices']} health ids, got {len(result['healthIds'])}")

        # test 72: 不健康服务触发自愈
        record("test_72_recovery_ids_created",
               len(result["recoveryIds"]) > 0 and result["unhealthyCount"] > 0,
               f"expected >0 recovery ids, got {len(result.get('recoveryIds', []))}")

        # test 73: 状态分布统计
        record("test_73_status_count",
               sum(result["statusCount"].values()) == result["totalServices"],
               f"expected sum={result['totalServices']}, got {sum(result['statusCount'].values())}")

        # test 74: 巡检时间记录
        record("test_74_inspected_at",
               result["inspectedAt"] != "",
               f"expected non-empty inspectedAt")

        # test 75: 自定义服务列表巡检
        custom_result = await svc.inspect_all(services=[
            {"name": "custom-svc-1", "type": CHECK_TYPE_HTTP, "status": HEALTH_HEALTHY},
            {"name": "custom-svc-2", "type": CHECK_TYPE_TCP, "status": HEALTH_HEALTHY},
        ])
        record("test_75_custom_inspect",
               custom_result["totalServices"] == 2 and len(custom_result["recoveryIds"]) == 0,
               f"expected 2 services/0 recoveries, got {custom_result['totalServices']}/{len(custom_result['recoveryIds'])}")


class TestStats:
    """统计测试"""

    async def run(self, svc):
        # 准备数据
        task = await svc.create_task(
            task_name="统计测试任务",
            task_type=TASK_TYPE_BACKUP,
            target=TARGET_DB,
        )
        await svc.execute_task(task["id"])

        await svc.create_health_check(
            check_name="统计测试健康",
            service_name="stats-svc",
            check_type=CHECK_TYPE_HTTP,
        )

        await svc.detect_fault(
            fault_type="统计测试故障",
            fault_source="stats-svc",
        )

        opt = await svc.propose_optimization(
            optimization_type="统计测试优化",
            target="stats-svc",
            proposal="测试建议",
        )
        await svc.approve_optimization(opt["id"])

        # test 76: 统计字段完整性
        stats = await svc.get_stats()
        record("test_76_stats_fields",
               all(k in stats for k in ["totalTasks", "totalHealthChecks",
                                          "totalRecoveries", "totalOptimizations",
                                          "taskStatusCount", "healthStatusCount",
                                          "recoveryStatusCount", "optimizationStatusCount"]),
               f"missing fields: {stats}")

        # test 77: 统计数量正确
        record("test_77_stats_count",
               stats["totalTasks"] >= 1 and stats["totalHealthChecks"] >= 1
               and stats["totalRecoveries"] >= 1 and stats["totalOptimizations"] >= 1,
               f"expected >=1 each, got {stats['totalTasks']}/{stats['totalHealthChecks']}/{stats['totalRecoveries']}/{stats['totalOptimizations']}")

        # test 78: 任务状态分布(含 success)
        record("test_78_task_status_distribution",
               TASK_STATUS_SUCCESS in stats["taskStatusCount"],
               f"expected success in: {stats['taskStatusCount']}")

        # test 79: 优化状态分布(含 approved)
        record("test_79_optimization_status_distribution",
               OPTIMIZATION_STATUS_APPROVED in stats["optimizationStatusCount"],
               f"expected approved in: {stats['optimizationStatusCount']}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("AI 智能维护模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestMaintenanceTask,
        TestHealthCheck,
        TestRecovery,
        TestOptimization,
        TestInspectAll,
        TestStats,
    ]

    for cls in test_classes:
        reset_store()
        svc = MaintenanceService()
        print(f"[{cls.__name__}]")
        instance = cls()
        await instance.run(svc)
        print()

    # 输出全部结果
    print("=" * 60)
    print("测试结果汇总:")
    print("-" * 60)
    for r in RESULTS:
        print(r)
    print("-" * 60)
    print(f"通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
    print("=" * 60)

    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
