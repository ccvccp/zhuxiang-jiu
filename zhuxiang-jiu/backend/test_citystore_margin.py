"""县（区）网店保证金子系统测试(Service 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_citystore_margin.py

覆盖:
    1. 锁定: apply 预存扣款 1000 + locked 状态 + pending 期无起止
    2. 确认开业: startDate/endDate 补写(365 天)
    3. 驳回: 全额退还(rejected)
    4. 到期结算: 按年任务完成度(80% → 退 800 扣 200; ≥100% 全退)
    5. 中途取消: 按已运营期折算目标(干满进度全退/躺平按比例)
    6. 幂等: 重复结算不双退
    7. 实时年任务进度(completionRateLive)
"""
import asyncio
import os
import sys
from datetime import date, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from repositories.store import reset_store
from repositories.wallet_repository import WalletRepository
from repositories.citystore_repository import (
    CityStoreRepository, STORE_STATUS_CANCELLED)
from services.citystore_service import CityStoreService

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


async def _ensure_wallet(wallet, mid, balance=5000.0):
    if await wallet.get_account(mid) is None:
        await wallet.open_account(mid, {
            "userId": mid, "status": "active", "balance": 0.0})
    await wallet.add_balance(mid, balance)


async def _apply(svc, wallet, mid, district_code, name):
    await _ensure_wallet(wallet, mid)
    return await svc.apply(
        member_id=mid, member_level=5, store_name=name,
        district_code=district_code, id_name="测试店主",
        id_number="11010519491231002X", signature_confirm=True)


async def _add_orders(repo, sc, amount, count, created_at):
    """repo 直塞订单(自定义 createdAt 落结算期内)"""
    for i in range(count):
        await repo.add_order({
            "storeCode": sc, "orderNo": f"MO-{sc}-{i}", "productId": "P1",
            "productName": "竹香酒", "quantity": 1,
            "retailPrice": amount, "totalAmount": amount,
            "customerPhone": "", "deliveryCityCode": "",
            "salesChannel": 2, "createdAt": created_at,
        })


