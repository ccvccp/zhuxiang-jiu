"""智单·AI智能订单大模型 升级用例(服务层直调——绕开 auth 中间件
X-Role 直连 403 的既有限制, 与 test_zw_mode 同类 HTTP 问题的规避)

覆盖:
    [三态灰度 zy 同款]
    1. 默认 off / 决策面 409 口径
    2. shadow 进化冻结(反馈只留痕, etaRecentWeight 不动)
    3. assist 进化恢复
    [扫描调度器]
    4. run_scan 汇总(体检等级/异常数/检测器)+各自留痕
    [业务挂接]
    5. 退款申请自动附风险建议书(riskAssist 返回+随单持久化)

运行: $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; python test_zhidan_upgrade.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repositories.store import _mock_store  # noqa: E402

PASS = 0
FAIL = 0
RESULTS = []


def check(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def _seed_orders():
    """最小种子: 3 单(1 COMPLETED 供退款挂接 + 2 PAID 供体检/ETA)"""
    def item(pid, qty, price):
        return {"productId": pid, "productName": f"P{pid}",
                "quantity": qty, "unitPrice": price,
                "subtotal": round(qty * price, 2)}

    def order(oid, status, created, paid=""):
        return {
            "orderId": oid, "memberId": 7, "orderType": "RT",
            "status": status, "items": [item(1, 1, 268)],
            "priceDetail": {"goodsTotal": 268, "actualAmount": 268},
            "address": {}, "remark": "",
            "logistics": {"carrier": "SF" if paid else "",
                          "waybillNo": "", "shippedAt": paid,
                          "signedAt": paid},
            "payment": {"method": "wechat" if paid else "",
                        "tradeNo": "", "paidAt": paid},
            "review": {"rating": 0, "content": "", "reviewedAt": ""},
            "refund": {"reason": "", "refundedAt": "",
                       "refundedAmount": 0, "audit": {}},
            "usedPoints": 0, "consumedPoints": 0,
            "timeline": [], "createdAt": created, "updatedAt": created,
        }

    day = "2026-10-01"
    orders = [
        order("RT-U-01", "COMPLETED", f"{day}T08:00:00+00:00",
              paid=f"{day}T08:05:00+00:00"),
        order("RT-U-02", "PAID", f"{day}T10:00:00+00:00",
              paid=f"{day}T10:01:00+00:00"),
        order("RT-U-03", "PAID", f"{day}T12:00:00+00:00",
              paid=f"{day}T12:01:00+00:00"),
    ]
    _mock_store["orders_v2"] = {o["orderId"]: o for o in orders}
    _mock_store["orders"] = list(orders)


async def main():
    from services.zd_mode_service import (
        current_mode, require_decision_mode,
    )
    from services.zd_evolution_service import ZdEvolutionService
    from services.zd_scan_scheduler import run_scan
    from services.order_service import OrderService

    # ---- 三态灰度 ----
    os.environ.pop("ZD_MODE", None)
    m = await current_mode()
    check("模式-默认off", m["mode"] == "off", str(m))
    try:
        await require_decision_mode()
        check("模式-off决策面409", False, "未抛出")
    except ValueError:
        check("模式-off决策面409", True)

    _seed_orders()
    evo = ZdEvolutionService()

    os.environ["ZD_MODE"] = "shadow"
    w_before = (await evo.get_params())["etaRecentWeight"]
    rec = await evo.feedback("eta_forecast", "adopted", "观测期")
    w_after = (await evo.get_params())["etaRecentWeight"]
    check("模式-shadow进化冻结",
          rec.get("evolved") is False and w_after == w_before
          and "冻结" in str(rec.get("note", "")), str(rec)[:90])

    os.environ["ZD_MODE"] = "assist"
    rec = await evo.feedback("eta_forecast", "adopted", "生产")
    check("模式-assist进化恢复",
          rec.get("etaRecentWeightAfter") is not None, str(rec)[:90])

    # ---- 扫描调度器 ----
    scan = await run_scan()
    check("调度-扫描汇总",
          scan.get("success") is True and "checkupGrade" in scan
          and "anomalyOrders" in scan and "scannedAt" in scan,
          str(scan)[:90])
    from services.zd_insight_service import ZdInsightService
    from services.zd_risk_service import ZdRiskService
    checkups = await ZdInsightService().checkups(limit=5)
    anomalies = await ZdRiskService().anomalies(limit=5)
    check("调度-体检留痕(checkups)", len(checkups) >= 1,
          str(len(checkups)))
    check("调度-异常扫描留痕(anomalies)", isinstance(anomalies, list),
          str(type(anomalies)))

    # ---- 业务挂接: 退款申请自动附风险建议书 ----
    svc = OrderService()
    r = await svc.apply_return("RT-U-01", "不想要了")
    check("挂接-退款返回风险建议书",
          r.get("riskAssist") is not None
          and "score" in r["riskAssist"]
          and "suggestion" in r["riskAssist"],
          str(r.get("riskAssist"))[:90])
    check("挂接-建议书永不自动口径",
          "永不自动" in str(r.get("riskAssist", {}).get("note", "")),
          str(r.get("riskAssist", {}).get("note", "")))
    order = await svc.get_by_id("RT-U-01")
    persisted = (order.get("order", {}).get("refund", {})
                 .get("riskAssist"))
    check("挂接-建议书随单持久化",
          persisted is not None and "score" in persisted,
          str(persisted)[:80])

    os.environ.pop("ZD_MODE", None)
    print(os.linesep.join(RESULTS))
    print("-" * 58)
    print(f"通过: {PASS} / {PASS + FAIL}")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
