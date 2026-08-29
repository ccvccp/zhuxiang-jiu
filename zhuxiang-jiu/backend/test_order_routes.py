"""订单管理模块单元测试(17 端点)

测试框架: pytest + pytest-asyncio
HTTP 客户端: httpx.AsyncClient + ASGITransport(直连 ASGI app, 走完整 FastAPI 栈)
数据隔离: 每个测试前 reset_store() 重置内存 + 清空 asyncio 锁(跨事件循环安全)

覆盖维度:
  - 成功路径 / 错误路径 (401/403/404/409)
  - 价格计算引擎 (L1 不打折 / 优惠券 / 积分抵扣 / 运费)
  - 状态流转 (创建→支付→发货→收货→评价→退货→退款)
  - 售后退款 (库存回滚 + 积分扣回)
  - 超时自动处理 (关闭/确认/完成)
  - 权限守卫 (X-Member-Id 缺失→401, 非 admin→403)

商品: ZX42-2026L07 (stock=500, ¥268)
会员: memberId=1, points=100, level=1 (L1 不打折, discountRate=1.0)

运行: pytest test_order_routes.py -v
"""

import pytest
import httpx

from main import app
from repositories.store import _mock_store, reset_store
from core.locks import _async_locks


# ============================================================
# 常量与公共数据
# ============================================================

MEMBER_HEADERS = {"X-Member-Id": "1"}
ADMIN_HEADERS = {"X-Role": "admin"}

ITEMS = [
    {
        "productId": "ZX42-2026L07",
        "productName": "竹奕·竹香型 42° 500ml",
        "quantity": 2,
        "unitPrice": 268.00,
    }
]
ADDRESS = {
    "name": "张三",
    "phone": "13800000001",
    "province": "山东省",
    "city": "泰安市",
    "district": "泰山区",
    "detail": "竹香路1号",
}


# ============================================================
# Fixture: 每个测试前重置内存数据 + 清空锁
# ============================================================

@pytest.fixture
async def client():
    """重置 store 与 asyncio 锁, 返回直连 ASGI 的异步客户端

    清空 _async_locks 是为避免上一轮事件循环中创建的 asyncio.Lock
    被当前循环复用导致的 "Future attached to a different loop" 错误。
    """
    reset_store()
    _async_locks.clear()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ============================================================
# 流转辅助函数(均断言 200, 用于构造前置状态)
# ============================================================

