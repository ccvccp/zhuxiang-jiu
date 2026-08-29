"""合规合法智能监控模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 ComplianceService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_compliance_routes.py

覆盖 12 个接口对应的业务方法:
    1. 行为监控(3):   monitor_behavior / list_behavior_monitors / get_behavior_monitor
    2. 条款监控(2):   monitor_terms / list_terms_monitors
    3. 法律知识(2):   add_legal_knowledge / search_legal_knowledge
    4. 风险预警(2):   raise_risk_warning / list_risk_warnings
    5. 监管报送(2):   submit_regulatory_report / accept_regulatory_report
    6. 区块链存证(2): add_blockchain_evidence / verify_evidence_by_hash
    7. 分析报告(1):   create_analysis_report
    8. 持续优化(1):   update_optimization
    9. 统计(1):       get_stats
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.compliance_service import ComplianceService
from repositories.compliance_repository import (
    ComplianceRepository,
    # 风险等级
    RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM, RISK_LEVEL_HIGH, RISK_LEVEL_EXTREME,
    # 报送状态
    REPORT_STATUS_PENDING, REPORT_STATUS_SUBMITTED, REPORT_STATUS_ACCEPTED,
    # 存证类型
    EVIDENCE_TYPE_COMPLIANCE, EVIDENCE_TYPE_RISK, EVIDENCE_TYPE_DISPOSAL, EVIDENCE_TYPE_REGULATORY,
    # 处置方式
    DISPOSAL_WARN, DISPOSAL_LIMIT, DISPOSAL_BLOCK, DISPOSAL_REPORT,
    # 报送类型
    REPORT_TYPE_LARGE_AMOUNT, REPORT_TYPE_SUSPICIOUS, REPORT_TYPE_REGULAR, REPORT_TYPE_INQUIRY,
    # 分析周期
    PERIOD_DAILY, PERIOD_WEEKLY, PERIOD_MONTHLY,
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

MODULE_ORDER = "零售订单"
MODULE_WALLET = "钱包盈利"
MODULE_RECYCLE = "老酒回收"
BEHAVIOR_ORDER = "下单"
BEHAVIOR_PAYMENT = "支付"
TERMS_USER = "用户协议"
TERMS_PRIVACY = "隐私政策"
TERMS_WALLET = "钱包协议"
LAW_CIVIL = "民法典"
LAW_ECOMMERCE = "电子商务法"
LAW_CATEGORY_CIVIL = "民事"
LAW_CATEGORY_COMMERCE = "电商"
RISK_TYPE_FRAUD = "欺诈风险"
RISK_TYPE_MONEY_LAUNDERING = "洗钱风险"
RISK_SOURCE_WALLET = "钱包模块"
REPORT_TARGET_PBOC = "央行/反洗钱中心"
REPORT_TARGET_MARKET = "市场监管"


# ============================================================
# 测试用例
# ============================================================

class TestBehaviorMonitor:
    """行为监控测试"""

    async def run(self, svc):
        # test 1: 正常行为监控(低风险)
        result = await svc.monitor_behavior(
            MODULE_ORDER, BEHAVIOR_ORDER,
            behavior_data={"orderId": "RT001", "amount": 536.0},
            risk_level=RISK_LEVEL_LOW
        )
        record("test_01_monitor_behavior_success",
               result["moduleName"] == MODULE_ORDER and result["id"] > 0,
               f"expected order/{'>0'}, got {result.get('moduleName')}/{result.get('id')}")

        # test 2: 风险等级对应处置方式(low→warn)
        record("test_02_low_risk_disposal",
               result["disposal"] == DISPOSAL_WARN,
               f"expected {DISPOSAL_WARN}, got {result['disposal']}")

        # test 3: 区块链存证生成
        record("test_03_evidence_generated",
               result.get("evidenceHash") is not None and result.get("evidenceId") > 0,
               f"expected evidence, got {result}")

        # test 4: 高风险处置方式(high→block)
        result = await svc.monitor_behavior(
            MODULE_WALLET, BEHAVIOR_PAYMENT,
            risk_level=RISK_LEVEL_HIGH
        )
        record("test_04_high_risk_disposal",
               result["disposal"] == DISPOSAL_BLOCK,
               f"expected {DISPOSAL_BLOCK}, got {result['disposal']}")

        # test 5: 中风险处置方式(medium→limit)
        result = await svc.monitor_behavior(
            MODULE_RECYCLE, BEHAVIOR_ORDER,
            risk_level=RISK_LEVEL_MEDIUM
        )
        record("test_05_medium_risk_disposal",
               result["disposal"] == DISPOSAL_LIMIT,
               f"expected {DISPOSAL_LIMIT}, got {result['disposal']}")

        # test 6: 模块名为空
        try:
            await svc.monitor_behavior("", BEHAVIOR_ORDER)
            record("test_06_empty_module", False, "应抛出ValueError")
        except ValueError:
            record("test_06_empty_module", True)

        # test 7: 非法风险等级
        try:
            await svc.monitor_behavior(MODULE_ORDER, BEHAVIOR_ORDER, risk_level="invalid")
            record("test_07_invalid_risk_level", False, "应抛出ValueError")
        except ValueError:
            record("test_07_invalid_risk_level", True)

        # test 8: 查询行为监控详情
        record_id = result["id"]
        record_data = await svc.get_behavior_monitor(record_id)
        record("test_08_get_behavior_detail",
               record_data["id"] == record_id,
               f"expected id={record_id}")

        # test 9: 查询不存在的监控记录
        try:
            await svc.get_behavior_monitor(99999)
            record("test_09_get_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_09_get_nonexistent", True)

        # test 10: 列表查询(按模块筛选)
        monitors = await svc.list_behavior_monitors(module_name=MODULE_WALLET)
        record("test_10_list_by_module",
               all(m["moduleName"] == MODULE_WALLET for m in monitors) and len(monitors) >= 1,
               f"expected all {MODULE_WALLET}, got {len(monitors)}")

        # test 11: 列表查询(按风险等级筛选)
        monitors = await svc.list_behavior_monitors(risk_level=RISK_LEVEL_HIGH)
        record("test_11_list_by_risk_level",
               all(m["riskLevel"] == RISK_LEVEL_HIGH for m in monitors),
               "expected all high risk")


class TestTermsMonitor:
    """条款监控测试"""

    async def run(self, svc):
        # test 12: 正常条款监控
        result = await svc.monitor_terms(
            TERMS_USER, "用户注册协议",
            terms_content="用户在注册时须同意本协议...",
            risk_level=RISK_LEVEL_LOW
        )
        record("test_12_monitor_terms_success",
               result["termsType"] == TERMS_USER and result["id"] > 0,
               f"expected {TERMS_USER}/{'>0'}")

        # test 13: 区块链存证生成
        record("test_13_evidence_generated",
               result.get("evidenceHash") is not None,
               "expected evidence hash")

        # test 14: 风险条款识别(高风险)
        result = await svc.monitor_terms(
            TERMS_WALLET, "钱包理财条款",
            risk_terms_identify={"found": True, "riskTerms": ["承诺保本"]},
            risk_level=RISK_LEVEL_HIGH
        )
        record("test_14_high_risk_terms",
               result["riskLevel"] == RISK_LEVEL_HIGH,
               f"expected {RISK_LEVEL_HIGH}, got {result['riskLevel']}")

        # test 15: 条款名为空
        try:
            await svc.monitor_terms(TERMS_USER, "")
            record("test_15_empty_terms_name", False, "应抛出ValueError")
        except ValueError:
            record("test_15_empty_terms_name", True)

        # test 16: 查询条款监控列表
        monitors = await svc.list_terms_monitors(terms_type=TERMS_USER)
        record("test_16_list_terms_by_type",
               all(m["termsType"] == TERMS_USER for m in monitors) and len(monitors) >= 1,
               f"expected all {TERMS_USER}")


class TestLegalKnowledge:
    """法律知识检索测试"""

    async def run(self, svc):
        # test 17: 新增法律知识
        result = await svc.add_legal_knowledge(
            LAW_CIVIL, LAW_CATEGORY_CIVIL,
            law_articles="第496条/第497条/第506条",
            law_interpretation="格式条款/免责条款/加重责任"
        )
        record("test_17_add_legal_success",
               result["lawName"] == LAW_CIVIL and result["id"] > 0,
               f"expected {LAW_CIVIL}/{'>0'}")

        # test 18: 新增电商法
        await svc.add_legal_knowledge(
            LAW_ECOMMERCE, LAW_CATEGORY_COMMERCE,
            law_articles="第14条/第20条/第35条"
        )

        # test 19: 按关键词检索
        results = await svc.search_legal_knowledge(keyword="格式")
        record("test_19_search_by_keyword",
               len(results) >= 1,
               f"expected >=1, got {len(results)}")

        # test 20: 按类别检索
        results = await svc.search_legal_knowledge(law_category=LAW_CATEGORY_CIVIL)
        record("test_20_search_by_category",
               all(r["lawCategory"] == LAW_CATEGORY_CIVIL for r in results),
               "expected all civil")

        # test 21: 空检索条件
        try:
            await svc.search_legal_knowledge()
            record("test_21_empty_search", False, "应抛出ValueError")
        except ValueError:
            record("test_21_empty_search", True)

        # test 22: 查询法律知识详情
        law_id = result["id"]
        law = await svc.get_legal_knowledge(law_id)
        record("test_22_get_legal_detail",
               law["id"] == law_id,
               f"expected id={law_id}")


class TestRiskWarning:
    """风险预警测试"""

    async def run(self, svc):
        # test 23: 正常风险预警(指定等级)
        result = await svc.raise_risk_warning(
            RISK_TYPE_FRAUD, RISK_SOURCE_WALLET,
            risk_score=85.0,
            risk_level=RISK_LEVEL_HIGH
        )
        record("test_23_risk_warning_success",
               result["riskType"] == RISK_TYPE_FRAUD and result["id"] > 0,
               f"expected fraud/{'>0'}")

        # test 24: 指定风险等级生效
        record("test_24_specified_risk_level",
               result["riskLevel"] == RISK_LEVEL_HIGH,
               f"expected {RISK_LEVEL_HIGH}, got {result['riskLevel']}")

        # test 25: 自动分级(评分70→high, 60≤score<80)
        result = await svc.raise_risk_warning(
            RISK_TYPE_MONEY_LAUNDERING, RISK_SOURCE_WALLET,
            risk_score=70.0
        )
        record("test_25_auto_grade_high",
               result["riskLevel"] == RISK_LEVEL_HIGH,
               f"expected {RISK_LEVEL_HIGH}, got {result['riskLevel']}")

        # test 26: 自动分级(评分50→medium)
        result = await svc.raise_risk_warning(
            RISK_TYPE_FRAUD, RISK_SOURCE_WALLET,
            risk_score=50.0
        )
        record("test_26_auto_grade_medium",
               result["riskLevel"] == RISK_LEVEL_MEDIUM,
               f"expected {RISK_LEVEL_MEDIUM}, got {result['riskLevel']}")

        # test 27: 自动分级(评分10→low)
        result = await svc.raise_risk_warning(
            RISK_TYPE_FRAUD, RISK_SOURCE_WALLET,
            risk_score=10.0
        )
        record("test_27_auto_grade_low",
               result["riskLevel"] == RISK_LEVEL_LOW,
               f"expected {RISK_LEVEL_LOW}, got {result['riskLevel']}")

        # test 28: 自动分级(评分95→extreme)
        result = await svc.raise_risk_warning(
            RISK_TYPE_FRAUD, RISK_SOURCE_WALLET,
            risk_score=95.0
        )
        record("test_28_auto_grade_extreme",
               result["riskLevel"] == RISK_LEVEL_EXTREME,
               f"expected {RISK_LEVEL_EXTREME}, got {result['riskLevel']}")

        # test 29: 极高风险处置方式(report 上报+人工复核, 2026-08-29 P0-14 对齐文档)
        record("test_29_high_risk_disposal",
               result["disposal"] == DISPOSAL_REPORT
               and result.get("needManualReview") is True,
               f"expected {DISPOSAL_REPORT}+manualReview, "
               f"got {result['disposal']}")

        # test 30: 风险类型为空
        try:
            await svc.raise_risk_warning("", RISK_SOURCE_WALLET)
            record("test_30_empty_risk_type", False, "应抛出ValueError")
        except ValueError:
            record("test_30_empty_risk_type", True)

        # test 31: 非法风险等级
        try:
            await svc.raise_risk_warning(RISK_TYPE_FRAUD, RISK_SOURCE_WALLET, risk_level="invalid")
            record("test_31_invalid_risk_level", False, "应抛出ValueError")
        except ValueError:
            record("test_31_invalid_risk_level", True)

        # test 32: 查询风险预警列表
        warnings = await svc.list_risk_warnings(risk_type=RISK_TYPE_FRAUD)
        record("test_32_list_by_risk_type",
               all(w["riskType"] == RISK_TYPE_FRAUD for w in warnings) and len(warnings) >= 1,
               "expected all fraud")


class TestRegulatoryReport:
    """监管报送测试"""

    async def run(self, svc):
        # test 33: 大额交易报送
        result = await svc.submit_regulatory_report(
            REPORT_TYPE_LARGE_AMOUNT, REPORT_TARGET_PBOC,
            report_data={"amount": 60000.0, "memberId": 1001}
        )
        record("test_33_submit_large_amount",
               result["reportType"] == REPORT_TYPE_LARGE_AMOUNT and result["id"] > 0,
               f"expected large_amount/{'>0'}")

        # test 34: 自动提交状态(submitted)
        record("test_34_auto_submitted",
               result["reportStatus"] == REPORT_STATUS_SUBMITTED,
               f"expected {REPORT_STATUS_SUBMITTED}, got {result['reportStatus']}")

        # test 35: 区块链存证生成
        record("test_35_evidence_generated",
               result.get("evidenceHash") is not None,
               "expected evidence hash")

        # test 36: 可疑交易报送
        result = await svc.submit_regulatory_report(
            REPORT_TYPE_SUSPICIOUS, REPORT_TARGET_PBOC,
            report_data={"reason": "可疑洗钱", "memberId": 1002}
        )
        record("test_36_submit_suspicious",
               result["reportType"] == REPORT_TYPE_SUSPICIOUS,
               f"expected {REPORT_TYPE_SUSPICIOUS}")

        # test 37: 报表报送
        result = await svc.submit_regulatory_report(
            REPORT_TYPE_REGULAR, REPORT_TARGET_MARKET,
            report_data={"period": "202608", "type": "月报"}
        )
        record_id = result["id"]

        # test 38: 受理监管报送
        result = await svc.accept_regulatory_report(record_id)
        record("test_38_accept_report",
               result["reportStatus"] == REPORT_STATUS_ACCEPTED,
               f"expected {REPORT_STATUS_ACCEPTED}, got {result['reportStatus']}")

        # test 39: 重复受理(状态非法)
        try:
            await svc.accept_regulatory_report(record_id)
            record("test_39_duplicate_accept", False, "应抛出ValueError")
        except ValueError:
            record("test_39_duplicate_accept", True)

        # test 40: 受理不存在的报送
        try:
            await svc.accept_regulatory_report(99999)
            record("test_40_accept_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_40_accept_nonexistent", True)

        # test 41: 非法报送类型
        try:
            await svc.submit_regulatory_report("invalid", REPORT_TARGET_PBOC)
            record("test_41_invalid_report_type", False, "应抛出ValueError")
        except ValueError:
            record("test_41_invalid_report_type", True)


class TestBlockchainEvidence:
    """区块链存证测试"""

    async def run(self, svc):
        # test 42: 新增合规存证
        result = await svc.add_blockchain_evidence(
            EVIDENCE_TYPE_COMPLIANCE,
            evidence_data="合规存证数据"
        )
        record("test_42_add_evidence_success",
               result["evidenceType"] == EVIDENCE_TYPE_COMPLIANCE and result["id"] > 0,
               f"expected compliance/{'>0'}")

        # test 43: 存证哈希生成
        evidence_hash = result["evidenceHash"]
        record("test_43_evidence_hash_generated",
               evidence_hash is not None and len(evidence_hash) > 0,
               f"expected hash, got {evidence_hash}")

        # test 44: 按哈希验证存证
        verify_result = await svc.verify_evidence_by_hash(evidence_hash)
        record("test_44_verify_by_hash",
               verify_result["verified"] is True and verify_result["evidenceHash"] == evidence_hash,
               "expected verified True")

        # test 45: 验证不存在的哈希
        try:
            await svc.verify_evidence_by_hash("0xNONEXISTENT")
            record("test_45_verify_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_45_verify_nonexistent", True)

        # test 46: 非法存证类型
        try:
            await svc.add_blockchain_evidence("invalid_type")
            record("test_46_invalid_evidence_type", False, "应抛出ValueError")
        except ValueError:
            record("test_46_invalid_evidence_type", True)

        # test 47: 查询存证列表
        evidences = await svc.list_blockchain_evidence(evidence_type=EVIDENCE_TYPE_COMPLIANCE)
        record("test_47_list_by_type",
               all(e["evidenceType"] == EVIDENCE_TYPE_COMPLIANCE for e in evidences) and len(evidences) >= 1,
               "expected all compliance")

        # test 48: 查询存证详情
        evidence_id = result["id"]
        detail = await svc.get_blockchain_evidence(evidence_id)
        record("test_48_get_evidence_detail",
               detail["id"] == evidence_id,
               f"expected id={evidence_id}")


class TestAnalysisReport:
    """分析报告测试"""

    async def run(self, svc):
        # test 49: 生成日报
        result = await svc.create_analysis_report(
            PERIOD_DAILY,
            effect_analysis={"totalMonitors": 100, "anomalies": 5},
            roi_evaluation={"cost": 5000, "benefit": 15000, "roi": 200},
            trend_prediction={"nextDay": "stable"}
        )
        record("test_49_create_daily_report",
               result["analysisPeriod"] == PERIOD_DAILY and result["id"] > 0,
               f"expected daily/{'>0'}")

        # test 50: 生成周报
        result = await svc.create_analysis_report(PERIOD_WEEKLY)
        record("test_50_create_weekly_report",
               result["analysisPeriod"] == PERIOD_WEEKLY,
               f"expected {PERIOD_WEEKLY}")

        # test 51: 非法分析周期
        try:
            await svc.create_analysis_report("yearly")
            record("test_51_invalid_period", False, "应抛出ValueError")
        except ValueError:
            record("test_51_invalid_period", True)

        # test 52: 查询分析报告列表
        reports = await svc.list_analysis_reports(analysis_period=PERIOD_DAILY)
        record("test_52_list_by_period",
               all(r["analysisPeriod"] == PERIOD_DAILY for r in reports) and len(reports) >= 1,
               "expected all daily")

        # test 53: 查询分析报告详情
        report_id = result["id"]
        detail = await svc.get_analysis_report(report_id)
        record("test_53_get_report_detail",
               detail["id"] == report_id,
               f"expected id={report_id}")


class TestOptimization:
    """持续优化测试"""

    async def run(self, svc):
        # test 54: 持续优化
        result = await svc.update_optimization(
            "规则优化",
            rule_optimize={"updatedRules": 10, "newRules": 5},
            knowledge_update={"addedLaws": 2},
            continuous_improve={"improvements": 8}
        )
        record("test_54_update_optimization",
               result["optimizationType"] == "规则优化" and result["id"] > 0,
               f"expected 规则优化/{'>0'}")

        # test 55: 优化类型为空
        try:
            await svc.update_optimization("")
            record("test_55_empty_type", False, "应抛出ValueError")
        except ValueError:
            record("test_55_empty_type", True)

        # test 56: 查询持续优化列表
        optimizations = await svc.list_optimizations(optimization_type="规则优化")
        record("test_56_list_optimizations",
               all(o["optimizationType"] == "规则优化" for o in optimizations) and len(optimizations) >= 1,
               "expected all 规则优化")


class TestStats:
    """统计测试"""

    async def run(self, svc):
        # 准备数据
        await svc.monitor_behavior(MODULE_ORDER, BEHAVIOR_ORDER, risk_level=RISK_LEVEL_LOW)
        await svc.monitor_behavior(MODULE_WALLET, BEHAVIOR_PAYMENT, risk_level=RISK_LEVEL_HIGH)
        await svc.monitor_terms(TERMS_USER, "用户协议", risk_level=RISK_LEVEL_LOW)
        await svc.raise_risk_warning(RISK_TYPE_FRAUD, RISK_SOURCE_WALLET, risk_score=85.0)
        await svc.submit_regulatory_report(REPORT_TYPE_LARGE_AMOUNT, REPORT_TARGET_PBOC)
        await svc.add_blockchain_evidence(EVIDENCE_TYPE_COMPLIANCE)

        # test 57: 统计字段完整性
        stats = await svc.get_stats()
        record("test_57_stats_fields",
               all(k in stats for k in ["totalBehaviorMonitors", "totalTermsMonitors",
                                          "totalRiskWarnings", "totalRegulatoryReports",
                                          "totalBlockchainEvidence", "riskLevelCount",
                                          "reportStatusCount"]),
               f"missing fields: {stats}")

        # test 58: 统计数量正确
        record("test_58_stats_count",
               stats["totalBehaviorMonitors"] >= 2 and stats["totalTermsMonitors"] >= 1,
               f"expected >=2/>=1, got {stats['totalBehaviorMonitors']}/{stats['totalTermsMonitors']}")

        # test 59: 风险等级分布(评分85→extreme)
        record("test_59_risk_level_distribution",
               RISK_LEVEL_EXTREME in stats["riskLevelCount"] and stats["riskLevelCount"][RISK_LEVEL_EXTREME] >= 1,
               f"expected extreme risk in: {stats['riskLevelCount']}")

        # test 60: 报送状态分布
        record("test_60_report_status_distribution",
               REPORT_STATUS_SUBMITTED in stats["reportStatusCount"],
               f"expected submitted in: {stats['reportStatusCount']}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("合规合法智能监控模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestBehaviorMonitor,
        TestTermsMonitor,
        TestLegalKnowledge,
        TestRiskWarning,
        TestRegulatoryReport,
        TestBlockchainEvidence,
        TestAnalysisReport,
        TestOptimization,
        TestStats,
    ]

    for cls in test_classes:
        reset_store()
        svc = ComplianceService()
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
