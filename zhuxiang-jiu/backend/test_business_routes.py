"""
跨模块业务路由单元测试
覆盖 5 个路由组 / 12 个端点:
  - /api/agent/upgrade|downgrade       (代理商服务)
  - /api/checkout/submit                (交易服务)
  - /api/inventory/deduct|restock       (供应链服务)
  - /api/warehouse/inbound|outbound|stocktake|slot-optimize|forecast (仓储服务)
  - /api/agent-shipping/claim|claims    (代理商区域认领)

运行: pytest test_business_routes.py -v
"""

import pytest
from fastapi.testclient import TestClient

from main import app, _mock_store

client = TestClient(app)


# ============================================================
#  代理商服务: /api/agent/upgrade
# ============================================================

class TestAgentUpgrade:
    """代理商升级端点测试"""

    def test_upgrade_success(self):
        """正常升级: D→C, 充值 ¥5000"""
        response = client.post("/api/agent/upgrade", json={
            "agentId": 1,
            "fromLevel": "C",
            "toLevel": "B",
            "payAmount": 5000,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["agentId"] == 1
        assert data["fromLevel"] == "C"
        assert data["toLevel"] == "B"
        assert data["wallet"] >= 5000
        assert len(data["logs"]) == 2
        assert "C→B" in data["logs"][0]["msg"]

    def test_upgrade_agent_not_found(self):
        """代理商不存在: 404"""
        response = client.post("/api/agent/upgrade", json={
            "agentId": 999,
            "fromLevel": "D",
            "toLevel": "C",
            "payAmount": 0,
        })
        assert response.status_code == 404
        assert "999" in response.json()["error"]

    def test_upgrade_zero_amount(self):
        """零金额升级(仅等级变更)"""
        response = client.post("/api/agent/upgrade", json={
            "agentId": 2,
            "fromLevel": "B",
            "toLevel": "A",
            "payAmount": 0,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_upgrade_response_fields(self):
        """验证响应字段完整性"""
        response = client.post("/api/agent/upgrade", json={
            "agentId": 1,
            "fromLevel": "C",
            "toLevel": "B",
            "payAmount": 100,
        })
        data = response.json()
        required = {"success", "agentId", "fromLevel", "toLevel", "wallet", "logs"}
        assert required.issubset(data.keys())
        for log in data["logs"]:
            assert "step" in log
            assert "level" in log
            assert "msg" in log

    def test_upgrade_negative_amount_blocked(self):
        """负金额应被 Pydantic 拦截(防止钱包被恶意扣减)"""
        # 先记录当前钱包余额和等级(用于副作用验证)
        _mock_store["agents"][1]["wallet"] = 1000
        _mock_store["agents"][1]["level"] = "D"
        response = client.post("/api/agent/upgrade", json={
            "agentId": 1,
            "fromLevel": "D",
            "toLevel": "C",
            "payAmount": -500,
        })
        # 1. HTTP 状态码:422 Unprocessable Entity(Pydantic 校验失败)
        assert response.status_code == 422
        # 2. 响应体为 FastAPI 标准 422 格式 {"detail": [...]}
        body = response.json()
        assert "detail" in body
        assert isinstance(body["detail"], list)
        assert len(body["detail"]) > 0
        # 3. 错误应定位到 payAmount 字段(ge=0 约束触发)
        detail_str = str(body["detail"])
        assert "payAmount" in detail_str
        # 4. 错误信息应体现非负约束(ge=0,greater_than_equal)
        assert "greater" in detail_str.lower()
        # 5. 副作用验证:钱包不应被扣减
        assert _mock_store["agents"][1]["wallet"] == 1000
        # 6. 副作用验证:等级不应被修改
        assert _mock_store["agents"][1]["level"] == "D"
        # 7. Content-Type 应为 JSON
        assert "application/json" in response.headers.get("content-type", "")


# ============================================================
#  代理商服务: /api/agent/downgrade
# ============================================================

class TestAgentDowngrade:
    """代理商降级端点测试"""

    def test_downgrade_success(self):
        """正常降级: B→C"""
        # 重置 agent 2 为 B 级,避免测试间状态污染
        _mock_store["agents"][2]["level"] = "B"
        response = client.post("/api/agent/downgrade", json={
            "agentId": 2,
            "fromLevel": "B",
            "reason": "考核未达标",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["fromLevel"] == "B"
        assert data["toLevel"] == "C"
        assert data["reason"] == "考核未达标"

    def test_downgrade_agent_not_found(self):
        """代理商不存在: 404"""
        response = client.post("/api/agent/downgrade", json={
            "agentId": 999,
            "fromLevel": "B",
            "reason": "test",
        })
        assert response.status_code == 404

    def test_downgrade_bottom_level(self):
        """D 级降级: 保持 D(最低级)"""
        # 重置 agent 1 为 D 级
        _mock_store["agents"][1]["level"] = "D"
        response = client.post("/api/agent/downgrade", json={
            "agentId": 1,
            "fromLevel": "D",
            "reason": "连续3月不达标",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["toLevel"] == "D"

    def test_downgrade_log_format(self):
        """日志格式验证"""
        response = client.post("/api/agent/downgrade", json={
            "agentId": 1,
            "fromLevel": "C",
            "reason": "test reason",
        })
        data = response.json()
        log = data["logs"][0]
        assert log["level"] == "WARN"
        assert "test reason" in log["msg"]

    def test_downgrade_top_level_S_to_A(self):
        """S 级降级: S→A(顶级降级分支)"""
        # 先把 agent 2 设为 S 级
        _mock_store["agents"][2]["level"] = "S"
        response = client.post("/api/agent/downgrade", json={
            "agentId": 2,
            "fromLevel": "S",
            "reason": "违规操作",
        })
        assert response.status_code == 200
        data = response.json()
        # 1. 核心字段断言:成功 + 等级变化
        assert data["success"] is True
        assert data["fromLevel"] == "S"
        assert data["toLevel"] == "A"  # 顶级降级分支
        # 2. agentId 与 reason 字段应原样回显
        assert data["agentId"] == 2
        assert data["reason"] == "违规操作"
        # 3. 日志格式验证:应有 step/level/msg 三字段
        assert len(data["logs"]) == 1
        log = data["logs"][0]
        assert log["step"] == "降级"
        assert log["level"] == "WARN"
        # 4. 日志消息应包含等级变化和原因
        assert "S→A" in log["msg"]
        assert "违规操作" in log["msg"]
        # 5. 验证 store 中等级已同步更新
        assert _mock_store["agents"][2]["level"] == "A"


# ============================================================
#  交易服务: /api/checkout/submit
# ============================================================

class TestCheckoutSubmit:
    """订单结算提交端点测试"""

    def test_submit_success(self):
        """正常下单"""
        response = client.post("/api/checkout/submit", json={
            "items": [
                {"productId": "ZX42-2026L07", "quantity": 2, "price": 599},
            ],
            "consignee": {"name": "张三", "phone": "13800001111", "address": "济南市"},
            "payment": {"method": "wechat", "amount": 1198},
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["orderId"].startswith("ZX")
        assert data["status"] == "pending"
        assert "创建成功" in data["message"]

    def test_submit_empty_items(self):
        """空订单(允许创建,前端应拦截)"""
        response = client.post("/api/checkout/submit", json={
            "items": [],
            "consignee": None,
            "payment": None,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_submit_order_id_format(self):
        """订单号格式验证: ZX + 6位数字"""
        response = client.post("/api/checkout/submit", json={"items": []})
        data = response.json()
        order_id = data["orderId"]
        assert order_id.startswith("ZX")
        assert len(order_id) == 8  # ZX + 6 digits

    def test_submit_order_stored(self):
        """订单应存入 mock_store"""
        before = len(_mock_store["orders"])
        client.post("/api/checkout/submit", json={"items": [{"test": True}]})
        after = len(_mock_store["orders"])
        assert after == before + 1


# ============================================================
#  供应链服务: /api/inventory/deduct
# ============================================================

class TestInventoryDeduct:
    """库存扣减端点测试"""

    def test_deduct_success(self):
        """正常扣减: 2 件"""
        # 先确保有库存
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 500
        response = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07",
            "quantity": 2,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["stockAfter"] == 498
        assert data["txId"].startswith("TX")

    def test_deduct_insufficient_stock(self):
        """库存不足: 返回 success=False"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 5
        response = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07",
            "quantity": 10,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "库存不足" in data["error"]

    def test_deduct_product_not_found(self):
        """产品不存在: 404"""
        response = client.post("/api/inventory/deduct", json={
            "productId": "NOT-EXIST",
            "quantity": 1,
        })
        assert response.status_code == 404

    def test_deduct_exact_stock(self):
        """扣减数=库存数(刚好清零)"""
        _mock_store["inventory"]["ZX42-2026L05"]["stock"] = 10
        response = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L05",
            "quantity": 10,
        })
        data = response.json()
        assert data["success"] is True
        assert data["stockAfter"] == 0

    def test_deduct_zero_quantity(self):
        """扣减 0 件(应成功,库存不变)"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 100
        response = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07",
            "quantity": 0,
        })
        data = response.json()
        assert data["success"] is True
        assert data["stockAfter"] == 100

    def test_deduct_negative_quantity_blocked(self):
        """扣减负数: Pydantic 校验拦截(ge=0,与 restock 对称)"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 100
        response = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07",
            "quantity": -10,
        })
        # 1. HTTP 状态码:422 Unprocessable Entity(Pydantic 校验失败)
        assert response.status_code == 422
        # 2. 响应体为 FastAPI 标准 422 格式 {"detail": [...]}
        body = response.json()
        assert "detail" in body
        assert isinstance(body["detail"], list)
        assert len(body["detail"]) > 0
        # 3. 错误应定位到 quantity 字段(ge=0 约束触发)
        detail_str = str(body["detail"])
        assert "quantity" in detail_str
        # 4. 错误信息应体现非负约束(ge=0,greater_than_equal)
        assert "greater" in detail_str.lower()
        # 5. 副作用验证:库存不应被修改
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 100
        # 6. Content-Type 应为 JSON
        assert "application/json" in response.headers.get("content-type", "")


# ============================================================
#  供应链服务: /api/inventory/restock
# ============================================================

class TestInventoryRestock:
    """库存回补端点测试"""

    def test_restock_success(self):
        """正常回补: +50"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 100
        response = client.post("/api/inventory/restock", json={
            "productId": "ZX42-2026L07",
            "quantity": 50,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["stockAfter"] == 150
        assert data["txId"].startswith("TX")

    def test_restock_product_not_found(self):
        """产品不存在: 404"""
        response = client.post("/api/inventory/restock", json={
            "productId": "NOT-EXIST",
            "quantity": 10,
        })
        assert response.status_code == 404

    def test_restock_zero(self):
        """回补 0 件(库存不变)"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 50
        response = client.post("/api/inventory/restock", json={
            "productId": "ZX42-2026L07",
            "quantity": 0,
        })
        data = response.json()
        assert data["success"] is True
        assert data["stockAfter"] == 50

    def test_restock_negative_blocked(self):
        """回补负数: Pydantic 校验拦截(ge=0)"""
        response = client.post("/api/inventory/restock", json={
            "productId": "ZX42-2026L07",
            "quantity": -10,
        })
        assert response.status_code == 422  # Pydantic Field(ge=0) validation


# ============================================================
#  仓储服务: /api/warehouse/inbound
# ============================================================

class TestWarehouseInbound:
    """AI智能入库端点测试"""

    def test_inbound_success(self):
        response = client.post("/api/warehouse/inbound", json={
            "warehouseId": "WH001",
            "productId": "ZX42-2026L07",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["slot"] == "A1"
        assert "视觉验货" in data["message"]

    def test_inbound_logs_stored(self):
        before = len(_mock_store["warehouse"]["inbound_log"])
        client.post("/api/warehouse/inbound", json={"productId": "ZX42-2026L05"})
        after = len(_mock_store["warehouse"]["inbound_log"])
        assert after == before + 1


# ============================================================
#  仓储服务: /api/warehouse/outbound
# ============================================================

class TestWarehouseOutbound:
    """AI智能出库端点测试"""

    def test_outbound_success(self):
        response = client.post("/api/warehouse/outbound", json={
            "warehouseId": "WH001",
            "productId": "ZX42-2026L07",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "波次拣选" in data["message"]

    def test_outbound_logs_stored(self):
        before = len(_mock_store["warehouse"]["outbound_log"])
        client.post("/api/warehouse/outbound", json={"productId": "ZX42-2026L05"})
        after = len(_mock_store["warehouse"]["outbound_log"])
        assert after == before + 1


# ============================================================
#  仓储服务: /api/warehouse/stocktake
# ============================================================

class TestWarehouseStocktake:
    """AI智能盘点端点测试"""

    def test_stocktake_success(self):
        response = client.post("/api/warehouse/stocktake", json={
            "warehouseId": "WH001",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["totalSlots"] >= 0
        assert data["occupiedSlots"] >= 0
        assert data["emptySlots"] == data["totalSlots"] - data["occupiedSlots"]
        assert data["accuracy"] == 0.98

    def test_stocktake_empty_request(self):
        """空请求体(字段都有默认值)"""
        response = client.post("/api/warehouse/stocktake", json={})
        assert response.status_code == 200
        assert response.json()["success"] is True


# ============================================================
#  仓储服务: /api/warehouse/slot-optimize
# ============================================================

class TestWarehouseSlotOptimize:
    """AI智能库位优化端点测试"""

    def test_slot_optimize_success(self):
        response = client.post("/api/warehouse/slot-optimize", json={
            "warehouseId": "WH001",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["optimized"] is True
        assert data["utilizationAfter"] > data["utilizationBefore"]
        assert data["improvement"] == "30%"


# ============================================================
#  仓储服务: GET /api/warehouse/forecast
# ============================================================

class TestWarehouseForecast:
    """AI智能库存预测端点测试"""

    def test_forecast_default(self):
        """无 productId: 返回默认产品预测"""
        response = client.get("/api/warehouse/forecast")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["productId"] == "ZX42-2026L07"
        assert len(data["forecast7d"]) == 7
        assert data["accuracy"] == 0.89

    def test_forecast_with_product(self):
        """指定 productId"""
        response = client.get("/api/warehouse/forecast?productId=ZX42-2026L05")
        data = response.json()
        assert data["productId"] == "ZX42-2026L05"

    def test_forecast_data_validity(self):
        """预测数据有效性: 7 天,数值为正"""
        response = client.get("/api/warehouse/forecast")
        forecast = response.json()["forecast7d"]
        assert len(forecast) == 7
        for val in forecast:
            assert val > 0


# ============================================================
#  代理商服务: POST /api/agent-shipping/claim
# ============================================================

class TestAgentShippingClaim:
    """代理商区域认领端点测试"""

    def test_claim_success(self):
        response = client.post("/api/agent-shipping/claim", json={
            "agentId": 1,
            "region": "taian",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["agentId"] == 1
        assert data["region"] == "taian"
        assert "已认领" in data["message"]

    def test_claim_agent_not_found(self):
        response = client.post("/api/agent-shipping/claim", json={
            "agentId": 999,
            "region": "jinan",
        })
        assert response.status_code == 404

    def test_claim_region_conflict(self):
        """区域已被认领: 409"""
        _mock_store["shipping_claims"]["test_region"] = 1
        response = client.post("/api/agent-shipping/claim", json={
            "agentId": 2,
            "region": "test_region",
        })
        assert response.status_code == 409
        assert "test_region" in response.json()["error"]

    def test_claim_same_agent_reclaim(self):
        """同一代理商重复认领: 允许(幂等)"""
        _mock_store["shipping_claims"]["idempotent_region"] = 1
        response = client.post("/api/agent-shipping/claim", json={
            "agentId": 1,
            "region": "idempotent_region",
        })
        assert response.status_code == 200
        assert response.json()["success"] is True


# ============================================================
#  代理商服务: GET /api/agent-shipping/claims
# ============================================================

class TestAgentShippingListClaims:
    """区域认领列表端点测试"""

    def test_list_claims_success(self):
        response = client.get("/api/agent-shipping/claims")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "claims" in data
        assert isinstance(data["claims"], dict)

    def test_list_claims_after_claim(self):
        """认领后列表应包含该记录"""
        client.post("/api/agent-shipping/claim", json={
            "agentId": 2,
            "region": "list_test_region",
        })
        response = client.get("/api/agent-shipping/claims")
        claims = response.json()["claims"]
        assert "list_test_region" in claims
        assert claims["list_test_region"] == 2


# ============================================================
#  跨端点集成测试
# ============================================================

class TestIntegration:
    """跨端点集成场景测试"""

    def test_deduct_then_restock_balance(self):
        """扣减再回补: 库存恢复"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 100
        # 扣减 30
        r1 = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07", "quantity": 30,
        })
        assert r1.json()["stockAfter"] == 70
        # 回补 30
        r2 = client.post("/api/inventory/restock", json={
            "productId": "ZX42-2026L07", "quantity": 30,
        })
        assert r2.json()["stockAfter"] == 100

    def test_upgrade_then_downgrade(self):
        """升级再降级: 等级往返"""
        # 升级 D→C
        r1 = client.post("/api/agent/upgrade", json={
            "agentId": 1, "fromLevel": "D", "toLevel": "C", "payAmount": 0,
        })
        assert r1.json()["toLevel"] == "C"
        # 降级 C→D
        r2 = client.post("/api/agent/downgrade", json={
            "agentId": 1, "fromLevel": "C", "reason": "test",
        })
        assert r2.json()["toLevel"] == "D"

    def test_warehouse_full_flow(self):
        """仓储全流程: 入库→盘点→出库→预测"""
        # 入库
        r1 = client.post("/api/warehouse/inbound", json={"productId": "ZX42-2026L07"})
        assert r1.json()["success"] is True
        # 盘点
        r2 = client.post("/api/warehouse/stocktake", json={})
        assert r2.json()["success"] is True
        # 出库
        r3 = client.post("/api/warehouse/outbound", json={"productId": "ZX42-2026L07"})
        assert r3.json()["success"] is True
        # 预测
        r4 = client.get("/api/warehouse/forecast?productId=ZX42-2026L07")
        assert r4.json()["success"] is True

    def test_upgrade_persistence_multi_step(self):
        """连续升级持久化: D→C→B→A→S,wallet 累积 + 失败注入不污染状态"""
        # 初始化 agent 3: D 级, wallet=0(测试隔离数据)
        _mock_store["agents"][3] = {"level": "D", "wallet": 0}
        steps = [("C", 1000), ("B", 2000), ("A", 3000), ("S", 4000)]
        cumulative_wallet = 0
        for to_lv, pay in steps:
            r = client.post("/api/agent/upgrade", json={
                "agentId": 3,
                "fromLevel": _mock_store["agents"][3]["level"],
                "toLevel": to_lv,
                "payAmount": pay,
            })
            assert r.status_code == 200
            cumulative_wallet += pay
            # 回查 store 验证持久化(模拟 SELECT)
            assert _mock_store["agents"][3]["level"] == to_lv
            assert _mock_store["agents"][3]["wallet"] == cumulative_wallet
        # 失败注入: 负金额被拦截, store 状态不被污染(事务回滚语义)
        r_fail = client.post("/api/agent/upgrade", json={
            "agentId": 3, "fromLevel": "S", "toLevel": "S", "payAmount": -999,
        })
        assert r_fail.status_code == 422
        assert _mock_store["agents"][3]["level"] == "S"
        assert _mock_store["agents"][3]["wallet"] == cumulative_wallet  # 10000

    def test_downgrade_persistence_chain(self):
        """连续降级持久化: S→A→B→C→D 完整降级链 + D 级幂等"""
        _mock_store["agents"][3] = {"level": "S", "wallet": 0}
        current = "S"
        expected_chain = ["A", "B", "C", "D"]
        for expected in expected_chain:
            r = client.post("/api/agent/downgrade", json={
                "agentId": 3, "fromLevel": current, "reason": "考核未达标",
            })
            assert r.status_code == 200
            assert r.json()["toLevel"] == expected
            # 回查 store 验证持久化
            assert _mock_store["agents"][3]["level"] == expected
            current = expected
        # D 级再降级: 幂等(保持 D)
        r2 = client.post("/api/agent/downgrade", json={
            "agentId": 3, "fromLevel": "D", "reason": "test",
        })
        assert r2.json()["toLevel"] == "D"
        assert _mock_store["agents"][3]["level"] == "D"

    def test_deduct_persistence_with_rollback(self):
        """扣减持久化 + 回滚: 成功累积, 失败(负数/不足/清零)不污染库存"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 100
        # 成功扣减 30
        r1 = client.post("/api/inventory/deduct", json={"productId": "ZX42-2026L07", "quantity": 30})
        assert r1.json()["stockAfter"] == 70
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 70  # 持久化
        # 失败1: 负数(422), 库存不变
        r2 = client.post("/api/inventory/deduct", json={"productId": "ZX42-2026L07", "quantity": -5})
        assert r2.status_code == 422
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 70  # 回滚
        # 失败2: 库存不足(success=False), 库存不变
        r3 = client.post("/api/inventory/deduct", json={"productId": "ZX42-2026L07", "quantity": 999})
        assert r3.json()["success"] is False
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 70  # 回滚
        # 成功回补 50
        r4 = client.post("/api/inventory/restock", json={"productId": "ZX42-2026L07", "quantity": 50})
        assert r4.json()["stockAfter"] == 120
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 120  # 持久化
        # 成功扣减到 0
        r5 = client.post("/api/inventory/deduct", json={"productId": "ZX42-2026L07", "quantity": 120})
        assert r5.json()["stockAfter"] == 0
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 0  # 持久化
        # 库存 0 再扣: 不足
        r6 = client.post("/api/inventory/deduct", json={"productId": "ZX42-2026L07", "quantity": 1})
        assert r6.json()["success"] is False
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 0  # 回滚
