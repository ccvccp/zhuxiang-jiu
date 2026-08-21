"""代理商管理模块单元测试(MVP)

覆盖 10 个端点 / 3 个业务域:
  - 准入管理(3):  apply / audit / applications
  - 档案管理(4):  list / detail / update / levels
  - 进货管理(3):  purchase / purchases(list) / purchases(detail)

测试维度:
  - 成功路径 / 错误路径(404/409/422)
  - 权限守卫(X-Agent-Id 缺失→401, 越权→403, X-Role:admin 缺失→403)
  - 业务规则(等级折扣/钱包扣减/库存扣减/状态校验/重复审核)
  - 边界场景(分页/筛选/空 items/余额不足/库存不足)
  - 状态持久化(store 回查)

初始数据(reset_store 后):
  - agent 1: level=C, wallet=50000, status=active, 折扣 0.75
  - agent 2: level=B, wallet=120000, status=active, 折扣 0.7
  - _agent_seq=2, _agent_apply_seq=0
  - 库存: ZX42-2026L07(stock=500, price=268), ZX42-2026B01(stock=800, price=88)

运行: pytest test_agent_routes.py -v
"""

import pytest
from fastapi.testclient import TestClient

from main import app, _mock_store
from repositories.store import reset_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_agent_store():
    """每个测试前重置 store 到初始状态(agent1: C/50000, agent2: B/120000)"""
    reset_store()
    yield


# 等级折扣速查(与 services/agent_service.py 的 LEVEL_DISCOUNTS 一致)
DISCOUNT_C = 0.75
DISCOUNT_B = 0.7


# ============================================================
#  准入管理: POST /api/agent/apply
# ============================================================

