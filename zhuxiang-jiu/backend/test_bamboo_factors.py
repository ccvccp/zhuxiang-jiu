"""竹信分 C 端因子挂点 + 月度快照专项测试(竹信分决策会材料 §四/§六 实施)

运行:
    python test_bamboo_factors.py

覆盖:
    1. 因子机制(publish_member_credit_event): 正常记录/幂等(refId)/
       时间窗上限(C4 月限3 / C5 日限1)/best-effort 不抛错
    2. 真实业务挂点:
       C1+C5 order.review(订单完成+2 / 优质评价+1, 直造订单直调)
       C2    credit.repay 按时还款+3(逾期 -20 已在产不双记)
       C4    payment.audit_refund 退款通过 -2(真实支付单链路)
       C6    ticket 投诉结案 -10 / 非投诉不记
       C7    c2c 成交双方各+2(HTTP offline 全链)
    3. 月度快照: admin 批算全量/本人历史查询/同月重算覆盖/正负聚合
"""
import asyncio
import os
import sys
from datetime import date, timedelta

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

PASS = 0
FAIL = 0
RESULTS = []

ADMIN = {"X-Role": "admin"}
MEMBER_H = {"X-Member-Id": "99201"}

U1, U2 = 99201, 99202          # 因子机制/C 端全链会员
U3 = 99203                     # 快照/其他
PRODUCT = "ZX42-2026C04"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def _old_date(years_ago: int) -> str:
    return (date.today() - timedelta(days=365 * years_ago + 10)).isoformat()


async def _ensure_credit(uid: int) -> int:
    """开通信用户(member 初始 350)并返回当前分"""
    from services.credit_service import CreditService, ROLE_MEMBER
    acct = await CreditService().repo.get_or_create_score(uid, ROLE_MEMBER)
    return acct.get("bambooScore", 0)


def _score(client, uid):
    r = client.get(f"/api/credit/score/{uid}", headers=MEMBER_H)
    return ((r.json().get("data") or {}).get("bambooScore"))


def run(coro):
    """独立 loop 执行协程(带锁 service 直调专用)

    全局 _async_locks 缓存的 asyncio.Lock 绑定创建它的 loop,
    跨 loop 复用会挂起——每次执行前后清空锁缓存,
    保证带锁链路全部在本 loop 内闭环(HTTP 仅用于无锁端点)。
    """
    from core import locks as _locks
    _locks._async_locks.clear()
    try:
        return asyncio.run(coro)
    finally:
        _locks._async_locks.clear()


# ============================================================
# 前置构造
# ============================================================

async def _make_life(seller_id: int, batch: str, price: float = 1000.0,
                     years_ago: int = 4) -> str:
    from services.trace_service import TraceService
    trace = TraceService()
    res = await trace.generate_life_codes(PRODUCT, batch, 1)
    item = res["lifeCodes"][0]
    await trace.activate_life_code(item["lifeCode"], seller_id,
                                   purchase_price=price)
    await trace.repo.update_life_code(item["id"], {
        "firstActivationDate": _old_date(years_ago)})
    return item["lifeCode"]


async def _prep():
    for uid in (U1, U2, U3):
        await _ensure_credit(uid)
    return {
        "c2c": await _make_life(U1, "B-BM-01", 1000.0, 4),
    }


async def _member_events(uid, behavior=None):
    from services.role_service import RoleService
    return await RoleService().repo.list_events(
        user_id=uid, behavior_code=behavior, limit=200)


# ============================================================
# 测试主体
# ============================================================

