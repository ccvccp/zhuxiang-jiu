"""支付业务接线测试(钱包充值/SVIP 购买必须先支付后发放)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_pay_business_wiring.py

背景(资损级修复):
    旧链路 /api/wallet/deposit 直接 add_balance、/api/member/level/renew-svip
    直接升级 L5——未经任何支付即发放资金/权益。新链路铁律:
    业务端点只创建支付单 → 渠道支付 → pay_callback 成功 →
    _dispatch_business 分发(wallet_deposit→入账 / member_svip→renew_svip)。

覆盖:
    1. SVIP 购买链: 创建单(pending ¥99) → 未支付不开通 → start(mock 落账)
       → paid + action=purchased → L5 + 留痕(svipPayNo/成长值不虚标)
    2. SVIP 续费链: L5 → start → action=renewed
    3. 钱包充值链: 创建单 → 未支付不入账 → start → paid 入账(流水 orderId=payNo)
    4. 回调幂等: 重复回调不重复入账/不重复升级
    5. real 模式: start 不自动落账(paying), 显式回调后才发放
    6. 未接线场景: retail 单回调仅记账不分发(granted=False)
    7. HTTP 层: renew-svip / wallet deposit 均返回支付单(payNo)而非直接发放
"""
import asyncio
import os
import sys
from datetime import datetime, UTC

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.member_service import MemberService
from services.wallet_service import WalletService
from services.payment_service import PaymentService
from repositories.member_repository import MemberRepository
from repositories.store import reset_store

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


async def _mk_member(phone: str, growth: int = 0, level: int = 1) -> int:
    repo = MemberRepository()
    m = await repo.create({
        "phone": phone, "password": "x" * 6, "nickname": phone,
        "level": level, "growth_value": growth, "points": 0, "status": 1,
        "created_at": datetime.now(UTC).isoformat(),
    })
    return m["id"]


