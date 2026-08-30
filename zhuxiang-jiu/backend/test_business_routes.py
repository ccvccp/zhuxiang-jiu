"""
跨模块业务路由单元测试
覆盖 5 个路由组 / 12 个端点:
  - /api/agent/upgrade|downgrade       (代理商服务)
  - /api/checkout/submit                (交易服务)
  - /api/inventory/deduct|restock       (供应链服务)
  - /api/warehouse/inbound|outbound|stocktake|slot-optimize|forecast (仓储服务)
  - /api/agent-shipping/claim|claims    (代理商区域认领)

测试维度:
  - 成功路径 / 错误路径 (404/409)
  - 请求校验 422 (缺失字段 / 非法类型 / 负值拦截)
  - 幂等性 (同等级升降级 / 同代理商重复认领)
  - 边界值 (超大金额 / 超大数量 / 空字符串 / 特殊字符)
  - 响应结构一致性 (success 字段 / detail 字段 / ID 格式)
  - 持久化 (store 回查 / 失败回滚不污染状态)
  - 字段透传 (consignee/payment/items/productId 原样存入)
  - 补充测试 (checkout/warehouse 响应结构 / 边界 / 持久化 / 算术一致性)

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
    """订单结算端点测试(P4.4 9 阶段事务契约)"""

    def setup_method(self):
        """每个测试前重置数据, 保证隔离"""
        from repositories.store import reset_store
        reset_store()

    def test_submit_success(self):
        """正常下单: 9 阶段事务全过"""
        response = client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "name": "竹香经典", "price": 599, "qty": 2}],
            "memberLevel": "L3",
            "points": 100,
            "paymentMethod": "wechat",
            "region": "taian",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["orderNo"].startswith("ZX")
        d = data["details"]
        # 价格计算: 1198 → L3 92 折=1102.16 → 满1000减80 → 积分抵1 → 运费0(2瓶免)
        assert d["originalTotal"] == 1198
        assert d["finalAmount"] == round(1198 * 0.92 - 80 - 1.0, 2)
        assert d["pointsUsed"] == 100
        assert d["shipperType"] == "manufacturer"   # 未认领区域 → 厂家直供
        assert data["asyncOps"] == ["order_notify", "blockchain_notarize"]
        # 兼容字段
        assert data["orderId"] == data["orderNo"]
        assert data["status"] == "已付款"

    def test_submit_empty_items_aborts(self):
        """空购物车: preflight 中止(对齐前端契约)"""
        response = client.post("/api/checkout/submit", json={"items": []})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error"] == "购物车为空"
        assert "failedStage" not in data   # 中止未开启事务

    def test_submit_insufficient_stock_rolls_back(self):
        """库存不足: 阶段4失败回滚, 库存不变"""
        response = client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "name": "竹香经典",
                       "price": 599, "qty": 99999}],
        })
        data = response.json()
        assert data["success"] is False
        assert data["failedStage"] == "阶段4-库存扣减"
        assert "库存不足" in data["error"]
        # 库存未被污染
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500

    def test_submit_invalid_coupon_rolls_back(self):
        """无效优惠券: 阶段5失败, 已扣库存恢复"""
        response = client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "name": "竹香经典",
                       "price": 599, "qty": 1}],
            "couponCode": "NO_SUCH_CODE",
        })
        data = response.json()
        assert data["success"] is False
        assert data["failedStage"] == "阶段5-优惠券核销"
        # 补偿回滚: 库存恢复
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500
        # 订单未落库
        assert _mock_store["checkout_orders"] == []

    def test_submit_insufficient_points_rolls_back(self):
        """积分不足: 阶段6失败, 库存/订单全量回滚"""
        response = client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "name": "竹香经典",
                       "price": 599, "qty": 1}],
            "memberLevel": "L1", "points": 99999,
        })
        data = response.json()
        assert data["success"] is False
        assert data["failedStage"] == "阶段6-积分扣减"
        assert "积分不足" in data["error"]
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500
        assert _mock_store["checkout_points"]["L1"] == 1000

    def test_submit_success_persists_all(self):
        """成功下单: 订单/分润/库存扣减/积分变动全落库"""
        before = _mock_store["inventory"]["ZX42-2026L07"]["stock"]
        response = client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "name": "竹香经典",
                       "price": 599, "qty": 2}],
            "memberLevel": "L3",
        })
        assert response.json()["success"] is True
        # 库存扣减
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == before - 2
        # 订单 + 分润记录落库
        order = _mock_store["checkout_orders"][-1]
        assert order["status"] == "已付款"
        assert order["points_used"] == 0
        profit = _mock_store["profit_records"][-1]
        assert profit["platform_share"] == round(order["final_amount"] * 0.8, 2)
        assert profit["hotel_share"] == round(order["final_amount"] * 0.2, 2)

    def test_submit_with_agent_shipper_accrues_fee(self):
        """已认领区域下单: 发货方为代理商 + 5% 服务费计提"""
        # agent 1 认领 region
        client.post("/api/agent-shipping/claim", json={"agentId": 1, "region": "taian"})
        response = client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "name": "竹香经典",
                       "price": 599, "qty": 2}],
            "region": "taian",
        })
        data = response.json()
        assert data["success"] is True
        assert data["details"]["shipperType"] == "agent"
        assert data["details"]["shipperAgentName"] == "泰安市级代理商"
        final = data["details"]["finalAmount"]
        assert data["details"]["manufacturerServiceFee"] == round(final * 0.05, 2)
        # 服务费记录落库
        fee = _mock_store["service_fees"][-1]
        assert fee["service_rate"] == 0.05
        assert fee["status"] == "待发放"


# ============================================================
#  供应链服务: /api/inventory/deduct
# ============================================================

class TestInventoryDeduct:
    """库存扣减端点测试(P4.4 多行事务契约)"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_deduct_success_single_line(self):
        """单行扣减: 多行契约 + 兼容字段"""
        response = client.post("/api/inventory/deduct", json={
            "items": [{"id": "ZX42-2026L07", "qty": 2}],
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["operation"] == "deduct"
        assert data["details"]["totalQty"] == 2
        assert data["details"]["lines"][0]["after"] == 498
        assert data["details"]["alertsTriggered"] == 0
        assert data["asyncOps"] == ["inventory_notify", "blockchain_notarize"]
        # 单行兼容字段
        assert data["productId"] == "ZX42-2026L07"
        assert data["stockAfter"] == 498
        assert data["txId"].startswith("TX")

    def test_deduct_legacy_single_product(self):
        """旧单品契约兼容: productId/quantity"""
        response = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07", "quantity": 2,
        })
        data = response.json()
        assert data["success"] is True
        assert data["stockAfter"] == 498

    def test_deduct_multi_line_with_flow_and_alert(self):
        """多行扣减: 流水落库 + 低库存预警"""
        _mock_store["inventory"]["ZX53-2026N20"]["stock"] = 12
        response = client.post("/api/inventory/deduct", json={
            "items": [
                {"id": "ZX42-2026L07", "name": "竹香经典", "qty": 1},
                {"id": "ZX53-2026N20", "name": "年份珍藏", "qty": 5},
            ],
            "reason": "订单出库", "refNo": "ZX_TEST_001",
        })
        data = response.json()
        assert data["success"] is True
        assert data["details"]["totalQty"] == 6
        # 预警: 12-5=7 ∈ (0,10]
        assert data["details"]["alertsTriggered"] == 1
        # 流水落库(2 行出库)
        flows = _mock_store["inventory_logs"]
        assert len(flows) == 2
        assert flows[0]["type"] == "出库"
        assert flows[0]["ref_no"] == "ZX_TEST_001"
        # 预警记录落库
        assert _mock_store["stock_alerts"][-1]["stock"] == 7

    def test_deduct_insufficient_stock(self):
        """库存不足: 事务失败, 库存不变"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 5
        response = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07", "quantity": 10,
        })
        data = response.json()
        assert data["success"] is False
        assert "库存不足" in data["error"]
        assert data["failedStage"] == "阶段3-库存扣减"
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 5

    def test_deduct_partial_failure_rolls_back(self):
        """多行部分失败: 已扣行补偿恢复"""
        response = client.post("/api/inventory/deduct", json={
            "items": [
                {"id": "ZX42-2026L07", "qty": 10},
                {"id": "NO_SUCH_PRODUCT", "qty": 1},
            ],
        })
        data = response.json()
        assert data["success"] is False
        assert "商品不存在" in data["error"]
        # 第一行已扣但整体回滚
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500
        assert _mock_store["inventory_logs"] == []

    def test_deduct_product_not_found(self):
        """产品不存在(单品): 事务失败(200 + success=False)"""
        response = client.post("/api/inventory/deduct", json={
            "productId": "NOT-EXIST", "quantity": 1,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "商品不存在" in data["error"]

    def test_deduct_exact_stock_to_zero(self):
        """扣减数=库存数(刚好清零, 不触发预警)"""
        _mock_store["inventory"]["ZX42-2026L05"]["stock"] = 10
        response = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L05", "quantity": 10,
        })
        data = response.json()
        assert data["success"] is True
        assert data["stockAfter"] == 0
        assert data["details"]["alertsTriggered"] == 0   # 0 不触发(前端语义)

    def test_deduct_zero_quantity_rejected(self):
        """qty=0 拒绝(多行契约必须>0)"""
        response = client.post("/api/inventory/deduct", json={
            "items": [{"id": "ZX42-2026L07", "qty": 0}],
        })
        data = response.json()
        assert data["success"] is False
        assert "必须>0" in data["error"]
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500

    def test_deduct_negative_quantity_rejected(self):
        """负数数量拒绝(不再走 Pydantic 422, 事务校验拒绝)"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 100
        response = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07", "quantity": -10,
        })
        data = response.json()
        assert data["success"] is False
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 100


