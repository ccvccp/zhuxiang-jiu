"""AI 智能监控模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 MonitorService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_monitor_routes.py

覆盖 12 个接口对应的业务方法:
    1. 指标采集(2):  collect_metric / list_metrics
    2. 告警管理(3):  raise_alert / list_alerts / (acknowledge/resolve/suppress)
    3. 故障事件(3):  raise_incident / list_incidents / (investigate/mitigate/resolve/postmortem)
    4. 仪表盘(2):    create_dashboard / list_dashboards
    5. 健康检查(1):  health_check
    6. 统计(1):      get_stats
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.monitor_service import MonitorService
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

METRIC_CPU = "cpu_usage"
METRIC_MEMORY = "memory_usage"
METRIC_QPS = "qps"
METRIC_LATENCY = "latency_p99"
METRIC_ERROR_RATE = "error_rate"
SOURCE_API_1 = "api-server-1"
SOURCE_DB_1 = "db-master-1"
ALERT_TYPE_SYSTEM = "system"
ALERT_TYPE_BUSINESS = "business"
ALERT_TYPE_PERFORMANCE = "performance"
SOURCE_ORDER = "order-module"
SOURCE_WALLET = "wallet-module"
INCIDENT_TYPE_SYSTEM = "system"
INCIDENT_TYPE_SECURITY = "security"
SOURCE_INFRA = "infrastructure"


# ============================================================
# 测试用例
# ============================================================

class TestMetricCollect:
    """指标采集测试"""

    async def run(self, svc):
        # test 01: 正常采集系统指标(CPU)
        result = await svc.collect_metric(
            METRIC_CPU, METRIC_TYPE_SYSTEM, 75.5,
            SOURCE_API_1, metric_unit="%",
            tags={"host": "api-1", "region": "cn-east"},
        )
        record("test_01_collect_cpu_success",
               result["metricName"] == METRIC_CPU and result["id"] > 0,
               f"expected cpu/{'>0'}, got {result.get('metricName')}/{result.get('id')}")

        # test 02: 采集业务指标(QPS)
        result = await svc.collect_metric(
            METRIC_QPS, METRIC_TYPE_BUSINESS, 1200.0,
            SOURCE_API_1, metric_unit="req/s",
        )
        record("test_02_collect_qps_success",
               result["metricType"] == METRIC_TYPE_BUSINESS,
               f"expected {METRIC_TYPE_BUSINESS}, got {result.get('metricType')}")

        # test 03: 异常检测(warning 级别)
        result = await svc.collect_metric(
            METRIC_CPU, METRIC_TYPE_SYSTEM, 85.0,
            SOURCE_API_1,
            threshold={"warning": 80, "critical": 90},
        )
        record("test_03_anomaly_warning",
               result["anomalyDetect"]["detected"] is True
               and result["anomalyDetect"]["level"] == ALERT_LEVEL_WARNING,
               f"expected detected/warning, got {result.get('anomalyDetect')}")

        # test 04: 异常检测(critical 级别)
        result = await svc.collect_metric(
            METRIC_CPU, METRIC_TYPE_SYSTEM, 95.0,
            SOURCE_API_1,
            threshold={"warning": 80, "critical": 90},
        )
        record("test_04_anomaly_critical",
               result["anomalyDetect"]["detected"] is True
               and result["anomalyDetect"]["level"] == ALERT_LEVEL_CRITICAL,
               f"expected detected/critical, got {result.get('anomalyDetect')}")

        # test 05: 无阈值时无异常
        result = await svc.collect_metric(
            METRIC_MEMORY, METRIC_TYPE_SYSTEM, 70.0,
            SOURCE_API_1,
        )
        record("test_05_no_threshold_no_anomaly",
               result["anomalyDetect"]["detected"] is False,
               f"expected not detected, got {result.get('anomalyDetect')}")

        # test 06: 指标名称为空
        try:
            await svc.collect_metric("", METRIC_TYPE_SYSTEM, 50.0, SOURCE_API_1)
            record("test_06_empty_metric_name", False, "应抛出ValueError")
        except ValueError:
            record("test_06_empty_metric_name", True)

        # test 07: 来源为空
        try:
            await svc.collect_metric(METRIC_CPU, METRIC_TYPE_SYSTEM, 50.0, "")
            record("test_07_empty_source", False, "应抛出ValueError")
        except ValueError:
            record("test_07_empty_source", True)

        # test 08: 非法指标类型
        try:
            await svc.collect_metric(METRIC_CPU, "invalid_type", 50.0, SOURCE_API_1)
            record("test_08_invalid_metric_type", False, "应抛出ValueError")
        except ValueError:
            record("test_08_invalid_metric_type", True)

        # test 09: 查询指标详情
        record_id = result.get("id", 1)
        detail = await svc.get_metric(record_id)
        record("test_09_get_metric_detail",
               detail["id"] == record_id,
               f"expected id={record_id}")

        # test 10: 查询不存在的指标
        try:
            await svc.get_metric(99999)
            record("test_10_get_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_10_get_nonexistent", True)

        # test 11: 列表查询(按指标名筛选)
        metrics = await svc.list_metrics(metric_name=METRIC_CPU)
        record("test_11_list_by_metric_name",
               all(m["metricName"] == METRIC_CPU for m in metrics) and len(metrics) >= 1,
               f"expected all {METRIC_CPU}, got {len(metrics)}")

        # test 12: 列表查询(按类型筛选)
        metrics = await svc.list_metrics(metric_type=METRIC_TYPE_BUSINESS)
        record("test_12_list_by_metric_type",
               all(m["metricType"] == METRIC_TYPE_BUSINESS for m in metrics),
               "expected all business")

        # test 13: 列表查询(按来源筛选)
        metrics = await svc.list_metrics(source=SOURCE_DB_1)
        record("test_13_list_by_source",
               all(m["source"] == SOURCE_DB_1 for m in metrics),
               "expected all db-master-1")


class TestAlertManagement:
    """告警管理测试(含状态机)"""

    async def run(self, svc):
        # test 14: 创建 info 级别告警
        result = await svc.raise_alert(
            "CPU 使用率正常", ALERT_TYPE_SYSTEM, ALERT_LEVEL_INFO,
            SOURCE_API_1, current_value=50.0,
        )
        record("test_14_raise_info_alert",
               result["alertLevel"] == ALERT_LEVEL_INFO and result["id"] > 0,
               f"expected info/{'>0'}")
        record("test_14a_info_default_channels",
               "log" in result["notification"]["channels"],
               f"expected log channel, got {result.get('notification')}")

        # test 15: 创建 warning 级别告警
        result = await svc.raise_alert(
            "CPU 使用率偏高", ALERT_TYPE_SYSTEM, ALERT_LEVEL_WARNING,
            SOURCE_API_1, current_value=85.0,
        )
        record("test_15_raise_warning_alert",
               result["alertLevel"] == ALERT_LEVEL_WARNING,
               f"expected {ALERT_LEVEL_WARNING}")
        record("test_15a_warning_default_channels",
               "inbox" in result["notification"]["channels"],
               f"expected inbox channel, got {result.get('notification')}")

        # test 16: 创建 critical 级别告警
        result = await svc.raise_alert(
            "CPU 使用率严重", ALERT_TYPE_SYSTEM, ALERT_LEVEL_CRITICAL,
            SOURCE_API_1, current_value=92.0,
        )
        record("test_16_raise_critical_alert",
               result["alertLevel"] == ALERT_LEVEL_CRITICAL,
               f"expected {ALERT_LEVEL_CRITICAL}")
        record("test_16a_critical_default_channels",
               "sms" in result["notification"]["channels"],
               f"expected sms channel, got {result.get('notification')}")

        # test 17: 创建 fatal 级别告警
        result = await svc.raise_alert(
            "服务宕机", ALERT_TYPE_SYSTEM, ALERT_LEVEL_FATAL,
            SOURCE_API_1, current_value=100.0,
        )
        record("test_17_raise_fatal_alert",
               result["alertLevel"] == ALERT_LEVEL_FATAL,
               f"expected {ALERT_LEVEL_FATAL}")
        record("test_17a_fatal_default_channels",
               "phone" in result["notification"]["channels"]
               and "sms" in result["notification"]["channels"],
               f"expected phone+sms channels, got {result.get('notification')}")

        # test 18: 创建业务告警
        result = await svc.raise_alert(
            "订单量异常下降", ALERT_TYPE_BUSINESS, ALERT_LEVEL_WARNING,
            SOURCE_ORDER,
        )
        record("test_18_raise_business_alert",
               result["alertType"] == ALERT_TYPE_BUSINESS,
               f"expected {ALERT_TYPE_BUSINESS}")

        # test 19: 告警名称为空
        try:
            await svc.raise_alert("", ALERT_TYPE_SYSTEM, ALERT_LEVEL_INFO, SOURCE_API_1)
            record("test_19_empty_alert_name", False, "应抛出ValueError")
        except ValueError:
            record("test_19_empty_alert_name", True)

        # test 20: 非法告警级别
        try:
            await svc.raise_alert("test", ALERT_TYPE_SYSTEM, "invalid", SOURCE_API_1)
            record("test_20_invalid_alert_level", False, "应抛出ValueError")
        except ValueError:
            record("test_20_invalid_alert_level", True)

        # === 状态机测试 ===

        # test 21: 确认告警(pending → acknowledged)
        result = await svc.raise_alert(
            "测试告警-确认流程", ALERT_TYPE_SYSTEM, ALERT_LEVEL_WARNING,
            SOURCE_API_1,
        )
        alert_id = result["id"]
        ack_result = await svc.acknowledge_alert(alert_id, "运维A")
        record("test_21_acknowledge_alert",
               ack_result["alertStatus"] == ALERT_STATUS_ACKNOWLEDGED
               and ack_result["acknowledgedBy"] == "运维A",
               f"expected {ALERT_STATUS_ACKNOWLEDGED}/运维A, "
               f"got {ack_result.get('alertStatus')}/{ack_result.get('acknowledgedBy')}")

        # test 22: 解决告警(acknowledged → resolved)
        res_result = await svc.resolve_alert(alert_id, "运维B")
        record("test_22_resolve_alert",
               res_result["alertStatus"] == ALERT_STATUS_RESOLVED,
               f"expected {ALERT_STATUS_RESOLVED}, got {res_result.get('alertStatus')}")

        # test 23: 抑制告警(resolved → suppressed)
        sup_result = await svc.suppress_alert(alert_id, "运维C")
        record("test_23_suppress_from_resolved",
               sup_result["alertStatus"] == ALERT_STATUS_SUPPRESSED,
               f"expected {ALERT_STATUS_SUPPRESSED}, got {sup_result.get('alertStatus')}")

        # test 24: 抑制告警(pending → suppressed, 直接抑制)
        result = await svc.raise_alert(
            "测试告警-直接抑制", ALERT_TYPE_SYSTEM, ALERT_LEVEL_INFO,
            SOURCE_API_1,
        )
        alert_id2 = result["id"]
        sup_result2 = await svc.suppress_alert(alert_id2)
        record("test_24_suppress_from_pending",
               sup_result2["alertStatus"] == ALERT_STATUS_SUPPRESSED,
               f"expected {ALERT_STATUS_SUPPRESSED}, got {sup_result2.get('alertStatus')}")

        # test 25: 非法状态流转(从 resolved 确认)
        try:
            await svc.acknowledge_alert(alert_id)
            record("test_25_illegal_transition_ack", False, "应抛出ValueError")
        except ValueError:
            record("test_25_illegal_transition_ack", True)

        # test 26: 非法状态流转(从 pending 解决, 须先 acknowledged)
        result = await svc.raise_alert(
            "测试告警-非法解决", ALERT_TYPE_SYSTEM, ALERT_LEVEL_INFO,
            SOURCE_API_1,
        )
        alert_id3 = result["id"]
        try:
            await svc.resolve_alert(alert_id3)
            record("test_26_illegal_resolve_from_pending", False, "应抛出ValueError")
        except ValueError:
            record("test_26_illegal_resolve_from_pending", True)

        # test 27: 确认不存在的告警
        try:
            await svc.acknowledge_alert(99999)
            record("test_27_acknowledge_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_27_acknowledge_nonexistent", True)

        # test 28: 解决不存在的告警
        try:
            await svc.resolve_alert(99999)
            record("test_28_resolve_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_28_resolve_nonexistent", True)

        # test 29: 列表查询(按状态筛选)
        pending_alerts = await svc.list_alerts(alert_status=ALERT_STATUS_PENDING)
        record("test_29_list_by_status",
               all(a["alertStatus"] == ALERT_STATUS_PENDING for a in pending_alerts),
               "expected all pending")

        # test 30: 列表查询(按级别筛选)
        fatal_alerts = await svc.list_alerts(alert_level=ALERT_LEVEL_FATAL)
        record("test_30_list_by_level",
               all(a["alertLevel"] == ALERT_LEVEL_FATAL for a in fatal_alerts)
               and len(fatal_alerts) >= 1,
               f"expected all fatal, got {len(fatal_alerts)}")

        # test 31: 列表查询(按类型筛选)
        business_alerts = await svc.list_alerts(alert_type=ALERT_TYPE_BUSINESS)
        record("test_31_list_by_type",
               all(a["alertType"] == ALERT_TYPE_BUSINESS for a in business_alerts),
               "expected all business")

        # test 32: 查询告警详情
        detail = await svc.get_alert(alert_id)
        record("test_32_get_alert_detail",
               detail["id"] == alert_id,
               f"expected id={alert_id}")

        # test 33: 查询不存在的告警
        try:
            await svc.get_alert(99999)
            record("test_33_get_nonexistent_alert", False, "应抛出KeyError")
        except KeyError:
            record("test_33_get_nonexistent_alert", True)


class TestIncidentManagement:
    """故障事件测试(含状态机)"""

    async def run(self, svc):
        # test 34: 创建 P0 故障
        result = await svc.raise_incident(
            "核心服务宕机", INCIDENT_TYPE_SYSTEM, INCIDENT_LEVEL_P0,
            SOURCE_INFRA, impact={"users": 5000, "services": ["order", "payment"]},
            assignee="值班运维",
        )
        record("test_34_raise_p0_incident",
               result["incidentLevel"] == INCIDENT_LEVEL_P0 and result["id"] > 0,
               f"expected {INCIDENT_LEVEL_P0}/{'>0'}")
        record("test_34a_initial_status_detected",
               result["incidentStatus"] == INCIDENT_STATUS_DETECTED,
               f"expected {INCIDENT_STATUS_DETECTED}, got {result.get('incidentStatus')}")
        record("test_34b_timeline_has_detection",
               any(item.get("event") == "检测" for item in result.get("timeline", [])),
               f"expected 检测 in timeline, got {result.get('timeline')}")

        # test 35: 创建 P1 故障
        result = await svc.raise_incident(
            "数据库响应慢", INCIDENT_TYPE_SYSTEM, INCIDENT_LEVEL_P1,
            SOURCE_DB_1,
        )
        record("test_35_raise_p1_incident",
               result["incidentLevel"] == INCIDENT_LEVEL_P1,
               f"expected {INCIDENT_LEVEL_P1}")

        # test 36: 创建 P2 故障
        result = await svc.raise_incident(
            "缓存命中率下降", INCIDENT_TYPE_SYSTEM, INCIDENT_LEVEL_P2,
            SOURCE_INFRA,
        )
        record("test_36_raise_p2_incident",
               result["incidentLevel"] == INCIDENT_LEVEL_P2,
               f"expected {INCIDENT_LEVEL_P2}")

        # test 37: 创建 P3 故障
        result = await svc.raise_incident(
            "日志写入延迟", INCIDENT_TYPE_SYSTEM, INCIDENT_LEVEL_P3,
            SOURCE_INFRA,
        )
        record("test_37_raise_p3_incident",
               result["incidentLevel"] == INCIDENT_LEVEL_P3,
               f"expected {INCIDENT_LEVEL_P3}")

        # test 38: 故障名称为空
        try:
            await svc.raise_incident("", INCIDENT_TYPE_SYSTEM, INCIDENT_LEVEL_P3,
                                       SOURCE_INFRA)
            record("test_38_empty_incident_name", False, "应抛出ValueError")
        except ValueError:
            record("test_38_empty_incident_name", True)

        # test 39: 来源为空
        try:
            await svc.raise_incident("test", INCIDENT_TYPE_SYSTEM, INCIDENT_LEVEL_P3, "")
            record("test_39_empty_source", False, "应抛出ValueError")
        except ValueError:
            record("test_39_empty_source", True)

        # test 40: 非法故障级别
        try:
            await svc.raise_incident("test", INCIDENT_TYPE_SYSTEM, "P9", SOURCE_INFRA)
            record("test_40_invalid_incident_level", False, "应抛出ValueError")
        except ValueError:
            record("test_40_invalid_incident_level", True)

        # === 状态机测试(完整流程: detected → postmortem) ===

        # test 41: 创建新故障用于状态流转测试
        result = await svc.raise_incident(
            "状态流转测试故障", INCIDENT_TYPE_SECURITY, INCIDENT_LEVEL_P1,
            SOURCE_INFRA, assignee="测试运维",
        )
        incident_id = result["id"]

        # test 42: 调查故障(detected → investigating)
        inv_result = await svc.investigate_incident(incident_id, "测试运维",
                                                      root_cause="数据库连接池耗尽")
        record("test_42_investigate_incident",
               inv_result["incidentStatus"] == INCIDENT_STATUS_INVESTIGATING,
               f"expected {INCIDENT_STATUS_INVESTIGATING}, "
               f"got {inv_result.get('incidentStatus')}")
        record("test_42a_root_cause_set",
               inv_result["rootCause"] == "数据库连接池耗尽",
               f"expected root cause set, got {inv_result.get('rootCause')}")
        record("test_42b_timeline_has_investigate",
               any(item.get("event") == "开始调查" for item in inv_result.get("timeline", [])),
               "expected 开始调查 in timeline")

        # test 43: 处置故障(investigating → mitigating)
        mit_result = await svc.mitigate_incident(incident_id, "测试运维",
                                                   mitigation="扩容连接池")
        record("test_43_mitigate_incident",
               mit_result["incidentStatus"] == INCIDENT_STATUS_MITIGATING,
               f"expected {INCIDENT_STATUS_MITIGATING}, "
               f"got {mit_result.get('incidentStatus')}")
        record("test_43a_mitigation_set",
               mit_result["mitigation"] == "扩容连接池",
               f"expected mitigation set, got {mit_result.get('mitigation')}")

        # test 44: 解决故障(mitigating → resolved)
        res_result = await svc.resolve_incident(incident_id, "测试运维")
        record("test_44_resolve_incident",
               res_result["incidentStatus"] == INCIDENT_STATUS_RESOLVED,
               f"expected {INCIDENT_STATUS_RESOLVED}, "
               f"got {res_result.get('incidentStatus')}")
        record("test_44a_resolved_at_set",
               res_result["resolvedAt"] != "",
               f"expected resolvedAt set, got {res_result.get('resolvedAt')}")

        # test 45: 复盘故障(resolved → postmortem)
        pm_result = await svc.postmortem_incident(incident_id, "测试运维",
                                                    postmortem_doc="复盘文档v1.md")
        record("test_45_postmortem_incident",
               pm_result["incidentStatus"] == INCIDENT_STATUS_POSTMORTEM,
               f"expected {INCIDENT_STATUS_POSTMORTEM}, "
               f"got {pm_result.get('incidentStatus')}")
        record("test_45a_postmortem_doc_set",
               pm_result["postmortemDoc"] == "复盘文档v1.md",
               f"expected postmortem doc set, got {pm_result.get('postmortemDoc')}")
        record("test_45b_postmortem_at_set",
               pm_result["postmortemAt"] != "",
               f"expected postmortemAt set, got {pm_result.get('postmortemAt')}")

        # test 46: 非法状态流转(从 postmortem 回到 investigating)
        try:
            await svc.investigate_incident(incident_id)
            record("test_46_illegal_investigate_from_postmortem", False, "应抛出ValueError")
        except ValueError:
            record("test_46_illegal_investigate_from_postmortem", True)

        # test 47: 非法状态流转(从 detected 直接到 resolved, 须先流转到 investigating/mitigating)
        result = await svc.raise_incident(
            "非法流转测试", INCIDENT_TYPE_SYSTEM, INCIDENT_LEVEL_P3, SOURCE_INFRA,
        )
        incident_id2 = result["id"]
        try:
            await svc.resolve_incident(incident_id2)
            record("test_47_illegal_resolve_from_detected", False, "应抛出ValueError")
        except ValueError:
            record("test_47_illegal_resolve_from_detected", True)

        # test 48: 调查不存在的故障
        try:
            await svc.investigate_incident(99999)
            record("test_48_investigate_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_48_investigate_nonexistent", True)

        # test 49: 处置不存在的故障
        try:
            await svc.mitigate_incident(99999)
            record("test_49_mitigate_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_49_mitigate_nonexistent", True)

        # test 50: 复盘不存在的故障
        try:
            await svc.postmortem_incident(99999)
            record("test_50_postmortem_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_50_postmortem_nonexistent", True)

        # test 51: 列表查询(按级别筛选)
        p0_incidents = await svc.list_incidents(incident_level=INCIDENT_LEVEL_P0)
        record("test_51_list_by_level",
               all(i["incidentLevel"] == INCIDENT_LEVEL_P0 for i in p0_incidents)
               and len(p0_incidents) >= 1,
               f"expected all P0, got {len(p0_incidents)}")

        # test 52: 列表查询(按类型筛选)
        security_incidents = await svc.list_incidents(incident_type=INCIDENT_TYPE_SECURITY)
        record("test_52_list_by_type",
               all(i["incidentType"] == INCIDENT_TYPE_SECURITY
                   for i in security_incidents),
               "expected all security")

        # test 53: 列表查询(按状态筛选)
        postmortem_incidents = await svc.list_incidents(
            incident_status=INCIDENT_STATUS_POSTMORTEM)
        record("test_53_list_by_status",
               all(i["incidentStatus"] == INCIDENT_STATUS_POSTMORTEM
                   for i in postmortem_incidents)
               and len(postmortem_incidents) >= 1,
               "expected all postmortem")

        # test 54: 查询故障详情
        detail = await svc.get_incident(incident_id)
        record("test_54_get_incident_detail",
               detail["id"] == incident_id,
               f"expected id={incident_id}")

        # test 55: 查询不存在的故障
        try:
            await svc.get_incident(99999)
            record("test_55_get_nonexistent_incident", False, "应抛出KeyError")
        except KeyError:
            record("test_55_get_nonexistent_incident", True)


class TestDashboard:
    """仪表盘配置测试"""

    async def run(self, svc):
        # test 56: 创建系统仪表盘
        result = await svc.create_dashboard(
            "系统监控大盘", DASHBOARD_TYPE_SYSTEM,
            owner="admin",
            widgets=[{"type": "chart", "metric": METRIC_CPU, "span": 6}],
            layout={"columns": 12},
            refresh_interval=15,
            is_shared=True,
        )
        record("test_56_create_system_dashboard",
               result["dashboardType"] == DASHBOARD_TYPE_SYSTEM and result["id"] > 0,
               f"expected {DASHBOARD_TYPE_SYSTEM}/{'>0'}")
        record("test_56a_widgets_set",
               len(result["widgets"]) == 1,
               f"expected 1 widget, got {len(result.get('widgets', []))}")
        record("test_56b_is_shared",
               result["isShared"] is True,
               f"expected True, got {result.get('isShared')}")

        # test 57: 创建业务仪表盘
        result = await svc.create_dashboard(
            "业务运营大盘", DASHBOARD_TYPE_BUSINESS,
        )
        record("test_57_create_business_dashboard",
               result["dashboardType"] == DASHBOARD_TYPE_BUSINESS,
               f"expected {DASHBOARD_TYPE_BUSINESS}")

        # test 58: 创建故障仪表盘
        result = await svc.create_dashboard(
            "故障追踪视图", DASHBOARD_TYPE_INCIDENT,
        )
        record("test_58_create_incident_dashboard",
               result["dashboardType"] == DASHBOARD_TYPE_INCIDENT,
               f"expected {DASHBOARD_TYPE_INCIDENT}")

        # test 59: 创建自定义仪表盘
        result = await svc.create_dashboard(
            "我的自定义视图", DASHBOARD_TYPE_CUSTOM,
            owner="user01",
            filters={"region": "cn-east"},
        )
        record("test_59_create_custom_dashboard",
               result["dashboardType"] == DASHBOARD_TYPE_CUSTOM,
               f"expected {DASHBOARD_TYPE_CUSTOM}")
        record("test_59a_owner_set",
               result["owner"] == "user01",
               f"expected user01, got {result.get('owner')}")

        # test 60: 仪表盘名称为空
        try:
            await svc.create_dashboard("", DASHBOARD_TYPE_SYSTEM)
            record("test_60_empty_dashboard_name", False, "应抛出ValueError")
        except ValueError:
            record("test_60_empty_dashboard_name", True)

        # test 61: 非法仪表盘类型
        try:
            await svc.create_dashboard("test", "invalid_type")
            record("test_61_invalid_dashboard_type", False, "应抛出ValueError")
        except ValueError:
            record("test_61_invalid_dashboard_type", True)

        # test 62: 列表查询(按类型筛选)
        system_dashboards = await svc.list_dashboards(
            dashboard_type=DASHBOARD_TYPE_SYSTEM)
        record("test_62_list_by_type",
               all(d["dashboardType"] == DASHBOARD_TYPE_SYSTEM
                   for d in system_dashboards)
               and len(system_dashboards) >= 1,
               f"expected all system, got {len(system_dashboards)}")

        # test 63: 列表查询(按 owner 筛选)
        user_dashboards = await svc.list_dashboards(owner="user01")
        record("test_63_list_by_owner",
               all(d["owner"] == "user01" for d in user_dashboards)
               and len(user_dashboards) >= 1,
               f"expected all user01, got {len(user_dashboards)}")

        # test 64: 查询仪表盘详情
        detail = await svc.get_dashboard(result["id"])
        record("test_64_get_dashboard_detail",
               detail["id"] == result["id"],
               f"expected id={result['id']}")

        # test 65: 查询不存在的仪表盘
        try:
            await svc.get_dashboard(99999)
            record("test_65_get_nonexistent_dashboard", False, "应抛出KeyError")
        except KeyError:
            record("test_65_get_nonexistent_dashboard", True)


class TestHealthCheck:
    """健康检查测试"""

    async def run(self, svc):
        # 准备数据(仅 info 告警, 无致命告警, 无 P0 故障)
        await svc.collect_metric(METRIC_CPU, METRIC_TYPE_SYSTEM, 50.0, SOURCE_API_1)
        await svc.raise_alert("普通信息告警", ALERT_TYPE_SYSTEM,
                                ALERT_LEVEL_INFO, SOURCE_API_1)

        # test 66: 健康检查字段完整性
        health = await svc.health_check()
        record("test_66_health_fields",
               all(k in health for k in ["status", "totalMetrics", "totalAlerts",
                                           "totalIncidents", "totalDashboards",
                                           "pendingAlerts", "activeIncidents",
                                           "fatalAlerts", "p0Incidents", "checkedAt"]),
               f"missing fields: {health}")

        # test 67: 健康状态(无致命告警无P0故障, 应为 healthy 或 warning)
        record("test_67_health_status",
               health["status"] in ("healthy", "warning"),
               f"expected healthy/warning, got {health.get('status')}")

        # test 68: 待处理告警数正确(info 告警默认 pending)
        record("test_68_pending_alerts",
               health["pendingAlerts"] >= 1,
               f"expected >=1, got {health.get('pendingAlerts')}")

        # test 69: 致命告警数为 0
        record("test_69_no_fatal_alerts",
               health["fatalAlerts"] == 0,
               f"expected 0, got {health.get('fatalAlerts')}")


class TestStats:
    """统计测试"""

    async def run(self, svc):
        # 准备数据
        await svc.collect_metric(METRIC_CPU, METRIC_TYPE_SYSTEM, 50.0, SOURCE_API_1)
        await svc.collect_metric(METRIC_QPS, METRIC_TYPE_BUSINESS, 1000.0, SOURCE_API_1)
        await svc.raise_alert("测试告警A", ALERT_TYPE_SYSTEM,
                                ALERT_LEVEL_WARNING, SOURCE_API_1)
        await svc.raise_alert("测试告警B", ALERT_TYPE_SYSTEM,
                                ALERT_LEVEL_FATAL, SOURCE_API_1)
        await svc.raise_incident("测试故障A", INCIDENT_TYPE_SYSTEM,
                                   INCIDENT_LEVEL_P0, SOURCE_INFRA)
        await svc.raise_incident("测试故障B", INCIDENT_TYPE_SECURITY,
                                   INCIDENT_LEVEL_P2, SOURCE_INFRA)
        await svc.create_dashboard("统计测试盘", DASHBOARD_TYPE_CUSTOM)

        # test 70: 统计字段完整性
        stats = await svc.get_stats()
        record("test_70_stats_fields",
               all(k in stats for k in ["totalMetrics", "totalAlerts",
                                          "totalIncidents", "totalDashboards",
                                          "alertStatusCount", "alertLevelCount",
                                          "incidentStatusCount", "incidentLevelCount",
                                          "metricTypeCount"]),
               f"missing fields: {stats}")

        # test 71: 统计数量正确
        record("test_71_stats_count",
               stats["totalMetrics"] >= 2 and stats["totalAlerts"] >= 2
               and stats["totalIncidents"] >= 2 and stats["totalDashboards"] >= 1,
               f"expected metrics>=2/alerts>=2/incidents>=2/dashboards>=1, "
               f"got {stats['totalMetrics']}/{stats['totalAlerts']}/"
               f"{stats['totalIncidents']}/{stats['totalDashboards']}")

        # test 72: 告警级别分布
        record("test_72_alert_level_distribution",
               ALERT_LEVEL_WARNING in stats["alertLevelCount"]
               and ALERT_LEVEL_FATAL in stats["alertLevelCount"],
               f"expected warning/fatal in: {stats.get('alertLevelCount')}")

        # test 73: 告警状态分布(初始为 pending)
        record("test_73_alert_status_distribution",
               ALERT_STATUS_PENDING in stats["alertStatusCount"],
               f"expected pending in: {stats.get('alertStatusCount')}")

        # test 74: 故障级别分布
        record("test_74_incident_level_distribution",
               INCIDENT_LEVEL_P0 in stats["incidentLevelCount"]
               and INCIDENT_LEVEL_P2 in stats["incidentLevelCount"],
               f"expected P0/P2 in: {stats.get('incidentLevelCount')}")

        # test 75: 故障状态分布(初始为 detected)
        record("test_75_incident_status_distribution",
               INCIDENT_STATUS_DETECTED in stats["incidentStatusCount"],
               f"expected detected in: {stats.get('incidentStatusCount')}")

        # test 76: 指标类型分布
        record("test_76_metric_type_distribution",
               METRIC_TYPE_SYSTEM in stats["metricTypeCount"]
               and METRIC_TYPE_BUSINESS in stats["metricTypeCount"],
               f"expected system/business in: {stats.get('metricTypeCount')}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("AI 智能监控模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestMetricCollect,
        TestAlertManagement,
        TestIncidentManagement,
        TestDashboard,
        TestHealthCheck,
        TestStats,
    ]

    for cls in test_classes:
        reset_store()
        svc = MonitorService()
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
    print("=== 测试结果 ===")
    print(f"通过率: {PASS / (PASS + FAIL) * 100:.1f}%  "
          f"({PASS}/{PASS + FAIL})")

    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
