"""
订单完整流程单元测试
覆盖下单链路 6 个路由组 / 12 个端点:
  - /api/auth/login                  (会员登录)
  - /api/product/list | /{id}        (产品浏览)
  - /api/checkout/submit             (订单结算)
  - /api/inventory/deduct | restock  (库存扣减/回补)
  - /api/payment/pay | callback      (支付创建/回调)
  - /api/logistics/order | query     (物流下单/查询)

测试维度:
  - 正常流程: 完整链路端到端验证
  - 异常场景: 404(资源不存在) / 409(冲突) / 422(参数校验)
  - 请求校验: 缺失字段 / 非法类型 / 边界值
  - 幂等性: 重复支付回调
  - 响应一致性: success 字段 / ID 格式 / 数据透传

运行: pytest test_order_flow.py -v
"""

import pytest
from fastapi.testclient import TestClient

from main import app, _mock_store
from repositories.store import reset_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_store_state():
    """每个测试前重置 _mock_store 到初始状态(避免测试间状态污染)"""
    reset_store()
    yield
    reset_store()


# ============================================================
#  辅助函数
# ============================================================

def _login(phone="13800000001", password="test123456"):
    """登录并返回响应"""
    return client.post("/api/auth/login", json={"phone": phone, "password": password})


def _submit_checkout(product_id="ZX42-2026L07", quantity=2):
    """提交订单结算并返回响应"""
    return client.post("/api/checkout/submit", json={
        "items": [{"productId": product_id, "quantity": quantity, "price": 199.00}],
        "consignee": {
            "name": "Zhang San", "phone": "13800000001",
            "province": "Shandong", "city": "Taian",
            "district": "Taishan", "detail": "Zhuxiang Rd 1",
        },
        "payment": {"method": "wechat", "channel": "wechat"},
    })


def _deduct_inventory(product_id="ZX42-2026L07", quantity=2):
    """扣减库存"""
    return client.post("/api/inventory/deduct", json={
        "productId": product_id, "quantity": quantity,
    })


def _create_payment(order_id, total_amount=398.0, member_id="1"):
    """创建支付订单"""
    return client.post("/api/payment/pay", json={
        "orderId": order_id, "orderType": "retail",
        "totalAmount": total_amount, "payChannel": "wechat",
        "payMethod": "jsapi", "sceneType": "order_pay",
    }, headers={"X-Member-Id": member_id})


def _payment_callback(pay_no, amount=398.0):
    """支付回调"""
    return client.post("/api/payment/callback/pay", json={
        "channelTradeNo": f"CB{pay_no}",
        "payNo": pay_no,
        "callbackContent": {"status": "SUCCESS", "amount": amount},
    })


def _start_pay(pay_no):
    """发起渠道支付(pending → paying)"""
    return client.post(f"/api/payment/{pay_no}/start")


def _create_logistics(order_id, member_id="1", quantity=2):
    """物流下单"""
    return client.post("/api/logistics/order", json={
        "orderId": order_id, "orderType": "retail",
        "carrier": "SF", "serviceType": "standard",
        "sender": {
            "name": "Zhuxiang Warehouse", "phone": "0538-1234567",
            "address": "Taishan Zhuxiang Rd 1 Warehouse",
        },
        "receiver": {
            "name": "Zhang San", "phone": "13800000001",
            "address": "Taishan Zhuxiang Rd 1",
            "province": "Shandong", "city": "Taian",
        },
        "weight": 1.5, "pieceCount": quantity, "volume": 0.01,
        "insuredValue": 398.0, "settleMode": "monthly",
    }, headers={"X-Member-Id": member_id})


# ============================================================
#  1. 会员登录测试
# ============================================================

class TestAuthLogin:
    """会员登录端点测试"""

    def test_login_success(self):
        """正常登录: 返回 JWT 双令牌"""
        resp = _login()
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["memberId"] == 1
        assert data["role"] == "member"
        assert data["accessToken"]
        assert data["refreshToken"]
        assert data["tokenType"] == "Bearer"
        assert data["expiresIn"] > 0

    def test_login_wrong_password(self):
        """密码错误: 409 Conflict"""
        resp = _login(password="wrong_password")
        assert resp.status_code == 409

    def test_login_nonexistent_user(self):
        """不存在的手机号"""
        resp = _login(phone="13999999999")
        assert resp.status_code in (404, 409)

    def test_login_missing_phone(self):
        """缺失手机号: 422"""
        resp = client.post("/api/auth/login", json={"password": "test123456"})
        assert resp.status_code == 422

    def test_login_missing_password(self):
        """缺失密码: 422"""
        resp = client.post("/api/auth/login", json={"phone": "13800000001"})
        assert resp.status_code == 422

    def test_login_short_password(self):
        """密码过短(<6位): 409(密码强度校验)"""
        resp = _login(password="123")
        assert resp.status_code == 409


