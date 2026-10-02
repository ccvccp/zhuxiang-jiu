"""查一笔真实订单的 address 字段结构(发货流接入参数映射用)"""
import asyncio
import json


async def t():
    from repositories.order_repository import OrderRepository
    repo = OrderRepository()
    orders = await repo.list_all() if hasattr(repo, "list_all") else []
    if not orders and hasattr(repo, "list_orders"):
        orders = await repo.list_orders()
    for o in (orders or [])[:2]:
        print("orderId:", o.get("orderId", o.get("id")),
              "| status:", o.get("status"))
        print("  address:", json.dumps(
            o.get("address", {}), ensure_ascii=False)[:220])
        print("  logistics:", json.dumps(
            o.get("logistics", {}), ensure_ascii=False)[:150])

    from services.logistics_service import SUPPORTED_CARRIERS
    print("\n承运商白名单:", SUPPORTED_CARRIERS)


asyncio.run(t())
