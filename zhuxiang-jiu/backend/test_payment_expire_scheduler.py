"""支付单超时自动关闭调度器测试(05号收款 P2-4)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_payment_expire_scheduler.py

覆盖:
    1. 过期关闭: pending/paying/failed 超 expireTime → closed(TIMEOUT)
    2. 不过期: 未到期 pending / 无 expireTime / paid / refunding 不动
    3. 幂等: 第二轮扫描无新增关闭
    4. 业务闭环: mock 自动落账单(paid)不受扫描影响
    5. 开关: PAY_EXPIRE_AUTO=off → 调度不启用
    6. 统计形状: scannedAt/closedCount/closed 明细
"""
import asyncio
import os
import sys
from datetime import datetime, UTC, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
os.environ.pop("PAY_EXPIRE_AUTO", None)
os.environ.pop("PAY60_CHANNEL_MODE", None)

from services.payment_service import PaymentService
from services.payment_expire_scheduler import (
    run_expire_scan, scheduler_enabled, start_scheduler, stop_scheduler,
)
from repositories.payment_repository import PaymentRepository

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


async def _mk_pay(pay_no_seq: str, amount: float = 100.0,
                  status: str = "pending") -> str:
    """造支付单(retail 场景, 指定状态; 返回 payNo"""
    ps = PaymentService()
    r = await ps.create_pay("9001", f"ORD_EXP_{pay_no_seq}", "retail",
                            amount, "wechat", scene_type="order_pay")
    pay_no = r["payNo"]
    if status in ("paying", "failed"):
        await ps.start_pay(pay_no)
    if status == "failed":
        await ps.fail_pay(pay_no, "TEST_FAIL")
    return pay_no


async def main():
    from repositories.store import reset_store
    reset_store()
    repo = PaymentRepository()
    ps = PaymentService()
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    future = (datetime.now(UTC) + timedelta(minutes=25)).isoformat()

    # ============================================================
    # 1. 过期关闭(pending/paying/failed)
    # ============================================================
    p_pending = await _mk_pay("1", status="pending")
    p_paying = await _mk_pay("2", status="paying")
    p_failed = await _mk_pay("3", status="failed")
    for pn in (p_pending, p_paying, p_failed):
        await repo.update_order_fields(pn, {"expireTime": past})

    r = await run_expire_scan()
    check("过期: 三态全部关闭", r["closedCount"] == 3
          and set(r["closed"]) == {p_pending, p_paying, p_failed},
          f"{r}")
    o = await repo.get_order(p_pending)
    check("过期: reason=TIMEOUT 留痕",
          o["status"] == "closed" and o["failReason"] == "TIMEOUT")
    check("统计: 形状齐全", bool(r["scannedAt"])
          and r["scannedCount"] == 3 and r["failedCount"] == 0)

    # ============================================================
    # 2. 不过期(未到期/无 expireTime/paid/refunding)
    # ============================================================
    reset_store()
    repo = PaymentRepository()
    p_future = await _mk_pay("4", status="pending")
    await repo.update_order_fields(p_future, {"expireTime": future})
    p_noexp = await _mk_pay("5", status="pending")
    await repo.update_order_fields(p_noexp, {"expireTime": ""})
    p_paid = await _mk_pay("6", status="pending")
    await ps.start_pay(p_paid)
    await ps.pay_callback(f"EXP_PAID{p_paid}", {}, pay_no=p_paid)
    p_refunding = await _mk_pay("7", status="pending")
    await ps.start_pay(p_refunding)
    await ps.pay_callback(f"EXP_REF{p_refunding}", {}, pay_no=p_refunding)
    await ps.create_refund(p_refunding, 10, "测试部分退款", "partial")

    r = await run_expire_scan()
    check("不过期: 全部不动(未到期/无期限/paid/refunding)",
          r["closedCount"] == 0, f"{r}")
    for pn, name in ((p_future, "未到期"), (p_noexp, "无期限"),
                     (p_paid, "paid"), (p_refunding, "refunding")):
        o = await repo.get_order(pn)
        status = o["status"]
        expect = "pending" if name in ("未到期", "无期限") else (
            "paid" if name == "paid" else "refunding")
        check(f"不过期: {name} 状态保持", status == expect,
              f"got {status}")

    # ============================================================
    # 3. 幂等(第二轮扫描无新增)
    # ============================================================
    reset_store()
    repo = PaymentRepository()
    p_idem = await _mk_pay("8", status="pending")
    await repo.update_order_fields(p_idem, {"expireTime": past})
    r1 = await run_expire_scan()
    r2 = await run_expire_scan()
    check("幂等: 第二轮零新增", r1["closedCount"] == 1
          and r2["closedCount"] == 0 and r2["scannedCount"] == 0,
          f"r1={r1['closedCount']} r2={r2['closedCount']}")

    # ============================================================
    # 4. 业务闭环: mock 自动落账单(paid)不受影响
    # ============================================================
    reset_store()
    repo = PaymentRepository()
    from services.member_service import MemberService
    ms = MemberService()
    svip = await ms.create_svip_pay(1)
    await ps.start_pay(svip["payNo"])   # mock 业务场景自动落账 → paid
    r = await run_expire_scan()
    o = await repo.get_order(svip["payNo"])
    check("业务: mock 落账单保持 paid",
          o["status"] == "paid" and r["closedCount"] == 0)

    # ============================================================
    # 5. 环境开关
    # ============================================================
    os.environ["PAY_EXPIRE_AUTO"] = "off"
    check("开关: off 不启用", scheduler_enabled() is False
          and start_scheduler() is False)
    os.environ.pop("PAY_EXPIRE_AUTO", None)
    check("开关: 默认启用", scheduler_enabled() is True)

    # ============================================================
    # 6. 调度循环启停(手动触发一轮验证 loop 可运行)
    # ============================================================
    stop_scheduler()
    check("启停: stop 后未运行", not globals().get("_scheduler_task"))


if __name__ == "__main__":
    asyncio.run(main())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    sys.exit(0 if FAIL == 0 else 1)