class TestAgentApply:
    def test_apply_success(self):
        """正常申请: 返回 applyId=1, status=pending"""
        resp = client.post("/api/agent/apply", json={
            "companyName": "青岛竹香酒业", "contactName": "赵经理",
            "contactPhone": "13900001111", "region": "山东省青岛市",
            "applyLevel": "B",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["applyId"] == 1
        assert data["status"] == "pending"
        assert data["applyLevel"] == "B"

    def test_apply_missing_field(self):
        """缺字段: 422"""
        resp = client.post("/api/agent/apply", json={
            "companyName": "青岛竹香酒业", "contactName": "赵经理",
        })
        assert resp.status_code == 422

    def test_apply_invalid_level(self):
        """申请等级非法: 409"""
        resp = client.post("/api/agent/apply", json={
            "companyName": "青岛竹香酒业", "contactName": "赵经理",
            "contactPhone": "13900001111", "region": "山东省青岛市",
            "applyLevel": "X",
        })
        assert resp.status_code == 409
        assert "非法" in resp.json()["error"]

    def test_apply_persisted(self):
        """申请持久化到 store"""
        resp = client.post("/api/agent/apply", json={
            "companyName": "青岛竹香酒业", "contactName": "赵经理",
            "contactPhone": "13900001111", "region": "山东省青岛市",
            "applyLevel": "C",
        })
        apply_id = resp.json()["applyId"]
        assert apply_id in _mock_store["agent_applications"]
        assert _mock_store["agent_applications"][apply_id]["company_name"] == "青岛竹香酒业"

    def test_apply_seq_increments(self):
        """连续申请: applyId 自增"""
        id1 = client.post("/api/agent/apply", json={
            "companyName": "A", "contactName": "a", "contactPhone": "1",
            "region": "r", "applyLevel": "C",
        }).json()["applyId"]
        id2 = client.post("/api/agent/apply", json={
            "companyName": "B", "contactName": "b", "contactPhone": "2",
            "region": "r", "applyLevel": "D",
        }).json()["applyId"]
        assert id2 == id1 + 1


# ============================================================
#  准入管理: POST /api/agent/audit/{apply_id}
# ============================================================

class TestAgentAudit:
    def _apply(self, level="B"):
        return client.post("/api/agent/apply", json={
            "companyName": "青岛竹香酒业", "contactName": "赵经理",
            "contactPhone": "13900001111", "region": "山东省青岛市",
            "applyLevel": level,
        }).json()["applyId"]

    def test_audit_approve_creates_agent(self):
        """审核通过: 创建代理商档案(agentId=3, 等级=申请等级)"""
        apply_id = self._apply("A")
        resp = client.post(f"/api/agent/audit/{apply_id}", json={
            "decision": "approved", "auditRemark": "资料齐全",
        }, headers={"X-Role": "admin"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["decision"] == "approved"
        assert data["agentId"] == 3  # _agent_seq 从 2 自增
        assert data["level"] == "A"
        # 档案已持久化
        assert 3 in _mock_store["agents"]
        assert _mock_store["agents"][3]["level"] == "A"
        assert _mock_store["agents"][3]["wallet"] == 0
        assert _mock_store["agents"][3]["status"] == "active"
        # 申请状态已更新
        assert _mock_store["agent_applications"][apply_id]["status"] == "approved"

    def test_audit_reject_no_agent_created(self):
        """审核拒绝: 不创建代理商"""
        apply_id = self._apply("C")
        resp = client.post(f"/api/agent/audit/{apply_id}", json={
            "decision": "rejected", "auditRemark": "资料不全",
        }, headers={"X-Role": "admin"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "rejected"
        assert data["auditRemark"] == "资料不全"
        # 未新增代理商
        assert len(_mock_store["agents"]) == 2
        assert _mock_store["agent_applications"][apply_id]["status"] == "rejected"

    def test_audit_no_admin(self):
        """非 admin: 403"""
        apply_id = self._apply()
        resp = client.post(f"/api/agent/audit/{apply_id}",
                           json={"decision": "approved"})
        assert resp.status_code == 403

    def test_audit_not_found(self):
        """申请不存在: 404"""
        resp = client.post("/api/agent/audit/999",
                           json={"decision": "approved"},
                           headers={"X-Role": "admin"})
        assert resp.status_code == 404

    def test_audit_already_processed(self):
        """重复审核: 409"""
        apply_id = self._apply()
        client.post(f"/api/agent/audit/{apply_id}",
                    json={"decision": "approved"},
                    headers={"X-Role": "admin"})
        resp = client.post(f"/api/agent/audit/{apply_id}",
                          json={"decision": "rejected"},
                          headers={"X-Role": "admin"})
        assert resp.status_code == 409
        assert "已处理" in resp.json()["error"]

    def test_audit_invalid_decision(self):
        """decision 非法: 409"""
        apply_id = self._apply()
        resp = client.post(f"/api/agent/audit/{apply_id}",
                           json={"decision": "foo"},
                           headers={"X-Role": "admin"})
        assert resp.status_code == 409


# ============================================================
#  准入管理: GET /api/agent/applications
# ============================================================

class TestAgentApplications:
    def test_applications_list_admin(self):
        """admin 列表: 200"""
        client.post("/api/agent/apply", json={
            "companyName": "A", "contactName": "a", "contactPhone": "1",
            "region": "r", "applyLevel": "C",
        })
        resp = client.get("/api/agent/applications",
                          headers={"X-Role": "admin"})
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_applications_filter_status(self):
        """状态筛选: pending→1, approved→0(审核前)"""
        client.post("/api/agent/apply", json={
            "companyName": "A", "contactName": "a", "contactPhone": "1",
            "region": "r", "applyLevel": "C",
        })
        assert client.get("/api/agent/applications?status=pending",
                          headers={"X-Role": "admin"}).json()["count"] == 1
        assert client.get("/api/agent/applications?status=approved",
                          headers={"X-Role": "admin"}).json()["count"] == 0

    def test_applications_no_admin(self):
        """非 admin: 403"""
        resp = client.get("/api/agent/applications")
        assert resp.status_code == 403


# ============================================================
#  档案管理: GET /api/agent/levels
# ============================================================

class TestAgentLevels:
    def test_levels(self):
        """等级体系: 5 级, S 折扣 0.6, D 折扣 0.8"""
        resp = client.get("/api/agent/levels")
        assert resp.status_code == 200
        levels = resp.json()["levels"]
        assert len(levels) == 5
        by_code = {l["level"]: l for l in levels}
        assert by_code["S"]["discountRate"] == 0.6
        assert by_code["D"]["discountRate"] == 0.8
        assert by_code["S"]["name"] == "顶级代理商"
        assert "rights" in by_code["S"]


# ============================================================
#  档案管理: GET /api/agent/list
# ============================================================

class TestAgentList:
    def test_list_default(self):
        """默认列表: 2 个代理商"""
        resp = client.get("/api/agent/list")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["page"] == 1
        assert len(data["agents"]) == 2
        # 装饰字段存在
        assert "levelName" in data["agents"][0]
        assert "statusName" in data["agents"][0]

    def test_list_filter_level(self):
        """等级筛选: level=C → 1 条"""
        resp = client.get("/api/agent/list?level=C")
        assert resp.json()["count"] == 1
        assert resp.json()["agents"][0]["level"] == "C"

    def test_list_filter_status(self):
        """状态筛选: status=active → 2 条"""
        resp = client.get("/api/agent/list?status=active")
        assert resp.json()["count"] == 2

    def test_list_no_match(self):
        """无匹配: level=S → 0 条"""
        resp = client.get("/api/agent/list?level=S")
        assert resp.json()["count"] == 0
        assert resp.json()["agents"] == []

    def test_list_pagination(self):
        """分页: page_size=1 → 第 1 页 1 条, 总数 2"""
        resp = client.get("/api/agent/list?page=1&page_size=1")
        data = resp.json()
        assert data["count"] == 2
        assert len(data["agents"]) == 1
        assert data["page"] == 1
        assert data["pageSize"] == 1

    def test_list_static_path_not_shadowed(self):
        """静态路径 /api/agent/list 不被 /{agent_id} 参数路由吞掉(返回 200 而非 422)"""
        resp = client.get("/api/agent/list")
        assert resp.status_code == 200


# ============================================================
#  档案管理: GET /api/agent/{agent_id}
# ============================================================

class TestAgentDetail:
    def test_detail_success(self):
        """详情: 含 levelName/discountRate"""
        resp = client.get("/api/agent/1")
        assert resp.status_code == 200
        agent = resp.json()["agent"]
        assert agent["id"] == 1
        assert agent["level"] == "C"
        assert agent["levelName"] == "初级代理商"
        assert agent["discountRate"] == 0.75
        assert agent["statusName"] == "正常"

    def test_detail_not_found(self):
        """代理商不存在: 404"""
        resp = client.get("/api/agent/999")
        assert resp.status_code == 404


# ============================================================
#  档案管理: PUT /api/agent/{agent_id}
# ============================================================

class TestAgentUpdate:
    def test_update_success(self):
        """更新资料: contact_name/contact_phone/address"""
        resp = client.put("/api/agent/1", json={
            "contactName": "新经理", "contactPhone": "13900002222",
            "address": "新地址 99 号",
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        agent = resp.json()["agent"]
        assert agent["contact_name"] == "新经理"
        assert agent["contact_phone"] == "13900002222"
        assert agent["address"] == "新地址 99 号"
        # 持久化
        assert _mock_store["agents"][1]["contact_name"] == "新经理"

    def test_update_no_auth(self):
        """未登录: 401"""
        resp = client.put("/api/agent/1", json={"contactName": "X"})
        assert resp.status_code == 401

    def test_update_other_agent_forbidden(self):
        """越权更新他人档案: 403"""
        resp = client.put("/api/agent/1", json={"contactName": "X"},
                          headers={"X-Agent-Id": "2"})
        assert resp.status_code == 403

    def test_update_not_found(self):
        """代理商不存在: 404"""
        resp = client.put("/api/agent/999", json={"contactName": "X"},
                          headers={"X-Agent-Id": "999"})
        assert resp.status_code == 404

    def test_update_no_fields(self):
        """无可更新字段: 409"""
        resp = client.put("/api/agent/1", json={},
                          headers={"X-Agent-Id": "1"})
        assert resp.status_code == 409

    def test_update_partial(self):
        """部分更新(仅 address)"""
        resp = client.put("/api/agent/1", json={"address": "仅改地址"},
                          headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        assert resp.json()["agent"]["address"] == "仅改地址"


# ============================================================
#  进货管理: POST /api/agent/{agent_id}/purchase
# ============================================================

class TestAgentPurchase:
    def test_purchase_success(self):
        """进货成功: 扣钱包 + 扣库存 + 记录(C 等级 0.75 折扣)
        268 × 1 × 0.75 = 201.00; wallet 50000 → 49799; stock 500 → 499
        """
        resp = client.post("/api/agent/1/purchase", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 1}],
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["totalAmount"] == 201.0
        assert data["goodsTotal"] == 268.0
        assert data["discountRate"] == 0.75
        assert data["wallet"] == 49799.0
        assert "purchaseId" in data
        # 库存扣减
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 499
        # 累计进货额
        assert _mock_store["agents"][1]["total_purchases"] == 201.0

    def test_purchase_multi_item_b_level(self):
        """多商品 B 等级折扣 0.7
        (268 + 88) × 0.7 = 249.2; wallet 120000 → 119750.8
        """
        resp = client.post("/api/agent/2/purchase", json={
            "items": [
                {"productId": "ZX42-2026L07", "quantity": 1},
                {"productId": "ZX42-2026B01", "quantity": 1},
            ],
        }, headers={"X-Agent-Id": "2"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["discountRate"] == 0.7
        assert data["goodsTotal"] == 356.0
        assert data["totalAmount"] == 249.2
        assert data["wallet"] == pytest.approx(119750.8)

    def test_purchase_no_auth(self):
        """未登录: 401"""
        resp = client.post("/api/agent/1/purchase", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 1}],
        })
        assert resp.status_code == 401

    def test_purchase_other_agent_forbidden(self):
        """越权为他人进货: 403"""
        resp = client.post("/api/agent/1/purchase", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 1}],
        }, headers={"X-Agent-Id": "2"})
        assert resp.status_code == 403

    def test_purchase_agent_not_found(self):
        """代理商不存在: 404"""
        resp = client.post("/api/agent/999/purchase", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 1}],
        }, headers={"X-Agent-Id": "999"})
        assert resp.status_code == 404

    def test_purchase_product_not_found(self):
        """商品不存在: 409"""
        resp = client.post("/api/agent/1/purchase", json={
            "items": [{"productId": "NO_SUCH_PRODUCT", "quantity": 1}],
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 409
        assert "不存在" in resp.json()["error"]

    def test_purchase_insufficient_stock(self):
        """库存不足: 409(且不扣钱包/库存)"""
        resp = client.post("/api/agent/1/purchase", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 999}],
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 409
        assert "库存不足" in resp.json()["error"]
        # 库存未变
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500
        # 钱包未变
        assert _mock_store["agents"][1]["wallet"] == 50000

    def test_purchase_insufficient_wallet(self):
        """钱包不足: 409"""
        _mock_store["agents"][1]["wallet"] = 100  # 远不够 201
        resp = client.post("/api/agent/1/purchase", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 1}],
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 409
        assert "余额不足" in resp.json()["error"]

    def test_purchase_suspended_agent(self):
        """暂停状态代理商: 409"""
        _mock_store["agents"][1]["status"] = "suspended"
        resp = client.post("/api/agent/1/purchase", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 1}],
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 409
        assert "状态异常" in resp.json()["error"]

    def test_purchase_empty_items(self):
        """空 items: 422(min_length=1)"""
        resp = client.post("/api/agent/1/purchase", json={
            "items": [],
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 422

    def test_purchase_zero_quantity(self):
        """数量 0: 422(gt=0)"""
        resp = client.post("/api/agent/1/purchase", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 0}],
        }, headers={"X-Agent-Id": "1"})
        assert resp.status_code == 422


