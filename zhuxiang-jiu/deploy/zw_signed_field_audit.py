"""引擎逻辑断层实证: 签收单的时效字段(signedTime vs signedAt)"""
import asyncio


async def t():
    from repositories.logistics_repository import LogisticsRepository
    repo = LogisticsRepository()
    orders = await repo.list_orders(limit=50)
    signed = [o for o in orders if o.get("status") == "signed"]
    print("签收单:", len(signed), "/", len(orders))
    for o in signed[:8]:
        print(f"  {o.get('waybillNo', '')[:26]} | signedTime="
              f"{str(o.get('signedTime', ''))[:19]} | signedAt="
              f"{o.get('signedAt', '')!r}")

    # 织物时效样本数
    from services.zw_fabric_service import ZwFabricService
    lake = await ZwFabricService().lake_overview()
    print("\n织物时效样本:", lake["timeliness"],
          "| 订单域:", lake["orders"])


asyncio.run(t())