# ============================================================
#  供应链服务: /api/inventory/restock
# ============================================================

class TestInventoryRestock:
    """库存回补端点测试(P4.4 多行契约)"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_restock_success(self):
        """正常回补: +50(多行契约 + 单行兼容字段)"""
        response = client.post("/api/inventory/restock", json={
            "items": [{"id": "ZX42-2026L07", "qty": 50}],
            "reason": "退货回仓", "refNo": "RT_TEST_001",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["operation"] == "restock"
        assert data["details"]["totalQty"] == 50
        assert data["details"]["reason"] == "退货回仓"
        assert data["stockAfter"] == 550
        assert data["txId"].startswith("TX")
        # 流水落库(入库)
        assert _mock_store["inventory_logs"][-1]["type"] == "入库"

    def test_restock_product_not_found(self):
        """产品不存在: 事务失败"""
        response = client.post("/api/inventory/restock", json={
            "productId": "NOT-EXIST", "quantity": 10,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "商品不存在" in data["error"]

    def test_restock_negative_rejected(self):
        """回补负数: 事务校验拒绝"""
        response = client.post("/api/inventory/restock", json={
            "productId": "ZX42-2026L07", "quantity": -10,
        })
        data = response.json()
        assert data["success"] is False
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500


# ============================================================
#  仓储服务: /api/warehouse/inbound
# ============================================================

class TestWarehouseInbound:
    """AI智能入库端点测试(P4.4 契约: 多行+库位分配+单据流水)"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_inbound_success(self):
        response = client.post("/api/warehouse/inbound", json={
            "items": [{"id": "ZX42-2026L07", "name": "竹香经典", "qty": 10}],
            "warehouseId": 1,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["operation"] == "inbound"
        assert data["details"]["totalQty"] == 10
        line = data["details"]["lines"][0]
        assert line["before"] == 500 and line["after"] == 510
        assert line["aiVerified"] is True
        assert data["details"]["aiVerificationRate"] == 0.96
        assert data["asyncOps"] == ["inbound_order", "stock_movement",
                                    "blockchain_notarize"]

    def test_inbound_new_product_allocates_slot(self):
        """新商品入库: 自动分配空闲库位"""
        response = client.post("/api/warehouse/inbound", json={
            "items": [{"id": "NEW-PRODUCT-999", "qty": 5}],
            "warehouseId": 1,
        })
        data = response.json()
        assert data["success"] is True
        line = data["details"]["lines"][0]
        assert line["before"] == 0 and line["after"] == 5
        assert line["location"] is not None   # 分配了库位
        # 库位占用 + 库存记录落库
        stocks = [s for s in _mock_store["warehouse_stock"]
                  if s["product_id"] == "NEW-PRODUCT-999"]
        assert len(stocks) == 1 and stocks[0]["stock_qty"] == 5
        assert stocks[0]["ai_stock_status"] == "low"   # 5 <= 20

    def test_inbound_persists_order_and_flow(self):
        """入库单+库存流水攒批落库"""
        before_orders = len(_mock_store["inbound_orders"])
        before_flows = len(_mock_store["stock_movements"])
        client.post("/api/warehouse/inbound", json={
            "items": [{"id": "ZX42-2026L07", "qty": 3}],
        })
        assert len(_mock_store["inbound_orders"]) == before_orders + 1
        assert len(_mock_store["stock_movements"]) == before_flows + 1
        assert _mock_store["stock_movements"][-1]["movement_type"] == "inbound"

    def test_inbound_invalid_warehouse(self):
        """仓库不存在: 事务失败"""
        response = client.post("/api/warehouse/inbound", json={
            "items": [{"id": "ZX42-2026L07", "qty": 1}],
            "warehouseId": 999,
        })
        data = response.json()
        assert data["success"] is False
        assert "仓库不存在" in data["error"]

    def test_inbound_empty_items(self):
        """空清单: preflight 中止"""
        response = client.post("/api/warehouse/inbound", json={"items": []})
        data = response.json()
        assert data["success"] is False
        assert "清单为空" in data["error"]


# ============================================================
#  仓储服务: /api/warehouse/outbound
# ============================================================

class TestWarehouseOutbound:
    """AI智能出库端点测试(P4.4 契约)"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_outbound_success(self):
        response = client.post("/api/warehouse/outbound", json={
            "items": [{"id": "ZX42-2026L07", "qty": 100}],
            "warehouseId": 1,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["operation"] == "outbound"
        assert data["details"]["totalQty"] == 100
        assert data["details"]["lines"][0]["wavePicked"] is True
        assert data["details"]["pickingEfficiencyGain"] == 0.50

    def test_outbound_insufficient_rolls_back(self):
        """出库不足: 事务回滚, 库存不变"""
        response = client.post("/api/warehouse/outbound", json={
            "items": [{"id": "ZX42-2026L07", "qty": 600}],
        })
        data = response.json()
        assert data["success"] is False
        assert "库存不足" in data["error"]
        stocks = [s for s in _mock_store["warehouse_stock"]
                  if s["warehouse_id"] == 1 and s["product_id"] == "ZX42-2026L07"]
        assert stocks[0]["stock_qty"] == 500
        assert _mock_store["outbound_orders"] == []

    def test_outbound_stock_not_found(self):
        """库存记录不存在: 事务失败"""
        response = client.post("/api/warehouse/outbound", json={
            "items": [{"id": "NO_SUCH_STOCK", "qty": 1}],
        })
        data = response.json()
        assert data["success"] is False
        assert "库存记录不存在" in data["error"]


# ============================================================
#  仓储服务: /api/warehouse/stocktake
# ============================================================

class TestWarehouseStocktake:
    """AI智能盘点端点测试(P4.4 契约: 实盘覆盖+盈亏汇总)"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_stocktake_surplus_and_deficit(self):
        """盘盈+盘亏: 差异汇总与库存覆盖"""
        response = client.post("/api/warehouse/stocktake", json={
            "items": [
                {"id": "ZX42-2026L07", "name": "竹香经典", "actualQty": 505},
                {"id": "ZX42-2026L05", "name": "竹韵佳酿", "actualQty": 290},
            ],
            "warehouseId": 1, "method": "drone_ai",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        d = data["details"]
        assert d["surplusQty"] == 5      # 505 - 500
        assert d["deficitQty"] == 10     # 300 - 290
        assert d["aiAccuracy"] == 0.98
        types = {line["diffType"] for line in d["diffLines"]}
        assert types == {"surplus", "deficit"}
        # 库存被实盘值覆盖
        stocks = {s["product_id"]: s["stock_qty"]
                  for s in _mock_store["warehouse_stock"] if s["warehouse_id"] == 1}
        assert stocks["ZX42-2026L07"] == 505
        assert stocks["ZX42-2026L05"] == 290
        # 盘点记录落库
        assert _mock_store["stocktaking_records"][-1]["surplus_qty"] == 5

    def test_stocktake_empty_items(self):
        """空清单: 中止"""
        response = client.post("/api/warehouse/stocktake", json={"items": []})
        data = response.json()
        assert data["success"] is False


# ============================================================
#  仓储服务: /api/warehouse/slot-optimize
# ============================================================

class TestWarehouseSlotOptimize:
    """AI智能库位优化端点测试(P4.4 契约: ABC 重排)"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_slot_optimize_success(self):
        response = client.post("/api/warehouse/slot-optimize", json={
            "warehouseId": 1,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        d = data["details"]
        assert d["optimizationGain"] == 0.30
        assert len(d["relocatedItems"]) > 0
        # 分区计数总和 = 重排商品数
        assert d["hotZoneCount"] + d["warmZoneCount"] + d["coldZoneCount"] \
            == len(d["relocatedItems"])
        # 重排记录含新旧库位
        first = d["relocatedItems"][0]
        assert "oldLocId" in first and "newLocId" in first


# ============================================================
#  仓储服务: GET /api/warehouse/forecast
# ============================================================

class TestWarehouseForecast:
    """AI智能库存预测端点测试(P4.4 契约)"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_forecast_default(self):
        """默认产品: ZX42-2026L07"""
        response = client.get("/api/warehouse/forecast")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        d = data["details"]
        assert d["productId"] == "ZX42-2026L07"
        assert d["currentQty"] == 500
        assert d["aiModel"] == "LSTM"
        assert d["aiAccuracy"] == 0.89
        # daysOfSupply = currentQty / dailyConsumption(17 = round(500/30))
        assert d["daysOfSupply"] == round(500 / 17, 1)

    def test_forecast_with_product(self):
        """指定 productId"""
        response = client.get("/api/warehouse/forecast?productId=ZX42-2026L05")
        data = response.json()
        assert data["details"]["productId"] == "ZX42-2026L05"
        assert data["details"]["currentQty"] == 300

    def test_forecast_not_found(self):
        """库存记录不存在"""
        response = client.get("/api/warehouse/forecast?productId=NO_SUCH")
        data = response.json()
        assert data["success"] is False
        assert "库存记录不存在" in data["error"]


# ============================================================
#  仓储服务: 新增 5 端点(multi-transfer/loss/cross-dock/safety-stock/env-monitor)
# ============================================================

class TestWarehouseMultiTransfer:
    """AI智能多仓调拨端点测试"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_transfer_success(self):
        """仓1→仓2 调拨: 源减目标增+双向流水"""
        response = client.post("/api/warehouse/multi-transfer", json={
            "items": [{"id": "ZX42-2026B01", "name": "口粮酒", "qty": 20}],
            "fromWarehouseId": 1, "toWarehouseId": 2,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["operation"] == "multiTransfer"
        d = data["details"]
        assert d["totalQty"] == 20
        assert d["transferTimeliness"] == 0.92
        line = d["transferLines"][0]
        assert line["fromBefore"] == 800 and line["fromAfter"] == 780
        assert line["toBefore"] == 30 and line["toAfter"] == 50
        # 双向流水落库
        flows = _mock_store["stock_movements"][-2:]
        assert {f["movement_type"] for f in flows} == {"transfer_out", "transfer_in"}
        assert _mock_store["transfer_orders"][-1]["total_qty"] == 20

    def test_transfer_same_warehouse_rejected(self):
        """源=目标仓: 中止"""
        response = client.post("/api/warehouse/multi-transfer", json={
            "items": [{"id": "ZX42-2026B01", "qty": 1}],
            "fromWarehouseId": 1, "toWarehouseId": 1,
        })
        data = response.json()
        assert data["success"] is False
        assert "不能相同" in data["error"]

    def test_transfer_insufficient_rolls_back(self):
        """源仓不足: 事务回滚"""
        response = client.post("/api/warehouse/multi-transfer", json={
            "items": [{"id": "ZX42-2026L07", "qty": 600}],
            "fromWarehouseId": 1, "toWarehouseId": 2,
        })
        data = response.json()
        assert data["success"] is False
        assert "源仓库存不足" in data["error"]
        assert _mock_store["transfer_orders"] == []


class TestWarehouseLoss:
    """AI智能损耗管理端点测试"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_loss_success(self):
        response = client.post("/api/warehouse/loss", json={
            "items": [{"id": "ZX42-2026L07", "name": "竹香经典",
                       "qty": 5, "rootCause": "运输破损"}],
            "warehouseId": 1, "lossType": "breakage",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        d = data["details"]
        assert d["totalLossQty"] == 5
        assert d["lossReduction"] == 0.20
        assert d["lossLines"][0]["rootCause"] == "运输破损"
        # 库存扣减 + 损耗记录落库
        stocks = [s for s in _mock_store["warehouse_stock"]
                  if s["warehouse_id"] == 1 and s["product_id"] == "ZX42-2026L07"]
        assert stocks[0]["stock_qty"] == 495
        assert _mock_store["loss_records"][-1]["loss_type"] == "breakage"

    def test_loss_invalid_type_rejected(self):
        """非法损耗类型: 中止"""
        response = client.post("/api/warehouse/loss", json={
            "items": [{"id": "ZX42-2026L07", "qty": 1}],
            "lossType": "theft",
        })
        data = response.json()
        assert data["success"] is False
        assert "非法损耗类型" in data["error"]

    def test_loss_insufficient_rolls_back(self):
        response = client.post("/api/warehouse/loss", json={
            "items": [{"id": "ZX42-2026L07", "qty": 99999}],
        })
        data = response.json()
        assert data["success"] is False
        assert _mock_store["loss_records"] == []


class TestWarehouseCrossDock:
    """AI智能仓配一体(越库)端点测试"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_cross_dock_success(self):
        """越库: 库存不变, 仅流水"""
        before_stock = {s["product_id"]: s["stock_qty"]
                        for s in _mock_store["warehouse_stock"]}
        response = client.post("/api/warehouse/cross-dock", json={
            "items": [{"id": "ZX42-2026L07", "name": "竹香经典", "qty": 30}],
            "warehouseId": 1, "carrierId": "SF-001",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        d = data["details"]
        assert d["totalQty"] == 30
        assert d["crossDockRate"] == 0.40
        assert d["crossDockLines"][0]["crossDocked"] is True
        # 库存不变
        after_stock = {s["product_id"]: s["stock_qty"]
                       for s in _mock_store["warehouse_stock"]}
        assert before_stock == after_stock
        # 流水落库(不动库存)
        assert _mock_store["stock_movements"][-1]["movement_type"] == "cross_dock"
        assert _mock_store["cross_dock_records"][-1]["carrier_id"] == "SF-001"


class TestWarehouseSafetyStock:
    """AI智能安全库存端点测试"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_safety_stock_success(self):
        response = client.get("/api/warehouse/safety-stock?productId=ZX42-2026L07")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        d = data["details"]
        assert d["currentQty"] == 500
        assert d["avgDailyDemand"] == 17        # round(500/30)
        assert d["leadTime"] == 7
        assert d["serviceLevel"] == "95%"
        # 再订货点 = 安全库存 + 日均 × 提前期
        assert d["reorderPoint"] == d["aiRecommendedSafety"] + 17 * 7
        # 副作用: AI 推荐安全库存回写
        stocks = [s for s in _mock_store["warehouse_stock"]
                  if s["product_id"] == "ZX42-2026L07"]
        assert stocks[0]["ai_recommended_safety"] == d["aiRecommendedSafety"]

    def test_safety_stock_not_found(self):
        response = client.get("/api/warehouse/safety-stock?productId=NO_SUCH")
        data = response.json()
        assert data["success"] is False


class TestWarehouseEnvMonitor:
    """AI智能温湿度监控端点测试"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_env_monitor_success(self):
        response = client.get("/api/warehouse/env-monitor?warehouseId=1")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        d = data["details"]
        assert d["warehouseName"] == "山东泰安工厂仓"
        assert d["tempRange"] == [5, 35]
        assert d["humidityRange"] == [40, 80]
        assert d["aiAnomalyDetection"] == 0.95
        assert isinstance(d["hasAnomaly"], bool)
        assert len(d["agedStocks"]) > 0
        # 副作用: 监控记录追加
        assert len(_mock_store["environment_monitoring"]) == 1

    def test_env_monitor_anomaly_consistency(self):
        """异常标志与温湿度区间一致"""
        response = client.get("/api/warehouse/env-monitor?warehouseId=1")
        d = response.json()["details"]
        in_temp = d["tempRange"][0] <= d["temp"] <= d["tempRange"][1]
        in_hum = d["humidityRange"][0] <= d["humidity"] <= d["humidityRange"][1]
        assert d["hasAnomaly"] == (not in_temp or not in_hum)


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
        """仓储全流程: 入库→出库→盘点→预测(新契约)"""
        # 入库 +50
        r1 = client.post("/api/warehouse/inbound", json={
            "items": [{"id": "ZX42-2026L07", "qty": 50}]})
        assert r1.json()["success"] is True
        assert r1.json()["details"]["lines"][0]["after"] == 550
        # 出库 -100
        r2 = client.post("/api/warehouse/outbound", json={
            "items": [{"id": "ZX42-2026L07", "qty": 100}]})
        assert r2.json()["success"] is True
        assert r2.json()["details"]["lines"][0]["after"] == 450
        # 盘点(实盘 460 → 盘盈 10)
        r3 = client.post("/api/warehouse/stocktake", json={
            "items": [{"id": "ZX42-2026L07", "actualQty": 460}]})
        assert r3.json()["success"] is True
        assert r3.json()["details"]["surplusQty"] == 10
        # 预测(基于覆盖后库存 460)
        r4 = client.get("/api/warehouse/forecast?productId=ZX42-2026L07")
        assert r4.json()["success"] is True
        assert r4.json()["details"]["currentQty"] == 460

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
        # 失败1: 负数(事务校验拒绝), 库存不变
        r2 = client.post("/api/inventory/deduct", json={"productId": "ZX42-2026L07", "quantity": -5})
        assert r2.json()["success"] is False
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


# ============================================================
#  请求体校验(422)补充测试
# ============================================================

class TestRequestValidation:
    """缺失字段 / 非法字段类型的 422 校验"""

    # ---- /api/agent/upgrade ----
    def test_upgrade_missing_agent_id(self):
        """upgrade 缺 agentId: 422"""
        response = client.post("/api/agent/upgrade", json={
            "fromLevel": "D", "toLevel": "C", "payAmount": 100,
        })
        assert response.status_code == 422

    def test_upgrade_wrong_pay_amount_type(self):
        """upgrade payAmount 传字符串: 422"""
        response = client.post("/api/agent/upgrade", json={
            "agentId": 1, "fromLevel": "D", "toLevel": "C",
            "payAmount": "lots_of_money",
        })
        assert response.status_code == 422

    # ---- /api/agent/downgrade ----
    def test_downgrade_missing_agent_id(self):
        """downgrade 缺 agentId: 422"""
        response = client.post("/api/agent/downgrade", json={
            "fromLevel": "B", "reason": "test",
        })
        assert response.status_code == 422

    def test_downgrade_missing_from_level(self):
        """downgrade 缺 fromLevel: 422"""
        response = client.post("/api/agent/downgrade", json={
            "agentId": 1, "reason": "test",
        })
        assert response.status_code == 422

    # ---- /api/inventory/deduct ----
    def test_deduct_missing_product_id(self):
        """deduct 缺 productId/items: preflight 中止(空清单)"""
        response = client.post("/api/inventory/deduct", json={"quantity": 1})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "清单为空" in data["error"]

    def test_deduct_wrong_quantity_type(self):
        """deduct quantity 传字符串: Pydantic 422"""
        response = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07", "quantity": "many",
        })
        assert response.status_code == 422

    # ---- /api/inventory/restock ----
    def test_restock_missing_product_id(self):
        """restock 缺 productId/items: preflight 中止"""
        response = client.post("/api/inventory/restock", json={"quantity": 1})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "清单为空" in data["error"]

    # ---- /api/agent-shipping/claim ----
    def test_claim_missing_agent_id(self):
        """claim 缺 agentId: 422"""
        response = client.post("/api/agent-shipping/claim", json={"region": "jinan"})
        assert response.status_code == 422

    def test_claim_missing_region(self):
        """claim 缺 region: 422"""
        response = client.post("/api/agent-shipping/claim", json={"agentId": 1})
        assert response.status_code == 422


# ============================================================
#  幂等性与并发场景补充测试
# ============================================================

class TestIdempotencyAndConcurrency:
    """重复请求、并发请求、幂等性测试"""

    def test_upgrade_same_level_idempotent(self):
        """同等级升级(D→D): 应成功(幂等,不报错)"""
        _mock_store["agents"][1] = {"level": "D", "wallet": 0}
        r1 = client.post("/api/agent/upgrade", json={
            "agentId": 1, "fromLevel": "D", "toLevel": "D", "payAmount": 0,
        })
        assert r1.status_code == 200
        assert r1.json()["toLevel"] == "D"

    def test_downgrade_same_level_idempotent(self):
        """同等级降级(D→D): 应保持 D(幂等)"""
        _mock_store["agents"][1] = {"level": "D", "wallet": 0}
        r1 = client.post("/api/agent/downgrade", json={
            "agentId": 1, "fromLevel": "D", "reason": "test",
        })
        assert r1.json()["toLevel"] == "D"
        # 再次降级 D→D 仍保持 D
        r2 = client.post("/api/agent/downgrade", json={
            "agentId": 1, "fromLevel": "D", "reason": "test",
        })
        assert r2.json()["toLevel"] == "D"

    def test_claim_same_agent_same_region_idempotent(self):
        """同一代理商重复认领同一区域: 幂等成功"""
        # 清理状态
        _mock_store["shipping_claims"].pop("idem_test_region", None)
        # 第一次认领
        r1 = client.post("/api/agent-shipping/claim", json={
            "agentId": 1, "region": "idem_test_region",
        })
        assert r1.status_code == 200
        # 第二次同代理商重复认领: 应幂等成功(不报 409)
        r2 = client.post("/api/agent-shipping/claim", json={
            "agentId": 1, "region": "idem_test_region",
        })
        assert r2.status_code == 200
        assert r2.json()["success"] is True

    def test_claim_different_agent_conflict(self):
        """不同代理商认领已占区域: 409 冲突"""
        _mock_store["shipping_claims"]["conflict_region"] = 1
        r = client.post("/api/agent-shipping/claim", json={
            "agentId": 2, "region": "conflict_region",
        })
        assert r.status_code == 409

    def test_upgrade_persistence_no_side_effect_on_failure(self):
        """升级失败(422 拦截): store 状态不被污染(事务回滚语义)"""
        _mock_store["agents"][2] = {"level": "B", "wallet": 1000}
        # 负金额被 Pydantic 422 拦截, 服务层不执行, store 不被污染
        r = client.post("/api/agent/upgrade", json={
            "agentId": 2, "fromLevel": "B", "toLevel": "A", "payAmount": -100,
        })
        assert r.status_code == 422
        assert _mock_store["agents"][2]["level"] == "B"
        assert _mock_store["agents"][2]["wallet"] == 1000

    def test_claim_failure_no_side_effect(self):
        """认领失败(409): shipping_claims 不被修改"""
        _mock_store["shipping_claims"]["side_effect_region"] = 1
        before_count = len(_mock_store["shipping_claims"])
        # 不同代理商尝试认领已占区域
        r = client.post("/api/agent-shipping/claim", json={
            "agentId": 2, "region": "side_effect_region",
        })
        assert r.status_code == 409
        # shipping_claims 不应被修改
        assert len(_mock_store["shipping_claims"]) == before_count
        assert _mock_store["shipping_claims"]["side_effect_region"] == 1


# ============================================================
#  边界值补充测试
# ============================================================

class TestBoundaryValues:
    """边界值测试"""

    def test_upgrade_large_pay_amount(self):
        """超大支付金额(¥10亿): 应成功(不限制上限)"""
        _mock_store["agents"][1] = {"level": "D", "wallet": 0}
        r = client.post("/api/agent/upgrade", json={
            "agentId": 1, "fromLevel": "D", "toLevel": "C",
            "payAmount": 1_000_000_000,
        })
        assert r.status_code == 200
        assert r.json()["wallet"] >= 1_000_000_000

    def test_deduct_huge_quantity_insufficient(self):
        """扣减超大数量(超过库存): success=False"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 100
        r = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07", "quantity": 2**31,
        })
        assert r.json()["success"] is False
        # 库存不被修改
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 100

    def test_restock_huge_quantity(self):
        """回补超大数量(>9999): 事务校验拒绝(防误操作)"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 0
        r = client.post("/api/inventory/restock", json={
            "productId": "ZX42-2026L07", "quantity": 2**31,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is False
        assert "超限" in data["error"]
        # 库存不变
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 0

    def test_claim_empty_string_region(self):
        """空字符串 region: Pydantic 应放行(服务层可能拒绝)"""
        # region: str 不限制长度, 空字符串会通过 Pydantic 校验
        r = client.post("/api/agent-shipping/claim", json={
            "agentId": 1, "region": "",
        })
        # 服务层行为: 不一定 409 或 200, 但不应 500
        assert r.status_code in (200, 409, 422)

    def test_claim_special_chars_in_region(self):
        """region 包含特殊字符: 应正常处理(不报 500)"""
        r = client.post("/api/agent-shipping/claim", json={
            "agentId": 1, "region": "北京-东城区<script>alert(1)</script>",
        })
        assert r.status_code in (200, 409)
        # 若成功, 响应里 region 应原样回显(不做转义, 由前端处理)
        if r.status_code == 200:
            assert r.json()["region"] == "北京-东城区<script>alert(1)</script>"

    def test_agent_id_zero(self):
        """agentId=0: 不应 500(可能是 404 或 200)"""
        r = client.post("/api/agent/upgrade", json={
            "agentId": 0, "fromLevel": "D", "toLevel": "C", "payAmount": 0,
        })
        assert r.status_code in (200, 404)

    def test_agent_id_negative(self):
        """agentId=-1: 不应 500"""
        r = client.post("/api/agent/upgrade", json={
            "agentId": -1, "fromLevel": "D", "toLevel": "C", "payAmount": 0,
        })
        assert r.status_code in (200, 404)

    def test_agent_id_string_type(self):
        """agentId 传字符串数字: Pydantic extra=allow 应放行"""
        r = client.post("/api/agent/upgrade", json={
            "agentId": "1", "fromLevel": "D", "toLevel": "C", "payAmount": 0,
        })
        # agentId 类型为 Any, 应放行; 服务层是否能处理取决于实现
        assert r.status_code in (200, 404, 422)


# ============================================================
#  响应结构一致性测试
# ============================================================

class TestResponseConsistency:
    """所有业务端点响应结构一致性"""

    def test_all_success_responses_have_success_field(self):
        """所有成功响应必须包含 success 字段"""
        # agent/upgrade
        _mock_store["agents"][1] = {"level": "D", "wallet": 0}
        r = client.post("/api/agent/upgrade", json={
            "agentId": 1, "fromLevel": "D", "toLevel": "C", "payAmount": 0,
        })
        assert "success" in r.json()

        # inventory/deduct
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 100
        r = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07", "quantity": 1,
        })
        assert "success" in r.json()

        # agent-shipping/claims (GET)
        r = client.get("/api/agent-shipping/claims")
        assert "success" in r.json()

    def test_error_responses_use_error_field(self):
        """错误响应使用 error 字段(4xx 自定义格式 / 事务失败 200+success=False)"""
        # 事务失败(200 + success=False + error, 对齐前端契约)
        r = client.post("/api/inventory/deduct", json={
            "productId": "NOT-EXIST", "quantity": 1,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is False
        assert "error" in body

        # 409 错误(agent-shipping 保留 4xx 映射)
        _mock_store["shipping_claims"]["err_region"] = 1
        r = client.post("/api/agent-shipping/claim", json={
            "agentId": 2, "region": "err_region",
        })
        assert r.status_code == 409
        body = r.json()
        assert body["success"] is False
        assert "error" in body

        # 422 错误: FastAPI 标准 {"detail": [...]}(Pydantic 校验,不经自定义处理器)
        r = client.post("/api/agent/upgrade", json={"fromLevel": "D"})
        assert r.status_code == 422
        assert "detail" in r.json()

    def test_tx_id_format_consistency(self):
        """库存操作 txId 格式统一: TX 前缀(单行兼容字段)"""
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 100
        r1 = client.post("/api/inventory/deduct", json={
            "productId": "ZX42-2026L07", "quantity": 1,
        })
        assert r1.json()["txId"].startswith("TX")

        r2 = client.post("/api/inventory/restock", json={
            "productId": "ZX42-2026L07", "quantity": 1,
        })
        assert r2.json()["txId"].startswith("TX")

    def test_order_id_format_consistency(self):
        """订单号格式统一: ZX 前缀(时间戳)"""
        r = client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "price": 599, "qty": 1}],
        })
        order_id = r.json()["orderId"]
        assert order_id.startswith("ZX")
        assert len(order_id) > 8   # ZX + 毫秒时间戳 + 序号


# ============================================================
#  AgentRepository 内存模式直接调用测试
#  覆盖未被 HTTP 路由间接触发的 _mem_* 方法
# ============================================================

from repositories.agent_repository import AgentRepository


class TestAgentRepositoryMemory:
    """AgentRepository 内存模式直接调用(list_all / save / get_wallet / get_level)

    所有方法均为 async, 需用 asyncio 事件循环运行。
    """

    def setup_method(self):
        """每个测试前重置内存数据,确保隔离"""
        from repositories.store import reset_store
        reset_store()
        self.repo = AgentRepository()

    async def test_list_all_returns_all_agents(self):
        """list_all 返回所有代理商(初始 2 个)"""
        agents = await self.repo.list_all()
        assert len(agents) == 2
        agent_ids = {a["id"] for a in agents}
        assert agent_ids == {1, 2}

    async def test_list_all_returns_list_type(self):
        """list_all 返回 list 类型"""
        agents = await self.repo.list_all()
        assert isinstance(agents, list)

    async def test_list_all_empty_after_clearing_store(self):
        """list_all 无代理商时返回空列表"""
        _mock_store["agents"].clear()
        agents = await self.repo.list_all()
        assert agents == []

    async def test_list_all_returns_copy_not_reference(self):
        """list_all 返回的列表修改不影响 store(浅拷贝)"""
        agents = await self.repo.list_all()
        agents.clear()
        # store 不受影响
        assert len(_mock_store["agents"]) == 2

    async def test_save_new_agent(self):
        """save 新增代理商"""
        new_agent = {"id": 3, "name": "测试代理商", "level": "D", "wallet": 0}
        result = await self.repo.save(3, new_agent)
        assert result == new_agent
        assert _mock_store["agents"][3] == new_agent

    async def test_save_overwrite_existing(self):
        """save 覆盖现有代理商"""
        new_data = {"id": 1, "name": "新名称", "level": "A", "wallet": 999}
        await self.repo.save(1, new_data)
        assert _mock_store["agents"][1]["name"] == "新名称"
        assert _mock_store["agents"][1]["level"] == "A"

    async def test_save_returns_same_dict(self):
        """save 返回值应与传入一致"""
        agent_data = {"id": 5, "name": "返回值测试", "level": "B", "wallet": 100}
        result = await self.repo.save(5, agent_data)
        assert result is agent_data

    async def test_save_then_get(self):
        """save 后 get 应返回相同数据"""
        agent_data = {"id": 7, "name": "存取测试", "level": "C", "wallet": 500}
        await self.repo.save(7, agent_data)
        retrieved = await self.repo.get(7)
        assert retrieved == agent_data

    async def test_get_wallet_existing_agent(self):
        """get_wallet 返回代理商钱包余额"""
        wallet = await self.repo.get_wallet(1)
        assert wallet == 50000

    async def test_get_wallet_nonexistent_raises_keyerror(self):
        """get_wallet 不存在的代理商抛 KeyError"""
        with pytest.raises(KeyError):
            await self.repo.get_wallet(999)

    async def test_get_level_existing_agent(self):
        """get_level 返回代理商等级"""
        level = await self.repo.get_level(1)
        assert level == "C"

    async def test_get_level_nonexistent_raises_keyerror(self):
        """get_level 不存在的代理商抛 KeyError"""
        with pytest.raises(KeyError):
            await self.repo.get_level(999)

    async def test_get_level_default_d_when_missing_field(self):
        """get_level 无 level 字段时默认 D"""
        _mock_store["agents"][10] = {"id": 10, "name": "无等级", "wallet": 0}
        level = await self.repo.get_level(10)
        assert level == "D"

    async def test_get_wallet_default_zero_when_missing_field(self):
        """get_wallet 无 wallet 字段时默认 0"""
        _mock_store["agents"][11] = {"id": 11, "name": "无钱包", "level": "D"}
        wallet = await self.repo.get_wallet(11)
        assert wallet == 0

    async def test_add_wallet_increments_balance(self):
        """add_wallet 累加余额"""
        new_wallet = await self.repo.add_wallet(1, 10000)
        assert new_wallet == 60000
        assert await self.repo.get_wallet(1) == 60000

    async def test_add_wallet_zero_amount(self):
        """add_wallet 0 元: 余额不变"""
        before = await self.repo.get_wallet(1)
        after = await self.repo.add_wallet(1, 0)
        assert after == before

    async def test_add_wallet_negative_amount(self):
        """add_wallet 负金额: 扣减余额"""
        new_wallet = await self.repo.add_wallet(1, -5000)
        assert new_wallet == 45000

    async def test_add_wallet_nonexistent_raises_keyerror(self):
        """add_wallet 不存在的代理商抛 KeyError"""
        with pytest.raises(KeyError):
            await self.repo.add_wallet(999, 100)

    async def test_update_level_returns_old_level(self):
        """update_level 返回旧等级"""
        old_level = await self.repo.update_level(1, "B")
        assert old_level == "C"
        assert await self.repo.get_level(1) == "B"

    async def test_update_level_nonexistent_raises_keyerror(self):
        """update_level 不存在的代理商抛 KeyError"""
        with pytest.raises(KeyError):
            await self.repo.update_level(999, "A")

    async def test_downgrade_level_s_to_a(self):
        """降级: S→A"""
        await self.repo.update_level(1, "S")
        new_level = await self.repo.downgrade_level(1)
        assert new_level == "A"

    async def test_downgrade_level_d_stays_d(self):
        """降级: D 保持 D(最低级)"""
        await self.repo.update_level(1, "D")
        new_level = await self.repo.downgrade_level(1)
        assert new_level == "D"

    async def test_downgrade_level_nonexistent_raises_keyerror(self):
        """downgrade_level 不存在的代理商抛 KeyError"""
        with pytest.raises(KeyError):
            await self.repo.downgrade_level(999)

    async def test_downgrade_level_all_transitions(self):
        """降级链: S→A→B→C→D 完整验证"""
        transitions = [("S", "A"), ("A", "B"), ("B", "C"), ("C", "D")]
        for from_level, expected in transitions:
            await self.repo.update_level(1, from_level)
            new_level = await self.repo.downgrade_level(1)
            assert new_level == expected, f"{from_level}→{expected} 失败"

    async def test_get_nonexistent_returns_none(self):
        """get 不存在的代理商返回 None"""
        agent = await self.repo.get(999)
        assert agent is None

    async def test_get_existing_returns_dict(self):
        """get 返回完整 dict"""
        agent = await self.repo.get(1)
        assert agent is not None
        assert agent["id"] == 1
        assert agent["name"] == "泰安市级代理商"
        assert agent["level"] == "C"
        assert agent["wallet"] == 50000


# ============================================================
#  Checkout/submit 补充测试: 响应结构 / 边界值 / 持久化 / 唯一性
# ============================================================

class TestCheckoutSupplementary:
    """订单结算端点补充测试(P4.4 新契约: 透传/唯一性/持久化)"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_response_structure_complete(self):
        """成功响应必须包含全部契约字段"""
        r = client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "price": 599, "qty": 1}],
        })
        data = r.json()
        required = {"success", "orderNo", "details", "logs", "asyncOps",
                    "orderId", "status", "message"}
        assert required.issubset(data.keys()), f"缺少字段: {required - data.keys()}"

    def test_consignee_payment_passthrough(self):
        """consignee/payment 字段应原样存入订单"""
        consignee = {"name": "李四", "phone": "13900002222", "address": "泰安市"}
        payment = {"method": "alipay", "amount": 584.0}
        client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "price": 599, "qty": 1}],
            "consignee": consignee, "payment": payment,
        })
        last_order = _mock_store["checkout_orders"][-1]
        assert last_order["consignee"] == consignee
        assert last_order["payment"] == payment

    def test_order_id_uniqueness_consecutive(self):
        """连续下单: 订单号唯一"""
        ids = set()
        for _ in range(3):
            r = client.post("/api/checkout/submit", json={
                "items": [{"id": "ZX42-2026L07", "price": 599, "qty": 1}]})
            ids.add(r.json()["orderNo"])
        assert len(ids) == 3

    def test_order_stored_with_created_at(self):
        """订单落库应包含 created_at 时间戳"""
        client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "price": 599, "qty": 1}]})
        last_order = _mock_store["checkout_orders"][-1]
        assert last_order["created_at"]
        assert last_order["paid_at"]

    def test_null_consignee_and_payment(self):
        """consignee/payment 显式传 null: 应成功(字段可选)"""
        r = client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "price": 599, "qty": 1}],
            "consignee": None, "payment": None,
        })
        assert r.status_code == 200
        assert r.json()["success"] is True


