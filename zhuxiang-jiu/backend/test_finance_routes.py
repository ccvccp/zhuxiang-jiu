"""财务管理模块单元测试(22 端点)

测试框架: pytest + pytest-asyncio
HTTP 客户端: httpx.AsyncClient + ASGITransport(直连 ASGI app, 走完整 FastAPI 栈)
数据隔离: 每个测试前 reset_store() 重置内存 + 清空 asyncio 锁(跨事件循环安全)

覆盖维度:
  - 财务凭证(6 端点): list/detail/order-auto/refund-auto/audit/closing
  - 发票管理(4 端点): list/detail/issue/red
  - 税务管理(4 端点): list/detail/calc/declare
  - 资金对账(3 端点): list/daily/resolve
  - 付款管理(3 端点): list/apply/approve
  - 财务报表(2 端点): profit/management

成功路径 + 错误路径(401/403/404/409/400)

运行: pytest test_finance_routes.py -v
"""

import pytest
import httpx

from main import app
from repositories.store import _mock_store, reset_store
from core.locks import _async_locks


# ============================================================
# 常量与公共 Header
# ============================================================

ADMIN_HEADERS = {"X-Role": "admin"}
MEMBER_HEADERS = {"X-Member-Id": "1"}

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
# Fixture
# ============================================================

@pytest.fixture
async def client():
    """重置 store 与 asyncio 锁, 返回直连 ASGI 的异步客户端"""
    reset_store()
    _async_locks.clear()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ============================================================
# 辅助函数:创建并支付订单(凭证生成前置)
# ============================================================

async def _create_paid_order(client, member_id=1, items=None):
    """创建并支付订单, 返回 orderId"""
    body = {"items": items or ITEMS, "address": ADDRESS, "usePoints": 0, "remark": ""}
    resp = await client.post(
        "/api/order/create", json=body,
        headers={"X-Member-Id": str(member_id)},
    )
    assert resp.status_code == 200, resp.text
    order_id = resp.json()["orderId"]
    pay = await client.post(
        f"/api/order/{order_id}/pay", json={}, headers=MEMBER_HEADERS,
    )
    assert pay.status_code == 200, pay.text
    return order_id


async def _create_refunded_order(client):
    """创建并完成退款流程的订单, 返回 orderId"""
    order_id = await _create_paid_order(client)
    # ship → confirm → review → return → refund
    await client.post(
        f"/api/order/{order_id}/ship",
        json={"carrier": "顺丰", "waybillNo": "SF1"},
        headers=ADMIN_HEADERS,
    )
    await client.post(f"/api/order/{order_id}/confirm", headers=MEMBER_HEADERS)
    await client.post(
        f"/api/order/{order_id}/review",
        json={"rating": 5, "content": "好"}, headers=MEMBER_HEADERS,
    )
    await client.post(
        f"/api/order/{order_id}/return",
        json={"reason": "不想要了"}, headers=MEMBER_HEADERS,
    )
    refund = await client.post(
        f"/api/order/{order_id}/refund", headers=ADMIN_HEADERS,
    )
    assert refund.status_code == 200, refund.text
    return order_id


