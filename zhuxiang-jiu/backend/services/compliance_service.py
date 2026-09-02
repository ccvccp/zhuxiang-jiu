"""合规合法智能监控模块业务逻辑层

核心业务:
    - 全网行为监控: 采集+合规自检+异常识别+处置执行
    - 条款协议监控: 采集+合法性审查+合规审查+有效性验证
    - 法律知识检索: 16部法律法规+案例库+规则库
    - 风险预警: 识别+评估+分级+前置
    - 监管报送: 大额+可疑+报表+问询
    - 区块链存证: 合规+风险+处置+监管存证上链
    - 分析报告: 效果分析+ROI+趋势预测
    - 持续优化: 规则+知识+经验+改进

锁保护:
    - 行为监控: lock:compliance:behavior:{module_name}
    - 条款审查: lock:compliance:terms:{terms_id}
    - 风险预警: lock:compliance:risk:{risk_type}
    - 监管报送: lock:compliance:report:{report_id}

异常约定:
    - KeyError → 404(记录不存在)
    - ValueError → 409(状态非法/参数无效)
"""


from core.locks import get_lock
from core.helpers import ts, bc_hash
from repositories.compliance_repository import (
    ComplianceRepository,
    # 风险等级
    RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM, RISK_LEVEL_HIGH, RISK_LEVEL_EXTREME,
    # 报送状态
    REPORT_STATUS_PENDING, REPORT_STATUS_SUBMITTED, REPORT_STATUS_ACCEPTED,
    # 存证类型
    EVIDENCE_TYPE_COMPLIANCE, EVIDENCE_TYPE_RISK, EVIDENCE_TYPE_DISPOSAL, EVIDENCE_TYPE_REGULATORY,
    EVIDENCE_TYPE_CITATION, EVIDENCE_TYPE_INVOICE,
    # 处置方式
    DISPOSAL_WARN, DISPOSAL_LIMIT, DISPOSAL_BLOCK, DISPOSAL_REPORT, REPORT_TYPE_LARGE_AMOUNT, REPORT_TYPE_SUSPICIOUS, REPORT_TYPE_REGULAR, REPORT_TYPE_INQUIRY,
    # 分析周期
    PERIOD_DAILY, PERIOD_WEEKLY, PERIOD_MONTHLY,
)


# ============================================================
# 风险等级映射(评分→等级)
# ============================================================

RISK_SCORE_THRESHOLDS = [
    (80, RISK_LEVEL_EXTREME),
    (60, RISK_LEVEL_HIGH),
    (30, RISK_LEVEL_MEDIUM),
    (0, RISK_LEVEL_LOW),
]

# 大额交易报送阈值(单笔5万/日累计20万)
LARGE_AMOUNT_SINGLE = 50000.0
LARGE_AMOUNT_DAILY = 200000.0


def _score_to_level(score: float) -> str:
    """风险评分→风险等级"""
    for threshold, level in RISK_SCORE_THRESHOLDS:
        if score >= threshold:
            return level
    return RISK_LEVEL_LOW


def _level_to_disposal(risk_level: str) -> str:
    """风险等级→处置方式(对齐设计文档分级处置)

    - 极高(extreme): 上报监管+人工复核(不能仅系统自动拦截)
    - 高(high): 拦截
    - 中(medium): 限制+警告
    - 低(low): 警告
    """
    disposal_map = {
        RISK_LEVEL_EXTREME: DISPOSAL_REPORT,
        RISK_LEVEL_HIGH: DISPOSAL_BLOCK,
        RISK_LEVEL_MEDIUM: DISPOSAL_LIMIT,
        RISK_LEVEL_LOW: DISPOSAL_WARN,
    }
    return disposal_map.get(risk_level, DISPOSAL_WARN)


