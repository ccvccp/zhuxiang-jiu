"""代理商返利结算 + 风控管理模块单元测试

覆盖 8 个端点 / 2 个业务域:
  - 返利结算(5):  rebate/calc / rebates(list) / rebate/withdraw / rebate/summary / rebate/tiers
  - 风控管理(3):  risk/report / risk/alerts / risk/assess

测试维度:
  - 成功路径 / 错误路径(404/409/422)
  - 权限守卫(X-Agent-Id 缺失→401, 越权→403, X-Role:admin 缺失→403)
  - 业务规则(超额累进返利计算/信用评分/窜货预警/返利提现)
  - 边界场景(分页/筛选/档位边界/重复提现)
  - 状态持久化(store 回查)

初始数据(reset_store 后):
  - agent 1: level=C, wallet=50000, region="山东省泰安市"(无 sales_region)
  - agent 2: level=B, wallet=120000, region="山东省济南市", sales_region="山东省德州市"(窜货)
  - 返利记录: RB20260701001(agent 1, 2026-07, T1, 300000→15000, pending)
  - 风控记录: RK20260701001(agent 1, 信用分 60, medium)

运行: pytest test_agent_rebate_risk.py -v
"""

import pytest
from fastapi.testclient import TestClient

from main import app, _mock_store
from repositories.store import reset_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_agent_store():
    """每个测试前重置 store 到初始状态"""
    reset_store()
    yield


# 初始返利记录 ID(与 store.py 种子数据一致)
SEED_REBATE_ID = "RB20260701001"


# ============================================================
#  返利档位说明: GET /api/agent/rebate/tiers
# ============================================================

class TestRebateTiers:
    def test_tiers_success(self):
        """档位说明: 4 档 T0-T3, 超额累进制"""
        resp = client.get("/api/agent/rebate/tiers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["tiers"]) == 4
        by_tier = {t["tier"]: t for t in data["tiers"]}
        # T0: 0-20万, 0%
        assert by_tier["T0"]["rate"] == 0.0
        assert by_tier["T0"]["min"] == 0
        # T1: 20-50万, 15%
        assert by_tier["T1"]["rate"] == 0.15
        assert by_tier["T1"]["min"] == 200000
        # T2: 50-100万, 25%
        assert by_tier["T2"]["rate"] == 0.25
        assert by_tier["T2"]["min"] == 500000
        # T3: 100万以上, 30%
        assert by_tier["T3"]["rate"] == 0.30
        assert by_tier["T3"]["min"] == 1000000
        assert by_tier["T3"]["max"] is None  # 无上限
        assert "超额累进" in data["calcMode"]

    def test_tiers_static_path_not_shadowed(self):
        """静态路径 /api/agent/rebate/tiers 不被 /{agent_id} 参数路由吞掉"""
        resp = client.get("/api/agent/rebate/tiers")
        assert resp.status_code == 200


# ============================================================
#  返利计算: POST /api/agent/{agent_id}/rebate/calc
# ============================================================

class TestRebateCalc:
    def test_calc_t1_success(self):
        """T1 档: 30万进货额 → 返利 15000(20-30万部分 × 15%)"""
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 300000, "period": "2026-08",
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["tier"] == "T1"
        assert data["rebateRate"] == 0.15
        assert data["rebateAmount"] == 15000.0
        assert data["status"] == "pending"
        assert data["rebateId"].startswith("RB")
        assert data["period"] == "2026-08"

    def test_calc_t2_progressive(self):
        """T2 档超额累进: 60万 → 70000
        0-20万: 0% + 20-50万: 15%(45000) + 50-60万: 25%(25000) = 70000
        """
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 600000, "period": "2026-08",
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "T2"
        assert data["rebateRate"] == 0.25
        assert data["rebateAmount"] == 70000.0

    def test_calc_t3_progressive(self):
        """T3 档超额累进: 120万 → 230000
        0-20万: 0 + 20-50万: 45000 + 50-100万: 125000 + 100-120万: 60000 = 230000
        """
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 1200000, "period": "2026-08",
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "T3"
        assert data["rebateRate"] == 0.30
        assert data["rebateAmount"] == 230000.0

    def test_calc_t0_below_threshold(self):
        """T0 档: 15万(未达 20万门槛) → 返利 0"""
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 150000, "period": "2026-08",
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "T0"
        assert data["rebateRate"] == 0.0
        assert data["rebateAmount"] == 0.0
        # 日志包含未达门槛警告
        assert any(l["step"] == "未达门槛" for l in data["logs"])

    def test_calc_boundary_20w(self):
        """边界值 20万: 归入 T1(20万门槛达 T1), 返利 0(20-20万部分为 0)"""
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 200000, "period": "2026-08",
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "T1"
        assert data["rebateAmount"] == 0.0

    def test_calc_boundary_50w(self):
        """边界值 50万: 归入 T2, 返利 45000(20-50万 × 15%)"""
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 500000, "period": "2026-08",
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "T2"
        assert data["rebateAmount"] == 45000.0

    def test_calc_boundary_100w(self):
        """边界值 100万: 归入 T3, 返利 170000(45000+125000)"""
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 1000000, "period": "2026-08",
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "T3"
        assert data["rebateAmount"] == 170000.0

    def test_calc_persisted(self):
        """返利记录持久化到 store"""
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 600000, "period": "2026-08",
        }, headers={"X-Agent-Id": "1"})
        rebate_id = resp.json()["rebateId"]
        assert rebate_id in _mock_store["agent_rebates"]
        assert _mock_store["agent_rebates"][rebate_id]["tier"] == "T2"
        assert _mock_store["agent_rebates"][rebate_id]["rebateAmount"] == 70000.0

    def test_calc_default_period(self):
        """不传 period: 默认当前月(YYYY-MM 格式)"""
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 300000,
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        period = resp.json()["period"]
        assert len(period) == 7  # YYYY-MM
        assert period[4] == "-"

    def test_calc_no_auth(self):
        """未登录: 401"""
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 300000,
        })
        assert resp.status_code == 401

    def test_calc_other_agent_forbidden(self):
        """越权计算他人返利: 403"""
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 300000,
        }, headers={"X-Agent-Id": "2"})
        assert resp.status_code == 403

    def test_calc_agent_not_found(self):
        """代理商不存在: 404"""
        resp = client.post("/api/agent/999/rebate/calc", json={
            "purchaseAmount": 300000,
        }, headers={"X-Agent-Id": "999"})
        assert resp.status_code == 404

    def test_calc_negative_amount(self):
        """负数进货额: 422(ge=0)"""
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": -100,
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 422

    def test_calc_missing_amount(self):
        """缺 purchaseAmount: 422"""
        resp = client.post("/api/agent/1/rebate/calc", json={},
                          headers={"X-Agent-Id": "1"})
        assert resp.status_code == 422