def main():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    codes = run(_prep())

    # --------------------------------------------------------
    print("[01 因子机制 publish_member_credit_event]")
    from services.role_service import (
        RoleService,
        BEHAVIOR_ORDER_COMPLETED, BEHAVIOR_REFUND_USER_CLAIMED,
        BEHAVIOR_REVIEW_QUALITY,
    )
    svc = RoleService()

    async def _t1():
        return await svc.publish_member_credit_event(
            U1, BEHAVIOR_ORDER_COMPLETED, "test", ref_id="T-ORD-001")
    r1 = run(_t1())
    check("test_bm_01 正常记录(C1 +2, 350→352)",
          r1.get("recorded") is True and _score(client, U1) == 352,
          str((r1.get("recorded"), _score(client, U1))))

    r2 = run(_t1())
    check("test_bm_02 幂等(同 refId 跳过, 分数不变)",
          r2.get("recorded") is False and r2.get("reason") == "duplicate"
          and _score(client, U1) == 352,
          str((r2.get("recorded"), r2.get("reason"), _score(client, U1))))

    async def _t3():
        results = []
        for i in range(4):  # 月限 3: 第 4 单 skip
            results.append(await svc.publish_member_credit_event(
                U1, BEHAVIOR_REFUND_USER_CLAIMED, "test",
                ref_id=f"T-RF-{i:03d}"))
        return results
    rs = run(_t3())
    recorded = [x for x in rs if x.get("recorded")]
    skipped = [x for x in rs if not x.get("recorded")]
    check("test_bm_03 C4 月度上限3(4单仅记3, 第4单 limit_month)",
          len(recorded) == 3 and len(skipped) == 1
          and skipped[0].get("reason") == "limit_month"
          and _score(client, U1) == 352 - 6,
          str((len(recorded), len(skipped), _score(client, U1))))

    async def _t4():
        a = await svc.publish_member_credit_event(
            U1, BEHAVIOR_REVIEW_QUALITY, "test", ref_id="T-RV-A")
        b = await svc.publish_member_credit_event(
            U1, BEHAVIOR_REVIEW_QUALITY, "test", ref_id="T-RV-B")
        return a, b
    a, b = run(_t4())
    check("test_bm_04 C5 日限1(第二条 skip, 352-6+1=347)",
          a.get("recorded") is True and b.get("recorded") is False
          and b.get("reason") == "limit_day"
          and _score(client, U1) == 347,
          str((a.get("recorded"), b.get("recorded"), _score(client, U1))))

    async def _t5():
        return await svc.publish_member_credit_event(
            U1, "not_a_behavior", "test", ref_id="T-X")
    r5 = run(_t5())
    check("test_bm_05 非法行为码 best-effort 不抛(recorded=False)",
          r5.get("recorded") is False and "error" in r5.get("reason", ""),
          str(r5))

    # --------------------------------------------------------
    print("[02 C7 C2C 成交双方守约(offline 全链, 直调)]")
    async def _t7():
        from services.recycle_c2c_service import RecycleC2CService
        svc2 = RecycleC2CService()
        listing = await svc2.create_listing(U1, codes["c2c"], 1200)
        await svc2.buy_listing(listing["listingNo"], U2, "守约买家")
        return await svc2.confirm_sale(listing["listingNo"], U1)
    r7 = run(_t7())
    seller_events = run(_member_events(U1, "c2c_trade_settled"))
    buyer_events = run(_member_events(U2, "c2c_trade_settled"))
    check("test_bm_06 C7 双方各记一次(事件流水在位)",
          r7.get("status") == "sold" and len(seller_events) == 1
          and len(buyer_events) == 1,
          str((r7.get("status"), len(seller_events), len(buyer_events))))
    check("test_bm_07 C7 分值(卖家 347+2=349, 买家 350+2=352)",
          _score(client, U1) == 349 and _score(client, U2) == 352,
          str((_score(client, U1), _score(client, U2))))

    # --------------------------------------------------------
    print("[03 C2 paylater 按时还款 +3]")
    async def _t8():
        from services.credit_service import CreditService, LEVEL_L3
        cs = CreditService()
        # 先享后付须 L3(≥550 额度 2000); 等级升级有持续期要求,
        # 测试用 admin 强制升级(分设等级下限 550)
        await cs.upgrade_level(U2, LEVEL_L3, operator="admin",
                               reason="开通先享后付(测试)")
        order = await cs.create_paylater_order(U2, 50.0,
                                               order_no="BM-PAY-01")
        return order, await cs.repay_paylater_order(order["orderId"])
    order, repay = run(_t8())
    check("test_bm_08 C2 按时还款+3(550→553)",
          order.get("status") in ("active", "approved")
          and repay.get("success", True) is not False
          and _score(client, U2) == 553,
          str((order.get("status"), _score(client, U2))))

    # --------------------------------------------------------
    print("[04 C4 payment 退款审核 -2(真实支付单链路)]")
    async def _t9():
        from services.payment_service import PaymentService
        ps = PaymentService()
        pay = await ps.create_pay(U2, "BM-ORDER-01", "retail", 100.0,
                                  "wechat")
        await ps.start_pay(pay["payNo"])           # pending → paying
        await ps.pay_callback(
            f"BMCB{pay['payNo']}",
            {"status": "SUCCESS", "amount": 100.0}, pay["payNo"])
        refund = await ps.create_refund(pay["payNo"], 20.0, "测试退款")
        return await ps.audit_refund(refund["refundNo"], "approved")
    r9 = run(_t9())
    ev = run(_member_events(U2, "refund_user_claimed"))
    check("test_bm_09 C4 退款审核通过-2(553→551, refId=refundNo)",
          r9.get("success") is True and len(ev) == 1
          and _score(client, U2) == 551,
          str((r9.get("success"), len(ev), _score(client, U2))))

    # --------------------------------------------------------
    print("[05 C6 ticket 投诉结案 -10 / 非投诉不记]")
    async def _t10():
        from services.ticket_service import TicketService, \
            TICKET_TYPE_COMPLAINT
        from repositories.ticket_repository import TICKET_TYPE_PRESALE
        ts_ = TicketService()
        t1 = await ts_.create_ticket(U2, TICKET_TYPE_COMPLAINT, "high",
                                     "竹香酒有沉淀物, 要求处理")
        await ts_.resolve_ticket(t1["ticketNo"], 88, "已安排检测换货")
        await ts_.confirm_ticket(t1["ticketNo"], U2, 4)
        closed = await ts_.close_ticket(t1["ticketNo"])
        t2 = await ts_.create_ticket(U2, TICKET_TYPE_PRESALE, "low",
                                     "咨询整箱价格")
        await ts_.resolve_ticket(t2["ticketNo"], 88, "已报价")
        await ts_.confirm_ticket(t2["ticketNo"], U2, 5)
        await ts_.close_ticket(t2["ticketNo"])
        return closed
    closed = run(_t10())
    ev = run(_member_events(U2, "complaint_confirmed"))
    check("test_bm_10 C6 投诉结案-10 / 售前工单不记(551→541)",
          closed.get("status") == "closed" and len(ev) == 1
          and _score(client, U2) == 541,
          str((closed.get("status"), len(ev), _score(client, U2))))

    # --------------------------------------------------------
    print("[06 C1+C5 order.review 挂点(直造订单直调)]")
    async def _t11():
        from services.order_service import OrderService
        osvc = OrderService()
        oid = "BM-ORD-C1C5"
        await osvc.order_repo.save(oid, {
            "orderId": oid, "memberId": U3, "status": "RECEIVED",
            "items": [{"productId": PRODUCT, "quantity": 1}],
            "priceDetail": {"actualAmount": 199.0},
            "timeline": [], "logistics": {}, "createdAt": "2026-10-01",
        })
        return await osvc.review(oid, 5, "这款竹香酒入口绵甜回味悠长, 包装"
                                        "印刷很精美, 送长辈特别有面子, 全家"
                                        "都赞不绝口, 回购第三箱了!")
    r11 = run(_t11())
    ev1 = run(_member_events(U3, "order_completed"))
    ev5 = run(_member_events(U3, "review_quality"))
    check("test_bm_11 C1 订单完成+2 与 C5 优质评价+1(350→353)",
          r11.get("success") is True and len(ev1) == 1 and len(ev5) == 1
          and _score(client, U3) == 353,
          str((r11.get("success"), len(ev1), len(ev5),
               _score(client, U3))))

    # --------------------------------------------------------
    print("[07 月度快照]")
    # snapshot/run 受 CREDIT_MODE 决策面门控(off 时 409), 走 service 直调
    async def _t12():
        from services.credit_service import CreditService
        return await CreditService().run_monthly_snapshot_scan()
    d = run(_t12())
    check("test_bm_12 全量批算快照(3 账户 0 失败)",
          d.get("accounts") == 3 and d.get("snapshotted") == 3
          and d.get("failed") == [],
          str((d.get("accounts"), d.get("snapshotted"), d.get("failed"))))

    r = client.get(f"/api/credit/snapshots/{U2}", headers=MEMBER_H)
    snaps = (r.json().get("data") or [])
    s_u2 = next((s for s in snaps if s.get("userId") == U2), None)
    check("test_bm_13 本人快照在位(分值/等级/正负聚合)",
          r.status_code == 200 and s_u2 is not None
          and s_u2.get("bambooScore") == 541
          and s_u2.get("monthEventCount", 0) >= 5
          and s_u2.get("monthNegative", 0) <= -12,
          str(s_u2))

    async def _t14():
        from services.credit_service import CreditService
        await CreditService().adjust_score(U3, +100, "快照重算验证",
                                           operator="admin")
    run(_t14())
    run(_t12())
    r = client.get(f"/api/credit/snapshots/{U3}", headers=MEMBER_H)
    s_u3 = next((s for s in (r.json().get("data") or [])
                 if s.get("userId") == U3), None)
    check("test_bm_14 同月重算覆盖(353→453 后快照同步)",
          s_u3 is not None and s_u3.get("bambooScore") == 453,
          str(s_u3))

    r = client.get("/api/credit/snapshots/99299", headers=MEMBER_H)
    check("test_bm_15 无快照用户空列表",
          r.status_code == 200 and (r.json().get("data") or []) == [],
          str(r.json().get("data")))

    # --------------------------------------------------------
    print()
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("=" * 60)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

