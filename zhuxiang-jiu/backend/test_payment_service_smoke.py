"""payment_service.py 冒烟测试(内存模式)

覆盖 20 个业务方法的完整业务流:
    - 支付: 创建/查询/列表/发起/回调(幂等)/关闭/失败
    - 退款: 创建(部分/全额)/审核(通过/拒绝)/回调(幂等)/撤回/列表
    - 付款: 创建(自动通过/人工审核)/审核/执行/回调(成功/失败重试)/重试/查询/列表
"""
import asyncio
import sys
import os

os.environ["STORE_MODE"] = "asyncio"
os.environ["LOCK_MODE"] = "asyncio"
sys.path.insert(0, os.path.dirname(__file__))


async def main():
    from services.payment_service import PaymentService
    from repositories.payment_repository import (
        PAY_STATUS_PENDING, PAY_STATUS_PAYING, PAY_STATUS_PAID,
        PAY_STATUS_FAILED, PAY_STATUS_CLOSED, PAY_STATUS_REFUNDING, PAY_STATUS_REFUNDED,
        REFUND_STATUS_PENDING, REFUND_STATUS_AUDITING, REFUND_STATUS_APPROVED,
        REFUND_STATUS_REJECTED, REFUND_STATUS_REFUNDED, REFUND_STATUS_CANCELLED,
        PAYOUT_STATUS_PENDING, PAYOUT_STATUS_APPROVED, PAYOUT_STATUS_PAYING,
        PAYOUT_STATUS_PAID, PAYOUT_STATUS_FAILED, PAYOUT_STATUS_REJECTED,
    )

    svc = PaymentService()
    passed = 0
    failed = 0

    def check(name, ok, detail=""):
        nonlocal passed, failed
        if ok:
            passed += 1
        else:
            failed += 1
            print(f"  [FAIL] {name}: {detail}")

    print("=== 1. 创建支付订单 ===")
    r = await svc.create_pay(
        user_id="9001", order_id="ORD001", order_type="retail",
        total_amount=1000.00, pay_channel="alipay", pay_method="jsapi",
        scene_type="order_pay", discount_amount=100.00, points_amount=50.00,
    )
    check("创建支付成功", r["success"] and r["actualAmount"] == 850.00)
    pay_no = r["payNo"]
    # 幂等校验: 同一订单再次创建应失败
    try:
        await svc.create_pay(
            user_id="9001", order_id="ORD001", order_type="retail",
            total_amount=1000.00, pay_channel="alipay",
        )
        check("同订单重复创建失败", False)
    except ValueError:
        check("同订单重复创建失败", True)

    # 参数校验
    try:
        await svc.create_pay("9001", "ORD_X", "retail", 0, "alipay")
        check("金额过低失败", False)
    except ValueError:
        check("金额过低失败", True)
    try:
        await svc.create_pay("9001", "ORD_Y", "retail", 100, "invalid_channel")
        check("渠道非法失败", False)
    except ValueError:
        check("渠道非法失败", True)

    print("=== 2. 查询/列表 ===")
    r = await svc.get_pay(pay_no)
    check("查询支付详情", r["success"] and r["payNo"] == pay_no)
    try:
        await svc.get_pay("NOT_EXIST")
        check("查询不存在抛 KeyError", False)
    except KeyError:
        check("查询不存在抛 KeyError", True)
    r = await svc.list_pays("9001")
    check("用户列表", r["count"] == 1)
    r = await svc.list_pays("9001", status=PAY_STATUS_PENDING)
    check("按 status 筛选", r["count"] == 1)

    print("=== 3. 发起支付 ===")
    r = await svc.start_pay(pay_no)
    check("start_pay 成功", r["success"] and r["status"] == PAY_STATUS_PAYING)
    # 重复 start_pay 应失败
    try:
        await svc.start_pay(pay_no)
        check("重复 start_pay 失败", False)
    except ValueError:
        check("重复 start_pay 失败", True)

    print("=== 4. 支付回调(含幂等) ===")
    # 首次回调
    r = await svc.pay_callback("ALIPAY_TRADE_001", {"trade_status": "TRADE_SUCCESS"}, pay_no)
    check("首次回调成功", r["success"] and not r["idempotent"])
    # 状态校验
    r = await svc.get_pay(pay_no)
    check("回调后状态 paid", r["status"] == PAY_STATUS_PAID)
    # 重复回调(幂等返回)
    r = await svc.pay_callback("ALIPAY_TRADE_001", {"trade_status": "TRADE_SUCCESS"}, pay_no)
    check("重复回调幂等返回", r["success"] and r["idempotent"] is True)

    print("=== 5. 关闭支付 ===")
    # 创建第二个支付单测试关闭
    r = await svc.create_pay("9001", "ORD002", "retail", 500, "wechat")
    pay_no2 = r["payNo"]
    r = await svc.close_pay(pay_no2, "USER_CANCEL")
    check("关闭支付成功", r["success"] and r["status"] == PAY_STATUS_CLOSED)
    # 已支付不可关闭
    try:
        await svc.close_pay(pay_no)
        check("已支付不可关闭", False)
    except ValueError:
        check("已支付不可关闭", True)

    print("=== 6. 支付失败 ===")
    r = await svc.create_pay("9001", "ORD003", "retail", 300, "alipay")
    pay_no3 = r["payNo"]
    await svc.start_pay(pay_no3)
    r = await svc.fail_pay(pay_no3, "CHANNEL_FAIL")
    check("fail_pay 成功", r["success"] and r["status"] == PAY_STATUS_FAILED)
    # 重新 start_pay 可从 failed 到 paying
    r = await svc.start_pay(pay_no3)
    check("failed 可重新 start_pay", r["success"])

    print("=== 7. 创建退款(部分退款) ===")
    r = await svc.create_refund(pay_no, 300, "商品质量问题", "partial")
    check("部分退款创建", r["success"] and r["refundAmount"] == 300)
    refund_no1 = r["refundNo"]
    # 支付单状态变为 refunding
    r = await svc.get_pay(pay_no)
    check("支付单状态 refunding", r["status"] == PAY_STATUS_REFUNDING)

    print("=== 8. 退款超额校验 ===")
    # 已退 300, 实付 850, 剩余可退 550, 退 600 应失败
    try:
        await svc.create_refund(pay_no, 600, "超额测试", "partial")
        check("退款超额失败", False)
    except ValueError:
        check("退款超额失败", True)
    # 退 550 成功
    r = await svc.create_refund(pay_no, 550, "剩余退款", "partial")
    check("剩余退款成功", r["success"])
    refund_no2 = r["refundNo"]
    # 全额退款应失败(无可退金额)
    try:
        await svc.create_refund(pay_no, 0, "全额退款", "full")
        check("无可退金额失败", False)
    except ValueError:
        check("无可退金额失败", True)

    print("=== 9. 退款审核(通过 → 回调) ===")
    # 审核通过
    r = await svc.audit_refund(refund_no1, "approved", "admin", "同意")
    check("退款审核通过", r["success"] and r["status"] == REFUND_STATUS_APPROVED)
    # 退款回调
    r = await svc.refund_callback("ALIPAY_REFUND_001", {"refund_status": "REFUND_SUCCESS"}, refund_no1)
    check("退款回调成功", r["success"] and not r["idempotent"])
    check("退款回调金额", r["refundAmount"] == 300)
    # 重复回调幂等
    r = await svc.refund_callback("ALIPAY_REFUND_001", {"refund_status": "REFUND_SUCCESS"}, refund_no1)
    check("退款回调幂等", r["success"] and r["idempotent"])

    print("=== 10. 退款审核(拒绝 → 支付单回退) ===")
    r = await svc.audit_refund(refund_no2, "rejected", "admin", "拒绝")
    check("退款审核拒绝", r["success"] and r["status"] == REFUND_STATUS_REJECTED)
    # 支付单状态回退为 paid
    r = await svc.get_pay(pay_no)
    check("拒绝后支付单回退 paid", r["status"] == PAY_STATUS_PAID)

    print("=== 11. 退款撤回 ===")
    # 创建第3笔退款并撤回
    r = await svc.create_refund(pay_no, 200, "测试撤回", "partial")
    refund_no3 = r["refundNo"]
    r = await svc.cancel_refund(refund_no3)
    check("退款撤回", r["success"] and r["status"] == REFUND_STATUS_CANCELLED)
    # 支付单状态回退
    r = await svc.get_pay(pay_no)
    check("撤回后支付单回退 paid", r["status"] == PAY_STATUS_PAID)

    print("=== 12. 全额退款流程 ===")
    # 重新全额退款
    r = await svc.create_refund(pay_no, 0, "全额退款", "full")
    check("全额退款创建", r["success"] and r["refundAmount"] == 550)  # 850 - 300 = 550
    refund_no4 = r["refundNo"]
    await svc.audit_refund(refund_no4, "approved")
    r = await svc.refund_callback("ALIPAY_REFUND_002", {}, refund_no4)
    check("全额退款回调", r["success"])
    # 支付单状态变为 refunded(累计退款 = 实付金额)
    r = await svc.get_pay(pay_no)
    check("全额退款后支付单 refunded", r["status"] == PAY_STATUS_REFUNDED,
          f"实际 {r.get('status')}, refundedAmount={r.get('refundedAmount')}")

    print("=== 13. 退款列表 ===")
    r = await svc.list_refunds(pay_no)
    check("退款列表", r["count"] == 4)
    r = await svc.list_pending_refunds()
    check("待审核列表(应为0)", r["count"] == 0, f"实际 {r['count']}")

    print("=== 14. 创建付款(小额自动通过) ===")
    r = await svc.create_payout(
        payout_type="wallet_withdraw", source_id="WD001",
        payee_name="张三", payee_account="6222000112345678",
        payee_bank="工商银行", amount=1000.00,
    )
    check("小额付款自动通过", r["success"] and r["status"] == PAYOUT_STATUS_APPROVED)
    payout_no1 = r["payoutNo"]

    # 幂等校验: 同一 source 再次创建应失败
    try:
        await svc.create_payout(
            "wallet_withdraw", "WD001", "张三", "6222", "工商", 1000
        )
        check("同来源重复创建失败", False)
    except ValueError:
        check("同来源重复创建失败", True)

    print("=== 15. 创建付款(大额人工审核) ===")
    r = await svc.create_payout(
        "wallet_withdraw", "WD002", "李四", "6222000099998888",
        "建设银行", 10000.00, payee_phone="13800009002",
    )
    check("大额付款待审核", r["success"] and r["status"] == PAYOUT_STATUS_PENDING)
    payout_no2 = r["payoutNo"]

    print("=== 16. 付款审核 ===")
    r = await svc.audit_payout(payout_no2, "approved", "admin", "同意")
    check("付款审核通过", r["success"] and r["status"] == PAYOUT_STATUS_APPROVED)
    # 拒绝测试
    r = await svc.create_payout("salary", "SAL001", "王五", "6222", "工行", 8000)
    payout_no3 = r["payoutNo"]
    r = await svc.audit_payout(payout_no3, "rejected", "admin", "拒绝")
    check("付款审核拒绝", r["success"] and r["status"] == PAYOUT_STATUS_REJECTED)

    print("=== 17. 执行打款 + 成功回调 ===")
    r = await svc.execute_payout(payout_no1)
    check("执行打款", r["success"] and r["status"] == PAYOUT_STATUS_PAYING)
    r = await svc.payout_callback(payout_no1, "BK001", {"status": "SUCCESS"}, success=True)
    check("打款成功回调", r["success"] and r["status"] == PAYOUT_STATUS_PAID)
    # 重复回调幂等
    r = await svc.payout_callback(payout_no1, "BK001", {}, success=True)
    check("重复回调幂等", r["success"] and r["idempotent"])

    print("=== 18. 打款失败 + 重试 ===")
    r = await svc.execute_payout(payout_no2)
    # 第一次失败
    r = await svc.payout_callback(payout_no2, "BK002", {}, success=False,
                                    fail_reason="NETWORK_TIMEOUT")
    check("第一次失败", not r["success"] and r["status"] == PAYOUT_STATUS_FAILED)
    check("重试次数 1", r["retryCount"] == 1)
    # 重试
    r = await svc.retry_payout(payout_no2)
    check("重试打款", r["success"] and r["status"] == PAYOUT_STATUS_PAYING)
    # 第二次失败
    r = await svc.payout_callback(payout_no2, "BK003", {}, success=False,
                                    fail_reason="BANK_REJECT")
    check("重试次数 2", r["retryCount"] == 2)
    # 第二次重试
    await svc.retry_payout(payout_no2)
    # 第三次失败(达到上限)
    r = await svc.payout_callback(payout_no2, "BK004", {}, success=False,
                                    fail_reason="BANK_REJECT")
    check("重试次数 3 自动拒绝", r["status"] == PAYOUT_STATUS_REJECTED,
          f"实际 {r.get('status')}")
    # 再次重试应报错
    try:
        await svc.retry_payout(payout_no2)
        check("已拒绝不可重试", False)
    except ValueError:
        check("已拒绝不可重试", True)

    print("=== 19. 付款查询/列表 ===")
    r = await svc.get_payout(payout_no1)
    check("查询付款详情", r["success"] and r["payeeAccountMasked"].endswith("5678"))
    r = await svc.list_payouts(payout_type="wallet_withdraw")
    check("按类型筛选", r["count"] == 2)
    r = await svc.list_payouts(status=PAYOUT_STATUS_PAID)
    check("按状态筛选", r["count"] == 1)
    r = await svc.list_pending_payouts()
    check("待审核列表(0)", r["count"] == 0, f"实际 {r['count']}")

    print("=== 20. 状态机完整性验证 ===")
    # 已支付的支付单不可关闭
    try:
        await svc.close_pay(pay_no)
        check("已退款支付单不可关闭", False)
    except ValueError:
        check("已退款支付单不可关闭", True)
    # 已退款支付单不可再退款
    try:
        await svc.create_refund(pay_no, 100, "测试", "partial")
        check("已退款支付单不可再退款", False)
    except ValueError:
        check("已退款支付单不可再退款", True)

    print(f"\n通过: {passed}  失败: {failed}  总计: {passed + failed}")
    if failed == 0:
        print("全部测试通过!")
    return failed


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
