"""清理 ZW-DEMO 演示物流单(先备份留档, 用户已确认)

步骤:
    1. 全量找 ZW-DEMO* 运单 + 轨迹 → 备份 /tmp/zw_demo_backup.json
    2. 删除: 订单 hash / status 索引 / orderId 索引 / 轨迹 list
    3. 验证: 全量无演示单, 织物聚合与承运商评分(冷启动回退)
"""
import asyncio
import json


async def t():
    from repositories.logistics_repository import LogisticsRepository
    from repositories.backend import (
        is_redis_mode, get_redis_client, _k)
    repo = LogisticsRepository()

    orders = await repo.list_orders(limit=None)
    demos = [o for o in orders
             if str(o.get("waybillNo", "")).startswith("ZW-DEMO")]
    print("待清理演示单:", len(demos))

    # 1. 备份(订单+轨迹)
    backup = []
    for o in demos:
        wb = o["waybillNo"]
        backup.append({"order": o,
                       "tracks": await repo.list_tracks(wb, limit=50)})
    with open("/tmp/zw_demo_backup.json", "w",
              encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=1)
    print("备份:", len(backup), "单 → /tmp/zw_demo_backup.json")

    # 2. 删除(Redis 生产模式)
    if not is_redis_mode():
        print("非 Redis 模式, 退出(生产为 Redis)")
        return
    client = await get_redis_client()
    removed = 0
    for o in demos:
        wb = o["waybillNo"]
        status = o.get("status", "")
        # status 索引
        if status:
            await client.srem(_k("logistics:order:index:status",
                                 status), wb)
        # orderId 索引
        if o.get("orderId"):
            await client.hdel(_k("logistics:order:index:orderId"),
                              o["orderId"])
        # 订单 hash + 轨迹 list
        await client.delete(_k("logistics:order", wb))
        await client.delete(_k("logistics:track", wb))
        removed += 1
    print("已删除:", removed, "单(含轨迹)")

    # 3. 验证
    left = await repo.list_orders(limit=None)
    print("\n剩余物流单:", len(left), "| 仍有演示单:",
          any(str(o.get("waybillNo", "")).startswith("ZW-DEMO")
              for o in left))

    from services.zw_fabric_service import ZwFabricService
    lake = await ZwFabricService().lake_overview()
    print("织物聚合:", lake["orders"], "|", lake["timeliness"])

    from services.zw_route_service import ZwRouteService
    scores = await ZwRouteService().carrier_scores()
    for c, s in scores.items():
        print(f"  {c}: {s['score']} 分({s['explain'][:38]})")


asyncio.run(t())
