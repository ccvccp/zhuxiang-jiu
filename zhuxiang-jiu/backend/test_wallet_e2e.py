"""钱包盈利模块 端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 WalletService 方法, 模拟 22 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_wallet_e2e.py

覆盖 22 个接口对应的业务方法:
    1. 钱包账户(2):  open / get_info
    2. 充值提现(4):  deposit / withdraw / get_withdrawal / list_pending_withdrawals
    3. 提现审批(2):  approve_withdrawal / mark_withdrawal_paid
    4. 消费退款(3):  pay / refund / list_transactions
    5. 收益计算(3):  calc_daily_interest / settle_monthly_interest / interest_rules(常量)
    6. 定期管理(4):  transfer_to_regular / list_deposits / settle_deposit / early_settle_deposit
    7. 奖品管理(2):  list_rewards / claim_reward
    8. 奖品履约(2):  ship_reward / sign_reward
"""
import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.wallet_service import (
    WalletService,
    CURRENT_ANNUAL_RATE,
    DEPOSIT_TIERS,
    REWARD_TIERS,
    LPR_RATE,
    LPR_CEILING,
    WITHDRAW_AUTO_APPROVE_THRESHOLD,
    REBATE_RATE,
    REBATE_MAX_PER_ORDER,
    MIN_DEPOSIT,
    OPEN_MIN_GROWTH,
)
from repositories.wallet_repository import WalletRepository
from repositories.member_repository import MemberRepository
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
    # ============================================================
    # 初始化: 清理残留 + 准备测试会员
    # ============================================================
    for k in list(_mock_store.keys()):
        if k.startswith("wallet") or k.startswith("_wallet"):
            del _mock_store[k]

    # 准备一个 L2 会员(成长值 600, 满足开通条件)
    _mock_store["members"][9001] = {
        "id": 9001, "phone": "13800009001",
        "password": "x", "nickname": "端到端测试用户",
        "level": 2, "growth_value": 600, "points": 100,
        "status": 1, "reg_source": "phone",
        "created_at": "2026-08-21T00:00:00+00:00", "last_login_at": "",
    }
    # 准备一个成长值不足的会员
    _mock_store["members"][9002] = {
        "id": 9002, "phone": "13800009002",
        "password": "x", "nickname": "低等级用户",
        "level": 1, "growth_value": 100, "points": 50,
        "status": 1, "reg_source": "phone",
        "created_at": "2026-08-21T00:00:00+00:00", "last_login_at": "",
    }

    repo = WalletRepository()
    member_repo = MemberRepository()
    svc = WalletService(wallet_repo=repo, member_repo=member_repo)
    user_id = 9001

    # ============================================================
    # 1. 钱包账户(2 接口)
    # ============================================================
    print("\n========== 1. 钱包账户(2 接口) ==========")

    # 1.1 开通钱包 - 低等级会员应失败
    print("\n[Test 1.1] 开通钱包(低等级会员, 应 ValueError)")
    try:
        await svc.open(9002)
        check("低等级会员拒绝开通", False, "未抛 ValueError")
    except ValueError:
        check("低等级会员拒绝开通", True)

    # 1.2 开通钱包 - 正常
    print("[Test 1.2] 开通钱包(正常)")
    r = await svc.open(user_id)
    check("开通成功", r["success"] is True and r["status"] == "active")
    check("初始余额为 0", r["balance"] == 0)

    # 1.3 重复开通应失败
    print("[Test 1.3] 重复开通(应 ValueError)")
    try:
        await svc.open(user_id)
        check("重复开通拒绝", False, "未抛 ValueError")
    except ValueError:
        check("重复开通拒绝", True)

    # 1.4 钱包首页
    print("[Test 1.4] 钱包首页 info")
    r = await svc.get_info(user_id)
    check("info success", r["success"] is True)
    check("info statusName 正常", r["statusName"] == "正常")
    check("info currentBalance 0", r["currentBalance"] == 0)
    check("info claimableRewardCount 0", r["claimableRewardCount"] == 0)

    # ============================================================
    # 2. 充值提现(4 接口)
    # ============================================================
    print("\n========== 2. 充值提现(4 接口) ==========")

    # 2.1 充值 ¥5000
    print("[Test 2.1] 充值 ¥5000(支付宝)")
    r = await svc.deposit(user_id, 5000.0, "alipay")
    check("充值成功", r["success"] is True)
    check("充值后余额 5000", r["balanceAfter"] == 5000.0, f"actual={r.get('balanceAfter')}")
    check("充值交易编号", r["txNo"].startswith("WT"))

    # 2.2 充值低于 ¥100 应失败
    print("[Test 2.2] 充值低于 ¥100(应 ValueError)")
    try:
        await svc.deposit(user_id, 50.0)
        check("充值低于 100 拒绝", False)
    except ValueError:
        check("充值低于 100 拒绝", True)

    # 2.3 提现 ¥1000(自动通过)
    print("[Test 2.3] 提现 ¥1000(自动通过)")
    r = await svc.withdraw(user_id, 1000.0, "bank", "6222000112345678")
    check("提现成功", r["success"] is True)
    check("提现自动通过", r["autoApproved"] is True)
    check("提现单编号", r["withdrawNo"].startswith("WD"))
    wd_auto_no = r["withdrawNo"]

    # 2.4 提现 ¥8000(待审核) - 先补充充值确保余额充足
    print("[Test 2.4] 补充充值 ¥10000(为待审核提现)")
    await svc.deposit(user_id, 10000.0, "bank")
    print("[Test 2.4] 提现 ¥8000(待审核)")
    r = await svc.withdraw(user_id, 8000.0, "bank", "6222000112345678")
    check("提现待审核", r["success"] is True and r["status"] == "pending")
    wd_pending_no = r["withdrawNo"]

    # 2.5 提现单详情(通过 repository 查询)
    print("[Test 2.5] 提现单详情")
    wd = await repo.get_withdrawal(wd_pending_no)
    check("提现单查询", wd is not None and wd["amount"] == 8000.0)
    check("提现单 userId", wd["userId"] == user_id)

    # 2.6 待审核列表
    print("[Test 2.6] 待审核提现列表")
    r = await svc.list_pending_withdrawals()
    check("待审核列表", r["success"] is True and r["count"] >= 1)

    # ============================================================
    # 3. 提现审批(2 接口)
    # ============================================================
    print("\n========== 3. 提现审批(2 接口) ==========")

    # 3.1 审核拒绝 → 释放冻结
    print("[Test 3.1] 审核拒绝提现")
    bal_before = (await svc.get_info(user_id))["currentBalance"]
    r = await svc.approve_withdrawal(wd_pending_no, "rejected", "admin", "测试拒绝")
    check("审核拒绝", r["success"] is True and r["status"] == "rejected")
    bal_after = (await svc.get_info(user_id))["currentBalance"]
    check("拒绝后余额恢复", bal_after == bal_before + 8000.0,
          f"before={bal_before}, after={bal_after}")

    # 3.2 审核通过 → 打款
    print("[Test 3.2] 审核通过 + 打款")
    r = await svc.withdraw(user_id, 6000.0, "bank", "6222000112345678")
    wd_approve_no = r["withdrawNo"]
    r = await svc.approve_withdrawal(wd_approve_no, "approved", "admin", "测试通过")
    check("审核通过", r["success"] is True and r["status"] == "approved")
    r = await svc.mark_withdrawal_paid(wd_approve_no)
    check("打款完成", r["success"] is True and r["status"] == "paid")

    # 3.3 自动通过的提现也需打款
    print("[Test 3.3] 自动通过提现打款")
    r = await svc.mark_withdrawal_paid(wd_auto_no)
    check("自动通过打款", r["success"] is True and r["status"] == "paid")

    # ============================================================
    # 4. 消费退款(3 接口)
    # ============================================================
    print("\n========== 4. 消费退款(3 接口) ==========")

    # 4.1 消费 ¥1000(返利 1% = ¥10)
    print("[Test 4.1] 消费支付 ¥1000(返利 1%)")
    r = await svc.pay(user_id, 1000.0, "ORD-E2E-001")
    check("消费成功", r["success"] is True)
    check("消费返利 10", r["rebate"] == 10.0, f"actual={r.get('rebate')}")
    check("消费交易编号", r["txNo"].startswith("WT"))

    # 4.2 退款 ¥500
    print("[Test 4.2] 退款 ¥500")
    r = await svc.refund(user_id, 500.0, "ORD-E2E-001")
    check("退款成功", r["success"] is True and r["amount"] == 500.0)

    # 4.3 交易明细
    print("[Test 4.3] 交易明细")
    r = await svc.list_transactions(user_id)
    check("交易明细", r["success"] is True and r["count"] >= 4,
          f"actual count={r.get('count')}")
    # 按类型筛选
    r = await svc.list_transactions(user_id, tx_type="deposit")
    check("交易明细 type 筛选", r["count"] >= 1)

    # ============================================================
    # 5. 收益计算(3 接口)
    # ============================================================
    print("\n========== 5. 收益计算(3 接口) ==========")

    # 5.1 日补贴预估
    print("[Test 5.1] 日补贴预估")
    r = await svc.calc_daily_interest(user_id)
    check("日补贴成功", r["success"] is True)
    check("日补贴 > 0", r["dailyInterest"] > 0)
    bal = (await svc.get_info(user_id))["currentBalance"]
    expected_daily = round(bal * CURRENT_ANNUAL_RATE / 365, 2)
    check("日补贴公式", r["dailyInterest"] == expected_daily,
          f"actual={r.get('dailyInterest')}, expected={expected_daily}")

    # 5.2 月度结付(无待入账应 ValueError)
    print("[Test 5.2] 月度结付(无待入账, 应 ValueError)")
    try:
        await svc.settle_monthly_interest(user_id)
        check("无待入账拒绝", False)
    except ValueError:
        check("无待入账拒绝", True)

    # 5.3 收益规则(常量校验)
    print("[Test 5.3] 收益规则常量校验")
    check("活期年化 3%", CURRENT_ANNUAL_RATE == 0.03)
    check("LPR 上限 13.8%", LPR_CEILING == 0.138)
    check("LPR 3.45%", LPR_RATE == 0.0345)
    check("返利率 1%", REBATE_RATE == 0.01)
    check("返利上限 100", REBATE_MAX_PER_ORDER == 100.0)
    check("最低充值 100", MIN_DEPOSIT == 100.0)
    check("开通成长值 500", OPEN_MIN_GROWTH == 500)
    check("自动通过阈值 5000", WITHDRAW_AUTO_APPROVE_THRESHOLD == 5000.0)
    check("定期档位 4 种", len(DEPOSIT_TIERS) == 4)
    check("奖品档位 9 档", len(REWARD_TIERS) == 9)

    # ============================================================
    # 6. 定期管理(4 接口)
    # ============================================================
    print("\n========== 6. 定期管理(4 接口) ==========")

    # 先充值确保余额充足
    await svc.deposit(user_id, 10000.0, "bank")

    # 6.1 转定期 ¥5000/12 月
    print("[Test 6.1] 转定期 ¥5000/12 月")
    r = await svc.transfer_to_regular(user_id, 5000.0, 12)
    check("转定期成功", r["success"] is True)
    check("定期编号", r["depositNo"].startswith("DP"))
    check("12 月补贴 150", r["expectedInterest"] == 150.0,
          f"actual={r.get('expectedInterest')}")
    check("年化 3%", r["annualRate"] == 0.03)
    check("奖品匹配", "竹香经典" in r.get("rewardName", ""))
    check("LPR 合规", r["compliance"]["compliant"] is True)
    # 综合收益率 = (150 + 536) / 5000 = 13.72%
    expected_rate = (150 + 536) / 5000
    check("综合收益率 13.72%", abs(r["compliance"]["actualRate"] - expected_rate) < 0.001)
    dp_no = r["depositNo"]

    # 6.2 定期列表
    print("[Test 6.2] 定期列表")
    r = await svc.list_deposits(user_id)
    check("定期列表", r["success"] is True and r["count"] >= 1)

    # 6.3 定期未到期取出应失败
    print("[Test 6.3] 定期未到期取出(应 ValueError)")
    try:
        await svc.settle_deposit(user_id, dp_no)
        check("未到期拒绝", False)
    except ValueError:
        check("未到期拒绝", True)

    # 6.4 定期提前取出(1% 手续费)
    print("[Test 6.4] 定期提前取出(1% 手续费)")
    r = await svc.early_settle_deposit(user_id, dp_no)
    check("提前取出成功", r["success"] is True)
    check("手续费 50(5000×1%)", r["fee"] == 50.0, f"actual={r.get('fee')}")
    check("到账 4950", r["actualAmount"] == 4950.0, f"actual={r.get('actualAmount')}")
    check("损失补贴", r["lossInterest"] == 150.0)
    check("损失奖品", r["lossReward"] is True)

    # ============================================================
    # 7. 奖品管理(2 接口) + 定期到期产生奖品
    # ============================================================
    print("\n========== 7. 奖品管理(2 接口) ==========")

    # 7.1 转定期用于到期取出(产生奖品)
    print("[Test 7.1] 转定期 ¥5000/12 月(用于到期)")
    await svc.deposit(user_id, 5000.0, "bank")
    r = await svc.transfer_to_regular(user_id, 5000.0, 12)
    dp_no2 = r["depositNo"]

    # 修改到期日为昨天(模拟到期)
    from datetime import datetime, timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    await repo.update_deposit_fields(dp_no2, {"endDate": yesterday})

    # 7.2 定期到期取出(产生奖品)
    print("[Test 7.2] 定期到期取出(产生奖品)")
    r = await svc.settle_deposit(user_id, dp_no2)
    check("到期取出成功", r["success"] is True)
    check("本金 5000", r["amount"] == 5000.0)
    check("补贴 150", r["interest"] == 150.0)
    check("奖品编号", r["rewardNo"] != "" and r["rewardNo"].startswith("RW"))
    check("奖品名称", "竹香经典" in r["rewardName"])
    rw_no = r["rewardNo"]

    # 7.3 奖品列表
    print("[Test 7.3] 奖品列表")
    r = await svc.list_rewards(user_id)
    check("奖品列表", r["success"] is True and r["count"] >= 1)
    r = await svc.list_rewards(user_id, status="claimable")
    check("可领取奖品", r["count"] >= 1)

    # 7.4 领取奖品
    print("[Test 7.4] 领取奖品")
    r = await svc.claim_reward(user_id, rw_no, address_id=1)
    check("领取成功", r["success"] is True and r["status"] == "claimed")

    # ============================================================
    # 8. 奖品履约(2 接口)
    # ============================================================
    print("\n========== 8. 奖品履约(2 接口) ==========")

    # 8.1 奖品发货
    print("[Test 8.1] 奖品发货")
    r = await svc.ship_reward(rw_no, "SF1234567890")
    check("发货成功", r["success"] is True and r["status"] == "shipped")
    check("运单号", r["waybillNo"] == "SF1234567890")

    # 8.2 奖品签收
    print("[Test 8.2] 奖品签收")
    r = await svc.sign_reward(rw_no)
    check("签收成功", r["success"] is True and r["status"] == "signed")

    # ============================================================
    # 9. 边界与异常测试
    # ============================================================
    print("\n========== 9. 边界与异常测试 ==========")

    # 9.1 钱包未开通的用户
    print("[Test 9.1] 未开通钱包用户操作(应 KeyError)")
    try:
        await svc.get_info(8888)
        check("未开通 get_info 拒绝", False)
    except KeyError:
        check("未开通 get_info 拒绝", True)

    # 9.2 余额不足提现
    print("[Test 9.2] 余额不足提现(应 ValueError)")
    bal = (await svc.get_info(user_id))["currentBalance"]
    try:
        await svc.withdraw(user_id, bal + 99999, "bank", "6222000112345678")
        check("余额不足提现拒绝", False)
    except ValueError:
        check("余额不足提现拒绝", True)

    # 9.3 非法支付渠道
    print("[Test 9.3] 非法支付渠道(应 ValueError)")
    try:
        await svc.deposit(user_id, 500.0, "invalid_channel")
        check("非法渠道拒绝", False)
    except ValueError:
        check("非法渠道拒绝", True)

    # 9.4 非法定期存期
    print("[Test 9.4] 非法定期存期(应 ValueError)")
    try:
        await svc.transfer_to_regular(user_id, 5000.0, 8)
        check("非法定期存期拒绝", False)
    except ValueError:
        check("非法定期存期拒绝", True)

    # 9.5 重复审核
    print("[Test 9.5] 重复审核(应 ValueError)")
    try:
        await svc.approve_withdrawal(wd_approve_no, "approved", "admin", "重复")
        check("重复审核拒绝", False)
    except ValueError:
        check("重复审核拒绝", True)

    # 9.6 返利上限(单笔 ¥100)
    print("[Test 9.6] 返利上限 ¥100")
    await svc.deposit(user_id, 50000.0, "bank")
    r = await svc.pay(user_id, 50000.0, "ORD-E2E-002")
    check("返利上限 100", r["rebate"] == 100.0, f"actual={r.get('rebate')}")

    # 9.7 奖品过期
    print("[Test 9.7] 奖品过期(应 ValueError)")
    # 创建一个奖品并修改过期时间
    await svc.deposit(user_id, 5000.0, "bank")
    r = await svc.transfer_to_regular(user_id, 5000.0, 12)
    dp_no3 = r["depositNo"]
    yesterday_str = (datetime.now() - timedelta(days=1)).isoformat()
    await repo.update_deposit_fields(dp_no3, {"endDate": yesterday})
    r = await svc.settle_deposit(user_id, dp_no3)
    rw_no2 = r["rewardNo"]
    # 修改奖品过期时间为过去
    past_time = (datetime.now() - timedelta(days=1)).isoformat()
    await repo.update_reward_fields(rw_no2, {"claimDeadline": past_time})
    try:
        await svc.claim_reward(user_id, rw_no2)
        check("过期奖品拒绝", False)
    except ValueError:
        check("过期奖品拒绝", True)

    # ============================================================
    # 10. 最终状态校验
    # ============================================================
    print("\n========== 10. 最终状态校验 ==========")
    r = await svc.get_info(user_id)
    print(f"  最终余额: ¥{r['currentBalance']:.2f}")
    print(f"  累计充值: ¥{r['totalDeposit']:.2f}")
    print(f"  累计提现: ¥{r['totalWithdraw']:.2f}")
    print(f"  累计返利: ¥{r['totalRebate']:.2f}")
    print(f"  累计补贴: ¥{r['totalInterest']:.2f}")
    print(f"  累计奖品: ¥{r['totalReward']:.2f}")
    check("最终状态 success", r["success"] is True)
    check("累计返利 > 0", r["totalRebate"] > 0)
    check("累计充值 > 0", r["totalDeposit"] > 0)
    check("累计补贴 > 0", r["totalInterest"] > 0)
    check("累计奖品 > 0", r["totalReward"] > 0)

    # ============================================================
    # 汇总
    # ============================================================
    print("\n" + "=" * 60)
    print("  钱包盈利模块端到端测试报告")
    print("=" * 60)
    print(f"  通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
    print("=" * 60)

    if FAIL > 0:
        print("\n失败用例:")
        for name, detail in RESULTS:
            print(f"  - {name}: {detail}")
        return 1
    else:
        print("\n全部测试通过!")
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(run_e2e())
    sys.exit(exit_code)
