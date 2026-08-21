"""P1 表 Service 层冒烟测试(对账流程 + 渠道管理)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_payment_p1_service.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.payment_service import PaymentService
from repositories.store import _mock_store
from repositories.payment_repository import (
    RECON_STATUS_MATCHED, RECON_STATUS_DIFF, RECON_STATUS_INVESTIGATING,
    RECON_STATUS_RESOLVED,
    CHANNEL_STATUS_ACTIVE, CHANNEL_STATUS_MAINTENANCE, CHANNEL_STATUS_DISABLED,
    CHANNEL_TYPE_THIRD_PARTY, FEE_TYPE_RATIO,
)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


async def run():
    global PASS, FAIL

    # 清理
    for k in list(_mock_store.keys()):
        if "payment" in k or "recon" in k or "channel" in k:
            del _mock_store[k]

    svc = PaymentService()
    print("\n========================================")
    print("  P1 表 Service 层冒烟测试")
    print("========================================\n")

    # ============================================================
    # 1. 渠道配置
    # ============================================================
    print("【1. 渠道配置 - CRUD + 限额校验】")

    # 创建渠道
    print("  --- create_channel ---")
    ch = await svc.create_channel(
        channel_code="WECHAT", channel_name="微信支付",
        channel_type=CHANNEL_TYPE_THIRD_PARTY,
        supported_methods=["native", "jsapi"],
        supported_scenes=["order_pay", "wallet_deposit"],
        merchant_id="1900000101", fee_rate=0.006,
        min_amount=0.01, max_amount=50000,
        daily_limit=500000, monthly_limit=5000000,
    )
    check("创建渠道", ch["channelCode"] == "WECHAT")
    check("默认状态 active", ch["status"] == CHANNEL_STATUS_ACTIVE)

    # 参数非法
    try:
        await svc.create_channel(
            channel_code="BAD", channel_name="x",
            channel_type="invalid", supported_methods=[], supported_scenes=[],
            merchant_id="x", fee_rate=0.006,
        )
        check("非法渠道类型失败", False)
    except ValueError:
        check("非法渠道类型失败", True)

    try:
        await svc.create_channel(
            channel_code="BAD2", channel_name="x",
            channel_type=CHANNEL_TYPE_THIRD_PARTY,
            supported_methods=[], supported_scenes=[],
            merchant_id="x", fee_rate=1.5,
        )
        check("费率超限失败", False)
    except ValueError:
        check("费率超限失败", True)

    try:
        await svc.create_channel(
            channel_code="BAD3", channel_name="x",
            channel_type=CHANNEL_TYPE_THIRD_PARTY,
            supported_methods=[], supported_scenes=[],
            merchant_id="x", fee_rate=0.006,
            min_amount=100, max_amount=50,
        )
        check("最小>最大失败", False)
    except ValueError:
        check("最小>最大失败", True)

    # 查询
    print("  --- get_channel ---")
    ch = await svc.get_channel("WECHAT")
    check("查询渠道", ch["channelName"] == "微信支付")
    try:
        await svc.get_channel("NOT_EXIST")
        check("不存在抛 KeyError", False)
    except KeyError:
        check("不存在抛 KeyError", True)

    # 更新
    print("  --- update_channel ---")
    ch = await svc.update_channel("WECHAT", {"feeRate": 0.0055})
    check("更新费率", ch["feeRate"] == 0.0055)
    try:
        await svc.update_channel("WECHAT", {"feeRate": 2.0})
        check("费率超限失败", False)
    except ValueError:
        check("费率超限失败", True)

    # 启停
    print("  --- toggle_channel_status ---")
    ch = await svc.toggle_channel_status("WECHAT", CHANNEL_STATUS_MAINTENANCE)
    check("状态变维护中", ch["status"] == CHANNEL_STATUS_MAINTENANCE)
    try:
        await svc.toggle_channel_status("WECHAT", CHANNEL_STATUS_MAINTENANCE)
        check("重复状态失败", False)
    except ValueError:
        check("重复状态失败", True)
    ch = await svc.toggle_channel_status("WECHAT", CHANNEL_STATUS_ACTIVE)
    check("状态恢复启用", ch["status"] == CHANNEL_STATUS_ACTIVE)

    # 限额校验
    print("  --- check_channel_limit ---")
    r = await svc.check_channel_limit("WECHAT", 1000)
    check("正常金额通过", r["passed"] is True)
    r = await svc.check_channel_limit("WECHAT", 60000)
    check("超过单笔最大失败", not r["passed"])

    # 渠道未启用时校验
    await svc.toggle_channel_status("WECHAT", CHANNEL_STATUS_DISABLED)
    try:
        await svc.check_channel_limit("WECHAT", 1000)
        check("渠道停用校验失败", False)
    except ValueError:
        check("渠道停用校验失败", True)
    await svc.toggle_channel_status("WECHAT", CHANNEL_STATUS_ACTIVE)

    # 列表
    print("  --- list_channels ---")
    await svc.create_channel(
        channel_code="ALIPAY", channel_name="支付宝",
        channel_type=CHANNEL_TYPE_THIRD_PARTY,
        supported_methods=["native"], supported_scenes=["order_pay"],
        merchant_id="2088...", fee_rate=0.005,
    )
    r = await svc.list_channels()
    check("列出所有渠道 2 个", r["count"] == 2)
    r = await svc.list_active_channels()
    check("启用渠道 2 个", r["count"] == 2)

    # 累计交易额
    print("  --- record_channel_transaction ---")
    r = await svc.record_channel_transaction("WECHAT", 1000)
    check("累计 dailyAmount=1000", r["dailyAmount"] == 1000.0)
    r = await svc.record_channel_transaction("WECHAT", 500)
    check("再累计 dailyAmount=1500", r["dailyAmount"] == 1500.0)

    # 重置统计
    print("  --- reset_daily_stats / reset_monthly_stats ---")
    r = await svc.reset_daily_stats()
    check("重置日统计 2 个", r["count"] == 2)
    ch = await svc.get_channel("WECHAT")
    check("日累计已重置", ch["dailyAmount"] == 0.0)
    check("月累计未重置", ch["monthlyAmount"] == 1500.0)
    r = await svc.reset_monthly_stats()
    check("重置月统计 2 个", r["count"] == 2)
    ch = await svc.get_channel("WECHAT")
    check("月累计已重置", ch["monthlyAmount"] == 0.0)

    # ============================================================
    # 2. 对账记录 - 完全对平场景
    # ============================================================
    print("\n【2. 对账记录 - 完全对平场景】")

    # 先创建一个已支付订单
    pay = await svc.create_pay(
        user_id="U001", order_id="ORD001", order_type="order",
        total_amount=850.0, pay_channel="wechat", pay_method="native",
        scene_type="order_pay",
    )
    pay_no = pay["payNo"]
    await svc.start_pay(pay_no)
    await svc.pay_callback(
        channel_trade_no="WX_TRADE_001",
        callback_content={"amount": 850.0, "status": "SUCCESS"},
        pay_no=pay_no,
    )

    # 启动对账
    print("  --- start_reconciliation (完全对平) ---")
    recon = await svc.start_reconciliation("2026-08-22", "wechat")
    check("对账批次创建", recon["reconNo"] == "RECON20260822WECHAT")
    check("对账状态 matched", recon["status"] == RECON_STATUS_MATCHED)
    check("平台流水 1 笔", recon["platformCount"] == 1)
    check("渠道流水 1 笔", recon["channelCount"] == 1)
    check("差异 0 笔", recon["diffCount"] == 0)
    check("对平类型 full", recon["matchType"] == "full")

    # 重复对账应失败
    try:
        await svc.start_reconciliation("2026-08-22", "wechat")
        check("重复对账失败", False)
    except ValueError:
        check("重复对账失败", True)

    # 查询
    print("  --- get_reconciliation ---")
    r = await svc.get_reconciliation("RECON20260822WECHAT")
    check("查询对账详情", r["reconNo"] == "RECON20260822WECHAT")
    try:
        await svc.get_reconciliation("NOT_EXIST")
        check("不存在抛 KeyError", False)
    except KeyError:
        check("不存在抛 KeyError", True)

    # 列表
    print("  --- list_reconciliations ---")
    r = await svc.list_reconciliations(recon_date="2026-08-22")
    check("按日期查询 1 条", r["count"] == 1)
    r = await svc.list_reconciliations(channel="wechat")
    check("按渠道查询 1 条", r["count"] == 1)
    r = await svc.list_reconciliations(status=RECON_STATUS_MATCHED)
    check("按状态查询 1 条 matched", r["count"] == 1)

    # ============================================================
    # 3. 对账记录 - 差异场景(状态机: diff → investigating → resolved)
    # ============================================================
    print("\n【3. 对账记录 - 差异场景 + 状态机】")

    # 创建另一日已支付订单(用于差异对账)
    pay2 = await svc.create_pay(
        user_id="U002", order_id="ORD002", order_type="order",
        total_amount=1000.0, pay_channel="alipay", pay_method="native",
        scene_type="order_pay",
    )
    await svc.start_pay(pay2["payNo"])
    await svc.pay_callback(
        channel_trade_no="ALI_TRADE_001",
        callback_content={"amount": 1000.0, "status": "SUCCESS"},
        pay_no=pay2["payNo"],
    )

    # 启动 ALIPAY 对账(完全对平, 无差异)
    recon2 = await svc.start_reconciliation("2026-08-22", "alipay")
    check("ALIPAY 对账 matched", recon2["status"] == RECON_STATUS_MATCHED)

    # 手动构造差异场景: 创建一个 diff 状态的对账批次
    from repositories.payment_repository import PaymentRepository
    repo = PaymentRepository()
    await repo.create_recon({
        "reconNo": "RECON20260821WECHAT", "reconDate": "2026-08-21",
        "channel": "wechat", "status": "diff",
        "platformCount": 1, "platformAmount": 850.0,
        "channelCount": 1, "channelAmount": 860.0,
        "diffCount": 1, "diffAmount": 10.0,
        "diffDetails": [{
            "payNo": "PAY_OLD", "channelTradeNo": "WX_OLD",
            "type": "amount_mismatch", "platformAmount": 850.0,
            "channelAmount": 860.0, "diffAmount": 10.0,
            "handleSuggestion": "refund",
        }],
    })

    # 待处理差异列表
    print("  --- list_pending_diffs ---")
    r = await svc.list_pending_diffs()
    check("待处理差异 1 条", r["count"] == 1)

    # 介入调查
    print("  --- investigate_diff ---")
    r = await svc.investigate_diff("RECON20260821WECHAT", operator="admin01")
    check("状态变 investigating", r["status"] == RECON_STATUS_INVESTIGATING)

    # 状态非法: matched 不可调查
    try:
        await svc.investigate_diff("RECON20260822WECHAT", "admin")
        check("matched 不可调查", False)
    except ValueError:
        check("matched 不可调查", True)

    # 处理完成
    print("  --- resolve_reconciliation ---")
    r = await svc.resolve_reconciliation("RECON20260821WECHAT", operator="admin01",
                                            remark="已退款 10 元")
    check("状态变 resolved", r["status"] == RECON_STATUS_RESOLVED)

    # resolved 移出 pending
    r = await svc.list_pending_diffs()
    check("resolved 后 pending 0 条", r["count"] == 0)

    # 状态非法: resolved 不可再处理
    try:
        await svc.resolve_reconciliation("RECON20260821WECHAT", "admin")
        check("resolved 不可再处理", False)
    except ValueError:
        check("resolved 不可再处理", True)

    # ============================================================
    # 汇总
    # ============================================================
    print("\n========================================")
    print(f"  通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
    print("========================================")
    if FAIL == 0:
        print("  全部测试通过!")
    return FAIL


if __name__ == "__main__":
    rc = asyncio.run(run())
    sys.exit(rc)