async def _generate_posted_voucher(client, order_id):
    """生成凭证并过账, 返回 voucherNo"""
    # 自动生成凭证
    resp = await client.post(
        f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    voucher_no = resp.json()["voucherNo"]
    # 审核 draft → audited
    r1 = await client.post(
        f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
    )
    assert r1.status_code == 200, r1.text
    # 过账 audited → posted
    r2 = await client.post(
        f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
    )
    assert r2.status_code == 200, r2.text
    return voucher_no


# ============================================================
# 1. 财务凭证 - 列表
# ============================================================

class TestVoucherList:
    """凭证列表(5): 成功/筛选/空/非admin/period筛选"""

    async def test_list_success(self, client):
        order_id = await _create_paid_order(client)
        await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["count"] == 1
        assert data["vouchers"][0]["source"] == "order"

    async def test_list_filter_by_status(self, client):
        order_id = await _create_paid_order(client)
        await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        # draft 凭证
        resp = await client.get(
            "/api/finance/voucher/list?status=draft", headers=ADMIN_HEADERS,
        )
        assert resp.json()["count"] == 1
        # posted 凭证(此时无)
        resp2 = await client.get(
            "/api/finance/voucher/list?status=posted", headers=ADMIN_HEADERS,
        )
        assert resp2.json()["count"] == 0

    async def test_list_empty(self, client):
        resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    async def test_list_no_admin(self, client):
        resp = await client.get("/api/finance/voucher/list", headers=MEMBER_HEADERS)
        assert resp.status_code == 403

    async def test_list_filter_by_source(self, client):
        order_id = await _create_paid_order(client)
        await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        resp = await client.get(
            "/api/finance/voucher/list?source=order", headers=ADMIN_HEADERS,
        )
        assert resp.json()["count"] == 1
        resp2 = await client.get(
            "/api/finance/voucher/list?source=refund", headers=ADMIN_HEADERS,
        )
        assert resp2.json()["count"] == 0


# ============================================================
# 2. 财务凭证 - 详情
# ============================================================

class TestVoucherDetail:
    """凭证详情(3): 成功(含分录)/不存在/非admin"""

    async def test_detail_success(self, client):
        order_id = await _create_paid_order(client)
        r = await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        voucher_no = r.json()["voucherNo"]
        resp = await client.get(
            f"/api/finance/voucher/{voucher_no}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        voucher = resp.json()["voucher"]
        assert voucher["voucherNo"] == voucher_no
        assert len(voucher["entries"]) == 3
        # 借贷平衡: 借=实付金额, 贷=不含税+税额
        debit = sum(e["amount"] for e in voucher["entries"] if e["direction"] == "debit")
        credit = sum(e["amount"] for e in voucher["entries"] if e["direction"] == "credit")
        assert abs(debit - credit) < 0.01

    async def test_detail_not_found(self, client):
        resp = await client.get(
            "/api/finance/voucher/FZ_NOPE", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404

    async def test_detail_no_admin(self, client):
        resp = await client.get(
            "/api/finance/voucher/FZ1", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403


# ============================================================
# 3. 财务凭证 - 自动生成(订单)
# ============================================================

class TestVoucherAutoFromOrder:
    """订单自动凭证(5): 成功/订单不存在/订单未支付/重复生成/非admin"""

    async def test_auto_success(self, client):
        order_id = await _create_paid_order(client)
        resp = await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        voucher = data["voucher"]
        # 实付 ¥536 → 不含税 ¥474.34, 增值税 ¥61.66
        assert voucher["amount"] == 536.0
        assert round(voucher["amountWithoutTax"], 2) == 474.34
        assert round(voucher["taxAmount"], 2) == 61.66
        assert voucher["status"] == "draft"
        assert voucher["source"] == "order"
        assert len(voucher["entries"]) == 3

    async def test_auto_order_not_found(self, client):
        resp = await client.post(
            "/api/finance/voucher/auto/order/RT_NOPE", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404

    async def test_auto_order_unpaid(self, client):
        # 仅创建未支付订单
        body = {"items": ITEMS, "address": ADDRESS, "usePoints": 0, "remark": ""}
        r = await client.post(
            "/api/order/create", json=body, headers=MEMBER_HEADERS,
        )
        order_id = r.json()["orderId"]
        resp = await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409

    async def test_auto_duplicate(self, client):
        order_id = await _create_paid_order(client)
        await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        resp = await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409
        assert "已生成" in resp.json()["error"]

    async def test_auto_no_admin(self, client):
        order_id = await _create_paid_order(client)
        resp = await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403


# ============================================================
# 4. 财务凭证 - 自动生成(退款红字)
# ============================================================

class TestVoucherAutoFromRefund:
    """退款红字凭证(4): 成功(借贷反转)/订单未退款/重复生成/订单不存在"""

    async def test_refund_auto_success(self, client):
        order_id = await _create_refunded_order(client)
        resp = await client.post(
            f"/api/finance/voucher/auto/refund/{order_id}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        voucher = data["voucher"]
        assert voucher["type"] == "refund"
        assert voucher.get("isRed") is True
        # 借贷反转: 借方=收入+税额, 贷方=银行存款
        debit = sum(e["amount"] for e in voucher["entries"] if e["direction"] == "debit")
        credit = sum(e["amount"] for e in voucher["entries"] if e["direction"] == "credit")
        assert abs(debit - credit) < 0.01
        # 银行存款在贷方
        bank_entry = [e for e in voucher["entries"] if e["subject"] == "银行存款"][0]
        assert bank_entry["direction"] == "credit"

    async def test_refund_auto_not_refunded(self, client):
        order_id = await _create_paid_order(client)  # 仅 PAID, 未退款
        resp = await client.post(
            f"/api/finance/voucher/auto/refund/{order_id}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409

    async def test_refund_auto_duplicate(self, client):
        order_id = await _create_refunded_order(client)
        await client.post(
            f"/api/finance/voucher/auto/refund/{order_id}", headers=ADMIN_HEADERS,
        )
        resp = await client.post(
            f"/api/finance/voucher/auto/refund/{order_id}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409

    async def test_refund_auto_order_not_found(self, client):
        resp = await client.post(
            "/api/finance/voucher/auto/refund/RT_NOPE", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404


# ============================================================
# 5. 财务凭证 - 审核
# ============================================================

class TestVoucherAudit:
    """凭证审核(5): 草稿→已审核/已审核→已过账/已过账不可审/不存在/非admin"""

    async def test_audit_draft_to_audited(self, client):
        order_id = await _create_paid_order(client)
        r = await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        voucher_no = r.json()["voucherNo"]
        resp = await client.post(
            f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "audited"
        assert data["statusName"] == "已审核"

    async def test_audit_audited_to_posted(self, client):
        order_id = await _create_paid_order(client)
        r = await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        voucher_no = r.json()["voucherNo"]
        await client.post(
            f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
        )
        resp = await client.post(
            f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "posted"

    async def test_audit_posted_conflict(self, client):
        order_id = await _create_paid_order(client)
        r = await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        voucher_no = r.json()["voucherNo"]
        # 审核两次 → posted
        await client.post(
            f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
        )
        await client.post(
            f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
        )
        # 第三次审核 → 409
        resp = await client.post(
            f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409

    async def test_audit_not_found(self, client):
        resp = await client.post(
            "/api/finance/voucher/audit/FZ_NOPE", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404

    async def test_audit_no_admin(self, client):
        resp = await client.post(
            "/api/finance/voucher/audit/FZ1", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403


# ============================================================
# 6. 财务凭证 - 月末结账
# ============================================================

class TestMonthEndClosing:
    """月末结账(4): 成功/period缺失/无凭证/非admin"""

    async def test_closing_success(self, client):
        order_id = await _create_paid_order(client)
        await _generate_posted_voucher(client, order_id)
        # 获取账期(从凭证查)
        r = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        period = r.json()["vouchers"][0]["period"]
        resp = await client.post(
            "/api/finance/voucher/closing",
            json={"period": period}, headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        closing = data["closing"]
        assert closing["voucherCount"] == 1
        assert closing["summary"]["netRevenue"] > 0
        assert closing["summary"]["incomeTax"] >= 0

    async def test_closing_no_period(self, client):
        resp = await client.post(
            "/api/finance/voucher/closing",
            json={}, headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 400

    async def test_closing_no_vouchers(self, client):
        resp = await client.post(
            "/api/finance/voucher/closing",
            json={"period": "202601"}, headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["closing"]["voucherCount"] == 0

    async def test_closing_no_admin(self, client):
        resp = await client.post(
            "/api/finance/voucher/closing",
            json={"period": "202601"}, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403


# ============================================================
# 7. 发票管理 - 列表
# ============================================================

class TestInvoiceList:
    """发票列表(3): 成功/筛选/非admin"""

    async def test_list_success(self, client):
        order_id = await _create_paid_order(client)
        await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "张三",
            }, headers=MEMBER_HEADERS,
        )
        resp = await client.get("/api/finance/invoice/list", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    async def test_list_filter_by_status(self, client):
        order_id = await _create_paid_order(client)
        await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "张三",
            }, headers=MEMBER_HEADERS,
        )
        resp = await client.get(
            "/api/finance/invoice/list?status=issued", headers=ADMIN_HEADERS,
        )
        assert resp.json()["count"] == 1
        resp2 = await client.get(
            "/api/finance/invoice/list?status=red", headers=ADMIN_HEADERS,
        )
        assert resp2.json()["count"] == 0

    async def test_list_no_admin(self, client):
        resp = await client.get("/api/finance/invoice/list", headers=MEMBER_HEADERS)
        assert resp.status_code == 403


# ============================================================
# 8. 发票管理 - 详情
# ============================================================

class TestInvoiceDetail:
    """发票详情(2): 成功/不存在"""

    async def test_detail_success(self, client):
        order_id = await _create_paid_order(client)
        r = await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "张三",
            }, headers=MEMBER_HEADERS,
        )
        invoice_no = r.json()["invoiceNo"]
        resp = await client.get(
            f"/api/finance/invoice/{invoice_no}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        invoice = resp.json()["invoice"]
        assert invoice["invoiceNo"] == invoice_no
        assert invoice["title"] == "张三"
        assert invoice["amount"] == 536.0

    async def test_detail_not_found(self, client):
        resp = await client.get(
            "/api/finance/invoice/FP_NOPE", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404


# ============================================================
# 9. 发票管理 - 开具
# ============================================================

class TestInvoiceIssue:
    """开具发票(6): 成功/订单不存在/抬头缺失/企业无税号/重复开票/未登录"""

    async def test_issue_success(self, client):
        order_id = await _create_paid_order(client)
        resp = await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "张三", "taxNo": "",
            }, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 200
        invoice = resp.json()["invoice"]
        assert invoice["amount"] == 536.0
        assert round(invoice["amountWithoutTax"], 2) == 474.34
        assert round(invoice["taxAmount"], 2) == 61.66

    async def test_issue_order_not_found(self, client):
        resp = await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": "RT_NOPE", "titleType": "personal",
                "title": "张三",
            }, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 404

    async def test_issue_no_title(self, client):
        order_id = await _create_paid_order(client)
        resp = await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "",
            }, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409

    async def test_issue_company_no_taxno(self, client):
        order_id = await _create_paid_order(client)
        resp = await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "company",
                "title": "竹香酒业有限公司", "taxNo": "",
            }, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409
        assert "税号" in resp.json()["error"]

    async def test_issue_duplicate(self, client):
        order_id = await _create_paid_order(client)
        await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "张三",
            }, headers=MEMBER_HEADERS,
        )
        resp = await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "张三",
            }, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 409

    async def test_issue_no_auth(self, client):
        order_id = await _create_paid_order(client)
        resp = await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "张三",
            },
        )
        assert resp.status_code == 401


# ============================================================
# 10. 发票管理 - 红字冲红
# ============================================================

class TestInvoiceRed:
    """红字冲红(4): 成功/不存在/已红冲不可再冲/原因校验"""

    async def test_red_success(self, client):
        order_id = await _create_paid_order(client)
        r = await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "张三",
            }, headers=MEMBER_HEADERS,
        )
        invoice_no = r.json()["invoiceNo"]
        resp = await client.post(
            f"/api/finance/invoice/red/{invoice_no}",
            json={"reason": "退货退款"}, headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["redInvoiceNo"] != invoice_no
        assert data["redInvoice"]["amount"] == -536.0
        assert data["redInvoice"]["redOriginalNo"] == invoice_no

    async def test_red_not_found(self, client):
        resp = await client.post(
            "/api/finance/invoice/red/FP_NOPE",
            json={"reason": "x"}, headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404

    async def test_red_already_red(self, client):
        order_id = await _create_paid_order(client)
        r = await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "张三",
            }, headers=MEMBER_HEADERS,
        )
        invoice_no = r.json()["invoiceNo"]
        await client.post(
            f"/api/finance/invoice/red/{invoice_no}",
            json={"reason": "退货"}, headers=ADMIN_HEADERS,
        )
        resp = await client.post(
            f"/api/finance/invoice/red/{invoice_no}",
            json={"reason": "x"}, headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409

    async def test_red_no_admin(self, client):
        order_id = await _create_paid_order(client)
        r = await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "张三",
            }, headers=MEMBER_HEADERS,
        )
        invoice_no = r.json()["invoiceNo"]
        resp = await client.post(
            f"/api/finance/invoice/red/{invoice_no}",
            json={"reason": "x"}, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403


# ============================================================
# 11. 税务管理 - 列表
# ============================================================

class TestTaxList:
    """税务申报列表(3): 成功/筛选/空"""

    async def test_list_success(self, client):
        order_id = await _create_paid_order(client)
        await _generate_posted_voucher(client, order_id)
        # 计算税额(会创建 4 条申报记录)
        period_resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        period = period_resp.json()["vouchers"][0]["period"]
        await client.post(
            f"/api/finance/tax/calc/{period}", headers=ADMIN_HEADERS,
        )
        resp = await client.get("/api/finance/tax/list", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["count"] >= 4

    async def test_list_filter_by_tax_type(self, client):
        order_id = await _create_paid_order(client)
        await _generate_posted_voucher(client, order_id)
        period_resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        period = period_resp.json()["vouchers"][0]["period"]
        await client.post(
            f"/api/finance/tax/calc/{period}", headers=ADMIN_HEADERS,
        )
        resp = await client.get(
            "/api/finance/tax/list?taxType=vat", headers=ADMIN_HEADERS,
        )
        assert resp.json()["count"] == 1
        assert resp.json()["declarations"][0]["taxType"] == "vat"

    async def test_list_empty(self, client):
        resp = await client.get("/api/finance/tax/list", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ============================================================
# 12. 税务管理 - 详情
# ============================================================

class TestTaxDetail:
    """税务申报详情(2): 成功/不存在"""

    async def test_detail_success(self, client):
        order_id = await _create_paid_order(client)
        await _generate_posted_voucher(client, order_id)
        period_resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        period = period_resp.json()["vouchers"][0]["period"]
        calc = await client.post(
            f"/api/finance/tax/calc/{period}", headers=ADMIN_HEADERS,
        )
        decl_no = calc.json()["declarationNos"][0]
        resp = await client.get(
            f"/api/finance/tax/{decl_no}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["declaration"]["declarationNo"] == decl_no

    async def test_detail_not_found(self, client):
        resp = await client.get(
            "/api/finance/tax/SB_NOPE", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404


# ============================================================
# 13. 税务管理 - 税额计算
# ============================================================

class TestTaxCalc:
    """税额计算(4): 成功/无凭证/非admin/计算口径正确"""

    async def test_calc_success(self, client):
        order_id = await _create_paid_order(client)
        await _generate_posted_voucher(client, order_id)
        period_resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        period = period_resp.json()["vouchers"][0]["period"]
        resp = await client.post(
            f"/api/finance/tax/calc/{period}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        detail = data["detail"]
        # 应纳增值税 = 销项税额 61.66
        assert round(detail["vat"]["payable"], 2) == 61.66
        # 消费税从价 = 不含税 474.34 × 20% = 94.87
        assert round(detail["consumptionTax"]["adValorem"], 2) == 94.87
        # 消费税从量 = 2件 × 0.5 = 1
        assert detail["consumptionTax"]["perUnit"] == 1.0
        # 附加税基数 = 增值税 61.66 + 消费税 95.87 = 157.53
        assert round(detail["surtax"]["base"], 2) == 157.53
        # 城建税 = 157.53 × 7% = 11.03
        assert round(detail["surtax"]["city"], 2) == 11.03
        # 应创建 4 个申报记录
        assert len(data["declarationNos"]) == 4

    async def test_calc_no_vouchers(self, client):
        resp = await client.post(
            "/api/finance/tax/calc/202601", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["detail"]["voucherCount"] == 0

    async def test_calc_no_admin(self, client):
        resp = await client.post(
            "/api/finance/tax/calc/202601", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403

    async def test_calc_consumption_tax_correct(self, client):
        """消费税: 从价20% + 从量0.5元/斤"""
        order_id = await _create_paid_order(client)
        await _generate_posted_voucher(client, order_id)
        period_resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        period = period_resp.json()["vouchers"][0]["period"]
        resp = await client.post(
            f"/api/finance/tax/calc/{period}", headers=ADMIN_HEADERS,
        )
        detail = resp.json()["detail"]
        # 从价 = 不含税 474.34 × 20% = 94.87
        assert round(detail["consumptionTax"]["adValorem"], 2) == 94.87
        # 从量 = 2件 × 0.5 = 1.0
        assert detail["consumptionTax"]["perUnit"] == 1.0
        # 合计
        assert round(detail["consumptionTax"]["total"], 2) == 95.87


# ============================================================
# 14. 税务管理 - 申报
# ============================================================

class TestTaxDeclare:
    """税务申报(5): 待申报→已申报/已申报→已缴款/已缴款不可再申报/不存在/非admin"""

    async def _setup_decl(self, client):
        order_id = await _create_paid_order(client)
        await _generate_posted_voucher(client, order_id)
        period_resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        period = period_resp.json()["vouchers"][0]["period"]
        calc = await client.post(
            f"/api/finance/tax/calc/{period}", headers=ADMIN_HEADERS,
        )
        return calc.json()["declarationNos"][0]

    async def test_declare_pending_to_declared(self, client):
        decl_no = await self._setup_decl(client)
        resp = await client.post(
            f"/api/finance/tax/declare/{decl_no}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "declared"

    async def test_declare_declared_to_paid(self, client):
        decl_no = await self._setup_decl(client)
        await client.post(
            f"/api/finance/tax/declare/{decl_no}", headers=ADMIN_HEADERS,
        )
        resp = await client.post(
            f"/api/finance/tax/declare/{decl_no}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "paid"

    async def test_declare_paid_conflict(self, client):
        decl_no = await self._setup_decl(client)
        await client.post(
            f"/api/finance/tax/declare/{decl_no}", headers=ADMIN_HEADERS,
        )
        await client.post(
            f"/api/finance/tax/declare/{decl_no}", headers=ADMIN_HEADERS,
        )
        resp = await client.post(
            f"/api/finance/tax/declare/{decl_no}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409

    async def test_declare_not_found(self, client):
        resp = await client.post(
            "/api/finance/tax/declare/SB_NOPE", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404

    async def test_declare_no_admin(self, client):
        resp = await client.post(
            "/api/finance/tax/declare/SB1", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403


# ============================================================
# 15. 资金对账 - 列表
# ============================================================

class TestReconList:
    """对账列表(3): 成功/筛选/空"""

    async def test_list_success(self, client):
        order_id = await _create_paid_order(client)
        await client.post(
            "/api/finance/recon/daily/2026-08-21", headers=ADMIN_HEADERS,
        )
        resp = await client.get("/api/finance/recon/list", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    async def test_list_filter_by_date(self, client):
        await client.post(
            "/api/finance/recon/daily/2026-08-21", headers=ADMIN_HEADERS,
        )
        resp = await client.get(
            "/api/finance/recon/list?date=2026-08-21", headers=ADMIN_HEADERS,
        )
        assert resp.json()["count"] == 1
        resp2 = await client.get(
            "/api/finance/recon/list?date=2026-01-01", headers=ADMIN_HEADERS,
        )
        assert resp2.json()["count"] == 0

    async def test_list_empty(self, client):
        resp = await client.get("/api/finance/recon/list", headers=ADMIN_HEADERS)
        assert resp.json()["count"] == 0


# ============================================================
# 16. 资金对账 - 日终对账
# ============================================================

class TestReconDaily:
    """日终对账(4): 成功(三方一致)/无订单/非admin/重复对账"""

    async def test_daily_success(self, client):
        order_id = await _create_paid_order(client)
        resp = await client.post(
            "/api/finance/recon/daily/2026-08-21", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        recon = data["reconciliation"]
        assert recon["status"] == "matched"
        # 订单侧应有 1 笔, ¥536
        assert recon["orderSide"]["count"] >= 1

    async def test_daily_no_orders(self, client):
        resp = await client.post(
            "/api/finance/recon/daily/2026-01-01", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["reconciliation"]["orderSide"]["count"] == 0

    async def test_daily_no_admin(self, client):
        resp = await client.post(
            "/api/finance/recon/daily/2026-08-21", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403

    async def test_daily_idempotent(self, client):
        """重复对账应覆盖原记录"""
        await client.post(
            "/api/finance/recon/daily/2026-08-21", headers=ADMIN_HEADERS,
        )
        resp = await client.post(
            "/api/finance/recon/daily/2026-08-21", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        # 列表只有一条
        list_resp = await client.get(
            "/api/finance/recon/list?date=2026-08-21", headers=ADMIN_HEADERS,
        )
        assert list_resp.json()["count"] == 1


# ============================================================
# 17. 资金对账 - 差异处理
# ============================================================

class TestReconResolve:
    """差异处理(4): 成功/无差异不可处理/已处理/不存在"""

    async def _make_diff_recon(self, client):
        """构造有差异的对账记录(直接写 _mock_store)"""
        from datetime import datetime, timezone, timedelta
        _tz = timezone(timedelta(hours=8))
        now = datetime.now(_tz).isoformat()
        recon = {
            "reconId": "2026-08-21:daily",
            "date": "2026-08-21",
            "type": "daily",
            "status": "diff",
            "orderSide": {"count": 5, "amount": 1000.00},
            "paySide": {"count": 5, "amount": 1000.00},
            "bankSide": {"count": 4, "amount": 800.00},
            "diffAmount": 200.00,
            "differences": [
                {"side": "pay-vs-bank", "amount": 200.00, "desc": "支付渠道与银行不一致"},
            ],
            "resolvedBy": "",
            "resolvedAt": "",
            "resolveNote": "",
            "createdAt": now,
            "updatedAt": now,
        }
        _mock_store.setdefault("finance_reconciliations", {})
        _mock_store["finance_reconciliations"]["2026-08-21:daily"] = recon

    async def test_resolve_success(self, client):
        await self._make_diff_recon(client)
        resp = await client.post(
            "/api/finance/recon/2026-08-21:daily/resolve",
            json={"reason": "银行T+1到账延迟", "handler": "财务-李四"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "resolved"
        assert data["resolvedBy"] == "财务-李四"

    async def test_resolve_no_diff(self, client):
        # 一致的对账记录不可处理
        await client.post(
            "/api/finance/recon/daily/2026-08-21", headers=ADMIN_HEADERS,
        )
        resp = await client.post(
            "/api/finance/recon/2026-08-21:daily/resolve",
            json={"reason": "x", "handler": "x"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409

    async def test_resolve_already_resolved(self, client):
        await self._make_diff_recon(client)
        await client.post(
            "/api/finance/recon/2026-08-21:daily/resolve",
            json={"reason": "x", "handler": "x"},
            headers=ADMIN_HEADERS,
        )
        resp = await client.post(
            "/api/finance/recon/2026-08-21:daily/resolve",
            json={"reason": "y", "handler": "y"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409

    async def test_resolve_not_found(self, client):
        resp = await client.post(
            "/api/finance/recon/2099-01-01:daily/resolve",
            json={"reason": "x", "handler": "x"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404


# ============================================================
# 18. 付款管理 - 列表
# ============================================================

class TestPaymentList:
    """付款列表(3): 成功/筛选/空"""

    async def test_list_success(self, client):
        await client.post(
            "/api/finance/payment/apply",
            json={
                "type": "supplier", "payee": "供应商A",
                "amount": 5000, "description": "采购货款",
            }, headers=ADMIN_HEADERS,
        )
        resp = await client.get("/api/finance/payment/list", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    async def test_list_filter_by_type(self, client):
        await client.post(
            "/api/finance/payment/apply",
            json={
                "type": "supplier", "payee": "A", "amount": 1000,
                "description": "x",
            }, headers=ADMIN_HEADERS,
        )
        await client.post(
            "/api/finance/payment/apply",
            json={
                "type": "logistics", "payee": "B", "amount": 500,
                "description": "y",
            }, headers=ADMIN_HEADERS,
        )
        resp = await client.get(
            "/api/finance/payment/list?type=supplier", headers=ADMIN_HEADERS,
        )
        assert resp.json()["count"] == 1

    async def test_list_empty(self, client):
        resp = await client.get("/api/finance/payment/list", headers=ADMIN_HEADERS)
        assert resp.json()["count"] == 0


# ============================================================
# 19. 付款管理 - 申请
# ============================================================

class TestPaymentApply:
    """付款申请(5): 成功/缺类型/缺收款方/金额非法/非admin"""

    async def test_apply_success(self, client):
        resp = await client.post(
            "/api/finance/payment/apply",
            json={
                "type": "supplier", "payee": "竹香原料供应商",
                "amount": 8000, "description": "8月原料采购",
            }, headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        payment = data["payment"]
        assert payment["amount"] == 8000
        assert payment["status"] == "pending"
        # 8000 < 1万 → 一级审批
        assert payment["requiredLevel"] == 1

    async def test_apply_tier2(self, client):
        """1万-10万需二级审批"""
        resp = await client.post(
            "/api/finance/payment/apply",
            json={
                "type": "supplier", "payee": "X",
                "amount": 50000, "description": "x",
            }, headers=ADMIN_HEADERS,
        )
        assert resp.json()["payment"]["requiredLevel"] == 2

    async def test_apply_tier3(self, client):
        """>10万需三级审批"""
        resp = await client.post(
            "/api/finance/payment/apply",
            json={
                "type": "supplier", "payee": "X",
                "amount": 150000, "description": "x",
            }, headers=ADMIN_HEADERS,
        )
        assert resp.json()["payment"]["requiredLevel"] == 3

    async def test_apply_invalid_amount(self, client):
        resp = await client.post(
            "/api/finance/payment/apply",
            json={
                "type": "supplier", "payee": "X",
                "amount": 0, "description": "x",
            }, headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409

    async def test_apply_no_admin(self, client):
        resp = await client.post(
            "/api/finance/payment/apply",
            json={
                "type": "supplier", "payee": "X",
                "amount": 1000, "description": "x",
            }, headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403


# ============================================================
# 20. 付款管理 - 审批
# ============================================================

class TestPaymentApprove:
    """付款审批(8): 一级通过/二级审批流/三级审批流/拒绝/状态跳跃/不存在/已批准不可再审/非admin"""

    async def _apply(self, client, amount):
        r = await client.post(
            "/api/finance/payment/apply",
            json={
                "type": "supplier", "payee": "X",
                "amount": amount, "description": "x",
            }, headers=ADMIN_HEADERS,
        )
        return r.json()["paymentNo"]

    async def test_approve_tier1_success(self, client):
        """一级审批直接通过(<1万)"""
        payment_no = await self._apply(client, 5000)
        resp = await client.post(
            f"/api/finance/payment/{payment_no}/approve",
            json={"level": 1, "approver": "主管", "decision": "approve"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    async def test_approve_tier2_two_levels(self, client):
        """二级审批需两次通过(1万-10万)"""
        payment_no = await self._apply(client, 50000)
        # 一级通过 → approving
        r1 = await client.post(
            f"/api/finance/payment/{payment_no}/approve",
            json={"level": 1, "approver": "主管", "decision": "approve"},
            headers=ADMIN_HEADERS,
        )
        assert r1.json()["status"] == "approving"
        # 二级通过 → approved
        r2 = await client.post(
            f"/api/finance/payment/{payment_no}/approve",
            json={"level": 2, "approver": "经理", "decision": "approve"},
            headers=ADMIN_HEADERS,
        )
        assert r2.json()["status"] == "approved"

    async def test_approve_tier3_three_levels(self, client):
        """三级审批需三次通过(>10万)"""
        payment_no = await self._apply(client, 150000)
        await client.post(
            f"/api/finance/payment/{payment_no}/approve",
            json={"level": 1, "approver": "主管", "decision": "approve"},
            headers=ADMIN_HEADERS,
        )
        await client.post(
            f"/api/finance/payment/{payment_no}/approve",
            json={"level": 2, "approver": "经理", "decision": "approve"},
            headers=ADMIN_HEADERS,
        )
        r3 = await client.post(
            f"/api/finance/payment/{payment_no}/approve",
            json={"level": 3, "approver": "总监", "decision": "approve"},
            headers=ADMIN_HEADERS,
        )
        assert r3.status_code == 200
        assert r3.json()["status"] == "approved"

    async def test_approve_reject(self, client):
        """任一级可拒绝"""
        payment_no = await self._apply(client, 50000)
        resp = await client.post(
            f"/api/finance/payment/{payment_no}/approve",
            json={"level": 1, "approver": "主管",
                  "decision": "reject", "reason": "单据不全"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    async def test_approve_level_skip(self, client):
        """级别跳跃: 一级未审批直接二级 → 409"""
        payment_no = await self._apply(client, 50000)
        resp = await client.post(
            f"/api/finance/payment/{payment_no}/approve",
            json={"level": 2, "approver": "经理", "decision": "approve"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409
        assert "级别跳跃" in resp.json()["error"]

    async def test_approve_not_found(self, client):
        resp = await client.post(
            "/api/finance/payment/FK_NOPE/approve",
            json={"level": 1, "approver": "x", "decision": "approve"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404

    async def test_approve_already_approved(self, client):
        """已批准的付款不可再审批"""
        payment_no = await self._apply(client, 5000)
        await client.post(
            f"/api/finance/payment/{payment_no}/approve",
            json={"level": 1, "approver": "x", "decision": "approve"},
            headers=ADMIN_HEADERS,
        )
        resp = await client.post(
            f"/api/finance/payment/{payment_no}/approve",
            json={"level": 1, "approver": "y", "decision": "approve"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409

    async def test_approve_no_admin(self, client):
        resp = await client.post(
            "/api/finance/payment/FK1/approve",
            json={"level": 1, "approver": "x", "decision": "approve"},
            headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403


# ============================================================
# 21. 财务报表 - 利润表
# ============================================================

class TestProfitStatement:
    """利润表(4): 成功/无凭证/非admin/结构完整"""

    async def test_profit_success(self, client):
        order_id = await _create_paid_order(client)
        await _generate_posted_voucher(client, order_id)
        period_resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        period = period_resp.json()["vouchers"][0]["period"]
        resp = await client.get(
            f"/api/finance/report/profit/{period}", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        stmt = resp.json()["statement"]
        # 营业收入 = 不含税 474.34
        assert round(stmt["revenue"]["mainRevenue"], 2) == 474.34
        # 利润总额 = 收入 - 成本 - 税金 - 费用
        expected_profit = (
            stmt["revenue"]["mainRevenue"]
            - stmt["cost"]["total"]
            - stmt["taxAndSurcharge"]["total"]
            - stmt["expenses"]["total"]
        )
        assert round(stmt["profitBeforeTax"], 2) == round(expected_profit, 2)
        # 净利润 = 利润总额 - 所得税
        assert round(stmt["netProfit"], 2) == round(
            stmt["profitBeforeTax"] - stmt["incomeTax"]["amount"], 2
        )

    async def test_profit_no_vouchers(self, client):
        resp = await client.get(
            "/api/finance/report/profit/202601", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["statement"]["revenue"]["mainRevenue"] == 0

    async def test_profit_no_admin(self, client):
        resp = await client.get(
            "/api/finance/report/profit/202601", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403

    async def test_profit_structure_complete(self, client):
        """利润表结构完整: 收入/成本/税金/费用/利润总额/所得税/净利润"""
        order_id = await _create_paid_order(client)
        await _generate_posted_voucher(client, order_id)
        period_resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        period = period_resp.json()["vouchers"][0]["period"]
        resp = await client.get(
            f"/api/finance/report/profit/{period}", headers=ADMIN_HEADERS,
        )
        stmt = resp.json()["statement"]
        # 必备字段
        for key in ("revenue", "cost", "taxAndSurcharge", "expenses",
                    "profitBeforeTax", "incomeTax", "netProfit"):
            assert key in stmt
        # 成本细分
        for key in ("production", "purchase", "logistics", "packaging", "total"):
            assert key in stmt["cost"]
        # 费用细分
        for key in ("sales", "admin", "finance", "total"):
            assert key in stmt["expenses"]


# ============================================================
# 22. 财务报表 - 管理报表
# ============================================================

class TestManagementReport:
    """管理报表(3): 成功/非admin/结构完整"""

    async def test_management_success(self, client):
        order_id = await _create_paid_order(client)
        resp = await client.get(
            "/api/finance/report/management/2026-08-21", headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        report = resp.json()["report"]
        assert report["sales"]["orderCount"] >= 1
        assert report["sales"]["orderAmount"] >= 536.0

    async def test_management_no_admin(self, client):
        resp = await client.get(
            "/api/finance/report/management/2026-08-21", headers=MEMBER_HEADERS,
        )
        assert resp.status_code == 403

    async def test_management_structure_complete(self, client):
        """管理报表结构: sales/finance/balance"""
        await _create_paid_order(client)
        resp = await client.get(
            "/api/finance/report/management/2026-08-21", headers=ADMIN_HEADERS,
        )
        report = resp.json()["report"]
        for key in ("sales", "finance", "balance"):
            assert key in report
        for key in ("orderCount", "orderAmount", "refundCount",
                    "refundAmount", "netAmount"):
            assert key in report["sales"]
        for key in ("receivable", "payable", "netCash"):
            assert key in report["balance"]


# ============================================================
# 23. 端到端集成
# ============================================================

class TestFinanceEndToEnd:
    """端到端集成(2): 完整流程/月度报表"""

    async def test_full_flow(self, client):
        """订单→支付→凭证→过账→开票→税额计算→申报→利润表"""
        order_id = await _create_paid_order(client)

        # 1. 自动凭证
        r = await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        voucher_no = r.json()["voucherNo"]

        # 2. 审核过账
        await client.post(
            f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
        )
        await client.post(
            f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
        )

        # 3. 开具发票
        inv = await client.post(
            "/api/finance/invoice/issue",
            json={
                "orderId": order_id, "titleType": "personal",
                "title": "张三",
            }, headers=MEMBER_HEADERS,
        )
        assert inv.status_code == 200

        # 4. 税额计算
        period_resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        period = period_resp.json()["vouchers"][0]["period"]
        calc = await client.post(
            f"/api/finance/tax/calc/{period}", headers=ADMIN_HEADERS,
        )
        assert calc.status_code == 200
        assert len(calc.json()["declarationNos"]) == 4

        # 5. 申报增值税
        vat_no = calc.json()["declarationNos"][0]
        dec = await client.post(
            f"/api/finance/tax/declare/{vat_no}", headers=ADMIN_HEADERS,
        )
        assert dec.json()["status"] == "declared"
        paid = await client.post(
            f"/api/finance/tax/declare/{vat_no}", headers=ADMIN_HEADERS,
        )
        assert paid.json()["status"] == "paid"

        # 6. 利润表
        stmt = await client.get(
            f"/api/finance/report/profit/{period}", headers=ADMIN_HEADERS,
        )
        assert stmt.status_code == 200
        assert stmt.json()["statement"]["netProfit"] > 0

    async def test_refund_full_flow(self, client):
        """退款→红字凭证→利润表(退款冲减收入)"""
        order_id = await _create_paid_order(client)
        # 收入凭证
        r = await client.post(
            f"/api/finance/voucher/auto/order/{order_id}", headers=ADMIN_HEADERS,
        )
        voucher_no = r.json()["voucherNo"]
        await client.post(
            f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
        )
        await client.post(
            f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN_HEADERS,
        )

        # 触发退款流程
        await client.post(
            f"/api/order/{order_id}/ship",
            json={"carrier": "顺丰", "waybillNo": "SF1"},
            headers=ADMIN_HEADERS,
        )
        await client.post(f"/api/order/{order_id}/confirm", headers=MEMBER_HEADERS)
        await client.post(
            f"/api/order/{order_id}/review",
            json={"rating": 5, "content": "好"}, headers=MEMBER_HEADERS,
        )
        await client.post(
            f"/api/order/{order_id}/return",
            json={"reason": "不想要了"}, headers=MEMBER_HEADERS,
        )
        await client.post(
            f"/api/order/{order_id}/refund", headers=ADMIN_HEADERS,
        )

        # 生成红字凭证
        red = await client.post(
            f"/api/finance/voucher/auto/refund/{order_id}", headers=ADMIN_HEADERS,
        )
        assert red.status_code == 200
        red_no = red.json()["voucherNo"]
        # 过账
        await client.post(
            f"/api/finance/voucher/audit/{red_no}", headers=ADMIN_HEADERS,
        )
        await client.post(
            f"/api/finance/voucher/audit/{red_no}", headers=ADMIN_HEADERS,
        )

        # 查询利润表 - 收入应为 0(收入 - 退款)
        period_resp = await client.get("/api/finance/voucher/list", headers=ADMIN_HEADERS)
        period = period_resp.json()["vouchers"][0]["period"]
        stmt = await client.get(
            f"/api/finance/report/profit/{period}", headers=ADMIN_HEADERS,
        )
        assert stmt.status_code == 200
        assert stmt.json()["statement"]["revenue"]["mainRevenue"] == 0


# ============================================================
# 24. 鉴权与异常 - 综合
# ============================================================

class TestFinancePermission:
    """权限守卫(3): 全部接口需 admin/发票需 member"""

    async def test_all_admin_endpoints_require_admin(self, client):
        """22 个接口中除发票外, 其余均需 admin"""
        # 凭证 list
        r = await client.get("/api/finance/voucher/list", headers=MEMBER_HEADERS)
        assert r.status_code == 403
        # 税额计算
        r = await client.post(
            "/api/finance/tax/calc/202601", headers=MEMBER_HEADERS,
        )
        assert r.status_code == 403
        # 付款申请
        r = await client.post(
            "/api/finance/payment/apply",
            json={"type": "x", "payee": "x", "amount": 100},
            headers=MEMBER_HEADERS,
        )
        assert r.status_code == 403
        # 利润表
        r = await client.get(
            "/api/finance/report/profit/202601", headers=MEMBER_HEADERS,
        )
        assert r.status_code == 403

    async def test_invoice_issue_requires_member(self, client):
        """发票开具需 X-Member-Id"""
        r = await client.post(
            "/api/finance/invoice/issue",
            json={"orderId": "x", "title": "x"},
        )
        assert r.status_code == 401

    async def test_invoice_issue_member_invalid(self, client):
        """X-Member-Id 非数字 → 401"""
        r = await client.post(
            "/api/finance/invoice/issue",
            json={"orderId": "x", "title": "x"},
            headers={"X-Member-Id": "abc"},
        )
        assert r.status_code == 401
