"""收款管理模块 端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 PaymentService 方法, 模拟 20 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_payment_routes.py

覆盖 20 个接口对应的业务方法:
    1. 支付订单(6):  create_pay / get_pay / list_pays / start_pay / pay_callback / close_pay
    2. 退款(6):      create_refund / list_refunds / audit_refund / cancel_refund
                     refund_callback / list_pending_refunds
    3. 付款(8):      create_payout / list_payouts / list_pending_payouts / get_payout
                     audit_payout / execute_payout / retry_payout / payout_callback

测试覆盖:
    - 完整业务流(支付→退款→付款)
    - 幂等性(回调重复/同订单重复创建/同来源重复创建)
    - 状态机(paid 不可关闭/refunded 不可再退/失败重试上限)
    - 退款超额校验(累计不超过原支付金额)
    - 付款分级审核(小额自动/大额人工)
"""
import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.payment_service import PaymentService
from repositories.payment_repository import (
    PaymentRepository,
    PAY_STATUS_PENDING, PAY_STATUS_PAYING, PAY_STATUS_PAID,
    PAY_STATUS_FAILED, PAY_STATUS_CLOSED, PAY_STATUS_REFUNDING, PAY_STATUS_REFUNDED,
    REFUND_STATUS_PENDING, REFUND_STATUS_AUDITING, REFUND_STATUS_APPROVED,
    REFUND_STATUS_REJECTED, REFUND_STATUS_REFUNDED, REFUND_STATUS_CANCELLED,
    PAYOUT_STATUS_PENDING, PAYOUT_STATUS_AUDITING, PAYOUT_STATUS_APPROVED,
    PAYOUT_STATUS_PAYING, PAYOUT_STATUS_PAID, PAYOUT_STATUS_FAILED,
    PAYOUT_STATUS_REJECTED,
)
from repositories.store import _mock_store

# 测试结果收集
PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")
        RESULTS.append((name, detail))


def expect_error(name, exc_type, exc):
    """验证是否抛出了预期类型的异常"""
    if isinstance(exc, exc_type):
        check(name, True)
    else:
        check(name, False, f"期望 {exc_type.__name__}, 实际 {type(exc).__name__}: {exc}")