# ============================================================
#  Warehouse 端点补充测试: 响应结构 / 边界值 / 持久化 / 一致性
# ============================================================

class TestWarehouseSupplementary:
    """仓储端点补充测试(P4.4 新契约: 响应结构/透传/空体)"""

    def setup_method(self):
        from repositories.store import reset_store
        reset_store()

    def test_inbound_response_structure(self):
        """入库成功响应必须包含全部契约字段"""
        r = client.post("/api/warehouse/inbound", json={
            "items": [{"id": "ZX42-2026L07", "qty": 1}]})
        data = r.json()
        required = {"success", "operation", "details", "logs", "asyncOps"}
        assert required.issubset(data.keys())
        assert {"totalQty", "lines", "aiVerificationRate",
                "warehouseId"}.issubset(data["details"].keys())

    def test_inbound_product_id_passthrough(self):
        """productId 应原样出现在行明细"""
        r = client.post("/api/warehouse/inbound", json={
            "items": [{"id": "CUSTOM-PID-001", "qty": 1}]})
        assert r.json()["details"]["lines"][0]["id"] == "CUSTOM-PID-001"

    def test_outbound_response_structure(self):
        """出库成功响应契约字段"""
        r = client.post("/api/warehouse/outbound", json={
            "items": [{"id": "ZX42-2026L07", "qty": 1}]})
        data = r.json()
        assert data["success"] is True
        assert {"totalQty", "lines", "pickingEfficiencyGain",
                "warehouseId"}.issubset(data["details"].keys())

    def test_stocktake_match_line(self):
        """盘点无差异行: diffType=match"""
        r = client.post("/api/warehouse/stocktake", json={
            "items": [{"id": "ZX42-2026L07", "actualQty": 500}]})
        data = r.json()
        assert data["details"]["diffLines"][0]["diffType"] == "match"
        assert data["details"]["surplusQty"] == 0
        assert data["details"]["deficitQty"] == 0

    def test_slot_optimize_empty_body_succeeds(self):
        """空请求体: 应成功(字段可选)"""
        r = client.post("/api/warehouse/slot-optimize", json={})
        assert r.status_code == 200
        assert r.json()["success"] is True

    def test_forecast_replenishment_logic(self):
        """预测补货建议逻辑: daysOfSupply 与 replenishmentSuggested 一致"""
        r = client.get("/api/warehouse/forecast?productId=ZX42-2026L07")
        d = r.json()["details"]
        assert d["replenishmentSuggested"] == (d["daysOfSupply"] < 7)

    def test_forecast_empty_product_id_query(self):
        """productId 传空字符串: 不应 500(回退默认产品)"""
        r = client.get("/api/warehouse/forecast?productId=")
        assert r.status_code == 200
        assert r.json()["success"] is True