async def _create(client, member_id=1, use_points=0, items=None):
    """创建订单, 返回 orderId"""
    body = {
        "items": items or ITEMS,
        "address": ADDRESS,
        "usePoints": use_points,
        "remark": "",
    }
    resp = await client.post(
        "/api/order/create", json=body,
        headers={"X-Member-Id": str(member_id)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["orderId"]


async def _pay(client, order_id):
    resp = await client.post(
        f"/api/order/{order_id}/pay", json={}, headers=MEMBER_HEADERS,
    )
    assert resp.status_code == 200, resp.text


async def _ship(client, order_id):
    resp = await client.post(
        f"/api/order/{order_id}/ship",
        json={"carrier": "顺丰", "waybillNo": "SF0001"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text


async def _confirm(client, order_id):
    resp = await client.post(
        f"/api/order/{order_id}/confirm", headers=MEMBER_HEADERS,
    )
    assert resp.status_code == 200, resp.text


async def _review(client, order_id, rating=5):
    resp = await client.post(
        f"/api/order/{order_id}/review",
        json={"rating": rating, "content": "好酒"},
        headers=MEMBER_HEADERS,
    )
    assert resp.status_code == 200, resp.text


async def _apply_return(client, order_id, reason="不想要了"):
    resp = await client.post(
        f"/api/order/{order_id}/return",
        json={"reason": reason}, headers=MEMBER_HEADERS,
    )
    assert resp.status_code == 200, resp.text


async def _to_shipped(client, order_id):
    await _pay(client, order_id)
    await _ship(client, order_id)


async def _to_received(client, order_id):
    await _to_shipped(client, order_id)
    await _confirm(client, order_id)


async def _to_completed(client, order_id):
    await _to_received(client, order_id)
    await _review(client, order_id)


async def _to_returning(client, order_id):
    await _to_completed(client, order_id)
    await _apply_return(client, order_id)


# ============================================================
# 1. 创建订单: POST /api/order/create
# ============================================================

class TestOrderCreate:
    """创建订单(6): 成功/库存不足/商品不存在/积分不足/会员不存在/未登录"""

    async def test_create_success(self, client):
        resp = await client.post(
            "/api/order/create",
            json={"items": ITEMS, "address": ADDRESS,
                  "usePoints": 0, "remark": "备注"},
            headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "PENDING"
        assert data["statusName"] == "待付款"
        assert data["memberId"] == 1
        assert data["usedPoints"] == 0
        pd = data["priceDetail"]
        assert pd["goodsTotal"] == 536.0
        assert pd["actualAmount"] == 536.0
        assert pd["discountRate"] == 1.0
        # 库存预扣 500 → 498
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 498
        assert data["orderId"]

    async def test_create_insufficient_stock(self, client):
        items = [{
            "productId": "ZX42-2026L07", "productName": "x",
            "quantity": 600, "unitPrice": 268.00,
        }]
        resp = await client.post(
            "/api/order/create",
            json={"items": items, "address": ADDRESS,
                  "usePoints": 0, "remark": ""},
            headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409
        assert "库存不足" in resp.json()["error"]

    async def test_create_product_not_found(self, client):
        items = [{
            "productId": "ZX42-NOPE", "productName": "x",
            "quantity": 1, "unitPrice": 100.00,
        }]
        resp = await client.post(
            "/api/order/create",
            json={"items": items, "address": ADDRESS,
                  "usePoints": 0, "remark": ""},
            headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409
        assert "不存在" in resp.json()["error"]

    async def test_create_insufficient_points(self, client):
        # 会员仅 100 积分, 使用 200 → 积分不足(ValueError→409)
        resp = await client.post(
            "/api/order/create",
            json={"items": ITEMS, "address": ADDRESS,
                  "usePoints": 200, "remark": ""},
            headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409
        assert "不足" in resp.json()["error"]

    async def test_create_member_not_found(self, client):
        resp = await client.post(
            "/api/order/create",
            json={"items": ITEMS, "address": ADDRESS,
                  "usePoints": 0, "remark": ""},
            headers={"X-Member-Id": "999"},
        )
        assert resp.status_code == 404

    async def test_create_no_auth(self, client):
        resp = await client.post(
            "/api/order/create",
            json={"items": ITEMS, "address": ADDRESS,
                  "usePoints": 0, "remark": ""},
        )
        assert resp.status_code == 401


class TestOrderAgeGate:
    """酒类合规年龄门(P0-1): 未声明拒绝 / 首次声明放行并回写 / 未成年硬拦截"""

    async def _register_fresh_member(self, client):
        """注册无成年声明的新会员(注册不传 birthdate/ageConfirmed)"""
        reg = await client.post("/api/member/register", json={
            "phone": "13900000000", "password": "abc123456",
        })
        assert reg.status_code == 200, reg.text
        return reg.json()["memberId"]

    async def test_age_confirm_required(self, client):
        """新会员下单未带 ageConfirmed → 409 要求成年声明"""
        member_id = await self._register_fresh_member(client)
        resp = await client.post(
            "/api/order/create",
            json={"items": ITEMS, "address": ADDRESS,
                  "usePoints": 0, "remark": ""},
            headers={"X-Member-Id": str(member_id)},
        )
        assert resp.status_code == 409
        assert "18" in resp.json()["error"]

    async def test_age_confirmed_first_order(self, client):
        """首次下单带 ageConfirmed=true → 成功且回写会员标记"""
        member_id = await self._register_fresh_member(client)
        resp = await client.post(
            "/api/order/create",
            json={"items": ITEMS, "address": ADDRESS,
                  "usePoints": 0, "remark": "", "ageConfirmed": True},
            headers={"X-Member-Id": str(member_id)},
        )
        assert resp.status_code == 200, resp.text
        assert _mock_store["members"][member_id]["ageConfirmed"] is True

    async def test_age_confirmed_persisted_second_order(self, client):
        """首次声明后二次下单免重复确认"""
        member_id = await self._register_fresh_member(client)
        first = await client.post(
            "/api/order/create",
            json={"items": ITEMS, "address": ADDRESS,
                  "usePoints": 0, "remark": "", "ageConfirmed": True},
            headers={"X-Member-Id": str(member_id)},
        )
        assert first.status_code == 200
        second = await client.post(
            "/api/order/create",
            json={"items": ITEMS, "address": ADDRESS,
                  "usePoints": 0, "remark": ""},
            headers={"X-Member-Id": str(member_id)},
        )
        assert second.status_code == 200

    async def test_minor_member_blocked(self, client):
        """会员出生日期未成年 → 硬拦截(即使曾声明)"""
        _mock_store["members"][1]["birthdate"] = "2012-01-01"
        _mock_store["members"][1]["ageVerified"] = True
        _mock_store["members"][1]["ageConfirmed"] = True
        resp = await client.post(
            "/api/order/create",
            json={"items": ITEMS, "address": ADDRESS,
                  "usePoints": 0, "remark": ""},
            headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409
        assert "未成年人" in resp.json()["error"]


# ============================================================
# 2. 价格试算: POST /api/order/price/preview
# ============================================================

class TestOrderPricePreview:
    """价格试算(4): 成功/L1不打折/积分抵扣/会员不存在"""

    async def test_preview_success(self, client):
        resp = await client.post(
            "/api/order/price/preview",
            json={"items": ITEMS, "usePoints": 0},
            headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["memberId"] == 1
        assert data["memberLevel"] == 1
        pd = data["priceDetail"]
        assert pd["goodsTotal"] == 536.0
        assert pd["actualAmount"] == 536.0

    async def test_preview_l1_no_discount(self, client):
        # L1 折扣率 1.0: 会员折扣=0, 优惠券 536<1000 不减, 满 99 免运费
        resp = await client.post(
            "/api/order/price/preview",
            json={"items": ITEMS, "usePoints": 0},
            headers=MEMBER_HEADERS,
        )
        pd = resp.json()["priceDetail"]
        assert pd["memberDiscount"] == 0.0
        assert pd["discountRate"] == 1.0
        assert pd["couponDiscount"] == 0.0
        assert pd["shippingFee"] == 0

    async def test_preview_points_deduction(self, client):
        # 100 竹叶 = ¥1, 上限 536*30%=160.8, 实抵 ¥1 → 实付 535
        resp = await client.post(
            "/api/order/price/preview",
            json={"items": ITEMS, "usePoints": 100},
            headers=MEMBER_HEADERS,
        )
        pd = resp.json()["priceDetail"]
        assert pd["pointsDiscount"] == -1.0
        assert pd["actualAmount"] == 535.0

    async def test_preview_member_not_found(self, client):
        resp = await client.post(
            "/api/order/price/preview",
            json={"items": ITEMS, "usePoints": 0},
            headers={"X-Member-Id": "999"},
        )
        assert resp.status_code == 404


# ============================================================
# 3. 查询: 详情/我的订单/状态列表/管理端列表
# ============================================================

class TestOrderQuery:
    """订单查询(6)"""

    async def test_get_detail_success(self, client):
        order_id = await _create(client)
        resp = await client.get(
            f"/api/order/{order_id}", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["order"]["orderId"] == order_id
        assert data["order"]["statusName"] == "待付款"

    async def test_get_detail_not_found(self, client):
        resp = await client.get(
            "/api/order/RT_NOPE", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 404

    async def test_my_orders(self, client):
        await _create(client)
        resp = await client.get("/api/order/my", headers=MEMBER_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["orders"][0]["status"] == "PENDING"

    async def test_my_orders_by_status(self, client):
        await _create(client)  # PENDING
        resp = await client.get(
            "/api/order/my?status=PENDING", headers=MEMBER_HEADERS,
        )
        assert resp.json()["count"] == 1
        resp2 = await client.get(
            "/api/order/my?status=PAID", headers=MEMBER_HEADERS,
        )
        assert resp2.json()["count"] == 0

    async def test_list_statuses(self, client):
        resp = await client.get("/api/order/statuses")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["statuses"]) == 9
        codes = {s["code"] for s in data["statuses"]}
        assert "PENDING" in codes
        assert "REFUNDED" in codes

    async def test_admin_list(self, client):
        await _create(client)
        resp = await client.get(
            "/api/order/admin/list", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["count"] == 1


# ============================================================
# 4. 支付: POST /api/order/{orderId}/pay
# ============================================================

class TestOrderPay:
    """支付订单(4): 成功/状态异常/订单不存在/未登录"""

    async def test_pay_success(self, client):
        order_id = await _create(client)
        resp = await client.post(
            f"/api/order/{order_id}/pay", json={}, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "PAID"
        assert data["consumedPoints"] == 536
        assert data["payment"]["method"] == "wechat"
        # 会员成长值 +536, 积分 100+536=636
        assert _mock_store["members"][1]["growth_value"] == 536
        assert _mock_store["members"][1]["points"] == 636

    async def test_pay_wrong_status(self, client):
        order_id = await _create(client)
        await _pay(client, order_id)  # 已 PAID
        resp = await client.post(
            f"/api/order/{order_id}/pay", json={}, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409

    async def test_pay_order_not_found(self, client):
        resp = await client.post(
            "/api/order/RT_NOPE/pay", json={}, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 404

    async def test_pay_no_auth(self, client):
        order_id = await _create(client)
        resp = await client.post(f"/api/order/{order_id}/pay", json={})
        assert resp.status_code == 401


# ============================================================
# 5. 取消: POST /api/order/{orderId}/cancel
# ============================================================

class TestOrderCancel:
    """取消订单(3): 成功/释放库存+退还积分/状态异常"""

    async def test_cancel_success(self, client):
        order_id = await _create(client)
        resp = await client.post(
            f"/api/order/{order_id}/cancel",
            json={"reason": "不想要了"}, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "CANCELLED"
        assert data["statusName"] == "已取消"

    async def test_cancel_releases_stock_and_refunds_points(self, client):
        # 用 100 积分下单 → 库存 -2, 积分 -100; 取消后双双恢复
        order_id = await _create(client, use_points=100)
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 498
        assert _mock_store["members"][1]["points"] == 0
        resp = await client.post(
            f"/api/order/{order_id}/cancel",
            json={"reason": "用户取消"}, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 200
        # 库存回补 498 → 500
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500
        # 积分退还 0 → 100
        assert _mock_store["members"][1]["points"] == 100

    async def test_cancel_wrong_status(self, client):
        order_id = await _create(client)
        await _pay(client, order_id)  # PAID, 不可取消
        resp = await client.post(
            f"/api/order/{order_id}/cancel",
            json={"reason": "x"}, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409


# ============================================================
# 6. 发货: POST /api/order/{orderId}/ship (admin)
# ============================================================

class TestOrderShip:
    """发货(3): 成功/权限校验/状态异常"""

    async def test_ship_success(self, client):
        order_id = await _create(client)
        await _pay(client, order_id)
        resp = await client.post(
            f"/api/order/{order_id}/ship",
            json={"carrier": "顺丰", "waybillNo": "SF0001"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "SHIPPED"
        assert data["logistics"]["carrier"] == "顺丰"
        assert data["logistics"]["waybillNo"] == "SF0001"

    async def test_ship_no_admin(self, client):
        order_id = await _create(client)
        await _pay(client, order_id)
        resp = await client.post(
            f"/api/order/{order_id}/ship",
            json={"carrier": "顺丰", "waybillNo": "SF0001"},
            headers=MEMBER_HEADERS,  # 非 admin
        )
        assert resp.status_code == 403

    async def test_ship_wrong_status(self, client):
        order_id = await _create(client)  # PENDING, 未支付
        resp = await client.post(
            f"/api/order/{order_id}/ship",
            json={"carrier": "顺丰", "waybillNo": "SF0001"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409


# ============================================================
# 7. 确认收货: POST /api/order/{orderId}/confirm
# ============================================================

class TestOrderConfirm:
    """确认收货(2): 成功/状态异常"""

    async def test_confirm_success(self, client):
        order_id = await _create(client)
        await _to_shipped(client, order_id)
        resp = await client.post(
            f"/api/order/{order_id}/confirm", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "RECEIVED"

    async def test_confirm_wrong_status(self, client):
        order_id = await _create(client)  # PENDING
        resp = await client.post(
            f"/api/order/{order_id}/confirm", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409


# ============================================================
# 8. 评价: POST /api/order/{orderId}/review
# ============================================================

class TestOrderReview:
    """评价(3): 成功/评分范围/状态异常"""

    async def test_review_success(self, client):
        order_id = await _create(client)
        await _to_received(client, order_id)
        resp = await client.post(
            f"/api/order/{order_id}/review",
            json={"rating": 5, "content": "好酒"},
            headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert data["rewardPoints"] == 100  # 5 星返 100
        # 积分 636(支付后) + 100(评价奖励) = 736
        assert _mock_store["members"][1]["points"] == 736

    async def test_review_invalid_rating(self, client):
        order_id = await _create(client)
        await _to_received(client, order_id)
        resp = await client.post(
            f"/api/order/{order_id}/review",
            json={"rating": 6, "content": "x"},
            headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409

    async def test_review_wrong_status(self, client):
        order_id = await _create(client)  # PENDING
        resp = await client.post(
            f"/api/order/{order_id}/review",
            json={"rating": 5, "content": "x"},
            headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409


# ============================================================
# 9. 申请退货: POST /api/order/{orderId}/return
# ============================================================

class TestOrderReturn:
    """申请退货(2): 成功/状态异常"""

    async def test_return_success(self, client):
        order_id = await _create(client)
        await _to_completed(client, order_id)
        resp = await client.post(
            f"/api/order/{order_id}/return",
            json={"reason": "包装破损"}, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "RETURNING"

    async def test_return_wrong_status(self, client):
        order_id = await _create(client)  # PENDING, 未完成
        resp = await client.post(
            f"/api/order/{order_id}/return",
            json={"reason": "x"}, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409


# ============================================================
# 10. 退款: POST /api/order/{orderId}/refund (admin)
# ============================================================

class TestOrderRefund:
    """退款(2): 成功(库存回滚+积分扣回)/状态异常"""

    async def test_refund_success(self, client):
        order_id = await _create(client)
        await _to_returning(client, order_id)
        # 退款前: 库存 498(下单扣 2), 积分 736(支付+536, 评价+100)
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 498
        assert _mock_store["members"][1]["points"] == 736
        resp = await client.post(
            f"/api/order/{order_id}/refund", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "REFUNDED"
        assert data["refundedAmount"] == 536.0
        # 库存回滚 498 → 500
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500
        # 积分扣回 consumedPoints=536: 736 - 536 = 200
        assert _mock_store["members"][1]["points"] == 200

    async def test_refund_wrong_status(self, client):
        order_id = await _create(client)  # PENDING, 非 RETURNING
        resp = await client.post(
            f"/api/order/{order_id}/refund", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409


# ============================================================
# 11. 超时自动处理 (admin)
# ============================================================

class TestOrderTimeout:
    """超时处理(3): 关闭/确认/完成"""

    async def test_timeout_close(self, client):
        order_id = await _create(client)  # PENDING
        resp = await client.post(
            f"/api/order/{order_id}/timeout/close", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "CLOSED"
        # 库存释放 498 → 500
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500

    async def test_timeout_confirm(self, client):
        order_id = await _create(client)
        await _to_shipped(client, order_id)  # SHIPPED
        resp = await client.post(
            f"/api/order/{order_id}/timeout/confirm", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "RECEIVED"

    async def test_timeout_complete(self, client):
        order_id = await _create(client)
        await _to_received(client, order_id)  # RECEIVED
        resp = await client.post(
            f"/api/order/{order_id}/timeout/complete", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert data["rewardPoints"] == 100  # 默认五星


# ============================================================
# 12. 删除: DELETE /api/order/{orderId}
# ============================================================

class TestOrderDelete:
    """删除订单(2): 终态成功/非终态失败"""

    async def test_delete_terminal_success(self, client):
        order_id = await _create(client)
        # 取消使其进入终态 CANCELLED
        await client.post(
            f"/api/order/{order_id}/cancel",
            json={"reason": "x"}, headers=MEMBER_HEADERS,
        )
        resp = await client.delete(
            f"/api/order/{order_id}", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        # 再查应 404
        resp2 = await client.get(
            f"/api/order/{order_id}", headers=MEMBER_HEADERS,
        )
        assert resp2.status_code == 404

    async def test_delete_non_terminal_fails(self, client):
        order_id = await _create(client)  # PENDING, 非终态
        resp = await client.delete(
            f"/api/order/{order_id}", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409


# ============================================================
# 13. 权限校验
# ============================================================

class TestOrderPermission:
    """权限守卫(3): 未登录 401/非 admin 发货 403/非 admin 退款 403"""

    async def test_create_no_auth_401(self, client):
        resp = await client.post(
            "/api/order/create",
            json={"items": ITEMS, "address": ADDRESS,
                  "usePoints": 0, "remark": ""},
        )
        assert resp.status_code == 401

    async def test_non_admin_ship_403(self, client):
        order_id = await _create(client)
        await _pay(client, order_id)
        resp = await client.post(
            f"/api/order/{order_id}/ship",
            json={"carrier": "顺丰", "waybillNo": "SF1"},
            headers=MEMBER_HEADERS,  # X-Member-Id 而非 X-Role: admin
        )
        assert resp.status_code == 403

    async def test_non_admin_refund_403(self, client):
        order_id = await _create(client)
        await _to_returning(client, order_id)
        resp = await client.post(
            f"/api/order/{order_id}/refund",
            headers=MEMBER_HEADERS,  # 非 admin
        )
        assert resp.status_code == 403


# ============================================================
# 14. 完整状态流转
# ============================================================

class TestOrderFullFlow:
    """完整流程(1): 创建→支付→发货→收货→评价→退货→退款"""

    async def test_full_flow(self, client):
        order_id = await _create(client)

        # 支付 PENDING → PAID
        r = await client.post(
            f"/api/order/{order_id}/pay", json={}, headers=MEMBER_HEADERS,
        )
        assert r.json()["status"] == "PAID"

        # 发货 PAID → SHIPPED
        r = await client.post(
            f"/api/order/{order_id}/ship",
            json={"carrier": "顺丰", "waybillNo": "SF1"},
            headers=ADMIN_HEADERS,
        )
        assert r.json()["status"] == "SHIPPED"

        # 确认收货 SHIPPED → RECEIVED
        r = await client.post(
            f"/api/order/{order_id}/confirm", headers=MEMBER_HEADERS,
        )
        assert r.json()["status"] == "RECEIVED"

        # 评价 RECEIVED → COMPLETED
        r = await client.post(
            f"/api/order/{order_id}/review",
            json={"rating": 5, "content": "好"}, headers=MEMBER_HEADERS,
        )
        assert r.json()["status"] == "COMPLETED"

        # 申请退货 COMPLETED → RETURNING
        r = await client.post(
            f"/api/order/{order_id}/return",
            json={"reason": "不想要了"}, headers=MEMBER_HEADERS,
        )
        assert r.json()["status"] == "RETURNING"

        # 退款 RETURNING → REFUNDED
        r = await client.post(
            f"/api/order/{order_id}/refund", headers=ADMIN_HEADERS,
        )
        assert r.json()["status"] == "REFUNDED"

        # 库存回滚 + 积分扣回
        assert _mock_store["inventory"]["ZX42-2026L07"]["stock"] == 500
        assert _mock_store["members"][1]["points"] == 200
