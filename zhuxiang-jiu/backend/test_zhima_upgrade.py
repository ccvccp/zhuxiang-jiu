"""智码·AI智能二维码大模型 升级验证(四件套, 2026-10-03)

覆盖(AUTH-TEST-01 后新范式——服务层直调 + 内存模式):
    1. 三态: off 决策拒绝 / shadow 生成只留痕(dryRun 无实例)
    2. shadow 核销只留痕(实例状态不变更)
    3. assist 真生成(55号签名)+once 核销+重放拒绝
    4. override: 非法值拒绝
    5. 调度器: run_scan 留痕 daily_scan 事件
    6. 挂接: ship 附发货交接码建议书(assist 真码 / off 不附)
    7. fail-soft: 智码异常不阻断发货主流程

运行: python test_zhima_upgrade.py
"""
import test_support  # noqa: F401 (直跑自举: 内存模式; 首行约定)

import asyncio
import os
import sys

os.environ["QR70_MODE"] = "assist"   # 基线 assist(分段切档)

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


def set_mode(m):
    os.environ["QR70_MODE"] = m


def seed_paid_order(order_id):
    """内存模式造 PAID 订单(ship 挂接靶, orders_v2 dict)"""
    from repositories.store import _mock_store
    _mock_store.setdefault("orders_v2", {})
    _mock_store.setdefault("orders", [])
    order = {
        "orderId": order_id, "memberId": 1,
        "status": "PAID",
        "items": [{"productId": "ZX42-2026L07",
                   "productName": "竹奕·竹香型 42° 500ml",
                   "quantity": 2, "price": 268.0}],
        "address": {"province": "山东省", "city": "济南市",
                    "detail": "历下区"},
        "timeline": [], "logistics": {},
        "amount": 536.0, "createdAt": "2026-10-03T00:00:00Z",
    }
    _mock_store["orders_v2"][order_id] = order
    _mock_store["orders"].append(order)
    return order


