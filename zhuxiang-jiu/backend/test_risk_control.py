"""
AI决策筹划模块(模块29) /api/decision/risk-control 端点单元测试
覆盖三种风控场景: 低风险/中风险/高风险 + 边界值 + 权限校验 + 参数校验

运行: pytest test_risk_control.py -v
"""

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

RISK_CONTROL_URL = "/api/decision/risk-control"
ADMIN_HEADERS = {"X-Role": "admin"}
MEMBER_HEADERS = {"X-Role": "member"}
GUEST_HEADERS = {"X-Role": "guest"}


def make_payload(amount: float, max_amount: float = 10000,
                 check_type: str = "transaction_anomaly") -> dict:
    """构造风控请求体"""
    return {
        "checkType": check_type,
        "target": {
            "userId": "U10086",
            "transactionId": f"ZX20260819-{int(amount)}",
            "amount": amount,
        },
        "thresholds": {
            "maxAmount": max_amount,
            "frequencyPerDay": 5,
        },
        "autoCircuitBreak": True,
    }


# ============================================================
#  三种风控场景测试(对照 curl 测试结果)
# ============================================================

class TestRiskScenarios:
    """低/中/高风险三场景参数化测试"""

    @pytest.mark.parametrize(
        "amount,max_amount,expected_level,expected_action,expected_breaker,anomaly_count",
        [
            # 低风险: 3000/10000 = 0.30
            (3000, 10000, "low", "pass", "standby", 0),
            # 中风险: 7500/10000 = 0.75
            (7500, 10000, "medium", "pass_with_monitoring", "standby", 1),
            # 高风险: 9500/10000 = 0.95
            (9500, 10000, "high", "block", "tripped", 1),
        ],
        ids=["low-risk-3000", "medium-risk-7500", "high-risk-9500"],
    )
    def test_risk_levels(
        self, amount, max_amount,
        expected_level, expected_action, expected_breaker, anomaly_count,
    ):
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(amount, max_amount),
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()

        # 基础结构断言
        assert data["success"] is True
        assert data["operation"] == "risk-control"
        assert data["details"]["coverage"] == "96%"

        # 风控结果断言
        assert data["details"]["riskLevel"] == expected_level
        assert data["details"]["action"] == expected_action
        assert data["details"]["circuitBreaker"] == expected_breaker
        assert len(data["details"]["anomalies"]) == anomaly_count

    def test_low_risk_no_anomalies(self):
        """低风险: 无异常记录, 允许交易"""
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(3000),
            headers=ADMIN_HEADERS,
        )
        data = response.json()
        details = data["details"]

        assert details["riskLevel"] == "low"
        assert details["anomalies"] == []
        assert details["circuitBreaker"] == "standby"
        assert details["action"] == "pass"
        assert details["recommendation"] == "Allow transaction"

    def test_medium_risk_one_anomaly(self):
        """中风险: 1条异常, 标记观察"""
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(7500),
            headers=ADMIN_HEADERS,
        )
        data = response.json()
        details = data["details"]

        assert details["riskLevel"] == "medium"
        assert len(details["anomalies"]) == 1

        anomaly = details["anomalies"][0]
        assert anomaly["type"] == "amount_near_limit"
        assert "7500" in anomaly["detail"]
        assert "10000" in anomaly["detail"]
        assert anomaly["score"] == 0.75

        assert details["circuitBreaker"] == "standby"
        assert details["action"] == "pass_with_monitoring"

    def test_high_risk_circuit_breaker_tripped(self):
        """高风险: 熔断器触发, 拦截交易"""
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(9500),
            headers=ADMIN_HEADERS,
        )
        data = response.json()
        details = data["details"]

        assert details["riskLevel"] == "high"
        assert details["circuitBreaker"] == "tripped"
        assert details["action"] == "block"
        assert details["recommendation"] == "Block transaction"

        anomaly = details["anomalies"][0]
        assert anomaly["score"] == 0.95

    def test_log_entry_contains_risk_data(self):
        """日志条目包含风险数据"""
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(7500),
            headers=ADMIN_HEADERS,
        )
        data = response.json()

        assert len(data["logs"]) >= 1
        log = data["logs"][0]
        assert log["stage"] == "risk-control"
        assert "medium" in log["message"]
        assert log["data"]["amount"] == 7500
        assert log["data"]["maxAmount"] == 10000
        assert log["data"]["ratio"] == 0.75
        assert log["data"]["action"] == "pass_with_monitoring"