async def run_service():
    global PASS, FAIL
    reset_store()
    # 全程 mock 渠道(默认); real 分支单独 os.environ 切换
    os.environ.pop("PAY60_CHANNEL_MODE", None)

    ms, ws, ps = MemberService(), WalletService(), PaymentService()
    repo = MemberRepository()

    # ============================================================
    # 1. SVIP 购买链(L2 会员)
    # ============================================================
    mid = await _mk_member("13900000001", growth=600, level=2)  # L2 会员
    r = await ms.create_svip_pay(mid)
    check("SVIP: 创建支付单 pending ¥99",
          r["success"] and r["status"] == "pending"
          and r["actualAmount"] == 99.0 and r["sceneType"] == "member_svip",
          f"{r}")
    check("SVIP: 未支付不开通", (await ms.get_level(mid))["level"] == 2)

    s = await ps.start_pay(r["payNo"])
    d = s.get("dispatch") or {}
    check("SVIP: start(mock)即 paid+分发", s["status"] == "paid"
          and d.get("granted") is True)
    check("SVIP: action=purchased直升 L5",
          (d.get("business") or {}).get("action") == "purchased")
    m = await repo.get_by_id(mid)
    check("SVIP: 等级 L5+留痕",
          m.get("level") == 5 and bool(m.get("svipPurchasedAt"))
          and m.get("svipPurchasedFromLevel") == 2
          and m.get("svipPayNo") == r["payNo"], f"{m.get('svipPayNo')}")
    check("SVIP: 成长值不虚标(仍 600)", m.get("growth_value") == 600)

    # ============================================================
    # 2. SVIP 续费链(L5 → renewed)
    # ============================================================
    r2 = await ms.create_svip_pay(mid)
    s2 = await ps.start_pay(r2["payNo"])
    d2 = (s2.get("dispatch") or {}).get("business") or {}
    check("SVIP: L5 再付费 action=renewed", s2["status"] == "paid"
          and d2.get("action") == "renewed")

    # ============================================================
    # 3. 钱包充值链
    # ============================================================
    await ws.open(mid)
    dp = await ws.create_deposit_pay(mid, 500.0, "alipay")
    check("充值: 创建支付单 pending", dp["success"]
          and dp["status"] == "pending" and dp["actualAmount"] == 500.0
          and dp["sceneType"] == "wallet_deposit", f"{dp}")
    bal0 = (await ws.get_info(mid))["currentBalance"]
    check("充值: 未支付不入账", bal0 == 0.0, f"bal={bal0}")

    s3 = await ps.start_pay(dp["payNo"])
    bal1 = (await ws.get_info(mid))["currentBalance"]
    check("充值: start(mock)入账 500", s3["status"] == "paid"
          and (s3.get("dispatch") or {}).get("granted") is True
          and bal1 == 500.0, f"bal={bal1}")
    txs = await ws.list_transactions(mid, tx_type="deposit")
    check("充值: 流水 orderId 留痕支付单",
          txs["transactions"][0].get("orderId") == dp["payNo"],
          f"{txs['transactions'][0].get('orderId')}")

    # ============================================================
    # 4. 回调幂等(不重复入账/不重复升级)
    # ============================================================
    cb = await ps.pay_callback(f"MOCK{dp['payNo']}", {"dup": True},
                               pay_no=dp["payNo"])
    bal2 = (await ws.get_info(mid))["currentBalance"]
    check("充值: 重复回调不重复入账",
          cb.get("idempotent") is True and bal2 == 500.0, f"bal={bal2}")
    cb = await ps.pay_callback(f"MOCK{r['payNo']}", {"dup": True},
                               pay_no=r["payNo"])
    check("SVIP: 重复回调幂等", cb.get("idempotent") is True)

    # ============================================================
    # 5. real 模式(fail-hard: 不自动落账, 等真实渠道回调)
    # ============================================================
    os.environ["PAY60_CHANNEL_MODE"] = "real"
    dp2 = await ws.create_deposit_pay(mid, 300.0, "bank")
    s4 = await ps.start_pay(dp2["payNo"])
    bal3 = (await ws.get_info(mid))["currentBalance"]
    check("real: start 返回 paying 不入账",
          s4["status"] == "paying" and bal3 == 500.0,
          f"status={s4['status']} bal={bal3}")
    cb = await ps.pay_callback(f"REAL{dp2['payNo']}",
                               {"channel": "real_gateway"},
                               pay_no=dp2["payNo"])
    bal4 = (await ws.get_info(mid))["currentBalance"]
    check("real: 真实回调后入账 300",
          (cb.get("dispatch") or {}).get("granted") is True and bal4 == 800.0,
          f"bal={bal4}")
    os.environ.pop("PAY60_CHANNEL_MODE", None)

    # ============================================================
    # 6. 未接线场景(retail 仅记账不分发)
    # ============================================================
    pr = await ps.create_pay("9001", "ORD_WIRING_1", "retail",
                            200, "wechat", scene_type="order_pay")
    await ps.start_pay(pr["payNo"])
    # retail 非自动落账场景, 显式回调
    cb = await ps.pay_callback(f"ALI{pr['payNo']}", {}, pay_no=pr["payNo"])
    d6 = cb.get("dispatch") or {}
    check("未接线: retail 仅记账不分发",
          d6.get("granted") is False and "无业务分发" in (d6.get("msg") or ""),
          f"{d6}")


def run_http():
    global PASS, FAIL
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    M = {"X-Member-Id": "1"}

    # SVIP: HTTP 层只创建支付单
    r = client.post("/api/member/level/renew-svip", headers=M)
    body = r.json()
    check("HTTP SVIP: 返回支付单非直接开通",
          r.status_code == 200 and body.get("payNo")
          and body.get("status") == "pending"
          and "action" not in body, f"{r.status_code} {r.text[:120]}")
    r = client.get(f"/api/payment/{body['payNo']}", headers=M)
    check("HTTP SVIP: 支付单可查询",
          r.status_code == 200 and r.json().get("status") == "pending",
          f"{r.status_code}")
    r = client.post("/api/member/level/renew-svip")
    check("HTTP SVIP: 无头 401", r.status_code == 401)


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
