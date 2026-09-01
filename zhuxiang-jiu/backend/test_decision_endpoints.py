"""
AI决策筹划模块(模块29) 11 个 decision 端点单元测试
覆盖 6 层架构(感知/知识/决策/编排/执行/反馈)的成功路径、权限校验、参数校验、边界值

被测端点(不含已有专测的 /api/decision/risk-control):
  感知层: POST /api/decision/data-ingest
  知识层: GET  /api/decision/knowledge/query
         POST /api/decision/knowledge/ingest
  决策层: POST /api/decision/strategy-plan
         POST /api/decision/forecast-simulate
         POST /api/decision/governance
  编排层: POST /api/decision/orchestrate
         POST /api/decision/capability-route
  执行层: POST /api/decision/role-copilot
  反馈层: POST /api/decision/feedback-loop
         POST /api/decision/retrospective

运行: pytest test_decision_endpoints.py -v
覆盖率: pytest test_decision_endpoints.py test_risk_control.py test_business_routes.py \
        --cov=main --cov=models --cov-report=term-missing
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from main import app
from models import DecisionErrorCode

client = TestClient(app)

# 角色请求头(对照 ROLE_LEVELS: guest=0/member=1/agent=2/store_owner=3/admin=4)
ADMIN_HEADERS = {"X-Role": "admin"}
STORE_OWNER_HEADERS = {"X-Role": "store_owner"}
AGENT_HEADERS = {"X-Role": "agent"}
MEMBER_HEADERS = {"X-Role": "member"}
GUEST_HEADERS = {"X-Role": "guest"}


# ============================================================
#  公共断言工具
# ============================================================

def assert_success(response, operation: str):
    """断言 200 + success 响应结构"""
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["operation"] == operation
    assert isinstance(data["details"], dict) and len(data["details"]) > 0
    assert isinstance(data["logs"], list)
    assert isinstance(data["asyncOps"], list)
    return data


def assert_forbidden(response):
    """断言 403 + DECISION_003"""
    assert response.status_code == 403
    data = response.json()
    assert data["success"] is False
    assert "DECISION_003" in data["error"]
    assert data["errorCode"] == "DECISION_003"


def assert_unprocessable(response):
    """断言 422 Pydantic 校验失败"""
    assert response.status_code == 422


# ============================================================
#  感知层: POST /api/decision/data-ingest
# ============================================================

class TestDataIngest:
    """感知层数据采集(admin only)"""

    URL = "/api/decision/data-ingest"

    def _payload(self, data_type="metrics", items=None, source="module:04",
                 realtime=False):
        return {
            "source": source,
            "dataType": data_type,
            "payload": items if items is not None else [
                {"key": "sales", "value": 1000.0,
                 "timestamp": "2026-08-20T10:00:00Z"},
            ],
            "realtime": realtime,
        }

    def test_admin_can_ingest_metrics(self):
        """admin: 注入 metrics 成功"""
        response = client.post(self.URL, json=self._payload(),
                              headers=ADMIN_HEADERS)
        data = assert_success(response, "data-ingest")
        details = data["details"]
        assert details["ingestedCount"] == 1
        assert details["source"] == "module:04"
        assert details["bufferState"] == "healthy"
        assert "processedAt" in details
        # 日志包含采集信息
        assert len(data["logs"]) >= 1
        log = data["logs"][0]
        assert log["stage"] == "感知层-数据采集"
        assert "1" in log["message"] and "metrics" in log["message"]
        assert log["data"]["source"] == "module:04"
        assert log["data"]["realtime"] is False

    @pytest.mark.parametrize("data_type", ["metrics", "events", "logs", "alerts"])
    def test_all_data_types(self, data_type):
        """4 种合法 dataType 全部通过"""
        response = client.post(self.URL,
                               json=self._payload(data_type=data_type),
                               headers=ADMIN_HEADERS)
        data = assert_success(response, "data-ingest")
        assert data["details"]["ingestedCount"] == 1

    def test_realtime_flag_propagated(self):
        """realtime=True 透传到日志"""
        response = client.post(self.URL,
                               json=self._payload(realtime=True),
                               headers=ADMIN_HEADERS)
        data = response.json()
        assert data["logs"][0]["data"]["realtime"] is True

    def test_batch_payload_count(self):
        """批量注入: 3 条 payload → ingestedCount=3"""
        items = [
            {"key": f"k{i}", "value": float(i), "timestamp": "2026-08-20T10:00:00Z"}
            for i in range(3)
        ]
        response = client.post(self.URL, json=self._payload(items=items),
                                headers=ADMIN_HEADERS)
        assert response.json()["details"]["ingestedCount"] == 3

    def test_member_forbidden(self):
        """member: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=MEMBER_HEADERS)
        assert_forbidden(response)

    def test_guest_forbidden(self):
        """guest: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=GUEST_HEADERS)
        assert_forbidden(response)

    def test_no_role_header_defaults_guest_forbidden(self):
        """无 X-Role: 默认 guest → 403"""
        response = client.post(self.URL, json=self._payload())
        assert_forbidden(response)

    def test_invalid_data_type_rejected(self):
        """非法 dataType: 422"""
        response = client.post(self.URL,
                               json=self._payload(data_type="invalid"),
                               headers=ADMIN_HEADERS)
        assert_unprocessable(response)

    def test_missing_payload_rejected(self):
        """缺少 payload: 422"""
        response = client.post(self.URL,
                               json={"source": "module:04", "dataType": "metrics"},
                               headers=ADMIN_HEADERS)
        assert_unprocessable(response)

    def test_missing_source_rejected(self):
        """缺少 source: 422"""
        response = client.post(self.URL,
                               json={"dataType": "metrics", "payload": []},
                               headers=ADMIN_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  知识层: GET /api/decision/knowledge/query
# ============================================================

class TestKnowledgeQuery:
    """知识层 RAG 查询(全角色可读)"""

    URL = "/api/decision/knowledge/query"

    def test_guest_can_query(self):
        """guest: 只读允许"""
        response = client.get(self.URL, params={"q": "L3 留存消费"},
                             headers=GUEST_HEADERS)
        data = assert_success(response, "knowledge-query")
        details = data["details"]
        assert details["query"] == "L3 留存消费"
        assert details["recallRate"] == "93%"
        assert isinstance(details["results"], list) and len(details["results"]) >= 1
        result = details["results"][0]
        assert "content" in result
        assert "source" in result
        assert 0 <= result["confidence"] <= 1
        # 上下文存在
        assert details["context"]["semanticGraph"] == "user_domain"
        assert details["context"]["orgMemory"] is not None

    def test_admin_can_query(self):
        """admin: 查询成功"""
        response = client.get(self.URL, params={"q": "SVIP 条件"},
                             headers=ADMIN_HEADERS)
        data = assert_success(response, "knowledge-query")
        assert data["details"]["query"] == "SVIP 条件"

    def test_query_without_role_header_defaults_guest(self):
        """无 X-Role: 默认 guest → 200"""
        response = client.get(self.URL, params={"q": "test"})
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_topk_limits_results(self):
        """topK=1 限制结果数量"""
        response = client.get(self.URL,
                             params={"q": "test", "topK": 1},
                             headers=ADMIN_HEADERS)
        data = assert_success(response, "knowledge-query")
        # mock 返回 1 条结果, topK=1 应保持 1 条
        assert len(data["details"]["results"]) <= 1

    def test_scope_param_propagated_to_log(self):
        """scope 透传到日志"""
        response = client.get(self.URL,
                             params={"q": "test", "scope": "tech"},
                             headers=ADMIN_HEADERS)
        data = response.json()
        assert "scope=tech" in data["logs"][0]["message"]

    def test_recall_rate_in_log_data(self):
        """日志 data 字段含 recallRate"""
        response = client.get(self.URL, params={"q": "x"},
                             headers=ADMIN_HEADERS)
        log_data = response.json()["logs"][0].get("data", {})
        assert log_data.get("recallRate") == "93%"

    def test_missing_q_param_rejected(self):
        """缺少 q 必填参数: 422"""
        response = client.get(self.URL, headers=ADMIN_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  知识层: POST /api/decision/knowledge/ingest
# ============================================================

class TestKnowledgeIngest:
    """知识层知识注入(admin only)"""

    URL = "/api/decision/knowledge/ingest"

    def _payload(self, category="rule", title="L3 留存消费规则",
                 content="L3 留存消费 ¥2000/年"):
        return {
            "category": category,
            "title": title,
            "content": content,
            "tags": ["会员", "L3"],
            "source": "module:02",
        }

    def test_admin_can_ingest_rule(self):
        """admin: 注入 rule 成功"""
        response = client.post(self.URL, json=self._payload(),
                              headers=ADMIN_HEADERS)
        data = assert_success(response, "knowledge-ingest")
        details = data["details"]
        assert details["category"] == "rule"
        assert details["indexed"] is True
        assert details["graphUpdated"] is True
        assert details["knowledgeId"].startswith("KN-")
        # 异步任务触发
        assert "embedding_update" in data["asyncOps"]
        assert "graph_rebuild" in data["asyncOps"]
        # 日志含 tags
        assert data["logs"][0]["data"]["tags"] == ["会员", "L3"]

    @pytest.mark.parametrize("category",
                             ["rule", "lesson_learned", "constraint", "red_line"])
    def test_all_categories(self, category):
        """4 种合法 category 全部通过"""
        response = client.post(self.URL,
                              json=self._payload(category=category),
                              headers=ADMIN_HEADERS)
        data = assert_success(response, "knowledge-ingest")
        assert data["details"]["category"] == category

    def test_knowledge_id_format(self):
        """knowledgeId 格式: KN-YYYYMMDD-XXXX"""
        response = client.post(self.URL, json=self._payload(),
                              headers=ADMIN_HEADERS)
        kid = response.json()["details"]["knowledgeId"]
        # KN-20260820-xxxx (4位十六进制)
        assert kid.startswith("KN-")
        parts = kid.split("-")
        assert len(parts) == 3
        assert len(parts[2]) == 4

    def test_member_forbidden(self):
        """member: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=MEMBER_HEADERS)
        assert_forbidden(response)

    def test_guest_forbidden(self):
        """guest: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=GUEST_HEADERS)
        assert_forbidden(response)

    def test_invalid_category_rejected(self):
        """非法 category: 422"""
        response = client.post(self.URL,
                              json=self._payload(category="invalid"),
                              headers=ADMIN_HEADERS)
        assert_unprocessable(response)

    def test_missing_title_rejected(self):
        """缺少 title: 422"""
        response = client.post(self.URL,
                              json={"category": "rule", "content": "x"},
                              headers=ADMIN_HEADERS)
        assert_unprocessable(response)

    def test_missing_content_rejected(self):
        """缺少 content: 422"""
        response = client.post(self.URL,
                              json={"category": "rule", "title": "x"},
                              headers=ADMIN_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  决策层: POST /api/decision/strategy-plan
# ============================================================

class TestStrategyPlan:
    """决策层策略筹划(member+)"""

    URL = "/api/decision/strategy-plan"

    def _payload(self, scenarios=None, role="member"):
        return {
            "role": role,
            "goal": "本月 GMV 突破 ¥10000",
            "constraints": {
                "budget": 5000,
                "timeframe": "next_30d",
                "riskTolerance": "medium",
            },
            "whatIfScenarios": (
                [{"name": "方案A", "params": {"channel": "live"}}]
                if scenarios is None else scenarios
            ),
        }

    def test_member_can_plan(self):
        """member: 筹划成功"""
        response = client.post(self.URL, json=self._payload(),
                              headers=MEMBER_HEADERS)
        data = assert_success(response, "strategy-plan")
        details = data["details"]
        assert isinstance(details["goalDecomposed"], list)
        assert len(details["goalDecomposed"]) >= 1
        # 子目标结构
        sub = details["goalDecomposed"][0]
        assert "subGoal" in sub and "priority" in sub
        assert details["resourceAssessment"]["budgetSufficient"] is True
        assert details["planningEfficiency"] == "60%"
        assert details["needsApproval"] is False
        # What-if 结果
        assert len(details["whatIfResults"]) >= 1
        assert details["recommendedPath"] == "方案A"

    def test_admin_can_plan(self):
        """admin: 通过"""
        response = client.post(self.URL, json=self._payload(),
                              headers=ADMIN_HEADERS)
        assert response.status_code == 200

    def test_empty_scenarios_uses_default(self):
        """空 whatIfScenarios → 默认方案"""
        response = client.post(self.URL, json=self._payload(scenarios=[]),
                              headers=MEMBER_HEADERS)
        details = response.json()["details"]
        assert details["recommendedPath"] == "默认方案"
        assert len(details["whatIfResults"]) == 1

    def test_multiple_scenarios(self):
        """多场景 What-if 推演"""
        scenarios = [
            {"name": f"方案{i}", "params": {"x": i}} for i in range(3)
        ]
        response = client.post(self.URL, json=self._payload(scenarios=scenarios),
                              headers=MEMBER_HEADERS)
        data = response.json()
        assert len(data["details"]["whatIfResults"]) == 3
        assert data["details"]["recommendedPath"] == "方案0"

    def test_guest_forbidden(self):
        """guest: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=GUEST_HEADERS)
        assert_forbidden(response)

    def test_missing_goal_rejected(self):
        """缺少 goal: 422"""
        response = client.post(self.URL,
                              json={"role": "member",
                                    "constraints": {"budget": 100}},
                              headers=MEMBER_HEADERS)
        assert_unprocessable(response)

    def test_invalid_risk_tolerance_rejected(self):
        """非法 riskTolerance: 422"""
        payload = self._payload()
        payload["constraints"]["riskTolerance"] = "extreme"
        response = client.post(self.URL, json=payload,
                              headers=MEMBER_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  决策层: POST /api/decision/forecast-simulate
# ============================================================

class TestForecastSimulate:
    """决策层预测推演(member+)"""

    URL = "/api/decision/forecast-simulate"

    def _payload(self, target="sales_volume", timeframe="next_7d",
                 method="monte_carlo", iterations=100, factors=None):
        return {
            "target": target,
            "timeframe": timeframe,
            "method": method,
            "iterations": iterations,
            "factors": factors if factors is not None else ["traffic", "conv_rate"],
        }

    def test_member_can_forecast(self):
        """member: 蒙特卡洛推演成功"""
        response = client.post(self.URL, json=self._payload(),
                              headers=MEMBER_HEADERS)
        data = assert_success(response, "forecast-simulate")
        details = data["details"]
        assert details["target"] == "sales_volume"
        assert details["method"] == "monte_carlo"
        assert details["iterations"] == 100
        assert details["accuracy"] == "90%"
        # 预测结果
        fc = details["forecast"]
        assert fc["p50"] == 320 and fc["p75"] == 380 and fc["p95"] == 450
        assert isinstance(fc["confidenceInterval"], list) and len(fc["confidenceInterval"]) == 2
        # 因子权重
        assert "traffic" in details["factorsWeight"]
        assert "conv_rate" in details["factorsWeight"]

    @pytest.mark.parametrize("method", ["monte_carlo", "lstm", "arima"])
    def test_all_methods(self, method):
        """3 种预测方法全部通过"""
        response = client.post(self.URL,
                              json=self._payload(method=method),
                              headers=MEMBER_HEADERS)
        data = assert_success(response, "forecast-simulate")
        assert data["details"]["method"] == method

    @pytest.mark.parametrize("target", ["sales_volume", "inventory", "capacity", "churn"])
    def test_all_targets(self, target):
        """4 种预测目标全部通过"""
        response = client.post(self.URL,
                              json=self._payload(target=target),
                              headers=MEMBER_HEADERS)
        data = assert_success(response, "forecast-simulate")
        assert data["details"]["target"] == target

    def test_empty_factors_yields_empty_weights(self):
        """空 factors → 空 factorsWeight"""
        response = client.post(self.URL,
                              json=self._payload(factors=[]),
                              headers=MEMBER_HEADERS)
        details = response.json()["details"]
        assert details["factorsWeight"] == {}

    def test_factors_weight_distribution(self):
        """2 个 factor 各占 0.5 权重"""
        response = client.post(self.URL,
                              json=self._payload(factors=["a", "b"]),
                              headers=MEMBER_HEADERS)
        weights = response.json()["details"]["factorsWeight"]
        assert weights == {"a": 0.5, "b": 0.5}

    def test_guest_forbidden(self):
        """guest: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=GUEST_HEADERS)
        assert_forbidden(response)

    def test_iterations_below_minimum_rejected(self):
        """iterations < 100: 422"""
        response = client.post(self.URL,
                              json=self._payload(iterations=99),
                              headers=MEMBER_HEADERS)
        assert_unprocessable(response)

    def test_iterations_above_maximum_rejected(self):
        """iterations > 100000: 422"""
        response = client.post(self.URL,
                              json=self._payload(iterations=100001),
                              headers=MEMBER_HEADERS)
        assert_unprocessable(response)

    def test_invalid_target_rejected(self):
        """非法 target: 422"""
        response = client.post(self.URL,
                              json=self._payload(target="invalid"),
                              headers=MEMBER_HEADERS)
        assert_unprocessable(response)

    def test_missing_timeframe_rejected(self):
        """缺少 timeframe: 422"""
        payload = self._payload()
        del payload["timeframe"]
        response = client.post(self.URL, json=payload,
                              headers=MEMBER_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  决策层: POST /api/decision/governance
# ============================================================

class TestGovernance:
    """决策层治理决策(admin only)"""

    URL = "/api/decision/governance"

    def _payload(self, action="launch_promotion", require_approval="auto"):
        return {
            "proposedAction": {
                "action": action,
                "target": "module:04",
                "params": {"discount": 0.85},
            },
            "ruleCheck": True,
            "requireApproval": require_approval,
        }

    def test_admin_can_govern(self):
        """admin: 治理决策成功"""
        response = client.post(self.URL, json=self._payload(),
                              headers=ADMIN_HEADERS)
        data = assert_success(response, "governance")
        details = data["details"]
        assert details["action"] == "launch_promotion"
        assert details["permissionGranted"] is True
        assert details["executionMode"] == "auto"
        assert details["complianceRate"] == "100%"
        # 规则校验结果
        rc = details["ruleCheckResult"]
        assert rc["passed"] is True
        assert rc["violations"] == []
        assert rc["checkedRules"] == 14
        # 区块链存证
        bn = details["blockchainNotarize"]
        assert bn["hash"].startswith("0x")
        assert bn["type"] == "决策存证"
        # 异步任务
        assert "blockchain_notarize" in data["asyncOps"]
        assert "audit_log" in data["asyncOps"]

    def test_manual_approval_mode(self):
        """requireApproval=manual: executionMode=manual"""
        response = client.post(self.URL,
                              json=self._payload(require_approval="manual"),
                              headers=ADMIN_HEADERS)
        details = response.json()["details"]
        assert details["executionMode"] == "manual"

    def test_member_forbidden(self):
        """member: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=MEMBER_HEADERS)
        assert_forbidden(response)

    def test_guest_forbidden(self):
        """guest: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=GUEST_HEADERS)
        assert_forbidden(response)

    def test_agent_forbidden(self):
        """agent: 403(admin only)"""
        response = client.post(self.URL, json=self._payload(),
                              headers=AGENT_HEADERS)
        assert_forbidden(response)

    def test_missing_proposed_action_rejected(self):
        """缺少 proposedAction: 422"""
        response = client.post(self.URL,
                              json={"ruleCheck": True},
                              headers=ADMIN_HEADERS)
        assert_unprocessable(response)

    def test_invalid_require_approval_rejected(self):
        """非法 requireApproval: 422"""
        response = client.post(self.URL,
                              json=self._payload(require_approval="invalid"),
                              headers=ADMIN_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  编排层: POST /api/decision/orchestrate
# ============================================================

class TestOrchestrate:
    """编排层跨域工作流编排(agent+)"""

    URL = "/api/decision/orchestrate"

    def _payload(self, modules=None, workflow="sales_flow"):
        return {
            "workflow": workflow,
            "modules": modules or ["module:01", "module:02", "module:04"],
            "context": {"region": "TA"},
            "decompose": True,
            "parallel": True,
        }

    def test_agent_can_orchestrate(self):
        """agent: 工作流编排成功(v35 中枢真实路由: 无语义模块名 → fallback)"""
        response = client.post(self.URL, json=self._payload(),
                              headers=AGENT_HEADERS)
        data = assert_success(response, "orchestrate")
        details = data["details"]
        assert details["workflow"] == "sales_flow"
        # P1 真实化: module:01 等非语义文本路由不到能力 → 全 fallback
        assert details["successRate"] == "0%"
        assert details["duration"] == "<5ms"
        # 任务分解
        tasks = details["tasks"]
        assert len(tasks) == 3
        t1 = tasks[0]
        assert t1["id"] == "T1" and t1["module"] == "module:01"
        assert t1["depends"] == []  # 首任务无依赖
        assert tasks[-1]["status"] == "fallback"
        assert tasks[0]["status"] == "fallback"
        assert tasks[0]["capability"] is None
        # 依赖链 T2 → T1
        assert tasks[1]["depends"] == ["T1"]
        # 并行组
        assert len(details["parallelGroups"]) == 3

    def test_agent_orchestrate_semantic_routed(self):
        """v35 真实化: 语义模块文本(查订单/转人工) → 能力命中 pass"""
        response = client.post(self.URL,
                              json=self._payload(
                                  modules=["查订单", "转人工"]),
                              headers=AGENT_HEADERS)
        details = response.json()["details"]
        assert details["successRate"] == "100%"
        assert all(t["status"] == "pass" for t in details["tasks"])
        assert details["tasks"][0]["capability"] == "order.query"
        assert details["tasks"][1]["capability"] == "chat.human"

    def test_admin_can_orchestrate(self):
        """admin: 通过"""
        response = client.post(self.URL, json=self._payload(),
                              headers=ADMIN_HEADERS)
        assert response.status_code == 200

    def test_single_module(self):
        """单模块编排(非语义模块名 → fallback)"""
        response = client.post(self.URL,
                              json=self._payload(modules=["module:01"]),
                              headers=AGENT_HEADERS)
        details = response.json()["details"]
        assert len(details["tasks"]) == 1
        assert details["tasks"][0]["status"] == "fallback"

    def test_member_forbidden(self):
        """member: 403(agent+)"""
        response = client.post(self.URL, json=self._payload(),
                              headers=MEMBER_HEADERS)
        assert_forbidden(response)

    def test_guest_forbidden(self):
        """guest: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=GUEST_HEADERS)
        assert_forbidden(response)

    def test_missing_modules_rejected(self):
        """缺少 modules: 422"""
        response = client.post(self.URL,
                              json={"workflow": "x", "context": {}},
                              headers=AGENT_HEADERS)
        assert_unprocessable(response)

    def test_missing_context_rejected(self):
        """缺少 context: 422"""
        response = client.post(self.URL,
                              json={"workflow": "x", "modules": ["m"]},
                              headers=AGENT_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  编排层: POST /api/decision/capability-route
# ============================================================

class TestCapabilityRoute:
    """编排层能力路由(store_owner+)"""

    URL = "/api/decision/capability-route"

    def _payload(self, caps=None, task="extract_intent"):
        return {
            "requiredCapabilities": (
                # v35 真实化: 插件池为 hub 真实能力注册表(非硬编码 nlp/vision)
                ["order.query", "chat.human"] if caps is None else caps
            ),
            "task": task,
            "budget": {"maxLatency": "500ms", "maxCost": "¥1.0"},
            "preferPlugins": [],
        }

    def test_store_owner_can_route(self):
        """store_owner: 能力路由成功(v35 真实能力注册表 8 插件池)"""
        response = client.post(self.URL, json=self._payload(),
                              headers=STORE_OWNER_HEADERS)
        data = assert_success(response, "capability-route")
        details = data["details"]
        assert details["pluginPool"] == 8
        assert details["reuseRate"] == "100%"
        assert len(details["selectedPlugins"]) == 2
        # 插件结构
        p0 = details["selectedPlugins"][0]
        assert "id" in p0 and "type" in p0 and "latency" in p0 and "cost" in p0
        # 组合表达式
        assert " → " in details["composition"]
        # 总延迟与总成本
        assert details["totalLatency"].endswith("ms")
        assert details["totalCost"].startswith("¥")

    def test_admin_can_route(self):
        """admin: 通过"""
        response = client.post(self.URL, json=self._payload(),
                              headers=ADMIN_HEADERS)
        assert response.status_code == 200

    @pytest.mark.parametrize("caps,expected_count",
                             [
                                 # v35 真实化: hub 真实能力注册表 id
                                 (["order.query"], 1),
                                 (["order.query", "chat.human"], 2),
                                 (["order.query", "chat.human", "knowledge.rag"], 3),
                                 (["order.query", "chat.human", "knowledge.rag", "hub.ops"], 4),
                             ],
                             ids=["one-cap", "two-caps", "three-caps",
                                  "four-caps"])
    def test_capability_combinations(self, caps, expected_count):
        """不同能力组合 → 不同插件数"""
        response = client.post(self.URL, json=self._payload(caps=caps),
                              headers=STORE_OWNER_HEADERS)
        details = response.json()["details"]
        assert len(details["selectedPlugins"]) == expected_count

    def test_unknown_capability_filtered(self):
        """未知能力被过滤掉"""
        response = client.post(self.URL,
                              json=self._payload(
                                  caps=["order.query", "unknown_cap"]),
                              headers=STORE_OWNER_HEADERS)
        details = response.json()["details"]
        assert len(details["selectedPlugins"]) == 1

    def test_empty_capabilities_yields_empty_selection(self):
        """空 capabilities → 空选择"""
        response = client.post(self.URL,
                              json=self._payload(caps=[]),
                              headers=STORE_OWNER_HEADERS)
        details = response.json()["details"]
        assert details["selectedPlugins"] == []
        assert details["composition"] == ""
        assert details["totalLatency"] == "0ms"
        assert details["totalCost"] == "¥0.00"

    def test_agent_forbidden(self):
        """agent: 403(store_owner+)"""
        response = client.post(self.URL, json=self._payload(),
                              headers=AGENT_HEADERS)
        assert_forbidden(response)

    def test_member_forbidden(self):
        """member: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=MEMBER_HEADERS)
        assert_forbidden(response)

    def test_missing_required_capabilities_rejected(self):
        """缺少 requiredCapabilities: 422"""
        response = client.post(self.URL,
                              json={"task": "x"},
                              headers=STORE_OWNER_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  执行层: POST /api/decision/role-copilot
# ============================================================

class TestRoleCopilot:
    """执行层角色决策助理(全角色)"""

    URL = "/api/decision/role-copilot"

    def _payload(self, role="member", intent="recommend_product", mode="copilot"):
        return {
            "role": role,
            "intent": intent,
            "context": {"userId": "U10086"},
            "mode": mode,
        }

    @pytest.mark.parametrize("role,expected_min_recs",
                             [
                                 ("member", 1),
                                 ("agent", 1),
                                 ("guest", 1),
                                 ("store_owner", 1),
                                 ("admin", 1),
                             ],
                             ids=["member", "agent", "guest", "store_owner", "admin"])
    def test_role_specific_recommendations(self, role, expected_min_recs):
        """5 种角色各自返回针对性建议"""
        response = client.post(self.URL,
                              json=self._payload(role=role),
                              headers={"X-Role": role})
        data = assert_success(response, "role-copilot")
        details = data["details"]
        assert details["role"] == role
        assert details["decisionAccuracy"] == "92%"
        assert len(details["recommendations"]) >= expected_min_recs
        # 每条建议结构
        rec = details["recommendations"][0]
        assert "type" in rec and "target" in rec
        assert "reason" in rec and "confidence" in rec

    def test_copilot_mode_needs_confirm(self):
        """copilot 模式: needsUserConfirm=True"""
        response = client.post(self.URL,
                              json=self._payload(mode="copilot"),
                              headers=MEMBER_HEADERS)
        details = response.json()["details"]
        assert details["executionMode"] == "copilot"
        assert details["needsUserConfirm"] is True

    def test_agent_mode_no_confirm(self):
        """agent 模式: needsUserConfirm=False"""
        response = client.post(self.URL,
                              json=self._payload(mode="agent"),
                              headers=ADMIN_HEADERS)
        details = response.json()["details"]
        assert details["executionMode"] == "agent"
        assert details["needsUserConfirm"] is False

    def test_no_role_header_still_works_for_guest(self):
        """无 X-Role: 端点不强制角色,role 字段决定建议"""
        response = client.post(self.URL,
                              json=self._payload(role="guest"),
                              headers={})
        assert response.status_code == 200
        assert response.json()["details"]["role"] == "guest"

    def test_intent_propagated_to_log(self):
        """intent 透传到日志"""
        response = client.post(self.URL,
                              json=self._payload(intent="optimize_pricing"),
                              headers=MEMBER_HEADERS)
        log_data = response.json()["logs"][0].get("data", {})
        assert log_data.get("intent") == "optimize_pricing"

    def test_invalid_mode_rejected(self):
        """非法 mode: 422"""
        response = client.post(self.URL,
                              json=self._payload(mode="auto"),
                              headers=MEMBER_HEADERS)
        assert_unprocessable(response)

    def test_missing_intent_rejected(self):
        """缺少 intent: 422"""
        response = client.post(self.URL,
                              json={"role": "member", "mode": "copilot"},
                              headers=MEMBER_HEADERS)
        assert_unprocessable(response)

    def test_invalid_role_enum_rejected(self):
        """非法 role 枚举值: 422"""
        response = client.post(self.URL,
                              json=self._payload(role="super_user"),
                              headers=MEMBER_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  反馈层: POST /api/decision/feedback-loop
# ============================================================

class TestFeedbackLoop:
    """反馈层反馈闭环(agent+)"""

    URL = "/api/decision/feedback-loop"

    def _payload(self, action_id="A001", ordered=True, clicked=True):
        return {
            "actionId": action_id,
            "outcome": {
                "delivered": True,
                "clicked": clicked,
                "ordered": ordered,
                "complained": False,
            },
            "cost": 0.5,
            "reflowMetrics": ["ctr", "conversion_rate"],
        }

    def test_agent_can_feedback(self):
        """agent: 反馈闭环成功"""
        response = client.post(self.URL, json=self._payload(),
                              headers=AGENT_HEADERS)
        data = assert_success(response, "feedback-loop")
        details = data["details"]
        assert details["actionId"] == "A001"
        assert details["feedbackLatency"] == "<24h"
        # 效果评估
        ev = details["evaluation"]
        assert "ctr" in ev and "conversionRate" in ev and "roi" in ev
        # 模型更新
        mu = details["modelUpdate"]
        assert mu["triggered"] is True
        assert "plugin" in mu and "improvement" in mu
        # 区块链存证
        assert details["blockchainNotarize"]["type"] == "反馈追踪"
        assert details["blockchainNotarize"]["hash"].startswith("0x")
        # 异步任务
        assert "model_iteration" in data["asyncOps"]
        assert "plugin_upgrade" in data["asyncOps"]
        assert "data_reflow" in data["asyncOps"]

    def test_admin_can_feedback(self):
        """admin: 通过"""
        response = client.post(self.URL, json=self._payload(),
                              headers=ADMIN_HEADERS)
        assert response.status_code == 200

    def test_outcome_propagated_to_log(self):
        """outcome.ordered/clicked 透传到日志"""
        response = client.post(self.URL,
                              json=self._payload(ordered=True, clicked=False),
                              headers=AGENT_HEADERS)
        log_data = response.json()["logs"][0].get("data", {})
        assert log_data["ordered"] is True
        assert log_data["clicked"] is False

    def test_member_forbidden(self):
        """member: 403(agent+)"""
        response = client.post(self.URL, json=self._payload(),
                              headers=MEMBER_HEADERS)
        assert_forbidden(response)

    def test_guest_forbidden(self):
        """guest: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=GUEST_HEADERS)
        assert_forbidden(response)

    def test_missing_action_id_rejected(self):
        """缺少 actionId: 422"""
        response = client.post(self.URL,
                              json={"outcome": {"delivered": True,
                                                "clicked": False,
                                                "ordered": False}},
                              headers=AGENT_HEADERS)
        assert_unprocessable(response)

    def test_missing_outcome_rejected(self):
        """缺少 outcome: 422"""
        response = client.post(self.URL,
                              json={"actionId": "A001"},
                              headers=AGENT_HEADERS)
        assert_unprocessable(response)

    def test_outcome_missing_required_field_rejected(self):
        """outcome 缺 delivered: 422"""
        response = client.post(self.URL,
                              json={"actionId": "A001",
                                    "outcome": {"clicked": False, "ordered": False}},
                              headers=AGENT_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  反馈层: POST /api/decision/retrospective
# ============================================================

class TestRetrospective:
    """反馈层复盘优化(store_owner+)"""

    URL = "/api/decision/retrospective"

    def _payload(self, event="weekly_review", period="2026-W33",
                 depth="root_cause"):
        return {
            "event": event,
            "period": period,
            "scope": ["module:04", "module:02"],
            "depth": depth,
        }

    def test_store_owner_can_retrospect(self):
        """store_owner: 复盘成功(root_cause 深度)"""
        response = client.post(self.URL, json=self._payload(),
                              headers=STORE_OWNER_HEADERS)
        data = assert_success(response, "retrospective")
        details = data["details"]
        assert details["event"] == "weekly_review"
        assert details["coverage"] == "85%"
        # root_cause 深度含根因分析
        analysis = details["analysis"]
        assert "gmv" in analysis
        assert "target" in analysis
        assert "achievement" in analysis
        assert "rootCause" in analysis
        # 经验沉淀
        assert len(details["lessonsLearned"]) >= 1
        lesson = details["lessonsLearned"][0]
        assert "title" in lesson and "detail" in lesson and "tag" in lesson
        assert details["strategyOptimization"] is not None
        # 异步任务
        assert "knowledge_ingest" in data["asyncOps"]
        assert "strategy_update" in data["asyncOps"]

    def test_summary_depth(self):
        """summary 深度: 分析字段更少(无 rootCause)"""
        response = client.post(self.URL,
                              json=self._payload(depth="summary"),
                              headers=STORE_OWNER_HEADERS)
        details = response.json()["details"]
        assert "rootCause" not in details["analysis"]
        assert "gmv" in details["analysis"]
        assert "achievement" in details["analysis"]

    def test_admin_can_retrospect(self):
        """admin: 通过"""
        response = client.post(self.URL, json=self._payload(),
                              headers=ADMIN_HEADERS)
        assert response.status_code == 200

    def test_period_propagated_to_log(self):
        """period 透传到日志"""
        response = client.post(self.URL,
                              json=self._payload(period="2026-08"),
                              headers=STORE_OWNER_HEADERS)
        log_data = response.json()["logs"][0].get("data", {})
        assert log_data["period"] == "2026-08"
        assert log_data["depth"] == "root_cause"

    def test_agent_forbidden(self):
        """agent: 403(store_owner+)"""
        response = client.post(self.URL, json=self._payload(),
                              headers=AGENT_HEADERS)
        assert_forbidden(response)

    def test_member_forbidden(self):
        """member: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=MEMBER_HEADERS)
        assert_forbidden(response)

    def test_guest_forbidden(self):
        """guest: 403"""
        response = client.post(self.URL, json=self._payload(),
                              headers=GUEST_HEADERS)
        assert_forbidden(response)

    def test_invalid_depth_rejected(self):
        """非法 depth: 422"""
        response = client.post(self.URL,
                              json=self._payload(depth="deep"),
                              headers=STORE_OWNER_HEADERS)
        assert_unprocessable(response)

    def test_missing_event_rejected(self):
        """缺少 event: 422"""
        response = client.post(self.URL,
                              json={"period": "2026-W33", "depth": "summary"},
                              headers=STORE_OWNER_HEADERS)
        assert_unprocessable(response)

    def test_missing_period_rejected(self):
        """缺少 period: 422"""
        response = client.post(self.URL,
                              json={"event": "x", "depth": "summary"},
                              headers=STORE_OWNER_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  跨端点横切关注点
# ============================================================

class TestCrossCutting:
    """横切: 日志结构 / asyncOps / X-Role 默认值 / 异常处理"""

    def test_all_success_responses_have_consistent_shape(self):
        """所有成功响应统一结构: success/operation/details/logs/asyncOps"""
        endpoints = [
            ("post", "/api/decision/data-ingest", ADMIN_HEADERS,
             {"source": "module:01", "dataType": "metrics",
              "payload": [{"key": "k", "value": 1.0,
                           "timestamp": "2026-08-20T10:00:00Z"}]}),
            ("post", "/api/decision/knowledge/ingest", ADMIN_HEADERS,
             {"category": "rule", "title": "t", "content": "c"}),
            ("post", "/api/decision/strategy-plan", MEMBER_HEADERS,
             {"role": "member", "goal": "g",
              "constraints": {"budget": 100}}),
        ]
        for method, url, headers, body in endpoints:
            response = getattr(client, method)(url, json=body, headers=headers)
            data = response.json()
            assert set(data.keys()) >= {"success", "operation", "details",
                                        "logs", "asyncOps"}, \
                f"{url} 响应缺少必要字段"

    def test_log_entry_structure(self):
        """日志条目结构: stage/message/data"""
        response = client.post(
            "/api/decision/knowledge/ingest",
            json={"category": "rule", "title": "t", "content": "c"},
            headers=ADMIN_HEADERS,
        )
        log = response.json()["logs"][0]
        assert "stage" in log
        assert "message" in log
        # data 可选,但若存在必须是 dict
        if "data" in log and log["data"] is not None:
            assert isinstance(log["data"], dict)

    def test_forbidden_response_consistent(self):
        """所有 403 响应结构一致: success/error/errorCode"""
        endpoints = [
            ("/api/decision/data-ingest", MEMBER_HEADERS),
            ("/api/decision/governance", AGENT_HEADERS),
            ("/api/decision/orchestrate", MEMBER_HEADERS),
            ("/api/decision/capability-route", AGENT_HEADERS),
            ("/api/decision/feedback-loop", MEMBER_HEADERS),
            ("/api/decision/retrospective", MEMBER_HEADERS),
        ]
        for url, headers in endpoints:
            body = self._minimal_body(url)
            response = client.post(url, json=body, headers=headers)
            assert response.status_code == 403, f"{url} 应返回 403"
            data = response.json()
            assert data["success"] is False
            assert "DECISION_003" in data["error"]
            assert data["errorCode"] == "DECISION_003"

    def _minimal_body(self, url):
        """为受保护端点构造最小可过校验的请求体"""
        if "data-ingest" in url:
            return {"source": "module:01", "dataType": "metrics",
                    "payload": [{"key": "k", "value": 1.0,
                                 "timestamp": "2026-08-20T10:00:00Z"}]}
        if "knowledge/ingest" in url:
            return {"category": "rule", "title": "t", "content": "c"}
        if "strategy-plan" in url:
            return {"role": "member", "goal": "g",
                    "constraints": {"budget": 100}}
        if "forecast-simulate" in url:
            return {"target": "sales_volume", "timeframe": "next_7d"}
        if "governance" in url:
            return {"proposedAction": {"action": "a", "target": "t"}}
        if "orchestrate" in url:
            return {"workflow": "w", "modules": ["m"], "context": {}}
        if "capability-route" in url:
            return {"requiredCapabilities": ["nlp"], "task": "t"}
        if "feedback-loop" in url:
            return {"actionId": "a",
                    "outcome": {"delivered": True, "clicked": False,
                                "ordered": False}}
        if "retrospective" in url:
            return {"event": "e", "period": "p", "depth": "summary"}
        return {}

    def test_invalid_role_header_falls_back_to_guest(self):
        """非法 X-Role 值 → 默认 guest 行为(对开放端点返回 200)"""
        response = client.get(
            "/api/decision/knowledge/query",
            params={"q": "test"},
            headers={"X-Role": "superuser"},
        )
        # get_current_role 不识别 superuser → 默认 guest → 全角色端点 200
        assert response.status_code == 200

    def test_general_exception_handler_returns_500(self):
        """通用异常处理器: DECISION_010"""
        # 触发 main.py general_exception_handler 的简单方式:
        # 传入无法解析的请求体使 Pydantic 之前抛错(实际 FastAPI 会先 422,
        # 此用例验证 422 不会落到 500)
        response = client.post(
            "/api/decision/data-ingest",
            content="not-json",
            headers={"X-Role": "admin", "Content-Type": "application/json"},
        )
        # 应该是 422 而非 500(参数解析失败)
        assert response.status_code in (400, 422)

    def test_general_exception_handler_triggers_500(self):
        """触发 500 兜底处理器: 直接调用 general_exception_handler 验证响应"""
        from core.errors import general_exception_handler
        from fastapi.responses import JSONResponse

        exc = RuntimeError("模拟内部错误")
        response = asyncio.run(general_exception_handler(request=None, exc=exc))

        assert isinstance(response, JSONResponse)
        assert response.status_code == 500
        body = json.loads(response.body.decode("utf-8"))
        assert body["success"] is False
        assert "DECISION_010" in body["error"]
        assert "模拟内部错误" in body["error"]
        assert body["errorCode"] == DecisionErrorCode.e010.value


# ============================================================
#  系统端点: health / mode / mode/switch
#  (补充覆盖 main.py 506-530 行,推动 main.py 覆盖率 ≥95%)
# ============================================================

class TestSystemEndpoints:
    """系统端点: 健康检查 + 模式查询/切换"""

    def test_health_no_auth_required(self):
        """health: 无需认证,返回模块状态"""
        response = client.get("/api/decision/health")
        data = assert_success(response, "health")
        details = data["details"]
        assert details["status"] == "healthy"
        assert details["module"] == "AI决策筹划模块(29)"
        assert details["aiRate"] == "95%"
        assert details["pluginPool"] == 120
        assert details["mockMode"] is True
        assert "h" in details["uptime"]  # _uptime() 返回 Nh 格式

    def test_get_mode_admin_only(self):
        """get_mode: admin 返回当前模式"""
        response = client.get("/api/decision/mode", headers=ADMIN_HEADERS)
        data = assert_success(response, "mode")
        details = data["details"]
        assert details["mode"] == "mock"
        assert details["apiBase"] == "/api/decision"
        assert details["endpoints"] == 15
        assert details["aiCapabilities"] == 10

    def test_get_mode_member_forbidden(self):
        """get_mode: member 403"""
        response = client.get("/api/decision/mode", headers=MEMBER_HEADERS)
        assert_forbidden(response)

    def test_get_mode_guest_forbidden(self):
        """get_mode: guest 403(对应 curl 验证场景)"""
        response = client.get("/api/decision/mode", headers=GUEST_HEADERS)
        assert_forbidden(response)

    def test_get_mode_no_header_defaults_guest_forbidden(self):
        """get_mode: 无 X-Role 头 → 默认 guest → 403"""
        response = client.get("/api/decision/mode")
        assert_forbidden(response)

    @pytest.mark.parametrize(
        "role_header,expected_status",
        [
            ("admin", 200),
            ("store_owner", 403),
            ("agent", 403),
            ("member", 403),
            ("guest", 403),
            (None, 403),
        ],
        ids=["admin-pass", "store_owner-403", "agent-403",
             "member-403", "guest-403", "no-header-403"],
    )
    def test_mode_access_role_matrix(self, role_header, expected_status):
        """权限矩阵: 仅 admin 可访问 /mode,其余角色(含默认 guest)均 403"""
        headers = {"X-Role": role_header} if role_header else {}
        response = client.get("/api/decision/mode", headers=headers)
        if expected_status == 200:
            data = assert_success(response, "mode")
            assert data["details"]["mode"] == "mock"
        else:
            assert_forbidden(response)

    def test_switch_mode_to_live(self):
        """switch_mode: 切换至 live 模式"""
        response = client.post("/api/decision/mode/switch",
                              json={"mode": "live"},
                              headers=ADMIN_HEADERS)
        data = assert_success(response, "mode-switch")
        details = data["details"]
        assert details["mode"] == "live"
        # 验证日志记录了切换
        assert len(data["logs"]) >= 1
        assert "live" in data["logs"][0]["message"]

        # 还原 mock 模式,避免污染后续测试
        client.post("/api/decision/mode/switch",
                   json={"mode": "mock"},
                   headers=ADMIN_HEADERS)

    def test_switch_mode_with_custom_api_base(self):
        """switch_mode: 自定义 apiBase"""
        response = client.post("/api/decision/mode/switch",
                              json={"mode": "live", "apiBase": "/api/v2"},
                              headers=ADMIN_HEADERS)
        details = response.json()["details"]
        assert details["apiBase"] == "/api/v2"
        # 还原
        client.post("/api/decision/mode/switch",
                   json={"mode": "mock", "apiBase": "/api/decision"},
                   headers=ADMIN_HEADERS)

    def test_switch_mode_member_forbidden(self):
        """switch_mode: member 403"""
        response = client.post("/api/decision/mode/switch",
                              json={"mode": "live"},
                              headers=MEMBER_HEADERS)
        assert_forbidden(response)

    def test_switch_mode_invalid_mode_rejected(self):
        """非法 mode: 422"""
        response = client.post("/api/decision/mode/switch",
                              json={"mode": "invalid"},
                              headers=ADMIN_HEADERS)
        assert_unprocessable(response)


# ============================================================
#  main.py 启动入口测试
#  覆盖 if __name__ == "__main__" 块(行 88-93)
# ============================================================

class TestMainEntryPoint:
    """main.py 启动入口测试

    通过 exec 执行 main.py 源码并设置 __name__ == "__main__",
    同时 monkeypatch uvicorn.run 拦截实际启动,验证环境变量解析和调用参数。
    """

    def test_main_entry_default_host_port(self, monkeypatch):
        """启动入口: 默认 host=0.0.0.0, port=8000"""
        import sys
        from types import ModuleType

        # 拦截 uvicorn.run
        calls = []
        fake_uvicorn = ModuleType("uvicorn")
        fake_uvicorn.run = lambda app, host, port, **kwargs: calls.append(
            {"host": host, "port": port}
        )
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

        # 清除 PORT/HOST 环境变量,使用默认值
        monkeypatch.delenv("PORT", raising=False)
        monkeypatch.delenv("HOST", raising=False)

        # exec main.py 源码,设置 __name__ == "__main__"
        import main
        with open(main.__file__, encoding="utf-8") as f:
            source = f.read()
        exec(compile(source, main.__file__, "exec"),
             {"__name__": "__main__", "__file__": main.__file__})

        # 验证 uvicorn.run 被调用,使用默认参数
        assert len(calls) == 1
        assert calls[0]["host"] == "0.0.0.0"
        assert calls[0]["port"] == 8000

    def test_main_entry_custom_host_port(self, monkeypatch):
        """启动入口: 自定义 PORT=9999, HOST=127.0.0.1"""
        import sys
        from types import ModuleType

        calls = []
        fake_uvicorn = ModuleType("uvicorn")
        fake_uvicorn.run = lambda app, host, port, **kwargs: calls.append(
            {"host": host, "port": port}
        )
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

        # 设置自定义环境变量
        monkeypatch.setenv("PORT", "9999")
        monkeypatch.setenv("HOST", "127.0.0.1")

        import main
        with open(main.__file__, encoding="utf-8") as f:
            source = f.read()
        exec(compile(source, main.__file__, "exec"),
             {"__name__": "__main__", "__file__": main.__file__})

        assert len(calls) == 1
        assert calls[0]["host"] == "127.0.0.1"
        assert calls[0]["port"] == 9999
