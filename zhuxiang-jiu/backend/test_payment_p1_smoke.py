"""P1 表(对账记录 + 渠道配置)冒烟测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_payment_p1_smoke.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from repositories.payment_repository import (
    PaymentRepository,
    RECON_STATUS_PENDING, RECON_STATUS_MATCHED, RECON_STATUS_DIFF,
    RECON_STATUS_INVESTIGATING, RECON_STATUS_RESOLVED,
    CHANNEL_STATUS_ACTIVE, CHANNEL_STATUS_MAINTENANCE, CHANNEL_STATUS_DISABLED,
    DIFF_TYPE_AMOUNT_MISMATCH, HANDLE_SUGGEST_REFUND,
)
from repositories.store import _mock_store

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

    repo = PaymentRepository()
    print("\n========================================")
    print("  P1 表冒烟测试 (对账 + 渠道)")
    print("========================================\n")

    # ============================================================
    # 1. 对账记录
    # ============================================================
    print("【1. 对账记录 - CRUD】")

    # 创建
    print("  --- create_recon ---")
    r = await repo.create_recon({
        "reconNo": "RECON20260821WX",
        "reconDate": "2026-08-21",
        "channel": "WECHAT",
        "status": RECON_STATUS_PENDING,
        "platformCount": 100,
        "platformAmount": 30000.0,
        "channelCount": 100,
        "channelAmount": 30000.0,
    })
    check("创建对账批次", r["reconNo"] == "RECON20260821WX")
    check("默认 diffDetails 为空数组", r["diffDetails"] == [])
    check("默认 diffCount = 0", r["diffCount"] == 0)

    # 重复创建应失败
    try:
        await repo.create_recon({"reconNo": "RECON20260821WX",
                                   "reconDate": "2026-08-21", "channel": "WECHAT"})
        check("重复创建对账失败", False)
    except ValueError:
        check("重复创建对账失败", True)

    # 查询
    print("  --- get_recon ---")
    r = await repo.get_recon("RECON20260821WX")
    check("查询对账详情", r and r["channel"] == "WECHAT")
    r = await repo.get_recon("NOT_EXIST")
    check("不存在返回 None", r is None)

    # 更新字段
    print("  --- update_recon_fields ---")
    r = await repo.update_recon_fields("RECON20260821WX", {
        "finishedAt": "2026-08-22 02:05:00",
    })
    check("更新字段", r["finishedAt"] == "2026-08-22 02:05:00")
    try:
        await repo.update_recon_fields("NOT_EXIST", {"x": 1})
        check("更新不存在抛 KeyError", False)
    except KeyError:
        check("更新不存在抛 KeyError", True)

    # 状态机: pending → diff
    print("  --- update_recon_status: pending → diff ---")
    r = await repo.update_recon_status("RECON20260821WX", RECON_STATUS_DIFF)
    check("状态变为 diff", r["status"] == RECON_STATUS_DIFF)
    check("statusName 中文", r["statusName"] == "存在差异")

    # diff_pending 集合应包含
    pending = await repo.list_pending_diffs()
    check("diff_pending 包含 1 条", len(pending) == 1)

    # 添加差异明细
    print("  --- add_diff_detail ---")
    r = await repo.add_diff_detail("RECON20260821WX", {
        "payNo": "PAY202608210001",
        "channelTradeNo": "420000222021...",
        "type": DIFF_TYPE_AMOUNT_MISMATCH,
        "platformAmount": 850.00,
        "channelAmount": 860.00,
        "diffAmount": 10.00,
        "handleSuggestion": HANDLE_SUGGEST_REFUND,
    })
    check("差异明细已添加", len(r["diffDetails"]) == 1)
    check("diffCount = 1", r["diffCount"] == 1)
    check("diffAmount = 10.00", r["diffAmount"] == 10.00)

    # 状态机: diff → investigating → resolved
    print("  --- update_recon_status: diff → investigating → resolved ---")
    await repo.update_recon_status("RECON20260821WX", RECON_STATUS_INVESTIGATING)
    pending = await repo.list_pending_diffs()
    check("investigating 仍在 pending", len(pending) == 1)

    await repo.update_recon_status("RECON20260821WX", RECON_STATUS_RESOLVED,
                                     extra={"remark": "已退款"})
    pending = await repo.list_pending_diffs()
    check("resolved 移出 pending", len(pending) == 0)
    r = await repo.get_recon("RECON20260821WX")
    check("resolved extra 字段", r["remark"] == "已退款")

    # 列表查询
    print("  --- list_recons ---")
    # 创建多个批次
    await repo.create_recon({
        "reconNo": "RECON20260821ALI", "reconDate": "2026-08-21",
        "channel": "ALIPAY", "status": RECON_STATUS_MATCHED,
        "platformCount": 50, "platformAmount": 15000.0,
        "channelCount": 50, "channelAmount": 15000.0,
    })
    await repo.create_recon({
        "reconNo": "RECON20260820WX", "reconDate": "2026-08-20",
        "channel": "WECHAT", "status": RECON_STATUS_MATCHED,
        "platformCount": 80, "platformAmount": 24000.0,
        "channelCount": 80, "channelAmount": 24000.0,
    })

    # 按日期查询
    r = await repo.list_recons(recon_date="2026-08-21")
    check("按日期查询 2 条", len(r) == 2, f"实际 {len(r)}")

    # 按渠道查询
    r = await repo.list_recons(channel="WECHAT")
    check("按渠道查询 2 条", len(r) == 2, f"实际 {len(r)}")

    # 按状态查询
    r = await repo.list_recons(status=RECON_STATUS_MATCHED)
    check("按状态查询 2 条 matched", len(r) == 2, f"实际 {len(r)}")

    # 对账锁
    print("  --- acquire_recon_lock ---")
    ok1 = await repo.acquire_recon_lock("2026-08-22", "WECHAT")
    check("首次获取对账锁", ok1 is True)
    ok2 = await repo.acquire_recon_lock("2026-08-22", "WECHAT")
    check("重复获取对账锁失败", ok2 is False)
    ok3 = await repo.acquire_recon_lock("2026-08-22", "ALIPAY")
    check("不同渠道可获取锁", ok3 is True)

    # ============================================================
    # 2. 渠道配置
    # ============================================================
    print("\n【2. 渠道配置 - CRUD】")

    # 创建
    print("  --- create_channel ---")
    r = await repo.create_channel({
        "channelCode": "WECHAT",
        "channelName": "微信支付",
        "channelType": "third_party",
        "supportedMethods": ["native", "jsapi", "h5"],
        "supportedScenes": ["order_pay", "wallet_deposit"],
        "merchantId": "1900000101",
        "feeRate": 0.006,
        "feeType": "ratio",
        "minAmount": 0.01,
        "maxAmount": 50000.00,
        "dailyLimit": 500000.00,
        "monthlyLimit": 5000000.00,
    })
    check("创建渠道", r["channelCode"] == "WECHAT")
    check("默认状态 active", r["status"] == CHANNEL_STATUS_ACTIVE)
    check("默认 dailyAmount = 0", r["dailyAmount"] == 0.0)

    # 重复创建
    try:
        await repo.create_channel({"channelCode": "WECHAT", "channelName": "x"})
        check("重复创建渠道失败", False)
    except ValueError:
        check("重复创建渠道失败", True)

    # 查询
    print("  --- get_channel ---")
    r = await repo.get_channel("WECHAT")
    check("查询渠道", r and r["channelName"] == "微信支付")
    check("supportedMethods 反序列化", isinstance(r["supportedMethods"], list))
    r = await repo.get_channel("NOT_EXIST")
    check("不存在返回 None", r is None)

    # 创建第二个渠道
    await repo.create_channel({
        "channelCode": "ALIPAY", "channelName": "支付宝",
        "channelType": "third_party", "status": CHANNEL_STATUS_DISABLED,
        "feeRate": 0.005, "feeType": "ratio",
        "minAmount": 0.01, "maxAmount": 100000.00,
        "dailyLimit": 1000000.00, "monthlyLimit": 10000000.00,
    })

    # 更新字段
    print("  --- update_channel_fields ---")
    r = await repo.update_channel_fields("WECHAT", {"feeRate": 0.0055})
    check("更新费率", r["feeRate"] == 0.0055)

    # 状态变更
    print("  --- update_channel_status ---")
    r = await repo.update_channel_status("WECHAT", CHANNEL_STATUS_MAINTENANCE)
    check("状态变为维护中", r["status"] == CHANNEL_STATUS_MAINTENANCE)
    r = await repo.update_channel_status("WECHAT", CHANNEL_STATUS_ACTIVE)
    check("状态恢复启用", r["status"] == CHANNEL_STATUS_ACTIVE)

    # 非法状态
    try:
        await repo.update_channel_status("WECHAT", "invalid")
        check("非法状态失败", False)
    except ValueError:
        check("非法状态失败", True)

    # 列表查询
    print("  --- list_channels ---")
    r = await repo.list_channels()
    check("列出所有渠道 2 个", len(r) == 2, f"实际 {len(r)}")
    r = await repo.list_channels(status=CHANNEL_STATUS_ACTIVE)
    check("按状态筛选 active", len(r) == 1)
    r = await repo.list_channels(channel_type="third_party")
    check("按类型筛选 third_party", len(r) == 2)
    r = await repo.list_active_channels()
    check("list_active_channels 1 个", len(r) == 1)

    # 限额校验
    print("  --- check_limit ---")
    # 单笔限额校验
    r = await repo.check_limit("WECHAT", 0.005)
    check("低于单笔最小失败", not r["passed"] and "低于单笔最小" in r["reason"])
    r = await repo.check_limit("WECHAT", 60000)
    check("超过单笔最大失败", not r["passed"] and "超过单笔最大" in r["reason"])
    r = await repo.check_limit("WECHAT", 1000)
    check("正常金额通过", r["passed"] is True)

    # 累计交易额
    print("  --- add_transaction_amount ---")
    r = await repo.add_transaction_amount("WECHAT", 1000)
    check("累计交易额 dailyAmount=1000", r["dailyAmount"] == 1000.0)
    check("累计交易额 dailyCount=1", r["dailyCount"] == 1)
    check("累计交易额 monthlyAmount=1000", r["monthlyAmount"] == 1000.0)

    # 再累计
    r = await repo.add_transaction_amount("WECHAT", 500)
    check("再累计 dailyAmount=1500", r["dailyAmount"] == 1500.0)
    check("再累计 dailyCount=2", r["dailyCount"] == 2)

    # 限额校验(接近上限)
    print("  --- check_limit(接近上限) ---")
    # WECHAT: maxAmount=50000, dailyLimit=500000
    # 累计 dailyAmount=1500, 退 49800 应通过(1500+49800=51300 ≤ 500000)
    r = await repo.check_limit("WECHAT", 49800)
    check("接近上限通过", r["passed"] is True, f"reason={r.get('reason')}")
    # 退 500000 应失败(超过单笔最大 50000)
    r = await repo.check_limit("WECHAT", 500000)
    check("超过单笔最大失败", not r["passed"] and "超过单笔最大" in r["reason"])

    # 单日累计超限测试(专用渠道, maxAmount=dailyLimit=1000)
    await repo.create_channel({
        "channelCode": "TEST_LIMIT", "channelName": "限额测试渠道",
        "channelType": "third_party",
        "feeRate": 0.001, "feeType": "ratio",
        "minAmount": 0.01, "maxAmount": 1000.00,
        "dailyLimit": 1000.00, "monthlyLimit": 10000.00,
    })
    await repo.add_transaction_amount("TEST_LIMIT", 600)
    # 退 500 应通过(600+500=1100? 不, 600+500=1100 > 1000 应失败)
    # 实际: 600+400=1000 ≤ 1000 通过
    r = await repo.check_limit("TEST_LIMIT", 400)
    check("单日累计刚好通过", r["passed"] is True, f"reason={r.get('reason')}")
    # 600+401=1001 > 1000 失败
    r = await repo.check_limit("TEST_LIMIT", 401)
    check("单日累计超限失败", not r["passed"] and "单日累计" in r["reason"],
          f"reason={r.get('reason')}")

    # 重置统计
    print("  --- reset_daily_stats / reset_monthly_stats ---")
    count = await repo.reset_daily_stats()
    check("重置日统计 3 个渠道", count == 3)
    r = await repo.get_channel("WECHAT")
    check("日累计已重置", r["dailyAmount"] == 0.0 and r["dailyCount"] == 0)
    check("月累计未重置", r["monthlyAmount"] == 1500.0)

    count = await repo.reset_monthly_stats()
    check("重置月统计 3 个渠道", count == 3)
    r = await repo.get_channel("WECHAT")
    check("月累计已重置", r["monthlyAmount"] == 0.0)

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