async def run_e2e():
    global PASS, FAIL

    # ============================================================
    # 初始化: 清理残留
    # ============================================================
    for k in list(_mock_store.keys()):
        if k.startswith("payment") or k.startswith("_payment"):
            del _mock_store[k]

    svc = PaymentService()
    print("\n========================================")
    print("  收款管理模块端到端测试 (20 接口)")
    print("========================================\n")

    # ============================================================
    # 1. 支付订单 - 创建/查询/列表
    # ============================================================
    print("【1. 支付订单 - 创建/查询/列表】")

    # 1.1 创建支付订单
    print("  --- create_pay: 创建支付订单 ---")
    r = await svc.create_pay(
        user_id="9001", order_id="ORD20260821001", order_type="retail",
        total_amount=1000.00, pay_channel="alipay", pay_method="jsapi",
        scene_type="order_pay",
        discount_amount=100.00, points_amount=50.00,
    )
    check("create_pay 成功", r["success"] and r["payNo"].startswith("PAY"))
    check("actualAmount 计算 1000-100-50=850", r["actualAmount"] == 850.00)
    pay_no = r["payNo"]

    # 1.2 幂等: 同订单再次创建应失败
    print("  --- create_pay: 幂等校验(同订单重复创建) ---")
    try:
        await svc.create_pay(
            user_id="9001", order_id="ORD20260821001", order_type="retail",
            total_amount=1000.00, pay_channel="alipay",
        )
        check("同订单重复创建支付应失败", False)
    except ValueError as e:
        check("同订单重复创建支付失败", "活跃支付单" in str(e))

    # 1.3 参数校验: 金额过低
    print("  --- create_pay: 参数校验 ---")
    try:
        await svc.create_pay("9001", "ORD_X", "retail", 0.001, "alipay")
        check("金额过低应失败", False)
    except ValueError:
        check("金额过低失败", True)

    # 1.4 参数校验: 渠道非法
    try:
        await svc.create_pay("9001", "ORD_Y", "retail", 100, "invalid_channel")
        check("渠道非法应失败", False)
    except ValueError:
        check("渠道非法失败", True)

    # 1.4b 游客扫码付(P0-3, 设计文档 2.7)
    print("  --- create_pay: 游客扫码付(guest_order_pay) ---")

    # 游客正常创建(免登录临时单)
    r = await svc.create_pay(
        user_id="guest", order_id="GUEST_ORD_001", order_type="retail",
        total_amount=398.00, pay_channel="wechat", pay_method="native",
        scene_type="guest_order_pay",
        guest_phone="13900001111", age_confirmed=True,
    )
    check("游客支付创建成功", r["success"] and r["payNo"].startswith("PAY"))
    check("游客单 isGuest 标记", r.get("isGuest") is True)
    check("游客单 guestPhone 落库", r.get("guestPhone") == "13900001111")
    check("游客单全额无抵扣", r["actualAmount"] == 398.00)
    guest_pay_no = r["payNo"]

    # 游客单笔上限 ¥5,000(边界: 5000 允许 / 5000.01 拒绝)
    r = await svc.create_pay(
        user_id="guest", order_id="GUEST_ORD_002", order_type="retail",
        total_amount=5000.00, pay_channel="alipay", pay_method="h5",
        scene_type="guest_order_pay",
        guest_phone="13900001111", age_confirmed=True,
    )
    check("游客单笔 5000 边界允许", r["success"])
    try:
        await svc.create_pay(
            user_id="guest", order_id="GUEST_ORD_003", order_type="retail",
            total_amount=5000.01, pay_channel="alipay", pay_method="h5",
            scene_type="guest_order_pay",
            guest_phone="13900001111", age_confirmed=True,
        )
        check("游客超单笔上限应失败", False)
    except ValueError as e:
        check("游客超单笔上限失败", "上限" in str(e))

    # 游客手机号必填
    try:
        await svc.create_pay(
            user_id="guest", order_id="GUEST_ORD_004", order_type="retail",
            total_amount=100.00, pay_channel="wechat", pay_method="native",
            scene_type="guest_order_pay", age_confirmed=True,
        )
        check("游客缺手机号应失败", False)
    except ValueError as e:
        check("游客缺手机号失败", "手机号" in str(e))

    # 年龄声明必填(酒类合规 P0-1 联动)
    try:
        await svc.create_pay(
            user_id="guest", order_id="GUEST_ORD_005", order_type="retail",
            total_amount=100.00, pay_channel="wechat", pay_method="native",
            scene_type="guest_order_pay", guest_phone="13900001111",
        )
        check("游客缺年龄声明应失败", False)
    except ValueError as e:
        check("游客缺年龄声明失败", "18" in str(e))

    # 仅零售标品(团购/定制/钱包充值需登录)
    try:
        await svc.create_pay(
            user_id="guest", order_id="GUEST_ORD_006", order_type="groupbuy",
            total_amount=100.00, pay_channel="wechat", pay_method="native",
            scene_type="guest_order_pay",
            guest_phone="13900001111", age_confirmed=True,
        )
        check("游客团购支付应失败", False)
    except ValueError as e:
        check("游客团购支付失败", "零售" in str(e))

    # 不支持优惠/积分抵扣
    try:
        await svc.create_pay(
            user_id="guest", order_id="GUEST_ORD_007", order_type="retail",
            total_amount=100.00, pay_channel="wechat", pay_method="native",
            scene_type="guest_order_pay", discount_amount=10.00,
            guest_phone="13900001111", age_confirmed=True,
        )
        check("游客优惠抵扣应失败", False)
    except ValueError as e:
        check("游客优惠抵扣失败", "抵扣" in str(e))

    # 仅扫码付方式(transfer 拒绝)
    try:
        await svc.create_pay(
            user_id="guest", order_id="GUEST_ORD_008", order_type="retail",
            total_amount=100.00, pay_channel="bank", pay_method="transfer",
            scene_type="guest_order_pay",
            guest_phone="13900001111", age_confirmed=True,
        )
        check("游客 transfer 支付应失败", False)
    except ValueError as e:
        check("游客 transfer 支付失败", "扫码" in str(e))

    # 游客单 15 分钟超时(区别于登录单 30 分钟)
    r = await svc.get_pay(guest_pay_no)
    check("游客单超时字段存在", bool(r.get("expireTime")))

    # 登录单不受 guest 规则影响(带抵扣正常, 独立 user 避免污染 1.6 列表计数)
    r = await svc.create_pay(
        user_id="9002", order_id="ORD20260821010", order_type="retail",
        total_amount=8000.00, pay_channel="alipay", pay_method="jsapi",
        scene_type="order_pay", discount_amount=100.00,
    )
    check("登录单超5000带抵扣正常", r["success"] and r.get("isGuest") is False)

    # 1.5 查询支付详情
    print("  --- get_pay: 查询支付详情 ---")
    r = await svc.get_pay(pay_no)
    check("get_pay 成功", r["success"] and r["payNo"] == pay_no)
    check("statusName 字段存在", r.get("statusName") == "待支付")

    # 不存在抛 KeyError
    try:
        await svc.get_pay("NOT_EXIST")
        check("get_pay 不存在抛 KeyError", False)
    except KeyError:
        check("get_pay 不存在抛 KeyError", True)

    # 1.6 列表查询
    print("  --- list_pays: 用户列表 ---")
    r = await svc.list_pays("9001")
    check("list_pays 返回 1 条", r["count"] == 1 and r["items"][0]["payNo"] == pay_no)
    r = await svc.list_pays("9001", status=PAY_STATUS_PENDING)
    check("list_pays status 筛选", r["count"] == 1)
    r = await svc.list_pays("9001", status=PAY_STATUS_PAID)
    check("list_pays status 不匹配返回 0", r["count"] == 0)

    # ============================================================
    # 2. 支付订单 - 发起支付 + 回调(含幂等)
    # ============================================================
    print("\n【2. 支付订单 - 发起/回调/幂等】")

    # 2.1 发起支付
    print("  --- start_pay: pending → paying ---")
    r = await svc.start_pay(pay_no)
    check("start_pay 成功", r["success"] and r["status"] == PAY_STATUS_PAYING)

    # 重复发起应失败
    try:
        await svc.start_pay(pay_no)
        check("重复 start_pay 失败", False)
    except ValueError:
        check("重复 start_pay 失败", True)

    # 2.2 支付回调(首次)
    print("  --- pay_callback: 首次回调 ---")
    r = await svc.pay_callback(
        channel_trade_no="ALIPAY_TRADE_20260821001",
        callback_content={"trade_status": "TRADE_SUCCESS"},
        pay_no=pay_no,
    )
    check("pay_callback 成功", r["success"] and not r["idempotent"])
    check("返回 orderId", r.get("orderId") == "ORD20260821001")

    # 2.3 重复回调(幂等返回)
    print("  --- pay_callback: 重复回调幂等 ---")
    r = await svc.pay_callback(
        channel_trade_no="ALIPAY_TRADE_20260821001",
        callback_content={"trade_status": "TRADE_SUCCESS"},
        pay_no=pay_no,
    )
    check("重复回调幂等返回", r["success"] and r["idempotent"] is True)

    # 状态校验
    r = await svc.get_pay(pay_no)
    check("回调后状态 paid", r["status"] == PAY_STATUS_PAID)

    # ============================================================
    # 3. 支付订单 - 关闭/失败
    # ============================================================
    print("\n【3. 支付订单 - 关闭/失败/重试】")

    # 3.1 关闭支付(已支付不可关闭)
    print("  --- close_pay: 已支付不可关闭 ---")
    try:
        await svc.close_pay(pay_no)
        check("已支付不可关闭", False)
    except ValueError:
        check("已支付不可关闭", True)

    # 3.2 创建第二个支付单测试关闭 + 失败 + 重试
    r = await svc.create_pay("9001", "ORD20260821002", "retail", 500, "wechat")
    pay_no2 = r["payNo"]
    r = await svc.close_pay(pay_no2, "USER_CANCEL")
    check("close_pay 成功", r["success"] and r["status"] == PAY_STATUS_CLOSED)
    # 重复关闭幂等
    r = await svc.close_pay(pay_no2, "USER_CANCEL")
    check("重复 close 幂等", r.get("idempotent") is True)

    # 3.3 失败 + 重试
    r = await svc.create_pay("9001", "ORD20260821003", "retail", 300, "alipay")
    pay_no3 = r["payNo"]
    await svc.start_pay(pay_no3)
    r = await svc.fail_pay(pay_no3, "CHANNEL_FAIL")
    check("fail_pay 成功", r["success"] and r["status"] == PAY_STATUS_FAILED)

    # failed 可重新 start_pay(状态机支持失败重试)
    r = await svc.start_pay(pay_no3)
    check("failed 可重新 start_pay", r["success"] and r["status"] == PAY_STATUS_PAYING)

    # 回调成功
    r = await svc.pay_callback("ALIPAY_TRADE_003", {}, pay_no3)
    check("failed → start → callback 成功", r["success"] and not r["idempotent"])

    # ============================================================
    # 4. 退款 - 创建/审核/回调
    # ============================================================
    print("\n【4. 退款 - 创建/审核/回调】")

    # 4.1 创建部分退款
    print("  --- create_refund: 部分退款 ---")
    r = await svc.create_refund(pay_no, 300, "商品质量问题", "partial")
    check("部分退款创建", r["success"] and r["refundAmount"] == 300)
    refund_no1 = r["refundNo"]
    # occupiedRefund 为已占用额度(不含本次), 首次为 0
    check("occupiedRefund 字段(首次=0)", r.get("occupiedRefund") == 0,
          f"实际 {r.get('occupiedRefund')}")
    check("remainRefundable 剩余 550", r.get("remainRefundable") == 550)

    # 支付单状态 → refunding
    r = await svc.get_pay(pay_no)
    check("支付单状态 refunding", r["status"] == PAY_STATUS_REFUNDING)

    # 4.2 退款超额校验
    print("  --- create_refund: 退款超额校验 ---")
    # 实付 850, 已占 300, 剩余 550, 退 600 应失败
    try:
        await svc.create_refund(pay_no, 600, "超额测试", "partial")
        check("退款超额应失败", False)
    except ValueError as e:
        check("退款超额失败", "超额" in str(e))

    # 退 550 成功(剩余全部)
    r = await svc.create_refund(pay_no, 550, "剩余退款", "partial")
    check("剩余退款成功", r["success"])
    refund_no2 = r["refundNo"]

    # 再退应失败(无可退金额)
    try:
        await svc.create_refund(pay_no, 100, "超额", "partial")
        check("无额度退款应失败", False)
    except ValueError:
        check("无额度退款失败", True)

    # 4.3 审核通过 + 退款回调
    print("  --- audit_refund + refund_callback ---")
    r = await svc.audit_refund(refund_no1, "approved", "admin", "同意")
    check("退款审核通过", r["success"] and r["status"] == REFUND_STATUS_APPROVED)

    r = await svc.refund_callback(
        channel_refund_no="ALIPAY_REFUND_001",
        callback_content={"refund_status": "REFUND_SUCCESS"},
        refund_no=refund_no1,
    )
    check("退款回调成功", r["success"] and not r["idempotent"])
    check("退款回调金额 300", r["refundAmount"] == 300)

    # 重复回调幂等
    r = await svc.refund_callback(
        channel_refund_no="ALIPAY_REFUND_001",
        callback_content={"refund_status": "REFUND_SUCCESS"},
        refund_no=refund_no1,
    )
    check("重复回调幂等", r["success"] and r["idempotent"] is True)

    # 4.4 审核拒绝 → 支付单状态回退
    print("  --- audit_refund: 拒绝 → 支付单回退 ---")
    r = await svc.audit_refund(refund_no2, "rejected", "admin", "拒绝")
    check("退款审核拒绝", r["success"] and r["status"] == REFUND_STATUS_REJECTED)

    r = await svc.get_pay(pay_no)
    check("拒绝后支付单回退 paid", r["status"] == PAY_STATUS_PAID)

    # 4.5 撤回退款
    print("  --- cancel_refund ---")
    r = await svc.create_refund(pay_no, 200, "测试撤回", "partial")
    refund_no3 = r["refundNo"]
    r = await svc.cancel_refund(refund_no3)
    check("退款撤回", r["success"] and r["status"] == REFUND_STATUS_CANCELLED)
    # 支付单状态回退
    r = await svc.get_pay(pay_no)
    check("撤回后支付单回退 paid", r["status"] == PAY_STATUS_PAID)

    # 4.6 全额退款流程
    print("  --- create_refund: 全额退款 ---")
    r = await svc.create_refund(pay_no, 0, "全额退款", "full")
    check("全额退款创建", r["success"])
    check("全额退款自动计算剩余 550", r["refundAmount"] == 550,
          f"实际 {r.get('refundAmount')}")
    refund_no4 = r["refundNo"]

    await svc.audit_refund(refund_no4, "approved")
    r = await svc.refund_callback("ALIPAY_REFUND_002", {}, refund_no4)
    check("全额退款回调成功", r["success"])

    # 支付单状态 → refunded(累计退款 = 实付金额)
    r = await svc.get_pay(pay_no)
    check("全额退款后 refunded", r["status"] == PAY_STATUS_REFUNDED,
          f"实际 {r.get('status')}, refundedAmount={r.get('refundedAmount')}")

    # 4.7 退款列表
    print("  --- list_refunds / list_pending_refunds ---")
    r = await svc.list_refunds(pay_no)
    check("退款列表 4 条", r["count"] == 4, f"实际 {r['count']}")
    r = await svc.list_pending_refunds()
    check("待审核退款 0 条", r["count"] == 0)

    # ============================================================
    # 5. 状态机完整性
    # ============================================================
    print("\n【5. 状态机完整性验证】")

    # 已退款的支付单不可关闭
    try:
        await svc.close_pay(pay_no)
        check("已退款支付单不可关闭", False)
    except ValueError:
        check("已退款支付单不可关闭", True)

    # 已退款的支付单不可再退款
    try:
        await svc.create_refund(pay_no, 100, "测试", "partial")
        check("已退款支付单不可再退", False)
    except ValueError:
        check("已退款支付单不可再退", True)

    # ============================================================
    # 6. 付款 - 创建(小额自动通过)
    # ============================================================
    print("\n【6. 付款 - 小额自动通过】")

    # 6.1 小额付款(自动通过)
    print("  --- create_payout: 小额自动通过 ---")
    r = await svc.create_payout(
        payout_type="wallet_withdraw", source_id="WD20260821001",
        payee_name="张三", payee_account="6222000112345678",
        payee_bank="工商银行", amount=1000.00,
    )
    check("小额付款自动通过", r["success"] and r["status"] == PAYOUT_STATUS_APPROVED)
    payout_no1 = r["payoutNo"]
    check("账号脱敏保留后4位", r["payeeAccountMasked"].endswith("5678"))

    # 6.2 幂等: 同来源重复创建
    print("  --- create_payout: 幂等校验 ---")
    try:
        await svc.create_payout(
            "wallet_withdraw", "WD20260821001", "张三", "6222", "工行", 1000
        )
        check("同来源重复创建付款失败", False)
    except ValueError as e:
        check("同来源重复创建付款失败", "已存在付款单" in str(e))

    # 6.3 参数校验
    try:
        await svc.create_payout("wallet_withdraw", "WD_X", "张三", "6222", "工行", 0)
        check("付款金额 ≤ 0 失败", False)
    except ValueError:
        check("付款金额 ≤ 0 失败", True)

    # ============================================================
    # 7. 付款 - 大额人工审核
    # ============================================================
    print("\n【7. 付款 - 大额人工审核】")

    r = await svc.create_payout(
        "wallet_withdraw", "WD20260821002", "李四",
        "6222000099998888", "建设银行", 10000.00,
        payee_phone="13800009002",
    )
    check("大额付款待审核", r["success"] and r["status"] == PAYOUT_STATUS_PENDING)
    payout_no2 = r["payoutNo"]

    # 7.1 审核通过
    print("  --- audit_payout: 通过 ---")
    r = await svc.audit_payout(payout_no2, "approved", "admin", "同意")
    check("付款审核通过", r["success"] and r["status"] == PAYOUT_STATUS_APPROVED)

    # 7.2 审核拒绝
    r = await svc.create_payout("salary", "SAL20260821001", "王五",
                                  "62221111", "工行", 8000)
    payout_no3 = r["payoutNo"]
    r = await svc.audit_payout(payout_no3, "rejected", "admin", "拒绝")
    check("付款审核拒绝", r["success"] and r["status"] == PAYOUT_STATUS_REJECTED)

    # ============================================================
    # 8. 付款 - 执行打款 + 成功回调
    # ============================================================
    print("\n【8. 付款 - 执行/成功回调/幂等】")

    # 8.1 执行打款
    print("  --- execute_payout ---")
    r = await svc.execute_payout(payout_no1)
    check("执行打款", r["success"] and r["status"] == PAYOUT_STATUS_PAYING)

    # 重复执行应失败
    try:
        await svc.execute_payout(payout_no1)
        check("重复 execute 失败", False)
    except ValueError:
        check("重复 execute 失败", True)

    # 8.2 成功回调
    print("  --- payout_callback: 成功 ---")
    r = await svc.payout_callback(
        payout_no1, "BK20260821001",
        {"status": "SUCCESS"}, success=True,
    )
    check("打款成功回调", r["success"] and r["status"] == PAYOUT_STATUS_PAID)

    # 8.3 重复回调幂等
    r = await svc.payout_callback(payout_no1, "BK20260821001", {}, success=True)
    check("重复回调幂等", r["success"] and r["idempotent"] is True)

    # ============================================================
    # 9. 付款 - 失败 + 重试 + 自动拒绝
    # ============================================================
    print("\n【9. 付款 - 失败/重试/自动拒绝】")

    # 9.1 执行打款(payout_no2)
    await svc.execute_payout(payout_no2)

    # 9.2 第一次失败
    r = await svc.payout_callback(
        payout_no2, "BK002", {}, success=False,
        fail_reason="NETWORK_TIMEOUT",
    )
    check("第一次失败", not r["success"] and r["status"] == PAYOUT_STATUS_FAILED)
    check("重试次数 1", r["retryCount"] == 1)

    # 9.3 重试 + 第二次失败
    await svc.retry_payout(payout_no2)
    r = await svc.payout_callback(payout_no2, "BK003", {},
                                    success=False, fail_reason="BANK_REJECT")
    check("重试次数 2", r["retryCount"] == 2)

    # 9.4 第二次重试 + 第三次失败(自动拒绝)
    await svc.retry_payout(payout_no2)
    r = await svc.payout_callback(payout_no2, "BK004", {},
                                    success=False, fail_reason="BANK_REJECT")
    check("重试次数 3 自动拒绝", r["status"] == PAYOUT_STATUS_REJECTED,
          f"实际 {r.get('status')}")

    # 9.5 已拒绝不可重试
    try:
        await svc.retry_payout(payout_no2)
        check("已拒绝不可重试", False)
    except ValueError:
        check("已拒绝不可重试", True)

    # ============================================================
    # 10. 付款 - 查询/列表
    # ============================================================
    print("\n【10. 付款 - 查询/列表】")

    # 10.1 查询付款详情
    print("  --- get_payout ---")
    r = await svc.get_payout(payout_no1)
    check("查询付款详情", r["success"] and r["payoutNo"] == payout_no1)
    check("账号脱敏", r["payeeAccountMasked"].endswith("5678"))
    check("手机号脱敏", "*" in r.get("payeePhoneMasked", ""))

    # 不存在抛 KeyError
    try:
        await svc.get_payout("NOT_EXIST")
        check("get_payout 不存在抛 KeyError", False)
    except KeyError:
        check("get_payout 不存在抛 KeyError", True)

    # 10.2 列表查询
    print("  --- list_payouts ---")
    r = await svc.list_payouts(payout_type="wallet_withdraw")
    check("按类型筛选 2 条", r["count"] == 2, f"实际 {r['count']}")
    r = await svc.list_payouts(status=PAYOUT_STATUS_PAID)
    check("按状态 paid 筛选 1 条", r["count"] == 1)

    # 10.3 待审核列表
    print("  --- list_pending_payouts ---")
    r = await svc.list_pending_payouts()
    check("待审核付款 0 条", r["count"] == 0)

    # ============================================================
    # 11. 跨场景验证 - 钱包充值场景
    # ============================================================
    print("\n【11. 跨场景验证 - 钱包充值】")

    r = await svc.create_pay(
        user_id="9001", order_id="WD20260821003", order_type="wallet_deposit",
        total_amount=5000.00, pay_channel="alipay", pay_method="page",
        scene_type="wallet_deposit",
    )
    check("钱包充值支付单创建", r["success"] and r["sceneType"] == "wallet_deposit")
    pay_no_wd = r["payNo"]

    await svc.start_pay(pay_no_wd)
    r = await svc.pay_callback("ALIPAY_WD_001", {}, pay_no_wd)
    check("钱包充值支付成功", r["success"])

    r = await svc.list_pays("9001", scene_type="wallet_deposit")
    check("按场景筛选钱包充值", r["count"] == 1)

    # ============================================================
    # 12. 状态机终态汇总
    # ============================================================
    print("\n【12. 状态机终态汇总】")

    # 创建多笔支付单, 验证各终态
    # closed 终态
    r = await svc.create_pay("9001", "ORD_CLOSED", "retail", 200, "wechat")
    pn_closed = r["payNo"]
    await svc.close_pay(pn_closed, "TIMEOUT")
    r = await svc.get_pay(pn_closed)
    check("closed 终态", r["status"] == PAY_STATUS_CLOSED)

    # refunded 终态(已在上文验证 pay_no)
    r = await svc.get_pay(pay_no)
    check("refunded 终态", r["status"] == PAY_STATUS_REFUNDED)

    # payout rejected 终态
    r = await svc.get_payout(payout_no2)
    check("payout rejected 终态", r["status"] == PAYOUT_STATUS_REJECTED)

    # payout paid 终态
    r = await svc.get_payout(payout_no1)
    check("payout paid 终态", r["status"] == PAYOUT_STATUS_PAID)

    # ============================================================
    # 汇总
    # ============================================================
    print("\n========================================")
    print(f"  通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
    print("========================================")
    if FAIL == 0:
        print("  全部测试通过!")
    else:
        print(f"  {FAIL} 项失败:")
        for name, detail in RESULTS:
            print(f"    - {name}: {detail}")
    return FAIL


if __name__ == "__main__":
    rc = asyncio.run(run_e2e())
    sys.exit(rc)