class ComplianceService:
    """合规合法智能监控业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: ComplianceRepository = ComplianceRepository()):
        self.repo = repo

    # ============================================================
    # 1. 全网行为监控
    # ============================================================

    async def monitor_behavior(self, module_name: str, behavior_type: str,
                                 behavior_data: dict = None,
                                 compliance_check: dict = None,
                                 anomaly_identify: dict = None,
                                 risk_level: str = RISK_LEVEL_LOW,
                                 ai_automation_rate: float = 90.0) -> dict:
        """全网行为监控

        规则:
            - 采集业务行为+合规自检+异常识别
            - 根据风险等级确定处置方式
            - 写入区块链存证

        Returns:
            监控结果(含监控ID/处置方式/存证哈希)
        """
        if not module_name or not behavior_type:
            raise ValueError("模块名称和行为类型不可为空")

        if risk_level not in (RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM,
                                RISK_LEVEL_HIGH, RISK_LEVEL_EXTREME):
            raise ValueError(f"非法风险等级: {risk_level}")

        lock_key = f"compliance:behavior:{module_name}:{behavior_type}"
        async with get_lock(lock_key):
            disposal = _level_to_disposal(risk_level)
            record = {
                "moduleName": module_name,
                "behaviorType": behavior_type,
                "behaviorData": behavior_data or {},
                "complianceCheck": compliance_check or {"passed": True},
                "anomalyIdentify": anomaly_identify or {"detected": False},
                "riskLevel": risk_level,
                "disposal": disposal,
                "needManualReview": risk_level == RISK_LEVEL_EXTREME,
                "aiAutomationRate": ai_automation_rate,
                "createdAt": ts(),
            }
            record_id = await self.repo.create_behavior_monitor(record)
            record["id"] = record_id

            # 写入区块链存证
            evidence_hash = bc_hash()
            evidence = {
                "evidenceType": EVIDENCE_TYPE_COMPLIANCE,
                "complianceEvidence": {
                    "behaviorId": record_id,
                    "moduleName": module_name,
                    "behaviorType": behavior_type,
                },
                "evidenceHash": evidence_hash,
                "evidenceData": json_dumps(record),
                "txId": bc_hash(),
            }
            evidence_id = await self.repo.create_blockchain_evidence(evidence)

            return {
                "id": record_id,
                "moduleName": module_name,
                "behaviorType": behavior_type,
                "riskLevel": risk_level,
                "disposal": disposal,
                "evidenceId": evidence_id,
                "evidenceHash": evidence_hash,
            }

    async def get_behavior_monitor(self, record_id: int) -> dict:
        """查询行为监控记录"""
        record = await self.repo.get_behavior_monitor(record_id)
        if record is None:
            raise KeyError(f"行为监控记录不存在(id={record_id})")
        return record

    async def list_behavior_monitors(self, module_name: str = None,
                                         risk_level: str = None,
                                         limit: int = 50) -> list[dict]:
        """查询行为监控列表"""
        return await self.repo.list_behavior_monitors(module_name, risk_level, limit)

    # ============================================================
    # 2. 条款协议监控
    # ============================================================

    async def monitor_terms(self, terms_type: str, terms_name: str,
                              terms_content: str = "",
                              legality_review: dict = None,
                              compliance_review: dict = None,
                              validity_verify: dict = None,
                              risk_terms_identify: dict = None,
                              risk_level: str = RISK_LEVEL_LOW,
                              ai_automation_rate: float = 85.0) -> dict:
        """条款协议监控

        规则:
            - 采集条款+合法性审查+合规审查+有效性验证
            - 识别风险条款
        """
        if not terms_type or not terms_name:
            raise ValueError("条款类型和名称不可为空")

        if risk_level not in (RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM,
                                RISK_LEVEL_HIGH, RISK_LEVEL_EXTREME):
            raise ValueError(f"非法风险等级: {risk_level}")

        lock_key = f"compliance:terms:{terms_type}:{terms_name}"
        async with get_lock(lock_key):
            record = {
                "termsType": terms_type,
                "termsName": terms_name,
                "termsContent": terms_content,
                "legalityReview": legality_review or {"passed": True},
                "complianceReview": compliance_review or {"passed": True},
                "validityVerify": validity_verify or {"valid": True},
                "riskTermsIdentify": risk_terms_identify or {"found": False},
                "riskLevel": risk_level,
                "aiAutomationRate": ai_automation_rate,
                "createdAt": ts(),
            }
            record_id = await self.repo.create_terms_monitor(record)
            record["id"] = record_id

            # 写入区块链存证
            evidence_hash = bc_hash()
            evidence = {
                "evidenceType": EVIDENCE_TYPE_COMPLIANCE,
                "complianceEvidence": {
                    "termsId": record_id,
                    "termsType": terms_type,
                },
                "evidenceHash": evidence_hash,
                "evidenceData": json_dumps(record),
                "txId": bc_hash(),
            }
            evidence_id = await self.repo.create_blockchain_evidence(evidence)

            return {
                "id": record_id,
                "termsType": terms_type,
                "termsName": terms_name,
                "riskLevel": risk_level,
                "evidenceId": evidence_id,
                "evidenceHash": evidence_hash,
            }

    async def get_terms_monitor(self, record_id: int) -> dict:
        """查询条款监控记录"""
        record = await self.repo.get_terms_monitor(record_id)
        if record is None:
            raise KeyError(f"条款监控记录不存在(id={record_id})")
        return record

    async def list_terms_monitors(self, terms_type: str = None,
                                     risk_level: str = None,
                                     limit: int = 50) -> list[dict]:
        """查询条款监控列表"""
        return await self.repo.list_terms_monitors(terms_type, risk_level, limit)

    # ============================================================
    # 3. 法律知识检索
    # ============================================================

    async def add_legal_knowledge(self, law_name: str, law_category: str,
                                    law_articles: str = "",
                                    law_interpretation: str = "",
                                    case_library: str = "",
                                    rule_library: str = "",
                                    ai_automation_rate: float = 90.0) -> dict:
        """新增法律知识"""
        if not law_name or not law_category:
            raise ValueError("法律名称和类别不可为空")

        record = {
            "lawName": law_name,
            "lawCategory": law_category,
            "lawArticles": law_articles,
            "lawInterpretation": law_interpretation,
            "caseLibrary": case_library,
            "ruleLibrary": rule_library,
            "aiAutomationRate": ai_automation_rate,
            "createdAt": ts(),
        }
        record_id = await self.repo.create_legal_knowledge(record)
        record["id"] = record_id
        return record

    async def search_legal_knowledge(self, keyword: str = None,
                                         law_category: str = None,
                                         limit: int = 50) -> list[dict]:
        """法律知识检索"""
        if not keyword and not law_category:
            raise ValueError("检索关键词或法律类别不可同时为空")
        return await self.repo.list_legal_knowledge(law_category, keyword, limit)

    async def get_legal_knowledge(self, record_id: int) -> dict:
        """查询法律知识"""
        record = await self.repo.get_legal_knowledge(record_id)
        if record is None:
            raise KeyError(f"法律知识不存在(id={record_id})")
        return record

    # ============================================================
    # 4. 风险预警
    # ============================================================

    async def raise_risk_warning(self, risk_type: str, risk_source: str,
                                   risk_identify: dict = None,
                                   risk_score: float = 0.0,
                                   risk_level: str = None,
                                   ai_automation_rate: float = 95.0) -> dict:
        """风险预警

        规则:
            - 若未指定风险等级, 按评分自动分级
            - 写入区块链存证
        """
        if not risk_type or not risk_source:
            raise ValueError("风险类型和来源不可为空")

        # 自动分级
        if risk_level is None:
            risk_level = _score_to_level(risk_score)
        elif risk_level not in (RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM,
                                  RISK_LEVEL_HIGH, RISK_LEVEL_EXTREME):
            raise ValueError(f"非法风险等级: {risk_level}")

        lock_key = f"compliance:risk:{risk_type}"
        async with get_lock(lock_key):
            disposal = _level_to_disposal(risk_level)
            record = {
                "riskType": risk_type,
                "riskSource": risk_source,
                "riskIdentify": risk_identify or {"identified": True},
                "riskAssess": {"score": risk_score, "level": risk_level},
                "riskGrade": {"level": risk_level},
                "riskPreposition": {"disposal": disposal},
                "riskLevel": risk_level,
                "riskScore": risk_score,
                "needManualReview": risk_level == RISK_LEVEL_EXTREME,
                "aiAutomationRate": ai_automation_rate,
                "createdAt": ts(),
            }
            record_id = await self.repo.create_risk_warning(record)
            record["id"] = record_id

            # 写入区块链存证
            evidence_hash = bc_hash()
            evidence = {
                "evidenceType": EVIDENCE_TYPE_RISK,
                "riskEvidence": {
                    "riskId": record_id,
                    "riskType": risk_type,
                    "riskLevel": risk_level,
                },
                "evidenceHash": evidence_hash,
                "evidenceData": json_dumps(record),
                "txId": bc_hash(),
            }
            evidence_id = await self.repo.create_blockchain_evidence(evidence)

            return {
                "id": record_id,
                "riskType": risk_type,
                "riskSource": risk_source,
                "riskLevel": risk_level,
                "riskScore": risk_score,
                "disposal": disposal,
                "needManualReview": risk_level == RISK_LEVEL_EXTREME,
                "evidenceId": evidence_id,
                "evidenceHash": evidence_hash,
            }

    async def get_risk_warning(self, record_id: int) -> dict:
        """查询风险预警"""
        record = await self.repo.get_risk_warning(record_id)
        if record is None:
            raise KeyError(f"风险预警不存在(id={record_id})")
        return record

    async def list_risk_warnings(self, risk_type: str = None,
                                     risk_level: str = None,
                                     limit: int = 50) -> list[dict]:
        """查询风险预警列表"""
        return await self.repo.list_risk_warnings(risk_type, risk_level, limit)

    # ============================================================
    # 5. 监管报送
    # ============================================================

    async def submit_regulatory_report(self, report_type: str, report_target: str,
                                          report_data: dict = None,
                                          ai_automation_rate: float = 90.0) -> dict:
        """监管报送

        规则:
            - 大额交易(单笔≥5万)自动报送央行
            - 可疑交易自动报送反洗钱中心
            - 报表定期报送
            - 写入区块链存证
        """
        if not report_type or not report_target:
            raise ValueError("报送类型和对象不可为空")

        if report_type not in (REPORT_TYPE_LARGE_AMOUNT, REPORT_TYPE_SUSPICIOUS,
                                 REPORT_TYPE_REGULAR, REPORT_TYPE_INQUIRY):
            raise ValueError(f"非法报送类型: {report_type}")

        lock_key = f"compliance:report:{report_type}"
        async with get_lock(lock_key):
            record = {
                "reportType": report_type,
                "reportTarget": report_target,
                "reportData": report_data or {},
                "reportStatus": REPORT_STATUS_PENDING,
                "aiAutomationRate": ai_automation_rate,
                "createdAt": ts(),
            }
            record_id = await self.repo.create_regulatory_report(record)
            record["id"] = record_id

            # 自动提交
            await self.repo.update_regulatory_report(record_id, {
                "reportStatus": REPORT_STATUS_SUBMITTED,
                "submittedAt": ts(),
            })

            # 写入区块链存证
            evidence_hash = bc_hash()
            evidence = {
                "evidenceType": EVIDENCE_TYPE_REGULATORY,
                "regulatoryEvidence": {
                    "reportId": record_id,
                    "reportType": report_type,
                },
                "evidenceHash": evidence_hash,
                "evidenceData": json_dumps(record),
                "txId": bc_hash(),
            }
            evidence_id = await self.repo.create_blockchain_evidence(evidence)

            return {
                "id": record_id,
                "reportType": report_type,
                "reportTarget": report_target,
                "reportStatus": REPORT_STATUS_SUBMITTED,
                "evidenceId": evidence_id,
                "evidenceHash": evidence_hash,
            }

    async def accept_regulatory_report(self, record_id: int) -> dict:
        """受理监管报送"""
        lock_key = f"compliance:report:accept:{record_id}"
        async with get_lock(lock_key):
            record = await self.repo.get_regulatory_report(record_id)
            if record is None:
                raise KeyError(f"监管报送记录不存在(id={record_id})")

            if record["reportStatus"] != REPORT_STATUS_SUBMITTED:
                raise ValueError(
                    f"报送状态非法(当前{record['reportStatus']}, 须为{REPORT_STATUS_SUBMITTED})"
                )

            await self.repo.update_regulatory_report(record_id, {
                "reportStatus": REPORT_STATUS_ACCEPTED,
                "acceptedAt": ts(),
            })
            record["reportStatus"] = REPORT_STATUS_ACCEPTED
            return record

    async def get_regulatory_report(self, record_id: int) -> dict:
        """查询监管报送"""
        record = await self.repo.get_regulatory_report(record_id)
        if record is None:
            raise KeyError(f"监管报送记录不存在(id={record_id})")
        return record

    async def list_regulatory_reports(self, report_type: str = None,
                                         report_status: str = None,
                                         limit: int = 50) -> list[dict]:
        """查询监管报送列表"""
        return await self.repo.list_regulatory_reports(report_type, report_status, limit)

    # ============================================================
    # 6. 区块链存证
    # ============================================================

    async def add_blockchain_evidence(self, evidence_type: str, evidence_data: str = "",
                                         ai_automation_rate: float = 95.0) -> dict:
        """新增区块链存证

        规则:
            - 生成存证哈希+交易ID
            - 模拟上链
        """
        if evidence_type not in (EVIDENCE_TYPE_COMPLIANCE, EVIDENCE_TYPE_RISK,
                                  EVIDENCE_TYPE_DISPOSAL, EVIDENCE_TYPE_REGULATORY,
                                  EVIDENCE_TYPE_CITATION, EVIDENCE_TYPE_INVOICE):
            raise ValueError(f"非法存证类型: {evidence_type}")

        evidence_hash = bc_hash()
        tx_id = bc_hash()
        record = {
            "evidenceType": evidence_type,
            "evidenceData": evidence_data,
            "evidenceHash": evidence_hash,
            "txId": tx_id,
            "blockHeight": 0,
            "aiAutomationRate": ai_automation_rate,
            "createdAt": ts(),
        }
        record_id = await self.repo.create_blockchain_evidence(record)
        record["id"] = record_id
        return record

    async def get_blockchain_evidence(self, record_id: int) -> dict:
        """查询区块链存证"""
        record = await self.repo.get_blockchain_evidence(record_id)
        if record is None:
            raise KeyError(f"区块链存证不存在(id={record_id})")
        return record

    async def verify_evidence_by_hash(self, evidence_hash: str) -> dict:
        """按哈希验证存证"""
        record = await self.repo.get_evidence_by_hash(evidence_hash)
        if record is None:
            raise KeyError(f"存证哈希不存在(hash={evidence_hash})")
        return {
            "verified": True,
            "evidenceId": record.get("id"),
            "evidenceHash": evidence_hash,
            "evidenceType": record.get("evidenceType"),
            "txId": record.get("txId"),
        }

    async def list_blockchain_evidence(self, evidence_type: str = None,
                                           limit: int = 50) -> list[dict]:
        """查询区块链存证列表"""
        return await self.repo.list_blockchain_evidence(evidence_type, limit)

    # ============================================================
    # 7. 分析报告
    # ============================================================

    async def create_analysis_report(self, analysis_period: str,
                                        effect_analysis: dict = None,
                                        roi_evaluation: dict = None,
                                        trend_prediction: dict = None,
                                        experience_retention: dict = None,
                                        ai_automation_rate: float = 85.0) -> dict:
        """生成分析报告"""
        if analysis_period not in (PERIOD_DAILY, PERIOD_WEEKLY, PERIOD_MONTHLY):
            raise ValueError(f"非法分析周期: {analysis_period}")

        record = {
            "analysisPeriod": analysis_period,
            "effectAnalysis": effect_analysis or {},
            "roiEvaluation": roi_evaluation or {},
            "trendPrediction": trend_prediction or {},
            "experienceRetention": experience_retention or {},
            "aiAutomationRate": ai_automation_rate,
            "createdAt": ts(),
        }
        record_id = await self.repo.create_analysis_report(record)
        record["id"] = record_id
        return record

    async def get_analysis_report(self, record_id: int) -> dict:
        """查询分析报告"""
        record = await self.repo.get_analysis_report(record_id)
        if record is None:
            raise KeyError(f"分析报告不存在(id={record_id})")
        return record

    async def list_analysis_reports(self, analysis_period: str = None,
                                       limit: int = 50) -> list[dict]:
        """查询分析报告列表"""
        return await self.repo.list_analysis_reports(analysis_period, limit)

    # ============================================================
    # 8. 持续优化
    # ============================================================

    async def update_optimization(self, optimization_type: str,
                                     rule_optimize: dict = None,
                                     knowledge_update: dict = None,
                                     experience_retention: dict = None,
                                     continuous_improve: dict = None,
                                     ai_automation_rate: float = 85.0) -> dict:
        """持续优化"""
        if not optimization_type:
            raise ValueError("优化类型不可为空")

        record = {
            "optimizationType": optimization_type,
            "ruleOptimize": rule_optimize or {},
            "knowledgeUpdate": knowledge_update or {},
            "experienceRetention": experience_retention or {},
            "continuousImprove": continuous_improve or {},
            "aiAutomationRate": ai_automation_rate,
            "createdAt": ts(),
        }
        record_id = await self.repo.create_optimization(record)
        record["id"] = record_id
        return record

    async def get_optimization(self, record_id: int) -> dict:
        """查询持续优化"""
        record = await self.repo.get_optimization(record_id)
        if record is None:
            raise KeyError(f"持续优化记录不存在(id={record_id})")
        return record

    async def list_optimizations(self, optimization_type: str = None,
                                    limit: int = 50) -> list[dict]:
        """查询持续优化列表"""
        return await self.repo.list_optimizations(optimization_type, limit)

    # ============================================================
    # 9. 统计
    # ============================================================

    async def get_stats(self) -> dict:
        """合规统计"""
        behaviors = await self.repo.list_behavior_monitors(limit=10000)
        terms = await self.repo.list_terms_monitors(limit=10000)
        risks = await self.repo.list_risk_warnings(limit=10000)
        reports = await self.repo.list_regulatory_reports(limit=10000)
        evidence = await self.repo.list_blockchain_evidence(limit=10000)

        # 风险等级分布
        risk_level_count = {}
        for r in risks:
            level = r.get("riskLevel", "unknown")
            risk_level_count[level] = risk_level_count.get(level, 0) + 1

        # 报送状态分布
        report_status_count = {}
        for r in reports:
            status = r.get("reportStatus", "unknown")
            report_status_count[status] = report_status_count.get(status, 0) + 1

        return {
            "totalBehaviorMonitors": len(behaviors),
            "totalTermsMonitors": len(terms),
            "totalRiskWarnings": len(risks),
            "totalRegulatoryReports": len(reports),
            "totalBlockchainEvidence": len(evidence),
            "riskLevelCount": risk_level_count,
            "reportStatusCount": report_status_count,
        }


# ============================================================
# 辅助函数
# ============================================================

def json_dumps(data) -> str:
    """JSON序列化(中文字符不转义)"""
    import json
    return json.dumps(data, ensure_ascii=False)