# ============================================================
#  InventoryRepository 内存模式直接调用测试
#  覆盖 _mem_get_stock / _mem_set_stock 的所有分支
# ============================================================

from repositories.inventory_repository import InventoryRepository


class TestInventoryRepositoryMemory:
    """InventoryRepository 内存模式直接调用(get_stock / set_stock)

    覆盖 _mem_get_stock 行 65-67 和 _mem_set_stock 行 69-74 的所有分支:
      - 产品存在: 返回 stock
      - 产品不存在: 返回 0
      - int 类型 product_id: 自动转 str
      - set_stock 新增产品: 初始化 reserved=0
      - set_stock 覆盖现有: 保留 reserved
    """

    def setup_method(self):
        """每个测试前重置内存数据,确保隔离"""
        from repositories.store import reset_store
        reset_store()
        self.repo = InventoryRepository()

    async def test_get_stock_existing_product(self):
        """get_stock 返回现有产品库存(ZX42-2026L07: 500)"""
        stock = await self.repo.get_stock("ZX42-2026L07")
        assert stock == 500

    async def test_get_stock_nonexistent_returns_zero(self):
        """get_stock 不存在的产品返回 0(不抛异常)"""
        stock = await self.repo.get_stock("NONEXISTENT")
        assert stock == 0

    async def test_get_stock_int_product_id_auto_str(self):
        """get_stock int 类型 product_id 自动转 str(与 str key 匹配)"""
        # 先用 str key 写入, 再用 int 查询
        _mock_store["inventory"]["12345"] = {"stock": 100, "reserved": 0}
        stock = await self.repo.get_stock(12345)
        assert stock == 100

    async def test_get_stock_returns_int_type(self):
        """get_stock 返回 int 类型"""
        stock = await self.repo.get_stock("ZX42-2026L07")
        assert isinstance(stock, int)

    async def test_get_stock_empty_store(self):
        """get_stock 空库存时返回 0"""
        _mock_store["inventory"].clear()
        stock = await self.repo.get_stock("ZX42-2026L07")
        assert stock == 0

    async def test_set_stock_new_product(self):
        """set_stock 新产品: 应初始化 reserved=0 并设置 stock"""
        result = await self.repo.set_stock("NEW-PRODUCT", 200)
        assert result == 200
        product = _mock_store["inventory"]["NEW-PRODUCT"]
        assert product["stock"] == 200
        assert product["reserved"] == 0

    async def test_set_stock_overwrite_existing(self):
        """set_stock 覆盖现有产品: 保留 reserved 字段"""
        # ZX42-2026L07 初始 reserved=0, 先修改 reserved
        _mock_store["inventory"]["ZX42-2026L07"]["reserved"] = 50
        await self.repo.set_stock("ZX42-2026L07", 999)
        product = _mock_store["inventory"]["ZX42-2026L07"]
        assert product["stock"] == 999
        # reserved 应保留原值 50
        assert product["reserved"] == 50

    async def test_set_stock_returns_set_value(self):
        """set_stock 返回设置的值"""
        result = await self.repo.set_stock("ZX42-2026L07", 100)
        assert result == 100

    async def test_set_stock_int_product_id_auto_str(self):
        """set_stock int 类型 product_id 自动转 str key"""
        await self.repo.set_stock(67890, 300)
        assert _mock_store["inventory"]["67890"]["stock"] == 300

    async def test_set_stock_zero(self):
        """set_stock 设置为 0"""
        result = await self.repo.set_stock("ZERO-STOCK", 0)
        assert result == 0
        assert _mock_store["inventory"]["ZERO-STOCK"]["stock"] == 0

    async def test_set_stock_then_get_stock(self):
        """set_stock 后 get_stock 应返回相同值"""
        await self.repo.set_stock("ROUND-TRIP", 777)
        stock = await self.repo.get_stock("ROUND-TRIP")
        assert stock == 777

    async def test_set_stock_large_value(self):
        """set_stock 大数值(1 亿)"""
        result = await self.repo.set_stock("LARGE", 100_000_000)
        assert result == 100_000_000
        assert await self.repo.get_stock("LARGE") == 100_000_000

    async def test_set_stock_preserves_reserved_on_overwrite(self):
        """set_stock 覆盖时不重置 reserved(即使初始无 reserved 字段)"""
        # 先手动添加无 reserved 字段的产品
        _mock_store["inventory"]["NO-RESERVED"] = {"stock": 10}
        await self.repo.set_stock("NO-RESERVED", 20)
        # 由于 key 已存在, 不会触发初始化, reserved 保持缺失
        product = _mock_store["inventory"]["NO-RESERVED"]
        assert product["stock"] == 20
        # "reserved" 不存在(因为 set_stock 只在 key 不存在时才初始化)
        assert "reserved" not in product

    # ---------- _mem_deduct 异常路径(行 80, 82) ----------

    async def test_deduct_nonexistent_raises_keyerror(self):
        """deduct 不存在的产品抛 KeyError(行 80)"""
        with pytest.raises(KeyError):
            await self.repo.deduct("NONEXISTENT", 1)

    async def test_deduct_insufficient_stock_raises_valueerror(self):
        """deduct 库存不足抛 ValueError, 含当前库存和需要量(行 82)"""
        # ZX42-2026L07 初始库存 500
        with pytest.raises(ValueError, match="库存不足"):
            await self.repo.deduct("ZX42-2026L07", 501)

    async def test_deduct_exact_stock_succeeds(self):
        """deduct 恰好等于库存: 成功, 余额为 0"""
        new_stock = await self.repo.deduct("ZX42-2026L07", 500)
        assert new_stock == 0

    async def test_deduct_zero_quantity_succeeds(self):
        """deduct 0 件: 成功, 库存不变"""
        before = await self.repo.get_stock("ZX42-2026L07")
        after = await self.repo.deduct("ZX42-2026L07", 0)
        assert after == before

    async def test_deduct_then_restock_roundtrip(self):
        """deduct 后 restock: 库存回到原值"""
        original = await self.repo.get_stock("ZX42-2026L07")
        await self.repo.deduct("ZX42-2026L07", 100)
        await self.repo.restock("ZX42-2026L07", 100)
        assert await self.repo.get_stock("ZX42-2026L07") == original

    # ---------- _mem_restock 异常路径(行 90) ----------

    async def test_restock_nonexistent_raises_keyerror(self):
        """restock 不存在的产品抛 KeyError(行 90)"""
        with pytest.raises(KeyError):
            await self.repo.restock("NONEXISTENT", 10)

    async def test_restock_zero_quantity_succeeds(self):
        """restock 0 件: 成功, 库存不变"""
        before = await self.repo.get_stock("ZX42-2026L07")
        after = await self.repo.restock("ZX42-2026L07", 0)
        assert after == before

    async def test_restock_increments_stock(self):
        """restock 累加库存"""
        new_stock = await self.repo.restock("ZX42-2026L07", 100)
        assert new_stock == 600


