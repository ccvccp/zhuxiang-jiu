"""收款管理模块 HTTP 层鉴权测试(v2 安全加固: 9 端点补鉴权头校验)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_payment_auth.py

覆盖(TD-4 遗留项验收):
    1. 支付单归属鉴权: 详情/发起/关闭/退款/退款列表 5 端点
       - 无头 401 / 他人 403 / 归属 200 / admin 200
    2. 游客单凭单号访问(单号即凭证); 游客单退款仅 admin(资金敏感)
    3. 对账批次列表/详情: admin 403/200(财务敏感)
    4. 渠道详情: admin 403/200(含商户号/费率)
    5. channels/active: 保持公开但字段白名单脱敏(无 merchantId/feeRate)
    6. 不存在的支付单 → 404(鉴权前先查存在性, 不泄露单号存在性歧义)
"""
import asyncio
import base64
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from fastapi.testclient import TestClient

from main import app
from repositories.store import _mock_store

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


M = {"X-Member-Id": "1001"}          # 归属会员
OTHER = {"X-Member-Id": "1002"}       # 非归属会员
ADMIN = {"X-Role": "admin"}


def run(client):
    global PASS, FAIL
    for k in list(_mock_store.keys()):
        if "payment" in k or "recon" in k or "channel" in k:
            del _mock_store[k]

    # ---- 造数据: 会员单 + 游客单 + 渠道 ----
    r = client.post("/api/payment/pay", headers=M, json={
        "orderId": "AUTH-ORD-1", "orderType": "retail",
        "totalAmount": 300.0, "payChannel": "alipay", "payMethod": "jsapi",
        "sceneType": "order_pay"})
    check("造数: 会员支付单创建", r.status_code == 200 and r.json().get("success") is True,
          f"{r.status_code} {r.text[:200]}")
    pay_no = r.json()["payNo"]

    r = client.post("/api/payment/pay", json={
        "orderId": "AUTH-ORD-G", "orderType": "retail",
        "totalAmount": 199.0, "payChannel": "wechat", "payMethod": "native",
        "sceneType": "guest_order_pay",
        "guestPhone": "13800009999", "ageConfirmed": True})
    check("造数: 游客支付单创建", r.status_code == 200 and r.json().get("success") is True)
    guest_no = r.json()["payNo"]

    r = client.post("/api/payment/channel", headers=ADMIN, json={
        "channelCode": "alipay", "channelName": "支付宝",
        "channelType": "third_party", "merchantId": "2088000001"})
    check("造数: 渠道创建", r.status_code == 200)

    # ============================================================
    # 1. GET /api/payment/{pay_no} 详情归属鉴权
    # ============================================================
    r = client.get(f"/api/payment/{pay_no}")
    check("详情: 无头 401", r.status_code == 401, f"got {r.status_code}")
    r = client.get(f"/api/payment/{pay_no}", headers=OTHER)
    check("详情: 他人 403", r.status_code == 403, f"got {r.status_code}")
    r = client.get(f"/api/payment/{pay_no}", headers=M)
    check("详情: 归属 200", r.status_code == 200 and r.json()["payNo"] == pay_no)
    r = client.get(f"/api/payment/{pay_no}", headers=ADMIN)
    check("详情: admin 200", r.status_code == 200)
    r = client.get("/api/payment/PAYNOTEXIST404")
    check("详情: 不存在 404", r.status_code == 404, f"got {r.status_code}")
    r = client.get(f"/api/payment/{guest_no}")
    check("详情: 游客单无头 200(单号即凭证)", r.status_code == 200,
          f"got {r.status_code}")

    # ============================================================
    # 2. POST /{pay_no}/start 发起支付归属鉴权
    # ============================================================
    r = client.post(f"/api/payment/{pay_no}/start")
    check("发起: 无头 401", r.status_code == 401, f"got {r.status_code}")
    r = client.post(f"/api/payment/{pay_no}/start", headers=OTHER)
    check("发起: 他人 403", r.status_code == 403, f"got {r.status_code}")
    r = client.post(f"/api/payment/{pay_no}/start", headers=M)
    check("发起: 归属 200", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    r = client.post(f"/api/payment/{guest_no}/start")
    check("发起: 游客单无头 200", r.status_code == 200,
          f"got {r.status_code} {r.text[:150]}")

    # ============================================================
    # 3. POST /{pay_no}/close 关闭归属鉴权(新建一张会员单)
    # ============================================================
    r = client.post("/api/payment/pay", headers=M, json={
        "orderId": "AUTH-ORD-2", "orderType": "retail",
        "totalAmount": 88.0, "sceneType": "order_pay"})
    close_no = r.json()["payNo"]
    r = client.post(f"/api/payment/{close_no}/close", headers=OTHER,
                    json={"reason": "USER_CANCEL"})
    check("关闭: 他人 403", r.status_code == 403, f"got {r.status_code}")
    r = client.post(f"/api/payment/{close_no}/close", headers=M,
                    json={"reason": "USER_CANCEL"})
    check("关闭: 归属 200", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    r = client.post(f"/api/payment/{close_no}/close", headers=M,
                    json={"reason": "USER_CANCEL"})
    check("关闭: 已关闭再关幂等 200", r.status_code == 200
          and r.json().get("idempotent") is True)

    # ============================================================
    # 4. POST /{pay_no}/refund 退款归属鉴权(资金敏感)
    # ============================================================
    # 会员单先支付成功(start 已发, 走回调置 paid)
    r = client.post("/api/payment/callback/pay", json={
        "channelTradeNo": "AUTH-TRADE-1", "payNo": pay_no})
    check("造数: 会员单回调置 paid", r.status_code == 200
          and r.json().get("success") is True, f"{r.text[:150]}")
    r = client.post(f"/api/payment/{pay_no}/refund",
                    json={"refundAmount": 10.0, "refundReason": "测试"})
    check("退款: 无头 401", r.status_code == 401, f"got {r.status_code}")
    r = client.post(f"/api/payment/{pay_no}/refund", headers=OTHER,
                    json={"refundAmount": 10.0, "refundReason": "测试"})
    check("退款: 他人 403", r.status_code == 403, f"got {r.status_code}")
    r = client.post(f"/api/payment/{pay_no}/refund", headers=M,
                    json={"refundAmount": 10.0, "refundReason": "测试"})
    check("退款: 归属 200", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    # 游客单: 支付成功后退款仅 admin
    client.post(f"/api/payment/{guest_no}/start")
    r = client.post("/api/payment/callback/pay", json={
        "channelTradeNo": "AUTH-TRADE-G", "payNo": guest_no})
    check("造数: 游客单回调置 paid", r.status_code == 200
          and r.json().get("success") is True, f"{r.text[:150]}")
    r = client.post(f"/api/payment/{guest_no}/refund",
                    json={"refundAmount": 19.0, "refundReason": "游客退款"})
    check("退款: 游客单无头 403(走客服)", r.status_code == 403,
          f"got {r.status_code}")
    r = client.post(f"/api/payment/{guest_no}/refund", headers=ADMIN,
                    json={"refundAmount": 19.0, "refundReason": "客服代办"})
    check("退款: 游客单 admin 200", r.status_code == 200,
          f"{r.status_code} {r.text[:150]}")

    # ============================================================
    # 5. GET /{pay_no}/refunds 退款列表归属鉴权
    # ============================================================
    r = client.get(f"/api/payment/{pay_no}/refunds")
    check("退款列表: 无头 401", r.status_code == 401, f"got {r.status_code}")
    r = client.get(f"/api/payment/{pay_no}/refunds", headers=OTHER)
    check("退款列表: 他人 403", r.status_code == 403, f"got {r.status_code}")
    r = client.get(f"/api/payment/{pay_no}/refunds", headers=M)
    check("退款列表: 归属 200", r.status_code == 200)
    r = client.get(f"/api/payment/{guest_no}/refunds")
    check("退款列表: 游客单无头 200", r.status_code == 200)

    # ============================================================
    # 6. 对账批次列表/详情 admin 鉴权(财务敏感)
    # ============================================================
    r = client.get("/api/payment/reconciliations")
    check("对账列表: 无头 403", r.status_code == 403, f"got {r.status_code}")
    r = client.get("/api/payment/reconciliations", headers=ADMIN)
    check("对账列表: admin 200", r.status_code == 200, f"got {r.status_code}")
    r = client.post("/api/payment/reconciliation/start", headers=ADMIN,
                    json={"reconDate": "2026-09-01", "channel": "alipay"})
    check("造数: 对账批次创建", r.status_code == 200, f"{r.text[:150]}")
    recon_no = r.json().get("reconNo", "")
    r = client.get(f"/api/payment/reconciliation/{recon_no}")
    check("对账详情: 无头 403", r.status_code == 403, f"got {r.status_code}")
    r = client.get(f"/api/payment/reconciliation/{recon_no}", headers=ADMIN)
    check("对账详情: admin 200", r.status_code == 200, f"got {r.status_code}")

    # ============================================================
    # 7. 渠道详情 admin 鉴权(商户号/费率敏感)
    # ============================================================
    r = client.get("/api/payment/channel/alipay")
    check("渠道详情: 无头 403", r.status_code == 403, f"got {r.status_code}")
    r = client.get("/api/payment/channel/alipay", headers=ADMIN)
    check("渠道详情: admin 200 且含商户号", r.status_code == 200
          and r.json().get("merchantId") == "2088000001")

    # ============================================================
    # 8. channels/active 公开 + 脱敏
    # ============================================================
    r = client.get("/api/payment/channels/active")
    body = r.json()
    check("启用渠道: 公开 200", r.status_code == 200 and body.get("success") is True)
    items = body.get("items", [])
    check("启用渠道: 非空", len(items) >= 1)
    leaked = [k for k in ("merchantId", "feeRate", "dailyLimit",
                          "monthlyLimit", "retryMax", "settleCycle")
              if k in items[0]] if items else ["merchantId"]
    check("启用渠道: 敏感字段已脱敏", not leaked, f"泄露字段 {leaked}")
    check("启用渠道: 收银台字段保留",
          all(k in items[0] for k in ("channelCode", "channelName")) if items else False)

    # ============================================================
    # 9. 回调端点保持公开(渠道推送链路)
    # ============================================================
    r = client.post("/api/payment/callback/pay", json={
        "channelTradeNo": "AUTH-TRADE-1", "payNo": pay_no})
    check("回调: 公开幂等 200", r.status_code == 200
          and r.json().get("idempotent") is True)


def main():
    client = TestClient(app)
    run(client)
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