def main():
    from services.qr70_mode_service import (
        current_mode, require_decision_mode, set_override)
    from services.qr70_hub_service import Qr70HubService
    from services.qr70_shipping_service import Qr70ShippingService
    hub = Qr70HubService()

    async def phase_mode():
        # --- 1. off 决策拒绝 ---
        set_mode("off")
        m = await current_mode()
        check("三态-off 读取", m["mode"] == "off"
              and m["source"] == "env", str(m))
        try:
            await require_decision_mode()
            check("三态-off 决策面拒绝(ValueError)", False)
        except ValueError:
            check("三态-off 决策面拒绝(ValueError)", True)

        # --- 2. shadow 生成只留痕 ---
        set_mode("shadow")
        r = await hub.generate(1, "manage-workbench",
                               {"station": "A-01"}, "admin_console")
        check("shadow-生成 dryRun 留痕",
              r.get("dryRun") is True and r.get("mode") == "shadow"
              and r.get("status") == "shadow", str(r)[:120])
        evs = await _events(hub)
        check("shadow-生成事件 code_shadow_generated",
              any(e.get("type") == "code_shadow_generated"
                  for e in evs))
        check("shadow-不产生真实码实例(无 code/nonce)",
              not r.get("code") and not r.get("nonce"))

        # --- 3. assist 真生成 + once 核销 + 重放 ---
        set_mode("assist")
        g = await hub.generate(1, "shipping-handover",
                               {"waveNo": "W-1", "carrier": "SF",
                                "orderId": "RT-T1"}, "warehouse")
        check("assist-真生成(55号签名)",
              bool(g.get("code")) and bool(g.get("nonce"))
              and g.get("status") == "generated"
              and g.get("mode") == "assist", str(g)[:120])
        rd = await hub.redeem(g["code"], operator_id=9)
        check("assist-once 核销", rd.get("redeemed") is True,
              str(rd)[:120])
        rd2 = await hub.redeem(g["code"], operator_id=9)
        check("assist-重放拒绝", rd2.get("redeemed") is False
              and rd2.get("verifyStatus") == "replayed",
              str(rd2)[:120])

        # --- 4. shadow 核销只留痕(实例状态不变更) ---
        g2 = await hub.generate(1, "shipping-handover",
                                {"waveNo": "W-2", "carrier": "YT",
                                 "orderId": "RT-T2"}, "warehouse")
        set_mode("shadow")
        rd3 = await hub.redeem(g2["code"], operator_id=9)
        check("shadow-核销 dryRun 留痕",
              rd3.get("dryRun") is True
              and rd3.get("redeemed") is False, str(rd3)[:120])
        detail = await hub.code_detail_by_nonce(g2["nonce"])
        check("shadow-核销不变更实例状态",
              detail.get("status") == "generated", str(detail)[:120])
        set_mode("assist")

        # --- 5. override 非法拒绝 ---
        try:
            await set_override("bogus")
            check("override-非法值拒绝(ValueError)", False)
        except ValueError:
            check("override-非法值拒绝(ValueError)", True)
        r = await set_override("")     # 合法调用不炸
        check("override-清除回落不炸", isinstance(r, dict)
              and "mode" in r, str(r)[:80])

    async def phase_scan():
        # --- 6. 调度器 run_scan 留痕 ---
        from services.qr70_scan_scheduler import run_scan
        summary = await run_scan()
        evs = await _events(hub)
        has_scan = any(e.get("type") == "daily_scan"
                       for e in evs)
        check("调度器-run_scan 留痕 daily_scan",
              has_scan and "codeSnapshot" in summary
              and "scannedAt" in summary, str(summary)[:120])

    async def phase_hook():
        # --- 7. 挂接: ship 附发货交接码建议书 ---
        from services.order_service import OrderService
        svc = OrderService()
        oid = "RT-ZM-001"
        seed_paid_order(oid)
        r = await svc.ship(oid, "顺丰速运", "SF1234567890")
        assist = r.get("shipAssist") or {}
        check("挂接-ship 附发货交接码建议书",
              r.get("success") is True
              and assist.get("nonce") and assist.get("code")
              and assist.get("dryRun") is False,
              str(assist)[:150])

        # --- 8. off 档不附 ---
        oid2 = "RT-ZM-002"
        seed_paid_order(oid2)
        set_mode("off")
        r2 = await svc.ship(oid2, "圆通", "YT9876543210")
        check("挂接-off 不附建议书",
              r2.get("success") is True
              and r2.get("shipAssist") is None,
              str(r2.get("shipAssist"))[:80])
        set_mode("assist")

        # --- 9. fail-soft: 智码异常不阻断发货 ---
        import services.qr70_shipping_service as sp
        _orig = sp.Qr70ShippingService.issue

        async def _boom(self, **kw):
            raise RuntimeError("智码侧模拟故障")
        sp.Qr70ShippingService.issue = _boom
        try:
            oid3 = "RT-ZM-003"
            seed_paid_order(oid3)
            r3 = await svc.ship(oid3, "韵达", "YD1112223334")
            check("fail-soft-智码故障不阻断发货",
                  r3.get("success") is True
                  and r3.get("status") == "SHIPPED"
                  and "跳过" in str(
                      (r3.get("shipAssist") or {}).get("note", "")),
                  str(r3.get("shipAssist"))[:120])
        finally:
            sp.Qr70ShippingService.issue = _orig

        # --- 10. shipping_service 直发(issue 版式建议) ---
        s = await Qr70ShippingService().issue(
            wave_no="W-ZM-9", order_id="RT-ZM-009",
            sku_names=["竹奕·竹香型 42° 500ml"],
            destination_province="新疆维吾尔自治区",
            carrier="顺丰速运")
        check("发货码-远途版式建议",
              bool(s.get("code")) and isinstance(
                  s.get("layoutBadges"), list),
              str(s)[:150])

    async def _events(hub):
        v = await hub.events_view(limit=50)
        return (v.get("events") or v
                if isinstance(v, dict) else v) or []

    asyncio.run(phase_mode())
    asyncio.run(phase_scan())
    asyncio.run(phase_hook())

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