# ============================================================
#  OrderRepository 内存模式直接调用测试
#  覆盖 _mem_count / _mem_list_all 的所有分支
# ============================================================

from repositories.order_repository import OrderRepository


class TestOrderRepositoryMemory:
    """OrderRepository 内存模式直接调用(count / list_all / create)

    覆盖 _mem_count 行 42 和 _mem_list_all 行 45 的所有分支:
      - 空订单: count=0, list_all=[]
      - 单个订单: count=1, list_all 返回该订单
      - 多个订单: count 正确, list_all 保留顺序
      - list_all 返回浅拷贝(修改不影响 store)
    """

    def setup_method(self):
        """每个测试前重置内存数据,确保隔离"""
        from repositories.store import reset_store
        reset_store()
        self.repo = OrderRepository()

    async def test_count_zero_initially(self):
        """count 初始为 0(行 42, 空订单场景)"""
        count = await self.repo.count()
        assert count == 0

    async def test_count_after_create(self):
        """count 创建一个订单后为 1"""
        await self.repo.create({"orderId": "O001", "items": []})
        count = await self.repo.count()
        assert count == 1

    async def test_count_multiple_orders(self):
        """count 创建多个订单后数量正确"""
        for i in range(10):
            await self.repo.create({"orderId": f"O{i}", "seq": i})
        count = await self.repo.count()
        assert count == 10

    async def test_count_returns_int_type(self):
        """count 返回 int 类型"""
        await self.repo.create({"orderId": "O1"})
        count = await self.repo.count()
        assert isinstance(count, int)

    async def test_list_all_empty(self):
        """list_all 无订单时返回空列表(行 45)"""
        orders = await self.repo.list_all()
        assert orders == []

    async def test_list_all_single_order(self):
        """list_all 返回单个订单"""
        await self.repo.create({"orderId": "SINGLE", "items": ["A"]})
        orders = await self.repo.list_all()
        assert len(orders) == 1
        assert orders[0]["orderId"] == "SINGLE"

    async def test_list_all_preserves_insertion_order(self):
        """list_all 保留插入顺序(FIFO)"""
        for i in range(5):
            await self.repo.create({"orderId": f"O{i}", "seq": i})
        orders = await self.repo.list_all()
        ids = [o["orderId"] for o in orders]
        assert ids == ["O0", "O1", "O2", "O3", "O4"]

    async def test_list_all_returns_list_type(self):
        """list_all 返回 list 类型"""
        orders = await self.repo.list_all()
        assert isinstance(orders, list)

    async def test_list_all_returns_shallow_copy(self):
        """list_all 返回浅拷贝: 修改列表不影响 store"""
        await self.repo.create({"orderId": "COPY-TEST"})
        orders = await self.repo.list_all()
        orders.clear()
        # store 不受影响
        assert await self.repo.count() == 1

    async def test_create_returns_order_id(self):
        """create 返回 orderId"""
        result = await self.repo.create({"orderId": "RET-001", "status": "paid"})
        assert result == "RET-001"

    async def test_create_without_order_id_returns_none(self):
        """create 无 orderId 字段返回 None"""
        result = await self.repo.create({"items": [], "status": "pending"})
        assert result is None

    async def test_create_persists_to_store(self):
        """create 持久化到 store(回查一致)"""
        await self.repo.create({"orderId": "PERSIST-001", "amount": 100})
        orders = await self.repo.list_all()
        target = next(o for o in orders if o["orderId"] == "PERSIST-001")
        assert target["amount"] == 100