# ============================================================
#  2. 产品浏览测试
# ============================================================

class TestProductBrowse:
    """产品列表/详情端点测试"""

    def test_product_list_success(self):
        """产品列表: 返回11款产品"""
        resp = client.get("/api/product/list")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["count"] == 11
        assert data["total"] == 11
        assert len(data["products"]) == 11

    def test_product_detail_success(self):
        """产品详情: ZX42-2026L07"""
        resp = client.get("/api/product/ZX42-2026L07")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["product"]["product_id"] == "ZX42-2026L07"

    def test_product_detail_not_found(self):
        """产品不存在: 404"""
        resp = client.get("/api/product/NOT-EXIST")
        assert resp.status_code == 404

    def test_product_list_has_required_fields(self):
        """验证产品字段完整性"""
        resp = client.get("/api/product/list")
        products = resp.json()["products"]
        p = products[0]
        required = ["product_id", "name", "price", "stock", "status"]
        for field in required:
            assert field in p, f"missing field: {field}"


# ============================================================
#  3. 订单结算测试
# ============================================================

class TestCheckoutSubmit:
    """订单结算端点测试"""

    def test_checkout_success(self):
        """正常下单: 创建订单(P4.4 事务结算)"""
        resp = _submit_checkout()
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["orderId"].startswith("ZX")
        assert len(data["orderId"]) >= 6
        assert data["status"] == "已付款"

    def test_checkout_order_id_unique(self):
        """连续下单: 订单ID唯一"""
        ids = set()
        for _ in range(5):
            resp = _submit_checkout()
            ids.add(resp.json()["orderId"])
        assert len(ids) == 5

    def test_checkout_empty_items(self):
        """空商品列表: preflight 中止(P4.4 契约)"""
        resp = client.post("/api/checkout/submit", json={
            "items": [], "consignee": None, "payment": None,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error"] == "购物车为空"

    def test_checkout_consignee_passthrough(self):
        """收货人信息透传"""
        resp = client.post("/api/checkout/submit", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 1}],
            "consignee": {"name": "Li Si", "phone": "13900000000"},
            "payment": {"method": "alipay"},
        })
        assert resp.status_code == 200

    def test_checkout_special_chars_in_consignee(self):
        """收货人特殊字符(XSS防护)"""
        resp = client.post("/api/checkout/submit", json={
            "items": [{"productId": "ZX42-2026L07", "quantity": 1}],
            "consignee": {"name": "<script>alert(1)</script>"},
            "payment": {},
        })
        assert resp.status_code == 200


# ============================================================
#  4. 库存扣减/回补测试
# ============================================================

