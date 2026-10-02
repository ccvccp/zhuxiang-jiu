"""智运看板对齐检查: 引擎修复+数据清理后的当前快照

    1. 承运商表现(样本骤降后的展示口径)
    2. zw 留痕表演示数据残留(claims/risk/decisions/eta)
    3. circuit 状态(看板未展示的引擎)
"""
import asyncio


async def t():
    from services.zw_route_service import ZwRouteService
    from services.zw_fabric_service import ZwFabricService, _ZwStore
    from services.zw_risk_service import ZwRiskService
    from services.zw_circuit_service import ZwCircuitService

    print("== ① 承运商表现(清理后) ==")
    cp = await ZwFabricService().carrier_performance()
    for c, p in cp.items():
        print(f"  {c}: total={p['total']} signed={p['signed']} "
              f"signRate={p['signRate']} avg={p['avgSignHours']}h")

    print("\n== ② zw 留痕表残留扫描 ==")
    store = _ZwStore()
    for table in ("claims", "risk_assess", "route_decisions",
                  "eta_records", "feedbacks", "strategy_suggestions"):
        rows = await store.list(table)
        demo = [r for r in rows
                if "演示" in str(r.get("description", ""))
                + str(r.get("note", ""))
                or "工作台演示" in str(r)]
        print(f"  {table}: {len(rows)} 条"
              + (f" (含演示 {len(demo)})" if demo else ""))

    claims = await ZwRiskService().claims(limit=10)
    for c in claims[:5]:
        print(f"    claim {c['claimNo']}: {c['claimTypeName']} | "
              f"{c['description'][:24]}")

    print("\n== ③ circuit 状态(看板缺区块) ==")
    st = await ZwCircuitService().circuit_status()
    for c in st.get("carriers", [])[:5]:
        print(f"  {c.get('carrierName')}: {c.get('state')}")


asyncio.run(t())