# ============================================================
#  返利记录列表: GET /api/agent/{agent_id}/rebates
# ============================================================

class TestRebatesList:
    def test_rebates_list_with_seed(self):
        """列表含种子数据: 1 条(agent 1, 2026-07, pending)"""
        resp = client.get("/api/agent/1/rebates",
                          headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["rebates"][0]["rebateId"] == SEED_REBATE_ID
        assert data["rebates"][0]["tier"] == "T1"
        assert data["rebates"][0]["status"] == "pending"

    def test_rebates_filter_status_pending(self):
        """状态筛选 pending → 1 条"""
        resp = client.get("/api/agent/1/rebates?status=pending",
                          headers={"X-Agent-Id": "1"})
        assert resp.json()["count"] == 1

    def test_rebates_filter_status_withdrawn(self):
        """状态筛选 withdrawn → 0 条"""
        resp = client.get("/api/agent/1/rebates?status=withdrawn",
                          headers={"X-Agent-Id": "1"})
        assert resp.json()["count"] == 0
        assert resp.json()["rebates"] == []

    def test_rebates_pagination(self):
        """分页: 进 1 次 + 种子 1 条 = 2 条, page_size=1 → 第 1 页 1 条"""
        client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": 300000, "period": "2026-08",
        }, headers={"X-Agent-Id": "1"})
        resp = client.get("/api/agent/1/rebates?page=1&page_size=1",
                          headers={"X-Agent-Id": "1"})
        data = resp.json()
        assert data["count"] == 2
        assert len(data["rebates"]) == 1

    def test_rebates_no_auth(self):
        """未登录: 401"""
        resp = client.get("/api/agent/1/rebates")
        assert resp.status_code == 401

    def test_rebates_other_agent_forbidden(self):
        """越权查询他人返利: 403"""
        resp = client.get("/api/agent/1/rebates",
                          headers={"X-Agent-Id": "2"})
        assert resp.status_code == 403

    def test_rebates_agent_not_found(self):
        """代理商不存在: 404"""
        resp = client.get("/api/agent/999/rebates",
                          headers={"X-Agent-Id": "999"})
        assert resp.status_code == 404