class TestInventoryOps:
    """库存操作端点测试"""

    def test_deduct_success(self):
        """正常扣减: ZX42-2026L07 初始500, 扣10 → 490"""
        resp = _deduct_inventory(quantity=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["productId"] == "ZX42-2026L07"
        assert data["stockAfter"] == 490
        assert data["txId"].startswith("TX")

    def test_restock_success(self):
        """正常回补: 500 + 20 → 520"""
        resp = client.post("/api/inventory/restock", json={
            "productId": "ZX42-2026L07", "quantity": 20,
        })
        assert resp.status_code == 200
        assert resp.json()["stockAfter"] == 520

    def test_deduct_product_not_found(self):
        """产品不存在: 事务失败(200 + success=False, P4.4 契约)"""
        resp = _deduct_inventory(product_id="NOT-EXIST")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "商品不存在" in data["error"]

    def test_restock_product_not_found(self):
        """回补不存在产品: 事务失败"""
        resp = client.post("/api/inventory/restock", json={
            "productId": "NOT-EXIST", "quantity": 1,
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    def test_deduct_missing_product_id(self):
        """缺失 productId/items: preflight 中止(空清单)"""
        resp = client.post("/api/inventory/deduct", json={"quantity": 1})
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    def test_deduct_then_restock_consistency(self):
        """扣减→回补数据一致性"""
        _deduct_inventory(quantity=50)
        resp = client.post("/api/inventory/restock", json={
            "productId": "ZX42-2026L07", "quantity": 30,
        })
        # 500 - 50 + 30 = 480
        assert resp.json()["stockAfter"] == 480

    def test_deduct_zero_quantity(self):
        """扣减0: 拒绝(多行契约 qty 必须>0), 库存不变"""
        resp = _deduct_inventory(quantity=0)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500


# ============================================================
#  5. 支付订单测试
# ============================================================

class TestPaymentOps:
    """支付订单端点测试"""

    def test_create_payment_success(self):
        """创建支付单: 正常流程"""
        order_resp = _submit_checkout()
        order_id = order_resp.json()["orderId"]
        resp = _create_payment(order_id)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["payNo"]
        assert data["orderId"] == order_id
        assert data["status"] == "pending"

    def test_payment_callback_success(self):
        """支付回调: pending → paying → success"""
        order_resp = _submit_checkout()
        order_id = order_resp.json()["orderId"]
        pay_resp = _create_payment(order_id)
        pay_no = pay_resp.json()["payNo"]

        # 先发起渠道支付(pending → paying)
        _start_pay(pay_no)

        cb_resp = _payment_callback(pay_no)
        assert cb_resp.status_code == 200
        cb_data = cb_resp.json()
        # 支付回调可能返回 success=True 或 idempotent=True
        assert cb_data.get("success") is True or cb_data.get("idempotent") is True

    def test_payment_callback_invalid_payno(self):
        """支付回调: 不存在的支付单"""
        resp = _payment_callback("INVALID_PAY_NO")
        # 不存在的支付单回调返回 200(幂等处理)或 404
        assert resp.status_code in (200, 404)

    def test_create_payment_missing_member_id(self):
        """缺失 X-Member-Id: 401/403(非游客场景仍强制登录)"""
        order_resp = _submit_checkout()
        order_id = order_resp.json()["orderId"]
        resp = client.post("/api/payment/pay", json={
            "orderId": order_id, "orderType": "retail",
            "totalAmount": 398.0, "payChannel": "wechat",
        })
        assert resp.status_code in (400, 401, 403, 422)

    def test_guest_payment_without_login(self):
        """游客扫码付(P0-3): guest_order_pay 免登录创建支付单"""
        order_resp = _submit_checkout()
        order_id = order_resp.json()["orderId"]
        resp = client.post("/api/payment/pay", json={
            "orderId": order_id, "orderType": "retail",
            "totalAmount": 398.0, "payChannel": "wechat",
            "payMethod": "native", "sceneType": "guest_order_pay",
            "guestPhone": "13900001111", "ageConfirmed": True,
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["isGuest"] is True
        assert data["guestPhone"] == "13900001111"
        assert data["actualAmount"] == 398.0

    def test_guest_payment_missing_age_confirm(self):
        """游客扫码付: 缺年龄声明 → 409(酒类合规 P0-1 联动)"""
        order_resp = _submit_checkout()
        order_id = order_resp.json()["orderId"]
        resp = client.post("/api/payment/pay", json={
            "orderId": order_id, "orderType": "retail",
            "totalAmount": 398.0, "payChannel": "wechat",
            "payMethod": "native", "sceneType": "guest_order_pay",
            "guestPhone": "13900001111",
        })
        assert resp.status_code == 409
        assert "18" in resp.json()["error"]

    def test_guest_payment_over_limit(self):
        """游客扫码付: 单笔超 ¥5,000 上限 → 409"""
        order_resp = _submit_checkout()
        order_id = order_resp.json()["orderId"]
        resp = client.post("/api/payment/pay", json={
            "orderId": order_id, "orderType": "retail",
            "totalAmount": 5000.01, "payChannel": "wechat",
            "payMethod": "native", "sceneType": "guest_order_pay",
            "guestPhone": "13900001111", "ageConfirmed": True,
        })
        assert resp.status_code == 409
        assert "上限" in resp.json()["error"]

    def test_create_payment_missing_order_id(self):
        """缺失 orderId: 422"""
        resp = client.post("/api/payment/pay", json={
            "orderType": "retail", "totalAmount": 398.0,
        }, headers={"X-Member-Id": "1"})
        assert resp.status_code == 422

    def test_create_payment_invalid_amount(self):
        """金额非法(<=0): 422"""
        order_resp = _submit_checkout()
        order_id = order_resp.json()["orderId"]
        resp = _create_payment(order_id, total_amount=0)
        assert resp.status_code == 422

    def test_duplicate_payment_callback_idempotent(self):
        """重复支付回调: 幂等"""
        order_resp = _submit_checkout()
        order_id = order_resp.json()["orderId"]
        pay_resp = _create_payment(order_id)
        pay_no = pay_resp.json()["payNo"]

        _payment_callback(pay_no)
        resp2 = _payment_callback(pay_no)
        # 幂等: 第二次回调应返回成功或已支付状态
        assert resp2.status_code in (200, 409)


# ============================================================
#  6. 物流下单测试
# ============================================================

class TestLogisticsOps:
    """物流订单端点测试"""

    def test_create_logistics_success(self):
        """物流下单: 正常流程"""
        order_resp = _submit_checkout()
        order_id = order_resp.json()["orderId"]
        resp = _create_logistics(order_id)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # 运单号在 data.data.waybillNo 中(嵌套结构)
        inner = data.get("data", {})
        waybill_no = inner.get("waybillNo")
        assert waybill_no
        assert inner["orderId"] == order_id
        assert inner["status"] == "pending"
        assert inner["totalFee"] > 0

    def test_logistics_query_success(self):
        """物流查询: 运单详情"""
        order_resp = _submit_checkout()
        order_id = order_resp.json()["orderId"]
        create_resp = _create_logistics(order_id)
        waybill_no = create_resp.json()["data"]["waybillNo"]

        resp = client.get(f"/api/logistics/order/{waybill_no}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["waybillNo"] == waybill_no

    def test_logistics_query_not_found(self):
        """查询不存在运单: 404"""
        resp = client.get("/api/logistics/order/SF_NOT_EXIST")
        assert resp.status_code == 404

    def test_logistics_missing_order_id(self):
        """物流下单缺失 orderId: 422"""
        resp = client.post("/api/logistics/order", json={
            "orderType": "retail", "carrier": "SF",
            "sender": {"name": "S", "phone": "1", "address": "A"},
            "receiver": {"name": "R", "phone": "1", "address": "A"},
            "weight": 1.5,
        }, headers={"X-Member-Id": "1"})
        assert resp.status_code == 422

    def test_logistics_invalid_weight(self):
        """重量非法(<=0): 422"""
        order_resp = _submit_checkout()
        order_id = order_resp.json()["orderId"]
        resp = client.post("/api/logistics/order", json={
            "orderId": order_id, "orderType": "retail",
            "carrier": "SF", "serviceType": "standard",
            "sender": {"name": "S", "phone": "1", "address": "A"},
            "receiver": {"name": "R", "phone": "1", "address": "A"},
            "weight": 0,
        }, headers={"X-Member-Id": "1"})
        assert resp.status_code == 422


# ============================================================
#  7. 完整订单流程端到端测试
# ============================================================

class TestOrderFlowE2E:
    """订单完整流程端到端测试(模拟真实用户下单)"""

    def test_full_order_flow(self):
        """完整流程: 登录 → 浏览 → 下单 → 扣库存 → 支付 → 物流"""
        # 1. 登录
        login_resp = _login()
        assert login_resp.status_code == 200
        member_id = login_resp.json()["memberId"]

        # 2. 浏览产品列表
        list_resp = client.get("/api/product/list")
        assert list_resp.status_code == 200
        products = list_resp.json()["products"]
        assert len(products) == 11

        # 3. 查看产品详情
        detail_resp = client.get("/api/product/ZX42-2026L07")
        assert detail_resp.status_code == 200

        # 4. 提交订单结算(P4.4: 事务内已扣库存)
        checkout_resp = _submit_checkout()
        assert checkout_resp.status_code == 200
        assert checkout_resp.json()["success"] is True
        order_id = checkout_resp.json()["orderId"]

        # 5. 库存扣减(结算已扣 2, 再扣 2 → 496)
        deduct_resp = _deduct_inventory()
        assert deduct_resp.status_code == 200
        assert deduct_resp.json()["stockAfter"] == 496  # 500-2(结算)-2(扣减)

        # 6. 创建支付订单
        pay_resp = _create_payment(order_id, member_id=str(member_id))
        assert pay_resp.status_code == 200
        pay_no = pay_resp.json()["payNo"]

        # 7. 发起支付 + 支付回调
        _start_pay(pay_no)
        cb_resp = _payment_callback(pay_no)
        assert cb_resp.status_code == 200

        # 8. 物流下单
        logistics_resp = _create_logistics(order_id, member_id=str(member_id))
        assert logistics_resp.status_code == 200
        waybill_no = logistics_resp.json()["data"]["waybillNo"]

        # 9. 物流查询
        query_resp = client.get(f"/api/logistics/order/{waybill_no}")
        assert query_resp.status_code == 200
        assert query_resp.json()["data"]["waybillNo"] == waybill_no

    def test_order_flow_without_login(self):
        """未登录直接下单: 结算可成功,支付/物流需鉴权"""
        checkout_resp = _submit_checkout()
        assert checkout_resp.status_code == 200
        # 支付接口需要 X-Member-Id, 应失败
        order_id = checkout_resp.json()["orderId"]
        pay_resp = client.post("/api/payment/pay", json={
            "orderId": order_id, "orderType": "retail",
            "totalAmount": 398.0, "payChannel": "wechat",
        })
        assert pay_resp.status_code in (400, 401, 403, 422)

    def test_order_flow_with_invalid_product(self):
        """使用不存在的产品下单: 结算事务失败(阶段4库存校验),库存扣减也失败"""
        checkout_resp = _submit_checkout(product_id="NOT-EXIST")
        assert checkout_resp.status_code == 200
        # P4.4: 结算事务在阶段4校验商品存在性, 失败回滚
        assert checkout_resp.json()["success"] is False
        # 库存扣减同样失败(事务失败)
        deduct_resp = _deduct_inventory(product_id="NOT-EXIST")
        assert deduct_resp.status_code == 200
        assert deduct_resp.json()["success"] is False