# ============================================================
#  ShippingClaimRepository 内存模式直接调用测试
#  覆盖 _mem_is_claimed / _mem_get_claim / _mem_set_claim / _mem_list_all
# ============================================================

from repositories.shipping_repository import ShippingClaimRepository


class TestShippingRepositoryMemory:
    """ShippingClaimRepository 内存模式直接调用

    覆盖 _mem_is_claimed 行 55 和相关方法的所有分支:
      - 未认领区域: is_claimed=False, get_claim=None
      - 已认领区域: is_claimed=True, get_claim 返回 agent_id
      - set_claim 后状态切换
      - list_all 返回 dict 副本
    """

    def setup_method(self):
        """每个测试前重置内存数据,确保隔离"""
        from repositories.store import reset_store
        reset_store()
        self.repo = ShippingClaimRepository()

    async def test_is_claimed_unclaimed_returns_false(self):
        """is_claimed 未认领区域返回 False(行 55)"""
        result = await self.repo.is_claimed("未认领区域")
        assert result is False

    async def test_is_claimed_claimed_returns_true(self):
        """is_claimed 已认领区域返回 True(行 55)"""
        await self.repo.set_claim("已认领区域", 1)
        result = await self.repo.is_claimed("已认领区域")
        assert result is True

    async def test_is_claimed_after_set_transitions(self):
        """is_claimed 状态切换: set 前后 False→True"""
        region = "状态切换区"
        assert await self.repo.is_claimed(region) is False
        await self.repo.set_claim(region, 1)
        assert await self.repo.is_claimed(region) is True

    async def test_is_claimed_empty_region_name(self):
        """is_claimed 空字符串区域名: 返回 False"""
        result = await self.repo.is_claimed("")
        assert result is False

    async def test_is_claimed_returns_bool_type(self):
        """is_claimed 返回 bool 类型"""
        result = await self.repo.is_claimed("测试")
        assert isinstance(result, bool)

    async def test_get_claim_unclaimed_returns_none(self):
        """get_claim 未认领返回 None"""
        result = await self.repo.get_claim("无人认领")
        assert result is None

    async def test_get_claim_claimed_returns_agent_id(self):
        """get_claim 已认领返回 agent_id"""
        await self.repo.set_claim("已认领", 42)
        result = await self.repo.get_claim("已认领")
        assert result == 42

    async def test_get_claim_string_agent_id(self):
        """get_claim 非数字 agent_id 原样存储"""
        await self.repo.set_claim("文本ID区", "agent_abc")
        result = await self.repo.get_claim("文本ID区")
        assert result == "agent_abc"

    async def test_get_claim_zero_agent_id(self):
        """get_claim agent_id=0: 应正确存储和返回"""
        await self.repo.set_claim("零ID区", 0)
        assert await self.repo.is_claimed("零ID区") is True
        result = await self.repo.get_claim("零ID区")
        assert result == 0

    async def test_set_claim_overwrite(self):
        """set_claim 覆盖现有认领"""
        await self.repo.set_claim("覆盖区", 1)
        await self.repo.set_claim("覆盖区", 2)
        result = await self.repo.get_claim("覆盖区")
        assert result == 2

    async def test_set_claim_then_is_claimed_consistent(self):
        """set_claim 后 is_claimed 和 get_claim 一致"""
        await self.repo.set_claim("一致性区", 100)
        assert await self.repo.is_claimed("一致性区") is True
        assert await self.repo.get_claim("一致性区") == 100

    async def test_list_all_empty_returns_dict(self):
        """list_all 无认领返回空 dict"""
        result = await self.repo.list_all()
        assert result == {}
        assert isinstance(result, dict)

    async def test_list_all_returns_all_claims(self):
        """list_all 返回所有认领"""
        await self.repo.set_claim("区域1", 1)
        await self.repo.set_claim("区域2", 2)
        claims = await self.repo.list_all()
        assert len(claims) == 2
        assert claims["区域1"] == 1
        assert claims["区域2"] == 2

    async def test_list_all_returns_copy_not_reference(self):
        """list_all 返回副本: 修改不影响 store"""
        await self.repo.set_claim("副本测试", 1)
        claims = await self.repo.list_all()
        claims.clear()
        # store 不受影响
        assert await self.repo.is_claimed("副本测试") is True