async def main() -> int:
    reset_store()
    svc = CityStoreService()
    repo = CityStoreRepository()
    wallet = WalletRepository()

    # ============ 1. 锁定与确认开业 ============
    r = await _apply(svc, wallet, 7101, "110105", "保证金主测店")
    sc = r["storeCode"]
    acc = await wallet.get_account(7101)
    check("锁定: 扣款 1000", abs(acc["balance"] - 4000.0) < 0.01,
          f"balance={acc['balance']}")
    m = await svc.get_margin(sc)
    check("锁定: locked 无起止", m["status"] == "locked"
          and m["startDate"] == "" and m["endDate"] == "")

    await svc.audit_store(sc, "admin", True)
    m = await svc.get_margin(sc)
    expect_end = (date.fromisoformat(m["startDate"])
                  + timedelta(days=365)).isoformat()
    check("开业: 起止补写 365 天", m["endDate"] == expect_end)

    # ============ 2. 实时进度 ============
    # 订单时间落在保证金期内(startDate=今天, endDate=明年)
    await _add_orders(repo, sc, 20000.0, 2, "2026-12-01T10:00:00")
    m = await svc.get_margin(sc)
    check("进度: 年进货 4 万", m.get("annualPurchasedLive") == 40000.0,
          str(m.get("annualPurchasedLive")))
    check("进度: 完成率 0.8", abs(m.get("completionRateLive", 0) - 0.8) < 0.001)

    # ============ 3. 到期结算 80% → 退 800 扣 200 ============
    m = await repo.get_margin_by_store(sc)
    m["startDate"], m["endDate"] = "2025-01-01", "2025-12-31"
    await repo.save_margin(m)
    await _add_orders(repo, sc, 20000.0, 2, "2025-06-15T10:00:00")
    settled = await svc.settle_margin(sc, "expired")
    check("到期 80%: 退 800", settled["refundAmount"] == 800.0
          and settled["completionRate"] == 0.8,
          str(settled.get("refundAmount")))
    acc = await wallet.get_account(7101)
    # 4000(剩) + 4000(订单外无操作) + 800(结算) = 4800
    check("到期 80%: 钱包 +800", abs(acc["balance"] - 4800.0) < 0.01,
          f"balance={acc['balance']}")
    again = await svc.settle_margin(sc, "expired")
    check("幂等: 重复结算不双退", again["refundAmount"] == 800.0)
    acc = await wallet.get_account(7101)
    check("幂等: 钱包不变", abs(acc["balance"] - 4800.0) < 0.01)

    # ============ 4. 到期 ≥100% 全退 ============
    r = await _apply(svc, wallet, 7102, "110106", "满额店")
    sc2 = r["storeCode"]
    await svc.audit_store(sc2, "admin", True)
    m = await repo.get_margin_by_store(sc2)
    m["startDate"], m["endDate"] = "2025-01-01", "2025-12-31"
    await repo.save_margin(m)
    await _add_orders(repo, sc2, 60000.0, 1, "2025-03-15T10:00:00")
    settled = await svc.settle_margin(sc2, "expired")
    check("满额: 完成率封顶 1.0 全退", settled["refundAmount"] == 1000.0
          and settled["completionRate"] == 1.0
          and settled["deductedAmount"] == 0.0,
          str(settled.get("refundAmount")))

    # ============ 5. 中途取消(按已运营期折算) ============
    # 5a. 干满进度: 开业 182 天完成 25000
    r = await _apply(svc, wallet, 7103, "110107", "中途干满店")
    sc3 = r["storeCode"]
    await svc.audit_store(sc3, "admin", True)
    m = await repo.get_margin_by_store(sc3)
    m["startDate"] = (date.today() - timedelta(days=182)).isoformat()
    m["endDate"] = (date.today() + timedelta(days=183)).isoformat()
    await repo.save_margin(m)
    await _add_orders(repo, sc3, 25000.0, 1, "2026-09-10T10:00:00")
    settled = await svc.settle_margin(sc3, "cancelled")
    # prorated = 50000×182/365 ≈ 24931.5 → 25000/24931.5 > 1 → 封顶全退
    check("中途: 干满进度全退", settled["refundAmount"] == 1000.0,
          f"refund={settled.get('refundAmount')} "
          f"rate={settled.get('completionRate')}")
    await svc.update_status(sc3, STORE_STATUS_CANCELLED, "admin")

    # 5b. 躺平取消: 开业 100 天完成 5000
    r = await _apply(svc, wallet, 7104, "110108", "中途躺平店")
    sc4 = r["storeCode"]
    await svc.audit_store(sc4, "admin", True)
    m = await repo.get_margin_by_store(sc4)
    m["startDate"] = (date.today() - timedelta(days=100)).isoformat()
    m["endDate"] = (date.today() + timedelta(days=265)).isoformat()
    await repo.save_margin(m)
    await _add_orders(repo, sc4, 5000.0, 1, "2026-09-10T10:00:00")
    # 走 update_status 取消路径联动结算
    await svc.update_status(sc4, STORE_STATUS_CANCELLED, "admin")
    m = await svc.get_margin(sc4)
    # prorated = 50000×100/365 ≈ 13698.6 → 5000/13698.6 ≈ 0.365 → 退 ≈365
    check("中途: 躺平按比例扣", m["status"] == "settled"
          and 350.0 < m["refundAmount"] < 380.0
          and m["deductedAmount"] > 600.0,
          f"refund={m.get('refundAmount')} rate={m.get('completionRate')}")

    # ============ 6. 驳回全额退 ============
    r = await _apply(svc, wallet, 7105, "110109", "驳回店")
    sc5 = r["storeCode"]
    acc_before = (await wallet.get_account(7105))["balance"]
    await svc.audit_store(sc5, "admin", False)
    acc_after = (await wallet.get_account(7105))["balance"]
    check("驳回: 全额退", abs(acc_after - acc_before - 1000.0) < 0.01,
          f"{acc_before} -> {acc_after}")
    m = await svc.get_margin(sc5)
    check("驳回: settled/rejected", m["status"] == "settled"
          and m["settleReason"] == "rejected"
          and m["refundAmount"] == 1000.0)

    # ============ 7. 结算原因非法 ============
    try:
        await svc.settle_margin(sc, "hack")
        check("原因非法拦截", False)
    except ValueError:
        check("原因非法拦截", True)

    # ============ 8. 到期提醒(30/7/1 三档梯度站内信) ============
    from repositories.message_repository import (
        MessageRepository, CHANNEL_INMAIL, CATEGORY_SYSTEM)

    msg_repo = MessageRepository()

    r = await _apply(svc, wallet, 7106, "110111", "提醒店")
    sc6 = r["storeCode"]
    await svc.audit_store(sc6, "admin", True)
    m = await repo.get_margin_by_store(sc6)
    # 剩 20 天 → 触发 30 档
    m["startDate"] = (date.today() - timedelta(days=345)).isoformat()
    m["endDate"] = (date.today() + timedelta(days=20)).isoformat()
    await repo.save_margin(m)
    await _add_orders(repo, sc6, 20000.0, 1, "2026-09-10T10:00:00")

    result = await svc.run_margin_reminder_round()
    s6 = [x for x in result["sent"] if x["storeCode"] == sc6]
    check("提醒: 30 档触发", len(s6) == 1 and s6[0]["step"] == 30,
          str(result.get("sent")))

    msgs = await msg_repo.list_messages(7106, channel=CHANNEL_INMAIL)
    rem = [x for x in msgs if "保证金到期提醒" in (x.get("title") or "")]
    check("提醒: 站内信落库", len(rem) == 1
          and "30 天后到期" in rem[0]["title"]
          and rem[0].get("category") == CATEGORY_SYSTEM,
          str(rem[:1]))
    check("提醒: 内容含进度与预估", "完成率 40.0%" in rem[0]["content"]
          and "退还 ¥400.00" in rem[0]["content"]
          and "扣除 ¥600.00" in rem[0]["content"],
          rem[0]["content"] if rem else "")
    m = await repo.get_margin_by_store(sc6)
    check("提醒: 档位标记", m.get("remindedSteps") == "30",
          str(m.get("remindedSteps")))

    # 幂等: 同档重跑不重发
    result = await svc.run_margin_reminder_round()
    s6 = [x for x in result["sent"] if x["storeCode"] == sc6]
    check("提醒: 同档幂等", len(s6) == 0, str(result.get("sent")))

    # 剩 5 天 → 7 档(30 已标)
    m["endDate"] = (date.today() + timedelta(days=5)).isoformat()
    await repo.save_margin(m)
    result = await svc.run_margin_reminder_round()
    s6 = [x for x in result["sent"] if x["storeCode"] == sc6]
    check("提醒: 降档 7 天触发", len(s6) == 1 and s6[0]["step"] == 7,
          str(s6))
    m = await repo.get_margin_by_store(sc6)
    check("提醒: 档位累计 7,30", m.get("remindedSteps") == "7,30",
          str(m.get("remindedSteps")))

    # 剩 1 天 → 1 档; settled 后不再提醒
    m["endDate"] = (date.today() + timedelta(days=1)).isoformat()
    await repo.save_margin(m)
    result = await svc.run_margin_reminder_round()
    s6 = [x for x in result["sent"] if x["storeCode"] == sc6]
    check("提醒: 1 天档触发", len(s6) == 1 and s6[0]["step"] == 1,
          str(s6))
    await svc.settle_margin(sc6, "expired")
    result = await svc.run_margin_reminder_round()
    s6 = [x for x in result["sent"] if x["storeCode"] == sc6]
    check("提醒: settled 跳过", len(s6) == 0, str(s6))

    # 未开业(pending 无起止)不提醒
    r = await _apply(svc, wallet, 7107, "110112", "未开业提醒店")
    sc7 = r["storeCode"]
    result = await svc.run_margin_reminder_round()
    s7 = [x for x in result["sent"] if x["storeCode"] == sc7]
    check("提醒: 未开业跳过", len(s7) == 0, str(s7))

    # ============ 9. 开业审计幂等(预置起止不被重审重置) ============
    r = await _apply(svc, wallet, 7108, "110113", "审计幂等店")
    sc8 = r["storeCode"]
    m = await repo.get_margin_by_store(sc8)
    # 外部预置起止(运营造数场景: 剩 20 天)
    m["startDate"] = (date.today() - timedelta(days=345)).isoformat()
    m["endDate"] = (date.today() + timedelta(days=20)).isoformat()
    await repo.save_margin(m)
    await svc.audit_store(sc8, "admin", True)
    m = await repo.get_margin_by_store(sc8)
    check("审计: 预置起止不被开业重置",
          m["startDate"] == (date.today() - timedelta(days=345)).isoformat()
          and m["endDate"] == (date.today() + timedelta(days=20)).isoformat(),
          f"{m.get('startDate')}~{m.get('endDate')}")

    # ============ 10. 保证金治理(管理端总览/清单/对账) ============
    # 10a. 总览: 统计 + 到期预警(sc8 剩 20 天) + 滞留(临时造过期 locked)
    ov = await svc.margin_admin_overview()
    check("治理: 总览统计", ov["total"] >= 8 and ov["locked"] >= 2
          and ov["settled"] >= 5, str({k: ov[k] for k in
                                       ("total", "locked", "settled")}))
    check("治理: 30 天内到期计数", ov["upcoming30"] >= 1,
          str(ov["upcoming30"]))
    m7 = await repo.get_margin_by_store(sc7)
    m7["startDate"] = (date.today() - timedelta(days=400)).isoformat()
    m7["endDate"] = (date.today() - timedelta(days=1)).isoformat()
    await repo.save_margin(m7)
    ov = await svc.margin_admin_overview()
    od7 = [x for x in ov["overdue"] if x["storeCode"] == sc7]
    check("治理: 滞留监控检出", len(od7) == 1 and od7[0]["daysOver"] == 1,
          str(ov["overdue"]))
    m7["startDate"], m7["endDate"] = "", ""  # 还原(未开业)
    await repo.save_margin(m7)

    # 10b. 清单: locked 含剩余天数与实时进度; days 筛选
    lst = await svc.margin_admin_list(status="locked")
    l8 = [x for x in lst["margins"] if x["storeCode"] == sc8]
    check("治理: locked 清单字段", l8 and l8[0]["daysLeft"] == 20
          and l8[0].get("completionRateLive") == 0.0,
          str(l8[:1]))
    lst = await svc.margin_admin_list(status="locked", days=10)
    check("治理: days 筛选排除", all(x.get("daysLeft") is None
                                    or x["daysLeft"] > 10
                                    for x in lst["margins"]),
          str([x.get("daysLeft") for x in lst["margins"]]))

    # 10c. 对账: 恒等式平衡 → 脏数据失配 → 还原复平
    rec = await svc.margin_reconciliation()
    check("治理: 对账恒等式平衡", rec["ok"] is True
          and rec["internal"]["ok"] and rec["walletTx"]["ok"], str(rec))
    m = await repo.get_margin_by_store(sc8)
    m["amount"] = 2000.0  # 模拟脏数据(锁定流水仍 1000)
    await repo.save_margin(m)
    rec = await svc.margin_reconciliation()
    check("治理: 钱包失配检出", rec["ok"] is False
          and rec["walletTx"]["ok"] is False, str(rec["walletTx"]))
    m["amount"] = 1000.0
    await repo.save_margin(m)
    rec = await svc.margin_reconciliation()
    check("治理: 还原复平", rec["ok"] is True, str(rec["ok"]))

    print("=" * 60)
    print("县（区）网店保证金子系统测试".center(50))
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"TOTAL: {PASS} pass, {FAIL} fail / {PASS + FAIL}")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