# ============================================================
#  返利提现: POST /api/agent/{agent_id}/rebate/withdraw
# ============================================================

class TestRebateWithdraw:
    def test_withdraw_success(self):
        """提现成功: pending→withdrawn, 钱包增加
        初始 wallet=50000, 返利 15000 → wallet=65000
        """
        resp = client.post("/api/agent/1/rebate/withdraw", json={
            "rebateId": SEED_REBATE_ID,
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["amount"] == 15000.0
        assert data["wallet"] == 65000.0
        assert data["status"] == "withdrawn"
        # 持久化: 状态已更新
        assert _mock_store["agent_rebates"][SEED_REBATE_ID]["status"] == "withdrawn"
        assert _mock_store["agents"][1]["wallet"] == 65000.0

    def test_withdraw_already_withdrawn(self):
        """重复提现: 409"""
        client.post("/api/agent/1/rebate/withdraw", json={
            "rebateId": SEED_REBATE_ID,
        }, headers={"X-Agent-Id": "1"})
        resp = client.post("/api/agent/1/rebate/withdraw", json={
            "rebateId": SEED_REBATE_ID,
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 409
        assert "已提现" in resp.json()["error"]

    def test_withdraw_not_found(self):
        """返利记录不存在: 404"""
        resp = client.post("/api/agent/1/rebate/withdraw", json={
            "rebateId": "RB_NO_SUCH",
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 404

    def test_withdraw_wrong_agent(self):
        """返利记录不属于该代理商: 404
        SEED_REBATE_ID 属于 agent 1, 用 agent 2 提现 → 404
        """
        resp = client.post("/api/agent/2/rebate/withdraw", json={
            "rebateId": SEED_REBATE_ID,
        }, headers={"X-Agent-Id": "2"})
        assert resp.status_code == 404

    def test_withdraw_no_auth(self):
        """未登录: 401"""
        resp = client.post("/api/agent/1/rebate/withdraw", json={
            "rebateId": SEED_REBATE_ID,
        })
        assert resp.status_code == 401

    def test_withdraw_other_agent_forbidden(self):
        """越权提现他人返利: 403"""
        resp = client.post("/api/agent/1/rebate/withdraw", json={
            "rebateId": SEED_REBATE_ID,
        }, headers={"X-Agent-Id": "2"})
        assert resp.status_code == 403

    def test_withdraw_missing_rebate_id(self):
        """缺 rebateId: 422"""
        resp = client.post("/api/agent/1/rebate/withdraw", json={},
                          headers={"X-Agent-Id": "1"})
        assert resp.status_code == 422


# ============================================================
#  返利汇总: GET /api/agent/{agent_id}/rebate/summary
# ============================================================

class TestRebateSummary:
    def test_summary_success(self):
        """汇总: 种子数据 1 条(2026-07, pending, 15000)
        本年累计=15000, 可提现=15000, 已提现=0
        """
        resp = client.get("/api/agent/1/rebate/summary",
                          headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["yearTotal"] == 15000.0
        assert data["withdrawable"] == 15000.0
        assert data["withdrawnTotal"] == 0.0
        assert data["totalCount"] == 1

    def test_summary_after_withdraw(self):
        """提现后汇总: withdrawable=0, withdrawnTotal=15000"""
        client.post("/api/agent/1/rebate/withdraw", json={
            "rebateId": SEED_REBATE_ID,
        }, headers={"X-Agent-Id": "1"})
        resp = client.get("/api/agent/1/rebate/summary",
                          headers={"X-Agent-Id": "1"})
        data = resp.json()
        assert data["withdrawable"] == 0.0
        assert data["withdrawnTotal"] == 15000.0

    def test_summary_no_auth(self):
        """未登录: 401"""
        resp = client.get("/api/agent/1/rebate/summary")
        assert resp.status_code == 401

    def test_summary_other_agent_forbidden(self):
        """越权查询他人汇总: 403"""
        resp = client.get("/api/agent/1/rebate/summary",
                          headers={"X-Agent-Id": "2"})
        assert resp.status_code == 403

    def test_summary_agent_not_found(self):
        """代理商不存在: 404"""
        resp = client.get("/api/agent/999/rebate/summary",
                          headers={"X-Agent-Id": "999"})
        assert resp.status_code == 404


# ============================================================
#  风控报告: GET /api/agent/{agent_id}/risk/report
# ============================================================

class TestRiskReport:
    def test_report_success(self):
        """风控报告: 含信用评分 + 指标(种子数据 agent 1, 信用分 60, medium)"""
        resp = client.get("/api/agent/1/risk/report",
                          headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["creditScore"] == 60.0
        assert data["riskLevel"] == "medium"
        assert "indicators" in data
        assert data["indicators"]["purchaseCount"] == 0
        assert data["latestAssessmentId"] == "RK20260701001"

    def test_report_no_alert_agent1(self):
        """agent 1 无窜货预警(无 sales_region)"""
        resp = client.get("/api/agent/1/risk/report",
                          headers={"X-Agent-Id": "1"})
        alerts = resp.json()["alerts"]
        assert alerts == []

    def test_report_with_cross_region_alert(self):
        """agent 2 有窜货预警(sales_region ≠ region)"""
        resp = client.get("/api/agent/2/risk/report",
                          headers={"X-Agent-Id": "2"})
        assert resp.status_code == 200
        data = resp.json()
        # agent 2 无评级记录 → 实时计算(信用分 60, medium)
        assert data["creditScore"] == 60.0
        assert data["riskLevel"] == "medium"
        # 检测到窜货预警
        assert len(data["alerts"]) == 1
        alert = data["alerts"][0]
        assert alert["type"] == "cross_region"
        assert alert["level"] == "high"
        assert alert["authorizedRegion"] == "山东省济南市"
        assert alert["detectedRegion"] == "山东省德州市"

    def test_report_no_auth(self):
        """未登录: 401"""
        resp = client.get("/api/agent/1/risk/report")
        assert resp.status_code == 401

    def test_report_other_agent_forbidden(self):
        """越权查询他人风控报告: 403"""
        resp = client.get("/api/agent/1/risk/report",
                          headers={"X-Agent-Id": "2"})
        assert resp.status_code == 403

    def test_report_agent_not_found(self):
        """代理商不存在: 404"""
        resp = client.get("/api/agent/999/risk/report",
                          headers={"X-Agent-Id": "999"})
        assert resp.status_code == 404


# ============================================================
#  窜货预警列表: GET /api/agent/risk/alerts
# ============================================================

class TestRiskAlerts:
    def test_alerts_success(self):
        """预警列表: agent 2 有窜货预警"""
        resp = client.get("/api/agent/risk/alerts",
                          headers={"X-Role": "admin"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["count"] == 1
        alert = data["alerts"][0]
        assert alert["agentId"] == 2
        assert alert["agentName"] == "济南核心代理商"
        assert alert["type"] == "cross_region"
        assert alert["authorizedRegion"] == "山东省济南市"
        assert alert["detectedRegion"] == "山东省德州市"

    def test_alerts_no_admin(self):
        """非 admin: 403"""
        resp = client.get("/api/agent/risk/alerts")
        assert resp.status_code == 403

    def test_alerts_static_path_not_shadowed(self):
        """静态路径 /api/agent/risk/alerts 不被 /{agent_id} 参数路由吞掉"""
        resp = client.get("/api/agent/risk/alerts",
                          headers={"X-Role": "admin"})
        assert resp.status_code == 200

    def test_alerts_empty_when_no_mismatch(self):
        """无窜货时: count=0(移除 agent 2 的 sales_region)"""
        _mock_store["agents"][2].pop("sales_region", None)
        resp = client.get("/api/agent/risk/alerts",
                          headers={"X-Role": "admin"})
        assert resp.json()["count"] == 0
        assert resp.json()["alerts"] == []


# ============================================================
#  信用评级: POST /api/agent/{agent_id}/risk/assess
# ============================================================

class TestRiskAssess:
    def test_assess_success(self):
        """信用评级成功: agent 1 无进货 → 信用分 60, medium"""
        resp = client.post("/api/agent/1/risk/assess",
                            headers={"X-Role": "admin"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["creditScore"] == 60.0
        assert data["riskLevel"] == "medium"
        assert data["riskLevelName"] == "中风险"
        assert data["riskId"].startswith("RK")
        assert data["indicators"]["purchaseCount"] == 0
        assert data["indicators"]["totalPurchases"] == 0.0
        # 持久化: 评级记录已保存
        assert data["riskId"] in _mock_store["agent_risks"]
        assert _mock_store["agent_risks"][data["riskId"]]["type"] == "assessment"

    def test_assess_with_purchase(self):
        """有进货记录: 信用分提升
        agent 1 进货 1 次(268×0.75=201) → 信用分 60 + 2 + 0.00201 ≈ 62.0
        """
        # 先进货
        client.post("/api/agent/1/purchase", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 1}],
        }, headers={"X-Agent-Id": "1"})
        # 再评级
        resp = client.post("/api/agent/1/risk/assess",
                           headers={"X-Role": "admin"})
        data = resp.json()
        assert data["creditScore"] == 62.0
        assert data["indicators"]["purchaseCount"] == 1
        assert data["indicators"]["totalPurchases"] == 201.0

    def test_assess_cross_region_alert(self):
        """agent 2 评级含窜货预警"""
        resp = client.post("/api/agent/2/risk/assess",
                           headers={"X-Role": "admin"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["alerts"]) == 1
        assert data["alerts"][0]["type"] == "cross_region"
        # 日志含窜货预警
        assert any(l["step"] == "窜货预警" for l in data["logs"])

    def test_assess_no_admin(self):
        """非 admin: 403"""
        resp = client.post("/api/agent/1/risk/assess")
        assert resp.status_code == 403

    def test_assess_agent_not_found(self):
        """代理商不存在: 404"""
        resp = client.post("/api/agent/999/risk/assess",
                           headers={"X-Role": "admin"})
        assert resp.status_code == 404

    def test_assess_updates_report(self):
        """评级后风控报告使用最新评级"""
        # 先评级(信用分 60)
        client.post("/api/agent/1/risk/assess",
                    headers={"X-Role": "admin"})
        # 进货
        client.post("/api/agent/1/purchase", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 1}],
        }, headers={"X-Agent-Id": "1"})
        # 再次评级(信用分 62)
        assess_resp = client.post("/api/agent/1/risk/assess",
                                   headers={"X-Role": "admin"})
        assert assess_resp.json()["creditScore"] == 62.0
        # 风控报告使用最新评级
        report_resp = client.get("/api/agent/1/risk/report",
                                 headers={"X-Agent-Id": "1"})
        assert report_resp.json()["creditScore"] == 62.0


# ============================================================
#  返利超额累进计算逻辑验证(对照任务示例)
# ============================================================

class TestRebateProgressiveCalc:
    """超额累进计算逻辑验证(对照任务文档示例)"""

    def _calc(self, amount):
        """调用返利计算并返回结果"""
        resp = client.post("/api/agent/1/rebate/calc", json={
            "purchaseAmount": amount, "period": "2026-08",
        }, headers={"X-Agent-Id": "1"})
        data = resp.json()
        return data["rebateAmount"], data["tier"]

    def test_example_60w(self):
        """示例验证: 60万 → T2, 返利 70000
        0-20万: 0% + 20-50万: 15%(45000) + 50-60万: 25%(25000) = 70000
        """
        rebate, tier = self._calc(600000)
        assert tier == "T2"
        assert rebate == 70000.0

    def test_example_120w(self):
        """示例验证: 120万 → T3, 返利 230000
        0-20万: 0 + 20-50万: 45000 + 50-100万: 125000 + 100-120万: 60000 = 230000
        """
        rebate, tier = self._calc(1200000)
        assert tier == "T3"
        assert rebate == 230000.0

    def test_progressive_not_flat_rate(self):
        """验证超额累进(非总额×税率):
        60万若按 T2 总额 25% = 150000, 但实际累进 = 70000
        """
        rebate, _ = self._calc(600000)
        assert rebate == 70000.0
        assert rebate != 600000 * 0.25  # 非简单总额×税率

    def test_t0_zero_rebate(self):
        """T0 档(15万): 返利 0"""
        rebate, tier = self._calc(150000)
        assert tier == "T0"
        assert rebate == 0.0

    def test_t1_exact(self):
        """T1 档(30万): 20-30万部分 × 15% = 15000"""
        rebate, tier = self._calc(300000)
        assert tier == "T1"
        assert rebate == 15000.0

    def test_t2_exact(self):
        """T2 档(80万): 20-50万(45000) + 50-80万(75000) = 120000"""
        rebate, tier = self._calc(800000)
        assert tier == "T2"
        assert rebate == 120000.0

    def test_t3_exact(self):
        """T3 档(150万): 45000+125000+150000 = 320000"""
        rebate, tier = self._calc(1500000)
        assert tier == "T3"
        assert rebate == 320000.0

    def test_zero_amount(self):
        """进货额 0: T0, 返利 0"""
        rebate, tier = self._calc(0)
        assert tier == "T0"
        assert rebate == 0.0
