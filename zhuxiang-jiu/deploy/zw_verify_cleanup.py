"""清理引擎验证测试单(两轮验证残留, 非真实业务)

    - SF-ETA-TEST-0001 (pending, 导致 SF 揽收率 67% 熔断误报 open)
    - SF10261003ZY0001 (签收闭环验证单)
清理后 circuit 状态应恢复 closed/unknown(不再误报)。
"""
import asyncio
import json

TARGETS = ["SF-ETA-TEST-0001", "SF10261003ZY0001"]


async def t():
    from repositories.logistics_repository import LogisticsRepository
    from repositories.backend import (
        is_redis_mode, get_redis_client, _k)
    repo = LogisticsRepository()

    backup = []
    for wb in TARGETS:
        o = await repo.get_order(wb)
        if o:
            backup.append({"order": o,
                           "tracks": await repo.list_tracks(wb, 50)})
    with open("/tmp/zw_verify_backup.json", "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=1)
    print("备份:", len(backup), "单 → /tmp/zw_verify_backup.json")

    if not is_redis_mode():
        print("非 Redis, 退出"); return
    client = await get_redis_client()
    for entry in backup:
        o = entry["order"]
        wb = o["waybillNo"]
        if o.get("status"):
            await client.srem(_k("logistics:order:index:status",
                                 o["status"]), wb)
        if o.get("orderId"):
            await client.hdel(_k("logistics:order:index:orderId"),
                              o["orderId"])
        await client.delete(_k("logistics:order", wb))
        await client.delete(_k("logistics:track", wb))
    print("已删除:", len(backup), "单")

    left = await repo.list_orders(limit=None)
    print("\n剩余:", len(left), "单:",
          [o["waybillNo"][:20] for o in left])

    from services.zw_circuit_service import ZwCircuitService
    status = await ZwCircuitService().circuit_status()
    print("\ncircuit 复验:")
    for c in status.get("carriers", []):
        print(f"  {c.get('carrierName')}: state={c.get('state')}"
              f" | pickupRate={c.get('pickupRate')}"
              f" | sample={c.get('sample')}")

    from services.zw_route_service import ZwRouteService
    for c, s in (await ZwRouteService().carrier_scores()).items():
        print(f"  评分 {c}: {s['score']}")


asyncio.run(t())