# ============================================================
#  进货管理: GET /api/agent/{agent_id}/purchases
# ============================================================

class TestAgentPurchasesList:
    def _purchase(self, agent_id=1, pid="ZX42-2026L07", qty=1):
        return client.post(f"/api/agent/{agent_id}/purchase", json={
            "items": [{"productId": pid, "quantity": qty}],
        }, headers={"X-Agent-Id": str(agent_id)}).json()["purchaseId"]

    def test_purchases_list(self):
        """进货记录列表: 1 条"""
        self._purchase()
        resp = client.get("/api/agent/1/purchases",
                          headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["purchases"][0]["purchaseId"].startswith("AP")
        assert data["purchases"][0]["totalAmount"] == 201.0

    def test_purchases_pagination(self):
        """分页: 进 2 次, page_size=1 → 第 1 页 1 条, 总数 2"""
        self._purchase()
        self._purchase()
        resp = client.get("/api/agent/1/purchases?page=1&page_size=1",
                          headers={"X-Agent-Id": "1"})
        data = resp.json()
        assert data["count"] == 2
        assert len(data["purchases"]) == 1

    def test_purchases_empty(self):
        """无进货记录: count=0"""
        resp = client.get("/api/agent/1/purchases",
                          headers={"X-Agent-Id": "1"})
        assert resp.json()["count"] == 0
        assert resp.json()["purchases"] == []

    def test_purchases_no_auth(self):
        """未登录: 401"""
        resp = client.get("/api/agent/1/purchases")
        assert resp.status_code == 401

    def test_purchases_other_agent_forbidden(self):
        """越权查询他人进货: 403"""
        resp = client.get("/api/agent/1/purchases",
                          headers={"X-Agent-Id": "2"})
        assert resp.status_code == 403

    def test_purchases_agent_not_found(self):
        """代理商不存在: 404"""
        resp = client.get("/api/agent/999/purchases",
                          headers={"X-Agent-Id": "999"})
        assert resp.status_code == 404


# ============================================================
#  进货管理: GET /api/agent/{agent_id}/purchases/{purchase_id}
# ============================================================

class TestAgentPurchaseDetail:
    def _purchase(self, agent_id=1):
        return client.post(f"/api/agent/{agent_id}/purchase", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 1}],
        }, headers={"X-Agent-Id": str(agent_id)}).json()["purchaseId"]

    def test_purchase_detail_success(self):
        """进货明细: items 含 productName/subtotal"""
        pid = self._purchase()
        resp = client.get(f"/api/agent/1/purchases/{pid}",
                          headers={"X-Agent-Id": "1"})
        assert resp.status_code == 200
        purchase = resp.json()["purchase"]
        assert purchase["purchaseId"] == pid
        assert purchase["agentId"] == 1
        assert len(purchase["items"]) == 1
        assert purchase["items"][0]["productName"] == "竹奕·竹香经典 42° 500ml"
        assert purchase["items"][0]["subtotal"] == 268.0

    def test_purchase_detail_not_found(self):
        """进货记录不存在: 404"""
        resp = client.get("/api/agent/1/purchases/AP_NO_SUCH",
                          headers={"X-Agent-Id": "1"})
        assert resp.status_code == 404

    def test_purchase_detail_wrong_agent(self):
        """越权查询他人进货明细: 404(属于 agent1, 用 agent2 查)"""
        pid = self._purchase(agent_id=1)
        resp = client.get(f"/api/agent/2/purchases/{pid}",
                          headers={"X-Agent-Id": "2"})
        assert resp.status_code == 404

    def test_purchase_detail_no_auth(self):
        """未登录: 401"""
        pid = self._purchase()
        resp = client.get(f"/api/agent/1/purchases/{pid}")
        assert resp.status_code == 401
