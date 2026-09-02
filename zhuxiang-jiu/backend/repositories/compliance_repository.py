"""合规合法智能监控模块数据访问层(双模式: 内存 + Redis)

表清单:
    behavior_monitors:   全网行为合规监控表
    terms_monitors:      条款协议合规监控表
    legal_knowledge:     法律知识库表
    risk_warnings:       风险预警表
    regulatory_reports:  监管报送表
    blockchain_evidence: 区块链存证表
    analysis_reports:    智能分析预测表
    optimizations:      持续优化表

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 风险等级: low/medium/high/extreme
    - 报送状态: pending/submitted/accepted
    - 存证类型: compliance/risk/disposal/regulatory
"""

import json
from datetime import datetime

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 风险等级
# ============================================================

RISK_LEVEL_LOW = "low"            # 低风险
RISK_LEVEL_MEDIUM = "medium"      # 中风险
RISK_LEVEL_HIGH = "high"          # 高风险
RISK_LEVEL_EXTREME = "extreme"    # 极高风险

# 报送状态
REPORT_STATUS_PENDING = "pending"      # 待报送
REPORT_STATUS_SUBMITTED = "submitted"  # 已报送
REPORT_STATUS_ACCEPTED = "accepted"    # 已受理

# 存证类型
EVIDENCE_TYPE_COMPLIANCE = "compliance"  # 合规存证
EVIDENCE_TYPE_RISK = "risk"              # 风险存证
EVIDENCE_TYPE_DISPOSAL = "disposal"      # 处置存证
EVIDENCE_TYPE_REGULATORY = "regulatory"  # 监管存证
EVIDENCE_TYPE_CITATION = "citation"      # 出处声明存证(40号博主跟随版权溯源)

# 监控处置方式
DISPOSAL_WARN = "warn"        # 警告
DISPOSAL_LIMIT = "limit"      # 限制
DISPOSAL_BLOCK = "block"      # 拦截
DISPOSAL_REPORT = "report"    # 上报

# 报送类型
REPORT_TYPE_LARGE_AMOUNT = "large_amount"      # 大额交易报送
REPORT_TYPE_SUSPICIOUS = "suspicious"          # 可疑交易报送
REPORT_TYPE_REGULAR = "regular"                # 报表报送
REPORT_TYPE_INQUIRY = "inquiry"               # 问询应对

# 分析周期
PERIOD_DAILY = "daily"
PERIOD_WEEKLY = "weekly"
PERIOD_MONTHLY = "monthly"


