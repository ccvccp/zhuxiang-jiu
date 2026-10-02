"""risk_assess 留痕查询验证(看板数据源)"""
import asyncio


async def t():
    from services.zw_fabric_service import _ZwStore
    rows = await _ZwStore().list("risk_assess")
    rows = sorted(rows, key=lambda r: r.get("assessedAt", ""),
                  reverse=True)
    print("风控留痕:", len(rows), "条")
    for r in rows[:3]:
        print(f"  #{r['riskId']} | {r['riskScore']}({r['riskLevel']})"
              f" | {str(r.get('assessedAt', ''))[:19]}")


asyncio.run(t())