# ============================================================
#  边界值测试
# ============================================================

class TestRiskBoundaries:
    """阈值边界: 0.7 和 0.9 临界点"""

    @pytest.mark.parametrize(
        "amount,expected_level",
        [
            (6999, "low"),      # 0.6999 → low
            (7000, "medium"),   # 0.70 → medium (>=0.7)
            (8999, "medium"),   # 0.8999 → medium
            (9000, "high"),      # 0.90 → high (>=0.9)
        ],
        ids=["just-below-0.7", "exactly-0.7", "just-below-0.9", "exactly-0.9"],
    )
    def test_threshold_boundaries(self, amount, expected_level):
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(amount),
            headers=ADMIN_HEADERS,
        )
        data = response.json()
        assert data["details"]["riskLevel"] == expected_level

    def test_zero_amount_is_low(self):
        """金额为0: 低风险"""
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(0),
            headers=ADMIN_HEADERS,
        )
        data = response.json()
        assert data["details"]["riskLevel"] == "low"
        assert data["details"]["anomalies"] == []

    def test_amount_equals_max(self):
        """金额等于上限: 高风险(1.0 >= 0.9)"""
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(10000, max_amount=10000),
            headers=ADMIN_HEADERS,
        )
        data = response.json()
        assert data["details"]["riskLevel"] == "high"
        assert data["details"]["circuitBreaker"] == "tripped"


# ============================================================
#  角色权限测试
# ============================================================

class TestRiskPermissions:
    """risk-control 端点要求 admin 角色"""

    def test_admin_access_granted(self):
        """admin 角色: 200"""
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(5000),
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_member_forbidden(self):
        """member 角色: 403"""
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(5000),
            headers=MEMBER_HEADERS,
        )
        assert response.status_code == 403
        data = response.json()
        assert data["success"] is False
        assert "DECISION_003" in data["error"]

    def test_guest_forbidden(self):
        """guest 角色: 403"""
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(5000),
            headers=GUEST_HEADERS,
        )
        assert response.status_code == 403

    def test_no_role_header_defaults_guest_forbidden(self):
        """无 X-Role 头: 默认 guest → 403"""
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(5000),
        )
        assert response.status_code == 403


# ============================================================
#  参数校验测试
# ============================================================

class TestRiskValidation:
    """Pydantic 模型校验"""

    def test_missing_check_type(self):
        """缺少 checkType: 422"""
        response = client.post(
            RISK_CONTROL_URL,
            json={"target": {"amount": 5000}},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 422

    def test_missing_target(self):
        """缺少 target: 422"""
        response = client.post(
            RISK_CONTROL_URL,
            json={"checkType": "transaction_anomaly"},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 422

    def test_invalid_check_type(self):
        """非法 checkType: 422"""
        response = client.post(
            RISK_CONTROL_URL,
            json={"checkType": "invalid_type", "target": {"amount": 5000}},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 422

    def test_custom_thresholds(self):
        """自定义阈值: maxAmount=5000, amount=4000 → medium(0.8)"""
        response = client.post(
            RISK_CONTROL_URL,
            json=make_payload(4000, max_amount=5000),
            headers=ADMIN_HEADERS,
        )
        data = response.json()
        assert data["details"]["riskLevel"] == "medium"
        assert data["details"]["anomalies"][0]["score"] == 0.8