# ============================================================
#  WarehouseRepository 内存模式直接调用测试
#  覆盖 _mem_count_inbound / _mem_count_outbound 的所有分支
# ============================================================

from repositories.warehouse_repository import WarehouseRepository


class TestWarehouseRepositoryMemory:
    """WarehouseRepository 内存模式直接调用

    覆盖 _mem_count_inbound 行 79 和 _mem_count_outbound 行 82 的所有分支:
      - 空日志: count=0
      - 追加后计数增加
      - count 返回 int 类型
      - count_inbound_before / count_outbound_before 基线对比
      - append 返回值一致
      - get_slots 返回 dict
    """

    def setup_method(self):
        """每个测试前重置内存数据,确保隔离"""
        from repositories.store import reset_store
        reset_store()
        self.repo = WarehouseRepository()

    async def test_count_inbound_zero_initially(self):
        """count_inbound 初始为 0(行 79)"""
        count = await self.repo.count_inbound()
        assert count == 0

    async def test_count_inbound_after_append(self):
        """count_inbound 追加后计数增加(行 79)"""
        await self.repo.append_inbound_log({"action": "inbound"})
        count = await self.repo.count_inbound()
        assert count == 1

    async def test_count_inbound_multiple_appends(self):
        """count_inbound 多次追加后计数正确(行 79)"""
        for i in range(5):
            await self.repo.append_inbound_log({"seq": i})
        count = await self.repo.count_inbound()
        assert count == 5

    async def test_count_inbound_returns_int_type(self):
        """count_inbound 返回 int 类型"""
        await self.repo.append_inbound_log({"x": 1})
        count = await self.repo.count_inbound()
        assert isinstance(count, int)

    async def test_count_outbound_zero_initially(self):
        """count_outbound 初始为 0(行 82)"""
        count = await self.repo.count_outbound()
        assert count == 0

    async def test_count_outbound_after_append(self):
        """count_outbound 追加后计数增加(行 82)"""
        await self.repo.append_outbound_log({"action": "outbound"})
        count = await self.repo.count_outbound()
        assert count == 1

    async def test_count_outbound_multiple_appends(self):
        """count_outbound 多次追加后计数正确(行 82)"""
        for i in range(7):
            await self.repo.append_outbound_log({"seq": i})
        count = await self.repo.count_outbound()
        assert count == 7

    async def test_count_outbound_returns_int_type(self):
        """count_outbound 返回 int 类型"""
        await self.repo.append_outbound_log({"x": 1})
        count = await self.repo.count_outbound()
        assert isinstance(count, int)

    async def test_count_inbound_before_baseline(self):
        """count_inbound_before 基线对比: 返回当前与基线的差"""
        for i in range(3):
            await self.repo.append_inbound_log({"i": i})
        diff = await self.repo.count_inbound_before(0)
        assert diff == 3

    async def test_count_inbound_before_negative_when_baseline_higher(self):
        """count_inbound_before 基线高于当前时返回负数"""
        diff = await self.repo.count_inbound_before(10)
        assert diff == -10

    async def test_count_outbound_before_baseline(self):
        """count_outbound_before 基线对比: 返回当前与基线的差"""
        for i in range(4):
            await self.repo.append_outbound_log({"i": i})
        diff = await self.repo.count_outbound_before(1)
        assert diff == 3

    async def test_count_outbound_before_negative_when_baseline_higher(self):
        """count_outbound_before 基线高于当前时返回负数"""
        diff = await self.repo.count_outbound_before(5)
        assert diff == -5

    async def test_append_inbound_log_returns_same_log(self):
        """append_inbound_log 返回值应与传入一致"""
        log = {"action": "inbound", "productId": "TEST", "slot": "A1"}
        result = await self.repo.append_inbound_log(log)
        assert result == log

    async def test_append_outbound_log_returns_same_log(self):
        """append_outbound_log 返回值应与传入一致"""
        log = {"action": "outbound", "productId": "TEST"}
        result = await self.repo.append_outbound_log(log)
        assert result == log

    async def test_get_slots_returns_dict(self):
        """get_slots 返回 dict 类型(初始 2 个库位)"""
        slots = await self.repo.get_slots()
        assert isinstance(slots, dict)
        assert len(slots) == 2
        assert slots["A1"] == "ZX42-2026L07"
        assert slots["A2"] == "ZX42-2026L05"

    async def test_inbound_outbound_independent(self):
        """入库和出库日志独立计数"""
        await self.repo.append_inbound_log({"i": 1})
        await self.repo.append_inbound_log({"i": 2})
        await self.repo.append_outbound_log({"o": 1})
        assert await self.repo.count_inbound() == 2
        assert await self.repo.count_outbound() == 1
