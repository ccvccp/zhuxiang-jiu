"""payment_repository.py 冒烟测试(内存模式)

验证 P0 三张表的核心 CRUD + 幂等性 + 状态机 + 索引维护
"""
import asyncio
import sys
import os

# 设置内存模式
os.environ["STORE_MODE"] = "asyncio"

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))


async def main():
    from repositories.payment_repository import (
        PaymentRepository,
        PAY_STATUS_PENDING, PAY_STATUS_PAYING, PAY_STATUS_PAID,
        PAY_STATUS_FAILED, PAY_STATUS_CLOSED, PAY_STATUS_REFUNDED,
        REFUND_STATUS_PENDING, REFUND_STATUS_AUDITING, REFUND_STATUS_APPROVED,
        REFUND_STATUS_REJECTED, REFUND_STATUS_REFUNDED,
        PAYOUT_STATUS_PENDING, PAYOUT_STATUS_AUDITING, PAYOUT_STATUS_APPROVED,
        PAYOUT_STATUS_PAYING, PAYOUT_STATUS_PAID, PAYOUT_STATUS_FAILED,
        PAYOUT_STATUS_REJECTED,
    )

    repo = PaymentRepository()
    passed = 0
    failed = 0

    def check(name, ok, detail=""):
        nonlocal passed, failed
        if ok:
            passed += 1
        else:
            failed += 1
            print(f"  [FAIL] {name}: {detail}")

    # ========================================================
    # 1. 序列号生成
    # ========================================================
    print("=== 1. 序列号生成 ===")
    pay_no = await repo.next_pay_no()
    check("支付单号前缀 PAY", pay_no.startswith("PAY"))
    refund_no = await repo.next_refund_no()
    check("退款单号前缀 RF", refund_no.startswith("RF"))
    payout_no = await repo.next_payout_no()
    check("付款单号前缀 PO", payout_no.startswith("PO"))
    # 唯一性
    pay_no2 = await repo.next_pay_no()
    check("支付单号递增不重复", pay_no != pay_no2, f"{pay_no} == {pay_no2}")

    # ========================================================
    # 2. 支付订单 CRUD
    # ========================================================
    print("=== 2. 支付订单 CRUD ===")
    order = {
        "payNo": pay_no,
        "orderId": "ORD001",
        "orderType": "retail",
        "userId": "9001",
        "payChannel": "alipay",
        "payMethod": "jsapi",
        "totalAmount": 1000.00,
        "discountAmount": 100.00,
        "pointsAmount": 50.00,
        "actualAmount": 850.00,
        "refundedAmount": 0.00,
        "status": PAY_STATUS_PENDING,
        "sceneType": "order_pay",
        "createdAt": "2026-08-21T20:15:00+08:00",
    }
    saved = await repo.save_order(order)
    check("save_order 成功", saved.get("payNo") == pay_no)

    # 重复保存应报错
    try:
        await repo.save_order(order)
        check("重复 save_order 报错", False, "未抛出 ValueError")
    except ValueError:
        check("重复 save_order 报错", True)

    # 查询
    got = await repo.get_order(pay_no)
    check("get_order 成功", got is not None and got["payNo"] == pay_no)
    check("get_order 不存在返回 None", await repo.get_order("NOT_EXIST") is None)

    # 用户索引查询
    user_orders = await repo.list_orders("9001")
    check("list_orders 按用户索引", len(user_orders) == 1)

    # 按 status 筛选
    user_orders_pending = await repo.list_orders("9001", status=PAY_STATUS_PENDING)
    check("list_orders status 筛选", len(user_orders_pending) == 1)
    user_orders_paid = await repo.list_orders("9001", status=PAY_STATUS_PAID)
    check("list_orders status 不匹配返回空", len(user_orders_paid) == 0)

    # 按 sceneType 筛选
    user_orders_scene = await repo.list_orders("9001", scene_type="order_pay")
    check("list_orders sceneType 筛选", len(user_orders_scene) == 1)

    # 按 orderId 查询
    by_order = await repo.list_by_order("ORD001")
    check("list_by_order", len(by_order) == 1)
    by_order_filtered = await repo.list_by_order("ORD001", order_type="retail")
    check("list_by_order type 筛选", len(by_order_filtered) == 1)

    # ========================================================
    # 3. 幂等校验(find_active_by_order)
    # ========================================================
    print("=== 3. 幂等校验(活跃支付单) ===")
    active = await repo.find_active_by_order("ORD001")
    check("find_active_by_order 找到活跃单", active is not None)
    active_filtered = await repo.find_active_by_order("ORD001", order_type="retail")
    check("find_active_by_order type 匹配", active_filtered is not None)
    active_wrong = await repo.find_active_by_order("ORD001", order_type="groupbuy")
    check("find_active_by_order type 不匹配返回 None", active_wrong is None)
    active_notexist = await repo.find_active_by_order("NOT_EXIST")
    check("find_active_by_order 不存在返回 None", active_notexist is None)

    # ========================================================
    # 4. 状态机流转
    # ========================================================
    print("=== 4. 状态机流转 ===")
    # pending → paying
    await repo.update_order_fields(pay_no, {
        "status": PAY_STATUS_PAYING,
        "channelTradeNo": "ALIPAY202608210001",
    })
    got = await repo.get_order(pay_no)
    check("状态 pending → paying", got["status"] == PAY_STATUS_PAYING)
    # 渠道交易号索引维护
    by_ctn = await repo.get_by_channel_trade_no("ALIPAY202608210001")
    check("渠道交易号索引建立", by_ctn is not None and by_ctn["payNo"] == pay_no)

    # paying → paid
    await repo.update_order_fields(pay_no, {
        "status": PAY_STATUS_PAID,
        "payTime": "2026-08-21T20:15:30+08:00",
        "callbackTime": "2026-08-21T20:15:31+08:00",
    })
    got = await repo.get_order(pay_no)
    check("状态 paying → paid", got["status"] == PAY_STATUS_PAID)

    # paid 后 active 校验(paid 仍在活跃集合)
    active = await repo.find_active_by_order("ORD001")
    check("paid 仍在活跃集合", active is not None and active["payNo"] == pay_no)

    # ========================================================
    # 5. 回调幂等锁
    # ========================================================
    print("=== 5. 回调幂等锁 ===")
    lock1 = await repo.acquire_callback_lock("ALIPAY202608210001")
    check("首次获取回调锁", lock1 is True)
    lock2 = await repo.acquire_callback_lock("ALIPAY202608210001")
    check("重复获取回调锁失败", lock2 is False)
    # 退款回调锁
    lock3 = await repo.acquire_refund_callback_lock("ALIPAY_REFUND_001")
    check("首次获取退款回调锁", lock3 is True)
    lock4 = await repo.acquire_refund_callback_lock("ALIPAY_REFUND_001")
    check("重复获取退款回调锁失败", lock4 is False)

    # ========================================================
    # 6. 部分退款累计
    # ========================================================
    print("=== 6. 部分退款累计 ===")
    # 第一次部分退款 300
    new_total = await repo.add_refunded_amount(pay_no, 300.00)
    check("累计退款 300", abs(new_total - 300.00) < 0.01, f"实际 {new_total}")
    # 第二次部分退款 200
    new_total = await repo.add_refunded_amount(pay_no, 200.00)
    check("累计退款 500", abs(new_total - 500.00) < 0.01, f"实际 {new_total}")
    # 不存在的支付单
    try:
        await repo.add_refunded_amount("NOT_EXIST", 100)
        check("add_refunded_amount 不存在报错", False)
    except KeyError:
        check("add_refunded_amount 不存在报错", True)

    # ========================================================
    # 7. 退款记录 CRUD + pending 集合
    # ========================================================
    print("=== 7. 退款记录 CRUD + pending 集合 ===")
    rf_no = await repo.next_refund_no()
    refund = {
        "refundNo": rf_no,
        "payNo": pay_no,
        "orderId": "ORD001",
        "userId": "9001",
        "refundType": "partial",
        "refundAmount": 300.00,
        "refundedAmount": 0.00,
        "refundReason": "商品质量问题",
        "refundChannel": "alipay",
        "status": REFUND_STATUS_PENDING,
        "createdAt": "2026-08-21T20:50:00+08:00",
    }
    await repo.save_refund(refund)
    got_rf = await repo.get_refund(rf_no)
    check("get_refund 成功", got_rf is not None)
    check("get_refund 不存在返回 None", await repo.get_refund("NOT_EXIST") is None)

    # pending 集合
    pending_rf = await repo.list_pending_refunds()
    check("pending 退款集合有 1 项", len(pending_rf) == 1, f"实际 {len(pending_rf)}")

    # 按 pay_no 查询
    rf_list = await repo.list_refunds(pay_no)
    check("list_refunds 按 payNo", len(rf_list) == 1)
    rf_list_paid = await repo.list_refunds(pay_no, status=REFUND_STATUS_REFUNDED)
    check("list_refunds status 不匹配返回空", len(rf_list_paid) == 0)

    # 状态流转: pending → auditing → approved → refunded
    await repo.update_refund_fields(rf_no, {"status": REFUND_STATUS_AUDITING})
    pending_rf = await repo.list_pending_refunds()
    check("auditing 仍在 pending 集合", len(pending_rf) == 1)

    # approved 已通过审核, 移出 pending 集合
    await repo.update_refund_fields(rf_no, {"status": REFUND_STATUS_APPROVED})
    pending_rf = await repo.list_pending_refunds()
    check("approved 移出 pending 集合", len(pending_rf) == 0, f"实际 {len(pending_rf)}")

    await repo.update_refund_fields(rf_no, {
        "status": REFUND_STATUS_REFUNDED,
        "refundTime": "2026-08-21T21:05:00+08:00",
        "channelRefundNo": "ALIPAY_REFUND_001",
    })
    pending_rf = await repo.list_pending_refunds()
    check("refunded 仍在 pending 集合外", len(pending_rf) == 0, f"实际 {len(pending_rf)}")

    # 累计退款金额
    refunded_total = await repo.sum_refunded_amount(pay_no)
    check("sum_refunded_amount 300", abs(refunded_total - 300.00) < 0.01, f"实际 {refunded_total}")

    # 再加一笔已退款记录,验证累计
    rf_no2 = await repo.next_refund_no()
    refund2 = {**refund, "refundNo": rf_no2, "refundAmount": 200.00,
               "status": REFUND_STATUS_REFUNDED}
    await repo.save_refund(refund2)
    refunded_total = await repo.sum_refunded_amount(pay_no)
    check("sum_refunded_amount 累计 500", abs(refunded_total - 500.00) < 0.01,
          f"实际 {refunded_total}")

    # 不存在退款记录
    try:
        await repo.update_refund_fields("NOT_EXIST", {"status": REFUND_STATUS_APPROVED})
        check("update_refund_fields 不存在报错", False)
    except KeyError:
        check("update_refund_fields 不存在报错", True)

    # ========================================================
    # 8. 付款记录 CRUD + pending 集合
    # ========================================================
    print("=== 8. 付款记录 CRUD + pending 集合 ===")
    po_no = await repo.next_payout_no()
    payout = {
        "payoutNo": po_no,
        "payoutType": "wallet_withdraw",
        "sourceId": "WD202608210001",
        "payeeName": "张三",
        "payeeAccount": "6222000112345678",
        "payeeBank": "工商银行",
        "amount": 1000.00,
        "taxAmount": 0.00,
        "actualAmount": 1000.00,
        "payChannel": "bank_transfer",
        "status": PAYOUT_STATUS_PENDING,
        "retryCount": 0,
        "createdAt": "2026-08-21T21:00:00+08:00",
    }
    await repo.save_payout(payout)
    got_po = await repo.get_payout(po_no)
    check("get_payout 成功", got_po is not None)
    check("get_payout 不存在返回 None", await repo.get_payout("NOT_EXIST") is None)

    # pending 集合
    pending_po = await repo.list_pending_payouts()
    check("pending 付款集合有 1 项", len(pending_po) == 1)

    # 按 source_id 查询(幂等校验)
    by_source = await repo.find_by_source("WD202608210001")
    check("find_by_source 成功", by_source is not None and by_source["payoutNo"] == po_no)
    by_source_typed = await repo.find_by_source("WD202608210001", payout_type="wallet_withdraw")
    check("find_by_source type 匹配", by_source_typed is not None)
    by_source_wrong = await repo.find_by_source("WD202608210001", payout_type="salary")
    check("find_by_source type 不匹配返回 None", by_source_wrong is None)

    # 按 type 列表查询
    po_list = await repo.list_payouts(payout_type="wallet_withdraw")
    check("list_payouts type 筛选", len(po_list) == 1)
    po_list_pending = await repo.list_payouts(status=PAYOUT_STATUS_PENDING)
    check("list_payouts status 筛选", len(po_list_pending) == 1)

    # 状态流转: pending → auditing → approved → paying → paid
    await repo.update_payout_fields(po_no, {"status": PAYOUT_STATUS_AUDITING})
    pending_po = await repo.list_pending_payouts()
    check("auditing 仍在 pending 集合", len(pending_po) == 1)

    # approved 已通过审核, 移出 pending 集合
    await repo.update_payout_fields(po_no, {"status": PAYOUT_STATUS_APPROVED,
                                              "auditor": "admin"})
    pending_po = await repo.list_pending_payouts()
    check("approved 移出 pending 集合", len(pending_po) == 0, f"实际 {len(pending_po)}")

    # paying 打款中, 也不在 pending 集合
    await repo.update_payout_fields(po_no, {"status": PAYOUT_STATUS_PAYING,
                                              "channelPayoutNo": "BK202608210001"})
    pending_po = await repo.list_pending_payouts()
    check("paying 不在 pending 集合", len(pending_po) == 0, f"实际 {len(pending_po)}")

    await repo.update_payout_fields(po_no, {"status": PAYOUT_STATUS_PAID,
                                              "payTime": "2026-08-21T21:10:00+08:00"})
    pending_po = await repo.list_pending_payouts()
    check("paid 仍在 pending 集合外", len(pending_po) == 0, f"实际 {len(pending_po)}")

    # 不存在付款记录
    try:
        await repo.update_payout_fields("NOT_EXIST", {"status": PAYOUT_STATUS_APPROVED})
        check("update_payout_fields 不存在报错", False)
    except KeyError:
        check("update_payout_fields 不存在报错", True)

    # ========================================================
    # 9. 付款重试次数累加
    # ========================================================
    print("=== 9. 付款重试次数累加 ===")
    po_no2 = await repo.next_payout_no()
    payout2 = {**payout, "payoutNo": po_no2, "sourceId": "WD202608210002"}
    await repo.save_payout(payout2)
    # 初始 retryCount=0
    got_po = await repo.get_payout(po_no2)
    check("初始 retryCount=0", got_po.get("retryCount") == 0)
    # +1
    n1 = await repo.increment_payout_retry(po_no2)
    check("increment 重试 +1", n1 == 1)
    # +1
    n2 = await repo.increment_payout_retry(po_no2)
    check("increment 重试 +2", n2 == 2)
    # update_payout_fields 中 retryCount 累加
    await repo.update_payout_fields(po_no2, {"retryCount": 1, "status": PAYOUT_STATUS_FAILED,
                                              "failReason": "BANK_ACCOUNT_INVALID"})
    got_po = await repo.get_payout(po_no2)
    check("update_payout_fields retryCount 累加到 3", got_po.get("retryCount") == 3,
          f"实际 {got_po.get('retryCount')}")
    # failed 仍在活跃集合(可重试)
    active_po = await repo.find_by_source("WD202608210002")
    check("failed 仍可通过 source 查到", active_po is not None)

    # ========================================================
    # 10. 状态机终态验证
    # ========================================================
    print("=== 10. 状态机终态验证 ===")
    # closed 终态(不在活跃集合)
    po_no3 = await repo.next_payout_no()
    payout3 = {**payout, "payoutNo": po_no3, "sourceId": "WD202608210003"}
    await repo.save_payout(payout3)
    await repo.update_payout_fields(po_no3, {"status": PAYOUT_STATUS_REJECTED})
    pending_po = await repo.list_pending_payouts()
    check("rejected 移出 pending 集合", len(pending_po) == 0)

    # 支付单 closed 终态不在活跃集合
    pay_no_closed = await repo.next_pay_no()
    order_closed = {**order, "payNo": pay_no_closed, "orderId": "ORD002"}
    await repo.save_order(order_closed)
    await repo.update_order_fields(pay_no_closed, {"status": PAY_STATUS_CLOSED})
    active = await repo.find_active_by_order("ORD002")
    check("closed 不在活跃集合", active is None)

    # refunded 终态不在活跃集合
    pay_no_refunded = await repo.next_pay_no()
    order_refunded = {**order, "payNo": pay_no_refunded, "orderId": "ORD003"}
    await repo.save_order(order_refunded)
    await repo.update_order_fields(pay_no_refunded, {"status": PAY_STATUS_REFUNDED})
    active = await repo.find_active_by_order("ORD003")
    check("refunded 不在活跃集合", active is None)

    # ========================================================
    # 汇总
    # ========================================================
    print(f"\n通过: {passed}  失败: {failed}  总计: {passed + failed}")
    if failed == 0:
        print("全部测试通过!")
    return failed


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
