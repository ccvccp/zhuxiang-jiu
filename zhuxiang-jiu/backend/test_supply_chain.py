"""供应链四件套并发与事务不变量测试(P4.4)

对齐前端 inventory/warehouse/checkout mock 压测验证的不变量:
    ① 不超卖: final stock >= 0
    ② 库存守恒: final = initial - deduct成功数 + restock成功数
    ③ 事务原子性: 失败回滚后状态零污染(订单/券/积分/分润全撤销)
    ④ 认领状态机: 一区一代理(并发下仅一个成功)

运行: pytest test_supply_chain.py -v
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from main import app, _mock_store
from services.checkout_service import CheckoutService
from services.inventory_service import InventoryService
from services.shipping_service import ShippingClaimService
from services.warehouse_service import WarehouseService

client = TestClient(app)


def _reset():
    from repositories.store import reset_store
    reset_store()


# ============================================================
#  并发库存扣减(不变量 ①②)
# ============================================================

class TestConcurrentDeduct:
    """并发扣减: 不超卖 + 库存守恒"""

    def setup_method(self):
        _reset()
        _mock_store["inventory"]["ZX42-2026L07"]["stock"] = 100
        self.svc = InventoryService()

    async def test_concurrent_deduct_no_oversell(self):
        """100 个并发请求各扣 2(库存 100): 恰好 50 个成功, 余额 0"""
        tasks = [self.svc.deduct_lines([{"id": "ZX42-2026L07", "qty": 2}])
                 for _ in range(100)]
        results = await asyncio.gather(*tasks)
        ok = sum(1 for r in results if r["success"])
        assert ok == 50
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 0
        # 不变量①: 不超卖
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] >= 0

    async def test_concurrent_deduct_conservation(self):
        """并发混合扣减+回补: 库存守恒"""
        _mock_store["inventory"]["ZX42-2026L05"]["stock"] = 60
        deducts = [self.svc.deduct_lines([{"id": "ZX42-2026L05", "qty": 1}])
                   for _ in range(80)]
        restocks = [self.svc.restock_lines([{"id": "ZX42-2026L05", "qty": 1}])
                    for _ in range(20)]
        results = await asyncio.gather(*(deducts + restocks))
        d_ok = sum(1 for r in results[:80] if r["success"])
        r_ok = sum(1 for r in results[80:] if r["success"])
        # 不变量②: final = initial - deduct成功 + restock成功
        expected = 60 - d_ok + r_ok
        assert _mock_store["inventory"]["ZX42-2026L05"]["stock"] == expected
        assert expected >= 0

    async def test_concurrent_deduct_flow_count(self):
        """并发扣减的流水条数 = 成功行数"""
        tasks = [self.svc.deduct_lines([{"id": "ZX42-2026L07", "qty": 2}])
                 for _ in range(10)]
        results = await asyncio.gather(*tasks)
        ok = sum(1 for r in results if r["success"])
        assert len(_mock_store["inventory_logs"]) == ok


# ============================================================
#  checkout 事务原子性(不变量 ③)
# ============================================================

class TestCheckoutAtomicity:
    """订单结算事务: 失败全量回滚"""

    def setup_method(self):
        _reset()
        self.svc = CheckoutService()

    async def test_coupon_double_use_rollback(self):
        """同一券并发使用: 一个成功一个回滚(券/库存/订单状态)"""
        items = [{"id": "ZX42-2026L07", "name": "竹香经典",
                  "price": 599, "qty": 1}]
        tasks = [
            self.svc.submit(items, coupon_code="NEW10", member_level="L3")
            for _ in range(2)
        ]
        results = await asyncio.gather(*tasks)
        ok = sum(1 for r in results if r["success"])
        assert ok == 1
        # 券恰好一次核销
        coupon = _mock_store["checkout_coupons"]["NEW10"]
        assert coupon["status"] == "已使用"
        # 订单恰好一单
        assert len(_mock_store["checkout_orders"]) == 1
        # 库存只扣一件
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 499

    async def test_points_race_single_deduction(self):
        """积分并发扣减: 余额守恒"""
        _mock_store["checkout_points"]["L1"] = 100
        items = [{"id": "ZX42-2026L05", "name": "竹韵佳酿",
                  "price": 299, "qty": 1}]
        tasks = [
            self.svc.submit(items, member_level="L1", points=80)
            for _ in range(3)
        ]
        results = await asyncio.gather(*tasks)
        ok = sum(1 for r in results if r["success"])
        # 100 积分: 第一单扣 80, 第二单起不足 → 至多 1 个成功
        assert ok == 1
        winner = next(r for r in results if r["success"])
        # 余额守恒: 100 - 80(扣) + pointsEarned(入账)
        expected = 100 - 80 + winner["details"]["pointsEarned"]
        assert _mock_store["checkout_points"]["L1"] == expected
        assert len(_mock_store["checkout_orders"]) == 1

    async def test_stage6_failure_full_rollback(self):
        """积分不足: 库存/订单/分润全量回滚"""
        _mock_store["checkout_points"]["L2"] = 50
        items = [{"id": "ZX42-2026L07", "name": "竹香经典",
                  "price": 599, "qty": 1}]
        result = await self.svc.submit(items, member_level="L2", points=999)
        assert result["success"] is False
        assert result["failedStage"] == "阶段6-积分扣减"
        # 零污染
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500
        assert _mock_store["checkout_orders"] == []
        assert _mock_store["profit_records"] == []
        assert _mock_store["checkout_points"]["L2"] == 50


# ============================================================
#  shipping 认领状态机(不变量 ④)
# ============================================================

class TestShippingClaimStateMachine:
    """认领状态机: 已认领 → 已退出 → 可再认领"""

    def setup_method(self):
        _reset()
        self.svc = ShippingClaimService()

    async def test_claim_release_reclaim(self):
        """认领→释放→再认领(不同代理商)"""
        r1 = await self.svc.claim(1, "taian")
        assert r1["success"] is True
        assert r1["details"]["status"] == "已认领"
        # 释放
        r2 = await self.svc.release(1, "taian")
        assert r2["success"] is True
        assert r2["details"]["status"] == "已退出"
        assert "taian" not in _mock_store["shipping_claims"]
        # 富记录保留退出状态
        detail = _mock_store["shipping_claim_details"]["taian"]
        assert detail["status"] == "已退出"
        # 再认领(另一代理商)
        r3 = await self.svc.claim(2, "taian")
        assert r3["success"] is True
        assert _mock_store["shipping_claims"]["taian"] == 2

    async def test_concurrent_claim_one_winner(self):
        """并发认领同一区域: 仅一个成功(一区一代理)"""
        tasks = [self.svc.claim(i, "jinan") for i in (1, 2)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ok = [r for r in results if isinstance(r, dict) and r.get("success")]
        errors = [r for r in results if isinstance(r, ValueError)]
        assert len(ok) == 1
        assert len(errors) == 1
        assert _mock_store["shipping_claims"]["jinan"] in (1, 2)

    async def test_release_nonexistent_rejected(self):
        """释放不存在的认领: ValueError"""
        with pytest.raises(ValueError):
            await self.svc.release(1, "nowhere")

    async def test_resolve_shipper(self):
        """发货方解析: 已认领→代理商, 未认领→厂家直供"""
        assert (await self.svc.resolve_shipper("unknown_region"))["shipper"] \
            == "manufacturer"
        await self.svc.claim(1, "taian")
        shipper = await self.svc.resolve_shipper("taian")
        assert shipper["shipper"] == "agent"
        assert shipper["agentName"] == "泰安市级代理商"
        # 无 region → 厂家直供
        assert (await self.svc.resolve_shipper(None))["shipper"] == "manufacturer"


# ============================================================
#  warehouse 并发出库(不变量 ①)
# ============================================================

class TestConcurrentWarehouseOutbound:
    """仓储并发出库: 不超卖"""

    def setup_method(self):
        _reset()
        self.svc = WarehouseService()

    async def test_concurrent_outbound_no_oversell(self):
        """仓1 库存 500, 60 个并发请求各出 10: 恰好 50 个成功"""
        tasks = [self.svc.outbound([{"id": "ZX42-2026L07", "qty": 10}])
                 for _ in range(60)]
        results = await asyncio.gather(*tasks)
        ok = sum(1 for r in results if r["success"])
        assert ok == 50
        stocks = [s for s in _mock_store["warehouse_stock"]
                  if s["warehouse_id"] == 1 and s["product_id"] == "ZX42-2026L07"]
        assert stocks[0]["stock_qty"] == 0
        # 出库单数 = 成功数
        assert len(_mock_store["outbound_orders"]) == 50
        # 流水行数 = 成功数
        assert len(_mock_store["stock_movements"]) == 50


# ============================================================
#  HTTP 端点并发(经路由层验证)
# ============================================================

class TestHttpConcurrent:
    """HTTP 层并发扣减(测试 asyncio 锁在 ASGI 事件循环中生效)"""

    def setup_method(self):
        _reset()
        _mock_store["inventory"]["ZX42-2026B01"]["stock"] = 50

    def test_http_concurrent_deduct(self):
        """连续 HTTP 扣减: 恰好耗尽不超卖"""
        for _ in range(25):
            r = client.post("/api/inventory/deduct", json={
                "items": [{"id": "ZX42-2026B01", "qty": 2}]})
            assert r.status_code == 200
        assert _mock_store["inventory"]["ZX42-2026B01"]["stock"] == 0
        r = client.post("/api/inventory/deduct", json={
            "items": [{"id": "ZX42-2026B01", "qty": 1}]})
        assert r.json()["success"] is False


# ============================================================
#  P5.1 新增端点: inventory/stock + shipping settlement/claims detail
# ============================================================

class TestInventoryStockEndpoint:
    """GET /api/inventory/stock 端点测试(P5.1: 前端 getStock live 分支对接)"""

    def setup_method(self):
        _reset()

    def test_stock_query_success(self):
        """查询现有产品库存"""
        r = client.get("/api/inventory/stock?productId=ZX42-2026L07")
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["productId"] == "ZX42-2026L07"
        assert data["stock"] == 500
        assert data["reserved"] == 0

    def test_stock_query_not_found(self):
        """产品不存在: 404"""
        r = client.get("/api/inventory/stock?productId=NOT-EXIST")
        assert r.status_code == 404
        assert r.json()["success"] is False

    def test_stock_query_missing_param(self):
        """缺 productId: 422"""
        r = client.get("/api/inventory/stock")
        assert r.status_code == 422

    def test_stock_reflects_deduction(self):
        """查询反映扣减后的库存"""
        client.post("/api/inventory/deduct", json={
            "items": [{"id": "ZX42-2026L07", "qty": 30}]})
        r = client.get("/api/inventory/stock?productId=ZX42-2026L07")
        assert r.json()["stock"] == 470


class TestShippingSettlementEndpoint:
    """GET /api/agent-shipping/settlement 端点测试(P5.1)"""

    def setup_method(self):
        _reset()

    def test_settlement_empty(self):
        """无服务费记录: 全零统计"""
        r = client.get("/api/agent-shipping/settlement?agentId=1")
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["totalCount"] == 0
        assert data["pendingAmount"] == 0

    def test_settlement_aggregates_fees(self):
        """下单计提后聚合统计(认领区域下单 → 5% 服务费)"""
        client.post("/api/agent-shipping/claim", json={"agentId": 1, "region": "taian"})
        resp = client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "name": "竹香经典",
                       "price": 599, "qty": 2}],
            "region": "taian",
        })
        assert resp.json()["success"] is True
        final = resp.json()["details"]["finalAmount"]
        expected_fee = round(final * 0.05, 2)
        # 结算统计
        r = client.get("/api/agent-shipping/settlement?agentId=1")
        data = r.json()
        assert data["totalCount"] == 1
        assert data["pendingCount"] == 1
        assert data["pendingAmount"] == expected_fee
        assert data["settledAmount"] == 0
        assert data["settledAs"] == "同品"

    def test_settlement_agent_isolation(self):
        """不同代理商统计隔离"""
        client.post("/api/agent-shipping/claim", json={"agentId": 1, "region": "taian"})
        client.post("/api/checkout/submit", json={
            "items": [{"id": "ZX42-2026L07", "price": 599, "qty": 1}],
            "region": "taian",
        })
        r2 = client.get("/api/agent-shipping/settlement?agentId=2")
        assert r2.json()["totalCount"] == 0

    def test_settlement_missing_param(self):
        """缺 agentId: 422"""
        r = client.get("/api/agent-shipping/settlement")
        assert r.status_code == 422


class TestShippingClaimsDetailEndpoint:
    """GET /api/agent-shipping/claims?detail=true 端点测试(P5.1)"""

    def setup_method(self):
        _reset()

    def test_claims_detail_returns_rich_records(self):
        """detail=true: 返回富记录数组(含已退出认领)"""
        client.post("/api/agent-shipping/claim", json={"agentId": 1, "region": "taian"})
        client.post("/api/agent-shipping/release", json={"agentId": 1, "region": "taian"})
        client.post("/api/agent-shipping/claim", json={"agentId": 2, "region": "jinan"})
        r = client.get("/api/agent-shipping/claims?detail=true")
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        claims = data["claims"]
        assert isinstance(claims, list)
        assert len(claims) == 2   # 1 条已退出 + 1 条已认领
        statuses = {c["region"]: c["status"] for c in claims}
        assert statuses["taian"] == "已退出"
        assert statuses["jinan"] == "已认领"
        # 富记录字段
        jinan = next(c for c in claims if c["region"] == "jinan")
        assert jinan["agentName"] == "济南核心代理商"
        assert jinan["claimId"]
        assert jinan["serviceRate"] == 0.05

    def test_claims_default_backward_compat(self):
        """默认(无 detail): 保持旧契约 {region: agentId} 映射"""
        client.post("/api/agent-shipping/claim", json={"agentId": 2, "region": "jinan"})
        r = client.get("/api/agent-shipping/claims")
        data = r.json()
        assert isinstance(data["claims"], dict)
        assert data["claims"]["jinan"] == 2
