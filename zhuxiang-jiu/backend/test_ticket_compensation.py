"""P1-9 投诉三级补偿测试(Service 层 + HTTP 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_ticket_compensation.py

覆盖(设计文档 5.3.3 投诉处理流程三级补偿):
    1. 方案制定: minor→仅优惠券 / general→补发+优惠券 /
       severe→退款+escalated 主管介入
    2. 提案校验: 非投诉工单拒 / 分级非法拒 / 重复提案拒 /
       状态非法拒 / severe 无订单号拒
    3. 执行校验: 未确认执行拒 / 重复执行拒 / 无方案拒
    4. 执行分派: minor 发券(couponNo 落补偿券存储) /
       general 发券+补发单号 / severe 退款(收款链路全流程)
    5. HTTP 层: propose/execute 鉴权(无头 403/非 staff 403)/全链路
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.ticket_service import (
    TicketService,
    COMP_LEVEL_MINOR, COMP_LEVEL_GENERAL, COMP_LEVEL_SEVERE,
)
from repositories.store import _mock_store, reset_store
from repositories.backend import get_in_memory_store

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


USER = 701
HANDLER = 10


async def _new_complaint(svc, order_id=""):
    return await svc.create_ticket(
        USER, "complaint", "medium", "酒有破损, 要求处理",
        source="user", order_id=order_id, user_level=2)


async def _seed_paid_order(order_id: str, amount: float = 299.0):
    """注入一笔已支付订单(走收款链路真实建单+支付)"""
    from services.payment_service import PaymentService
    payment = PaymentService()
    pay = await payment.create_pay(
        USER, order_id, "retail", amount, "wechat", pay_method="native")
    await payment.start_pay(pay["payNo"])
    await payment.pay_callback(
        f"TEST{order_id}", {"code": 0}, pay_no=pay["payNo"])
    return pay


async def run_service():
    reset_store()
    get_in_memory_store().pop("ticket_compensation_coupons", None)
    svc = TicketService()

    # ============================================================
    # 1. 方案制定(三级)
    # ============================================================
    # minor: 仅优惠券
    t = await _new_complaint(svc)
    await svc.assign_ticket(t["ticketNo"], HANDLER, "客服A")
    r = await svc.propose_compensation(t["ticketNo"], COMP_LEVEL_MINOR, HANDLER)
    comp = r["compensation"]
    check("轻微: 仅优惠券方案",
          comp["planCoupon"] is True and comp["planReship"] is False
          and comp["planRefund"] is False)
    check("轻微: 不升级", r.get("escalated") is False)
    check("轻微: 默认券额 ¥10", abs(comp["couponAmount"] - 10.0) < 0.01)
    check("轻微: 提案后待确认", r["status"] == "wait_confirm")

    # general: 补发+优惠券
    t2 = await _new_complaint(svc)
    await svc.assign_ticket(t2["ticketNo"], HANDLER, "客服A")
    r = await svc.propose_compensation(t2["ticketNo"], COMP_LEVEL_GENERAL,
                                       HANDLER, coupon_amount=20.0)
    comp = r["compensation"]
    check("一般: 补发+优惠券",
          comp["planCoupon"] is True and comp["planReship"] is True
          and comp["planRefund"] is False)
    check("一般: 自定义券额 ¥20", abs(comp["couponAmount"] - 20.0) < 0.01)

    # severe: 退款+升级(需 orderId)
    t3 = await _new_complaint(svc, order_id="ORD-COMP-001")
    await _seed_paid_order("ORD-COMP-001")
    await svc.assign_ticket(t3["ticketNo"], HANDLER, "客服A")
    r = await svc.propose_compensation(t3["ticketNo"], COMP_LEVEL_SEVERE, HANDLER)
    comp = r["compensation"]
    check("严重: 退款+升级",
          comp["planRefund"] is True and r.get("escalated") is True)

    # ============================================================
    # 2. 提案校验
    # ============================================================
    # 非投诉工单
    t_presale = await svc.create_ticket(USER, "presale", "low", "咨询问题")
    await svc.assign_ticket(t_presale["ticketNo"], HANDLER, "客服A")
    try:
        await svc.propose_compensation(t_presale["ticketNo"],
                                       COMP_LEVEL_MINOR, HANDLER)
        check("校验: 非投诉工单拒绝", False)
    except ValueError as e:
        check("校验: 非投诉工单拒绝", "投诉" in str(e))

    # 分级非法
    t4 = await _new_complaint(svc)
    await svc.assign_ticket(t4["ticketNo"], HANDLER, "客服A")
    try:
        await svc.propose_compensation(t4["ticketNo"], "fatal", HANDLER)
        check("校验: 分级非法拒绝", False)
    except ValueError:
        check("校验: 分级非法拒绝", True)

    # 重复提案
    try:
        await svc.propose_compensation(t["ticketNo"], COMP_LEVEL_MINOR, HANDLER)
        check("校验: 重复提案拒绝", False)
    except ValueError as e:
        check("校验: 重复提案拒绝", "已制定" in str(e))

    # severe 无订单号
    t5 = await _new_complaint(svc)
    await svc.assign_ticket(t5["ticketNo"], HANDLER, "客服A")
    try:
        await svc.propose_compensation(t5["ticketNo"], COMP_LEVEL_SEVERE, HANDLER)
        check("校验: severe 无订单拒绝", False)
    except ValueError as e:
        check("校验: severe 无订单拒绝", "订单" in str(e))

    # 状态非法(pending 未分配)
    t6 = await _new_complaint(svc)
    try:
        await svc.propose_compensation(t6["ticketNo"], COMP_LEVEL_MINOR, HANDLER)
        check("校验: 未分配状态拒绝", False)
    except ValueError as e:
        check("校验: 未分配状态拒绝", "processing" in str(e))

    # ============================================================
    # 3. 执行校验
    # ============================================================
    # 未确认(wait_confirm)执行 → 拒
    try:
        await svc.execute_compensation(t["ticketNo"])
        check("校验: 未确认执行拒绝", False)
    except ValueError as e:
        check("校验: 未确认执行拒绝", "确认" in str(e))

    # 无方案
    try:
        await svc.execute_compensation(t_presale["ticketNo"])
        check("校验: 无方案拒绝", False)
    except ValueError as e:
        check("校验: 无方案拒绝", "无补偿方案" in str(e))

    # ============================================================
    # 4. 执行分派
    # ============================================================
    # minor: 用户确认 → 执行 → 发券
    await svc.confirm_ticket(t["ticketNo"], USER, 5)
    r = await svc.execute_compensation(t["ticketNo"])
    exec_info = r["compensationExecution"]
    check("执行: minor 发券", exec_info["couponNo"].startswith("CP"))
    check("执行: 方案状态 executed", r["compensation"]["status"] == "executed")

    # 补偿券落库
    coupons = get_in_memory_store().get("ticket_compensation_coupons", {})
    check("执行: 补偿券记录落库",
          exec_info["couponNo"] in coupons
          and abs(coupons[exec_info["couponNo"]]["amount"] - 10.0) < 0.01)

    # 重复执行 → 拒
    try:
        await svc.execute_compensation(t["ticketNo"])
        check("校验: 重复执行拒绝", False)
    except ValueError as e:
        check("校验: 重复执行拒绝", "非法" in str(e))

    # general: 发券 + 补发单号
    await svc.confirm_ticket(t2["ticketNo"], USER, 4)
    r = await svc.execute_compensation(t2["ticketNo"])
    exec_info = r["compensationExecution"]
    check("执行: general 发券+补发单",
          exec_info["couponNo"].startswith("CP")
          and exec_info["reshipOrderId"].startswith("RS"))

    # severe: 退款(收款链路)
    await svc.confirm_ticket(t3["ticketNo"], USER, 3)
    r = await svc.execute_compensation(t3["ticketNo"])
    exec_info = r["compensationExecution"]
    check("执行: severe 退款单号", bool(exec_info["refundNo"]))
    check("执行: severe 退款结果含金额",
          exec_info["refundResult"]
          and abs(exec_info["refundResult"]["refundAmount"] - 299.0) < 0.01,
          f"r={exec_info['refundResult']}")

    # 退款后工单补偿完整
    check("执行: severe 补偿闭环",
          r["compensation"]["status"] == "executed"
          and r["compensation"]["refundNo"] == exec_info["refundNo"])


def run_http():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    async def _prepare():
        reset_store()
        get_in_memory_store().pop("ticket_compensation_coupons", None)
        svc = TicketService()
        t = await _new_complaint(svc, order_id="ORD-COMP-H1")
        await _seed_paid_order("ORD-COMP-H1", 199.0)
        await svc.assign_ticket(t["ticketNo"], HANDLER, "客服A")
        return t["ticketNo"]

    ticket_no = asyncio.run(_prepare())

    # 无 staff 头 → 403
    r = client.post(f"/api/ticket/{ticket_no}/compensation/propose",
                    json={"level": "minor", "handlerId": HANDLER})
    check("HTTP 提案: 无头 403", r.status_code == 403, f"{r.status_code}")

    # member 角色 → 403
    r = client.post(f"/api/ticket/{ticket_no}/compensation/propose",
                    json={"level": "minor", "handlerId": HANDLER},
                    headers={"X-Role": "member"})
    check("HTTP 提案: member 403", r.status_code == 403, f"{r.status_code}")

    # cs_staff 提案
    r = client.post(f"/api/ticket/{ticket_no}/compensation/propose",
                    json={"level": "severe", "handlerId": HANDLER},
                    headers={"X-Role": "cs_staff"})
    check("HTTP 提案: severe 200",
          r.status_code == 200 and r.json()["data"]["compensation"]["planRefund"],
          f"{r.status_code} {r.text[:150]}")

    # 未确认执行 → 409
    r = client.post(f"/api/ticket/{ticket_no}/compensation/execute",
                    headers={"X-Role": "cs_staff"})
    check("HTTP 执行: 未确认 409", r.status_code == 409, f"{r.status_code}")

    # 用户确认 → 执行
    r = client.post(f"/api/ticket/{ticket_no}/confirm",
                    json={"satisfaction": 5},
                    headers={"X-Member-Id": str(USER)})
    check("HTTP 确认: 200", r.status_code == 200, f"{r.status_code}")

    r = client.post(f"/api/ticket/{ticket_no}/compensation/execute",
                    headers={"X-Role": "admin"})
    body = r.json()
    check("HTTP 执行: 200 退款+发券",
          r.status_code == 200
          and body["data"]["compensationExecution"]["refundNo"]
          and body["data"]["compensationExecution"]["couponNo"].startswith("CP"),
          f"{r.status_code} {r.text[:200]}")


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