class ComplianceRepository:
    """合规合法智能监控数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_behavior_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("compliance_behavior")
        return self._mem_next_id("_compliance_behavior_seq")

    async def next_terms_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("compliance_terms")
        return self._mem_next_id("_compliance_terms_seq")

    async def next_legal_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("compliance_legal")
        return self._mem_next_id("_compliance_legal_seq")

    async def next_risk_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("compliance_risk")
        return self._mem_next_id("_compliance_risk_seq")

    async def next_report_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("compliance_report")
        return self._mem_next_id("_compliance_report_seq")

    async def next_evidence_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("compliance_evidence")
        return self._mem_next_id("_compliance_evidence_seq")

    async def next_analysis_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("compliance_analysis")
        return self._mem_next_id("_compliance_analysis_seq")

    async def next_optimization_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("compliance_optimization")
        return self._mem_next_id("_compliance_optimization_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("compliance", entity, "seq"))

    # ============================================================
    # 行为监控表 CRUD
    # ============================================================

    async def create_behavior_monitor(self, record: dict) -> int:
        record_id = await self.next_behavior_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "riskLevel" not in record:
            record["riskLevel"] = RISK_LEVEL_LOW
        if is_redis_mode():
            await self._redis_create("compliance", "behavior", record_id, record)
        else:
            self._mem_create("compliance_behavior_monitors", record_id, record)
        return record_id

    async def get_behavior_monitor(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("compliance", "behavior", record_id)
        return self._mem_get("compliance_behavior_monitors", record_id)

    async def list_behavior_monitors(self, module_name: str = None,
                                       risk_level: str = None,
                                       limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list("compliance", "behavior", module_name,
                                          "moduleName", risk_level, "riskLevel", limit)
        return self._mem_list("compliance_behavior_monitors", module_name,
                              "moduleName", risk_level, "riskLevel", limit)

    async def update_behavior_monitor(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("compliance", "behavior", record_id, updates)
        else:
            self._mem_update("compliance_behavior_monitors", record_id, updates)

    # ============================================================
    # 条款监控表 CRUD
    # ============================================================

    async def create_terms_monitor(self, record: dict) -> int:
        record_id = await self.next_terms_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "riskLevel" not in record:
            record["riskLevel"] = RISK_LEVEL_LOW
        if is_redis_mode():
            await self._redis_create("compliance", "terms", record_id, record)
        else:
            self._mem_create("compliance_terms_monitors", record_id, record)
        return record_id

    async def get_terms_monitor(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("compliance", "terms", record_id)
        return self._mem_get("compliance_terms_monitors", record_id)

    async def list_terms_monitors(self, terms_type: str = None,
                                    risk_level: str = None,
                                    limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list("compliance", "terms", terms_type,
                                          "termsType", risk_level, "riskLevel", limit)
        return self._mem_list("compliance_terms_monitors", terms_type,
                              "termsType", risk_level, "riskLevel", limit)

    async def update_terms_monitor(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("compliance", "terms", record_id, updates)
        else:
            self._mem_update("compliance_terms_monitors", record_id, updates)

    # ============================================================
    # 法律知识库表 CRUD
    # ============================================================

    async def create_legal_knowledge(self, record: dict) -> int:
        record_id = await self.next_legal_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if is_redis_mode():
            await self._redis_create("compliance", "legal", record_id, record)
        else:
            self._mem_create("compliance_legal_knowledge", record_id, record)
        return record_id

    async def get_legal_knowledge(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("compliance", "legal", record_id)
        return self._mem_get("compliance_legal_knowledge", record_id)

    async def list_legal_knowledge(self, law_category: str = None,
                                     keyword: str = None,
                                     limit: int = 50) -> list[dict]:
        if is_redis_mode():
            records = await self._redis_list_all("compliance", "legal", limit)
        else:
            records = self._mem_list_all("compliance_legal_knowledge", limit)
        if law_category:
            records = [r for r in records if r.get("lawCategory") == law_category]
        if keyword:
            kw = keyword.lower()
            records = [r for r in records
                       if kw in (r.get("lawName", "").lower())
                       or kw in (r.get("lawArticles", "").lower() if isinstance(r.get("lawArticles"), str) else "")
                       or kw in (r.get("lawInterpretation", "").lower() if isinstance(r.get("lawInterpretation"), str) else "")]
        return records[:limit]

    async def update_legal_knowledge(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("compliance", "legal", record_id, updates)
        else:
            self._mem_update("compliance_legal_knowledge", record_id, updates)

    # ============================================================
    # 风险预警表 CRUD
    # ============================================================

    async def create_risk_warning(self, record: dict) -> int:
        record_id = await self.next_risk_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "riskLevel" not in record:
            record["riskLevel"] = RISK_LEVEL_LOW
        if "riskScore" not in record:
            record["riskScore"] = 0.0
        if is_redis_mode():
            await self._redis_create("compliance", "risk", record_id, record)
        else:
            self._mem_create("compliance_risk_warnings", record_id, record)
        return record_id

    async def get_risk_warning(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("compliance", "risk", record_id)
        return self._mem_get("compliance_risk_warnings", record_id)

    async def list_risk_warnings(self, risk_type: str = None,
                                   risk_level: str = None,
                                   limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list("compliance", "risk", risk_type,
                                          "riskType", risk_level, "riskLevel", limit)
        return self._mem_list("compliance_risk_warnings", risk_type,
                              "riskType", risk_level, "riskLevel", limit)

    async def update_risk_warning(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("compliance", "risk", record_id, updates)
        else:
            self._mem_update("compliance_risk_warnings", record_id, updates)

    # ============================================================
    # 监管报送表 CRUD
    # ============================================================

    async def create_regulatory_report(self, record: dict) -> int:
        record_id = await self.next_report_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "reportStatus" not in record:
            record["reportStatus"] = REPORT_STATUS_PENDING
        if is_redis_mode():
            await self._redis_create("compliance", "report", record_id, record)
        else:
            self._mem_create("compliance_regulatory_reports", record_id, record)
        return record_id

    async def get_regulatory_report(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("compliance", "report", record_id)
        return self._mem_get("compliance_regulatory_reports", record_id)

    async def list_regulatory_reports(self, report_type: str = None,
                                        report_status: str = None,
                                        limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list("compliance", "report", report_type,
                                          "reportType", report_status, "reportStatus", limit)
        return self._mem_list("compliance_regulatory_reports", report_type,
                              "reportType", report_status, "reportStatus", limit)

    async def update_regulatory_report(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("compliance", "report", record_id, updates)
        else:
            self._mem_update("compliance_regulatory_reports", record_id, updates)

    # ============================================================
    # 区块链存证表 CRUD
    # ============================================================

    async def create_blockchain_evidence(self, record: dict) -> int:
        record_id = await self.next_evidence_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if "blockHeight" not in record:
            record["blockHeight"] = 0
        if is_redis_mode():
            await self._redis_create("compliance", "evidence", record_id, record)
        else:
            self._mem_create("compliance_blockchain_evidence", record_id, record)
        return record_id

    async def get_blockchain_evidence(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("compliance", "evidence", record_id)
        return self._mem_get("compliance_blockchain_evidence", record_id)

    async def list_blockchain_evidence(self, evidence_type: str = None,
                                          limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_single("compliance", "evidence",
                                                  evidence_type, "evidenceType", limit)
        return self._mem_list_single("compliance_blockchain_evidence",
                                      evidence_type, "evidenceType", limit)

    async def get_evidence_by_hash(self, evidence_hash: str) -> dict | None:
        """按哈希查询存证"""
        if is_redis_mode():
            client = await get_redis_client()
            record_id = await client.get(_k("compliance", "evidence_hash", evidence_hash))
            if not record_id:
                return None
            return await self._redis_get("compliance", "evidence", int(record_id))
        self._ensure_store()
        for record in self.store.get("compliance_blockchain_evidence", {}).values():
            if record.get("evidenceHash") == evidence_hash:
                return record
        return None

    async def update_blockchain_evidence(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("compliance", "evidence", record_id, updates)
        else:
            self._mem_update("compliance_blockchain_evidence", record_id, updates)

    # ============================================================
    # 分析预测表 CRUD
    # ============================================================

    async def create_analysis_report(self, record: dict) -> int:
        record_id = await self.next_analysis_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if is_redis_mode():
            await self._redis_create("compliance", "analysis", record_id, record)
        else:
            self._mem_create("compliance_analysis_reports", record_id, record)
        return record_id

    async def get_analysis_report(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("compliance", "analysis", record_id)
        return self._mem_get("compliance_analysis_reports", record_id)

    async def list_analysis_reports(self, analysis_period: str = None,
                                       limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_single("compliance", "analysis",
                                                  analysis_period, "analysisPeriod", limit)
        return self._mem_list_single("compliance_analysis_reports",
                                      analysis_period, "analysisPeriod", limit)

    async def update_analysis_report(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("compliance", "analysis", record_id, updates)
        else:
            self._mem_update("compliance_analysis_reports", record_id, updates)

    # ============================================================
    # 持续优化表 CRUD
    # ============================================================

    async def create_optimization(self, record: dict) -> int:
        record_id = await self.next_optimization_id()
        record["id"] = record_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in record:
            record["createdAt"] = now
        if is_redis_mode():
            await self._redis_create("compliance", "optimization", record_id, record)
        else:
            self._mem_create("compliance_optimizations", record_id, record)
        return record_id

    async def get_optimization(self, record_id: int) -> dict | None:
        if is_redis_mode():
            return await self._redis_get("compliance", "optimization", record_id)
        return self._mem_get("compliance_optimizations", record_id)

    async def list_optimizations(self, optimization_type: str = None,
                                   limit: int = 50) -> list[dict]:
        if is_redis_mode():
            return await self._redis_list_single("compliance", "optimization",
                                                  optimization_type, "optimizationType", limit)
        return self._mem_list_single("compliance_optimizations",
                                      optimization_type, "optimizationType", limit)

    async def update_optimization(self, record_id: int, updates: dict) -> None:
        if is_redis_mode():
            await self._redis_update("compliance", "optimization", record_id, updates)
        else:
            self._mem_update("compliance_optimizations", record_id, updates)

    # ============================================================
    # 内存模式通用实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含合规模块的键(懒初始化)"""
        if "compliance_behavior_monitors" not in self.store:
            self.store["compliance_behavior_monitors"] = {}
            self.store["compliance_terms_monitors"] = {}
            self.store["compliance_legal_knowledge"] = {}
            self.store["compliance_risk_warnings"] = {}
            self.store["compliance_regulatory_reports"] = {}
            self.store["compliance_blockchain_evidence"] = {}
            self.store["compliance_analysis_reports"] = {}
            self.store["compliance_optimizations"] = {}
            self.store["_compliance_behavior_seq"] = 0
            self.store["_compliance_terms_seq"] = 0
            self.store["_compliance_legal_seq"] = 0
            self.store["_compliance_risk_seq"] = 0
            self.store["_compliance_report_seq"] = 0
            self.store["_compliance_evidence_seq"] = 0
            self.store["_compliance_analysis_seq"] = 0
            self.store["_compliance_optimization_seq"] = 0

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

    def _mem_list_single(self, table: str, filter_value: str, filter_key: str,
                           limit: int) -> list[dict]:
        self._ensure_store()
        records = list(self.store[table].values())
        if filter_value:
            records = [r for r in records if r.get(filter_key) == filter_value]
        records.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return records[:limit]

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
        evidence_hash = record.get("evidenceHash")
        if evidence_hash:
            await client.set(_k(module, "evidence_hash", evidence_hash), record_id)

    async def _redis_get(self, module: str, entity: str,
                           record_id: int) -> dict | None:
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

    async def _redis_list(self, module: str, entity: str,
                            filter_value: str, filter_key: str,
                            filter_value2: str, filter_key2: str,
                            limit: int) -> list[dict]:
        records = await self._redis_list_all(module, entity, limit * 5)
        if filter_value:
            records = [r for r in records if r.get(filter_key) == filter_value]
        if filter_value2:
            records = [r for r in records if r.get(filter_key2) == filter_value2]
        return records[:limit]

    async def _redis_list_single(self, module: str, entity: str,
                                   filter_value: str, filter_key: str,
                                   limit: int) -> list[dict]:
        records = await self._redis_list_all(module, entity, limit * 5)
        if filter_value:
            records = [r for r in records if r.get(filter_key) == filter_value]
        return records[:limit]

    async def _redis_list_all(self, module: str, entity: str,
                                limit: int) -> list[dict]:
        client = await get_redis_client()
        records = []
        keys = await client.keys(_k(module, entity, "*"))
        for key in keys:
            if "seq" in key or "evidence_hash" in key:
                continue
            data = await client.get(key)
            if data:
                records.append(json.loads(data))
        records.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return records[:limit]
